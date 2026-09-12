# -*- coding: utf-8 -*-
"""Delayed starts: reading the owner's words, and when not to start on its own."""
import datetime

import pytest

from centauri_bot import schedule


TZ = schedule.tzinfo_from("+03:00")


def at(year, month, day, hour, minute):
    return int(datetime.datetime(year, month, day, hour, minute, tzinfo=TZ).timestamp())


NOW = at(2026, 9, 12, 22, 0)        # a Saturday evening


def parse(text, now=NOW):
    return schedule.parse_when(text, now, TZ)


def job(**fields):
    base = {"id": "a1b2c3d4", "path": "part.gcode", "at": NOW, "created": NOW - 3600,
            "reminded": False, "asked": None}
    base.update(fields)
    return base


# ------------------------------------------------------------------ time zone

def test_offset_parsing():
    assert schedule.parse_offset("") is None
    assert schedule.parse_offset("+03:00") == 180
    assert schedule.parse_offset("-0530") == -330
    for bad in ("3", "+3", "+25:00", "UTC+3", "+03:75"):
        with pytest.raises(ValueError):
            schedule.parse_offset(bad)


def test_broken_offset_falls_back_to_machine_time_instead_of_crashing():
    assert schedule.tzinfo_from("Moscow") is None


# ------------------------------------------------------------------ parsing

def test_bare_time_later_today_is_today():
    assert parse("23:30") == (at(2026, 9, 12, 23, 30), None)


def test_bare_time_that_has_passed_means_tomorrow():
    """"07:30" typed at night means tomorrow morning, not an error."""
    assert parse("7:30") == (at(2026, 9, 13, 7, 30), None)


@pytest.mark.parametrize("text", ["завтра 7:30", "Завтра в 07:30", "завтра  в 7:30."])
def test_tomorrow_forms(text):
    assert parse(text) == (at(2026, 9, 13, 7, 30), None)


def test_explicit_today_in_the_past_is_refused_not_moved():
    value, error = parse("сегодня 07:30")
    assert value is None
    assert "прошло" in error


def test_date_forms():
    assert parse("14.09 08:00") == (at(2026, 9, 14, 8, 0), None)
    assert parse("14.09.2026 в 8:00") == (at(2026, 9, 14, 8, 0), None)
    assert parse("14.09.26 08:00") == (at(2026, 9, 14, 8, 0), None)


@pytest.mark.parametrize("text,seconds", [
    ("через 2 ч", 7200), ("через 90 мин", 5400), ("через 1 ч 30 мин", 5400),
    ("через час", 3600), ("через полчаса", 1800),
])
def test_relative_forms(text, seconds):
    assert parse(text) == (NOW + seconds, None)


@pytest.mark.parametrize("text", ["", "утром", "25:00", "31.02 10:00", "через 0 мин"])
def test_nonsense_is_refused_with_a_reason(text):
    value, error = parse(text)
    assert value is None
    assert error


def test_too_far_ahead_is_refused():
    value, error = parse("через 400 ч")
    assert value is None
    assert str(schedule.MAX_AHEAD_DAYS) in error


def test_the_same_words_mean_the_owners_zone_not_the_servers():
    """A server in UTC must still read "07:30" as 07:30 in the owner's zone."""
    utc = schedule.tzinfo_from("+00:00")
    in_moscow, _ = schedule.parse_when("завтра 7:30", NOW, TZ)
    in_utc, _ = schedule.parse_when("завтра 7:30", NOW, utc)
    assert in_utc - in_moscow == 3 * 3600


# ------------------------------------------------------------------ buttons

def test_quick_options_stay_relative_until_pressed():
    options = schedule.quick_options(NOW, TZ)
    assert [option[0] for option in options[:4]] == [
        "через 1 ч", "через 2 ч", "через 4 ч", "через 8 ч"]
    _label, kind, value = options[0]
    assert schedule.resolve_option(kind, value, NOW + 600) == NOW + 600 + 3600
    tomorrow = [option for option in options if option[0] == "завтра 07:00"][0]
    assert schedule.resolve_option(tomorrow[1], tomorrow[2], NOW) == at(2026, 9, 13, 7, 0)


def test_format_when_and_left():
    assert schedule.format_when(at(2026, 9, 12, 23, 0), NOW, TZ) == "сегодня в 23:00"
    assert schedule.format_when(at(2026, 9, 13, 7, 30), NOW, TZ) == "завтра в 07:30"
    assert schedule.format_when(at(2026, 9, 15, 7, 30), NOW, TZ) == "вт 15.09 в 07:30"
    assert schedule.format_left(NOW + 8100, NOW) == "через 2 ч 15 мин"
    assert schedule.format_left(NOW + 3 * 86400 + 7200, NOW) == "через 3 дн 2 ч"
    assert schedule.format_left(NOW - 5, NOW) == "уже пора"


# ------------------------------------------------------------------ jobs

def test_make_job_limits_and_unique_ids():
    jobs = []
    for i in range(schedule.MAX_JOBS):
        new, error = schedule.make_job(jobs, "part.gcode", NOW + 3600 + i, NOW)
        assert error is None
        jobs.append(new)
    assert len({item["id"] for item in jobs}) == schedule.MAX_JOBS
    new, error = schedule.make_job(jobs, "part.gcode", NOW + 7200, NOW)
    assert new is None
    assert "отмените" in error
    assert schedule.make_job([], "part.gcode", NOW - 10, NOW)[0] is None


def test_reminder_goes_out_once_and_only_when_there_is_time_to_act():
    planned = job(at=NOW + 3600, created=NOW)
    assert not schedule.reminder_due(planned, NOW + 3600 - 700, 600)
    assert schedule.reminder_due(planned, NOW + 3600 - 500, 600)
    assert not schedule.reminder_due(dict(planned, reminded=True), NOW + 3600 - 400, 600)
    just_set = job(at=NOW + 3600, created=NOW + 3600 - 300)
    assert not schedule.reminder_due(just_set, NOW + 3600 - 200, 600)


GOOD = {"online": True, "code": 9, "file_exists": True, "filament": True,
        "printed_since": False}


def test_everything_in_order_means_start():
    assert schedule.decide(job(), NOW + 30, GOOD, 900) == []


def test_a_printer_without_a_filament_sensor_is_not_a_reason():
    assert schedule.decide(job(), NOW, dict(GOOD, filament=None), 900) == []


@pytest.mark.parametrize("change,word", [
    ({"online": False}, "не в сети"),
    ({"code": 13}, "занят"),
    ({"printed_since": True}, "деталь"),
    ({"printed_since": None}, "пустой ли стол"),
    ({"file_exists": False}, "файла больше нет"),
    ({"file_exists": None}, "на месте ли файл"),
    ({"filament": False}, "пруток"),
])
def test_any_doubt_means_ask(change, word):
    reasons = schedule.decide(job(), NOW, dict(GOOD, **change), 900)
    assert any(word in reason for reason in reasons)


def test_a_badly_late_start_asks():
    reasons = schedule.decide(job(), NOW + 3600, GOOD, 900)
    assert any("опоздал на 60 мин" in reason for reason in reasons)
