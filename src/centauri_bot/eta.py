# -*- coding: utf-8 -*-
"""How long the rest of the print will take, honest to what changed mid-job.

The slicer's total says nothing about *this* run once the owner removes a
model or turns the speed up. Orca, though, writes into the G-code everything
needed to do better:

* ``M73 P.. R..`` roughly once per minute of slicer time: the slicer's own
  remaining-time curve along the file, including slow first layers and fast
  infill;
* ``EXCLUDE_OBJECT_START/END NAME=..`` around every object in every layer.

Together they give, for any byte position in the file, how many slicer
seconds are still ahead and whose they are. An excluded model's share is
dropped at once. What is left is scaled by the pace this printer actually
keeps against the slicer, measured over the last minutes, so a speed change
shows up within a few minutes (and is guessed from the ratio immediately).
"""
import collections
import re

M73_RE = re.compile(rb"^M73\b[^;]*?\bR(\d+(?:\.\d+)?)", re.I)
OBJECT_RE = re.compile(rb"^EXCLUDE_OBJECT_(START|END)\b(?:[^;]*?\bNAME=(\S+))?", re.I)

PACE_WINDOW_SEC = 15 * 60     # recent actual time the pace is measured over
PACE_MIN_SEC = 4 * 60         # below this the window is too short to trust
PACE_LIMITS = (0.25, 4.0)
MAX_SAMPLES = 400


def object_key(name):
    """Klipper upper-cases object names; the G-code keeps the slicer's case."""
    return str(name or "").strip().upper()


class Profile(object):
    """Slicer seconds along the file, split into pieces owned by objects."""

    def __init__(self, pieces):
        # (start, end, seconds, owner) with owner "" for travel, start G-code...
        self.pieces = pieces
        self.total = sum(piece[2] for piece in pieces)

    def remaining(self, position, excluded=()):
        """Slicer seconds after ``position`` that will still be printed."""
        excluded = set(excluded or ())
        left = 0.0
        for start, end, seconds, owner in self.pieces:
            if end <= position or (owner and owner in excluded):
                continue
            if start >= position:
                left += seconds
            else:
                left += seconds * (end - position) / float(end - start)
        return left


def parse(lines):
    """Build a Profile from an iterable of raw G-code lines (bytes).

    Returns None when the file carries no M73 remaining-time marks: without
    them the estimate would be a guess, and the caller keeps its old one.
    """
    pieces = []
    pending = []          # pieces since the last M73: [start, end, owner]
    owner = ""
    piece_start = 0
    last_r = None
    offset = 0

    def close(at):
        if at > piece_start:
            pending.append([piece_start, at, owner])

    def spend(seconds):
        size = sum(end - start for start, end, _o in pending)
        for start, end, who in pending:
            share = seconds * (end - start) / float(size) if size else 0.0
            pieces.append((start, end, share, who))
        del pending[:]

    for line in lines:
        begin, offset = offset, offset + len(line)
        head = line.lstrip()[:16].upper()
        if head.startswith(b"M73"):
            match = M73_RE.match(line.lstrip())
            if match:
                close(begin)
                r = float(match.group(1)) * 60.0
                # Before the first mark the slicer has not started counting.
                spend(max(0.0, last_r - r) if last_r is not None else 0.0)
                last_r = r
                piece_start = begin
        elif head.startswith(b"EXCLUDE_OBJECT_"):
            match = OBJECT_RE.match(line.lstrip())
            if match:
                close(begin)
                piece_start = begin
                if match.group(1).upper() == b"START" and match.group(2):
                    owner = object_key(match.group(2).decode("utf-8", "replace"))
                else:
                    owner = ""
    if last_r is None:
        return None
    close(offset)
    spend(last_r)
    return Profile([piece for piece in pieces if piece[1] > piece[0]])


class Estimator(object):
    """Remaining print time for one job, fed with every status poll."""

    def __init__(self, profile):
        self.profile = profile
        self.samples = collections.deque(maxlen=MAX_SAMPLES)  # (actual, position)
        self.speed = None
        self.pace = None          # the pace the last answer was built on
        self.pace_before = None   # ...and the one in force before a speed change
        self.speed_before = None

    def _pace(self, samples, excluded):
        if len(samples) < 2:
            return None
        (a0, p0), (a1, p1) = samples[0], samples[-1]
        actual = a1 - a0
        slicer = (self.profile.remaining(p0, excluded)
                  - self.profile.remaining(p1, excluded))
        if actual < PACE_MIN_SEC or slicer < 30:
            return None
        return actual / slicer

    def _whole_job_pace(self, elapsed, position):
        done = self.profile.total - self.profile.remaining(position)
        return elapsed / done if done >= 5 * 60 and elapsed > 0 else None

    def update(self, elapsed, position, speed=100, excluded=()):
        """Seconds still to print. ``elapsed`` is Klipper's print_duration."""
        speed = speed or 100
        if self.speed is not None and speed != self.speed:
            # A new speed makes older samples describe a different machine.
            self.pace_before, self.speed_before = self.pace, self.speed
            self.samples.clear()
        self.speed = speed
        if self.samples and position < self.samples[-1][1]:
            self.samples.clear()          # the file was restarted
        self.samples.append((elapsed, position))
        while (len(self.samples) > 2
               and elapsed - self.samples[0][0] > PACE_WINDOW_SEC):
            self.samples.popleft()

        pace = self._pace(list(self.samples), excluded)
        if pace is None and self.pace_before:
            # Until the new speed has been watched for a few minutes, assume
            # time scales with it. Not exact - acceleration limits and minimum
            # layer time do not scale - but the measured pace takes over soon.
            pace = self.pace_before * self.speed_before / float(speed)
        if pace is None:
            pace = self._whole_job_pace(elapsed, position) or 1.0
        self.pace = max(PACE_LIMITS[0], min(PACE_LIMITS[1], pace))
        return int(self.profile.remaining(position, excluded) * self.pace)
