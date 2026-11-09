## What changed

Support for OpenCentauri/COSMOS printers through Moonraker, delayed print
starts, and an optional live print card that redraws itself in the chat.

## New

- **COSMOS / Moonraker.** Status, files, camera and, after explicit opt-ins,
  pause/resume/cancel and remote file start. The setup wizard detects the
  firmware by itself (`backend: "auto"`).
- **Remove one failed model** from a multi-object COSMOS print, picked by
  number on a picture of the bed. Bound to the exact object and file and
  rechecked right before the command.
- **Finish time on the clock** next to the time left, computed from the
  job's own G-code and the pace the printer actually keeps.
- **Delayed start.** Send a `.gcode` into the chat: it is uploaded and started
  now or at a set time, only if the printer is idle, the file is still there
  and the filament sensor sees filament. Otherwise the bot asks.
- **"Delete all files"** on the COSMOS file screen, behind its own opt-in.
- **Live print card** (opt-in, `"status_widget": true`). While a print runs,
  the status is a Telegram Bot API 10.4 widget with the state, progress,
  layer, temperatures and time left. It updates itself silently, with no
  "edited" mark, and the buttons stay under it. Needs a current Telegram app;
  an older one shows the short caption and the same buttons. If Telegram
  refuses the widget, the usual message goes out.

## Fixes

- The status message stays one message at the bottom of the chat, even after
  Telegram's 48-hour limit on deleting a bot's messages.
- Object removal works for models with Cyrillic names.
- After a print, schedule or delete confirmation, the status no longer freezes
  in an untracked message.

## Compatibility

- Windows 10 / 11
- Python 3.9 or newer
- Elegoo Centauri Carbon on the same local network
- Tested on stock firmware V1.4.49. OpenCentauri/COSMOS is covered by
  automated tests and in daily use on one printer.

## Known limitations

- Centauri Carbon 2, macOS and Linux have not yet been verified.
- One bot installation controls one printer.
- The live print card works in private chats only (Telegram's rule) and has
  no camera frame; `/snap` still sends one.

## Updating from v1.1.x

Unpack the new ZIP over the old program folder. Settings in `%LOCALAPPDATA%` are
not touched; there is no need to run `Setup.cmd` again. The new features are
off until you turn them on in `config.json`; see `docs/configuration.md`.

## Verifying the download

    certutil -hashfile <file>.zip SHA256

and compare with `SHA256SUMS.txt`.

---

Full changelog: [CHANGELOG.md](https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/blob/main/CHANGELOG.md)
