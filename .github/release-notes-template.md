## What this is

The first public release of a Telegram bot for the Elegoo Centauri Carbon.
It runs on your own Windows PC, talks directly to the printer on the local
network, and does not require a server or cloud account.

## Changes

- Guided `Setup.cmd`: enter the Telegram token and printer IP, then let the
  wizard discover your chat ID and verify the printer connections.
- Live printer status, camera snapshots, print notifications and optional
  controls from Telegram.
- Monitoring-only mode for installations where remote control must be disabled.
- Optional per-user Windows autostart, read-only diagnostics and rotating logs
  with Telegram tokens removed.
- Project support and Tribute links in `/help`, plus one unobtrusive note after
  a successful print at most once per month.

## Fixes

- Configuration and runtime state are stored outside the program folder and
  written atomically, so updates do not overwrite user settings.
- Public-safety checks cover both the repository and the downloadable archive.

## Compatibility

- Windows 10 / 11
- Python 3.9 or newer
- Elegoo Centauri Carbon on the same local network
- Verified on printer firmware V1.4.49

## Known limitations

- Other printer firmware versions, Centauri Carbon 2, macOS and Linux have not
  yet been verified.
- One bot installation controls one printer.

## Updating from the previous version

This is the first public release. For later updates, unpack the new ZIP over the
old program folder; settings in `%LOCALAPPDATA%` are not touched.

## Verifying the download

    certutil -hashfile <file>.zip SHA256

and compare with `SHA256SUMS.txt`.

---

Full changelog: [CHANGELOG.md](https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/blob/main/CHANGELOG.md)
