# -*- coding: utf-8 -*-
"""Dependency-free Moonraker HTTP client and status normalizer.

Moonraker's documented polling interval is one to two seconds.  Polling keeps
the distributable ZIP dependency-free and gives this first COSMOS backend a
small, auditable surface.  Commands are exposed here, but the policy in
``backend.py`` decides whether the bot may call them.
"""
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request


log = logging.getLogger(__name__)

QUERY_OBJECTS = (
    "webhooks", "virtual_sdcard", "print_stats", "extruder", "heater_bed",
    "fan", "fan_generic aux_fan", "fan_generic case_fan", "led case",
    "display_status", "gcode_move", "exclude_object",
)


# Что разрешено запускать по кнопке из подсказки принтера: имя макроса
# (COSMOS зовёт свои служебные с подчёркивания) и до шести простых KEY=VALUE.
# Команда приходит от прошивки, а не от пользователя, поэтому форма проверяется
# жёстко: произвольный G-code так не пролезет.
PROMPT_ACTION_RE = re.compile(
    r"^_?[A-Z][A-Z0-9_]{0,63}(?: [A-Z0-9_]{1,32}=[-A-Za-z0-9_.]{1,32}){0,6}$")


class MoonrakerError(Exception):
    """A safe, human-readable Moonraker failure."""


class MoonrakerOffline(MoonrakerError):
    """The request never got an answer: timeout or network.

    Separate from errors Klipper itself reported, because a long macro
    looks exactly like a dead link - see run_macro.
    """


def valid_base_url(value):
    try:
        parsed = urllib.parse.urlsplit(str(value or "").strip())
    except ValueError:
        return False
    return (parsed.scheme in ("http", "https") and bool(parsed.hostname)
            and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment)


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def normalized_gcode_path(value):
    """Return a safe relative printable path, or an empty string."""
    path = str(value or "").strip().replace("\\", "/")
    if not path or len(path) > 512 or "\x00" in path or path.startswith("/"):
        return ""
    parts = path.split("/")
    if any(not part or part in (".", "..") for part in parts):
        return ""
    return path if path.lower().endswith((".gcode", ".gco", ".gc")) else ""


def valid_gcode_path(value):
    return bool(normalized_gcode_path(value))


MACRO_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
OBJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}$")


def normalized_macro_name(value):
    name = str(value or "").strip().upper()
    return name if MACRO_RE.match(name) and not name.startswith("_") else ""


def normalized_object_name(value):
    """Return a Klipper object name safe to place in a fixed G-code command."""
    name = str(value or "").strip()
    return name if OBJECT_RE.match(name) else ""


def normalize_exclude_state(objects):
    """Keep only exact, command-safe names from Klipper's exclude_object data."""
    raw = (objects or {}).get("exclude_object") or {}
    names = []
    for item in raw.get("objects") or []:
        value = item.get("name") if isinstance(item, dict) else item
        name = normalized_object_name(value)
        if name and name not in names:
            names.append(name)
    excluded = []
    for value in raw.get("excluded_objects") or []:
        name = normalized_object_name(value)
        if name and name in names and name not in excluded:
            excluded.append(name)
    current = normalized_object_name(raw.get("current_object"))
    return {
        "Objects": names,
        "ExcludedObjects": excluded,
        "CurrentObject": current if current in names else "",
    }


def normalize_status(objects):
    """Translate Klipper objects to the stable status shape used by the UI."""
    objects = objects or {}
    hooks = objects.get("webhooks") or {}
    stats = objects.get("print_stats") or {}
    card = objects.get("virtual_sdcard") or {}
    display = objects.get("display_status") or {}
    extruder = objects.get("extruder") or {}
    bed = objects.get("heater_bed") or {}
    move = objects.get("gcode_move") or {}
    # COSMOS exposes the two enclosure fans as Klipper ``fan_generic``
    # objects.  The short names were accepted in no version of the live
    # object API, so using them made the UI show both fans as off even after
    # their M106 P2/P3 commands had succeeded.
    aux_fan = (objects.get("fan_generic aux_fan")
               or objects.get("aux_fan") or {})
    case_fan = (objects.get("fan_generic case_fan")
                or objects.get("case_fan") or {})

    klippy_state = str(hooks.get("state") or "").lower()
    print_state = str(stats.get("state") or "standby").lower()
    codes = {
        "standby": 0,
        "printing": 13,
        "paused": 6,
        "complete": 9,
        "cancelled": 8,
        # An unknown settled code after printing becomes a visible stalled
        # event in PrinterLifecycle instead of being misreported as success.
        "error": 77,
    }
    code = codes.get(print_state, 77 if print_state else 0)
    if klippy_state and klippy_state != "ready":
        code = 77

    progress_fraction = max(_number(card.get("progress")),
                            _number(display.get("progress")))
    progress = max(0, min(100, int(round(progress_fraction * 100))))
    elapsed = max(0, int(_number(stats.get("print_duration"))))
    total = int(elapsed / progress_fraction) if progress_fraction > 0 else 0
    info = stats.get("info") or {}
    filename = str(stats.get("filename") or card.get("file_path") or "")
    position = move.get("gcode_position") or [0, 0, 0, 0]

    print_info = {
        "Status": code,
        "Progress": progress,
        "CurrentLayer": int(_number(info.get("current_layer"))),
        "TotalLayer": int(_number(info.get("total_layer"))),
        "CurrentTicks": elapsed,
        "TotalTicks": max(elapsed, total),
        "Filename": filename,
        "TaskId": filename,
        "PrintSpeedPct": int(round(_number(move.get("speed_factor"), 1.0) * 100)),
    }
    return {
        "PrintInfo": print_info,
        "TempOfNozzle": _number(extruder.get("temperature")),
        "TempTargetNozzle": _number(extruder.get("target")),
        "TempOfHotbed": _number(bed.get("temperature")),
        "TempTargetHotbed": _number(bed.get("target")),
        "CurrentFanSpeed": {
            "ModelFan": int(max(0, min(1, _number(
                (objects.get("fan") or {}).get("speed")))) * 100),
            "BoxFan": int(max(0, min(1, _number(
                case_fan.get("speed")))) * 100),
            "AuxiliaryFan": int(max(0, min(1, _number(
                aux_fan.get("speed")))) * 100),
        },
        "CurrentCoord": {
            "X": _number(position[0] if len(position) > 0 else 0),
            "Y": _number(position[1] if len(position) > 1 else 0),
            "Z": _number(position[2] if len(position) > 2 else 0),
        },
        "Moonraker": {
            "KlippyState": klippy_state,
            "PrintState": print_state,
            "Message": str(stats.get("message") or hooks.get("state_message") or ""),
        },
        "ExcludeObject": normalize_exclude_state(objects),
        "LightStatus": {"SecondLight": int(bool(
            ((objects.get("led case") or {}).get("color_data") or [[0, 0, 0, 0]])[0][3]))},
    }


class Client(object):
    """The small Moonraker API surface required by this bot."""

    def __init__(self, base_url, api_key="", timeout=5, opener=None,
                 camera_url="", allow_external_camera=False):
        base_url = str(base_url or "").strip().rstrip("/")
        if not valid_base_url(base_url):
            raise ValueError("invalid Moonraker URL")
        self.base_url = base_url
        self.api_key = str(api_key or "")
        self.timeout = max(1, float(timeout))
        self.opener = opener or urllib.request.urlopen
        self.camera_url = str(camera_url or "").strip()
        self.allow_external_camera = bool(allow_external_camera)

    def _url(self, path):
        return urllib.parse.urljoin(self.base_url + "/", path.lstrip("/"))

    def _headers(self):
        headers = {"Accept": "application/json", "User-Agent": "centauri-bot"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        return headers

    def _open(self, request, max_bytes):
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read(max_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise MoonrakerError("HTTP %s" % exc.code)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise MoonrakerOffline("нет связи: %s" % reason)
        if len(raw) > max_bytes:
            raise MoonrakerError("ответ слишком большой")
        return raw

    def _json(self, path, method="GET", form=None):
        data = None
        headers = self._headers()
        if form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(self._url(path), data=data,
                                         headers=headers, method=method)
        raw = self._open(request, 8_000_000)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise MoonrakerError("Moonraker вернул не JSON")
        if not isinstance(payload, dict):
            raise MoonrakerError("неожиданный ответ Moonraker")
        if payload.get("error"):
            message = (payload["error"] or {}).get("message", "ошибка API")
            raise MoonrakerError(str(message)[:240])
        return payload.get("result", payload)

    def printer_info(self):
        """Klipper's own identification: version, hostname, state.

        Answers even when Klipper sits in an error state, which makes it the
        honest probe for "is there a Moonraker on this address at all" - unlike
        status(), which raises as soon as the object list comes back empty.
        """
        return self._json("/printer/info") or {}

    def status(self):
        query = "&".join(urllib.parse.quote(item) for item in QUERY_OBJECTS)
        result = self._json("/printer/objects/query?" + query)
        objects = (result or {}).get("status") or {}
        if not objects:
            raise MoonrakerError("пустой статус Klipper")
        return normalize_status(objects)

    def list_files(self):
        return [item["path"] for item in self.list_file_records()]

    def list_file_records(self):
        """Return the documented file-list metadata without trusting it as a path."""
        result = self._json("/server/files/list?root=gcodes")
        files = result if isinstance(result, list) else (result or {}).get("files", [])
        records = []
        for item in files or []:
            path = item.get("path") if isinstance(item, dict) else None
            path = normalized_gcode_path(path)
            if path:
                records.append({
                    "path": path,
                    "size": max(0, int(_number(item.get("size")))),
                    "modified": max(0, int(_number(item.get("modified")))),
                    "permissions": str(item.get("permissions") or ""),
                })
        # A fresh upload is normally what a person wants to print first.
        return sorted(records, key=lambda item: (-item["modified"], item["path"].lower()))

    def pause(self):
        self._json("/printer/print/pause", method="POST", form={})

    def resume(self):
        self._json("/printer/print/resume", method="POST", form={})

    def cancel(self):
        self._json("/printer/print/cancel", method="POST", form={})

    def exclude_object_state(self):
        """Return fresh object and job identity data for a safe exclusion."""
        result = self._json("/printer/objects/query?exclude_object&print_stats")
        objects = (result or {}).get("status") or {}
        if not objects:
            raise MoonrakerError("пустой статус объектов Klipper")
        state = normalize_exclude_state(objects)
        stats = objects.get("print_stats") or {}
        state.update({
            "PrintState": str(stats.get("state") or "").lower(),
            "Filename": str(stats.get("filename") or ""),
        })
        return state

    def exclude_object(self, name):
        """Exclude one exact slicer-declared object; arbitrary G-code is refused."""
        name = normalized_object_name(name)
        if not name:
            raise MoonrakerError("некорректное имя объекта")
        self._script(["EXCLUDE_OBJECT NAME=%s" % name])

    def start(self, filename):
        filename = normalized_gcode_path(filename)
        if not filename:
            raise MoonrakerError("некорректный путь к G-code")
        self._json("/printer/print/start", method="POST",
                   form={"filename": filename})

    def delete(self, filename):
        filename = normalized_gcode_path(filename)
        if not filename:
            raise MoonrakerError("некорректный путь к G-code")
        encoded = urllib.parse.quote(filename, safe="/")
        self._json("/server/files/gcodes/" + encoded, method="DELETE")

    def diagnostics(self):
        """Read only, compact COSMOS health data suitable for Telegram."""
        server = self._json("/server/info") or {}
        printer = self._json("/printer/info") or {}
        listed = self._json("/printer/objects/list") or {}
        objects = listed.get("objects", []) if isinstance(listed, dict) else []
        # Board memory. Moonraker reports no swap figure, and COSMOS's zram has
        # silently failed to start before - so free memory is the signal that is
        # actually reachable from here. It sits near 15 MB when zram is missing
        # and near 30 MB when it is running. Never let this break /diag: the
        # rest of the card is more important than the memory line.
        memory = {}
        try:
            memory = (self._json("/machine/proc_stats") or {}).get("system_memory") or {}
        except MoonrakerError:
            memory = {}
        return {
            "moonraker_version": str(server.get("moonraker_version") or "—"),
            "klippy_state": str(server.get("klippy_state") or printer.get("state") or "—"),
            "klippy_message": str(server.get("klippy_state_message") or printer.get("state_message") or ""),
            "klipper_version": str(printer.get("software_version") or "—"),
            "warnings": len(server.get("warnings") or []),
            "failed_components": len(server.get("failed_components") or []),
            "object_count": len(objects),
            "memory_total": int(_number(memory.get("total"))),
            "memory_available": int(_number(memory.get("available"))),
        }

    def list_macros(self):
        """Read the public macro names without exposing macro source code."""
        result = self._json("/printer/objects/list") or {}
        objects = result.get("objects", []) if isinstance(result, dict) else []
        names = []
        for item in objects:
            text = str(item or "")
            if text.startswith("gcode_macro "):
                name = normalized_macro_name(text.split(" ", 1)[1])
                if name:
                    names.append(name)
        return sorted(set(names))

    def gcode_busy(self):
        """True while Klipper is executing gcode - a macro, a print, anything."""
        try:
            result = self._json("/printer/objects/query?idle_timeout") or {}
        except MoonrakerError:
            return False
        block = (result.get("status") or {}).get("idle_timeout") or {}
        return str(block.get("state")) == "Printing"

    def run_macro(self, name):
        """A timeout here is not a failure: long macros hold the request open.

        /printer/gcode/script answers only when the script ends, and
        BED_MESH_CALIBRATE runs about ten minutes - far past our 5 s.
        Klipper flips idle_timeout to "Printing" while any gcode runs,
        so when the link goes quiet we ask the printer who is right.
        Errors Klipper reported itself still come through as failures.
        """
        name = normalized_macro_name(name)
        if not name:
            raise MoonrakerError("некорректное имя макроса")
        try:
            self._json("/printer/gcode/script", method="POST",
                       form={"script": name})
        except MoonrakerOffline:
            if not self.gcode_busy():
                raise

    def _script(self, lines):
        script = "\n".join(str(line) for line in lines if line)
        if not script:
            raise MoonrakerError("пустая команда")
        self._json("/printer/gcode/script", method="POST", form={"script": script})

    def set_light(self, enabled):
        value = 1 if bool(enabled) else 0
        self._script(["SET_LED LED=case WHITE=%d" % value, "SYNC_CAMERA_LED"])

    def set_speed(self, percent):
        percent = int(percent)
        if percent not in (50, 75, 100, 125, 150):
            raise MoonrakerError("недопустимая скорость")
        self._script(["M220 S%d" % percent])

    def set_temperatures(self, nozzle, bed):
        nozzle, bed = int(nozzle), int(bed)
        if (nozzle, bed) not in {(0, 0), (220, 60), (245, 80)}:
            raise MoonrakerError("недопустимый температурный профиль")
        self._script(["SET_HEATER_TEMPERATURE HEATER=extruder TARGET=%d" % nozzle,
                      "SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=%d" % bed])

    def set_fans(self, values):
        values = dict(values or {})
        mapped = (("ModelFan", 1), ("AuxiliaryFan", 2), ("BoxFan", 3))
        lines = []
        for key, index in mapped:
            percent = int(values.get(key, 0))
            if percent not in (0, 25, 50, 75, 100):
                raise MoonrakerError("недопустимая скорость вентилятора")
            lines.append("M106 P%d S%d" % (index, round(percent * 255 / 100)))
        self._script(lines)

    def history(self, limit=8):
        limit = max(1, min(20, int(limit)))
        result = self._json("/server/history/list?limit=%d" % limit) or {}
        jobs = result.get("jobs", []) if isinstance(result, dict) else []
        return [job for job in jobs if isinstance(job, dict)][:limit]

    def gcode_store(self, count=40):
        result = self._json("/server/gcode_store?count=%d" % int(count)) or {}
        store = result.get("gcode_store")
        return store if isinstance(store, list) else []

    def active_prompt(self, count=40):
        """The dialog COSMOS is showing on the printer screen, if any.

        Klipper macros drive that screen with "action:prompt_*" lines in the
        gcode responses - the same feed Mainsail listens to. Reading them lets
        the bot put the very same buttons into Telegram, instead of leaving the
        user to walk to the printer and press LOAD there.
        """
        prompt = shown = None
        for entry in self.gcode_store(count):
            text = str((entry or {}).get("message") or "").strip()
            if text.startswith("//"):
                text = text[2:].strip()
            if not text.startswith("action:prompt"):
                continue
            command, _, tail = text[len("action:"):].partition(" ")
            tail = tail.strip()
            if command == "prompt_begin":
                prompt = {"title": tail, "text": [], "buttons": []}
            elif prompt is None:
                continue
            elif command == "prompt_text":
                prompt["text"].append(tail)
            elif command in ("prompt_button", "prompt_footer_button"):
                label, _, rest = tail.partition("|")
                gcode = rest.split("|")[0].strip()
                if label.strip() and PROMPT_ACTION_RE.match(gcode):
                    prompt["buttons"].append((label.strip(), gcode))
            elif command == "prompt_show":
                shown = prompt
            elif command in ("prompt_end", "prompt_close"):
                prompt = shown = None
        return shown

    def run_prompt_action(self, gcode):
        """Run one command that the printer's own prompt offered."""
        gcode = str(gcode or "").strip()
        if not PROMPT_ACTION_RE.match(gcode):
            raise MoonrakerError("недопустимая команда подсказки")
        try:
            self._json("/printer/gcode/script", method="POST", form={"script": gcode})
        except MoonrakerOffline:
            if not self.gcode_busy():
                raise

    def bed_mesh(self):
        """The saved mesh, whether or not a profile is loaded into memory.

        profile_name is empty until something loads a profile - Mainsail does it
        when its heightmap tab opens. Keying off it made the map look missing
        unless the web UI happened to be open, so fall back to the saved
        profiles: "default" first, since PRINT_START uses exactly that one.
        """
        result = self._json("/printer/objects/query?bed_mesh") or {}
        mesh = ((result.get("status") or {}).get("bed_mesh") or {})
        profiles = mesh.get("profiles") or {}
        loaded = str(mesh.get("profile_name") or "")
        порядок = [loaded, "default"] + sorted(profiles)
        profile, points = "", None
        for имя in порядок:
            узел = profiles.get(имя) if имя else None
            if isinstance(узел, dict) and isinstance(узел.get("points"), list):
                profile, points = имя, узел["points"]
                break
        if not profile or not isinstance(points, list):
            raise MoonrakerError("сохранённая сетка стола не найдена")
        try:
            rows = [[float(value) for value in row] for row in points]
        except (TypeError, ValueError):
            raise MoonrakerError("сетка стола содержит некорректные данные")
        if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
            raise MoonrakerError("сетка стола неполная")
        return {"profile": profile, "points": rows, "loaded": profile == loaded,
                "mesh_min": mesh.get("mesh_min"), "mesh_max": mesh.get("mesh_max")}

    def _camera_snapshot_url(self):
        if self.camera_url:
            return self._url(self.camera_url)
        result = self._json("/server/webcams/list")
        webcams = (result or {}).get("webcams", [])
        for camera in webcams:
            if camera.get("enabled", True) and camera.get("snapshot_url"):
                return self._url(str(camera["snapshot_url"]))
        return ""

    def camera_available(self):
        """Whether a snapshot endpoint is configured, without fetching a frame."""
        return bool(self._camera_snapshot_url())

    def grab_frame(self, max_bytes=8_000_000):
        url = self._camera_snapshot_url()
        if not url:
            return None
        base_host = urllib.parse.urlsplit(self.base_url).hostname
        camera_host = urllib.parse.urlsplit(url).hostname
        if not self.allow_external_camera and camera_host != base_host:
            raise MoonrakerError("внешний адрес камеры запрещён настройками")
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        raw = self._open(request, max_bytes)
        a, b = raw.find(b"\xff\xd8"), raw.rfind(b"\xff\xd9")
        if a < 0 or b <= a:
            raise MoonrakerError("камера вернула не JPEG")
        return raw[a:b + 2]
