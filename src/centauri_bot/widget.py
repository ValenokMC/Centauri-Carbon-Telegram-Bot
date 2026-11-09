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


KIND = "widget"              # storage.message_kind() of a widget status message

# sendWidget limits, from the Bot API reference.
HTML_LIMIT = 32768           # bytes of UTF-8
HTML_COMPRESSED_LIMIT = 4096  # after zlib at level 9
DATA_LIMIT = 1024            # bytes of UTF-8 in chat_data
CAPTION_LIMIT = 1024         # characters after entity parsing

VERSION = 1

# Short keys: 1024 bytes is 512 Cyrillic letters, and a long file name has to
# fit next to the numbers.
#   v  format version          k  "p" printing, "w" paused, "x" anything else
#   i  state icon              s  state name
#   n  printer name            f  file name
#   p  progress, %             l  [layer, layers]
#   tn [nozzle, target]        tb [bed, target]        tc chamber
#   r  seconds left            e  finish, epoch seconds (printing only)
#   sp speed, % (only if not 100)                      o  1 online, 0 not
#   u  when the bot wrote this, epoch seconds

HTML = """<style>
body{margin:0;padding:10px 12px 8px;font:15px/1.35 system-ui,sans-serif}
.h{display:flex;justify-content:space-between;gap:8px;font-weight:600}
.h span:last-child{color:var(--tg-theme-hint-color);font-weight:400;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.f,.u,small{color:var(--tg-theme-hint-color)}
.f{font-size:.85em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin:2px 0 6px}
.p{font-size:2em;font-weight:700;font-variant-numeric:tabular-nums}
.b{height:8px;border-radius:4px;background:var(--tg-theme-secondary-bg-color);overflow:hidden;margin:2px 0 6px}
.b i{display:block;height:100%;width:0;background:var(--tg-theme-button-color);transition:width .6s}
.w .b i{background:#e5a50a}.x .b i{background:var(--tg-theme-destructive-text-color)}
.g{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;margin-top:8px;font-variant-numeric:tabular-nums}
small,.u{font-size:.8em}.u{margin-top:6px}.z{color:var(--tg-theme-destructive-text-color)}
</style>
<div id=c><div class=h><span id=s></span><span id=n></span></div>
<div class=f id=f></div><div class=p id=p></div><div class=b><i id=b></i></div>
<div id=t></div><div id=e></div>
<div class=g><div><small>сопло</small><br><span id=tn></span></div>
<div><small>стол</small><br><span id=tb></span></div>
<div><small>камера</small><br><span id=tc></span></div></div>
<div class=u id=u></div></div>
<p id=q hidden>⬇️ Статус — ниже</p>
<script>
var W=window.TelegramWidget,d=null;
function $(i){return document.getElementById(i)}
function x(i,v){$(i).textContent=v}
function dur(s){var m=Math.max(1,Math.round(s/60)),h=Math.floor(m/60);m%=60;
return h?(m?h+' ч '+m+' мин':h+' ч'):m+' мин'}
function deg(a){if(!a)return'—';var c=Math.round(a[0]),t=Math.round(a[1]||0);
return t&&Math.abs(c-t)>2?c+'→'+t+'°':c+'°'}
function clock(e){return new Date(e*1e3).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}
function tick(){if(!d)return;var now=Date.now()/1e3,
left=d.e?Math.max(0,d.e-now):d.r,l=d.l||[];
x('t',(l.length?'слой '+l[0]+' из '+l[1]:'')+(left>0?' · ещё '+dur(left):''));
x('e',d.e&&left>0?'готово ≈ '+clock(d.e):'');
var age=now-d.u;$('u').className=age>600?'u z':'u';
x('u',(d.sp?'скорость '+d.sp+'% · ':'')+(age>600?'нет обновлений '+dur(age):'обновлено '+clock(d.u)))}
function draw(){d=null;try{d=JSON.parse(W.chatData)}catch(e){}
var ok=!!(d&&d.v);$('c').hidden=!ok;$('q').hidden=ok;if(!ok)return;
$('c').className=d.k;x('s',d.i+' '+d.s);x('n',d.n||'');x('f',d.f||'');
var p=Math.max(0,Math.min(100,+d.p||0));x('p',(Math.round(p*10)/10)+'%');
$('b').style.width=p+'%';x('tn',deg(d.tn));x('tb',deg(d.tb));
x('tc',d.tc==null?'—':Math.round(d.tc)+'°');tick()}
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


def card(status, online, printer_name, now):
    """The widget's state as a dict, or None when there is no status yet."""
    if not status:
        return None
    info = status.get("PrintInfo") or {}
    code = info.get("Status")
    name, icon = ps.state_meta(code)
    if not online:
        name, icon = "связь потеряна", "🔌"
    if code == ps.STATUS_PRINTING:
        kind = "p"
    elif code in ps.STATUS_PAUSED:
        kind = "w"
    else:
        kind = "x"
    left = max(0, int((info.get("TotalTicks") or 0) - (info.get("CurrentTicks") or 0)))
    data = {
        "v": VERSION, "k": kind, "i": icon, "s": name,
        "n": str(printer_name or "")[:40],
        "f": "" if code in ps.STATUS_DONE else str(info.get("Filename") or ""),
        "p": round(float(info.get("Progress") or 0), 1),
        "l": [info.get("CurrentLayer") or 0, info.get("TotalLayer") or 0],
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
    # Only the printer name and state are left to blame; both are short.
    data["n"] = ""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def same_content(a, b):
    """Equal apart from the "written at" stamp: no reason to edit."""
    if a is None or b is None:
        return a is b
    return {k: v for k, v in a.items() if k != "u"} == \
        {k: v for k, v in b.items() if k != "u"}
