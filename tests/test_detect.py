# -*- coding: utf-8 -*-
"""Определение прошивки: штатная Elegoo или OpenCentauri/COSMOS."""
import pytest

from centauri_bot import backend, detect, sdcp


class ФейкКлиент:
    """Отвечает как Moonraker только на заранее названном адресе."""

    def __init__(self, отвечает):
        self.отвечает = отвечает

    def __call__(self, url, api_key="", timeout=3):
        self.url = url
        return self

    def printer_info(self):
        if self.url != self.отвечает:
            raise RuntimeError("нет связи")
        return {"state": "ready", "hostname": "printer"}


@pytest.fixture
def порты(monkeypatch):
    """Управляем тем, какие TCP-порты считаются открытыми."""
    открытые = set()

    def достижим(host, port, timeout=3):
        return port in открытые

    monkeypatch.setattr(sdcp, "tcp_reachable", достижим)
    monkeypatch.setattr(detect.sdcp, "tcp_reachable", достижим)
    return открытые


def test_cosmos_opoznan_po_otvetu_moonraker(порты):
    порты.add(80)
    имя, url, пояснение = detect.detect(
        "1.2.3.4", client_factory=ФейкКлиент("http://1.2.3.4"))
    assert имя == backend.MOONRAKER
    assert url == "http://1.2.3.4"
    assert "COSMOS" in пояснение


def test_moonraker_na_svoyom_portu(порты):
    """Голый Moonraker без nginx слушает 7125."""
    порты.update({7125})
    имя, url, _ = detect.detect(
        "1.2.3.4", client_factory=ФейкКлиент("http://1.2.3.4:7125"))
    assert имя == backend.MOONRAKER
    assert url == "http://1.2.3.4:7125"


def test_otkrytyy_port_bez_otveta_ne_schitaetsya_cosmos(порты):
    """Порт 80 может занимать что угодно: без ответа Moonraker это не COSMOS."""
    порты.update({80, sdcp.WS_PORT})
    имя, url, _ = detect.detect(
        "1.2.3.4", client_factory=ФейкКлиент("никогда"))
    assert имя == backend.SDCP
    assert url == ""


def test_stokovaya_proshivka_po_portu_sdcp(порты):
    порты.add(sdcp.WS_PORT)
    имя, url, пояснение = detect.detect(
        "1.2.3.4", client_factory=ФейкКлиент("никогда"))
    assert имя == backend.SDCP
    assert url == ""
    assert "Elegoo" in пояснение


def test_molchanie_ne_ugadyvaetsya(порты):
    """Принтер выключен — честно говорим, что не знаем, а не гадаем."""
    имя, url, пояснение = detect.detect(
        "1.2.3.4", client_factory=ФейкКлиент("никогда"))
    assert имя == ""
    assert url == ""
    assert "не ответил" in пояснение


def test_resolve_razvorachivaet_auto(порты):
    порты.add(80)
    cfg = {"backend": "auto", "printer_ip": "1.2.3.4"}
    итог = detect.resolve(cfg, client_factory=ФейкКлиент("http://1.2.3.4"))
    assert итог["backend"] == backend.MOONRAKER
    assert итог["moonraker_url"] == "http://1.2.3.4"
    assert cfg["backend"] == "auto", "исходный конфиг не меняем"


def test_resolve_ne_trogaet_yavnyy_backend(порты):
    cfg = {"backend": "sdcp", "printer_ip": "1.2.3.4"}
    assert detect.resolve(cfg) is cfg


def test_resolve_ne_zatiraet_zadannyy_url(порты):
    порты.add(80)
    cfg = {"backend": "auto", "printer_ip": "1.2.3.4",
           "moonraker_url": "http://svoy:7125"}
    итог = detect.resolve(cfg, client_factory=ФейкКлиент("http://1.2.3.4"))
    assert итог["moonraker_url"] == "http://svoy:7125"


def test_resolve_padaet_esli_printer_molchit(порты):
    with pytest.raises(LookupError):
        detect.resolve({"backend": "auto", "printer_ip": "1.2.3.4"})
