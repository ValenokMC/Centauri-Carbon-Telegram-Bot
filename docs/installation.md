# Installation

Fifteen minutes, most of it waiting for Python to install. You will not open a
Python file, and you will not type a chat id by hand.

## 1. Install Python

Download from [python.org/downloads](https://www.python.org/downloads/) and run
the installer.

> [!IMPORTANT]
> On the first screen, tick **"Add python.exe to PATH"** before pressing
> Install. It is easy to miss, and it is the single most common reason
> `Setup.cmd` says `Python not found`.

Anything from 3.9 upwards works. If you already have Python, you are done here —
`py -3 --version` in a terminal will tell you.

## 2. Create your bot and get a token

1. Open [@BotFather](https://t.me/BotFather) in Telegram.
2. Send `/newbot`.
3. Give it a display name — anything, for example `My Centauri`.
4. Give it a username ending in `bot`, for example `my_centauri_1234_bot`. It has
   to be unique across all of Telegram, so expect a couple of tries.
5. BotFather replies with a token that looks like
   `123456789:AAE...`. Keep that message; you will paste the token in a moment.

> [!CAUTION]
> That token is the password to your bot. Anyone who has it can act as your bot.
> Do not put it in a screenshot, an issue, or a chat message. If it leaks, send
> `/revoke` to BotFather and take a new one.

## 3. Find your printer's address

On the printer: **Settings → Network**. Write down the IP address — something
like `192.168.1.50`.

If your router hands out addresses by DHCP, it is worth reserving one for the
printer so it does not change. Every router does this differently; look for
"DHCP reservation" or "static lease".

## 4. Download and unpack

Take the ZIP from the
[latest release](https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/releases/latest)
and unpack it anywhere you like — Documents, Desktop, a second drive.

A path with spaces or Cyrillic characters is fine; that case is tested.

> [!TIP]
> Unpack it properly, do not run it from inside the ZIP. Windows will let you
> double-click a `.cmd` inside a compressed folder, and it will fail in a
> confusing way.

## 5. Run the wizard

Double-click **`Setup.cmd`** (or `Настроить.cmd`).

It goes through eight steps:

| Step | What happens |
|---|---|
| 1. Python | Checks the version is high enough. |
| 2. Token | You paste the token. **Nothing appears as you type** — that is deliberate, it is not frozen. Press Enter. |
| 3. Verification | Asks Telegram whether the token works, and shows you the bot's username. |
| 4. chat_id | Asks you to press `/start` in your own bot, then finds your id from the bot's own updates. |
| 5. Printer | You enter the address from step 3. |
| 6. Connection | Checks port 3030 (status) and 3031 (camera), reporting each separately. |
| 7. Name | A label for the printer in messages. |
| 8. Mode | Monitoring only, or monitoring and control. |

Then it shows a summary — with the token masked — and asks whether to save.

### About step 4

Open the bot BotFather just created (its username is shown in step 3) and press
**Start**. The wizard is watching its own updates and will pick your id up
within a few seconds.

If several people have written to the bot, the wizard shows a list and asks
which one is you. It will never choose for you: whoever ends up in that field
becomes the only person the bot obeys.

### If the printer does not answer

Not fatal. The wizard offers to save anyway, so you can fix the network later
without redoing the whole thing. Check `Check.cmd` afterwards.

A closed camera port (3031) is not an error at all — the bot runs without
photos.

## 6. Start it

Double-click **`Run.cmd`** (or `Запустить.cmd`). A console window opens and
stays open; that window *is* the bot. Closing it stops the bot.

Send `/status` to your bot in Telegram. You should get the status message with
buttons.

> [!WARNING]
> Run only one copy at a time. Telegram allows exactly one long-polling consumer
> per token, and a second copy silently steals updates from the first — the
> symptom is buttons that work only every other press.

## 7. Optional: start with Windows

Run **`Install-Autostart.cmd`**.

It prints exactly what it is about to register — the task name, the program, the
arguments, the trigger — and asks before doing anything. The task runs as your
user, at logon, with no administrator rights and no elevated privileges.

It uses `pythonw.exe` where available, so no console window appears at every
logon.

To remove it: **`Remove-Autostart.cmd`**. That really removes it; there is
nothing left behind.

## Where your data lives

```
%LOCALAPPDATA%\CentauriCarbonTelegramBot\
├── config.json        your token, chat id, printer address
├── state.json         the pinned message id, install date
├── maintenance.json   accumulated printing hours
├── status-codes.txt   any printer status code not yet documented
└── logs\              rotating log, token redacted
```

Paste `%LOCALAPPDATA%\CentauriCarbonTelegramBot` into the Explorer address bar,
or run `Check.cmd` which prints the path.

This folder is deliberately **outside** the program folder. You can delete the
program folder and unpack a new version over the top without losing your
settings.

## Updating

1. Download the new ZIP.
2. Unpack it over the old folder, or beside it.
3. Run `Run.cmd`.

Your configuration is untouched — it is not in the folder you replaced. Re-run
`Setup.cmd` only if you want to change something.

## Uninstalling

1. `Remove-Autostart.cmd`, if you ever installed it.
2. Delete the program folder.
3. Delete `%LOCALAPPDATA%\CentauriCarbonTelegramBot\` if you want your settings
   gone too.
4. In [@BotFather](https://t.me/BotFather), `/deletebot` to remove the bot.

Nothing is written to the registry, and nothing is installed system-wide.
