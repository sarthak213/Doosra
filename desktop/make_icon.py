"""
Build desktop/assets/doosra.ico (16-256 px) from frontend/public/favicon.svg.

The SVG is drawn by headless Microsoft Edge (part of Windows) at 1024 px with a
transparent background, then Pillow scales it down for each icon size. The .ico
is committed, so this only runs when the logo changes:

    python desktop/make_icon.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
SVG = HERE.parent / "frontend" / "public" / "favicon.svg"
OUT = HERE / "assets" / "doosra.ico"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
RENDER = 1024
PITCH = "#0f1e16"                                 # the app's background (--pitch in frontend/src/index.css)
EDGE = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")]


def render(svg: Path, size: int, out: Path) -> None:
    edge = next((p for p in EDGE if p.exists()), None)
    if not edge:
        sys.exit("Microsoft Edge not found")
    page = out.with_suffix(".html")
    page.write_text(f"<html><body style='margin:0;background:transparent'>"
                    f"<img src='{svg.as_uri()}' width='{size}' height='{size}' style='display:block'></body></html>",
                    encoding="utf-8")
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([str(edge), "--headless", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={profile}",
                        "--default-background-color=00000000", f"--window-size={size},{size}",
                        f"--screenshot={out}", page.as_uri()], check=True, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "ball.png"
        render(SVG, RENDER, png)
        big = Image.open(png).convert("RGBA")
    if big.getpixel((0, 0))[3] != 0:
        sys.exit("the render has a background; expected a transparent corner")
    OUT.parent.mkdir(exist_ok=True)
    big.resize((256, 256), Image.LANCZOS).save(OUT, sizes=[(s, s) for s in SIZES])
    big.resize((512, 512), Image.LANCZOS).save(OUT.with_suffix(".png"))   # for the installer and README
    for px in (55, 110):                                                   # the installer's header (no alpha: white)
        tile = Image.new("RGB", (px, px), "white")
        ball = big.resize((px, px), Image.LANCZOS)
        tile.paste(ball, mask=ball)
        tile.save(OUT.with_name(f"wizard-small-{px}.bmp"))
    for scale in (1, 2):                                                   # the welcome and finish pages' side panel
        w, h = 164 * scale, 314 * scale
        panel = Image.new("RGB", (w, h), PITCH)
        ball = big.resize((w * 3 // 5,) * 2, Image.LANCZOS)
        panel.paste(ball, ((w - ball.width) // 2, h // 3 - ball.height // 2), mask=ball)
        panel.save(OUT.with_name(f"wizard-large-{w}.bmp"))
    print(f"wrote {OUT} ({', '.join(map(str, SIZES))} px)")


if __name__ == "__main__":
    main()
