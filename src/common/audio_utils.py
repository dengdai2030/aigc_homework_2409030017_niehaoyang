"""音频工具：编解码、拼接、程序化生成 BGM 与音效。

为什么不直接下素材？
  1) 版权干净，可随作业一起提交；
  2) 参数可复现，"背景音乐"也变成一段可审查的代码；
  3) 顺手产出作业5 素材库需要的 5 种音效。
"""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 24000  # edge-tts 输出为 24kHz 单声道


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


# ---------------------------------------------------------------- 编解码
def to_wav(src: Path, dst: Path, sr: int = SR) -> Path:
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src),
           "-ac", "1", "-ar", str(sr), "-acodec", "pcm_s16le", str(dst)]
    subprocess.run(cmd, check=True)
    return dst


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        data = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    return data, sr


def write_wav(path: Path, data: np.ndarray, sr: int = SR) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(data, -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((pcm * 32767).astype(np.int16).tobytes())
    return path


def to_mp3(src_wav: Path, dst_mp3: Path, bitrate: str = "128k") -> Path:
    dst_mp3.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src_wav),
           "-codec:a", "libmp3lame", "-b:a", bitrate, str(dst_mp3)]
    subprocess.run(cmd, check=True)
    return dst_mp3


def duration_of(path: Path) -> float:
    """不依赖 ffprobe：解码成 wav 后用 wave 模块读帧数，精度到毫秒。"""
    tmp = path.with_suffix(".dur.wav")
    to_wav(path, tmp)
    data, sr = read_wav(tmp)
    tmp.unlink(missing_ok=True)
    return len(data) / sr


def probe_duration(path: Path) -> float:
    """读任意音视频文件的时长（解析 ffmpeg -i 的 stderr，无需 ffprobe）。"""
    import re

    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                          capture_output=True, text=True, errors="ignore")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr)
    if not m:
        raise RuntimeError(f"无法读取时长：{path}")
    h, mi, se = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(se)


def silence(seconds: float, sr: int = SR) -> np.ndarray:
    return np.zeros(int(seconds * sr), dtype=np.float32)


# ---------------------------------------------------------------- 合成基元
def _adsr(n: int, attack: float = 0.01, decay: float = 0.3, sr: int = SR) -> np.ndarray:
    a = max(1, int(attack * sr))
    d = max(1, int(decay * sr))
    env = np.ones(n, dtype=np.float32)
    a = min(a, n)
    env[:a] = np.linspace(0, 1, a)
    tail = min(d, n - a)
    if tail > 0:
        env[a:a + tail] = np.exp(-np.linspace(0, 5, tail))
        env[a + tail:] = 0
    return env


def music_box_note(freq: float, dur: float, amp: float = 0.5, sr: int = SR) -> np.ndarray:
    """八音盒音色：基频 + 两个非整数倍泛音，指数衰减。"""
    n = int(dur * sr)
    t = np.arange(n) / sr
    wave_ = (np.sin(2 * np.pi * freq * t) * 1.0
             + np.sin(2 * np.pi * freq * 2.01 * t) * 0.32
             + np.sin(2 * np.pi * freq * 3.02 * t) * 0.14)
    return (wave_ * _adsr(n, 0.004, dur * 0.9, sr) * amp).astype(np.float32)


def pad_chord(freqs: list[float], dur: float, amp: float = 0.12, sr: int = SR) -> np.ndarray:
    n = int(dur * sr)
    t = np.arange(n) / sr
    w = np.zeros(n, dtype=np.float32)
    for f in freqs:
        w += np.sin(2 * np.pi * f * t + np.random.uniform(0, 6.28))
        w += 0.3 * np.sin(2 * np.pi * f * 1.005 * t)
    w /= max(1, len(freqs))
    env = np.ones(n, dtype=np.float32)
    fade = int(0.6 * sr)
    fade = min(fade, n // 2)
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    return (w * env * amp).astype(np.float32)


def noise(dur: float, amp: float = 0.2, sr: int = SR, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(dur * sr)).astype(np.float32) * amp)


def lowpass(x: np.ndarray, alpha: float = 0.05) -> np.ndarray:
    """一阶 IIR 低通，用来把白噪声磨成风声。"""
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc += alpha * (v - acc)
        y[i] = acc
    return y


# ---------------------------------------------------------------- BGM
PENTATONIC = [261.63, 293.66, 329.63, 392.00, 440.00,
              523.25, 587.33, 659.25, 783.99, 880.00]


def make_bgm(duration: float, out_wav: Path, seed: int = 7) -> Path:
    """生成一段 60-90 BPM 的八音盒摇篮曲，带柔和铺底。"""
    rng = np.random.default_rng(seed)
    total = int((duration + 3) * SR)
    buf = np.zeros(total, dtype=np.float32)

    beat = 0.78
    # 铺底和弦：C - Am - F - G 循环
    chords = [[130.81, 196.00, 261.63], [110.00, 164.81, 220.00],
              [87.31, 174.61, 261.63], [98.00, 196.00, 246.94]]
    t = 0.0
    ci = 0
    while t < duration:
        seg = pad_chord(chords[ci % 4], beat * 4, amp=0.10)
        s = int(t * SR)
        e = min(total, s + len(seg))
        buf[s:e] += seg[: e - s]
        t += beat * 4
        ci += 1

    # 旋律：五声音阶随机游走，强拍落音
    t, idx = 0.0, 5
    while t < duration:
        step = rng.choice([-2, -1, -1, 0, 1, 1, 2])
        idx = int(np.clip(idx + step, 0, len(PENTATONIC) - 1))
        freq = PENTATONIC[idx]
        note = music_box_note(freq, beat * 1.8, amp=0.22)
        s = int(t * SR)
        e = min(total, s + len(note))
        buf[s:e] += note[: e - s]
        if rng.random() < 0.25:  # 偶尔留白，呼吸感
            t += beat * 2
        else:
            t += beat
    buf = buf[: int(duration * SR)]
    buf /= max(1e-6, np.abs(buf).max()) / 0.85
    return write_wav(out_wav, buf)


# ---------------------------------------------------------------- 音效
def make_sfx(name: str, out_wav: Path, seed: int = 1) -> Path:
    rng = np.random.default_rng(seed)
    if name == "wind_chime":
        total = np.zeros(int(4.0 * SR), dtype=np.float32)
        for k in range(7):
            f = rng.choice([1046.5, 1174.7, 1318.5, 1568.0, 1760.0])
            note = music_box_note(float(f), 2.2, amp=0.35)
            s = int(rng.uniform(0, 1.6) * SR)
            e = min(len(total), s + len(note))
            total[s:e] += note[: e - s]
        y = total

    elif name == "night_wind":
        base = lowpass(noise(6.0, 0.9, seed=seed), alpha=0.02)
        mod = 0.5 + 0.5 * np.sin(2 * np.pi * 0.18 * np.arange(len(base)) / SR)
        y = base * mod * 0.5

    elif name == "heartbeat":
        y = np.zeros(int(3.0 * SR), dtype=np.float32)
        for k in range(4):
            s = int((0.2 + k * 0.75) * SR)
            for off, amp in ((0, 1.0), (0.22, 0.55)):
                n = int(0.18 * SR)
                t = np.arange(n) / SR
                thump = np.sin(2 * np.pi * 62 * t) * np.exp(-t * 26) * amp * 0.55
                ss = s + int(off * SR)
                e = min(len(y), ss + n)
                y[ss:e] += thump[: e - ss]

    elif name == "match_strike":
        burst = noise(0.12, 0.7, seed=seed)
        crack = noise(1.6, 0.12, seed=seed + 1)
        y = np.concatenate([burst * np.linspace(1, 0, len(burst)),
                            crack * np.linspace(0.6, 0, len(crack))])

    elif name == "raindrop":
        y = np.zeros(int(3.0 * SR), dtype=np.float32)
        for _ in range(22):
            n = int(0.09 * SR)
            t = np.arange(n) / SR
            f = rng.uniform(1200, 2600)
            drop = np.sin(2 * np.pi * f * t) * np.exp(-t * 60) * 0.5
            s = int(rng.uniform(0, 2.9) * SR)
            e = min(len(y), s + n)
            y[s:e] += drop[: e - s]

    else:
        raise KeyError(f"未知音效 {name}，可选 wind_chime / night_wind / heartbeat / match_strike / raindrop")

    y = y.astype(np.float32)
    y /= max(1e-6, np.abs(y).max()) / 0.8
    return write_wav(out_wav, y)
