"""Translation map for asset imports.

Defines the translation map keyed by source string at an untracked path
beside the source material. Every map entry records the source string,
its authored English translation, and the fields it covers.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Iterable

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
    if translation is None and "english" in entry:
        translation = entry.get("english")
    if translation is None or not isinstance(translation, str) or not translation.strip():
        raise ValueError(f"Translation for source string {source!r} is empty")
    if contains_non_english(translation):
        raise ValueError(
            f"Translation for source string {source!r} contains non-English characters: {translation!r}"
        )

    fields_raw = entry.get("fields")
    if fields_raw is None:
        raise ValueError(f"Translation entry for {source!r} missing 'fields'")
    if isinstance(fields_raw, str):
        fields_list = [fields_raw]
    elif hasattr(fields_raw, "__iter__"):
        fields_list = list(fields_raw)
    else:
        raise ValueError(
            f"Translation entry 'fields' must be a collection of strings, got {type(fields_raw).__name__}"
        )
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


def make_translation_entry(
    source: str,
    translation: str,
    fields: Iterable[str] | str,
) -> dict[str, Any]:
    """Helper to construct and validate a single translation entry."""
    entry = {
        "source": source,
        "translation": translation,
        "fields": fields if not isinstance(fields, str) else [fields],
    }
    return validate_translation_entry(entry, source_key=source)


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

    Both source_dir and relative_path are required arguments. The source
    directory belongs to the operator and is never hardcoded, guessed, or
    given a default.
    """
    if source_dir is None or not str(source_dir).strip():
        raise ValueError("source_dir is required and cannot be empty")
    if relative_path is None or not str(relative_path).strip():
        raise ValueError("relative_path is required and cannot be empty")

    base = Path(source_dir)
    rel = Path(relative_path)
    if rel.is_absolute():
        return rel
    if base.is_file() or (base.suffix and not base.is_dir()):
        return base.parent / rel
    return base / rel


def load_translation_map(
    path: Path | str | None = None,
    *,
    source_dir: Path | str | None = None,
    relative_path: Path | str | None = None,
) -> dict[str, dict[str, Any]]:
    """Load and validate the translation map from an untracked JSON file.

    Requires either 'path' or both 'source_dir' and 'relative_path'.
    Never guesses a default path or source directory.
    """
    if path is not None:
        target_path = Path(path)
    elif source_dir is not None and relative_path is not None:
        target_path = resolve_translation_map_path(source_dir, relative_path)
    else:
        raise ValueError(
            "load_translation_map requires either 'path' or both 'source_dir' and 'relative_path'"
        )

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
    path: Path | str | None = None,
    *,
    source_dir: Path | str | None = None,
    relative_path: Path | str | None = None,
) -> Path:
    """Validate and write the translation map to an untracked JSON file.

    Non-ASCII characters in source strings are escaped as \\uXXXX to ensure
    the file contains only ASCII characters.
    """
    if path is not None:
        target_path = Path(path)
    elif source_dir is not None and relative_path is not None:
        target_path = resolve_translation_map_path(source_dir, relative_path)
    else:
        raise ValueError(
            "save_translation_map requires either 'path' or both 'source_dir' and 'relative_path'"
        )

    validated = validate_translation_map(translation_map)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(validated, indent=2, ensure_ascii=True)
    target_path.write_text(serialized, encoding="utf-8")
    return target_path
