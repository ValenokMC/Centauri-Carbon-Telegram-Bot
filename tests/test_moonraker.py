# -*- coding: utf-8 -*-
"""Moonraker translation and HTTP client tests; no socket is opened."""
import io
import json
import urllib.parse

import pytest

from centauri_bot import moonraker


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class FakeOpener(object):
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        value = self.replies.pop(0)
        if isinstance(value, bytes):
            return Response(value)
        return Response(json.dumps(value).encode("utf-8"))


def printing_objects():
    return {
        "webhooks": {"state": "ready"},
        "print_stats": {
            "state": "printing", "filename": "parts/cube.gcode",
            "print_duration": 600, "info": {"current_layer": 12, "total_layer": 40},
        },
        "virtual_sdcard": {"progress": 0.25, "file_path": "parts/cube.gcode"},
        "display_status": {"progress": 0.24},
        "extruder": {"temperature": 211.2, "target": 215},
        "heater_bed": {"temperature": 59.8, "target": 60},
        "fan": {"speed": 0.5},
        "gcode_move": {"gcode_position": [10, 20, 3.4, 1]},
    }


def test_normalize_printing_status_matches_existing_ui_shape():
    status = moonraker.normalize_status(printing_objects())
    info = status["PrintInfo"]
    assert info["Status"] == 13
    assert info["Filename"] == "parts/cube.gcode"
    assert info["Progress"] == 25
    assert info["CurrentLayer"] == 12
    assert info["TotalTicks"] == 2400
    assert status["TempOfNozzle"] == 211.2
    assert status["CurrentFanSpeed"]["ModelFan"] == 50
    assert status["CurrentCoord"]["Z"] == 3.4


@pytest.mark.parametrize("state,code", [
    ("standby", 0), ("paused", 6), ("complete", 9),
    ("cancelled", 8), ("error", 77),
])
def test_normalize_print_states(state, code):
    objects = printing_objects()
    objects["print_stats"]["state"] = state
    assert moonraker.normalize_status(objects)["PrintInfo"]["Status"] == code


def test_client_queries_documented_objects_and_sends_api_key_as_header():
    fake = FakeOpener([{"result": {"status": printing_objects()}}])
    client = moonraker.Client("http://printer.local", api_key="top-secret",
                              opener=fake)
    status = client.status()

    request, timeout = fake.requests[0]
    assert status["PrintInfo"]["Progress"] == 25
    assert request.get_header("X-api-key") == "top-secret"
    assert "top-secret" not in request.full_url
    assert "/printer/objects/query?webhooks&virtual_sdcard&print_stats" in request.full_url
    assert timeout == 5


def test_file_list_filters_non_gcode_and_start_encodes_exact_path():
    fake = FakeOpener([
        {"result": [
            {"path": "parts/A & B.gcode"}, {"path": "notes.txt"},
            {"path": "parts/second.GCO"},
        ]},
        {"result": "ok"},
    ])
    client = moonraker.Client("http://printer.local", opener=fake)
    assert client.list_files() == ["parts/A & B.gcode", "parts/second.GCO"]
    client.start("parts/A & B.gcode")

    request, _ = fake.requests[1]
    assert request.get_method() == "POST"
    assert urllib.parse.parse_qs(request.data.decode()) == {
        "filename": ["parts/A & B.gcode"]}


def test_camera_refuses_unapproved_external_host_before_fetching_image():
    fake = FakeOpener([{"result": {"webcams": [{
        "enabled": True, "snapshot_url": "http://camera.example/snapshot.jpg",
    }]}}])
    client = moonraker.Client("http://printer.local", opener=fake)
    with pytest.raises(moonraker.MoonrakerError, match="запрещён"):
        client.grab_frame()
    assert len(fake.requests) == 1


@pytest.mark.parametrize("value", [
    "", "printer.local", "ftp://printer.local", "http://user:pass@printer.local",
    "http://printer.local/?token=secret",
])
def test_invalid_base_urls_are_rejected(value):
    assert not moonraker.valid_base_url(value)

