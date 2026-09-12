# -*- coding: utf-8 -*-
"""A G-code sent into the chat, and a print started later on its own.

Driven through the same handlers and Bot methods the running bot uses, against
a fake Telegram and a fake Moonraker. No socket, no sleep.
"""
import datetime

import pytest

from centauri_bot import backend, config as config_mod, handlers, schedule, storage

from conftest import status


OWNER = "555000111"
STRANGER = "999888777"
TZ = schedule.tzinfo_from("+03:00")


def at(year, month, day, hour, minute):
    return int(datetime.datetime(year, month, day, hour, minute, tzinfo=TZ).timestamp())


NOW = at(2026, 9, 12, 22, 0)        # a Saturday evening


class FakeMoonraker(object):
    def __init__(self):
        self.files = ["old.gcode"]
        self.uploaded = []
        self.started = []
        self.history_jobs = []
        self.filament = True
        self.metadata = {"estimated_time": 4920, "filament_name": "eSUN PLA+",
                         "filament_weight_total": 48.2}

    def list_file_records(self):
        return [{"path": path, "size": 1000, "modified": 0, "permissions": "rw"}
                for path in self.files]

    def list_files(self):
        return list(self.files)

    def upload(self, name, data):
        self.uploaded.append((name, data))
        if name not in self.files:
            self.files.insert(0, name)
        return name

    def file_metadata(self, path):
        return dict(self.metadata)

    def filament_detected(self):
        return self.filament

    def history(self, limit=8):
        return list(self.history_jobs)

    def start(self, path):
        self.started.append(path)


@pytest.fixture
def cosmos(bot):
    bot.cfg.update({"backend": "moonraker", "moonraker_allow_remote_start": True,
                    "schedule_utc_offset": "+03:00"})
    bot.backend_name = backend.MOONRAKER
    bot.moonraker = FakeMoonraker()
    bot.status = status(9)
    bot.online = True
    bot.clock_control.now = NOW
    return bot


def document(name="part.gcode", file_id="doc-1", size=1234, chat=OWNER):
    return {"message_id": 7, "chat": {"id": chat}, "from": {"id": chat, "is_bot": False},
            "document": {"file_name": name, "file_id": file_id, "file_size": size}}


def text_message(text, chat=OWNER):
    return {"message_id": 8, "chat": {"id": chat}, "from": {"id": chat, "is_bot": False},
            "text": text}


def callback(data, chat=OWNER, message_id=42):
    return {"id": "cb-1", "data": data, "from": {"id": chat, "is_bot": False},
            "message": {"message_id": message_id, "chat": {"id": chat}}}


def buttons(keyboard):
    return [button for row in keyboard or [] for button in row]


def data_of(keyboard, prefix):
    return [button["callback_data"] for button in buttons(keyboard)
            if button.get("callback_data", "").startswith(prefix)]


def send_file(bot, name="part.gcode", data=b"G1 X10"):
    bot.api.documents["doc-1"] = data
    handlers.handle_message(bot, document(name))


def open_picker(bot, name="part.gcode"):
    send_file(bot, name)
    handlers.handle_callback(bot, callback(data_of(bot.api.sent[-1][2], "sched:new:")[0]))
    return bot.api.edited[-1]


def plan(bot, path="old.gcode", start=NOW + 3600):
    job, error = bot.add_scheduled(path, start)
    assert error is None
    return job


# ------------------------------------------------------------ receiving files

def test_a_stranger_document_is_refused_and_never_downloaded(cosmos):
    cosmos.api.documents["doc-1"] = b"G1"
    handlers.handle_message(cosmos, document(chat=STRANGER))
    assert cosmos.api.downloads == []
    assert "личный" in cosmos.api.sent[-1][1]


def test_without_cosmos_remote_start_the_file_is_explained_not_taken(bot):
    bot.api.documents["doc-1"] = b"G1"
    handlers.handle_message(bot, document())
    assert bot.api.downloads == []
    assert "moonraker_allow_remote_start" in bot.api.sent[-1][1]


def test_a_file_that_is_not_gcode_is_refused(cosmos):
    handlers.handle_message(cosmos, document("photo.jpg"))
    assert cosmos.api.downloads == []
    assert "не G-code" in cosmos.api.sent[-1][1]


def test_a_file_over_the_telegram_limit_is_refused_before_downloading(cosmos):
    handlers.handle_message(cosmos, document(size=25_000_000))
    assert cosmos.api.downloads == []
    assert "20 МБ" in cosmos.api.sent[-1][1]


def test_gcode_is_uploaded_and_offered_for_print_now_or_later(cosmos):
    send_file(cosmos, "Сборка PLA.gcode", b"G1 X10")

    assert cosmos.moonraker.uploaded == [("Сборка PLA.gcode", b"G1 X10")]
    text, keyboard = cosmos.api.sent[-1][1], cosmos.api.sent[-1][2]
    assert "Файл на принтере" in text and "Сборка PLA.gcode" in text
    assert "1 ч 22 мин" in text and "eSUN PLA+" in text
    assert data_of(keyboard, "ask:print:")
    assert data_of(keyboard, "sched:new:")
    # the "receiving…" message does not stay behind in the chat
    assert (OWNER, 1001) in cosmos.api.deleted


def test_replacing_a_file_of_the_same_name_is_said_out_loud(cosmos):
    send_file(cosmos, "old.gcode")
    assert "заменён" in cosmos.api.sent[-1][1]


def test_a_failed_upload_says_so_and_offers_nothing_to_start(cosmos):
    def refuse(name, data):
        from centauri_bot.moonraker import MoonrakerError
        raise MoonrakerError("HTTP 413")
    cosmos.moonraker.upload = refuse
    send_file(cosmos)
    assert "не загрузилось" in cosmos.api.sent[-1][1]
    assert not data_of(cosmos.api.sent[-1][2], "ask:print:")


# ------------------------------------------------------------ planning

def test_a_quick_option_is_planned_only_after_confirmation(cosmos):
    _, _, text, keyboard = open_picker(cosmos)
    assert "Когда запустить" in text
    tomorrow = [button for button in buttons(keyboard) if button["text"] == "завтра 07:00"][0]

    handlers.handle_callback(cosmos, callback(tomorrow["callback_data"]))
    _, _, text, keyboard = cosmos.api.edited[-1]
    assert "Запланировать печать?" in text and "завтра в 07:00" in text
    assert "пустым" in text
    assert storage.load_schedule() == []

    confirm = keyboard[0][0]["callback_data"]
    handlers.handle_callback(cosmos, callback(confirm))
    jobs = storage.load_schedule()
    assert [(item["path"], item["at"]) for item in jobs] == [("part.gcode", at(2026, 9, 13, 7, 0))]

    handlers.handle_callback(cosmos, callback(confirm))      # a replayed tap is harmless
    assert len(storage.load_schedule()) == 1


def test_a_typed_time_leads_to_the_same_confirmation(cosmos):
    _, _, _, keyboard = open_picker(cosmos)
    handlers.handle_callback(cosmos, callback(data_of(keyboard, "sched:custom:")[0]))
    assert "Своё время" in cosmos.api.edited[-1][2]

    handlers.handle_message(cosmos, text_message("когда-нибудь"))
    assert "Не понял время" in cosmos.api.edited[-1][2]
    assert storage.load_schedule() == []

    handlers.handle_message(cosmos, text_message("14.09 08:15"))
    text, keyboard = cosmos.api.sent[-1][1], cosmos.api.sent[-1][2]
    assert "пн 14.09 в 08:15" in text
    handlers.handle_callback(cosmos, callback(keyboard[0][0]["callback_data"]))
    assert storage.load_schedule()[0]["at"] == at(2026, 9, 14, 8, 15)


def test_a_command_abandons_the_typed_time_and_works_as_usual(cosmos):
    _, _, _, keyboard = open_picker(cosmos)
    handlers.handle_callback(cosmos, callback(data_of(keyboard, "sched:custom:")[0]))
    handlers.handle_message(cosmos, text_message("/status"))
    assert cosmos.schedule_draft is None
    assert "Demo Centauri" in cosmos.api.sent[-1][1]


def test_print_confirmation_offers_to_put_the_file_off_for_later(cosmos):
    handlers.show_files(cosmos, OWNER, force_new=True)
    handlers.handle_callback(cosmos, callback(data_of(cosmos.api.sent[-1][2], "ask:print:")[0]))
    keyboard = cosmos.api.sent[-1][2]
    assert keyboard[0][0]["callback_data"].startswith("do:print:")
    assert data_of(keyboard, "sched:new:")


def test_the_plan_lists_jobs_and_cancels_one(cosmos):
    plan(cosmos, "part.gcode", NOW + 7200)
    handlers.handle_message(cosmos, text_message("/plan"))
    text, keyboard = cosmos.api.sent[-1][1], cosmos.api.sent[-1][2]
    assert "Запланированные печати" in text
    assert "part.gcode" in text and "через 2 ч" in text

    handlers.handle_callback(cosmos, callback(data_of(keyboard, "schedcancel:")[0]))
    assert storage.load_schedule() == []
    assert "Задание отменено" in cosmos.api.answers[-1][1]


def test_the_status_keyboard_shows_how_many_prints_are_planned(cosmos):
    assert not data_of(cosmos.keyboard(), "plan")
    plan(cosmos)
    assert [button["text"] for button in buttons(cosmos.keyboard())
            if button["callback_data"] == "plan"] == ["⏰ Запланировано: 1"]


def test_the_offset_setting_is_validated(base_config):
    base_config["schedule_utc_offset"] = "Moscow"
    assert any("schedule_utc_offset" in item for item in config_mod.validate(base_config))
    base_config["schedule_utc_offset"] = "+03:00"
    assert not any("schedule_utc_offset" in item for item in config_mod.validate(base_config))


# ------------------------------------------------------------ at start time

def test_a_reminder_goes_out_once_before_the_start(cosmos):
    job = plan(cosmos)
    cosmos.clock_control.now = NOW + 3600 - 300
    cosmos.check_schedule()
    assert "Скоро старт" in cosmos.api.sent[-1][1]
    assert data_of(cosmos.api.sent[-1][2], "schedcancel:" + job["id"])

    count = len(cosmos.api.sent)
    cosmos.check_schedule()
    assert len(cosmos.api.sent) == count
    assert cosmos.moonraker.started == []


def test_a_due_job_starts_when_everything_checks_out(cosmos):
    plan(cosmos)
    cosmos.clock_control.now = NOW + 3600 + 20
    cosmos.check_schedule()
    assert cosmos.moonraker.started == ["old.gcode"]
    assert storage.load_schedule() == []
    assert "Запустил печать по расписанию" in cosmos.api.sent[-1][1]


@pytest.mark.parametrize("spoil,word", [
    (lambda bot: bot.moonraker.history_jobs.append({"start_time": NOW + 60}), "деталь"),
    (lambda bot: setattr(bot, "status", status(13, "other.gcode", progress=10)), "занят"),
    (lambda bot: setattr(bot.moonraker, "filament", False), "пруток"),
    (lambda bot: bot.moonraker.files.remove("old.gcode"), "файла больше нет"),
])
def test_a_due_job_asks_instead_of_starting_when_in_doubt(cosmos, spoil, word):
    job = plan(cosmos)
    spoil(cosmos)
    cosmos.clock_control.now = NOW + 3600 + 20
    cosmos.check_schedule()

    assert cosmos.moonraker.started == []
    text, keyboard = cosmos.api.sent[-1][1], cosmos.api.sent[-1][2]
    assert "не запустил" in text and word in text
    assert data_of(keyboard, "schedrun:" + job["id"])
    assert storage.load_schedule()[0]["asked"]

    count = len(cosmos.api.sent)
    cosmos.check_schedule()                       # asked once, not on every pass
    assert len(cosmos.api.sent) == count


def test_an_unreadable_history_counts_as_an_unknown_bed(cosmos):
    def broken(limit=8):
        raise RuntimeError("offline")
    cosmos.moonraker.history = broken
    plan(cosmos)
    cosmos.clock_control.now = NOW + 3600 + 20
    cosmos.check_schedule()
    assert cosmos.moonraker.started == []
    assert "пустой ли стол" in cosmos.api.sent[-1][1]


def test_an_undelivered_question_is_asked_again_on_the_next_pass(cosmos):
    plan(cosmos)
    cosmos.moonraker.filament = False
    cosmos.clock_control.now = NOW + 3600 + 20
    cosmos.refresh_main = lambda **kwargs: None
    cosmos.check_schedule()
    assert not storage.load_schedule()[0]["asked"]


def test_the_owner_can_start_an_asked_job_after_a_fresh_confirmation(cosmos):
    plan(cosmos)
    cosmos.moonraker.filament = False
    cosmos.clock_control.now = NOW + 3600 + 20
    cosmos.check_schedule()

    handlers.handle_callback(cosmos, callback(data_of(cosmos.api.sent[-1][2], "schedrun:")[0]))
    assert cosmos.moonraker.started == []
    confirm = cosmos.api.sent[-1][2][0][0]["callback_data"]
    assert confirm.startswith("do:schedrun:")

    handlers.handle_callback(cosmos, callback(confirm))
    assert cosmos.moonraker.started == ["old.gcode"]
    assert storage.load_schedule() == []

    handlers.handle_callback(cosmos, callback(confirm))
    assert cosmos.moonraker.started == ["old.gcode"]
