"""全局配置：模型、路径、成本参数。

所有密钥只从环境变量 / .env 读取，绝不硬编码进仓库。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "outputs"
CACHE_DIR = ROOT / ".cache"
LOG_DIR = ROOT / "logs"

for _d in (DATA_DIR, OUTPUT_DIR, CACHE_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_dotenv() -> None:
    """极简 .env 解析，避免额外依赖。已存在的环境变量优先。"""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()


@dataclass
class LLMConfig:
    provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "replay"))
    base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"))
    api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-chat"))
    temperature: float = 0.8
    max_tokens: int = 4096
    timeout: int = 90
    max_retries: int = 3
    # 单位：元 / 千 token（仅用于本地成本估算，不代表官方报价）
    price_in_per_1k: float = 0.001
    price_out_per_1k: float = 0.002


@dataclass
class ImageConfig:
    provider: str = field(default_factory=lambda: os.getenv("IMAGE_PROVIDER", "manifest"))
    base_url: str = field(default_factory=lambda: os.getenv("IMAGE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1"))
    api_key: str = field(default_factory=lambda: os.getenv("IMAGE_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("IMAGE_MODEL", "wanx2.1-t2i-turbo"))
    size: str = "1024*1024"
    # 角色一致性：固定 seed + 统一角色特征卡
    seed: int = 20261028
    price_per_image: float = 0.14


@dataclass
class TTSConfig:
    provider: str = field(default_factory=lambda: os.getenv("TTS_PROVIDER", "edge"))
    voice_map: dict = field(
        default_factory=lambda: {
            "narrator": "zh-CN-XiaoxiaoNeural",
            "xiaoman": "zh-CN-XiaoyiNeural",
            "adeng": "zh-CN-YunxiNeural",
            "momo": "zh-CN-YunjianNeural",
        }
    )


LLM = LLMConfig()
IMAGE = ImageConfig()
TTS = TTSConfig()
