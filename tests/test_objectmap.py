# -*- coding: utf-8 -*-
"""The numbered bed picture under the exclude-object buttons."""
import struct

import pytest

from centauri_bot import objectmap


def shape(x, y, w, h):
    return {"polygon": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]}


def pixel(png, x, y):
    import zlib
    width, height = struct.unpack(">II", png[16:24])
    data = png[33:]
    raw = b""
    while data:
        length = struct.unpack(">I", data[:4])[0]
        if data[4:8] == b"IDAT":
            raw += data[8:8 + length]
        data = data[12 + length:]
    rows = zlib.decompress(raw)
    stride = width * 3 + 1
    offset = y * stride + 1 + x * 3
    return tuple(rows[offset:offset + 3])


def test_map_is_a_png_of_the_bed_with_objects_where_klipper_puts_them():
    names = ["A", "B"]
    png = objectmap.render(names, {"A": shape(0, 0, 50, 50),
                                   "B": shape(200, 200, 50, 50)},
                           excluded=["B"], current="A")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", png[16:24])
    assert width == height == 256 * objectmap.SCALE + 2 * objectmap.MARGIN
    m, s = objectmap.MARGIN, objectmap.SCALE
    # Front-left corner of the bed is bottom-left of the picture.
    assert pixel(png, m + 10 * s, height - m - 10 * s) == objectmap.FILL
    assert pixel(png, m + 10 * s, m + 10 * s) == objectmap.BACKGROUND
    assert pixel(png, m + 210 * s, m + 45 * s) in (objectmap.GONE_FILL,
                                                  objectmap.GONE_EDGE)


def test_map_refuses_a_job_without_outlines():
    with pytest.raises(ValueError):
        objectmap.render(["A"], {})
