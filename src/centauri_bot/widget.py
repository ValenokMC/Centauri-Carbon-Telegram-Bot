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
import html as _html
import json
import re
import zlib

from . import printer_state as ps


VERSION = 5

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

HTML = r"""<style>
:root{--g:light-dark(rgba(0,0,0,.05),rgba(255,255,255,.07));--l:light-dark(rgba(0,0,0,.09),rgba(255,255,255,.13));--ac:var(--tg-theme-button-color)}
body{margin:0;padding:8px}
.cd{border-radius:18px;padding:12px;border:1px solid var(--l);background:linear-gradient(140deg,light-dark(rgba(60,120,255,.12),rgba(90,150,255,.2)),light-dark(rgba(255,255,255,.45),rgba(255,255,255,.03)) 65%)}
.k-w{--ac:#e5a50a}.k-x{--ac:var(--tg-theme-destructive-text-color)}.k-i{--ac:var(--tg-theme-hint-color)}
.top{display:flex;gap:12px;align-items:center}
.rg{width:70px;height:70px;border-radius:50%;flex:none;display:flex;align-items:center;justify-content:center}
.rg b{width:56px;height:56px;border-radius:50%;background:var(--tg-theme-bg-color);display:flex;align-items:center;justify-content:center;font-size:1.2em;font-variant-numeric:tabular-nums}
.k-i .rg{display:none}
.mn{flex:1;min-width:0}
.s{font-size:1.15em;font-weight:700}
.dt{width:8px;height:8px;border-radius:50%;background:var(--ac);display:inline-block;margin-right:7px;vertical-align:middle}
.f,.h,.u,.n{color:var(--tg-theme-hint-color)}
.f{font-size:.88em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.l{margin-top:4px;font-variant-numeric:tabular-nums}.h{font-size:.85em;margin-top:1px}
.cp{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.cp span{padding:4px 10px;border-radius:99px;background:var(--g);border:1px solid var(--l);font-size:.85em;font-variant-numeric:tabular-nums}
.a,.rw{display:flex;gap:6px;margin-top:8px}.a button,.rw button{flex:1}
.g{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:6px}
button{background:var(--g);border:1px solid var(--l);color:inherit;border-radius:12px;padding:9px 6px;font:inherit;font-size:.9em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
button:active{background:var(--l)}
button.k{color:var(--tg-theme-destructive-text-color)}
button.y{background:var(--tg-theme-button-color);color:var(--tg-theme-button-text-color);border-color:transparent}
button:disabled{color:var(--tg-theme-hint-color);background:none;border-color:transparent;padding:4px 6px 0}
.hd{display:flex;align-items:center;gap:8px}.hd button{flex:none;padding:6px 12px}
.hd b{font-size:1.08em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tx{margin-top:8px;font-size:.92em;line-height:1.4}.tx div{min-height:.6em}
.n{font-size:.84em;margin-top:6px}.u{font-size:.75em;margin-top:6px;text-align:right}
.z{color:var(--tg-theme-destructive-text-color)}
</style>
<div id=c class=cd><div id=cv><div class=top><div class=rg id=rg><b id=p></b></div>
<div class=mn><div class=s><i class=dt></i><span id=s></span></div><div class=f id=f></div>
<div class=l><span id=l></span> <span id=t></span></div><div class=h id=e></div></div></div>
<div class=cp id=m></div><div class=a id=a></div><div class=g id=g hidden></div></div>
<div id=v hidden><div class=hd><button id=bk>‹ Назад</button><b id=vt></b></div><div class=tx id=vx></div><div id=vk></div></div>
<div class=n id=n></div><div class=u id=u></div></div>
<p id=q hidden>⬇️ Статус — ниже</p>
<script>
var W=window.TelegramWidget,d=null,ask=null,busy=0,menu=0,sc=null;
function $(i){return document.getElementById(i)}
function x(i,v){$(i).textContent=v}
function clr(i){var a=$(i);while(a.firstChild)a.removeChild(a.firstChild);return a}
function el(p,t,v){var e=document.createElement(t);e.textContent=v;p.appendChild(e);return e}
function dur(s){var m=Math.max(1,Math.round(s/60)),h=Math.floor(m/60);m%=60;
return h?(m?h+' ч '+m+' мин':h+' ч'):m+' мин'}
function clock(e){return new Date(e*1e3).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}
function tick(){if(!d)return;var now=Date.now()/1e3,left=d.e?Math.max(0,d.e-now):d.r;
x('t',left>0?(d.l?'· ':'')+'ещё '+dur(left):'');
x('e',d.e&&left>0?'готово ≈ '+clock(d.e):'');
var age=now-d.u;$('u').className=age>600?'u z':'u';
x('u',(d.sp?'скорость '+d.sp+'% · ':'')+(age>600?'нет обновлений '+dur(age):'обновлено '+clock(d.u)))}
function mk(p,t,c,f){var e=el(p,'button',t);if(c)e.className=c;if(f)e.onclick=f;else e.disabled=true;return e}
function go(q){if(busy)return;busy=1;menu=0;x('n','Отправляю…');
W.request(q).then(function(r){busy=0;var a=null;try{a=JSON.parse(r.result)}catch(e){}a=a||{};
ask=a.t?a:null;if(a.sc)sc=a.sc;if(a.close||ask)sc=null;
x('n',ask?ask.q:a.m||'');show()},
function(e){busy=0;ask=null;x('n',e&&e.code=='RATE_LIMITED'?'Слишком часто, подожди немного':'Не дошло до бота, попробуй ещё раз');show()})}
function tap(c){if(c=='b'){sc=null;x('n','');show()}else if(c.slice(0,2)=='u:'){var u=c.slice(2);
try{if(/^https:\/\/t\.me\//.test(u))W.openTelegramLink(u);else W.openLink(u)}catch(e){}}else go(c)}
function keys(){var a=clr('a'),g=clr('g');g.hidden=!menu;
if(!d||!W.request||W.isForward)return;
if(ask){mk(a,ask.y,'y',function(){go('do:'+ask.a+':'+ask.t)});mk(a,'Отмена','',function(){ask=null;x('n','');show()});return}
var b=d.b||'',mn=d.mn||[];
if(b.indexOf('P')>=0)mk(a,'⏸ Пауза','',function(){go('ask:pause')});
if(b.indexOf('R')>=0)mk(a,'▶️ Дальше','',function(){go('ask:resume')});
if(b.indexOf('S')>=0)mk(a,'⏹ Стоп','k',function(){go('ask:stop')});
if(b.indexOf('C')>=0)mk(a,'📷 Кадр','',function(){go('snap')});
if(mn.length)mk(a,menu?'✕ Скрыть':'⚙️ Ещё','',function(){menu=!menu;x('n','');show()});
if(menu)mn.forEach(function(i){mk(g,i[0],'',function(){go('c:'+i[1])})})}
function screen(){x('vt',sc.t||'');var t=clr('vx');(sc.x||[]).forEach(function(s){el(t,'div',s)});
var k=clr('vk');(sc.k||[]).forEach(function(r){var w=el(k,'div','');w.className='rw';
r.forEach(function(b){mk(w,b[0],b[2],b[1]?function(){tap(b[1])}:null)})})}
function show(){$('cv').hidden=!!sc;$('v').hidden=!sc;if(sc)screen();else keys()}
function draw(){d=null;try{d=JSON.parse(W.chatData)}catch(e){}
var ok=!!(d&&d.v);$('c').hidden=!ok;$('q').hidden=ok;if(!ok)return;
$('c').className='cd k-'+d.k;x('s',d.s);x('f',d.f||'');
var p=Math.max(0,Math.min(100,+d.p||0));x('p',Math.floor(p)+'%');
$('rg').style.background='conic-gradient(var(--ac) '+p+'%,var(--l) 0)';
x('l',d.l||'');var m=clr('m');(d.m||'').split(' · ').forEach(function(s){if(s)el(m,'span',s)});
if(!busy)show();tick()}
$('bk').onclick=function(){sc=null;x('n','');show()};
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
    0: "Свободен", 1: "Парковка", 5: "Ставлю на паузу", 6: "Пауза", 7: "Останавливается",
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
    live = is_live(status, online)
    if not live:
        kind = "i"          # no job: idle, finished, or no connection
    elif code == ps.STATUS_PRINTING:
        kind = "p"
    elif code in ps.STATUS_PAUSED:
        kind = "w"
    else:
        kind = "x"          # a job that stopped unexpectedly
    left = max(0, int((info.get("TotalTicks") or 0) - (info.get("CurrentTicks") or 0))) \
        if live else 0
    total = _whole(info.get("TotalLayer")) if live else 0
    temps = "сопло %s · стол %s" % (
        _temp(status.get("TempOfNozzle"), status.get("TempTargetNozzle")),
        _temp(status.get("TempOfHotbed"), status.get("TempTargetHotbed")))
    # Stock SDCP reports the air inside the printer; COSMOS does not, and an
    # empty cell for it only puzzled.
    if status.get("TempOfBox") is not None:
        temps += " · внутри %d°" % round(float(status.get("TempOfBox") or 0))
    data = {
        "v": VERSION, "k": kind, "s": state_ru(code, online),
        "f": str(info.get("Filename") or "") if live else "",
        "p": round(float(info.get("Progress") or 0), 1) if live else 0,
        "l": ("слой %d из %d" % (_whole(info.get("CurrentLayer")), total) if total
              else "" if live else
              "Можно ставить печать" if online else "Переподключаюсь к принтеру"),
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


# ------------------------------------------------------------ screens on the card
#
# Every other screen of the bot (files, fans, macros, confirmations...) is
# drawn on the card too. The bot runs the very handler the inline button would
# run, takes the text and keyboard it would have put under the message, and
# answers the card's request with them as a screen:
#   sc = {"t": title, "x": [lines], "k": [[[label, code, style], ...], ...]}
# code "c:<callback_data>" runs that button, "u:<url>" opens a link, "b" goes
# back to the card, "" is a label that cannot be pressed. The answer reaches
# only the card that asked and changes nothing else: going back is local.

BACK = {"refresh", "brief", "cardmenu"}


def lines(text):
    """Telegram HTML to plain lines; blank runs collapse to one."""
    plain = re.sub(r"<br\s*/?>", "\n", str(text or ""))
    plain = _html.unescape(re.sub(r"<[^>]+>", "", plain))
    out = []
    for line in plain.split("\n"):
        line = line.strip()
        if line or (out and out[-1]):
            out.append(line)
    while out and not out[-1]:
        out.pop()
    return out


def _style(label):
    if label.startswith("✅"):
        return "y"
    if label.startswith(("🗑", "⏹")):
        return "k"
    return ""


def screen(text, keyboard, title=None):
    """A bot screen as the card draws it. ``title`` replaces the text, for
    the submenus whose text is only the status the card already shows."""
    body = lines(text)
    if title:
        head, body = title, []
    else:
        head, body = (body[0], body[1:]) if body else ("", [])
    rows = []
    for row in keyboard or []:
        out = []
        for button in row:
            label = str(button.get("text") or "")
            data = button.get("callback_data") or ""
            if button.get("url"):
                code = "u:" + button["url"]
            elif data in BACK:
                code = "b"
            elif data and data != "noop" and len(("c:" + data).encode("utf-8")) <= QUERY_LIMIT:
                code = "c:" + data
            else:
                code = ""
            out.append([label, code, _style(label)])
        if out:
            rows.append(out)
    return {"t": head, "x": body, "k": rows}


def fit(sc, note=""):
    """The answer carrying ``sc`` within 1024 bytes, or None if even a cut
    version does not fit: long lines are shortened, then the last lines
    dropped, then the button labels shortened."""
    sc = {"t": sc["t"][:80], "x": [line[:140] for line in sc["x"]],
          "k": [[list(b) for b in row] for row in sc["k"]]}

    def dump():
        return json.dumps({"sc": sc, "m": note} if note else {"sc": sc},
                          ensure_ascii=False, separators=(",", ":"))

    def fits():
        return len(dump().encode("utf-8")) <= ANSWER_LIMIT

    while not fits() and sc["x"]:
        sc["x"] = sc["x"][:-2] + (["…"] if len(sc["x"]) > 2 else [])
        if sc["x"] == ["…"]:
            sc["x"] = []
    for width in (28, 18, 12):
        if fits():
            break
        for row in sc["k"]:
            for b in row:
                if len(b[0]) > width:
                    b[0] = b[0][:width - 1] + "…"
    return dump() if fits() else None


def answer(**fields):
    """The bot's answer to a request from the card: JSON the card reads.

    m      a line to show under the buttons
    a, t, q, y  a confirmation: the action, its one-use token, the question
           and the label of the button that confirms
    close  back to the card (after an action that ended its screen)
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
