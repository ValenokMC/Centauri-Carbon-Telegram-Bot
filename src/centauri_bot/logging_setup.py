# -*- coding: utf-8 -*-
"""Rotating log files that are safe to attach to a bug report.

A log the user cannot safely send is a log that does not help anybody. So a
filter runs over every record, on the way to both the file and the console,
and rewrites anything that looks like a bot token or a Telegram API path.

The filter is a backstop, not the primary defence: telegram_api never puts the
token in a message in the first place. It exists because a future edit, or a
library we do not control, might.
"""
import logging
import logging.handlers
import os
import re

from . import paths


LOG_NAME = "centauri-bot.log"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3

# A BotFather token anywhere in free text.
TOKEN_RE = re.compile(r"\b(\d{6,12}):([A-Za-z0-9_-]{30,})")
# The same thing embedded in an API path, which is how it would most likely
# escape: "/bot123456:AA.../sendMessage".
BOT_PATH_RE = re.compile(r"/bot(\d{6,12}):[A-Za-z0-9_-]{30,}")


def scrub(text):
    """Replace any token-shaped substring with a redacted form."""
    if not text:
        return text
    text = BOT_PATH_RE.sub(lambda m: "/bot%s:***" % m.group(1), text)
    text = TOKEN_RE.sub(lambda m: "%s:***" % m.group(1), text)
    return text


class RedactingFilter(logging.Filter):
    """Scrubs the formatted message and every argument."""

    def filter(self, record):
        try:
            if isinstance(record.msg, str):
                record.msg = scrub(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: scrub(v) if isinstance(v, str) else v
                                   for k, v in record.args.items()}
                else:
                    record.args = tuple(scrub(a) if isinstance(a, str) else a
                                        for a in record.args)
        except Exception:
            # A logging filter must never be the thing that crashes the bot.
            pass
        return True


def configure(level="INFO", to_console=True, directory=None):
    """Set up the root logger. Safe to call twice - handlers are replaced."""
    directory = directory or paths.logs_dir()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    numeric = getattr(logging, str(level).upper(), logging.INFO)
    root.setLevel(numeric)
    redactor = RedactingFilter()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    handler = logging.handlers.RotatingFileHandler(
        os.path.join(directory, LOG_NAME), maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT, encoding="utf-8")
    handler.setFormatter(fmt)
    handler.addFilter(redactor)
    root.addHandler(handler)

    if to_console:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        console.addFilter(redactor)
        root.addHandler(console)

    return root


def log_path(directory=None):
    return os.path.join(directory or paths.logs_dir(), LOG_NAME)
