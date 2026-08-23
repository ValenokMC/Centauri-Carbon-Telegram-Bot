# Security notes for users

[SECURITY.md](../SECURITY.md) is the policy — how to report a vulnerability, and
the threat model. This page is the practical version.

## The one thing that matters

> [!CAUTION]
> **SDCP has no authentication.** None. Anyone who can open a TCP connection to
> your printer's port 3030 can pause it, stop it, move the head, and start a
> print — with or without this bot.

So:

- **Never forward ports 3030 or 3031 to the internet.** Not "just for a while".
  Not "it is only my IP". There is no password on the other side.
- **Do not put the printer on a network you do not control** — a shared flat
  network, an office guest Wi-Fi, a hackerspace LAN.
- If you want access from outside your home, use a VPN into your own network.
  The bot then runs on a machine inside it and nothing is exposed.

This is a property of the printer, not of this bot. It is worth knowing whatever
software you use.

## Your token

The token is the password to your bot.

**Where it lives:** `%LOCALAPPDATA%\CentauriCarbonTelegramBot\config.json`, on
your disk, readable by your Windows account.

**Where it does not live:** not in the repository, not in a log, not in a
message, not on any screen. The wizard never echoes it, `Check.cmd` shows only a
masked form, and every log line is scrubbed before it is written.

**If it leaks:**

1. Open [@BotFather](https://t.me/BotFather).
2. Send `/revoke`, choose your bot.
3. Take the new token. The old one stops working immediately.
4. Run `Setup.cmd` again.

Nobody who holds your token can act as *you* — the bot still obeys only the
configured chat — but they can act *as your bot*, and see what it is told.

## What is safe to share

| | |
|---|---|
| ✅ The log file | The token is redacted on the way out. Read it anyway before attaching. |
| ✅ The version, your Windows version, the firmware version | Nothing sensitive. |
| ✅ A screenshot of the status message | Check the printer name has not been set to something identifying. |
| ❌ `config.json` | The token is in it. |
| ❌ A screenshot with the token visible | Including one where you thought it was cropped. |
| ❌ Your `chat_id` | Not catastrophic, but it identifies your account. |
| ❌ Your printer's IP address | Reveals your internal network layout. |

## Access control

The bot obeys exactly one chat. Every incoming update is checked before it
reaches a command, and anything from elsewhere is refused with a short message.

There is no second user, no roles, no read-only guest. If you want somebody else
to watch the printer, they run their own bot with their own token.

**Monitoring-only mode** is a genuine restriction, not a UI change: the control
buttons are absent, and a command arriving anyway is refused in code. Choose it
in the wizard if you never need to pause or stop remotely.

## Dangerous actions

Stop, pause and starting a print all require an explicit confirmation. Starting
a print also shows a camera frame first, so you can see whether the bed is
clear.

That confirmation is the last thing between a mis-tap in your pocket and a hot
machine running unattended. It is not going to be made optional.

## What the bot does not do

- **No telemetry unless you explicitly opt in.** The wizard defaults to No and
  declining disables no feature. If enabled, one minimal report is sent at most
  once per 30 days; it contains only a random installation id, project code and
  application version. It never contains Telegram or printer data. See
  [PRIVACY.md](../PRIVACY.md) for the exact fields, endpoint and withdrawal.
- **No inbound connections.** Nothing listens on a port. There is nothing to
  firewall and nothing to scan.
- **No third-party runtime dependencies.** The whole supply chain is the Python
  standard library. There is no package to typosquat, and no transitive
  dependency to audit.
- **No auto-update.** The bot never downloads or runs anything. You update it by
  unpacking a new ZIP.

## Autostart

Opt-in. It prints exactly what it will create before creating it. It registers a
per-user logon task with no administrator rights and no elevation, and
`Remove-Autostart.cmd` really removes it.

Do not enable it on a shared computer: anyone who logs in as you gets a running
bot pointed at your printer.

## Hardening

- Put the printer on its own VLAN or a separate SSID, if your router can.
- Use monitoring-only mode unless you actually need remote control.
- Reserve the printer's IP in your router, so the address in your config does
  not silently start pointing at something else.
- Keep the log level at `INFO` for normal running; `DEBUG` is more verbose about
  what your printer is doing.
