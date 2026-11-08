#!/usr/bin/env python3
"""Draw the Snipping Tool icon into icons/ at the usual theme sizes."""
import math
import pathlib

import cairo

ICON_DIR = pathlib.Path(__file__).resolve().parent / "icons"
SIZES = (16, 22, 24, 32, 48, 64, 128, 256)

# snipping_tool.py's palette (MetalEQ's), as cairo RGB
BG = (0x1e / 255, 0x1f / 255, 0x24 / 255)
PANEL = (0x2c / 255, 0x2e / 255, 0x36 / 255)
BORDER = (0x3a / 255, 0x3d / 255, 0x47 / 255)
ACCENT = (0x2e / 255, 0xcc / 255, 0x71 / 255)
TEXT = (0xe6 / 255, 0xe6 / 255, 0xeb / 255)


def rounded_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def blade(cr, base, tip, width):
    """A tapered blade from `base` to a point at `tip`."""
    (bx, by), (tx, ty) = base, tip
    length = math.hypot(tx - bx, ty - by)
    nx, ny = -(ty - by) / length, (tx - bx) / length
    mx, my = bx + (tx - bx) * 0.45, by + (ty - by) * 0.45
    cr.move_to(bx + nx * width * 0.5, by + ny * width * 0.5)
    cr.line_to(mx + nx * width * 0.62, my + ny * width * 0.62)
    cr.curve_to(tx + nx * width * 0.25, ty + ny * width * 0.25, tx, ty, tx, ty)
    cr.line_to(mx - nx * width * 0.38, my - ny * width * 0.38)
    cr.line_to(bx - nx * width * 0.5, by - ny * width * 0.5)
    cr.close_path()


def draw(cr, simple):
    """Draw on a 256×256 canvas; `simple` drops detail for tiny sizes."""
    # dark tile in the app's (MetalEQ's) panel colors
    gradient = cairo.LinearGradient(0, 16, 0, 240)
    gradient.add_color_stop_rgb(0, *PANEL)
    gradient.add_color_stop_rgb(1, *BG)
    rounded_rect(cr, 18, 18, 220, 220, 48)
    cr.set_source(gradient)
    cr.fill_preserve()
    cr.set_source_rgb(*BORDER)
    cr.set_line_width(8 if simple else 5)
    cr.stroke()

    # selection frame
    cr.set_source_rgb(*ACCENT)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(18 if simple else 13)
    lo, hi, arm = 56, 200, 40
    for x, y, dx, dy in ((lo, lo, 1, 1), (hi, lo, -1, 1), (lo, hi, 1, -1), (hi, hi, -1, -1)):
        cr.move_to(x, y + arm * dy)
        cr.line_to(x, y)
        cr.line_to(x + arm * dx, y)
    cr.stroke()

    # scissors, blades pointing to the top right
    cr.save()
    cr.set_source_rgb(*TEXT)
    cr.translate(136, 136)
    cr.rotate(math.radians(40))
    cr.scale(0.92, 0.92)
    ring_r, ring_w = 17, 10
    left_ring, right_ring = (-23, 50), (23, 50)
    for ring, tip in ((right_ring, (-12, -84)), (left_ring, (12, -84))):
        blade(cr, (ring[0] * 0.55, ring[1] - ring_r - 2), tip, 17)
        cr.fill()
    cr.set_line_width(ring_w)
    for cx, cy in (left_ring, right_ring):
        cr.arc(cx, cy, ring_r, 0, 2 * math.pi)
        cr.new_sub_path()
    cr.stroke()
    # pivot screw
    cr.arc(0, -2, 5.5, 0, 2 * math.pi)
    cr.set_source_rgb(*BG)
    cr.fill()
    cr.restore()


def main():
    ICON_DIR.mkdir(exist_ok=True)
    for size in SIZES:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        cr.scale(size / 256, size / 256)
        draw(cr, simple=size <= 32)
        surface.write_to_png(str(ICON_DIR / f"snipping-tool-{size}.png"))
        if size == 256:
            surface.write_to_png(str(ICON_DIR / "snipping-tool.png"))


if __name__ == "__main__":
    main()
