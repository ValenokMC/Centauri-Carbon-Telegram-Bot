# -*- coding: utf-8 -*-
"""Configuration: validation, atomic save, and the redaction guarantee."""
import json
import os

import pytest

from centauri_bot import config as config_mod
from centauri_bot import paths


VALID_TOKEN = "123456789:FAKEfakeFAKEfakeFAKEfakeFAKEfake12345"


# ------------------------------------------------------------------ tokens

@pytest.mark.parametrize("value", [
    VALID_TOKEN,
    "1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw-_",
])
def test_valid_tokens_are_accepted(value):
    assert config_mod.valid_token(value)


@pytest.mark.parametrize("value", [
    "", None, "nonsense", "123:short",
    "abcdefghij:FAKEfakeFAKEfakeFAKEfakeFAKEfake12345",   # non-numeric bot id
    "123456789 FAKEfakeFAKEfakeFAKEfakeFAKEfake12345",    # space, not colon
])
def test_malformed_tokens_are_rejected(value):
    assert not config_mod.valid_token(value)


def test_redact_never_reveals_the_secret_half():
    redacted = config_mod.redact(VALID_TOKEN)
    secret = VALID_TOKEN.split(":", 1)[1]
    assert secret not in redacted
    assert redacted.startswith("123456789:")
    # Enough of a fingerprint to tell two tokens apart, and no more.
    assert len(redacted) < len(VALID_TOKEN)


def test_redact_handles_empty_and_malformed():
    assert config_mod.redact("") == "(empty)"
    assert config_mod.redact(None) == "(empty)"
    assert config_mod.redact("no-colon-here") == "***"


def test_summary_never_contains_the_token(base_config):
    text = "\n".join(config_mod.summary(base_config))
    assert base_config["telegram_token"] not in text
    assert base_config["telegram_token"].split(":", 1)[1] not in text


# ------------------------------------------------------------------ hosts

@pytest.mark.parametrize("value", ["192.168.1.10", "10.0.0.5", "printer.local",
                                   "centauri", "my-printer.lan"])
def test_valid_hosts(value):
    assert config_mod.valid_host(value)


@pytest.mark.parametrize("value", ["", None, "999.1.1.1", "192.168.1",
                                   "192.168.1.256", "has space", "-bad.local"])
def test_invalid_hosts(value):
    assert not config_mod.valid_host(value)


@pytest.mark.parametrize("value,expected", [
    ("555000111", True), ("-1001234567890", True),
    ("", False), (None, False), ("abc", False), ("12.5", False),
])
def test_chat_id_validation(value, expected):
    assert config_mod.valid_chat_id(value) is expected


# ------------------------------------------------------------------ load/save

def test_load_missing_config_raises_with_a_useful_message():
    with pytest.raises(config_mod.ConfigError) as excinfo:
        config_mod.load()
    assert "Setup.cmd" in str(excinfo.value)


def test_save_then_load_round_trips(base_config):
    config_mod.save(base_config)
    loaded = config_mod.load()
    assert loaded["chat_id"] == base_config["chat_id"]
    assert loaded["printer_ip"] == base_config["printer_ip"]


def test_load_fills_in_defaults_for_missing_keys(base_config):
    trimmed = {"telegram_token": VALID_TOKEN, "chat_id": "1", "printer_ip": "10.0.0.1"}
    config_mod.save(trimmed)
    loaded = config_mod.load()
    assert loaded["keepalive_sec"] == config_mod.DEFAULTS["keepalive_sec"]
    assert loaded["maintenance_hours"] == config_mod.DEFAULTS["maintenance_hours"]
    assert loaded["anonymous_statistics"] is False
    assert loaded["backend"] == "sdcp"
    assert loaded["moonraker_allow_remote_start"] is False


def test_moonraker_config_requires_a_safe_url_or_printer_host(base_config):
    base_config["backend"] = "moonraker"
    base_config["moonraker_url"] = "http://printer.local"
    assert config_mod.validate(base_config) == []

    base_config["moonraker_url"] = "ftp://printer.local"
    assert any("moonraker_url" in item for item in config_mod.validate(base_config))


def test_summary_never_contains_moonraker_api_key(base_config):
    base_config.update({
        "backend": "moonraker",
        "moonraker_url": "http://printer.local",
        "moonraker_api_key": "moonraker-secret-value",
    })
    assert "moonraker-secret-value" not in "\n".join(config_mod.summary(base_config))


def test_validate_lists_every_problem_at_once():
    problems = config_mod.validate({"telegram_token": "", "chat_id": "",
                                    "printer_ip": ""})
    assert len(problems) == 3


def test_load_valid_refuses_an_incomplete_config():
    config_mod.save({"telegram_token": "", "chat_id": "", "printer_ip": ""})
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_valid()


def test_save_is_atomic_and_leaves_no_temp_files(base_config):
    config_mod.save(base_config)
    leftovers = [n for n in os.listdir(paths.data_dir()) if n.endswith(".tmp")]
    assert leftovers == []


def test_config_is_written_outside_the_repository(base_config):
    """The whole point of paths.data_dir(): a token can never be committed."""
    written = config_mod.save(base_config)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(config_mod.__file__)))
    assert not os.path.abspath(written).startswith(os.path.abspath(repo))


def test_example_config_in_the_repository_holds_no_real_values():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "examples", "config.example.json"),
              encoding="utf-8") as f:
        example = json.load(f)
    assert example["telegram_token"] == ""
    assert example["chat_id"] == ""
    assert example["printer_ip"] == ""
    assert not config_mod.valid_token(example["telegram_token"])
