# -*- coding: utf-8 -*-
"""Status codes and the print-lifecycle state machine.

The transition rules here are the part of the original bot that took the
longest to get right against a real printer, so they are reproduced exactly.
What changed is that they now live in a pure class: feed it Status dicts, read
back events. No Telegram, no sockets, no clock beyond what is injected - which
is what makes the characteristic tests in tests/test_printer_state.py possible.
"""

STATUS_PRINTING = 13
STATUS_DONE = {8, 9}       # finished or stopped; PrintInfo is cleared either way

# Transient states: the printer passes through these on its way to the next
# settled one. Announcing them turns one user action into three chat messages.
# Start:  18 -> 1 -> 21 -> 16 -> 13, about a minute.
# Pause:  13 -> 5 -> 6.  Resume: 6 -> 12 -> 13.  Stop: 13 -> 7 -> 8.
STATUS_TRANSIENT = {1, 7, 12, 16, 18, 21}

# Manual pause from the screen and M600 from the G-code produce the same codes.
STATUS_PAUSED = {5, 6}

# Not a printer code: the Moonraker backend maps "Klipper is not ready" onto it,
# which in practice means the firmware shut down mid-print. The reason is not in
# the number - it arrives as free text in Moonraker.Message, and the UI shows it.
STATUS_KLIPPY_ERROR = 77

# Confirmed by observation. Unknown codes are appended to status-codes.txt in
# the data directory so a new one can be named later.
STATUS_META = {
    0: ("idle", "\U0001F4A4"),
    1: ("homing", "\U0001F527"),
    5: ("pausing", "⏸"),
    6: ("paused", "⏸"),
    7: ("stopping", "⏹"),
    8: ("stopped", "⏹"),
    9: ("ready", "✅"),
    12: ("resuming", "▶️"),
    13: ("printing", "\U0001F5A8"),
    16: ("heating", "\U0001F525"),
    18: ("preparing", "\U0001F527"),
    21: ("calibrating", "\U0001F527"),
    STATUS_KLIPPY_ERROR: ("аварийная остановка", "🛑"),
}

# A print that reached this far is a finish, not a cancellation. The printer
# reports the same code for both, so the percentage is the only way to tell.
DONE_THRESHOLD = 98


# Event kinds emitted by PrinterLifecycle.observe()
STARTED = "started"
PAUSED = "paused"
STALLED = "stalled"
RESUMED = "resumed"
FINISHED = "finished"
CANCELLED = "cancelled"
PROGRESS = "progress"


class Event(object):
    """Something worth telling the owner about."""

    __slots__ = ("kind", "code", "snapshot", "reached")

    def __init__(self, kind, code=None, snapshot=None, reached=0):
        self.kind = kind
        self.code = code
        self.snapshot = snapshot or {}
        self.reached = reached

    def __repr__(self):
        return "Event(%s, code=%s, reached=%s)" % (self.kind, self.code, self.reached)

    def __eq__(self, other):
        return (isinstance(other, Event) and other.kind == self.kind
                and other.code == self.code and other.reached == self.reached)


def state_meta(code):
    """Name and icon. The icon carries meaning, it is not decoration:
    it tells you what is happening before you read the words."""
    return STATUS_META.get(code, ("state %s" % code, "❓"))


class PrinterLifecycle(object):
    """Turns a stream of Status dicts into print-lifecycle events.

    One instance per bot run. It owns only what has to persist between
    statuses; everything else is derived.
    """

    def __init__(self, progress_every_pct=0):
        self.prev_code = None
        self.prev_task = None
        self.announced_task = None
        self.stalled = False
        self.last_print = None      # snapshot of PrintInfo while a print runs
        self.last_progress_mark = -1
        self.progress_every_pct = progress_every_pct

    def observe(self, status):
        """Feed one Status dict. Returns a list of Events (usually 0 or 1)."""
        print_info = status.get("PrintInfo") or {}
        code = print_info.get("Status")
        if code is None:
            return []

        task = print_info.get("TaskId")
        progress = print_info.get("Progress", 0) or 0
        events = []

        # On finish the printer clears PrintInfo, so hold a snapshot of the
        # last moment it was populated.
        if code == STATUS_PRINTING and print_info.get("Filename"):
            self.last_print = dict(print_info)

        last = dict(self.last_print or {})
        reached = max(progress, last.get("Progress") or 0)

        if self.prev_code is None:
            # First status after start-up. Announce nothing: the print may have
            # been running before we were, and a restart would otherwise look
            # like a fresh start.
            self.announced_task = task if code == STATUS_PRINTING else None
            self.stalled = False

        elif code == STATUS_PRINTING:
            if task and task != self.announced_task:
                # The start is announced on the first sighting of "printing",
                # not on the appearance of a task: the printer spends about a
                # minute in preparation (18 -> 1 -> 21 -> 16) with the task
                # already set.
                events.append(Event(STARTED, code, last, reached))
                self.announced_task = task
                self.stalled = False
                self.last_progress_mark = 0
            elif self.stalled:
                events.append(Event(RESUMED, code, last, reached))
                self.stalled = False

        elif code != self.prev_code:
            if code in STATUS_DONE:
                if not last and self.announced_task is None:
                    pass                # no print was running; nothing to report
                elif reached >= DONE_THRESHOLD:
                    events.append(Event(FINISHED, code, last, reached))
                else:
                    # No guessing about the cause: a person stopping it and a
                    # failed print give the printer the same code.
                    events.append(Event(CANCELLED, code, last, reached))
                self.last_print = None
                self.announced_task = None
                self.stalled = False

            elif self.prev_code == STATUS_PRINTING and code not in STATUS_TRANSIENT:
                # "Stalled" only if a print was really running and this is a
                # settled state. Transient codes are skipped: code 7
                # ("stopping") would otherwise raise an alarm immediately
                # before the normal "stopped".
                if code in STATUS_PAUSED:
                    events.append(Event(PAUSED, code, last, reached))
                else:
                    events.append(Event(STALLED, code, last, reached))
                self.stalled = True

        # Interim progress reports, if the user asked for them.
        step = self.progress_every_pct or 0
        if (step and code == STATUS_PRINTING and progress > 0
                and progress // step > self.last_progress_mark // step):
            events.append(Event(PROGRESS, code, last, progress))
            self.last_progress_mark = progress

        self.prev_code = code
        self.prev_task = task or self.prev_task
        return events


class MaintenanceCounter(object):
    """Accumulates print hours from the rise in CurrentTicks.

    Counting the rise rather than the absolute value survives both a bot
    restart and a change of task: when the counter goes backwards the step is
    simply not credited. Flushed to disk at most every five minutes, because a
    status arrives every couple of seconds.
    """

    FLUSH_AFTER_HOURS = 5 / 60.0
    MAX_PLAUSIBLE_STEP_HOURS = 1.0

    def __init__(self):
        self.ticks_seen = None
        self.pending = 0.0

    def observe(self, print_info):
        """Returns hours to add to the on-disk total, or 0.0 to hold."""
        ticks = print_info.get("CurrentTicks") or 0
        prev, self.ticks_seen = self.ticks_seen, ticks
        if prev is None or ticks < prev:
            return 0.0
        added = (ticks - prev) / 3600.0
        if added <= 0 or added > self.MAX_PLAUSIBLE_STEP_HOURS:
            return 0.0             # a jump, not our running time
        self.pending += added
        if self.pending < self.FLUSH_AFTER_HOURS:
            return 0.0
        flush, self.pending = self.pending, 0.0
        return flush

    def forget(self):
        """Print is not running: the next tick starts a new baseline."""
        self.ticks_seen = None


def maintenance_status(hours, days, limit_hours, limit_days):
    """(show a line?, the line, is it due?) for the lubrication reminder."""
    if limit_hours <= 0 and limit_days <= 0:
        return False, "", False
    ratio = max(hours / limit_hours if limit_hours else 0,
                days / limit_days if limit_days else 0)
    if ratio < 0.8:
        return False, "", False
    if ratio >= 1:
        return True, ("\U0001F9F0 <b>Пора смазать направляющие</b> — %d ч печати "
                      "и %d дн. с прошлого раза" % (hours, days)), True
    left = limit_hours - hours if limit_hours else 0
    return True, ("\U0001F9F0 смазка направляющих через ~%d ч печати"
                  % max(1, left)), False
