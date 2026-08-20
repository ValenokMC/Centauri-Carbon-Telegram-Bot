# -*- coding: utf-8 -*-
"""Links to the author, and the once-a-month support note.

Two rules shape everything here.

First, the support button is a permanent part of the project. It lives on the
/help screen, always, and there is no setting that hides it.

Second, it is never allowed to become nagging. It is kept off the status
message, off every notification, off dangerous confirmations, off error and
connection-loss messages. The only unprompted appearance is a two-line note
appended to an already-positive message - a finished print - and at most once
every 30 days.

Nothing is measured, reported or phoned home. Whether the button was ever
pressed is not knowable from here, by design.
"""
import time


# The single source of truth for the author's links. Changing the Tribute
# address means changing these two lines and nothing else.
#
# Two forms of the same page: the t.me deep link opens Tribute inside Telegram,
# which is what an inline button in a chat should do; the web address is for
# README, FUNDING.yml and anywhere a browser will open it.
TRIBUTE_URL_TELEGRAM = "https://t.me/tribute/app?startapp=dP54"
TRIBUTE_URL_WEB = "https://web.tribute.tg/d/P54"

GITHUB_URL = "https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot"
ISSUES_URL = GITHUB_URL + "/issues"
DOCS_URL = GITHUB_URL + "#readme"
SUPPORT_BOT_URL = "https://t.me/SupporBiBot?start=centauri_bot"

REMINDER_INTERVAL_DAYS = 30
REMINDER_INTERVAL_SEC = REMINDER_INTERVAL_DAYS * 86400

SUPPORT_BUTTON = {"text": "☕ Поддержать автора", "url": TRIBUTE_URL_TELEGRAM}

# Short on purpose. Two lines, no timer, no counter, no second ask.
REMINDER_TEXT = ("\n\n<i>Если бот оказался полезен, "
                 "можно поддержать его развитие ☕</i>")


def help_keyboard():
    """The /help screen. This is the one place the support button always is."""
    return [
        [{"text": "📖 Документация", "url": DOCS_URL}],
        [{"text": "🐛 Сообщить об ошибке", "url": ISSUES_URL},
         {"text": "💬 Написать автору", "url": SUPPORT_BOT_URL}],
        [dict(SUPPORT_BUTTON)],
        [{"text": "↩️ Вернуться к принтеру", "callback_data": "refresh"}],
    ]


def reminder_keyboard():
    """One button under the monthly note. Nothing else."""
    return [[dict(SUPPORT_BUTTON)]]


def due(state, now=None, interval_sec=REMINDER_INTERVAL_SEC):
    """Is the monthly note due?

    Three conditions, all required:
      * the install date is known - an unstamped state means the wizard has not
        finished, and a brand-new user is the last person to ask;
      * at least one interval has passed since installation, so nobody is asked
        in their first month;
      * at least one interval has passed since the note was last shown.

    ``state`` is the dict from storage.load_state(). Both timestamps live on
    disk, which is what makes the interval survive a restart: a bot that is
    restarted twice a day must not ask twice a day.
    """
    now = time.time() if now is None else now
    installed = state.get("installed_at")
    if not installed:
        return False
    if now - installed < interval_sec:
        return False
    last = state.get("last_support_reminder_at")
    if last and now - last < interval_sec:
        return False
    return True


def mark_shown(state, now=None):
    """Stamp the note as shown. Call only after it really reached the user.

    Stamping before delivery would silently swallow a whole month whenever
    Telegram happened to be unreachable at that moment.
    """
    state["last_support_reminder_at"] = time.time() if now is None else now
    return state
