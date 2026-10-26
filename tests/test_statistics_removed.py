# -*- coding: utf-8 -*-
"""The anonymous statistics of 1.1.0-1.1.2 are gone, leftovers included.

An installation that had opted in keeps a random id in state.json. The bot
deletes it on the next start; these tests hold that to the letter and check
that nothing else in the file is touched on the way.
"""
import importlib
import json
import os

import pytest

from centauri_bot import paths, storage


def read_raw_state():
    with open(paths.state_path(), encoding="utf-8") as f:
        return json.load(f)


def test_the_statistics_module_no_longer_exists():
    with pytest.raises(ImportError):
        importlib.import_module("centauri_bot.telemetry")


def test_forget_statistics_deletes_the_leftover_id_and_keeps_the_rest():
    storage.save_state({
        "message_id": 42,
        "installed_at": 1000.0,
        "last_support_reminder_at": None,
        "telemetry_installation_id": "0b5c2a0e-8a63-4f0e-9d1b-1f3e6c2a9a11",
        "last_telemetry_at": 2000,
    })

    assert storage.forget_statistics() is True

    raw = read_raw_state()
    assert "telemetry_installation_id" not in raw
    assert "last_telemetry_at" not in raw
    assert raw["message_id"] == 42
    assert raw["installed_at"] == 1000.0


def test_forget_statistics_leaves_a_clean_state_file_alone():
    storage.save_state({"message_id": 7, "installed_at": 1.0,
                        "last_support_reminder_at": None})
    before = os.stat(paths.state_path()).st_mtime_ns

    assert storage.forget_statistics() is False
    assert os.stat(paths.state_path()).st_mtime_ns == before


def test_forget_statistics_does_not_create_a_state_file():
    assert not os.path.exists(paths.state_path())
    assert storage.forget_statistics() is False
    assert not os.path.exists(paths.state_path())
