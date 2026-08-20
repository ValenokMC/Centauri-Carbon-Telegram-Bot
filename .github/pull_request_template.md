## What this changes

<!-- One or two sentences. What is different after this, from a user's point of view? -->

## Why

<!-- The problem, not the solution. If it fixes an issue, link it: Fixes #123 -->

## How it was tested

<!--
Say what you actually ran. "Tests pass" is fine for a documentation change; for
anything touching the printer or Telegram, say whether you tried it on real
hardware and with what firmware.
-->

- [ ] `python -m pytest tests/ -q` passes
- [ ] `python tools/check_public_safety.py` passes
- [ ] Tried against a real printer — model and firmware:
- [ ] Not applicable (documentation only)

## Checklist

- [ ] No third-party runtime dependency added to `src/`
- [ ] No test opens a socket, contacts Telegram, or writes to the real `%LOCALAPPDATA%`
- [ ] No token, private IP address, or personal path anywhere in the diff
- [ ] Comments explain *why*, where the code is not obvious
- [ ] `CHANGELOG.md` updated under `[Unreleased]`, if a user would notice this

## Anything the reviewer should know

<!--
Trade-offs you made, alternatives you rejected, parts you are unsure about.
A note saying "I am not certain about X" is much more useful than silence.
-->
