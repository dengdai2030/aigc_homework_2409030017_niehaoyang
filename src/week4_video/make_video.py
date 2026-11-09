"""作业4：把「图 + 音频 + 字幕」自动剪成故事短片（方案B 剪辑式）。

为什么选剪辑式而不是生成式？
  - 8 个场景若逐段调视频模型，成本约 ¥30-60 且镜头语言不可控；
  - 已有的绘本插图本身角色一致，用 Ken Burns（缓慢推拉）就能做出"会呼吸的画面"；
  - 全流程可复现：同一份 timeline.json 能重剪出完全一样的片子。

管线：
  timeline.json ─┬─> 每场景时长 ─> ffmpeg zoompan 逐场景出片段
                 └─> 逐句字幕  ─> .ass 字幕文件
  片段 concat + 原声 + 字幕烧录 ─> MP4
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from ..common.audio_utils import ffmpeg_exe
from ..common.config import OUTPUT_DIR

logger = logging.getLogger("storyforge.video")

VIDEO_DIR = OUTPUT_DIR / "video"
TMP = VIDEO_DIR / "_clips"

W, H, FPS = 1920, 1080, 25

SPEAKER_COLOR = {          # ASS 用 &HBBGGRR
    "narrator": "&H00F2F2F2",
    "xiaoman": "&H0090D8FF",
    "adeng": "&H0068C8FF",
    "momo": "&H00C0B0A8",
}


# ---------------------------------------------------------------- 字幕
def _wrap(text: str, n: int = 20) -> str:
    """字幕断行：两行时在标点处尽量对半分，三行以上再贪心填充。"""
    marks = "，。！？；、："
    if len(text) <= n:
        return text
    if len(text) <= 2 * n:
        cuts = [i + 1 for i, c in enumerate(text) if c in marks]
        if cuts:
            best = min(cuts, key=lambda i: abs(i - len(text) / 2))
            if 6 <= best <= len(text) - 4:
                return text[:best] + "\\N" + text[best:]
        return text[:n] + "\\N" + text[n:]

    lines, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= n:
            cut = max((i for i, c in enumerate(cur) if c in marks), default=-1)
            if cut >= n - 7:
                lines.append(cur[: cut + 1])
                cur = cur[cut + 1:]
            else:
                lines.append(cur)
                cur = ""
    if cur:
        lines.append(cur)
    return "\\N".join(lines)


def _ts(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(timeline: dict, out: Path) -> Path:
    title = timeline["title"]
    segs = timeline["segments"]
    total = timeline["total_duration"]

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,SimHei,46,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,0,0,0,0,100,100,0.6,0,1,2.4,1.2,2,140,140,66,1
Style: Title,KaiTi,96,&H00FFF6E0,&H000000FF,&H00182038,&H00000000,0,0,0,0,100,100,4,0,1,3,3,5,80,80,0,1
Style: Moral,KaiTi,54,&H00E8D6B8,&H000000FF,&H00182038,&H00000000,0,0,0,0,100,100,2,0,1,2.6,2,2,180,180,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    # 片头标题
    ev.append(f"Dialogue: 1,{_ts(0.4)},{_ts(4.6)},Title,,0,0,0,,{{\\fad(600,500)}}{title}")
    ev.append(f"Dialogue: 1,{_ts(1.1)},{_ts(4.6)},Sub,,0,0,0,,"
              f"{{\\fad(600,500)\\fs30\\c&H00E8D6B8&}}刘星宝 · AIGC 应用开发实践 · 第 8 周")

    for s in segs:
        color = SPEAKER_COLOR.get(s["speaker"], "&H00FFFFFF")
        txt = _wrap(s["text"], 20)
        ev.append(
            f"Dialogue: 0,{_ts(s['start'])},{_ts(s['end'])},Sub,,0,0,0,,"
            f"{{\\fad(140,140)\\c{color}}}{txt}"
        )

    # 片尾寓意
    ev.append(f"Dialogue: 1,{_ts(total + 0.4)},{_ts(total + 6.5)},Moral,,0,0,0,,"
              f"{{\\fad(700,700)}}{_wrap(timeline.get('moral', ''), 16)}")

    out.write_text(head + "\n".join(ev) + "\n", encoding="utf-8-sig")
    return out


# ---------------------------------------------------------------- 片段
def scene_ranges(timeline: dict) -> list[tuple[int, float, float]]:
    """把逐句时间轴合并成场景区间（首句起点 → 末句终点 + 尾巴）。"""
    ranges: dict[int, list[float]] = {}
    for s in timeline["segments"]:
        r = ranges.setdefault(s["scene"], [s["start"], s["end"]])
        r[0] = min(r[0], s["start"])
        r[1] = max(r[1], s["end"])
    out = []
    keys = sorted(ranges)
    for i, k in enumerate(keys):
        a, b = ranges[k]
        # 场景之间留 0.35s 交叉，避免硬切；最后一个场景留 3s 给片尾
        end = (ranges[keys[i + 1]][0] + 0.25) if i + 1 < len(keys) else b + 5.0
        out.append((k, a, max(0.6, end - a)))
    return out


def make_clip(img: Path, dur: float, out: Path, mode: int = 0) -> Path:
    """单场景 Ken Burns：偶数镜推近，奇数镜拉远并轻微平移。"""
    frames = max(2, int(dur * FPS))
    if mode % 2 == 0:
        z = f"min(1+{0.14 / frames:.8f}*on,1.14)"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    else:
        z = f"max(1.14-{0.14 / frames:.8f}*on,1.0)"
        x = f"(iw-iw/zoom)*on/{frames}"
        y = "ih/2-(ih/zoom/2)"

    vf = (f"scale={int(W * 1.34)}:-2,"
          f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={FPS},"
          f"format=yuv420p")
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error",
           "-loop", "1", "-i", str(img), "-t", f"{dur:.3f}",
           "-vf", vf, "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "20", "-pix_fmt", "yuv420p", str(out)]
    subprocess.run(cmd, check=True)
    return out


def render(timeline_path: Path, image_dir: Path, audio: Path,
           out_mp4: Path, keep_clips: bool = False, img_prefix: str = "") -> Path:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    timeline = json.loads(Path(timeline_path).read_text(encoding="utf-8"))

    ass = build_ass(timeline, TMP / "subs.ass")
    ranges = scene_ranges(timeline)
    logger.info("场景区间：%s", [(k, round(d, 1)) for k, _, d in ranges])

    clips = []
    for i, (scene_id, start, dur) in enumerate(ranges):
        img = image_dir / f"{img_prefix}scene{scene_id:02d}.png"
        if not img.exists():
            raise FileNotFoundError(f"缺少场景图 {img}")
        clip = TMP / f"clip{i:02d}.mp4"
        make_clip(img, dur, clip, mode=i)
        clips.append(clip)
        logger.info("片段 %d（第 %d 幕）%.1fs", i, scene_id, dur)

    lst = TMP / "list.txt"
    lst.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")

    # 在片段目录内执行，避免 Windows 中文路径在 concat 里转义出错
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "concat", "-safe", "0", "-i", lst.name,
           "-i", str(audio),
           "-vf", "ass=subs.ass",
           "-c:v", "libx264", "-preset", "medium", "-crf", "21",
           "-c:a", "aac", "-b:a", "192k", "-shortest",
           "-movflags", "+faststart", str(out_mp4.resolve())]
    subprocess.run(cmd, check=True, cwd=str(TMP))
    if not keep_clips:
        for c in clips:
            c.unlink(missing_ok=True)
    logger.info("视频完成：%s", out_mp4)
    return out_mp4
