# -*- coding: utf-8 -*-
"""The chat keeps one live status message, even once it is older than 48 h.

Telegram lets a bot delete its own messages in a private chat for 48 hours
only. First every lost connection and restart left a full second panel next to
the old one; then the old one was edited in place instead, and /status, /files
and notifications silently landed far up the chat. Now the new message goes to
the bottom and the undeletable one shrinks to a buttonless pointer.
"""
from centauri_bot import storage


class OldMessage(object):
    """Make deleteMessage refuse the way Telegram does after 48 hours."""

    def __init__(self, api):
        self.api = api
        api.delete_message = self.refuse

    def refuse(self, chat, message_id):
        return {"ok": False,
                "description": "Bad Request: message can't be deleted for everyone"}


def test_fresh_message_is_moved_down(bot, fake_telegram):
    storage.set_message_id(500)
    mid = bot.refresh_main(force_new=True, text="связь потеряна")
    assert fake_telegram.deleted == [(bot.owner, 500)]
    assert mid != 500 and storage.message_id() == mid
    assert len(fake_telegram.sent) == 1


def test_old_message_still_comes_down_and_is_retired(bot, fake_telegram):
    """/status, /files and notifications must reach the bottom of the chat
    even once the old message can no longer be deleted."""
    OldMessage(fake_telegram)
    storage.set_message_id(500)
    mid = bot.refresh_main(force_new=True, text="связь потеряна")
    assert mid != 500 and storage.message_id() == mid
    assert [t for _, t, _, _ in fake_telegram.sent] == ["связь потеряна"]
    assert fake_telegram.edited[-1][1:] == (500, bot.RETIRED_TEXT, None)


def test_repeated_events_on_old_message_retire_it_once(bot, fake_telegram):
    storage.set_message_id(500)
    refuse = OldMessage(fake_telegram).refuse
    bot.refresh_main(force_new=True, text="связь потеряна")
    # the fresh message is deletable again, as Telegram allows for 48 h
    fake_telegram.delete_message = lambda chat, mid: (
        {"ok": True} if mid != 500 else refuse(chat, mid))
    for text in ("бот перезапущен", "связь восстановлена"):
        bot.refresh_main(force_new=True, text=text)
    retired = [e for e in fake_telegram.edited if e[2] == bot.RETIRED_TEXT]
    assert [e[1] for e in retired] == [500]
    assert len(fake_telegram.sent) == 3


def test_old_message_deleted_by_hand_is_replaced(bot, fake_telegram):
    OldMessage(fake_telegram)
    fake_telegram.fail_edits = True     # "message to edit not found"
    storage.set_message_id(500)
    mid = bot.refresh_main(force_new=True, text="статус")
    assert mid != 500 and storage.message_id() == mid
    assert len(fake_telegram.sent) == 1


def test_failed_send_keeps_old_message_working(bot, fake_telegram):
    fake_telegram.send_message = lambda *a, **k: {"ok": False, "description": "flood"}
    storage.set_message_id(500)
    assert bot.refresh_main(force_new=True, text="статус") == 500
    assert fake_telegram.deleted == []
    assert fake_telegram.edited[-1][1:3] == (500, "статус")


def test_unchanged_content_is_not_a_failed_edit(bot, fake_telegram):
    def not_modified(chat, message_id, text, keyboard=None, photo=None, is_photo=False):
        return {"ok": False, "description": "Bad Request: message is not modified"}
    fake_telegram.edit_message = not_modified
    storage.set_message_id(500)
    assert bot.refresh_main(text="статус") == 500
    assert fake_telegram.deleted == [] and fake_telegram.sent == []


def test_photo_message_gets_a_caption_when_no_frame(bot, fake_telegram):
    calls = []

    def edit(chat, message_id, text, keyboard=None, photo=None, is_photo=False):
        calls.append(("photo" if photo else "caption" if is_photo else "text"))
        if is_photo:
            return {"ok": True, "result": {"message_id": message_id}}
        return {"ok": False,
                "description": "Bad Request: there is no text in the message to edit"}
    fake_telegram.edit_message = edit
    storage.set_message_id(500)
    assert bot.refresh_main(text="статус", photo=False) == 500
    assert calls == ["text", "caption"]
    assert fake_telegram.sent == []


def test_old_photo_message_is_retired_through_its_caption(bot, fake_telegram):
    calls = []

    def edit(chat, message_id, text, keyboard=None, photo=None, is_photo=False):
        calls.append((text, is_photo))
        if is_photo:
            return {"ok": True, "result": {"message_id": message_id}}
        return {"ok": False,
                "description": "Bad Request: there is no text in the message to edit"}
    fake_telegram.edit_message = edit
    OldMessage(fake_telegram)
    storage.set_message_id(500)
    assert bot.refresh_main(force_new=True, text="связь потеряна", photo=False) != 500
    assert calls == [(bot.RETIRED_TEXT, False), (bot.RETIRED_TEXT, True)]
