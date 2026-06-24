"""Generate the VCDS Toolkit application icon (installer/app.ico).

A boost-gauge motif in the DeltaModTech palette: a navy rounded-square tile, a
Delta-blue gauge arc with tick marks, and a teal needle swept up toward boost.

Rendered large and down-sampled into a multi-resolution .ico. Re-run after any
tweak:

    python scripts/make_icon.py
"""

from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw

NAVY = (26, 26, 46, 255)      # #1A1A2E  background tile
BLUE = (0, 102, 204, 255)     # #0066CC  Delta blue gauge
TEAL = (0, 201, 167, 255)     # #00C9A7  needle
LIGHT = (231, 237, 246, 255)  # tick marks / hub highlight


def render(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded-square background tile.
    m = int(size * 0.045)
    d.rounded_rectangle([m, m, size - m, size - m], radius=int(size * 0.21), fill=NAVY)

    cx = cy = size / 2.0
    R = size * 0.34

    # Gauge arc — bottom ~270°, open at the top (PIL angles: 0=right, 90=down).
    bbox = [cx - R, cy - R, cx + R, cy + R]
    d.arc(bbox, start=315, end=585, fill=BLUE, width=int(size * 0.058))

    # Tick marks around the arc.
    for ang in range(315, 586, 30):
        a = math.radians(ang)
        r1, r2 = R - size * 0.018, R - size * 0.08
        d.line(
            [cx + r1 * math.cos(a), cy + r1 * math.sin(a),
             cx + r2 * math.cos(a), cy + r2 * math.sin(a)],
            fill=LIGHT, width=max(2, int(size * 0.012)),
        )

    # Needle swept up-and-right (an "active" reading), drawn as a tapered blade.
    na = math.radians(305)
    L = R * 0.95
    nx, ny = cx + L * math.cos(na), cy + L * math.sin(na)
    ta = math.radians(305 + 180)
    tl = R * 0.30
    tx, ty = cx + tl * math.cos(ta), cy + tl * math.sin(ta)
    perp = na + math.pi / 2
    w = size * 0.024
    px, py = math.cos(perp) * w, math.sin(perp) * w
    d.polygon([(nx, ny), (cx + px, cy + py), (tx, ty), (cx - px, cy - py)], fill=TEAL)

    # Center hub.
    hub = size * 0.05
    d.ellipse([cx - hub, cy - hub, cx + hub, cy + hub], fill=LIGHT)
    d.ellipse([cx - hub / 2, cy - hub / 2, cx + hub / 2, cy + hub / 2], fill=BLUE)

    return img


def main(preview_path: str | None = None) -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(os.path.dirname(here), "installer")
    os.makedirs(out_dir, exist_ok=True)

    big = render(1024)
    ico_path = os.path.join(out_dir, "app.ico")
    big.save(ico_path, sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)])
    print(f"wrote {ico_path}")

    if preview_path:
        big.resize((256, 256), Image.LANCZOS).save(preview_path)
        print(f"wrote {preview_path}")


if __name__ == "__main__":
    import sys

    main(sys.argv[1] if len(sys.argv) > 1 else None)
