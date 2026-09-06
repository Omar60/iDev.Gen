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

    # A source string omitted from the entry is taken from the map key
    keyed_entry = {"translation": "dormitory", "fields": ["label"]}
    val_keyed = validate_translation_entry(keyed_entry, source_key="\u5bbf\u820d")
    assert val_keyed["source"] == "\u5bbf\u820d"

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
    # A bare string is refused rather than wrapped: "label" and ["label"]
    # reaching the same entry is one field written two ways.
    with pytest.raises(ValueError, match="must be a collection of strings"):
        validate_translation_entry({**base, "fields": "label"})


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
    source_dir = tmp_path / "sources"
    source_dir.mkdir()

    with pytest.raises(ValueError, match="source_dir is required"):
        resolve_translation_map_path("", "map.json")
    with pytest.raises(ValueError, match="relative_path is required"):
        resolve_translation_map_path(source_dir, "")

    # Relative path resolved against the source directory
    assert resolve_translation_map_path(source_dir, "translations.json") == (
        source_dir / "translations.json"
    )


def test_resolve_translation_map_path_keeps_a_dotted_directory_beside_itself(tmp_path):
    """A source directory whose NAME carries a dot still gets the map inside it.

    Deciding file-or-directory from the name put the map in the PARENT of any
    source directory called `AmazingDraw v1.2` - out of the material it is meant
    to sit beside, and silently, since both paths exist and neither is wrong to
    look at. The filesystem is asked instead.
    """
    dotted = tmp_path / "AmazingDraw v1.2"
    dotted.mkdir()
    assert resolve_translation_map_path(dotted, "map.json") == dotted / "map.json"


def test_resolve_translation_map_path_refuses_a_non_directory_and_an_absolute_path(tmp_path):
    """A source_dir that is not a directory, and an absolute relative_path, are refused."""
    source_dir = tmp_path / "sources"
    source_dir.mkdir()

    a_file = tmp_path / "amateurs.json"
    a_file.write_text("{}", encoding="utf-8")
    with pytest.raises(NotADirectoryError, match="must be an existing directory"):
        resolve_translation_map_path(a_file, "map.json")
    with pytest.raises(NotADirectoryError, match="must be an existing directory"):
        resolve_translation_map_path(tmp_path / "no-such-dir", "map.json")

    # An absolute destination is beside nothing, so it is not a resolution.
    with pytest.raises(ValueError, match="must be relative to the source directory"):
        resolve_translation_map_path(source_dir, tmp_path / "elsewhere.json")


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
    map_path = resolve_translation_map_path(source_dir, "translation_map.json")
    saved_path = save_translation_map(map_data, map_path)
    assert saved_path.is_file()

    # Verify written file is strictly ASCII
    raw_bytes = saved_path.read_bytes()
    assert all(b <= 0x7F for b in raw_bytes)
    # Escaped sequence \u5bbf appears in raw JSON
    escape_str = chr(92) + "u5bbf"
    assert escape_str.encode("ascii") in raw_bytes

    # Load map back
    loaded = load_translation_map(map_path)
    assert loaded["\u5bbf\u820d"]["translation"] == "dormitory"

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


def test_saving_refuses_a_destination_this_repository_would_track(tmp_path):
    """3.1 / 2.1: the map holds source prose, so a tracked destination is refused.

    "Untracked" was a word in a docstring until this ran: nothing stopped a
    caller handing a repo path and committing 428 rooms of somebody else's
    wording. Asked of `git check-ignore`, so the answer comes from the same
    .gitignore a commit would consult rather than from a second list here.
    """
    map_data = {
        "\u5bbf\u820d": {"source": "\u5bbf\u820d", "translation": "dormitory", "fields": ["label"]},
    }

    tracked = ROOT / "backend" / "would-be-committed.json"
    with pytest.raises(ValueError, match="would be tracked by git"):
        save_translation_map(map_data, tracked)
    assert not tracked.exists(), "a refused save must write nothing"

    # The .gitignore pattern covers the map's own name under the repo.
    ignored = ROOT / "translation_map.json"
    assert not ignored.exists(), "probe name is taken by a real file"
    save_translation_map(map_data, ignored)
    try:
        assert ignored.is_file()
    finally:
        ignored.unlink()

    # Outside the repository there is nothing to track it, so it is allowed.
    outside = tmp_path / "operator" / "anything.json"
    assert save_translation_map(map_data, outside).is_file()


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
    """This suite's own tests run on a checkout where the source libraries are absent.

    Only the test files are read, and deliberately: the module under test needs
    `subprocess` to ask git whether a destination is tracked, while a FIXTURE
    that started reading a real library would need exactly that kind of import.
    Asserting the suite's import list catches it; running the suite on this
    machine never would, because the libraries are here.
    """
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


def refused_strings_in_map(entries, translation_map) -> list[str]:
    """Map keys that came from an entry the guard refuses, named by their entry.

    The guard never returns a refused entry's text - that is the whole point of
    it - so the strings are collected here, from the entries this caller already
    holds, and only their identifiers are ever reported.
    """
    from backend.asset_guard import guard_entries

    entries = list(entries)
    _, report = guard_entries(entries)
    refused_ids = set(report["refused_identifiers"])

    offenders = []
    for entry in entries:
        identifier = str(entry.get("identifier") or entry.get("id") or entry.get("key") or "")
        if identifier not in refused_ids:
            continue
        for field in sorted(entry):
            value = entry[field]
            values = value if isinstance(value, (list, tuple)) else [value]
            for item in values:
                if isinstance(item, str) and item in translation_map:
                    offenders.append(f"{identifier}:{field}")
    return offenders


def test_no_string_from_a_refused_entry_reaches_the_map():
    """A refused entry's prose is not translated, so it is never a map key.

    The entry is named when it fails and its text never is: a report that
    quoted the string to prove the string is there would publish it to say so.
    """
    accepted_room = "\u5ba2\u5385"
    refused_room = "\u6821\u56ed\u8d70\u5eca"

    entries = [
        {"identifier": "acc-room-01", "library": "general_scenes", "label": accepted_room},
        {"identifier": "school_corridor_01", "label": refused_room},
    ]

    clean_map = {
        accepted_room: {
            "source": accepted_room,
            "translation": "living room",
            "fields": ["label"],
        },
    }
    assert refused_strings_in_map(entries, clean_map) == []

    # The same map with the refused room's string added is what this forbids
    dirty_map = dict(clean_map)
    dirty_map[refused_room] = {
        "source": refused_room,
        "translation": "school corridor",
        "fields": ["label"],
    }
    offenders = refused_strings_in_map(entries, dirty_map)
    assert offenders == ["school_corridor_01:label"]

    # It names the entry, and carries neither the source string nor its
    # translation, which is what a report of this is allowed to say
    assert "school_corridor_01" in offenders[0]
    assert refused_room not in offenders[0]
    assert "school corridor" not in offenders[0]

    # A not-adopted library is refused for its own reason and lands here too
    profile_name = "\u7f8e\u5948"
    profile_entries = [{"identifier": "amateurs-01", "library": "amateurs",
                        "display_name": profile_name}]
    profile_map = {profile_name: {"source": profile_name, "translation": "Haruna",
                                  "fields": ["display_name"]}}
    assert refused_strings_in_map(profile_entries, profile_map) == ["amateurs-01:display_name"]
