"""作业4：一键生成故事短片。

用法：
    python scripts/run_week4.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.config import OUTPUT_DIR  # noqa: E402
from src.week4_video.make_video import render  # noqa: E402


def main() -> None:
    import json

    tl_path = OUTPUT_DIR / "audio" / "timeline.json"
    tl = json.loads(tl_path.read_text(encoding="utf-8"))
    out = render(
        timeline_path=tl_path,
        image_dir=OUTPUT_DIR / "images",
        audio=OUTPUT_DIR / "audio" / tl["audio_file"],
        out_mp4=OUTPUT_DIR / "video" / f"{tl['title']}_故事短片.mp4",
    )
    size = out.stat().st_size / 1024 / 1024
    print(f"视频：{out}（{size:.1f} MB）")


if __name__ == "__main__":
    main()
