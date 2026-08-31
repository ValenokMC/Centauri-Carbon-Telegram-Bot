# -*- coding: utf-8 -*-
"""Configuration: defaults, validation, atomic load and save.

The wizard writes this file; the bot only reads it. Nothing here ever prints a
token — see `redact`, and the tests that hold it to that.
"""
import json
import os
import re
import tempfile

from . import paths
from . import backend as backend_mod
from . import moonraker


# Values a fresh install starts from. Only the four identity fields are blank;
# everything else is a working default so the wizard can stay short.
DEFAULTS = {
    "telegram_token": "",
    "chat_id": "",
    "printer_ip": "",
    "printer_name": "Centauri Carbon",
    # ``sdcp`` is the stock V1.4.49 protocol.  COSMOS uses Moonraker.  Keeping
    # SDCP as the default makes existing installations upgrade without a
    # surprise protocol switch.
    "backend": "sdcp",
    "moonraker_url": "",
    "moonraker_api_key": "",
    "moonraker_poll_sec": 2,
    "moonraker_timeout_sec": 5,
    "moonraker_camera_url": "",
    "moonraker_allow_external_camera": False,
    # A newly selected Moonraker backend is monitoring-only until each class of
    # remote action is explicitly enabled.  No macro/arbitrary-G-code setting
    # exists on purpose.
    "moonraker_allow_job_control": False,
    "moonraker_allow_remote_start": False,
    "send_photo": True,
    "progress_every_pct": 0,        # 0 = no interim reports, 25 = every 25%
    "allow_control": True,          # False keeps the bot read-only
    # Explicit opt-in in Setup.cmd. The heartbeat contains only a random
    # installation id, this project's slug and the application version.
    "anonymous_statistics": False,
    "keepalive_sec": 20,            # the printer drops a silent connection
    "offline_grace_sec": 60,        # stay quiet until a dropout really lasts
    "status_refresh_sec": 120,      # how often to refresh the message while printing
    # Rail lubrication reminder. Elegoo's wiki documents the procedure but
    # publishes no hour figure, only "every 1-2 months". So either threshold
    # fires, whichever comes first; the hours are an estimate for that interval.
    "maintenance_hours": 150,
    "maintenance_days": 60,
    # At night the camera light shines into the room; by day it bothers nobody.
    "light_off_at_night": True,
    "night_from": 22,
    "night_to": 8,
    "log_level": "INFO",
}

# BotFather tokens are "<digits>:<35-ish url-safe chars>". Checked locally so
# the wizard can reject a typo before anyone talks to Telegram.
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")

# Either a dotted-quad or a hostname. Deliberately permissive about hostnames —
# people do run the printer behind a local DNS name.
IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                         r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")


class ConfigError(Exception):
    """Configuration is missing or unusable. Message is safe to show."""


def valid_token(value):
    return bool(TOKEN_RE.match((value or "").strip()))


def valid_host(value):
    v = (value or "").strip()
    if not v:
        return False
    m = IPV4_RE.match(v)
    if m:
        return all(0 <= int(g) <= 255 for g in m.groups())
    # A value made only of digits and dots is someone typing an address, not a
    # hostname. "192.168.1" is a legal hostname by the letter of the spec, but
    # from a person setting up a printer it is a truncated IP, and accepting it
    # would send them off debugging the network instead of the typo.
    if all(part.isdigit() for part in v.split(".") if part) and any(
            c.isdigit() for c in v):
        return False
    return bool(HOSTNAME_RE.match(v))


def valid_chat_id(value):
    v = str(value or "").strip()
    return bool(v) and (v.lstrip("-").isdigit())


def redact(value):
    """A token reduced to something safe to log or show on screen.

    Keeps the numeric bot id — which is public, it is in the bot's own username
    — and the last three characters, enough for a human to tell two tokens
    apart. The secret half never appears.
    """
    v = str(value or "")
    if not v:
        return "(empty)"
    head, sep, tail = v.partition(":")
    if not sep:
        return "***"
    return "%s:***%s" % (head, tail[-3:] if len(tail) > 6 else "")


def load(path=None):
    """Read config, filling in defaults. Raises ConfigError if unusable."""
    p = path or paths.config_path()
    if not os.path.exists(p):
        raise ConfigError(
            "Configuration not found: %s\nRun Setup.cmd first." % p)
    try:
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        raise ConfigError("Cannot read %s: %s" % (p, e))
    if not isinstance(cfg, dict):
        raise ConfigError("%s does not contain a configuration object." % p)
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    return cfg


def validate(cfg):
    """Return a list of human-readable problems. Empty list means usable."""
    problems = []
    if not valid_token(cfg.get("telegram_token")):
        problems.append("telegram_token is missing or malformed "
                        "(expected the token BotFather gave you)")
    if not valid_chat_id(cfg.get("chat_id")):
        problems.append("chat_id is missing or not a number")
    if not valid_host(cfg.get("printer_ip")):
        problems.append("printer_ip is not a valid IP address or hostname")
    selected = backend_mod.name(cfg)
    if not selected:
        problems.append("backend must be 'sdcp' or 'moonraker'")
    if selected == backend_mod.MOONRAKER:
        url = cfg.get("moonraker_url") or (
            "http://%s" % str(cfg.get("printer_ip") or "").strip())
        if not moonraker.valid_base_url(url):
            problems.append("moonraker_url is not a valid HTTP or HTTPS URL")
    return problems


def load_valid(path=None):
    cfg = load(path)
    problems = validate(cfg)
    if problems:
        raise ConfigError("Configuration is incomplete:\n  - %s\n\nRun Setup.cmd again."
                          % "\n  - ".join(problems))
    return cfg


def save(cfg, path=None):
    """Write config atomically, so a crash mid-write cannot truncate it."""
    p = path or paths.config_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4, sort_keys=True)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # The token lives in this file: keep it out of other users' reach where the
    # platform lets us say so. Best-effort — a failure here is not fatal.
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def summary(cfg):
    """Lines describing the config for a human. Never contains the token."""
    return [
        "Bot token      : %s" % redact(cfg.get("telegram_token")),
        "Owner chat id  : %s" % (cfg.get("chat_id") or "(not set)"),
        "Printer address: %s" % (cfg.get("printer_ip") or "(not set)"),
        "Printer name   : %s" % cfg.get("printer_name"),
        "Backend        : %s" % (backend_mod.name(cfg) or "(invalid)"),
        "Mode           : %s" % (
            "monitoring and permitted controls"
            if backend_mod.allowed_actions(cfg) - backend_mod.READ_ACTIONS
            else "monitoring only"),
        "Camera photos  : %s" % ("on" if cfg.get("send_photo") else "off"),
        "Anonymous stats: %s" % ("on" if cfg.get("anonymous_statistics") else "off"),
    ]
