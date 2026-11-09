"""作业3：一键生成有声书 + 同步高亮网页。

用法：
    python scripts/run_week3.py
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.config import DATA_DIR, OUTPUT_DIR  # noqa: E402
from src.week3_tts.tts import build_sync_page, synthesize_script  # noqa: E402


def main() -> None:
    script = DATA_DIR / "stories" / "script.json"
    timeline = asyncio.run(synthesize_script(script, bgm=True))
    page = build_sync_page(timeline)

    web = OUTPUT_DIR / "web"
    shutil.copyfile(OUTPUT_DIR / "audio" / timeline["audio_file"], web / timeline["audio_file"])

    # 网页用压缩后的 JPEG，别把 44MB 的 PNG 塞进去
    from src.week2_illustration.book import _web_image

    img_src, img_dst = OUTPUT_DIR / "images", web / "images"
    for i in sorted({s["scene"] for s in timeline["segments"]}):
        _web_image(img_src / f"scene{i:02d}.png", img_dst)

    print(f"音频时长：{timeline['total_duration']:.1f} 秒")
    print(f"音色：{', '.join(timeline['stats']['voices'])}")
    print(f"有声书：{OUTPUT_DIR / 'audio' / timeline['audio_file']}")
    print(f"同步网页：{page}")


if __name__ == "__main__":
    main()
