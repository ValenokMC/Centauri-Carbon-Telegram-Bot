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
from . import printer_state as ps
from . import storage
from . import ui


log = logging.getLogger(__name__)

CONFIRM_LABELS = {
    "pause": ("поставить на паузу", "⏸ Поставить печать на паузу?"),
    "resume": ("продолжить печать", "▶️ Продолжить печать?"),
    "stop": ("остановить",
             "⏹ <b>Остановить печать?</b>\nОтменить это будет нельзя."),
}

CONTROL_OFF = "Управление выключено в настройках.\n\n"

MACRO_DESCRIPTIONS = {
    "CHECK_CALIBRATION": "покажет на экране принтера состояние калибровок",
    "CLEAN_NOZZLE": "нагреет сопло, выполнит homing и переместит его в зону очистки",
    "LOAD_FILAMENT": "нагреет сопло и подаст филамент",
    "UNLOAD_FILAMENT": "выполнит homing, переместит голову и выгрузит филамент",
    "MOVE_TO_TRAY": "выполнит homing и переместит сопло в задний лоток",
}

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
    rows = ui.kb_files(files, allow_control=bot.cfg.get("allow_control", True),
                       can_start=can_start, refs=refs, can_delete=can_delete,
                       delete_refs=delete_refs)
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
    enabled = [name for name in names if bot.macro_allowed(name)]
    refs = bot.prepare_macro_choices(enabled)
    _show_readonly(bot, chat, mid, is_photo, force_new,
                   ui.macros_text(names, enabled), ui.kb_macros(enabled, refs))


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

    if data == "help":
        bot.api.answer_callback(query["id"])
        text, keyboard = ui.help_screen(
            bot.cfg.get("allow_control", True), allowed=bot.allowed_actions())
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
                  "установит обдув %d%%, корпус %d%% и дополнительный %d%%" % (
                      target["ModelFan"], target["BoxFan"], target["AuxiliaryFan"]))


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
        bot.api.send_message(
            chat,
            "🖨 <b>Запустить печать?</b>\n<i>%s</i>\n\n"
            "Убедись по снимку, что стол пуст." % escape(name),
            keyboard=ui.kb_confirm("print:%s" % confirmation, "печатать"),
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
    if what.startswith("macro:"):
        choice = what.split(":", 1)[1]
        name = bot.resolve_macro_choice(choice)
        if not name or not bot.macro_allowed(name):
            bot.api.answer_callback(query["id"], "Макрос не разрешён или список устарел.")
            return
        token = bot.issue_macro_confirmation(name)
        bot.api.answer_callback(query["id"])
        bot.api.edit_message(chat, mid,
                             "🧩 <b>Запустить макрос?</b>\n<code>%s</code>\n\n"
                             "Действие: %s.\n\n"
                             "Макрос может двигать механизмы или менять состояние принтера."
                             % (escape(name), escape(MACRO_DESCRIPTIONS.get(
                                 name, "действие не описано в боте"))),
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
    action = backend.START if what.startswith("print:") else (backend.DELETE if what.startswith("delete:") else (backend.RUN_MACRO if what.startswith("macro:") else None))
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
    elif what.startswith("macro:"):
        token = what.split(":", 1)[1]
        name = bot.consume_macro_confirmation(token)
        if not name or not bot.macro_allowed(name):
            note = "⚠️ Макрос не разрешён или подтверждение устарело.\n\n"
        else:
            ok, info = bot.perform(backend.RUN_MACRO, name)
            note = "🧩 Макрос <code>%s</code> отправлен.\n\n" % escape(name) if ok else "⚠️ Макрос не запустился (%s).\n\n" % info
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
    if message.get("from", {}).get("is_bot") or not (message.get("text") or "").strip():
        return
    chat = str(message["chat"]["id"])
    text = message["text"].strip().lower()

    sender = message.get("from", {}).get("id")
    if not _is_owner(bot, chat, sender):
        bot.api.send_message(chat, "Этот бот личный.")
        log.info("message from a foreign chat was refused")
        return

    # Whatever the bot answers, the status with its buttons goes out last, so
    # it is always at the bottom under the thumb and needs no scrolling.
    if text.startswith("/snap"):
        bot.api.send_message(chat, "📷 " + time.strftime("%H:%M:%S"), photo=bot.grab())
    elif text.startswith("/files"):
        show_files(bot, chat, force_new=True)
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
            bot.cfg.get("allow_control", True), allowed=bot.allowed_actions())
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
