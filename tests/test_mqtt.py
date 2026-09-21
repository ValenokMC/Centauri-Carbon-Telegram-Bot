# -*- coding: utf-8 -*-
"""Проверка собственного MQTT-клиента на поддельном брокере. Сокеты не открываются."""
import socket
import struct

import pytest

from centauri_bot import mqtt


class ФейкСокет(object):
    """Отдаёт заранее записанные ответы и запоминает всё отправленное."""

    def __init__(self, ответы=()):
        self.отправлено = bytearray()
        self.входящее = bytearray(b"".join(ответы))
        self.закрыт = False
        self.таймаут = None

    def sendall(self, data):
        self.отправлено += data

    def recv(self, count):
        if not self.входящее:
            raise socket.timeout("нет данных")
        кусок, self.входящее = self.входящее[:count], self.входящее[count:]
        return bytes(кусок)

    def settimeout(self, value):
        self.таймаут = value

    def close(self):
        self.закрыт = True


def пакет(kind, body=b""):
    return bytes([kind]) + mqtt._encode_length(len(body)) + body


def connack(code=0):
    return пакет(mqtt.CONNACK, bytes([0, code]))


def publish(topic, payload):
    raw = topic.encode("utf-8")
    return пакет(mqtt.PUBLISH, struct.pack(">H", len(raw)) + raw + payload)


@pytest.fixture
def клиент(monkeypatch):
    def сделать(ответы=(), **kwargs):
        сокет = ФейкСокет(ответы)
        monkeypatch.setattr(socket, "create_connection",
                            lambda addr, timeout=None: сокет)
        c = mqtt.Client("printer.local", client_id="bot", **kwargs)
        return c, сокет
    return сделать


def test_dlina_kodiruetsya_po_semi_bit():
    assert mqtt._encode_length(0) == b"\x00"
    assert mqtt._encode_length(127) == b"\x7f"
    assert mqtt._encode_length(128) == b"\x80\x01"
    assert mqtt._encode_length(16383) == b"\xff\x7f"


def test_connect_neset_login_i_kod_dostupa(клиент):
    c, сокет = клиент([connack()], username="elegoo", password="12345678")
    c.connect()

    отправлено = bytes(сокет.отправлено)
    assert отправлено[0] == mqtt.CONNECT
    assert b"MQTT" in отправлено
    assert b"elegoo" in отправлено and b"12345678" in отправлено
    # Флаги: логин, пароль, чистая сессия.
    флаги = отправлено[отправлено.index(b"MQTT") + 5]
    assert флаги & 0x80 and флаги & 0x40 and флаги & 0x02


def test_bez_logina_flagi_ne_vystavlyayutsya(клиент):
    c, сокет = клиент([connack()])
    c.connect()
    флаги = bytes(сокет.отправлено)[bytes(сокет.отправлено).index(b"MQTT") + 5]
    assert not флаги & 0x80 and not флаги & 0x40


def test_nevernyy_kod_dostupa_obyasnyaetsya_po_russki(клиент):
    c, _ = клиент([connack(4)], username="elegoo", password="плохой")
    with pytest.raises(mqtt.MqttError) as e:
        c.connect()
    assert "код доступа" in str(e.value)


def test_otkaz_brokera_ne_teryaetsya(клиент):
    c, _ = клиент([connack(3)])
    with pytest.raises(mqtt.MqttError) as e:
        c.connect()
    assert "недоступен" in str(e.value)


def test_publish_i_subscribe_uhodyat_v_socket(клиент):
    c, сокет = клиент([connack()])
    c.connect()
    сокет.отправлено.clear()

    c.subscribe(["elegoo/SN/api_status", "elegoo/SN/bot/api_response"])
    c.publish("elegoo/SN/api_register", '{"client_id": "bot"}')

    отправлено = bytes(сокет.отправлено)
    assert отправлено[0] == (mqtt.SUBSCRIBE | 0x02)
    assert b"elegoo/SN/api_status" in отправлено
    assert b"elegoo/SN/bot/api_response" in отправлено
    assert b'{"client_id": "bot"}' in отправлено


def test_poll_vozvraschaet_temu_i_telo(клиент):
    c, _ = клиент([connack(), publish("elegoo/SN/api_status", b'{"a": 1}')])
    c.connect()
    assert c.poll() == ("elegoo/SN/api_status", b'{"a": 1}')


def test_poll_propuskaet_sluzhebnye_pakety(клиент):
    """PINGRESP и SUBACK не должны выглядеть как сообщение принтера."""
    c, _ = клиент([connack(), пакет(mqtt.PINGRESP),
                   пакет(mqtt.SUBACK, b"\x00\x01\x00"),
                   publish("elegoo/SN/api_status", b"{}")])
    c.connect()
    assert c.poll() is None
    assert c.poll() is None
    assert c.poll() == ("elegoo/SN/api_status", b"{}")


def test_poll_molchit_kogda_nichego_ne_prishlo(клиент):
    c, _ = клиент([connack()])
    c.connect()
    assert c.poll() is None


def test_dlinnyy_paket_sobiraetsya_iz_kuskov(клиент):
    """Длина больше 127 занимает два байта — здесь ломалась наивная реализация."""
    тело = b"x" * 300
    c, _ = клиент([connack(), publish("t", тело)])
    c.connect()
    тема, payload = c.poll()
    assert тема == "t" and payload == тело


def test_zakrytie_shlyot_disconnect(клиент):
    c, сокет = клиент([connack()])
    c.connect()
    сокет.отправлено.clear()
    c.close()
    assert bytes(сокет.отправлено)[0] == mqtt.DISCONNECT
    assert сокет.закрыт


def test_broker_zakryl_soedinenie(клиент):
    c, сокет = клиент([connack()])
    c.connect()
    сокет.входящее = bytearray()

    def recv(_count):
        return b""

    сокет.recv = recv
    with pytest.raises(mqtt.MqttError) as e:
        c.poll()
    assert "закрыл соединение" in str(e.value)
