"""作业0 演示视频：真机录屏（Playwright 驱动 Edge）+ 旁白 + BGM。

产物：outputs/video/作业0_故事网页演示.mp4（≤ 2 分钟）

为什么不用"截图拼视频"：作业0 的核心卖点是交互（填表 → 换主题 → 翻页），
静态截图看不出"点一下就变了"。所以用浏览器自动化真跑一遍，直接录。
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.audio_utils import ffmpeg_exe, make_bgm, read_wav, to_mp3, to_wav, write_wav  # noqa: E402
from src.common.config import OUTPUT_DIR  # noqa: E402

PAGE = (ROOT / "src" / "week0_web" / "index.html").as_uri()
RAW_DIR = OUTPUT_DIR / "video" / "_demo_raw"
OUT = OUTPUT_DIR / "video" / "作业0_故事网页演示.mp4"

NARRATION = (
    "这是故事小屋。写下孩子的名字、年龄，还有他最喜欢的事。"
    "然后选一个世界：森林、太空、海底，或者夜晚的城市。"
    "点开始讲故事——左边的画面会跟着故事换，右边是可以一页一页翻的文字。"
    "换一个世界，同一个孩子就有了另一个故事。"
    "整个网页只有一个文件，双击就能打开。"
)


async def record() -> Path:
    from playwright.async_api import async_playwright

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for f in RAW_DIR.glob("*.webm"):
        f.unlink()

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(RAW_DIR),
            record_video_size={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()
        await page.goto(PAGE)
        await page.wait_for_timeout(4000)

        # 1) 填表
        await page.fill("#fName", "", timeout=5000)
        await page.type("#fName", "小满", delay=320)
        await page.wait_for_timeout(900)
        await page.fill("#fAge", "7")
        await page.fill("#fLike", "", timeout=5000)
        await page.type("#fLike", "数星星", delay=300)
        await page.wait_for_timeout(1200)

        # 2) 挨个悬停主题卡
        cards = page.locator(".theme")
        for i in range(await cards.count()):
            await cards.nth(i).hover()
            await page.wait_for_timeout(1000)

        # 3) 选太空，生成
        await cards.nth(1).click()
        await page.wait_for_timeout(1200)
        await page.click("#go")
        await page.wait_for_timeout(5200)

        # 4) 翻三页
        for _ in range(3):
            await page.click("#next")
            await page.wait_for_timeout(4000)

        # 5) 回到首页换世界
        await page.click("#back")
        await page.wait_for_timeout(1800)
        await page.click(".theme:nth-child(1)")
        await page.wait_for_timeout(1400)
        await page.click("#go")
        await page.wait_for_timeout(3600)
        await page.click("#next")
        await page.wait_for_timeout(4000)

        await ctx.close()
        await browser.close()

    webms = sorted(RAW_DIR.glob("*.webm"))
    if not webms:
        raise RuntimeError("没有录到视频")
    return webms[-1]


async def narrate() -> Path:
    import edge_tts

    out = RAW_DIR / "narration.mp3"
    comm = edge_tts.Communicate(NARRATION, "zh-CN-XiaoxiaoNeural", rate="-4%")
    await comm.save(str(out))
    return out


def mix_audio(narration: Path, total: float) -> Path:
    """旁白 + 低音量 BGM，长度对齐到视频时长。"""
    import numpy as np

    from src.common.audio_utils import SR

    n_wav = RAW_DIR / "n.wav"
    to_wav(narration, n_wav)
    n, _ = read_wav(n_wav)

    bgm_wav = RAW_DIR / "bgm.wav"
    make_bgm(total, bgm_wav)
    b, _ = read_wav(bgm_wav)

    size = int(total * SR)
    out = np.zeros(size, dtype=np.float32)
    m = min(len(n), size)
    out[:m] += n[:m] * 1.0
    if len(b) < size:
        b = np.pad(b, (0, size - len(b)))
    out += b[:size] * 0.16
    out /= max(1e-6, np.abs(out).max()) / 0.94
    wav = RAW_DIR / "mixed.wav"
    write_wav(wav, out)
    return to_mp3(wav, RAW_DIR / "mixed.mp3")


def main() -> None:
    webm = asyncio.run(record())
    print(f"录屏：{webm}")

    # webm → mp4（先转，才能读时长）
    tmp_mp4 = RAW_DIR / "screen.mp4"
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(webm),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-r", "25", str(tmp_mp4)], check=True)

    from src.common.audio_utils import probe_duration

    total = probe_duration(tmp_mp4)
    print(f"录屏时长：{total:.1f}s")

    narration = asyncio.run(narrate())
    audio = mix_audio(narration, total)

    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error",
                    "-i", str(tmp_mp4), "-i", str(audio),
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                    "-shortest", "-movflags", "+faststart", str(OUT)], check=True)
    print(f"成片：{OUT}（{OUT.stat().st_size / 1024 / 1024:.1f} MB）")


if __name__ == "__main__":
    main()
