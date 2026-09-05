# -*- coding: utf-8 -*-
"""The running bot: four threads and the shared state between them.

  printer_loop   - polls Moonraker or holds SDCP, turns statuses into events
  keepalive_loop - stops the stock SDCP connection dropping when silent
  refresh_loop   - keeps the status message current while a print runs
  telemetry_loop - optional anonymous heartbeat, at most once per 30 days
  telegram_loop  - long-polls for updates (runs on the main thread)

Only one instance of this may run at a time: Telegram allows exactly one
long-polling consumer per token, and a second one silently steals updates from
the first.
"""
import logging
import threading
import time

from . import backend
from . import config as config_mod
from . import moonraker
from . import printer_state as ps
from . import sdcp
from . import storage
from . import support
from . import telemetry
from . import ui
from .telegram_api import TelegramAPI


log = logging.getLogger(__name__)


class Bot(object):

    def __init__(self, cfg, api=None, clock=time.time):
        self.cfg = cfg
        self.api = api or TelegramAPI(cfg["telegram_token"])
        self.clock = clock
        self.owner = str(cfg["chat_id"])
        self.owner_user = str(cfg.get("owner_user_id") or "")
        self.host = cfg["printer_ip"]
        self.backend_name = backend.name(cfg)
        self.confirmations = backend.ConfirmationStore(clock=clock)
        self.moonraker = None
        if self.backend_name == backend.MOONRAKER:
            base_url = cfg.get("moonraker_url") or ("http://%s" % self.host)
            self.moonraker = moonraker.Client(
                base_url,
                api_key=cfg.get("moonraker_api_key", ""),
                timeout=cfg.get("moonraker_timeout_sec", 5),
                camera_url=cfg.get("moonraker_camera_url", ""),
                allow_external_camera=cfg.get(
                    "moonraker_allow_external_camera", False),
            )

        self.lock = threading.RLock()
        self.main_lock = threading.Lock()

        self.status = None
        self.online = False
        self.ws = None
        self.pending = {}          # RequestID -> Ack payload
        self.files = []
        self.file_info = {}
        self.cooldown_pending = False
        self.fan_draft = None
        self.offline_since = None
        self.loss_reported = False
        self.frame = None
        self.frame_time = 0.0
        self.print_frame = None
        self.print_frame_time = 0.0
        # Макрос, запущенный из бота: (имя, когда, видели ли его в работе).
        # Нужен, чтобы доложить об окончании — HTTP-вызов возвращается сразу,
        # а сама калибровка идёт ещё десять минут.
        self.macro_watch = None
        self.prompt_shown = None   # подпись показанной подсказки принтера
        self.prompt_tick = 0

        self.lifecycle = ps.PrinterLifecycle(cfg.get("progress_every_pct") or 0)
        self.maintenance = ps.MaintenanceCounter()
        self.stopping = threading.Event()

    # ------------------------------------------------------------- helpers

    def _snapshot(self):
        with self.lock:
            return self.status, self.online

    def maintenance_view(self):
        """(show, line, due) for the lubrication reminder."""
        limit_h = float(self.cfg.get("maintenance_hours", 150) or 0)
        limit_d = float(self.cfg.get("maintenance_days", 60) or 0)
        data = storage.load_maintenance()
        hours = data["hours"] + self.maintenance.pending
        days = (self.clock() - data["since"]) / 86400.0
        return ps.maintenance_status(hours, days, limit_h, limit_d)

    def render(self, header="", detailed=False):
        status, online = self._snapshot()
        show, line, _ = self.maintenance_view()
        return ui.render(status, online, self.cfg.get("printer_name", "Centauri Carbon"),
                         header=header, detailed=detailed,
                         maintenance_line=line if show else "")

    def keyboard(self, detailed=False):
        status, _ = self._snapshot()
        show, _, due = self.maintenance_view()
        return ui.kb_main(
            status, allow_control=self.cfg.get("allow_control", True),
            allowed=self.allowed_actions(), detailed=detailed,
            maintenance=(show, due))

    def allowed_actions(self):
        return backend.allowed_actions(self.cfg)

    def action_allowed(self, action):
        return backend.is_allowed(self.cfg, action)

    def prepare_file_choices(self, files, kind="file-choice"):
        """Return short callback tokens bound to these exact file paths."""
        return [self.confirmations.issue(kind, path) for path in files]

    def resolve_file_choice(self, token, kind="file-choice"):
        """Resolve and consume a file-list choice.

        Numeric values are accepted only for keyboards made by releases before
        path-bound tokens existed.  New keyboards never emit an index.
        """
        if str(token).isdigit():
            index = int(token)
            with self.lock:
                files = list(self.files or [])
            return files[index] if 0 <= index < len(files) else None
        return self.confirmations.consume(kind, token)

    def issue_print_confirmation(self, path):
        return self.confirmations.issue("print", path)

    def consume_print_confirmation(self, token):
        if str(token).isdigit():
            # Compatibility with an already delivered legacy confirmation.
            index = int(token)
            with self.lock:
                files = list(self.files or [])
            return files[index] if 0 <= index < len(files) else None
        return self.confirmations.consume("print", token)

    def issue_delete_confirmation(self, path):
        return self.confirmations.issue("delete", path)

    def consume_delete_confirmation(self, token):
        return self.confirmations.consume("delete", token)

    def issue_action_confirmation(self, action):
        """Bind a one-use confirmation to one exact control action."""
        return self.confirmations.issue("job-action", action)

    def consume_action_confirmation(self, action, token):
        confirmed = self.confirmations.consume("job-action", token)
        return confirmed == action

    def issue_control_confirmation(self, action, value):
        return self.confirmations.issue("hardware-control", (action, value))

    def consume_control_confirmation(self, action, token):
        item = self.confirmations.consume("hardware-control", token)
        return item[1] if item and item[0] == action else None

    def macro_allowed(self, name):
        allowed = {moonraker.normalized_macro_name(item)
                   for item in (self.cfg.get("moonraker_macro_whitelist") or [])}
        return bool(self.action_allowed(backend.RUN_MACRO)
                    and moonraker.normalized_macro_name(name) in allowed)

    def prepare_macro_choices(self, names):
        return [self.confirmations.issue("macro-choice", name) for name in names]

    def resolve_macro_choice(self, token):
        return self.confirmations.consume("macro-choice", token)

    def issue_macro_confirmation(self, name):
        return self.confirmations.issue("macro", name)

    def consume_macro_confirmation(self, token):
        return self.confirmations.consume("macro", token)

    def prepare_prompt_choices(self, actions):
        """Bind the printer's own prompt commands to short callback tokens."""
        return [self.confirmations.issue("prompt-choice", g) for g in actions]

    def resolve_prompt_choice(self, token):
        return self.confirmations.consume("prompt-choice", token)

    def prepare_object_choices(self, values):
        """Bind buttons to an exact object and the current print filename."""
        return [self.confirmations.issue("exclude-choice", value)
                for value in values]

    def resolve_object_choice(self, token):
        return self.confirmations.consume("exclude-choice", token)

    def issue_object_confirmation(self, value):
        return self.confirmations.issue("exclude-object", value)

    def consume_object_confirmation(self, token):
        return self.confirmations.consume("exclude-object", token)

    # ------------------------------------------------------------- camera

    def grab(self, max_age=0):
        if not self.cfg.get("send_photo"):
            return None
        if max_age:
            with self.lock:
                cached, when = self.frame, self.frame_time
            if cached and self.clock() - when < max_age:
                return cached
        if self.backend_name == backend.MOONRAKER:
            try:
                shot = self.moonraker.grab_frame()
            except moonraker.MoonrakerError as e:
                log.debug("Moonraker camera unavailable: %s", e)
                shot = None
        else:
            shot = sdcp.grab_frame(self.host)
        if shot:
            with self.lock:
                self.frame, self.frame_time = shot, self.clock()
        return shot

    def capture_print_frame(self, progress):
        """Keep a frame taken while the print was still up.

        By the time the "ready" code arrives the bed is already dropping, and a
        finish photo shows the part sunk out of view. So near the end frames
        are taken more often, and the finish message uses the last one where
        the top surface is still visible.
        """
        every = 20 if (progress or 0) >= 95 else 90
        with self.lock:
            when = self.print_frame_time
        if self.clock() - when < every:
            return
        shot = self.grab()
        if shot:
            with self.lock:
                self.print_frame, self.print_frame_time = shot, self.clock()

    # -------------------------------------------------- the single message

    def refresh_main(self, force_new=False, text=None, photo=None, keyboard=None):
        """Keep exactly one status message in the chat.

        Edits in place; with force_new it is recreated as the last message in
        the chat so the buttons sit under the thumb. ``text`` replaces the
        usual status, which is how a notification becomes this same message
        instead of a second one beside it.

        The lock is essential: the event thread and the refresh thread once met
        on the same message, one deleted it, the other failed to edit and made
        another, and the chat was left with two. The old message is now deleted
        in every case, including when a new one is made after a failed edit.
        """
        with self.main_lock:
            mid = storage.message_id()
            if photo is None:
                photo = self.grab(max_age=10)
            body = self.render() if text is None else text
            kb = keyboard if keyboard is not None else self.keyboard()

            if mid and not force_new:
                if self.api.edit_message(self.owner, mid, body,
                                         keyboard=kb, photo=photo).get("ok"):
                    return mid
                # the edit failed: the message was deleted by hand, or is stale

            if mid:
                self.api.delete_message(self.owner, mid)
                storage.set_message_id(None)

            answer = self.api.send_message(self.owner, body, keyboard=kb, photo=photo)
            if not answer.get("ok"):
                return None
            mid = answer["result"]["message_id"]
            storage.set_message_id(mid)
            return mid

    def edit_main_from_callback(self, clicked_mid, text, keyboard=None,
                                photo=None, is_photo=False):
        """Edit the one tracked UI message after a button press.

        Telegram keeps old inline keyboards usable.  A user can therefore
        press a button on a stale status message while a newer message is the
        one stored in ``message_id``.  Prefer the tracked message and delete
        the stale one; if that edit is no longer possible, adopt the clicked
        message instead.  This preserves the one-message invariant for both
        the Files screen and its Back button.
        """
        with self.main_lock:
            tracked_mid = storage.message_id()

            if tracked_mid and tracked_mid != clicked_mid:
                answer = self.api.edit_message(
                    self.owner, tracked_mid, text, keyboard=keyboard,
                    photo=photo, is_photo=is_photo)
                if answer.get("ok"):
                    self.api.delete_message(self.owner, clicked_mid)
                    return tracked_mid

            answer = self.api.edit_message(
                self.owner, clicked_mid, text, keyboard=keyboard,
                photo=photo, is_photo=is_photo)
            if not answer.get("ok"):
                for stale_mid in {tracked_mid, clicked_mid}:
                    if stale_mid:
                        self.api.delete_message(self.owner, stale_mid)
                storage.set_message_id(None)
                answer = self.api.send_message(
                    self.owner, text, keyboard=keyboard, photo=photo)
                if not answer.get("ok"):
                    return None
                new_mid = answer["result"]["message_id"]
                storage.set_message_id(new_mid)
                return new_mid

            if tracked_mid and tracked_mid != clicked_mid:
                self.api.delete_message(self.owner, tracked_mid)
            storage.set_message_id(clicked_mid)
            return clicked_mid

    # ------------------------------------------------------------- commands

    def run_command(self, cmd, data=None, wait=4.0):
        """Send an SDCP command and wait for its Ack. Returns (ok, detail)."""
        with self.lock:
            ws = self.ws
        if not ws:
            return False, "нет связи с принтером"
        try:
            rid = ws.command(cmd, data)
        except Exception as e:
            return False, "не смог отправить: %r" % e
        deadline = self.clock() + wait
        while self.clock() < deadline:
            with self.lock:
                if rid in self.pending:
                    result = self.pending.pop(rid)
                    return (result.get("Ack") == 0), ("Ack=%s" % result.get("Ack"))
            time.sleep(0.1)
        return False, "принтер не ответил"

    def refresh_files(self):
        """Refresh ``self.files`` through the selected backend."""
        if not self.action_allowed(backend.FILES):
            return False, "список файлов недоступен"
        if self.backend_name == backend.MOONRAKER:
            try:
                records = self.moonraker.list_file_records()
            except moonraker.MoonrakerError as e:
                return False, str(e)
            with self.lock:
                self.files = [record["path"] for record in records]
                self.file_info = {record["path"]: record for record in records}
            return True, "Moonraker"
        return self.run_command(sdcp.CMD_FILE_LIST, {"Url": "/local"}, wait=6)

    def perform(self, action, value=None):
        """Execute one named operation after a final permission check."""
        if not self.action_allowed(action):
            return False, "команда запрещена настройками"
        if action == backend.RUN_MACRO and not self.macro_allowed(value):
            return False, "макрос не разрешён настройками"

        # Re-check live state at execution time.  A confirmation screen can be
        # left open while the printer disconnects or changes state; the old
        # button must then fail closed instead of sending a stale command.
        if action not in backend.READ_ACTIONS:
            if not self.online or not self.status:
                return False, "принтер не в сети"
            code = (self.status.get("PrintInfo") or {}).get("Status")
            if code == 77:
                return False, "принтер в состоянии ошибки"
            if action == backend.PAUSE and code != ps.STATUS_PRINTING:
                return False, "печать сейчас не выполняется"
            if action == backend.RESUME and code not in ps.STATUS_PAUSED:
                return False, "принтер сейчас не на паузе"
            if action == backend.CANCEL and code in (None, 0, 8, 9):
                return False, "активной печати нет"
            if (action == backend.EXCLUDE_OBJECT
                    and code != ps.STATUS_PRINTING and code not in ps.STATUS_PAUSED):
                return False, "активной печати нет"
            if action == backend.START and code not in (0, 8, 9):
                return False, "принтер занят"

        if self.backend_name == backend.MOONRAKER:
            if action == backend.EXCLUDE_OBJECT:
                payload = value if isinstance(value, dict) else {}
                object_name = moonraker.normalized_object_name(payload.get("name"))
                expected_file = str(payload.get("filename") or "")
                if not object_name or not expected_file:
                    return False, "подтверждение объекта некорректно"
                try:
                    live = self.moonraker.exclude_object_state()
                except moonraker.MoonrakerError as e:
                    return False, str(e)
                if live.get("PrintState") not in ("printing", "paused"):
                    return False, "активной печати уже нет"
                if live.get("Filename") != expected_file:
                    return False, "задание печати сменилось"
                names = list(live.get("Objects") or [])
                excluded = set(live.get("ExcludedObjects") or [])
                active = [name for name in names if name not in excluded]
                if object_name not in active:
                    return False, "объект уже исключён или больше не существует"
                if len(active) < 2:
                    return False, "нельзя убрать единственный оставшийся объект"
                value = object_name
            methods = {
                backend.PAUSE: lambda: self.moonraker.pause(),
                backend.RESUME: lambda: self.moonraker.resume(),
                backend.CANCEL: lambda: self.moonraker.cancel(),
                backend.EXCLUDE_OBJECT: lambda: self.moonraker.exclude_object(value),
                backend.START: lambda: self.moonraker.start(value),
                backend.DELETE: lambda: self.moonraker.delete(value),
                backend.RUN_MACRO: lambda: self.moonraker.run_macro(value),
                backend.PROMPT: lambda: self.moonraker.run_prompt_action(value),
                backend.LIGHT: lambda: self.moonraker.set_light(value),
                backend.SPEED: lambda: self.moonraker.set_speed(value),
                backend.TEMPERATURE: lambda: self.moonraker.set_temperatures(value[0], value[1]),
                backend.FANS: lambda: self.moonraker.set_fans(value),
            }
            method = methods.get(action)
            if method is None:
                return False, "эта команда не поддерживается Moonraker-режимом"
            try:
                method()
                if action == backend.RUN_MACRO:
                    with self.lock:
                        self.macro_watch = (value, self.clock(), False)
                if action == backend.EXCLUDE_OBJECT:
                    with self.lock:
                        state = ((self.status or {}).get("ExcludeObject") or {})
                        excluded = list(state.get("ExcludedObjects") or [])
                        if value not in excluded:
                            excluded.append(value)
                        state["ExcludedObjects"] = excluded
                return True, "Moonraker"
            except moonraker.MoonrakerError as e:
                return False, str(e)

        if action == backend.PAUSE:
            command = (sdcp.CMD_PAUSE, None)
        elif action == backend.RESUME:
            command = (sdcp.CMD_RESUME, None)
        elif action == backend.CANCEL:
            command = (sdcp.CMD_STOP, None)
        elif action == backend.START:
            command = (sdcp.CMD_START,
                       {"Filename": value, "StartLayer": 0})
        elif action == backend.LIGHT:
            command = (sdcp.CMD_SET,
                       {"LightStatus": {"SecondLight": int(bool(value))}})
        elif action == backend.SPEED:
            command = (sdcp.CMD_SET, {"PrintSpeedPct": int(value)})
        elif action == backend.TEMPERATURE:
            command = (sdcp.CMD_SET,
                       {"TempTargetNozzle": int(value[0]),
                        "TempTargetHotbed": int(value[1])})
        elif action == backend.FANS:
            command = (sdcp.CMD_SET, {"TargetFanSpeed": dict(value)})
        else:
            return False, "неизвестная команда"
        return self.run_command(command[0], command[1])

    def diagnostics(self):
        """Return a safe read-only health summary for COSMOS."""
        if not self.action_allowed(backend.DIAGNOSTICS):
            return False, "диагностика недоступна"
        if self.backend_name != backend.MOONRAKER:
            return False, "диагностика COSMOS доступна только через Moonraker"
        try:
            return True, self.moonraker.diagnostics()
        except moonraker.MoonrakerError as e:
            return False, str(e)

    def exclude_objects(self):
        """Read a fresh object list instead of trusting the polling cache."""
        if (not self.action_allowed(backend.EXCLUDE_OBJECT)
                or self.backend_name != backend.MOONRAKER):
            return False, "исключение объектов доступно только через Moonraker"
        try:
            return True, self.moonraker.exclude_object_state()
        except moonraker.MoonrakerError as e:
            return False, str(e)

    def history(self):
        if not self.action_allowed(backend.HISTORY) or self.backend_name != backend.MOONRAKER:
            return False, "история доступна только через Moonraker"
        try:
            return True, self.moonraker.history()
        except moonraker.MoonrakerError as e:
            return False, str(e)

    def bed_mesh(self):
        if not self.action_allowed(backend.HEIGHT_MAP) or self.backend_name != backend.MOONRAKER:
            return False, "карта доступна только через Moonraker"
        try:
            return True, self.moonraker.bed_mesh()
        except moonraker.MoonrakerError as e:
            return False, str(e)

    def macros(self):
        if not self.action_allowed(backend.MACROS) or self.backend_name != backend.MOONRAKER:
            return False, "макросы доступны только через Moonraker"
        try:
            return True, self.moonraker.list_macros()
        except moonraker.MoonrakerError as e:
            return False, str(e)

    def light_off_if_night(self):
        """Turn the light off after a print, but only at night.

        By day a lit chamber bothers nobody; at night it shines into the room
        until morning. Returns a line for the message, or empty.
        """
        if not self.cfg.get("light_off_at_night", True) or not self.is_night():
            return ""
        with self.lock:
            lit = (((self.status or {}).get("LightStatus") or {}).get("SecondLight") == 1)
        if not lit:
            return ""
        if not self.action_allowed(backend.LIGHT):
            return ""
        ok, info = self.perform(backend.LIGHT, False)
        return "\n🌙 Свет выключен — ночь." if ok else \
               "\n⚠️ Свет погасить не вышло (%s)." % info

    def is_night(self, hour=None):
        """Night by local time; the window may cross midnight."""
        a = int(self.cfg.get("night_from", 22)) % 24
        b = int(self.cfg.get("night_to", 8)) % 24
        h = hour if hour is not None else time.localtime().tm_hour
        return (h >= a or h < b) if a > b else (a <= h < b)

    # ---------------------------------------------------- support reminder

    def maybe_support_note(self):
        """The two-line monthly note, or "".

        Called only from the finished-print path. Never from an error, a pause,
        a connection loss or a confirmation - see support.py for why.
        """
        state = storage.load_state()
        if not support.due(state, now=self.clock()):
            return ""
        return support.REMINDER_TEXT

    def confirm_support_note_shown(self):
        """Stamp the reminder only after the message really went out."""
        state = storage.load_state()
        support.mark_shown(state, now=self.clock())
        storage.save_state(state)

    # ------------------------------------------------------------- events

    def announce(self, event):
        """Turn one lifecycle event into the single status message."""
        note_appended = False
        photo = None
        keyboard = None

        if event.kind == ps.STARTED:
            text = self.render("🖨 <b>Печать началась</b>\n")
        elif event.kind == ps.RESUMED:
            text = self.render("▶️ <b>Печать продолжилась</b>\n")
        elif event.kind == ps.PAUSED:
            text = self.render("⏸ <b>Печать на паузе</b>\nПродолжить — кнопкой ниже.\n")
        elif event.kind == ps.STALLED:
            # The status object carries Klipper's own reason for the stop;
            # ui.stall_header prefers it over the bare numeric code.
            status, _ = self._snapshot()
            text = self.render(ui.stall_header(status, event.code))
        elif event.kind == ps.PROGRESS:
            text = self.render("📊 <b>Идёт печать</b>\n")
        elif event.kind == ps.FINISHED:
            text = ui.done_text(event.snapshot) + self.light_off_if_night()
            # The one place an unprompted support note is allowed: a print that
            # just finished successfully.
            note = self.maybe_support_note()
            if note:
                text += note
                keyboard = support.reminder_keyboard()
                note_appended = True
            photo = self._take_print_frame()
        elif event.kind == ps.CANCELLED:
            text = ui.cancelled_text(event.snapshot, event.reached) + self.light_off_if_night()
            photo = self._take_print_frame()
        else:
            return

        try:
            mid = self.refresh_main(force_new=True, text=text, photo=photo,
                                    keyboard=keyboard)
        except Exception as e:
            log.warning("notification did not go out: %r", e)
            return
        # Stamp only on confirmed delivery, so an unreachable Telegram does not
        # silently swallow a whole month.
        if note_appended and mid is not None:
            self.confirm_support_note_shown()
        if event.kind in (ps.FINISHED, ps.CANCELLED) and self.cfg.get("notify_cooldown", True):
            with self.lock:
                nozzle = float((self.status or {}).get("TempOfNozzle") or 0)
            self.cooldown_pending = nozzle > float(self.cfg.get("cooldown_temp_c", 50))

    def _watch_macro(self):
        """Report when a macro started from the bot has finished.

        /printer/gcode/script answers only when the script ends, so the button
        press cannot tell us anything about the ten minutes that follow. Klipper
        keeps idle_timeout at "Printing" while gcode runs, so we watch that.
        """
        with self.lock:
            watch = self.macro_watch
        if not watch:
            return
        name, started, seen_busy = watch
        try:
            busy = self.moonraker.gcode_busy()
        except Exception:
            return
        if busy:
            with self.lock:
                self.macro_watch = (name, started, True)
            return
        # Klipper flips to "Printing" a moment after the command lands; without
        # this grace the watcher would call every macro finished instantly.
        if not seen_busy and self.clock() - started < 20:
            return
        with self.lock:
            self.macro_watch = None
        self._report_macro_done(name, int(self.clock() - started))

    def _report_macro_done(self, name, seconds):
        needs_save = name in ui.BED_CALIB_MACROS
        keyboard = None
        if needs_save and self.macro_allowed("SAVE_CALIBRATION"):
            keyboard = ui.kb_after_calibration(
                self.prepare_macro_choices(["SAVE_CALIBRATION"])[0])
        try:
            self.refresh_main(force_new=True,
                              text=ui.macro_done_text(name, seconds, needs_save),
                              keyboard=keyboard)
        except Exception as e:
            log.warning("macro report did not go out: %r", e)

    def _watch_prompt(self):
        """Mirror the dialog from the printer screen into Telegram."""
        if not self.action_allowed(backend.PROMPT):
            return
        with self.lock:
            self.prompt_tick += 1
            tick = self.prompt_tick
        if tick % 3:            # раз в три опроса: подсказка не срочная
            return
        try:
            prompt = self.moonraker.active_prompt()
        except Exception:
            return
        signature = None if not prompt else (
            prompt["title"], tuple(prompt["text"]),
            tuple(gcode for _label, gcode, _style in prompt["buttons"]))
        with self.lock:
            same = signature == self.prompt_shown
            self.prompt_shown = signature
        if signature is None or same:
            return
        refs = self.prepare_prompt_choices(
            [gcode for _label, gcode, _style in prompt["buttons"]])
        try:
            self.refresh_main(force_new=True, text=ui.prompt_text(prompt),
                              keyboard=ui.kb_prompt(prompt["buttons"], refs))
        except Exception as e:
            log.warning("prompt did not go out: %r", e)

    def _maybe_notify_cooldown(self, status):
        if not self.cooldown_pending:
            return
        info = status.get("PrintInfo") or {}
        threshold = float(self.cfg.get("cooldown_temp_c", 50))
        nozzle = float(status.get("TempOfNozzle") or 0)
        target = float(status.get("TempTargetNozzle") or 0)
        if info.get("Status") == ps.STATUS_PRINTING or nozzle > threshold or target > 0:
            return
        self.cooldown_pending = False
        try:
            self.refresh_main(force_new=True, text=self.render(
                "❄️ <b>Принтер остыл</b>\nСопло %.0f°C — можно безопасно заняться деталью.\n" % nozzle))
        except Exception as e:
            log.warning("cooldown notification did not go out: %r", e)

    def _take_print_frame(self):
        with self.lock:
            shot, self.print_frame, self.print_frame_time = self.print_frame, None, 0.0
        return shot

    def show_connection_lost(self, elapsed):
        """Replace the main panel with an offline view, without a stale photo."""
        text = self.render(
            "⚠️ <b>Связь с принтером потеряна</b>\n"
            "Нет ответа уже %d с, продолжаю переподключаться.\n" % elapsed)
        try:
            return self.refresh_main(force_new=True, text=text, photo=False)
        except Exception as e:
            log.warning("connection-loss panel did not go out: %r", e)
            return None

    def show_connection_restored(self):
        """Put the live main panel back at the bottom after reconnection."""
        text = self.render("🔌 <b>Связь с принтером восстановлена</b>\n")
        try:
            return self.refresh_main(force_new=True, text=text)
        except Exception as e:
            log.warning("connection-restored panel did not go out: %r", e)
            return None

    # -------------------------------------------------------------- loops

    def printer_loop(self):
        if self.backend_name == backend.MOONRAKER:
            return self._moonraker_printer_loop()
        return self._sdcp_printer_loop()

    def _maybe_report_connection_loss(self):
        with self.lock:
            since, reported = self.offline_since, self.loss_reported
        grace = int(self.cfg.get("offline_grace_sec", 60))
        if since and not reported and self.clock() - since > grace:
            with self.lock:
                self.loss_reported = True
            self.show_connection_lost(int(self.clock() - since))

    def _moonraker_printer_loop(self):
        interval = max(1, float(self.cfg.get("moonraker_poll_sec", 2)))
        while not self.stopping.is_set():
            self._maybe_report_connection_loss()
            try:
                status = self.moonraker.status()
                with self.lock:
                    was_online = self.online
                    self.online = True
                    self.offline_since = None
                    had_reported, self.loss_reported = self.loss_reported, False
                if not was_online:
                    log.info("printer: Moonraker connected")
                if had_reported:
                    self.show_connection_restored()
                self._handle_status(status)
                self._watch_macro()
                self._watch_prompt()
            except Exception as e:
                log.info("printer: Moonraker unavailable - %s", e)
                with self.lock:
                    self.online = False
                    if self.offline_since is None:
                        self.offline_since = self.clock()
            self.stopping.wait(interval)

    def _sdcp_printer_loop(self):
        while not self.stopping.is_set():
            self._maybe_report_connection_loss()
            ws = None
            try:
                ws = sdcp.WS(self.host)
                with self.lock:
                    self.ws, self.online = ws, True
                    self.offline_since = None
                    had_reported, self.loss_reported = self.loss_reported, False
                log.info("printer: connected")
                if had_reported:
                    self.show_connection_restored()
                for raw in ws.messages():
                    self._handle_printer_message(raw, ws)
            except Exception as e:
                log.info("printer: dropped - %r", e)
                with self.lock:
                    self.online, self.ws = False, None
                    if self.offline_since is None:
                        self.offline_since = self.clock()
            finally:
                if ws:
                    ws.close()
            self.stopping.wait(10)

    def _handle_printer_message(self, raw, ws):
        import json
        try:
            payload = json.loads(raw)
        except ValueError:
            return
        topic = payload.get("Topic", "")
        if "/response/" in topic:
            data = payload.get("Data", {})
            inner = data.get("Data", {})
            with self.lock:
                self.pending[data.get("RequestID")] = inner
                if (data.get("Cmd") == sdcp.CMD_FILE_LIST
                        and isinstance(inner.get("FileList"), list)):
                    self.files = [f.get("name") for f in inner["FileList"]
                                  if f.get("type") == 1]
            return

        status = payload.get("Status")
        if not isinstance(status, dict):
            return
        ws.mainboard = payload.get("MainboardID", "") or ws.mainboard
        self._handle_status(status)

    def _handle_status(self, status):
        print_info = status.get("PrintInfo") or {}
        code = print_info.get("Status")
        if code is None:
            return
        storage.remember_code(code)
        with self.lock:
            self.status = status

        if code == ps.STATUS_PRINTING and print_info.get("Filename"):
            try:
                self.capture_print_frame(print_info.get("Progress", 0) or 0)
            except Exception as e:
                log.debug("print frame not taken: %r", e)
            try:
                hours = self.maintenance.observe(print_info)
                if hours:
                    data = storage.load_maintenance()
                    data["hours"] = round(data["hours"] + hours, 3)
                    storage.save_maintenance(data)
            except Exception as e:
                log.debug("running time not counted: %r", e)
        else:
            self.maintenance.forget()

        for event in self.lifecycle.observe(status):
            self.announce(event)
        self._maybe_notify_cooldown(status)

    def keepalive_loop(self):
        """The printer closes the websocket if the client stays silent.
        Asking for a status is harmless and moves traffic both ways."""
        if self.backend_name != backend.SDCP:
            return
        while not self.stopping.is_set():
            self.stopping.wait(max(5, int(self.cfg.get("keepalive_sec", 20))))
            with self.lock:
                ws = self.ws if self.online else None
            if not ws:
                continue
            try:
                ws.command(sdcp.CMD_STATUS)
            except Exception as e:
                log.debug("keepalive did not go out: %r", e)

    def refresh_loop(self):
        """Refresh the message while a print runs. Idle needs no touching."""
        while not self.stopping.is_set():
            self.stopping.wait(max(30, int(self.cfg.get("status_refresh_sec", 120))))
            status, online = self._snapshot()
            if not online or not status:
                continue
            if (status.get("PrintInfo") or {}).get("Status") != ps.STATUS_PRINTING:
                continue
            try:
                self.refresh_main()
            except Exception as e:
                log.warning("status not refreshed: %r", e)

    def telegram_loop(self):
        from .handlers import handle_callback, handle_message
        offset = None
        self.api.set_my_commands([
            {"command": "status", "description": "состояние принтера"},
            {"command": "snap", "description": "кадр с камеры"},
            {"command": "files", "description": "файлы на принтере"},
            {"command": "diag", "description": "диагностика COSMOS"},
            {"command": "mesh", "description": "карта высот стола"},
            {"command": "history", "description": "история печатей"},
            {"command": "macros", "description": "макросы COSMOS"},
            {"command": "help", "description": "справка"},
        ])
        while not self.stopping.is_set():
            try:
                answer = self.api.get_updates(offset=offset, timeout=25)
                for update in answer.get("result", []):
                    offset = update["update_id"] + 1
                    try:
                        if "callback_query" in update:
                            handle_callback(self, update["callback_query"])
                        elif "message" in update:
                            handle_message(self, update["message"])
                    except Exception as e:
                        log.warning("update handling failed: %r", e)
            except Exception as e:
                log.warning("telegram loop: %r", e)
                self.stopping.wait(5)

    def run(self):
        log.info("bot started, printer %s", self.host)
        targets = [self.printer_loop, self.keepalive_loop, self.refresh_loop]
        if self.cfg.get("anonymous_statistics", False):
            targets.append(lambda: telemetry.loop(self.stopping, self.cfg))
        for target in targets:
            threading.Thread(target=target, daemon=True).start()
        try:
            self.telegram_loop()
        except KeyboardInterrupt:
            log.info("exit")
        finally:
            self.stopping.set()
