"""作业5：儿童故事素材库 + 多模态检索。

检索架构（两级，可降级）：
    首选：Chinese-CLIP（文本塔 + 图像塔共享同一向量空间）
          → 一个自然语言 query 可以同时检索文字素材和图片素材，分数可比
    降级：文本用 TF-IDF 字符 n-gram，图像用分块 HSV 直方图 + 画面亮度
          → 没有 torch 也能跑，只是跨模态可比性差

索引落盘到 data/material_library/index/，避免每次启动重算。
"""
from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..common.config import DATA_DIR, OUTPUT_DIR

logger = logging.getLogger("storyforge.rag")

LIB_DIR = DATA_DIR / "material_library"
INDEX_DIR = LIB_DIR / "index"
IMAGE_DIR = LIB_DIR / "images"
SFX_DIR = LIB_DIR / "sfx"

CLIP_MODEL = "OFA-Sys/chinese-clip-vit-base-patch16"


# ---------------------------------------------------------------- 素材条目
@dataclass
class Item:
    id: str
    kind: str                 # template / image / sfx
    title: str
    text: str                 # 用于建索引的全部文本
    payload: dict = field(default_factory=dict)
    vector: np.ndarray | None = None      # 检索用向量（CLIP 模式下图文同空间）
    vis_vector: np.ndarray | None = None  # 纯视觉向量，用于"以图搜图"

    def brief(self) -> dict:
        d = {"id": self.id, "kind": self.kind, "title": self.title}
        d.update({k: v for k, v in self.payload.items()
                  if k in ("file", "world", "tags", "age", "premise", "desc", "duration")})
        return d


# ---------------------------------------------------------------- 编码器
class ClipEncoder:
    """Chinese-CLIP：文本与图像映射到同一空间。"""

    name = "chinese-clip"

    def __init__(self) -> None:
        import torch
        from transformers import ChineseCLIPModel, ChineseCLIPProcessor

        self.torch = torch
        self.model = ChineseCLIPModel.from_pretrained(CLIP_MODEL)
        self.proc = ChineseCLIPProcessor.from_pretrained(CLIP_MODEL)
        self.model.eval()

    def text(self, texts: list[str]) -> np.ndarray:
        inputs = self.proc(text=texts, return_tensors="pt", padding=True,
                           truncation=True, max_length=64)
        with self.torch.no_grad():
            feat = self.model.get_text_features(**inputs)
        return _l2(feat.numpy())

    def image(self, paths: list[Path]) -> np.ndarray:
        from PIL import Image

        out = []
        for p in paths:
            im = Image.open(p).convert("RGB")
            inputs = self.proc(images=im, return_tensors="pt")
            with self.torch.no_grad():
                feat = self.model.get_image_features(**inputs)
            out.append(feat.numpy()[0])
        return _l2(np.stack(out))


class TfidfEncoder:
    """降级方案：字符 n-gram TF-IDF + 分块 HSV 直方图。"""

    name = "tfidf+color"

    def __init__(self) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 3),
                                   min_df=1, sublinear_tf=True)

    def fit_text(self, corpus: list[str]) -> None:
        self.vec.fit(corpus)

    def text(self, texts: list[str]) -> np.ndarray:
        return _l2(self.vec.transform(texts).toarray())

    def image(self, paths: list[Path]) -> np.ndarray:
        return _l2(np.stack([_hsv_feature(p) for p in paths]))


def _l2(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.maximum(n, 1e-9)


def _hsv_feature(path: Path, bins: int = 8) -> np.ndarray:
    """3x3 分块 HSV 直方图 + 全局亮度/饱和度。捕捉"色调+构图"这类弱语义特征。"""
    from PIL import Image

    im = Image.open(path).convert("RGB").resize((144, 144))
    hsv = np.asarray(im.convert("HSV"), dtype=np.float32) / 255.0
    feats = []
    for i in range(3):
        for j in range(3):
            blk = hsv[i * 48:(i + 1) * 48, j * 48:(j + 1) * 48]
            h, _ = np.histogram(blk[..., 0], bins=bins, range=(0, 1), density=True)
            s, _ = np.histogram(blk[..., 1], bins=4, range=(0, 1), density=True)
            feats.append(np.concatenate([h, s]))
    feats.append(np.array([hsv[..., 2].mean(), hsv[..., 1].mean(),
                           hsv[..., 2].std(), hsv[..., 0].mean()]))
    return np.concatenate(feats).astype(np.float32)


# ---------------------------------------------------------------- 素材库
class MaterialLibrary:
    def __init__(self, encoder=None) -> None:
        self.items: list[Item] = []
        self.encoder = encoder
        self.multimodal = isinstance(encoder, ClipEncoder)
        self._img_matrix: np.ndarray | None = None
        self._txt_matrix: np.ndarray | None = None

    # -- 装载 ----------------------------------------------------------
    def load(self) -> "MaterialLibrary":
        self.items = []
        tpl_path = LIB_DIR / "templates.json"
        if tpl_path.exists():
            for t in json.loads(tpl_path.read_text(encoding="utf-8")):
                # 标题/世界/标签重复几次，等价于给这些字段加权
                text = " ".join([
                    t["title"]] * 3 + [t["world"]] * 3 + [" ".join(t["tags"])] * 2
                    + [t["premise"], " ".join(t["beats"])])
                self.items.append(Item(f"TPL-{t['id'][-3:]}", "template", t["title"],
                                       text, t))
        img_index = LIB_DIR / "images.json"
        if img_index.exists():
            for im in json.loads(img_index.read_text(encoding="utf-8")):
                text = " ".join([im["caption"]] * 2 + [im.get("world", "")] * 2
                                + [" ".join(im.get("tags", []))] * 2)
                self.items.append(Item(im["id"], "image", im["caption"], text, im))
        sfx_index = LIB_DIR / "sfx.json"
        if sfx_index.exists():
            for s in json.loads(sfx_index.read_text(encoding="utf-8")):
                text = " ".join([s["name"], s["desc"], " ".join(s.get("tags", []))])
                self.items.append(Item(s["id"], "sfx", s["name"], text, s))
        logger.info("素材库装载：%d 条", len(self.items))
        return self

    # -- 建索引 --------------------------------------------------------
    def build_index(self, force: bool = False) -> "MaterialLibrary":
        cache = INDEX_DIR / f"{self.encoder.name}.pkl"
        if cache.exists() and not force:
            data = pickle.loads(cache.read_bytes())
            if len(data["items"]) == len(self.items):
                for it, v, vv in zip(self.items, data["vectors"], data.get("vis") or [None] * len(self.items)):
                    it.vector = v
                    it.vis_vector = vv
                if isinstance(self.encoder, TfidfEncoder):
                    # 向量化器不进缓存，重新 fit 一次（35 条文本，毫秒级）
                    self.encoder.fit_text([it.text for it in self.items])
                self._split()
                logger.info("复用索引缓存 %s", cache.name)
                return self

        texts = [it.text for it in self.items]
        if isinstance(self.encoder, TfidfEncoder):
            self.encoder.fit_text(texts)
        txt_vecs = self.encoder.text(texts)
        for it, v in zip(self.items, txt_vecs):
            it.vector = v

        img_items = [it for it in self.items if it.kind == "image"]
        if img_items:
            paths = [IMAGE_DIR / it.payload["file"] for it in img_items]
            img_vecs = self.encoder.image(paths)
            for it, v in zip(img_items, img_vecs):
                it.vis_vector = v
                if self.multimodal:
                    # CLIP：图文同空间，直接用视觉向量做跨模态检索
                    it.vector = v

        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(
            {"items": self.items,
             "vectors": [it.vector for it in self.items],
             "vis": [it.vis_vector for it in self.items]}))
        self._split()
        return self

    def _split(self) -> None:
        t = [it for it in self.items if it.kind != "image"]
        i = [it for it in self.items if it.kind == "image"]
        self._txt_items, self._txt_matrix = t, (np.stack([x.vector for x in t]) if t else None)
        self._img_items, self._img_matrix = i, (np.stack([x.vector for x in i]) if i else None)

    # -- 检索 ----------------------------------------------------------
    def search(self, query: str, top_k: int = 6, kind: str | None = None) -> list[dict]:
        q = self.encoder.text([query])
        results = []

        pools = []
        if kind in (None, "template"):
            pools.append(("text", self._txt_items, self._txt_matrix))
        if kind in (None, "image"):
            pools.append(("image", self._img_items, self._img_matrix))

        for tag, items, mat in pools:
            if mat is None:
                continue
            sims = (mat @ q[0])
            order = np.argsort(-sims)[:top_k]
            for idx in order:
                it = items[int(idx)]
                if kind is None and it.kind == "sfx":
                    continue
                results.append({**it.brief(), "score": round(float(sims[int(idx)]), 4),
                                "modality": tag})
        results.sort(key=lambda r: -r["score"])
        return results[:top_k]

    def similar_images(self, image_id: str, top_k: int = 5) -> list[dict]:
        """以图搜图：只看视觉特征，不看文字。"""
        target = next((it for it in self.items if it.id == image_id), None)
        if target is None or target.vis_vector is None:
            return []
        out = []
        for it in self.items:
            if it.kind != "image" or it.id == image_id or it.vis_vector is None:
                continue
            out.append({**it.brief(), "score": round(float(it.vis_vector @ target.vis_vector), 4),
                        "modality": "image"})
        out.sort(key=lambda r: -r["score"])
        return out[:top_k]

    # -- 结构化建议 ----------------------------------------------------
    def recommend(self, idea: str, top_k: int = 5) -> dict:
        hits = self.search(idea, top_k=top_k)
        tpl = next((h for h in hits if h["kind"] == "template"), None)
        imgs = [h for h in hits if h["kind"] == "image"][:3]

        sfx_hits = []
        if tpl:
            for sid in tpl.get("sfx", []):
                for it in self.items:
                    if it.kind == "sfx" and it.payload.get("key") == sid:
                        sfx_hits.append(it.brief())
                        break

        suggestion = {
            "input_idea": idea,
            "matched_template": tpl,
            "reference_images": imgs,
            "sfx": sfx_hits,
            "next_step": None,
        }
        if tpl:
            suggestion["next_step"] = {
                "story_request": {
                    "protagonist": "（由孩子姓名替换）",
                    "age": 7,
                    "scene": tpl.get("world"),
                    "theme": tpl["title"],
                    "length": "中",
                    "audience_age": tpl.get("age"),
                },
                "scene_plan": tpl.get("beats", []),
                "style_prompt": tpl.get("visual_style"),
                "characters": tpl.get("characters"),
            }
        return suggestion


# ---------------------------------------------------------------- 装配
def build_library(prefer_clip: bool = True) -> MaterialLibrary:
    encoder = None
    if prefer_clip:
        try:
            encoder = ClipEncoder()
            logger.info("使用 Chinese-CLIP 编码器")
        except Exception as exc:  # noqa: BLE001
            logger.warning("CLIP 不可用（%s），降级到 TF-IDF + HSV", type(exc).__name__)
    if encoder is None:
        encoder = TfidfEncoder()
    lib = MaterialLibrary(encoder).load()
    lib.build_index()
    return lib


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    lib = build_library()
    for q in ["太空冒险", "怕黑的孩子和灯笼", "海底会发光的珊瑚"]:
        print(f"\n=== {q} ===")
        for r in lib.search(q, top_k=4):
            print(f"  {r['score']:.3f}  [{r['kind']}] {r['title']}")
