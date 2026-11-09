# -*- coding: utf-8 -*-
"""Card v6: everything inside the widget, and faster taps. All offline.

In card mode the bot never puts an inline keyboard or a message of its own
beside the card: a screen is a page the card draws, or a picture slot made by
post_picture. These tests drive the main flows with a recorder and check it.
"""
import collections
import json
import logging
import re

import pytest

from centauri_bot import backend, handlers, heightmap, objectmap, storage, ui, widget
from centauri_bot import printer_state as ps
from centauri_bot.telegram_api import TelegramAPI

from conftest import status
from test_widget import (card_bot, card_of, last_answer, live_bot, printing,  # noqa: F401
                         widget_query)


OWNER = "555000111"


def offline_api():
    """A client that has made no connection (the suite builds none for real)."""
    api = TelegramAPI.__new__(TelegramAPI)
    TelegramAPI.__init__(api, "123:fake")
    return api


class FakeMoonraker(object):
    """Answers like COSMOS and counts how often it was asked."""

    def __init__(self):
        self.calls = collections.Counter()
        self.paths = ["/local/ECC_0.2mm_PLA_Bracket_left_v%d_xxx.gcode" % i
                      for i in range(1, 9)]
        self.names = ["A.DRC_ID_0_COPY_0", "B.DRC_ID_1_COPY_0", "C.DRC_ID_2_COPY_0"]
        self.deleted = []

    def list_file_records(self):
        self.calls["files"] += 1
        return [{"path": p, "size": 1_000_000, "modified": 1_700_000_000}
                for p in self.paths]

    def history(self):
        self.calls["history"] += 1
        return [{"filename": "a.gcode", "status": "completed", "total_duration": 600}]

    def list_macros(self):
        self.calls["macros"] += 1
        return []

    def diagnostics(self):
        self.calls["diag"] += 1
        return {"klippy_state": "ready"}

    def bed_mesh(self):
        self.calls["mesh"] += 1
        return {"points": [[0.0, 0.1], [0.2, 0.3]], "profile": "default", "loaded": True}

    def exclude_object_state(self):
        self.calls["objects"] += 1
        a, b, n = self.names
        return {"Objects": list(self.names), "ExcludedObjects": [b],
                "CurrentObject": a, "PrintState": "printing", "Filename": "x.gcode",
                "Shapes": {a: {"polygon": [(10, 10), (60, 10), (60, 60), (10, 60)]},
                           b: {"polygon": [(100, 100), (150, 100), (150, 150), (100, 150)]}}}

    def delete(self, path):
        self.deleted.append(path)

    def file_metadata(self, path):
        return {}

    def set_light(self, on):
        # the finish notice may switch the light off at night, whatever the hour of the test
        self.calls["light"] += 1


@pytest.fixture
def mr_bot(card_bot):                                                  # noqa: F811
    card_bot.cfg.update({"backend": "moonraker", "moonraker_allow_job_control": True,
                         "moonraker_allow_remote_start": True,
                         "moonraker_allow_file_delete": True,
                         "moonraker_allow_hardware_controls": True})
    card_bot.backend_name = backend.MOONRAKER
    card_bot.moonraker = FakeMoonraker()
    return card_bot


def pages_of(bot, fake, first_data):
    """Every page of a paged screen, following the ‹ › row like the card."""
    handlers.handle_widget_query(bot, widget_query(first_data, qid="pg-0"))
    out = [last_answer(fake)["sc"]]
    while True:
        nxt = [b[1] for b in out[-1]["k"][-1] if b[0] == "›"]
        if not nxt:
            return out
        handlers.handle_widget_query(bot, widget_query(nxt[0], qid="pg-%d" % len(out)))
        out.append(last_answer(fake)["sc"])


def labels(pages):
    return [b[0] for sc in pages for row in sc["k"] for b in row
            if not b[1].startswith("pg:") and not re.fullmatch(r"\d+/\d+", b[0])]


# ------------------------------------------------------------ 1a: pages

def test_files_are_paged_one_row_per_file_with_every_label_distinct(mr_bot, fake_telegram):
    pages = pages_of(mr_bot, fake_telegram, "c:files")
    assert len(pages) > 1
    for sc in pages:
        assert len(json.dumps({"sc": sc}, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8"))             <= widget.ANSWER_LIMIT
        assert sc["x"] == []                         # the names are on the buttons
        assert sc["t"].startswith("📂 Файлы на принтере · 8")
    # every file: its print and its bin button in ONE row, on the same page
    rows = [row for sc in pages for row in sc["k"]]
    file_rows = [row for row in rows if row[0][0].startswith("🖨")]
    assert len(file_rows) == 8
    for i, row in enumerate(file_rows, 1):
        assert "_v%d_" % i in row[0][0] and row[0][1].startswith("c:ask:print:")
        assert row[1][0] == "🗑" and row[1][1].startswith("c:ask:delete:")
    shown = labels(pages)
    own = [label for label in shown if label != "🗑"]
    assert len(own) == len(set(own))
    # "delete all" and "back" are rows of their own, at the end
    assert rows[-3 + 0][0][1].startswith("c:ask:delall:") or any(
        r[0][1].startswith("c:ask:delall:") for r in rows)
    assert [b[1] for b in pages[-1]["k"][-2]] == ["b"]
    assert fake_telegram.sent == [] and fake_telegram.edited == []


def test_bin_button_asks_first_and_never_deletes(mr_bot, fake_telegram):
    handlers.handle_widget_query(mr_bot, widget_query("c:files"))
    sc = last_answer(fake_telegram)["sc"]
    code = next(b[1] for row in sc["k"] for b in row if b[0] == "🗑")
    handlers.handle_widget_query(mr_bot, widget_query(code, qid="wq-2"))
    ask = last_answer(fake_telegram)["sc"]
    assert mr_bot.moonraker.deleted == []
    assert "Удалить файл" in ask["t"]
    yes = next(b for row in ask["k"] for b in row if b[2] == "y")
    assert yes[1].startswith("c:do:delete:")
    handlers.handle_widget_query(mr_bot, widget_query(yes[1], qid="wq-3"))
    assert len(mr_bot.moonraker.deleted) == 1            # only the second step deletes


def test_pages_stay_short_enough_for_a_widget_bubble():
    rows = [[["🖨 файл %d" % i, "c:ask:print:t%d" % i, ""], ["🗑", "c:ask:delete:t%d" % i, "k"]]
            for i in range(30)] + [[["↩️ Назад", "b", ""]]]
    lines = ["строка %d " % i + "ж" * 200 for i in range(40)]
    for sc in ({"t": "Файлы", "x": [], "k": rows},
               {"t": "Текст", "x": lines, "k": rows[:3]}):
        pages = [json.loads(p)["sc"] for p in widget.paginate(sc, "tok", "заметка")]
        assert len(pages) > 1
        for n, page in enumerate(pages):
            assert len(page["x"]) <= widget.MAX_LINES
            assert len(page["k"]) <= widget.MAX_ROWS     # the nav row included
            assert all(len(line) <= widget.LINE_WIDTH for line in page["x"])
            nav = page["k"][-1]
            assert nav[len(nav) // 2 if len(nav) == 3 else (0 if n == 0 else 1)][0]                 == "%d/%d" % (n + 1, len(pages))
        assert pages[0]["k"][-1][-1][1] == "pg:tok:1"
        assert pages[-1]["k"][-1][0][1] == "pg:tok:%d" % (len(pages) - 2)


def test_small_screen_is_one_page_without_a_nav_row():
    sc = {"t": "Т", "x": ["a", "b"], "k": [[["ok", "b", ""]]]}
    [page] = widget.paginate(sc, "tok")
    assert json.loads(page)["sc"]["k"] == [[["ok", "b", ""]]]
    many = {"t": "Т", "x": ["a"] * 9, "k": []}           # too many lines for one bubble
    assert len(widget.paginate(many, "tok")) == 2


def test_nav_row_has_arrows_only_where_there_is_a_page(mr_bot, fake_telegram):
    pages = pages_of(mr_bot, fake_telegram, "c:files")
    total = len(pages)
    first, last = pages[0]["k"][-1], pages[-1]["k"][-1]
    assert [b[0] for b in first] == ["1/%d" % total, "›"]
    assert [b[0] for b in last] == ["‹", "%d/%d" % (total, total)]
    tok = first[1][1].split(":")[1]
    assert first[1][1] == "pg:%s:1" % tok and last[0][1] == "pg:%s:%d" % (tok, total - 2)
    assert all(len(b[1].encode("utf-8")) <= widget.QUERY_LIMIT for sc in pages
               for row in sc["k"] for b in row)


def test_stale_or_unknown_page_says_so(mr_bot, fake_telegram):
    pages = pages_of(mr_bot, fake_telegram, "c:files")
    tok = pages[0]["k"][-1][1][1].split(":")[1]
    handlers.handle_widget_query(mr_bot, widget_query("pg:nope:0"))
    assert last_answer(fake_telegram)["m"] == "Экран устарел, открой заново"
    mr_bot.clock_control.now += widget.SCREEN_TTL_SEC + 1
    handlers.handle_widget_query(mr_bot, widget_query("pg:%s:1" % tok))
    assert "устарел" in last_answer(fake_telegram)["m"]


def test_screen_store_keeps_at_most_sixteen(card_bot):
    toks = [card_bot.store_screen({"t": str(i), "x": [], "k": []}) for i in range(20)]
    assert len(card_bot.screens) == widget.SCREENS_KEPT
    assert card_bot.get_screen(toks[0]) is None and card_bot.get_screen(toks[-1])


def test_paginate_never_makes_labels_alike():
    rows = [[["🖨 ECC_0.2_Сборка_Creality CR-PETG_0.1_%dh14m.gcode" % i, "c:ask:print:t%d" % i, ""]]
            for i in range(1, 19)]
    sc = {"t": "Файлы", "x": [], "k": rows}
    pages = widget.paginate(sc, "tok")
    assert pages and all(len(p.encode("utf-8")) <= widget.ANSWER_LIMIT for p in pages)
    shown = [b[0] for p in pages for row in json.loads(p)["sc"]["k"] for b in row
             if not b[1].startswith("pg:") and "/" not in b[0]]
    assert len(shown) == len(set(shown)) == 18
    # even a label that has to be shortened keeps its end
    assert widget.middle("ECC_0.2_Сборка_Creality CR-PETG_0.1_2h14m.gcode", 30).endswith("14m.gcode")


def test_kb_files_labels_keep_the_end_of_the_name():
    names = ["/local/ECC_0.2_Сборка_Creality CR-PETG_0.1_%dh.gcode" % i for i in (2, 3, 4)]
    rows = ui.kb_files(names, can_start=True, refs=["a", "b", "c"], can_delete=True,
                       delete_refs=["d", "e", "f"])
    texts = [row[0]["text"] for row in rows[:-1] if row[0]["text"].startswith(("🖨", "🗑"))]
    assert len(texts) == len(set(texts)) == 6


# ------------------------------------------------------------ 1b / 1c: figures

def decode(fig):
    """What the card's script does with the compact figure: (number, x0, y0,
    x1, y1, state) in 4 mm cells, skipping what is not drawn."""
    out = []
    for n, state in enumerate(fig["s"]):
        if state == "3":
            continue
        cells = [objectmap.ALPHABET.index(c) for c in fig["b"][4 * n:4 * n + 4]]
        out.append((n + 1, *cells, int(state)))
    return out


def test_objects_figure_coordinates_and_states():
    names = ["A", "B", "C"]
    shapes = {"A": {"polygon": [(10, 10), (60, 10), (60, 60), (10, 60)]},
              "B": {"polygon": [(100, 100), (150, 100), (150, 150), (100, 150)]}}
    fig = objectmap.figure(names, shapes, excluded=["B"], current="A")
    assert fig["s"] == "213"                   # printing, excluded, not drawn
    assert len(fig["b"]) == 4 * len(names)
    # 4 mm cells (63 over the bed), y from the top (the back wall)
    assert decode(fig) == [(1, 2, 48, 15, 61, 2), (2, 24, 26, 37, 39, 1)]
    assert decode(objectmap.figure(["A"], {"A": shapes["A"]}))[0][-1] == 0
    far = {"A": {"polygon": [(-20, -5), (400, -5), (400, 400)]}}
    assert decode(objectmap.figure(["A"], far)) == [(1, 0, 0, 63, 63, 0)]
    tiny = {"A": {"polygon": [(100, 100), (101, 100), (101, 101)]}}
    [(_, x0, y0, x1, y1, _)] = decode(objectmap.figure(["A"], tiny))
    assert x1 > x0 and y1 > y0                             # never an empty box
    with pytest.raises(ValueError):
        objectmap.figure(["A"], {})


def test_widget_alphabet_is_the_bots_alphabet():
    assert "'%s'" % objectmap.ALPHABET in widget.HTML


def test_objects_on_the_card_are_drawn_not_sent_as_a_picture(mr_bot, fake_telegram):
    mr_bot.status = status(13, "x.gcode", progress=50)
    handlers.handle_widget_query(mr_bot, widget_query("c:objects"))
    sc = last_answer(fake_telegram)["sc"]
    assert fake_telegram.sent == []                       # no PNG
    assert decode(sc["g"])[0][0] == 1 and decode(sc["g"])[0][-1] == 2
    assert sc["t"] == "✂️ Убрать объект" and sc["x"] == ["Осталось 2 из 3"]
    codes = [b[1] for row in sc["k"] for b in row]
    assert sum(c.startswith("c:ask:exclude:") for c in codes) == 2


def test_objects_outside_card_mode_are_still_a_png(mr_bot, fake_telegram):
    mr_bot.cfg["status_widget"] = False
    handlers.show_objects(mr_bot, OWNER, force_new=True)
    assert fake_telegram.sent[-1][3] is True              # sent with a photo
    assert fake_telegram.widgets[-1:] == fake_telegram.widgets[:1]   # no new card


def test_mesh_figure_flips_rows_and_uses_integers():
    fig = heightmap.figure([[0.0, 0.1], [0.2, 0.3]])["m"]
    # Klipper's first row is the front edge; the picture's first row the back
    assert fig == {"w": 2, "h": 2, "z": [200, 300, 0, 100], "lo": 0.0, "hi": 0.3}
    assert all(isinstance(v, int) for v in fig["z"])
    with pytest.raises(ValueError):
        heightmap.figure([[0.0, 0.1], [0.2]])


def test_mesh_on_the_card_is_drawn(mr_bot, fake_telegram):
    handlers.handle_widget_query(mr_bot, widget_query("c:mesh"))
    sc = last_answer(fake_telegram)["sc"]
    assert sc["g"]["m"]["z"] == [200, 300, 0, 100] and fake_telegram.sent == []
    assert sc["t"] == "🗺 Карта высот стола" and len(sc["x"]) == 2


# ------------------------------------------------------------ 1d: screens the bot opens

def command(bot, text):
    handlers.handle_message(bot, {"chat": {"id": OWNER}, "text": text,
                                  "from": {"id": OWNER, "is_bot": False}})


@pytest.mark.parametrize("cmd", ["/files", "/plan", "/mesh", "/history", "/macros",
                                 "/help", "/diag"])
def test_commands_in_card_mode_are_a_card_with_go(mr_bot, fake_telegram, cmd):
    sent_before = len(fake_telegram.sent)
    command(mr_bot, cmd)
    assert len(fake_telegram.sent) == sent_before         # no message
    data = card_of(fake_telegram.widgets[-1]["chat_data"])
    assert data["go"]
    handlers.handle_widget_query(mr_bot, widget_query("pg:%s:0" % data["go"]))
    sc = last_answer(fake_telegram)["sc"]
    assert sc["t"] and fake_telegram.widgets[-1]["caption"]
    assert "<" not in re.sub(r"</?b>", "", fake_telegram.widgets[-1]["caption"])


def test_go_is_cleared_after_the_card_took_it(mr_bot, fake_telegram):
    command(mr_bot, "/files")
    tok = card_of(fake_telegram.widgets[-1]["chat_data"])["go"]
    handlers.handle_widget_query(mr_bot, widget_query("pg:%s:0" % tok))
    mr_bot.clock_control.now += 11
    mr_bot.status = printing(progress=50)
    mr_bot.push_widget()
    assert "go" not in card_of(fake_telegram.widget_edits[-1][1])


def test_unserved_go_survives_pushes_until_it_expires(mr_bot, fake_telegram):
    command(mr_bot, "/files")
    mr_bot.clock_control.now += 11
    mr_bot.status = printing(progress=50)
    mr_bot.push_widget()
    assert "go" in card_of(fake_telegram.widget_edits[-1][1])
    mr_bot.clock_control.now += widget.SCREEN_TTL_SEC + 20
    mr_bot.push_widget()
    assert "go" not in card_of(fake_telegram.widget_edits[-1][1])


def test_printer_dialog_and_notices_are_screens(mr_bot, fake_telegram):
    mr_bot._schedule_notice("⏰ Скоро печать@NL@подробности".replace("@NL@", chr(10)), None)
    data = card_of(fake_telegram.widgets[-1]["chat_data"])
    assert mr_bot.get_screen(data["go"])["t"] == "⏰ Скоро печать"
    mr_bot._report_macro_done("SAVE_CALIBRATION", 60)
    assert card_of(fake_telegram.widgets[-1]["chat_data"])["go"] != data["go"]
    assert fake_telegram.sent == []


def test_help_screen_carries_the_support_note_as_a_screen(mr_bot, fake_telegram):
    storage.update_state(installed_at=1_700_000_000 - 90 * 86400)
    command(mr_bot, "/help")
    tok = card_of(fake_telegram.widgets[-1]["chat_data"])["go"]
    codes = [b[1] for row in mr_bot.get_screen(tok)["k"] for b in row]
    assert any(c.startswith("u:https://") for c in codes)
    assert fake_telegram.sent == []


# ------------------------------------------------------------ 1e: events

def test_finish_in_card_mode_is_a_picture_and_a_card(mr_bot, fake_telegram):
    mr_bot.print_frame = b"\xff\xd8frame"
    mr_bot.status = status(9, "a.gcode")
    snapshot = {"Filename": "a.gcode", "CurrentTicks": 600, "TotalLayer": 10}
    mr_bot.announce(ps.Event(ps.FINISHED, 9, snapshot))
    [(_, caption, keyboard, has_photo)] = fake_telegram.sent
    assert has_photo and keyboard is None and "Печать закончена" in caption
    assert fake_telegram.widgets[-1]["caption"].startswith("✅ <b>Печать закончена</b>")
    assert storage.message_kind() == widget.KIND


def test_finish_with_the_support_note_opens_a_screen(mr_bot, fake_telegram):
    storage.update_state(installed_at=1_700_000_000 - 90 * 86400)
    mr_bot.status = status(9, "a.gcode")
    mr_bot.announce(ps.Event(ps.FINISHED, 9, {"Filename": "a.gcode", "CurrentTicks": 60}))
    tok = card_of(fake_telegram.widgets[-1]["chat_data"])["go"]
    assert any(b[1].startswith("u:") for row in mr_bot.get_screen(tok)["k"] for b in row)
    assert fake_telegram.sent == []
    assert storage.load_state().get("last_support_reminder_at")


def test_connection_lost_and_back_show_the_card(mr_bot, fake_telegram):
    mr_bot.online = False
    mr_bot.show_connection_lost(75)
    assert fake_telegram.sent == []
    assert "потеряна" in fake_telegram.widgets[-1]["caption"]
    assert card_of(fake_telegram.widgets[-1]["chat_data"])["s"] == "Нет связи"
    mr_bot.online = True
    mr_bot.show_connection_restored()
    assert "восстановлена" in fake_telegram.widgets[-1]["caption"]


# ------------------------------------------------------------ no keyboards anywhere

def test_no_inline_keyboard_and_no_stray_message_in_card_mode(mr_bot, fake_telegram):
    seen = []
    send, edit = fake_telegram.send_message, fake_telegram.edit_message

    def send_spy(chat, text, keyboard=None, photo=None):
        seen.append(("send", text, keyboard, bool(photo)))
        return send(chat, text, keyboard=keyboard, photo=photo)

    def edit_spy(chat, mid, text, keyboard=None, photo=None, is_photo=False):
        seen.append(("edit", text, keyboard, bool(photo)))
        return edit(chat, mid, text, keyboard=keyboard, photo=photo, is_photo=is_photo)

    fake_telegram.send_message, fake_telegram.edit_message = send_spy, edit_spy
    mr_bot.cfg["send_photo"] = True
    mr_bot.grab = lambda max_age=0: b"\xff\xd8frame"
    mr_bot.print_frame = b"\xff\xd8frame"
    mr_bot.moonraker.list_macros = lambda: ["CALIBRATE_BED_60"]

    for data in ("c:files", "c:objects", "c:mesh", "c:history", "c:macros", "c:diag",
                 "c:menu:speed", "c:menu:fans", "c:menu:temp", "c:help", "c:plan",
                 "c:ask:stop", "ask:pause", "snap", "c:details", "c:light"):
        handlers.handle_widget_query(mr_bot, widget_query(data))
    for cmd in ("/files", "/plan", "/mesh", "/history", "/macros", "/help", "/diag",
                "/snap", "hello"):
        command(mr_bot, cmd)
    for event in (ps.Event(ps.STARTED, 13), ps.Event(ps.PAUSED, 6),
                  ps.Event(ps.PROGRESS, 13, None, 50),
                  ps.Event(ps.FINISHED, 9, {"Filename": "a.gcode", "CurrentTicks": 60}),
                  ps.Event(ps.CANCELLED, 8, {"Filename": "a.gcode"}, 40)):
        mr_bot.announce(event)
    mr_bot.show_connection_lost(80)
    mr_bot.show_connection_restored()
    mr_bot.cooldown_pending = True
    mr_bot.status = status(0)
    mr_bot._maybe_notify_cooldown(mr_bot.status)
    mr_bot._schedule_notice("⏰ напоминание", [[{"text": "Да", "callback_data": "x"}]])

    assert seen
    assert [s for s in seen if s[2]] == []                # no inline keyboard, ever
    # the only extra messages are the picture slots
    assert all(s[3] for s in seen if s[0] == "send"), [s[1] for s in seen if not s[3]]
    assert all(w["keyboard"] in ([], None) for w in fake_telegram.widgets)


# ------------------------------------------------------------ 2: faster

def test_no_telegram_call_before_the_answer_to_a_tap(mr_bot, fake_telegram):
    inside = []
    real = fake_telegram.edit_widget_data
    fake_telegram.edit_widget_data = lambda *a: (inside.append(mr_bot.capturing()), real(*a))[1]
    mr_bot.cfg["send_photo"] = True
    grabbed = []
    mr_bot.grab = lambda max_age=0: grabbed.append(1) or b"\xff\xd8f"
    for data in ("c:files", "c:refresh", "c:details", "c:brief", "c:objects", "c:mesh"):
        handlers.handle_widget_query(mr_bot, widget_query(data))
    assert [c for c in inside if c is not None] == []     # never during a capture
    assert grabbed == []                                  # no frame for a card tap


def test_reads_are_cached_and_dropped_on_change(mr_bot, fake_telegram):
    for _ in range(3):
        handlers.handle_widget_query(mr_bot, widget_query("c:files"))
        handlers.handle_widget_query(mr_bot, widget_query("c:history"))
        handlers.handle_widget_query(mr_bot, widget_query("c:objects"))
    calls = mr_bot.moonraker.calls
    assert (calls["files"], calls["history"], calls["objects"]) == (1, 1, 1)
    mr_bot.clock_control.now += 4                          # objects: 3 s
    handlers.handle_widget_query(mr_bot, widget_query("c:objects"))
    handlers.handle_widget_query(mr_bot, widget_query("c:files"))
    assert (calls["files"], calls["objects"]) == (1, 2)
    mr_bot.status = status(0)                              # idle: deleting is allowed
    mr_bot.perform(backend.DELETE, "/local/x.gcode")
    handlers.handle_widget_query(mr_bot, widget_query("c:files"))
    assert calls["files"] == 2
    mr_bot.clock_control.now += 16
    handlers.handle_widget_query(mr_bot, widget_query("c:files"))
    assert calls["files"] == 3


def test_failed_read_is_not_cached(mr_bot):
    from centauri_bot import moonraker
    boom = {"on": True}

    def read():
        if boom["on"]:
            raise moonraker.MoonrakerError("нет связи")
        return ["ok"]
    with pytest.raises(moonraker.MoonrakerError):
        mr_bot.cached("macros", read)
    boom["on"] = False
    assert mr_bot.cached("macros", read) == ["ok"]


def test_unchanged_caption_is_not_sent_again(card_bot, fake_telegram):
    card_bot.refresh_main()
    first = len(fake_telegram.edited)
    card_bot.refresh_main()
    card_bot.refresh_main()
    assert len(fake_telegram.edited) <= first + 0 or len(fake_telegram.edited) == first
    card_bot.status = printing(progress=77)
    card_bot.refresh_main()
    assert "77%" in fake_telegram.edited[-1][2]


def test_tap_log_has_no_token(card_bot, fake_telegram, caplog):
    caplog.set_level(logging.INFO)
    handlers.handle_widget_query(card_bot, widget_query("ask:stop"))
    token = last_answer(fake_telegram)["t"]
    handlers.handle_widget_query(card_bot, widget_query("do:cancel:" + token))
    handlers.handle_widget_query(card_bot, widget_query("c:ask:print:SECRET123"))
    text = caplog.text
    assert re.search(r"card tap ask answered in \d+ ms", text)
    assert re.search(r"card tap do answered in \d+ ms", text)
    assert re.search(r"card tap c:ask answered in \d+ ms", text)
    assert token not in text and "SECRET123" not in text


def test_stats_line_counts_calls_and_polls(card_bot, caplog):
    caplog.set_level(logging.INFO)
    api = offline_api()
    api._counts.update({"sendWidget": 1, "editMessageWidgetData": 40})
    card_bot.api._api.take_counts = api.take_counts
    card_bot.note_poll(0.3)
    card_bot.note_poll(0.5)
    card_bot.clock_control.now += 601
    card_bot.log_stats()
    assert "telegram calls in 10 min: editMessageWidgetData=40 sendWidget=1" in caplog.text
    assert "moonraker poll avg 400 ms" in caplog.text


def test_telegram_call_honours_its_timeout_and_reconnects_when_idle(monkeypatch):
    import time as time_mod
    api = offline_api()
    made = []

    class Sock(object):
        def settimeout(self, value):
            self.value = value

    class Conn(object):
        sock = Sock()

        def __init__(self, *a, **k):
            made.append(self)
            self.timeout = None

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return self

        def read(self):
            return b'{"ok":true}'

        def close(self):
            pass

    monkeypatch.setattr("http.client.HTTPSConnection", Conn)
    api.call("getMe", timeout=7)
    assert made[0].timeout == 7 and made[0].sock.value == 7
    api.call("getMe")
    assert len(made) == 1                                  # still fresh: reused
    api._tls.last_used -= api.IDLE_RECONNECT_SEC + 1
    api.call("getMe")
    assert len(made) == 2                                  # idle too long: new connection
    assert api.take_counts() == {"getMe": 3}
    assert api.take_counts() == {}
    assert time_mod                                       # (module imported for clarity)


# ------------------------------------------------------------ taps that must not wait

def test_pause_answers_first_and_reports_through_the_note(card_bot, fake_telegram):
    order = []
    real = fake_telegram.answer_widget_query
    fake_telegram.answer_widget_query = lambda q, d=None: (order.append("answer"), real(q, d))[1]
    card_bot.perform = lambda action, value=None: (order.append("perform"), (False, "нет"))[1]
    handlers.handle_widget_query(card_bot, widget_query("ask:pause"))
    token = last_answer(fake_telegram)["t"]
    order.clear()
    handlers.handle_widget_query(card_bot, widget_query("do:pause:" + token))
    assert order == ["answer", "perform"]
    assert "Пауза не прошла" in card_of(fake_telegram.widget_edits[-1][1])["n"]


def test_work_queue_runs_follow_ups_off_the_tap(card_bot):
    import queue
    card_bot.work = queue.Queue()
    ran = []
    card_bot.defer(lambda: ran.append(1))
    assert ran == [] and card_bot.work.qsize() == 1        # queued, not run in the tap


def test_failed_snapshot_is_a_note_not_a_message(card_bot, fake_telegram):
    card_bot.cfg["send_photo"] = True
    card_bot.grab = lambda max_age=0: None
    handlers.handle_widget_query(card_bot, widget_query("snap"))
    assert fake_telegram.sent == []
    assert "Кадр получить не вышло" in card_of(fake_telegram.widget_edits[-1][1])["n"]


# ------------------------------------------------------------ the state itself

def fullest_keyboard(bot):
    bot.cfg.update({"backend": "moonraker", "moonraker_allow_job_control": True,
                    "moonraker_allow_remote_start": True,
                    "moonraker_allow_file_delete": True,
                    "moonraker_allow_hardware_controls": True,
                    "moonraker_macro_whitelist": ["SAVE_CALIBRATION", "CALIBRATE_BED_60"],
                    "send_photo": True})
    bot.backend_name = backend.MOONRAKER
    bot.moonraker = FakeMoonraker()
    storage.save_schedule([{"id": "j1", "path": "/a.gcode", "at": 1_700_100_000,
                            "created": 1_700_000_000}])
    storage.save_maintenance({"hours": 999, "since": 1_000_000_000})


def test_fullest_menu_and_a_long_name_still_fit_chat_data(card_bot):
    fullest_keyboard(card_bot)
    card_bot.status = status(13, "Очень_длинное_имя_файла_" * 10 + ".gcode", progress=5)
    data = card_bot._card()
    assert data["mn"]
    text = widget.encode(data)
    assert len(text.encode("utf-8")) <= widget.DATA_LIMIT
    # worst case: a long note and go as well; the print's own numbers survive
    data.update(n="ж" * 300, go="abcdefgh")
    text = widget.encode(data)
    assert len(text.encode("utf-8")) <= widget.DATA_LIMIT
    assert card_of(text)["p"] == 5 and card_of(text)["v"] == widget.VERSION


def test_encode_sheds_the_menu_when_nothing_else_helps():
    data = widget.card(printing(), True, "P", 1_700_000_000, keys="PSC",
                       items=[["ж" * 60, "c:" + "x" * 50] for _ in range(40)])
    text = widget.encode(data)
    assert len(text.encode("utf-8")) <= widget.DATA_LIMIT
    assert card_of(text)["s"] == "Печатает"


def test_menu_skips_items_that_could_not_be_sent_back():
    keyboard = [[{"text": "ok", "callback_data": "files"},
                 {"text": "long", "callback_data": "x" * 70}]]
    assert widget.menu(keyboard) == [["ok", "files"]]


# ------------------------------------------------------------ the card shows screens

@pytest.mark.parametrize("target", ["c:menu:speed", "c:menu:fans", "c:menu:temp"])
def test_hardware_screens_come_back_with_a_title_and_buttons(card_bot, fake_telegram, target):
    """The live card once drew these empty. The answer must carry a screen
    with a title and buttons, as JSON the card can read."""
    card_bot.cfg.update({"backend": "moonraker", "moonraker_allow_hardware_controls": True})
    card_bot.backend_name = backend.MOONRAKER
    card_bot.moonraker = FakeMoonraker()
    handlers.handle_widget_query(card_bot, widget_query(target))
    raw = fake_telegram.widget_answers[-1][1]
    assert len(raw.encode("utf-8")) <= widget.ANSWER_LIMIT
    sc = json.loads(raw)["sc"]
    assert sc["t"] and sc["k"] and all(isinstance(b[0], str) and b[0] for r in sc["k"] for b in r)


def test_widget_reads_an_answer_whatever_shape_it_arrives_in():
    """The card parses defensively (a string, a string inside a string, an
    object) and says what it got when a screen comes in empty."""
    html = widget.HTML
    assert "pj(" in html and "typeof a.sc=='string'" in html
    assert "пустой экран" not in html                      # the temporary diagnostic is gone
    assert "<canvas" in html and "pg:" in html and "localStorage" in html
    assert widget.html_fits()


def test_a_note_never_changes_where_the_pages_break():
    rows = [[["🖨 файл %d" % i, "c:ask:print:t%d" % i, ""], ["🗑", "c:ask:delete:t%d" % i, "k"]]
            for i in range(20)]
    sc = {"t": "Файлы", "x": [], "k": rows}
    plain = [json.loads(p)["sc"] for p in widget.paginate(sc, "tok")]
    noted = [json.loads(p)["sc"] for p in widget.paginate(sc, "tok", "Читаю список…")]
    assert plain == noted


def test_script_is_one_closure_and_has_no_global_named_like_a_window_property():
    """window.screen, window.status, window.menu... are built in: a global
    function of that name does not replace them in every sandbox, and the
    card's screens were drawn by a call that threw."""
    script = widget.HTML.split("<script>")[1].split("</script>")[0].strip()
    assert script.startswith("(function(){") and script.endswith("})();")
    assert not re.search(r"\bfunction\s+screen\b|\bscreen\s*\(", script)
    assert "drawScreen" in script
    top = {"screen", "status", "name", "close", "open", "print", "history", "location",
           "top", "parent", "frames", "length", "origin", "event", "menu", "keys"}
    declared = set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)", script))
    declared |= set(re.findall(r"\bvar\s+([A-Za-z_$][\w$]*)", script))
    assert not declared & top, declared & top
    # errors are shown in the note line, not swallowed by the promise
    assert "'ошибка: '" in script and script.count("catch(e){er(e)}") >= 4
    # no inline handlers in the markup: everything is attached by the script
    markup = widget.HTML.split("<script>")[0]
    assert not re.search(r"\son\w+\s*=", markup)
    assert widget.html_fits()


def test_html_keeps_a_safety_margin_under_the_sendwidget_limit():
    """sendWidget refuses code above 4096 bytes after zlib -9, and a card
    already sent can never be repaired. The hard limit is checked elsewhere;
    this keeps ~200 bytes of room, so a one-line change cannot push the next
    release over it."""
    import zlib
    assert len(zlib.compress(widget.HTML.encode("utf-8"), 9)) <= 3900


# ------------------------------------------------------------ round 5

def big_job(mr_bot, count=56, excluded=(3, 20, 41), current=10):
    """A job of many small parts spread over the bed, with real-size outlines."""
    names = ["Part_%02d.STL_ID_%d" % (i, i) for i in range(1, count + 1)]
    shapes = {}
    for i, name in enumerate(names):
        x, y = 10 + 30 * (i % 8), 10 + 30 * (i // 8)
        shapes[name] = {"polygon": [(x, y), (x + 20, y), (x + 20, y + 20), (x, y + 20)]}
    state = {"Objects": names, "ExcludedObjects": [names[i - 1] for i in excluded],
             "CurrentObject": names[current - 1], "PrintState": "printing",
             "Filename": "x.gcode", "Shapes": shapes}
    mr_bot.moonraker.exclude_object_state = lambda: state
    return names, shapes, state


def test_a_job_of_56_objects_keeps_its_drawing_on_page_one(mr_bot, fake_telegram):
    names, shapes, state = big_job(mr_bot)
    expected = objectmap.figure(names, shapes, state["ExcludedObjects"], names[9])
    assert len(json.dumps(expected, separators=(",", ":"))) < 400      # was 1396 bytes
    handlers.handle_widget_query(mr_bot, widget_query("c:objects"))
    first = fake_telegram.widget_answers[-1][1]
    assert len(first.encode("utf-8")) <= widget.ANSWER_LIMIT
    page0 = json.loads(first)["sc"]
    assert page0["g"] == expected                                      # not dropped
    drawn = decode(page0["g"])
    assert len(drawn) == 56
    assert [d[-1] for d in drawn if d[0] in (3, 20, 41)] == [1, 1, 1]
    assert [d[0] for d in drawn if d[-1] == 2] == [10]
    # the first box: x 10..30 mm, y 10..30 mm -> cells, y from the top
    assert drawn[0][:5] == (1, 2, 63 - 8, 8, 63 - 2)
    pages = pages_of(mr_bot, fake_telegram, "c:objects")
    assert len(pages) > 1 and "g" in pages[0] and all("g" not in p for p in pages[1:])
    for sc in pages:
        assert len(json.dumps({"sc": sc}, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")) <= widget.ANSWER_LIMIT
        assert len(sc["k"]) <= widget.MAX_ROWS
    exclude = [b for sc in pages for row in sc["k"] for b in row
               if b[1].startswith("c:ask:exclude:")]
    assert len(exclude) == 53                                          # 56 minus 3 gone
    shown = labels(pages)
    assert len(shown) == len(set(shown))


def test_a_drawing_that_cannot_fit_is_thinned_then_dropped_with_a_log(caplog):
    caplog.set_level(logging.INFO)
    rows = [[["❌ 1 · деталь", "c:ask:exclude:abc", ""]]]
    huge = {"b": "ABCD" * 300, "s": "0" * 300}
    pages = widget.paginate({"t": "T", "x": [], "k": rows, "g": huge}, "tok")
    assert pages and "g" not in json.loads(pages[0])["sc"]
    assert "drawing dropped" in caplog.text
    # the excluded ones are given up first
    thin = widget.thin_figure({"b": "AAAABBBBCCCC", "s": "101"})
    assert thin == {"b": "AAAABBBB", "s": "30"}
    assert widget.thin_figure({"b": "AAAA", "s": "1"}) is None


def test_macros_on_the_card_are_a_title_and_buttons(mr_bot, fake_telegram):
    mr_bot.cfg["moonraker_macro_whitelist"] = ["PARK_NOZZLE", "CALIBRATE_BED_60"]
    mr_bot.moonraker.list_macros = lambda: ["PARK_NOZZLE", "CALIBRATE_BED_60"]
    handlers.handle_widget_query(mr_bot, widget_query("c:macros"))
    sc = last_answer(fake_telegram)["sc"]
    assert sc["t"] == "🧩 Макросы" and sc["x"] == []
    codes = [b[1] for row in sc["k"] for b in row]
    assert "c:bedcalib" in codes and any(c.startswith("c:ask:macro:") for c in codes)
    # the explanation is on the screen that asks before running
    ask = next(c for c in codes if c.startswith("c:ask:macro:"))
    handlers.handle_widget_query(mr_bot, widget_query(ask, qid="wq-2"))
    confirm = last_answer(fake_telegram)["sc"]
    assert any("Что произойдёт" in line for line in confirm["x"])
    handlers.handle_widget_query(mr_bot, widget_query("c:bedcalib", qid="wq-3"))
    temps = last_answer(fake_telegram)["sc"]
    assert temps["t"] == "📐 Калибровка стола" and temps["x"] == []
    # outside card mode the text is what it was
    mr_bot.cfg["status_widget"] = False
    handlers.show_macros(mr_bot, OWNER, force_new=True)
    assert "Что можно запустить" in fake_telegram.sent[-1][1]


@pytest.mark.parametrize("kind", [{"type": "sign_data"}, {"type": "sent_transaction"},
                                  {}, None, "data"])
def test_only_a_buttons_own_data_is_acted_on(card_bot, fake_telegram, kind):
    query = widget_query("ask:stop")
    query.pop("type")
    if kind is not None:
        query["type"] = kind
    answers_before = len(fake_telegram.widget_answers)
    handlers.handle_widget_query(card_bot, query)
    assert len(fake_telegram.widget_answers) == answers_before + 1
    assert last_answer(fake_telegram) == {"m": "Неизвестная кнопка."}
    assert card_bot.commands == [] and fake_telegram.sent == []


def test_a_data_query_is_still_answered(card_bot, fake_telegram):
    handlers.handle_widget_query(card_bot, widget_query("ask:stop"))
    assert "t" in last_answer(fake_telegram)


def test_html_changes_must_come_with_a_new_version():
    """A sent widget keeps its HTML for good. The bot replaces an old card only
    when the version in storage.message_kind() differs, so every change of the
    HTML needs a new VERSION. The hash makes forgetting that impossible."""
    import hashlib
    digest = hashlib.sha256(widget.HTML.encode("utf-8")).hexdigest()[:12]
    assert digest == widget.HTML_SHA, "HTML changed: bump VERSION and update HTML_SHA"
    assert widget.KIND == "widget-%d" % widget.VERSION == "widget-7"
