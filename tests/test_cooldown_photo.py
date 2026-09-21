# -*- coding: utf-8 -*-
"""The "cooled down" notice shows the part as it was when the print ended.

By the time the nozzle is cold the bed has dropped and a live frame shows the
part sunk out of view, so the notice repeats the finish frame instead.
"""
from centauri_bot import printer_state as ps

from conftest import status


def _capture_photos(bot):
    photos = []
    send = bot.api.send_message

    def spy(chat, text, keyboard=None, photo=None):
        photos.append((text, photo))
        return send(chat, text, keyboard=keyboard, photo=photo)

    bot.api.send_message = spy
    return photos


def _finish_hot(bot, kind=ps.FINISHED):
    bot.status = status(9, TempOfNozzle=210, TempTargetNozzle=0)
    bot.announce(ps.Event(kind, 9, {"Filename": "demo.gcode"}, 100))


def _cool(bot):
    bot._maybe_notify_cooldown(status(9, TempOfNozzle=40, TempTargetNozzle=0))


def test_cooled_notice_repeats_the_finish_frame_not_a_live_one(bot):
    photos = _capture_photos(bot)
    bot.print_frame = b"bed still up"
    bot.grab = lambda max_age=0: b"bed already down"

    _finish_hot(bot)
    assert photos[-1][1] == b"bed still up"

    _cool(bot)
    assert "остыл" in photos[-1][0]
    assert photos[-1][1] == b"bed still up"


def test_cancelled_print_gets_the_same_treatment(bot):
    photos = _capture_photos(bot)
    bot.print_frame = b"bed still up"
    bot.grab = lambda max_age=0: b"bed already down"

    _finish_hot(bot, ps.CANCELLED)
    _cool(bot)
    assert photos[-1][1] == b"bed still up"


def test_without_a_finish_frame_the_cooled_notice_goes_without_a_photo(bot):
    photos = _capture_photos(bot)
    bot.print_frame = None
    bot.grab = lambda max_age=0: b"bed already down"

    _finish_hot(bot)
    _cool(bot)
    assert "остыл" in photos[-1][0]
    assert not photos[-1][1]


def test_the_finish_frame_is_used_once(bot):
    photos = _capture_photos(bot)
    bot.print_frame = b"bed still up"
    _finish_hot(bot)
    _cool(bot)
    assert bot.cooldown_photo is None
    sent = len(photos)
    _cool(bot)
    assert len(photos) == sent
