"""Tests for translation map definition, validation, and path resolution.

Asserts that:
- A map entry whose translation is empty is rejected with ValueError.
- A map entry whose translation still contains non-English characters is rejected with ValueError.
- Valid translation entries with English translations and fields are accepted.
- Missing or invalid fields are rejected.
- Source string mismatches are rejected.
- Map-level validation succeeds on valid maps and rejects invalid maps.
- Resolving translation map path requires source_dir and relative_path arguments.
- Loading and saving round-trips correctly and validates JSON.
- Files are pure ASCII, contain no illegal control bytes, and have no trailing whitespace.
- The test suite reaches no source library.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from backend.translation_map import (
    contains_non_english,
    load_translation_map,
    make_translation_entry,
    resolve_translation_map_path,
    save_translation_map,
    validate_translation_entry,
    validate_translation_map,
)

ROOT = Path(__file__).resolve().parents[1]
LEGAL_CONTROLS = {0x09, 0x0A, 0x0D}  # tab, newline, carriage return


def test_rejects_empty_translation():
    """Verify that a translation map entry with an empty translation is rejected."""
    for empty_val in ("", "   ", "\t\t", None):
        entry = {
            "source": "\u5bbf\u820d",
            "translation": empty_val,
            "fields": ["label"],
        }
        with pytest.raises(ValueError, match="is empty"):
            validate_translation_entry(entry)


def test_rejects_translation_still_containing_non_english_characters():
    """Verify that a map entry whose translation still contains non-English characters is rejected."""
    # Chinese characters in translation
    zh_entry = {
        "source": "\u5bbf\u820d",
        "translation": "\u5bbf\u820d",
        "fields": ["label"],
    }
    with pytest.raises(ValueError, match="contains non-English characters"):
        validate_translation_entry(zh_entry)

    # Mixed English and Chinese
    mixed_entry = {
        "source": "\u5bbf\u820d",
        "translation": "cozy \u5bbf\u820d room",
        "fields": ["label"],
    }
    with pytest.raises(ValueError, match="contains non-English characters"):
        validate_translation_entry(mixed_entry)

    # Cyrillic script
    cyrillic_entry = {
        "source": "\u5bbf\u820d",
        "translation": "\u043a\u043e\u043c\u043d\u0430\u0442\u0430",
        "fields": ["label"],
    }
    with pytest.raises(ValueError, match="contains non-English characters"):
        validate_translation_entry(cyrillic_entry)

    # Japanese Katakana
    katakana_entry = {
        "source": "\u5bbf\u820d",
        "translation": "\u30b9\u30bf\u30b8\u30aa",
        "fields": ["label"],
    }
    with pytest.raises(ValueError, match="contains non-English characters"):
        validate_translation_entry(katakana_entry)

    # Fullwidth punctuation
    fullwidth_entry = {
        "source": "\u5bbf\u820d",
        "translation": "studio \uff08interior\uff09",
        "fields": ["label"],
    }
    with pytest.raises(ValueError, match="contains non-English characters"):
        validate_translation_entry(fullwidth_entry)


def test_accepts_valid_translation_entry():
    """Verify that a valid translation entry with English translation and fields is accepted."""
    entry = {
        "source": "\u5bbf\u820d",
        "translation": "dormitory",
        "fields": ["label", "notes"],
    }
    validated = validate_translation_entry(entry, source_key="\u5bbf\u820d")
    assert validated["source"] == "\u5bbf\u820d"
    assert validated["translation"] == "dormitory"
    assert validated["fields"] == ["label", "notes"]

    # Typographical punctuation in English text is accepted
    typo_entry = {
        "source": "\u753b\u5ba4",
        "translation": "artist\u2019s studio \u2014 high ceiling",
        "fields": ["notes"],
    }
    val_typo = validate_translation_entry(typo_entry)
    assert val_typo["translation"] == "artist\u2019s studio \u2014 high ceiling"

    # Derived source from source_key, 'english' alias, and single string field
    alias_entry = {
        "english": "dormitory",
        "fields": "label",
    }
    val_alias = validate_translation_entry(alias_entry, source_key="\u5bbf\u820d")
    assert val_alias["source"] == "\u5bbf\u820d"
    assert val_alias["translation"] == "dormitory"
    assert val_alias["fields"] == ["label"]

    # make_translation_entry helper with single string and list fields
    helper_res = make_translation_entry("\u6559\u5ba4", "classroom", "label")
    assert helper_res["source"] == "\u6559\u5ba4"
    assert helper_res["translation"] == "classroom"
    assert helper_res["fields"] == ["label"]

    helper_list = make_translation_entry("\u6559\u5ba4", "classroom", ["label", "notes"])
    assert helper_list["fields"] == ["label", "notes"]

    # Empty text, Latin-1 loanword characters, and script outside NON_ENGLISH_PATTERN
    assert not contains_non_english("")
    assert not contains_non_english("canteen / caf\u00e9")
    assert contains_non_english("\u0531")  # Armenian capital A

    # Non-dict entry raises TypeError
    with pytest.raises(TypeError, match="must be a dict"):
        validate_translation_entry("not-a-dict")


def test_rejects_missing_or_invalid_fields():
    """Verify that an entry missing 'fields' or carrying empty/invalid fields is rejected."""
    base = {"source": "\u5bbf\u820d", "translation": "dormitory"}
    # Missing fields
    with pytest.raises(ValueError, match="missing 'fields'"):
        validate_translation_entry(dict(base))
    # Empty fields list
    with pytest.raises(ValueError, match="must cover at least one field"):
        validate_translation_entry({**base, "fields": []})
    # Invalid field item (empty string)
    with pytest.raises(ValueError, match="Invalid field name"):
        validate_translation_entry({**base, "fields": [""]})
    # Invalid fields type
    with pytest.raises(ValueError, match="must be a collection of strings"):
        validate_translation_entry({**base, "fields": 123})


def test_rejects_source_mismatch_or_empty_source():
    """Verify that empty source string or mismatch with map key is rejected."""
    # Empty source
    with pytest.raises(ValueError, match="missing source string"):
        validate_translation_entry({"source": "", "translation": "dormitory", "fields": ["label"]})
    # Mismatch with map key
    with pytest.raises(ValueError, match="does not match map key"):
        validate_translation_entry(
            {"source": "\u5bbf\u820d", "translation": "dormitory", "fields": ["label"]},
            source_key="\u6559\u5ba4",
        )


def test_validate_translation_map_entire_map():
    """Verify map-level validation over multiple entries."""
    valid_map = {
        "\u5bbf\u820d": {
            "source": "\u5bbf\u820d",
            "translation": "dormitory",
            "fields": ["label"],
        },
        "\u6559\u5ba4": {
            "source": "\u6559\u5ba4",
            "translation": "classroom",
            "fields": ["label", "notes"],
        },
    }
    res = validate_translation_map(valid_map)
    assert len(res) == 2
    assert res["\u5bbf\u820d"]["translation"] == "dormitory"

    # Non-dict map raises TypeError
    with pytest.raises(TypeError, match="must be a dict"):
        validate_translation_map(["not", "a", "dict"])

    # Non-string key raises ValueError
    with pytest.raises(ValueError, match="keys must be non-empty strings"):
        validate_translation_map({"": {"source": "", "translation": "x", "fields": ["label"]}})

    # Map containing invalid entry raises ValueError
    invalid_map = {
        "\u5bbf\u820d": {
            "source": "\u5bbf\u820d",
            "translation": "",
            "fields": ["label"],
        },
    }
    with pytest.raises(ValueError, match="is empty"):
        validate_translation_map(invalid_map)


def test_resolve_translation_map_path_requires_arguments(tmp_path):
    """Verify that resolving the map path requires source_dir and relative_path."""
    with pytest.raises(ValueError, match="source_dir is required"):
        resolve_translation_map_path("", "map.json")
    with pytest.raises(ValueError, match="relative_path is required"):
        resolve_translation_map_path("some/source/dir", "")

    # Relative path resolved against source directory
    res_dir = resolve_translation_map_path("sources", "translations.json")
    assert res_dir == Path("sources") / "translations.json"

    # Relative path resolved beside a fictitious source file path with suffix
    res_file = resolve_translation_map_path("sources/amateurs.json", "translations.json")
    assert res_file == Path("sources") / "translations.json"

    # Relative path resolved beside a real existing file on disk
    real_source_file = tmp_path / "actual_source.json"
    real_source_file.write_text("{}", encoding="utf-8")
    assert resolve_translation_map_path(real_source_file, "map.json") == tmp_path / "map.json"

    # Absolute path preserved
    abs_path = Path("/absolute/path/to/translations.json")
    assert resolve_translation_map_path("sources", abs_path) == abs_path


def test_load_and_save_translation_map_roundtrip(tmp_path):
    """Verify saving and loading translation map to/from untracked path."""
    source_dir = tmp_path / "operator_sources"
    source_dir.mkdir()
    map_data = {
        "\u5bbf\u820d": {
            "source": "\u5bbf\u820d",
            "translation": "dormitory",
            "fields": ["label"],
        },
    }
    saved_path = save_translation_map(
        map_data,
        source_dir=source_dir,
        relative_path="translation_map.json",
    )
    assert saved_path.is_file()

    # Verify written file is strictly ASCII
    raw_bytes = saved_path.read_bytes()
    assert all(b <= 0x7F for b in raw_bytes)
    # Escaped sequence \u5bbf appears in raw JSON
    escape_str = chr(92) + "u5bbf"
    assert escape_str.encode("ascii") in raw_bytes

    # Load map back
    loaded = load_translation_map(
        source_dir=source_dir,
        relative_path="translation_map.json",
    )
    assert loaded["\u5bbf\u820d"]["translation"] == "dormitory"

    # Saving with direct path
    direct_path = tmp_path / "direct.json"
    save_translation_map(map_data, path=direct_path)
    assert direct_path.is_file()
    assert load_translation_map(direct_path)["\u5bbf\u820d"]["translation"] == "dormitory"

    # Loading requires path or source_dir + relative_path
    with pytest.raises(ValueError, match="requires either 'path' or both"):
        load_translation_map()

    # Saving requires path or source_dir + relative_path
    with pytest.raises(ValueError, match="requires either 'path' or both"):
        save_translation_map(map_data)

    # Missing file raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        load_translation_map(tmp_path / "non_existent.json")

    # Invalid JSON raises ValueError
    bad_json = tmp_path / "corrupt.json"
    bad_json.write_text("{not json}", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_translation_map(bad_json)

    # Rejects loading file with invalid translation (contains non-English)
    bad_file = tmp_path / "bad_map.json"
    bad_file.write_text(
        json.dumps({"\u5bbf\u820d": {"source": "\u5bbf\u820d", "translation": "\u5bbf\u820d", "fields": ["label"]}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="contains non-English characters"):
        load_translation_map(bad_file)


def test_translation_map_files_are_pure_ascii_and_contain_no_control_bytes():
    """Verify translation map files are pure ASCII with no illegal control bytes or trailing whitespace."""
    targets = [
        ROOT / "backend" / "translation_map.py",
        ROOT / "tests" / "test_translation_map.py",
    ]
    for path in targets:
        assert path.exists(), f"missing file: {path}"
        raw_bytes = path.read_bytes()
        assert len(raw_bytes) > 0
        for idx, b in enumerate(raw_bytes):
            assert b <= 0x7F, f"{path.name}:{idx}: byte {hex(b)} exceeds ASCII range"
            if b < 0x20:
                assert b in LEGAL_CONTROLS, f"{path.name}:{idx}: illegal control byte {hex(b)}"
        assert 0x08 not in raw_bytes, f"{path.name}: contains literal backspace byte"

        lines = path.read_text(encoding="utf-8").splitlines()
        for line_num, line in enumerate(lines, 1):
            assert line == line.rstrip(), f"{path.name}:{line_num}: has trailing whitespace"


def test_translation_map_reaches_no_source_library():
    """Verify translation map module and its tests do not import config or file-search tools."""
    allowed = {"__future__", "ast", "json", "pathlib", "pytest", "re", "typing", "backend"}
    for filename in ("test_translation_map.py",):
        tree = ast.parse((ROOT / "tests" / filename).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        assert imported <= allowed, f"{filename} imports {sorted(imported - allowed)}"
