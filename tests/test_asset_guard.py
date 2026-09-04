"""Tests for the asset import guard function and deny-list.

Asserts that:
- One fixture entry is refused per signal across all five refusal signals.
- The five returned signal names are distinct.
- The returned value carries no part of the entry's text.
- Accepted adult and university cases are accepted (one assertion each).
- Word boundaries and token splitting prevent false refusals on substrings.
- Chinese markers and allow-list work as expected when built from escapes.
- No tracked file contains non-ASCII or illegal C0 control bytes.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import backend.asset_guard as asset_guard
from backend.asset_guard import (
    ALL_SIGNALS,
    SIGNAL_IDENTIFIER,
    SIGNAL_LIBRARY,
    SIGNAL_MINOR_PROFILE_KEY,
    SIGNAL_TAGS,
    SIGNAL_THEME_TEXT,
    guard_entries,
    guard_entry,
)

ROOT = Path(__file__).resolve().parents[1]
LEGAL_CONTROLS = {0x09, 0x0A, 0x0D}  # tab, newline, carriage return


def test_one_fixture_refused_per_signal_five_distinct_signals():
    """Verify one fixture entry is refused per signal with five distinct signals."""
    # Signal 1: source library
    lib_entry = {
        "identifier": "fixture-lib-01",
        "library": "forbidden-archive",
        "theme": "modern living room with leather couch",
    }
    res_lib = guard_entry(lib_entry, refused_libraries=("forbidden-archive",))
    assert res_lib == (SIGNAL_LIBRARY, "fixture-lib-01")

    # Signal 2: minor-coded profile key
    prof_entry = {
        "identifier": "fixture-prof-01",
        "profile_key": "jc",
        "theme": "standing near window in studio",
    }
    res_prof = guard_entry(prof_entry)
    assert res_prof == (SIGNAL_MINOR_PROFILE_KEY, "fixture-prof-01")

    # Signal 3: identifier
    id_entry = {
        "identifier": "school_hallway_01",
        "theme": "sunlight through large windows",
    }
    res_id = guard_entry(id_entry)
    assert res_id == (SIGNAL_IDENTIFIER, "school_hallway_01")

    # Signal 4: tags
    tag_entry = {
        "identifier": "fixture-tag-01",
        "tags": ["classroom", "indoor"],
        "theme": "wooden desks and chairs",
    }
    res_tag = guard_entry(tag_entry)
    assert res_tag == (SIGNAL_TAGS, "fixture-tag-01")

    # Signal 5: theme text
    theme_entry = {
        "identifier": "fixture-theme-01",
        "theme": "afternoon sunlight in a high school corridor",
    }
    res_theme = guard_entry(theme_entry)
    assert res_theme == (SIGNAL_THEME_TEXT, "fixture-theme-01")

    # Verify five distinct signal names
    returned_signals = {res_lib[0], res_prof[0], res_id[0], res_tag[0], res_theme[0]}
    assert len(returned_signals) == 5
    assert returned_signals == {
        "library",
        "minor_profile_key",
        "identifier",
        "tags",
        "theme_text",
    }


def test_accepted_cases_one_assertion_each():
    """Operator breadth decision: accepted adult and university cases."""
    # 1. campus
    assert guard_entry({"identifier": "acc-01", "theme": "walking across the campus at night"}) is None

    # 2. dormitory
    assert guard_entry({"identifier": "acc-02", "theme": "reading on a bed in the dormitory"}) is None

    # 3. playground
    assert guard_entry({"identifier": "acc-03", "theme": "sunset over the city playground park"}) is None

    # 4. gymnasium
    assert guard_entry({"identifier": "acc-04", "theme": "training inside a modern gymnasium"}) is None

    # 5. academy
    assert guard_entry({"identifier": "acc-05", "theme": "classical painting academy studio"}) is None

    # 6. college student
    assert guard_entry({"identifier": "acc-06", "theme": "portrait of a college student in a library"}) is None

    # 7. Chinese university student (\u5927\u5b66\u751f) built from escapes
    zh_uni_student = "".join(chr(cp) for cp in [0x5927, 0x5B66, 0x751F])
    assert guard_entry({"identifier": "acc-07", "theme": f"portrait of {zh_uni_student} in bookstore"}) is None

    # 8. Plain adult room entry
    adult_room = {
        "identifier": "adult_penthouse_01",
        "library": "standard_interiors",
        "tags": ["living_room", "luxury", "couch"],
        "theme": "luxurious executive penthouse with glass coffee table and panoramic city view",
    }
    assert guard_entry(adult_room) is None


def test_word_boundary_proof():
    """English markers match on word/token boundaries, never as bare substrings."""
    # Substring proofs: canteen, childproof, and bjc_01 must be accepted
    assert guard_entry({"identifier": "acc-canteen", "profile_key": "canteen"}) is None
    assert guard_entry({"identifier": "acc-childproof", "profile_key": "childproof"}) is None
    assert guard_entry({"identifier": "bjc_01", "theme": "urban nighttime street"}) is None

    # Same strings in theme text must be accepted
    assert guard_entry({"identifier": "acc-theme-canteen", "theme": "lunch in the office canteen"}) is None
    assert guard_entry({"identifier": "acc-theme-childproof", "theme": "cabinet with childproof lock"}) is None

    # Whole tokens: teen, child, and jc must be refused
    res_teen = guard_entry({"identifier": "ref-teen", "profile_key": "teen"})
    assert res_teen == (SIGNAL_MINOR_PROFILE_KEY, "ref-teen")

    res_child = guard_entry({"identifier": "ref-child", "profile_key": "child"})
    assert res_child == (SIGNAL_MINOR_PROFILE_KEY, "ref-child")

    res_jc_id = guard_entry({"identifier": "jc_01", "theme": "street scene"})
    assert res_jc_id == (SIGNAL_IDENTIFIER, "jc_01")

    res_jc_prof = guard_entry({"identifier": "ref-jc-prof", "profile_key": "jc"})
    assert res_jc_prof == (SIGNAL_MINOR_PROFILE_KEY, "ref-jc-prof")


def test_returned_value_carries_no_entry_text():
    """The returned refusal tuple must carry only signal and identifier, never text."""
    secret_text = "confidential_prompt_text_do_not_leak_12345"
    entry = {
        "identifier": "clean_ident_99",
        "theme_text": f"high school classroom with {secret_text}",
        "tags": [f"tag_{secret_text}"],
    }
    res = guard_entry(entry)
    assert res is not None
    signal, identifier = res

    # The identifier must match
    assert identifier == "clean_ident_99"

    # None of the entry's text content may appear in any part of the return value
    assert secret_text not in signal
    assert secret_text not in identifier
    assert secret_text not in str(res)
    assert secret_text not in repr(res)

    # Check all fields of the 5 refusal fixtures
    fixture = {
        "identifier": "ident_alpha",
        "library": "denied_source",
        "theme": "forbidden theme description",
        "tags": ["forbidden", "tags"],
    }
    res_fixture = guard_entry(fixture, refused_libraries=("denied_source",))
    assert res_fixture is not None
    for field_val in ("denied_source", "forbidden theme description", "forbidden", "tags"):
        assert field_val not in str(res_fixture)


def test_chinese_school_markers_and_allow_list():
    """Chinese school markers refuse, university allow-list accepts, built from escapes."""
    # Escaped Chinese school glyph (U+5B66 U+6821)
    zh_school = "".join(chr(cp) for cp in [0x5B66, 0x6821])
    res_school = guard_entry({"identifier": "zh-scene-01", "theme": f"hallway in {zh_school}"})
    assert res_school == (SIGNAL_THEME_TEXT, "zh-scene-01")

    # Escaped Chinese student glyph (U+5B66 U+751F)
    zh_student = "".join(chr(cp) for cp in [0x5B66, 0x751F])
    res_student = guard_entry({"identifier": "zh-scene-02", "theme": f"portrait of {zh_student}"})
    assert res_student == (SIGNAL_THEME_TEXT, "zh-scene-02")

    # Escaped Chinese university glyphs: \u5927\u5b66 and \u5927\u5b66\u751f are accepted
    zh_uni = "".join(chr(cp) for cp in [0x5927, 0x5B66])
    assert guard_entry({"identifier": "zh-uni-01", "theme": f"courtyard of {zh_uni}"}) is None

    zh_uni_student = "".join(chr(cp) for cp in [0x5927, 0x5B66, 0x751F])
    assert guard_entry({"identifier": "zh-uni-stud-01", "theme": f"portrait of {zh_uni_student}"}) is None

    # Retained Chinese school markers with U+6821: campus (\u6821\u56ed), building (\u6821\u820d), gate (\u6821\u95e8)
    zh_campus = "".join(chr(cp) for cp in [0x6821, 0x56ED])
    assert guard_entry({"identifier": "zh-camp-01", "theme": f"view of {zh_campus}"}) == (
        SIGNAL_THEME_TEXT,
        "zh-camp-01",
    )

    zh_bldg = "".join(chr(cp) for cp in [0x6821, 0x820D])
    assert guard_entry({"identifier": "zh-bldg-01", "theme": f"view of {zh_bldg}"}) == (
        SIGNAL_THEME_TEXT,
        "zh-bldg-01",
    )

    zh_gate = "".join(chr(cp) for cp in [0x6821, 0x95E8])
    assert guard_entry({"identifier": "zh-gate-01", "theme": f"view of {zh_gate}"}) == (
        SIGNAL_THEME_TEXT,
        "zh-gate-01",
    )

    # Removed Chinese markers (\u5bbf\u820d dormitory, \u64cd\u573a playground) must be accepted
    zh_dorm = "".join(chr(cp) for cp in [0x5BBF, 0x820D])
    assert guard_entry({"identifier": "zh-dorm-01", "theme": f"room in {zh_dorm}"}) is None

    zh_play = "".join(chr(cp) for cp in [0x64CD, 0x573A])
    assert guard_entry({"identifier": "zh-play-01", "theme": f"running on {zh_play}"}) is None


def test_keyword_arguments():
    """The guard function supports explicit keyword arguments across accepted fields."""
    res_kw = guard_entry(identifier="kw_01", theme_text="high school classroom")
    assert res_kw == (SIGNAL_THEME_TEXT, "kw_01")
    assert guard_entry(identifier="kw_acc", theme_text="modern art studio") is None

    # Various accepted field names passed as kwargs
    assert guard_entry(id="prof_01", kind="profile", profile="jc") == (
        SIGNAL_MINOR_PROFILE_KEY,
        "prof_01",
    )
    assert guard_entry(key="tag_01", tag="classroom") == (
        SIGNAL_TAGS,
        "tag_01",
    )
    assert guard_entry(
        source_library="custom_lib",
        description="clean studio",
        tags=["art", "indoor"],
    ) is None


def test_every_text_field_is_checked_not_only_the_first():
    """A school-set label behind a harmless theme is still refused.

    Reading one field of an `or` chain accepted a school-set label whenever any
    earlier field held text, which is every real entry.
    """
    school_glyph = "".join(chr(cp) for cp in [0x5B66, 0x6821])
    lounge_glyph = "".join(chr(cp) for cp in [0x5BA2, 0x5385])
    for field, text in (
        ("label", "after school in the classroom"),
        ("notes", "schoolgirl in uniform"),
        ("description", "the blackboard behind her"),
        ("label", school_glyph),
    ):
        entry = {"identifier": "multi_01", "theme": "a quiet living room at dusk"}
        entry[field] = text
        assert guard_entry(entry) == (SIGNAL_THEME_TEXT, "multi_01"), field

    accepted = {
        "identifier": "multi_acc",
        "theme": "executive office at night",
        "label": "glass desk",
        "notes": "leather sofa",
        "description": lounge_glyph,
    }
    assert guard_entry(accepted) is None


def test_the_deny_list_cannot_be_emptied_by_entry_or_caller(monkeypatch):
    """An entry is source material and a caller is not an override.

    The entry carried its own `refused_libraries` key straight into the decision,
    which let the material being judged decide what the deny-list was.
    """
    monkeypatch.setattr(asset_guard, "REFUSED_LIBRARIES", ("denied_source",))
    entry = {"identifier": "ovr_01", "library": "denied_source"}
    assert guard_entry(dict(entry)) is not None
    assert guard_entry(dict(entry), refused_libraries=()) is not None
    assert guard_entry({**entry, "refused_libraries": ()}) is not None
    # A caller may still add a library for a test.
    assert guard_entry(
        {"identifier": "ovr_02", "library": "extra_source"},
        refused_libraries=("extra_source",),
    ) is not None
    # A caller passing a different library still leaves the original refusing.
    assert guard_entry(
        {"identifier": "ovr_03", "library": "denied_source"},
        refused_libraries=("different_source",),
    ) is not None


def test_undergraduate_is_adult_university_material():
    """Removed from the school markers by operator decision, with college."""
    assert guard_entry(
        identifier="uni_01",
        theme="portrait of an undergraduate student in a library",
    ) is None


def test_unknown_keyword_arguments_raise_naming_the_argument():
    """Any caller keyword argument outside known entry fields raises TypeError naming it."""
    bypass_args = ("allow_school", "force", "include_refused", "skip_guard", "strict")
    for arg in bypass_args:
        with pytest.raises(TypeError) as exc_info:
            guard_entry(**{arg: True})
        assert arg in str(exc_info.value), f"expected '{arg}' in {exc_info.value}"

        with pytest.raises(TypeError) as exc_info_entry:
            guard_entry({"identifier": "item_01"}, **{arg: True})
        assert arg in str(exc_info_entry.value), f"expected '{arg}' in {exc_info_entry.value}"


def test_bypass_shaped_keys_on_entry_dict_do_not_raise_and_are_ignored():
    """A bypass-shaped key on the entry dict is ignored: it does not raise and does not bypass."""
    bypass_keys = ("allow_school", "force", "include_refused", "skip_guard", "strict")
    for idx, key in enumerate(bypass_keys, 1):
        refused = {"identifier": f"ref_item_{idx}", "theme": "high school classroom", key: True}
        assert guard_entry(refused) == (SIGNAL_THEME_TEXT, f"ref_item_{idx}")

        accepted = {"identifier": f"acc_item_{idx}", "theme": "modern art studio", key: True}
        assert guard_entry(accepted) is None

    refused_all = {
        "identifier": "ref_all_bypass",
        "theme": "junior high hallway",
        "allow_school": True,
        "force": True,
        "include_refused": True,
        "skip_guard": True,
        "strict": False,
    }
    assert guard_entry(refused_all) == (SIGNAL_THEME_TEXT, "ref_all_bypass")

    accepted_all = {
        "identifier": "acc_all_bypass",
        "theme": "downtown coffee shop",
        "allow_school": True,
        "force": True,
        "include_refused": True,
        "skip_guard": True,
        "strict": False,
    }
    assert guard_entry(accepted_all) is None


def test_environment_variables_cannot_bypass_guard(monkeypatch):
    """No environment variable can disable the guard or include refused entries."""
    bypass_envs = [
        ("IDEVGEN_ALLOW_SCHOOL", "1"),
        ("IDEVGEN_GUARD", "0"),
        ("IDEVGEN_GUARD", "false"),
        ("IDEVGEN_IMPORT_FORCE", "true"),
        ("IDEVGEN_FORCE", "1"),
        ("IDEVGEN_SKIP_GUARD", "1"),
        ("IDEVGEN_INCLUDE_REFUSED", "true"),
        ("IDEVGEN_STRICT", "0"),
    ]
    refused = {"identifier": "ref_env", "theme": "high school corridor"}
    accepted = {"identifier": "acc_env", "theme": "mountain cabin"}

    for var, val in bypass_envs:
        monkeypatch.setenv(var, val)
        assert guard_entry(refused) == (SIGNAL_THEME_TEXT, "ref_env"), f"failed for {var}={val}"
        assert guard_entry(accepted) is None, f"failed for {var}={val}"


def test_guard_module_imports_nothing_that_can_be_configured():
    """The guard decides from its own constants and from nothing else.

    Grepping for "os.environ" and "getenv" passed a module that said
    `from os import environ`, and passed a module that read a JSON file to decide.
    The property is the import list, so assert on the import list: anything the
    guard could be told what to do by has to appear there first.
    """
    allowed_imports = {"__future__", "re", "typing"}
    source = (ROOT / "backend" / "asset_guard.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert "logging" not in imported, "guard must not import logging"
    assert imported <= allowed_imports, f"guard imports {sorted(imported - allowed_imports)}"

    # No file is opened and nothing is printed, so no file can turn the guard off
    # and nothing is written to stdout.
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "open" not in called, "guard must not call open()"
    assert "print" not in called, "guard must not call print()"
    attrs = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for reader in ("read_text", "read_bytes", "load", "loads", "getenv"):
        assert reader not in attrs, f"guard calls {reader}"


def test_guard_entries_mixed_sequence_and_no_theme_text_in_report():
    """A mixed sequence produces correct counts, per-signal counts, per-library counts,

    refused identifiers in input order, unchanged accepted entries in order,
    and no fixture text reaches the report.
    """
    planted_token = "TEST_PLANTED_TOKEN_ABSENCE_PROOF_XYZ991"

    ref_lib_1 = {
        "identifier": "ref-lib-01",
        "library": "forbidden-archive",
        "theme": f"modern living room with {planted_token}",
        "label": f"archive room {planted_token}",
        "notes": f"lighting notes {planted_token}",
    }
    ref_prof_1 = {
        "identifier": "ref-prof-01",
        "profile_key": "jc",
        "theme": f"standing near window with {planted_token}",
        "label": f"profile {planted_token}",
        "notes": f"pose notes {planted_token}",
    }
    ref_lib_2 = {
        "identifier": "ref-lib-02",
        "library": "forbidden-archive",
        "theme": f"hallway view with {planted_token}",
        "label": f"archive hall {planted_token}",
        "notes": f"hallway notes {planted_token}",
    }
    ref_id_1 = {
        "identifier": "school_hallway_01",
        "theme": f"sunlight through large windows with {planted_token}",
        "label": f"hallway {planted_token}",
        "notes": f"window notes {planted_token}",
    }
    ref_tag_1 = {
        "identifier": "ref-tag-01",
        "tags": ["classroom", "indoor"],
        "theme": f"wooden desks and chairs with {planted_token}",
        "label": f"classroom {planted_token}",
        "notes": f"desk notes {planted_token}",
    }
    ref_theme_1 = {
        "identifier": "ref-theme-01",
        "theme": f"afternoon sunlight in a high school corridor with {planted_token}",
        "label": f"corridor {planted_token}",
        "notes": f"corridor notes {planted_token}",
    }

    acc_1 = {
        "identifier": "acc-room-01",
        "library": "standard_interiors",
        "theme": "modern penthouse living room with beige couch",
        "label": "penthouse lounge",
    }
    acc_2 = {
        "identifier": "acc-room-02",
        "library": "standard_interiors",
        "theme": "industrial kitchen with marble island counter",
        "label": "kitchen island",
    }

    # Interleave accepted and refused entries
    sequence = [
        acc_1,
        ref_lib_1,
        ref_prof_1,
        acc_2,
        ref_lib_2,
        ref_id_1,
        ref_tag_1,
        ref_theme_1,
    ]

    accepted, report = guard_entries(sequence, refused_libraries=("forbidden-archive",))

    # Right counts
    assert report["accepted"] == 2
    assert report["refused"] == 6

    # Right per-signal counts
    assert report["by_signal"] == {
        SIGNAL_LIBRARY: 2,
        SIGNAL_MINOR_PROFILE_KEY: 1,
        SIGNAL_IDENTIFIER: 1,
        SIGNAL_TAGS: 1,
        SIGNAL_THEME_TEXT: 1,
    }

    # Right per-library count (2 from forbidden-archive)
    assert report["by_library"] == {
        "forbidden-archive": 2,
    }

    # Refused identifiers in input order
    expected_refused_ids = [
        "ref-lib-01",
        "ref-prof-01",
        "ref-lib-02",
        "school_hallway_01",
        "ref-tag-01",
        "ref-theme-01",
    ]
    assert report["refused_identifiers"] == expected_refused_ids

    # Accepted entries unchanged and in order
    assert len(accepted) == 2
    assert accepted[0] is acc_1
    assert accepted[1] is acc_2
    assert accepted == [acc_1, acc_2]

    # No text reaches the report
    serialized_report = json.dumps(report, sort_keys=True)
    assert planted_token not in serialized_report
    for field_text in (
        "modern living room",
        "archive room",
        "standing near window",
        "hallway view",
        "sunlight through large windows",
        "wooden desks and chairs",
        "afternoon sunlight in a high school corridor",
        "modern penthouse",
        "industrial kitchen",
    ):
        assert field_text not in serialized_report


def test_guard_entries_zero_count_keys_when_all_accepted():
    """Zero-count keys are present in a report over entries that were all accepted."""
    entries = [
        {"identifier": "acc-01", "theme": "mountain cabin with wood fireplace"},
        {"identifier": "acc-02", "theme": "lakeside deck at twilight"},
    ]
    accepted, report = guard_entries(entries, refused_libraries=("denied-lib",))

    assert accepted == entries
    assert report["accepted"] == 2
    assert report["refused"] == 0

    # All signals present with count 0
    for sig in ALL_SIGNALS:
        assert sig in report["by_signal"]
        assert report["by_signal"][sig] == 0
    assert report["by_signal"] == {
        SIGNAL_LIBRARY: 0,
        SIGNAL_MINOR_PROFILE_KEY: 0,
        SIGNAL_IDENTIFIER: 0,
        SIGNAL_TAGS: 0,
        SIGNAL_THEME_TEXT: 0,
    }

    # Configured refused library present with count 0
    assert "denied-lib" in report["by_library"]
    assert report["by_library"]["denied-lib"] == 0

    # No refused identifiers
    assert report["refused_identifiers"] == []


def test_guard_entries_empty_sequence():
    """An empty sequence produces an empty accepted list and zero counts."""
    accepted, report = guard_entries([])
    assert accepted == []
    assert report["accepted"] == 0
    assert report["refused"] == 0
    for sig in ALL_SIGNALS:
        assert report["by_signal"][sig] == 0
    assert report["by_library"] == {}
    assert report["refused_identifiers"] == []


def test_guard_entries_refused_library_with_zero_entries():
    """A configured refused library with zero matching entries has count 0 in report."""
    entries = [
        {"identifier": "item-01", "library": "used-denied", "theme": "studio apartment"},
        {"identifier": "item-02", "library": "clean-lib", "theme": "quiet library hall"},
    ]
    accepted, report = guard_entries(
        entries,
        refused_libraries=("used-denied", "unused-denied"),
    )
    assert len(accepted) == 1
    assert accepted[0] is entries[1]
    assert report["accepted"] == 1
    assert report["refused"] == 1
    assert report["by_library"]["used-denied"] == 1
    assert report["by_library"]["unused-denied"] == 0
    assert report["refused_identifiers"] == ["item-01"]


def test_guard_entries_unexpected_keyword_arguments_raise():
    """Any caller keyword argument outside known entry fields raises TypeError naming it."""
    bypass_args = ("allow_school", "force", "include_refused", "skip_guard", "strict")
    for arg in bypass_args:
        with pytest.raises(TypeError) as exc_info:
            guard_entries([], **{arg: True})
        assert arg in str(exc_info.value), f"expected '{arg}' in {exc_info.value}"


def test_guard_entries_source_library_and_lib_aliases_and_duplicate_refused_libs():
    """guard_entries handles source_library, lib, and deduplicates refused_libraries."""
    entries = [
        {"identifier": "ref-01", "source_library": "denied-a", "theme": "hallway"},
        {"identifier": "ref-02", "lib": "denied-b", "theme": "lounge"},
    ]
    accepted, report = guard_entries(
        entries,
        refused_libraries=("denied-a", "denied-a", "denied-b"),
    )
    assert report["accepted"] == 0
    assert report["refused"] == 2
    assert report["by_library"] == {"denied-a": 1, "denied-b": 1}
    assert report["refused_identifiers"] == ["ref-01", "ref-02"]


def test_the_guard_suite_reaches_no_source_library():
    """The guard's own tests run on a checkout where the source libraries are absent.

    Every fixture in this suite is invented, and the property that keeps it that
    way is the import list: reading a real library needs `os`, `glob`, `shutil`
    or the app's config to find one. Asserting the imports catches a fixture that
    starts reading from disk, which running the suite on this machine never
    would - the libraries are here.
    """
    allowed = {"__future__", "ast", "json", "pathlib", "pytest", "backend"}
    for name in ("test_asset_guard.py", "test_asset_guard_markers.py"):
        tree = ast.parse((ROOT / "tests" / name).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        assert imported <= allowed, f"{name} imports {sorted(imported - allowed)}"


def test_file_is_pure_ascii_and_no_control_bytes():
    """This test file and the guard module must be pure ASCII with no C0 control bytes except legal whitespace."""
    for rel_path in ("tests/test_asset_guard.py", "backend/asset_guard.py"):
        target = ROOT / rel_path
        assert target.exists()
        raw_bytes = target.read_bytes()
        assert len(raw_bytes) > 0
        for idx, b in enumerate(raw_bytes):
            assert b <= 0x7F, f"{rel_path}: byte {hex(b)} at {idx} exceeds ASCII range"
            if b < 0x20:
                assert b in LEGAL_CONTROLS, f"{rel_path}: illegal control byte {hex(b)} at {idx}"
        assert 0x08 not in raw_bytes, f"{rel_path}: contains literal backspace byte"

        lines = target.read_text(encoding="utf-8").splitlines()
        for line_num, line in enumerate(lines, 1):
            assert line == line.rstrip(), f"{rel_path}: line {line_num} has trailing whitespace"
