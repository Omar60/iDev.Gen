"""Translation map for asset imports.

Defines the translation map keyed by source string at an untracked path
beside the source material. Every map entry records the source string,
its authored English translation, and the fields it covers.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# Regex matching CJK ideographs, syllabaries, symbols, fullwidth forms,
# and non-Latin scripts.
NON_ENGLISH_PATTERN: re.Pattern = re.compile(
    r"["
    r"\u4e00-\u9fff"  # CJK Unified Ideographs
    r"\u3400-\u4dbf"  # CJK Unified Ideographs Extension A
    r"\uf900-\ufaff"  # CJK Compatibility Ideographs
    r"\u3040-\u309f"  # Hiragana
    r"\u30a0-\u30ff"  # Katakana
    r"\u31f0-\u31ff"  # Katakana Phonetic Extensions
    r"\uac00-\ud7af"  # Hangul Syllables
    r"\u1100-\u11ff"  # Hangul Jamo
    r"\u3130-\u318f"  # Hangul Compatibility Jamo
    r"\u3000-\u303f"  # CJK Symbols and Punctuation
    r"\uff00-\uffef"  # Halfwidth and Fullwidth Forms
    r"\u2e80-\u2fd5"  # CJK Radicals / Kangxi Radicals
    r"\u3100-\u312f"  # Bopomofo
    r"\u0400-\u052f"  # Cyrillic
    r"\u0600-\u06ff"  # Arabic
    r"\u0750-\u077f"  # Arabic Supplement
    r"\u08a0-\u08ff"  # Arabic Extended-A
    r"\u0590-\u05ff"  # Hebrew
    r"\u0e00-\u0e7f"  # Thai
    r"\u0900-\u097f"  # Devanagari
    r"\u0370-\u03ff"  # Greek
    r"]"
)

# Allowed English typographical punctuation characters (beyond standard ASCII).
# This repository writes 2052 em dashes and its own curly quotes on purpose, so
# a rule that refused them would refuse the house style along with the source.
ALLOWED_TYPOGRAPHY: frozenset[str] = frozenset(
    (
        "\u2018",  # left single quotation mark
        "\u2019",  # right single quotation mark
        "\u201c",  # left double quotation mark
        "\u201d",  # right double quotation mark
        "\u2013",  # en dash
        "\u2014",  # em dash
        "\u2026",  # horizontal ellipsis
        "\u2022",  # bullet
    )
)


def contains_non_english(text: str) -> bool:
    """Return True if text contains characters outside English.

    English text uses ASCII printable characters, standard whitespace,
    common typographical punctuation (curly quotes, dashes, ellipsis),
    and Latin loanword accented letters. Non-Latin scripts (CJK, Cyrillic,
    Arabic, Thai, etc.) and fullwidth forms return True.
    """
    if not text:
        return False
    if NON_ENGLISH_PATTERN.search(text):
        return True
    for ch in text:
        cp = ord(ch)
        if cp <= 0x7F:
            continue
        if ch in ALLOWED_TYPOGRAPHY:
            continue
        if 0x00C0 <= cp <= 0x00FF:
            # Latin-1 supplement letters (e.g. cafe, resume)
            continue
        return True
    return False


def validate_translation_entry(
    entry: dict[str, Any],
    source_key: str | None = None,
) -> dict[str, Any]:
    """Validate and normalize a translation map entry.

    Each entry must be a dictionary specifying:
    - 'source': the non-English source string (matching source_key if provided)
    - 'translation': the authored English translation (cannot be empty or contain non-English characters)
    - 'fields': collection of non-empty strings identifying the entry fields it covers

    Raises TypeError if entry is not a dict.
    Raises ValueError if translation is empty, contains non-English characters,
    source is missing/empty, source does not match source_key, or fields is missing/empty.
    """
    if not isinstance(entry, dict):
        raise TypeError(f"Translation entry must be a dict, got {type(entry).__name__}")

    source = entry.get("source")
    if source is None and source_key is not None:
        source = source_key
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"Translation entry missing source string: {entry!r}")
    if source_key is not None and source != source_key:
        raise ValueError(
            f"Entry source string {source!r} does not match map key {source_key!r}"
        )

    translation = entry.get("translation")
    if translation is None or not isinstance(translation, str) or not translation.strip():
        raise ValueError(f"Translation for source string {source!r} is empty")
    if contains_non_english(translation):
        raise ValueError(
            f"Translation for source string {source!r} contains non-English characters: {translation!r}"
        )

    fields_raw = entry.get("fields")
    if fields_raw is None:
        raise ValueError(f"Translation entry for {source!r} missing 'fields'")
    # A bare string is refused rather than wrapped: "label" and ["label"]
    # reaching the same entry is one field written two ways.
    if isinstance(fields_raw, str) or not hasattr(fields_raw, "__iter__"):
        raise ValueError(
            f"Translation entry 'fields' must be a collection of strings, got {type(fields_raw).__name__}"
        )
    fields_list = list(fields_raw)
    if not fields_list:
        raise ValueError(f"Translation entry for {source!r} must cover at least one field")
    for f in fields_list:
        if not isinstance(f, str) or not f.strip():
            raise ValueError(f"Invalid field name in translation entry for {source!r}: {f!r}")

    normalized_fields = sorted(set(f.strip() for f in fields_list))
    return {
        "source": source,
        "translation": translation.strip(),
        "fields": normalized_fields,
    }


def validate_translation_map(
    map_data: Any,
) -> dict[str, dict[str, Any]]:
    """Validate an entire translation map keyed by source string.

    Ensures map_data is a dict where every key is a non-empty string and
    every value is a valid translation entry whose source matches the key.
    """
    if not isinstance(map_data, dict):
        raise TypeError(f"Translation map must be a dict, got {type(map_data).__name__}")

    normalized: dict[str, dict[str, Any]] = {}
    for key, entry in map_data.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"Translation map keys must be non-empty strings, got {key!r}")
        normalized[key] = validate_translation_entry(entry, source_key=key)
    return normalized


def resolve_translation_map_path(
    source_dir: Path | str,
    relative_path: Path | str,
) -> Path:
    """Resolve the path to the translation map beside the source material.

    Both arguments are required. The source directory belongs to the operator
    and is never hardcoded, guessed, or given a default.

    `source_dir` has to BE a directory, and is asked of the filesystem rather
    than of the name. Reading "there is a dot in it, so it is a file, so the map
    goes in the parent" put the map one level ABOVE a source directory called
    anything like `AmazingDraw v1.2` - outside the material it is supposed to
    sit beside, and silently, since both paths exist. An absolute
    `relative_path` is refused for the same reason: it is beside nothing.
    """
    if source_dir is None or not str(source_dir).strip():
        raise ValueError("source_dir is required and cannot be empty")
    if relative_path is None or not str(relative_path).strip():
        raise ValueError("relative_path is required and cannot be empty")

    base = Path(source_dir)
    if not base.is_dir():
        raise NotADirectoryError(
            f"source_dir must be an existing directory, got {base}"
        )
    rel = Path(relative_path)
    if rel.is_absolute():
        raise ValueError(
            f"relative_path must be relative to the source directory, got {rel}"
        )
    return base / rel


def _is_tracked_location(path: Path) -> bool:
    """True when `path` sits inside this repository and git does not ignore it.

    The map carries the source libraries' own prose, which the operator's
    licence decision keeps out of this public repo. Asked of `git check-ignore`
    so the answer comes from the same .gitignore a commit would consult, rather
    than from a second list kept here that would drift from it.
    """
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError:
        return False  # outside the repository entirely: nothing here tracks it
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", str(resolved)],
        cwd=ROOT, capture_output=True, text=True,
    )
    if ignored.returncode == 0:
        return False  # git ignores it
    if ignored.returncode != 1:
        return False  # not a git repository, or git is unavailable
    return True


def load_translation_map(path: Path | str) -> dict[str, dict[str, Any]]:
    """Load and validate the translation map from an untracked JSON file.

    Takes a path `resolve_translation_map_path` built. One way in, so the
    required source directory cannot be walked around by passing a path instead.
    """
    target_path = Path(path)
    if not target_path.is_file():
        raise FileNotFoundError(f"Translation map file not found: {target_path}")

    raw_text = target_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in translation map {target_path}: {exc}") from exc

    return validate_translation_map(data)


def save_translation_map(
    translation_map: dict[str, Any],
    path: Path | str,
) -> Path:
    """Validate and write the translation map to an untracked JSON file.

    Non-ASCII characters in source strings are escaped as \\uXXXX so the file
    contains only ASCII. Refuses a destination this repository would track:
    the map carries source prose, and "untracked" has to be something the code
    checks rather than something a docstring says.
    """
    target_path = Path(path)
    if _is_tracked_location(target_path):
        raise ValueError(
            f"Translation map would be tracked by git at {target_path}: it carries "
            f"source prose and must live at an untracked path beside the source material"
        )

    validated = validate_translation_map(translation_map)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(validated, indent=2, ensure_ascii=True)
    target_path.write_text(serialized, encoding="utf-8")
    return target_path
