# -*- coding: utf-8 -*-
"""SDCP 3.0.0 over a hand-rolled WebSocket, plus the MJPEG camera grab.

The command codes are taken from the printer's own web interface (main.js),
not guessed. Verified against Centauri Carbon firmware V1.4.49.

This module is deliberately a near-literal copy of the code that has been
running against a real printer: the framing, the timeouts and the reconnection
behaviour are all load-bearing, and rewriting them for tidiness would throw
away the only thing that makes them trustworthy.
"""
import base64
import json
import logging
import os
import socket
import struct
import threading
import time
import uuid


log = logging.getLogger(__name__)

# --- SDCP command codes (from the printer's web interface) ---
CMD_STATUS = 0
CMD_ATTR = 1
CMD_START = 128
CMD_PAUSE = 129
CMD_STOP = 130
CMD_RESUME = 131
CMD_FILE_LIST = 258
CMD_HISTORY = 320
CMD_SET = 403          # EDIT_PRINTER_STATUS_DATA: light, speed, heat, fans

WS_PORT = 3030
WS_PATH = "/websocket"
CAMERA_PORT = 3031


class PrinterOffline(Exception):
    """No usable connection to the printer right now."""


class WS:
    """Minimal client-side WebSocket. Only what SDCP needs."""

    def __init__(self, host, port=WS_PORT, path=WS_PATH, connect_timeout=10,
                 read_timeout=45):
        self.lock = threading.Lock()
        self.sock = socket.socket()
        self.sock.settimeout(connect_timeout)
        self.sock.connect((host, port))
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            "GET %s HTTP/1.1\r\nHost: %s:%d\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
            % (path, host, port, key)
        ).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("printer closed the connection")
            head += chunk
        first, _, rest = head.partition(b"\r\n\r\n")
        if b"101" not in first.split(b"\r\n")[0]:
            raise ConnectionError("upgrade rejected")
        self.buf = rest
        self.sock.settimeout(read_timeout)
        self.mainboard = ""

    def _frame(self, op, payload=b""):
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        hdr = bytes([0x80 | op])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        with self.lock:
            self.sock.sendall(hdr + mask + masked)

    def command(self, cmd, data=None, mainboard=None):
        """Send an SDCP command; returns the RequestID to match the Ack against."""
        mb = mainboard if mainboard is not None else self.mainboard
        rid = uuid.uuid4().hex
        self._frame(1, json.dumps({
            "Id": uuid.uuid4().hex,
            "Data": {"Cmd": cmd, "Data": data or {}, "RequestID": rid,
                     "MainboardID": mb, "TimeStamp": int(time.time()), "From": 0},
            "Topic": "sdcp/request/%s" % mb,
        }).encode())
        return rid

    def messages(self):
        """Yield decoded text payloads until the connection dies."""
        while True:
            while True:
                d = self.buf
                if len(d) < 2:
                    break
                b0, b1 = d[0], d[1]
                op, ln, i = b0 & 0x0F, b1 & 0x7F, 2
                if ln == 126:
                    if len(d) < 4:
                        break
                    ln, i = struct.unpack(">H", d[2:4])[0], 4
                elif ln == 127:
                    if len(d) < 10:
                        break
                    ln, i = struct.unpack(">Q", d[2:10])[0], 10
                if b1 & 0x80:
                    i += 4
                if len(d) < i + ln:
                    break
                payload, self.buf = d[i:i + ln], d[i + ln:]
                if op == 8:
                    raise ConnectionError("printer sent close")
                if op == 9:
                    self._frame(10, payload)
                elif op in (1, 2):
                    yield payload.decode("utf-8", "replace")
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("connection dropped")
            self.buf += chunk

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def grab_frame(host, timeout=5, port=CAMERA_PORT, max_bytes=4_000_000):
    """One JPEG from the printer's MJPEG stream, or None.

    Reads until a complete SOI..EOI pair is in the buffer, then stops. The
    stream never ends on its own, so a byte cap and a deadline are the only
    things that make this return.
    """
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.sendall(b"GET /video HTTP/1.0\r\nHost: %s\r\n\r\n" % host.encode())
        buf = b""
        started = time.time()
        while time.time() - started < timeout:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
            a = buf.find(b"\xff\xd8")
            b = buf.find(b"\xff\xd9", a + 2)
            if a >= 0 and b > a:
                return buf[a:b + 2]
            if len(buf) > max_bytes:
                break
    except Exception as e:
        log.debug("camera unavailable: %r", e)
    finally:
        s.close()
    return None


def tcp_reachable(host, port, timeout=3):
    """Is a TCP port open? Used by the setup wizard's connectivity check."""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass
