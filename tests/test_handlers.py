# -*- coding: utf-8 -*-
"""Access control, dangerous-command confirmation, and keyboard layout."""
import pytest

from centauri_bot import backend, handlers, sdcp, storage, ui

from conftest import status


OWNER = "555000111"
STRANGER = "999888777"


def message(text, chat=OWNER, is_bot=False):
    return {"message_id": 5, "chat": {"id": chat},
            "from": {"id": chat, "is_bot": is_bot}, "text": text}


def callback(data, chat=OWNER, with_photo=False):
    msg = {"message_id": 42, "chat": {"id": chat}}
    if with_photo:
        msg["photo"] = [{"file_id": "x"}]
    return {"id": "cb-1", "data": data, "message": msg,
            "from": {"id": chat, "is_bot": False}}


@pytest.fixture
def online_bot(bot):
    """A bot that believes the printer is connected and answering."""
    bot.status = status(13, "Demo_Print.gcode", progress=50)
    bot.online = True
    bot.ws = object()
    bot.run_command = lambda *a, **k: (True, "Ack=0")
    return bot


# ------------------------------------------------------------ authorisation

def test_stranger_sending_a_message_is_refused(bot):
    handlers.handle_message(bot, message("/status", chat=STRANGER))
    assert len(bot.api.sent) == 1
    chat, text, _, _ = bot.api.sent[0]
    assert chat == STRANGER
    assert "личный" in text


def test_stranger_gets_no_printer_information(bot):
    handlers.handle_message(bot, message("/status", chat=STRANGER))
    body = bot.api.sent[0][1]
    assert bot.cfg["printer_ip"] not in body
    assert "Demo_Print" not in body


def test_stranger_pressing_a_button_is_refused_and_nothing_is_edited(bot):
    handlers.handle_callback(bot, callback("do:stop", chat=STRANGER))
    assert bot.api.answers == [("cb-1", "Не для тебя.")]
    assert bot.api.edited == []
    assert bot.api.sent == []


def test_callback_sender_cannot_hide_behind_owner_chat(bot):
    forged = callback("do:stop")
    forged["from"]["id"] = STRANGER
    handlers.handle_callback(bot, forged)
    assert bot.api.answers == [("cb-1", "Не для тебя.")]


def test_stranger_cannot_stop_a_print(online_bot):
    commands = []
    online_bot.run_command = lambda cmd, *a, **k: (commands.append(cmd), (True, ""))[1]
    handlers.handle_callback(online_bot, callback("do:stop", chat=STRANGER))
    assert commands == []


def test_owner_is_served(bot):
    bot.refresh_main = lambda **k: 99
    handlers.handle_message(bot, message("/help"))
    assert bot.api.sent
    assert "Что умею" in bot.api.sent[0][1]


def test_monitoring_only_help_hides_control_descriptions(bot):
    bot.cfg["allow_control"] = False
    handlers.handle_message(bot, message("/help"))
    body = bot.api.sent[0][1].lower()
    for unavailable in ("пауза", "продолжить", "стоп", "свет",
                        "скорость", "нагрев"):
        assert unavailable not in body


def test_messages_from_other_bots_are_ignored(bot):
    """Service messages loop: pin -> service message -> reply -> pin again."""
    handlers.handle_message(bot, message("anything", is_bot=True))
    assert bot.api.sent == []


def test_messages_without_text_are_ignored(bot):
    handlers.handle_message(bot, {"message_id": 1, "chat": {"id": OWNER},
                                  "from": {"id": OWNER, "is_bot": False}})
    assert bot.api.sent == []


def test_files_command_replaces_the_tracked_message_and_back_edits_it(bot):
    """``/files`` and Back must still leave exactly one bot message."""
    storage.set_message_id(77)
    bot.files = ["/local/Calibration Cube.gcode"]
    bot.run_command = lambda *a, **k: (True, "Ack=0")

    handlers.handle_message(bot, message("/files"))

    assert bot.api.deleted == [(OWNER, 77)]
    assert len(bot.api.sent) == 1
    assert "Файлы на принтере" in bot.api.sent[0][1]
    file_message_id = storage.message_id()

    back = callback("refresh")
    back["message"]["message_id"] = file_message_id
    handlers.handle_callback(bot, back)

    assert len(bot.api.sent) == 1
    assert bot.api.deleted == [(OWNER, 77)]
    assert bot.api.edited[-1][1] == file_message_id
    assert "Demo Centauri" in bot.api.edited[-1][2]


def test_files_command_keeps_photo_message_editable_on_back(bot):
    """With snapshots enabled the file list remains the same photo message."""
    bot.cfg["send_photo"] = True
    bot.grab = lambda max_age=0: b"jpeg"
    bot.files = ["/local/Calibration Cube.gcode"]
    bot.run_command = lambda *a, **k: (True, "Ack=0")

    handlers.handle_message(bot, message("/files"))

    assert len(bot.api.sent) == 1
    assert bot.api.sent[0][3] is True
    file_message_id = storage.message_id()
    back = callback("refresh", with_photo=True)
    back["message"]["message_id"] = file_message_id
    handlers.handle_callback(bot, back)
    assert len(bot.api.sent) == 1
    assert bot.api.edited[-1][1] == file_message_id


def test_files_button_on_stale_message_uses_tracked_main_and_deletes_stale(bot):
    """A still-clickable old keyboard must not create a second UI message."""
    storage.set_message_id(77)
    bot.files = ["/local/Calibration Cube.gcode"]
    bot.run_command = lambda *a, **k: (True, "Ack=0")

    handlers.handle_callback(bot, callback("files"))

    assert storage.message_id() == 77
    assert bot.api.sent == []
    assert bot.api.edited[-1][1] == 77
    assert "Файлы на принтере" in bot.api.edited[-1][2]
    assert bot.api.deleted == [(OWNER, 42)]

    back = callback("refresh")
    back["message"]["message_id"] = 77
    handlers.handle_callback(bot, back)

    assert bot.api.edited[-1][1] == 77
    assert "Demo Centauri" in bot.api.edited[-1][2]
    assert bot.api.deleted == [(OWNER, 42)]


def test_back_on_stale_file_message_removes_it_and_updates_tracked_main(bot):
    """Regression for a file list left above a newer status message."""
    storage.set_message_id(77)

    handlers.handle_callback(bot, callback("refresh"))

    assert storage.message_id() == 77
    assert bot.api.sent == []
    assert bot.api.edited[-1][1] == 77
    assert "Demo Centauri" in bot.api.edited[-1][2]
    assert bot.api.deleted == [(OWNER, 42)]


def test_back_recreates_one_message_when_both_edits_fail(bot):
    storage.set_message_id(77)
    bot.api.fail_edits = True

    handlers.handle_callback(bot, callback("refresh"))

    assert set(bot.api.deleted) == {(OWNER, 42), (OWNER, 77)}
    assert len(bot.api.sent) == 1
    assert storage.message_id() == 1001
    assert "Demo Centauri" in bot.api.sent[0][1]


def test_cosmos_diagnostics_button_is_read_only_and_renders_health(bot):
    bot.cfg["backend"] = "moonraker"
    bot.backend_name = backend.MOONRAKER
    bot.moonraker = type("Moonraker", (), {"diagnostics": lambda self: {
        "moonraker_version": "v0.9", "klipper_version": "v0.13",
        "klippy_state": "ready", "klippy_message": "", "warnings": 0,
        "failed_components": 0, "object_count": 111,
    }})()

    handlers.handle_callback(bot, callback("diag"))

    assert "Диагностика COSMOS" in bot.api.edited[-1][2]
    assert "ready" in bot.api.edited[-1][2]


def test_mesh_and_macro_execution_have_separate_safe_paths(online_bot):
    online_bot.cfg.update({"backend": "moonraker", "moonraker_macro_whitelist": ["LOAD_FILAMENT"]})
    online_bot.backend_name = backend.MOONRAKER
    called = []
    online_bot.moonraker = type("Moonraker", (), {
        "bed_mesh": lambda self: {"profile": "default", "points": [[-0.1, 0.0], [0.1, 0.2]]},
        "list_macros": lambda self: ["LOAD_FILAMENT", "UNLOAD_FILAMENT"],
        "run_macro": lambda self, name: called.append(name),
    })()

    handlers.handle_callback(online_bot, callback("mesh"))
    assert "Карта высот" in online_bot.api.edited[-1][2]

    handlers.handle_callback(online_bot, callback("macros"))
    macro_button = [button for row in online_bot.api.edited[-1][3] for button in row
                    if button["callback_data"].startswith("ask:macro:")][0]
    handlers.handle_callback(online_bot, callback(macro_button["callback_data"]))
    confirm = online_bot.api.edited[-1][3][0][0]["callback_data"]
    assert called == []
    handlers.handle_callback(online_bot, callback(confirm))
    assert called == ["LOAD_FILAMENT"]


def test_cosmos_hardware_control_requires_confirmation(online_bot):
    online_bot.cfg.update({"backend": "moonraker", "moonraker_allow_hardware_controls": True})
    online_bot.backend_name = backend.MOONRAKER
    calls = []
    online_bot.moonraker = type("Moonraker", (), {
        "set_light": lambda self, value: calls.append(value),
    })()
    online_bot.status = status(0, LightStatus={"SecondLight": 0})

    handlers.handle_callback(online_bot, callback("light"))
    confirm = online_bot.api.edited[-1][3][0][0]["callback_data"]
    assert calls == []
    handlers.handle_callback(online_bot, callback(confirm))
    assert calls == [True]
    handlers.handle_callback(online_bot, callback(confirm))
    assert calls == [True]


def test_exclude_object_lists_live_models_confirms_and_rechecks_job(online_bot):
    online_bot.cfg.update({"backend": "moonraker",
                           "moonraker_allow_job_control": True})
    online_bot.backend_name = backend.MOONRAKER
    names = ["CUBE.DRC_ID_0_COPY_0", "PLUG.DRC_ID_1_COPY_0"]
    live = {"Objects": names, "ExcludedObjects": [],
            "CurrentObject": names[0], "PrintState": "printing",
            "Filename": "Demo_Print.gcode"}
    excluded = []
    online_bot.moonraker = type("Moonraker", (), {
        "exclude_object_state": lambda self: dict(live),
        "exclude_object": lambda self, name: excluded.append(name),
    })()
    online_bot.status = status(
        13, "Demo_Print.gcode", progress=50,
        ExcludeObject={"Objects": names, "ExcludedObjects": [],
                       "CurrentObject": names[0]})

    handlers.handle_callback(online_bot, callback("objects"))
    text, keyboard = online_bot.api.edited[-1][2:]
    assert "Объекты текущей печати" in text
    ask = [button for row in keyboard for button in row
           if button["callback_data"].startswith("ask:exclude:")][1]

    handlers.handle_callback(online_bot, callback(ask["callback_data"]))
    confirm = online_bot.api.edited[-1][3][0][0]["callback_data"]
    assert excluded == []
    handlers.handle_callback(online_bot, callback(confirm))
    assert excluded == [names[1]]
    handlers.handle_callback(online_bot, callback(confirm))
    assert excluded == [names[1]]


def test_exclude_object_confirmation_refuses_a_changed_print(online_bot):
    online_bot.cfg.update({"backend": "moonraker",
                           "moonraker_allow_job_control": True})
    online_bot.backend_name = backend.MOONRAKER
    names = ["FIRST", "SECOND"]
    live = {"Objects": names, "ExcludedObjects": [], "CurrentObject": "FIRST",
            "PrintState": "printing", "Filename": "Demo_Print.gcode"}
    excluded = []
    online_bot.moonraker = type("Moonraker", (), {
        "exclude_object_state": lambda self: dict(live),
        "exclude_object": lambda self, name: excluded.append(name),
    })()
    online_bot.status = status(
        13, "Demo_Print.gcode", ExcludeObject={"Objects": names,
        "ExcludedObjects": [], "CurrentObject": "FIRST"})

    handlers.handle_callback(online_bot, callback("objects"))
    ask = [button for row in online_bot.api.edited[-1][3] for button in row
           if button["callback_data"].startswith("ask:exclude:")][0]
    handlers.handle_callback(online_bot, callback(ask["callback_data"]))
    confirm = online_bot.api.edited[-1][3][0][0]["callback_data"]
    live["Filename"] = "another.gcode"
    handlers.handle_callback(online_bot, callback(confirm))
    assert excluded == []
    assert "задание печати сменилось" in online_bot.api.answers[-1][1]


def test_connection_loss_and_recovery_replace_the_one_main_panel(bot):
    """Network notices must never sit above a separate stale status panel."""
    storage.set_message_id(77)
    bot.online = False

    bot.show_connection_lost(75)

    assert bot.api.deleted == [(OWNER, 77)]
    assert len(bot.api.sent) == 1
    assert bot.api.sent[-1][3] is False
    assert "Связь с принтером потеряна" in bot.api.sent[-1][1]
    offline_mid = storage.message_id()

    bot.online = True
    bot.grab = lambda max_age=0: b"jpeg"
    bot.show_connection_restored()

    assert bot.api.deleted == [(OWNER, 77), (OWNER, offline_mid)]
    assert len(bot.api.sent) == 2
    assert bot.api.sent[-1][3] is True
    assert "Связь с принтером восстановлена" in bot.api.sent[-1][1]
    assert storage.message_id() != offline_mid


def test_telegram_failure_cannot_stop_printer_reconnection_loop(bot):
    def fail_refresh(**kwargs):
        raise OSError("telegram unavailable")

    bot.refresh_main = fail_refresh

    assert bot.show_connection_lost(75) is None
    assert bot.show_connection_restored() is None


# ------------------------------------------------------ dangerous commands

def test_stop_asks_for_confirmation_before_acting(online_bot):
    sent_commands = []
    online_bot.run_command = lambda cmd, *a, **k: (
        sent_commands.append(cmd), (True, "Ack=0"))[1]

    handlers.handle_callback(online_bot, callback("ask:stop"))
    assert sent_commands == []                       # nothing happened yet

    _, _, text, keyboard = online_bot.api.edited[-1]
    assert "Остановить печать?" in text
    assert "Отменить это будет нельзя" in text
    labels = [b["text"] for row in keyboard for b in row]
    assert any("Да" in l for l in labels)
    assert any("Отмена" in l for l in labels)


def test_confirmed_stop_sends_the_stop_command(online_bot):
    sent_commands = []
    online_bot.run_command = lambda cmd, *a, **k: (
        sent_commands.append(cmd), (True, "Ack=0"))[1]
    handlers.handle_callback(online_bot, callback("ask:stop"))
    confirm = online_bot.api.edited[-1][3][0][0]["callback_data"]
    handlers.handle_callback(online_bot, callback(confirm))
    assert sdcp.CMD_STOP in sent_commands


def test_print_confirmation_stays_bound_to_original_file(online_bot):
    online_bot.status = status(0)
    online_bot.files = ["/local/original.gcode"]
    sent = []
    online_bot.run_command = lambda cmd, data=None, **kwargs: (
        sent.append((cmd, data)), (True, "Ack=0"))[1]

    handlers.show_files(online_bot, OWNER, force_new=True)
    file_keyboard = online_bot.api.sent[-1][2]
    ask_data = file_keyboard[0][0]["callback_data"]
    handlers.handle_callback(online_bot, callback(ask_data))

    confirm_keyboard = online_bot.api.sent[-1][2]
    do_data = confirm_keyboard[0][0]["callback_data"]
    online_bot.files = ["/local/replaced.gcode"]
    handlers.handle_callback(online_bot, callback(do_data))

    starts = [(cmd, data) for cmd, data in sent if cmd == sdcp.CMD_START]
    assert starts == [(sdcp.CMD_START,
                       {"Filename": "/local/original.gcode", "StartLayer": 0})]

    # Replaying the same old Telegram callback is harmless.
    handlers.handle_callback(online_bot, callback(do_data))
    starts = [(cmd, data) for cmd, data in sent if cmd == sdcp.CMD_START]
    assert len(starts) == 1


def test_delete_requires_a_fresh_one_use_confirmation_and_never_targets_current_print(online_bot):
    online_bot.cfg.update({"backend": "moonraker", "moonraker_allow_file_delete": True})
    online_bot.backend_name = backend.MOONRAKER
    online_bot.moonraker = type("Moonraker", (), {"delete": lambda self, path: deleted.append(path)})()
    online_bot.status = status(0)
    online_bot.files = ["old.gcode"]
    online_bot.refresh_files = lambda: (True, "Moonraker")
    deleted = []

    handlers.show_files(online_bot, OWNER, force_new=True)
    delete_button = [button for row in online_bot.api.sent[-1][2] for button in row
                     if button["callback_data"].startswith("ask:delete:")][0]
    handlers.handle_callback(online_bot, callback(delete_button["callback_data"]))
    confirm = online_bot.api.sent[-1][2][0][0]["callback_data"]
    handlers.handle_callback(online_bot, callback(confirm))
    assert deleted == ["old.gcode"]
    handlers.handle_callback(online_bot, callback(confirm))
    assert deleted == ["old.gcode"]

    online_bot.files = ["current.gcode"]
    online_bot.status = status(13, "current.gcode", progress=5)
    handlers.show_files(online_bot, OWNER, force_new=True)
    delete_button = [button for row in online_bot.api.sent[-1][2] for button in row
                     if button["callback_data"].startswith("ask:delete:")][0]
    handlers.handle_callback(online_bot, callback(delete_button["callback_data"]))
    assert "текущей печати" in online_bot.api.answers[-1][1]
    assert deleted == ["old.gcode"]


def test_pause_asks_for_confirmation(online_bot):
    handlers.handle_callback(online_bot, callback("ask:pause"))
    text = online_bot.api.edited[-1][2]
    assert "паузу" in text


def test_resume_asks_for_one_use_confirmation(online_bot):
    online_bot.status = status(6, "Demo_Print.gcode", progress=50)
    sent_commands = []
    online_bot.run_command = lambda cmd, *a, **k: (
        sent_commands.append(cmd), (True, "Ack=0"))[1]
    handlers.handle_callback(online_bot, callback("ask:resume"))
    assert sent_commands == []
    confirm = online_bot.api.edited[-1][3][0][0]["callback_data"]
    handlers.handle_callback(online_bot, callback(confirm))
    assert sdcp.CMD_RESUME in sent_commands
    sent_commands.clear()
    handlers.handle_callback(online_bot, callback(confirm))
    assert sent_commands == []


def test_control_commands_are_refused_in_monitoring_only_mode(online_bot):
    online_bot.cfg["allow_control"] = False
    sent_commands = []
    online_bot.run_command = lambda cmd, *a, **k: (
        sent_commands.append(cmd), (True, "Ack=0"))[1]
    handlers.handle_callback(online_bot, callback("do:stop"))
    assert sent_commands == []
    assert "выключено" in online_bot.api.answers[-1][1]


def test_confirmed_job_action_fails_closed_after_disconnect(online_bot):
    handlers.handle_callback(online_bot, callback("ask:pause"))
    confirm = online_bot.api.edited[-1][3][0][0]["callback_data"]
    online_bot.online = False
    handlers.handle_callback(online_bot, callback(confirm))
    assert "не в сети" in online_bot.api.answers[-1][1]


def test_job_action_refuses_wrong_live_state(online_bot):
    handlers.handle_callback(online_bot, callback("ask:pause"))
    confirm = online_bot.api.edited[-1][3][0][0]["callback_data"]
    online_bot.status = status(0)
    handlers.handle_callback(online_bot, callback(confirm))
    assert "не выполняется" in online_bot.api.answers[-1][1]


# ------------------------------------------------------------- keyboards

def test_monitoring_only_mode_shows_no_control_buttons():
    keyboard = ui.kb_main(status(13, "demo.gcode", progress=50), allow_control=False)
    labels = [b["text"] for row in keyboard for b in row]
    assert not any("Пауза" in l or "Стоп" in l or "Свет" in l for l in labels)
    assert any("Обновить" in l for l in labels)


def test_printing_shows_pause_and_stop():
    keyboard = ui.kb_main(status(13, "demo.gcode", progress=50))
    labels = [b["text"] for row in keyboard for b in row]
    assert any("Пауза" in l for l in labels)
    assert any("Стоп" in l for l in labels)
    assert not any("Продолжить" in l for l in labels)


def test_exclude_button_only_appears_for_multiple_active_objects():
    allowed = {backend.PAUSE, backend.CANCEL, backend.EXCLUDE_OBJECT}
    multiple = status(13, "demo.gcode", ExcludeObject={
        "Objects": ["ONE", "TWO"], "ExcludedObjects": [],
        "CurrentObject": "ONE"})
    labels = [b["text"] for row in ui.kb_main(multiple, allowed=allowed)
              for b in row]
    assert any("Убрать объект" in label for label in labels)

    multiple["ExcludeObject"]["ExcludedObjects"] = ["TWO"]
    labels = [b["text"] for row in ui.kb_main(multiple, allowed=allowed)
              for b in row]
    assert not any("Убрать объект" in label for label in labels)


def test_paused_shows_resume_not_pause():
    keyboard = ui.kb_main(status(6, "demo.gcode", progress=50))
    labels = [b["text"] for row in keyboard for b in row]
    assert any("Продолжить" in l for l in labels)
    assert not any("Пауза" in l for l in labels)


def test_idle_shows_neither_pause_nor_stop():
    keyboard = ui.kb_main(status(0))
    labels = [b["text"] for row in keyboard for b in row]
    assert not any("Пауза" in l or "Стоп" in l or "Продолжить" in l for l in labels)


def test_maintenance_button_appears_only_when_due():
    without = ui.kb_main(status(0), maintenance=(False, False))
    with_it = ui.kb_main(status(0), maintenance=(True, True))
    assert not any("🧰" in b["text"] for row in without for b in row)
    assert any("🧰" in b["text"] for row in with_it for b in row)


def test_every_callback_button_has_data_and_every_link_has_a_url():
    """A button with neither is a button that does nothing when pressed."""
    keyboards = [ui.kb_main(status(13, "demo.gcode", progress=50)),
                 ui.kb_speed(100), ui.kb_temp(),
                 ui.kb_fans({"ModelFan": 50, "BoxFan": 0, "AuxiliaryFan": 0}),
                 ui.kb_confirm("stop", "остановить"),
                 ui.kb_files(["/local/a.gcode"]),
                 ui.help_screen()[1]]
    for keyboard in keyboards:
        for row in keyboard:
            for button in row:
                assert "text" in button
                assert ("callback_data" in button) or ("url" in button)


def test_fan_draft_marks_changes_and_counts_them():
    current = {"ModelFan": 100, "BoxFan": 0, "AuxiliaryFan": 0}
    keyboard = ui.kb_fans(current, {"ModelFan": 50})
    labels = [b["text"] for row in keyboard for b in row]
    assert any("✎" in l for l in labels)
    assert any("Отправить (1)" in l for l in labels)


# --------------------------------------------------------------- rendering

def test_render_shows_progress_and_file(bot):
    text = ui.render(status(13, "Demo_Print.gcode", progress=42), True, "Demo Centauri")
    assert "Demo_Print.gcode" in text
    assert "42%" in text
    assert "Demo Centauri" in text


def test_render_says_connection_lost_when_offline():
    text = ui.render(status(13, "demo.gcode", progress=42), False, "Demo")
    assert "связь потеряна" in text.lower() or "нет связи" in text.lower()


def test_render_without_a_status_is_still_a_message():
    text = ui.render(None, False, "Demo Centauri")
    assert "Demo Centauri" in text
    assert "Нет данных" in text


def test_render_detailed_adds_fans_and_chamber():
    brief = ui.render(status(13, "demo.gcode", progress=10), True, "Demo")
    detailed = ui.render(status(13, "demo.gcode", progress=10), True, "Demo",
                         detailed=True)
    assert "обдув" not in brief
    assert "обдув" in detailed
    assert "камера" in detailed


def test_cancelled_job_with_stale_moonraker_metadata_has_no_print_controls():
    stopped = status(8, "cancelled.gcode", progress=0,
                     CurrentLayer=40, TotalLayer=150)
    keyboard = ui.kb_main(stopped, allowed={backend.PAUSE, backend.RESUME,
                                             backend.CANCEL})
    callbacks = [button.get("callback_data") for row in keyboard for button in row]
    assert "ask:pause" not in callbacks
    assert "ask:resume" not in callbacks
    assert "ask:stop" not in callbacks
    shown = ui.render(stopped, True, "Demo")
    assert "cancelled.gcode" not in shown
    assert "слой 40" not in shown


def test_unknown_status_code_is_described_not_hidden():
    text = ui.render(status(77, "demo.gcode", progress=50), True, "Demo")
    assert "77" in text


def test_progress_bar_turns_yellow_when_stalled():
    assert "🟩" in ui.bar(50, code=13)
    assert "🟨" in ui.bar(50, code=6)
