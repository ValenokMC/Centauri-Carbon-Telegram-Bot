# -*- coding: utf-8 -*-
"""Telegram Bot API over a persistent HTTPS connection, standard library only.

The token appears in exactly one place — the request path — and never in a log
line, an exception message, or a printed URL. `TelegramError` carries the API
description, not the request.
"""
import http.client
import json
import logging
import ssl
import threading
import time
import uuid


log = logging.getLogger(__name__)

API_HOST = "api.telegram.org"


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

    # -- connection ------------------------------------------------------

    def _conn(self, reset=False):
        conn = getattr(self._tls, "conn", None)
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

    def call(self, method, params=None, files=None, timeout=30):
        """Invoke a Bot API method. files: {name: (filename, bytes)}.

        Returns the decoded response dict. Never raises for an API-level
        refusal — callers check ``ok`` — but logs the description.
        """
        path = "/bot%s/%s" % (self._token, method)
        params = {k: v for k, v in (params or {}).items() if v is not None}
        body, ctype = self._encode(params, files)
        headers = {"Content-Type": ctype, "Content-Length": str(len(body)),
                   "Connection": "keep-alive"}

        # Three attempts: the connection may have gone stale while the bot was
        # idle (the second attempt opens a fresh one), and on 429 we wait for
        # exactly as long as Telegram asks before trying again.
        for attempt in (1, 2, 3):
            try:
                conn = self._conn(reset=(attempt == 2))
                conn.request("POST", path, body=body, headers=headers)
                raw = conn.getresponse().read()
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
        for k, (fname, blob) in files.items():
            body += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; "
                     "filename=\"%s\"\r\nContent-Type: image/jpeg\r\n\r\n"
                     % (boundary, k, fname)).encode("utf-8")
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
            return self.call("sendPhoto", p, files={"photo": ("snap.jpg", photo)})
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

    def delete_message(self, chat, message_id):
        return self.call("deleteMessage", {"chat_id": chat, "message_id": message_id})

    def answer_callback(self, callback_id, text=None):
        return self.call("answerCallbackQuery",
                         {"callback_query_id": callback_id,
                          "text": text[:190] if text else None})

    def set_my_commands(self, commands):
        return self.call("setMyCommands", {"commands": commands})
