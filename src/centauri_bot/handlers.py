# -*- coding: utf-8 -*-
"""What happens when the owner presses a button or sends a command.

Every handler takes the Bot as its first argument rather than reaching for a
global, so tests can drive them with a fake Telegram and a fake printer.

Access control is the first thing in both entry points: an update from any chat
other than the configured owner is answered with a refusal and goes no further.
"""
import logging
import time
from html import escape

from . import backend
from . import heightmap
from . import moonraker
from . import printer_state as ps
from . import schedule
from . import storage
from . import ui
from .telegram_api import TelegramError


log = logging.getLogger(__name__)

CONFIRM_LABELS = {
    "pause": ("поставить на паузу", "⏸ Поставить печать на паузу?"),
    "resume": ("продолжить печать", "▶️ Продолжить печать?"),
    "stop": ("остановить",
             "⏹ <b>Остановить печать?</b>\nОтменить это будет нельзя."),
}

CONTROL_OFF = "Управление выключено в настройках.\n\n"

# The printer needs a moment to act on a command before its next status is
# worth rendering; without this the message redraws showing the old state and
# looks like the button did nothing. Named so the tests can shrink it - a suite
# that really slept would take minutes.
SETTLE_SEC = 1.2
SETTLE_AFTER_ACTION_SEC = 1.5


def _is_owner(bot, chat, sender=None):
    if str(chat) != bot.owner:
        return False
    if not bot.owner_user:
        return True
    return sender is not None and str(sender) == bot.owner_user


def show_files(bot, chat, mid=None, is_photo=False, force_new=False):
    """Show the file list without leaving a second status message behind.

    A callback edits the message whose button was pressed.  ``/files`` has no
    such message id, so it recreates the tracked main message as the file list;
    the Back button can then turn that very message back into the status.
    """
    ok, info = bot.refresh_files()
    with bot.lock:
        files = list(bot.files or [])
        file_info = dict(bot.file_info or {})
    if not files:
        text = "Список файлов получить не вышло (%s)." % info
        if force_new:
            bot.refresh_main(force_new=True, text=text, keyboard=bot.keyboard())
        elif mid:
            bot.edit_main_from_callback(
                mid, text, keyboard=bot.keyboard(), is_photo=is_photo)
        else:
            bot.api.send_message(chat, text)
        return
    body = ui.files_text(files, info=file_info)
    can_start = bot.action_allowed(backend.START)
    refs = bot.prepare_file_choices(files[:8]) if can_start else None
    can_delete = bot.action_allowed(backend.DELETE)
    delete_refs = bot.prepare_file_choices(files[:8], "delete-choice") if can_delete else None
    # Bound to the list as shown: a file uploaded after this screen is never
    # swept away by a button that did not know about it.
    delete_all_ref = (bot.confirmations.issue("delete-all-choice", tuple(files))
                      if can_delete and len(files) > 1 else None)
    rows = ui.kb_files(files, allow_control=bot.cfg.get("allow_control", True),
                       can_start=can_start, refs=refs, can_delete=can_delete,
                       delete_refs=delete_refs, delete_all_ref=delete_all_ref)
    if force_new:
        bot.refresh_main(force_new=True, text=body, keyboard=rows)
    elif mid:
        bot.edit_main_from_callback(
            mid, body, keyboard=rows, is_photo=is_photo)
    else:
        bot.api.send_message(chat, body, keyboard=rows)


def _show_readonly(bot, chat, mid, is_photo, force_new, text, keyboard, photo=None):
    if force_new:
        bot.refresh_main(force_new=True, text=text, keyboard=keyboard, photo=photo)
    elif mid:
        bot.edit_main_from_callback(mid, text, keyboard=keyboard, photo=photo,
                                    is_photo=is_photo)
    else:
        bot.api.send_message(chat, text, keyboard=keyboard, photo=photo)


def show_history(bot, chat, mid=None, is_photo=False, force_new=False):
    ok, result = bot.history()
    text = ui.history_text(result) if ok else "⚠️ Историю получить не вышло: %s" % result
    _show_readonly(bot, chat, mid, is_photo, force_new, text, ui.kb_back())


def show_mesh(bot, chat, mid=None, is_photo=False, force_new=False):
    ok, result = bot.bed_mesh()
    if not ok:
        _show_readonly(bot, chat, mid, is_photo, force_new,
                       "⚠️ Карту стола получить не вышло: %s" % result, ui.kb_back())
        return
    try:
        photo = heightmap.render(result["points"])
        text = ui.height_map_text(result)
    except (KeyError, TypeError, ValueError) as e:
        photo, text = None, "⚠️ Сохранённая сетка стола некорректна: %s" % e
    _show_readonly(bot, chat, mid, is_photo, force_new, text, ui.kb_back(), photo=photo)


def show_macros(bot, chat, mid=None, is_photo=False, force_new=False):
    ok, names = bot.macros()
    if not ok:
        _show_readonly(bot, chat, mid, is_photo, force_new,
                       "⚠️ Макросы получить не вышло: %s" % names, ui.kb_back())
        return
    enabled = ui.macro_order(name for name in names if bot.macro_allowed(name))
    refs = bot.prepare_macro_choices(enabled)
    _show_readonly(bot, chat, mid, is_photo, force_new,
                   ui.macros_text(names, enabled), ui.kb_macros(enabled, refs))


def show_bed_calibration(bot, chat, mid=None, is_photo=False, force_new=False):
    """Second screen for bed calibration: pick the temperature to take the mesh at."""
    ok, names = bot.macros()
    if not ok:
        _show_readonly(bot, chat, mid, is_photo, force_new,
                       "⚠️ Макросы получить не вышло: %s" % names, ui.kb_back())
        return
    enabled = ui.macro_order(name for name in names
                             if name in ui.BED_CALIB_MACROS and bot.macro_allowed(name))
    if not enabled:
        _show_readonly(bot, chat, mid, is_photo, force_new,
                       "⚠️ Калибровка стола недоступна: ни один из макросов "
                       "CALIBRATE_BED_* не разрешён в настройках бота.", ui.kb_back())
        return
    refs = bot.prepare_macro_choices(enabled)
    _show_readonly(bot, chat, mid, is_photo, force_new,
                   ui.bed_calibration_text(), ui.kb_bed_temps(enabled, refs))


def show_objects(bot, chat, mid=None, is_photo=False, force_new=False):
    ok, state = bot.exclude_objects()
    if not ok:
        _show_readonly(bot, chat, mid, is_photo, force_new,
                       "⚠️ Объекты получить не вышло: %s" % state, ui.kb_back())
        return
    names = list(state.get("Objects") or [])
    excluded = set(state.get("ExcludedObjects") or [])
    active = [name for name in names if name not in excluded]
    if state.get("PrintState") not in ("printing", "paused"):
        text = "🧩 Сейчас нет активной печати с отдельными объектами."
        rows = ui.kb_back()
    elif len(active) < 2:
        text = ("🧩 Убрать модель нельзя: в задании не размечено несколько "
                "объектов или остался только один.")
        rows = ui.kb_back()
    else:
        values = [{"name": name, "filename": state.get("Filename") or ""}
                  for name in active]
        refs = bot.prepare_object_choices(values)
        text = ui.objects_text(state)
        rows = ui.kb_objects(active, refs, state.get("CurrentObject") or "")
    _show_readonly(bot, chat, mid, is_photo, force_new, text, rows)


def handle_callback(bot, query):
    chat = str(query["message"]["chat"]["id"])
    mid = query["message"]["message_id"]
    data = query.get("data", "")
    is_photo = "photo" in query.get("message", {})

    sender = query.get("from", {}).get("id")
    if not _is_owner(bot, chat, sender):
        bot.api.answer_callback(query["id"], "Не для тебя.")
        log.info("callback from a foreign chat was refused")
        return

    if data == "noop":
        bot.api.answer_callback(query["id"])
        return

    # Any other button ends a "type your own time" prompt: the owner has moved
    # on, and the next ordinary message must not be read as a start time.
    if not data.startswith("sched"):
        with bot.lock:
            bot.schedule_draft = None

    if data == "plan":
        bot.api.answer_callback(query["id"])
        show_plan(bot, chat, mid, is_photo)
        return

    if data.startswith(("sched:", "schedcancel:", "schedrun:")):
        _schedule_callback(bot, chat, mid, query, data, is_photo)
        return

    if data == "help":
        bot.api.answer_callback(query["id"])
        text, keyboard = ui.help_screen(
            bot.cfg.get("allow_control", True), allowed=bot.allowed_actions(),
            can_schedule=bot.schedule_available())
        bot.api.edit_message(chat, mid, text, keyboard=keyboard, is_photo=is_photo)
        return

    if data == "maint:done":
        storage.reset_maintenance()
        bot.maintenance.pending = 0.0
        bot.api.answer_callback(query["id"], "Счётчик обслуживания сброшен.")
        bot.api.edit_message(
            chat, mid,
            bot.render("🧰 <b>Обслуживание отмечено</b>\nСчётчик пошёл заново.\n"),
            keyboard=bot.keyboard(), photo=bot.grab(max_age=5), is_photo=is_photo)
        return

    if data in ("refresh", "details", "brief", "snap"):
        detailed = (data == "details")
        bot.api.answer_callback(query["id"])
        bot.edit_main_from_callback(
            mid, bot.render(detailed=detailed),
            keyboard=bot.keyboard(detailed), photo=bot.grab(max_age=5),
            is_photo=is_photo)
        return

    if data == "files":
        bot.api.answer_callback(query["id"], "Читаю список…")
        show_files(bot, chat, mid, is_photo)
        return

    if data == "diag":
        bot.api.answer_callback(query["id"], "Проверяю COSMOS…")
        ok, result = bot.diagnostics()
        text = ui.diagnostics_text(result) if ok else "⚠️ Диагностика не прошла: %s" % result
        bot.edit_main_from_callback(mid, text, keyboard=bot.keyboard(),
                                    is_photo=is_photo)
        return

    if data == "history":
        bot.api.answer_callback(query["id"], "Читаю историю…")
        show_history(bot, chat, mid, is_photo)
        return

    if data == "mesh":
        bot.api.answer_callback(query["id"], "Читаю сохранённую сетку…")
        show_mesh(bot, chat, mid, is_photo)
        return

    if data == "macros":
        bot.api.answer_callback(query["id"], "Читаю макросы…")
        show_macros(bot, chat, mid, is_photo)
        return

    if data.startswith("askprompt:"):
        # Кнопка, которую сам принтер пометил предупреждением.
        gcode = bot.resolve_prompt_choice(data.split(":", 1)[1])
        if not gcode:
            bot.api.answer_callback(query["id"],
                                    "Кнопка устарела — обновите статус.")
            return
        token = bot.prepare_prompt_choices([gcode])[0]
        bot.api.answer_callback(query["id"])
        bot.api.edit_message(chat, mid,
                             ui.prompt_confirm_text(gcode, gcode),
                             keyboard=ui.kb_confirm("prompt:%s" % token,
                                                    "отправить"),
                             is_photo=is_photo)
        return

    if data.startswith("prompt:"):
        # Кнопка из окна, которое принтер сам открыл у себя на экране.
        gcode = bot.resolve_prompt_choice(data.split(":", 1)[1])
        if not gcode:
            bot.api.answer_callback(query["id"],
                                    "Кнопка устарела — обновите статус.")
            return
        ok, info = bot.perform(backend.PROMPT, gcode)
        bot.api.answer_callback(query["id"],
                                "Отправлено." if ok else "Не прошло: %s" % info)
        with bot.lock:
            bot.prompt_shown = None      # пусть следующий опрос покажет новое окно
        return

    if data == "bedcalib":
        bot.api.answer_callback(query["id"])
        show_bed_calibration(bot, chat, mid, is_photo)
        return

    if data == "objects":
        bot.api.answer_callback(query["id"], "Читаю объекты печати…")
        show_objects(bot, chat, mid, is_photo)
        return

    if data.startswith("menu:"):
        bot.api.answer_callback(query["id"])
        which = data[5:]
        if which == "fans":
            with bot.lock:
                bot.fan_draft = {}
        keyboard = _submenu(bot, which)
        bot.api.edit_message(chat, mid, bot.render(), keyboard=keyboard,
                             is_photo=is_photo)
        return

    if data == "light":
        _toggle_light(bot, chat, mid, query)
        return

    if data.startswith("set:fan:"):
        key, raw = data[len("set:fan:"):].split(":")
        with bot.lock:
            draft = dict(bot.fan_draft or {})
            draft[key] = int(raw)
            bot.fan_draft = draft
        bot.api.answer_callback(
            query["id"], "%s: %s" % (ui.FAN_HUMAN[key],
                                     "выкл" if raw == "0" else raw + "%"))
        bot.api.edit_message(chat, mid, bot.render(),
                             keyboard=ui.kb_fans(_fan_current(bot), draft),
                             is_photo=is_photo)
        return

    if data == "fans:cancel":
        with bot.lock:
            bot.fan_draft = None
        bot.api.answer_callback(query["id"])
        bot.api.edit_message(chat, mid, bot.render(), keyboard=bot.keyboard(),
                             photo=bot.grab(max_age=5))
        return

    if data == "fans:apply":
        _apply_fans(bot, chat, mid, query)
        return

    if data.startswith("set:"):
        _apply_setting(bot, chat, mid, query, data)
        return

    if data.startswith("ask:"):
        _ask_confirmation(bot, chat, mid, query, data[4:], is_photo)
        return

    if data.startswith("do:"):
        _do_action(bot, chat, mid, query, data[3:])
        return

    bot.api.answer_callback(query["id"])


# ------------------------------------------------------------------ pieces

def _submenu(bot, which):
    status, _ = bot._snapshot()
    if which == "speed":
        current = ((status or {}).get("PrintInfo") or {}).get("PrintSpeedPct")
        return ui.kb_speed(current)
    if which == "temp":
        return ui.kb_temp()
    if which == "fans":
        return ui.kb_fans(_fan_current(bot), {})
    return bot.keyboard()


def _fan_current(bot):
    status, _ = bot._snapshot()
    fans = (status or {}).get("CurrentFanSpeed") or {}
    return {k: int(fans.get(k, 0)) for k in ui.FAN_KEYS}


def _toggle_light(bot, chat, mid, query):
    if not bot.action_allowed(backend.LIGHT):
        bot.api.answer_callback(query["id"], CONTROL_OFF.strip())
        return
    with bot.lock:
        lit = (((bot.status or {}).get("LightStatus") or {}).get("SecondLight") == 1)
    _ask_hardware(bot, chat, mid, query, backend.LIGHT, not lit,
                  "💡 Свет %s" % ("включить" if not lit else "выключить"),
                  "переключит свет корпуса и синхронизирует свет камеры")


def _apply_fans(bot, chat, mid, query):
    with bot.lock:
        draft = dict(bot.fan_draft or {})
        bot.fan_draft = None
    target = _fan_current(bot)
    target.update(draft)
    _ask_hardware(bot, chat, mid, query, backend.FANS, target,
                  "🌀 Применить вентиляторы",
                  "установит обдув детали %d%%, приток %d%% и вытяжку %d%%" % (
                      target["ModelFan"], target["AuxiliaryFan"], target["BoxFan"]))


def _apply_setting(bot, chat, mid, query, data):
    _, what, value = data.split(":", 2)
    action = backend.SPEED if what == "speed" else backend.TEMPERATURE
    if what == "speed":
        _ask_hardware(bot, chat, mid, query, action, int(value),
                      "⚡ Установить %s%%" % value,
                      "изменит коэффициент скорости текущей печати")
    else:
        label, nozzle, bed = ui.HEAT_PRESETS.get(value, ("?", 0, 0))
        _ask_hardware(bot, chat, mid, query, action, (nozzle, bed),
                      "🌡 " + label, "установит сопло %d°C и стол %d°C" % (nozzle, bed))


def _ask_hardware(bot, chat, mid, query, action, value, label, description):
    if not bot.action_allowed(action):
        bot.api.answer_callback(query["id"], CONTROL_OFF.strip())
        return
    token = bot.issue_control_confirmation(action, value)
    bot.api.answer_callback(query["id"])
    bot.api.edit_message(chat, mid, "<b>Подтвердить?</b>\n%s\n\nДействие: %s."
                         % (escape(label), escape(description)),
                         keyboard=ui.kb_confirm("control:%s:%s" % (action, token), "выполнить"),
                         is_photo="photo" in query.get("message", {}))


def _ask_confirmation(bot, chat, mid, query, what, is_photo):
    """Dangerous actions always get a second screen. No support button here."""
    if what.startswith("print:"):
        if not bot.action_allowed(backend.START):
            bot.api.answer_callback(query["id"], CONTROL_OFF.strip())
            return
        choice = what.split(":", 1)[1]
        path = bot.resolve_file_choice(choice)
        if not path:
            bot.api.answer_callback(query["id"], "Список устарел. Открой файлы заново.")
            return
        name = path.rsplit("/", 1)[-1]
        confirmation = bot.issue_print_confirmation(path)
        bot.api.answer_callback(query["id"])
        schedule_ref = (bot.confirmations.issue("schedule-path", path)
                        if bot.schedule_available() else None)
        bot.api.send_message(
            chat,
            "🖨 <b>Запустить печать?</b>\n<i>%s</i>\n\n"
            "Убедись по снимку, что стол пуст." % escape(name),
            keyboard=ui.kb_print_confirm(confirmation, schedule_ref),
            photo=bot.grab())
        return
    if what.startswith("delete:"):
        if not bot.action_allowed(backend.DELETE):
            bot.api.answer_callback(query["id"], CONTROL_OFF.strip())
            return
        choice = what.split(":", 1)[1]
        path = bot.resolve_file_choice(choice, "delete-choice")
        if not path:
            bot.api.answer_callback(query["id"], "Список устарел. Открой файлы заново.")
            return
        with bot.lock:
            current = (bot.status or {}).get("PrintInfo", {}).get("Filename")
        if current == path:
            bot.api.answer_callback(query["id"], "Нельзя удалить файл текущей печати.")
            return
        token = bot.issue_delete_confirmation(path)
        bot.api.answer_callback(query["id"])
        bot.api.send_message(chat, "🗑 <b>Удалить файл?</b>\n<i>%s</i>\n\nВосстановить его с принтера будет нельзя." % escape(path.rsplit("/", 1)[-1]), keyboard=ui.kb_confirm("delete:%s" % token, "удалить"))
        return
    if what.startswith("delall:"):
        if not bot.action_allowed(backend.DELETE):
            bot.api.answer_callback(query["id"], CONTROL_OFF.strip())
            return
        paths = bot.confirmations.consume("delete-all-choice", what.split(":", 1)[1])
        if not paths:
            bot.api.answer_callback(query["id"], "Список устарел. Открой файлы заново.")
            return
        with bot.lock:
            current = (bot.status or {}).get("PrintInfo", {}).get("Filename")
        paths = tuple(path for path in paths if path != current)
        if not paths:
            bot.api.answer_callback(query["id"], "Удалять нечего: остался только файл текущей печати.")
            return
        token = bot.confirmations.issue("delete-all", paths)
        planned = sum(1 for job in bot.scheduled_jobs() if job.get("path") in paths) \
            if bot.schedule_available() else 0
        text = "🗑 <b>Удалить все файлы с принтера (%d)?</b>\n\nВосстановить их будет нельзя." % len(paths)
        if current:
            text += "\nФайл текущей печати останется."
        if planned:
            text += "\n⏰ Запланированных стартов с этими файлами: %d — они не смогут начаться." % planned
        bot.api.answer_callback(query["id"])
        bot.api.send_message(chat, text, keyboard=ui.kb_confirm("delall:%s" % token, "удалить все"))
        return
    if what.startswith("exclude:"):
        if not bot.action_allowed(backend.EXCLUDE_OBJECT):
            bot.api.answer_callback(query["id"], CONTROL_OFF.strip())
            return
        choice = what.split(":", 1)[1]
        value = bot.resolve_object_choice(choice)
        if not isinstance(value, dict) or not value.get("name"):
            bot.api.answer_callback(query["id"],
                                    "Список устарел. Открой объекты заново.")
            return
        token = bot.issue_object_confirmation(value)
        bot.api.answer_callback(query["id"])
        bot.api.edit_message(
            chat, mid,
            "❌ <b>Убрать модель из текущей печати?</b>\n%s\n\n"
            "Klipper пропустит все оставшиеся движения этой модели. "
            "Уже напечатанная часть останется на столе; вернуть её в это задание нельзя."
            % escape(ui.object_label(value["name"])),
            keyboard=ui.kb_confirm("exclude:%s" % token, "убрать модель"),
            is_photo=is_photo)
        return
    if what.startswith("macro:"):
        choice = what.split(":", 1)[1]
        name = bot.resolve_macro_choice(choice)
        if not name or not bot.macro_allowed(name):
            bot.api.answer_callback(query["id"], "Макрос не разрешён или список устарел.")
            return
        token = bot.issue_macro_confirmation(name)
        bot.api.answer_callback(query["id"])
        bot.api.edit_message(chat, mid,
                             "🧩 <b>Запустить «%s»?</b>\n\n"
                             "Что произойдёт: %s.\n\n"
                             "Системное имя: <code>%s</code>\n\n"
                             "Макрос может двигать механизмы или менять состояние принтера."
                             % (escape(ui.macro_label(name)),
                                escape(ui.macro_description(name)), escape(name)),
                             keyboard=ui.kb_confirm("macro:%s" % token, "запустить"),
                             is_photo=is_photo)
        return
    action = {"pause": backend.PAUSE, "resume": backend.RESUME,
              "stop": backend.CANCEL}.get(what)
    if action and not bot.action_allowed(action):
        bot.api.answer_callback(query["id"], CONTROL_OFF.strip())
        return
    if not action:
        bot.api.answer_callback(query["id"], "Неизвестное действие.")
        return
    label, question = CONFIRM_LABELS.get(what, ("выполнить", "Выполнить?"))
    token = bot.issue_action_confirmation(action)
    bot.api.answer_callback(query["id"])
    bot.api.edit_message(chat, mid, question,
                         keyboard=ui.kb_confirm("job:%s:%s" % (action, token), label),
                         is_photo=is_photo)


def _do_action(bot, chat, mid, query, what):
    note = ""
    action = (backend.START if what.startswith(("print:", "sched:", "schedrun:")) else
              backend.DELETE if what.startswith(("delete:", "delall:")) else
              backend.EXCLUDE_OBJECT if what.startswith("exclude:") else
              backend.RUN_MACRO if what.startswith("macro:") else None)
    value = None
    if what.startswith("control:"):
        parts = what.split(":", 2)
        candidate = parts[1] if len(parts) == 3 else ""
        token = parts[2] if len(parts) == 3 else ""
        if candidate in {backend.LIGHT, backend.SPEED, backend.TEMPERATURE, backend.FANS}:
            value = bot.consume_control_confirmation(candidate, token)
            if value is not None:
                action = candidate
    if what.startswith("job:"):
        parts = what.split(":", 2)
        candidate = parts[1] if len(parts) == 3 else ""
        token = parts[2] if len(parts) == 3 else ""
        if candidate in backend.JOB_ACTIONS and bot.consume_action_confirmation(
                candidate, token):
            action = candidate
    if not action or not bot.action_allowed(action):
        note = CONTROL_OFF
    elif action == backend.PAUSE:
        ok, info = bot.perform(backend.PAUSE)
        note = "⏸ Команда паузы принята.\n\n" if ok else "⚠️ Пауза не прошла (%s).\n\n" % info
    elif action == backend.RESUME:
        ok, info = bot.perform(backend.RESUME)
        note = "▶️ Команда продолжения принята.\n\n" if ok \
            else "⚠️ Не продолжилось (%s).\n\n" % info
    elif action == backend.CANCEL:
        ok, info = bot.perform(backend.CANCEL)
        note = "⏹ Команда остановки принята.\n\n" if ok \
            else "⚠️ Не остановилось (%s).\n\n" % info
    elif what.startswith("print:"):
        token = what.split(":", 1)[1]
        path = bot.consume_print_confirmation(token)
        if path:
            ok, info = bot.perform(backend.START, path)
            note = "🖨 Печать запущена.\n\n" if ok else "⚠️ Не запустилось (%s).\n\n" % info
        else:
            note = "⚠️ Подтверждение устарело. Выбери файл заново.\n\n"
    elif what.startswith("sched:"):
        choice = bot.confirmations.consume("schedule-confirm", what.split(":", 1)[1])
        if not choice or not bot.schedule_available():
            note = "⚠️ Подтверждение устарело. Выберите время заново.\n\n"
        else:
            path, start = choice
            job, error = bot.add_scheduled(path, start)
            note = (ui.schedule_added_note(
                        path, schedule.format_when(start, bot.clock(), bot.schedule_tz()))
                    if job else "⚠️ Не запланировал: %s.\n\n" % escape(error))
    elif what.startswith("schedrun:"):
        job_id = bot.confirmations.consume("schedule-run", what.split(":", 1)[1])
        job = schedule.find(bot.scheduled_jobs(), job_id) if job_id else None
        if not job:
            note = "⚠️ Задание уже отменено или подтверждение устарело.\n\n"
        else:
            ok, info = bot.perform(backend.START, job["path"])
            if ok:
                bot.cancel_scheduled(job["id"])
                note = "🖨 Печать запущена.\n\n"
            else:
                note = "⚠️ Не запустилось (%s).\n\n" % info
    elif what.startswith("delete:"):
        token = what.split(":", 1)[1]
        path = bot.consume_delete_confirmation(token)
        if path:
            with bot.lock:
                current = (bot.status or {}).get("PrintInfo", {}).get("Filename")
            if current == path:
                note = "⚠️ Файл текущей печати удалить нельзя.\n\n"
            else:
                ok, info = bot.perform(backend.DELETE, path)
                note = "🗑 Файл удалён.\n\n" if ok else "⚠️ Удаление не прошло (%s).\n\n" % info
        else:
            note = "⚠️ Подтверждение устарело. Открой файлы заново.\n\n"
    elif what.startswith("delall:"):
        paths = bot.confirmations.consume("delete-all", what.split(":", 1)[1])
        if not paths:
            note = "⚠️ Подтверждение устарело. Открой файлы заново.\n\n"
        else:
            removed, failed = 0, []
            for path in paths:
                # The print may have started while the question was open.
                with bot.lock:
                    current = (bot.status or {}).get("PrintInfo", {}).get("Filename")
                if path == current:
                    continue
                ok, info = bot.perform(backend.DELETE, path)
                if ok:
                    removed += 1
                else:
                    failed.append(info)
            note = "🗑 Удалено файлов: %d.\n\n" % removed
            if failed:
                note = "⚠️ Удалено %d, не удалилось %d (%s).\n\n" % (
                    removed, len(failed), escape(str(failed[0])))
            bot.refresh_files()
    elif what.startswith("exclude:"):
        token = what.split(":", 1)[1]
        value = bot.consume_object_confirmation(token)
        if not isinstance(value, dict) or not value.get("name"):
            note = "⚠️ Подтверждение устарело. Открой объекты заново.\n\n"
        else:
            ok, info = bot.perform(backend.EXCLUDE_OBJECT, value)
            note = ("🧩 Модель <b>%s</b> убрана из текущей печати.\n\n"
                    % escape(ui.object_label(value["name"]))) if ok \
                else "⚠️ Убрать модель не вышло (%s).\n\n" % escape(info)
    elif what.startswith("prompt:"):
        # Подтверждённая кнопка подсказки, помеченной принтером как опасная.
        gcode = bot.resolve_prompt_choice(what.split(":", 1)[1])
        if not gcode:
            note = "⚠️ Подтверждение устарело.\n\n"
        else:
            ok, info = bot.perform(backend.PROMPT, gcode)
            note = ("🖐 Отправлено принтеру.\n\n" if ok
                    else "⚠️ Не прошло: %s\n\n" % info)
        with bot.lock:
            bot.prompt_shown = None
    elif what.startswith("macro:"):
        token = what.split(":", 1)[1]
        name = bot.consume_macro_confirmation(token)
        if not name or not bot.macro_allowed(name):
            note = "⚠️ Макрос не разрешён или подтверждение устарело.\n\n"
        else:
            ok, info = bot.perform(backend.RUN_MACRO, name)
            note = "🧩 Действие «%s» запущено.\n\n" % escape(ui.macro_label(name)) \
                if ok else "⚠️ Макрос не запустился (%s).\n\n" % info
    elif action in {backend.LIGHT, backend.SPEED, backend.TEMPERATURE, backend.FANS}:
        ok, info = bot.perform(action, value)
        names = {backend.LIGHT: "💡 Свет", backend.SPEED: "⚡ Скорость",
                 backend.TEMPERATURE: "🌡 Нагрев", backend.FANS: "🌀 Вентиляторы"}
        note = "%s: команда принята.\n\n" % names[action] if ok else "⚠️ Команда не принялась (%s).\n\n" % info
    bot.api.answer_callback(query["id"], note or "Готово")
    time.sleep(SETTLE_AFTER_ACTION_SEC)
    # Answering the same callback twice is refused by Telegram with "query is
    # too old or invalid", which used to fill the log.
    bot.api.edit_message(chat, mid, bot.render(note), keyboard=bot.keyboard(),
                         photo=bot.grab(max_age=5))


# ------------------------------------------------------------------ messages

def handle_message(bot, message):
    # Service messages - "bot pinned a message", joins and the like - arrive
    # here too, without text. Answering them is a loop: pin -> service ->
    # answer -> pin again, and the bot buries the chat by itself. Anything that
    # is not text from a human is skipped.
    document = message.get("document")
    if message.get("from", {}).get("is_bot") or not (
            (message.get("text") or "").strip() or isinstance(document, dict)):
        return
    chat = str(message["chat"]["id"])
    text = (message.get("text") or "").strip().lower()

    sender = message.get("from", {}).get("id")
    if not _is_owner(bot, chat, sender):
        bot.api.send_message(chat, "Этот бот личный.")
        log.info("message from a foreign chat was refused")
        return

    if isinstance(document, dict):
        handle_document(bot, chat, document)
        return
    if _maybe_schedule_text(bot, chat, text):
        return

    # Whatever the bot answers, the status with its buttons goes out last, so
    # it is always at the bottom under the thumb and needs no scrolling.
    if text.startswith("/snap"):
        bot.api.send_message(chat, "📷 " + time.strftime("%H:%M:%S"), photo=bot.grab())
    elif text.startswith("/files"):
        show_files(bot, chat, force_new=True)
        return
    elif text.startswith("/plan"):
        show_plan(bot, chat, force_new=True)
        return
    elif text.startswith("/diag") or text.startswith("/diagnostics"):
        ok, result = bot.diagnostics()
        bot.api.send_message(chat, ui.diagnostics_text(result) if ok else "⚠️ Диагностика не прошла: %s" % result)
    elif text.startswith("/mesh"):
        show_mesh(bot, chat, force_new=True)
        return
    elif text.startswith("/history"):
        show_history(bot, chat, force_new=True)
        return
    elif text.startswith("/macros"):
        show_macros(bot, chat, force_new=True)
        return
    elif text.startswith("/help") or text.startswith("/start"):
        body, keyboard = ui.help_screen(
            bot.cfg.get("allow_control", True), allowed=bot.allowed_actions(),
            can_schedule=bot.schedule_available())
        bot.api.send_message(chat, body, keyboard=keyboard)
        _maybe_help_reminder(bot, chat)
        return
    # Anything else silently shows the status: any message reads as "show me
    # what is going on". A separate "unknown command" reply is just noise.

    if bot.refresh_main(force_new=True) is None:
        bot.api.send_message(chat, "Не смог показать статус — проверь связь с принтером.")


def _maybe_help_reminder(bot, chat):
    """If the printer has been idle for a long time the finished-print path
    never runs, so /help is the fallback place for the monthly note."""
    note = bot.maybe_support_note()
    if not note:
        return
    from . import support
    answer = bot.api.send_message(chat, note.strip(),
                                  keyboard=support.reminder_keyboard())
    if answer.get("ok"):
        bot.confirm_support_note_shown()


# ------------------------------------------------------------ scheduled starts

# Telegram's Bot API hands out files up to this size and no bigger.
MAX_DOCUMENT_BYTES = 20_000_000
# How long a "type your own time" prompt keeps listening for the answer.
DRAFT_TTL_SEC = 600


def _document_name(document):
    """A safe G-code file name from what Telegram reports, or ""."""
    raw = str(document.get("file_name") or "").replace("\\", "/").rsplit("/", 1)[-1]
    raw = "".join(ch for ch in raw if ch.isprintable()).strip()[:200]
    return moonraker.normalized_gcode_path(raw)


def handle_document(bot, chat, document):
    """A G-code sent into the chat: download, upload, offer print or schedule."""
    if not bot.schedule_available():
        bot.api.send_message(
            chat, "📥 Файлы принимаю только с COSMOS и при разрешённом запуске "
                  "печати из бота (<code>moonraker_allow_remote_start</code>).")
        return
    name = _document_name(document)
    if not name:
        bot.api.send_message(chat, "📥 Это не G-code. Пришлите файл <code>.gcode</code>.")
        return
    size = int(document.get("file_size") or 0)
    if size > MAX_DOCUMENT_BYTES:
        bot.api.send_message(
            chat, "📥 Файл %.0f МБ, а Telegram отдаёт ботам файлы до %d МБ.\n"
                  "Залейте его из слайсера — запланировать можно из «📂 Файлы»."
                  % (size / 1_000_000.0, MAX_DOCUMENT_BYTES // 1_000_000))
        return

    progress = bot.api.send_message(chat, "📥 Принимаю <i>%s</i>…" % escape(name))
    progress_mid = (progress.get("result") or {}).get("message_id")
    try:
        data = bot.api.download_document(document.get("file_id"), MAX_DOCUMENT_BYTES)
    except TelegramError as e:
        _finish_upload(bot, chat, progress_mid,
                       "⚠️ Файл из Telegram не скачался: %s" % escape(str(e)))
        return
    ok, result, replaced = bot.upload_file(name, data)
    if not ok:
        _finish_upload(bot, chat, progress_mid,
                       "⚠️ На принтер не загрузилось: %s" % escape(str(result)))
        return
    details = bot.file_details(result)
    details.setdefault("size", len(data))
    print_ref = bot.prepare_file_choices([result])[0]
    schedule_ref = bot.confirmations.issue("schedule-path", result)
    _finish_upload(bot, chat, progress_mid, ui.file_card_text(result, details, replaced),
                   ui.kb_file_card(print_ref, schedule_ref))


def _finish_upload(bot, chat, progress_mid, text, keyboard=None):
    if progress_mid:
        bot.api.delete_message(chat, progress_mid)
    bot.refresh_main(force_new=True, text=text, keyboard=keyboard or ui.kb_back())


def show_plan(bot, chat, mid=None, is_photo=False, force_new=False):
    now, tz = bot.clock(), bot.schedule_tz()
    jobs = schedule.ordered(bot.scheduled_jobs())
    rows = [(job, schedule.format_when(job["at"], now, tz),
             schedule.format_left(job["at"], now)) for job in jobs]
    _show_readonly(bot, chat, mid, is_photo, force_new,
                   ui.plan_text(rows), ui.kb_plan(jobs))


def _schedule_callback(bot, chat, mid, query, data, is_photo):
    """Buttons under sched:, schedcancel: and schedrun:."""
    if not bot.schedule_available():
        bot.api.answer_callback(query["id"], "Отложенный старт недоступен в этих настройках.")
        return

    if data.startswith("schedcancel:"):
        job = bot.cancel_scheduled(data.split(":", 1)[1])
        bot.api.answer_callback(query["id"],
                                "Задание отменено." if job else "Такого задания уже нет.")
        show_plan(bot, chat, mid, is_photo)
        return

    if data.startswith("schedrun:"):
        # Bound to the job id, not to a five-minute token: this button sits
        # under a message that may be read hours later.
        job = schedule.find(bot.scheduled_jobs(), data.split(":", 1)[1])
        if not job:
            bot.api.answer_callback(query["id"], "Такого задания уже нет.")
            return
        token = bot.confirmations.issue("schedule-run", job["id"])
        bot.api.answer_callback(query["id"])
        bot.api.send_message(chat, ui.schedule_run_confirm_text(job),
                             keyboard=ui.kb_confirm("schedrun:%s" % token, "печатать"),
                             photo=bot.grab())
        return

    kind, _, ref = data[len("sched:"):].partition(":")
    if kind == "new":
        path = bot.confirmations.consume("schedule-path", ref)
        if not path:
            bot.api.answer_callback(query["id"], "Кнопка устарела. Откройте файл заново.")
            return
        options = schedule.quick_options(bot.clock(), bot.schedule_tz())
        refs = [bot.confirmations.issue("schedule-when", (path, option_kind, value))
                for _label, option_kind, value in options]
        custom = bot.confirmations.issue("schedule-custom", path)
        bot.api.answer_callback(query["id"])
        bot.api.edit_message(chat, mid, ui.schedule_pick_text(path),
                             keyboard=ui.kb_schedule_pick(
                                 [option[0] for option in options], refs, custom),
                             is_photo=is_photo)
        return

    if kind == "when":
        choice = bot.confirmations.consume("schedule-when", ref)
        if not choice:
            bot.api.answer_callback(query["id"], "Кнопка устарела. Откройте файл заново.")
            return
        path, option_kind, value = choice
        at = schedule.resolve_option(option_kind, value, bot.clock())
        text, keyboard = _schedule_confirmation(bot, path, at)
        bot.api.answer_callback(query["id"])
        bot.api.edit_message(chat, mid, text, keyboard=keyboard, is_photo=is_photo)
        return

    if kind == "custom":
        path = bot.confirmations.consume("schedule-custom", ref)
        if not path:
            bot.api.answer_callback(query["id"], "Кнопка устарела. Откройте файл заново.")
            return
        with bot.lock:
            bot.schedule_draft = {"path": path, "until": bot.clock() + DRAFT_TTL_SEC,
                                  "mid": mid, "is_photo": is_photo}
        bot.api.answer_callback(query["id"])
        bot.api.edit_message(chat, mid, ui.schedule_custom_text(path, schedule.HINT),
                             keyboard=ui.kb_cancel(), is_photo=is_photo)
        return

    bot.api.answer_callback(query["id"])


def _schedule_confirmation(bot, path, at):
    """The last screen before a job is created: exact time and what gets checked."""
    now = bot.clock()
    error = schedule.check_at(at, now)
    if error:
        return ui.schedule_custom_text(path, schedule.HINT, error), ui.kb_back()
    token = bot.confirmations.issue("schedule-confirm", (path, at))
    reminder = int(float(bot.cfg.get("schedule_reminder_min", 10) or 0))
    text = ui.schedule_confirm_text(
        path, schedule.format_when(at, now, bot.schedule_tz()),
        schedule.format_left(at, now), reminder)
    return text, ui.kb_confirm("sched:%s" % token, "стол пустой, запланировать")


def _maybe_schedule_text(bot, chat, text):
    """A typed start time for the file being scheduled. True if it was taken."""
    with bot.lock:
        draft = bot.schedule_draft
    if not draft:
        return False
    if text.startswith("/") or bot.clock() > draft["until"]:
        with bot.lock:
            bot.schedule_draft = None
        return False
    at, error = schedule.parse_when(text, bot.clock(), bot.schedule_tz())
    if error:
        # Stay in the same message; the owner is clearly mid-way through this.
        bot.api.edit_message(chat, draft["mid"],
                             ui.schedule_custom_text(draft["path"], schedule.HINT, error),
                             keyboard=ui.kb_cancel(), is_photo=draft.get("is_photo"))
        return True
    with bot.lock:
        bot.schedule_draft = None
    bot.api.delete_message(chat, draft["mid"])
    body, keyboard = _schedule_confirmation(bot, draft["path"], at)
    bot.refresh_main(force_new=True, text=body, keyboard=keyboard)
    return True
