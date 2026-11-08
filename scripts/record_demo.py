"""
Record the README demo GIF.

Drives the web UI in headless Chromium, screenshots at a fixed interval while
the agent streams, then stitches the frames with ffmpeg.

    .venv/bin/python -m uvicorn server:app --port 8000   # in one terminal
    .venv/bin/python scripts/record_demo.py              # in another

Needs: pip install playwright && playwright install chromium, and ffmpeg.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FRAMES = ROOT / ".frames"
OUT = ROOT / "docs" / "demo.gif"

URL = "http://127.0.0.1:8000"
PROMPT = "write fizzbuzz.py for 1 to 20, then run it to prove it works"

WIDTH, HEIGHT = 1180, 660
FPS = 5                 # capture rate; also the GIF playback rate
MAX_SECONDS = 70        # give up if the turn never finishes
TAIL_FRAMES = 6         # linger on the finished state
GIF_WIDTH = 900


def main() -> int:
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found")
    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    n = 0

    def shot(page):
        nonlocal n
        page.screenshot(path=str(FRAMES / f"{n:04d}.png"))
        n += 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT},
                                device_scale_factor=1)
        page.goto(URL, wait_until="networkidle")

        for _ in range(FPS):           # hold on the empty state for a second
            shot(page)

        page.click("#input")
        page.type("#input", PROMPT, delay=28)
        shot(page)
        page.press("#input", "Enter")

        # Stream: screenshot on a clock until the turn-end usage line lands.
        deadline = time.time() + MAX_SECONDS
        while time.time() < deadline:
            shot(page)
            if page.locator(".usage").count():
                break
            time.sleep(1 / FPS)
        else:
            print("warning: turn did not finish in time", file=sys.stderr)

        for _ in range(TAIL_FRAMES):
            shot(page)
            time.sleep(1 / FPS)

        # Then open the file the agent just wrote.
        first = page.locator("#files .f").first
        if first.count():
            first.click()
            page.wait_for_timeout(400)
            for _ in range(FPS * 2):
                shot(page)
                time.sleep(1 / FPS)

        browser.close()

    print(f"captured {n} frames")

    palette = FRAMES / "palette.png"
    common = ["-y", "-framerate", str(FPS), "-i", str(FRAMES / "%04d.png")]
    subprocess.run(
        ["ffmpeg", *common, "-vf", f"scale={GIF_WIDTH}:-1:flags=lanczos,palettegen=stats_mode=diff",
         str(palette)], check=True, capture_output=True)
    subprocess.run(
        ["ffmpeg", *common, "-i", str(palette),
         "-lavfi", f"scale={GIF_WIDTH}:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3",
         str(OUT)], check=True, capture_output=True)

    shutil.rmtree(FRAMES)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
