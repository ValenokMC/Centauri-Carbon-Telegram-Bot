## What changed

This patch fixes duplicate bot panels caused by pressing Files or Back on an
older Telegram message whose inline keyboard was still active.

## Changes

- The bot now recognises when a button belongs to a stale message.
- It updates the currently tracked panel and removes the older one.
- If neither panel can be edited, it safely recreates one clean panel.

## Fixes

- Files and Back preserve the one-message interface even when an old inline
  keyboard is pressed.

## Compatibility

- Windows 10 / 11
- Python 3.9 or newer
- Elegoo Centauri Carbon on the same local network
- Verified on printer firmware V1.4.49

## Known limitations

- Other printer firmware versions, Centauri Carbon 2, macOS and Linux have not
  yet been verified.
- One bot installation controls one printer.

## Updating from v1.1.0

Unpack the new ZIP over the old program folder. Settings in `%LOCALAPPDATA%` are
not touched; there is no need to run `Setup.cmd` again.

## Verifying the download

    certutil -hashfile <file>.zip SHA256

and compare with `SHA256SUMS.txt`.

---

Full changelog: [CHANGELOG.md](https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/blob/main/CHANGELOG.md)
