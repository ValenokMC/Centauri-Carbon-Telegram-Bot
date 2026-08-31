# -*- coding: utf-8 -*-
"""Read-only diagnostics select the configured backend."""
from centauri_bot import __main__ as entry
from centauri_bot import config as config_mod
from centauri_bot import moonraker


def test_check_uses_moonraker_without_printing_api_key(
        base_config, monkeypatch, capsys):
    base_config.update({
        "backend": "moonraker",
        "moonraker_url": "http://printer.local",
        "moonraker_api_key": "never-print-this-key",
    })
    config_mod.save(base_config)

    class FakeClient(object):
        def __init__(self, url, api_key="", **kwargs):
            assert url == "http://printer.local"
            assert api_key == "never-print-this-key"

        def status(self):
            return {"PrintInfo": {"Status": 0}}

        def camera_available(self):
            return True

    monkeypatch.setattr(moonraker, "Client", FakeClient)
    assert entry.cmd_check() == 0
    shown = capsys.readouterr().out
    assert "Moonraker" in shown
    assert "never-print-this-key" not in shown

