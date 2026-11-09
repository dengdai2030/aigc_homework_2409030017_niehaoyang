"""作业1：结构化故事生成器（命令行）。

用法：
    # 用默认策略生成一个故事
    python -m src.week1_story.generate --protagonist 小满 --theme 怕黑与分享

    # 四种策略各跑一遍，做对比实验
    python -m src.week1_story.generate --compare

    # 只用某个策略
    python -m src.week1_story.generate --strategy few_shot --length 中
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.common.config import DATA_DIR, LLM  # noqa: E402
from src.common.llm import LLMClient, extract_json  # noqa: E402
from src.common.prompts import (STRATEGY_LABELS, StrategyName, StoryRequest,  # noqa: E402
                               build)
from src.common.schema import Story, StorySchemaError  # noqa: E402

STORY_DIR = DATA_DIR / "stories"
RAW_DIR = DATA_DIR / "raw_outputs"


def generate_once(strategy: StrategyName, req: StoryRequest,
                  client: LLMClient, *, save_raw: bool = True) -> tuple[Story | None, dict]:
    system, user = build(strategy, req)
    result = {"strategy": strategy, "label": STRATEGY_LABELS[strategy],
              "system": system, "user": user, "raw": "", "error": None,
              "story": None, "elapsed_ok": True}

    try:
        text = client.chat(system, user, tag=f"story:{strategy}", json_mode=True)
        result["raw"] = text
    except RuntimeError as exc:
        result["error"] = f"调用失败：{exc}"
        return None, result

    try:
        data = extract_json(text)
        story = Story.from_dict(data, min_scenes=3)
        result["story"] = story
    except (ValueError, StorySchemaError) as exc:
        result["error"] = f"解析/校验失败：{exc}"
        return None, result

    if save_raw:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        (RAW_DIR / f"{strategy}.raw.txt").write_text(text, encoding="utf-8")
        story.save(STORY_DIR / f"story_{strategy}.json")
    return story, result


def main() -> None:
    ap = argparse.ArgumentParser(description="结构化故事生成器")
    ap.add_argument("--strategy", default="schema_selfcheck",
                    choices=["zero_shot", "few_shot", "role_play_cot", "schema_selfcheck"])
    ap.add_argument("--compare", action="store_true", help="四种策略全部跑一遍")
    ap.add_argument("--protagonist", default="小满")
    ap.add_argument("--age", type=int, default=7)
    ap.add_argument("--scene", default="夜晚的城市屋顶")
    ap.add_argument("--theme", default="怕黑与分享")
    ap.add_argument("--length", default="中", choices=["短", "中", "长"])
    ap.add_argument("--audience", default="6-8岁")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    req = StoryRequest(args.protagonist, args.age, args.scene, args.theme,
                       args.length, args.audience)
    client = LLMClient()

    strategies = list(STRATEGY_LABELS) if args.compare else [args.strategy]
    ok, fail = 0, 0
    for s in strategies:
        story, res = generate_once(s, req, client)
        if story is None:
            fail += 1
            print(f"[失败] {res['label']}: {res['error']}")
            continue
        ok += 1
        print(f"[成功] {res['label']} -> 《{story.title}》 "
              f"{len(story.scenes)} 幕 / {story.char_count} 字")
        if args.out and len(strategies) == 1:
            story.save(args.out)

    print(f"\n共 {ok} 成功 / {fail} 失败；{client.usage.summary()}")


if __name__ == "__main__":
    main()
