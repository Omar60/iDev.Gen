"""Tests for refusal markers in backend.asset_guard.

Asserts that:
- Every escaped marker matches the exact text it was written for.
- An incorrectly escaped marker fails to match.
- No marker or marker collection is empty (except REFUSED_LIBRARIES).
- Both new files contain no byte/codepoint above U+007F and no C0 control bytes
  except tab, newline and carriage return.
- Fixture entries invented for the test match when expected.
"""
from __future__ import annotations

from pathlib import Path

from backend.asset_guard import (
    MINOR_PROFILE_KEYS,
    MINOR_PROFILE_KEYS_EN,
    MINOR_PROFILE_KEYS_ZH,
    REFUSED_LIBRARIES,
    SCHOOL_MARKERS,
    SCHOOL_MARKERS_EN,
    SCHOOL_MARKERS_ZH,
)

ROOT = Path(__file__).resolve().parents[1]
LEGAL_CONTROLS = {0x09, 0x0A, 0x0D}  # tab, newline, carriage return


def test_both_files_are_pure_ascii_and_contain_no_control_bytes():
    """Both backend/asset_guard.py and this test file must be strictly ASCII

    with no C0 control bytes except tab, newline, carriage return.
    """
    targets = [
        ROOT / "backend" / "asset_guard.py",
        ROOT / "tests" / "test_asset_guard_markers.py",
    ]
    for path in targets:
        assert path.exists(), f"missing required file: {path}"
        raw_bytes = path.read_bytes()
        assert len(raw_bytes) > 0, f"file is empty: {path}"
        for idx, b in enumerate(raw_bytes):
            assert b <= 0x7F, (
                f"{path.name}:{idx}: byte {hex(b)} exceeds ASCII range (U+007F)"
            )
            if b < 0x20:
                assert b in LEGAL_CONTROLS, (
                    f"{path.name}:{idx}: illegal control byte {hex(b)}"
                )
        # Explicitly ensure 0x08 (backspace) is absent
        assert 0x08 not in raw_bytes, f"{path.name}: contains literal backspace byte"


def test_neither_file_contains_trailing_whitespace():
    """Neither file may carry trailing whitespace."""
    targets = [
        ROOT / "backend" / "asset_guard.py",
        ROOT / "tests" / "test_asset_guard_markers.py",
    ]
    for path in targets:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line_num, line in enumerate(lines, 1):
            assert line == line.rstrip(), (
                f"{path.name}:{line_num}: has trailing whitespace"
            )


def test_marker_collections_non_empty_and_no_marker_empty():
    """No marker is empty, and no collection is empty (except REFUSED_LIBRARIES)."""
    collections = [
        ("SCHOOL_MARKERS_EN", SCHOOL_MARKERS_EN),
        ("SCHOOL_MARKERS_ZH", SCHOOL_MARKERS_ZH),
        ("SCHOOL_MARKERS", SCHOOL_MARKERS),
        ("MINOR_PROFILE_KEYS_EN", MINOR_PROFILE_KEYS_EN),
        ("MINOR_PROFILE_KEYS_ZH", MINOR_PROFILE_KEYS_ZH),
        ("MINOR_PROFILE_KEYS", MINOR_PROFILE_KEYS),
    ]
    for name, coll in collections:
        assert len(coll) > 0, f"collection {name} must not be empty"
        for idx, marker in enumerate(coll):
            assert isinstance(marker, str), f"{name}[{idx}] must be a string"
            assert len(marker) > 0, f"{name}[{idx}] must not be empty"

    # Task 1.2 fills refused source-library names; must be present and empty now.
    assert len(REFUSED_LIBRARIES) == 0, "REFUSED_LIBRARIES must be empty until task 1.2"


def test_escaped_school_markers_match_intended_text():
    """Every escaped Chinese school marker matches the text it was written for.

    Expected text is built from escape sequences / codepoints at test time.
    """
    expected_codepoints = {
        "\u5b66\u6821": [0x5B66, 0x6821],  # school
        "\u6559\u5ba4": [0x6559, 0x5BA4],  # classroom
        "\u5b66\u751f": [0x5B66, 0x751F],  # student
        "\u6821\u670d": [0x6821, 0x670D],  # school uniform
        "\u6821\u56ed": [0x6821, 0x56ED],  # campus
        "\u4e2d\u5b66": [0x4E2D, 0x5B66],  # middle / high school
        "\u9ad8\u4e2d": [0x9AD8, 0x4E2D],  # high school
        "\u521d\u4e2d": [0x521D, 0x4E2D],  # junior high school
        "\u5c0f\u5b66": [0x5C0F, 0x5B66],  # elementary school
        "\u5e7c\u513f\u56ed": [0x5E7C, 0x513F, 0x56ED],  # kindergarten
        "\u5973\u9ad8\u4e2d\u751f": [0x5973, 0x9AD8, 0x4E2D, 0x751F],  # female high school student
        "\u5973\u521d\u4e2d\u751f": [0x5973, 0x521D, 0x4E2D, 0x751F],  # female junior high student
        "\u5973\u4e2d\u5b66\u751f": [0x5973, 0x4E2D, 0x5B66, 0x751F],  # female secondary student
        "\u5973\u5b66\u751f": [0x5973, 0x5B66, 0x751F],  # female student
        "\u9ad8\u4e2d\u751f": [0x9AD8, 0x4E2D, 0x751F],  # high school student
        "\u521d\u4e2d\u751f": [0x521D, 0x4E2D, 0x751F],  # junior high student
        "\u4e2d\u5b66\u751f": [0x4E2D, 0x5B66, 0x751F],  # middle school student
        "\u5c0f\u5b66\u751f": [0x5C0F, 0x5B66, 0x751F],  # elementary school student
        "\u5973\u5c0f\u5b66\u751f": [0x5973, 0x5C0F, 0x5B66, 0x751F],  # female elementary student
        "\u8bfe\u684c": [0x8BFE, 0x684C],  # school desk
        "\u9ed1\u677f": [0x9ED1, 0x677F],  # blackboard
        "\u8bb2\u53f0": [0x8BB2, 0x53F0],  # podium / teacher platform
        "\u64cd\u573a": [0x64CD, 0x573A],  # playground / sports field
        "\u5bbf\u820d": [0x5BBF, 0x820D],  # dormitory
        "\u540c\u5b66": [0x540C, 0x5B66],  # classmate
        "\u6c34\u624b\u670d": [0x6C34, 0x624B, 0x670D],  # sailor uniform
        "\u8bfe\u5ba4": [0x8BFE, 0x5BA4],  # classroom
        "\u653e\u5b66": [0x653E, 0x5B66],  # after school
        "\u4e0a\u5b66": [0x4E0A, 0x5B66],  # attend school
        "\u6821\u820d": [0x6821, 0x820D],  # school building
        "\u6821\u95e8": [0x6821, 0x95E8],  # school gate
        "\u4e66\u5305": [0x4E66, 0x5305],  # schoolbag
        "jk\u5236\u670d": [0x6A, 0x6B, 0x5236, 0x670D],  # jk uniform
        "jc\u5236\u670d": [0x6A, 0x63, 0x5236, 0x670D],  # jc uniform
    }

    for marker in SCHOOL_MARKERS_ZH:
        assert marker in expected_codepoints, f"unexpected marker in SCHOOL_MARKERS_ZH: {repr(marker)}"
        expected_str = "".join(chr(cp) for cp in expected_codepoints[marker])
        assert marker == expected_str, f"marker {repr(marker)} did not match expected {repr(expected_str)}"

    assert len(SCHOOL_MARKERS_ZH) == len(expected_codepoints)


def test_escaped_minor_profile_keys_match_intended_text():
    """Every escaped Chinese minor-coded profile key matches intended text."""
    expected_codepoints = {
        "\u521d\u4e2d\u751f": [0x521D, 0x4E2D, 0x751F],  # junior high student
        "\u9ad8\u4e2d\u751f": [0x9AD8, 0x4E2D, 0x751F],  # high school student
        "\u4e2d\u5b66\u751f": [0x4E2D, 0x5B66, 0x751F],  # middle school student
        "\u5c0f\u5b66\u751f": [0x5C0F, 0x5B66, 0x751F],  # elementary school student
        "\u5973\u521d\u4e2d\u751f": [0x5973, 0x521D, 0x4E2D, 0x751F],  # female junior high student
        "\u5973\u9ad8\u4e2d\u751f": [0x5973, 0x9AD8, 0x4E2D, 0x751F],  # female high school student
        "\u5973\u4e2d\u5b66\u751f": [0x5973, 0x4E2D, 0x5B66, 0x751F],  # female secondary student
        "\u5973\u5c0f\u5b66\u751f": [0x5973, 0x5C0F, 0x5B66, 0x751F],  # female elementary student
        "\u5e7c\u5973": [0x5E7C, 0x5973],  # young girl
        "\u841d\u8389": [0x841D, 0x8389],  # loli
        "\u521d\u4e2d": [0x521D, 0x4E2D],  # junior high
        "\u9ad8\u4e2d": [0x9AD8, 0x4E2D],  # high school
    }

    for key in MINOR_PROFILE_KEYS_ZH:
        assert key in expected_codepoints, f"unexpected key in MINOR_PROFILE_KEYS_ZH: {repr(key)}"
        expected_str = "".join(chr(cp) for cp in expected_codepoints[key])
        assert key == expected_str, f"key {repr(key)} did not match expected {repr(expected_str)}"

    assert len(MINOR_PROFILE_KEYS_ZH) == len(expected_codepoints)


def test_incorrectly_escaped_marker_fails_matching():
    """An incorrectly escaped marker must fail to match the text it was written for."""
    intended_text = "".join(chr(cp) for cp in [0x5B66, 0x6821])  # school

    # Typo in codepoint fails match
    typo_marker = "\u5b67\u6821"
    assert typo_marker != intended_text
    assert typo_marker not in intended_text

    # Double-escaped string fails match
    double_escaped = chr(92) + "u5b66" + chr(92) + "u6821"
    assert double_escaped != intended_text
    assert double_escaped not in intended_text

    # Valid escaped marker matches
    valid_marker = "\u5b66\u6821"
    assert valid_marker == intended_text
    assert valid_marker in intended_text


def test_invented_fixtures_match_school_and_minor_markers():
    """Exercise invented fixture entries built from escapes at test time."""
    # Fixture in Chinese: theme text contains school marker
    school_glyph = "".join(chr(cp) for cp in [0x5B66, 0x6821])
    zh_fixture_theme = f"afternoon sunlight in {school_glyph} corridor"
    assert any(m in zh_fixture_theme for m in SCHOOL_MARKERS_ZH)

    # Fixture in English: tag contains school marker
    en_fixture_tag = "high school classroom"
    assert any(m in en_fixture_tag for m in SCHOOL_MARKERS_EN)

    # Fixture for minor profile key (English prefix)
    en_profile_key = "jc-petite-model"
    assert any(en_profile_key.startswith(k) or k in en_profile_key for k in MINOR_PROFILE_KEYS_EN)

    # Fixture for minor profile key (Chinese script)
    zh_profile_key = "".join(chr(cp) for cp in [0x521D, 0x4E2D, 0x751F]) + "-type-a"
    assert any(k in zh_profile_key for k in MINOR_PROFILE_KEYS_ZH)

    # Negative fixture: adult non-school entry matches nothing
    adult_fixture_theme = "luxurious executive office at night, glass desk and leather sofa"
    assert not any(m in adult_fixture_theme.lower() for m in SCHOOL_MARKERS)
    adult_profile_key = "office-director"
    assert not any(adult_profile_key.startswith(k) for k in MINOR_PROFILE_KEYS)


def test_asset_guard_source_code_uses_escapes():
    """Verify backend/asset_guard.py source code contains literal escape sequences."""
    guard_source = (ROOT / "backend" / "asset_guard.py").read_text(encoding="utf-8")
    escape_prefix = chr(92) + "u5b66"
    assert escape_prefix in guard_source, "source code must store Chinese markers as escape sequences"
