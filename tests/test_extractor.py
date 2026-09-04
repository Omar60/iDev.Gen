"""Tests for non-English string extractor from asset source directories.

Asserts that:
- source_dir is a required argument with no default, rejecting missing or invalid paths.
- Refused fixtures' strings are completely absent from output across all refusal signals.
- Every non-English string across diverse fields is extracted and reported with its identifier and field.
- English text, typography, and Latin loanwords are not extracted.
- Various JSON structures (list, dict-of-entries, wrapped list, single entry) are supported.
- Exact duplicates within the same field on the same entry are deduplicated while preserving occurrences across entries and fields.
- Files are pure ASCII with no control bytes and no trailing whitespace.
- The test suite reaches no source library.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from backend.extractor import extract_non_english_strings

ROOT = Path(__file__).resolve().parents[1]
LEGAL_CONTROLS = {0x09, 0x0A, 0x0D}  # tab, newline, carriage return


def test_requires_source_dir_argument(tmp_path):
    """Verify that source_dir is a required argument with no default."""
    # Calling with no arguments raises TypeError
    with pytest.raises(TypeError):
        extract_non_english_strings()

    # Calling with None or empty/whitespace string raises ValueError
    with pytest.raises(ValueError, match="source_dir is required"):
        extract_non_english_strings(None)
    with pytest.raises(ValueError, match="source_dir is required"):
        extract_non_english_strings("")
    with pytest.raises(ValueError, match="source_dir is required"):
        extract_non_english_strings("   ")

    # Calling with non-existent path raises NotADirectoryError
    non_existent = tmp_path / "does_not_exist"
    with pytest.raises(NotADirectoryError, match="must be an existing directory"):
        extract_non_english_strings(non_existent)

    # Calling with a file path raises NotADirectoryError
    file_path = tmp_path / "file.json"
    file_path.write_text("{}", encoding="utf-8")
    with pytest.raises(NotADirectoryError, match="must be an existing directory"):
        extract_non_english_strings(file_path)


def test_refused_fixture_strings_are_absent_from_output(tmp_path):
    """Verify a refused fixture's strings are absent from extractor output.

    Tests all five refusal signals (library, minor profile key, identifier, tags,
    theme text). Each refused entry carries a unique planted non-English token.
    The test asserts directly that each planted string is absent from the output,
    not by counting.
    """
    source_dir = tmp_path / "sources"
    source_dir.mkdir()

    planted_lib = "\u690d\u7269_refused_library_planted_token_111"
    planted_prof = "\u690d\u7269_refused_profile_planted_token_222"
    planted_ident = "\u690d\u7269_refused_ident_planted_token_333"
    planted_tag = "\u690d\u7269_refused_tag_planted_token_444"
    planted_theme = "\u690d\u7269_refused_theme_planted_token_555"

    accepted_str = "\u5ba2\u5385_accepted_string_666"

    entries = [
        # Accepted entry
        {
            "identifier": "acc-01",
            "library": "standard_scenes",
            "label": accepted_str,
            "theme": "luxurious living room with couch",
        },
        # Signal 1: refused library
        {
            "identifier": "ref-lib-01",
            "library": "forbidden-archive",
            "label": planted_lib,
            "theme": "quiet room with table",
        },
        # Signal 2: minor-coded profile key
        {
            "identifier": "ref-prof-01",
            "profile_key": "jc",
            "notes": planted_prof,
            "theme": "studio setting",
        },
        # Signal 3: refused identifier
        {
            "identifier": "school_corridor_01",
            "label": planted_ident,
            "theme": "sunlight through windows",
        },
        # Signal 4: refused tags
        {
            "identifier": "ref-tag-01",
            "tags": ["classroom", "indoor"],
            "notes": planted_tag,
            "theme": "wooden desks",
        },
        # Signal 5: refused theme text
        {
            "identifier": "ref-theme-01",
            "theme": "afternoon sunlight in a high school classroom",
            "label": planted_theme,
        },
    ]

    fixture_file = source_dir / "fixtures.json"
    fixture_file.write_text(json.dumps(entries), encoding="utf-8")

    results = extract_non_english_strings(
        source_dir,
        refused_libraries=("forbidden-archive",),
    )

    extracted_strings = [item["string"] for item in results]
    extracted_ids = [item["identifier"] for item in results]

    # Directly assert each planted refused string is absent
    assert planted_lib not in extracted_strings
    assert planted_prof not in extracted_strings
    assert planted_ident not in extracted_strings
    assert planted_tag not in extracted_strings
    assert planted_theme not in extracted_strings

    # Assert no refused identifier is listed
    assert "ref-lib-01" not in extracted_ids
    assert "ref-prof-01" not in extracted_ids
    assert "school_corridor_01" not in extracted_ids
    assert "ref-tag-01" not in extracted_ids
    assert "ref-theme-01" not in extracted_ids

    # Assert the accepted string is present with correct identifier and field
    assert accepted_str in extracted_strings
    assert any(
        item["string"] == accepted_str
        and item["identifier"] == "acc-01"
        and item["field"] == "label"
        for item in results
    )


def test_extracts_non_english_strings_across_all_fields(tmp_path):
    """Verify that every non-English string across diverse fields is extracted.

    Checks: label, notes, action_anchor, pose_hint, description, display_name,
    shot_variants list, and nested anchors dict. Also checks that English text
    with typographic punctuation and Latin loanwords is not extracted.
    """
    source_dir = tmp_path / "sources"
    source_dir.mkdir()

    label_zh = "\u5ba2\u5385"  # living room
    notes_zh = "\u6ce8\u610f\u5149\u7ebf"  # lighting notes
    anchor_zh = "\u62fd\u4f4f\u6905\u5b50"  # hold chair
    pose_zh = "\u7ad9\u7acb\u5fae\u7b11"  # standing smile
    desc_zh = "\u6e29\u67d4\u6c14\u8d28"  # gentle temperament
    display_zh = "\u7f8e\u5948"  # Haruna
    variant1_zh = "\u53d8\u4f53\u4e00"  # variant 1
    variant2_zh = "\u53d8\u4f53\u4e8c"  # variant 2
    camera_zh = "\u4fef\u62cd\u89c6\u89d2"  # overhead angle

    entry = {
        "identifier": "accepted-scene-01",
        "library": "general_scenes",
        "label": label_zh,
        "notes": notes_zh,
        "action_anchor": anchor_zh,
        "pose_hint": pose_zh,
        "description": desc_zh,
        "display_name": display_zh,
        "shot_variants": [variant1_zh, variant2_zh],
        "identities": [
            {"name": display_zh},
        ],
        "anchors": {
            "camera": camera_zh,
        },
        # English prose with em dash, curly quotes, and Latin loanword cafe
        "theme": "art studio \u2014 bright morning with \u2018natural\u2019 light and caf\u00e9 table",
    }

    (source_dir / "scenes.json").write_text(json.dumps([entry]), encoding="utf-8")

    results = extract_non_english_strings(source_dir)

    expected = [
        ("accepted-scene-01", "action_anchor", anchor_zh),
        ("accepted-scene-01", "anchors.camera", camera_zh),
        ("accepted-scene-01", "description", desc_zh),
        ("accepted-scene-01", "display_name", display_zh),
        ("accepted-scene-01", "identities.name", display_zh),
        ("accepted-scene-01", "label", label_zh),
        ("accepted-scene-01", "notes", notes_zh),
        ("accepted-scene-01", "pose_hint", pose_zh),
        ("accepted-scene-01", "shot_variants", variant1_zh),
        ("accepted-scene-01", "shot_variants", variant2_zh),
    ]

    for ident, field, string_val in expected:
        match = [
            item for item in results
            if item["identifier"] == ident and item["field"] == field and item["string"] == string_val
        ]
        assert len(match) == 1, f"Missing or duplicate extraction for {field}: {string_val}"

    # Verify English theme string was NOT extracted
    extracted_strings = [item["string"] for item in results]
    assert entry["theme"] not in extracted_strings
    assert len(results) == len(expected)


def test_handles_various_json_file_structures(tmp_path):
    """Verify extractor correctly handles different JSON file shapes in source_dir."""
    source_dir = tmp_path / "shapes"
    source_dir.mkdir()

    # Structure 1: list of entries
    s1 = "\u6c99\u53d1_list"
    (source_dir / "list_entries.json").write_text(
        json.dumps([{"identifier": "item-list", "label": s1}]),
        encoding="utf-8",
    )

    # Structure 2: dict of entries (keyed by identifier)
    s2 = "\u6c99\u53d1_dict_keys"
    (source_dir / "dict_entries.json").write_text(
        json.dumps({"item-dict": {"label": s2}}),
        encoding="utf-8",
    )

    # Structure 3: dict wrapping an entry list
    s3 = "\u6c99\u53d1_wrapped"
    (source_dir / "wrapped_entries.json").write_text(
        json.dumps({"entries": [{"identifier": "item-wrapped", "label": s3}]}),
        encoding="utf-8",
    )

    # Structure 4: single entry dict
    s4 = "\u6c99\u53d1_single"
    (source_dir / "single_entry.json").write_text(
        json.dumps({"identifier": "item-single", "label": s4}),
        encoding="utf-8",
    )

    results = extract_non_english_strings(source_dir)
    found_strings = {item["string"]: item["identifier"] for item in results}

    assert found_strings.get(s1) == "item-list"
    assert found_strings.get(s2) == "item-dict"
    assert found_strings.get(s3) == "item-wrapped"
    assert found_strings.get(s4) == "item-single"


def test_invalid_json_raises_value_error(tmp_path):
    """A corrupt JSON file in source_dir raises ValueError."""
    source_dir = tmp_path / "bad_json"
    source_dir.mkdir()
    (source_dir / "corrupt.json").write_text("{broken json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSON"):
        extract_non_english_strings(source_dir)


def test_empty_source_dir_returns_empty_list(tmp_path):
    """An empty source directory returns an empty list."""
    source_dir = tmp_path / "empty_dir"
    source_dir.mkdir()
    assert extract_non_english_strings(source_dir) == []


def test_deduplication_and_multiple_occurrences(tmp_path):
    """Verify deduplication within the same field while preserving distinct occurrences."""
    source_dir = tmp_path / "dedup"
    source_dir.mkdir()

    shared_zh = "\u5171\u4eab\u6587\u672c"  # shared text

    entry1 = {
        "identifier": "entry-01",
        "label": shared_zh,
        # Exact duplicate in same field list
        "shot_variants": [shared_zh, shared_zh],
    }
    entry2 = {
        "identifier": "entry-02",
        "label": shared_zh,
    }

    (source_dir / "entries.json").write_text(json.dumps([entry1, entry2]), encoding="utf-8")

    results = extract_non_english_strings(source_dir)

    # Entry 1 label
    e1_label = [r for r in results if r["identifier"] == "entry-01" and r["field"] == "label"]
    assert len(e1_label) == 1

    # Entry 1 variants (deduplicated to 1 despite being in list twice)
    e1_variants = [r for r in results if r["identifier"] == "entry-01" and r["field"] == "shot_variants"]
    assert len(e1_variants) == 1

    # Entry 2 label (preserved separately from Entry 1 label)
    e2_label = [r for r in results if r["identifier"] == "entry-02" and r["field"] == "label"]
    assert len(e2_label) == 1

    assert len(results) == 3


def test_extractor_files_are_pure_ascii_and_contain_no_control_bytes():
    """Verify extractor files are pure ASCII with no illegal control bytes or trailing whitespace."""
    targets = [
        ROOT / "backend" / "extractor.py",
        ROOT / "tests" / "test_extractor.py",
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


def test_extractor_suite_reaches_no_source_library():
    """The extractor's own tests run on a checkout where the source libraries are absent.

    Asserting on the test file's imports ensures tests only use mock fixtures in tmp_path
    and do not attempt to read real external source directories.
    """
    allowed = {"__future__", "ast", "json", "pathlib", "pytest", "backend"}
    for filename in ("test_extractor.py",):
        tree = ast.parse((ROOT / "tests" / filename).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        assert imported <= allowed, f"{filename} imports {sorted(imported - allowed)}"
