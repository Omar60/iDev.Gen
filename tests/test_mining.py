"""Tests for splitting a fused source entry into candidate rows.

Asserts that:
- No single row carries both a camera position and an act: each row's wording
  holds its own cut and none of the other slots' text.
- The wording is the cut byte for byte, and every row records the identifier
  the source gave the entry.
- A part the entry does not carry produces no row at all.
- The split reads the cut map only: a fused entry whose prose has an obvious
  comma to parse on still produces exactly the cuts the map named.
- An upload missing a cut stops before any row is produced, and the stop names
  every identifier the map is short of.

Every fixture here is invented, so the suite runs on a checkout with no source
directory present.
"""
from __future__ import annotations

import pytest

from backend.cut_map import CutMissingError, validate_cut_map
from backend.mining import identifier_for, split_fused_entries, split_fused_entry

CAMERA = "low angle from the foot of the bed"
ACT = "kneeling upright with both hands behind her head"
ROOM = "a narrow attic room with a sloped ceiling"

FUSED_ENTRY: dict[str, object] = {
    "identifier": "invented_fused_01",
    "prompt": f"{CAMERA}, {ACT}, {ROOM}",
    "tags": ["invented", "fixture"],
}

FULL_CUT: dict[str, str] = {"camera": CAMERA, "act": ACT, "room": ROOM}

# The same entry with no act in it: a camera and a room, and nothing that
# describes her body.
CAMERA_AND_ROOM_ENTRY: dict[str, object] = {
    "identifier": "invented_fused_02",
    "prompt": f"{CAMERA} in {ROOM}",
}
CAMERA_AND_ROOM_CUT: dict[str, str] = {"camera": CAMERA, "room": ROOM}


def test_no_single_row_carries_both_a_camera_position_and_an_act():
    """Each row holds its own cut and none of the other slots' text.

    The failure this is written against is a split that hands every row the
    whole fused string, which passes any test that only counts rows: three rows
    arrive, each of them is the entry, and a camera judged on that wording is
    judged on an act as well.
    """
    rows = split_fused_entry(FUSED_ENTRY, FULL_CUT)
    assert [row["slot"] for row in rows] == ["camera", "act", "room"]

    by_slot = {row["slot"]: row["wording"] for row in rows}
    for slot, wording in by_slot.items():
        for other_slot, other_wording in by_slot.items():
            if other_slot == slot:
                continue
            assert other_wording not in wording, (
                f"the {slot} row carries the {other_slot} text as well"
            )


def test_the_wording_is_the_cut_byte_for_byte_and_records_the_identifier():
    rows = split_fused_entry(FUSED_ENTRY, FULL_CUT)
    by_slot = {row["slot"]: row["wording"] for row in rows}
    assert by_slot == FULL_CUT
    assert all(row["source_identifier"] == "invented_fused_01" for row in rows)


def test_a_part_the_entry_does_not_carry_produces_no_row():
    """No act row, rather than an act row with an empty wording.

    An empty row would be drawn, composed and judged like any other, and its
    verdict would be about a blank.
    """
    rows = split_fused_entry(CAMERA_AND_ROOM_ENTRY, CAMERA_AND_ROOM_CUT)
    assert [row["slot"] for row in rows] == ["camera", "room"]
    assert not [row for row in rows if row["slot"] == "act"]
    assert all(row["wording"].strip() for row in rows)


def test_the_split_reads_the_map_and_never_the_prose():
    """A comma the map did not cut on is not a boundary.

    The fixture's prose has two obvious commas in it. The map names one cut
    covering the first two clauses and one covering the third, and that is what
    comes back: a parser would return three rows here, and the extra one would
    read like a row somebody wrote.
    """
    camera_and_act_together = f"{CAMERA}, {ACT}"
    cut = {"camera": camera_and_act_together, "room": ROOM}
    rows = split_fused_entry(FUSED_ENTRY, cut)
    assert [row["slot"] for row in rows] == ["camera", "room"]
    assert rows[0]["wording"] == camera_and_act_together


def test_a_cut_that_is_not_in_the_entry_is_refused_by_the_split():
    """The split inherits the cut map's substring rule rather than trusting it."""
    typed = dict(FULL_CUT, act="lying on her side with one knee raised")
    with pytest.raises(ValueError, match="not a substring"):
        split_fused_entry(FUSED_ENTRY, typed)


def test_an_upload_missing_a_cut_stops_with_every_shortfall_named():
    """The shortfall is collected across the whole upload before anything runs.

    Asserted with TWO entries missing, and on BOTH names. A split that checked
    each entry as it reached it would also raise here - the loop's own lookup
    raises - and a test with one entry missing, or one that only read the
    exception's type, would pass on it. What it would hand the operator is one
    identifier per run over a library of 55.
    """
    third = {"identifier": "invented_fused_03", "prompt": f"{CAMERA} in {ROOM}"}
    cut_map = validate_cut_map({"invented_fused_01": FULL_CUT})
    entries = [FUSED_ENTRY, CAMERA_AND_ROOM_ENTRY, third]

    with pytest.raises(CutMissingError) as excinfo:
        split_fused_entries(entries, cut_map)
    assert excinfo.value.identifiers == ["invented_fused_02", "invented_fused_03"]

    entries = [FUSED_ENTRY, CAMERA_AND_ROOM_ENTRY]

    whole_map = validate_cut_map(
        {"invented_fused_01": FULL_CUT, "invented_fused_02": CAMERA_AND_ROOM_CUT}
    )
    rows = split_fused_entries(entries, whole_map)
    assert [(row["source_identifier"], row["slot"]) for row in rows] == [
        ("invented_fused_01", "camera"),
        ("invented_fused_01", "act"),
        ("invented_fused_01", "room"),
        ("invented_fused_02", "camera"),
        ("invented_fused_02", "room"),
    ]


def test_an_entry_with_no_identifier_is_refused():
    """A mined row with nothing to attribute it to cannot be diagnosed later."""
    assert identifier_for({"id": "invented_fused_09"}) == "invented_fused_09"
    assert identifier_for({"key": "invented_fused_10"}) == "invented_fused_10"

    with pytest.raises(ValueError, match="carries no identifier"):
        identifier_for({"prompt": CAMERA})

    with pytest.raises(TypeError, match="must be a dict"):
        identifier_for([CAMERA])
