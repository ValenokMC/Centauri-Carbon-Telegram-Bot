## What changed

This patch keeps printer connection notices inside the bot's single main panel
instead of leaving separate messages above it.

## Changes

- After the configured grace period, connection loss replaces the main panel
  with a clear offline view and removes the stale camera frame.
- When the printer reconnects, the offline view is replaced by a current status
  at the bottom of the chat, with a fresh frame when the camera is available.
- Brief network interruptions below the grace period remain silent.

## Fixes

- Telegram errors while drawing a network notice cannot stop the printer
  reconnection loop.

## Compatibility

- Windows 10 / 11
- Python 3.9 or newer
- Elegoo Centauri Carbon on the same local network
- Verified on printer firmware V1.4.49

## Known limitations

- Other printer firmware versions, Centauri Carbon 2, macOS and Linux have not
  yet been verified.
- One bot installation controls one printer.

## Updating from v1.1.1

Unpack the new ZIP over the old program folder. Settings in `%LOCALAPPDATA%` are
not touched; there is no need to run `Setup.cmd` again.

## Verifying the download

    certutil -hashfile <file>.zip SHA256

and compare with `SHA256SUMS.txt`.

---

Full changelog: [CHANGELOG.md](https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/blob/main/CHANGELOG.md)
