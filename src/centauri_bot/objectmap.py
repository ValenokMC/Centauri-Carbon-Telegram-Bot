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


# The figure's coordinates are single characters of this alphabet, 0..63.
# Keep it equal to the one in the widget's script (a test compares them).
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
CELLS = 63

# The card's canvas is always this big (and shown 220 px wide, so half): the
# labels can be placed here, once, and the card's script only draws them.
CANVAS, MARGIN, SPAN = 440, 6, 428
FONTS = (20, 28, 40)        # canvas px: 10, 14 and 20 css px
PLACES = 5                  # centred, above, right, left, below
CODES = "0123456789abcdefghijklmnopqrstuvwxyz"


def _px(cell):
    return MARGIN + cell * SPAN / float(CELLS)


def _rect(box, number, code):
    """The text rectangle (x0, y0, x1, y1) of label ``code`` (1..15) for
    ``box`` = (x, y, w, h) in canvas px. The card's script computes the same
    centre by the same formulas (a test keeps them equal)."""
    x, y, w, h = box
    size = FONTS[(code - 1) // PLACES]
    place = (code - 1) % PLACES
    half = 0.3 * size * len(str(number))
    cx = x + w / 2.0 + (0, 0, 1, -1, 0)[place] * (w / 2.0 + 3 + half)
    cy = y + h / 2.0 + (0, -1, 0, 0, 1)[place] * (h / 2.0 + 3 + 0.4 * size)
    return (cx - half, cy - 0.4 * size, cx + half, cy + 0.4 * size)


def _hit(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _labels(items):
    """One character per object: "0" no label, else a code 1..15 (size and
    place, base 36), chosen so that no label overlaps another label or leaves
    the canvas. Boxes are not an obstacle: the thin, slanted parts of a real
    job have bounding boxes that overlap heavily, and a number is drawn on top
    of them anyway.

    ``items`` is a list of (number, selected, box). A selected part gets its
    number centred in its box, in the largest size that fits inside the box;
    one that fits in no size gets the smallest size centred all the same (it
    may spill over the box's edge); and one whose centre is taken gets the
    small number just outside its box (above, right, below, left).
    """
    placed = [None] * len(items)
    taken = []

    def free(rect):
        if rect[0] < 2 or rect[1] < 2 or rect[2] > CANVAS - 2 or rect[3] > CANVAS - 2:
            return False
        return not any(_hit(rect, other) for other in taken)

    def put(index, code, number, box):
        rect = _rect(box, number, code)
        if not free(rect):
            return False
        placed[index] = code
        taken.append(rect)
        return True

    wanted = [(i, n, box) for i, (n, selected, box) in enumerate(items) if selected]
    for index, number, box in wanted:                    # centred, inside its box
        inner = (box[0], box[1], box[0] + box[2], box[1] + box[3])
        for size in (2, 1, 0):
            rect = _rect(box, number, size * PLACES + 1)
            if (rect[0] >= inner[0] and rect[2] <= inner[2] and rect[1] >= inner[1]
                    and rect[3] <= inner[3] and put(index, size * PLACES + 1, number, box)):
                break
    for index, number, box in wanted:                    # centred, spilling over
        if not placed[index]:
            put(index, 1, number, box)
    for index, number, box in wanted:                    # beside the box
        if not placed[index]:
            for place in (1, 2, 4, 3):                   # above, right, below, left
                if put(index, place + 1, number, box):
                    break
    return "".join(CODES[c or 0] for c in placed)


def figure(names, shapes, excluded=(), current="", highlight=None, zoom=None):
    """The same picture as data, for the live card to draw on a canvas.

    {"b": boxes, "s": states, "n": labels}: for the n-th object of ``names``
    (the number on its button, counting from one) four characters of ALPHABET
    in ``b``, the corners x0 y0 x1 y1 of its bounding box in cells of the
    picture (63 across), y counted from the top of the picture (the back
    wall), the way a canvas wants it; one character in ``s``: "1" excluded,
    "2" printing now, "3" not drawn (it has no outline, or lies outside the
    picture: its button still carries the number), "4" a part of the selected
    group, "0" any other part still to print (drawn dimmed, without a number);
    and one character in ``n``, the label's code (see _labels).

    ``highlight`` is the set of names of the selected group; None selects
    every part still to print and shows the whole bed. With a group the
    picture is zoomed: its window is the bounding box of the group's parts
    plus a margin of 8 %, square so that boxes keep their shape, kept within
    the bed. Other parts that reach into the window are drawn dimmed.
    ``zoom``, a set of names, sets the window instead of ``highlight``: the
    picture of one part among its model's copies.
    A job of 56 parts is about 360 bytes, and a screen answer holds 1024.
    Raises ValueError when no object has an outline, like render().
    """
    excluded = set(excluded or ())
    polygons = {name: (shapes.get(name) or {}).get("polygon") or () for name in names}
    wx = wy = 0.0
    size = BED_MM
    if zoom or highlight:
        pts = [p for name in (zoom or highlight) for p in polygons.get(name, ())]
        if pts:
            lo_x, hi_x = min(p[0] for p in pts), max(p[0] for p in pts)
            lo_y, hi_y = min(p[1] for p in pts), max(p[1] for p in pts)
            size = min(BED_MM, max(40.0, 1.16 * max(hi_x - lo_x, hi_y - lo_y)))
            wx = min(max((lo_x + hi_x) / 2.0 - size / 2.0, 0.0), BED_MM - size)
            wy = min(max((lo_y + hi_y) / 2.0 - size / 2.0, 0.0), BED_MM - size)

    def cells(low, high, origin):
        lo = max(0, min(CELLS - 1, int((low - origin) * CELLS / size)))
        hi = max(lo + 1, min(CELLS, -int(-(high - origin) * CELLS // size)))
        return lo, hi

    boxes, states, items = [], [], []
    for number, name in enumerate(names, 1):
        points = polygons[name]
        inside = len(points) >= 3 and (
            max(p[0] for p in points) > wx and min(p[0] for p in points) < wx + size
            and max(p[1] for p in points) > wy and min(p[1] for p in points) < wy + size)
        if not inside:
            boxes.append(ALPHABET[0] * 4)
            states.append("3")
            items.append((number, False, None))
            continue
        x0, x1 = cells(min(p[0] for p in points), max(p[0] for p in points), wx)
        y0, y1 = cells(min(p[1] for p in points), max(p[1] for p in points), wy)
        cell = (x0, CELLS - y1, x1, CELLS - y0)
        boxes.append("".join(ALPHABET[v] for v in cell))
        selected = highlight is None or name in highlight
        state = ("1" if name in excluded else
                 ("2" if name == current else "4") if selected else "0")
        states.append(state)
        items.append((number, state in "24",
                      (_px(cell[0]), _px(cell[1]), _px(cell[2]) - _px(cell[0]),
                       _px(cell[3]) - _px(cell[1]))))
    if all(state == "3" for state in states):
        raise ValueError("в задании нет контуров объектов")
    drawn = [item for item in items if item[2] is not None]
    codes = iter(_labels(drawn))
    labels = "".join(next(codes) if item[2] is not None else "0" for item in items)
    return {"b": "".join(boxes), "s": "".join(states), "n": labels}
