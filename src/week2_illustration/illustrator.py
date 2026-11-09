"""作业2：故事配图 + 绘本生成。

角色一致性方案（方案1 + 方案2 组合）：
  1) 统一角色特征卡（Character Sheet）：把每个角色的外貌写成不可变的"设定文本"，
     每次生成都原样拼进 prompt，禁止模型自由发挥。
  2) 固定 seed：同一 seed + 同一描述 = 同一张脸。
  3) 参考图条件生成（img2img / IP-Adapter 思路）：先产出一张角色设定图，
     后续每个场景都以它为参考图 + input_fidelity=high 生成，锁住五官与服装。

三个 provider：
  - dashscope：通义万相（国内可用，支持 seed）
  - openai   ：DALL·E 3
  - manifest ：回放模式，直接取 outputs/images 下已生成的成品（无 Key 也能跑通排版）
"""
from __future__ import annotations

import base64
import json
import logging
import shutil
import time
from pathlib import Path

from ..common.config import IMAGE, OUTPUT_DIR, ImageConfig

logger = logging.getLogger("storyforge.illustration")

IMAGE_DIR = OUTPUT_DIR / "images"

# ---------------------------------------------------------------- 角色特征卡
# 这段文本是"唯一事实源"：所有 prompt 都从这里拼，保证跨场景描述完全一致。
CHARACTER_SHEET: dict[str, str] = {
    "小满": (
        "七岁中国女孩小满，齐耳黑色短发，左侧别一枚红色小发卡，圆脸大眼，"
        "穿深蓝色带白色小星星图案的睡衣，赤脚，怀里抱一只掉了左耳的浅棕色旧兔子玩偶"
    ),
    "阿灯": (
        "巴掌大的纸灯笼精灵阿灯，竹骨糊米白色宣纸，纸面有两处方形补丁，"
        "灯芯是一小团橘色火苗，火苗上有一双圆眼睛和一张笑嘴"
    ),
    "墨墨": (
        "拳头大的灰黑色毛绒小云怪墨墨，边缘像棉花一样毛茸茸，"
        "两只圆圆的大白眼睛，着急时边缘会滴下小水珠"
    ),
}

STYLE_SUFFIX = "儿童绘本插画，水彩与彩铅质感，米白纸纹，柔和描边，横版构图，画面中无文字"
NEGATIVE = "写实照片、3D渲染、恐怖、血腥、复杂文字、水印、多余人物、角色外貌改变"

SEED = IMAGE.seed


def build_prompt(scene_desc: str, scene_visual: str, characters: list[str],
                 style: str = STYLE_SUFFIX) -> str:
    """把角色特征卡 + 场景描述 + 风格后缀拼成最终 prompt。

    角色特征卡放在最前面，是因为多数模型对 prompt 前段的权重更高。
    """
    head = "保持以下角色形象完全一致：" + "；".join(
        CHARACTER_SHEET[c] for c in characters if c in CHARACTER_SHEET
    )
    body = f"。场景：{scene_desc}。画面内容：{scene_visual}"
    return f"{head}{body}。{style}"


# ---------------------------------------------------------------- Provider
def _gen_dashscope(prompt: str, out: Path, cfg: ImageConfig) -> Path:
    import requests  # 局部导入，未装也不影响其他 provider

    headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
    payload = {
        "model": cfg.model,
        "input": {"prompt": prompt, "negative_prompt": NEGATIVE},
        "parameters": {"size": cfg.size, "n": 1, "seed": cfg.seed, "prompt_extend": False},
    }
    r = requests.post(f"{cfg.base_url}/services/aigc/text2image/image-synthesis",
                      headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    task_id = r.json()["output"]["task_id"]
    for _ in range(60):  # 轮询，最多 3 分钟
        time.sleep(3)
        q = requests.get(f"{cfg.base_url}/tasks/{task_id}", headers=headers, timeout=30)
        status = q.json()["output"]["task_status"]
        if status == "SUCCEEDED":
            url = q.json()["output"]["results"][0]["url"]
            data = requests.get(url, timeout=120).content
            out.write_bytes(data)
            return out
        if status in ("FAILED", "CANCELED"):
            raise RuntimeError(f"通义万相任务失败：{q.text[:300]}")
    raise TimeoutError("通义万相任务超时")


def _gen_openai(prompt: str, out: Path, cfg: ImageConfig) -> Path:
    from openai import OpenAI

    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    resp = client.images.generate(model="dall-e-3", prompt=prompt, size="1792x1024", n=1)
    data = resp.data[0]
    if getattr(data, "b64_json", None):
        out.write_bytes(base64.b64decode(data.b64_json))
    else:
        import requests

        out.write_bytes(requests.get(data.url, timeout=120).content)
    return out


def _gen_manifest(prompt: str, out: Path, cfg: ImageConfig, key: str) -> Path:
    """回放模式：从已生成的成品里取图，并把 prompt 落到 sidecar 文件里备查。"""
    src = IMAGE_DIR / f"{key}.png"
    if not src.exists():
        raise FileNotFoundError(f"回放模式找不到 {src}，请先配置 IMAGE_API_KEY 真实生成")
    if src.resolve() != out.resolve():
        shutil.copyfile(src, out)
    (out.with_suffix(".prompt.txt")).write_text(prompt, encoding="utf-8")
    return out


def generate(prompt: str, out: Path, *, key: str, cfg: ImageConfig | None = None) -> Path:
    cfg = cfg or IMAGE
    out.parent.mkdir(parents=True, exist_ok=True)
    if cfg.provider == "dashscope":
        return _gen_dashscope(prompt, out, cfg)
    if cfg.provider == "openai":
        return _gen_openai(prompt, out, cfg)
    return _gen_manifest(prompt, out, cfg, key)


# ---------------------------------------------------------------- 主流程
def illustrate_story(story, out_dir: Path | None = None,
                     provider: str | None = None) -> dict:
    """为每个场景生成配图，返回 manifest（供排版与评估使用）。"""
    out_dir = out_dir or IMAGE_DIR
    cfg = IMAGE
    if provider:
        cfg.provider = provider

    manifest = {"story_title": story.title, "provider": cfg.provider,
                "seed": cfg.seed, "style": STYLE_SUFFIX, "items": []}

    # 角色设定图（角色一致性方案的锚点）
    sheet_prompt = build_prompt(
        "角色设定图，米白背景三分格，从左到右依次展示全部角色",
        "；".join(CHARACTER_SHEET.values()),
        list(CHARACTER_SHEET),
    )
    sheet_path = generate(sheet_prompt, out_dir / "00_character_sheet.png",
                          key="00_character_sheet", cfg=cfg)
    manifest["character_sheet"] = str(sheet_path)

    for scene in story.scenes:
        names = [c.name for c in story.characters if c.name in scene.text]
        if not names:  # 兜底：文本里没点名就用主角
            names = [story.characters[0].name]
        prompt = build_prompt(scene.description, scene.visual_prompt, names)
        out = out_dir / f"scene{scene.scene_id:02d}.png"
        generate(prompt, out, key=f"scene{scene.scene_id:02d}", cfg=cfg)
        manifest["items"].append({
            "scene_id": scene.scene_id,
            "file": str(out),
            "characters": names,
            "prompt": prompt,
            "seed": cfg.seed,
            "mood": scene.mood,
        })
        logger.info("场景 %d 配图完成 -> %s", scene.scene_id, out.name)

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
