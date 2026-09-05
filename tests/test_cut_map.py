"""Tests for the curated cut map that splits fused source entries.

Asserts that:
- Every cut is a substring of the source entry it claims, and a fragment that
  is not found in that entry is refused by slot name.
- A cut spanning two of the entry's fields is refused rather than accepted
  against a joined string that exists nowhere in the source.
- A source identifier missing from the map stops rather than being guessed at,
  and the stop names the identifier.
- An omitted slot is how the map says the entry carries no such part, while an
  empty or misspelt slot is refused.
- Round-tripping through the file keeps the cuts byte for byte, and a
  destination this repository would track is refused.
- This test file and its module carry no non-English glyph.

Every fixture here is invented. The source strings are written in escape
sequences so no tracked file carries a non-English glyph, and the tests run on
a checkout with no source directory present.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.cut_map import (
    CUT_SLOTS,
    CutMissingError,
    cut_for,
    load_cut_map,
    missing_cuts,
    resolve_cut_map_path,
    save_cut_map,
    validate_cut_against_entry,
    validate_cut_entry,
    validate_cut_map,
)

ROOT = Path(__file__).resolve().parents[1]
LEGAL_CONTROLS = {0x09, 0x0A, 0x0D}  # tab, newline, carriage return

# An invented fused entry in the shape the source libraries use: one clause of
# English naming a camera direction, a body geometry and a piece of furniture
# together, plus a second field and a nested list, because a cut has to be
# found wherever the entry actually keeps its prose.
FUSED_ENTRY: dict[str, object] = {
    "identifier": "invented_fused_01",
    "prompt": (
        "low angle from the foot of the bed, "
        "kneeling upright with both hands behind her head, "
        "a narrow attic room with a sloped ceiling"
    ),
    "note": "shot on a tripod at ankle height",
    "tags": ["invented", "fixture"],
    "variants": [{"line": "overhead looking straight down"}],
}

GOOD_CUT: dict[str, str] = {
    "camera": "low angle from the foot of the bed",
    "act": "kneeling upright with both hands behind her head",
    "room": "a narrow attic room with a sloped ceiling",
}


def test_every_cut_is_a_substring_of_its_source_entry():
    """A cut found in the entry passes; one that is not is refused by slot name."""
    assert validate_cut_against_entry("invented_fused_01", GOOD_CUT, FUSED_ENTRY) == GOOD_CUT

    # A cut may be found in any of the entry's strings, including a nested one.
    nested = {"camera": "overhead looking straight down"}
    assert validate_cut_against_entry("invented_fused_01", nested, FUSED_ENTRY) == nested

    # A fragment nobody wrote: plausible English, absent from the entry.
    typed = dict(GOOD_CUT, act="lying on her side with one knee raised")
    with pytest.raises(ValueError, match="not a substring") as excinfo:
        validate_cut_against_entry("invented_fused_01", typed, FUSED_ENTRY)
    assert "'act'" in str(excinfo.value)
    assert "invented_fused_01" in str(excinfo.value)


def test_a_cut_spanning_two_fields_is_refused():
    """The entry's strings are searched whole, never joined.

    Joining invents a boundary. A cut running from the end of `prompt` into the
    start of `note` would then pass as a substring of a string that exists
    nowhere in the source, and the row it produced would be attributed to an
    author who never wrote it.
    """
    spanning = {"camera": "a sloped ceilingshot on a tripod at ankle height"}
    with pytest.raises(ValueError, match="not a substring"):
        validate_cut_against_entry("invented_fused_01", spanning, FUSED_ENTRY)


def test_an_entry_missing_from_the_map_stops_the_import():
    """A cut is never guessed: the lookup raises and names the identifier."""
    cut_map = validate_cut_map({"invented_fused_01": GOOD_CUT})

    assert cut_for(cut_map, "invented_fused_01") == GOOD_CUT

    with pytest.raises(CutMissingError) as excinfo:
        cut_for(cut_map, "invented_fused_02")
    assert "invented_fused_02" in str(excinfo.value)
    assert excinfo.value.identifiers == ["invented_fused_02"]

    # CutMissingError is a ValueError, so a caller catching the import's own
    # refusal type stops on this too rather than carrying on with no cut.
    assert isinstance(excinfo.value, ValueError)


def test_the_whole_shortfall_is_named_at_once():
    """Every identifier the map is short of, in the order given, deduplicated."""
    cut_map = validate_cut_map({"invented_fused_01": GOOD_CUT})
    asked = [
        "invented_fused_01",
        "invented_fused_03",
        "invented_fused_02",
        "invented_fused_03",
    ]
    missing = missing_cuts(cut_map, asked)
    assert missing == ["invented_fused_03", "invented_fused_02"]

    message = str(CutMissingError(missing))
    assert "invented_fused_03" in message
    assert "invented_fused_02" in message
    assert "2 fused entries" in message


def test_an_omitted_slot_is_how_the_map_says_nothing_is_cut():
    """Absent and None are the same answer; empty and misspelt are refused."""
    camera_and_room_only = {
        "camera": "low angle from the foot of the bed",
        "room": "a narrow attic room with a sloped ceiling",
    }
    assert validate_cut_entry(camera_and_room_only) == camera_and_room_only
    assert "act" not in validate_cut_entry(dict(camera_and_room_only, act=None))

    with pytest.raises(ValueError, match="is empty"):
        validate_cut_entry(dict(camera_and_room_only, act="   "))

    with pytest.raises(ValueError, match="does not cut"):
        validate_cut_entry(dict(camera_and_room_only, camrea="low angle"))

    with pytest.raises(ValueError, match="cuts nothing"):
        validate_cut_entry({})

    with pytest.raises(ValueError, match="must be a string"):
        validate_cut_entry({"camera": ["low angle"]})


def test_validate_cut_map_rejects_a_bad_key_or_a_bad_entry():
    """The map is keyed by source identifier and validates every entry."""
    with pytest.raises(TypeError, match="must be a dict"):
        validate_cut_map([("invented_fused_01", GOOD_CUT)])

    with pytest.raises(ValueError, match="non-empty source identifiers"):
        validate_cut_map({"  ": GOOD_CUT})

    with pytest.raises(ValueError, match="invented_fused_01"):
        validate_cut_map({"invented_fused_01": {"act": ""}})


def test_the_cuts_survive_the_file_byte_for_byte(tmp_path):
    """Round-trip keeps the source prose exactly, and the file stays ASCII.

    The source strings are non-English, so the written file has to escape them:
    a cut that came back one normalisation different would no longer be a
    substring of the entry it was cut from.
    """
    # Written in escape sequences, never as glyphs: this file is tracked, and
    # the same rule that keeps the refusal markers escaped applies here.
    source_prose = "\u5bbf\u820d\u306e\u7a93\u8fba \u4f4e\u30a2\u30f3\u30b0\u30eb"
    cut_map = {
        "invented_fused_01": GOOD_CUT,
        "invented_fused_04": {"camera": source_prose},
    }
    destination = tmp_path / "beside-the-source" / "cuts.json"
    save_cut_map(cut_map, destination)

    raw = destination.read_bytes()
    assert all(b < 0x80 for b in raw), "the cut map file must be pure ASCII"

    loaded = load_cut_map(destination)
    assert loaded["invented_fused_04"]["camera"] == source_prose
    assert loaded["invented_fused_01"] == GOOD_CUT

    # The entry it was cut from still contains it, which is the property the
    # round-trip exists to keep.
    entry = {"identifier": "invented_fused_04", "prompt": f"x {source_prose} y"}
    assert validate_cut_against_entry("invented_fused_04", loaded["invented_fused_04"], entry)


def test_loading_refuses_a_missing_file_and_invalid_json(tmp_path):
    with pytest.raises(FileNotFoundError, match="Cut map file not found"):
        load_cut_map(tmp_path / "absent.json")

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON in cut map"):
        load_cut_map(broken)


def test_saving_refuses_a_destination_this_repository_would_track():
    """The cuts are source prose, so a tracked path is refused by the code.

    Asked of the same repository a commit would consult, not of a second list
    kept here that would drift from .gitignore.
    """
    tracked = ROOT / "backend" / "invented-cuts-should-never-live-here.json"
    assert not tracked.exists()
    with pytest.raises(ValueError, match="would be tracked by git"):
        save_cut_map({"invented_fused_01": GOOD_CUT}, tracked)
    assert not tracked.exists(), "a refused save must write nothing"


def test_the_cut_map_path_sits_beside_the_source_material(tmp_path):
    """Resolved against the operator's source directory, never guessed."""
    source_dir = tmp_path / "AmazingDraw v1.2"
    source_dir.mkdir()
    resolved = resolve_cut_map_path(source_dir, "cuts.json")
    assert resolved == source_dir / "cuts.json"

    with pytest.raises(ValueError):
        resolve_cut_map_path(source_dir, Path(tmp_path / "elsewhere.json"))

    with pytest.raises(NotADirectoryError):
        resolve_cut_map_path(source_dir / "absent", "cuts.json")


def test_the_slots_are_the_three_the_catalogue_holds():
    """A fourth slot is a fourth component, which this change does not add."""
    assert CUT_SLOTS == ("camera", "act", "room")


def test_this_test_file_and_its_module_carry_no_non_english_glyph():
    """No tracked file in this change carries a non-English character."""
    for path in (ROOT / "backend" / "cut_map.py", Path(__file__)):
        raw = path.read_bytes()
        assert all(b < 0x80 for b in raw), f"{path.name} must be pure ASCII"
        text = raw.decode("ascii")
        illegal = {ord(c) for c in text if ord(c) < 0x20 and ord(c) not in LEGAL_CONTROLS}
        assert not illegal, f"{path.name} carries control bytes {sorted(illegal)}"
