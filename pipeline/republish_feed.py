#!/usr/bin/env python3
"""只重建 feed.xml/index.html 并推送(不重新编码音频)。
适用: 硬盘上的音频被外部修改后(如整体音量修正), 需要让 feed 的 enclosure length
与硬盘实际字节数一致。bytes 字段按 /Volumes/DataVault 实际文件大小自动校正。
用法: python3 pipeline/republish_feed.py
"""
import json
import os
import xml.etree.ElementTree as ET

from publish import (EPISODES_JSON, METADATA_FILE, PODCAST_HDD_DIR, ROOT,
                     build_feed, build_index, ensure_pages, git_publish, load_episodes,
                     wait_live)


def main():
    with open(METADATA_FILE) as f:
        meta = json.load(f)
    base = meta["base_url"].rstrip("/")
    eps = load_episodes()
    if not eps:
        raise SystemExit("episodes.json 为空")
    changed, missing = 0, []
    for e in eps:
        p = os.path.join(PODCAST_HDD_DIR, f"{e['date']}.m4a")
        if os.path.exists(p):
            sz = os.path.getsize(p)
            if sz != e["bytes"]:
                e["bytes"] = sz
                changed += 1
        else:
            missing.append(e["date"])
    with open(EPISODES_JSON, "w") as f:
        json.dump(eps, f, ensure_ascii=False, indent=1)
    feed_xml = build_feed(meta, eps)
    ET.fromstring(feed_xml)  # 护栏
    with open(os.path.join(ROOT, "feed.xml"), "w") as f:
        f.write(feed_xml)
    with open(os.path.join(ROOT, "index.html"), "w") as f:
        f.write(build_index(meta, eps))
    print(f"bytes 更新 {changed} 期; 硬盘缺失 {len(missing)} 期 {missing}")
    ok = git_publish("feed: sync enclosure lengths with HDD audio")
    if not ok:
        raise SystemExit("push failed")
    ensure_pages()
    wait_live(base, eps[0]["date"])  # 本项目的 wait_live 参数是 date_str(自动补 .m4a)


if __name__ == "__main__":
    main()
