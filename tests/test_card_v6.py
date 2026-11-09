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
    file_rows = [row for row in rows if row[0][1].startswith("c:ask:print:")]
    assert len(file_rows) == 8
    for i, row in enumerate(file_rows, 1):
        assert "_v%d_" % i in row[0][0] and row[0][3] == "pr"           # plain name + printer icon
        assert row[1][1].startswith("c:ask:delete:") and row[1][3] == "de"
        assert row[1][0] == "🗑"                       # nothing left to strip: the emoji stays
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
    assert decode(objectmap.figure(["A"], {"A": shapes["A"]}))[0][-1] == 4      # selected, to print
    assert decode(objectmap.figure(["A", "B"], shapes, current="B", highlight={"A"}))[0][-1] == 4
    # zoomed to A's window, B (far away) is not in the picture at all
    assert [d[-1] for d in decode(objectmap.figure(["A", "B"], shapes, highlight={"A"}))] == [4]
    assert [d[-1] for d in decode(objectmap.figure(["A", "B"], shapes, highlight=set()))] == [0, 0]
    far = {"A": {"polygon": [(-20, -5), (400, -5), (400, 400)]}}
    assert decode(objectmap.figure(["A"], far)) == [(1, 0, 0, 63, 63, 4)]
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
    # three parts, B already gone: two models are still to print -> the models first
    assert sc["t"] == "✂️ Убрать объект" and sc["x"] == ["Осталось 2 из 3 · моделей 2"]
    assert "g" not in sc                                  # no map on the list of models
    codes = [b[1] for row in sc["k"] for b in row]
    assert sum(c.startswith("c:objgrp:") for c in codes) == 2
    handlers.handle_widget_query(mr_bot, widget_query(next(c for c in codes if "objgrp" in c),
                                                      qid="wq-2"))
    level2 = last_answer(fake_telegram)["sc"]
    assert level2["t"] == "✂️ A" and decode(level2["g"])[0][-1] == 2    # A is printing now
    assert [b[0] for row in level2["k"] for b in row if b[1].startswith("c:xo:")] == ["▶ 1"]


def test_objects_outside_card_mode_are_still_a_png(mr_bot, fake_telegram):
    mr_bot.cfg["status_widget"] = False
    handlers.show_objects(mr_bot, OWNER, force_new=True)
    assert fake_telegram.sent[-1][3] is True              # sent with a photo
    assert fake_telegram.widgets[-1:] == fake_telegram.widgets[:1]   # no new card


def test_mesh_figure_flips_rows_and_uses_integers():
    fig = heightmap.figure([[0.0, 0.1], [0.2, 0.3]])["m"]
    # Klipper's first row is the front edge; the picture's first row the back
    assert fig == {"w": 2, "h": 2, "z": "66990033"}    # two digits each, 00..99 of the spread
    assert isinstance(fig["z"], str)                  # a string: no list of numbers
    with pytest.raises(ValueError):
        heightmap.figure([[0.0, 0.1], [0.2]])


def test_mesh_on_the_card_is_drawn(mr_bot, fake_telegram):
    handlers.handle_widget_query(mr_bot, widget_query("c:mesh"))
    sc = last_answer(fake_telegram)["sc"]
    assert sc["g"]["m"]["z"] == "66990033" and fake_telegram.sent == []
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
    assert widget.menu(keyboard) == [["ok", "files", "fi"]]


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
    assert "JSON.parse(r.result)" in html
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
    this keeps ~100 bytes of room, so a one-line change cannot push the next
    release over it. (It was 3900 until the icon set needed the room.)"""
    import zlib
    assert len(zlib.compress(widget.HTML.encode("utf-8"), 9)) <= 4000


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


def test_a_drawing_that_cannot_fit_is_thinned_then_dropped_with_a_log(caplog):
    caplog.set_level(logging.INFO)
    rows = [[["❌ 1 · деталь", "c:ask:exclude:abc", ""]]]
    huge = {"b": "ABCD" * 300, "s": "0" * 300}
    pages = widget.paginate({"t": "T", "x": [], "k": rows, "g": huge}, "tok")
    assert pages and "g" not in json.loads(pages[0])["sc"]
    assert "drawing dropped" in caplog.text
    # the excluded ones are given up first
    thin = widget.thin_figure({"b": "AAAABBBBCCCC", "s": "101"})
    assert thin == {"b": "AAAABBBB", "s": "30", "n": ""}
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
    assert widget.KIND == "widget-%d" % widget.VERSION == "widget-12"


# ------------------------------------------------------------ round 6

def test_action_row_never_cuts_a_label():
    """At 320 px four buttons got ~65 px each and "⏸ Пауза" was clipped. The
    row now wraps, its buttons keep their full label, and below 400 px (a
    phone's bubble) it is two by two; wider, one row of four."""
    css = widget.HTML.split("</style>")[0]
    assert re.search(r"\.a,\.wr\{flex-wrap:wrap\}", css)
    assert re.search(r"\.a button,\.wr button\{min-width:max-content;", css)
    assert re.search(r"@media\(max-width:399px\)\{\.a button\{flex:1 1 40%\}\}", css)
    # rows of three or more short buttons (speed, fans, pager) wrap too
    assert "'rw'+(r.length>2?' wr':'')" in widget.HTML
    # nothing clips the four of them: no ellipsis rule beats min-width on .a
    assert not re.search(r"\.a button\{[^}]*text-overflow", css)


def test_toast_of_a_screen_stays_off_the_note_line(mr_bot, fake_telegram):
    handlers.handle_widget_query(mr_bot, widget_query("c:files"))
    answer = last_answer(fake_telegram)
    assert "sc" in answer and "m" not in answer              # no "Читаю список…"
    # a result is kept when the tap closes back to the card
    handlers.handle_widget_query(mr_bot, widget_query("c:maint:done", qid="wq-2"))
    done = last_answer(fake_telegram)
    assert done.get("close") == 1 and "Счётчик" in done["m"]


def test_history_lines_start_with_the_state_and_the_time(mr_bot, fake_telegram):
    mr_bot.moonraker.history = lambda: [
        {"filename": "dir/Очень_длинное_имя_модели_для_проверки_обрезки.gcode",
         "status": "completed", "total_duration": 4800},
        {"filename": "b.gcode", "status": "cancelled", "total_duration": 600},
        {"filename": "c.gcode", "status": "klippy_shutdown", "total_duration": 60}]
    handlers.handle_widget_query(mr_bot, widget_query("c:history"))
    sc = last_answer(fake_telegram)["sc"]
    assert sc["x"][0].startswith("✅ 1 ч 20 мин · Очень_длинное")
    assert sc["x"][1] == "⏹ 10 мин · b.gcode" and sc["x"][2].startswith("⚠️ 1 мин · c")
    # outside the card the line is what it was
    assert "• <i>b.gcode</i> · cancelled · 10 мин" in ui.history_text(
        [{"filename": "b.gcode", "status": "cancelled", "total_duration": 600}])


def test_files_header_says_how_many_are_shown(mr_bot, fake_telegram):
    handlers.handle_widget_query(mr_bot, widget_query("c:files"))
    assert last_answer(fake_telegram)["sc"]["t"] == "📂 Файлы на принтере · 8"
    mr_bot.moonraker.paths = ["/local/f%02d.gcode" % i for i in range(18)]
    mr_bot.clock_control.now += 20                           # past the 15 s read cache
    handlers.handle_widget_query(mr_bot, widget_query("c:files", qid="wq-2"))
    assert last_answer(fake_telegram)["sc"]["t"] == "📂 Файлы на принтере · последние 8 из 18"


def test_outdated_card_is_replaced_while_the_printer_is_idle(live_bot, fake_telegram):
    live_bot.status = status(0)
    storage.set_message_id(500, kind="widget-6")
    live_bot.push_widget()
    [sent] = fake_telegram.widgets
    assert sent["silent"] is True and (live_bot.owner, 500) in fake_telegram.deleted
    assert storage.message_kind() == widget.KIND and storage.message_id() != 500
    live_bot.clock_control.now += 30
    live_bot.push_widget()
    assert len(fake_telegram.widgets) == 1                  # not replaced again


def test_outdated_card_is_tried_once_and_not_in_a_loop(live_bot, fake_telegram):
    live_bot.status = status(0)
    fake_telegram.widget_answer = {"ok": False, "description": "Bad Request: METHOD_INVALID"}
    storage.set_message_id(500, kind="widget-6")
    for _ in range(4):
        live_bot.clock_control.now += 30
        live_bot.push_widget()
    assert len(fake_telegram.widgets) == 1
    assert storage.message_id() == 500                       # the old card is kept


# ------------------------------------------------------------ the finish frame

def moon_status(progress, z, code=13, task="job-1", **extra):
    return status(code, "part.gcode", progress=progress, task=task,
                  CurrentCoord={"X": 100.0, "Y": 100.0, "Z": z}, **extra)


@pytest.fixture
def frame_bot(bot, fake_telegram):
    """A COSMOS-backed bot with a camera that hands out frames A, B, C, D in
    turn (only the print-frame capture asks without max_age), and a record of
    every picture sent."""
    bot.backend_name = backend.MOONRAKER
    bot.cfg["light_off_at_night"] = False       # the test must not depend on the hour
    bot.online = True
    bot.frames = [b"\xff\xd8A", b"\xff\xd8B", b"\xff\xd8C", b"\xff\xd8D", b"\xff\xd8E"]
    bot.taken = []

    def grab(max_age=0):
        if max_age:
            return b"\xff\xd8other"
        bot.taken.append(bot.frames[len(bot.taken)])
        return bot.taken[-1]
    bot.grab = grab
    bot.pictures = []
    real = fake_telegram.send_message

    def spy(chat, text, keyboard=None, photo=None):
        bot.pictures.append((text, photo))
        return real(chat, text, keyboard=keyboard, photo=photo)
    fake_telegram.send_message = spy
    return bot


def step(bot, st, seconds=100):
    bot.clock_control.now += seconds
    bot._handle_status(st)


def finish_picture(bot):
    return next(photo for text, photo in bot.pictures if "Печать закончена" in text)


def test_the_finish_picture_is_the_last_frame_before_the_bed_dropped(frame_bot):
    step(frame_bot, moon_status(50, 10.0))                      # frame A
    step(frame_bot, moon_status(98, 10.2))                      # frame B
    assert frame_bot.taken == [b"\xff\xd8A", b"\xff\xd8B"]
    step(frame_bot, moon_status(100, 40.2), 15)                 # the bed drops 30 mm
    step(frame_bot, moon_status(100, 40.2), 15)
    assert frame_bot.frame_frozen and len(frame_bot.taken) == 2  # C and D never taken
    step(frame_bot, moon_status(100, 40.2, code=9), 15)         # the finish code
    assert finish_picture(frame_bot) == b"\xff\xd8B"


def test_the_finish_picture_goes_through_post_picture_on_the_card(frame_bot, fake_telegram):
    frame_bot.cfg["status_widget"] = True
    step(frame_bot, moon_status(50, 10.0))
    step(frame_bot, moon_status(98, 10.2))
    step(frame_bot, moon_status(100, 40.2), 15)
    step(frame_bot, moon_status(100, 40.2, code=9), 15)
    assert finish_picture(frame_bot) == b"\xff\xd8B"
    assert fake_telegram.widgets                               # and the card follows


def test_a_z_hop_does_not_freeze_the_frame(frame_bot):
    step(frame_bot, moon_status(40, 10.0))
    step(frame_bot, moon_status(41, 10.4))                      # a Z-hop, not the bed
    step(frame_bot, moon_status(42, 10.2))
    assert not frame_bot.frame_frozen and frame_bot.z_max == 10.4
    assert frame_bot.print_frame == frame_bot.taken[-1] == b"\xff\xd8C"


def test_a_new_job_starts_again(frame_bot):
    step(frame_bot, moon_status(50, 10.0))
    step(frame_bot, moon_status(100, 40.0), 15)
    assert frame_bot.frame_frozen
    step(frame_bot, moon_status(1, 0.2, task="job-2"))
    assert not frame_bot.frame_frozen and frame_bot.z_max == 0.2
    assert frame_bot.print_frame == frame_bot.taken[-1]        # taken again


def test_the_jump_itself_is_not_the_highest_z(frame_bot):
    step(frame_bot, moon_status(50, 10.0))
    step(frame_bot, moon_status(60, 40.0), 15)
    assert frame_bot.z_max == 10.0


def test_the_cooldown_notice_repeats_the_frozen_frame(frame_bot):
    step(frame_bot, moon_status(50, 10.0))
    step(frame_bot, moon_status(98, 10.2))
    step(frame_bot, moon_status(100, 40.2), 15)
    step(frame_bot, moon_status(100, 40.2, code=9), 15)
    assert frame_bot.cooldown_pending
    cold = moon_status(100, 40.2, code=9, TempOfNozzle=30, TempTargetNozzle=0)
    frame_bot._maybe_notify_cooldown(cold)
    text, photo = frame_bot.pictures[-1]
    assert "остыл" in text and photo == b"\xff\xd8B"


def test_frames_come_every_ten_seconds_near_the_end(frame_bot):
    step(frame_bot, moon_status(50, 10.0))
    step(frame_bot, moon_status(97, 10.2))
    taken = len(frame_bot.taken)
    step(frame_bot, moon_status(97.5, 10.2), 5)                 # too soon
    assert len(frame_bot.taken) == taken
    step(frame_bot, moon_status(98, 10.2), 6)                   # 11 s since the last
    assert len(frame_bot.taken) == taken + 1
    step(frame_bot, moon_status(96, 10.2), 15)                  # 95-97 %: every 20 s
    assert len(frame_bot.taken) == taken + 1
    step(frame_bot, moon_status(96, 10.2), 6)
    assert len(frame_bot.taken) == taken + 2


def test_stock_printer_behaviour_is_unchanged(frame_bot):
    """Stock SDCP has no Z in a dict and its finish comes before the bed moves:
    no freezing, the last frame before the finish code is the one kept."""
    frame_bot.backend_name = backend.SDCP
    step(frame_bot, status(13, "part.gcode", progress=50, CurrenCoord="0,0,10"))
    step(frame_bot, status(13, "part.gcode", progress=100, CurrenCoord="0,0,40"), 15)
    step(frame_bot, status(13, "part.gcode", progress=100, CurrenCoord="0,0,45"), 15)
    assert not frame_bot.frame_frozen and frame_bot.z_max is None
    assert frame_bot.print_frame == frame_bot.taken[-1] == b"\xff\xd8C"


def test_a_lift_in_the_middle_of_a_print_does_not_freeze_the_frame(frame_bot):
    step(frame_bot, moon_status(30, 10.0))                      # frame A
    step(frame_bot, moon_status(40, 40.0))                      # filament change: +30 mm
    assert not frame_bot.frame_frozen and frame_bot.z_max == 10.0   # nor a raised threshold
    step(frame_bot, moon_status(41, 10.2))                      # back to printing: frame B
    step(frame_bot, moon_status(98, 10.4))                      # frame C
    step(frame_bot, moon_status(100, 40.4), 15)                 # the real drop
    step(frame_bot, moon_status(100, 40.4, code=9), 15)
    # frames kept refreshing through the lift; the finish one is the late, pre-drop frame
    assert len(frame_bot.taken) == 4
    assert finish_picture(frame_bot) == frame_bot.taken[-1] == frame_bot.frames[3]


def test_a_pause_park_is_ignored_for_z(frame_bot):
    step(frame_bot, moon_status(40, 10.0))
    step(frame_bot, moon_status(40, 60.0, code=6))              # paused, parked high
    assert frame_bot.z_max == 10.0 and not frame_bot.frame_frozen
    # (a paused status never even reaches the frame capture, and if one did:)
    frame_bot.capture_print_frame(96, moon_status(96, 60.0, code=6))
    assert not frame_bot.frame_frozen


def test_a_jump_at_96_percent_freezes(frame_bot):
    step(frame_bot, moon_status(90, 10.0))
    step(frame_bot, moon_status(96, 40.0))
    assert frame_bot.frame_frozen
    n = len(frame_bot.taken)
    step(frame_bot, moon_status(97, 40.0), 15)
    assert len(frame_bot.taken) == n


def test_a_jump_on_the_last_layer_freezes_before_95_percent(frame_bot):
    step(frame_bot, moon_status(80, 10.0, layer=299, total_layer=300))
    step(frame_bot, moon_status(90, 40.0, layer=300, total_layer=300))
    assert frame_bot.frame_frozen
    # an unknown layer count never makes "the last layer"
    other = moon_status(90, 40.0, layer=5, total_layer=0, task="job-9")
    frame_bot.capture_print_frame(90, moon_status(80, 10.0, task="job-9", layer=5, total_layer=0))
    frame_bot.capture_print_frame(90, other)
    assert not frame_bot.frame_frozen


def test_the_open_menu_ends_with_a_full_width_way_back():
    """"⚙️ Ещё" stays what it is while the menu is open; the way back is its own
    prominent full-width button, last in the grid."""
    html = widget.HTML
    assert "✕ Скрыть" not in html
    assert "mk(a,IC.mo?'Ещё':'⚙️ Ещё','',t,'mo')" in html                     # the toggle's label never changes
    assert "mk(g,'‹ Назад в главное','y',t)" in html          # the same handler closes the menu
    assert html.index("mn.forEach(function(i){mk(g") < html.index("'‹ Назад в главное'")
    assert ".g .y{grid-column:1/-1}" in html                 # across both columns, accent fill


# ------------------------------------------------------------ round 7: rings and a compact objects screen

def test_ring_maths_heating_at_target_cooling_and_off():
    # [now, target, fill %, hue, heating]
    assert widget.heat(180, 245, widget.NOZZLE_TOP) == [180, 245, 73, 13, 1]    # heating: pulses, amber-red
    assert widget.heat(245, 245, widget.NOZZLE_TOP) == [245, 245, 100, 0, 0]    # at target: full, red
    assert widget.heat(244, 245, widget.NOZZLE_TOP)[4] == 0                     # within 2 degrees
    assert widget.heat(250, 245, widget.NOZZLE_TOP)[2] == 100                   # never over
    cooling = widget.heat(150, 0, widget.NOZZLE_TOP)                            # target 0: of the top
    assert cooling[2] == 50 and cooling[4] == 0 and cooling[3] == 20
    assert widget.heat(30, 0, widget.NOZZLE_TOP)[3] == 210                      # cold: blue
    assert widget.heat(25, 0, widget.BED_TOP)[2] == 20 and widget.heat(25, 0, widget.BED_TOP)[3] == 210                          # bed: of 120
    assert widget.heat(0, 0, widget.BED_TOP) == [0, 0, 0, 210, 0]               # off
    hot, cool = widget.heat(100, 0, widget.BED_TOP), widget.heat(50, 0, widget.BED_TOP)
    assert hot[3] < cool[3] <= 45                                               # redder as it heats
    # a warm heater is never green or cyan: nothing between 46 and 209
    assert all(not 45 < widget.heat(t, 0, top)[3] < 210
               for top in (widget.NOZZLE_TOP, widget.BED_TOP) for t in range(0, 320))
    assert widget.heat(70, 70, widget.BED_TOP)[3] == 20 and widget.heat(180, 180, widget.NOZZLE_TOP)[3] == 13
    assert 0 <= widget.heat(70, 70, widget.BED_TOP)[3] <= 45
    assert widget.heat(None, None, widget.BED_TOP)[:2] == [0, 0]


def test_the_rings_and_their_motion_are_in_the_html():
    html = widget.HTML
    assert 'id=tn' in html and 'id=tb' in html and html.count("<svg viewBox") == 2
    # crisp rings: vector circles with a path length of 100, a dash per percent, a muted
    # track under a round-capped arc in the temperature's hue; no raster, no gradient
    assert "pathLength:100" in html and "'stroke-dasharray']=f+' 100'" in html
    assert "stroke:var(--l)" in html and "hsl('+a[3]+'" in html and "conic-gradient" not in html
    assert ".rg>svg{stroke-width:3.6}" in html                # the arc is at least 3.5 of 36
    assert ".rg>svg,.rg b{position:absolute;inset:0;" in html  # the text sits over the ring, centred
    assert ".rg b{justify-content:center}" in html
    # the pulse only runs when the viewer has not asked for less motion
    assert re.search(r"@media\(prefers-reduced-motion:no-preference\)\{\.hot \.rg,\.re\{animation", html)
    assert "innerHTML" not in html


def test_the_fullest_card_state_with_rings_still_fits(card_bot):
    fullest_keyboard(card_bot)
    card_bot.cfg["send_photo"] = True
    card_bot.status = status(13, "Очень_длинное_имя_файла_" * 10 + ".gcode", progress=5,
                             TempOfNozzle=180, TempTargetNozzle=245, TempOfHotbed=40,
                             TempTargetHotbed=70)
    data = card_bot._card()
    assert data["tn"].split("|")[4] == "1" and data["tb"].split("|")[4] == "1"
    assert data["ti"] == "внутри 30°" and data["ab"]
    data.update(n="ж" * 300, go="abcdefgh")
    text = widget.encode(data)
    assert len(text.encode("utf-8")) <= widget.DATA_LIMIT
    assert card_of(text)["tn"] == data["tn"] and card_of(text)["ab"] == data["ab"]


def test_the_action_buttons_are_data_the_card_only_draws():
    data = widget.card(status(13, "a.gcode"), True, "P", NOW_FOR_RINGS,
                       keys=widget.buttons(status(13, "a.gcode"), True,
                                           {"pause", "cancel"}, True))
    assert data["ab"] == [["Пауза", "ask:pause", "", "pa"], ["Стоп", "ask:stop", "k", "st"],
                          ["Кадр", "snap", "", "ca"]]
    assert "b" not in data
    assert "ab" not in widget.card(status(13, "a.gcode"), True, "P", NOW_FOR_RINGS)


NOW_FOR_RINGS = 1_700_000_000.0


def test_the_height_model_counts_figures_wrapped_lines_and_wrapped_rows():
    plain = {"t": "T", "x": [], "k": []}
    assert widget.estimate_height(plain) == widget.PAGE_BASE_PX
    assert widget.estimate_height(dict(plain, g={"b": "A", "s": "0"})) \
        == widget.PAGE_BASE_PX + widget.FIGURE_PX
    long_line = dict(plain, x=["ж" * 45])                # wraps at 320 px, not at 430
    assert widget.estimate_height(long_line, 320) > widget.estimate_height(long_line, 430)
    speeds = dict(plain, k=[[[l, "c:x", ""] for l in ("50%", "75%", "100%", "125%", "150%")]])
    assert widget.estimate_height(speeds, 320) >= widget.PAGE_BASE_PX + widget.ROW_PX



# ------------------------------------------------------------ round 8: the icon set

# Buttons that have no icon on purpose: confirmations, short level buttons, labels.
NO_ICON_BY_DESIGN = ("do:", "fans:", "noop", "set:", "maint:", "schedcancel:", "refresh")


def all_keyboards(bot):
    fullest_keyboard(bot)
    bot.status = printing()
    bot.status["ExcludeObject"] = {"Objects": ["a", "b"], "ExcludedObjects": [],
                                   "CurrentObject": "a"}
    return {
        "main": bot.keyboard(),
        "files": ui.kb_files(["/a.gcode", "/b.gcode"], can_start=True, refs=["1", "2"],
                             can_delete=True, delete_refs=["3", "4"], delete_all_ref="5"),
        "fans": ui.kb_fans({"ModelFan": 0, "BoxFan": 0, "AuxiliaryFan": 0}),
        "speed": ui.kb_speed(100), "temp": ui.kb_temp(),
        "objects": ui.kb_objects(["a", "b"], ["r1", "r2"], "a", ["a", "b"]),
        "macros": ui.kb_macros(["PARK", "CALIBRATE_BED_60"], ["m1", "m2"]),
        "bed": ui.kb_bed_temps(["CALIBRATE_BED_60"], ["m"]),
        "plan": ui.kb_plan([{"id": "j", "path": "/a.gcode", "at": 1}]),
        "back": ui.kb_back(), "confirm": ui.kb_confirm("do:x", "y")}


def test_every_panel_and_screen_button_has_an_icon_unless_by_design(card_bot):
    for name, keyboard in all_keyboards(card_bot).items():
        for row in keyboard:
            for button in row:
                data = button.get("callback_data")
                if data and not data.startswith(NO_ICON_BY_DESIGN):
                    assert widget.icon_for(data), (name, data)
    # the callbacks of the table named in the brief
    for data, icon in (("ask:pause", "pa"), ("ask:resume", "re"), ("ask:stop", "st"),
                       ("snap", "ca"), ("files", "fi"), ("ask:print:x", "pr"),
                       ("ask:delete:x", "de"), ("ask:delall:x", "de"), ("plan", "cl"),
                       ("objects", "cu"), ("ask:exclude:x", "cu"), ("menu:fans", "fa"),
                       ("menu:speed", "sp"), ("menu:temp", "he"), ("light", "li"),
                       ("mesh", "be"), ("history", "hi"), ("macros", "ma"),
                       ("ask:macro:x", "ma"), ("diag", "di"), ("help", "hp")):
        assert widget.icon_for(data) == icon, data
    assert widget.icon_for("set:fan:ModelFan:50") == "" and widget.icon_for("") == ""
    assert {icon for _prefix, icon in widget.ICON_PREFIXES} <= set(widget.ICONS)


def test_a_label_loses_its_emoji_only_when_an_icon_is_attached(card_bot):
    fullest_keyboard(card_bot)
    items = widget.menu(card_bot.keyboard())
    for item in items:
        if len(item) == 3:
            assert item[2] in widget.ICONS or item[2] == "fa1"
            assert not re.match(r"^[^\w«(\"']", item[0]), item      # plain text
        else:
            assert not widget.icon_for(item[1]), item                # no icon: label as it was
    assert ["Файлы", "files", "fi"] in items
    sc = widget.screen("<b>T</b>", [[{"text": "🖨 имя.gcode", "callback_data": "ask:print:x"},
                                      {"text": "🗑", "callback_data": "ask:delete:x"}],
                                     [{"text": "✅ Да", "callback_data": "do:x"},
                                      {"text": "↩️ Назад", "callback_data": "refresh"}]])
    assert sc["k"] == [[["имя.gcode", "c:ask:print:x", "", "pr"],
                        ["🗑", "c:ask:delete:x", "k", "de"]],        # nothing left: emoji stays
                       [["✅ Да", "c:do:x", "y"], ["Назад", "b", "", "bk"]]]
    assert widget.plain("⚙️ Ещё") == "Ещё" and widget.plain("50%") == "50%"


def test_the_fan_icon_spins_only_while_a_fan_runs(card_bot):
    fullest_keyboard(card_bot)
    keyboard = card_bot.keyboard()

    def fan(items):
        return next(i for i in items if i[1] == "menu:fans")
    assert fan(widget.menu(keyboard))[2] == "fa"
    assert fan(widget.menu(keyboard, fans_on=True))[2] == "fa1"
    card_bot.status = printing(CurrentFanSpeed={"ModelFan": 0, "BoxFan": 0, "AuxiliaryFan": 0})
    assert fan(card_bot._card()["mn"])[2] == "fa"
    card_bot.status = printing(CurrentFanSpeed={"ModelFan": 40, "BoxFan": 0, "AuxiliaryFan": 0})
    assert fan(card_bot._card()["mn"])[2] == "fa1"


def test_the_icon_table_is_valid_and_fits_one_answer():
    answer = widget.icons_answer()
    assert len(answer.encode("utf-8")) <= widget.ANSWER_LIMIT
    table = json.loads(answer)["ic"]
    assert table == widget.ICONS and len(table) >= 20
    for icon, path in table.items():
        assert re.fullmatch(r"[a-z]{2}", icon)
        assert re.fullmatch(r"M[MmLlHhVvCcSsQqTtAaZz0-9eE.,\s-]+", path), icon
    assert {"pa", "re", "fa", "ca"} <= set(table)


def test_the_icon_request_is_answered_to_the_owner_and_data_queries_only(card_bot, fake_telegram):
    handlers.handle_widget_query(card_bot, widget_query("ic"))
    assert json.loads(fake_telegram.widget_answers[-1][1])["ic"] == widget.ICONS
    handlers.handle_widget_query(card_bot, widget_query("ic", sender="999", qid="wq-2"))
    assert last_answer(fake_telegram) == {"m": "Этот бот личный."}
    other = widget_query("ic", qid="wq-3")
    other["type"] = {"type": "sign_data"}
    handlers.handle_widget_query(card_bot, other)
    assert last_answer(fake_telegram) == {"m": "Неизвестная кнопка."}
    handlers.handle_widget_query(card_bot, widget_query("ic", chat="123", qid="wq-4"))
    assert "ic" not in last_answer(fake_telegram)


def test_the_cards_script_fetches_icons_late_quietly_and_without_storage():
    script = widget.HTML.split("<script>")[1]
    assert "setTimeout(ic,4e3)" in script                    # after the first render
    assert "function ic(){if(IC.pa)return;" in script and "W.request('ic')" in script
    assert "function(){setTimeout(ic,3e4)}" in script        # a failure: later, no note line
    # every storage access is guarded; the card works without icons
    assert "try{IC=JSON.parse(localStorage.getItem('ic11'))||{}}catch(e){}" in script
    assert "try{localStorage.setItem('ic11',JSON.stringify(IC))}catch(e){}" in script
    assert "S('svg',{viewBox:'0 0 24 24','class':n})" in script and "catch(r){}" in script
    assert ".insertBefore(s,e.firstChild)" in script
    assert "innerHTML" not in script


def test_the_fullest_state_with_icon_ids_fits_chat_data(card_bot):
    fullest_keyboard(card_bot)
    card_bot.cfg["send_photo"] = True
    card_bot.status = status(13, "Очень_длинное_имя_файла_" * 10 + ".gcode", progress=5,
                             CurrentFanSpeed={"ModelFan": 80, "BoxFan": 0, "AuxiliaryFan": 0})
    data = card_bot._card()
    assert all(len(item) == 4 for item in data["ab"])
    assert any(len(item) == 3 for item in data["mn"])
    data.update(n="ж" * 300, go="abcdefgh")
    assert len(widget.encode(data).encode("utf-8")) <= widget.DATA_LIMIT


def test_the_time_left_comes_ready_from_the_bot():
    data = widget.card(printing(ticks=600, total_ticks=7800), True, "P", NOW_FOR_RINGS)
    assert data["et"] == "· ещё 2 ч" and data["ef"].startswith("готово ≈ ")
    assert widget.card(printing(ticks=0, total_ticks=300), True, "P", NOW_FOR_RINGS)["et"] \
        == "· ещё 5 мин"
    paused = widget.card(status(6, "a.gcode", ticks=100), True, "P", NOW_FOR_RINGS)
    assert "ef" not in paused and "et" in paused              # no finish clock on a pause
    assert "et" not in widget.card(status(0), True, "P", NOW_FOR_RINGS)


# ------------------------------------------------------------ the objects screen: groups, a map, a grid

def copies_job(mr_bot, groups, excluded=(), current=1):
    """A job of parts named like Orca names copies ("<MODEL>.STEP_ID_28_COPY_0"),
    spread over the bed with outlines of a real size. ``groups`` is a list of
    (model, number of copies); ``excluded`` and ``current`` count from one."""
    names = []
    for model, count in groups:
        for i in range(count):
            names.append("%s.STEP_ID_%d_COPY_0" % (model.replace(" ", "_"), i))
    shapes = {}
    for i, name in enumerate(names):
        x, y = 8 + 31 * (i % 8), 8 + 31 * (i // 8)
        shapes[name] = {"polygon": [(x, y), (x + 22, y), (x + 22, y + 22), (x, y + 22)]}
    state = {"Objects": names, "ExcludedObjects": [names[i - 1] for i in excluded],
             "CurrentObject": names[current - 1], "PrintState": "printing",
             "Filename": "x.gcode", "Shapes": shapes}
    mr_bot.moonraker.exclude_object_state = lambda: state
    return names, shapes, state


def numbers_of(pages):
    """The numbers on the grid buttons of the pages, in order."""
    return [int("".join(ch for ch in b[0] if ch.isdigit()))
            for sc in pages for row in sc["k"] for b in row
            if b[1].startswith("c:xo:")]


def assert_pages_fit(pages):
    for n, sc in enumerate(pages):
        for width in (320, 430):
            assert widget.estimate_height(sc, width) <= widget.MAX_HEIGHT_PX, (n, width)
        assert len(json.dumps({"sc": sc}, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")) <= widget.ANSWER_LIMIT


def test_two_models_of_28_copies_list_the_models_then_a_grid(mr_bot, fake_telegram):
    names, shapes, state = copies_job(mr_bot, [("МОДЕЛЬ А", 28), ("МОДЕЛЬ Б", 28)],
                                      excluded=(3,), current=10)
    first = pages_of(mr_bot, fake_telegram, "c:objects")
    assert len(first) == 1
    sc = first[0]
    assert sc["t"] == "✂️ Убрать объект" and sc["x"] == ["Осталось 55 из 56 · моделей 2"]
    rows = [r for r in sc["k"] if r[0][1].startswith("c:objgrp:")]
    assert [r[0][0] for r in rows] == ["МОДЕЛЬ А ×28 · осталось 27", "МОДЕЛЬ Б ×28 · осталось 28"]
    assert "g" not in sc                                  # no map on the list of models
    assert_pages_fit(first)

    second = pages_of(mr_bot, fake_telegram, rows[0][0][1])      # a tap on the first model
    assert len(second) <= 2
    assert second[0]["t"] == "✂️ МОДЕЛЬ А" and second[0]["x"] == ["Осталось 27 из 28"]
    assert_pages_fit(second)
    grid = numbers_of(second)
    assert sorted(grid) == [n for n in range(1, 29) if n != 3]  # 3 is gone; B is not here
    assert len(grid) == len(set(grid))
    marks = [b[0] for sc2 in second for row in sc2["k"] for b in row if b[0].startswith("▶")]
    assert marks == ["▶ 10"]                                     # the one printing now
    # the map: this model highlighted with its numbers, the rest dimmed
    st = "".join(str(d[-1]) for d in decode(second[0]["g"]))
    assert st.count("4") == 26 and st.count("2") == 1 and st.count("1") == 1
    assert st.count("0") == 28
    assert "g" in second[0] and all("g" not in p for p in second[1:])
    assert any(b[0] == "К группам" or b[0] == "‹ К группам"
               for sc2 in second for row in sc2["k"] for b in row)
    # up to 8 numbers in a row, and a grid button is the existing confirmation
    assert max(len(r) for sc2 in second for r in sc2["k"]) <= 8
    code = next(b[1] for row in second[0]["k"] for b in row if b[1].startswith("c:xo:"))
    handlers.handle_widget_query(mr_bot, widget_query(code, qid="wq-x"))
    ask = last_answer(fake_telegram)["sc"]
    assert "Убрать модель" in ask["t"]
    assert any(b[2] == "y" and b[1].startswith("c:do:exclude:") for row in ask["k"] for b in row)


def test_back_to_the_models_works_and_a_stale_model_token_says_so(mr_bot, fake_telegram):
    copies_job(mr_bot, [("МОДЕЛЬ А", 5), ("МОДЕЛЬ Б", 5)])
    first = pages_of(mr_bot, fake_telegram, "c:objects")[0]
    group = next(r[0][1] for r in first["k"] if r[0][1].startswith("c:objgrp:"))
    second = pages_of(mr_bot, fake_telegram, group)
    back = next(b[1] for row in second[0]["k"] for b in row if b[0].endswith("К группам"))
    assert back == "c:objects"
    again = pages_of(mr_bot, fake_telegram, back)
    assert again[0]["t"] == "✂️ Убрать объект"
    handlers.handle_widget_query(mr_bot, widget_query(group, qid="wq-2"))   # the token is spent
    assert "устарел" in last_answer(fake_telegram)["m"]


def test_56_identical_copies_are_one_screen_of_numbers(mr_bot, fake_telegram):
    model = "ПРУЖИННЫЙ ПРИЖИМ КАРТОНА — 28 ШТ"
    copies_job(mr_bot, [(model, 56)], current=1)
    pages = pages_of(mr_bot, fake_telegram, "c:objects")        # one model: the grid at once
    assert len(pages) <= 2
    assert_pages_fit(pages)
    assert pages[0]["t"] == "✂️ " + model and pages[0]["x"] == ["Осталось 56 из 56"]
    grid = numbers_of(pages)
    assert sorted(grid) == list(range(1, 57)) and len(grid) == len(set(grid))
    assert not any(b[0].endswith("К группам") for sc in pages for r in sc["k"] for b in r)
    st = "".join(str(d[-1]) for d in decode(pages[0]["g"]))
    assert st.count("4") == 55 and st.count("2") == 1            # all highlighted, one printing
    assert max(len(r) for sc in pages for r in sc["k"]) <= 8


def test_one_big_part_twenty_small_and_35_copies(mr_bot, fake_telegram):
    groups = [("ОСНОВАНИЕ", 1)] + [("МЕЛОЧЬ %d" % i, 1) for i in range(20)] + [("ЗАЖИМ", 35)]
    copies_job(mr_bot, groups, excluded=(25,), current=30)
    pages = pages_of(mr_bot, fake_telegram, "c:objects")
    assert_pages_fit(pages)
    models = [b for sc in pages for r in sc["k"] for b in r if b[1].startswith("c:objgrp:")]
    assert len(models) == 22                                      # all reachable
    assert len({b[0] for b in models}) == len(models)
    assert all("g" not in sc for sc in pages)                    # too many models for a map
    clips = next(b for b in models if b[0].startswith("ЗАЖИМ"))
    assert clips[0] == "ЗАЖИМ ×35 · осталось 34"
    second = pages_of(mr_bot, fake_telegram, clips[1])
    assert len(second) <= 2
    assert_pages_fit(second)
    grid = numbers_of(second)
    assert sorted(grid) == [n for n in range(22, 57) if n != 25]
    assert len(grid) == len(set(grid)) == 34
    assert [b[0] for sc in second for r in sc["k"] for b in r if b[0].startswith("▶")] == ["▶ 30"]
    assert "g" in second[0]


def test_the_object_screen_outside_card_mode_is_unchanged(mr_bot, fake_telegram):
    copies_job(mr_bot, [("МОДЕЛЬ А", 3), ("МОДЕЛЬ Б", 3)])
    mr_bot.cfg["status_widget"] = False
    handlers.show_objects(mr_bot, OWNER, force_new=True)
    text, keyboard, has_photo = fake_telegram.sent[-1][1], fake_telegram.sent[-1][2], fake_telegram.sent[-1][3]
    assert "Объекты текущей печати" in text and has_photo           # the PNG, the long list
    assert sum(b["callback_data"].startswith("ask:exclude:") for r in keyboard for b in r) == 6


# ------------------------------------------------------------ no numeric lists, ever

def numeric_lists(value, path="$"):
    """Where a list holds a number (or a bool, which is one to JavaScript)."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            found += numeric_lists(item, "%s.%s" % (path, key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            if isinstance(item, (int, float)):
                found.append("%s[%d]" % (path, i))
            else:
                found += numeric_lists(item, "%s[%d]" % (path, i))
    return found


def test_the_walker_finds_numbers_in_lists():
    assert numeric_lists({"a": [1, "x"], "b": {"c": [["y", 2.5]]}, "d": [True]}) \
        == ["$.a[0]", "$.b.c[0][1]", "$.d[0]"]
    assert numeric_lists({"a": 5, "b": ["x", ["y"]], "c": {"d": "1"}}) == []


def test_no_list_of_numbers_in_chat_data_or_any_answer_the_card_reads(mr_bot, fake_telegram):
    """Telegram Desktop's widget sandbox reads an array of numbers out of
    chat_data or an answer wrongly: elements come out joined by dots ("слой
    20.45 из 45" for [20, 45] on the first card, "245.245°" on a heating ring).
    Strings, and plain numbers outside a list, are fine. So nothing the bot
    sends to the card may hold a list with a number in it."""
    fullest_keyboard(mr_bot)
    mr_bot.cfg["send_photo"] = True
    mr_bot.grab = lambda max_age=0: bytes([255, 216]) + b"frame"
    mr_bot.cfg["moonraker_macro_whitelist"] = ["PARK_NOZZLE", "CALIBRATE_BED_60"]
    mr_bot.moonraker.list_macros = lambda: ["PARK_NOZZLE", "CALIBRATE_BED_60"]
    mr_bot.status = status(13, "Очень_длинное_имя_файла_" * 5 + ".gcode", progress=5,
                           TempOfNozzle=180, TempTargetNozzle=245, TempOfHotbed=40,
                           TempTargetHotbed=70,
                           CurrentFanSpeed={"ModelFan": 80, "BoxFan": 0, "AuxiliaryFan": 0})
    mr_bot.status["PrintInfo"]["PrintSpeedPct"] = 125
    data = mr_bot._card()
    data.update(n="заметка", go="abcdefgh")
    assert numeric_lists(json.loads(widget.encode(data))) == []
    assert numeric_lists(data) == []
    copies_job(mr_bot, [("МОДЕЛЬ А", 28), ("МОДЕЛЬ Б", 28)], excluded=(3,), current=10)
    group = None
    for query in ("c:files", "c:history", "c:macros", "c:diag", "c:mesh", "c:menu:fans",
                  "c:menu:speed", "c:menu:temp", "c:objects", "ic", "ask:stop", "snap"):
        handlers.handle_widget_query(mr_bot, widget_query(query, qid=query))
        answer = last_answer(fake_telegram)
        assert numeric_lists(answer) == [], (query, numeric_lists(answer))
        if query == "c:objects":
            group = next(b[1] for r in answer["sc"]["k"] for b in r
                         if b[1].startswith("c:objgrp:"))
    pages = pages_of(mr_bot, fake_telegram, group)            # the numbered grid and its map
    assert pages and all(numeric_lists({"sc": sc}) == [] for sc in pages)
    handlers.handle_widget_query(mr_bot, widget_query("c:mesh", qid="m"))
    assert isinstance(last_answer(fake_telegram)["sc"]["g"]["m"]["z"], str)   # heights: a string
    # the pager
    assert numeric_lists({"k": [widget.nav_row("tok", 1, 3)]}) == []


def test_the_heating_rings_and_the_air_arrive_as_strings(card_bot):
    card_bot.status = printing(TempOfNozzle=245, TempTargetNozzle=245, TempOfHotbed=70,
                               TempTargetHotbed=70)
    data = card_bot._card()
    assert data["tn"] == "245|245|100|0|0" and data["tb"] == "70|70|100|20|0"
    assert isinstance(data["ti"], str) and data["ti"] == "внутри 30°"
    assert numeric_lists(data) == []


# ------------------------------------------------------------ round 9: zoom and labels placed by the bot

def live_like_job(mr_bot):
    """56 invented parts shaped like a real job: 28 small copies of one model
    packed tightly in a grid, 24 of another, and 4 larger ones (the last two
    models are kept apart so that the zoom window of the first reaches some
    of them)."""
    names, shapes = [], {}

    def add(model, count, columns, x0, y0, side, pitch):
        for i in range(count):
            name = "%s.STEP_ID_%d_COPY_0" % (model, len(names))
            x, y = x0 + pitch * (i % columns), y0 + pitch * (i // columns)
            shapes[name] = {"polygon": [(x, y), (x + side, y), (x + side, y + side),
                                        (x, y + side)]}
            names.append(name)
    add("АЛЬФА", 28, 7, 20, 20, 24, 27)          # 7 x 4, gaps of 3 mm
    add("БЕТА", 24, 6, 20, 150, 18, 30)           # reaches into the window of the first
    add("ГАММА", 4, 2, 170, 190, 44, 52)
    state = {"Objects": names, "ExcludedObjects": [names[4]], "CurrentObject": names[9],
             "PrintState": "printing", "Filename": "x.gcode", "Shapes": shapes}
    mr_bot.moonraker.exclude_object_state = lambda: state
    return names, shapes, state


K = 428 / 63.0


def boxes_and_labels(fig):
    """What the card's script would draw: the boxes (x, y, w, h) in canvas px and
    the label rectangles, worked out here from the figure alone."""
    out, labels = [], []
    for n, state in enumerate(fig["s"]):
        if state == "3":
            continue
        a = [objectmap.ALPHABET.index(c) * K for c in fig["b"][4 * n:4 * n + 4]]
        box = (6 + a[0], 6 + a[1], a[2] - a[0], a[3] - a[1])
        out.append((n + 1, state, box))
        code = int(fig["n"][n], 36)
        if not code:
            continue
        size = 20 + 8 * ((code - 1) // 5)
        place = (code - 1) % 5
        x, y, w, h = box
        half = .3 * size * len(str(n + 1))
        cx = x + w / 2 + (0, 0, 1, -1, 0)[place] * (w / 2 + 3 + half)
        cy = y + h / 2 + (0, -1, 0, 0, 1)[place] * (h / 2 + 3 + .4 * size)
        labels.append((n + 1, (cx - half, cy - .4 * size, cx + half, cy + .4 * size), place))
    return out, labels


def overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def test_a_zoomed_map_labels_most_copies_and_no_label_overlaps(mr_bot, fake_telegram):
    names, shapes, state = live_like_job(mr_bot)
    first = pages_of(mr_bot, fake_telegram, "c:objects")
    assert len(first) >= 1 and "g" not in first[0]               # level one: no map at all
    assert all("g" not in sc for sc in first)
    group = next(b[1] for sc in first for r in sc["k"] for b in r
                 if b[1].startswith("c:objgrp:") and b[0].startswith("АЛЬФА"))
    pages = pages_of(mr_bot, fake_telegram, group)
    assert_pages_fit(pages)
    fig = pages[0]["g"]
    assert len(fig["n"]) == len(fig["s"]) == len(names)
    boxes, labels = boxes_and_labels(fig)
    mine = [(n, box) for n, state_, box in boxes if state_ in "24"]
    assert len(mine) == 27                                       # 28 copies minus the one gone
    # the window holds every copy, and they fill it (not squeezed into a corner)
    assert all(0 <= b[0] and b[0] + b[2] <= 440 and 0 <= b[1] and b[1] + b[3] <= 440
               for _n, b in mine)
    span = max(max(b[0] + b[2] for _n, b in mine) - min(b[0] for _n, b in mine),
               max(b[1] + b[3] for _n, b in mine) - min(b[1] for _n, b in mine))
    assert span > 0.75 * 428
    # the other models that reach into the window are there, dimmed, without labels
    dim = [n for n, state_, _b in boxes if state_ == "0"]
    assert dim and all(not any(lab[0] == n for lab in labels) for n in dim)
    # at least 70 % of the selected copies carry a label; the rest are on the grid
    labelled = {n for n, _r, _p in labels}
    assert len([n for n, _b in mine if n in labelled]) >= 0.7 * len(mine)
    # every label is on the canvas, and no two overlap each other or any box but its own
    for n, rect, place in labels:
        assert rect[0] >= 0 and rect[1] >= 0 and rect[2] <= 440 and rect[3] <= 440, (n, rect)
    for i, (n, rect, place) in enumerate(labels):
        for m, other, _p in labels[i + 1:]:
            assert not overlap(rect, other), (n, m)
    for sc in pages:
        assert numeric_lists({"sc": sc}) == []


def test_a_lone_copy_and_a_whole_bed_view_still_label(mr_bot):
    one = objectmap.figure(["A"], {"A": {"polygon": [(100, 100), (112, 100), (112, 112)]}},
                           highlight={"A"})
    assert one["s"] == "4" and one["n"] != "0"               # zoomed to a 40 mm window
    whole = objectmap.figure(["A", "B"], {"A": {"polygon": [(10, 10), (60, 10), (60, 60)]},
                                          "B": {"polygon": [(100, 100), (150, 100), (150, 150)]}})
    assert whole["n"] == "bb"                                # big boxes: the largest size, centred


def test_the_scripts_label_formulas_are_the_bots():
    """The card draws what objectmap placed; its formulas must stay equal."""
    html = widget.HTML
    assert "z=20+8*((v-1)/5|0);p=(v-1)%5" in html and "v=parseInt(g.n[n],36)" in html
    assert "[0,0,1,-1,0][p]*(w/2+3+.3*z*String(n+1).length)" in html
    assert "[0,-1,0,0,1][p]*(h/2+3+.4*z)" in html
    assert objectmap.FONTS == (20, 28, 40) and objectmap.PLACES == 5
    assert (objectmap.CANVAS, objectmap.MARGIN, objectmap.SPAN) == (440, 6, 428)
    assert 'width=440 height=440' in html and "B=428" in html


def test_object_tokens_have_a_store_of_their_own(mr_bot):
    """Tapping through a hundred parts must not push a pending "stop" out."""
    stop = mr_bot.issue_action_confirmation("cancel")
    mr_bot.prepare_object_choices([{"name": "n%d" % i} for i in range(200)])
    assert mr_bot.consume_action_confirmation("cancel", stop)       # still there
    assert mr_bot.confirmations is not mr_bot.object_tokens
    assert mr_bot.object_tokens.limit == 256 and mr_bot.confirmations.limit == 64
    token = mr_bot.issue_object_confirmation({"name": "x"})
    assert mr_bot.consume_object_confirmation(token) == {"name": "x"}


# ------------------------------------------------------------ the confirmation shows the part

def test_the_exclusion_confirmation_shows_only_the_chosen_part(mr_bot, fake_telegram):
    names, shapes, state = live_like_job(mr_bot)
    first = pages_of(mr_bot, fake_telegram, "c:objects")
    group = next(b[1] for sc in first for r in sc["k"] for b in r
                 if b[1].startswith("c:objgrp:") and b[0].startswith("АЛЬФА"))
    grid = pages_of(mr_bot, fake_telegram, group)
    cells = [b for sc in grid for r in sc["k"] for b in r if b[1].startswith("c:xo:")]
    chosen = next(b for b in cells if b[0] == "16")                 # a part without a drawn number
    handlers.handle_widget_query(mr_bot, widget_query(chosen[1], qid="wq-c"))
    answer = last_answer(fake_telegram)
    assert len(fake_telegram.widget_answers[-1][1].encode("utf-8")) <= widget.ANSWER_LIMIT
    sc = answer["sc"]
    assert "Убрать модель" in sc["t"] and "16" in " ".join(sc["x"])
    fig = sc["g"]
    # only the chosen part (number 16) is highlighted and labelled; the others are dimmed
    boxes, labels = boxes_and_labels(fig)
    assert [n for n, st, _b in boxes if st in "24"] == [16]
    assert [n for n, _r, _p in labels] == [16]
    assert sum(1 for _n, st, _b in boxes if st == "0") >= 27          # the rest of the model, dimmed
    assert fig["s"][4] == "1"                                         # the one already gone
    # unchanged flow: the second step is a one-use confirmation of that very part
    yes = next(b for r in sc["k"] for b in r if b[1].startswith("c:do:exclude:"))
    assert yes[2] == "y"
    for width in (320, 430):
        assert widget.estimate_height(sc, width) <= widget.MAX_HEIGHT_PX
    assert numeric_lists(answer) == []
    # the zoom is the model's window, not the part's: the 28 copies are all in the picture
    assert len(boxes) >= 28


def test_labels_ignore_boxes_but_never_overlap_each_other():
    """Thin slanted parts have bounding boxes that overlap heavily; the numbers
    are drawn on top, so only label-label overlaps and the canvas edge count."""
    shapes = {}
    names = []
    for i in range(20):                                   # overlapping boxes in a row
        name = "BAR.STEP_ID_%d_COPY_0" % i
        shapes[name] = {"polygon": [(30 + 6 * i, 60), (50 + 6 * i, 60), (50 + 6 * i, 150),
                                    (30 + 6 * i, 150)]}
        names.append(name)
    fig = objectmap.figure(names, shapes, highlight=set(names))
    boxes, labels = boxes_and_labels(fig)
    assert len(labels) >= 18
    for i, (n, rect, _p) in enumerate(labels):
        assert rect[0] >= 0 and rect[2] <= 440 and rect[1] >= 0 and rect[3] <= 440
        assert all(not overlap(rect, other) for _m, other, _q in labels[i + 1:])
