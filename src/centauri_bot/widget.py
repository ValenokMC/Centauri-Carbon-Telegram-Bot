# -*- coding: utf-8 -*-
"""The live print card: an HTML widget in the status message (Bot API 10.4).

A widget is fixed code plus changeable state. The code below is sent once with
sendWidget and never changes; the bot then rewrites the message's chat state
(``chat_data``, at most 1024 bytes) with editMessageWidgetData, and the open
widget redraws at once. Such an edit is silent: no "edited" mark, no
notification. So the card shows progress, temperatures and the time left
without anybody pressing "Refresh", and between the bot's edits it counts the
time left down by itself.

The widget runs in Telegram's sandbox: no network, no external scripts, the
chat's own colours as CSS variables. Everything it shows comes from the state
the bot writes, and it puts every value in with ``textContent``, never as
markup, so a file name cannot inject anything.

Pure functions only, like ui.py: the tests call them and look at the result.

What this module does not decide: when a widget is sent, and what happens when
Telegram refuses it. That is app.py, which falls back to the ordinary message.
"""
import json
import zlib

from . import printer_state as ps


VERSION = 2

# storage.message_kind() of a card sent with this very HTML. A sent widget's
# HTML can never be replaced, so a card from an older version is not ours to
# feed new data: its kind no longer matches, and the next refresh sends a new
# card in its place.
KIND = "widget-%d" % VERSION


def is_card(kind):
    """A card of any version: its text is a caption and its state can be cleared."""
    return str(kind or "").startswith("widget")

# sendWidget limits, from the Bot API reference.
HTML_LIMIT = 32768           # bytes of UTF-8
HTML_COMPRESSED_LIMIT = 4096  # after zlib at level 9
DATA_LIMIT = 1024            # bytes of UTF-8 in chat_data
CAPTION_LIMIT = 1024         # characters after entity parsing

# Short keys: 1024 bytes is 512 Cyrillic letters, and a long file name has to
# fit next to the numbers.
#   v  format version          k  "p" printing, "w" paused, "x" anything else
#   s  state, in Russian       f  file name
#   p  progress, %             l  [layer, layers], whole numbers
#   tn [nozzle, target]        tb [bed, target]
#   tc air inside the printer, only if the printer reports it (stock SDCP
#      does, COSMOS does not)
#   r  seconds left            e  finish, epoch seconds (printing only)
#   sp speed, % (only if not 100)                      o  1 online, 0 not
#   u  when the bot wrote this, epoch seconds

# Layout, top to bottom: state and percent; the file; the bar; layer and time
# left; temperatures and the finish time in grey; when it was updated. Small
# and dense on purpose: the buttons under the card already take half a phone
# screen. Sizes are in em, so the chat's own text size sets the scale.
HTML = """<style>
body{margin:0;padding:8px 12px 6px}
.r{display:flex;justify-content:space-between;align-items:baseline;gap:10px;white-space:nowrap}
.s{font-weight:600}
.pc{font-size:1.5em;font-weight:700}
.r,.pc{font-variant-numeric:tabular-nums}
.h,.f,.u{color:var(--tg-theme-hint-color)}
.h,.u{font-size:.85em}.u{font-size:.75em;margin-top:4px}
.f{font-size:.85em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.b{height:6px;border-radius:3px;background:var(--tg-theme-secondary-bg-color);overflow:hidden;margin:6px 0}
.b i{display:block;height:100%;width:0;background:var(--tg-theme-button-color);transition:width .6s}
.w .b i{background:#e5a50a}.x .b i{background:var(--tg-theme-destructive-text-color)}
.z{color:var(--tg-theme-destructive-text-color)}
</style>
<div id=c><div class=r><span class=s id=s></span><span class=pc id=p></span></div>
<div class=f id=f></div><div class=b><i id=b></i></div>
<div class=r><span id=l></span><span id=t></span></div>
<div class="r h"><span id=m></span><span id=e></span></div>
<div class=u id=u></div></div>
<p id=q hidden>⬇️ Статус — ниже</p>
<script>
var W=window.TelegramWidget,d=null;
function $(i){return document.getElementById(i)}
function x(i,v){$(i).textContent=v}
function dur(s){var m=Math.max(1,Math.round(s/60)),h=Math.floor(m/60);m%=60;
return h?(m?h+' ч '+m+' мин':h+' ч'):m+' мин'}
function deg(a){var c=Math.round(a[0]),t=Math.round(a[1]||0);
return t&&Math.abs(c-t)>2?c+'→'+t+'°':c+'°'}
function clock(e){return new Date(e*1e3).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}
function tick(){if(!d)return;var now=Date.now()/1e3,
left=d.e?Math.max(0,d.e-now):d.r;
x('t',left>0?'ещё '+dur(left):'');
x('e',d.e&&left>0?'готово ≈ '+clock(d.e):'');
var age=now-d.u;$('u').className=age>600?'u z':'u';
x('u',(d.sp?'скорость '+d.sp+'% · ':'')+(age>600?'нет обновлений '+dur(age):'обновлено '+clock(d.u)))}
function draw(){d=null;try{d=JSON.parse(W.chatData)}catch(e){}
var ok=!!(d&&d.v);$('c').hidden=!ok;$('q').hidden=ok;if(!ok)return;
$('c').className=d.k;x('s',d.s);x('f',d.f||'');
var p=Math.max(0,Math.min(100,+d.p||0));x('p',Math.floor(p)+'%');
$('b').style.width=p+'%';var l=d.l||[];
x('l',l[1]?'слой '+l[0]+' из '+l[1]:'');
x('m','сопло '+deg(d.tn)+' · стол '+deg(d.tb)+(d.tc==null?'':' · внутри '+Math.round(d.tc)+'°'));
tick()}
if(W){W.onEvent('chatDataChanged',draw);draw();setInterval(tick,15e3);if(W.ready)W.ready()}
</script>"""


def html_fits(html=HTML):
    """True when the code is within both sendWidget limits."""
    raw = html.encode("utf-8")
    return (len(raw) <= HTML_LIMIT
            and len(zlib.compress(raw, 9)) <= HTML_COMPRESSED_LIMIT)


def is_live(status, online):
    """A print is on, so the card has something worth keeping live.

    Paused and stalled jobs count: the card is the thing to look at then.
    A finished or cancelled job does not, even though Moonraker keeps its file
    name around afterwards.
    """
    if not status or not online:
        return False
    info = status.get("PrintInfo") or {}
    code = info.get("Status")
    return bool(info.get("Filename")) and code is not None and code != 0 \
        and code not in ps.STATUS_DONE


def _temps(status, cur, target):
    return [round(float(status.get(cur) or 0), 1),
            round(float(status.get(target) or 0), 1)]


# The card speaks Russian like the rest of the interface. printer_state keeps
# its English code names: they are what status-codes.txt and the logs use.
STATE_RU = {
    1: "Парковка", 5: "Ставлю на паузу", 6: "Пауза", 7: "Останавливается",
    8: "Остановлена", 9: "Готово", 12: "Продолжается", 13: "Печатает",
    16: "Нагрев", 18: "Подготовка", 21: "Калибровка",
    ps.STATUS_KLIPPY_ERROR: "Аварийная остановка",
}


def state_ru(code, online):
    if not online:
        return "Нет связи"
    return STATE_RU.get(code) or "Остановилась, код %s" % code


def _layer(value):
    # Moonraker estimates the layer from the height: 19.45 is layer 19.
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def card(status, online, printer_name, now):
    """The widget's state as a dict, or None when there is no status yet.

    ``printer_name`` stays in the caption under the card, not on it."""
    if not status:
        return None
    info = status.get("PrintInfo") or {}
    code = info.get("Status")
    name = state_ru(code, online)
    if code == ps.STATUS_PRINTING:
        kind = "p"
    elif code in ps.STATUS_PAUSED:
        kind = "w"
    else:
        kind = "x"
    left = max(0, int((info.get("TotalTicks") or 0) - (info.get("CurrentTicks") or 0)))
    data = {
        "v": VERSION, "k": kind, "s": name,
        "f": "" if code in ps.STATUS_DONE else str(info.get("Filename") or ""),
        "p": round(float(info.get("Progress") or 0), 1),
        "l": [_layer(info.get("CurrentLayer")), _layer(info.get("TotalLayer"))],
        "tn": _temps(status, "TempOfNozzle", "TempTargetNozzle"),
        "tb": _temps(status, "TempOfHotbed", "TempTargetHotbed"),
        "r": left, "o": 1 if online else 0, "u": int(now),
    }
    if status.get("TempOfBox") is not None:
        data["tc"] = round(float(status.get("TempOfBox") or 0), 1)
    # Only while it prints: on a pause the finish moves with every minute.
    if kind == "p" and online and left > 0:
        data["e"] = int(now + left)
    speed = info.get("PrintSpeedPct")
    if speed not in (None, 100):
        data["sp"] = speed
    return data


def encode(data):
    """chat_data text within 1024 bytes. A long file name is cut, then dropped."""
    if data is None:
        return ""
    data = dict(data)
    for cut in (None, 60, 24, 0):
        if cut is not None:
            data["f"] = data["f"][:cut] + ("…" if cut and len(data["f"]) > cut else "")
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(text.encode("utf-8")) <= DATA_LIMIT:
            return text
    return text   # without the file name the rest is numbers: well under the limit


def same_content(a, b):
    """Equal apart from the "written at" stamp: no reason to edit."""
    if a is None or b is None:
        return a is b
    return {k: v for k, v in a.items() if k != "u"} == \
        {k: v for k, v in b.items() if k != "u"}
