"""作业2：把「图 + 文」排版成绘本。

产出两种形态：
  outputs/book/把光借给你.pdf      —— A4 竖版，可打印
  outputs/book/index.html          —— 网页绘本，支持左右翻页 / 键盘方向键
"""
from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image as RLImage
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from ..common.config import OUTPUT_DIR

FONT_DIR = Path("C:/Windows/Fonts")
INK = HexColor("#3A3330")
ACCENT = HexColor("#C9713C")
MUTED = HexColor("#A89C93")

BOOK_DIR = OUTPUT_DIR / "book"
WEB_DIR = OUTPUT_DIR / "web"


def _register_fonts() -> tuple[str, str]:
    body, title = "Helvetica", "Helvetica-Bold"
    try:
        pdfmetrics.registerFont(TTFont("Kai", str(FONT_DIR / "simkai.ttf")))
        pdfmetrics.registerFont(TTFont("Hei", str(FONT_DIR / "simhei.ttf")))
        body, title = "Kai", "Hei"
    except Exception:  # pragma: no cover
        pass
    return body, title


def _prep(img_path: Path, max_w: int = 1500, quality: int = 88) -> Path:
    """把 PNG 压成体积可控的 JPEG，避免 PDF 动辄几十 MB。"""
    from PIL import Image as PILImage

    cache = BOOK_DIR / "_tmp"
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / (img_path.stem + f"_{max_w}.jpg")
    if out.exists():
        return out
    im = PILImage.open(img_path).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, int(im.height * max_w / im.width)), PILImage.LANCZOS)
    im.save(out, "JPEG", quality=quality, optimize=True)
    return out


def compose_cover(art: Path, title: str, subtitle: str, out: Path) -> Path:
    """把标题画进封面留白区，避免"图一页、字一页"的尴尬。

    顺带裁掉生成工具右下角的角标条（底部 28px）。
    """
    from PIL import Image as PILImage
    from PIL import ImageDraw, ImageFont

    im = PILImage.open(art).convert("RGB")
    im = im.crop((0, 0, im.width, im.height - 44))
    canvas_w = 1400
    im = im.resize((canvas_w, int(im.height * canvas_w / im.width)), PILImage.LANCZOS)

    draw = ImageDraw.Draw(im)
    f_title = ImageFont.truetype(str(FONT_DIR / "simkai.ttf"), 104)
    f_sub = ImageFont.truetype(str(FONT_DIR / "simkai.ttf"), 30)

    def center(text: str, font, y: int, fill: str, spacing: int = 12) -> None:
        widths = [draw.textlength(ch, font=font) for ch in text]
        total = sum(widths) + spacing * (len(text) - 1)
        x = (im.width - total) / 2
        for ch, w in zip(text, widths):
            draw.text((x, y), ch, font=font, fill=fill)
            x += w + spacing

    y0 = int(im.height * 0.07)
    # 深蓝底上用浅色字 + 深色描边，保证任何缩放下都可读
    for dx in (-2, 0, 2):
        for dy in (-2, 0, 2):
            if dx or dy:
                center(title, f_title, y0 + dy, "#1B2544", 12)
    center(title, f_title, y0, "#FFF8E8", 12)
    center(subtitle, f_sub, y0 + 146, "#EFDCBC", 8)

    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=92, optimize=True)
    return out


def build_pdf(story, image_dir: Path, out_pdf: Path,
              cover: Path | None = None, sheet: Path | None = None) -> Path:
    body_font, title_font = _register_fonts()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_pdf), pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=13 * mm, bottomMargin=13 * mm,
        title=story.title, author="刘星宝",
    )
    W, H = A4
    usable_w = W - 28 * mm

    st_h = ParagraphStyle("h", fontName=title_font, fontSize=15.5, leading=23,
                          textColor=ACCENT, spaceBefore=2, spaceAfter=8)
    st_body = ParagraphStyle("b", fontName=body_font, fontSize=15, leading=29,
                             textColor=INK, wordWrap="CJK", firstLineIndent=30)
    st_moral = ParagraphStyle("m", fontName=body_font, fontSize=17, leading=34,
                              textColor=ACCENT, alignment=1, wordWrap="CJK")
    st_meta = ParagraphStyle("meta", fontName=body_font, fontSize=10.5, leading=18,
                             textColor=MUTED, wordWrap="CJK")

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setStrokeColor(HexColor("#E8DDCB"))
        canvas.setLineWidth(0.6)
        canvas.line(14 * mm, 15 * mm, W - 14 * mm, 15 * mm)
        canvas.setFont(body_font, 9)
        canvas.setFillColor(MUTED)
        canvas.drawCentredString(W / 2, 10.5 * mm, f"{doc_.page}")
        canvas.restoreState()

    flow = []

    # ---- 封面（标题已烘焙进图）----
    if cover and cover.exists():
        cover_img = compose_cover(cover, story.title, "文 / 图　刘星宝",
                                  BOOK_DIR / "_tmp" / "cover_titled.jpg")
        cov_h = min(usable_w * 1.5, H - 26 * mm - 18)
        flow.append(RLImage(str(cover_img), width=cov_h * 2 / 3, height=cov_h))
    else:
        flow.append(Spacer(1, 160))
    flow.append(PageBreak())

    # ---- 角色介绍 ----
    if sheet and sheet.exists():
        flow.append(Paragraph("认识一下他们", st_h))
        flow.append(RLImage(str(_prep(sheet, 1400)), width=usable_w, height=usable_w / 1.5))
        flow.append(Spacer(1, 12))
        for c in story.characters:
            flow.append(Paragraph(
                f'<font color="#C9713C"><b>{html.escape(c.name)}</b></font>　'
                f'{html.escape(c.traits)}。{html.escape(c.appearance)}。', st_meta))
            flow.append(Spacer(1, 6))
        flow.append(PageBreak())

    # ---- 正文 ----
    img_h = usable_w / 1.5
    for i, scene in enumerate(story.scenes):
        img = image_dir / f"scene{scene.scene_id:02d}.png"
        if img.exists():
            flow.append(RLImage(str(_prep(img, 1400)), width=usable_w, height=img_h))
        flow.append(Spacer(1, 16))
        flow.append(Paragraph(
            f"第 {scene.scene_id} 幕　{html.escape(scene.description)}", st_h))
        flow.append(Paragraph(html.escape(scene.text), st_body))
        flow.append(Spacer(1, 18))
        flow.append(Paragraph(
            f'<font color="#C9BCB0">— {scene.mood} —</font>',
            ParagraphStyle("p", fontName=body_font, fontSize=9.5, alignment=1)))
        if i < len(story.scenes) - 1:
            flow.append(PageBreak())

    # ---- 寓意页 ----
    flow.append(PageBreak())
    flow.append(Spacer(1, 210))
    flow.append(Paragraph("这个故事想说", st_h))
    flow.append(Spacer(1, 10))
    flow.append(Paragraph(html.escape(story.moral), st_moral))
    flow.append(Spacer(1, 60))
    flow.append(Paragraph("— 完 —", ParagraphStyle("fin", fontName=body_font, fontSize=11,
                                                   textColor=MUTED, alignment=1)))

    doc.build(flow, onFirstPage=footer, onLaterPages=footer)
    return out_pdf


def _web_image(src: Path, out_dir: Path, max_w: int = 1400, quality: int = 86) -> str:
    """网页用的图：压成 JPEG，避免把 20MB 的 PNG 塞进网页目录。"""
    from PIL import Image as PILImage

    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / (src.stem + ".jpg")
    if not dst.exists():
        im = PILImage.open(src).convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, int(im.height * max_w / im.width)), PILImage.LANCZOS)
        im.save(dst, "JPEG", quality=quality, optimize=True)
    return f"images/{dst.name}"


def build_web(story, image_dir: Path, out_dir: Path,
              cover: Path | None = None) -> Path:
    """单文件网页绘本：翻页用 CSS transform，键盘/点击均可操作。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    img_out = out_dir / "images"
    img_out.mkdir(exist_ok=True)

    cover_name = _web_image(image_dir / "scene01.png", img_out)
    if cover and cover.exists():
        titled = img_out / "cover_titled.jpg"
        compose_cover(cover, story.title, "文 / 图　刘星宝", titled)
        cover_name = "images/cover_titled.jpg"

    pages = [{
        "id": s.scene_id,
        "desc": s.description,
        "text": s.text,
        "mood": s.mood,
        "img": _web_image(image_dir / f"scene{s.scene_id:02d}.png", img_out),
    } for s in story.scenes]

    tpl = (Path(__file__).parent / "book_template.html").read_text(encoding="utf-8")
    html_out = (tpl.replace("__TITLE__", html.escape(story.title))
                   .replace("__MORAL__", html.escape(story.moral))
                   .replace("__COVER__", cover_name)
                   .replace("__PAGES__", json.dumps(pages, ensure_ascii=False)))
    out = out_dir / "index.html"
    out.write_text(html_out, encoding="utf-8")
    return out
