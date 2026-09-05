#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refuse to publish anything that carries private data.

Run before every commit, in CI, and before a release is built:

    python tools/check_public_safety.py            # scan the repository
    python tools/check_public_safety.py dist/x.zip # scan a release archive

Exit code is non-zero if anything is found.

Two kinds of rule.

*Pattern rules* look for shapes that are private regardless of whose they are:
a bot token, a private key, an absolute user path, a private-range IP address.
These are the rules that keep working for contributors, whose secrets this
file has never seen.

*Fingerprint rules* look for specific known values - a particular home IP, a
particular hostname - by SHA-256 of the candidate token. The values themselves
are NOT in this file. That matters: a scanner that hard-codes the secrets it
looks for is itself a file that must never be published, which defeats the
purpose. Comparing digests means this file is safe to read, safe to publish,
and still catches the exact strings.

Nothing found is ever printed in full. A finding reports the file, the line,
the rule, and a redacted fragment - enough to locate it, never enough to leak
it into a terminal, a CI log, or a screenshot.
"""
import argparse
import hashlib
import io
import os
import re
import sys
import zipfile


# --------------------------------------------------------------- fingerprints
#
# SHA-256 of lowercased literals that must never appear. Adding one:
#     python -c "import hashlib;print(hashlib.sha256(input().strip().lower().encode()).hexdigest())"
# and paste the digest, never the value.
FINGERPRINTS = {
    "343163a82f101d8782f926651039a9c230c673846a6fbbd1c923308f4c6153d6":
        "author's LAN address",
    "33cfcd98ad8d94f2d98760b2747b28b0f918adb8fb079625da316a5cafd0f8b5":
        "author's WAN address",
    "fa23582775961fd1e1cf45b19f47327b2edd6b75f0aacd43b5d115b5c58a6bb9":
        "internal host name",
    "dc1e20f880a68a4b6b5bfbb73945d9ed565ff1357fda251b1733e5d9545488d4":
        "author's Windows account name",
    "b93d411e1ae56511462a23fd0a46f68d6b52013be20051c486dfe9273ea179e9":
        "author's private spool",
}

# How many words a fingerprint phrase may span. Keeping this small bounds the
# work: a 5 MB file with a window of 3 is a few hundred thousand cheap hashes.
MAX_PHRASE_WORDS = 3

WORD = re.compile(r"[A-Za-z0-9_.:@+-]+")


# ------------------------------------------------------------------ patterns

PATTERNS = [
    ("telegram-token",
     re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
     "a Telegram bot token"),

    ("telegram-api-url",
     re.compile(r"api\.telegram\.org/bot\d{6,12}:[A-Za-z0-9_-]{10,}"),
     "a Telegram API URL with a token in it"),

    ("private-key",
     re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----"),
     "a private key"),

    ("windows-user-path",
     # Placeholders are exempt, and that list is deliberate: documentation and
     # issue forms have to show the user what a path looks like, and
     # "C:\\Users\\USER" is the correct way to write it. Flagging that would
     # train people to ignore the scanner, which is worse than the finding it
     # would be reporting.
     re.compile(r"[A-Za-z]:\\+Users\\+"
                r"(?!Public\b|Default\b|All Users\b|USER\b|USERNAME\b|%USERNAME%|<[^>]+>|YourName\b)"
                r"[^\\\s\"'<>|]+",
                re.IGNORECASE),
     "an absolute path inside somebody's Windows profile"),

    ("private-ipv4",
     re.compile(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
                r"|192\.168\.\d{1,3}\.\d{1,3}"
                r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"),
     "a private-range IP address"),

    ("print-host",
     re.compile(r'"print_host"\s*:\s*"(?!\s*")[^"]+'),
     "a configured printer address"),

    ("ssh-deploy",
     re.compile(r"\b(?:scp|ssh|systemctl|rsync)\s+[^\s]", re.IGNORECASE),
     "a server deployment command"),

    ("env-assignment",
     re.compile(r"^\s*(?:TELEGRAM_TOKEN|BOT_TOKEN|API_KEY|SECRET_KEY|PASSWORD)\s*=\s*\S+",
                re.MULTILINE),
     "a secret assigned in an env file"),
]

# Files that are allowed to contain what would otherwise be a finding, with the
# reason. Kept deliberately short - every entry is a hole.
ALLOWED = {
    # This file describes the patterns it searches for.
    "tools/check_public_safety.py": {"telegram-token", "private-ipv4",
                                     "windows-user-path", "print-host",
                                     "ssh-deploy", "telegram-api-url",
                                     "private-key", "env-assignment"},
    # Tests must be able to use example values, and they use documentation
    # ranges and obviously-fake tokens.
    "tests/conftest.py": {"telegram-token", "private-ipv4", "print-host"},
    "tests/test_config.py": {"telegram-token", "private-ipv4"},
    "tests/test_isolation.py": {"telegram-token", "private-ipv4"},
    "tests/test_setup_wizard.py": {"telegram-token", "private-ipv4"},
    "tests/test_handlers.py": {"private-ipv4"},
    "tests/test_safety.py": {"private-ipv4", "print-host", "windows-user-path"},
    "tests/test_orca_and_support.py": {"private-ipv4", "print-host"},
    "tests/test_templates.py": {"private-ipv4", "print-host"},
    # Documentation has to show the user what an address looks like.
    "docs/centauri-carbon-2.md": {"private-ipv4"},
    "docs/configuration.md": {"private-ipv4"},
    "docs/installation.md": {"private-ipv4"},
    "docs/troubleshooting.md": {"private-ipv4", "print-host"},
    "docs/security.md": {"telegram-token", "private-ipv4", "print-host"},
    "docs/templates.md": {"print-host"},
    "README.md": {"private-ipv4"},
    "README_RU.md": {"private-ipv4"},
    "SECURITY.md": {"telegram-token", "private-ipv4", "print-host"},
    ".github/ISSUE_TEMPLATE/01-bug.yml": {"telegram-token", "private-ipv4"},
    ".github/ISSUE_TEMPLATE/02-installation.yml": {"private-ipv4"},
    # Разведчик печатает пример команды запуска в своей же справке.
    "tools/cc2-probe.py": {"private-ipv4"},
}

# Never scanned. Not exemptions - these simply must not exist in a clean tree,
# and are reported separately by check_forbidden_paths.
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "venv", ".venv",
             "node_modules", ".idea", ".vscode", "dist", "build"}

# Paths that must not exist at all.
FORBIDDEN_NAMES = re.compile(
    r"(^|/)(config\.json|\.env|Журнал\.csv|Journal\.csv|.*\.pyc|"
    r"id_rsa|id_ed25519|.*\.pem|.*\.key)$", re.IGNORECASE)

TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".cmd", ".bat",
                 ".ps1", ".cfg", ".ini", ".toml", ".csv", ".xml", ".config",
                 ".rels", ".model", ".gcode", ".html", ".svg", ".sh"}
ARCHIVE_SUFFIXES = {".zip", ".3mf"}
MAX_TEXT_BYTES = 8 * 1024 * 1024


class Finding(object):
    def __init__(self, path, line, rule, description, fragment):
        self.path = path
        self.line = line
        self.rule = rule
        self.description = description
        self.fragment = fragment

    def __str__(self):
        where = "%s:%s" % (self.path, self.line) if self.line else self.path
        return "  %-52s %-20s %s\n        %s" % (
            where[:52], self.rule, self.description, self.fragment)


def redact(text):
    """A fragment safe to print: enough to find it, not enough to use it."""
    text = text.strip().replace("\n", " ")
    if len(text) <= 8:
        return "%s***" % text[:2]
    return "%s***%s  (%d chars)" % (text[:4], text[-2:], len(text))


def redact_hard(text):
    """For a value we already know is secret, show nothing but its shape.

    The ordinary redaction keeps a head and a tail so a human can find the
    match. That is fine for a pattern hit, where the point is to look at it.
    It is not fine for a fingerprint hit: we already know exactly what that
    string is, the line number alone locates it, and a head-and-tail fragment
    next to the label "LAN address" reconstructs most of the secret in the CI
    log of a public repository.
    """
    return "<%d characters, redacted>" % len(text.strip())


# ------------------------------------------------------------------ scanning

def scan_text(text, path, allowed):
    findings = []
    for rule, pattern, description in PATTERNS:
        if rule in allowed:
            continue
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(Finding(path, line, rule, description,
                                    redact(match.group(0))))
    findings.extend(scan_fingerprints(text, path))
    return findings


def scan_fingerprints(text, path):
    """Hash every short phrase and compare against the known digests."""
    findings = []
    if not FINGERPRINTS:
        return findings
    words = [(m.group(0), m.start()) for m in WORD.finditer(text)]
    for index in range(len(words)):
        for span in range(1, MAX_PHRASE_WORDS + 1):
            if index + span > len(words):
                break
            phrase = " ".join(w for w, _ in words[index:index + span])
            digest = hashlib.sha256(phrase.lower().encode("utf-8")).hexdigest()
            label = FINGERPRINTS.get(digest)
            if label:
                line = text.count("\n", 0, words[index][1]) + 1
                findings.append(Finding(path, line, "known-private-value",
                                        label, redact_hard(phrase)))
    return findings


def scan_file(path, display, allowed_map):
    allowed = allowed_map.get(display.replace(os.sep, "/"), set())
    suffix = os.path.splitext(path)[1].lower()

    if suffix in ARCHIVE_SUFFIXES:
        return scan_archive(path, display, allowed_map)

    if suffix not in TEXT_SUFFIXES:
        return []
    try:
        if os.path.getsize(path) > MAX_TEXT_BYTES:
            return []
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    return scan_text(text, display, allowed)


def archive_exemptions(name, allowed_map):
    """The allow-list entry for a file inside an archive.

    A release archive wraps everything in one top-level directory, so an entry
    is "Centauri-Bot-v1.0.0-windows/docs/installation.md" while the allow-list
    is keyed on the repository path "docs/installation.md". Without stripping
    that prefix the allow-list silently does not apply inside an archive, and
    the release job fails on documentation that the repository scan accepted.
    """
    path = name.replace("\\", "/")
    if path in allowed_map:
        return allowed_map[path]
    _, _, without_prefix = path.partition("/")
    return allowed_map.get(without_prefix, set())


def scan_archive(path, display, allowed_map):
    """A .3mf is a ZIP. So is a release archive. Both get opened and read."""
    findings = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                suffix = os.path.splitext(name)[1].lower()
                if suffix not in TEXT_SUFFIXES:
                    continue
                info = z.getinfo(name)
                if info.file_size > MAX_TEXT_BYTES:
                    continue
                try:
                    text = z.read(name).decode("utf-8", "replace")
                except (OSError, zipfile.BadZipFile):
                    continue
                inner = "%s!%s" % (display, name)
                findings.extend(scan_text(text, inner,
                                          archive_exemptions(name, allowed_map)))
    except (OSError, zipfile.BadZipFile) as e:
        findings.append(Finding(display, None, "unreadable-archive",
                                "cannot be opened: %s" % e, ""))
    return findings


def check_forbidden_paths(root):
    findings = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            full = os.path.join(directory, name)
            display = os.path.relpath(full, root).replace(os.sep, "/")
            if FORBIDDEN_NAMES.search("/" + display):
                findings.append(Finding(display, None, "forbidden-file",
                                        "this file must never be committed", ""))
    for directory, dirnames, _ in os.walk(root):
        for name in list(dirnames):
            if name in ("__pycache__", ".pytest_cache"):
                display = os.path.relpath(os.path.join(directory, name),
                                          root).replace(os.sep, "/")
                findings.append(Finding(display, None, "cache-directory",
                                        "build cache must not be committed", ""))
            if name in SKIP_DIRS:
                dirnames.remove(name)
    return findings


def scan_tree(root):
    findings = list(check_forbidden_paths(root))
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            full = os.path.join(directory, name)
            display = os.path.relpath(full, root).replace(os.sep, "/")
            findings.extend(scan_file(full, display, ALLOWED))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("targets", nargs="*",
                        help="files or directories (default: the repository)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    targets = args.targets or [here]

    findings = []
    for target in targets:
        if os.path.isdir(target):
            findings.extend(scan_tree(target))
        elif os.path.isfile(target):
            # ALLOWED, not {}: scanning a release archive has to accept the same
            # documentation the repository scan accepts, or the release job
            # fails on an example IP address that CI just approved.
            findings.extend(scan_file(target, os.path.basename(target), ALLOWED))
        else:
            print("not found: %s" % target)
            return 2

    if not findings:
        if not args.quiet:
            print("public-safety scan: clean (%s)"
                  % ", ".join(os.path.basename(os.path.abspath(t)) for t in targets))
        return 0

    print("public-safety scan: %d finding(s)\n" % len(findings))
    for finding in findings:
        print(finding)
    print("\nNothing above is printed in full. Open the file at the line shown.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
