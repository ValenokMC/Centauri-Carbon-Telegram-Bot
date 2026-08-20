# Security

## Reporting a vulnerability

**Do not open a public issue.**

Use [GitHub's private vulnerability reporting](https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/security/advisories/new),
or write to [@SupporBiBot](https://t.me/SupporBiBot?start=centauri_bot) and ask
for a private channel.

Expect an acknowledgement within about a week. This is a one-person project;
there is no security team and no formal SLA. Please give a reasonable amount of
time for a fix before disclosing publicly.

## Threat model, stated plainly

This bot is designed for **one trusted user on a trusted local network**. That
is not a limitation to work around — it is the design.

### What the bot protects

| | |
|---|---|
| **Your token** | Stored in `%LOCALAPPDATA%`, never in the repository, never in a log. Log output is scrubbed of anything token-shaped on the way to both file and console. |
| **Access** | Every update is checked against the configured `chat_id`. Anything else is refused before it reaches a command. |
| **Destructive actions** | Stop, pause and starting a print require an explicit confirmation. |
| **Your files** | Config and state are written atomically, so a crash mid-write cannot corrupt them. |

### What the bot does NOT protect against, and cannot

| | |
|---|---|
| **An exposed printer** | SDCP has **no authentication at all**. Anyone who can open a TCP connection to port 3030 can pause, stop and move your printer, with or without this bot. Do not forward those ports. Do not put the printer on a network you do not control. |
| **A stolen token** | Anyone holding your token can act as your bot. It cannot act as *you* — the bot still only obeys the configured chat — but it can read what your bot is told. Revoke via `/revoke` in @BotFather. |
| **Your own PC being compromised** | The token is on disk, readable by your user account. There is no key vault here; adding one would be theatre while the same account can read the file anyway. |
| **Telegram itself** | Messages go through Telegram's servers. If that is unacceptable for your threat model, this is not the right tool. |

### Deliberate design decisions

- **No inbound connections.** The bot only makes outgoing connections — to
  Telegram, and to the printer on your LAN. Nothing listens on a port. There is
  nothing to firewall.
- **No telemetry.** Nothing is measured, counted, or sent anywhere. The author
  cannot tell whether you use the bot, or whether you ever pressed the support
  button — by design, not by omission.
- **No third-party runtime dependencies.** The entire supply chain is Python's
  standard library. There is no package to typosquat.
- **User data outside the program folder.** Re-extracting or updating the ZIP
  cannot overwrite your token or your state.
- **Autostart is opt-in, visible, and per-user.** It prints exactly what it will
  register, asks first, needs no administrator rights, and can be removed with
  one command.

## Supported versions

The latest release. This project is too small for parallel maintenance branches.

## Hardening you can do

- Put the printer on a separate VLAN or guest network, if your router allows it.
- Use monitoring-only mode if you never need to pause or stop remotely — the
  wizard offers it, and it refuses every control command.
- Do not enable autostart on a shared computer.
