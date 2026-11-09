"""统一的 LLM 客户端。

设计要点（对应第8周讲的工程挑战）：
1. 重试 + 指数退避：应对限流与超时。
2. 本地响应缓存：同 prompt 不重复花钱，便于调试与演示。
3. JSON 修复：模型返回带 ```json 围栏 / 前后废话 / 尾逗号时仍能解析。
4. 降级（replay）：无 API Key 时回放已生成的样本，保证 Demo 可运行。
5. 成本计量：按 token 估算花费，写入日志。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import CACHE_DIR, LLM, LLMConfig, LOG_DIR

logger = logging.getLogger("storyforge.llm")


# ---------------------------------------------------------------- JSON 修复
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict[str, Any]:
    """从任意模型输出中抠出第一个合法 JSON 对象。失败抛 ValueError。"""
    if not isinstance(text, str):
        raise ValueError("model output is not text")

    candidates: list[str] = []
    fenced = _JSON_FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))

    # 裸文本：从第一个 { 到最后一个 } 做括号配平扫描
    start = text.find("{")
    if start != -1:
        depth, in_str, esc = 0, False, False
        for i, ch in enumerate(text[start:], start):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break
    candidates.append(text)

    for cand in candidates:
        for fixer in (_no_fix, _strip_trailing_comma):
            try:
                data = json.loads(fixer(cand))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
    raise ValueError(f"无法从模型输出中解析 JSON：{text[:200]!r}")


def _no_fix(s: str) -> str:
    return s.strip()


def _strip_trailing_comma(s: str) -> str:
    s = re.sub(r",(\s*[}\]])", r"\1", s.strip())
    return s.replace("“", '"').replace("”", '"').replace("：", ":")


# ---------------------------------------------------------------- 用量统计
@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hits: int = 0
    cost_cny: float = 0.0
    records: list[dict] = field(default_factory=list)

    def add(self, pt: int, ct: int, cfg: LLMConfig, tag: str = "") -> None:
        self.calls += 1
        self.prompt_tokens += pt
        self.completion_tokens += ct
        cost = pt / 1000 * cfg.price_in_per_1k + ct / 1000 * cfg.price_out_per_1k
        self.cost_cny += cost
        self.records.append({"tag": tag, "prompt": pt, "completion": ct, "cost": round(cost, 5)})

    def summary(self) -> str:
        return (
            f"调用 {self.calls} 次 / 命中缓存 {self.cache_hits} 次 / "
            f"输入 {self.prompt_tokens} tok / 输出 {self.completion_tokens} tok / "
            f"估算成本 ¥{self.cost_cny:.4f}"
        )


# ---------------------------------------------------------------- 客户端
class LLMClient:
    def __init__(self, cfg: LLMConfig | None = None, cache: bool = True) -> None:
        self.cfg = cfg or LLM
        self.use_cache = cache
        self.usage = Usage()
        self._client = None

    # -- 公开方法 ------------------------------------------------------
    def chat(self, system: str, user: str, *, temperature: float | None = None,
             tag: str = "chat", json_mode: bool = False) -> str:
        key = self._cache_key(system, user, temperature)
        if self.use_cache:
            hit = self._read_cache(key)
            if hit is not None:
                self.usage.cache_hits += 1
                logger.info("[cache hit] %s", tag)
                return hit

        if self.cfg.provider == "replay":
            raise RuntimeError(
                "当前为 replay 模式（未配置 LLM_API_KEY）。"
                "请复制 .env.example 为 .env 并填入密钥，或直接使用 data/ 下已生成的样本。"
            )

        text = self._call_with_retry(system, user, temperature, tag, json_mode)
        if self.use_cache:
            self._write_cache(key, text)
        return text

    def chat_json(self, system: str, user: str, *, tag: str = "json",
                  temperature: float | None = None) -> dict[str, Any]:
        last_err: Exception | None = None
        prompt = user
        for attempt in range(2):  # 第一次失败后追加"只输出JSON"再试一次
            text = self.chat(system, prompt, temperature=temperature, tag=tag, json_mode=True)
            try:
                return extract_json(text)
            except ValueError as exc:
                last_err = exc
                logger.warning("[json repair] 第 %d 次解析失败：%s", attempt + 1, exc)
                prompt = user + "\n\n【重要】上一次输出无法被 json.loads 解析。请只输出一个 JSON 对象，不要任何解释、不要 Markdown 代码块。"
        raise ValueError(f"JSON 解析连续失败：{last_err}")

    # -- 内部实现 ------------------------------------------------------
    def _call_with_retry(self, system: str, user: str, temperature: float | None,
                         tag: str, json_mode: bool) -> str:
        client = self._ensure_client()
        last: Exception | None = None
        for attempt in range(self.cfg.max_retries):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.cfg.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": self.cfg.temperature if temperature is None else temperature,
                    "max_tokens": self.cfg.max_tokens,
                    "timeout": self.cfg.timeout,
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = client.chat.completions.create(**kwargs)
                text = resp.choices[0].message.content or ""
                pt = getattr(resp.usage, "prompt_tokens", 0) or 0
                ct = getattr(resp.usage, "completion_tokens", 0) or 0
                self.usage.add(pt, ct, self.cfg, tag)
                logger.info("[%s] ok, %d/%d tok", tag, pt, ct)
                return text
            except Exception as exc:  # noqa: BLE001 —— 需要覆盖网络/限流/格式各类异常
                last = exc
                wait = min(2 ** attempt + 0.5 * attempt, 8)
                logger.warning("[%s] 第 %d 次失败：%s，%.1fs 后重试", tag, attempt + 1, exc, wait)
                time.sleep(wait)
        raise RuntimeError(f"LLM 调用在 {self.cfg.max_retries} 次重试后仍失败：{last}") from last

    def _ensure_client(self):
        if self._client is None:
            if not self.cfg.api_key:
                raise RuntimeError("缺少 LLM_API_KEY，请在 .env 中配置")
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("未安装 openai，请先 pip install openai") from exc
            self._client = OpenAI(api_key=self.cfg.api_key, base_url=self.cfg.base_url)
        return self._client

    def _cache_key(self, system: str, user: str, temperature: float | None) -> str:
        raw = f"{self.cfg.model}|{self.cfg.temperature if temperature is None else temperature}|{system}|{user}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _cache_path(self, key: str) -> Path:
        return CACHE_DIR / f"{key}.json"

    def _read_cache(self, key: str) -> str | None:
        p = self._cache_path(key)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))["text"]
        return None

    def _write_cache(self, key: str, text: str) -> None:
        self._cache_path(key).write_text(
            json.dumps({"text": text, "model": self.cfg.model}, ensure_ascii=False), encoding="utf-8"
        )


def get_client(**kw) -> LLMClient:
    return LLMClient(**kw)
