# -*- coding: utf-8 -*-
"""The chat keeps exactly one status message, even once it is older than 48 h.

Telegram lets a bot delete its own messages in a private chat for 48 hours
only. Before this was handled, every lost connection and every restart tried
to move the status message down, the delete was refused, and a new message
appeared next to the old one.
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


def test_old_message_is_edited_instead_of_duplicated(bot, fake_telegram):
    OldMessage(fake_telegram)
    storage.set_message_id(500)
    mid = bot.refresh_main(force_new=True, text="связь потеряна")
    assert mid == 500 and storage.message_id() == 500
    assert fake_telegram.sent == []
    assert fake_telegram.edited[-1][1:3] == (500, "связь потеряна")


def test_repeated_events_on_old_message_never_add_messages(bot, fake_telegram):
    OldMessage(fake_telegram)
    storage.set_message_id(500)
    for text in ("связь потеряна", "бот перезапущен", "связь восстановлена"):
        bot.refresh_main(force_new=True, text=text)
    assert fake_telegram.sent == []
    assert storage.message_id() == 500


def test_old_message_deleted_by_hand_is_replaced(bot, fake_telegram):
    OldMessage(fake_telegram)
    fake_telegram.fail_edits = True     # "message to edit not found"
    storage.set_message_id(500)
    mid = bot.refresh_main(force_new=True, text="статус")
    assert mid != 500 and storage.message_id() == mid
    assert len(fake_telegram.sent) == 1


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
    OldMessage(fake_telegram)
    storage.set_message_id(500)
    assert bot.refresh_main(force_new=True, text="связь потеряна", photo=False) == 500
    assert calls == ["text", "caption"]
    assert fake_telegram.sent == []
