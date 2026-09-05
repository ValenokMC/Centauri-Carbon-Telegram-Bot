# -*- coding: utf-8 -*-
"""Разведчик протокола Centauri Carbon 2. Ничего не печатает и не двигает.

Зачем он нужен. Бот умеет говорить со стоковым Carbon 1 (SDCP по WebSocket) и с
Carbon 1 под COSMOS (Moonraker по HTTP). Carbon 2 не похож ни на то, ни на
другое: у него свой MQTT-брокер и JSON-RPC поверх него. Писать поддержку по
чужим пересказам протокола - значит гадать, поэтому сначала надо посмотреть,
что принтер отвечает на самом деле.

Скрипт подключается, регистрируется, слушает и складывает всё услышанное в файл
рядом с собой. Из команд шлёт только читающие: спросить о принтере и его
состояние. Ни печати, ни отмены, ни нагрева - на чужой машине этого делать
нельзя, и в коде их просто нет.

Запуск:

    python tools/cc2-probe.py --ip 192.168.1.50 --code 12345678

Код доступа: на принтере Настройки -> LAN Only, он показан на этом же экране.
Там же режим LAN Only надо включить, иначе локального API нет вовсе.
"""
import argparse
import json
import os
import random
import socket
import string
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

from centauri_bot import mqtt  # noqa: E402

DISCOVERY_PORT = 52700
MQTT_PORT = 1883
CAMERA_PORT = 8080

# Только читающие команды. Всё, что двигает механику или греет, сюда не попадает
# намеренно: разведчик запускают на чужом принтере.
READ_ONLY_METHODS = [
    (1001, "сведения о принтере"),
    (1002, "полное состояние"),
    (1044, "список файлов"),
]


def случайный_id(length=10):
    алфавит = string.ascii_lowercase + string.digits
    return "".join(random.choice(алфавит) for _ in range(length))


class Журнал(object):
    """Пишет и на экран, и в файл. Код доступа в файл не попадает никогда."""

    def __init__(self, путь, секрет):
        self.файл = open(путь, "w", encoding="utf-8")
        self.секрет = str(секрет or "")
        self.путь = путь

    def __call__(self, текст, на_экран=True):
        строка = str(текст)
        if self.секрет:
            строка = строка.replace(self.секрет, "<код доступа скрыт>")
        if на_экран:
            print(строка)
        self.файл.write(строка + "\n")
        self.файл.flush()

    def close(self):
        self.файл.close()


def найти_принтер(таймаут=3):
    """UDP-широковещание: вернуть список (адрес, ответ) от найденных принтеров."""
    сокет = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    сокет.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    сокет.settimeout(0.5)
    найдено = []
    try:
        сокет.sendto(b"M99999", ("255.255.255.255", DISCOVERY_PORT))
        крайний = time.time() + таймаут
        while time.time() < крайний:
            try:
                данные, откуда = сокет.recvfrom(65535)
            except socket.timeout:
                continue
            найдено.append((откуда[0], данные.decode("utf-8", "replace")))
    except OSError:
        pass
    finally:
        сокет.close()
    return найдено


def серийный_из_ответа(текст):
    try:
        узел = json.loads(текст)
    except ValueError:
        return ""
    for ключ in ("MainboardID", "mainboard_id", "sn", "SN", "serial"):
        значение = узел.get(ключ) or (узел.get("Data") or {}).get(ключ)
        if значение:
            return str(значение)
    return ""


def порт_открыт(host, port, таймаут=2):
    сокет = socket.socket()
    сокет.settimeout(таймаут)
    try:
        сокет.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        сокет.close()


def main(argv=None):
    парсер = argparse.ArgumentParser(
        description="Разведка протокола Centauri Carbon 2, только чтение")
    парсер.add_argument("--ip", help="адрес принтера; без него ищем сами")
    парсер.add_argument("--code", default="",
                        help="код доступа с экрана принтера (LAN Only)")
    парсер.add_argument("--sn", default="",
                        help="серийный номер, если сами не нашли")
    парсер.add_argument("--seconds", type=int, default=90,
                        help="сколько слушать состояние, по умолчанию 90")
    парсер.add_argument("--out", default="",
                        help="куда писать журнал, по умолчанию рядом со скриптом")
    арг = парсер.parse_args(argv)

    путь = арг.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "cc2-probe-%s.log" % time.strftime("%Y%m%d-%H%M%S"))
    журнал = Журнал(путь, арг.code)
    журнал("Разведчик Centauri Carbon 2. Только чтение: печать не запускается, "
           "нагрев не включается, механика не двигается.")
    журнал("Время: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    журнал("")

    # ── 1. поиск ──────────────────────────────────────────────────────────
    адрес, серийный = арг.ip or "", арг.sn or ""
    журнал("1. Поиск принтера в сети (UDP %d)" % DISCOVERY_PORT)
    for найденный_адрес, ответ in найти_принтер():
        журнал("   ответил %s: %s" % (найденный_адрес, ответ[:400]))
        адрес = адрес or найденный_адрес
        серийный = серийный or серийный_из_ответа(ответ)
    if not адрес:
        журнал("   никто не ответил. Укажи адрес вручную: --ip 192.168.х.х")
        журнал.close()
        return 1
    журнал("   работаем с %s" % адрес)
    журнал("")

    # ── 2. порты ──────────────────────────────────────────────────────────
    журнал("2. Какие порты открыты")
    for порт, что in ((MQTT_PORT, "MQTT, основной протокол"),
                      (CAMERA_PORT, "камера MJPEG"),
                      (3030, "SDCP — так говорит Carbon 1"),
                      (3031, "камера Carbon 1"),
                      (80, "веб-интерфейс"),
                      (7125, "Moonraker")):
        журнал("   %-6d %-28s %s" % (
            порт, что, "открыт" if порт_открыт(адрес, порт) else "закрыт"))
    журнал("")

    if not серийный:
        журнал("Серийный номер неизвестен, а без него не составить темы MQTT.")
        журнал("Найди его в настройках принтера и запусти снова с --sn <номер>.")
        журнал.close()
        return 1

    # ── 3. подключение ────────────────────────────────────────────────────
    client_id = случайный_id()
    журнал("3. Подключение к MQTT %s:%d" % (адрес, MQTT_PORT))
    журнал("   серийный номер: %s" % серийный)
    журнал("   наш client_id : %s" % client_id)
    соединение = mqtt.Client(адрес, MQTT_PORT, client_id=client_id,
                             username="elegoo", password=арг.code,
                             keepalive=30, timeout=10)
    try:
        соединение.connect()
    except mqtt.MqttError as e:
        журнал("   не подключились: %s" % e)
        журнал("")
        журнал("Если написано про код доступа — проверь, что на принтере включён "
               "режим LAN Only, и возьми код с того же экрана.")
        журнал.close()
        return 1
    журнал("   подключились")
    журнал("")

    основа = "elegoo/%s" % серийный
    try:
        # ── 4. подписка на всё ────────────────────────────────────────────
        журнал("4. Подписываемся на %s/# и слушаем" % основа)
        соединение.subscribe(["%s/#" % основа])

        # ── 5. регистрация ────────────────────────────────────────────────
        request_id = случайный_id(24)
        журнал("5. Регистрируемся")
        соединение.publish("%s/api_register" % основа, json.dumps(
            {"client_id": client_id, "request_id": request_id}))

        # ── 6. читающие запросы ───────────────────────────────────────────
        время_запросов = time.time() + 5
        очередь = list(READ_ONLY_METHODS)
        следующий_ping = time.time() + 10
        номер = 0

        конец = time.time() + max(10, арг.seconds)
        журнал("6. Слушаем %d секунд. Всё, что придёт, попадёт в журнал." % арг.seconds)
        журнал("")
        сообщений = 0
        темы = {}
        while time.time() < конец:
            if time.time() >= следующий_ping:
                следующий_ping = time.time() + 10
                соединение.publish("%s/%s/api_request" % (основа, client_id),
                                   json.dumps({"type": "PING"}))
                журнал("   -> PING", на_экран=False)

            # Запросы шлём по одному и с паузой: принтер глушит ответы, если
            # запросов подряд больше трёх.
            if очередь and time.time() >= время_запросов:
                метод, описание = очередь.pop(0)
                номер += 1
                время_запросов = time.time() + 4
                тело = {"id": номер, "method": метод, "params": {}}
                соединение.publish("%s/%s/api_request" % (основа, client_id),
                                   json.dumps(тело))
                журнал("   -> запрос %d (%s): %s" % (метод, описание, json.dumps(тело)))

            полученное = соединение.poll()
            if not полученное:
                continue
            тема, тело = полученное
            сообщений += 1
            темы[тема] = темы.get(тема, 0) + 1
            текст = тело.decode("utf-8", "replace")
            журнал("   <- [%s] %s" % (тема, текст[:2000]), на_экран=False)
            if сообщений <= 5:
                print("   <- %s (%d байт)" % (тема, len(тело)))
    except mqtt.MqttError as e:
        журнал("   связь прервалась: %s" % e)
    except KeyboardInterrupt:
        журнал("   остановлено с клавиатуры")
    finally:
        соединение.close()

    журнал("")
    журнал("Итог: сообщений %d" % сообщений)
    for тема in sorted(темы):
        журнал("   %-52s %d" % (тема, темы[тема]))
    журнал("")
    журнал("Журнал: %s" % путь)
    журнал("Код доступа в него не попал. Серийный номер попал — если не хочешь "
           "его показывать, замени перед отправкой.")
    журнал.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
