#!/usr/bin/env python3
"""把瓜仔口播稿合成单集音频。
用法: python3 pipeline/tts_episode.py episodes/<日期>/script.txt

脚本格式: 每行 "M：..." (也接受 男:/女:F: 和半角冒号, 全部走同一把嗓子), 空行 = 段落停顿。
目标时长 10–20 分钟 (edge-tts 中文 ≈ 4.9 字/秒 → 约 2700–5800 字)。
输出: episodes/<日期>/episode.mp3 (24kHz mono, 64kbps), 临时文件在 episodes/<日期>/tmp/
"""
import os
import re
import subprocess
import sys

from common import EDGE_TTS, FFMPEG, VOICES

LINE_RE = re.compile(r"^\s*([MFmf男女])[：:]\s*(.+)$")
SILENCE_NORMAL = 0.35   # 句间
SILENCE_PARA = 0.9      # 段间(空行)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def tts_line(text, voice, out_mp3):
    txt = out_mp3 + ".txt"
    with open(txt, "w", encoding="utf-8") as f:
        f.write(text)
    for attempt in range(3):
        r = run([EDGE_TTS, "-v", voice, "-f", txt, "--write-media", out_mp3, "--no-progress"])
        if r.returncode == 0 and os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 2000:
            return
        if attempt < 2:
            import time
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f"TTS failed: {text[:30]}… ({r.stderr[:200]})")


def to_wav(src, dst):
    r = run([FFMPEG, "-y", "-i", src, "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", dst])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg wav fail: {r.stderr[-200:]}")


def make_silence(dst, seconds):
    r = run([FFMPEG, "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
             "-t", str(seconds), "-c:a", "pcm_s16le", dst])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg silence fail: {r.stderr[-200:]}")


def mp3_duration(path):
    r = run([FFMPEG, "-i", path])
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if not m:
        return None
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def para_break_at(lines, idx):
    """segment idx 在原始行里后面是否紧跟空行(段末)"""
    seen_nonblank = 0
    for j in range(len(lines) - 1, -1, -1):
        if lines[j].strip():
            if seen_nonblank == idx:
                nxt = lines[j + 1] if j + 1 < len(lines) else None
                return nxt is None or not nxt.strip()
            seen_nonblank += 1
    return False


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: tts_episode.py <episode_dir/script.txt>")
    script_path = os.path.abspath(sys.argv[1])
    epi_dir = os.path.dirname(script_path)
    tmp = os.path.join(epi_dir, "tmp")
    os.makedirs(tmp, exist_ok=True)

    lines = [l.rstrip() for l in open(script_path, encoding="utf-8")]
    segments = []
    for l in lines:
        if not l.strip():
            continue
        m = LINE_RE.match(l)
        if not m:
            sys.exit(f"ERROR: 无法识别的行(应以 M： 开头): {l[:60]}")
        segments.append((VOICES["M"], m.group(2).strip()))

    total_chars = sum(len(t) for _, t in segments)
    print(f"segments={len(segments)} chars={total_chars}")
    if not (2600 <= total_chars <= 5800):
        print(f"WARNING: 总字数 {total_chars} 超出 10–20 分钟范围(约 2700–5800 字), 请先调整稿件")

    parts = []
    for i, (voice, text) in enumerate(segments):
        mp3 = os.path.join(tmp, f"seg{i:03d}.mp3")
        tts_line(text, voice, mp3)
        wav = os.path.join(tmp, f"seg{i:03d}.wav")
        to_wav(mp3, wav)
        parts.append(wav)
        if i < len(segments) - 1:
            gap = SILENCE_PARA if para_break_at(lines, i) else SILENCE_NORMAL
            sil = os.path.join(tmp, f"sil{i:03d}.wav")
            make_silence(sil, gap)
            parts.append(sil)

    concat = os.path.join(tmp, "concat.txt")
    with open(concat, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")
    full_wav = os.path.join(tmp, "full.wav")
    r = run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", concat, "-c", "copy", full_wav])
    if r.returncode != 0:
        sys.exit("concat failed: " + r.stderr[-300:])

    out = os.path.join(epi_dir, "episode.mp3")
    r = run([FFMPEG, "-y", "-i", full_wav, "-c:a", "libmp3lame", "-b:a", "64k", "-ar", "24000", out])
    if r.returncode != 0:
        sys.exit("mp3 encode failed: " + r.stderr[-300:])

    dur = mp3_duration(out) or 0
    print(f"OK {out}  {dur/60:.1f} min ({int(dur)}s)  size={os.path.getsize(out)//1024}KB")
    if dur < 600:
        print("WARNING: 音频不足 10 分钟, 建议增加故事")
    if dur > 1200:
        print("WARNING: 音频超过 20 分钟, 建议精简")


if __name__ == "__main__":
    main()
