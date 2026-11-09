"""作业5：素材库检索命令行。

用法：
    python -m src.week5_rag.cli "太空冒险"
    python -m src.week5_rag.cli "一个怕黑的小女孩" --kind image
    python -m src.week5_rag.cli "海底珊瑚灯灭了" --recommend
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.week5_rag.library import build_library  # noqa: E402

KIND_LABEL = {"template": "故事模板", "image": "配图", "sfx": "音效"}


def show(results: list[dict]) -> None:
    for r in results:
        star = "★" * max(1, round(r["score"] * 5)) if r["score"] > 0 else ""
        print(f"  {r['score']:.3f} {star:<5} [{KIND_LABEL.get(r['kind'], r['kind'])}] "
              f"{r['title']}" + (f"  ({r.get('file')})" if r.get("file") else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--kind", default=None, choices=["template", "image", "sfx"])
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--recommend", action="store_true")
    ap.add_argument("--no-clip", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)
    lib = build_library(prefer_clip=not args.no_clip)
    print(f"编码器：{lib.encoder.name}　素材 {len(lib.items)} 条\n")

    if args.recommend:
        sug = lib.recommend(args.query)
        print(json.dumps(sug, ensure_ascii=False, indent=2))
        return

    print(f"查询：{args.query}")
    show(lib.search(args.query, top_k=args.top, kind=args.kind))


if __name__ == "__main__":
    main()
