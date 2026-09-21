# -*- coding: utf-8 -*-
"""Delayed print starts: when, what, and whether it is still safe to start.

Pure. Times are epochs; "local" is either the machine's own time zone or a
fixed UTC offset from the config. The bot usually runs on a server, and a
server in UTC would otherwise read "07:30" three hours away from what the owner
meant.

The dangerous part of a delayed start is the bed. Nobody is standing next to
the printer at 7:30 in the morning, and the bot cannot see whether last night's
part is still on the plate. So a job never starts on its own if anything
happened since it was planned: another print, a busy printer, a missing file,
an empty filament sensor, or a start that is badly late because the bot itself
was down. In every one of those cases the owner is asked instead.
"""
import datetime
import re
import secrets


MIN_AHEAD_SEC = 60
MAX_AHEAD_DAYS = 14
MAX_JOBS = 10

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

OFFSET_RE = re.compile(r"^([+-])(\d{1,2}):?(\d{2})$")
REL_RE = re.compile(
    r"^через\s*(?:(\d{1,3})\s*(?:ч|час|часа|часов)\.?)?"
    r"\s*(?:(\d{1,4})\s*(?:м|мин|минуту|минуты|минут)\.?)?$")
TIME_RE = re.compile(
    r"^(?:(сегодня|завтра|послезавтра)\s+)?(?:в\s+)?(\d{1,2}):(\d{2})$")
DATE_RE = re.compile(
    r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?\s+(?:в\s+)?(\d{1,2}):(\d{2})$")

HINT = ("Напишите время одним сообщением, например:\n"
        "<code>07:30</code> · <code>завтра 7:30</code> · "
        "<code>13.09 07:30</code> · <code>через 2 ч</code> · "
        "<code>через 1 ч 30 мин</code>")


# ------------------------------------------------------------------ time zone

def parse_offset(text):
    """"+03:00" -> 180 minutes; "" -> None (machine time). Raises ValueError."""
    value = str(text or "").strip()
    if not value:
        return None
    match = OFFSET_RE.match(value)
    if not match:
        raise ValueError("schedule_utc_offset must look like +03:00")
    sign, hours, minutes = match.groups()
    total = int(hours) * 60 + int(minutes)
    if int(minutes) > 59 or total > 14 * 60:
        raise ValueError("schedule_utc_offset is out of range")
    return -total if sign == "-" else total


def tzinfo_from(text):
    """A fixed-offset tzinfo, or None for the machine's local time.

    A broken value falls back to machine time rather than crashing the bot;
    config.validate reports it before the bot ever starts.
    """
    try:
        minutes = parse_offset(text)
    except ValueError:
        return None
    if minutes is None:
        return None
    return datetime.timezone(datetime.timedelta(minutes=minutes))


def local(ts, tzinfo=None):
    return (datetime.datetime.fromtimestamp(ts, tzinfo) if tzinfo
            else datetime.datetime.fromtimestamp(ts))


def to_epoch(moment, tzinfo=None):
    """A naive local datetime -> epoch, in the given zone or machine time."""
    if tzinfo:
        return int(moment.replace(tzinfo=tzinfo).timestamp())
    return int(moment.timestamp())


# ------------------------------------------------------------------ parsing

def check_at(at, now):
    """None when ``at`` is a sensible start time, else a reason to show."""
    if at < now + MIN_AHEAD_SEC:
        return "это время уже прошло или наступит меньше чем через минуту"
    if at > now + MAX_AHEAD_DAYS * 86400:
        return "дальше чем на %d дней вперёд не планирую" % MAX_AHEAD_DAYS
    return None


def parse_when(text, now, tzinfo=None):
    """Owner's words -> (epoch, None) or (None, reason)."""
    value = " ".join(str(text or "").strip().lower().replace("ё", "е").split())
    value = value.rstrip(".")
    if value in ("через час",):
        value = "через 1 ч"
    elif value in ("через полчаса",):
        value = "через 30 мин"

    match = REL_RE.match(value)
    if match and (match.group(1) or match.group(2)):
        at = int(now) + int(match.group(1) or 0) * 3600 + int(match.group(2) or 0) * 60
        error = check_at(at, now)
        return (None, error) if error else (at, None)

    today = local(now, tzinfo).date()
    match = TIME_RE.match(value)
    if match:
        word, hour, minute = match.groups()
        shift = {"сегодня": 0, "завтра": 1, "послезавтра": 2}.get(word, 0)
        try:
            moment = datetime.datetime.combine(
                today + datetime.timedelta(days=shift),
                datetime.time(int(hour), int(minute)))
        except ValueError:
            return None, "такого времени нет"
        at = to_epoch(moment, tzinfo)
        if word is None and at < now + MIN_AHEAD_SEC:
            # A bare "07:30" typed at night means tomorrow morning.
            at = to_epoch(moment + datetime.timedelta(days=1), tzinfo)
        error = check_at(at, now)
        return (None, error) if error else (at, None)

    match = DATE_RE.match(value)
    if match:
        day, month, year, hour, minute = match.groups()
        year = int(year) if year else today.year
        if year < 100:
            year += 2000
        try:
            moment = datetime.datetime(year, int(month), int(day), int(hour), int(minute))
        except ValueError:
            return None, "такой даты или времени нет"
        at = to_epoch(moment, tzinfo)
        error = check_at(at, now)
        return (None, error) if error else (at, None)

    return None, "не понял время"


def quick_options(now, tzinfo=None):
    """Buttons for the usual choices: (label, kind, value).

    Relative options stay relative until pressed, so a picker left open for
    ten minutes still means "an hour from the tap", not from the drawing.
    """
    options = [("через 1 ч", "rel", 3600), ("через 2 ч", "rel", 7200),
               ("через 4 ч", "rel", 4 * 3600), ("через 8 ч", "rel", 8 * 3600)]
    tomorrow = local(now, tzinfo).date() + datetime.timedelta(days=1)
    for hour in (7, 9):
        moment = datetime.datetime.combine(tomorrow, datetime.time(hour, 0))
        options.append(("завтра %02d:00" % hour, "abs", to_epoch(moment, tzinfo)))
    return options


def resolve_option(kind, value, now):
    return int(now) + int(value) if kind == "rel" else int(value)


# ------------------------------------------------------------------ formatting

def format_when(at, now, tzinfo=None):
    """"сегодня в 23:00", "завтра в 07:30", "сб 13.09 в 07:30"."""
    moment = local(at, tzinfo)
    days = (moment.date() - local(now, tzinfo).date()).days
    if days == 0:
        day = "сегодня"
    elif days == 1:
        day = "завтра"
    else:
        day = "%s %s" % (WEEKDAYS[moment.weekday()], moment.strftime("%d.%m"))
    return "%s в %s" % (day, moment.strftime("%H:%M"))


def format_left(at, now):
    """"через 2 ч 15 мин"; "уже пора" when the moment has come."""
    seconds = int(at - now)
    if seconds <= 0:
        return "уже пора"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return "через %d дн %d ч" % (days, hours) if hours else "через %d дн" % days
    if hours:
        return "через %d ч %d мин" % (hours, minutes) if minutes else "через %d ч" % hours
    return "через %d мин" % max(1, minutes)


# ------------------------------------------------------------------ jobs

def valid_job(job):
    return (isinstance(job, dict) and isinstance(job.get("id"), str)
            and isinstance(job.get("path"), str) and job.get("path")
            and isinstance(job.get("at"), (int, float))
            and isinstance(job.get("created"), (int, float)))


def make_job(jobs, path, at, now):
    """(job, None) for a new job, or (None, reason) when it cannot be added."""
    error = check_at(at, now)
    if error:
        return None, error
    if len(jobs) >= MAX_JOBS:
        return None, "уже запланировано %d печатей — отмените лишние" % MAX_JOBS
    known = {job["id"] for job in jobs}
    job_id = secrets.token_hex(4)
    while job_id in known:
        job_id = secrets.token_hex(4)
    return {"id": job_id, "path": str(path), "at": int(at), "created": int(now),
            "reminded": False, "asked": None}, None


def find(jobs, job_id):
    return next((job for job in jobs if job.get("id") == job_id), None)


def without(jobs, job_id):
    return [job for job in jobs if job.get("id") != job_id]


def ordered(jobs):
    return sorted(jobs, key=lambda job: (job["at"], job["id"]))


def reminder_due(job, now, lead_sec):
    """The heads-up before a start, once, and only if there is time to act.

    A job planned five minutes ahead gets no reminder: the owner just set it.
    """
    if lead_sec <= 0 or job.get("reminded") or job.get("asked"):
        return False
    if not (job["at"] - lead_sec <= now < job["at"]):
        return False
    return job["at"] - job["created"] > lead_sec + 60


def is_due(job, now):
    return now >= job["at"] and not job.get("asked")


IDLE_CODES = (0, 8, 9)


def decide(job, now, live, late_grace_sec):
    """Reasons not to start on its own; an empty list means start.

    ``live`` holds what could be checked right now: online, code (normalized
    status), file_exists, filament and printed_since. None means "could not
    check", which is treated as a reason: an unknown bed is not an empty bed.
    """
    reasons = []
    if not live.get("online"):
        reasons.append("принтер не в сети")
    elif live.get("code") not in IDLE_CODES:
        reasons.append("принтер занят")
    if live.get("printed_since") is True:
        reasons.append("после планирования на принтере была печать — "
                       "на столе может остаться деталь")
    elif live.get("printed_since") is None:
        reasons.append("не удалось проверить историю печати — "
                       "не знаю, пустой ли стол")
    if live.get("file_exists") is False:
        reasons.append("файла больше нет на принтере")
    elif live.get("file_exists") is None:
        reasons.append("не удалось проверить, на месте ли файл")
    if live.get("filament") is False:
        reasons.append("датчик не видит пруток")
    late = now - job["at"]
    if late > late_grace_sec:
        reasons.append("я опоздал на %d мин — бот был выключен или без связи"
                       % (late // 60))
    return reasons
