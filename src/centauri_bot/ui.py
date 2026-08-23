# -*- coding: utf-8 -*-
"""Everything the owner actually sees: the status text and the keyboards.

The interface language is Russian. That is not an oversight - see the
compatibility note in the README. A full English localisation is not written
and not tested, and shipping a half-translated interface would be worse than
shipping an honest single-language one.

Pure functions only. Everything is passed in, nothing is read from a socket or
a global, which is what lets tests assert on exact button layouts.
"""
from . import printer_state as ps
from . import support


HEAT_PRESETS = {
    "petg": ("🔥 PETG 245/80", 245, 80),
    "pla": ("🔥 PLA 220/60", 220, 60),
    "off": ("❄️ остыть", 0, 0),
}
SPEEDS = [50, 75, 100, 125, 150]

FAN_KEYS = ("ModelFan", "BoxFan", "AuxiliaryFan")
FAN_LEVELS = (0, 25, 50, 75, 100)
FAN_HUMAN = {"ModelFan": "обдув", "BoxFan": "корпус", "AuxiliaryFan": "доп"}


# ------------------------------------------------------------------ helpers

def hhmm(sec):
    """Human duration: "44 мин", "1 ч 20 мин"."""
    sec = int(max(0, sec))
    h, m = sec // 3600, (sec % 3600) // 60
    if h and m:
        return "%d ч %d мин" % (h, m)
    if h:
        return "%d ч" % h
    return "%d мин" % max(1, m)


def plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def bar(pct, code=None, width=10):
    """Progress bar. The fill colour tracks the state: green while printing,
    yellow if the print has stalled halfway. Readable at a glance, unread."""
    pct = max(0, min(100, int(pct or 0)))
    full = round(width * pct / 100)
    fill = "🟩" if code in (None, ps.STATUS_PRINTING) else "🟨"
    return fill * full + "⬜" * (width - full)


def heat_icon(cur, target):
    """Hot or heating, versus cold. Also says whether it is safe to touch."""
    cur = cur or 0
    if (target or 0) > 0 or cur >= 40:
        return "🔥"
    return "❄️"


def temp(cur, target):
    """Show the target only when it differs - otherwise it is noise."""
    cur = cur or 0
    if target and abs(cur - target) > 2:
        return "%.0f→%.0f°" % (cur, target)
    return "%.0f°" % cur


def done_text(snapshot):
    """Finish message, built from the snapshot: the printer has already
    cleared the live PrintInfo by the time this is needed."""
    filename = snapshot.get("Filename") or ""
    if not filename:
        return "✅ <b>Печать закончена</b>"
    took = hhmm(snapshot.get("CurrentTicks") or snapshot.get("TotalTicks") or 0)
    layers = snapshot.get("TotalLayer")
    tail = (" · %d %s" % (layers, plural(layers, "слой", "слоя", "слоёв"))) if layers else ""
    return "✅ <b>Печать закончена</b>\n<i>%s</i>\nзаняло %s%s" % (filename, took, tail)


def cancelled_text(snapshot, reached):
    return ("⏹ <b>Печать остановлена</b>\n📄 <i>%s</i>\nна %s%%, слой %s из %s"
            % (snapshot.get("Filename", "—"), reached,
               snapshot.get("CurrentLayer", "?"), snapshot.get("TotalLayer", "?")))


# ------------------------------------------------------------------ status text

def render(status, online, printer_name, header="", detailed=False,
           maintenance_line=""):
    """The single status message.

    The block order is deliberately inverted. On a phone the keyboard takes up
    half the screen and pushes the text upward, so the things that matter most
    - state, progress, time left - sit last, right against the buttons. The
    rest drifts up out of the way. Blocks are separated by a blank line:
    in detailed mode seven solid lines are unreadable.
    """
    if not status:
        lines = []
        if header:
            lines.append(header)
        lines.append("<b>%s</b>\nНет данных от принтера." % printer_name)
        return "\n".join(lines)

    print_info = status.get("PrintInfo") or {}
    code = print_info.get("Status")
    name, icon = ps.state_meta(code)

    # An unknown code on an unfinished job is almost certainly a pause or a
    # stop. Saying so is more honest than frightening the user with
    # "state 5", but the number stays: it is how STATUS_META grows.
    if code not in ps.STATUS_META and (print_info.get("Filename") or ""):
        if (print_info.get("CurrentLayer") or 0) <= 1 and (print_info.get("Progress") or 0) == 0:
            name, icon = "готовится, код %s" % code, "🔧"
        elif (print_info.get("Progress") or 0) < 100:
            name, icon = "встал, код %s" % code, "⏸"
    if not online:
        name, icon = "связь потеряна", "🔌"

    filename = print_info.get("Filename") or ""
    blocks = []

    # -- header: what is happening, and to what --------------------------
    top = []
    if header:
        top.append(header)
    # The summary goes first. Telegram shows the start of a caption in the
    # pinned-message strip and in the chat list, so the most informative thing
    # belongs there.
    summary = "%s <b>%s</b> · %s" % (icon, printer_name, name)
    if filename and online:
        summary += " · %s%%" % print_info.get("Progress", 0)
    top.append(summary)
    if filename:
        top.append("📄 <i>%s</i>" % filename)
    # The camera is deliberately not shown here: a third emoji pushed the line
    # onto a second row. It is reference information and lives in details.
    top.append("%s сопло %s · %s стол %s" % (
        heat_icon(status.get("TempOfNozzle"), status.get("TempTargetNozzle")),
        temp(status.get("TempOfNozzle"), status.get("TempTargetNozzle")),
        heat_icon(status.get("TempOfHotbed"), status.get("TempTargetHotbed")),
        temp(status.get("TempOfHotbed"), status.get("TempTargetHotbed"))))
    if print_info.get("PrintSpeedPct") not in (None, 100):
        top.append("⚡ скорость %s%%" % print_info["PrintSpeedPct"])
    blocks.append(top)

    if detailed:
        # -- around the part: air, fans, light ---------------------------
        env = ["🏠 камера %.0f°" % (status.get("TempOfBox") or 0)]
        fans = status.get("CurrentFanSpeed") or {}
        if fans:
            env.append("🌀 обдув %s%% · корпус %s%% · доп %s%%" % (
                fans.get("ModelFan", 0), fans.get("BoxFan", 0),
                fans.get("AuxiliaryFan", 0)))
        light = (status.get("LightStatus") or {}).get("SecondLight")
        if light is not None:
            env.append("💡 свет горит" if light == 1 else "🌙 свет выключен")
        blocks.append(env)

        # -- time and position -------------------------------------------
        pos = []
        if filename:
            pos.append("⏱ прошло %s из ≈%s"
                       % (hhmm(print_info.get("CurrentTicks", 0)),
                          hhmm(print_info.get("TotalTicks", 0))))
        if status.get("CurrenCoord"):
            pos.append("📍 <code>%s</code>" % status["CurrenCoord"])
        blocks.append(pos)

    # -- maintenance: its own line, only when it is nearly due ------------
    if maintenance_line:
        blocks.append([maintenance_line])

    # -- the important part: right against the buttons --------------------
    if filename:
        left = (print_info.get("TotalTicks") or 0) - (print_info.get("CurrentTicks") or 0)
        blocks.append([
            "%s <b>%s%%</b>" % (bar(print_info.get("Progress"), code),
                                print_info.get("Progress", 0)),
            "🧱 слой %s из %s · ⏳ ещё %s"
            % (print_info.get("CurrentLayer", "?"),
               print_info.get("TotalLayer", "?"), hhmm(left)),
        ])
    elif online:
        blocks.append(["🟢 свободен — можно ставить печать"])
    else:
        blocks.append(["🔌 нет связи с принтером"])

    return "\n\n".join("\n".join(b) for b in blocks if b)


# ------------------------------------------------------------------ keyboards

def kb_main(status, allow_control=True, detailed=False, maintenance=(False, False)):
    """The keyboard under the status message.

    Every button edits this same message and sends nothing new: otherwise new
    messages pile up below and the status drifts out of reach.

    ``maintenance`` is (show, due) from printer_state.maintenance_status.

    Note what is NOT here: the support button. The status message is the one
    the user looks at twenty times a print, and a donation ask on it would be
    exactly the nagging this project refuses to do. It lives on /help.
    """
    status = status or {}
    print_info = status.get("PrintInfo") or {}
    printing = print_info.get("Status") == ps.STATUS_PRINTING
    busy = bool(print_info.get("Filename")) and (print_info.get("Progress", 0) or 0) < 100

    rows = [[{"text": "🔄 Обновить", "callback_data": "refresh"},
             {"text": "🔼 Кратко" if detailed else "ℹ️ Подробнее",
              "callback_data": "brief" if detailed else "details"}]]
    if allow_control:
        ctl = []
        if printing:
            ctl.append({"text": "⏸ Пауза", "callback_data": "ask:pause"})
        elif busy:
            ctl.append({"text": "▶️ Продолжить", "callback_data": "do:resume"})
        if busy:
            ctl.append({"text": "⏹ Стоп", "callback_data": "ask:stop"})
        if ctl:
            rows.append(ctl)
        lit = ((status.get("LightStatus") or {}).get("SecondLight") == 1)
        rows.append([
            {"text": "💡 Свет выкл" if lit else "💡 Свет вкл", "callback_data": "light"},
            {"text": "⚡ Скорость", "callback_data": "menu:speed"},
            {"text": "🌡 Нагрев", "callback_data": "menu:temp"},
        ])
    rows.append([{"text": "📂 Файлы", "callback_data": "files"},
                 {"text": "🌀 Вентиляторы", "callback_data": "menu:fans"}])

    show_maint, due = maintenance
    if show_maint:
        rows.append([{"text": "🧰 Смазал, сбросить счётчик" if due else "🧰 Уже смазал",
                      "callback_data": "maint:done"}])
    rows.append([{"text": "❔ О проекте", "callback_data": "help"}])
    return rows


def kb_speed(current=None):
    row = [{"text": ("• %d%%" % v) if v == current else "%d%%" % v,
            "callback_data": "set:speed:%d" % v} for v in SPEEDS]
    return [row, [{"text": "↩️ Назад", "callback_data": "refresh"}]]


def kb_temp():
    row = [{"text": HEAT_PRESETS[k][0], "callback_data": "set:temp:" + k}
           for k in ("petg", "pla", "off")]
    return [row, [{"text": "↩️ Назад", "callback_data": "refresh"}]]


def kb_confirm(action, label):
    """Dangerous actions get a second look. No support button here either -
    a donation ask next to "stop the print, this cannot be undone" would be
    grotesque."""
    return [[{"text": "✅ Да, %s" % label, "callback_data": "do:" + action},
             {"text": "↩️ Отмена", "callback_data": "refresh"}]]


def kb_fans(current, draft=None):
    """Fan speeds are collected as a draft and sent in one command.

    Otherwise the menu closed after each button, and setting three fans meant
    entering it three times.
    """
    draft = dict(draft or {})
    rows = []
    # Five steps plus a label do not fit on one row - the buttons get clipped.
    # So the label with the chosen value gets its own line, with the steps under it.
    for key, label in (("ModelFan", "🌀 обдув"), ("BoxFan", "🏠 корпус"),
                       ("AuxiliaryFan", "💨 доп")):
        val = draft.get(key, current.get(key, 0))
        shown = "выкл" if val == 0 else "%d%%" % val
        changed_mark = " ✎" if key in draft and draft[key] != current.get(key, 0) else ""
        rows.append([{"text": "%s · %s%s" % (label, shown, changed_mark),
                      "callback_data": "noop"}])
        rows.append([{"text": ("• " if val == v else "") + ("выкл" if v == 0 else "%d%%" % v),
                      "callback_data": "set:fan:%s:%d" % (key, v)} for v in FAN_LEVELS])
    changed = sum(1 for k in FAN_KEYS if draft.get(k, current.get(k, 0)) != current.get(k, 0))
    rows.append([
        {"text": "✅ Отправить (%d)" % changed if changed else "✅ Отправить",
         "callback_data": "fans:apply"},
        {"text": "↩️ Назад", "callback_data": "fans:cancel"},
    ])
    return rows


def kb_files(files, allow_control=True, limit=8):
    rows = []
    if allow_control:
        for i, path in enumerate(files[:limit]):
            base = path.rsplit("/", 1)[-1]
            rows.append([{"text": "🖨 %s" % base[:38], "callback_data": "ask:print:%d" % i}])
    rows.append([{"text": "↩️ Назад к статусу", "callback_data": "refresh"}])
    return rows


def files_text(files, limit=8):
    lines = ["<b>Файлы на принтере</b> — последние %d из %d"
             % (min(limit, len(files)), len(files))]
    for path in files[:limit]:
        lines.append("• %s" % path.rsplit("/", 1)[-1])
    return "\n".join(lines)


HELP_TEXT_HEADER = (
    "<b>Что умею</b>\n\n"
    "/status — состояние со снимком и кнопками\n"
    "/snap — только кадр с камеры\n"
    "/files — файлы на принтере\n"
    "/help — эта справка\n\n"
    "<b>Кнопки под статусом</b>\n"
)

HELP_TEXT_FOOTER = (
    "Все правят одно и то же сообщение, новых не досылают — статус остаётся на месте.\n\n"
    "В чате всегда ровно одно сообщение со статусом: оно правится на месте и "
    "переезжает вниз, когда я пишу что-то ещё.\n\n"
    "Сам напишу, когда печать начнётся, встанет (например, на смену прутка), "
    "продолжится или закончится."
)


def help_screen(allow_control=True):
    """Text and keyboard for /help — the permanent home of the support button."""
    buttons = ("обновить · подробнее · пауза/продолжить · стоп · свет · "
               "скорость · нагрев · файлы\n" if allow_control else
               "обновить · подробнее · файлы\n")
    return HELP_TEXT_HEADER + buttons + HELP_TEXT_FOOTER, support.help_keyboard()
