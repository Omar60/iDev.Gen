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

from backend.extractor import (
    extract_non_english_strings,
    find_uncovered_strings,
)

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


def test_nested_entries_reach_the_guard_one_by_one(tmp_path):
    """A file wrapping its entries is split, so each entry is guarded on its own.

    The source libraries write a file-level 'library', 'version' and
    'description' beside the collection they carry. Read as one entry, the
    guard sees one identifier and one set of fields, and every refused entry
    nested inside rides through as a field of an accepted whole.
    """
    source_dir = tmp_path / "sources"
    source_dir.mkdir()

    planted_profile = "\u690d\u7269\u005f\u006e\u0065\u0073\u0074\u0065\u0064\u005f\u0072\u0065\u0066\u0075\u0073\u0065\u0064\u005f\u0070\u0072\u006f\u0066\u0069\u006c\u0065\u005f\u0037\u0037\u0037"
    accepted_scene = "\u5ba2\u5385\u005f\u006e\u0065\u0073\u0074\u0065\u0064\u005f\u0061\u0063\u0063\u0065\u0070\u0074\u0065\u0064\u005f\u0038\u0038\u0038"

    payload = {
        "library": "amateur_profiles",
        "version": 3,
        "description": "a file-level description sitting beside the collection",
        "profiles": {
            "jc-asymmetric": {"display_name": planted_profile},
        },
        "items": [
            {"id": "general-bakery-cooling-rack", "label": accepted_scene},
        ],
    }
    (source_dir / "amateurs.json").write_text(json.dumps(payload), encoding="utf-8")

    results = extract_non_english_strings(source_dir)
    extracted = [item["string"] for item in results]

    assert planted_profile not in extracted
    assert accepted_scene in extracted

    # The accepted entry is reported under its own identifier, not under a
    # wrapper the whole file collapsed into.
    assert any(
        item["string"] == accepted_scene
        and item["identifier"] == "general-bakery-cooling-rack"
        and item["field"] == "label"
        for item in results
    )


def test_list_items_are_guarded_one_by_one(tmp_path):
    """A pool of bare strings is one entry per item, not one entry per list.

    A role pool holds refused roles beside accepted ones. Carried as a single
    entry, the list is refused or accepted whole.
    """
    source_dir = tmp_path / "pool"
    source_dir.mkdir()

    refused_role = "\u5973\u521d\u4e2d\u751f"  # female junior high student
    accepted_role = "\u5973\u79d8\u4e66"  # female secretary

    (source_dir / "amateurs.json").write_text(
        json.dumps({"identity_pool": [refused_role, accepted_role]}),
        encoding="utf-8",
    )

    results = extract_non_english_strings(source_dir)
    extracted = [item["string"] for item in results]

    assert refused_role not in extracted
    assert accepted_role in extracted
    assert any(
        item["string"] == accepted_role and item["identifier"] == "identity_pool[1]"
        for item in results
    )


def test_every_reported_string_names_its_entry(tmp_path):
    """No reported string carries an empty identifier.

    A string reported without an identifier cannot be named when an upload is
    refused for translation, which is what the report is for.
    """
    source_dir = tmp_path / "mixed"
    source_dir.mkdir()

    (source_dir / "wrapped.json").write_text(
        json.dumps({
            "library": "general_scenes",
            "items": [{"id": "scene-01", "label": "\u5ba2\u5385"}],
        }),
        encoding="utf-8",
    )
    (source_dir / "triggers.json").write_text(
        json.dumps({"z": {"trigger_one": ["\u97e9\u4f73\u4eba"]}}),
        encoding="utf-8",
    )
    (source_dir / "pool.json").write_text(
        json.dumps({"identity_pool": ["\u5973\u79d8\u4e66"]}),
        encoding="utf-8",
    )
    # A container carrying a scalar of its own beside the collection it holds:
    # the scalar is an entry named after the container, not a nameless string.
    (source_dir / "meta.json").write_text(
        json.dumps({
            "meta": {
                "caption": "\u5ba2\u5385",
                "groups": {"g1": {"label": "\u6c99\u53d1"}},
            }
        }),
        encoding="utf-8",
    )

    results = extract_non_english_strings(source_dir)

    assert results
    for item in results:
        assert item["identifier"], f"string reported with no identifier: {item['field']}"


def test_unsupported_json_shape_raises(tmp_path):
    """A JSON file that is neither a list nor an object refuses rather than collapses."""
    source_dir = tmp_path / "odd"
    source_dir.mkdir()
    (source_dir / "bare.json").write_text(json.dumps("just a string"), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported JSON shape"):
        extract_non_english_strings(source_dir)


def test_option_field_strings_are_pruned_before_extraction(tmp_path):
    """A school-coded garment is not listed, and its room still is.

    The room is accepted, so its own strings are extracted. The one garment
    the guard drops from `uniform_fit` is not among them.
    """
    source_dir = tmp_path / "rooms"
    source_dir.mkdir()

    room_label = "\u4f53\u9762\u8bd5\u8863\u95f4"
    kept_garment = "\u4e1d\u7ef8\u540a\u5e26\u88d9"
    dropped_garment = "\u6821\u670d"  # school uniform

    payload = {
        "library": "general_scenes",
        "items": [
            {
                "id": "general-fitting-room",
                "label": room_label,
                "scene_theme": "fitting room with a long mirror and a velvet stool",
                "uniform_fit": [kept_garment, dropped_garment],
            }
        ],
    }
    (source_dir / "general_scenes.json").write_text(json.dumps(payload), encoding="utf-8")

    results = extract_non_english_strings(source_dir)
    extracted = [item["string"] for item in results]

    assert dropped_garment not in extracted
    assert room_label in extracted
    assert kept_garment in extracted


def test_find_uncovered_strings_requires_arguments(tmp_path):
    """Verify source_dir and translation_map argument requirements."""
    with pytest.raises(ValueError, match="source_dir is required"):
        find_uncovered_strings("", {})
    with pytest.raises(ValueError, match="source_dir is required"):
        find_uncovered_strings(None, {})
    with pytest.raises(TypeError, match="translation_map must be a dict or a path"):
        find_uncovered_strings(tmp_path, 12345)


def test_find_uncovered_strings_reports_uncovered_and_covered(tmp_path):
    """Verify coverage reporting for covered, uncovered, and partial maps."""
    source_dir = tmp_path / "sources"
    source_dir.mkdir()

    s_action = "\u62fd\u4f4f\u6905\u5b50"
    s_notes = "\u6ce8\u610f\u5149\u7ebf"
    s_label = "\u5ba2\u5385"

    entry = {
        "identifier": "scene-alpha",
        "action_anchor": s_action,
        "notes": s_notes,
        "label": s_label,
    }
    (source_dir / "scenes.json").write_text(json.dumps([entry]), encoding="utf-8")

    tier_fields = {"action_anchor", "notes"}

    # 1. Empty map reports all strings in tier as uncovered
    uncovered_empty = find_uncovered_strings(source_dir, {}, tier_fields)
    assert len(uncovered_empty) == 2
    assert any(
        u["identifier"] == "scene-alpha" and u["field"] == "action_anchor" and u["string"] == s_action
        for u in uncovered_empty
    )
    assert any(
        u["identifier"] == "scene-alpha" and u["field"] == "notes" and u["string"] == s_notes
        for u in uncovered_empty
    )
    # label is not in tier_fields, so it is not reported
    assert not any(u["field"] == "label" for u in uncovered_empty)

    # 2. Partial map covers s_action only
    partial_map = {
        s_action: {
            "source": s_action,
            "translation": "grip the chair",
            "fields": ["action_anchor"],
        }
    }
    uncovered_partial = find_uncovered_strings(source_dir, partial_map, tier_fields)
    assert len(uncovered_partial) == 1
    assert uncovered_partial[0]["identifier"] == "scene-alpha"
    assert uncovered_partial[0]["field"] == "notes"
    assert uncovered_partial[0]["string"] == s_notes

    # 3. Complete map covers both strings
    full_map = {
        **partial_map,
        s_notes: {
            "source": s_notes,
            "translation": "lighting notes",
            "fields": ["notes"],
        },
    }
    uncovered_full = find_uncovered_strings(source_dir, full_map, tier_fields)
    assert uncovered_full == []


def test_refused_entry_strings_are_never_reported_as_uncovered(tmp_path):
    """The coverage check runs the guard, so refused entries never appear as uncovered."""
    source_dir = tmp_path / "sources"
    source_dir.mkdir()

    s_accepted = "\u5ba2\u5385"
    s_refused_school = "\u6821\u56ed\u8d70\u5eca"

    entries = [
        {
            "identifier": "acc-room-01",
            "notes": s_accepted,
        },
        {
            "identifier": "school_corridor_01",
            "notes": s_refused_school,
        },
    ]
    (source_dir / "entries.json").write_text(json.dumps(entries), encoding="utf-8")

    # With empty map, only the accepted entry's string is reported as uncovered
    uncovered = find_uncovered_strings(source_dir, {}, {"notes"})
    assert len(uncovered) == 1
    assert uncovered[0]["identifier"] == "acc-room-01"
    assert uncovered[0]["string"] == s_accepted

    # Assert refused entry string and identifier are completely absent
    extracted_strings = [u["string"] for u in uncovered]
    extracted_ids = [u["identifier"] for u in uncovered]
    assert s_refused_school not in extracted_strings
    assert "school_corridor_01" not in extracted_ids


def test_find_uncovered_strings_field_filter(tmp_path):
    """None checks every field, and a bare string is refused rather than wrapped."""
    source_dir = tmp_path / "sources"
    source_dir.mkdir()

    s_anchor = "\u62fd\u4f4f\u6905\u5b50"
    s_notes = "\u6ce8\u610f\u5149\u7ebf"
    s_label = "\u5ba2\u5385"

    entry = {
        "identifier": "item-01",
        "action_anchor": s_anchor,
        "notes": s_notes,
        "label": s_label,
    }
    (source_dir / "items.json").write_text(json.dumps([entry]), encoding="utf-8")

    # None filter checks all non-English fields
    res_all = find_uncovered_strings(source_dir, {}, None)
    assert len(res_all) == 3
    assert {r["field"] for r in res_all} == {"action_anchor", "notes", "label"}

    # A bare string is one field written two ways, so it raises
    with pytest.raises(TypeError, match="fields must be a collection of strings"):
        find_uncovered_strings(source_dir, {}, "notes")


def test_find_uncovered_strings_treats_empty_or_non_english_translation_as_uncovered(tmp_path):
    """An entry with an empty or non-English translation in the map is reported as uncovered."""
    source_dir = tmp_path / "sources"
    source_dir.mkdir()

    s1 = "\u6ce8\u610f\u5149\u7ebf"
    s2 = "\u62fd\u4f4f\u6905\u5b50"

    (source_dir / "data.json").write_text(
        json.dumps([{"identifier": "e1", "notes": s1, "action_anchor": s2}]),
        encoding="utf-8",
    )

    bad_map = {
        s1: {"source": s1, "translation": "", "fields": ["notes"]},
        s2: {"source": s2, "translation": "\u62fd\u4f4f", "fields": ["action_anchor"]},
    }
    uncovered = find_uncovered_strings(source_dir, bad_map, {"notes", "action_anchor"})
    assert len(uncovered) == 2


def test_find_uncovered_strings_with_file_path(tmp_path):
    """A map file path is loaded and validated on the way in."""
    from backend.translation_map import save_translation_map

    source_dir = tmp_path / "sources"
    source_dir.mkdir()

    s = "\u6ce8\u610f\u5149\u7ebf"
    (source_dir / "items.json").write_text(
        json.dumps([{"identifier": "e1", "notes": s}]),
        encoding="utf-8",
    )

    map_path = tmp_path / "untracked_map.json"
    save_translation_map(
        {s: {"source": s, "translation": "lighting notes", "fields": ["notes"]}},
        map_path,
    )

    # Passing path to find_uncovered_strings
    assert find_uncovered_strings(source_dir, map_path, {"notes"}) == []

