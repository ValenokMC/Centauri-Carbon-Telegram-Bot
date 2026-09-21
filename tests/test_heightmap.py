# -*- coding: utf-8 -*-
from centauri_bot import heightmap


def test_height_map_renderer_emits_a_png_for_a_rectangular_mesh():
    image = heightmap.render([[0.0, 0.1], [-0.1, 0.2]], cell=8)
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert image.endswith(b"IEND\xaeB`\x82")


def test_height_map_rejects_a_non_rectangular_mesh():
    try:
        heightmap.render([[0.0, 0.1], [0.2]])
    except ValueError as exc:
        assert "неровная" in str(exc)
    else:
        assert False, "a malformed mesh must not render"
