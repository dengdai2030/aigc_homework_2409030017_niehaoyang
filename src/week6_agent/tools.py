"""作业6：Agent 的工具层。

每个工具都是「纯函数 + 明确 schema」，Agent 只依赖这张表，
因此换模型、换框架（LangChain / LlamaIndex）时工具不用重写。

依赖关系写在 DEPENDS 里：用户中途改需求时，只重跑受影响的下游工具，
不会把已经生成的图片白白重做一遍。
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..common.config import DATA_DIR, OUTPUT_DIR

STORY_DIR = DATA_DIR / "stories"
IMAGE_DIR = OUTPUT_DIR / "images"
AUDIO_DIR = OUTPUT_DIR / "audio"
VIDEO_DIR = OUTPUT_DIR / "video"
BOOK_DIR = OUTPUT_DIR / "book"

# 哪个工具依赖哪些产物 —— 用于"只重跑受影响步骤"
DEPENDS = {
    "search_materials": [],
    "write_story": ["request"],
    "illustrate": ["story"],
    "voice": ["story"],
    "compose_video": ["story", "voice"],
    "build_book": ["story", "illustrate"],
}


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, str]
    fn: Callable[..., dict]
    produces: list[str] = field(default_factory=list)

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description,
                "parameters": self.params, "produces": self.produces}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"未注册的工具：{name}，可用：{list(self._tools)}")
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]


# ---------------------------------------------------------------- 工具实现
def _t_search(query: str, top_k: int = 5) -> dict:
    from ..week5_rag.library import build_library

    lib = build_library(prefer_clip=False)
    return {"query": query, "hits": lib.search(query, top_k=top_k),
            "recommendation": lib.recommend(query)}


def _t_write_story(request: dict, story_file: str | None = None) -> dict:
    """写故事。

    在线：调 LLM（S4 Schema+自检策略）；
    离线：读取 data/stories 下的同名样本（replay），保证 Demo 可复现。
    """
    from ..common.config import LLM
    from ..common.prompts import StoryRequest, build
    from ..common.schema import Story

    if LLM.provider != "replay" and LLM.api_key:
        from ..common.llm import LLMClient

        req = StoryRequest(**request)
        system, user = build("schema_selfcheck", req)
        client = LLMClient()
        story = Story.from_dict(client.chat_json(system, user, tag="agent:story"))
        path = STORY_DIR / "agent_story.json"
        story.save(path)
        return {"story_file": str(path), "source": "llm", "title": story.title}

    if not story_file:
        raise FileNotFoundError("replay 模式必须指定 story_file")
    story = Story.load(STORY_DIR / story_file)
    return {"story_file": str(STORY_DIR / story_file), "source": "replay",
            "title": story.title, "scenes": len(story.scenes)}


def _t_illustrate(story_file: str, source_dir: str, src_prefix: str = "",
                  out_prefix: str = "") -> dict:
    """配图：把已生成的场景图按 story 的场景号落到 outputs/images。

    在线模式下这里是 illustrate_story()（走通义万相/DALL·E）；
    离线模式直接取素材库里的成品，排版与后续流程完全一致。
    """
    from ..common.schema import Story

    story = Story.load(story_file)
    src = Path(source_dir)
    made = []
    for s in story.scenes:
        name = f"{out_prefix}_scene{s.scene_id:02d}.png" if out_prefix else f"scene{s.scene_id:02d}.png"
        dst = IMAGE_DIR / name
        if src_prefix:
            # 案例2 从素材库取图，输出用独立文件名，避免覆盖案例1
            cand = src / f"{src_prefix}{s.scene_id:02d}_{_slug_map(src_prefix, s.scene_id)}.png"
            if not cand.exists():
                cand = src / f"{src_prefix}{s.scene_id:02d}.png"
            if not cand.exists():
                raise FileNotFoundError(f"缺少素材 {cand}")
            shutil.copyfile(cand, dst)
        elif src.resolve() != dst.parent.resolve():
            shutil.copyfile(src / f"scene{s.scene_id:02d}.png", dst)
        made.append(str(dst))
    return {"images": made, "count": len(made)}


def _slug_map(prefix: str, sid: int) -> str:
    return {1: "rocket", 2: "earth", 3: "star", 4: "sandbottle"}.get(sid, "")


def _t_voice(script_file: str, tag: str) -> dict:
    import asyncio

    from ..week3_tts.tts import synthesize_script

    timeline = asyncio.run(synthesize_script(
        STORY_DIR / script_file, bgm=True, timeline_name=f"timeline_{tag}.json"))
    # 按 tag 重命名，避免两个案例互相覆盖
    mp3 = AUDIO_DIR / timeline["audio_file"]
    if tag:
        new = AUDIO_DIR / f"{tag}_{mp3.name}"
        shutil.copyfile(mp3, new)
        timeline["audio_file"] = new.name
    (AUDIO_DIR / f"timeline_{tag}.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"audio": str(AUDIO_DIR / timeline["audio_file"]),
            "duration": timeline["total_duration"],
            "timeline": str(AUDIO_DIR / f"timeline_{tag}.json")}


def _t_compose_video(story_file: str, timeline_file: str, tag: str,
                     img_prefix: str = "") -> dict:
    from ..week4_video.make_video import render

    timeline = json.loads(Path(timeline_file).read_text(encoding="utf-8"))
    out = VIDEO_DIR / f"{tag}_故事短片.mp4"
    prefix = f"{img_prefix}_" if img_prefix else ""
    render(Path(timeline_file), IMAGE_DIR,
           AUDIO_DIR / timeline["audio_file"], out, img_prefix=prefix)
    return {"video": str(out), "duration": timeline["total_duration"]}


def _t_build_book(story_file: str, tag: str) -> dict:
    from ..week2_illustration.book import build_pdf

    from ..common.schema import Story

    story = Story.load(story_file)
    out = BOOK_DIR / f"{tag}_{story.title}.pdf"
    build_pdf(story, IMAGE_DIR, out,
              cover=IMAGE_DIR / "09_cover.png" if tag == "案例1" else None,
              sheet=IMAGE_DIR / "00_character_sheet.png" if tag == "案例1" else None)
    return {"book": str(out), "pages": len(story.scenes) + 2}


def default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(Tool("search_materials", "在儿童故事素材库里做多模态检索，返回模板/配图/音效与结构化建议",
                      {"query": "str", "top_k": "int=5"}, _t_search, ["materials"]))
    reg.register(Tool("write_story", "根据故事参数生成结构化故事 JSON（角色/场景/分镜提示词/寓意）",
                      {"request": "dict", "story_file": "str?"}, _t_write_story, ["story"]))
    reg.register(Tool("illustrate", "为每个场景生成/取用配图，保持角色一致",
                      {"story_file": "str", "source_dir": "str", "src_prefix": "str",
                       "out_prefix": "str"}, _t_illustrate, ["images"]))
    reg.register(Tool("voice", "多角色配音并合成有声书，输出时间轴",
                      {"script_file": "str", "tag": "str"}, _t_voice, ["audio", "timeline"]))
    reg.register(Tool("compose_video", "把配图+配音+字幕自动剪成短片",
                      {"story_file": "str", "timeline_file": "str", "tag": "str",
                       "img_prefix": "str"}, _t_compose_video, ["video"]))
    reg.register(Tool("build_book", "把图文排版成 PDF 绘本",
                      {"story_file": "str", "tag": "str"}, _t_build_book, ["book"]))
    return reg
