"""Shared paths and helpers for the tech-gossip (科技八卦局) pipeline."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE = os.path.join(ROOT, "pipeline")
DATA_DIR = os.path.join(ROOT, "data")
EPISODES_DIR = os.path.join(ROOT, "episodes")
SEEN_FILE = os.path.join(DATA_DIR, "seen.json")
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")

TTS_VENV = os.path.expanduser("~/.hermes/tts/edge-tts")
EDGE_TTS = os.path.join(TTS_VENV, "bin", "edge-tts")
FFMPEG = os.path.expanduser("~/.hermes/tools/ffmpeg-9.0.1-darwin-arm64/ffmpeg")
if not os.path.exists(FFMPEG):  # fallback: system
    FFMPEG = "ffmpeg"

# 单一主播: 瓜仔 (科技圈首席吃瓜官), 男声云扬 (用户已认可的声音)
VOICES = {
    "M": "zh-CN-YunyangNeural",
    "F": "zh-CN-YunyangNeural",  # 兼容旧行头, 实际同一把嗓子
}


def today_str() -> str:
    import datetime
    return datetime.date.today().isoformat()


def episode_dir(date_str: str) -> str:
    return os.path.join(EPISODES_DIR, date_str)
