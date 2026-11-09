"""作业6：跑两个完整创作案例 + 一次中途干预。

用法：
    python scripts/run_week6.py            # 只跑案例1（复用已有产物，秒级）
    python scripts/run_week6.py --all      # 案例1 + 案例2 + 干预演示
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.config import DATA_DIR, OUTPUT_DIR  # noqa: E402
from src.week6_agent.agent import CaseSpec, StoryAgent  # noqa: E402
from src.week6_agent.tools import default_registry  # noqa: E402

CASE1 = CaseSpec(
    name="案例1 · 把光借给你",
    idea="一个怕黑的小女孩在阁楼点亮旧灯笼，遇见一只靠吞灯光取暖的乌云小怪",
    request={"protagonist": "小满", "age": 7, "scene": "夜晚的城市屋顶",
             "theme": "怕黑与分享", "length": "长", "audience_age": "6-8岁"},
    story_file="main_story.json",
    script_file="script.json",
    image_source=str(OUTPUT_DIR / "images"),
    image_prefix="",
    tag="案例1",
    src_prefix="",
)

CASE2 = CaseSpec(
    name="案例2 · 第六颗星",
    idea="在太空站过夜的小男孩，想念家里的灯，遇见一颗很久没人跟它说话的星星",
    request={"protagonist": "阿木", "age": 6, "scene": "太空站",
             "theme": "想念家人", "length": "短", "audience_age": "5-7岁"},
    story_file="space_story.json",
    script_file="space_script.json",
    image_source=str(DATA_DIR / "material_library" / "images"),
    image_prefix="案例2",
    tag="案例2",
    src_prefix="space",
)


def run_case(case: CaseSpec, skip_video: bool = False) -> StoryAgent:
    agent = StoryAgent(default_registry())
    if skip_video:
        agent.registry.get("compose_video").fn = lambda **kw: {"video": "（本次跳过）"}
    rep = agent.run(case)
    print(f"\n=== {case.name} ===")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    agent.save_trace(OUTPUT_DIR / "traces" / f"trace_{case.tag}.json")
    agent.render_log(OUTPUT_DIR / "traces" / f"log_{case.tag}.md")
    return agent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="跑案例2与干预演示")
    ap.add_argument("--skip-video", action="store_true")
    args = ap.parse_args()

    if not args.all:
        run_case(CASE1, skip_video=True)

    if args.all:
        agent2 = run_case(CASE2, skip_video=args.skip_video)
        # 中途干预：把主角名字改成"小树"（只重跑配音/剪辑/排版，图片复用）
        res = agent2.intervene("把主角的名字改成小树", case=CASE2)
        print("\n=== 干预结果 ===")
        print(json.dumps(res, ensure_ascii=False, indent=2))
        agent2.save_trace(OUTPUT_DIR / "traces" / "trace_案例2_干预后.json")
        agent2.render_log(OUTPUT_DIR / "traces" / "log_案例2_干预后.md")


if __name__ == "__main__":
    main()
