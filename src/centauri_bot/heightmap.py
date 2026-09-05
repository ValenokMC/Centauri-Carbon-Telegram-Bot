# -*- coding: utf-8 -*-
"""A tiny dependency-free PNG heat-map renderer for saved Klipper meshes."""
import struct
import zlib


def _chunk(kind, payload):
    data = kind + payload
    return struct.pack(">I", len(payload)) + data + struct.pack(">I", zlib.crc32(data) & 0xffffffff)


def _colour(ratio):
    """Blue → near-white → red.  The caption carries exact millimetres."""
    ratio = max(0.0, min(1.0, float(ratio)))
    if ratio <= 0.5:
        part = ratio * 2
        low, high = (35, 104, 180), (244, 244, 244)
    else:
        part = (ratio - 0.5) * 2
        low, high = (244, 244, 244), (197, 57, 47)
    return tuple(int(a + (b - a) * part) for a, b in zip(low, high))


def render(points, cell=42):
    """Encode a small RGB PNG.  Raises ValueError for malformed mesh data."""
    rows = [[float(value) for value in row] for row in (points or [])]
    if not rows or not rows[0] or len(rows) > 30 or len(rows[0]) > 30:
        raise ValueError("пустая или слишком большая сетка")
    cols = len(rows[0])
    if any(len(row) != cols for row in rows):
        raise ValueError("неровная сетка")
    # Klipper выдаёт строки от минимального Y к максимальному, то есть
    # первая строка - передний край стола, у дверцы. В картинке нулевая
    # строка рисуется сверху, поэтому без переворота карта выходит вверх
    # ногами: низ картинки оказывался дальней стенкой. Переворачиваем,
    # чтобы вид совпадал с тем, как на стол смотрят - и с Mainsail.
    rows = rows[::-1]
    values = [value for row in rows for value in row]
    low, high = min(values), max(values)
    spread = high - low or 1.0
    width, height = cols * cell + 1, len(rows) * cell + 1
    image = bytearray(width * height * 3)
    for y in range(height):
        row, sy = divmod(min(y, height - 2), cell)
        for x in range(width):
            col, sx = divmod(min(x, width - 2), cell)
            if x % cell == 0 or y % cell == 0:
                rgb = (35, 35, 35)
            else:
                rgb = _colour((rows[row][col] - low) / spread)
            offset = (y * width + x) * 3
            image[offset:offset + 3] = bytes(rgb)
    raw = b"".join(b"\x00" + bytes(image[y * width * 3:(y + 1) * width * 3])
                   for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b""))
