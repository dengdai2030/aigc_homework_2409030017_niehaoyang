"""故事数据结构与校验。

模型输出的 JSON 不可信，必须逐字段校验并给出可读的报错，
这样 prompt 迭代时才能快速定位是"没生成"还是"格式错"。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


class StorySchemaError(ValueError):
    pass


@dataclass
class Character:
    name: str
    traits: str
    appearance: str

    @classmethod
    def from_dict(cls, d: dict) -> "Character":
        for k in ("name", "traits", "appearance"):
            if not d.get(k):
                raise StorySchemaError(f"character 缺少字段 {k}: {d}")
        return cls(str(d["name"]).strip(), str(d["traits"]).strip(), str(d["appearance"]).strip())


@dataclass
class Scene:
    scene_id: int
    description: str
    text: str
    mood: str
    visual_prompt: str

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        for k in ("scene_id", "description", "text", "mood", "visual_prompt"):
            if k not in d or d[k] in ("", None):
                raise StorySchemaError(f"scene 缺少字段 {k}: {d}")
        try:
            sid = int(d["scene_id"])
        except (TypeError, ValueError) as exc:
            raise StorySchemaError(f"scene_id 必须是整数，收到 {d['scene_id']!r}") from exc
        return cls(sid, str(d["description"]).strip(), str(d["text"]).strip(),
                   str(d["mood"]).strip(), str(d["visual_prompt"]).strip())


@dataclass
class Story:
    title: str
    characters: list[Character]
    scenes: list[Scene]
    moral: str
    meta: dict[str, Any] = field(default_factory=dict)

    # -- 校验 ----------------------------------------------------------
    def validate(self, min_scenes: int = 4) -> "Story":
        if not self.title.strip():
            raise StorySchemaError("title 为空")
        if not self.characters:
            raise StorySchemaError("characters 为空")
        if len(self.scenes) < min_scenes:
            raise StorySchemaError(f"scenes 只有 {len(self.scenes)} 个，至少需要 {min_scenes} 个")
        ids = [s.scene_id for s in self.scenes]
        if len(set(ids)) != len(ids):
            raise StorySchemaError(f"scene_id 重复：{ids}")
        if not self.moral.strip():
            raise StorySchemaError("moral 为空")
        for s in self.scenes:
            if len(s.text) < 20:
                raise StorySchemaError(f"场景 {s.scene_id} 文本过短（{len(s.text)} 字）")
        return self

    # -- 序列化 --------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict, min_scenes: int = 4) -> "Story":
        if not isinstance(d, dict):
            raise StorySchemaError("顶层不是对象")
        missing = [k for k in ("title", "characters", "scenes", "moral") if k not in d]
        if missing:
            raise StorySchemaError(f"缺少顶层字段：{missing}")
        story = cls(
            title=str(d["title"]).strip(),
            characters=[Character.from_dict(c) for c in d["characters"]],
            scenes=[Scene.from_dict(s) for s in d["scenes"]],
            moral=str(d["moral"]).strip(),
            meta=d.get("meta", {}) or {},
        )
        return story.validate(min_scenes)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "Story":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # -- 便捷属性 ------------------------------------------------------
    @property
    def full_text(self) -> str:
        return "\n\n".join(s.text for s in self.scenes)

    @property
    def char_count(self) -> int:
        return sum(len(s.text) for s in self.scenes)

    def character(self, name: str) -> Character | None:
        for c in self.characters:
            if c.name == name:
                return c
        return None
