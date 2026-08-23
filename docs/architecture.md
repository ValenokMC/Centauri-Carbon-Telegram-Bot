# Architecture

Written for someone about to change the code. If you only want to run the bot,
[installation.md](installation.md) is the one you want.

## Shape

```
                        ┌──────────────────────────────┐
   api.telegram.org ◄──►│  telegram_loop  (main thread) │
                        │    handlers.py                │
                        └───────────────┬──────────────┘
                                        │  Bot (shared state, RLock)
                        ┌───────────────┴──────────────┐
   printer :3030   ◄──► │  printer_loop                 │
                        │    sdcp.WS → printer_state    │
                        ├──────────────────────────────┤
                        │  keepalive_loop               │
                        │  refresh_loop                 │
                        └──────────────────────────────┘
   printer :3031   ◄──── sdcp.grab_frame (on demand)
   stats endpoint  ◄──── telemetry_loop (only after explicit opt-in, monthly)
```

Four normal threads, plus one optional statistics thread, one shared `Bot`
object, one re-entrant lock around the mutable
parts. No inbound sockets: everything is an outgoing connection.

## Modules

| Module | Responsibility | Talks to the outside? |
|---|---|---|
| `paths.py` | Where user data lives | filesystem |
| `config.py` | Defaults, validation, atomic save, **redaction** | filesystem |
| `storage.py` | `state.json`, `maintenance.json`, seen status codes | filesystem |
| `logging_setup.py` | Rotating logs with the token scrubbed | filesystem |
| `telegram_api.py` | Bot API over a persistent HTTPS connection | network |
| `sdcp.py` | WebSocket framing, SDCP commands, MJPEG grab | network |
| `printer_state.py` | **Pure.** Status codes → lifecycle events | no |
| `ui.py` | **Pure.** Status text and keyboards | no |
| `support.py` | **Pure.** Links and the 30-day rule | no |
| `telemetry.py` | Explicit opt-in, minimal monthly heartbeat | network, filesystem |
| `handlers.py` | What a button or a command does | via `Bot` |
| `app.py` | The threads and the shared state | via the above |
| `setup_wizard.py` | First run | network, filesystem |
| `autostart.py` | Task Scheduler registration | subprocess |

The four **pure** modules hold most of the logic worth getting right, and can be
tested by calling a function and looking at what comes back. That is deliberate:
they were extracted from a single 1300-line script, and the extraction was only
safe because characteristic tests were written against the original behaviour
first.

## The state machine

`printer_state.PrinterLifecycle` takes `Status` dictionaries and returns events.
It holds five things between statuses: the previous code, the previous task, the
task already announced, whether the print has stalled, and a snapshot of the
last populated `PrintInfo`.

Four rules encode things learned from a real printer, and each is expensive to
rediscover:

**The first status after start-up announces nothing.** A print may have been
running before the bot was, and a restart must not look like a fresh start.

**Transient codes are skipped.** Starting goes `18 → 1 → 21 → 16 → 13` over
about a minute, and pausing goes `13 → 5 → 6`. Announcing each would turn one
user action into three or four messages. `STATUS_TRANSIENT` lists them.

**Finish and cancellation share a code.** The printer reports the same thing for
both, and clears `PrintInfo` at the same moment. The percentage reached is the
only way to tell them apart — hence the snapshot, and `DONE_THRESHOLD = 98`.

**"Stalled" needs a settled state.** Code 7 ("stopping") arrives immediately
before the normal "stopped"; treating it as a stall raised an alarm right before
a routine message.

## The single status message

`Bot.refresh_main` keeps exactly one message in the chat. It is edited in place;
buttons edit it too. With `force_new` it is deleted and recreated at the bottom,
so the keyboard stays under the thumb.

The lock around it is load-bearing. The event thread and the refresh thread once
met on the same message: one deleted it, the other failed to edit and created
another, and the chat was left with two. The old message is now deleted in every
path, including the one where a new message is created after a failed edit.

## The token

It exists in exactly three places: `config.json`, the `TelegramAPI` instance,
and the request path. It is never in a log line, an exception message, or a
printed URL.

Three things enforce that, in layers:

1. `telegram_api` never puts it into a message — errors carry the API's
   description, not the request.
2. `logging_setup.RedactingFilter` rewrites anything token-shaped on every
   record, on the way to both the file and the console. A backstop, for the edit
   nobody thought about.
3. `tools/check_public_safety.py` fails the build if a token reaches the
   repository.

`config.redact()` is what the wizard and `Check.cmd` display: the numeric bot id
(which is public — it is in the bot's username) plus three characters, enough to
tell two tokens apart and useless to anyone else.

## The support reminder

`support.due()` takes the state dict and a clock and returns a boolean. Three
conditions, all required: the install date is known, a full interval has passed
since installation, and a full interval has passed since the last showing.

Both timestamps are on disk. That is what makes the interval survive a restart —
an in-memory timer would ask again every time the bot came up, which for someone
using autostart would be daily.

`last_support_reminder_at` is written **only after Telegram confirms delivery**.
Stamping it first would silently swallow a month whenever the network happened
to be down at that moment.

The note is appended to a finished print and to nothing else. `tests/test_support.py`
asserts its absence from the status keyboard, confirmations, pauses, stalls and
cancellations, because "it does not nag" is a promise made in the README and an
untested promise stops being true.

## Testing rules

Three invariants, each with a test that enforces it:

- **No test opens a socket.** `FakeTelegram` replaces the API object; nothing in
  the suite constructs a real `TelegramAPI`, and a test greps for it.
- **No test writes to the real `%LOCALAPPDATA%`.** The data-dir fixture is
  autouse, so a test that forgets to ask is still isolated.
- **No test sleeps.** `SETTLE_SEC` exists so the post-command delays can be
  collapsed; the whole suite runs in well under a second.

If a change breaks one of those, the change is wrong.

## Things deliberately not done

- **No async.** A few threads and a lock are enough for these sockets, and are
  readable by someone who does not know asyncio.
- **No dependency injection framework.** Handlers take the `Bot` as their first
  argument. That is the whole mechanism.
- **No abstraction over Telegram or SDCP.** There is one of each and there will
  be one of each.
- **No config hot-reload.** Stop, edit, start. A printer control tool that
  changes behaviour under you is not a nice thing to own.
