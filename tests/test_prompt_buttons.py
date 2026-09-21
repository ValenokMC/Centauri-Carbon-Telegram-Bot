# -*- coding: utf-8 -*-
"""Кнопки из окон принтера: разбор, стиль и подтверждение опасных."""
import json

from centauri_bot import moonraker, ui


class Ответ:
    def __init__(s, тело): s.тело = тело
    def read(s, n): return s.тело
    def __enter__(s): return s
    def __exit__(s, *a): return False


def клиент(строки):
    def opener(request, timeout=None):
        if "gcode_store" in request.full_url:
            return Ответ(json.dumps({"result": {"gcode_store":
                [{"message": m} for m in строки]}}).encode())
        return Ответ(b'{"result":{}}')
    return moonraker.Client("http://p", opener=opener)


ПРОВЕРКА_КАЛИБРОВКИ = [
    "// action:prompt_begin Welcome to COSMOS!",
    "// action:prompt_text OK! Resonance Compensation calibrated",
    "// action:prompt_text X Default bed mesh missing",
    "// action:prompt_footer_button Close|_CLOSE_PROMPT",
    "// action:prompt_footer_button Calibrate All|_CALIBRATE_ALL_STEP_1|warning",
    "// action:prompt_show",
]


def test_stil_knopki_chitaetsya():
    prompt = клиент(ПРОВЕРКА_КАЛИБРОВКИ).active_prompt()
    assert prompt["buttons"] == [
        ("Close", "_CLOSE_PROMPT", ""),
        ("Calibrate All", "_CALIBRATE_ALL_STEP_1", "warning"),
    ]


def test_tekst_okna_sohranyaetsya_tselikom():
    prompt = клиент(ПРОВЕРКА_КАЛИБРОВКИ).active_prompt()
    assert prompt["title"] == "Welcome to COSMOS!"
    assert "X Default bed mesh missing" in prompt["text"]


def test_opasnaya_knopka_vedyot_na_podtverzhdenie():
    prompt = клиент(ПРОВЕРКА_КАЛИБРОВКИ).active_prompt()
    rows = ui.kb_prompt(prompt["buttons"], ["r1", "r2"])

    close, calibrate = rows[0][0], rows[1][0]
    assert close["callback_data"] == "prompt:r1"
    assert calibrate["callback_data"] == "askprompt:r2", "нельзя запускать с одного нажатия"
    assert calibrate["text"].startswith("⚠️")
    assert close["text"].startswith("🖐")


def test_obychnaya_knopka_srabatyvaet_srazu():
    """Вставить пластик — не то действие, ради которого стоит переспрашивать."""
    кнопки = [("LOAD", "_LOAD_FILAMENT_STEP_PUSH", "")]
    строка = ui.kb_prompt(кнопки, ["r1"])[0][0]
    assert строка["callback_data"] == "prompt:r1"


def test_stil_error_tozhe_schitaetsya_opasnym():
    assert ui.prompt_is_risky("error")
    assert ui.prompt_is_risky("WARNING")
    assert not ui.prompt_is_risky("")
    assert not ui.prompt_is_risky("primary")


def test_ekran_podtverzhdeniya_pokazyvaet_komandu():
    текст = ui.prompt_confirm_text("Calibrate All", "_CALIBRATE_ALL_STEP_1")
    assert "_CALIBRATE_ALL_STEP_1" in текст
    assert "печать" in текст, "надо предупредить про идущую печать"
