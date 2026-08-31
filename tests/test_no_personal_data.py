"""Guard for a public repo: no personal data in anything git tracks.

Writing the forbidden values into this file would publish them, so the rules are
patterns, not a blocklist: user-home paths, emails and API tokens. Every tracked
file is scanned, including docs and CI config — the leak this catches in practice
is a real path pasted into an example.

The second rule is not a pattern at all: no image may be tracked. A photograph
of a person is not a path, an email or a token, and the text scan below skips
binaries by suffix — so six of them sat tracked under `data/depth-sources/`
through a `.gitignore` exception, green the whole time, until a push to this
public repo was about to publish them (2026-08-31, history rewritten to drop
them). Nothing in this repo needs a checked-in image, so the rule is "none"
rather than a pattern over their contents.
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

# No image is tracked, full stop. `.svg` is in here with the binaries: it is text,
# so the scan above reads it, but a photograph base64-encoded into one is invisible
# to every pattern. If the app ever needs a real asset — a logo, a UI icon — add its
# path to ALLOWED_IMAGES in the same commit that adds the file, so the exception is
# a decision somebody made rather than a suffix nobody checked.
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff",
                  ".avif", ".heic", ".heif", ".ico", ".svg"}
ALLOWED_IMAGES: set[str] = set()


def image_offenders(files) -> list[str]:
    """The tracked paths that are images and are not on the allowlist."""
    return [f for f in files
            if Path(f).suffix.lower() in IMAGE_SUFFIXES and f not in ALLOWED_IMAGES]


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


def test_no_images_are_tracked():
    """The rule the text scan cannot enforce: a person in a JPEG is not a pattern."""
    offenders = image_offenders(tracked_files())
    assert not offenders, (
        "images are tracked in a public repo:\n" + "\n".join(offenders)
        + "\n\nSource frames and generated photographs belong in the ignored part "
          "of data/. If one of these is a real app asset, add it to ALLOWED_IMAGES.")


def test_the_image_rule_actually_bites():
    """Same reason the scanner has its own test: a guard that cannot fail is decoration."""
    assert image_offenders(["data/depth-sources/profile_90.jpg"]) == \
        ["data/depth-sources/profile_90.jpg"]
    # The suffix match is case-blind, or one screenshot walks straight past it.
    assert image_offenders(["docs/shot.PNG"]) == ["docs/shot.PNG"]
    assert image_offenders(["backend/main.py", "data/catalogue-seed.json"]) == []
    # The allowlist is what keeps this rule usable when a real asset arrives.
    assert image_offenders(list(ALLOWED_IMAGES)) == []


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
