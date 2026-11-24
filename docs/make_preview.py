# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Abhinav Verma
"""Render docs/preview.gif: effects playing on a real keyboard layout (the K510's 84 lamps).

    python docs/make_preview.py
"""
import json
import math
import pathlib
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lumikey import core as k  # noqa: E402
from lumikey.gui import key_rects  # noqa: E402
from tests.test_effects import FakeMusic  # noqa: E402

LAYOUT = [(19.05 * c, 19.05 * r, 0) for r in range(5) for c in range(16)]
EFFECTS = [("galaxy", "Galaxy"), ("aurora", "Aurora"), ("synthwave", "Synthwave"),
           ("fire", "Fire"), ("equalizer", "Equalizer (music)"), ("nebula+ripple", "Nebula + Ripple (typing)")]
KEY, GAP, PAD, LABEL = 22, 4, 18, 30
FPS, SECONDS = 15, 3


def lamps_from(path):
    try:
        raw = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        raw = LAYOUT
    w, h = max(x for x, _, _ in raw), max(y for _, y, _ in raw)
    return [k.Lamp(x, y, x / w, y / h, u) for x, y, u in raw]


def main():
    lamps = lamps_from(sys.argv[1] if len(sys.argv) > 1 else "")
    rects = key_rects(lamps)  # (x, row, width) in keys, real keycap widths
    cols = max(x + w for x, _, w in rects)
    rows = max(r for _, r, _ in rects) + 1
    tw, th = round(cols * (KEY + GAP)) + 2 * PAD, rows * (KEY + GAP) + 2 * PAD + LABEL
    W, H = 2 * tw, 3 * th
    music, scenes = FakeMusic(), []
    for spec, title in EFFECTS:
        base_cls, color, layers = k.parse_spec(spec, None)
        effects = [base_cls(lamps, color), *(cls(lamps, c) for cls, c in layers)]
        for e in effects:
            e.audio = music
        scenes.append((effects, title))
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 15)
    except OSError:
        font = ImageFont.load_default()
    frames = []
    for n in range(FPS * SECONDS):
        t = n / FPS
        music.update(t)
        img = Image.new("RGB", (W, H), (14, 11, 24))
        glow = Image.new("RGB", (W, H), (0, 0, 0))
        d, g = ImageDraw.Draw(img), ImageDraw.Draw(glow)
        for s, (effects, title) in enumerate(scenes):
            ox, oy = (s % 2) * tw, (s // 2) * th
            if n % 6 == 0 and len(effects) > 1:
                for e in effects:
                    e.on_key((n * 37) % len(lamps), t)
            colors = list(effects[0].render(t))
            for layer in effects[1:]:
                layer.apply(colors, t)
            for (kx, row, kw), c in zip(rects, colors):
                rgb = tuple(round(255 * k.clamp(v) ** (1 / 2.2)) for v in c)
                x = ox + PAD + kx * (KEY + GAP)
                y = oy + PAD + LABEL + row * (KEY + GAP)
                w = kw * (KEY + GAP) - GAP
                face = tuple(round(18 + v * 0.85) for v in rgb)
                d.rounded_rectangle([x, y, x + w, y + KEY], 5, fill=face)
                g.rounded_rectangle([x - 2, y - 2, x + w + 2, y + KEY + 2], 7, fill=rgb)
            d.text((ox + PAD, oy + PAD), title, fill=(235, 230, 250), font=font)
        glow = glow.filter(ImageFilter.GaussianBlur(9)).point(lambda v: v * 0.45)
        img = ImageChops.add(img, glow)
        frames.append(img.quantize(colors=96, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE))
    out = pathlib.Path(__file__).parent / "preview.gif"
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=1000 // FPS, loop=0, optimize=True)
    print(f"{out} ({out.stat().st_size // 1024} KB, {W}x{H})")


if __name__ == "__main__":
    main()
