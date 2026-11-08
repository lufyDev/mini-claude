"""
Record a README GIF.

Drives the web UI in headless Chromium, screenshots at a fixed interval while
the agent streams, then stitches the frames with ffmpeg.

    .venv/bin/python -m uvicorn server:app --port 8000     # in one terminal

    # the hero clip: write a script, run it, recover from a bad interpreter
    .venv/bin/python scripts/record_demo.py --open-file

    # the skills clip: instructions that were not in context arrive on demand
    .venv/bin/python scripts/record_demo.py \
        --out docs/skills.gif \
        --prompt "review primes.py" \
        --expand load_skill

Needs: pip install playwright && playwright install chromium, and ffmpeg.
"""
import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FRAMES = ROOT / ".frames"

URL = "http://127.0.0.1:8000"
WIDTH, HEIGHT = 1180, 548
FPS = 5                 # capture rate; also the GIF playback rate
MAX_SECONDS = 90        # give up if the turn never finishes
TAIL_FRAMES = 6         # linger on the finished state
GIF_WIDTH = 900


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="docs/demo.gif")
    ap.add_argument("--prompt",
                    default="write fizzbuzz.py for 1 to 20, then run it to prove it works")
    ap.add_argument("--expand", default=None,
                    help="after the turn, click open the first tool row with this "
                         "name so its result is visible in the clip")
    ap.add_argument("--open-file", action="store_true",
                    help="after the turn, open the first file in the workspace pane")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    out = (ROOT / args.out).resolve()

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found")
    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)
    out.parent.mkdir(parents=True, exist_ok=True)

    n = 0

    def shot(page):
        nonlocal n
        page.screenshot(path=str(FRAMES / f"{n:04d}.png"))
        n += 1

    def hold(page, frames):
        for _ in range(frames):
            shot(page)
            time.sleep(1 / FPS)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT},
                                device_scale_factor=1)
        page.goto(URL, wait_until="networkidle")

        for _ in range(FPS):           # hold on the starting state for a second
            shot(page)

        page.click("#input")
        page.type("#input", args.prompt, delay=28)
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

        hold(page, TAIL_FRAMES)

        # Open a tool's result, so the clip shows what actually came back.
        if args.expand:
            row = page.locator(".tool", has=page.locator(f".name:text-is('{args.expand}')")).first
            if row.count():
                row.locator(".tool-head").click()
                page.wait_for_timeout(300)
                hold(page, FPS * 3)
            else:
                print(f"warning: no tool row named {args.expand}", file=sys.stderr)

        if args.open_file:
            first = page.locator("#files .f").first
            if first.count():
                first.click()
                page.wait_for_timeout(400)
                hold(page, FPS * 2)

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
         str(out)], check=True, capture_output=True)

    shutil.rmtree(FRAMES)
    print(f"wrote {out} ({out.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
