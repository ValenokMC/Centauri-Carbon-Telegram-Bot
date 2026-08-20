# Troubleshooting

Start with `Check.cmd`. It reads your configuration, shows it without the token,
and probes both printer ports. Most of what follows is a reading of its output.

---

## `Python not found`

The launcher tries `py -3`, then `python`, then `python3`, and none answered.

- **Most likely:** Python was installed without *Add python.exe to PATH*.
  Re-run the installer, choose Modify, and tick it. Or reinstall.
- **Also possible:** you have the Microsoft Store stub. Typing `python` opens
  the Store instead of running anything. Install from
  [python.org](https://www.python.org/downloads/) instead.
- **Check it:** open a terminal and run `py -3 --version`.

---

## The wizard rejects the token

The format check is local, before anything is sent. A token is
`digits` `:` `at least 30 letters, digits, hyphens or underscores`.

- Copied only part of it — the message from BotFather wraps, and the tail is
  easy to miss.
- Copied the bot's *username* rather than its token.
- Picked up a stray space or a line break. Paste it into Notepad first if you
  are not sure.

If the format is right but Telegram rejects it, the token was revoked. Send
`/token` to BotFather to see the current one.

---

## The wizard cannot find my chat id

It watches the bot's own updates for up to a minute.

- **Press `/start` in the right bot.** Step 3 of the wizard prints the username;
  open that one, not another bot you already had.
- **Actually press Start.** Opening the chat is not enough — the button has to
  be pressed, or a message sent.
- **Another copy of the bot is running.** It will consume the update first. Stop
  it and try again.
- **The bot was used before with a different program**, and there is a backlog.
  Send it a fresh message and wait.

Fallback: the wizard offers to take the id by hand. To find it, forward any
message from yourself to a bot that reports ids — but be aware that hands your
account details to a stranger, which is the reason the wizard does this itself.

---

## Port 3030 closed

The printer is not answering. In order of likelihood:

1. The printer is switched off or asleep.
2. The address is wrong or has changed. Check on the printer: **Settings →
   Network**. DHCP moves addresses around; reserve one in your router.
3. The PC and the printer are on different networks — a guest Wi-Fi, or a 2.4/5
   GHz split with isolation on.
4. Client isolation ("AP isolation") is enabled on your router.
5. A firewall is blocking the outgoing connection. Rare, but check any
   third-party security suite.

Test it directly:

```
ping 192.168.1.50
```

If ping works but 3030 does not, the printer is reachable but its service is
not — restart the printer.

---

## Port 3031 closed

The camera is off. **This is not a fault.** Everything else works; you get no
photos. Enable the camera on the printer if you want them, or set
`send_photo` to `false` to stop the bot trying.

---

## Buttons work only every other press

Two copies of the bot are running. Telegram gives each update to exactly one
long-polling consumer, so the two are splitting them between themselves.

- Check for a second console window.
- Check Task Manager for more than one `python.exe` or `pythonw.exe`.
- If you enabled autostart and then also ran `Run.cmd`, that is exactly this.

---

## Two status messages in the chat

Delete both. The bot creates a fresh one on the next update.

This could happen in older versions when the event thread and the refresh thread
met on the same message. The current version holds a lock across the whole
operation and deletes the old message in every path.

---

## "Не для тебя" when I press a button

The chat you are pressing from is not the configured `chat_id`. That is the
access control working.

If it is genuinely you — for example you moved to a different Telegram account —
re-run `Setup.cmd` and let it discover the id again.

---

## The bot says a print finished when I stopped it

The printer reports the **same status code** for a completed print and a stopped
one, and clears the job information at the same moment. The bot decides from the
percentage reached: at or above 98 per cent it is a finish, below that it is a
stop.

Stopping in the last two per cent will therefore be reported as a finish. There
is no way to tell them apart from the outside.

---

## Nothing happens when a print starts

The start is announced when the printer first reports *printing*, not when the
job appears. The printer spends about a minute beforehand in preparation,
homing, calibration and heating, and announcing those would produce four
messages for one action.

If nothing arrives after that minute, check `Check.cmd`.

---

## The maintenance reminder will not go away

Press **"🧰 Смазал, сбросить счётчик"** under the status message. That resets
both the hours and the date.

To disable it entirely, set `maintenance_hours` and `maintenance_days` to `0` in
`config.json`.

---

## Autostart does not start it

- Open Task Scheduler and look for `CentauriCarbonTelegramBot`.
- The task triggers **at logon**, not at boot. If the machine boots to a lock
  screen without anyone logging in, it will not run.
- If the task exists but reports an error, the program folder has probably moved
  since it was registered. Run `Remove-Autostart.cmd`, then
  `Install-Autostart.cmd` again from the new location.

---

## Still stuck

1. Set `log_level` to `"DEBUG"` in `config.json`.
2. Reproduce the problem.
3. Open `logs\centauri-bot.log`.
4. Open an [issue](https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/issues)
   with the installation form filled in, and attach the log.

The log has the token redacted, so it is safe to attach. Read it anyway before
you do — it takes thirty seconds and is cheaper than revoking a token.
