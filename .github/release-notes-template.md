## What changed

This release fixes the duplicate status left after opening files with `/files`
and adds optional anonymous installation statistics.

## Changes

- Statistics are disabled by default and require an explicit Yes in `Setup.cmd`.
- No keeps every bot feature working.
- If enabled, only a random installation id, project code and version are sent,
  at most once every 30 days. See `PRIVACY.md` for the exact contract.

## Fixes

- `/files` now replaces the tracked main message, so Back returns to that same
  message instead of leaving a second status behind.

## Compatibility

- Windows 10 / 11
- Python 3.9 or newer
- Elegoo Centauri Carbon on the same local network
- Verified on printer firmware V1.4.49

## Known limitations

- Other printer firmware versions, Centauri Carbon 2, macOS and Linux have not
  yet been verified.
- One bot installation controls one printer.

## Updating from v1.0.0

Unpack the new ZIP over the old program folder. Settings in `%LOCALAPPDATA%` are
not touched. Run `Setup.cmd` only if you want to choose whether to contribute
anonymous statistics; the default for existing installs remains No.

## Verifying the download

    certutil -hashfile <file>.zip SHA256

and compare with `SHA256SUMS.txt`.

---

Full changelog: [CHANGELOG.md](https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/blob/main/CHANGELOG.md)
