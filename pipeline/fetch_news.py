#!/usr/bin/env python3
"""抓科技圈素材(正规媒体 + 社区) → 去重(含历史已播) → 八卦价值打分 → 输出候选 news.json

用法: python3 pipeline/fetch_news.py
输出: episodes/<今天>/news.json   (最多 30 条候选, 每条带来源/链接/摘要)
规则:
  - 只收录近 24h(不足时自动放宽到 48h/72h) 的信息
  - data/seen.json 中已出现的信息不再出现(保证不重复播过)
  - 本次进入候选池的信息会立即写入 seen —— 就算今天失败,明天也不会重播
  - 社区源(HN/Reddit/V2EX)只负责"发现瓜", 成稿前必须用媒体源交叉核实
"""
import concurrent.futures
import hashlib
import html
import json
import os
import re
import ssl
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from common import DATA_DIR, SEEN_FILE, episode_dir, today_str

# (显示名, URL, 权重, 类型 rss/atom/api)。媒体=核实, 社区=发现, 官方=内幕原料
FEEDS = [
    ("TechCrunch", "https://techcrunch.com/feed/", 3.5, "rss"),
    ("The Verge", "https://www.theverge.com/rss/index.xml", 3.5, "atom"),
    ("WIRED", "https://www.wired.com/feed/rss", 3.0, "rss"),
    ("量子位", "https://www.qbitai.com/feed", 3.5, "rss"),
    ("IT之家", "https://www.ithome.com/rss/", 3.0, "rss"),
    ("爱范儿", "https://www.ifanr.com/feed", 2.5, "rss"),
    ("OpenAI Blog", "https://openai.com/news/rss.xml", 3.0, "rss"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", 2.5, "rss"),
    ("Hacker News (frontpage)", "https://hnrss.org/frontpage", 2.5, "rss"),
    ("Reddit r/technology", "https://www.reddit.com/r/technology/.rss", 2.0, "rss"),
    ("Google News Tech", "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en", 2.0, "rss"),
    ("V2EX 热议", "https://www.v2ex.com/api/topics/hot.json", 2.0, "api"),
]

# 八卦价值词: 人物 / 冲突事件 / 商业内幕
PERSON_KW = re.compile(
    r"(马斯克|Musk|SpaceX|特斯拉|Tesla|Altman|奥特曼|OpenAI|黄仁勋|Jensen|NVIDIA|英伟达|"
    r"扎克伯格|Zuckerberg|Meta|库克|Tim Cook|Apple|苹果|Nadella|微软|Microsoft|"
    r"雷军|周鸿祎|马云|马化腾|李彦宏|Sam Altman|Demis Hassabis|Pichai|Amodei|Dario|"
    r"Ilya|Brooks|字节跳动|百度|腾讯|阿里|华为|小米|DeepSeek|Anthropic|Claude)",
    re.IGNORECASE,
)
DRAMA_KW = re.compile(
    r"(离职|辞职|辞任|挖角|挖人|跳槽|收购|并购|诉讼|起诉|被诉|禁令|罚款|处罚|裁员|"
    r"丑闻|争议|炮轰|硬刚|互怼|开火|内幕|爆料|独家|分手|离婚|婚姻|期权|股权|股份|抛售|"
    r"内斗|宫斗|背叛|出局|解雇|罢免|调查|门事件|估值|融资|IPO|上市|对赌|反垄断|"
    r"scand|resign|quit|lawsuit|sued|fired|poach|buyout|acquisition|feud|drama|"
    r"antitrust|regulat|investigation|ban|ban\b)",
    re.IGNORECASE,
)
TECH_KW = re.compile(
    r"(大模型|LLM|GPT|Gemini|Claude|Groq|算力|GPU|芯片|半导体|机器人|智能体|agent|"
    r"AGI|AI|人工智能|数据中心|云计算|自动驾驶|电动车|手机|大语言模型|推理)",
    re.IGNORECASE,
)

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_date(text, kind):
    if not text:
        return None
    text = text.strip()
    try:
        if kind == "atom":
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsedate_to_datetime(text)
    except Exception:
        return None


ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"


def _strip_block(s):
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s or "", flags=re.S)
    return s


def regex_items(raw):
    """XML 解析失败时的兜底: 正则抽 <item>/<entry> 块"""
    text = raw.decode("utf-8", "ignore")
    items = []
    blocks = re.findall(r"<item[\s>].*?</item>", text, re.S)
    entry_mode = False
    if not blocks:
        blocks = re.findall(r"<entry[\s>].*?</entry>", text, re.S)
        entry_mode = True
    for block in blocks:
        def g(tag):
            m = re.search(r"<%s[^>]*>(.*?)</%s>" % (tag, tag), block, re.S)
            return _strip_block(m.group(1)).strip() if m else ""

        title = strip_html(g("title"))
        link = g("link").strip()
        if not link:
            m = re.search(r'<link[^>]*href=["\']([^"\']+)', block)
            link = m.group(1) if m else ""
        pub_s = g("pubDate") or g("published") or g("updated")
        desc = strip_html(g("description") or g("summary") or g("content"))
        pub = parse_date(pub_s, "atom" if entry_mode else "rss")
        if title and link:
            items.append({"title": title, "link": link, "published": pub, "desc": desc[:300]})
    return items


def fetch_feed(name, url, weight, kind):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as r:
        raw = r.read()
    items = []
    if kind == "api" and "v2ex" in url:
        topics = json.loads(raw.decode("utf-8", "ignore"))
        for t in topics[:40]:
            created = t.get("created")
            pub = datetime.fromtimestamp(int(created), tz=timezone.utc) if created else None
            items.append({
                "title": t.get("title", ""), "link": t.get("url", ""),
                "published": pub, "desc": f"V2EX 热议话题, {t.get('replies', 0)} 条回复",
            })
        return name, items, None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        items = regex_items(raw)
        if items:
            return name, items, None
        try:
            raw2 = re.sub(rb"&(?!\d+;|#\w+;|[a-zA-Z]\w*;)", b"&amp;", raw)
            root = ET.fromstring(raw2)
        except ET.ParseError as e:
            return name, [], f"xml error: {e}"
    if kind == "atom":
        entries = root.findall(f"{ATOM}entry") or list(root.iter(f"{ATOM}entry"))
        for en in entries:
            link = ""
            for a in en.iter(f"{ATOM}link"):
                if a.get("rel") in (None, "alternate"):
                    link = a.get("href", "")
                    break
            title = strip_html((en.findtext(f"{ATOM}title") or ""))
            pub = parse_date(en.findtext(f"{ATOM}published") or en.findtext(f"{ATOM}updated"), "atom")
            desc = strip_html(en.findtext(f"{ATOM}summary") or en.findtext(f"{ATOM}content"))
            if title and link:
                items.append({"title": title, "link": link, "published": pub, "desc": desc[:300]})
    else:
        for it in root.iter("item"):
            title = strip_html(it.findtext("title") or "")
            link = (it.findtext("link") or "").strip()
            pub = parse_date(it.findtext("pubDate") or it.findtext(f"{DC}date"), "rss")
            desc = strip_html(it.findtext("description") or it.findtext("summary") or "")
            if title and link:
                items.append({"title": title, "link": link, "published": pub, "desc": desc[:300]})
    return name, items, None


def norm_title(t: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", t.lower())


def score_item(it):
    """八卦价值评分: 人物/冲突 > 科技感, 新鲜度加分"""
    text = it["title"] + " " + it.get("desc", "")
    s = it["source_weight"]
    if PERSON_KW.search(text):
        s += 2.5
    if DRAMA_KW.search(text):
        s += 2.5
    if TECH_KW.search(text):
        s += 1.0
    age_h = it.get("age_hours", 72)
    if age_h <= 6:
        s += 1.0
    elif age_h <= 24:
        s += 0.5
    return round(s, 2)


def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return json.load(f)
    except Exception:
        return {"items": {}, "updated": None}


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    now = datetime.now(timezone.utc)
    seen = load_seen()
    seen_set = set(seen["items"].keys())  # keys = sha1(normalized url + title)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(fetch_feed, *spec) for spec in FEEDS]
        for f in concurrent.futures.as_completed(futs):
            results.append(f.result())

    print("feed fetch results:", file=sys.stderr)
    pool = []
    failed = []
    for name, items, err in results:
        if err:
            failed.append(f"{name}: {err}")
            continue
        print(f"  {name}: {len(items)} raw items", file=sys.stderr)
        for it in items:
            it["source"] = name
            it["source_weight"] = next(w for n2, u2, w, k2 in FEEDS if n2 == name)
            key = hashlib.sha1((it["link"] + "|" + it["title"]).encode()).hexdigest()
            it["key"] = key
            pool.append(it)

    fresh = [it for it in pool if it["key"] not in seen_set]
    window = None
    cands = []
    for hours in (24, 48, 72):
        cands = []
        for it in fresh:
            if it["published"] is None:
                continue
            age = now - it["published"].astimezone(timezone.utc)
            hours_age = age.total_seconds() / 3600
            if 0 <= hours_age <= hours:
                it["age_hours"] = round(hours_age, 1)
                it["score"] = score_item(it)
                cands.append(it)
        if len(cands) >= 8 or hours == 72:
            window = hours
            break
    cands.sort(key=lambda x: (-x["score"], x["age_hours"]))

    by_title = {}
    for it in cands:
        nt = norm_title(it["title"])
        if nt in by_title:
            if it["score"] > by_title[nt]["score"]:
                by_title[nt] = it
        else:
            by_title[nt] = it
    kept = list(by_title.values())
    kept.sort(key=lambda x: (-x["score"], x["age_hours"]))
    top = kept[:30]

    for it in kept:
        seen["items"][it["key"]] = {
            "title": it["title"], "source": it["source"],
            "published": it["published"].astimezone(timezone.utc).isoformat() if it["published"] else None,
            "first_seen": now.isoformat(),
        }
    cutoff = now - timedelta(days=90)
    seen["items"] = {
        k: v for k, v in seen["items"].items()
        if v.get("first_seen") and datetime.fromisoformat(v["first_seen"]) >= cutoff
    }
    seen["updated"] = now.isoformat()
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, ensure_ascii=False, indent=1)

    out_dir = episode_dir(today_str())
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "date": today_str(),
        "window_hours": window,
        "fetched_at": now.isoformat(),
        "failed_feeds": failed,
        "candidates": [
            {
                "title": it["title"], "source": it["source"], "link": it["link"],
                "published": it["published"].astimezone(timezone.utc).isoformat(),
                "age_hours": it["age_hours"], "score": it["score"], "summary": it.get("desc", ""),
            }
            for it in top
        ],
    }
    out_file = os.path.join(out_dir, "news.json")
    with open(out_file, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(f"\nOK  {len(top)} candidates → {out_file} (window {window}h, seen total {len(seen['items'])})")
    for i, it in enumerate(top, 1):
        print(f"  {i:2d}. [{it['score']:4.1f}] {it['title'][:70]}  — {it['source']} ({it['age_hours']}h)")
    if len(top) < 6:
        print("WARN: 候选不足 6 条, 今天可能凑不齐一个完整节目", file=sys.stderr)
    if failed:
        print("FAIL(已忽略): " + "; ".join(failed[:4]), file=sys.stderr)


if __name__ == "__main__":
    main()
