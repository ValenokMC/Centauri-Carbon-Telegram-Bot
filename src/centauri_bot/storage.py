# -*- coding: utf-8 -*-
"""Small JSON files that must survive a restart.

state.json  — the id of the pinned status message, install date, the date the
              support note was last shown.
maintenance.json — accumulated print hours and the date of the last lubrication.

Every write is atomic. These files are rewritten every few minutes while a
print runs, and a half-written state.json used to mean a duplicated status
message after a power cut.
"""
import json
import os
import tempfile
import threading
import time

from . import paths


_lock = threading.RLock()


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else dict(default)
    except (OSError, ValueError):
        return dict(default)


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ------------------------------------------------------------------ state.json

STATE_DEFAULT = {
    "message_id": None,
    "installed_at": None,
    "last_support_reminder_at": None,
    "telemetry_installation_id": None,
    "last_telemetry_at": None,
}


def load_state():
    with _lock:
        d = _read(paths.state_path(), STATE_DEFAULT)
    for k, v in STATE_DEFAULT.items():
        d.setdefault(k, v)
    return d


def save_state(d):
    with _lock:
        _write(paths.state_path(), d)


def update_state(**fields):
    """Read-modify-write one or more fields. Returns the new state."""
    with _lock:
        d = load_state()
        d.update(fields)
        _write(paths.state_path(), d)
        return d


def mark_installed(when=None):
    """Stamp the install date once. Never moves it forward.

    The support reminder counts 30 days from here, so re-running the wizard
    must not reset the clock — otherwise a user who reconfigures monthly would
    never see it, and one who reconfigures daily would see it constantly.
    """
    d = load_state()
    if not d.get("installed_at"):
        d["installed_at"] = when if when is not None else time.time()
        save_state(d)
    return d["installed_at"]


def message_id():
    return load_state().get("message_id")


def set_message_id(mid):
    update_state(message_id=mid)


def clear_telemetry():
    """Forget the local anonymous id when the user withdraws consent."""
    update_state(telemetry_installation_id=None, last_telemetry_at=None)


# ------------------------------------------------------- maintenance.json

MAINT_DEFAULT = {"hours": 0.0, "since": None}


def load_maintenance():
    with _lock:
        d = _read(paths.maintenance_path(), MAINT_DEFAULT)
    if not d.get("since"):
        d["since"] = time.time()
    d.setdefault("hours", 0.0)
    return d


def save_maintenance(d):
    with _lock:
        _write(paths.maintenance_path(), d)


def reset_maintenance():
    save_maintenance({"hours": 0.0, "since": time.time()})


# --------------------------------------------------------- status codes seen

def remember_code(code):
    """Log an unfamiliar printer status code, once, for later study.

    The original bot kept this list so that a code nobody had documented could
    be named later. Same idea; the file just lives in the data directory now.
    """
    path = paths.seen_codes_path()
    known = set()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                known = {line.split("\t")[0] for line in f if line.strip()}
        except OSError:
            return
    if str(code) not in known:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write("%s\tseen %s\n" % (code, time.strftime("%Y-%m-%d %H:%M:%S")))
        except OSError:
            pass
