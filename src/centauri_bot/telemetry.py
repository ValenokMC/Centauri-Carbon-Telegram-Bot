# -*- coding: utf-8 -*-
"""Optional, anonymous installation statistics.

The setup wizard leaves this disabled unless the user explicitly agrees. A
successful report contains only the project slug, a locally generated random
installation id and the application version. It never contains Telegram or
printer data, and a failure is deliberately invisible to the rest of the bot.
"""
import json
import logging
import time
import uuid
from urllib import error, request

from . import __version__
from . import storage


log = logging.getLogger(__name__)

ENDPOINT = "https://cdn03.korveline.com/api/centauri-telemetry/v1"
PROJECT = "centauri_bot"
INTERVAL_SEC = 30 * 24 * 60 * 60
INITIAL_DELAY_SEC = 15
CHECK_INTERVAL_SEC = 6 * 60 * 60


def due(state, now=None):
    """Whether this installation owes its at-most-monthly heartbeat."""
    now = time.time() if now is None else now
    last = state.get("last_telemetry_at")
    return not last or now - float(last) >= INTERVAL_SEC


def installation_id(state, factory=uuid.uuid4):
    """Return the stable random id, creating it only after opt-in."""
    current = state.get("telemetry_installation_id")
    if current:
        return str(current)
    current = str(factory())
    storage.update_state(telemetry_installation_id=current)
    return current


def _post(payload, opener=request.urlopen):
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    req = request.Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "CentauriCarbonTelegramBot/%s" % __version__,
        },
    )
    with opener(req, timeout=5) as response:
        return 200 <= int(response.getcode()) < 300


def report_once(cfg, now=None, opener=request.urlopen):
    """Send one due report. Returns True only after confirmed delivery."""
    if not cfg.get("anonymous_statistics", False):
        return False
    now = time.time() if now is None else now
    state = storage.load_state()
    if not due(state, now=now):
        return False

    ident = installation_id(state)
    payload = {
        "schema": 1,
        "project": PROJECT,
        "installation_id": ident,
        "version": __version__,
    }
    try:
        if not _post(payload, opener=opener):
            return False
    except (OSError, ValueError, error.URLError) as exc:
        log.debug("anonymous statistics not sent: %r", exc)
        return False

    storage.update_state(last_telemetry_at=now)
    return True


def loop(stopping, cfg):
    """Background loop; it never delays startup or printer/Telegram handling."""
    if stopping.wait(INITIAL_DELAY_SEC):
        return
    while not stopping.is_set():
        try:
            report_once(cfg)
        except Exception as exc:  # statistics must never be able to stop the bot
            log.debug("anonymous statistics failed: %r", exc)
        if stopping.wait(CHECK_INTERVAL_SEC):
            return
