# Publishing this repository — manual checklist

Everything here has to be done by a human in the GitHub web interface. None of
it was done automatically, and none of it can be undone by a `git push`.

Work top to bottom; the order matters in a few places.

---

## 1. Create the repository

- Owner: **ValenokMC**
- Name: **Centauri-Carbon-Telegram-Bot**
- Visibility: **Public**
- **Do not** let GitHub add a README, a `.gitignore` or a licence — they are
  already here and would collide.

```bash
gh repo create ValenokMC/Centauri-Carbon-Telegram-Bot --public --source . --remote origin
```

or add the remote by hand:

```bash
git remote add origin https://github.com/ValenokMC/Centauri-Carbon-Telegram-Bot.git
```

## 2. Push

```bash
git push -u origin main
```

## 3. Description and topics

**Settings → General**, or the ⚙ beside "About" on the repository front page.

**Description:**

> Telegram bot for the Elegoo Centauri Carbon. Runs on your own PC — no server, no cloud.

**Website:** leave empty, or the releases page.

**Topics:**

```
elegoo
centauri-carbon
telegram-bot
3d-printing
sdcp
python
windows
printer-monitoring
```

## 4. Social preview

**Settings → General → Social preview → Edit → Upload an image.**

Upload `assets/social-preview.png` — 1280 × 640, under 1 MB.

This is what appears when the link is pasted into Telegram, Discord or Twitter.
Worth doing before you share the link anywhere.

## 5. Features

**Settings → General → Features:**

- ✅ **Issues**
- ✅ **Discussions** — set up the categories below
- ❌ Wikis — the documentation lives in `docs/`, and two places to look is worse
  than one
- ❌ Projects — not needed at this size

## 6. Discussions categories

**Discussions → ⚙ (top right) → Categories.**

| Category | Format | For |
|---|---|---|
| **Announcements** | Announcement | Releases and breaking changes. Maintainer posts only. |
| **Q&A** | Q&A | "How do I", "is this normal". Answers can be marked. |
| **Ideas** | Open discussion | Before something becomes a feature request. |
| **Show and tell** | Open discussion | Prints, setups, what people built. |

Delete "General" — it collects everything and belongs nowhere.

## 7. Sponsorships

**Settings → General → Features → Sponsorships → ✅**

`.github/FUNDING.yml` is already committed, so a **Sponsor** button appears once
this is enabled. Nothing else is needed; the Tribute link is in the file.

## 8. Security

**Settings → Security → Code security and analysis:**

- ✅ **Private vulnerability reporting** — this is what `SECURITY.md` points
  people at, so it must be on
- ✅ Dependabot alerts
- ✅ Secret scanning, and **Push protection**

Push protection is worth enabling even though there is a local scanner: the
scanner runs when someone remembers to run it, push protection runs always.

## 9. Community profile

**Insights → Community Standards.** Every row should be green:

- ✅ Description
- ✅ README
- ✅ Code of conduct
- ✅ Contributing
- ✅ License
- ✅ Security policy
- ✅ Issue templates
- ✅ Pull request template

## 10. Branch protection

**Settings → Branches → Add branch ruleset** (or classic protection) for `main`:

- ✅ Require a pull request before merging
- ✅ Require status checks to pass:
  - `Tests (Python 3.9)`
  - `Tests (Python 3.12)`
  - `Syntax and byte-compile`
  - `Public-safety scan`
  - `Build and inspect the release archive`
- ✅ Require branches to be up to date before merging
- ✅ Block force pushes
- ✅ Do not allow bypassing the above settings

> As the only maintainer you may find "require a pull request" annoying. Leaving
> the **status checks** required while allowing yourself to push directly is a
> reasonable middle ground — the safety scanner still gates everything.

## 11. Merge behaviour

**Settings → General → Pull Requests:**

- ✅ Allow squash merging — set the default message to **pull request title and
  description**
- ❌ Allow merge commits
- ❌ Allow rebase merging
- ✅ Always suggest updating pull request branches
- ✅ **Automatically delete head branches**

One merge style keeps the history readable.

## 12. First release

Only after everything above, and after a real run on a real printer.

```bash
python -m pytest tests/ -q
python tools/check_public_safety.py
python tools/build_release.py --version 1.0.0
python tools/check_public_safety.py dist/Centauri-Bot-v1.0.0-windows.zip
```

Then tag it:

```bash
git tag -a v1.0.0 -m "v1.0.0"
git push origin v1.0.0
```

The release workflow builds the archive, re-runs the tests and both scans, and
opens a **draft** release. Edit the notes from
`.github/release-notes-template.md`, check both files are attached
(`Centauri-Bot-v1.0.0-windows.zip` and `SHA256SUMS.txt`), then publish.

**Before publishing, download your own archive, unpack it somewhere clean, and
run `Setup.cmd`.** It is the only way to find out that something a user needs
was left out of the allow-list.

## 13. Pin it

**Your profile → Customize your pins.** Pin both this repository and the
calibrator, so the two read as one line of tools.

---

## Not automated on purpose

None of the above is done by a script, and the release workflow opens a draft
rather than publishing. Making a repository public, uploading an image under an
account's name, and publishing a downloadable binary are all decisions a person
should take deliberately.
