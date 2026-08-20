# Examples

`config.example.json` shows every key the bot understands, with the three
identity fields left empty.

You do not need this file. `Setup.cmd` writes a real configuration for you, in
`%LOCALAPPDATA%\CentauriCarbonTelegramBot\config.json` — outside this folder, so
that updating or re-extracting the program can never overwrite your token.

It is here for two reasons: to document the tunable values in one place, and so
that anyone reviewing the project can see exactly what the bot stores without
having to install it.

**Never put your real token in this file, and never attach your real
`config.json` to a bug report.** Attach the log instead — it is written with the
token redacted. See [SECURITY.md](../SECURITY.md).
