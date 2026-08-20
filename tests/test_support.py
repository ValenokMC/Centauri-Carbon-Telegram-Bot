# -*- coding: utf-8 -*-
"""The support link, and the promise that the reminder stays unobtrusive.

Every rule from the project's own brief is a test here, because "it only asks
once a month" is a claim made to users, and an untested claim about restraint
is the kind that quietly stops being true after a refactor.
"""
from centauri_bot import printer_state as ps
from centauri_bot import storage, support, ui

from conftest import status


DAY = 86400


# ------------------------------------------------------------------ the link

def test_tribute_url_is_a_single_source_of_truth():
    assert support.TRIBUTE_URL_TELEGRAM.startswith("https://")
    assert support.TRIBUTE_URL_WEB.startswith("https://")
    assert "dP54" in support.TRIBUTE_URL_TELEGRAM
    assert "P54" in support.TRIBUTE_URL_WEB


def test_tribute_link_carries_no_tracking_parameters():
    """Nothing is measured. The link must not smuggle an identifier either."""
    for url in (support.TRIBUTE_URL_TELEGRAM, support.TRIBUTE_URL_WEB):
        lowered = url.lower()
        for marker in ("utm_", "ref=", "referrer", "click_id", "uid="):
            assert marker not in lowered


def test_help_screen_always_offers_support():
    """There is no setting that hides this. That is on purpose."""
    _, keyboard = ui.help_screen()
    urls = [b["url"] for row in keyboard for b in row if "url" in b]
    assert support.TRIBUTE_URL_TELEGRAM in urls


def test_help_screen_has_all_five_promised_entries():
    _, keyboard = ui.help_screen()
    texts = [b["text"] for row in keyboard for b in row]
    assert any("Документация" in t for t in texts)
    assert any("ошибке" in t for t in texts)
    assert any("автору" in t and "Написать" in t for t in texts)
    assert any("Поддержать" in t for t in texts)
    assert any("Вернуться" in t for t in texts)


def test_monitoring_only_help_does_not_promise_control_buttons():
    text, _ = ui.help_screen(allow_control=False)
    for unavailable in ("пауза", "продолжить", "стоп", "свет",
                        "скорость", "нагрев"):
        assert unavailable not in text.lower()
    for available in ("обновить", "подробнее", "файлы"):
        assert available in text.lower()


# ------------------------------------------- where the button must NOT appear

def test_status_keyboard_never_carries_the_support_button(bot):
    keyboard = ui.kb_main(status(13, "demo.gcode", progress=50))
    urls = [b.get("url") for row in keyboard for b in row]
    assert support.TRIBUTE_URL_TELEGRAM not in urls


def test_confirmation_keyboard_never_carries_the_support_button():
    keyboard = ui.kb_confirm("stop", "остановить")
    urls = [b.get("url") for row in keyboard for b in row]
    assert support.TRIBUTE_URL_TELEGRAM not in urls
    assert len(keyboard) == 1 and len(keyboard[0]) == 2


def test_fan_and_speed_menus_carry_no_support_button():
    for keyboard in (ui.kb_speed(100), ui.kb_temp(),
                     ui.kb_fans({"ModelFan": 0, "BoxFan": 0, "AuxiliaryFan": 0})):
        urls = [b.get("url") for row in keyboard for b in row]
        assert support.TRIBUTE_URL_TELEGRAM not in urls


# ------------------------------------------------------------ the 30-day rule

def test_reminder_is_not_due_before_the_first_month():
    installed = 1_000_000.0
    state = {"installed_at": installed, "last_support_reminder_at": None}
    assert support.due(state, now=installed + 29 * DAY) is False
    assert support.due(state, now=installed + 31 * DAY) is True


def test_reminder_is_not_due_without_an_install_date():
    """An unstamped state means setup never finished. Ask nobody."""
    assert support.due({}, now=9_999_999_999) is False


def test_reminder_waits_a_full_interval_after_being_shown():
    installed = 1_000_000.0
    shown = installed + 31 * DAY
    state = {"installed_at": installed, "last_support_reminder_at": shown}
    assert support.due(state, now=shown + 29 * DAY) is False
    assert support.due(state, now=shown + 30 * DAY + 1) is True


def test_reminder_text_is_short_and_unpressured():
    text = support.REMINDER_TEXT.strip()
    assert len(text.splitlines()) <= 2
    for pushy in ("осталось", "последний", "срочно", "сегодня", "только"):
        assert pushy not in text.lower()


def test_reminder_keyboard_is_exactly_one_button():
    keyboard = support.reminder_keyboard()
    assert len(keyboard) == 1 and len(keyboard[0]) == 1
    assert keyboard[0][0]["url"] == support.TRIBUTE_URL_TELEGRAM


# ------------------------------------------------ persistence across restarts

def test_reminder_state_survives_a_restart(bot):
    """The interval lives on disk. A bot restarted twice a day must not ask
    twice a day - which is exactly what an in-memory timer would do."""
    storage.mark_installed(when=bot.clock_control.now)
    bot.clock_control.advance_days(31)

    assert bot.maybe_support_note() != ""
    bot.confirm_support_note_shown()

    # Simulate a restart: nothing in memory carries over, only the files.
    from centauri_bot.app import Bot
    restarted = Bot(bot.cfg, api=bot.api, clock=bot.clock)
    assert restarted.maybe_support_note() == ""


def test_install_date_is_not_reset_by_running_setup_again(bot):
    first = storage.mark_installed(when=1_000_000.0)
    again = storage.mark_installed(when=2_000_000.0)
    assert first == again == 1_000_000.0


# ------------------------------------------ only on a genuinely good moment

def test_note_is_appended_to_a_finished_print(bot):
    storage.mark_installed(when=bot.clock_control.now)
    bot.clock_control.advance_days(31)

    bot.announce(ps.Event(ps.FINISHED, 9, {"Filename": "demo.gcode",
                                           "CurrentTicks": 3600,
                                           "TotalLayer": 120}, 100))
    body = bot.api.sent[-1][1]
    assert "Печать закончена" in body
    assert "поддержать" in body.lower()


def test_note_is_not_attached_to_a_cancelled_print(bot):
    storage.mark_installed(when=bot.clock_control.now)
    bot.clock_control.advance_days(31)

    bot.announce(ps.Event(ps.CANCELLED, 8, {"Filename": "demo.gcode"}, 40))
    body = bot.api.sent[-1][1]
    assert "поддержать" not in body.lower()


def test_note_is_not_attached_to_a_stall(bot):
    storage.mark_installed(when=bot.clock_control.now)
    bot.clock_control.advance_days(31)

    bot.announce(ps.Event(ps.STALLED, 42, {"Filename": "demo.gcode"}, 30))
    body = bot.api.sent[-1][1]
    assert "поддержать" not in body.lower()


def test_note_is_not_attached_to_a_pause(bot):
    storage.mark_installed(when=bot.clock_control.now)
    bot.clock_control.advance_days(31)

    bot.announce(ps.Event(ps.PAUSED, 6, {"Filename": "demo.gcode"}, 30))
    body = bot.api.sent[-1][1]
    assert "поддержать" not in body.lower()


def test_a_second_finished_print_the_same_month_does_not_ask_again(bot):
    storage.mark_installed(when=bot.clock_control.now)
    bot.clock_control.advance_days(31)

    finished = ps.Event(ps.FINISHED, 9, {"Filename": "demo.gcode"}, 100)
    bot.announce(finished)
    assert "поддержать" in bot.api.sent[-1][1].lower()

    bot.clock_control.advance_days(2)
    bot.announce(finished)
    assert "поддержать" not in bot.api.sent[-1][1].lower()


def test_the_stamp_is_only_written_after_delivery_succeeds(bot):
    """If Telegram is unreachable at that moment, the month is not spent."""
    storage.mark_installed(when=bot.clock_control.now)
    bot.clock_control.advance_days(31)

    class Unreachable(object):
        def __getattr__(self, name):
            def call(*a, **k):
                return {"ok": False, "description": "network down"}
            return call

    bot.api = Unreachable()
    bot.announce(ps.Event(ps.FINISHED, 9, {"Filename": "demo.gcode"}, 100))
    assert storage.load_state().get("last_support_reminder_at") is None
