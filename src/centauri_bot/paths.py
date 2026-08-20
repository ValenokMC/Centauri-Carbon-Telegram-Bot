# -*- coding: utf-8 -*-
"""Where user data lives.

Nothing the user owns is written next to the code. The repository stays
read-only at runtime, so an update (or a re-extracted ZIP) can never wipe a
token, and a config file can never be committed by accident.
"""
import os


APP_DIR_NAME = "CentauriCarbonTelegramBot"


def data_dir():
    r"""%LOCALAPPDATA%\CentauriCarbonTelegramBot, created on demand.

    CENTAURI_BOT_DATA_DIR overrides it. Tests set that variable to a temporary
    directory; without it a test run would litter — or overwrite — the real
    profile of whoever runs the suite.
    """
    override = os.environ.get("CENTAURI_BOT_DATA_DIR")
    if override:
        base = override
    else:
        local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        base = os.path.join(local, APP_DIR_NAME)
    os.makedirs(base, exist_ok=True)
    return base


def config_path():
    return os.path.join(data_dir(), "config.json")


def state_path():
    return os.path.join(data_dir(), "state.json")


def maintenance_path():
    return os.path.join(data_dir(), "maintenance.json")


def seen_codes_path():
    return os.path.join(data_dir(), "status-codes.txt")


def logs_dir():
    d = os.path.join(data_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def backups_dir():
    d = os.path.join(data_dir(), "backups")
    os.makedirs(d, exist_ok=True)
    return d
