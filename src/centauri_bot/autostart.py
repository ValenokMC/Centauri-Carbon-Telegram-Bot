# -*- coding: utf-8 -*-
"""Optional autostart through the Windows Task Scheduler.

Three rules, because a background process the user did not understand and
cannot find is how well-meaning tools become malware in the user's mind:

  * never installed automatically - only by running Install-Autostart.cmd;
  * the exact command and trigger are printed and confirmed before anything is
    registered;
  * removal is one command and really removes it.

Current user only, ONLOGON trigger, no elevation. A task that needs
administrator rights to install is a task the user cannot remove without them
either.
"""
import os
import subprocess
import sys


TASK_NAME = "CentauriCarbonTelegramBot"


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _launch_command():
    """The command the task will run.

    pythonw.exe rather than python.exe: the bot has no console interface, and a
    console window reopening at every login is the fastest way to get an
    autostart uninstalled.
    """
    exe = sys.executable or "python.exe"
    windowless = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(windowless):
        exe = windowless
    return exe, os.path.join(_repo_root(), "src")


def describe():
    """Human-readable summary of exactly what will be created."""
    exe, src = _launch_command()
    return [
        "Задача Планировщика Windows:",
        "  имя      : %s" % TASK_NAME,
        "  запуск   : при входе текущего пользователя",
        "  права    : обычные, без администратора",
        "  программа: %s" % exe,
        "  аргументы: -m centauri_bot",
        "  рабочая  : %s" % src,
    ]


def _schtasks(args):
    try:
        done = subprocess.run(["schtasks"] + args, capture_output=True, timeout=30)
        return done.returncode, (done.stdout or b"").decode("cp866", "replace")
    except (OSError, subprocess.SubprocessError) as e:
        return 1, repr(e)


def installed():
    code, _ = _schtasks(["/Query", "/TN", TASK_NAME])
    return code == 0


def install():
    exe, src = _launch_command()
    # PYTHONPATH is set inside the command rather than relying on an installed
    # package: the release ZIP is meant to run where it was unpacked, with no
    # pip install step.
    command = '"%s" -m centauri_bot' % exe
    code, out = _schtasks([
        "/Create", "/TN", TASK_NAME, "/TR", command,
        "/SC", "ONLOGON", "/F", "/RL", "LIMITED",
    ])
    return code == 0, out


def remove():
    code, out = _schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
    return code == 0, out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    action = argv[0] if argv else "install"

    if action == "remove":
        if not installed():
            print("Автозапуск не был установлен — удалять нечего.")
            return 0
        done, out = remove()
        print("Автозапуск удалён." if done else "Не удалось удалить:\n%s" % out)
        return 0 if done else 1

    print("Будет создано:\n")
    for line in describe():
        print("  " + line)
    print("\nЭто НЕ включает бот прямо сейчас и не требует прав администратора.")
    print("Удалить в любой момент: Remove-Autostart.cmd\n")
    try:
        answer = input("  Создать задачу? [д/Н]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer not in ("д", "да", "y", "yes", "1", "+"):
        print("Ничего не создано.")
        return 1

    done, out = install()
    if not done:
        print("Не удалось создать задачу:\n%s" % out)
        return 1
    print("Готово. Бот будет запускаться при входе в Windows.")
    return 0
