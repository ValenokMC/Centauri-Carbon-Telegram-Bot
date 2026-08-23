# -*- coding: utf-8 -*-
"""Guarantees the rest of the suite silently depends on.

If any of these fail, the other tests stop being trustworthy: they would be
talking to the real Telegram, or writing into the real user profile, or leaking
a token into a log that someone is about to attach to a bug report.
"""
import logging
import os
import re

import pytest

from centauri_bot import logging_setup, paths, storage


# ------------------------------------------------------- no real filesystem

def test_data_dir_is_redirected_during_tests(isolated_data_dir):
    assert str(isolated_data_dir) == paths.data_dir()
    real = os.environ.get("LOCALAPPDATA", "")
    if real:
        assert not paths.data_dir().startswith(os.path.join(real, "CentauriCarbon"))


def test_every_path_stays_inside_the_data_dir():
    root = os.path.abspath(paths.data_dir())
    for path in (paths.config_path(), paths.state_path(), paths.maintenance_path(),
                 paths.seen_codes_path(), paths.logs_dir(), paths.backups_dir()):
        assert os.path.abspath(path).startswith(root)


# ---------------------------------------------------------- no real network

def test_no_test_constructs_a_real_telegram_client():
    """A grep, not a runtime check: the point is that nobody adds one later."""
    here = os.path.dirname(os.path.abspath(__file__))
    offenders = []
    for name in os.listdir(here):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(here, name), encoding="utf-8") as f:
            body = f.read()
        # Importing the name for a type check is fine; calling it is not.
        if re.search(r"TelegramAPI\s*\(", body):
            offenders.append(name)
    assert offenders == []


def test_sockets_are_never_opened_by_the_pure_modules(monkeypatch):
    import socket

    def refuse(*a, **k):
        raise AssertionError("a test tried to open a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)

    from centauri_bot import printer_state as ps
    from centauri_bot import ui, support, telemetry
    life = ps.PrinterLifecycle()
    life.observe({"PrintInfo": {"Status": 13, "Filename": "a.gcode",
                                "TaskId": "t", "Progress": 5}})
    ui.render({"PrintInfo": {"Status": 13}}, True, "Demo")
    support.due({"installed_at": 0})
    telemetry.due({"last_telemetry_at": None}, now=1)


# ------------------------------------------------------------- log hygiene

REAL_SHAPED_TOKEN = "7654321098:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


def test_scrub_removes_a_bare_token():
    scrubbed = logging_setup.scrub("failed with %s" % REAL_SHAPED_TOKEN)
    assert "AAHdqTcvCH" not in scrubbed
    assert "7654321098:***" in scrubbed


def test_scrub_removes_a_token_inside_an_api_path():
    line = "POST /bot%s/sendMessage failed" % REAL_SHAPED_TOKEN
    scrubbed = logging_setup.scrub(line)
    assert "AAHdqTcvCH" not in scrubbed
    assert "sendMessage" in scrubbed          # the useful part survives


def test_log_file_never_contains_the_token(tmp_path):
    logging_setup.configure("DEBUG", to_console=False, directory=str(tmp_path))
    log = logging.getLogger("test")
    log.warning("token is %s", REAL_SHAPED_TOKEN)
    log.warning("url /bot%s/getUpdates", REAL_SHAPED_TOKEN)
    for handler in logging.getLogger().handlers:
        handler.flush()

    body = (tmp_path / logging_setup.LOG_NAME).read_text(encoding="utf-8")
    assert "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw" not in body
    assert "***" in body


def test_log_rotation_is_bounded():
    """A log that grows forever is a log that fills a disk."""
    assert logging_setup.MAX_BYTES <= 5_000_000
    assert logging_setup.BACKUP_COUNT >= 1


# ------------------------------------------------------------ state on disk

def test_state_writes_are_atomic_and_leave_no_temp_files():
    storage.update_state(message_id=1)
    storage.update_state(message_id=2)
    leftovers = [n for n in os.listdir(paths.data_dir()) if n.startswith(".tmp-")]
    assert leftovers == []
    assert storage.load_state()["message_id"] == 2


def test_corrupt_state_file_falls_back_to_defaults_instead_of_crashing():
    with open(paths.state_path(), "w", encoding="utf-8") as f:
        f.write("{ this is not json")
    assert storage.load_state()["message_id"] is None


def test_unknown_status_codes_are_recorded_once():
    storage.remember_code(77)
    storage.remember_code(77)
    storage.remember_code(78)
    with open(paths.seen_codes_path(), encoding="utf-8") as f:
        lines = [l for l in f if l.strip()]
    assert len(lines) == 2
