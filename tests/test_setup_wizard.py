# -*- coding: utf-8 -*-
"""The setup wizard, driven end to end against a fake Telegram.

The chat-id discovery is the part worth testing hardest: it is the step that
replaces "go and message a stranger's bot to learn your own id", and the branch
where several people have written is the one where getting it wrong hands
control of a printer to the wrong person.
"""
import pytest

from centauri_bot import config as config_mod
from centauri_bot import setup_wizard as wiz

from conftest import FakeTelegram, VALID_TOKEN


def update_from(user_id, first_name="Demo", username="demo_user"):
    return {"update_id": user_id, "message": {
        "message_id": 1,
        "chat": {"id": user_id, "type": "private"},
        "from": {"id": user_id, "is_bot": False,
                 "first_name": first_name, "username": username},
        "text": "/start"}}


def factory_for(api):
    return lambda token, **kw: api


# --------------------------------------------------------- token validation

def test_token_format_is_checked_before_any_network_call():
    """A typo should be caught locally, not by a round trip."""
    assert config_mod.valid_token(VALID_TOKEN)
    assert not config_mod.valid_token("obviously-not-a-token")


def test_verify_token_reports_the_bot_username():
    api = FakeTelegram()
    username = wiz.verify_token(VALID_TOKEN, api_factory=factory_for(api))
    assert username == "demo_printer_bot"


def test_verify_token_survives_telegram_being_unreachable():
    """An offline machine must not block setup outright: the rest of the
    wizard still does useful work, and the token was format-checked already."""
    class Offline(object):
        def get_me(self):
            raise OSError("no route to host")

    assert wiz.verify_token(VALID_TOKEN, api_factory=lambda t, **k: Offline()) is None


# ------------------------------------------------------------ chat discovery

def test_single_user_is_found_automatically(capsys):
    api = FakeTelegram(updates=[update_from(555000111)])
    found = wiz.find_chat_id(VALID_TOKEN, "demo_printer_bot",
                             api_factory=factory_for(api),
                             attempts=1, pause=0, sleeper=lambda s: None)
    assert found == "555000111"


def test_bot_messages_are_ignored_during_discovery():
    noise = update_from(111)
    noise["message"]["from"]["is_bot"] = True
    api = FakeTelegram(updates=[noise, update_from(555000111)])
    found = wiz.find_chat_id(VALID_TOKEN, None, api_factory=factory_for(api),
                             attempts=1, pause=0, sleeper=lambda s: None)
    assert found == "555000111"


def test_several_users_are_never_resolved_by_guessing(monkeypatch):
    """Whoever lands in chat_id becomes the only person the bot obeys.
    Silently taking the first is how someone else ends up owning the printer."""
    api = FakeTelegram(updates=[update_from(111111, "First"),
                                update_from(222222, "Second")])
    asked = {}

    def fake_choice(prompt, options):
        asked["options"] = options
        return options[1][0]                 # the human picks the second

    monkeypatch.setattr(wiz, "ask_choice", fake_choice)
    found = wiz.find_chat_id(VALID_TOKEN, None, api_factory=factory_for(api),
                             attempts=1, pause=0, sleeper=lambda s: None)

    assert len(asked["options"]) == 2
    assert found == "222222"
    # Both candidates are shown with enough detail to tell them apart.
    labels = [label for _, label in asked["options"]]
    assert any("First" in l for l in labels)
    assert any("Second" in l for l in labels)


def test_no_messages_offers_manual_entry(monkeypatch):
    api = FakeTelegram(updates=[])
    monkeypatch.setattr(wiz, "ask_yes", lambda *a, **k: True)
    monkeypatch.setattr(wiz, "ask", lambda *a, **k: "555000111")
    found = wiz.find_chat_id(VALID_TOKEN, None, api_factory=factory_for(api),
                             attempts=2, pause=0, sleeper=lambda s: None)
    assert found == "555000111"


def test_no_messages_and_no_manual_entry_returns_nothing(monkeypatch):
    api = FakeTelegram(updates=[])
    monkeypatch.setattr(wiz, "ask_yes", lambda *a, **k: False)
    found = wiz.find_chat_id(VALID_TOKEN, None, api_factory=factory_for(api),
                             attempts=1, pause=0, sleeper=lambda s: None)
    assert found is None


# ------------------------------------------------------------- whole flow

def test_full_wizard_writes_a_usable_config(monkeypatch):
    api = FakeTelegram(updates=[update_from(555000111)])
    monkeypatch.setattr(wiz, "ask_secret", lambda prompt: VALID_TOKEN)
    monkeypatch.setattr(wiz, "ask_yes", lambda *a, **k: True)
    monkeypatch.setattr(wiz, "ask_choice", lambda prompt, options: options[1][0])
    monkeypatch.setattr(wiz, "ask", lambda prompt, default=None: {
        "IP-адрес или имя принтера": "10.0.0.5",
        "Понятное имя": "Demo Centauri",
    }.get(prompt, default or "10.0.0.5"))
    # No sockets: the port probe is the only thing here that would open one.
    monkeypatch.setattr(wiz.sdcp, "tcp_reachable", lambda *a, **k: True)

    code = wiz.run(api_factory=factory_for(api))
    assert code == 0

    cfg = config_mod.load_valid()
    assert cfg["chat_id"] == "555000111"
    assert cfg["printer_ip"] == "10.0.0.5"
    assert cfg["printer_name"] == "Demo Centauri"
    assert cfg["allow_control"] is True
    assert cfg["anonymous_statistics"] is True


def test_declining_statistics_still_saves_a_fully_working_bot(monkeypatch):
    api = FakeTelegram(updates=[update_from(555000111)])
    monkeypatch.setattr(wiz, "ask_secret", lambda prompt: VALID_TOKEN)

    def answer(prompt, default=True):
        return False if "анонимную статистику" in prompt else True

    monkeypatch.setattr(wiz, "ask_yes", answer)
    monkeypatch.setattr(wiz, "ask_choice", lambda prompt, options: options[1][0])
    monkeypatch.setattr(wiz, "ask", lambda prompt, default=None: {
        "IP-адрес или имя принтера": "10.0.0.5",
        "Понятное имя": "Demo Centauri",
    }.get(prompt, default or "10.0.0.5"))
    monkeypatch.setattr(wiz.sdcp, "tcp_reachable", lambda *a, **k: True)

    assert wiz.run(api_factory=factory_for(api)) == 0
    cfg = config_mod.load_valid()
    assert cfg["anonymous_statistics"] is False
    assert cfg["allow_control"] is True
    assert cfg["telegram_token"] == VALID_TOKEN


def test_wizard_stamps_the_install_date_for_the_support_interval(monkeypatch):
    from centauri_bot import storage
    api = FakeTelegram(updates=[update_from(555000111)])
    monkeypatch.setattr(wiz, "ask_secret", lambda prompt: VALID_TOKEN)
    monkeypatch.setattr(wiz, "ask_yes", lambda *a, **k: True)
    monkeypatch.setattr(wiz, "ask_choice", lambda prompt, options: options[1][0])
    monkeypatch.setattr(wiz, "ask", lambda prompt, default=None: default or "10.0.0.5")
    monkeypatch.setattr(wiz.sdcp, "tcp_reachable", lambda *a, **k: True)

    wiz.run(api_factory=factory_for(api))
    assert storage.load_state()["installed_at"] is not None


def test_rerunning_the_wizard_keeps_existing_user_data(monkeypatch, base_config):
    """Re-configuring must not wipe the maintenance counter or the pinned
    message id - those are the user's, not the wizard's."""
    from centauri_bot import storage
    config_mod.save(base_config)
    storage.save_maintenance({"hours": 42.5, "since": 1_000_000.0})
    storage.set_message_id(777)

    api = FakeTelegram(updates=[update_from(555000111)])
    monkeypatch.setattr(wiz, "ask_secret", lambda prompt: VALID_TOKEN)
    monkeypatch.setattr(wiz, "ask_yes", lambda *a, **k: True)
    monkeypatch.setattr(wiz, "ask_choice", lambda prompt, options: options[1][0])
    monkeypatch.setattr(wiz, "ask", lambda prompt, default=None: default or "10.0.0.9")
    monkeypatch.setattr(wiz.sdcp, "tcp_reachable", lambda *a, **k: True)

    wiz.run(api_factory=factory_for(api))

    assert storage.load_maintenance()["hours"] == 42.5
    assert storage.message_id() == 777


def test_declining_the_summary_writes_nothing(monkeypatch):
    api = FakeTelegram(updates=[update_from(555000111)])
    monkeypatch.setattr(wiz, "ask_secret", lambda prompt: VALID_TOKEN)
    monkeypatch.setattr(wiz, "ask_choice", lambda prompt, options: options[1][0])
    monkeypatch.setattr(wiz, "ask", lambda prompt, default=None: default or "10.0.0.5")
    monkeypatch.setattr(wiz.sdcp, "tcp_reachable", lambda *a, **k: True)

    # Answer everything yes except the final "Save?" - keying on the prompt
    # rather than on a call count, which would silently pass if the wizard ever
    # grew or lost a question.
    monkeypatch.setattr(wiz, "ask_yes",
                        lambda prompt, default=True: "Сохранить" not in prompt)

    assert wiz.run(api_factory=factory_for(api)) == 1
    with pytest.raises(config_mod.ConfigError):
        config_mod.load()


def test_summary_shown_to_the_user_hides_the_token(monkeypatch, capsys, base_config):
    for line in config_mod.summary(base_config):
        assert VALID_TOKEN not in line
    joined = "\n".join(config_mod.summary(base_config))
    assert "***" in joined
