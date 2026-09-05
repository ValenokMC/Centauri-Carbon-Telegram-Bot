# Changelog

All notable changes to this project are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Firmware auto-detection. The setup wizard now asks the printer which firmware
  it runs instead of making the user pick, and `backend: "auto"` repeats that
  probe on every start. Moonraker answering `/printer/info` is positive proof of
  OpenCentauri/COSMOS; the stock SDCP port is only consulted when it stays
  silent, and an unanswered printer is reported rather than guessed at.

- OpenCentauri/COSMOS support through a dependency-free Moonraker polling
  backend for status, files, webcam snapshots and explicitly enabled job
  controls.
- Backend capability policy: Moonraker starts read-only, with separate opt-ins
  for pause/resume/cancel and remote file start.
- A confirmed "exclude object" control for multi-object COSMOS prints. The bot
  binds the confirmation to the exact object and print file, then rechecks both
  against Moonraker immediately before sending the fixed Klipper command.

### Changed

- The five owner-approved COSMOS macros now use clear Russian action names in
  Telegram. The macro screen and confirmation explain what each action does;
  the original Klipper name remains visible only as a technical identifier.

### Security

- File-start confirmations are one-use, expire after five minutes and bind to
  the exact path instead of a mutable list index.
- New private-chat configurations verify both the destination chat and callback
  sender. Existing group-chat configurations remain compatible until an owner
  user id is configured.
- Moonraker API keys are sent only in a request header and are omitted from
  configuration summaries and URLs.

## [1.1.2] — 2026-08-23

### Fixed

- Connection-lost and connection-restored notices now replace the single main
  bot panel instead of accumulating as separate Telegram messages. The offline
  view drops the stale camera frame, and the restored status returns to the
  bottom of the chat with a fresh frame when available.
- A Telegram failure while showing a network notice can no longer interrupt
  the printer reconnection loop.

## [1.1.1] — 2026-08-23

### Fixed

- Opening Files or pressing Back from a still-clickable older bot message now
  updates the tracked main message and removes the stale one instead of leaving
  two bot panels in the chat.

## [1.1.0] — 2026-08-23

### Added

- Optional anonymous installation statistics, disabled by default and offered
  separately by the setup wizard. When enabled, the bot reports only a random
  installation id, project code and version at most once per 30 days. Declining
  disables no feature, failures never affect the bot, and consent can be
  withdrawn by re-running the wizard.
- A complete bilingual privacy description in `PRIVACY.md`.

### Fixed

- Opening the printer file list with `/files` and then pressing Back no longer
  leaves a duplicate status message in the Telegram chat.

## [1.0.0] — 2026-08-23

First public release.

The bot itself is not new: it has been running against a real Centauri Carbon
for months. What is new is that somebody other than its author can install it.
Configuration is done by a wizard instead of by editing Python, and nothing
personal is baked into the code.

### Added

- **Setup wizard** (`Setup.cmd`). Checks Python, takes the token without echoing
  it, validates the format locally, verifies it with `getMe`, discovers the
  owner's `chat_id` from `getUpdates`, probes the printer's status and camera
  ports separately, and writes the configuration. The user never opens a Python
  file, and never types a chat id by hand.
- **Monitoring-only mode**, offered by the wizard, which refuses every control
  command.
- **Read-only diagnosis** (`Check.cmd`): shows the configuration without the
  token, and probes both printer ports.
- **Optional autostart** (`Install-Autostart.cmd`, `Remove-Autostart.cmd`) via
  the Windows Task Scheduler. Per-user, no administrator rights, prints exactly
  what it will create and asks first.
- **`/help` screen** with links to the documentation, the issue tracker, the
  support bot, and a way to support the author.
- **Rotating logs** with the token scrubbed, so a log is safe to attach to a bug
  report.
- **A once-a-month support note**, appended to a successfully finished print and
  to nothing else.

### Changed

- **User data now lives in `%LOCALAPPDATA%\CentauriCarbonTelegramBot\`** rather
  than beside the script. Updating or re-extracting the program can no longer
  overwrite a token, a maintenance counter, or a pinned message id.
- **The single-file script became a package.** Configuration, storage, the
  Telegram transport, the SDCP client, the lifecycle state machine and the
  interface are now separate modules — with characteristic tests written against
  the original behaviour *before* the split.
- Configuration and state files are written atomically.
- The interface is unchanged. Every message and every button behaves as it did;
  that is what the characteristic tests are for.

### Security

- The token is never written to a log, never printed by the wizard, and never
  included in an error message.
- `config.json` cannot reach the repository: it is not there, it is gitignored,
  and `tools/check_public_safety.py` fails the build if one appears.

[Unreleased]: https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/compare/v1.1.2...HEAD
[1.1.2]: https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/releases/tag/v1.0.0
