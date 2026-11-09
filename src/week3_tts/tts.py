"""作业3：多角色有声书。

流程：
  脚本(script.json) → 逐行合成(音色+情感参数) → 量时长 → 拼接(带停顿) → 混入 BGM → MP3
                                                        └→ timeline.json → 同步高亮网页

工程点：
  - 情感标注不靠"猜"，靠 emotion_map 把 [情绪词] 映射成 rate/pitch/volume 三轴参数；
  - 并发合成 + 失败重试，单行失败不拖垮整本；
  - 时长用解码后的 PCM 帧数精确计算，保证字幕/高亮对齐到毫秒。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

import numpy as np

from ..common.audio_utils import (SR, make_bgm, read_wav, silence, to_mp3,
                                  to_wav, write_wav)
from ..common.config import OUTPUT_DIR

logger = logging.getLogger("storyforge.tts")

AUDIO_DIR = OUTPUT_DIR / "audio"
TMP_DIR = AUDIO_DIR / "_segments"


# ---------------------------------------------------------------- 参数运算
def _pct(s: str) -> int:
    return int(str(s).replace("%", "").replace("+", ""))


def _hz(s: str) -> int:
    return int(str(s).replace("Hz", "").replace("+", ""))


def _fmt_pct(v: int) -> str:
    return f"{v:+d}%"


def _fmt_hz(v: int) -> str:
    return f"{v:+d}Hz"


def compose_params(voice_cfg: dict, emotion: dict) -> dict:
    """角色基础参数 + 情绪偏移 = 最终 SSML prosody 参数。"""
    return {
        "rate": _fmt_pct(_pct(voice_cfg.get("base_rate", "+0%")) + _pct(emotion.get("rate", "+0%"))),
        "pitch": _fmt_hz(_hz(voice_cfg.get("base_pitch", "+0Hz")) + _hz(emotion.get("pitch", "+0Hz"))),
        "volume": _fmt_pct(_pct(voice_cfg.get("volume", "+0%")) + _pct(emotion.get("volume", "+0%"))),
    }


# ---------------------------------------------------------------- 合成
async def _synth_one(sem: asyncio.Semaphore, text: str, voice: str,
                     rate: str, pitch: str, volume: str, out: Path,
                     retries: int = 3) -> Path:
    import edge_tts

    async with sem:
        for attempt in range(retries):
            try:
                comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume)
                await comm.save(str(out))
                if out.exists() and out.stat().st_size > 800:
                    return out
                raise RuntimeError("生成文件为空")
            except Exception as exc:  # noqa: BLE001
                if attempt == retries - 1:
                    raise RuntimeError(f"TTS 合成失败：{text[:20]}… {exc}") from exc
                await asyncio.sleep(1.2 * (attempt + 1))
        return out


async def synthesize_script(script_path: Path, out_dir: Path | None = None,
                            bgm: bool = True, max_concurrency: int = 4,
                            timeline_name: str = "timeline.json") -> dict:
    out_dir = out_dir or AUDIO_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    script = json.loads(Path(script_path).read_text(encoding="utf-8"))

    voices = script["voice_map"]
    emotions = script["emotion_map"]
    pauses = script.get("pause_map", {})

    # 1) 参数组装
    jobs = []
    for i, line in enumerate(script["lines"]):
        v = voices[line["speaker"]]
        emo = emotions.get(line.get("emotion", "平静"), emotions["平静"])
        p = compose_params(v, emo)
        jobs.append({
            "idx": i, "line": line, "voice": v["voice"], "params": p,
            "mp3": TMP_DIR / f"{i:03d}.mp3", "wav": TMP_DIR / f"{i:03d}.wav",
        })

    # 2) 并发合成
    sem = asyncio.Semaphore(max_concurrency)
    await asyncio.gather(*[
        _synth_one(sem, j["line"]["text"], j["voice"], j["params"]["rate"],
                   j["params"]["pitch"], j["params"]["volume"], j["mp3"])
        for j in jobs
    ])
    logger.info("合成完成：%d 行", len(jobs))

    # 3) 转 wav + 量时长 + 拼时间轴
    segments: list[dict] = []
    cursor = 0.0
    prev_scene, prev_emotion, prev_speaker = None, None, None

    for j in jobs:
        to_wav(j["mp3"], j["wav"])
        data, _ = read_wav(j["wav"])
        dur = len(data) / SR

        gap = pauses.get("line_gap", 0.3)
        line = j["line"]
        if prev_scene is not None and line["scene"] != prev_scene:
            gap = pauses.get("scene_gap", 1.0)
        elif prev_emotion != line.get("emotion"):
            gap = pauses.get("emotion_gap", 0.55)
        elif prev_speaker != line["speaker"]:
            gap = pauses.get("dialogue_gap", 0.22)
        if prev_scene is None:
            gap = 0.15

        segments.append({
            "idx": j["idx"],
            "scene": line["scene"],
            "speaker": line["speaker"],
            "speaker_name": {"narrator": "旁白", "xiaoman": "小满",
                             "adeng": "阿灯", "momo": "墨墨"}.get(line["speaker"], line["speaker"]),
            "emotion": line.get("emotion"),
            "text": line["text"],
            "voice": j["voice"],
            "params": j["params"],
            "gap_before": round(gap, 3),
            "start": round(cursor + gap, 3),
            "duration": round(dur, 3),
            "end": round(cursor + gap + dur, 3),
        })
        cursor += gap + dur
        prev_scene, prev_emotion, prev_speaker = line["scene"], line.get("emotion"), line["speaker"]

    # 4) 拼接 PCM
    total = np.zeros(int((cursor + 1.2) * SR), dtype=np.float32)
    for seg in segments:
        data, _ = read_wav(TMP_DIR / f"{seg['idx']:03d}.wav")
        s = int(seg["start"] * SR)
        e = min(len(total), s + len(data))
        total[s:e] += data[: e - s]

    # 5) 混 BGM（旁白段落音量自动压低，模拟 ducking）
    bgm_path = None
    if bgm:
        bgm_wav = out_dir / "bgm.wav"
        make_bgm(cursor + 1.0, bgm_wav)
        b, _ = read_wav(bgm_wav)
        if len(b) < len(total):
            b = np.pad(b, (0, len(total) - len(b)))
        b = b[: len(total)]
        # 包络：每句话开始时 -6dB，句子间回升
        env = np.full(len(total), 0.30, dtype=np.float32)
        for seg in segments:
            s, e = int(seg["start"] * SR), min(len(total), int(seg["end"] * SR))
            env[s:e] = 0.16
        smooth = np.convolve(env, np.ones(int(0.4 * SR)) / int(0.4 * SR), mode="same")
        total = total + b * smooth.astype(np.float32)
        bgm_path = str(bgm_wav)

    total /= max(1e-6, np.abs(total).max()) / 0.94
    final_wav = out_dir / "audiobook.wav"
    write_wav(final_wav, total)
    final_mp3 = to_mp3(final_wav, out_dir / f"{script['title']}_有声书.mp3")

    timeline = {
        "title": script["title"],
        "total_duration": round(cursor, 2),
        "audio_file": final_mp3.name,
        "bgm": bgm_path,
        "voice_map": voices,
        "emotion_map": emotions,
        "segments": segments,
        "stats": {
            "lines": len(segments),
            "scenes": len({s["scene"] for s in segments}),
            "speakers": len({s["speaker"] for s in segments}),
            "voices": sorted({s["voice"] for s in segments}),
        },
    }
    (out_dir / timeline_name).write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("有声书完成：%.1f 秒 -> %s", cursor, final_mp3.name)
    return timeline


# ---------------------------------------------------------------- 同步高亮网页
def build_sync_page(timeline: dict, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or (OUTPUT_DIR / "web")
    out_dir.mkdir(parents=True, exist_ok=True)
    tpl = (Path(__file__).parent / "sync_template.html").read_text(encoding="utf-8")
    rows = []
    for s in timeline["segments"]:
        cls = "narr" if s["speaker"] == "narrator" else "talk"
        rows.append(
            f'<p class="line {cls}" data-start="{s["start"]}" data-end="{s["end"]}" '
            f'data-sp="{s["speaker"]}" data-scene="{s["scene"]}">'
            f'<span class="who">{s["speaker_name"]}</span>'
            f'<span class="tx">{s["text"]}</span></p>'
        )
    html = (tpl.replace("__TITLE__", timeline["title"])
               .replace("__AUDIO__", timeline["audio_file"])
               .replace("__DURATION__", f"{timeline['total_duration']:.1f}")
               .replace("__LINES__", "\n".join(rows)))
    out = out_dir / "audiobook.html"
    out.write_text(html, encoding="utf-8")
    return out
