# Getting help

## Where to go, in order

1. **[Documentation](docs/installation.md)** — installation, configuration and
   troubleshooting cover most of what comes up.
2. **[Discussions](https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/discussions)** —
   questions, ideas, "is this supposed to happen", showing what you printed.
3. **[Issues](https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/issues)** —
   a reproducible bug, or a specific concrete request.
4. **[@SupporBiBot](https://t.me/SupporBiBot?start=centauri_bot)** — if you do
   not have a GitHub account, or would rather not write in public.

Issues are for things that can be reproduced and fixed. Discussions are for
everything else. Putting a question in an issue is not a crime; it just tends to
get a slower answer than the same question in Discussions.

## What to expect

This project is written and maintained by one person, in their own time. An
answer can take a few days, and a fix can take longer. A clear report with a log
attached gets dealt with much faster than "it doesn't work".

## What to send

**Do send:**

- the application version (shown by `Check.cmd`)
- your Windows version
- the printer model and firmware version
- whether you are in monitoring-only or monitoring-and-control mode
- what you did, what you expected, what happened instead
- the log from `%LOCALAPPDATA%\CentauriCarbonTelegramBot\logs\centauri-bot.log`

## What NOT to send

> [!CAUTION]
> **Never send your BotFather token.** Not in an issue, not in a screenshot, not
> in a chat message. Anyone who has it owns your bot and, through it, your
> printer.

- ❌ **Your bot token** — in any form, including a screenshot with it visible.
- ❌ **Your whole `config.json`** — the token is inside it.
- ❌ Screenshots showing your `chat_id` or your printer's IP address unblurred.
- ❌ Your whole `%LOCALAPPDATA%\CentauriCarbonTelegramBot\` folder.

The log is written with the token redacted, so the log itself is safe. Read it
before attaching anyway — it is a short file, and thirty seconds of checking is
cheaper than revoking a token.

**If you have already posted a token somewhere:** open [@BotFather](https://t.me/BotFather),
send `/revoke`, pick the bot, and take the new token. The old one stops working
immediately. Then run `Setup.cmd` again.

## Security problems

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
