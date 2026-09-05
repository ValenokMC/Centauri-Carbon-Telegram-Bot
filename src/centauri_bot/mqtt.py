# -*- coding: utf-8 -*-
"""A very small MQTT 3.1.1 client, standard library only.

The Centauri Carbon 2 speaks JSON-RPC over its own MQTT broker, so supporting
it needs an MQTT client. Pulling in paho-mqtt would end this project's one
genuinely useful property - it installs by unpacking an archive, with nothing
to fetch - so the handful of packet types the printer actually uses are
implemented here instead: CONNECT, SUBSCRIBE, PUBLISH, PINGREQ, DISCONNECT.

Deliberately absent: QoS 1 and 2, retained-message bookkeeping, TLS, will
messages, topic aliases. The printer needs none of them, and every one of them
is a piece of state that could go wrong unattended.
"""
import socket
import struct
import threading

CONNECT = 0x10
CONNACK = 0x20
PUBLISH = 0x30
SUBSCRIBE = 0x80
SUBACK = 0x90
PINGREQ = 0xC0
PINGRESP = 0xD0
DISCONNECT = 0xE0

CONNACK_REASONS = {
    0: "ok",
    1: "брокер не поддерживает MQTT 3.1.1",
    2: "идентификатор клиента отклонён",
    3: "сервис недоступен",
    4: "неверный логин или код доступа",
    5: "подключение не авторизовано",
}


class MqttError(Exception):
    """A safe, human-readable MQTT failure."""


def _encode_length(value):
    """MQTT's variable-length integer: seven bits per byte, top bit continues."""
    out = bytearray()
    while True:
        byte = value % 128
        value //= 128
        if value:
            byte |= 0x80
        out.append(byte)
        if not value:
            return bytes(out)


def _encode_string(text):
    raw = str(text).encode("utf-8")
    if len(raw) > 0xFFFF:
        raise MqttError("строка длиннее, чем допускает MQTT")
    return struct.pack(">H", len(raw)) + raw


class Client(object):
    """One connection to one broker. Not shared between threads for writing."""

    def __init__(self, host, port=1883, client_id="", username="", password="",
                 keepalive=30, timeout=10):
        self.host = str(host)
        self.port = int(port)
        self.client_id = str(client_id)
        self.username = str(username or "")
        self.password = str(password or "")
        self.keepalive = max(5, int(keepalive))
        self.timeout = max(1, float(timeout))
        self.sock = None
        self._buffer = b""
        self._packet_id = 0
        self._send_lock = threading.Lock()

    # ----------------------------------------------------------- соединение

    def connect(self):
        try:
            self.sock = socket.create_connection((self.host, self.port),
                                                 timeout=self.timeout)
        except OSError as e:
            raise MqttError("нет связи с брокером: %s" % e)
        self.sock.settimeout(self.timeout)

        flags = 0x02                       # clean session
        payload = _encode_string(self.client_id)
        if self.username:
            flags |= 0x80
            payload += _encode_string(self.username)
            if self.password:
                flags |= 0x40
                payload += _encode_string(self.password)
        variable = (_encode_string("MQTT") + bytes([4, flags])
                    + struct.pack(">H", self.keepalive))
        self._send(CONNECT, variable + payload)

        kind, body = self._read_packet()
        if kind != CONNACK or len(body) < 2:
            raise MqttError("брокер ответил не тем пакетом")
        code = body[1]
        if code:
            raise MqttError(CONNACK_REASONS.get(code, "отказ брокера, код %d" % code))

    def close(self):
        sock, self.sock = self.sock, None
        if not sock:
            return
        try:
            self._send_raw(sock, DISCONNECT, b"")
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ------------------------------------------------------------- операции

    def subscribe(self, topics):
        """Subscribe to a list of topic filters at QoS 0."""
        topics = [topics] if isinstance(topics, str) else list(topics)
        if not topics:
            return
        self._packet_id = (self._packet_id % 0xFFFF) + 1
        body = struct.pack(">H", self._packet_id)
        for topic in topics:
            body += _encode_string(topic) + b"\x00"
        self._send(SUBSCRIBE | 0x02, body)

    def publish(self, topic, payload):
        raw = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
        self._send(PUBLISH, _encode_string(topic) + raw)

    def ping(self):
        self._send(PINGREQ, b"")

    def poll(self):
        """Next (topic, payload) or None when nothing arrived before the timeout.

        Broker housekeeping - PINGRESP and SUBACK - is swallowed here so callers
        only ever see real messages.
        """
        try:
            kind, body = self._read_packet()
        except socket.timeout:
            return None
        if kind & 0xF0 != PUBLISH:
            return None
        if len(body) < 2:
            raise MqttError("испорченный пакет PUBLISH")
        length = struct.unpack(">H", body[:2])[0]
        topic = body[2:2 + length].decode("utf-8", "replace")
        payload = body[2 + length:]
        if kind & 0x06:                    # QoS > 0 carries a packet id we skip
            payload = payload[2:]
        return topic, payload

    # ------------------------------------------------------------- каркас

    def _send(self, kind, body):
        sock = self.sock
        if sock is None:
            raise MqttError("соединение закрыто")
        self._send_raw(sock, kind, body)

    def _send_raw(self, sock, kind, body):
        packet = bytes([kind]) + _encode_length(len(body)) + body
        with self._send_lock:
            try:
                sock.sendall(packet)
            except OSError as e:
                raise MqttError("не удалось отправить пакет: %s" % e)

    def _recv(self, count):
        while len(self._buffer) < count:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                raise
            except OSError as e:
                raise MqttError("обрыв соединения: %s" % e)
            if not chunk:
                raise MqttError("брокер закрыл соединение")
            self._buffer += chunk
        head, self._buffer = self._buffer[:count], self._buffer[count:]
        return head

    def _read_packet(self):
        kind = self._recv(1)[0]
        length, shift = 0, 0
        for _ in range(4):
            byte = self._recv(1)[0]
            length += (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        else:
            raise MqttError("слишком длинный заголовок пакета")
        if length > 4_000_000:
            raise MqttError("пакет слишком большой")
        return kind, self._recv(length) if length else b""
