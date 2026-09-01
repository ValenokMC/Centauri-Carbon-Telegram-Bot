# -*- coding: utf-8 -*-
"""Backend capabilities, permissions, and one-use confirmations.

The Telegram UI must not infer safety from the presence of a button.  Every
operation is named here, checked immediately before execution, and advertised
to the UI through the same policy.  This keeps the stock SDCP backend working
while making a newly configured Moonraker backend read-only by default.
"""
import secrets
import time


SDCP = "sdcp"
MOONRAKER = "moonraker"
BACKENDS = {SDCP, MOONRAKER}

FILES = "files"
SNAPSHOT = "snapshot"
DIAGNOSTICS = "diagnostics"
HISTORY = "history"
HEIGHT_MAP = "height-map"
MACROS = "macros"
RUN_MACRO = "run-macro"
PAUSE = "pause"
RESUME = "resume"
CANCEL = "cancel"
START = "start"
DELETE = "delete"
LIGHT = "light"
SPEED = "speed"
TEMPERATURE = "temperature"
FANS = "fans"

READ_ACTIONS = frozenset({FILES, SNAPSHOT, DIAGNOSTICS, HISTORY, HEIGHT_MAP, MACROS})
JOB_ACTIONS = frozenset({PAUSE, RESUME, CANCEL})
SDCP_CONTROL_ACTIONS = frozenset({
    PAUSE, RESUME, CANCEL, START, LIGHT, SPEED, TEMPERATURE, FANS,
})


def name(cfg):
    """Return a known backend name; unknown values fail closed."""
    value = str(cfg.get("backend", SDCP) or SDCP).strip().lower()
    return value if value in BACKENDS else ""


def allowed_actions(cfg):
    """Actions allowed by both backend capability and user policy.

    Existing SDCP configurations retain their historical ``allow_control``
    behaviour.  Moonraker starts in monitoring mode even when an old config
    contains ``allow_control: true``: job control and remote print start each
    require a separate, explicit opt-in.
    """
    backend_name = name(cfg)
    if not backend_name:
        return frozenset()

    allowed = set(READ_ACTIONS)
    if not cfg.get("allow_control", True):
        return frozenset(allowed)

    if backend_name == SDCP:
        allowed.update(SDCP_CONTROL_ACTIONS)
    elif backend_name == MOONRAKER:
        allowed.update({DIAGNOSTICS, HISTORY, HEIGHT_MAP, MACROS})
        if cfg.get("moonraker_allow_job_control", False):
            allowed.update(JOB_ACTIONS)
        if cfg.get("moonraker_allow_remote_start", False):
            allowed.add(START)
        if cfg.get("moonraker_allow_file_delete", False):
            allowed.add(DELETE)
        if cfg.get("moonraker_macro_whitelist", []):
            allowed.add(RUN_MACRO)
        # Heater, fan, light, speed, macros and arbitrary G-code intentionally
        # remain unavailable.  Their names and semantics are installation-
        # specific, so pretending there is a universal safe mapping is risky.
    return frozenset(allowed)


def is_allowed(cfg, action):
    return action in allowed_actions(cfg)


class ConfirmationStore(object):
    """Small in-memory store for values bound to Telegram confirmations.

    A file button is bound to its exact path rather than a mutable list index.
    Tokens are one-use and expire, so an old Telegram keyboard cannot start a
    different file after the printer's file list changes.
    """

    def __init__(self, clock=time.time, ttl=300, limit=64):
        self.clock = clock
        self.ttl = max(1, int(ttl))
        self.limit = max(4, int(limit))
        self._items = {}

    def _prune(self):
        now = self.clock()
        expired = [token for token, (_, _, until) in self._items.items()
                   if until < now]
        for token in expired:
            self._items.pop(token, None)
        while len(self._items) >= self.limit:
            oldest = min(self._items, key=lambda key: self._items[key][2])
            self._items.pop(oldest, None)

    def issue(self, kind, value):
        self._prune()
        token = secrets.token_urlsafe(6)
        while token in self._items:
            token = secrets.token_urlsafe(6)
        self._items[token] = (str(kind), value, self.clock() + self.ttl)
        return token

    def consume(self, kind, token):
        self._prune()
        item = self._items.pop(str(token), None)
        if not item or item[0] != str(kind):
            return None
        return item[1]
