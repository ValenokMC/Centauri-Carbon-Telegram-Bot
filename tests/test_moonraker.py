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
        "exclude_object": {
            "objects": [
                {"name": "CUBE.DRC_ID_0_COPY_0", "center": [140, 128]},
                {"name": "PLUG.DRC_ID_1_COPY_0", "center": [112, 128]},
            ],
            "excluded_objects": [],
            "current_object": "CUBE.DRC_ID_0_COPY_0",
        },
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
    assert status["ExcludeObject"] == {
        "Objects": ["CUBE.DRC_ID_0_COPY_0", "PLUG.DRC_ID_1_COPY_0"],
        "ExcludedObjects": [], "CurrentObject": "CUBE.DRC_ID_0_COPY_0",
    }


def test_normalize_cosmos_reads_fan_generic_enclosure_fans():
    objects = printing_objects()
    objects["fan_generic aux_fan"] = {"speed": 0.25}
    objects["fan_generic case_fan"] = {"speed": 0.75}
    fans = moonraker.normalize_status(objects)["CurrentFanSpeed"]
    assert fans == {"ModelFan": 50, "BoxFan": 75, "AuxiliaryFan": 25}


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
    assert "fan_generic%20aux_fan" in request.full_url
    assert "fan_generic%20case_fan" in request.full_url
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


def test_file_records_keep_safe_metadata_and_delete_url_encodes_the_filename():
    fake = FakeOpener([
        {"result": [
            {"path": "new.gcode", "size": 2_500_000, "modified": 200},
            {"path": "old.gcode", "size": 10, "modified": 100},
            {"path": "../escape.gcode", "size": 99},
        ]},
        {"result": {"item": {"path": "folder/A & B.gcode"}}},
    ])
    client = moonraker.Client("http://printer.local", opener=fake)
    assert client.list_file_records() == [
        {"path": "new.gcode", "size": 2_500_000, "modified": 200, "permissions": ""},
        {"path": "old.gcode", "size": 10, "modified": 100, "permissions": ""},
    ]
    client.delete("folder/A & B.gcode")
    request, _ = fake.requests[1]
    assert request.get_method() == "DELETE"
    assert request.full_url.endswith("/server/files/gcodes/folder/A%20%26%20B.gcode")


@pytest.mark.parametrize("path", ["", "/tmp/no.gcode", "../no.gcode", "folder/../no.gcode",
                                   "notes.txt", "bad\\..\\no.gcode"])
def test_unsafe_gcode_paths_are_rejected_without_http(path):
    fake = FakeOpener([])
    client = moonraker.Client("http://printer.local", opener=fake)
    with pytest.raises(moonraker.MoonrakerError, match="некорректный"):
        client.delete(path)
    assert fake.requests == []


def test_diagnostics_uses_only_documented_read_endpoints():
    fake = FakeOpener([
        {"result": {"moonraker_version": "v0.9", "klippy_state": "ready",
                    "warnings": ["one"], "failed_components": []}},
        {"result": {"software_version": "v0.13", "state": "ready"}},
        {"result": {"objects": ["webhooks", "toolhead", "extruder"]}},
    ])
    client = moonraker.Client("http://printer.local", opener=fake)
    assert client.diagnostics() == {
        "moonraker_version": "v0.9", "klippy_state": "ready", "klippy_message": "",
        "klipper_version": "v0.13", "warnings": 1, "failed_components": 0,
        "object_count": 3,
    }
    assert [request.get_method() for request, _ in fake.requests] == ["GET", "GET", "GET"]


def test_macros_history_and_saved_mesh_use_read_endpoints_until_macro_is_confirmed():
    fake = FakeOpener([
        {"result": {"objects": ["gcode_macro LOAD_FILAMENT", "gcode_macro _INTERNAL", "toolhead"]}},
        {"result": {"jobs": [{"filename": "cube.gcode", "status": "completed"}]}},
        {"result": {"status": {"bed_mesh": {"profile_name": "default", "profiles": {
            "default": {"points": [[-0.1, 0.0], [0.1, 0.2]]}}}}}},
        {"result": "ok"},
    ])
    client = moonraker.Client("http://printer.local", opener=fake)
    assert client.list_macros() == ["LOAD_FILAMENT"]
    assert client.history() == [{"filename": "cube.gcode", "status": "completed"}]
    assert client.bed_mesh()["points"][1][1] == 0.2
    client.run_macro("load_filament")
    request, _ = fake.requests[-1]
    assert request.get_method() == "POST"
    assert urllib.parse.parse_qs(request.data.decode()) == {"script": ["LOAD_FILAMENT"]}


def test_hardware_controls_only_emit_fixed_validated_cosmos_commands():
    fake = FakeOpener([{"result": "ok"}] * 4)
    client = moonraker.Client("http://printer.local", opener=fake)
    client.set_light(True)
    client.set_speed(125)
    client.set_temperatures(245, 80)
    client.set_fans({"ModelFan": 100, "AuxiliaryFan": 50, "BoxFan": 0})
    scripts = [urllib.parse.parse_qs(request.data.decode())["script"][0]
               for request, _ in fake.requests]
    assert scripts == [
        "SET_LED LED=case WHITE=1\nSYNC_CAMERA_LED", "M220 S125",
        "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=245\nSET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=80",
        "M106 P1 S255\nM106 P2 S128\nM106 P3 S0",
    ]
    with pytest.raises(moonraker.MoonrakerError, match="недопустимая"):
        client.set_speed(101)


def test_object_exclusion_rechecks_live_job_and_emits_one_fixed_command():
    fake = FakeOpener([
        {"result": {"status": printing_objects()}},
        {"result": "ok"},
    ])
    client = moonraker.Client("http://printer.local", opener=fake)
    state = client.exclude_object_state()
    assert state["Filename"] == "parts/cube.gcode"
    assert state["PrintState"] == "printing"
    client.exclude_object("PLUG.DRC_ID_1_COPY_0")
    request, _ = fake.requests[-1]
    assert urllib.parse.parse_qs(request.data.decode()) == {
        "script": ["EXCLUDE_OBJECT NAME=PLUG.DRC_ID_1_COPY_0"]}

    with pytest.raises(moonraker.MoonrakerError, match="некорректное"):
        client.exclude_object("cube\nCANCEL_PRINT")
    assert len(fake.requests) == 2


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
