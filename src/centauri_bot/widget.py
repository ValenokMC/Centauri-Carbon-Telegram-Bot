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
import datetime
import html as _html
import json
import logging
import re
import zlib

from . import printer_state as ps


log = logging.getLogger(__name__)

VERSION = 12

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

# Buttons on the card, one letter each (see ACTIONS, and buttons()).
PAUSE, RESUME, STOP, CAMERA = "P", "R", "S", "C"

# Short keys: 1024 bytes is 512 Cyrillic letters, and a long file name has to
# fit next to the rest.
#   v  format version          k  "p" printing, "w" paused, "x" anything else
#   s  state, in Russian       f  file name
#   p  progress, %             l  "слой 20 из 45"
#   tn, tb  the nozzle's and the bed's heating ring as "now|target|fill|hue|heating",
#      see heat() and _pipe(); ti the air inside
#      in whole degrees, only when the printer has it
#   et "· ещё 20 мин"         ef  "готово ≈ 14:30" (printing only)
#   o  1 online, 0 not (the speed, if changed, rides on the layer line)
#   ab the action buttons to draw: [[label, request, style, icon], ...], see ACTIONS
#   mn the rest of the panel behind "⚙️ Ещё": [[label, callback_data, icon], ...],
#      taken from the very keyboard the bot would put under a message, so
#      the owner's permissions decide here exactly as there
#   u  when the bot wrote this, epoch seconds
#   go a stored screen the bot wants shown now (a command's answer, the
#      printer's own dialog...): the card asks for it by itself, once
#   n  a short note that outlives the tap that caused it ("Пауза поставлена",
#      "Кадр получить не вышло"): the answer to a tap comes before the work
#
# The card's state classes are "p", "w" and "x": no other rule may use those
# names, or it restyles the whole card (the big percentage once did).

HTML = r"""<style>
:root{--g:light-dark(rgba(0,0,0,.05),rgba(255,255,255,.07));--l:light-dark(rgba(0,0,0,.12),rgba(255,255,255,.14));--b:light-dark(rgba(255,255,255,.7),rgba(255,255,255,.1));--ac:var(--tg-theme-button-color)}
[hidden]{display:none!important}
body{font-variant-numeric:tabular-nums;margin:0;padding:8px;color:var(--tg-theme-text-color)}
.cd{border-radius:18px;padding:12px;border:1px solid var(--l);background:light-dark(rgba(60,120,255,.08),rgba(90,150,255,.14))}
.k-w{--ac:#e5a50a}.k-x{--ac:var(--tg-theme-destructive-text-color)}.k-i{--ac:var(--tg-theme-hint-color)}
.top,.rg b,.hd,.a,.rw,.ht,.tl{display:flex}.top,.rg b,.hd,.tl{align-items:center}
.rg b{justify-content:center}.top{gap:12px}
.rg{position:relative;width:70px;height:70px;flex:none;color:var(--ac)}
.rg>svg,.rg b{position:absolute;inset:0;width:100%;height:100%}
.rg>svg{stroke-width:3.6}.rg b{color:var(--tg-theme-text-color);font-size:1.2em}
.tl{gap:8px;flex:1}.tl .rg{width:38px;height:38px}.ht{gap:10px;margin-top:10px}
svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8}
button svg{width:16px;height:16px;margin-right:5px}
@media(prefers-reduced-motion:no-preference){.hot .rg,.re{animation:pu 2s infinite}@keyframes pu{50%{opacity:.45}}}
.k-i #rg{display:none}
.mn{flex:1;min-width:0}
.s{font-size:1.15em;font-weight:700;color:var(--ac)}
.f,.h,.u,.n,button:disabled{color:var(--tg-theme-hint-color)}
.f{font-size:.88em}
.l{margin-top:4px}.h{font-size:.85em}
.cp{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.cp span{padding:4px 10px;border-radius:99px;background:var(--g);border:1px solid var(--l);font-size:.85em}
.a,.rw{gap:6px;margin-top:8px}.a button,.rw button{flex:1}
.g{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:6px}.g .y{grid-column:1/-1}
.f,.hd b,button{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
button{background:var(--b);border:1px solid var(--l);color:inherit;border-radius:12px;padding:9px 6px;font:inherit;font-size:.9em;min-width:0}
.rw .k:not(:only-child){flex:none;padding:9px 14px}
.rw button:disabled{flex:2;padding:9px 6px 0}
.a,.wr{flex-wrap:wrap}.a button,.wr button{min-width:max-content;padding-inline:4px}
@media(max-width:399px){.a button{flex:1 1 40%}}
#cz{width:220px;max-width:100%;height:auto;display:block;margin:8px auto 0;border-radius:12px;background:var(--g)}
button.k{color:var(--tg-theme-destructive-text-color)}
button.y{background:var(--tg-theme-button-color);color:var(--tg-theme-button-text-color);border-color:transparent}
button:disabled{background:none;border-color:transparent;padding:4px 6px 0}
.hd{gap:8px}.hd button{flex:none}
.hd b{flex:1;min-width:0;font-size:1.08em}
.tx{margin-top:8px;font-size:.92em;line-height:1.4}
.n{font-size:.84em;margin-top:6px}
</style>
<div id=c class=cd><div id=cv><div class=top><div class=rg id=rg><b id=p></b></div>
<div class=mn><div class=s><span id=s></span></div><div class=f id=f></div>
<div class=l><span id=l></span> <span id=t></span></div><div class=h id=e></div></div></div>
<div class=ht><div class=tl id=tn><i class=rg id=rn><b><svg viewBox="0 0 24 24"><path d="M8 4h8v6l-4 9-4-9z"/></svg></b></i><span id=xn></span></div>
<div class=tl id=tb><i class=rg id=rb><b><svg viewBox="0 0 24 24"><path d="M3 11h18v4H3zM6 15v5M18 15v5"/></svg></b></i><span id=xb></span></div></div><div class=cp id=m></div><div class=a id=a></div><div class=g id=g hidden></div></div>
<div id=v hidden><div class=hd><button id=bk>‹ Назад</button><b id=vt></b></div><canvas id=cz width=440 height=440 hidden></canvas><div class=tx id=vx></div><div id=vk></div></div>
<div class=n id=n></div></div>
<script>(function(){
var W=window.TelegramWidget,d=null,ask=null,busy=0,mnu=0,sc=null,ln='',IC={};try{IC=JSON.parse(localStorage.getItem('ic11'))||{}}catch(e){}
function $(i){return document.getElementById(i)}
function x(i,v){$(i).textContent=v}
function er(e){x('n','ошибка: '+String(e).slice(0,120))}
function clr(i){var a=$(i);a.textContent='';return a}
function el(p,t,v){var e=document.createElement(t);e.textContent=v;p.appendChild(e);return e}
function S(t,a){var o=document.createElementNS('http://www.w3.org/2000/svg',t);for(var k in a)o.setAttribute(k,a[k]);return o}
function rr(i,f,c){var e=$(i),s=S('svg',{viewBox:'0 0 36 36'}),o={cx:18,cy:18,r:16,pathLength:100,transform:'rotate(-90 18 18)',style:'stroke:var(--l)'};
if(c)e.style.color=c;if(e.r)e.removeChild(e.r);s.appendChild(S('circle',o));
if(f>0){o.style='';o['stroke-dasharray']=f+' 100';o['stroke-linecap']='round';s.appendChild(S('circle',o))}e.insertBefore(e.r=s,e.firstChild)}
function tm(k,v){var a=v?v.split('|'):[0,0,0,210,0],h=+a[4];$('t'+k).className='tl'+(h?' hot':'');rr('r'+k,+a[2],'hsl('+a[3]+',80%,50%)');x('x'+k,a[0]+'°'+(h?' → '+a[1]+'°':''))}
function mk(p,t,c,f,n){var e=el(p,'button',t),P=n&&IC[n.slice(0,2)];e.className=c;if(f)e.onclick=f;else e.disabled=true;
if(P)try{var s=S('svg',{viewBox:'0 0 24 24','class':n});s.appendChild(S('path',{d:P}));e.insertBefore(s,e.firstChild)}catch(r){}
return e}
function ic(){if(IC.pa)return;
W.request('ic').then(function(r){try{IC=JSON.parse(r.result).ic;try{localStorage.setItem('ic11',JSON.stringify(IC))}catch(e){}show()}catch(e){}},function(){setTimeout(ic,3e4)})}
function go(q){if(busy)return;busy=1;mnu=0;x('n','Отправляю…');
W.request(q).then(null,function(){return{result:'{"m":"Не дошло, ещё раз"}'}}).then(function(r){busy=0;try{var a=JSON.parse(r.result);
ask=a.t?a:null;if(a.sc)sc=a.sc;if(a.close||ask)sc=null;
x('n',ask?ask.q:a.m||'');show()}catch(e){er(e)}})}
function tap(c){if(c=='b'){sc=null;x('n','');show()}else if(c.slice(0,2)=='u:'){var u=c.slice(2);
try{if(/^https:\/\/t\.me\//.test(u))W.openTelegramLink(u);else W.openLink(u)}catch(e){}}else go(c)}
function btns(){var a=clr('a'),g=clr('g');g.hidden=!mnu;
if(!d||!W.request||W.isForward)return;
if(ask){mk(a,ask.y,'y',function(){go('do:'+ask.a+':'+ask.t)});mk(a,'Отмена','',function(){ask=null;x('n','');show()});return}
var mn=d.mn||[];
(d.ab||[]).forEach(function(i){mk(a,i[0],i[2],function(){go(i[1])},i[3])});
var t=function(){mnu=!mnu;x('n','');show()};
if(mn.length)mk(a,IC.mo?'Ещё':'⚙️ Ещё','',t,'mo');
if(mnu){mn.forEach(function(i){mk(g,i[0],'',function(){go('c:'+i[1])},i[2])});mk(g,'‹ Назад в главное','y',t)}}
function cv(n){return getComputedStyle(document.documentElement).getPropertyValue(n)}
function fig(g){var c=$('cz'),o=g&&g.s,m=g&&g.m,t,B=428,K=B/63,i,j,f,n,s,a,x,y,w,h,z,u,v,p,A='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';c.hidden=!(o||m);if(c.hidden)return;
c.width=440;t=c.getContext('2d');
if(m){for(i=0;i<m.h;i++)for(j=0;j<m.w;j++){u=+m.z.substr(2*(i*m.w+j),2)/99;
t.fillStyle='hsl('+(u<.5?210:5)+',65%,'+(95-110*Math.abs(u-.5))+'%)';t.fillRect(6+j*B/m.w,6+i*B/m.h,B/m.w+.5,B/m.h+.5)}return}
f=cv('--tg-theme-text-color');t.globalAlpha=.2;t.strokeStyle=f;t.lineWidth=3;t.strokeRect(6,6,B,B);
for(n=0;n<o.length;n++){s=+o[n];if(s==3)continue;
a=g.b.substr(4*n,4).split('').map(function(k){return A.indexOf(k)*K});x=6+a[0];y=6+a[1];w=a[2]-a[0];h=a[3]-a[1];
t.fillStyle=t.strokeStyle=cv(s==1?'--tg-theme-destructive-text-color':'--tg-theme-button-color');
t.globalAlpha=[.05,.25,.85,0,.4][s];t.fillRect(x,y,w,h);t.globalAlpha=[.35,.6,1,1,1][s];t.strokeRect(x,y,w,h);
v=parseInt(g.n[n],36);if(v){z=20+8*((v-1)/5|0);p=(v-1)%5;t.fillStyle=s==2&&!p?'#fff':f;t.font='600 '+z+'px sans-serif';
t.textAlign='center';t.textBaseline='middle';t.fillText(n+1,x+w/2+[0,0,1,-1,0][p]*(w/2+3+.3*z*String(n+1).length),y+h/2+[0,-1,0,0,1][p]*(h/2+3+.4*z))}}}
function drawScreen(){try{x('vt',sc.t||'');fig(sc.g);var t=clr('vx');(sc.x||[]).forEach(function(s){el(t,'div',s)});
var k=clr('vk');(sc.k||[]).forEach(function(r){var w=el(k,'div','');
w.className='rw'+(r.length>2?' wr':'');
r.forEach(function(b){mk(w,b[0],b[2],b[1]?function(){tap(b[1])}:null,b[3])})})}catch(e){er(e)}}
function show(){$('cv').hidden=!!sc;$('v').hidden=!sc;try{if(sc)drawScreen();else btns()}catch(e){er(e)}}
function draw(){try{d=null;try{d=JSON.parse(W.chatData)}catch(e){}
var ok=!!(d&&d.v);$('c').hidden=!ok;if(!ok)return;
$('c').className='cd k-'+d.k;x('s',d.s);x('f',d.f||'');
var p=Math.max(0,Math.min(100,+d.p||0));x('p',Math.floor(p)+'%');
rr('rg',p);tm('n',d.tn);tm('b',d.tb);
x('l',d.l||'');var m=clr('m');if(d.ti)el(m,'span',d.ti);
if(!busy)show();x('t',d.et||'');x('e',d.ef||'');
if(d.n&&d.n!=ln&&!busy&&!ask){ln=d.n;x('n',d.n)}if(!d.n)ln='';
if(d.go&&!busy&&W.request&&!W.isForward){var l='';try{l=localStorage.getItem('go')}catch(e){}
if(l!=d.go){try{localStorage.setItem('go',d.go)}catch(e){}go('pg:'+d.go+':0')}}}catch(e){er(e)}}
$('bk').onclick=function(){tap('b')};
if(W){W.onEvent('chatDataChanged',draw);draw();if(W.ready)W.ready();if(W.request&&!W.isForward)setTimeout(ic,4e3)}
})();
</script>"""

# First 12 hex digits of sha256 of HTML. A card that is already in the chat
# keeps the HTML it was sent with, so any change of HTML must bump VERSION; a
# test compares this with the real hash and fails until both are updated.
HTML_SHA = "63ef194c1a9e"


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


# What a full heating ring means when there is no target (the heater is off
# or cooling): the ring then shows the temperature against this, so it visibly
# drains as the nozzle or bed cools.
NOZZLE_TOP, BED_TOP = 300, 120


def _pipe(numbers):
    """[245, 245, 100, 0, 0] as "245|245|100|0|0".

    Telegram Desktop's widget sandbox reads an ARRAY OF NUMBERS out of chat_data
    wrongly (the elements come out joined by dots: "слой 20.45 из 45" for
    [20, 45], "245.245°" for a ring). Arrays of strings and plain numbers are
    fine. So the card gets numbers in strings, never in lists, anywhere."""
    return "|".join(str(n) for n in numbers)


def heat(cur, target, top):
    """One heating ring's numbers: [current, target, fill %, hue, heating].

    Fill is current / target (full at the target, never over), or current /
    ``top`` when there is no target. The hue is blue (210) below 40 °C, then
    amber (45) at 40 °C falling linearly to red (0) at 80 % of ``top``. ``heating`` is 1 while the
    target is still more than 2 degrees away: the ring then pulses.
    """
    cur, target = round(float(cur or 0)), round(float(target or 0))
    fill = min(100, max(0, cur * 100 // (target or top)))
    # Blue while cold; from 40 degrees amber (45) falling to red (0), never green.
    hue = 210 if cur < 40 else int(45 * (1 - min(1.0, (cur - 40) / (top * 0.8 - 40))))
    return [cur, target, fill, hue, 1 if target > 0 and cur < target - 2 else 0]


# What each letter of buttons() is on the card: label, request, style.
ACTIONS = {PAUSE: ["Пауза", "ask:pause", "", "pa"], RESUME: ["Дальше", "ask:resume", "", "re"],
           STOP: ["Стоп", "ask:stop", "k", "st"], CAMERA: ["Кадр", "snap", "", "ca"]}


def _duration(seconds):
    """"1 ч 20 мин", "2 ч", "5 мин": at least a minute."""
    minutes = max(1, int(seconds / 60.0 + 0.5))
    hours, minutes = divmod(minutes, 60)
    if hours:
        return "%d ч %d мин" % (hours, minutes) if minutes else "%d ч" % hours
    return "%d мин" % minutes


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


# Thin-line icons, 24x24, drawn by the card as <path d=...> with stroke 1.8,
# round caps and joins, in the text colour. The card's script has no room for
# them (its code is limited to 4096 bytes and cannot be replaced once sent), so
# it asks for this table once ("ic") and keeps it. Every button the bot sends
# carries an icon id as its last field; a client that cannot draw it still has
# the label, which loses its leading emoji only when an id is attached.
ICONS = {
    "pa": "M8 5v14M16 5v14",
    "re": "M7 4l13 8-13 8z",
    "st": "M6 6h12v12H6z",
    "ca": "M3 8h4l2-3h6l2 3h4v11H3zM12 10a3.5 3.5 0 1 0 .01 0",
    "mo": "M4 7h10M18 7h2M4 17h2M10 17h10M14 5v4M8 15v4",
    "fi": "M3 6h6l2 2h10v11H3z",
    "pr": "M7 9V4h10v5M7 17H4v-7h16v7h-3M7 14h10v6H7z",
    "de": "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13",
    "cl": "M12 4a8 8 0 1 0 .01 0M12 8v4l3 2",
    "cu": "M6 6a2.5 2.5 0 1 0 .01 0M6 18a2.5 2.5 0 1 0 .01 0M8 8l12 10M8 16L20 6",
    "fa": "M12 12C10 7 12 3 15 4s2 5-3 8M12 12c5 0 8 2 7 5s-5 2-7-5M12 12c-2 5-6 6-8 4s1-6 8-4",
    "sp": "M4 16a8 8 0 1 1 16 0M12 16l4-5",
    "he": "M12 3c1 4 5 5 5 10a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3 1-3 0-6 1-9",
    "li": "M9 18h6M10 21h4M12 3a6 6 0 0 0-3 11v2h6v-2a6 6 0 0 0-3-11",
    "be": "M3 17l9-5 9 5-9 5zM3 12l9-5 9 5",
    "hi": "M4 12a8 8 0 1 0 3-6.2M4 4v4h4M12 8v4l3 2",
    "ma": "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6",
    "di": "M3 12h4l2-5 4 10 2-5h6",
    "hp": "M9 9a3 3 0 1 1 4 2.8c-.7.4-1 1-1 2M12 17h.01",
    "in": "M12 8h.01M11 12h1v5h1",
    "bk": "M15 5l-7 7 7 7",
}

# The icon of a button by the start of its callback_data. The first match
# wins. The level buttons of the speed, fan and heat screens ("set:...") have
# none: five icons in a row of short labels would only crowd it.
ICON_PREFIXES = (
    ("ask:pause", "pa"), ("ask:resume", "re"), ("ask:stop", "st"), ("snap", "ca"),
    ("files", "fi"), ("ask:print:", "pr"), ("ask:delete:", "de"), ("ask:delall:", "de"),
    ("plan", "cl"), ("objects", "cu"), ("ask:exclude:", "cu"),
    ("menu:fans", "fa"), ("menu:speed", "sp"), ("menu:temp", "he"), ("light", "li"),
    ("mesh", "be"), ("history", "hi"), ("macros", "ma"), ("ask:macro:", "ma"),
    ("bedcalib", "ma"), ("diag", "di"), ("help", "hp"), ("details", "in"),
    ("objgrp:", "cu"),
)


def icon_for(data):
    """The icon id for a button's callback_data, or "" for none."""
    data = str(data or "")
    for prefix, icon in ICON_PREFIXES:
        if data.startswith(prefix):
            return icon
    return ""


def plain(label):
    """``label`` without its leading emoji and spaces, if something is left.

    "Letterlike symbols" (the U+2100 block, where the information sign "ℹ"
    lives) are letters to Unicode but emoji to a reader: stripped too."""
    text = str(label)
    start = 0
    while start < len(text):
        ch = text[start]
        if (ch.isalnum() and not 0x2100 <= ord(ch) <= 0x214F) or ch in "«(\"'":
            break
        start += 1
    return text[start:] or text


def icons_answer():
    """The answer to the card's "ic" request."""
    return _dump({"ic": ICONS})


def menu(keyboard, fans_on=False):
    """The "⚙️ Ещё" items: the usual panel's buttons, minus those on the card.

    The panel is built by ui.kb_main from the owner's settings, so a button
    the owner has not allowed is not in it, and so not here either.
    """
    items = []
    for row in keyboard or []:
        for button in row:
            data = button.get("callback_data")
            # The same limit a request has: an item that could not be sent
            # back would be a dead button.
            if (data and data not in ON_THE_CARD
                    and len(("c:" + data).encode("utf-8")) <= QUERY_LIMIT):
                icon = icon_for(data)
                if icon == "fa" and fans_on:
                    icon = "fa1"            # a fan is running: its icon spins
                label = str(button.get("text") or "")
                items.append([plain(label), data, icon] if icon else [label, data])
    return items


def menu_allows(keyboard, data):
    """A request "open:<data>" names a button the panel really has."""
    return any(item[1] == data for item in menu(keyboard))


def card(status, online, printer_name, now, keys="", items=None, go="", note="", tz=None):
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
    data = {
        "v": VERSION, "k": kind, "s": state_ru(code, online),
        "f": str(info.get("Filename") or "") if live else "",
        "p": round(float(info.get("Progress") or 0), 1) if live else 0,
        "l": ("слой %d из %d" % (_whole(info.get("CurrentLayer")), total) if total
              else "" if live else
              "Можно ставить печать" if online else "Переподключаюсь к принтеру"),
        "tn": _pipe(heat(status.get("TempOfNozzle"), status.get("TempTargetNozzle"), NOZZLE_TOP)),
        "tb": _pipe(heat(status.get("TempOfHotbed"), status.get("TempTargetHotbed"), BED_TOP)),
        "o": 1 if online else 0, "u": int(now),
    }
    # The time left and the finish clock, ready-made: the card's script has no
    # room for the arithmetic, and a push comes whenever the minute changes.
    if left > 0:
        data["et"] = ("· " if data["l"] else "") + "ещё " + _duration(left)
    # Stock SDCP reports the air inside the printer; COSMOS does not, and an
    # empty cell for it only puzzled.
    if status.get("TempOfBox") is not None:
        data["ti"] = "внутри %d°" % round(float(status.get("TempOfBox") or 0))
    if keys:
        data["ab"] = [list(ACTIONS[key]) for key in keys]
    if items:
        data["mn"] = items
    if go:
        data["go"] = go
    if note:
        data["n"] = note[:120]
    # Only while it prints: on a pause the finish moves with every minute.
    if kind == "p" and online and left > 0:
        data["ef"] = "готово ≈ " + datetime.datetime.fromtimestamp(
            now + left, tz).strftime("%H:%M")
    # The speed, when it is not the usual one, rides on the layer line: the
    # card's own script has no room left for a part of its own.
    speed = info.get("PrintSpeedPct")
    if speed not in (None, 100) and live:
        data["l"] = (data["l"] + " · " if data["l"] else "") + "⚡%s%%" % speed
    return data


def _dump(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _small(text):
    return len(text.encode("utf-8")) <= DATA_LIMIT


def encode(data):
    """chat_data text within 1024 bytes, always.

    What gives way, in order: a long file name is cut, then dropped; then the
    note; then the menu loses long labels, then items from its end, then goes
    entirely. The numbers and buttons of the print itself are never touched.
    """
    if data is None:
        return ""
    data = dict(data)
    name = data.get("f", "")
    for cut in (None, 60, 24, 0):
        if cut is not None:
            data["f"] = name[:cut] + ("…" if cut and len(name) > cut else "")
        text = _dump(data)
        if _small(text):
            return text
    data.pop("n", None)
    text = _dump(data)
    if _small(text):
        return text
    items = [list(item) for item in data.get("mn") or []]
    for width in (20, 12):
        items = [[item[0][:width]] + item[1:] for item in items]
        data["mn"] = items
        text = _dump(data)
        if _small(text):
            return text
    while items:
        items.pop()
        data["mn"] = items
        text = _dump(data)
        if _small(text):
            return text
    data.pop("mn", None)
    return _dump(data)


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
    if label.startswith(("✅", "▶")):
        return "y"
    if label.startswith(("🗑", "⏹")):
        return "k"
    return ""


def screen(text, keyboard, title=None, figure=None):
    """A bot screen as the card draws it. ``title`` replaces the text, for
    the submenus whose text is only the status the card already shows.
    ``figure`` is a drawing for the card's canvas (objectmap.figure,
    heightmap.figure), put under the key "g"."""
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
            icon = "bk" if code == "b" else icon_for(data) if code.startswith("c:") else ""
            if plain(label).isdigit():
                icon = ""            # a number button: the digits alone, as narrow as can be
            style = _style(label)
            out.append([plain(label), code, style, icon] if icon
                       else [label, code] if plain(label).isdigit() and not style
                       else [label, code, style])
        if out:
            rows.append(out)
    out = {"t": head, "x": body, "k": rows}
    if figure:
        out["g"] = figure
    return out


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
                    b[0] = middle(b[0], width)
    return dump() if fits() else None


# ------------------------------------------------------------------ pages
#
# A screen that does not fit one answer is cut into pages, never into a worse
# screen: every button keeps its full label (sixteen file buttons shortened to
# twelve letters all read alike), and a "‹ 2/3 ›" row moves between pages.
# The bot keeps the whole screen for a while under a short token; a page is
# asked for with "pg:<token>:<n>", and the screen is cut again on every request.

SCREEN_TTL_SEC = 600
SCREENS_KEPT = 16
# A widget bubble is 50-600 px tall and clips what does not fit (Telegram
# recommends staying under about 560 px at 320 px width). So a page is limited
# by height as well as by bytes: this many text lines and button rows (the
# nav row among them), and a line is cut rather than left to wrap.
MAX_LINES = 8
MAX_ROWS = 9
LINE_WIDTH = 60
# An estimate of how tall a page draws, in px, from what a bubble shows:
# everything but the content (back button, paddings, the note and update
# lines), a figure (170 px and its margin), a line of text, a row of buttons.
# The bubble is 600 px at most and clips; a page stays under MAX_HEIGHT_PX at
# the narrowest bubble, where lines wrap most.
PAGE_BASE_PX = 118
FIGURE_PX = 228
LINE_PX = 20
ROW_PX = 44
MAX_HEIGHT_PX = 560
CHAR_PX = 7.2          # a character of text; of a button label 7.5 and 20 around it
FIGURE_ON_EVERY_PAGE = 150     # bytes: only a drawing this small is repeated on each page


def thin_figure(figure):
    """The objects drawing without the excluded objects (state "1"), cut at
    the last object that is still drawn. None if it holds no states."""
    states = figure.get("s") if isinstance(figure, dict) else None
    if not states:
        return None
    thin = states.replace("1", "3").rstrip("3")
    return {"b": figure["b"][:4 * len(thin)], "s": thin,
            "n": (figure.get("n") or "")[:len(thin)]} if thin else None


def estimate_height(sc, width=320):
    """How many px the screen ``sc`` (title, lines "x", rows "k", figure "g")
    takes in a bubble ``width`` px wide, by the constants above."""
    inner = max(120, width - 42)
    per_line = max(10, int(inner / CHAR_PX))
    height = PAGE_BASE_PX + (FIGURE_PX if sc.get("g") else 0)
    for line in sc.get("x") or ():
        height += LINE_PX * max(1, -(-len(line) // per_line))
    for row in sc.get("k") or ():
        pad = 12 if len(row) > 2 else 20      # a row of many has narrow buttons
        need = sum(len(b[0]) * 7.5 + pad + (21 if len(b) > 3 else 0) for b in row) \
            + 6 * (len(row) - 1)
        # a row of three or more wraps; one or two buttons clip their label
        height += ROW_PX * (max(1, int(-(-need // inner))) if len(row) > 2 else 1)
    return height


def nav_row(tok, n, total):
    """[‹] n/N [›]; no arrow where there is no page to go to."""
    row = []
    if n > 0:
        row.append(["‹", "pg:%s:%d" % (tok, n - 1), ""])
    row.append(["%d/%d" % (n + 1, total), "", ""])
    if n < total - 1:
        row.append(["›", "pg:%s:%d" % (tok, n + 1), ""])
    return row


def _answer_text(sc, note=""):
    return _dump({"sc": sc, "m": note} if note else {"sc": sc})


def middle(label, width):
    """``label`` cut to ``width`` characters in the middle, so that the end,
    which tells similar names apart, stays."""
    if len(label) <= width:
        return label
    tail = width // 3
    return label[:width - 1 - tail] + "…" + label[-tail:]


def _tighter(kind, unit):
    """Ever shorter forms of one line or button row, for a unit that does not
    fit a page on its own."""
    if kind == "x":
        for width in (100, 60, 30):
            yield unit[:width]
    else:
        for width in (40, 28, 18):
            yield [[middle(b[0], width)] + list(b[1:]) for b in unit]


def paginate(sc, tok, note=""):
    """The answers for ``sc``: one string per page, each within 1024 bytes.

    ``tok`` is the token the bot stores the screen under, or a function
    that does so; it is only asked for when more than one page is needed.
    An empty list means nothing could be fitted.
    """
    sc = dict(sc)
    sc["t"] = sc["t"][:80]
    sc["x"] = [line if len(line) <= LINE_WIDTH else line[:LINE_WIDTH - 1] + "…"
               for line in sc["x"]]
    sc["k"] = [[list(b) for b in row] for row in sc["k"]]
    figure = sc.pop("g", None)
    whole = dict(sc, g=figure) if figure else sc
    # The note never changes where the pages break (a later request for the
    # same page carries none): it rides on page 0 when there is room.
    one = _answer_text(whole)
    if (_small_answer(one) and len(sc["x"]) <= MAX_LINES and len(sc["k"]) <= MAX_ROWS
            and estimate_height(whole) <= MAX_HEIGHT_PX):
        return [_with_note(whole, note)]
    tok = tok() if callable(tok) else tok
    on_every = bool(figure) and len(_dump(figure)) <= FIGURE_ON_EVERY_PAGE
    worst = nav_row(tok, 10, 99)      # the longest nav row any page can have

    def build(x, k, first):
        page = {"t": sc["t"], "x": x, "k": k + [worst]}
        if figure and (first or on_every):
            page["g"] = figure
        return page

    def fits(x, k, first):
        # the worst nav row is counted in the rows
        if len(x) > MAX_LINES or len(k) + 1 > MAX_ROWS:
            return False
        page = build(x, k, first)
        return (estimate_height(page) <= MAX_HEIGHT_PX
                and _small_answer(_answer_text(page)))

    if figure and not fits([], sc["k"][:1], True):
        # No room for the drawing and one button: try it without the objects
        # that are already gone, and only then give it up, loudly.
        thin = thin_figure(figure)
        figure = thin if thin and fits([], sc["k"][:1], True) else None
        if figure is None:
            log.info("screen drawing dropped: it does not fit an answer")
        on_every = bool(figure) and len(_dump(figure)) <= FIGURE_ON_EVERY_PAGE
    units = [("x", line) for line in sc["x"]] + [("k", row) for row in sc["k"]]
    pages = [([], [])]
    for kind, unit in units:
        for form in [unit] + list(_tighter(kind, unit)):
            x, k = pages[-1]
            first = len(pages) == 1
            trial = (x + [form], k) if kind == "x" else (x, k + [form])
            if fits(trial[0], trial[1], first):
                pages[-1] = trial
                break
            if x or k:
                # this page is full: the unit goes on a fresh one
                pages.append(([], []))
                x, k = pages[-1]
                trial = (x + [form], k) if kind == "x" else (x, k + [form])
                if fits(trial[0], trial[1], False):
                    pages[-1] = trial
                    break
        # a unit that fits no page even in its tightest form is dropped
    pages = [p for p in pages if p[0] or p[1]] or [([], [])]
    out = []
    for n, (x, k) in enumerate(pages):
        rows = k + ([nav_row(tok, n, len(pages))] if len(pages) > 1 else [])
        page = {"t": sc["t"], "x": x, "k": rows}
        if figure and (n == 0 or on_every):
            page["g"] = figure
        text = _with_note(page, note if n == 0 else "")
        if not _small_answer(text):
            return []
        out.append(text)
    return out


def _with_note(page, note):
    text = _answer_text(page, note)
    return text if _small_answer(text) else _answer_text(page)


def _small_answer(text):
    return len(text.encode("utf-8")) <= ANSWER_LIMIT


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
