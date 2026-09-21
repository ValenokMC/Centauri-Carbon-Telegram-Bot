# Centauri Carbon 2

The Carbon 2 does not speak either protocol this bot already knows. It is not a
variation on the Carbon 1 - it is a third backend.

| | Carbon 1 stock | Carbon 1 + COSMOS | Carbon 2 |
|---|---|---|---|
| transport | SDCP v3 over WebSocket | HTTP (Moonraker) | JSON-RPC over MQTT |
| port | 3030 | 80 / 7125 | 1883 |
| authentication | none | optional API key | required: `elegoo` + access code |
| discovery | UDP broadcast | — | UDP broadcast, port 52700 |
| camera | MJPEG on 3031 | through Moonraker | MJPEG on 8080, unauthenticated |

The access code is shown on the printer under Settings → LAN Only. That mode
must be switched on: without it the printer talks to Elegoo's cloud and exposes
no local API at all.

## What the community has established

Topics are built from the printer's serial number:

| topic | direction | purpose |
|---|---|---|
| `elegoo/<sn>/api_register` | to printer | register a client |
| `elegoo/<sn>/<request_id>/register_response` | from printer | registration result |
| `elegoo/<sn>/<client_id>/api_request` | to printer | commands and PING |
| `elegoo/<sn>/<client_id>/api_response` | from printer | replies and PONG |
| `elegoo/<sn>/api_status` | from printer | delta status updates |

Commands are `{"id": <n>, "method": <code>, "params": {}}`; replies mirror the
request with `result` in place of `params`. Reported method codes include 1001
printer information, 1002 full device state, 1020 start, 1021 pause, 1022
cancel, 1028 heaters, 1044 file list.

Known quirks, all of which shape the design:

- **Rate limiting.** Three or more requests in quick succession and replies are
  silently dropped for a while. Requests have to be spaced out.
- **Registration expires.** A client must publish `{"type": "PING"}` about every
  ten seconds or the printer forgets it; the connection is dropped after 65
  seconds of silence.
- **No file listing on some firmware.** On 01.03.02.51 the file methods do not
  answer at all.
- **The camera has no authentication.** Anyone on the LAN can watch it,
  regardless of the access code.

Sources: [pycentauri](https://github.com/bjan/pycentauri),
[elegoo-web](https://github.com/runnane/elegoo-web),
[CC2 protocol notes](https://github.com/danielcherubini/elegoo-homeassistant/blob/main/docs/CC2_PROTOCOL.md).

## Why a probe comes before a backend

Everything above is second-hand. Method codes and payload shapes differ between
firmware versions, and the bot has to name temperatures, progress and state
codes exactly right or it will report nonsense confidently - which is worse than
reporting nothing.

So `tools/cc2-probe.py` goes first. It connects, registers, subscribes to
everything under `elegoo/<sn>/#`, sends the three read-only requests, and writes
down every message the printer sends. The resulting log is what the real backend
gets built from.

The probe **only reads**. There is no code path in it that starts a print,
cancels one, moves an axis or switches on a heater. It is meant to be run on
somebody else's printer, and it should be safe to hand to a stranger.

```
python tools/cc2-probe.py --ip 192.168.1.50 --code 12345678
```

Step-by-step instructions for whoever runs it, in Russian, are in
[centauri-carbon-2-probe_RU.md](centauri-carbon-2-probe_RU.md).

The access code never reaches the log - it is replaced on the way out. The
serial number does, because it is part of every topic name; anyone uneasy about
that can replace it before sending the file on.

## MQTT without dependencies

`src/centauri_bot/mqtt.py` is a small MQTT 3.1.1 client built on sockets from
the standard library: CONNECT, SUBSCRIBE, PUBLISH, PINGREQ, DISCONNECT and
nothing else. QoS above 0, TLS, will messages and retained-message bookkeeping
are all absent, because the printer needs none of them.

Adding `paho-mqtt` would have been less work, and it would have cost this
project the property people actually praise it for: it installs by unpacking an
archive, with nothing to fetch. Roughly two hundred lines of packet handling is
a fair price for keeping that.

## State

Done: the MQTT client and the probe, both covered by tests against a fake
broker.

Not done: the backend itself. It waits on a probe log from a real Carbon 2,
because writing it against second-hand documentation would be guesswork
presented as support.
