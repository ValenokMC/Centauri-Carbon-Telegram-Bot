# -*- coding: utf-8 -*-
"""Uploading a G-code, reading its summary and the filament sensor; no socket."""
import pytest

from centauri_bot import moonraker

from test_moonraker import FakeOpener


def client(replies, **kwargs):
    fake = FakeOpener(replies)
    return moonraker.Client("http://printer.local", opener=fake, **kwargs), fake


def test_upload_sends_multipart_to_the_gcodes_root_and_returns_the_stored_path():
    api, fake = client([{"result": {"item": {"path": "Сборка PLA.gcode", "root": "gcodes"},
                                    "action": "create_file"}}], api_key="k")
    assert api.upload("Сборка PLA.gcode", b"G1 X1\n") == "Сборка PLA.gcode"

    request, timeout = fake.requests[0]
    assert request.get_method() == "POST"
    assert request.full_url.endswith("/server/files/upload")
    assert request.get_header("Content-type").startswith("multipart/form-data; boundary=")
    assert request.get_header("X-api-key") == "k"
    assert b'name="root"\r\n\r\ngcodes\r\n' in request.data
    assert 'filename="Сборка PLA.gcode"'.encode("utf-8") in request.data
    assert b"G1 X1\n" in request.data
    assert timeout >= moonraker.UPLOAD_TIMEOUT_SEC


def test_an_upload_answer_without_the_result_wrapper_is_understood():
    api, _ = client([{"item": {"path": "a.gcode"}, "action": "create_file"}])
    assert api.upload("a.gcode", b"G1") == "a.gcode"


@pytest.mark.parametrize("name,data", [
    ("notes.txt", b"x"), ("../a.gcode", b"x"), ("dir/a.gcode", b"x"), ("a.gcode", b""),
    ("a\r\nContent-Disposition: form-data; name=\"root\"\r\n\r\nconfig.gcode", b"x"),
])
def test_upload_refuses_bad_names_and_empty_files_before_any_request(name, data):
    api, fake = client([])
    with pytest.raises(moonraker.MoonrakerError):
        api.upload(name, data)
    assert fake.requests == []


def test_file_metadata_keeps_only_the_short_summary():
    api, fake = client([{"result": {
        "estimated_time": 4920.4, "filament_name": "eSUN PLA+", "filament_weight_total": 48.21,
        "layer_height": 0.2, "size": 1919426, "thumbnails": [{"data": "…"}]}}])
    assert api.file_metadata("a b.gcode") == {
        "estimated_time": 4920, "size": 1919426, "filament_weight_total": 48.21,
        "layer_height": 0.2, "filament_name": "eSUN PLA+"}
    assert "filename=a+b.gcode" in fake.requests[0][0].full_url


@pytest.mark.parametrize("sensors,expected", [
    ({}, None),
    ({"filament_switch_sensor filament_sensor": {"enabled": True, "filament_detected": True}}, True),
    ({"filament_switch_sensor filament_sensor": {"enabled": True, "filament_detected": False}}, False),
    ({"filament_switch_sensor filament_sensor": {"enabled": False, "filament_detected": False}}, None),
])
def test_filament_detected_reads_only_enabled_sensors(sensors, expected):
    replies = [{"result": {"objects": ["extruder", "heater_bed"] + list(sensors)}}]
    if sensors:
        replies.append({"result": {"status": sensors}})
    api, _ = client(replies)
    assert api.filament_detected() is expected
