# -*- coding: utf-8 -*-
"""Backend permissions and stale-button safety."""
from centauri_bot import backend


def test_stock_backend_keeps_existing_control_policy(base_config):
    assert backend.name(base_config) == backend.SDCP
    allowed = backend.allowed_actions(base_config)
    assert backend.PAUSE in allowed
    assert backend.START in allowed
    assert backend.TEMPERATURE in allowed


def test_moonraker_is_monitoring_only_after_migration(base_config):
    base_config["backend"] = "moonraker"
    allowed = backend.allowed_actions(base_config)
    assert backend.FILES in allowed
    assert backend.SNAPSHOT in allowed
    assert backend.PAUSE not in allowed
    assert backend.START not in allowed
    assert backend.TEMPERATURE not in allowed


def test_moonraker_job_control_and_remote_start_are_separate_opt_ins(base_config):
    base_config.update({
        "backend": "moonraker",
        "moonraker_allow_job_control": True,
        "moonraker_allow_remote_start": False,
    })
    allowed = backend.allowed_actions(base_config)
    assert {backend.PAUSE, backend.RESUME, backend.CANCEL} <= allowed
    assert backend.START not in allowed

    base_config["moonraker_allow_remote_start"] = True
    assert backend.START in backend.allowed_actions(base_config)


def test_global_monitoring_mode_overrides_every_control_opt_in(base_config):
    base_config.update({
        "backend": "moonraker",
        "allow_control": False,
        "moonraker_allow_job_control": True,
        "moonraker_allow_remote_start": True,
    })
    assert backend.allowed_actions(base_config) == backend.READ_ACTIONS


def test_confirmation_is_one_use_bound_to_value_and_expires():
    now = [100.0]
    store = backend.ConfirmationStore(clock=lambda: now[0], ttl=10)
    token = store.issue("print", "folder/original.gcode")

    assert store.consume("other-kind", token) is None
    # A kind mismatch consumes the token rather than leaving a guessing oracle.
    assert store.consume("print", token) is None

    token = store.issue("print", "folder/original.gcode")
    assert store.consume("print", token) == "folder/original.gcode"
    assert store.consume("print", token) is None

    token = store.issue("print", "folder/later.gcode")
    now[0] = 111.0
    assert store.consume("print", token) is None

