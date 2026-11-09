"""作业2：一键生成绘本（PDF + 网页）。

用法：
    python scripts/run_week2.py                 # 用已生成的图排版
    python scripts/run_week2.py --generate      # 重新调文生图 API 生成全部配图
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.config import DATA_DIR, OUTPUT_DIR  # noqa: E402
from src.common.schema import Story  # noqa: E402
from src.week2_illustration.book import build_pdf, build_web  # noqa: E402
from src.week2_illustration.illustrator import illustrate_story  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--story", default=str(DATA_DIR / "stories" / "main_story.json"))
    ap.add_argument("--generate", action="store_true", help="重新调用文生图 API")
    args = ap.parse_args()

    story = Story.load(args.story)
    img_dir = OUTPUT_DIR / "images"

    if args.generate:
        manifest = illustrate_story(story, img_dir)
        print(f"配图 {len(manifest['items'])} 张，seed={manifest['seed']}")

    pdf = build_pdf(story, img_dir, OUTPUT_DIR / "book" / f"{story.title}.pdf",
                    cover=img_dir / "09_cover.png",
                    sheet=img_dir / "00_character_sheet.png")
    web = build_web(story, img_dir, OUTPUT_DIR / "book",
                    cover=img_dir / "09_cover.png")

    print(f"PDF 绘本：{pdf}（{pdf.stat().st_size / 1024 / 1024:.1f} MB）")
    print(f"网页绘本：{web}")


if __name__ == "__main__":
    main()
