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

The third and fourth rules are this repo's side of an asset import: no tracked
file carries source prose in its own script, and nothing under data/ becomes
tracked without a .gitignore line somebody wrote naming it.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from backend.source_manifest import SOURCE_LIBRARIES

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


# No tracked file carries source prose in its own script. Written as CJK and not
# as "non-ASCII" on purpose: 119 of this repo's tracked files already carry
# non-ASCII, all of it typography this project wrote itself - em dashes, curly
# quotes, arrows - plus UI emoji in frontend/src/kinds.js. A non-ASCII rule is
# born failing against half the repo and gets "fixed" with a 119-path allowlist,
# which is a rule that cannot fail with extra steps. The property the guard spec
# is actually about is narrower and holds: no source library's prose, in Han,
# Hiragana, Katakana or Hangul, reaches a file git carries.
CJK_PATTERN = re.compile(
    "["
    "\u4e00-\u9fff"      # CJK Unified Ideographs
    "\u3400-\u4dbf"     # Extension A
    "\uf900-\ufaff"     # Compatibility Ideographs
    "\u3040-\u309f"     # Hiragana
    "\u30a0-\u30ff"     # Katakana
    "\u31f0-\u31ff"    # Katakana Phonetic Extensions
    "\uac00-\ud7af"     # Hangul Syllables
    "\u1100-\u11ff"     # Hangul Jamo
    "\u3130-\u318f"    # Hangul Compatibility Jamo
    "]"
)

# ComfyUI exports a graph with its node titles as the operator's install wrote
# them, and these four came off a Chinese distribution carrying one title each:
# "Empty Latent Image (Base Dimensions)" with two Han characters after it. That
# is a node title inside a saved graph, not source-library prose, and rewriting
# it edits a workflow this app maps by hand. On the allowlist for the same
# reason ALLOWED_IMAGES exists: an exception somebody decided, in the commit
# that needs it.
ALLOWED_CJK = {
    "data/krea2-attention-workflow.json",
    "data/krea2-depth-control-workflow.json",
    "data/krea2-kgreference-workflow.json",
    "data/krea2-kgwardrobe-workflow.json",
}


def cjk_offenders(files) -> list[str]:
    """The paths that carry CJK and are not on the allowlist."""
    offenders = []
    for rel in files:
        if rel in ALLOWED_CJK or Path(rel).suffix.lower() in SKIP_SUFFIXES:
            continue
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        match = CJK_PATTERN.search(text)
        if match:
            offenders.append(f"{rel}:{text[:match.start()].count(chr(10)) + 1}")
    return offenders


def test_no_cjk_in_tracked_files():
    """No source library's prose reaches a file git carries."""
    offenders = cjk_offenders(tracked_files())
    assert not offenders, (
        "source prose in its own script reached tracked files: "
        + ", ".join(offenders)
        + ". Imported prose belongs in the ignored part of data/, and its "
          "translation in the untracked map. If one of these is a node title or "
          "another asset this app did not import, add it to ALLOWED_CJK in the "
          "same commit.")


def test_the_cjk_rule_actually_bites(tmp_path):
    """A guard that cannot fail is decoration. Every range is proved separately."""
    for label, sample in (
        ("han", "\u5ba2\u5385"),
        ("hiragana", "\u3042\u3044"),
        ("katakana", "\u30ab\u30ca"),
        ("hangul", "\ud55c\uae00"),
    ):
        assert CJK_PATTERN.search(sample), label

    # A room text planted in a seed is caught wherever in the file it sits
    planted = tmp_path / "planted-rooms-seed.json"
    planted.write_text('[{"key": "room-01", "label": "\u5ba2\u5385"}]', encoding="utf-8")
    assert CJK_PATTERN.search(planted.read_text(encoding="utf-8"))

    # The allowlist is what keeps the rule usable when a real asset arrives
    assert cjk_offenders(sorted(ALLOWED_CJK)) == []

    # The typography this repo writes on purpose is not CJK and never fires
    for ours in ("a \u2014 b", "\u201cquoted\u201d", "caf\u00e9", "1 \u2192 2"):
        assert not CJK_PATTERN.search(ours), ours


# Everything under data/ is ignored by default and reaches the repo only through
# a "!" line somebody wrote. That is the rule that keeps an imported room's text
# out: an import writes its seed into data/, and the file stays invisible to git
# unless a person adds the exception. Git keeps tracking a file that was added
# before the ignore rule existed, though, so the two lists drift apart silently -
# three seeds were tracked with no line naming them when this was written.
def data_allowlist_drift() -> tuple[list[str], list[str]]:
    """(tracked under data/ with no "!" line, "!" lines naming nothing tracked)."""
    out = subprocess.run(["git", "ls-files", "data/"], cwd=ROOT,
                         capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git repository")
    tracked = {f for f in out.stdout.splitlines() if f}
    named = {line[1:] for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
             if line.startswith("!data/")}
    return sorted(tracked - named), sorted(named - tracked)


def test_tracked_data_files_are_named_by_gitignore():
    """An imported seed cannot become tracked without somebody writing the line."""
    unnamed, stale = data_allowlist_drift()
    assert not unnamed, (
        "tracked under data/ with no .gitignore exception naming them: "
        + ", ".join(unnamed)
        + ". This is how imported prose gets committed by accident. Add the "
          "'!' line if the file belongs in the repo, or git rm --cached it.")
    assert not stale, (
        "a .gitignore exception names a file git does not track: " + ", ".join(stale))


# The rule above is "nothing tracked under data/ is unnamed". This is the other
# direction, and 6.1 is where it is stated: nothing an import WRITES may become
# tracked, and nothing an import writes may be read at build time. The two are
# one guarantee - a fresh clone with no import run has to build - and neither
# half is visible from the other's assertion.
SEED_IMPORT = re.compile(r"""from\s+['"]([^'"]*data/[^'"]+\.json)['"]""")


def _frontend_build_time_seeds() -> list[str]:
    """Every data/ seed the frontend reads at build time, repo-relative."""
    found: list[str] = []
    for src in sorted((ROOT / "frontend" / "src").rglob("*.js*")):
        for match in SEED_IMPORT.finditer(src.read_text(encoding="utf-8")):
            resolved = (src.parent / match.group(1)).resolve()
            found.append(resolved.relative_to(ROOT).as_posix())
    return sorted(set(found))


def test_an_import_destination_is_neither_tracked_nor_bundled_into_the_build():
    """6.1: the operator's licence decision keeps imported prose out of git, and
    the frontend has to build on a clone where it was never imported.

    Both halves fail silently in the direction that matters. A destination that
    became tracked publishes 428 rooms of somebody else's wording and nothing
    goes red; a destination read by `import ... from '../../data/...json'`
    builds fine on the machine that ran the import and breaks every fresh
    clone, which is the failure `tests/test_catalogue_seed.py` was written for
    in the first place.

    Read off the declaration rather than off a filename pattern: the manifest is
    where a destination is named, so a library added there is covered here the
    day it is added and not the day somebody remembers this test.
    """
    destinations = sorted({
        dest
        for declaration in SOURCE_LIBRARIES.values()
        for dest in declaration.get("destinations", ())
    })
    assert destinations, "the manifest declares no destination; this asserts nothing"

    out = subprocess.run(["git", "ls-files", "data/"], cwd=ROOT,
                         capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git repository")
    tracked = {f for f in out.stdout.splitlines() if f}

    for dest in destinations:
        path = f"data/{dest}"
        assert path not in tracked, (
            f"{path} is tracked: an import would commit source prose. "
            "git rm --cached it and drop its .gitignore exception.")
        # Ignored whether or not the file exists yet, which is the state a
        # checkout is in before anybody imports.
        ignored = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
        assert ignored.returncode == 0, f"{path} is not ignored by .gitignore"

    # The nine rooms this project wrote, directed's look and the registers stay
    # build-time imports and stay tracked. What may not appear here is an import
    # destination.
    build_time = _frontend_build_time_seeds()
    assert build_time, "no build-time seed import found; the scan is broken"
    for seed in build_time:
        assert seed in tracked, (
            f"{seed} is imported at build time and is not tracked: a fresh "
            "clone does not build")
