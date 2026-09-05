# -*- coding: utf-8 -*-
"""First-run setup. The user never opens a Python file.

The wizard does three things a person should not have to do by hand: it checks
that the environment can run the bot at all, it finds the chat id by itself
(instead of sending the user to a third-party "what is my id" bot, which means
handing a stranger your account), and it verifies that the printer is actually
reachable before writing anything.

Nothing is written until the very last step, and re-running the wizard keeps
every existing answer as a default and never touches state.json, the
maintenance counter or the logs.

The Telegram API object is injected, so the test suite drives the whole flow
against a fake and never contacts Telegram.
"""
import getpass
import sys
import time

from . import backend
from . import detect as detect_mod
from . import config as config_mod
from . import moonraker
from . import paths
from . import sdcp
from . import storage
from .telegram_api import TelegramAPI, TelegramError


MIN_PYTHON = (3, 9)

BOLD, DIM, RED, GREEN, YELLOW, RESET = (
    "\033[1m", "\033[90m", "\033[31m", "\033[32m", "\033[33m", "\033[0m")

YES = {"д", "да", "y", "yes", "1", "+"}
NO = {"н", "нет", "n", "no", "0", "-"}


class SetupCancelled(Exception):
    pass


# ------------------------------------------------------------------ console io

def say(text=""):
    print(text)


def head(text):
    say("\n%s%s%s" % (BOLD, text, RESET))


def ok(text):
    say("  %s✓%s %s" % (GREEN, RESET, text))


def warn(text):
    say("  %s!%s %s" % (YELLOW, RESET, text))


def bad(text):
    say("  %s✗%s %s" % (RED, RESET, text))


def ask(prompt, default=None):
    tail = " [%s]" % default if default else ""
    while True:
        try:
            raw = input("  %s%s: " % (prompt, tail)).strip()
        except (EOFError, KeyboardInterrupt):
            raise SetupCancelled()
        if raw:
            return raw
        if default is not None:
            return default


def ask_yes(prompt, default=True):
    hint = " [Д/н]" if default else " [д/Н]"
    while True:
        try:
            raw = input("  %s%s: " % (prompt, hint)).strip().lower()
        except (EOFError, KeyboardInterrupt):
            raise SetupCancelled()
        if not raw:
            return default
        if raw in YES:
            return True
        if raw in NO:
            return False
        bad("Нужно «д» или «н». Enter — вариант по умолчанию.")


def ask_choice(prompt, options):
    """options: list of (key, label). Returns key."""
    say()
    for n, (_, label) in enumerate(options, start=1):
        say("   %2d  %s" % (n, label))
    while True:
        raw = ask(prompt)
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        bad("Нужен номер от 1 до %d." % len(options))


def ask_secret(prompt):
    """Read a token without echoing it.

    getpass shows nothing at all, which is stronger than masking with stars and
    is what a terminal can actually guarantee. If the console does not support
    it (a redirected stdin, some IDE consoles), fall back to a plain read and
    say plainly that the value will be visible.
    """
    try:
        return getpass.getpass("  %s: " % prompt).strip()
    except (getpass.GetPassWarning, EOFError, OSError):
        warn("Этот терминал не умеет скрывать ввод — токен будет виден на экране.")
        return ask(prompt)


# ------------------------------------------------------------------ steps

def check_python():
    head("1. Python")
    version = sys.version_info
    say("  Найден Python %d.%d.%d" % version[:3])
    if version < MIN_PYTHON:
        bad("Нужен Python %d.%d или новее." % MIN_PYTHON)
        return False
    ok("Версия подходит.")
    return True


def ask_token(existing=None):
    head("2. Токен бота")
    say("  %sОткрой @BotFather в Telegram, отправь /newbot и получи токен." % DIM)
    say("  Он выглядит так: 123456789:AA... — при вводе показан не будет.%s" % RESET)
    if existing:
        say("  Сейчас настроен: %s" % config_mod.redact(existing))
        if not ask_yes("Заменить токен?", default=False):
            return existing
    while True:
        token = ask_secret("Токен от BotFather")
        if config_mod.valid_token(token):
            ok("Формат токена правильный: %s" % config_mod.redact(token))
            return token
        bad("Это не похоже на токен BotFather (ожидается «цифры:буквы»).")
        if not ask_yes("Попробовать снова?", default=True):
            raise SetupCancelled()


def verify_token(token, api_factory=TelegramAPI):
    """getMe against the real API. Returns the bot's username, or None."""
    head("3. Проверка токена")
    api = api_factory(token)
    try:
        me = api.get_me()
    except TelegramError as e:
        bad("Telegram отклонил токен: %s" % e)
        return None
    except Exception as e:
        warn("Не удалось связаться с Telegram (%r)." % e)
        warn("Проверить токен сейчас нельзя — настройку можно продолжить.")
        return None
    username = me.get("username")
    ok("Токен рабочий. Бот: @%s" % username)
    return username


def find_chat_id(token, username=None, api_factory=TelegramAPI,
                 attempts=20, pause=3.0, sleeper=time.sleep):
    """Get the owner's chat id from getUpdates, with no third-party bot.

    Asking the user to message a stranger's "get my id" bot in order to set up
    a private one has always been the worst step of this kind of setup. The id
    is already in our own updates; we just have to wait for one.
    """
    head("4. Твой chat_id")
    where = ("@%s" % username) if username else "своего бота"
    say("  Открой %s в Telegram и нажми /start." % where)
    say("  %sЖду сообщение — до %d секунд.%s" % (DIM, int(attempts * pause), RESET))

    api = api_factory(token)
    seen = {}
    for _ in range(attempts):
        answer = api.get_updates(timeout=1)
        for update in answer.get("result", []):
            message = update.get("message") or {}
            sender = message.get("from") or {}
            chat = message.get("chat") or {}
            if sender.get("is_bot") or not chat.get("id"):
                continue
            seen[str(chat["id"])] = _describe(sender, chat)
        if seen:
            break
        sleeper(pause)

    if not seen:
        bad("Сообщений не пришло.")
        say("  %sВозможные причины: бот ещё не запущен командой /start, "
            "или токен от другого бота.%s" % (DIM, RESET))
        if ask_yes("Ввести chat_id вручную?", default=False):
            while True:
                raw = ask("chat_id (число)")
                if config_mod.valid_chat_id(raw):
                    return raw
                bad("chat_id — это число, иногда со знаком минус.")
        return None

    if len(seen) == 1:
        chat_id, label = next(iter(seen.items()))
        ok("Нашёл: %s" % label)
        return chat_id

    # Never pick for the user here. Whoever ends up in this field becomes the
    # only person the bot obeys, and silently choosing the first of several
    # would hand control of a printer to whoever happened to write first.
    warn("Писали несколько человек — выбери, кто владелец бота.")
    options = [(cid, label) for cid, label in sorted(seen.items())]
    return ask_choice("Номер владельца", options)


def _describe(sender, chat):
    name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x)
    username = sender.get("username")
    return "%s%s · chat_id %s" % (name or "без имени",
                                  (" @%s" % username) if username else "",
                                  chat.get("id"))


НАЗВАНИЯ_РЕЖИМОВ = {
    backend.SDCP: "Штатная прошивка Elegoo V1.4.x (SDCP)",
    backend.MOONRAKER: "OpenCentauri / COSMOS (Moonraker)",
}


def ask_backend(host=None, existing=None):
    """Determine the firmware, asking only when the printer stays silent."""
    head("6. Прошивка и протокол")
    if existing in backend.BACKENDS and not ask_yes(
            "Сменить прежний режим %s?" % existing, default=False):
        return existing, ""

    угадано, url, пояснение = "", "", ""
    if host:
        say("  %sСпрашиваю сам принтер, что на нём стоит...%s" % (DIM, RESET))
        угадано, url, пояснение = detect_mod.detect(host)
    if угадано:
        ok(пояснение)
        if ask_yes("Использовать режим «%s»?" % НАЗВАНИЯ_РЕЖИМОВ[угадано],
                   default=True):
            return угадано, url
    elif пояснение:
        warn(пояснение)

    say("  %sОпределить не вышло — выбери вручную.%s" % (DIM, RESET))
    return ask_choice("Что установлено на принтере", [
        (backend.SDCP, НАЗВАНИЯ_РЕЖИМОВ[backend.SDCP]),
        (backend.MOONRAKER, НАЗВАНИЯ_РЕЖИМОВ[backend.MOONRAKER]),
    ]), ""


def ask_printer(existing=None):
    head("5. Принтер")
    say("  %sАдрес принтера в локальной сети. Его видно на экране принтера:" % DIM)
    say("  Настройки → Сеть. Обычно это 192.168.x.x.%s" % RESET)
    while True:
        host = ask("IP-адрес или имя принтера", default=existing)
        if config_mod.valid_host(host):
            return host
        bad("Это не похоже на IP-адрес или имя хоста.")


def ask_moonraker(host, existing, detected_url=""):
    head("7. Moonraker")
    # Адрес, на который Moonraker уже ответил при определении прошивки, важнее
    # старой записи в конфиге: он проверен только что.
    default_url = (detected_url or existing.get("moonraker_url")
                   or ("http://%s" % host))
    while True:
        url = ask("Адрес Moonraker", default=default_url)
        if moonraker.valid_base_url(url):
            break
        bad("Нужен полный адрес http://... или https://... без логина и пароля.")

    current_key = existing.get("moonraker_api_key") or ""
    if current_key:
        key = (ask_secret("Новый API key") if ask_yes(
            "Заменить сохранённый API key?", default=False) else current_key)
    elif ask_yes("Moonraker требует API key?", default=False):
        key = ask_secret("API key Moonraker")
    else:
        key = ""
    return url, key


def check_printer(host, backend_name=backend.SDCP, moonraker_url="",
                  moonraker_api_key=""):
    """Probe both ports and report them separately.

    Status and camera are separate services on the printer, and they fail
    separately: a working printer with the camera disabled is a normal setup,
    not a broken one, and telling the user "printer unreachable" in that case
    would send them hunting for the wrong problem.
    """
    head("8. Связь с принтером")
    if backend_name == backend.MOONRAKER:
        try:
            client = moonraker.Client(
                moonraker_url or ("http://%s" % host),
                api_key=moonraker_api_key)
            client.status()
            status_ok = True
            ok("Moonraker отвечает и передаёт состояние Klipper.")
        except (ValueError, moonraker.MoonrakerError) as e:
            status_ok = False
            bad("Moonraker не ответил: %s" % e)
        try:
            camera_ok = status_ok and client.camera_available()
        except moonraker.MoonrakerError:
            camera_ok = False
        if camera_ok:
            ok("Moonraker сообщает адрес снимка камеры.")
        else:
            warn("Камера через Moonraker не найдена — бот будет без фотографий.")
        return status_ok, camera_ok

    status_ok = sdcp.tcp_reachable(host, sdcp.WS_PORT)
    camera_ok = sdcp.tcp_reachable(host, sdcp.CAMERA_PORT)

    if status_ok:
        ok("Порт состояния %d открыт — принтер отвечает." % sdcp.WS_PORT)
    else:
        bad("Порт состояния %d закрыт." % sdcp.WS_PORT)
        say("  %sПроверь: принтер включён, в той же сети, адрес введён верно.%s"
            % (DIM, RESET))

    if camera_ok:
        ok("Порт камеры %d открыт — снимки будут." % sdcp.CAMERA_PORT)
    else:
        warn("Порт камеры %d закрыт — бот будет работать без фотографий."
             % sdcp.CAMERA_PORT)

    return status_ok, camera_ok


def ask_mode(existing=None, backend_name=backend.SDCP):
    head("10. Режим работы")
    default_control = ((backend_name == backend.SDCP) if existing is None
                       else bool(existing))
    control_label = (
        "Мониторинг и управление — плюс пауза, продолжение и отмена"
        if backend_name == backend.MOONRAKER else
        "Мониторинг и управление — плюс пауза, стоп, свет, нагрев")
    mode = ask_choice("Что разрешить боту", [
        ("watch", "Только мониторинг — состояние, уведомления, снимки"),
        ("control", control_label),
    ]) if existing is None else (
        "control" if ask_yes("Разрешить управление принтером?",
                             default=default_control) else "watch")
    return mode == "control"


# ------------------------------------------------------------------ the flow

def run(api_factory=TelegramAPI, argv=None):
    say("%s=== Настройка Telegram-бота для Centauri Carbon ===%s" % (BOLD, RESET))
    say("Данные сохраняются в: %s" % paths.data_dir())

    existing = {}
    try:
        existing = config_mod.load()
        say("%sНайдена прежняя настройка — прежние ответы предложены как "
            "значения по умолчанию.%s" % (DIM, RESET))
    except config_mod.ConfigError:
        pass

    if not check_python():
        return 1

    try:
        token = ask_token(existing.get("telegram_token") or None)
        username = verify_token(token, api_factory=api_factory)

        chat_id = existing.get("chat_id") or None
        if chat_id and not ask_yes(
                "Прежний chat_id %s. Определить заново?" % chat_id, default=False):
            pass
        else:
            found = find_chat_id(token, username, api_factory=api_factory)
            if found:
                chat_id = found
        if not config_mod.valid_chat_id(chat_id):
            bad("Без chat_id бот не сможет тебе писать. Настройка не завершена.")
            return 1

        # Адрес спрашиваем раньше режима: зная адрес, можно спросить сам
        # принтер, какая на нём прошивка, вместо того чтобы гадать вслепую.
        host = ask_printer(existing.get("printer_ip") or None)
        backend_name, detected_url = ask_backend(
            host, existing.get("backend") if existing else None)
        moonraker_url, moonraker_api_key = "", ""
        if backend_name == backend.MOONRAKER:
            moonraker_url, moonraker_api_key = ask_moonraker(
                host, existing, detected_url)
        status_ok, camera_ok = check_printer(
            host, backend_name, moonraker_url, moonraker_api_key)
        if not status_ok and not ask_yes(
                "Принтер не отвечает. Всё равно сохранить настройку?", default=True):
            return 1

        head("9. Имя принтера")
        say("  %sТак принтер будет подписан в сообщениях.%s" % (DIM, RESET))
        name = ask("Понятное имя",
                   default=existing.get("printer_name") or "Centauri Carbon")

        allow_control = ask_mode(
            existing.get("allow_control") if existing else None,
            backend_name=backend_name)
        moonraker_job_control = False
        moonraker_remote_start = False
        if backend_name == backend.MOONRAKER and allow_control:
            moonraker_job_control = ask_yes(
                "Разрешить паузу, продолжение и отмену печати?",
                default=bool(existing.get("moonraker_allow_job_control", False)))
            moonraker_remote_start = ask_yes(
                "Разрешить удалённый запуск выбранного файла?",
                default=bool(existing.get("moonraker_allow_remote_start", False)))

        head("11. Анонимная статистика")
        say("  %sПомочь узнать, сколько установок реально используется?%s" % (DIM, RESET))
        say("  Передаются не чаще раза в 30 дней только случайный id установки,")
        say("  версия приложения и название проекта. Без Telegram-данных, IP принтера,")
        say("  имён файлов и статуса печати. Отказ ни на что не влияет.")
        anonymous_statistics = ask_yes(
            "Разрешить анонимную статистику?",
            default=bool(existing.get("anonymous_statistics", False)))
    except SetupCancelled:
        say("\nНастройка прервана. Ничего не сохранено.")
        return 1

    cfg = dict(config_mod.DEFAULTS)
    cfg.update(existing)
    cfg.update({
        "telegram_token": token,
        "chat_id": str(chat_id),
        "printer_ip": host,
        "printer_name": name,
        "backend": backend_name,
        "moonraker_url": moonraker_url,
        "moonraker_api_key": moonraker_api_key,
        "moonraker_allow_job_control": moonraker_job_control,
        "moonraker_allow_remote_start": moonraker_remote_start,
        "allow_control": allow_control,
        "anonymous_statistics": anonymous_statistics,
        "send_photo": bool(camera_ok) if not existing else existing.get("send_photo", True),
        "owner_user_id": (str(chat_id) if not str(chat_id).startswith("-") else
                          str(existing.get("owner_user_id") or "")),
    })

    head("Итог")
    for line in config_mod.summary(cfg):
        say("  " + line)

    if not ask_yes("\n  Сохранить?", default=True):
        say("Ничего не сохранено.")
        return 1

    path = config_mod.save(cfg)
    ok("Настройки записаны: %s" % path)
    # Stamp the install date now, not on first run: the 30-day support interval
    # should count from the moment the user actually set the bot up.
    storage.mark_installed()
    if not anonymous_statistics:
        storage.clear_telemetry()

    say("\n%sГотово.%s Запусти бот: Run.cmd (или Запустить.cmd)" % (GREEN, RESET))
    say("Автозапуск при входе в Windows: Install-Autostart.cmd")
    return 0


def main(argv=None):
    try:
        return run(argv=argv)
    except KeyboardInterrupt:
        say("\nПрервано.")
        return 1
