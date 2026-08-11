"""Guard for a public repo: no personal data in anything git tracks.

Writing the forbidden values into this file would publish them, so the rules are
patterns, not a blocklist: user-home paths, emails and API tokens. Every tracked
file is scanned, including docs and CI config — the leak this catches in practice
is a real path pasted into an example.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

PATTERNS = {
    # A Windows user folder: the drive letter alone is fine (docs say D:\ComfyUI),
    # what leaks is the account name that follows Users\.
    "windows user path": re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!<)[A-Za-z0-9._-]+", re.I),
    "unix home path": re.compile(r"/(?:home|Users)/(?!<)[A-Za-z0-9._-]+"),
    "email address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "api token": re.compile(r"\b(?:sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,})\b"),
}

# The scanner cannot flag its own regexes, and a placeholder is the point.
ALLOWED = {
    "tests/test_no_personal_data.py",
    "LICENSE",          # the MIT text mentions no paths, but keep it out of churn
}

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".ico", ".safetensors", ".db"}


def tracked_files() -> list[str]:
    # `--others --exclude-standard` adds files that are not staged yet: a leak in
    # a brand-new file must fail BEFORE it is committed, not after.
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git repository")
    return [f for f in out.stdout.splitlines() if f]


def test_no_personal_data_in_tracked_files():
    offenders = []
    for rel in tracked_files():
        if rel in ALLOWED or Path(rel).suffix.lower() in SKIP_SUFFIXES:
            continue
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                line = text[:match.start()].count("\n") + 1
                offenders.append(f"{rel}:{line}: {label}: {match.group(0)}")

    assert not offenders, "personal data in tracked files:\n" + "\n".join(offenders)


def test_the_scanner_actually_catches_things(tmp_path):
    """A guard that cannot fail is decoration; prove each pattern bites."""
    samples = {
        "windows user path": r"C:\Users\someone\ComfyUI",
        "unix home path": "/home/someone/ComfyUI",
        "email address": "someone@example.com",
        "api token": "ghp_" + "a" * 24,
    }
    for label, sample in samples.items():
        assert PATTERNS[label].search(sample), label
    # Placeholders stay legal, or the docs cannot show a path at all.
    assert not PATTERNS["windows user path"].search(r"C:\Users\<you>\ComfyUI")
    assert not PATTERNS["windows user path"].search(r"D:\ComfyUI\output")
