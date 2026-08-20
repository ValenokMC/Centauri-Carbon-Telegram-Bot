# Changelog

All notable changes to this project are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

Nothing yet.

## [1.0.0] — unreleased

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

[Unreleased]: https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot/releases/tag/v1.0.0
