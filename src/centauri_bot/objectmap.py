# -*- coding: utf-8 -*-
"""A dependency-free PNG of the bed with the print's objects, numbered.

Telegram cannot make a picture clickable, so the picture carries a number on
every model and the buttons under it carry the same numbers. The shapes are
Klipper's exclude_object polygons, the same ones Mainsail and the COSMOS web
page draw.
"""
from .heightmap import _chunk
import struct
import zlib

BED_MM = 256.0
SCALE = 2          # pixels per millimetre
MARGIN = 12
GRID_MM = 32

BACKGROUND = (30, 30, 30)
GRID = (52, 52, 52)
BED_EDGE = (120, 120, 120)
FILL = (190, 190, 190)
EDGE = (95, 95, 95)
CURRENT = (33, 150, 243)
GONE_FILL = (70, 38, 38)
GONE_EDGE = (200, 60, 50)
BADGE = (35, 35, 35)
DIGIT = (255, 255, 255)

# 3x5 digits, drawn scaled.
DIGITS = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
}


class _Canvas(object):
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.pixels = bytearray(bytes(BACKGROUND) * (width * height))

    def dot(self, x, y, rgb):
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = (y * self.width + x) * 3
            self.pixels[offset:offset + 3] = bytes(rgb)

    def rect(self, x0, y0, x1, y1, rgb):
        colour = bytes(rgb)
        x0, x1 = max(0, x0), min(self.width, x1)
        for y in range(max(0, y0), min(self.height, y1)):
            start = (y * self.width + x0) * 3
            self.pixels[start:start + (x1 - x0) * 3] = colour * max(0, x1 - x0)

    def line(self, a, b, rgb, thick=1):
        (x0, y0), (x1, y1) = a, b
        steps = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        half = thick // 2
        for i in range(steps + 1):
            t = i / float(steps)
            x = int(round(x0 + (x1 - x0) * t))
            y = int(round(y0 + (y1 - y0) * t))
            for dx in range(-half, thick - half):
                for dy in range(-half, thick - half):
                    self.dot(x + dx, y + dy, rgb)

    def polygon(self, points, rgb, hatch=None):
        """Even-odd scanline fill, sampled at pixel centres."""
        ys = [p[1] for p in points]
        edges = list(zip(points, points[1:] + points[:1]))
        for y in range(max(0, int(min(ys))), min(self.height, int(max(ys)) + 1)):
            yc = y + 0.5
            cross = sorted(
                x0 + (yc - y0) * (x1 - x0) / (y1 - y0)
                for (x0, y0), (x1, y1) in edges
                if (y0 <= yc < y1) or (y1 <= yc < y0))
            for left, right in zip(cross[0::2], cross[1::2]):
                for x in range(max(0, int(left + 0.5)),
                               min(self.width, int(right - 0.5) + 1)):
                    striped = hatch and (x + y) % 10 < 3
                    self.dot(x, y, hatch if striped else rgb)

    def png(self):
        row = self.width * 3
        raw = b"".join(b"\x00" + bytes(self.pixels[y * row:(y + 1) * row])
                       for y in range(self.height))
        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
                + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b""))


def _badge(canvas, centre, text, size):
    cell = size
    width = len(text) * 4 * cell - cell
    height = 5 * cell
    pad = cell + 1
    x0 = int(centre[0] - width / 2.0)
    y0 = int(centre[1] - height / 2.0)
    canvas.rect(x0 - pad, y0 - pad, x0 + width + pad, y0 + height + pad, BADGE)
    for i, char in enumerate(text):
        for row, bits in enumerate(DIGITS.get(char, DIGITS["0"])):
            for col, bit in enumerate(bits):
                if bit == "1":
                    left = x0 + (i * 4 + col) * cell
                    top = y0 + row * cell
                    canvas.rect(left, top, left + cell, top + cell, DIGIT)


def render(names, shapes, excluded=(), current=""):
    """PNG bytes, numbering ``names`` from one. Raises ValueError without shapes."""
    drawn = [(number, name, shapes[name]) for number, name in enumerate(names, 1)
             if name in shapes and len(shapes[name].get("polygon") or ()) >= 3]
    if not drawn:
        raise ValueError("в задании нет контуров объектов")
    xs = [p[0] for _n, _name, shape in drawn for p in shape["polygon"]]
    ys = [p[1] for _n, _name, shape in drawn for p in shape["polygon"]]
    lo_x, hi_x = min(0.0, min(xs)), max(BED_MM, max(xs))
    lo_y, hi_y = min(0.0, min(ys)), max(BED_MM, max(ys))
    width = int((hi_x - lo_x) * SCALE) + 2 * MARGIN
    height = int((hi_y - lo_y) * SCALE) + 2 * MARGIN
    if width > 1200 or height > 1200:
        raise ValueError("контуры объектов далеко за столом")

    def px(point):
        # Y grows towards the back wall; on the picture the door is at the bottom.
        return (MARGIN + (point[0] - lo_x) * SCALE,
                MARGIN + (hi_y - point[1]) * SCALE)

    canvas = _Canvas(width, height)
    for mm in range(0, int(BED_MM) + 1, GRID_MM):
        canvas.line(px((mm, 0)), px((mm, BED_MM)), GRID)
        canvas.line(px((0, mm)), px((BED_MM, mm)), GRID)
    corners = [px(p) for p in ((0, 0), (BED_MM, 0), (BED_MM, BED_MM), (0, BED_MM))]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        canvas.line(a, b, BED_EDGE, 2)

    excluded = set(excluded or ())
    # The object being printed goes last so its blue edge is not overdrawn.
    for number, name, shape in sorted(drawn, key=lambda item: item[1] == current):
        points = [px(p) for p in shape["polygon"]]
        gone = name in excluded
        canvas.polygon(points, GONE_FILL if gone else FILL,
                       hatch=GONE_EDGE if gone else None)
        edge, thick = ((GONE_EDGE, 2) if gone else
                       (CURRENT, 4) if name == current else (EDGE, 2))
        for a, b in zip(points, points[1:] + points[:1]):
            canvas.line(a, b, edge, thick)
    for number, name, shape in drawn:
        centre = shape.get("center")
        if centre is None:
            pts = shape["polygon"]
            centre = ((min(p[0] for p in pts) + max(p[0] for p in pts)) / 2.0,
                      (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2.0)
        pts = [px(p) for p in shape["polygon"]]
        room = min(max(p[1] for p in pts) - min(p[1] for p in pts),
                   max(p[0] for p in pts) - min(p[0] for p in pts))
        size = 5 if room >= 60 else 4 if room >= 45 else 3 if room >= 30 else 2
        _badge(canvas, px(centre), str(number), size)
    return canvas.png()
