# -*- coding: utf-8 -*-
"""Test fixtures: a fake Telegram, a fake printer, and an isolated data dir.

Two things are guaranteed here, and there is a test that checks each of them:

  * no test ever opens a socket. FakeTelegram replaces the API object outright,
    and nothing in the suite constructs a real TelegramAPI.
  * no test ever writes to the real %LOCALAPPDATA%. The data-dir fixture is
    autouse, so even a test that forgets to ask for it is isolated.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from centauri_bot import config as config_mod   # noqa: E402
from centauri_bot import paths, storage         # noqa: E402


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch):
    """Collapse the post-command settle delays.

    They matter against a real printer and not at all here, and left alone they
    turn a fast suite into a slow one nobody runs before committing.
    """
    from centauri_bot import handlers
    monkeypatch.setattr(handlers, "SETTLE_SEC", 0)
    monkeypatch.setattr(handlers, "SETTLE_AFTER_ACTION_SEC", 0)


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Point every path at a temporary directory, for every test."""
    target = tmp_path / "appdata"
    monkeypatch.setenv("CENTAURI_BOT_DATA_DIR", str(target))
    yield target


class FakeTelegram(object):
    """Records calls instead of making them.

    Deliberately not a mock library object: assertions in the tests read as
    "the third message had these buttons", which is the thing under test, and a
    hand-written recorder makes that legible.
    """

    def __init__(self, token="123456789:FAKEfakeFAKEfakeFAKEfakeFAKEfake12345",
                 updates=None, fail_edits=False):
        self.token = token
        self.sent = []          # (chat, text, keyboard, has_photo)
        self.edited = []        # (chat, message_id, text, keyboard)
        self.deleted = []
        self.answers = []
        self.commands = None
        self._updates = list(updates or [])
        self._next_id = 1000
        self.fail_edits = fail_edits
        self.me = {"id": 123456789, "username": "demo_printer_bot", "is_bot": True}

    # -- the surface app.py and handlers.py actually use ------------------

    def get_me(self):
        return dict(self.me)

    def get_updates(self, offset=None, timeout=25, allowed=None):
        batch, self._updates = self._updates, []
        return {"ok": True, "result": batch}

    def send_message(self, chat, text, keyboard=None, photo=None):
        self._next_id += 1
        self.sent.append((str(chat), text, keyboard, bool(photo)))
        return {"ok": True, "result": {"message_id": self._next_id}}

    def edit_message(self, chat, message_id, text, keyboard=None,
                     photo=None, is_photo=False):
        if self.fail_edits:
            return {"ok": False, "description": "message to edit not found"}
        self.edited.append((str(chat), message_id, text, keyboard))
        return {"ok": True, "result": {"message_id": message_id}}

    def delete_message(self, chat, message_id):
        self.deleted.append((str(chat), message_id))
        return {"ok": True}

    def answer_callback(self, callback_id, text=None):
        self.answers.append((callback_id, text))
        return {"ok": True}

    def set_my_commands(self, commands):
        self.commands = commands
        return {"ok": True}

    # -- helpers for assertions ------------------------------------------

    def texts(self):
        return [t for _, t, _, _ in self.sent]

    def all_keyboards(self):
        out = [k for _, _, k, _ in self.sent if k]
        out += [k for _, _, _, k in [(a, b, c, d) for a, b, c, d in self.edited] if k]
        return out

    @staticmethod
    def button_texts(keyboard):
        return [b.get("text", "") for row in (keyboard or []) for b in row]

    @staticmethod
    def button_urls(keyboard):
        return [b["url"] for row in (keyboard or []) for b in row if "url" in b]


@pytest.fixture
def fake_telegram():
    return FakeTelegram()


VALID_TOKEN = "123456789:FAKEfakeFAKEfakeFAKEfakeFAKEfake12345"


@pytest.fixture
def base_config():
    """A complete, valid configuration made entirely of invented values."""
    cfg = dict(config_mod.DEFAULTS)
    cfg.update({
        "telegram_token": VALID_TOKEN,
        "chat_id": "555000111",
        "owner_user_id": "555000111",
        "printer_ip": "10.0.0.5",
        "printer_name": "Demo Centauri",
        "send_photo": False,        # keeps the fake printer out of the way
        "allow_control": True,
    })
    return cfg


@pytest.fixture
def bot(base_config, fake_telegram):
    """A Bot wired to fakes, with the clock under the test's control."""
    from centauri_bot.app import Bot

    class Clock(object):
        def __init__(self):
            self.now = 1_700_000_000.0

        def __call__(self):
            return self.now

        def advance_days(self, days):
            self.now += days * 86400

    clock = Clock()
    b = Bot(base_config, api=fake_telegram, clock=clock)
    b.clock_control = clock
    return b


# ------------------------------------------------------------------ printer

def status(code, filename="", progress=0, task="task-1", layer=1, total_layer=100,
           ticks=0, total_ticks=3600, **extra):
    """Build a Status dict shaped like the printer's, with invented values."""
    info = {"Status": code, "Progress": progress, "CurrentLayer": layer,
            "TotalLayer": total_layer, "CurrentTicks": ticks,
            "TotalTicks": total_ticks}
    if filename:
        info["Filename"] = filename
        info["TaskId"] = task
    payload = {"PrintInfo": info, "TempOfNozzle": 210, "TempTargetNozzle": 210,
               "TempOfHotbed": 60, "TempTargetHotbed": 60, "TempOfBox": 30,
               "CurrentFanSpeed": {"ModelFan": 100, "BoxFan": 0, "AuxiliaryFan": 0},
               "LightStatus": {"SecondLight": 1}}
    payload.update(extra)
    return payload
