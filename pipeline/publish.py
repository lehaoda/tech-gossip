#!/usr/bin/env python3
"""发布一期《科技八卦局》: 音频存 Mac Studio 硬盘(podcast.lehaoda.com/tech_gossip 对外)
→ 重建 feed.xml + index.html → 推 GitHub Pages → 校验线上。
用法: python3 pipeline/publish.py episodes/<日期目录>
"""
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from email.utils import format_datetime
from xml.sax.saxutils import escape, quoteattr

from common import DATA_DIR, FFMPEG, METADATA_FILE, ROOT

REPO = "lehaoda/tech-gossip"
EPISODES_JSON = os.path.join(DATA_DIR, "episodes.json")

# 音频与封面存 Mac Studio 外挂硬盘, 由 docker/nginx 只读挂载后经 cloudflared 隧道对外:
# docker pod: ~/docker/podcast-server (nginx.conf 配了 mp3→audio/mpeg, m4a→audio/mp4)
PODCAST_HDD_DIR = "/Volumes/Immich-HDD-Temp/podcast/tech_gossip/episodes"
COVER_HDD = "/Volumes/Immich-HDD-Temp/podcast/tech_gossip/cover/cover.png"
AUDIO_BASE = "https://podcast.lehaoda.com/tech_gossip/episodes"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def mp3_duration(path):
    r = run([FFMPEG, "-i", path])
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if not m:
        return 0
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + int(float(s))


def load_episodes():
    try:
        with open(EPISODES_JSON) as f:
            return json.load(f)
    except Exception:
        return []


def summary_to_html(summary_path):
    """summary.md 每行一条新闻(标题 | 来源 | 链接, 可含「传闻」标记) → 带链接的 <ul>"""
    if not summary_path or not os.path.exists(summary_path):
        return ""
    out = ["<p>本期吃瓜原料与原始来源:</p><ul>"]
    for line in open(summary_path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.search(r"(https?://\S+)", line)
        text = line.lstrip("-·• ").strip()
        if m:
            url = m.group(1).rstrip(".,)）")
            label = text.replace(url, "").strip(" -—|｜:：(（").split("|")[0].strip() or url
            item = f"{escape(label)} <a href={quoteattr(url)}>{escape(label[:60])}</a>"
        else:
            item = escape(text)
        out.append(f"<li>{item}</li>")
    out.append("</ul>")
    return "".join(out)


def build_feed(meta, episodes):
    base = meta["base_url"].rstrip("/")
    now = datetime.now().astimezone()
    last = format_datetime(now, usegmt=False)
    items = []
    for ep in episodes[:20]:
        items.append(f"""  <item>
    <title>{escape(ep['title'])}</title>
    <description><![CDATA[{ep['description_html']}]]></description>
    <link>{base}/#{ep['date']}</link>
    <guid isPermaLink="true">{base}/#{ep['date']}</guid>
    <pubDate>{ep['pubdate']}</pubDate>
    <enclosure url="{AUDIO_BASE}/{ep['date']}.m4a" type="audio/mp4" length="{ep['bytes']}"/>
    <itunes:duration>{ep['duration']}</itunes:duration>
    <itunes:image href="{base}/cover.png"/>
  </item>""")
    cats = "".join(
        '<itunes:category text="%s"/>' % escape(c.split(":")[-1])
        for c in meta.get("categories", ["Technology"])
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:image="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(meta['title'])}</title>
    <description>{escape(meta['description'])}</description>
    <link>{base}</link>
    <language>{meta.get('language', 'zh-CN')}</language>
    <lastBuildDate>{last}</lastBuildDate>
    <image>
      <url>{base}/cover.png</url>
      <title>{escape(meta['title'])}</title>
      <link>{base}</link>
    </image>
    <generator>tech-gossip pipeline</generator>
    <itunes:explicit>{'true' if meta.get('explicit') else 'no'}</itunes:explicit>
    <itunes:author>{escape(meta['author'])}</itunes:author>
    <itunes:title>{escape(meta['title'])}</itunes:title>
    <itunes:subtitle>{escape(meta.get('subtitle', ''))}</itunes:subtitle>
    <itunes:owner>
      <itunes:name>{escape(meta['author'])}</itunes:name>
      <itunes:email>{escape(meta.get('owner_email', 'lehaoda@gmail.com'))}</itunes:email>
    </itunes:owner>
    {cats}
    <itunes:image href="{base}/cover.png"/>
{chr(10).join(items)}
  </channel>
</rss>
"""


def build_index(meta, episodes):
    base = meta["base_url"].rstrip("/")
    rows = []
    for ep in episodes:
        rows.append(f"""
  <section>
    <h2><a href="#{ep['date']}">{escape(ep['title'])}</a> <span class="dim">{ep['date']} · {ep['duration']/60:.1f} min</span></h2>
    <audio controls preload="none" src="{AUDIO_BASE}/{ep['date']}.m4a"></audio>
    <details>{ep['description_html']}</details>
  </section>""")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(meta['title'])} · 播客</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin:0 auto; max-width:860px; padding:48px 24px 80px; font-family:-apple-system,'PingFang SC','Hiragino Sans GB',sans-serif; background:#0a101e; color:#e8edf5; }}
header {{ display:flex; gap:28px; align-items:center; margin-bottom:40px; }}
header img {{ width:220px; height:220px; border-radius:20px; }}
h1 {{ font-size:34px; margin:0 0 8px; }}
.sub {{ color:#8b9bb8; line-height:1.6; font-size:15px; }}
.feed {{ display:inline-block; margin-top:14px; font-size:13px; color:#ffd84d; background:#201c10; padding:6px 12px; border-radius:8px; word-break:break-all; }}
section {{ border-top:1px solid #1d2b45; padding:24px 0; }}
h2 {{ font-size:20px; margin:0 0 14px; }}
h2 a {{ color:#fff; text-decoration:none; }}
.dim {{ color:#68789a; font-size:14px; font-weight:400; }}
audio {{ width:100%; }}
details summary {{ cursor:pointer; color:#9fb0cc; font-size:14px; margin-top:12px; }}
details ul {{ line-height:1.9; font-size:14px; color:#c6d2e6; }}
details a {{ color:#ffd84d; }}
</style>
</head>
<body>
<header>
  <img src="/cover.png" alt="{escape(meta['title'])}">
  <div>
    <h1>{escape(meta['title'])}</h1>
    <div class="sub">{escape(meta['description'])}</div>
    <a class="feed" href="/feed.xml">RSS: {base}/feed.xml</a>
  </div>
</header>
{"".join(rows)}
</body>
</html>
"""


def git_publish(commit_msg):
    if not os.path.exists(os.path.join(ROOT, ".git")):
        run(["git", "init", "-b", "main", ROOT])
    r = run(["git", "remote", "get-url", "origin"], cwd=ROOT)
    if r.returncode != 0:
        run(["gh", "repo", "create", REPO, "--public"], cwd=ROOT)
        run(["git", "remote", "add", "origin", f"https://github.com/{REPO}.git"], cwd=ROOT)
    run(["git", "add", "-A"], cwd=ROOT)
    r = run(["git", "commit", "-m", commit_msg], cwd=ROOT)
    if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
        print("commit fail:" + (r.stderr or r.stdout)[-300:])
        return False
    r = run(["git", "push", "origin", "main"], cwd=ROOT, timeout=300)
    if r.returncode != 0:
        print("PUSH FAIL:\n" + (r.stderr or r.stdout)[-500:])
        return False
    return True


def ensure_pages():
    r = run(["gh", "api", f"repos/{REPO}/pages"], cwd=ROOT)
    if r.returncode == 0:
        return True
    r = run(["gh", "api", "-X", "POST", f"repos/{REPO}/pages",
             "-f", "source[branch]=main", "-f", "source[path]=/"], cwd=ROOT)
    if r.returncode != 0:
        print("PAGES FAIL:\n" + (r.stderr or r.stdout)[-300:])
        return False
    return True


def wait_live(base, date_str, timeout_s=300):
    feed_url = f"{base}/feed.xml"
    audio_url = f"{AUDIO_BASE}/{date_str}.m4a"
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                body = r.read().decode("utf-8", "ignore")
            if "<rss" in body:
                with urllib.request.urlopen(urllib.request.Request(audio_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as r2:
                    if r2.status == 200:
                        print(f"LIVE: feed {feed_url}")
                        print(f"LIVE: audio {audio_url}")
                        return True
                print("feed live, audio not yet…")
        except Exception:
            pass
        time.sleep(15)
    print(f"TIMEOUT waiting for live pages (feed={feed_url})")
    return False


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: publish.py episodes/<date_dir>")
    epi_dir = os.path.abspath(sys.argv[1])
    date_str = os.path.basename(epi_dir)
    mp3 = os.path.join(epi_dir, "episode.mp3")
    if not os.path.exists(mp3):
        sys.exit(f"no episode.mp3 in {epi_dir}")
    with open(METADATA_FILE) as f:
        meta = json.load(f)
    base = meta["base_url"].rstrip("/")

    # 1) 音频存 Mac Studio 硬盘 (经 podcast.lehaoda.com/tech_gossip/episodes/ 对外):
    #    enclosure 用 .m4a (audio/mp4, IANA 标准 + Apple 推荐 AAC), 另存 .mp3 (audio/mpeg) 留底
    os.makedirs(PODCAST_HDD_DIR, exist_ok=True)
    site_mp3 = os.path.join(PODCAST_HDD_DIR, f"{date_str}.mp3")
    with open(mp3, "rb") as a, open(site_mp3, "wb") as b:
        b.write(a.read())
    site_m4a = os.path.join(PODCAST_HDD_DIR, f"{date_str}.m4a")
    r = run([FFMPEG, "-y", "-i", mp3, "-c:a", "aac", "-b:a", "64k", site_m4a])
    if r.returncode != 0:
        sys.exit("m4a encode failed: " + r.stderr[-300:])
    duration = mp3_duration(site_mp3)
    now = datetime.now().astimezone()
    pubdate = format_datetime(now, usegmt=False)
    desc_html = summary_to_html(os.path.join(epi_dir, "summary.md"))
    if not desc_html:
        desc_html = "<p>本期科技圈八卦深扒。</p>"

    # 2) episodes.json (新在前)
    episodes = [e for e in load_episodes() if e["date"] != date_str]
    episodes.insert(0, {
        "date": date_str,
        "title": f"{meta['title']} {date_str} · 第{len(episodes)+1}期",
        "description_html": desc_html,
        "duration": duration,
        "bytes": os.path.getsize(site_m4a),
        "pubdate": pubdate,
    })
    with open(EPISODES_JSON, "w") as f:
        json.dump(episodes, f, ensure_ascii=False, indent=1)

    # 3) feed + index
    feed_xml = build_feed(meta, episodes)
    import xml.etree.ElementTree as ET
    ET.fromstring(feed_xml)  # 护栏: XML 非法就停,不发出去
    with open(os.path.join(ROOT, "feed.xml"), "w") as f:
        f.write(feed_xml)
    with open(os.path.join(ROOT, "index.html"), "w") as f:
        f.write(build_index(meta, episodes))

    # 4) push + pages + 校验
    ok = git_publish(f"EP {date_str}: 第{len(episodes)}期 {meta['title']} (m4a enclosure)")
    if not ok:
        sys.exit("push failed")
    if not ensure_pages():
        sys.exit("pages enable failed (若仓库已开启 Pages 可忽略)")
    wait_live(base, date_str)
    print(f"episode page: {base}/")


if __name__ == "__main__":
    main()
