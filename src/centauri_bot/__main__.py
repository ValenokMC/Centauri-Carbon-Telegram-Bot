# -*- coding: utf-8 -*-
"""Entry point: python -m centauri_bot [run|setup|autostart|check]"""
import sys

from . import config as config_mod
from . import detect as detect_mod
from . import logging_setup
from . import paths


USAGE = """Использование:
  python -m centauri_bot           запустить бот
  python -m centauri_bot setup     мастер настройки
  python -m centauri_bot check     проверить настройку и связь, ничего не менять
  python -m centauri_bot autostart установить автозапуск
  python -m centauri_bot autostart remove   удалить автозапуск
  python -m centauri_bot where     показать каталог с данными
"""


def cmd_run():
    from .app import Bot
    try:
        cfg = config_mod.load_valid()
    except config_mod.ConfigError as e:
        print(str(e))
        return 1
    logging_setup.configure(cfg.get("log_level", "INFO"))
    try:
        cfg = detect_mod.resolve(cfg, log=print)
    except LookupError as e:
        print("Определить прошивку не вышло: %s" % e)
        return 1
    Bot(cfg).run()
    return 0


def cmd_check():
    """Read-only diagnosis. Touches nothing, contacts no printer control."""
    from . import backend
    from . import moonraker
    from . import sdcp
    try:
        cfg = config_mod.load()
    except config_mod.ConfigError as e:
        print(str(e))
        return 1
    print("Каталог данных: %s" % paths.data_dir())
    print("")
    for line in config_mod.summary(cfg):
        print("  " + line)
    problems = config_mod.validate(cfg)
    print("")
    if problems:
        print("Проблемы в настройке:")
        for p in problems:
            print("  - %s" % p)
        return 1
    host = cfg["printer_ip"]
    if backend.name(cfg) == backend.MOONRAKER:
        client = moonraker.Client(
            cfg.get("moonraker_url") or ("http://%s" % host),
            api_key=cfg.get("moonraker_api_key", ""),
            timeout=cfg.get("moonraker_timeout_sec", 5),
            camera_url=cfg.get("moonraker_camera_url", ""),
            allow_external_camera=cfg.get(
                "moonraker_allow_external_camera", False))
        try:
            client.status()
            status_ok = True
            print("  Moonraker       отвечает")
        except moonraker.MoonrakerError as e:
            status_ok = False
            print("  Moonraker       ошибка: %s" % e)
        try:
            camera_ok = status_ok and client.camera_available()
        except moonraker.MoonrakerError:
            camera_ok = False
        print("  Камера          %s" % ("настроена" if camera_ok else "не найдена"))
    else:
        status_ok = sdcp.tcp_reachable(host, sdcp.WS_PORT)
        camera_ok = sdcp.tcp_reachable(host, sdcp.CAMERA_PORT)
        print("  Порт состояния %-5d %s" % (
            sdcp.WS_PORT, "открыт" if status_ok else "закрыт"))
        print("  Порт камеры    %-5d %s" % (
            sdcp.CAMERA_PORT, "открыт" if camera_ok else "закрыт"))
    print("")
    print("  Лог: %s" % logging_setup.log_path())
    return 0 if status_ok else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    action = argv[0] if argv else "run"

    if action in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if action == "setup":
        from . import setup_wizard
        return setup_wizard.main(argv[1:])
    if action == "autostart":
        from . import autostart
        return autostart.main(argv[1:])
    if action == "check":
        return cmd_check()
    if action == "where":
        print(paths.data_dir())
        return 0
    if action == "run":
        return cmd_run()
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
