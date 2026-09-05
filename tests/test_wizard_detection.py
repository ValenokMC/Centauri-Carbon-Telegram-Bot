# -*- coding: utf-8 -*-
"""Мастер настройки и определение прошивки: сеть не трогается."""
import pytest

from centauri_bot import backend
from centauri_bot import setup_wizard as wiz


@pytest.fixture
def answers(monkeypatch):
    """Собрать ответы мастера и записать, о чём он спрашивал."""
    asked = {"yes": [], "choices": []}

    def ask_yes(prompt, default=True):
        asked["yes"].append(prompt)
        return True

    def ask_choice(prompt, options):
        asked["choices"].append(prompt)
        return options[0][0]

    monkeypatch.setattr(wiz, "ask_yes", ask_yes)
    monkeypatch.setattr(wiz, "ask_choice", ask_choice)
    return asked


def test_naydennaya_proshivka_predlagaetsya(monkeypatch, answers):
    monkeypatch.setattr(wiz.detect_mod, "detect", lambda host, **kw: (
        backend.MOONRAKER, "http://10.0.0.5:7125", "нашли Moonraker"))

    name, url = wiz.ask_backend("10.0.0.5")

    assert (name, url) == (backend.MOONRAKER, "http://10.0.0.5:7125")
    assert any("Использовать режим" in p for p in answers["yes"])
    assert not answers["choices"], "спрашивать вручную было незачем"


def test_otkaz_ot_naydennogo_vozvraschaet_k_ruchnomu_vyboru(monkeypatch):
    monkeypatch.setattr(wiz.detect_mod, "detect", lambda host, **kw: (
        backend.SDCP, "", "нашли сток"))
    monkeypatch.setattr(wiz, "ask_yes", lambda prompt, default=True: False)
    monkeypatch.setattr(wiz, "ask_choice",
                        lambda prompt, options: backend.MOONRAKER)

    assert wiz.ask_backend("10.0.0.5") == (backend.MOONRAKER, "")


def test_molchanie_printera_privodit_k_voprosu(monkeypatch, answers):
    monkeypatch.setattr(wiz.detect_mod, "detect",
                        lambda host, **kw: ("", "", "никто не ответил"))

    name, url = wiz.ask_backend("10.0.0.5")

    assert name == backend.SDCP        # первый вариант списка
    assert url == ""
    assert any("установлено" in p for p in answers["choices"])


def test_bez_adresa_ne_stuchimsya_nikuda(monkeypatch, answers):
    """Адрес спрашивают раньше, но подстраховка на случай пустого значения."""
    def ne_zvat(*a, **kw):
        raise AssertionError("определение без адреса вызываться не должно")

    monkeypatch.setattr(wiz.detect_mod, "detect", ne_zvat)
    assert wiz.ask_backend("") == (backend.SDCP, "")


def test_prezhniy_rezhim_ostavlyayut_bez_oprosa(monkeypatch):
    def ne_zvat(*a, **kw):
        raise AssertionError("уже настроенный режим переспрашивать нечем")

    monkeypatch.setattr(wiz.detect_mod, "detect", ne_zvat)
    monkeypatch.setattr(wiz, "ask_yes", lambda prompt, default=True: False)

    assert wiz.ask_backend("10.0.0.5", backend.MOONRAKER) == (
        backend.MOONRAKER, "")


def test_master_sprashivaet_adres_ranshe_proshivki():
    """Иначе определять нечего: без адреса не к кому стучаться."""
    import inspect
    source = inspect.getsource(wiz.run)
    assert source.index("ask_printer(") < source.index("ask_backend(")
