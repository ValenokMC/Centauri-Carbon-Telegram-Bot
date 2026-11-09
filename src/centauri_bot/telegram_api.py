# -*- coding: utf-8 -*-
"""Telegram Bot API over a persistent HTTPS connection, standard library only.

The token appears in exactly one place — the request path — and never in a log
line, an exception message, or a printed URL. `TelegramError` carries the API
description, not the request.
"""
import collections
import http.client
import json
import logging
import ssl
import threading
import time
import urllib.parse
import uuid


log = logging.getLogger(__name__)

API_HOST = "api.telegram.org"
# The Bot API refuses to hand out anything larger.
MAX_DOWNLOAD_BYTES = 20_000_000


class TelegramError(Exception):
    """Telegram refused a call. Message is the API description, never the URL."""


class TelegramAPI:
    """One instance per bot token.

    A TLS handshake to Telegram costs several seconds; a call on an already
    open connection costs milliseconds. Each thread keeps its own connection,
    because a long-polling getUpdates holds one for tens of seconds at a time.
    """

    def __init__(self, token, timeout=60):
        self._token = token
        self._timeout = timeout
        self._tls = threading.local()
        # Calls made since the last take_counts(), by method: the bot logs
        # them every few minutes to show what a card costs in requests.
        self._counts = collections.Counter()
        self._counts_lock = threading.Lock()

    def take_counts(self):
        with self._counts_lock:
            counts, self._counts = self._counts, collections.Counter()
        return counts

    # -- connection ------------------------------------------------------

    # A connection idle for longer than this is probably dead on the far side
    # (a NAT or the server dropped it) and would only fail after its timeout.
    IDLE_RECONNECT_SEC = 30

    def _conn(self, reset=False, timeout=None):
        conn = getattr(self._tls, "conn", None)
        idle = time.monotonic() - getattr(self._tls, "last_used", 0.0)
        if conn is not None and idle > self.IDLE_RECONNECT_SEC:
            reset = True
        if reset and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            conn = None
        if conn is None:
            conn = http.client.HTTPSConnection(
                API_HOST, timeout=self._timeout, context=ssl.create_default_context())
            self._tls.conn = conn
        # The timeout is per request: a hung call must not hold the caller,
        # and the locks it may hold, for the client's default minute.
        if timeout:
            conn.timeout = timeout
            if conn.sock is not None:
                conn.sock.settimeout(timeout)
        return conn

    def close(self):
        conn = getattr(self._tls, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._tls.conn = None

    # -- raw call --------------------------------------------------------

    def call(self, method, params=None, files=None, timeout=10):
        """Invoke a Bot API method. files: {name: (filename, bytes)}.

        Returns the decoded response dict. Never raises for an API-level
        refusal — callers check ``ok`` — but logs the description.
        """
        path = "/bot%s/%s" % (self._token, method)
        with self._counts_lock:
            self._counts[method] += 1
        if files:
            timeout = max(timeout, 30)     # a picture takes longer to upload
        params = {k: v for k, v in (params or {}).items() if v is not None}
        body, ctype = self._encode(params, files)
        headers = {"Content-Type": ctype, "Content-Length": str(len(body)),
                   "Connection": "keep-alive"}

        # Three attempts: the connection may have gone stale while the bot was
        # idle (the second attempt opens a fresh one), and on 429 we wait for
        # exactly as long as Telegram asks before trying again.
        for attempt in (1, 2, 3):
            try:
                conn = self._conn(reset=(attempt == 2), timeout=timeout)
                conn.request("POST", path, body=body, headers=headers)
                raw = conn.getresponse().read()
                self._tls.last_used = time.monotonic()
                answer = json.loads(raw.decode("utf-8"))
                if answer.get("ok"):
                    return answer
                wait = (answer.get("parameters") or {}).get("retry_after")
                if wait and attempt < 3:
                    time.sleep(min(int(wait) + 1, 10))
                    continue
                log.warning("telegram refused %s: %s", method, answer.get("description"))
                return answer
            except Exception as e:
                # repr(e) on a connection error carries the host, not the path,
                # so the token cannot leak here.
                if attempt == 3:
                    log.warning("telegram unreachable for %s: %r", method, e)
        return {"ok": False}

    @staticmethod
    def _encode(params, files):
        if not files:
            return json.dumps(params, ensure_ascii=False).encode("utf-8"), "application/json"
        boundary = "--wb" + uuid.uuid4().hex
        body = b""
        for k, v in params.items():
            if not isinstance(v, str):
                v = json.dumps(v, ensure_ascii=False)
            body += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                     % (boundary, k, v)).encode("utf-8")
        for k, attachment in files.items():
            if len(attachment) == 2:
                fname, blob = attachment
                mime = "image/jpeg"
            else:
                fname, blob, mime = attachment
            body += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; "
                     "filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"
                     % (boundary, k, fname, mime)).encode("utf-8")
            body += blob + b"\r\n"
        body += ("--%s--\r\n" % boundary).encode("utf-8")
        return body, "multipart/form-data; boundary=" + boundary

    # -- convenience -----------------------------------------------------

    def get_me(self):
        """Identity check used by the setup wizard. Raises on refusal."""
        answer = self.call("getMe")
        if not answer.get("ok"):
            raise TelegramError(answer.get("description") or "Telegram did not answer")
        return answer["result"]

    def get_updates(self, offset=None, timeout=25, allowed=None):
        return self.call("getUpdates",
                         {"offset": offset, "timeout": timeout,
                          "allowed_updates": allowed},
                         timeout=timeout + 15)

    def send_message(self, chat, text, keyboard=None, photo=None):
        p = {"chat_id": chat, "parse_mode": "HTML"}
        if keyboard:
            p["reply_markup"] = {"inline_keyboard": keyboard}
        if photo:
            p["caption"] = text
            is_png = bytes(photo).startswith(b"\x89PNG\r\n\x1a\n")
            attachment = ("height-map.png", photo, "image/png") if is_png else ("snap.jpg", photo)
            return self.call("sendPhoto", p, files={"photo": attachment})
        p["text"] = text
        p["disable_web_page_preview"] = True
        return self.call("sendMessage", p)

    def edit_message(self, chat, message_id, text, keyboard=None,
                     photo=None, is_photo=False):
        """Edit in place. ``is_photo`` — the original carries a picture, so the
        text lives in its caption and editMessageText does not apply."""
        p = {"chat_id": chat, "message_id": message_id, "parse_mode": "HTML"}
        if keyboard:
            p["reply_markup"] = {"inline_keyboard": keyboard}
        if photo:
            p["media"] = {"type": "photo", "media": "attach://photo",
                          "caption": text, "parse_mode": "HTML"}
            return self.call("editMessageMedia", p, files={"photo": ("snap.jpg", photo)})
        if is_photo:
            p["caption"] = text
            return self.call("editMessageCaption", p)
        p["text"] = text
        return self.call("editMessageText", p)

    # -- widgets (Bot API 10.4) --------------------------------------------
    # Plain calls like every other method here: the bot has no Telegram
    # library to upgrade. In production a widget goes to private chats only
    # and cannot be forwarded; an app without widget support shows the
    # caption instead, so the caption must make sense on its own.

    def send_widget(self, chat, html, caption=None, keyboard=None,
                    chat_data=None, silent=False, allow_queries=False):
        """``allow_queries`` lets the widget's own buttons ask the bot."""
        p = {"chat_id": chat, "html": html, "chat_data": chat_data,
             "caption": caption, "parse_mode": "HTML" if caption else None,
             "disable_notification": True if silent else None,
             "allow_queries": True if allow_queries else None}
        if keyboard:
            p["reply_markup"] = {"inline_keyboard": keyboard}
        return self.call("sendWidget", p)

    def answer_widget_query(self, query_id, data=None):
        """Within 15 seconds, or the widget sees TIMEOUT. Only the widget
        that asked receives ``data``; nothing is shown to anyone else."""
        return self.call("answerWidgetQuery",
                         {"widget_query_id": query_id, "data": data})

    def edit_widget_data(self, chat, message_id, chat_data):
        """Silent: no "edited" mark, no notification. "" clears the state."""
        return self.call("editMessageWidgetData",
                         {"chat_id": chat, "message_id": message_id,
                          "chat_data": chat_data})

    def delete_message(self, chat, message_id):
        return self.call("deleteMessage", {"chat_id": chat, "message_id": message_id})

    def answer_callback(self, callback_id, text=None):
        if not callback_id:
            # A button on the live card stands in for an inline button; its
            # widget query is answered on its own, there is no callback.
            return {"ok": True}
        return self.call("answerCallbackQuery",
                         {"callback_query_id": callback_id,
                          "text": text[:190] if text else None})

    def set_my_commands(self, commands):
        return self.call("setMyCommands", {"commands": commands})

    # -- files -----------------------------------------------------------

    def get_file(self, file_id):
        """Where Telegram keeps a document the owner sent. Raises TelegramError."""
        answer = self.call("getFile", {"file_id": file_id})
        if not answer.get("ok"):
            raise TelegramError(answer.get("description") or "Telegram не отдал файл")
        return answer.get("result") or {}

    def download_file(self, file_path, max_bytes=MAX_DOWNLOAD_BYTES):
        """Fetch a file by the path getFile returned.

        Uses a connection of its own: the thread-local one may be in the middle
        of a long poll. The token is in the request path only, and a failure
        says what went wrong, never where.
        """
        file_path = str(file_path or "")
        if not file_path or ".." in file_path.split("/"):
            raise TelegramError("некорректный путь файла")
        path = "/file/bot%s/%s" % (self._token,
                                   urllib.parse.quote(file_path, safe="/"))
        conn = http.client.HTTPSConnection(
            API_HOST, timeout=self._timeout, context=ssl.create_default_context())
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            if response.status != 200:
                raise TelegramError("файл не скачался: HTTP %d" % response.status)
            blob = response.read(max_bytes + 1)
        except TelegramError:
            raise
        except Exception as e:
            raise TelegramError("файл не скачался: %s" % type(e).__name__)
        finally:
            conn.close()
        if len(blob) > max_bytes:
            raise TelegramError("файл больше %d МБ" % (max_bytes // 1_000_000))
        return blob

    def download_document(self, file_id, max_bytes=MAX_DOWNLOAD_BYTES):
        info = self.get_file(file_id)
        if int(info.get("file_size") or 0) > max_bytes:
            raise TelegramError("файл больше %d МБ" % (max_bytes // 1_000_000))
        return self.download_file(info.get("file_path"), max_bytes)
