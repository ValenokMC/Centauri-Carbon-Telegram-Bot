# -*- coding: utf-8 -*-
"""The running bot: four threads and the shared state between them.

  printer_loop   - polls Moonraker or holds SDCP, turns statuses into events
  keepalive_loop - stops the stock SDCP connection dropping when silent
  refresh_loop   - keeps the status message current while a print runs
  schedule_loop  - reminds about and starts planned prints (COSMOS only)
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
from . import schedule
from . import sdcp
from . import storage
from . import support
from . import ui
from . import widget
from .telegram_api import TelegramAPI


log = logging.getLogger(__name__)


class ScreenCapture(object):
    """What one handler would have shown, taken instead of shown."""

    def __init__(self, mid):
        self.mid = mid          # the card
        self.text = None
        self.keyboard = None
        self.toast = None
        self.close = False      # the handler went back to the status

    def screen(self, text, keyboard):
        self.text, self.keyboard = text, keyboard


class _CapturingAPI(object):
    """The Telegram client, plus one detour for buttons on the live card.

    While a card button runs an ordinary button's handler on this thread,
    whatever that handler would put on the card message (an edit, a new
    screen message, a toast) is recorded instead, and the card draws it.
    Every other call, and every call from another thread, goes straight
    through.
    """

    def __init__(self, bot, api):
        self._bot = bot
        self._api = api

    def __getattr__(self, name):
        return getattr(self._api, name)

    def __setattr__(self, name, value):
        # A replaced detour method stays on the wrapper (a spy that calls the
        # original must reach the detour, not itself); settings and other
        # replaced methods belong to the client.
        if name.startswith("_") or hasattr(type(self), name):
            object.__setattr__(self, name, value)
        else:
            setattr(self._api, name, value)

    def _cap(self):
        return getattr(self._bot._tls, "capture", None)

    def edit_message(self, chat, message_id, text, keyboard=None,
                     photo=None, is_photo=False):
        cap = self._cap()
        if cap is not None and message_id == cap.mid:
            # A photo here is the camera frame of a status redraw; a screen
            # that is a picture goes through edit_main_from_callback.
            cap.screen(text, keyboard)
            return {"ok": True, "result": {"message_id": message_id}}
        return self._api.edit_message(chat, message_id, text, keyboard=keyboard,
                                      photo=photo, is_photo=is_photo)

    def send_message(self, chat, text, keyboard=None, photo=None):
        cap = self._cap()
        if cap is not None and str(chat) == self._bot.owner:
            if photo:
                self._bot.post_picture(photo)
            cap.screen(text, keyboard)
            return {"ok": True, "result": {"message_id": cap.mid}}
        return self._api.send_message(chat, text, keyboard=keyboard, photo=photo)

    def delete_message(self, chat, message_id):
        cap = self._cap()
        if cap is not None and message_id == cap.mid:
            return {"ok": True}         # the card stays; its screen changes
        return self._api.delete_message(chat, message_id)

    def answer_callback(self, callback_id, text=None):
        cap = self._cap()
        if cap is not None and not callback_id:
            cap.toast = text or cap.toast
            return {"ok": True}
        return self._api.answer_callback(callback_id, text)


class Bot(object):

    def __init__(self, cfg, api=None, clock=time.time):
        self.cfg = cfg
        self._tls = threading.local()
        self.api = _CapturingAPI(self, api or TelegramAPI(cfg["telegram_token"]))
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
                estimate_remaining=True,
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
        self.cooldown_photo = None
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
        # A "type your own time" prompt waiting for the owner's answer.
        self.schedule_draft = None
        self.schedule_lock = threading.Lock()

        # The live print card: what was last written into it and when, and a
        # pause after Telegram refused a widget, so a refusal is not retried
        # on every refresh.
        self.widget_sent = None
        self.widget_sent_at = 0.0
        self.widget_retry_at = 0.0
        self.snapshot_checked_at = 0.0

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

    def render(self, header="", detailed=False, compact=False):
        status, online = self._snapshot()
        show, line, _ = self.maintenance_view()
        return ui.render(status, online, self.cfg.get("printer_name", "Centauri Carbon"),
                         header=header, detailed=detailed,
                         maintenance_line=line if show else "",
                         now=self.clock(), tz=self.schedule_tz(), compact=compact)

    def keyboard(self, detailed=False):
        status, _ = self._snapshot()
        show, _, due = self.maintenance_view()
        scheduled = len(storage.load_schedule()) if self.schedule_available() else 0
        return ui.kb_main(
            status, allow_control=self.cfg.get("allow_control", True),
            allowed=self.allowed_actions(), detailed=detailed,
            maintenance=(show, due), scheduled=scheduled)

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

    # ---------------------------------------------------- scheduled starts

    def schedule_available(self):
        """Uploads and delayed starts need COSMOS and an allowed remote start."""
        return (self.backend_name == backend.MOONRAKER
                and self.moonraker is not None
                and self.action_allowed(backend.START))

    def schedule_tz(self):
        return schedule.tzinfo_from(self.cfg.get("schedule_utc_offset", ""))

    def scheduled_jobs(self):
        return storage.load_schedule()

    def add_scheduled(self, path, at):
        with self.schedule_lock:
            jobs = storage.load_schedule()
            job, error = schedule.make_job(jobs, path, at, self.clock())
            if job:
                storage.save_schedule(jobs + [job])
            return job, error

    def cancel_scheduled(self, job_id):
        with self.schedule_lock:
            jobs = storage.load_schedule()
            job = schedule.find(jobs, job_id)
            if job:
                storage.save_schedule(schedule.without(jobs, job_id))
            return job

    def _update_job(self, job_id, **fields):
        with self.schedule_lock:
            jobs = storage.load_schedule()
            for job in jobs:
                if job["id"] == job_id:
                    job.update(fields)
            storage.save_schedule(jobs)

    def upload_file(self, name, data):
        """(ok, path or reason, replaced) for a G-code received in Telegram."""
        if not self.schedule_available():
            return False, "загрузка файлов недоступна в этих настройках", False
        try:
            existing = {record["path"] for record in self.moonraker.list_file_records()}
        except moonraker.MoonrakerError:
            existing = set()
        try:
            path = self.moonraker.upload(name, data)
        except moonraker.MoonrakerError as e:
            return False, str(e), False
        self.refresh_files()
        return True, path, path in existing

    def file_details(self, path):
        with self.lock:
            details = dict((self.file_info or {}).get(path) or {})
        try:
            details.update(self.moonraker.file_metadata(path))
        except moonraker.MoonrakerError as e:
            log.debug("file metadata unavailable: %s", e)
        return details

    def schedule_live(self, job):
        """What can be checked right now about starting ``job``.

        Each probe stands alone: one that fails reads as "unknown", and
        schedule.decide treats unknown as a reason to ask, not to start.
        """
        status, online = self._snapshot()
        live = {"online": bool(online and status),
                "code": ((status or {}).get("PrintInfo") or {}).get("Status"),
                "file_exists": None, "filament": None, "printed_since": None}
        try:
            live["file_exists"] = job["path"] in self.moonraker.list_files()
        except Exception as e:
            log.info("schedule: file list unavailable - %s", e)
        try:
            live["filament"] = self.moonraker.filament_detected()
        except Exception as e:
            log.info("schedule: filament sensor unavailable - %s", e)
        try:
            recent = self.moonraker.history(limit=5)
            live["printed_since"] = any(
                float(item.get("start_time") or 0) > job["created"] for item in recent)
        except Exception as e:
            log.info("schedule: history unavailable - %s", e)
        return live

    def check_schedule(self):
        """One pass over planned starts: reminders, then jobs that are due."""
        if not self.schedule_available():
            return
        now, tz = self.clock(), self.schedule_tz()
        lead = max(0.0, float(self.cfg.get("schedule_reminder_min", 10) or 0)) * 60
        grace = max(1.0, float(self.cfg.get("schedule_late_grace_min", 15) or 15)) * 60
        for job in schedule.ordered(storage.load_schedule()):
            if schedule.reminder_due(job, now, lead):
                text = ui.schedule_reminder_text(
                    job, schedule.format_when(job["at"], now, tz),
                    schedule.format_left(job["at"], now))
                if self._schedule_notice(text, ui.kb_schedule_job(job["id"])):
                    self._update_job(job["id"], reminded=True)
            elif schedule.is_due(job, now):
                self._run_due_job(job, now, grace, tz)

    def _run_due_job(self, job, now, grace, tz):
        reasons = schedule.decide(job, now, self.schedule_live(job), grace)
        if not reasons:
            ok, info = self.perform(backend.START, job["path"])
            if ok:
                self.cancel_scheduled(job["id"])
                log.info("schedule: started %s", job["path"])
                self._schedule_notice(self.render(ui.schedule_started_text(job)), None)
                return
            reasons = ["запуск не прошёл: %s" % info]
        text = ui.schedule_ask_text(job, reasons, schedule.format_when(job["at"], now, tz))
        # Marked only once the owner has really been told. While Telegram is
        # unreachable the job is simply looked at again on the next pass.
        if self._schedule_notice(text, ui.kb_schedule_ask(job["id"])):
            self._update_job(job["id"], asked=int(now))

    def _schedule_notice(self, text, keyboard):
        try:
            return self.refresh_main(force_new=True, text=text,
                                     keyboard=keyboard) is not None
        except Exception as e:
            log.warning("schedule notice did not go out: %r", e)
            return False

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

    def refresh_main(self, force_new=False, text=None, photo=None, keyboard=None,
                     header="", quiet=False):
        """Keep exactly one status message in the chat.

        Edits in place; with force_new it is recreated as the last message in
        the chat so the buttons sit under the thumb. ``text`` replaces the
        usual status, which is how a notification becomes this same message
        instead of a second one beside it.

        The lock is essential: the event thread and the refresh thread once met
        on the same message, one deleted it, the other failed to edit and made
        another, and the chat was left with two. The old message is now deleted
        in every case, including when a new one is made after a failed edit.

        Telegram lets a bot delete its own messages in a private chat for 48
        hours only. An older status message still has to come down: editing it
        where it stands made /status, /files and every notification land
        silently far up the chat, where nobody saw them. So the new message is
        sent anyway and the old one is cut down to a one-line pointer without
        buttons. That leaves at most one such line per 48 quiet hours, since
        the new message can again be deleted for the next two days.

        With ``status_widget`` on, the status of a running print is the live
        card (widget.py): the status with ``header`` is drawn by the widget,
        and the caption under it is the short version. Any other screen
        (``text``) is still an ordinary message. If Telegram refuses the
        widget, the ordinary message goes out instead, exactly as before.
        """
        cap = self.capturing()
        if cap is not None:
            # A handler run for a tap on the card: a screen becomes the
            # card's screen, and "the status" means back to the card itself.
            if text is None:
                cap.close = True
                if cap.mid and storage.message_kind() == widget.KIND:
                    with self.main_lock:
                        self._push_widget_locked(cap.mid, force=True)
            else:
                if photo:
                    self.post_picture(photo)
                cap.screen(text, keyboard)
            return cap.mid
        with self.main_lock:
            mid = storage.message_id()
            kind = storage.message_kind() if mid else None
            kb = keyboard if keyboard is not None else self.keyboard()
            # The card carries its own buttons; under it only "Menu".
            card_kb = keyboard if keyboard is not None else (
                self.card_keyboard() if text is None else kb)
            live = text is None and self._widget_wanted()
            # A print that runs under an ordinary message gets its card on the
            # next refresh: moved down quietly, like any status recreation.
            promote = bool(live and mid and kind != widget.KIND and not force_new)
            cache = {}

            def body():
                if "body" not in cache:
                    cache["body"] = self.render(header) if text is None else text
                return cache["body"]

            def caption():
                return self.render(header, compact=True) if text is None else text

            def frame():
                if "photo" not in cache:
                    cache["photo"] = self.grab(max_age=10) if photo is None else photo
                return cache["photo"]

            def keep_old():
                if kind == widget.KIND:
                    return self._edit_widget(mid, caption(), card_kb)
                if widget.is_card(kind):
                    # a card of an older version: its caption only, never a
                    # frame, which would turn the widget into a photo
                    return self._edit_in_place(mid, caption(), kb, False)
                return self._edit_in_place(mid, body(), kb, frame())

            if mid and not force_new and not promote:
                if keep_old():
                    return mid
                # the edit failed: the message was deleted by hand, or is stale

            if live:
                new_mid = self._send_widget(card_kb, header, silent=promote or quiet)
                if new_mid:
                    self._drop_old(mid, kind)
                    return new_mid
                # refused: the ordinary message below, or the old one kept
                if promote and keep_old():
                    return mid

            answer = self.api.send_message(self.owner, body(), keyboard=kb, photo=frame())
            if not answer.get("ok"):
                # nothing new reached the chat: keep the old message working
                if mid and keep_old():
                    return mid
                return None
            new_mid = answer["result"]["message_id"]
            storage.set_message_id(new_mid)
            self._drop_old(mid, kind)
            return new_mid

    RETIRED_TEXT = "⬇️ Статус — ниже"

    def _drop_old(self, mid, kind):
        if mid and not self.api.delete_message(self.owner, mid).get("ok"):
            self._retire(mid, kind)

    def _retire(self, mid, kind=None):
        """Turn a status message Telegram no longer lets us delete into a
        pointer to the current one. Without buttons, so nothing is pressed on
        a stale panel. A message deleted by hand simply refuses both edits.
        A live card loses its state too, so it stops showing numbers that
        nobody updates any more."""
        forms = (dict(), dict(is_photo=True))
        if widget.is_card(kind):
            self.api.edit_widget_data(self.owner, mid, "")
            forms = (dict(is_photo=True),)
        for extra in forms:
            answer = self.api.edit_message(self.owner, mid, self.RETIRED_TEXT, **extra)
            if answer.get("ok") or "not modified" in str(answer.get("description")):
                return

    # ------------------------------------------------- the live print card

    WIDGET_RETRY_SEC = 600      # after a refused widget, ordinary messages for a while
    WIDGET_HEARTBEAT_SEC = 120  # rewrite unchanged numbers, so the card can tell it is alive

    def _widget_wanted(self):
        """A live card makes sense now: enabled, a private chat, a print on."""
        if not self.cfg.get("status_widget"):
            return False
        # Production Telegram sends widgets to private chats only, and a
        # private chat id is a user id: positive. A group gets the old message.
        if not self.owner.isdigit():
            return False
        if self.clock() < self.widget_retry_at:
            return False
        # Not only during a print: idle, the card is the panel too. Without
        # a connection the bot says so in an ordinary message.
        status, online = self._snapshot()
        return bool(status) and online

    def _card(self):
        status, online = self._snapshot()
        keys = widget.buttons(status, online, self.allowed_actions(),
                              camera=bool(self.cfg.get("send_photo")))
        return widget.card(status, online,
                           self.cfg.get("printer_name", "Centauri Carbon"), self.clock(),
                           keys=keys, items=widget.menu(self.keyboard()))

    FOLD_BUTTON = {"text": "▲ Свернуть меню", "callback_data": "refresh"}

    def card_keyboard(self):
        """Under the card: nothing. Every button is on the card itself; a
        screen it opens (files, macros...) brings its own buttons, and
        "Назад" takes them away again."""
        return []

    def card_menu_keyboard(self):
        """The full panel under a card of version 3, which had a "Menu"
        button outside the widget. Kept so such a card still answers."""
        return self.keyboard() + [[dict(self.FOLD_BUTTON)]]

    # ---------------------------------------------- a camera frame on request

    SNAPSHOT_TTL_SEC = 300

    def send_snapshot(self):
        """A fresh frame as its own message, taken down again after a while.

        The card cannot show a picture (no network in the widget, 1 KB of
        state), so the "📷" button on it sends one beside it. Only the newest
        frame stays; the previous one is deleted at once. The message id is
        kept in state.json, so a restart still cleans up.
        """
        shot = self.grab()
        if not shot or not self.post_picture(shot, "📷 %s" % time.strftime(
                "%H:%M", time.localtime(self.clock()))):
            return False
        # The frame went in below the card; the card moves down under it, so
        # the picture stays on top and the buttons stay under the thumb.
        try:
            self.refresh_main(force_new=True, quiet=True)
        except Exception as e:
            log.warning("status did not move under the frame: %r", e)
        return True

    def post_picture(self, photo, title="🖼"):
        """A picture beside the card: the camera frame, or a screen that is a
        picture (the bed map, the objects). A widget cannot show one. Only the
        newest stays, and it goes away after SNAPSHOT_TTL_SEC."""
        answer = self.api._api.send_message(
            self.owner, "%s · уберу через %d мин" % (title, self.SNAPSHOT_TTL_SEC // 60),
            photo=photo)
        if not answer.get("ok"):
            return False
        old = storage.load_state().get("snapshot_message")
        storage.update_state(snapshot_message=[answer["result"]["message_id"],
                                               self.clock() + self.SNAPSHOT_TTL_SEC])
        if old:
            self.api._api.delete_message(self.owner, old[0])
        return True

    def run_screen(self, handler):
        """Run an ordinary button's handler for a tap on the card, and return
        what it would have shown as a ScreenCapture."""
        cap = ScreenCapture(storage.message_id())
        self._tls.capture = cap
        try:
            handler()
        finally:
            self._tls.capture = None
        return cap

    def capturing(self):
        return getattr(self._tls, "capture", None)

    def expire_snapshot(self):
        """Take down a requested frame once its time is up. Looks at most
        every 15 s: it runs on every printer status."""
        if self.clock() - self.snapshot_checked_at < 15:
            return
        self.snapshot_checked_at = self.clock()
        snap = storage.load_state().get("snapshot_message")
        if not snap or self.clock() < float(snap[1]):
            return
        storage.update_state(snapshot_message=None)
        self.api.delete_message(self.owner, snap[0])

    def _send_widget(self, kb, header, silent=False):
        """Send the card. Its message id, or None if it did not go out."""
        caption = self.render(header, compact=True)
        if len(caption) > widget.CAPTION_LIMIT:
            return None
        data = self._card()
        answer = self.api.send_widget(self.owner, widget.HTML, caption=caption,
                                      keyboard=kb, chat_data=widget.encode(data),
                                      silent=silent, allow_queries=True)
        if not answer.get("ok"):
            self.widget_retry_at = self.clock() + self.WIDGET_RETRY_SEC
            log.info("live card refused, sending the ordinary message: %s",
                     answer.get("description") or "no answer")
            return None
        new_mid = answer["result"]["message_id"]
        storage.set_message_id(new_mid, kind=widget.KIND)
        self.widget_sent, self.widget_sent_at = data, self.clock()
        return new_mid

    def _edit_widget(self, mid, caption, kb):
        """Bring the card up to date: its numbers, then the caption and buttons."""
        self._push_widget_locked(mid, force=True)
        answer = self.api.edit_message(self.owner, mid, caption, keyboard=kb, is_photo=True)
        return bool(answer.get("ok")) or "not modified" in str(answer.get("description"))

    def push_widget(self):
        """Write fresh numbers into the card. Called on every printer status,
        throttled to ``widget_refresh_sec``. Silent, so it can run often."""
        if not self.cfg.get("status_widget"):
            return
        every = max(3.0, float(self.cfg.get("widget_refresh_sec") or 10))
        if self.clock() - self.widget_sent_at < every:
            return
        # A send or an edit of the message is under way: it brings the card
        # up to date itself, and the printer thread must not wait for it.
        if not self.main_lock.acquire(blocking=False):
            return
        try:
            mid = storage.message_id()
            if mid and storage.message_kind() == widget.KIND:
                self._push_widget_locked(mid)
        finally:
            self.main_lock.release()

    def _push_widget_locked(self, mid, force=False):
        data = self._card()
        if data is None:
            return False
        now = self.clock()
        if (not force and widget.same_content(data, self.widget_sent)
                and now - self.widget_sent_at < self.WIDGET_HEARTBEAT_SEC):
            return True
        answer = self.api.edit_widget_data(self.owner, mid, widget.encode(data))
        # Even on failure: the next try waits for the interval, not the next status.
        self.widget_sent_at = now
        description = str(answer.get("description") or "")
        if answer.get("ok") or "not modified" in description:
            self.widget_sent = data
            return True
        if description and "Too Many Requests" not in description:
            # Telegram says this is no card any more (deleted, replaced): treat
            # it as an ordinary message; the next refresh makes a new card.
            log.info("live card not updated: %s", description)
            storage.set_message_id(mid, kind=None)
        return False

    def _edit_in_place(self, mid, body, kb, photo):
        """Edit the status message whatever it currently is.

        The message may be text or a photo, and the new content may carry a
        frame or not; Telegram refuses some of these pairs. Each refusal is
        answered with the next form that can still fit: without the frame, then
        as a caption. "Not modified" means it already shows this content.
        """
        attempts = [dict(photo=photo)] if photo else []
        attempts += [dict(), dict(is_photo=True)]
        for extra in attempts:
            answer = self.api.edit_message(self.owner, mid, body, keyboard=kb, **extra)
            if answer.get("ok") or "not modified" in str(answer.get("description")):
                return True
            if "not found" in str(answer.get("description")):
                return False
        return False

    def edit_main_from_callback(self, clicked_mid, text, keyboard=None,
                                photo=None, is_photo=False, picture=False):
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
            tracked_kind = storage.message_kind() if tracked_mid else None

            def edit(target):
                if target == tracked_mid and widget.is_card(tracked_kind):
                    # The live card: its text is the caption. A screen that
                    # is a picture (the bed map, the objects) cannot be drawn
                    # on a widget: it replaces the card as an ordinary photo
                    # message, and "Назад" brings the card back. The camera
                    # frame of a plain refresh simply stays out.
                    capturing = self.capturing() is not None
                    if photo and picture and not capturing:
                        return False
                    if tracked_kind == widget.KIND:
                        self._push_widget_locked(target, force=True)
                    # On the card itself the picture goes beside it.
                    if photo and picture and capturing:
                        self.post_picture(photo)
                    form = dict(photo=None, is_photo=True)
                else:
                    form = dict(photo=photo, is_photo=is_photo)
                answer = self.api.edit_message(self.owner, target, text,
                                               keyboard=keyboard, **form)
                # Pressing Refresh on a message that already shows this is
                # not a failure, and must not rebuild the message.
                return bool(answer.get("ok")) or \
                    "not modified" in str(answer.get("description"))

            if tracked_mid and tracked_mid != clicked_mid:
                if edit(tracked_mid):
                    self.api.delete_message(self.owner, clicked_mid)
                    return tracked_mid

            if not edit(clicked_mid):
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
            else:
                storage.set_message_id(clicked_mid, kind=tracked_kind)
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
        # The status events pass a header, not finished text: refresh_main
        # draws them as the live card or as the usual message.
        text = None
        header = ""

        if event.kind == ps.STARTED:
            header = "🖨 <b>Печать началась</b>\n"
        elif event.kind == ps.RESUMED:
            header = "▶️ <b>Печать продолжилась</b>\n"
        elif event.kind == ps.PAUSED:
            header = "⏸ <b>Печать на паузе</b>\nПродолжить — кнопкой ниже.\n"
        elif event.kind == ps.STALLED:
            # The status object carries Klipper's own reason for the stop;
            # ui.stall_header prefers it over the bare numeric code.
            status, _ = self._snapshot()
            header = ui.stall_header(status, event.code)
        elif event.kind == ps.PROGRESS:
            header = "📊 <b>Идёт печать</b>\n"
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
                                    keyboard=keyboard, header=header)
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
            # By the time the nozzle has cooled the bed has long dropped, and a
            # fresh frame shows the part sunk out of view. The cooled notice
            # repeats the finish frame; "Refresh" still shows the live camera.
            self.cooldown_photo = photo if self.cooldown_pending else None

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
        shot, self.cooldown_photo = self.cooldown_photo, None
        try:
            self.refresh_main(force_new=True, text=self.render(
                "❄️ <b>Принтер остыл</b>\nСопло %.0f°C — можно безопасно заняться деталью.\n" % nozzle),
                photo=shot or False)
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
        try:
            return self.refresh_main(
                force_new=True, header="🔌 <b>Связь с принтером восстановлена</b>\n")
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
        try:
            self.push_widget()
        except Exception as e:
            log.debug("live card not updated: %r", e)
        try:
            self.expire_snapshot()
        except Exception as e:
            log.debug("camera frame not taken down: %r", e)

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

    def schedule_loop(self):
        """Look at planned starts every few seconds. COSMOS only."""
        if self.backend_name != backend.MOONRAKER:
            return
        while not self.stopping.is_set():
            try:
                self.check_schedule()
            except Exception as e:
                log.warning("schedule check failed: %r", e)
            self.stopping.wait(15)

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

    # Named outright: taps on the live card arrive as widget_query, which a
    # default subscription is not promised to include.
    UPDATES = ["message", "callback_query", "widget_query"]

    def telegram_loop(self):
        from .handlers import handle_callback, handle_message, handle_widget_query
        offset = None
        self.api.set_my_commands([
            {"command": "status", "description": "состояние принтера"},
            {"command": "snap", "description": "кадр с камеры"},
            {"command": "files", "description": "файлы на принтере"},
            {"command": "plan", "description": "запланированные печати"},
            {"command": "diag", "description": "диагностика COSMOS"},
            {"command": "mesh", "description": "карта высот стола"},
            {"command": "history", "description": "история печатей"},
            {"command": "macros", "description": "макросы COSMOS"},
            {"command": "help", "description": "справка"},
        ])
        while not self.stopping.is_set():
            try:
                answer = self.api.get_updates(offset=offset, timeout=25,
                                              allowed=self.UPDATES)
                for update in answer.get("result", []):
                    offset = update["update_id"] + 1
                    try:
                        if "callback_query" in update:
                            handle_callback(self, update["callback_query"])
                        elif "widget_query" in update:
                            handle_widget_query(self, update["widget_query"])
                        elif "message" in update:
                            handle_message(self, update["message"])
                    except Exception as e:
                        log.warning("update handling failed: %r", e)
            except Exception as e:
                log.warning("telegram loop: %r", e)
                self.stopping.wait(5)

    def run(self):
        log.info("bot started, printer %s", self.host)
        try:
            storage.forget_statistics()
        except OSError as e:  # housekeeping must never stop the bot starting
            log.debug("could not tidy state.json: %r", e)
        targets = [self.printer_loop, self.keepalive_loop, self.refresh_loop,
                   self.schedule_loop]
        for target in targets:
            threading.Thread(target=target, daemon=True).start()
        try:
            self.telegram_loop()
        except KeyboardInterrupt:
            log.info("exit")
        finally:
            self.stopping.set()
