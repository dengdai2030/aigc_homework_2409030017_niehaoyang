"""作业6：交互式多模态故事 Agent。

一个最小但完整的 Agent 循环：

    observe(创意) → plan(拆步骤) → act(调工具) → observe(结果) → replan
                                              ↑                │
                                              └── 用户干预 ────┘

关键设计：
1. 规划器可插拔：`LLMPlanner`（在线，让模型自己拆步骤）/ `RulePlanner`（离线，固定 DAG）。
2. 产物有依赖图：改一个字段只重跑下游，不重做已完成的图。
3. 全程留痕：每一步的工具名、入参、耗时、产物都写进 trace，可直接当"创作流程日志"交。
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..common.config import DATA_DIR, OUTPUT_DIR
from .tools import ToolRegistry, default_registry

logger = logging.getLogger("storyforge.agent")

TRACE_DIR = OUTPUT_DIR / "traces"


@dataclass
class Step:
    idx: int
    tool: str
    args: dict
    status: str = "pending"          # pending / running / done / failed / skipped
    result: dict = field(default_factory=dict)
    error: str = ""
    elapsed: float = 0.0

    def summary(self) -> str:
        if self.status == "done":
            keys = [k for k in self.result if k not in ("story_file",)]
            return "、".join(f"{k}={_short(self.result[k])}" for k in keys[:3])
        return self.error or self.status


def _short(v: Any, n: int = 60) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[: n - 1] + "…"


@dataclass
class CaseSpec:
    """一个完整的创作案例配置。"""
    name: str
    idea: str
    request: dict
    story_file: str
    script_file: str
    image_source: str
    image_prefix: str          # 输出文件名前缀（案例2 用"案例2"）
    tag: str
    src_prefix: str = ""       # 素材库里的文件名前缀（如 space）


# ---------------------------------------------------------------- 规划器
class RulePlanner:
    """离线规划：固定 DAG，但参数依赖上一步的检索结果。"""

    name = "rule"

    def plan(self, case: CaseSpec) -> list[Step]:
        return [
            Step(0, "search_materials", {"query": case.idea, "top_k": 5}),
            Step(1, "write_story", {"request": case.request, "story_file": case.story_file}),
            Step(2, "illustrate", {"story_file": "", "source_dir": case.image_source,
                                   "src_prefix": case.src_prefix,
                                   "out_prefix": case.image_prefix}),
            Step(3, "voice", {"script_file": case.script_file, "tag": case.tag}),
            Step(4, "compose_video", {"story_file": "", "timeline_file": "",
                                      "tag": case.tag, "img_prefix": case.image_prefix}),
            Step(5, "build_book", {"story_file": "", "tag": case.tag}),
        ]


class LLMPlanner(RulePlanner):
    """在线规划：把工具清单交给模型，让它给出步骤序列（JSON）。"""

    name = "llm"

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def plan(self, case: CaseSpec) -> list[Step]:
        from ..common.config import LLM
        from ..common.llm import LLMClient

        if LLM.provider == "replay" or not LLM.api_key:
            logger.warning("未配置 LLM，回退到规则规划器")
            return super().plan(case)

        system = ("你是一个多模态内容生产 Agent 的规划器。只输出 JSON："
                  '{"steps":[{"tool":"工具名","reason":"为什么"}]}')
        user = (f"可用工具：\n{json.dumps(self.registry.schemas(), ensure_ascii=False)}\n\n"
                f"用户创意：{case.idea}\n"
                "请给出从创意到「结构化故事+配图+配音+短视频+绘本」的最少步骤序列。")
        data = LLMClient().chat_json(system, user, tag="agent:plan")
        steps = []
        for i, s in enumerate(data.get("steps", [])):
            steps.append(Step(i, s["tool"], {}, status="pending"))
        return steps or super().plan(case)


# ---------------------------------------------------------------- Agent
class StoryAgent:
    def __init__(self, registry: ToolRegistry | None = None,
                 planner: RulePlanner | None = None) -> None:
        self.registry = registry or default_registry()
        self.planner = planner or RulePlanner()
        self.trace: list[dict] = []
        self.state: dict[str, Any] = {}
        self.case: CaseSpec | None = None

    # -- 主循环 --------------------------------------------------------
    def run(self, case: CaseSpec) -> dict:
        self.case = case
        self.state = {"idea": case.idea, "request": dict(case.request)}
        steps = self.planner.plan(case)
        self._log("plan", f"规划器={self.planner.name}，共 {len(steps)} 步："
                          + " → ".join(s.tool for s in steps))

        for step in steps:
            self._resolve_args(step)
            self._exec(step)
            if step.status == "failed":
                self._log("abort", f"{step.tool} 失败，终止：{step.error}")
                break
        return self.report()

    def _resolve_args(self, step: Step) -> None:
        """把上一步的产物接到下一步的入参上——这就是 Agent 的"记忆"。"""
        a = step.args
        if step.tool == "illustrate" and not a.get("story_file"):
            a["story_file"] = self.state.get("story_file", "")
        if step.tool in ("compose_video", "build_book") and not a.get("story_file"):
            a["story_file"] = self.state.get("story_file", "")
        if step.tool == "compose_video" and not a.get("timeline_file"):
            a["timeline_file"] = self.state.get("timeline", "")

    def _exec(self, step: Step) -> None:
        tool = self.registry.get(step.tool)
        step.status = "running"
        t0 = time.time()
        self._log("act", f"#{step.idx} {tool.name}({_short(step.args, 90)})")
        try:
            step.result = tool.fn(**step.args) or {}
            step.status = "done"
            for key, val in step.result.items():
                self.state[key] = val
        except Exception as exc:  # noqa: BLE001 —— Agent 必须兜住任何工具异常
            step.status = "failed"
            step.error = f"{type(exc).__name__}: {exc}"
            logger.exception("工具 %s 失败", step.tool)
        step.elapsed = round(time.time() - t0, 2)
        self._log("observe", f"#{step.idx} {step.tool} {step.status} "
                             f"{step.elapsed}s {step.summary()}")
        self.trace.append({**asdict(step), "summary": step.summary()})

    def _log(self, kind: str, msg: str) -> None:
        self.trace.append({"kind": kind, "msg": msg, "ts": round(time.time(), 3)})
        logger.info("[%s] %s", kind, msg)

    # -- 用户干预 ------------------------------------------------------
    def intervene(self, instruction: str, case: CaseSpec | None = None,
                  steps: list[str] | None = None) -> dict:
        """中途干预：解析指令 → 改需求 → 只重跑受影响的工具。"""
        case = case or self.case
        assert case is not None
        before = dict(self.state.get("request", case.request))
        after, changed = self._parse(instruction, before)

        self._log("intervene", f"用户：{instruction}")
        if not changed:
            need_llm = ("这条指令需要重新创作内容（改情节/改结尾），"
                        "当前为 replay 模式（未配置 LLM_API_KEY），无法在线生成。"
                        "配置密钥后可由 LLMPlanner 重跑 write_story。")
            self._log("reject", need_llm)
            return {"ok": False, "changed": {}, "reason": need_llm, "affected": []}

        self.state["request"] = after
        affected = self._affected(before, after)
        self._log("replan", f"变更字段：{changed}　受影响步骤：{affected}")

        # 改名：确定性改写全文，不需要重新"创作"
        if "protagonist" in changed and after["protagonist"] != before["protagonist"]:
            src = Path(self.state["story_file"])
            # 写到副本，保留原始故事不被干预污染
            dst = src.with_name(f"{src.stem}_干预后.json")
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            self.state["story_file"] = str(dst)
            self._rename_in_story(dst, before["protagonist"], after["protagonist"])
            self._log("rewrite", f"全文替换主角名：{before['protagonist']} → {after['protagonist']}"
                                 f"（写入副本 {dst.name}）")

        # 干预后的产物单独命名，不覆盖原案例
        tag = f"{case.tag}干预后"
        for tool_name in affected:
            args = {"tag": tag}
            args.update(self._tool_extra_args(tool_name, case))
            step = Step(99, tool_name, args)
            self._resolve_args(step)
            self._exec(step)
        return {"ok": True, "changed": changed, "affected": affected,
                "output_tag": tag}

    def _tool_extra_args(self, tool: str, case: CaseSpec) -> dict:
        if tool == "voice":
            return {"script_file": case.script_file}
        if tool == "compose_video":
            return {"timeline_file": self.state.get("timeline", ""),
                    "img_prefix": case.image_prefix}
        if tool == "illustrate":
            return {"source_dir": case.image_source, "src_prefix": case.src_prefix,
                    "out_prefix": case.image_prefix}
        return {}

    @staticmethod
    def _parse(ins: str, req: dict) -> tuple[dict, dict]:
        req = dict(req)
        changed: dict[str, str] = {}
        patterns = [
            (r"主角(?:的名字)?(?:改成|叫|换成)[「“\"]?([^\s，,。「」”\"]{1,8})", "protagonist"),
            (r"(?:年龄|岁数)(?:改成|换成)\s*(\d{1,2})", "age"),
            (r"(?:场景|地点|世界)(?:改成|换成|换到)[：:]?[「“\"]?([^\s，,。「」”\"]{1,12})", "scene"),
            (r"主题(?:改成|换成)[「“\"]?([^\s，,。「」”\"]{1,12})", "theme"),
        ]
        for pat, key in patterns:
            m = re.search(pat, ins)
            if m:
                val = m.group(1).strip()
                if key == "age":
                    val = int(val)
                if req.get(key) != val:
                    req[key] = val
                    changed[key] = val
        return req, changed

    @staticmethod
    def _affected(before: dict, after: dict) -> list[str]:
        """只改名字 → 图片可以复用；改了年龄/场景/主题 → 全链路重跑。"""
        if set(after) - set(before) or any(before[k] != after[k] for k in before
                                           if k not in ("protagonist",)):
            return ["write_story", "illustrate", "voice", "compose_video", "build_book"]
        if before.get("protagonist") != after.get("protagonist"):
            return ["voice", "compose_video", "build_book"]
        return []

    @staticmethod
    def _rename_in_story(path: Path, old: str, new: str) -> None:
        raw = path.read_text(encoding="utf-8")
        path.write_text(raw.replace(old, new), encoding="utf-8")

    # -- 报告 ----------------------------------------------------------
    def report(self) -> dict:
        done = [t for t in self.trace if "tool" in t]
        return {
            "case": self.case.name if self.case else "",
            "idea": self.state.get("idea"),
            "steps": len(done),
            "ok": all(t["status"] == "done" for t in done) if done else False,
            "total_elapsed": round(sum(t["elapsed"] for t in done), 1),
            "artifacts": {k: v for k, v in self.state.items()
                          if k in ("story_file", "audio", "video", "book", "timeline")},
        }

    def save_trace(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.trace, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def render_log(self, path: Path) -> Path:
        """把 trace 渲染成人类可读的创作流程日志（Markdown）。"""
        lines = [f"# 创作流程日志 · {self.case.name if self.case else ''}", ""]
        lines.append(f"- 用户创意：{self.state.get('idea')}")
        lines.append(f"- 规划器：{self.planner.name}")
        lines.append("")
        lines.append("| # | 步骤 | 工具 | 耗时 | 状态 | 产物 |")
        lines.append("|---|---|---|---|---|---|")
        for t in self.trace:
            if "tool" not in t:
                continue
            art = t["result"].get("video") or t["result"].get("audio") \
                or t["result"].get("book") or t["result"].get("story_file") \
                or t["result"].get("images") or ""
            if isinstance(art, list):
                art = f"{len(art)} 张"
            lines.append(f"| {t['idx']} | {t['tool']} | {t['tool']} | {t['elapsed']}s | "
                         f"{t['status']} | {Path(str(art)).name if art else ''} |")
        lines.append("")
        lines.append("## 决策与观察")
        for t in self.trace:
            if t.get("kind") in ("plan", "act", "observe", "intervene", "replan", "rewrite"):
                lines.append(f"- **{t['kind']}** {t['msg']}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
