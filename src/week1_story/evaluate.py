"""作业1：Prompt 策略自动评估。

对同一输入、四种策略的输出做可量化对比。
人工分（文笔、创意）在 docs/prompt_strategies.md 里单独给，避免"机器给自己打分"。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.common.llm import extract_json  # noqa: E402
from src.common.prompts import STRATEGY_LABELS  # noqa: E402
from src.common.schema import Story, StorySchemaError  # noqa: E402

RAW_DIR = ROOT / "data" / "raw_outputs"

REQUIRED_TOP = ["title", "characters", "scenes", "moral"]
REQUIRED_SCENE = ["scene_id", "description", "text", "mood", "visual_prompt"]

STYLE_HINTS = ["绘本", "插画", "水彩", "彩铅", "光线", "光", "风格"]
APPEARANCE_HINTS = ["发", "穿", "衣", "帽", "裙", "鞋", "眼睛", "围裙", "太空服"]


def cjk_ratio(s: str) -> float:
    if not s:
        return 0.0
    cjk = len(re.findall(r"[\u4e00-\u9fa5]", s))
    return cjk / len(s)


def dialogue_ratio(text: str) -> float:
    """粗估对白密度：出现引号或冒号的句子占全部句子的比例。"""
    sentences = [s for s in re.split(r"[。！？!?]", text) if s.strip()]
    if not sentences:
        return 0.0
    d = sum(1 for s in sentences if ("“" in s or "”" in s or "：" in s))
    return d / len(sentences)


def evaluate_one(raw_path: Path) -> dict:
    raw = raw_path.read_text(encoding="utf-8")
    name = raw_path.stem.replace(".raw", "")
    row = {"strategy": name, "label": STRATEGY_LABELS.get(name, name),
           "json_ok": False, "schema_ok": False, "extra_fields": [],
           "missing_fields": [], "scenes": 0, "avg_len": 0,
           "dialogue": 0.0, "vp_cjk": 0.0, "vp_full": 0, "appearance": 0,
           "char_match": False, "error": ""}

    try:
        data = extract_json(raw)
        row["json_ok"] = True
    except ValueError as exc:
        row["error"] = str(exc)[:80]
        return row

    row["extra_fields"] = [k for k in data if k not in REQUIRED_TOP]
    row["missing_fields"] = [k for k in REQUIRED_TOP if k not in data]

    try:
        story = Story.from_dict(data, min_scenes=3)
        row["schema_ok"] = True
    except (StorySchemaError, ValueError, KeyError) as exc:
        row["error"] = str(exc)[:120]
        story = None

    scenes = data.get("scenes") or []
    row["scenes"] = len(scenes)
    # 结构合规 = 字段齐全 + 无多余字段 + 场景数符合要求（本实验要求 4 幕）
    row["scene_ok"] = (len(scenes) == 4)
    row["strict_ok"] = bool(row["schema_ok"] and not row["extra_fields"] and row["scene_ok"])
    if not row["scene_ok"]:
        row["error"] = (row["error"] + "；" if row["error"] else "") + \
            f"场景数 {len(scenes)}，要求 4"
    if row["extra_fields"]:
        row["error"] = (row["error"] + "；" if row["error"] else "") + \
            f"多出字段 {row['extra_fields']}"
    if scenes:
        row["avg_len"] = round(sum(len(s.get("text", "")) for s in scenes) / len(scenes), 1)
        row["dialogue"] = round(
            sum(dialogue_ratio(s.get("text", "")) for s in scenes) / len(scenes), 2)
        vps = [s.get("visual_prompt", "") for s in scenes]
        row["vp_cjk"] = round(sum(cjk_ratio(v) for v in vps) / len(vps), 2)
        row["vp_full"] = sum(1 for v in vps if any(h in v for h in STYLE_HINTS)
                             and len(v) >= 40)
        chars = data.get("characters") or []
        row["appearance"] = sum(1 for c in chars
                                if any(h in (c.get("appearance") or "") for h in APPEARANCE_HINTS))
        row["char_match"] = any(c.get("name") == "阿木" for c in chars)

    return row


def main() -> None:
    rows = [evaluate_one(p) for p in sorted(RAW_DIR.glob("*.raw.txt"))]
    print(f"{'策略':<22}{'JSON':<6}{'解析':<6}{'严格合规':<10}{'场景':<6}{'均长':<7}"
          f"{'对白':<7}{'vp中文':<8}{'vp完整':<8}{'备注'}")
    for r in rows:
        print(f"{r['label']:<22}{'✓' if r['json_ok'] else '✗':<6}"
              f"{'✓' if r['schema_ok'] else '✗':<6}"
              f"{'✓' if r.get('strict_ok') else '✗':<10}{r['scenes']:<6}{r['avg_len']:<7}"
              f"{r['dialogue']:<7}{r['vp_cjk']:<8}{r['vp_full']}/{r['scenes']:<6}{r['error']}")

    out = ROOT / "data" / "prompt_eval.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
