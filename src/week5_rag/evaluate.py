"""作业5：检索效果评估。

做法：人工给 4 个查询标注"应该命中什么"，再让系统跑，算 P@5 / R@5。
标注是人工的，所以这个数字不是"模型自评"，是可复核的。
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.week5_rag.library import build_library  # noqa: E402

# 查询 → 人工标注的相关素材（id）
CASES = [
    {
        "query": "太空冒险",
        "relevant": ["TPL-002", "IMG-11", "IMG-12", "IMG-13", "IMG-14"],
        "note": "只要命中太空模板或任意太空插图就算相关",
    },
    {
        "query": "一个怕黑的小女孩和会发光的灯笼",
        "relevant": ["TPL-004", "IMG-02", "IMG-03", "IMG-04", "IMG-05", "IMG-08"],
        "note": "夜晚城市模板 + 灯笼相关插图",
    },
    {
        "query": "海底的珊瑚灯全灭了，鱼都不开心",
        "relevant": ["TPL-003", "IMG-16"],
        "note": "海底模板 + 灰白珊瑚图",
    },
    {
        "query": "雨夜街角那家只卖一分钟的商店",
        "relevant": ["TPL-010", "IMG-08"],
        "note": "时间商店模板 + 雨夜路灯图",
    },
    {
        "query": "换牙的小恐龙躲在蕨类丛里",
        "relevant": ["TPL-005", "IMG-17"],
        "note": "恐龙模板 + 小恐龙图",
    },
]


def run(prefer_clip: bool = False, k: int = 5) -> dict:
    lib = build_library(prefer_clip=prefer_clip)
    rows = []
    for c in CASES:
        hits = lib.search(c["query"], top_k=k)
        got = [h["id"] for h in hits]
        rel = set(c["relevant"])
        tp = len([g for g in got if g in rel])
        rows.append({
            "query": c["query"],
            "note": c["note"],
            "returned": got,
            "relevant": c["relevant"],
            "hit": [g for g in got if g in rel],
            "miss": [r for r in c["relevant"] if r not in got],
            "P@k": round(tp / k, 2),
            "R@k": round(tp / len(rel), 2),
            "top1": hits[0]["title"] if hits else "",
        })
    n = len(rows)
    summary = {
        "encoder": lib.encoder.name,
        "k": k,
        "items": len(lib.items),
        "P@k_mean": round(sum(r["P@k"] for r in rows) / n, 3),
        "R@k_mean": round(sum(r["R@k"] for r in rows) / n, 3),
        "top1_acc": round(sum(1 for r in rows if r["hit"] and r["hit"][0] == r["returned"][0]) / n, 3),
    }
    return {"summary": summary, "rows": rows}


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    out = run(prefer_clip=False)
    print(f"编码器：{out['summary']['encoder']}　素材 {out['summary']['items']} 条\n")
    print(f"{'查询':<24}{'P@5':<7}{'R@5':<7}{'Top1'}")
    for r in out["rows"]:
        print(f"{r['query'][:22]:<24}{r['P@k']:<7}{r['R@k']:<7}{r['top1']}")
    s = out["summary"]
    print(f"\n平均 P@5 = {s['P@k_mean']}　平均 R@5 = {s['R@k_mean']}　Top1 命中率 = {s['top1_acc']}")

    path = ROOT / "data" / "retrieval_eval.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {path}")


if __name__ == "__main__":
    main()
