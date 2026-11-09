# -*- coding: utf-8 -*-
"""The live print card: an HTML widget in the status message (Bot API 10.4).

A widget is fixed code plus changeable state. The code below is sent once with
sendWidget and never changes; the bot then rewrites the message's chat state
(``chat_data``, at most 1024 bytes) with editMessageWidgetData, and the open
widget redraws at once. Such an edit is silent: no "edited" mark, no
notification. So the card shows progress, temperatures and the time left
without anybody pressing "Refresh", and between the bot's edits it counts the
time left down by itself.

The card also carries the buttons that matter during a print: pause or
resume, stop, a camera frame. A tap sends a small request to the bot
(``widget_query``); a dangerous action first gets a one-use confirmation token
from the bot, and only a second tap with that token acts. The bot checks who
tapped and the token, exactly as for an inline button. Everything else lives
behind one "Menu" button under the card.

The widget runs in Telegram's sandbox: no network, no external scripts, the
chat's own colours as CSS variables. Everything it shows comes from the state
the bot writes, and it puts every value in with ``textContent``, never as
markup, so a file name cannot inject anything. The bot sends ready-made
strings ("слой 20 из 45") rather than numbers to format: the first live cards
showed "слой 20.45 из 45" for the data [20, 45], and a string is drawn as is.

Pure functions only, like ui.py: the tests call them and look at the result.

What this module does not decide: when a widget is sent, and what happens when
Telegram refuses it. That is app.py, which falls back to the ordinary message.
"""
import json
import zlib

from . import printer_state as ps


VERSION = 4

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
QUERY_LIMIT = 64             # bytes of a request from the widget
ANSWER_LIMIT = 1024          # bytes of the bot's answer to it

# Buttons on the card, one letter each in the state's "b".
PAUSE, RESUME, STOP, CAMERA = "P", "R", "S", "C"

# Short keys: 1024 bytes is 512 Cyrillic letters, and a long file name has to
# fit next to the rest.
#   v  format version          k  "p" printing, "w" paused, "x" anything else
#   s  state, in Russian       f  file name
#   p  progress, %             l  "слой 20 из 45"
#   m  "сопло 245° · стол 70°" (and the air inside, when the printer has it)
#   r  seconds left            e  finish, epoch seconds (printing only)
#   sp speed, % (only if not 100)                      o  1 online, 0 not
#   b  the buttons to draw, letters from above
#   mn the rest of the panel behind "⚙️ Ещё": [[label, callback_data], ...],
#      taken from the very keyboard the bot would put under a message, so
#      the owner's permissions decide here exactly as there
#   u  when the bot wrote this, epoch seconds
#
# The card's state classes are "p", "w" and "x": no other rule may use those
# names, or it restyles the whole card (the big percentage once did).

HTML = """<style>
body{margin:0;padding:10px 12px 8px}
.r{display:flex;justify-content:space-between;align-items:baseline;gap:10px;white-space:nowrap}
.s{font-size:1.15em;font-weight:600}
.pc{font-size:1.8em;font-weight:700;line-height:1.1}
.r,.pc{font-variant-numeric:tabular-nums}
.h,.f,.u,.n{color:var(--tg-theme-hint-color)}
.f{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
.h,.f{font-size:.92em}.u,.n{font-size:.82em}
.big{font-size:1.08em;margin-top:2px}
.b{height:7px;border-radius:4px;background:var(--tg-theme-secondary-bg-color);overflow:hidden;margin:8px 0 6px}
.b i{display:block;height:100%;width:0;background:var(--tg-theme-button-color);transition:width .6s}
.w .b i{background:#e5a50a}.x .b i{background:var(--tg-theme-destructive-text-color)}
.a{display:flex;gap:6px;margin-top:8px}.a button{flex:1;font-size:.95em;padding-left:2px;padding-right:2px}
.a .k{color:var(--tg-theme-destructive-text-color)}
.g{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:6px}.g button{font-size:.92em}
.z{color:var(--tg-theme-destructive-text-color)}
</style>
<div id=c><div class=r><span class=s id=s></span><span class=pc id=p></span></div>
<div class=f id=f></div><div class=b><i id=b></i></div>
<div class="r big"><span id=l></span><span id=t></span></div>
<div class="r h"><span id=m></span><span id=e></span></div>
<div class=a id=a></div><div class=g id=g hidden></div><div class=n id=n></div>
<div class=u id=u></div></div>
<p id=q hidden>⬇️ Статус — ниже</p>
<script>
var W=window.TelegramWidget,d=null,ask=null,busy=0,menu=0;
function $(i){return document.getElementById(i)}
function x(i,v){$(i).textContent=v}
function dur(s){var m=Math.max(1,Math.round(s/60)),h=Math.floor(m/60);m%=60;
return h?(m?h+' ч '+m+' мин':h+' ч'):m+' мин'}
function clock(e){return new Date(e*1e3).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}
function tick(){if(!d)return;var now=Date.now()/1e3,
left=d.e?Math.max(0,d.e-now):d.r;
x('t',left>0?'ещё '+dur(left):'');
x('e',d.e&&left>0?'готово ≈ '+clock(d.e):'');
var age=now-d.u;$('u').className=age>600?'u z':'u';
x('u',(d.sp?'скорость '+d.sp+'% · ':'')+(age>600?'нет обновлений '+dur(age):'обновлено '+clock(d.u)))}
function mk(t,c,f,p){var e=document.createElement('button');e.textContent=t;if(c)e.className=c;e.onclick=f;$(p||'a').appendChild(e)}
function clr(i){var a=$(i);while(a.firstChild)a.removeChild(a.firstChild)}
function go(q){if(busy)return;busy=1;menu=0;x('n','Отправляю…');
W.request(q).then(function(r){busy=0;var a=null;try{a=JSON.parse(r.result)}catch(e){}
ask=a&&a.t?a:null;x('n',ask?ask.q:(a&&a.m)||'');keys()},
function(e){busy=0;ask=null;x('n',e&&e.code=='RATE_LIMITED'?'Слишком часто, подожди немного':'Не дошло до бота, попробуй ещё раз');keys()})}
function keys(){clr('a');clr('g');$('g').hidden=!menu;
if(!d||!W.request||W.isForward)return;
if(ask){mk(ask.y,'k',function(){go('do:'+ask.a+':'+ask.t)});
mk('Отмена','',function(){ask=null;x('n','');keys()});return}
var b=d.b||'',mn=d.mn||[];
if(b.indexOf('P')>=0)mk('⏸ Пауза','',function(){go('ask:pause')});
if(b.indexOf('R')>=0)mk('▶️ Дальше','',function(){go('ask:resume')});
if(b.indexOf('S')>=0)mk('⏹ Стоп','k',function(){go('ask:stop')});
if(b.indexOf('C')>=0)mk('📷 Кадр','',function(){go('snap')});
if(mn.length)mk(menu?'✕ Скрыть':'⚙️ Ещё','',function(){menu=!menu;x('n','');keys()});
if(menu)mn.forEach(function(i){mk(i[0],'',function(){go('open:'+i[1])},'g')})}
function draw(){d=null;try{d=JSON.parse(W.chatData)}catch(e){}
var ok=!!(d&&d.v);$('c').hidden=!ok;$('q').hidden=ok;if(!ok)return;
$('c').className=d.k;x('s',d.s);x('f',d.f||'');
var p=Math.max(0,Math.min(100,+d.p||0));x('p',Math.floor(p)+'%');
$('b').style.width=p+'%';x('l',d.l||'');x('m',d.m||'');
if(!busy)keys();tick()}
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


def _whole(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _temp(cur, target):
    """"245°", or "180→245°" while it is still heating."""
    cur, target = round(float(cur or 0)), round(float(target or 0))
    if target and abs(cur - target) > 2:
        return "%d→%d°" % (cur, target)
    return "%d°" % cur


def buttons(status, online, allowed, camera):
    """The card's buttons for this moment, as the letters the HTML reads."""
    if not status or not online:
        return CAMERA if camera else ""
    info = status.get("PrintInfo") or {}
    code = info.get("Status")
    out = ""
    if code == ps.STATUS_PRINTING and "pause" in allowed:
        out += PAUSE
    if code in ps.STATUS_PAUSED and "resume" in allowed:
        out += RESUME
    if is_live(status, online) and "cancel" in allowed:
        out += STOP
    if camera:
        out += CAMERA
    return out


# Buttons of the usual panel the card already has in its own row, or that
# would only redraw what the card shows anyway.
ON_THE_CARD = {"refresh", "brief", "ask:pause", "ask:resume", "ask:stop", "snap"}


def menu(keyboard):
    """The "⚙️ Ещё" items: the usual panel's buttons, minus those on the card.

    The panel is built by ui.kb_main from the owner's settings, so a button
    the owner has not allowed is not in it, and so not here either.
    """
    items = []
    for row in keyboard or []:
        for button in row:
            data = button.get("callback_data")
            if data and data not in ON_THE_CARD:
                items.append([str(button.get("text") or ""), data])
    return items


def menu_allows(keyboard, data):
    """A request "open:<data>" names a button the panel really has."""
    return any(item[1] == data for item in menu(keyboard))


def card(status, online, printer_name, now, keys="", items=None):
    """The widget's state as a dict, or None when there is no status yet.

    ``printer_name`` stays in the caption under the card, not on it.
    ``keys`` is what buttons() returned, ``items`` what menu() did."""
    if not status:
        return None
    info = status.get("PrintInfo") or {}
    code = info.get("Status")
    if code == ps.STATUS_PRINTING:
        kind = "p"
    elif code in ps.STATUS_PAUSED:
        kind = "w"
    else:
        kind = "x"
    left = max(0, int((info.get("TotalTicks") or 0) - (info.get("CurrentTicks") or 0)))
    total = _whole(info.get("TotalLayer"))
    temps = "сопло %s · стол %s" % (
        _temp(status.get("TempOfNozzle"), status.get("TempTargetNozzle")),
        _temp(status.get("TempOfHotbed"), status.get("TempTargetHotbed")))
    # Stock SDCP reports the air inside the printer; COSMOS does not, and an
    # empty cell for it only puzzled.
    if status.get("TempOfBox") is not None:
        temps += " · внутри %d°" % round(float(status.get("TempOfBox") or 0))
    data = {
        "v": VERSION, "k": kind, "s": state_ru(code, online),
        "f": "" if code in ps.STATUS_DONE else str(info.get("Filename") or ""),
        "p": round(float(info.get("Progress") or 0), 1),
        "l": "слой %d из %d" % (_whole(info.get("CurrentLayer")), total) if total else "",
        "m": temps,
        "r": left, "o": 1 if online else 0, "b": keys, "u": int(now),
    }
    if items:
        data["mn"] = items
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
    return text   # without the file name the rest is short: well under the limit


def answer(**fields):
    """The bot's answer to a request from the card: JSON the card reads.

    m  a line to show under the buttons
    a, t, q, y  a confirmation: the action, its one-use token, the question
                and the label of the button that confirms
    """
    text = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > ANSWER_LIMIT:
        text = json.dumps({"m": str(fields.get("m") or "")[:200]},
                          ensure_ascii=False, separators=(",", ":"))
    return text


def same_content(a, b):
    """Equal apart from the "written at" stamp: no reason to edit."""
    if a is None or b is None:
        return a is b
    return {k: v for k, v in a.items() if k != "u"} == \
        {k: v for k, v in b.items() if k != "u"}
