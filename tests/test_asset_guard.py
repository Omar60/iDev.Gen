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

from pathlib import Path

from backend.asset_guard import (
    SIGNAL_IDENTIFIER,
    SIGNAL_LIBRARY,
    SIGNAL_MINOR_PROFILE_KEY,
    SIGNAL_TAGS,
    SIGNAL_THEME_TEXT,
    check_entry,
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


def test_keyword_arguments_and_check_entry_alias():
    """The guard function supports explicit keyword arguments and the check_entry alias."""
    # Keyword arguments directly
    res_kw = guard_entry(identifier="kw_01", theme_text="high school classroom")
    assert res_kw == (SIGNAL_THEME_TEXT, "kw_01")

    # check_entry alias
    res_alias = check_entry(identifier="alias_01", theme_text="middle school desk")
    assert res_alias == (SIGNAL_THEME_TEXT, "alias_01")

    # Accepted via keyword arguments
    assert check_entry(identifier="alias_acc", theme_text="modern art studio") is None


def test_file_is_pure_ascii_and_no_control_bytes():
    """This test file must be pure ASCII with no C0 control bytes except legal whitespace."""
    target = ROOT / "tests" / "test_asset_guard.py"
    assert target.exists()
    raw_bytes = target.read_bytes()
    assert len(raw_bytes) > 0
    for idx, b in enumerate(raw_bytes):
        assert b <= 0x7F, f"byte {hex(b)} at {idx} exceeds ASCII range"
        if b < 0x20:
            assert b in LEGAL_CONTROLS, f"illegal control byte {hex(b)} at {idx}"
    assert 0x08 not in raw_bytes, "contains literal backspace byte"

    lines = target.read_text(encoding="utf-8").splitlines()
    for line_num, line in enumerate(lines, 1):
        assert line == line.rstrip(), f"line {line_num} has trailing whitespace"
