# Contributing

Thanks for looking. This is a small project with a narrow purpose, so a note on
what fits and what does not will save you time.

## What fits

- Bug fixes, especially with a test that fails before and passes after.
- Support for printer status codes not yet in `STATUS_META` — if you have seen
  one on a real printer and know what it means.
- Documentation and translation fixes.
- Making the setup wizard clearer. It is the part most users see only once, and
  the part that decides whether they keep the tool.

## What probably does not fit

- **Support for other printers.** Not because it would be unwelcome, but because
  claiming it without a machine to test on would be a lie. If you have a
  Centauri Carbon 2 and want to work on it, open a discussion first — it is a
  different protocol and probably a different project.
- **A third-party dependency.** Having none is a feature: it is the entire
  supply chain, and it is why a release is a folder you unzip. Make the case in
  an issue before writing the code.
- **Telemetry, analytics, crash reporting.** No.
- **Anything that makes the support button more prominent** or more frequent
  than once every 30 days. The restraint is a promise made to users, and there
  are tests enforcing it.

## Before you open a pull request

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q
python tools/check_public_safety.py
```

Both must pass. CI runs the same two commands.

**The safety scanner is not optional.** It refuses anything carrying private
data — a token, a private IP address, an absolute path inside somebody's user
profile. If it flags your change it is usually right; if it is wrong, say so in
the pull request rather than quietly adding yourself to the allow-list.

## House rules for the code

- **Standard library only** in `src/`. `pytest` is fine in `tests/`.
- **Tests must not touch the outside world.** No sockets, no real Telegram, no
  real `%LOCALAPPDATA%`. `tests/test_isolation.py` enforces all three. If your
  change breaks one of those tests, the change is wrong, not the test.
- **Comments explain why, not what.** The code already says what it does. The
  valuable comment is the one recording what went wrong last time — most of the
  awkward-looking decisions here carry one, and it is the reason the
  awkward-looking decision is correct.
- **Match the surrounding style.** Four spaces, about 88 columns, and the same
  string formatting the module around you already uses.
- **Interface strings are Russian, everything else is English.** Do not
  half-translate in either direction.

## Commits

Plain, imperative, specific:

```
wizard: never pick a chat id when several people have written
```

not "fix bug". If the commit fixes something a user reported, mention the issue.
