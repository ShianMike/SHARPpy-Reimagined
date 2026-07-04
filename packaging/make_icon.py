#!/usr/bin/env python3
"""Generate the SHARPpy Reimagined application logo / Windows icon.

Authentic-retro pixel art: the whole mark is hand-placed on a small 32x32
grid and blown up with nearest-neighbour scaling, so it keeps the crisp,
deliberate pixels of an early-90s desktop icon instead of smooth vector curves.
The subject is the skew-T sounding SHARPpy is known for -- a red temperature
trace and a green dewpoint trace stepping up-and-to-the-left over a dark plot.

Flat solid colours, no anti-aliasing, no gradients. Emitted as:

* ``sharpmod/resources/icons/app.ico``  -- multi-resolution Windows icon
* ``sharpmod/resources/icons/app.png``  -- 512 px preview / general-purpose PNG

Run from the repository root::

    python packaging/make_icon.py
"""

from __future__ import annotations

import os

from PIL import Image

# --- pixel grid -------------------------------------------------------------
GRID = 32            # logical pixel canvas (icon is designed at this size)
BASE = 1024          # exported logo size (px); BASE / GRID = 32x nearest scale

# --- palette (limited, retro) ----------------------------------------------
BG = (24, 32, 52)        # plot background (dark slate-blue)
FRAME = (86, 104, 140)   # 1px inset border
GRIDLN = (44, 56, 84)    # faint plot grid
TEMP = (214, 66, 54)     # temperature trace (red)
DEWP = (66, 190, 110)    # dewpoint trace (green)


def _px(grid, x: int, y: int, color) -> None:
    if 0 <= x < GRID and 0 <= y < GRID:
        grid[y][x] = color


def _line(grid, x0: int, y0: int, x1: int, y1: int, color, thick: int = 1):
    """Bresenham line on the pixel grid (optionally 2 px thick)."""
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        _px(grid, x, y, color)
        if thick >= 2:
            _px(grid, x + 1, y, color)
        while True:
            e2 = 2 * err
            if e2 >= dy:
                if x == x1:
                    break
                err += dy
                x += sx
            if e2 <= dx:
                if y == y1:
                    break
                err += dx
                y += sy
            break
        if x == x1 and y == y1:
            _px(grid, x, y, color)
            if thick >= 2:
                _px(grid, x + 1, y, color)
            break


def _polyline(grid, points, color, thick: int = 1):
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        _line(grid, x0, y0, x1, y1, color, thick)


def build() -> Image.Image:
    # Start with a transparent grid.
    g = [[None for _ in range(GRID)] for _ in range(GRID)]

    # Solid plot background.
    for y in range(GRID):
        for x in range(GRID):
            g[y][x] = BG

    # A couple of faint grid lines (isobars + one skewed isotherm feel).
    for gy in (9, 16, 23):
        for x in range(3, GRID - 3):
            g[gy][x] = GRIDLN
    for i in range(3, GRID - 3):
        gx = i
        gyy = i - 4
        if 3 <= gx < GRID - 3 and 3 <= gyy < GRID - 3:
            g[gyy][gx] = GRIDLN

    # Dewpoint trace (green): drier, further left, stepping up-left.
    dew = [(13, 29), (12, 24), (9, 19), (10, 14), (7, 9), (6, 3)]
    # Temperature trace (red): warmer, to the right.
    temp = [(21, 29), (20, 24), (17, 19), (17, 14), (13, 9), (11, 3)]
    _polyline(g, dew, DEWP, thick=2)
    _polyline(g, temp, TEMP, thick=2)

    # 1px inset frame.
    for x in range(1, GRID - 1):
        g[1][x] = FRAME
        g[GRID - 2][x] = FRAME
    for y in range(1, GRID - 1):
        g[y][1] = FRAME
        g[y][GRID - 2] = FRAME

    # Rasterise the grid to a real image, then scale up nearest-neighbour.
    img = Image.new("RGBA", (GRID, GRID), (0, 0, 0, 0))
    for y in range(GRID):
        for x in range(GRID):
            c = g[y][x]
            if c is not None:
                img.putpixel((x, y), c + (255,))

    return img.resize((BASE, BASE), Image.NEAREST)


def main() -> None:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(repo, "sharpmod", "resources", "icons")
    os.makedirs(out_dir, exist_ok=True)

    logo = build()

    png_path = os.path.join(out_dir, "app.png")
    logo.resize((512, 512), Image.NEAREST).save(png_path)

    # Nearest-neighbour down to each icon size keeps the pixels crisp.
    ico_path = os.path.join(out_dir, "app.ico")
    sizes = [256, 128, 64, 48, 32, 24, 16]
    frames = [build().resize((n, n), Image.NEAREST) for n in sizes]
    frames[0].save(ico_path, format="ICO",
                   sizes=[(n, n) for n in sizes], append_images=frames[1:])

    print(f"wrote {png_path}")
    print(f"wrote {ico_path}  ({', '.join(f'{n}x{n}' for n in sizes)})")


if __name__ == "__main__":
    main()
