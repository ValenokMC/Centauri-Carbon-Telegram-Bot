# -*- coding: utf-8 -*-
"""Anonymous statistics are opt-in, minimal and never affect the bot."""
import json
import uuid

from centauri_bot import __version__
from centauri_bot import storage
from centauri_bot import telemetry


class Response(object):
    def __init__(self, code=204):
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getcode(self):
        return self.code


class Recorder(object):
    def __init__(self, code=204, error=None):
        self.code = code
        self.error = error
        self.calls = []

    def __call__(self, req, timeout):
        self.calls.append((req, timeout))
        if self.error:
            raise self.error
        return Response(self.code)


def test_opt_out_opens_no_network_and_creates_no_identifier():
    rec = Recorder()
    assert telemetry.report_once({"anonymous_statistics": False}, now=1000,
                                 opener=rec) is False
    assert rec.calls == []
    state = storage.load_state()
    assert state["telemetry_installation_id"] is None
    assert state["last_telemetry_at"] is None


def test_opt_in_sends_only_the_documented_fields_and_stamps_success():
    rec = Recorder()
    assert telemetry.report_once({"anonymous_statistics": True}, now=1000,
                                 opener=rec) is True
    assert len(rec.calls) == 1
    req, timeout = rec.calls[0]
    payload = json.loads(req.data.decode("utf-8"))

    assert req.full_url == telemetry.ENDPOINT
    assert req.full_url.startswith("https://")
    assert timeout <= 5
    assert set(payload) == {"schema", "project", "installation_id", "version"}
    assert payload["schema"] == 1
    assert payload["project"] == "centauri_bot"
    assert payload["version"] == __version__
    uuid.UUID(payload["installation_id"])

    state = storage.load_state()
    assert state["telemetry_installation_id"] == payload["installation_id"]
    assert state["last_telemetry_at"] == 1000


def test_success_is_not_sent_again_before_thirty_days():
    rec = Recorder()
    cfg = {"anonymous_statistics": True}
    assert telemetry.report_once(cfg, now=1000, opener=rec)
    assert not telemetry.report_once(cfg, now=1000 + telemetry.INTERVAL_SEC - 1,
                                     opener=rec)
    assert len(rec.calls) == 1


def test_monthly_report_reuses_the_random_installation_id():
    rec = Recorder()
    cfg = {"anonymous_statistics": True}
    assert telemetry.report_once(cfg, now=1000, opener=rec)
    first = json.loads(rec.calls[0][0].data.decode("utf-8"))["installation_id"]
    assert telemetry.report_once(cfg, now=1000 + telemetry.INTERVAL_SEC, opener=rec)
    second = json.loads(rec.calls[1][0].data.decode("utf-8"))["installation_id"]
    assert first == second


def test_failure_never_raises_and_does_not_claim_success():
    rec = Recorder(error=OSError("offline"))
    assert telemetry.report_once({"anonymous_statistics": True}, now=1000,
                                 opener=rec) is False
    state = storage.load_state()
    # Keep the id for a later retry, otherwise restarts would inflate installs.
    assert state["telemetry_installation_id"]
    assert state["last_telemetry_at"] is None


def test_withdrawing_consent_forgets_the_local_identifier():
    storage.update_state(telemetry_installation_id=str(uuid.uuid4()),
                         last_telemetry_at=123)
    storage.clear_telemetry()
    state = storage.load_state()
    assert state["telemetry_installation_id"] is None
    assert state["last_telemetry_at"] is None
