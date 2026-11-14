#!/usr/bin/env python3
"""《科技八卦局》每日视频: 瓜仔出镜 + 新闻配图 + 精准字幕 + 播客原声。

用法: .venv/bin/python pipeline/make_video.py episodes/<日期>

输入:
  episodes/<日期>/script.txt      口播稿 (M： 行头)
  episodes/<日期>/tmp/seg*.wav    TTS 分句 wav (算精准字幕时间轴)
  episodes/<日期>/episode.mp3     整期音频
  episodes/<日期>/scenes.json     场景表 (agent 写稿时生成):
    {"date":"2026-11-10","episode":"第1期","scenes":[
       {"type":"open","start":0,"end":4},
       {"type":"story","start":5,"end":30,"title":"Anthropic IPO","source":"TechCrunch","link":"https://..."},
       {"type":"outro","start":100,"end":112}]}
    start/end = script.txt 中非空行的 0 基下标
  assets/guazai.png               瓜仔抠图 (3000px 透明底)
  assets/cover.png                整张封面 (开场/结尾用)

输出:
  episodes/<日期>/video.mp4       1080p30 完整视频
  episodes/<日期>/preview.mp4     720p 前 45s 预览 (给 Telegram)
  /Volumes/Immich-HDD-Temp/podcast/tech_gossip/videos/<日期>.mp4  (自动拷硬盘)
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from email.utils import parsedate_to_datetime  # noqa: F401

from common import FFMPEG, ROOT

EP_W, EP_H, FPS = 1920, 1080, 30
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0"}
HDD_VIDEOS_CANDIDATES = [
    "/Volumes/DataVault/podcast/tech_gossip/videos",              # 2026-11-13 卷改名后
    "/Volumes/Immich-HDD-Temp/podcast/tech_gossip/videos",        # 旧挂载名
]


def hdd_videos_dir():
    for base in HDD_VIDEOS_CANDIDATES:
        if os.path.isdir(os.path.dirname(os.path.dirname(os.path.dirname(base)))):
            os.makedirs(base, exist_ok=True)
            return base
    sys.exit("HDD 未挂载 (DataVault / Immich-HDD-Temp 都不存在) — 视频不存到别处, 报失败")
FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
SIL_NORMAL, SIL_PARA = 0.35, 0.9


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def wav_dur(path):
    r = run([FFMPEG, "-i", path])
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if not m:
        return 0.0
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def parse_segments(epi_dir):
    """按 tts_episode.py 的拼接顺序算每行(非空)的 start/end 秒。"""
    lines = [l.rstrip() for l in open(os.path.join(epi_dir, "script.txt"), encoding="utf-8")]
    LINE_RE = re.compile(r"^\s*([MFmf男女])[：:]\s*(.+)$")
    segs = []
    t = 0.0
    for i, l in enumerate(lines):
        if not l.strip():
            continue
        m = LINE_RE.match(l)
        if not m:
            continue
        idx = len(segs)
        segs.append({"line_idx": i, "text": m.group(2).strip(), "start": t})
        tmp = os.path.join(epi_dir, "tmp", f"seg{idx:03d}.wav")
        d = wav_dur(tmp) if os.path.exists(tmp) else 0.0
        t += d
        segs[-1]["end"] = t  # 本行音频结束点
        # 停顿: 该行后面(原始行)是否为空行/结尾
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if nxt is None or not nxt.strip():
            t += SIL_PARA
        else:
            t += SIL_NORMAL
    return lines, segs


def para_break_at(lines, line_idx):
    nxt = lines[line_idx + 1] if line_idx + 1 < len(lines) else None
    return nxt is None or not nxt.strip()


def build_ass(epi_dir, segs, out_path):
    def ts(t):
        h = int(t // 3600)
        mnt = int(t % 3600 // 60)
        s = t % 60
        return f"{h}:{mnt:02d}:{s:05.2f}"

    out = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {EP_W}",
        f"PlayResY: {EP_H}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sub,Hiragino Sans GB,52,&H00FFFFFF,&H00FFFFFF,&H00101828,&H80000000,-1,0,0,0,100,100,1,0,1,5,1,2,90,90,60,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for s in segs:
        text = s["text"]
        # 简单换行: 超过 24 字拆两行 (\N)
        if len(text) > 24:
            mid = min([i for i in range(12, len(text)) if text[i] in "，、。；："] + [len(text) - 12])
            text = text[:mid + 1] + "\\N" + text[mid + 1:]
        text = text.replace("(", "\\(").replace(")", "\\)")
        out.append(f"Dialogue: 0,{ts(s['start'])},{ts(s['end'])},Sub,,0,0,0,,{text}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def make_bg(out_path, size=(EP_W, EP_H)):
    from PIL import Image, ImageDraw, ImageFilter
    import random
    w, h = size
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        k = y / h
        r = int(8 + 6 * k)
        g = int(14 + 10 * k)
        b = int(30 + 18 * k)
        for x in range(w):
            # 轻微左侧冷光
            glow = math.exp(-((x - w * 0.18) ** 2) / (2 * (w * 0.35) ** 2)) * (1 - k * 0.5)
            px[x, y] = (min(255, r + int(glow * 14)), min(255, g + int(glow * 16)), min(255, b + int(glow * 30)))
    d = ImageDraw.Draw(img)
    random.seed(42)
    for _ in range(220):
        x, y = random.randint(0, w - 1), random.randint(0, h - 1)
        a = random.randint(30, 130)
        rad = random.choice([1, 1, 1, 2])
        d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=(180, 200, 255, a) if False else (150 + a // 2, 170 + a // 2, 255))
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img.save(out_path)
    return out_path


def cover_crop(img, box_w, box_h):
    from PIL import Image
    w, h = img.size
    scale = max(box_w / w, box_h / h)
    nw, nh = int(w * scale) + 1, int(h * scale) + 1
    img = img.resize((nw, nh), Image.LANCZOS)
    x = (nw - box_w) // 2
    y = (nh - box_h) // 2
    return img.crop((x, y, x + box_w, y + box_h))


def rounded(img, radius=36):
    from PIL import Image, ImageDraw
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1], radius, fill=255)
    img = img.convert("RGBA")
    img.putalpha(mask)
    return img


def fetch_image(link, out_path, fallback_title):
    """抓 og:image; 失败则生成标题卡。返回 (path, credit_or_None)"""
    credit = None
    if link:
        try:
            req = urllib.request.Request(link, headers=UA)
            raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
            m = re.search(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)', raw) \
                or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']', raw)
            if m:
                img_url = m.group(1)
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                data = urllib.request.urlopen(urllib.request.Request(img_url, headers=UA), timeout=20).read()
                if len(data) > 8000:
                    with open(out_path, "wb") as f:
                        f.write(data)
                    credit = "图源: 原报道"
                    return out_path, credit
        except Exception:
            pass
    # fallback: 标题卡
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1600, 900), (16, 24, 44))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1600, 200], fill=(20, 30, 56))
    f_big = ImageFont.truetype(FONT, 84)
    f_sm = ImageFont.truetype(FONT, 44)
    title = fallback_title[:16]
    d.text((60, 620), title, font=f_big, fill=(255, 216, 77))
    d.text((60, 740), "科技八卦局 · 图文示意", font=f_sm, fill=(139, 155, 184))
    d.text((60, 60), "科技八卦局", font=f_big, fill=(232, 237, 245))
    img.save(out_path)
    return out_path, None


def draw_plate(scene, epi_dir, guazai, plate_out):
    """PIL 合成单个场景的静态底版 (1920x1080, 不含浮动的瓜仔)"""
    from PIL import Image, ImageDraw, ImageFont
    f_brand = ImageFont.truetype(FONT, 46)
    f_title = ImageFont.truetype(FONT, 74)
    f_src = ImageFont.truetype(FONT, 40)
    f_big = ImageFont.truetype(FONT, 150)

    plate = Image.open(plate_bg_path).convert("RGBA")
    d = ImageDraw.Draw(plate)

    # 顶部品牌条
    d.text((48, 40), "科技八卦局", font=f_brand, fill=(255, 216, 77))
    d.text((252, 54), "TECH GOSSIP", font=ImageFont.truetype(FONT, 30), fill=(107, 124, 160))
    if episode_tag:
        d.text((EP_W - 48 - d.textlength(episode_tag, font=f_src), 48), episode_tag, font=f_src, fill=(139, 155, 184))

    st = scene["type"]
    if st in ("open", "outro"):
        # 整张封面居中 + 大字幕位
        cov = Image.open(os.path.join(ROOT, "cover.png")).convert("RGBA")
        cov = cov.resize((900, 900), Image.LANCZOS)
        plate.alpha_composite(cov, ((EP_W - 900) // 2, 80))
        return plate

    # story: 右侧新闻图 (980x560), 左侧留给瓜仔
    img_path, credit = fetch_image(scene.get("link"), os.path.join(epi_dir, "tmp", f"scene_{scene['start']}.jpg"), scene.get("title", ""))
    credit = credit or ""
    try:
        news = Image.open(img_path).convert("RGB")
    except Exception:
        print(f"  WARN: 新闻图加载失败, 用标题卡: {img_path}")
        news = None
    if news is not None:
        news = cover_crop(news, 980, 560)
        news = rounded(news, 30)
    else:
        news = rounded(Image.open(os.path.join(epi_dir, "tmp", f"scene_{scene['start']}.jpg")).convert("RGB"), 30)
    nx, ny = 880, 200
    # 图片阴影
    shadow = Image.new("RGBA", news.size, (0, 0, 0, 0))
    from PIL import ImageDraw as ID
    ID.Draw(shadow).rounded_rectangle([0, 0, news.size[0] - 1, news.size[1] - 1], 30, fill=(0, 0, 0, 120))
    shadow = shadow.filter(__import__("PIL.ImageFilter", fromlist=["GaussianBlur"]).GaussianBlur(18))
    plate.alpha_composite(shadow, (nx - 10, ny + 14))
    plate.alpha_composite(news, (nx, ny))
    # 图内右下: 来源标注 (来源 + 图源)
    attr = f"来源: {scene['source']}" if scene.get("source") else ""
    if credit:
        attr = f"{attr} · {credit}" if attr else credit
    if attr:
        fa = ImageFont.truetype(FONT, 26)
        d.text((nx + 980 - 40 - d.textlength(attr, font=fa), ny + 560 - 46), attr, font=fa, fill=(235, 240, 250))

    # 标题 lower-third (图下)
    title = scene.get("title", "")
    if title:
        by = ny + 560 + 46
        d.rectangle([nx - 4, by - 14, nx + 6, by + 78], fill=(255, 216, 77))
        tfont = f_title
        tw = d.textlength(title[:22], font=tfont)
        if tw > 930:  # 自动缩字号, 不超出图宽
            tfont = ImageFont.truetype(FONT, max(44, int(74 * 930 / tw)))
        d.text((nx + 22, by), title[:22], font=tfont, fill=(245, 248, 255))
    return plate


plate_bg_path = None
episode_tag = ""


def render_scene(scene, epi_dir, guazai_path, out_mp4):
    dur = scene["_dur"]
    plate = draw_plate(scene, epi_dir, guazai_path, os.path.join(epi_dir, "tmp", f"plate_{scene['start']}.png"))
    plate.save(os.path.join(epi_dir, "tmp", f"plate_{scene['start']}.png"))
    plate_p = os.path.join(epi_dir, "tmp", f"plate_{scene['start']}.png")
    if scene["type"] in ("open", "outro"):
        # 封面本身就有瓜仔, 不再叠加
        fc = "[0]scale=%d:%d,format=yuv420p,fade=t=in:st=0:d=0.35,fade=t=out:st=%.3f:d=0.35[v]" % (EP_W, EP_H, dur - 0.35)
        cmd = [
            FFMPEG, "-y",
            "-loop", "1", "-t", f"{dur:.3f}", "-i", plate_p,
            "-filter_complex", fc,
            "-map", "[v]", "-r", str(FPS), "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            out_mp4,
        ]
    else:
        cmd = [
            FFMPEG, "-y",
            "-loop", "1", "-t", f"{dur:.3f}", "-i", plate_p,
            "-loop", "1", "-t", f"{dur:.3f}", "-i", guazai_path,
            "-filter_complex", (
                "[0]scale=%d:%d,format=rgba[plate];"
                "[1]scale=-1:520,format=rgba[host];"
                "[plate][host]overlay=x=100:y='280+10*sin(2*PI*t/3.2)',"
                "fade=t=in:st=0:d=0.35,fade=t=out:st=%.3f:d=0.35,format=yuv420p[v]"
            ) % (EP_W, EP_H, dur - 0.35),
            "-map", "[v]", "-r", str(FPS), "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            out_mp4,
        ]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"scene render fail (start={scene['start']}): {r.stderr[-400:]}")


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: make_video.py episodes/<date>")
    epi_dir = os.path.abspath(sys.argv[1])
    tmp = os.path.join(epi_dir, "tmp")
    os.makedirs(tmp, exist_ok=True)

    global plate_bg_path, episode_tag
    bg_path = os.path.join(ROOT, "assets", "bg_1080.png")
    if not os.path.exists(bg_path):
        make_bg(bg_path)
    plate_bg_path = bg_path

    scenes_json = os.path.join(epi_dir, "scenes.json")
    if not os.path.exists(scenes_json):
        sys.exit(f"缺少 {scenes_json}: 写稿时需同步生成场景表 (type/start/end/title/source/link)")
    with open(scenes_json) as f:
        meta = json.load(f)
    episode_tag = f"{meta.get('date', '')} · {meta.get('episode', '')}"

    _, segs = parse_segments(epi_dir)
    total = segs[-1]["end"] if segs else 0
    print(f"segments={len(segs)} total={total:.1f}s")

    # 场景时长 = 该行段 [start, end) 秒
    for sc in meta["scenes"]:
        s0 = segs[sc["start"]]["start"] if sc["start"] < len(segs) else 0
        s1 = segs[min(sc["end"], len(segs) - 1)]["end"] if segs else 0
        sc["_dur"] = max(1.5, s1 - s0 + 0.35)
    covered_end = max(s["_dur"] for s in meta["scenes"])
    print("scenes:", [(s["type"], round(s["_dur"], 1)) for s in meta["scenes"]])

    guazai = os.path.join(ROOT, "assets", "guazai.png")
    if not os.path.exists(guazai):
        sys.exit("缺少 assets/guazai.png (瓜仔抠图)")

    clip_files = []
    for i, sc in enumerate(meta["scenes"]):
        out = os.path.join(tmp, f"clip_{i:02d}.mp4")
        print(f"rendering scene {i} [{sc['type']} {sc.get('title', '')}] {sc['_dur']:.1f}s")
        render_scene(sc, epi_dir, guazai, out)
        clip_files.append(out)

    concat = os.path.join(tmp, "clips.txt")
    with open(concat, "w") as f:
        for c in clip_files:
            f.write(f"file '{c}'\n")
    joined = os.path.join(tmp, "joined.mp4")
    r = run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", concat, "-c", "copy", joined])
    if r.returncode != 0:
        sys.exit("concat fail: " + r.stderr[-300:])

    # 烧字幕 + 混入播客原声
    ass = os.path.join(epi_dir, "subs.ass")
    build_ass(epi_dir, segs, ass)
    video_out = os.path.join(epi_dir, "video.mp4")
    r = run([
        FFMPEG, "-y", "-i", joined, "-i", os.path.join(epi_dir, "episode.mp3"),
        "-vf", f"ass={ass}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-shortest",
        video_out,
    ])
    if r.returncode != 0:
        sys.exit("final encode fail: " + r.stderr[-400:])

    # 预览: 前 45s 720p
    preview = os.path.join(epi_dir, "preview.mp4")
    r = run([FFMPEG, "-y", "-t", "45", "-i", video_out,
             "-vf", "scale=1280:720", "-r", str(FPS),
             "-c:v", "libx264", "-preset", "medium", "-crf", "24", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "96k", preview])
    if r.returncode != 0:
        print("WARN preview fail: " + r.stderr[-200:])

    # 拷硬盘 (对外 https://podcast.lehaoda.com/tech_gossip/videos/)
    hdd_dir = hdd_videos_dir()
    date_str = os.path.basename(epi_dir)
    hdd_out = os.path.join(hdd_dir, f"{date_str}.mp4")
    shutil.copyfile(video_out, hdd_out)

    def dur_of(p):
        return wav_dur(p)
    print(f"OK video: {video_out}  {dur_of(video_out)/60:.1f} min  {os.path.getsize(video_out)//1024//1024}MB")
    print(f"OK preview: {preview}  {os.path.getsize(preview)//1024}KB")
    print(f"OK HDD: {hdd_out}")
    print(f"URL: https://podcast.lehaoda.com/tech_gossip/videos/{date_str}.mp4")


if __name__ == "__main__":
    main()
