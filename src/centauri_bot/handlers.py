# -*- coding: utf-8 -*-
"""What happens when the owner presses a button or sends a command.

Every handler takes the Bot as its first argument rather than reaching for a
global, so tests can drive them with a fake Telegram and a fake printer.

Access control is the first thing in both entry points: an update from any chat
other than the configured owner is answered with a refusal and goes no further.
"""
import logging
import time

from . import printer_state as ps
from . import sdcp
from . import storage
from . import ui


log = logging.getLogger(__name__)

CONFIRM_LABELS = {
    "pause": ("поставить на паузу", "⏸ Поставить печать на паузу?"),
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


def _is_owner(bot, chat):
    return str(chat) == bot.owner


def show_files(bot, chat, mid=None, is_photo=False, force_new=False):
    """Show the file list without leaving a second status message behind.

    A callback edits the message whose button was pressed.  ``/files`` has no
    such message id, so it recreates the tracked main message as the file list;
    the Back button can then turn that very message back into the status.
    """
    ok, info = bot.run_command(sdcp.CMD_FILE_LIST, {"Url": "/local"}, wait=6)
    with bot.lock:
        files = list(bot.files or [])
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
    body = ui.files_text(files)
    rows = ui.kb_files(files, allow_control=bot.cfg.get("allow_control", True))
    if force_new:
        bot.refresh_main(force_new=True, text=body, keyboard=rows)
    elif mid:
        bot.edit_main_from_callback(
            mid, body, keyboard=rows, is_photo=is_photo)
    else:
        bot.api.send_message(chat, body, keyboard=rows)


def handle_callback(bot, query):
    chat = str(query["message"]["chat"]["id"])
    mid = query["message"]["message_id"]
    data = query.get("data", "")
    is_photo = "photo" in query.get("message", {})

    if not _is_owner(bot, chat):
        bot.api.answer_callback(query["id"], "Не для тебя.")
        log.info("callback from a foreign chat was refused")
        return

    if data == "noop":
        bot.api.answer_callback(query["id"])
        return

    if data == "help":
        bot.api.answer_callback(query["id"])
        text, keyboard = ui.help_screen(bot.cfg.get("allow_control", True))
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
    with bot.lock:
        lit = (((bot.status or {}).get("LightStatus") or {}).get("SecondLight") == 1)
    ok, info = bot.run_command(sdcp.CMD_SET,
                               {"LightStatus": {"SecondLight": 0 if lit else 1}})
    note = ("💡 Свет %s.\n\n" % ("выключен" if lit else "включён")) if ok \
        else "⚠️ Свет не переключился (%s).\n\n" % info
    bot.api.answer_callback(query["id"], note)
    time.sleep(SETTLE_SEC)
    bot.api.edit_message(chat, mid, bot.render(note), keyboard=bot.keyboard(),
                         photo=bot.grab(max_age=5))


def _apply_fans(bot, chat, mid, query):
    with bot.lock:
        draft = dict(bot.fan_draft or {})
        bot.fan_draft = None
    target = _fan_current(bot)
    target.update(draft)
    if not bot.cfg.get("allow_control"):
        note = CONTROL_OFF
    else:
        ok, info = bot.run_command(sdcp.CMD_SET, {"TargetFanSpeed": target})
        note = ("🌀 Вентиляторы: обдув %d%% · корпус %d%% · доп %d%%.\n\n"
                % (target["ModelFan"], target["BoxFan"], target["AuxiliaryFan"])) if ok \
            else "⚠️ Вентиляторы не приняты (%s).\n\n" % info
    bot.api.answer_callback(query["id"], note)
    time.sleep(SETTLE_SEC)
    bot.api.edit_message(chat, mid, bot.render(note), keyboard=bot.keyboard(),
                         photo=bot.grab(max_age=5))


def _apply_setting(bot, chat, mid, query, data):
    _, what, value = data.split(":", 2)
    if not bot.cfg.get("allow_control"):
        note = CONTROL_OFF
    elif what == "speed":
        ok, info = bot.run_command(sdcp.CMD_SET, {"PrintSpeedPct": int(value)})
        note = ("⚡ Скорость печати %s%%.\n\n" % value) if ok \
            else "⚠️ Скорость не принялась (%s).\n\n" % info
    else:
        label, nozzle, bed = ui.HEAT_PRESETS.get(value, ("?", 0, 0))
        ok, info = bot.run_command(sdcp.CMD_SET,
                                   {"TempTargetNozzle": nozzle, "TempTargetHotbed": bed})
        note = ("🌡 Нагрев: %s.\n\n" % label) if ok \
            else "⚠️ Нагрев не принялся (%s).\n\n" % info
    bot.api.answer_callback(query["id"], note)
    time.sleep(SETTLE_SEC)
    bot.api.edit_message(chat, mid, bot.render(note), keyboard=bot.keyboard(),
                         photo=bot.grab(max_age=5))


def _ask_confirmation(bot, chat, mid, query, what, is_photo):
    """Dangerous actions always get a second screen. No support button here."""
    if what.startswith("print:"):
        index = int(what.split(":")[1])
        with bot.lock:
            files = list(bot.files or [])
        name = files[index].rsplit("/", 1)[-1] if index < len(files) else "?"
        bot.api.answer_callback(query["id"])
        bot.api.send_message(
            chat,
            "🖨 <b>Запустить печать?</b>\n<i>%s</i>\n\n"
            "Убедись по снимку, что стол пуст." % name,
            keyboard=ui.kb_confirm("print:%d" % index, "печатать"),
            photo=bot.grab())
        return
    label, question = CONFIRM_LABELS.get(what, ("выполнить", "Выполнить?"))
    bot.api.answer_callback(query["id"])
    bot.api.edit_message(chat, mid, question, keyboard=ui.kb_confirm(what, label),
                         is_photo=is_photo)


def _do_action(bot, chat, mid, query, what):
    note = ""
    if not bot.cfg.get("allow_control"):
        note = CONTROL_OFF
    elif what == "pause":
        ok, info = bot.run_command(sdcp.CMD_PAUSE)
        note = "⏸ Команда паузы принята.\n\n" if ok else "⚠️ Пауза не прошла (%s).\n\n" % info
    elif what == "resume":
        ok, info = bot.run_command(sdcp.CMD_RESUME)
        note = "▶️ Команда продолжения принята.\n\n" if ok \
            else "⚠️ Не продолжилось (%s).\n\n" % info
    elif what == "stop":
        ok, info = bot.run_command(sdcp.CMD_STOP)
        note = "⏹ Команда остановки принята.\n\n" if ok \
            else "⚠️ Не остановилось (%s).\n\n" % info
    elif what.startswith("print:"):
        index = int(what.split(":")[1])
        with bot.lock:
            files = list(bot.files or [])
        if index < len(files):
            ok, info = bot.run_command(sdcp.CMD_START,
                                       {"Filename": files[index], "StartLayer": 0})
            note = "🖨 Печать запущена.\n\n" if ok else "⚠️ Не запустилось (%s).\n\n" % info
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

    if not _is_owner(bot, chat):
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
    elif text.startswith("/help") or text.startswith("/start"):
        body, keyboard = ui.help_screen(bot.cfg.get("allow_control", True))
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
