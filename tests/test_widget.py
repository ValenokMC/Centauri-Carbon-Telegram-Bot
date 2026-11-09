# -*- coding: utf-8 -*-
"""The live print card (Bot API 10.4 widgets) and its fallback.

While a print runs, the status message is an HTML widget whose numbers the bot
rewrites silently. Every way that can fail must end in the message the bot
sent before widgets existed: a refused widget, a group chat, a card deleted by
hand, a caption that does not fit.
"""
import json
import re
import zlib

import pytest

from centauri_bot import handlers, storage, widget
from centauri_bot.telegram_api import TelegramAPI

from conftest import status


NOW = 1_700_000_000.0


def printing(progress=42, ticks=600, total_ticks=3600, **extra):
    return status(13, "Benchy_PLA.gcode", progress=progress, layer=12,
                  total_layer=300, ticks=ticks, total_ticks=total_ticks, **extra)


@pytest.fixture
def live_bot(bot):
    bot.cfg["status_widget"] = True
    bot.status = printing()
    bot.online = True
    return bot


def card_of(chat_data):
    return json.loads(chat_data)


# ------------------------------------------------------------ the HTML itself

def test_html_fits_the_sendwidget_limits():
    raw = widget.HTML.encode("utf-8")
    assert len(raw) <= widget.HTML_LIMIT
    assert len(zlib.compress(raw, 9)) <= widget.HTML_COMPRESSED_LIMIT
    assert "\x00" not in widget.HTML
    assert widget.html_fits()


def test_state_classes_do_not_restyle_the_card():
    """The card gets class "p", "w" or "x" by state. A rule for one of those
    names elsewhere restyled the whole card: on the first live card ".p" (the
    big percentage) made every line large and bold."""
    style = widget.HTML.split("</style>")[0]
    for kind in ("p", "w", "x", "i"):
        rules = re.findall(r"(?:^|[}\n,])\s*\.%s\s*[{,]" % kind, style)
        assert rules == [], kind
    assert "'cd k-'+d.k" in widget.HTML        # kinds are prefixed


def test_html_loads_nothing_and_never_parses_state_as_markup():
    """The sandbox loads nothing anyway; a file name must not become HTML.
    The only URL handling is opening a link the bot sent, on a tap."""
    assert not re.search(r"<script[^>]*\bsrc\b", widget.HTML)
    assert not re.search(r"\bsrc=|url\(|fetch\(|XMLHttpRequest|WebSocket", widget.HTML)
    assert "innerHTML" not in widget.HTML
    assert "textContent" in widget.HTML
    assert "chatDataChanged" in widget.HTML


# ------------------------------------------------------------ the card's state

def test_printing_card_carries_the_numbers_and_the_finish_time():
    data = widget.card(printing(), True, "Demo Centauri", NOW)
    assert data["k"] == "p" and data["p"] == 42
    # Ready-made strings: the card draws them as they are.
    assert data["l"] == "слой 12 из 300"
    assert data["m"] == "сопло 210° · стол 60° · внутри 30°"
    assert data["r"] == 3000 and data["e"] == int(NOW) + 3000
    assert data["f"] == "Benchy_PLA.gcode" and data["u"] == int(NOW)
    assert "sp" not in data


def test_paused_card_has_no_finish_time():
    data = widget.card(status(6, "a.gcode", progress=50, ticks=100), True, "P", NOW)
    assert data["k"] == "w" and "e" not in data


def test_offline_card_says_so():
    data = widget.card(printing(), False, "P", NOW)
    assert data["o"] == 0 and data["s"] == "Нет связи" and "e" not in data


def test_card_speaks_russian():
    assert widget.card(printing(), True, "P", NOW)["s"] == "Печатает"
    assert widget.card(status(6, "a.gcode"), True, "P", NOW)["s"] == "Пауза"
    assert widget.card(status(77, "a.gcode"), True, "P", NOW)["s"] == "Аварийная остановка"
    assert "42" in widget.card(status(42, "a.gcode"), True, "P", NOW)["s"]


def test_layer_is_a_finished_string():
    """The live cards showed "слой 20.45 из 45" for the data [20, 45]: the bot
    now writes the line itself, and a fractional layer is cut to a whole one."""
    st = printing()
    st["PrintInfo"]["CurrentLayer"] = 19.45
    assert widget.card(st, True, "P", NOW)["l"] == "слой 19 из 300"
    st["PrintInfo"]["TotalLayer"] = 0
    assert widget.card(st, True, "P", NOW)["l"] == ""


def test_heating_shows_the_target():
    st = printing(TempOfNozzle=180, TempTargetNozzle=245)
    assert widget.card(st, True, "P", NOW)["m"].startswith("сопло 180→245°")


def test_no_air_temperature_when_the_printer_has_none():
    """COSMOS sends no chamber temperature; the card must not show a dash for it."""
    st = printing()
    st.pop("TempOfBox")
    assert widget.card(st, True, "P", NOW)["m"] == "сопло 210° · стол 60°"


@pytest.mark.parametrize("code, allowed, camera, want", [
    (13, {"pause", "resume", "cancel"}, True, "PSC"),
    (6, {"pause", "resume", "cancel"}, True, "RSC"),
    (13, {"pause", "resume", "cancel"}, False, "PS"),
    (13, set(), True, "C"),                    # monitoring only: just the camera
    (13, set(), False, ""),
])
def test_buttons_follow_the_state_and_the_policy(code, allowed, camera, want):
    st = status(code, "a.gcode")
    assert widget.buttons(st, True, allowed, camera) == want


def test_answer_is_small_json():
    text = widget.answer(a="cancel", t="tok", q="Остановить?", y="Да")
    assert json.loads(text) == {"a": "cancel", "t": "tok", "q": "Остановить?", "y": "Да"}
    long = widget.answer(m="ж" * 2000)
    assert len(long.encode("utf-8")) <= widget.ANSWER_LIMIT


def test_compact_caption_is_one_russian_line(live_bot):
    caption = live_bot.render("", compact=True)
    assert caption == "🖨 <b>Demo Centauri</b> · печатает · 42%"


def test_speed_is_shown_only_when_changed():
    info = printing()
    info["PrintInfo"]["PrintSpeedPct"] = 125
    assert widget.card(info, True, "P", NOW)["sp"] == 125


@pytest.mark.parametrize("st, online, live", [
    (printing(), True, True),
    (status(6, "a.gcode"), True, True),        # paused
    (printing(), False, False),                # no connection
    (status(9, "a.gcode"), True, False),       # finished, name left behind
    (status(8, "a.gcode"), True, False),       # cancelled
    (status(0), True, False),                  # idle
    (None, True, False),
])
def test_only_a_running_print_is_live(st, online, live):
    assert widget.is_live(st, online) is live


def test_long_cyrillic_file_name_still_fits_chat_data():
    st = status(13, "Очень_длинное_имя_файла_" * 40 + ".gcode", progress=1)
    text = widget.encode(widget.card(st, True, "Принтер " * 10, NOW))
    assert len(text.encode("utf-8")) <= widget.DATA_LIMIT
    assert card_of(text)["v"] == widget.VERSION


def test_unchanged_numbers_are_the_same_content():
    a = widget.card(printing(), True, "P", NOW)
    b = widget.card(printing(), True, "P", NOW + 5)
    a["u"], b["u"] = 1, 2
    # The finish time moves with the clock while printing; compare a pause.
    a.pop("e"), b.pop("e")
    assert widget.same_content(a, b)
    assert not widget.same_content(a, dict(b, p=43))


# ------------------------------------------------------------ the API calls

class Recorder(object):
    def __init__(self):
        self.calls = []

    def call(self, method, params=None, files=None, timeout=30):
        self.calls.append((method, params))
        return {"ok": True, "result": {"message_id": 7}}


def test_send_widget_is_a_plain_bot_api_call():
    rec = Recorder()
    TelegramAPI.send_widget(rec, "555", "<p>x</p>", caption="<b>c</b>",
                            keyboard=[[{"text": "a", "callback_data": "b"}]],
                            chat_data="{}", silent=True, allow_queries=True)
    method, p = rec.calls[0]
    assert method == "sendWidget"
    assert p["html"] == "<p>x</p>" and p["caption"] == "<b>c</b>"
    assert p["parse_mode"] == "HTML" and p["chat_data"] == "{}"
    assert p["disable_notification"] is True and p["allow_queries"] is True
    assert p["reply_markup"] == {"inline_keyboard": [[{"text": "a", "callback_data": "b"}]]}


def test_answer_widget_query_call():
    rec = Recorder()
    TelegramAPI.answer_widget_query(rec, "q1", '{"m":"ok"}')
    assert rec.calls == [("answerWidgetQuery",
                          {"widget_query_id": "q1", "data": '{"m":"ok"}'})]


def test_edit_widget_data_call():
    rec = Recorder()
    TelegramAPI.edit_widget_data(rec, "555", 9, "")
    assert rec.calls == [("editMessageWidgetData",
                          {"chat_id": "555", "message_id": 9, "chat_data": ""})]


# ------------------------------------------------------------ sending the card

def test_off_by_default_nothing_changes(bot, fake_telegram):
    bot.status, bot.online = printing(), True
    bot.refresh_main(force_new=True)
    assert fake_telegram.widgets == [] and len(fake_telegram.sent) == 1


def test_running_print_goes_out_as_the_card(live_bot, fake_telegram):
    storage.set_message_id(500)
    mid = live_bot.refresh_main(force_new=True, header="🖨 <b>Печать началась</b>\n")
    assert fake_telegram.sent == []
    [sent] = fake_telegram.widgets
    assert sent["html"] == widget.HTML and sent["chat"] == live_bot.owner
    assert sent["caption"].startswith("🖨 <b>Печать началась</b>")
    assert "42%" in sent["caption"]
    assert "сопло" not in sent["caption"] and "ещё" not in sent["caption"]  # on the card
    # Pause, stop and the camera are on the card; under it only "Menu".
    assert sent["keyboard"] == live_bot.card_keyboard()
    assert sent["allow_queries"] is True
    assert sent["silent"] is False                   # an event still notifies
    assert card_of(sent["chat_data"])["p"] == 42
    assert card_of(sent["chat_data"])["b"] == "PS"   # no camera in the test config
    assert storage.message_id() == mid and storage.message_kind() == widget.KIND
    assert fake_telegram.deleted == [(live_bot.owner, 500)]


def test_refused_card_falls_back_to_the_old_message(live_bot, fake_telegram):
    fake_telegram.widget_answer = {"ok": False, "description": "Bad Request: METHOD_INVALID"}
    mid = live_bot.refresh_main(force_new=True, header="🖨 <b>Печать началась</b>\n")
    [(_, text, _, _)] = fake_telegram.sent
    assert "сопло" in text                            # the full status, as before
    assert storage.message_id() == mid and storage.message_kind() is None
    # and it is not retried on every refresh
    live_bot.refresh_main(force_new=True)
    assert len(fake_telegram.widgets) == 1 and len(fake_telegram.sent) == 2
    live_bot.clock_control.now += live_bot.WIDGET_RETRY_SEC + 1
    live_bot.refresh_main(force_new=True)
    assert len(fake_telegram.widgets) == 2


def test_unreachable_telegram_also_falls_back(live_bot, fake_telegram):
    fake_telegram.widget_answer = {"ok": False}
    assert live_bot.refresh_main(force_new=True) is not None
    assert len(fake_telegram.sent) == 1


def test_group_chat_gets_the_old_message(live_bot, fake_telegram):
    live_bot.owner = "-1001234567890"
    live_bot.refresh_main(force_new=True)
    assert fake_telegram.widgets == [] and len(fake_telegram.sent) == 1


def test_other_screens_are_the_cards_too(live_bot, fake_telegram):
    """Card mode: a screen the bot opens by itself is stored, and the card
    is told to open it ("go"); the caption is the screen's first line."""
    live_bot.refresh_main(force_new=True, text="📂 Файлы\nстрока")
    [sent] = fake_telegram.widgets
    assert fake_telegram.sent == [] and sent["caption"] == "📂 Файлы"
    tok = card_of(sent["chat_data"])["go"]
    assert live_bot.get_screen(tok)["x"] == ["строка"]


def test_finished_print_replaces_the_card(live_bot, fake_telegram):
    live_bot.refresh_main(force_new=True)
    card_mid = storage.message_id()
    live_bot.status = status(9)
    live_bot.refresh_main(force_new=True, text="✅ <b>Печать закончена</b>")
    assert fake_telegram.sent == []                    # the card says it, in its caption
    assert fake_telegram.widgets[-1]["caption"] == "✅ <b>Печать закончена</b>"
    assert (live_bot.owner, card_mid) in fake_telegram.deleted
    assert storage.message_kind() == widget.KIND


def test_undeletable_card_is_retired_and_emptied(live_bot, fake_telegram):
    live_bot.refresh_main(force_new=True)
    card_mid = storage.message_id()
    fake_telegram.delete_message = lambda chat, mid: {
        "ok": False, "description": "Bad Request: message can't be deleted for everyone"}
    live_bot.status = status(9)
    live_bot.refresh_main(force_new=True, text="готово")
    assert (card_mid, "") in fake_telegram.widget_edits
    assert fake_telegram.edited[-1][1:] == (card_mid, live_bot.RETIRED_TEXT, None)
    assert (card_mid, "caption") in fake_telegram.edit_forms
    assert (card_mid, "text") not in fake_telegram.edit_forms


# ------------------------------------------------------------ keeping it live

def test_refresh_edits_the_card_in_place(live_bot, fake_telegram):
    live_bot.refresh_main(force_new=True)
    mid = storage.message_id()
    live_bot.status = printing(progress=43)
    assert live_bot.refresh_main() == mid
    assert card_of(fake_telegram.widget_edits[-1][1])["p"] == 43
    assert fake_telegram.edit_forms[-1] == (mid, "caption")
    assert "43%" in fake_telegram.edited[-1][2]
    assert len(fake_telegram.widgets) == 1 and fake_telegram.sent == []


def test_print_under_an_ordinary_message_gets_its_card_quietly(live_bot, fake_telegram):
    storage.set_message_id(500)                       # a text status from before
    mid = live_bot.refresh_main()                     # what refresh_loop calls
    [sent] = fake_telegram.widgets
    assert sent["silent"] is True
    assert mid != 500 and storage.message_kind() == widget.KIND
    assert fake_telegram.deleted == [(live_bot.owner, 500)]


def test_card_of_an_older_version_is_replaced(live_bot, fake_telegram):
    """A sent widget keeps its HTML forever. After an update the old card
    would read the new data with the old code, so a new card takes over."""
    storage.set_message_id(500, kind="widget")        # what 1.2.0's first card stored
    mid = live_bot.refresh_main()
    assert mid != 500 and storage.message_kind() == widget.KIND
    assert fake_telegram.widgets[-1]["silent"] is True
    assert (live_bot.owner, 500) in fake_telegram.deleted
    assert fake_telegram.widget_edits == []           # old card never fed new data


def test_older_card_is_edited_through_its_caption_only(bot, fake_telegram):
    bot.cfg["send_photo"] = True
    bot.grab = lambda max_age=0: b"\xff\xd8frame"
    bot.status, bot.online = status(9), True          # not live: no new card
    storage.set_message_id(500, kind="widget")
    bot.refresh_main()
    assert all(form != "photo" for _, form in fake_telegram.edit_forms)


def test_refused_promotion_keeps_the_old_message(live_bot, fake_telegram):
    fake_telegram.widget_answer = {"ok": False, "description": "Bad Request"}
    storage.set_message_id(500)
    assert live_bot.refresh_main() == 500
    assert fake_telegram.sent == [] and fake_telegram.deleted == []
    assert fake_telegram.edited[-1][1] == 500


def test_push_is_throttled_and_skips_unchanged_numbers(live_bot, fake_telegram):
    live_bot.cfg["widget_refresh_sec"] = 10
    live_bot.refresh_main(force_new=True)
    mid = storage.message_id()
    clock = live_bot.clock_control

    live_bot.push_widget()                            # same second: nothing
    assert fake_telegram.widget_edits == []

    live_bot.status = status(6, "Benchy_PLA.gcode", progress=42)   # paused
    clock.now += 11
    live_bot.push_widget()
    assert [m for m, _ in fake_telegram.widget_edits] == [mid]
    assert card_of(fake_telegram.widget_edits[-1][1])["k"] == "w"

    clock.now += 11                                   # nothing moved
    live_bot.push_widget()
    assert len(fake_telegram.widget_edits) == 1

    clock.now += live_bot.WIDGET_HEARTBEAT_SEC        # but say it is alive
    live_bot.push_widget()
    assert len(fake_telegram.widget_edits) == 2
    # pushes never touch the caption, so no "edited" mark appears
    assert fake_telegram.edited == []


def test_push_on_every_printer_status(live_bot, fake_telegram):
    live_bot.refresh_main(force_new=True)
    live_bot.clock_control.now += 30
    live_bot._handle_status(printing(progress=44))
    assert card_of(fake_telegram.widget_edits[-1][1])["p"] == 44


def test_card_deleted_by_hand_becomes_an_ordinary_message(live_bot, fake_telegram):
    live_bot.refresh_main(force_new=True)
    mid = storage.message_id()
    fake_telegram.widget_data_answer = {"ok": False,
                                        "description": "Bad Request: message to edit not found"}
    live_bot.clock_control.now += 30
    live_bot.status = printing(progress=45)
    live_bot.push_widget()
    assert storage.message_id() == mid and storage.message_kind() is None
    # the next refresh makes a new card
    fake_telegram.widget_data_answer = None
    assert live_bot.refresh_main() != mid
    assert len(fake_telegram.widgets) == 2


def test_flood_wait_keeps_the_card(live_bot, fake_telegram):
    live_bot.refresh_main(force_new=True)
    fake_telegram.widget_data_answer = {"ok": False,
                                        "description": "Too Many Requests: retry after 5"}
    live_bot.clock_control.now += 30
    live_bot.status = printing(progress=45)
    live_bot.push_widget()
    assert storage.message_kind() == widget.KIND


def test_no_card_no_push(bot, fake_telegram):
    bot.cfg["status_widget"] = True
    bot.status, bot.online = printing(), True
    storage.set_message_id(500)                       # ordinary message
    bot.clock_control.now += 30
    bot.push_widget()
    assert fake_telegram.widget_edits == []


# ------------------------------------------------------------ buttons on the card

def card_callback(data, mid):
    return {"id": "cb-1", "data": data,
            "message": {"message_id": mid, "chat": {"id": "555000111"},
                        "widget": {"html": "…"}},
            "from": {"id": "555000111", "is_bot": False}}


def test_refresh_button_on_the_card_edits_caption_and_numbers(live_bot, fake_telegram):
    live_bot.cfg["send_photo"] = True
    live_bot.grab = lambda max_age=0: b"\xff\xd8frame"
    live_bot.refresh_main(force_new=True)
    mid = storage.message_id()
    handlers.handle_callback(live_bot, card_callback("refresh", mid))
    assert fake_telegram.edit_forms[-1] == (mid, "caption")   # never a photo
    assert "сопло" not in fake_telegram.edited[-1][2]         # short caption
    assert fake_telegram.widget_edits and fake_telegram.widget_edits[-1][0] == mid
    assert storage.message_id() == mid and storage.message_kind() == widget.KIND
    assert fake_telegram.sent == [] and fake_telegram.deleted == []


def test_details_button_opens_the_full_text(live_bot, fake_telegram):
    live_bot.refresh_main(force_new=True)
    mid = storage.message_id()
    handlers.handle_callback(live_bot, card_callback("details", mid))
    assert "сопло" in fake_telegram.edited[-1][2]


def test_unchanged_refresh_does_not_rebuild_the_card(live_bot, fake_telegram):
    live_bot.refresh_main(force_new=True)
    mid = storage.message_id()
    fake_telegram.edit_message = lambda *a, **k: {
        "ok": False, "description": "Bad Request: message is not modified"}
    handlers.handle_callback(live_bot, card_callback("refresh", mid))
    assert fake_telegram.deleted == [] and fake_telegram.sent == []
    assert storage.message_kind() == widget.KIND


def test_stop_on_the_card_still_asks_first(live_bot, fake_telegram):
    sent_commands = []
    live_bot.ws = object()
    live_bot.run_command = lambda cmd, *a, **k: (sent_commands.append(cmd), (True, "Ack=0"))[1]
    live_bot.refresh_main(force_new=True)
    mid = storage.message_id()
    handlers.handle_callback(live_bot, card_callback("ask:stop", mid))
    assert sent_commands == []
    assert fake_telegram.edit_forms[-1] == (mid, "caption")
    _, edited_mid, text, keyboard = fake_telegram.edited[-1]
    assert edited_mid == mid and "Остановить печать?" in text
    assert any("Да" in b["text"] for row in keyboard for b in row)


def test_long_screen_on_the_card_becomes_an_ordinary_message(live_bot, fake_telegram):
    """A caption holds 1024 characters; a longer screen replaces the card."""
    live_bot.refresh_main(force_new=True)
    mid = storage.message_id()

    def edit(chat, message_id, text, keyboard=None, photo=None, is_photo=False):
        return {"ok": False, "description": "Bad Request: MEDIA_CAPTION_TOO_LONG"}
    fake_telegram.edit_message = edit
    new_mid = live_bot.edit_main_from_callback(mid, "x" * 2000, is_photo=True)
    assert new_mid != mid and storage.message_id() == new_mid
    assert storage.message_kind() is None
    assert (live_bot.owner, mid) in fake_telegram.deleted


# ------------------------------------------------------------ taps on the card

def widget_query(data, sender="555000111", chat="555000111", qid="wq-1"):
    return {"id": qid, "from": {"id": sender, "is_bot": False},
            "chat": {"id": chat, "type": "private"}, "message_id": 77,
            "chat_instance": "1", "type": {"type": "data"}, "data": data}


def last_answer(fake_telegram):
    return json.loads(fake_telegram.widget_answers[-1][1])


@pytest.fixture
def card_bot(live_bot):
    live_bot.ws = object()
    live_bot.commands = []
    live_bot.run_command = lambda cmd, *a, **k: (live_bot.commands.append(cmd), (True, "Ack=0"))[1]
    live_bot.refresh_main(force_new=True)
    return live_bot


def test_stop_on_the_card_asks_then_acts(card_bot, fake_telegram):
    from centauri_bot import sdcp
    handlers.handle_widget_query(card_bot, widget_query("ask:stop"))
    ask = last_answer(fake_telegram)
    assert card_bot.commands == []                     # nothing happened yet
    assert ask["a"] == "cancel" and ask["t"] and "Остановить" in ask["q"]
    request = "do:%s:%s" % (ask["a"], ask["t"])
    assert len(request.encode("utf-8")) <= widget.QUERY_LIMIT
    handlers.handle_widget_query(card_bot, widget_query(request, qid="wq-2"))
    assert sdcp.CMD_STOP in card_bot.commands
    # the answer comes first and says the command is on its way; the result
    # is the card's note, written by the follow-up
    assert "Отправляю" in last_answer(fake_telegram)["m"]
    assert "остановлена" in card_of(fake_telegram.widget_edits[-1][1])["n"]
    assert [q for q, _ in fake_telegram.widget_answers] == ["wq-1", "wq-2"]


def test_card_token_is_one_use(card_bot, fake_telegram):
    handlers.handle_widget_query(card_bot, widget_query("ask:pause"))
    ask = last_answer(fake_telegram)
    request = "do:%s:%s" % (ask["a"], ask["t"])
    handlers.handle_widget_query(card_bot, widget_query(request))
    handlers.handle_widget_query(card_bot, widget_query(request))
    assert len(card_bot.commands) == 1
    assert "устарело" in last_answer(fake_telegram)["m"]


def test_token_for_pause_does_not_stop(card_bot, fake_telegram):
    handlers.handle_widget_query(card_bot, widget_query("ask:pause"))
    token = last_answer(fake_telegram)["t"]
    handlers.handle_widget_query(card_bot, widget_query("do:cancel:" + token))
    assert card_bot.commands == []


def test_forged_or_foreign_taps_do_nothing(card_bot, fake_telegram):
    handlers.handle_widget_query(card_bot, widget_query("do:cancel:guess"))
    handlers.handle_widget_query(card_bot, widget_query("ask:stop", sender="999"))
    handlers.handle_widget_query(card_bot, widget_query("rm -rf"))
    assert card_bot.commands == []
    answers = [json.loads(d) for _, d in fake_telegram.widget_answers]
    assert all("t" not in a for a in answers) and len(answers) == 3


def test_control_off_card_refuses(card_bot, fake_telegram):
    card_bot.cfg["allow_control"] = False
    handlers.handle_widget_query(card_bot, widget_query("ask:stop"))
    assert "выключено" in last_answer(fake_telegram)["m"]


def test_camera_button_sends_a_frame_that_goes_away(card_bot, fake_telegram):
    card_bot.cfg["send_photo"] = True
    card_bot.grab = lambda max_age=0: b"\xff\xd8frame"
    handlers.handle_widget_query(card_bot, widget_query("snap"))
    assert "Снимаю" in last_answer(fake_telegram)["m"]
    [(_, text, _, has_photo)] = fake_telegram.sent
    assert has_photo and "уберу" in text
    snap_mid = storage.load_state()["snapshot_message"][0]

    # a second frame replaces the first at once
    handlers.handle_widget_query(card_bot, widget_query("snap", qid="wq-2"))
    assert (card_bot.owner, snap_mid) in fake_telegram.deleted
    snap_mid = storage.load_state()["snapshot_message"][0]

    card_bot.clock_control.now += card_bot.SNAPSHOT_TTL_SEC + 20
    card_bot.expire_snapshot()
    assert (card_bot.owner, snap_mid) in fake_telegram.deleted
    assert storage.load_state()["snapshot_message"] is None


def test_camera_off_says_so(card_bot, fake_telegram):
    handlers.handle_widget_query(card_bot, widget_query("snap"))
    assert "выключена" in last_answer(fake_telegram)["m"]
    assert fake_telegram.sent == []


def test_menu_button_opens_and_folds_the_panel(card_bot, fake_telegram):
    mid = storage.message_id()
    handlers.handle_callback(card_bot, card_callback("cardmenu", mid))
    opened = fake_telegram.edited[-1][3]
    labels = [b["text"] for row in opened for b in row]
    assert "📂 Файлы" in labels and "▲ Свернуть меню" in labels
    handlers.handle_callback(card_bot, card_callback("refresh", mid))
    assert fake_telegram.edited[-1][3] == card_bot.card_keyboard()


def test_widget_queries_reach_the_handler(card_bot, fake_telegram):
    fake_telegram._updates = [{"update_id": 1, "widget_query": widget_query("ask:stop")}]
    card_bot.api.get_updates = (lambda orig: lambda **kw: (
        card_bot.stopping.set(), orig(**kw))[1])(fake_telegram.get_updates)
    card_bot.telegram_loop()
    assert fake_telegram.widget_answers and "t" in last_answer(fake_telegram)
    assert "widget_query" in card_bot.UPDATES


# ------------------------------------------------------------ the whole panel on the card

def test_menu_is_the_panel_minus_the_card_buttons(card_bot):
    items = widget.menu(card_bot.keyboard())
    datas = [d for _, d in items]
    assert "files" in datas and "help" in datas
    assert not set(datas) & widget.ON_THE_CARD
    assert card_of(widget.encode(card_bot._card()))["mn"] == items


def test_nothing_under_the_card(card_bot, fake_telegram):
    assert fake_telegram.widgets[-1]["keyboard"] == []


def test_card_state_with_menu_and_a_long_name_fits(card_bot):
    card_bot.cfg["send_photo"] = True
    card_bot.status = status(13, "Очень_длинное_имя_файла_" * 10 + ".gcode", progress=5)
    text = widget.encode(card_bot._card())
    assert len(text.encode("utf-8")) <= widget.DATA_LIMIT
    assert card_of(text)["mn"]                         # the menu survived


def test_menu_item_opens_its_screen_on_the_card(card_bot, fake_telegram):
    """A screen of the bot is drawn on the card; the message is not touched."""
    card_bot.refresh_files = lambda: (True, "")
    card_bot.files = ["/local/a.gcode"]
    edits_before = len(fake_telegram.edited)
    handlers.handle_widget_query(card_bot, widget_query("c:files"))
    sc = last_answer(fake_telegram)["sc"]
    assert any("a.gcode" in text for text in [sc["t"]] + sc["x"] + [b[0] for r in sc["k"] for b in r])
    codes = [b[1] for row in sc["k"] for b in row]
    assert any(c.startswith("c:ask:print:") for c in codes)
    assert "b" in codes                                   # "Назад" is local
    assert len(fake_telegram.edited) == edits_before      # no inline buttons
    assert fake_telegram.sent == []
    # no stray answerCallbackQuery for a tap that had no callback
    assert all(cid is None for cid, _ in fake_telegram.answers)


def test_nested_screen_and_its_confirmation_stay_on_the_card(card_bot, fake_telegram):
    """Fans -> a level -> apply: every step is a screen on the card."""
    card_bot.cfg["backend"] = "sdcp"
    handlers.handle_widget_query(card_bot, widget_query("c:menu:fans"))
    sc = last_answer(fake_telegram)["sc"]
    assert sc["t"] == "🌀 Вентиляторы" and sc["x"] == []  # a title, not the status
    level = next(b[1] for row in sc["k"] for b in row if b[1].startswith("c:set:fan:"))
    handlers.handle_widget_query(card_bot, widget_query(level, qid="wq-2"))
    assert "sc" in last_answer(fake_telegram)
    assert fake_telegram.sent == []


def test_screen_action_result_goes_back_to_the_card(card_bot, fake_telegram):
    handlers.handle_widget_query(card_bot, widget_query("c:ask:stop"))
    sc = last_answer(fake_telegram)["sc"]
    confirm = next(b for row in sc["k"] for b in row if b[2] == "y")
    handlers.handle_widget_query(card_bot, widget_query(confirm[1], qid="wq-2"))
    from centauri_bot import sdcp
    assert sdcp.CMD_STOP in card_bot.commands
    answer = last_answer(fake_telegram)
    assert answer.get("close") == 1 and "останов" in answer["m"].lower()


def test_forged_screen_taps_do_nothing(card_bot, fake_telegram):
    handlers.handle_widget_query(card_bot, widget_query("c:do:job:cancel:guess"))
    handlers.handle_widget_query(card_bot, widget_query("c:files", sender="999"))
    assert card_bot.commands == []
    assert fake_telegram.sent == []


def test_screen_too_big_for_one_answer_is_paged_never_sent_under_the_card(card_bot, fake_telegram):
    card_bot.refresh_files = lambda: (True, "")
    card_bot.files = ["/local/ECC_0.2mm_PLA_Bracket_left_v%d_xxx.gcode" % i
                      for i in range(1, 9)]
    handlers.handle_widget_query(card_bot, widget_query("c:files"))
    answer = last_answer(fake_telegram)
    assert fake_telegram.edited == [] and fake_telegram.sent == []
    assert "sc" in answer and "под карточкой" not in json.dumps(answer, ensure_ascii=False)


def test_fit_keeps_answers_within_1kb():
    sc = widget.screen("<b>Файлы</b>\n" + "\n".join("строка %d " % i + "ж" * 60
                                                    for i in range(30)),
                       [[{"text": "🖨 " + "ж" * 50, "callback_data": "ask:print:abcdefgh"}]] * 8)
    text = widget.fit(sc, "заметка")
    assert text and len(text.encode("utf-8")) <= widget.ANSWER_LIMIT
    assert json.loads(text)["sc"]["k"]


def test_screen_converts_buttons():
    sc = widget.screen("<b>Заголовок</b>\nстрока &amp; ещё", [
        [{"text": "✅ Да", "callback_data": "do:job:cancel:t"},
         {"text": "↩️ Отмена", "callback_data": "refresh"}],
        [{"text": "сайт", "url": "https://example.org"},
         {"text": "подпись", "callback_data": "noop"}]])
    assert sc["t"] == "Заголовок" and sc["x"] == ["строка & ещё"]
    assert sc["k"] == [[["✅ Да", "c:do:job:cancel:t", "y"], ["↩️ Отмена", "b", ""]],
                       [["сайт", "u:https://example.org", ""], ["подпись", "", ""]]]


def test_idle_printer_gets_the_card_too(bot, fake_telegram):
    bot.cfg["status_widget"] = True
    bot.status, bot.online = status(0), True
    bot.refresh_main(force_new=True)
    [sent] = fake_telegram.widgets
    data = card_of(sent["chat_data"])
    assert data["k"] == "i" and data["s"] == "Свободен"
    assert data["l"] == "Можно ставить печать" and data["mn"]
    assert "свободен" in sent["caption"]


def test_frame_goes_on_top_and_the_card_moves_under_it(card_bot, fake_telegram):
    card_bot.cfg["send_photo"] = True
    card_bot.grab = lambda max_age=0: b"\xff\xd8frame"
    old_card = storage.message_id()
    handlers.handle_widget_query(card_bot, widget_query("snap"))
    [(_, _, _, has_photo)] = fake_telegram.sent
    assert has_photo
    new_card = storage.message_id()
    assert new_card != old_card and storage.message_kind() == widget.KIND
    assert new_card > storage.load_state()["snapshot_message"][0]   # below the frame
    assert fake_telegram.widgets[-1]["silent"] is True
    assert (card_bot.owner, old_card) in fake_telegram.deleted


def test_picture_screen_replaces_the_card_and_back_restores_it(card_bot, fake_telegram):
    card_bot.bed_mesh = lambda: (True, {"points": [[0.0, 0.1], [0.2, 0.0]]})
    card_bot.backend_name = "moonraker"
    card_mid = storage.message_id()
    handlers.show_mesh(card_bot, card_bot.owner, card_mid, is_photo=True)
    assert (card_bot.owner, card_mid) in fake_telegram.deleted
    assert fake_telegram.sent[-1][3] is True              # the map went out as a photo
    assert storage.message_kind() is None
    photo_mid = storage.message_id()

    msg = {"message_id": photo_mid, "chat": {"id": card_bot.owner}, "photo": [{}]}
    handlers.handle_callback(card_bot, {"id": "cb", "data": "refresh", "message": msg,
                                        "from": {"id": card_bot.owner}})
    assert storage.message_kind() == widget.KIND
    assert (card_bot.owner, photo_mid) in fake_telegram.deleted
