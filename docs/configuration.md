# Configuration

Everything the wizard asks, plus the settings it does not — and how to change
them without breaking anything.

## The file

```
%LOCALAPPDATA%\CentauriCarbonTelegramBot\config.json
```

`examples/config.example.json` in the repository lists every key with the
identity fields blank. You do not need it: `Setup.cmd` writes a real one.

To change the basics — token, chat id, printer, name, mode — **re-run
`Setup.cmd`**. It offers every existing answer as the default, so pressing Enter
through the parts you do not want to change is safe, and it never touches your
state, your maintenance counter or your logs.

To change anything else, edit the file in a text editor while the bot is
stopped. It is plain JSON.

## Identity

| Key | What it is |
|---|---|
| `telegram_token` | From BotFather. Format `digits:letters`. Never share it. |
| `chat_id` | The one chat the bot obeys. A number, sometimes negative. |
| `owner_user_id` | The Telegram user allowed to press buttons in that chat. The wizard fills it for private chats. |
| `printer_ip` | IP address or hostname on your LAN. |
| `printer_name` | The label in messages. Cosmetic. |

## Printer backend

The setup wizard asks the printer which firmware it runs before offering the
choice, so most people never touch these keys by hand. `backend: "auto"` repeats
that probe on every start: Moonraker is asked first on ports 80 and 7125, and the
stock SDCP port 3030 is only consulted when Moonraker stays silent. Nothing is
guessed - if neither answers, the bot exits with an explanation rather than
running against the wrong protocol.

Prefer a fixed `"sdcp"` or `"moonraker"` once the printer is known: it removes
two network probes from every start and makes the mode obvious in the config.

| Key | Default | What it does |
|---|---|---|
| `backend` | `"sdcp"` | `"sdcp"` for stock Elegoo V1.4.x, `"moonraker"` for OpenCentauri/COSMOS, or `"auto"` to ask the printer at startup. |
| `moonraker_url` | `""` | Full Moonraker base URL. If blank in Moonraker mode, `http://<printer_ip>` is used. |
| `moonraker_api_key` | `""` | Optional API key, sent only in the `X-Api-Key` header. Treat this file as secret. |
| `moonraker_poll_sec` | `2` | Status polling interval. Moonraker documents one to two seconds for polling clients. |
| `moonraker_timeout_sec` | `5` | Per-request timeout. |
| `moonraker_camera_url` | `""` | Optional snapshot URL. Blank asks Moonraker for its enabled webcam. |
| `moonraker_allow_external_camera` | `false` | Allows a camera URL on a host different from Moonraker. Keep false unless the camera is deliberately separate. |
| `moonraker_allow_job_control` | `false` | Enables pause, resume, cancel and confirmed exclusion of one model from a multi-object print. Separate from the global `allow_control` switch. |
| `moonraker_allow_remote_start` | `false` | Enables starting a selected G-code file after a one-use confirmation. Separate because it starts a hot machine. |

Moonraker mode does not expose arbitrary G-code, macros, restarts, firmware
restarts, emergency stop, heater targets, fans, light or speed controls. Those
operations either have installation-specific names or a larger consequence
than this first backend can safely infer.

## Behaviour

| Key | Default | What it does |
|---|---|---|
| `allow_control` | `true` | Global control switch. `false` makes either backend read-only. Moonraker still requires the two narrower opt-ins above. |
| `send_photo` | `true` | `false` turns off camera frames everywhere. Useful if the camera is disabled, or you would rather not have photos in a chat. |
| `progress_every_pct` | `0` | `0` is off. `25` sends a progress report at each quarter. Off by default because most people find it noise. |
| `anonymous_statistics` | `false` | Explicit opt-in. If `true`, sends only a random installation id, project code and version at most once per 30 days. `false` disables no bot feature. Re-run `Setup.cmd` to change it; see [PRIVACY.md](../PRIVACY.md). |

## Timing

| Key | Default | What it does |
|---|---|---|
| `keepalive_sec` | `20` | How often to poke the printer so it does not drop an idle connection. Raising it much causes disconnect-reconnect cycles. |
| `offline_grace_sec` | `60` | How long a dropout must last before you are told. Lower it and a Wi-Fi hiccup wakes you at 3am; that is why it exists. |
| `status_refresh_sec` | `120` | How often the status message is refreshed while printing. Minimum 30, enforced in code — Telegram rate-limits edits. |

## Night and light

| Key | Default | What it does |
|---|---|---|
| `light_off_at_night` | `true` | Turn the chamber light off after a print, but only at night. During the day a lit chamber bothers nobody; at night it shines into the room until morning. |
| `night_from` | `22` | Hour night starts, local time. |
| `night_to` | `8` | Hour night ends. The window may cross midnight. |

## Maintenance reminder

| Key | Default | What it does |
|---|---|---|
| `maintenance_hours` | `150` | Printing hours between rail lubrications. |
| `maintenance_days` | `60` | Or this many days, whichever comes first. |

Set both to `0` to switch the reminder off entirely.

Elegoo's wiki documents the lubrication procedure but publishes no figure in
hours — only "every one to two months". The 150 hours is an estimate for that
interval under ordinary use; either threshold fires, whichever arrives first. A
warning line appears at 80 per cent, and the reminder proper at 100.

Hours are counted from the printer's own `CurrentTicks`, by the rise rather than
the absolute value, so a restart or a change of job does not distort it. Press
the reset button in the chat after you lubricate.

## Logging

| Key | Default | What it does |
|---|---|---|
| `log_level` | `"INFO"` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. `DEBUG` is worth setting while diagnosing something, and worth unsetting afterwards. |

Logs go to `logs\centauri-bot.log`, rotating at 1 MB with three kept. The token
is scrubbed from every line on the way out, so the log is safe to attach to a
bug report. Your `config.json` is not — never attach that.

## After editing by hand

1. Stop the bot (close its window).
2. Edit and save.
3. Run `Check.cmd` — it validates the file and shows what it read, without the
   token.
4. Start the bot.

If the file will not parse, the bot says so and refuses to start rather than
running on half a configuration. Delete it and re-run `Setup.cmd` if you get
stuck; nothing else in the folder is affected.
