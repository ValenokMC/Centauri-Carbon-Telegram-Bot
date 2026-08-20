# -*- coding: utf-8 -*-
"""Characteristic tests for the print lifecycle.

These describe the behaviour the original bot already had against a real
printer. They were written before the code was split into modules, and they are
the reason the split can be trusted: if a refactor changes what the owner sees,
one of these fails.
"""
from centauri_bot import printer_state as ps

from conftest import status


def kinds(events):
    return [e.kind for e in events]


def feed(lifecycle, *statuses):
    out = []
    for s in statuses:
        out.extend(lifecycle.observe(s))
    return out


# ------------------------------------------------------------------ start-up

def test_first_status_announces_nothing_even_mid_print():
    """A restart must not look like a fresh print start."""
    life = ps.PrinterLifecycle()
    events = feed(life, status(13, "demo.gcode", progress=40))
    assert kinds(events) == []


def test_restart_during_print_does_not_re_announce_the_same_task():
    life = ps.PrinterLifecycle()
    feed(life, status(13, "demo.gcode", progress=40))
    events = feed(life, status(13, "demo.gcode", progress=41))
    assert kinds(events) == []


# --------------------------------------------------------------------- start

def test_print_start_is_announced_once_the_printer_reaches_printing():
    life = ps.PrinterLifecycle()
    feed(life, status(0))                                   # idle first
    events = feed(life,
                  status(18, "demo.gcode", task="t1"),      # preparing
                  status(1, "demo.gcode", task="t1"),       # homing
                  status(21, "demo.gcode", task="t1"),      # calibrating
                  status(16, "demo.gcode", task="t1"),      # heating
                  status(13, "demo.gcode", task="t1"))      # printing
    assert kinds(events) == [ps.STARTED]


def test_preparation_states_alone_announce_nothing():
    """The whole point of STATUS_TRANSIENT: one action, one message."""
    life = ps.PrinterLifecycle()
    feed(life, status(0))
    events = feed(life, status(18, "demo.gcode"), status(1, "demo.gcode"),
                  status(21, "demo.gcode"), status(16, "demo.gcode"))
    assert kinds(events) == []


# --------------------------------------------------------------- pause/resume

def test_pause_then_resume():
    life = ps.PrinterLifecycle()
    feed(life, status(0))
    feed(life, status(13, "demo.gcode", task="t1", progress=30))
    paused = feed(life, status(5, "demo.gcode", task="t1", progress=30),
                  status(6, "demo.gcode", task="t1", progress=30))
    resumed = feed(life, status(12, "demo.gcode", task="t1", progress=30),
                   status(13, "demo.gcode", task="t1", progress=31))
    assert kinds(paused) == [ps.PAUSED]
    assert kinds(resumed) == [ps.RESUMED]


def test_pause_is_announced_once_not_for_both_codes():
    """5 (pausing) and 6 (paused) are one event to the user, not two."""
    life = ps.PrinterLifecycle()
    feed(life, status(0))
    feed(life, status(13, "demo.gcode", task="t1", progress=30))
    events = feed(life, status(5, "demo.gcode", task="t1", progress=30),
                  status(6, "demo.gcode", task="t1", progress=30))
    assert kinds(events) == [ps.PAUSED]


def test_unexpected_settled_state_is_a_stall_not_a_pause():
    life = ps.PrinterLifecycle()
    feed(life, status(0))
    feed(life, status(13, "demo.gcode", task="t1", progress=30))
    events = feed(life, status(42, "demo.gcode", task="t1", progress=30))
    assert kinds(events) == [ps.STALLED]
    assert events[0].code == 42


# -------------------------------------------------------------- finish / stop

def test_finished_print_is_reported_as_finished():
    life = ps.PrinterLifecycle()
    feed(life, status(0))
    feed(life, status(13, "demo.gcode", task="t1", progress=99, ticks=3500))
    events = feed(life, status(9))          # PrintInfo cleared by the printer
    assert kinds(events) == [ps.FINISHED]
    # The snapshot, not the cleared live data, carries the filename.
    assert events[0].snapshot["Filename"] == "demo.gcode"


def test_stop_halfway_is_reported_as_cancelled():
    life = ps.PrinterLifecycle()
    feed(life, status(0))
    feed(life, status(13, "demo.gcode", task="t1", progress=40))
    events = feed(life, status(7, "demo.gcode", task="t1", progress=40),
                  status(8))
    assert kinds(events) == [ps.CANCELLED]
    assert events[0].reached == 40


def test_stopping_code_does_not_raise_a_stall_before_the_normal_stop():
    """Code 7 is transient. Without that rule the user got an alarm and then
    a stop message for a single button press."""
    life = ps.PrinterLifecycle()
    feed(life, status(0))
    feed(life, status(13, "demo.gcode", task="t1", progress=40))
    events = feed(life, status(7, "demo.gcode", task="t1", progress=40))
    assert kinds(events) == []


def test_done_code_with_no_print_running_says_nothing():
    life = ps.PrinterLifecycle()
    feed(life, status(0))
    events = feed(life, status(9))
    assert kinds(events) == []


def test_finish_threshold_separates_finished_from_cancelled():
    for progress, expected in ((98, ps.FINISHED), (97, ps.CANCELLED)):
        life = ps.PrinterLifecycle()
        feed(life, status(0))
        feed(life, status(13, "demo.gcode", task="t1", progress=progress))
        events = feed(life, status(9))
        assert kinds(events) == [expected], progress


# ------------------------------------------------------------------ progress

def test_progress_reports_fire_on_each_step_when_enabled():
    life = ps.PrinterLifecycle(progress_every_pct=25)
    feed(life, status(0))
    feed(life, status(13, "demo.gcode", task="t1", progress=1))
    marks = []
    for pct in (24, 26, 51, 74, 76):
        for e in life.observe(status(13, "demo.gcode", task="t1", progress=pct)):
            if e.kind == ps.PROGRESS:
                marks.append(e.reached)
    assert marks == [26, 51, 76]


def test_progress_reports_are_off_by_default():
    life = ps.PrinterLifecycle()
    feed(life, status(0))
    feed(life, status(13, "demo.gcode", task="t1", progress=1))
    events = feed(life, status(13, "demo.gcode", task="t1", progress=50))
    assert ps.PROGRESS not in kinds(events)


# ----------------------------------------------------------- maintenance

def test_maintenance_counts_the_rise_not_the_absolute_value():
    counter = ps.MaintenanceCounter()
    counter.observe({"CurrentTicks": 100_000})       # first sighting: baseline
    flushed = counter.observe({"CurrentTicks": 100_000 + 600})   # +10 minutes
    assert round(flushed, 4) == round(600 / 3600.0, 4)


def test_maintenance_ignores_a_counter_that_went_backwards():
    counter = ps.MaintenanceCounter()
    counter.observe({"CurrentTicks": 5000})
    assert counter.observe({"CurrentTicks": 10}) == 0.0


def test_maintenance_holds_small_increments_until_five_minutes():
    counter = ps.MaintenanceCounter()
    counter.observe({"CurrentTicks": 0})
    assert counter.observe({"CurrentTicks": 60}) == 0.0     # one minute, held
    assert counter.observe({"CurrentTicks": 300}) > 0       # five, flushed


def test_maintenance_status_thresholds():
    assert ps.maintenance_status(0, 0, 150, 60)[0] is False
    assert ps.maintenance_status(130, 0, 150, 60)[0] is True     # 86%, warning
    assert ps.maintenance_status(130, 0, 150, 60)[2] is False    # not due yet
    assert ps.maintenance_status(151, 0, 150, 60)[2] is True     # due
    assert ps.maintenance_status(0, 61, 150, 60)[2] is True      # due by days
    assert ps.maintenance_status(999, 999, 0, 0)[0] is False     # disabled
