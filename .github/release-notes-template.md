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
- **Live print card** (opt-in, `"status_widget": true`). The status is one
  Telegram Bot API 10.4 widget that updates itself silently: three rings for
  print progress, nozzle and bed, the state and the time left. Every button is
  on the card with its own icon; the rest of the panel opens on the card behind
  ⚙️ Ещё. Removing a part shows the numbered bed picture above the card and a
  grid of the same numbers on it. Needs a current Telegram app; an older one
  shows the card's caption, and commands such as `/files` still work there.

## Fixes

- The finish photo on COSMOS shows the part again, not the lowered bed.
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
- The live print card works in private chats only (Telegram's rule) and
  cannot show a picture itself: frames and the bed picture come as separate
  messages above the card and are deleted again.

## Updating from v1.1.x

Unpack the new ZIP over the old program folder. Settings in `%LOCALAPPDATA%` are
not touched; there is no need to run `Setup.cmd` again. The new features are
off until you turn them on in `config.json`; see `docs/configuration.md`.

## Verifying the download

    certutil -hashfile <file>.zip SHA256

and compare with `SHA256SUMS.txt`.

---

Full changelog: [CHANGELOG.md](https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/blob/main/CHANGELOG.md)
