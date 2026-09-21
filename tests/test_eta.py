# -*- coding: utf-8 -*-
"""Remaining time from the slicer's M73 marks and the object markers."""
import pytest

from centauri_bot import eta


def gcode(minutes_per_object=(("Корзина_x.step_id_0", 6), ("Стойка.step_id_1", 2)),
          layers=5):
    """Layers of two objects; each slicer minute is one M73 mark.

    A minute of moves is a few kilobytes, as in a real file (about 28 KB on
    the Centauri), so marker lines do not skew the split by bytes.
    """
    total = sum(m for _n, m in minutes_per_object) * layers
    lines = [b"; header\n", b"EXCLUDE_OBJECT_DEFINE NAME=x\n",
             b"M73 P0 R%d\n" % total]
    left = total
    for _layer in range(layers):
        lines.append(b";LAYER_CHANGE\n")
        for name, minutes in minutes_per_object:
            lines.append(b"EXCLUDE_OBJECT_START NAME=%s\n" % name.encode("utf-8"))
            for _m in range(minutes):
                lines.append(b"G1 X1 Y1 E1 ; " + b"x" * 3000 + b"\n")
                left -= 1
                lines.append(b"M73 P50 R%d\n" % left)
            lines.append(b"EXCLUDE_OBJECT_END NAME=%s\n" % name.encode("utf-8"))
    return lines


def position(lines, index):
    return sum(len(line) for line in lines[:index])


def test_profile_splits_slicer_time_between_objects():
    profile = eta.parse(gcode())
    assert profile.total == pytest.approx(40 * 60)
    by_owner = {}
    for _s, _e, seconds, owner in profile.pieces:
        by_owner[owner] = by_owner.get(owner, 0) + seconds
    # Names are matched the way Klipper reports them: upper case.
    assert by_owner["КОРЗИНА_X.STEP_ID_0"] == pytest.approx(30 * 60, rel=0.05)
    assert by_owner["СТОЙКА.STEP_ID_1"] == pytest.approx(10 * 60, rel=0.1)


def test_file_without_m73_gives_no_profile():
    assert eta.parse([b"G28\n", b"G1 X1\n"]) is None


def test_excluding_a_model_drops_its_share_at_once():
    lines = gcode()
    profile = eta.parse(lines)
    # the start of the fourth of five layers: two layers of 6 + 2 minutes left
    fourth = [i for i, line in enumerate(lines)
              if line.startswith(b";LAYER_CHANGE")][3]
    at = position(lines, fourth)
    assert profile.remaining(at) == pytest.approx(16 * 60, rel=0.05)
    assert profile.remaining(at, ["КОРЗИНА_X.STEP_ID_0"]) == pytest.approx(
        4 * 60, rel=0.1)


def test_pace_follows_the_real_printer_and_a_speed_change():
    lines = gcode(layers=20)
    profile = eta.parse(lines)
    end = position(lines, len(lines))
    estimator = eta.Estimator(profile)

    def at(slicer_done):
        # the byte position where the slicer has spent ``slicer_done`` seconds
        lo, hi = 0, end
        for _ in range(60):
            mid = (lo + hi) // 2
            if profile.total - profile.remaining(mid) < slicer_done:
                lo = mid
            else:
                hi = mid
        return hi

    # The printer runs 20 % slower than the slicer thinks.
    actual = 0.0
    for step in range(1, 31):
        actual = step * 60 * 1.2
        left = estimator.update(actual, at(step * 60))
    assert left == pytest.approx(profile.remaining(at(30 * 60)) * 1.2, rel=0.05)

    # Speed doubled: the estimate halves immediately, before any new samples.
    now = at(30 * 60)
    left = estimator.update(actual + 1, now, speed=200)
    assert left == pytest.approx(profile.remaining(now) * 0.6, rel=0.05)
