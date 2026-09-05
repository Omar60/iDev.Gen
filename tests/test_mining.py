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

import json
from pathlib import Path

import pytest

from backend.cut_map import CutMissingError, validate_cut_map
from backend.mining import (
    ActRequirementUndeterminedError,
    MINED_COMBINATIONS_FILE,
    MANNER_CANDID,
    NEEDS_NOTHING,
    NEEDS_SECOND_BODY,
    MANNER_POV,
    SOURCE_FAMILY_MANNERS,
    FamilyUndeclaredError,
    family_for,
    identifier_for,
    manner_for_family,
    combination_for,
    load_mined_combinations,
    needs_for_act,
    normalise_family,
    record_combinations,
    row_key,
    save_mined_combinations,
    second_body_families,
    split_fused_entries,
    validate_combination,
    split_fused_entry,
)

CAMERA = "low angle from the foot of the bed"
ACT = "kneeling upright with both hands behind her head"
ROOM = "a narrow attic room with a sloped ceiling"

FUSED_ENTRY: dict[str, object] = {
    "identifier": "invented_fused_01",
    "family": "facial POV",
    "prompt": f"{CAMERA}, {ACT}, {ROOM}",
    "tags": ["invented", "fixture"],
}

FULL_CUT: dict[str, str] = {"camera": CAMERA, "act": ACT, "room": ROOM}

# The same entry with no act in it: a camera and a room, and nothing that
# describes her body.
CAMERA_AND_ROOM_ENTRY: dict[str, object] = {
    "identifier": "invented_fused_02",
    "family": "fisheye POV",
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
    third = {
        "identifier": "invented_fused_03",
        "family": "fisheye POV",
        "prompt": f"{CAMERA} in {ROOM}",
    }
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


# ── 8.6 The manner is declared per source family ──────────────────────────


def test_every_mined_row_carries_the_manner_its_family_declares():
    """The manner comes from the family, and every row of an entry gets it.

    Both halves are asserted, because a split that wrote a manner onto the
    camera row alone would pass a test that only read `rows[0]`, and the act
    beside it would then reach the catalogue with no manner at all - drawn in
    whichever one the writer happened to be in.

    The two fixtures are deliberately from the two different declarations: a
    participant family and the unattended-camera one. A split that hard-wrote
    one manner is green on either fixture alone.
    """
    participant = split_fused_entry(FUSED_ENTRY, FULL_CUT)
    assert [row["manner"] for row in participant] == [MANNER_POV] * 3

    unattended = split_fused_entry(CAMERA_AND_ROOM_ENTRY, CAMERA_AND_ROOM_CUT)
    assert [row["manner"] for row in unattended] == [MANNER_CANDID] * 2


def test_the_participant_families_are_not_mined_into_directed():
    """The one answer the design rules out.

    `directed`'s instruction says somebody is photographing her, which a
    participant-camera entry contradicts, and a dead verdict inside it would
    mean the mismatch and not the row. Asserted over the whole declaration
    rather than over the three names, so a family added later is covered by
    this test on the day it is added.
    """
    assert MANNER_POV != "directed"
    assert "directed" not in SOURCE_FAMILY_MANNERS.values()
    assert SOURCE_FAMILY_MANNERS["fisheye_pov"] == MANNER_CANDID
    assert set(SOURCE_FAMILY_MANNERS) - {"fisheye_pov"} == {
        "rear_entry_pov",
        "stockings_pov",
        "facial_pov",
    }
    assert all(
        SOURCE_FAMILY_MANNERS[f] == MANNER_POV
        for f in set(SOURCE_FAMILY_MANNERS) - {"fisheye_pov"}
    )


def test_a_family_the_source_spells_differently_is_the_same_family():
    """The declaration is keyed on one spelling and the source writes several.

    `Rear-Entry POV`, `rear entry pov` and `rear_entry_pov` are one family. A
    lookup on the raw label would miss two of the three and refuse entries the
    map does declare.
    """
    assert normalise_family("Rear-Entry POV") == "rear_entry_pov"
    assert normalise_family("  fisheye   POV  ") == "fisheye_pov"
    assert manner_for_family("Rear-Entry POV") == MANNER_POV
    assert family_for({"category": "Facial POV"}) == "facial_pov"
    assert family_for({"identifier": "x"}) == ""


def test_an_entry_whose_family_declares_no_manner_is_refused_by_name():
    """No default, and the whole shortfall at once.

    Two entries are short here and BOTH names are asserted, which is the only
    observable difference between collecting the shortfall and stopping at the
    first one - the loop's own lookup raises either way. Same rule, and the
    same test shape, as the missing-cut shortfall above.

    The two cover the two halves of the failure: an entry naming a family
    nothing declares, and one naming no family at all. A refusal that only read
    the first would let the second through with whatever manner was default.
    """
    unknown_family = dict(FUSED_ENTRY, identifier="invented_fused_04", family="drone POV")
    no_family = {"identifier": "invented_fused_05", "prompt": f"{CAMERA} in {ROOM}"}
    cut_map = validate_cut_map(
        {
            "invented_fused_04": FULL_CUT,
            "invented_fused_05": CAMERA_AND_ROOM_CUT,
        }
    )

    with pytest.raises(FamilyUndeclaredError) as excinfo:
        split_fused_entries([unknown_family, no_family], cut_map)
    assert excinfo.value.identifiers == ["invented_fused_04", "invented_fused_05"]
    assert "drone_pov" in str(excinfo.value)
    assert "no family named" in str(excinfo.value)

    with pytest.raises(FamilyUndeclaredError):
        split_fused_entry(no_family, CAMERA_AND_ROOM_CUT)


# ── 8.7 A mined act declares what the photograph must provide ─────────────


def test_an_act_whose_wording_names_a_second_body_carries_the_requirement():
    """The catalogue's own reading, not a second one written here.

    `derive_multi_body` is what marks a room, and it is what marks an act: the
    rooms and the mined acts answering the same words two ways is the drift
    this import is written against.
    """
    assert needs_for_act("kneeling in front of him with both hands on his knees") == NEEDS_SECOND_BODY
    assert needs_for_act("standing between two nurses at the end of the bed") == NEEDS_SECOND_BODY
    assert needs_for_act(ACT) == NEEDS_NOTHING


def test_an_act_of_a_floored_family_carries_the_requirement_its_wording_omits():
    """The floor is the half a wording-only reading cannot see.

    The fixture act is pure geometry - nobody but her is in the sentence - and
    it still carries the requirement, because the family it came from puts the
    camera in a participant's hands and a participant is a body that is not
    hers.

    Asserted through the SPLIT and not only through the reading, because a
    derivation that is right in isolation and never reaches the row is the same
    as no derivation at all.
    """
    assert needs_for_act(ACT) == NEEDS_NOTHING
    assert needs_for_act(ACT, "facial POV") == NEEDS_SECOND_BODY

    rows = split_fused_entry(FUSED_ENTRY, FULL_CUT)
    act = [row for row in rows if row["slot"] == "act"][0]
    assert act["wording"] == ACT
    assert act["needs"] == NEEDS_SECOND_BODY


def test_the_floor_is_read_off_the_manner_declaration():
    """One list, not two.

    A family is mined into `pov` because its camera is held by a participant,
    which is the same fact as "there is a second body in this photograph". A
    second list saying so is a second calculation of one fact, and the day the
    two disagree an act is floored in one place and drawn in the other.
    """
    assert second_body_families() == {
        f for f, m in SOURCE_FAMILY_MANNERS.items() if m == MANNER_POV
    }
    assert "fisheye_pov" not in second_body_families()


def test_an_uncertain_reading_sets_the_requirement_rather_than_omitting_it():
    """The two errors are not symmetrical.

    None of these words is proof of a second body - `derive_multi_body` leaves
    them out on purpose, because over a room's prose they are as likely to be
    her. Over an act they are uncertain, and uncertain sets it: set in error
    only narrows a pool nobody switched on, while omitted in error deals a
    two-body act into a single-body photograph.
    """
    for wording in (
        "leaning back while someone steadies her shoulder",
        "her wrists held above her head by another pair of hands",
        "kneeling with his hand flat on the small of her back",
    ):
        assert needs_for_act(wording) == NEEDS_SECOND_BODY, wording


def test_a_camera_or_a_room_row_declares_no_requirement():
    """`needs` is the act's field.

    A camera or a room carrying `him` would narrow a pool for a requirement
    nothing about it asks for - the unattended fixture's room is a place, and a
    place needs nobody.
    """
    rows = split_fused_entry(CAMERA_AND_ROOM_ENTRY, CAMERA_AND_ROOM_CUT)
    assert [row["slot"] for row in rows] == ["camera", "room"]
    assert not any("needs" in row for row in rows)


def test_an_unfloored_family_leaves_a_single_body_act_drawable():
    """The floor is per family and not a blanket.

    `fisheye_pov`'s camera is a device in the room, so its acts are read on
    their wording alone. A derivation that floored everything would pass every
    other test in this section and would quietly empty the single-body pool.
    """
    entry = dict(CAMERA_AND_ROOM_ENTRY, identifier="invented_fused_06")
    entry["prompt"] = f"{CAMERA}, {ACT}, {ROOM}"
    rows = split_fused_entry(entry, FULL_CUT)
    act = [row for row in rows if row["slot"] == "act"][0]
    assert act["manner"] == MANNER_CANDID
    assert act["needs"] == NEEDS_NOTHING


# ── 8.8 An act whose requirement cannot be read is reported, not written ──


def test_an_act_with_no_word_in_it_is_reported_and_nothing_is_written():
    """Nothing written, and the entry named.

    The reachable case is a cut of punctuation: `", "` typed into the map where
    the clause was meant to go is a substring of the entry, non-empty, and
    passes every check the cut map makes. It arrives here as an act nobody
    wrote, and a requirement guessed for it is the error that deals a two-body
    act into a single-body photograph.

    Asserted over TWO entries and on BOTH names, for the missing-cut shortfall's
    reason: stopping at the first one raises the same exception type, and a
    test that read only the type would pass on a report that hands the operator
    one identifier per re-run.
    """
    punctuation = ", "
    first = {
        "identifier": "invented_fused_07",
        "family": "fisheye POV",
        "prompt": f"{CAMERA}{punctuation}{ROOM}",
    }
    second = dict(first, identifier="invented_fused_08")
    cut = {"camera": CAMERA, "act": punctuation, "room": ROOM}
    cut_map = validate_cut_map({"invented_fused_07": cut, "invented_fused_08": cut})

    with pytest.raises(ActRequirementUndeterminedError) as excinfo:
        split_fused_entries([first, second], cut_map)
    assert excinfo.value.identifiers == ["invented_fused_07", "invented_fused_08"]
    assert "invented_fused_07" in str(excinfo.value)
    assert "invented_fused_08" in str(excinfo.value)


def test_the_unreadable_act_takes_the_whole_upload_with_it():
    """No rows come back - not even the readable entry's.

    A partial mining is a catalogue that half describes a library, which is the
    rule the missing-cut shortfall already sets. The failure this is written
    against is a report that skips the bad entry and returns the rest, leaving
    a source entry mined into two rows of three with nothing saying so.
    """
    bad = {
        "identifier": "invented_fused_09",
        "family": "fisheye POV",
        "prompt": f"{CAMERA}, {ROOM}",
    }
    cut_map = validate_cut_map(
        {
            "invented_fused_01": FULL_CUT,
            "invented_fused_09": {"camera": CAMERA, "act": ", ", "room": ROOM},
        }
    )
    with pytest.raises(ActRequirementUndeterminedError) as excinfo:
        split_fused_entries([FUSED_ENTRY, bad], cut_map)
    assert excinfo.value.identifiers == ["invented_fused_09"]


def test_a_floored_family_does_not_rescue_an_unreadable_act():
    """The floor says what a family always needs, not what a row needs.

    A participant family floors every act it carries, so a check written after
    the floor never runs for three families of the four - and those are exactly
    the ones whose acts matter most, because they are the ones that carry the
    second body.
    """
    with pytest.raises(ActRequirementUndeterminedError):
        needs_for_act(", ", "facial POV", "invented_fused_10")
    with pytest.raises(ActRequirementUndeterminedError):
        needs_for_act("", "fisheye POV", "invented_fused_11")


# ── 8.10 The recorded combination is keys, in a tracked file ──────────────

TRACKED_COMBINATIONS = Path(__file__).resolve().parents[1] / "data" / MINED_COMBINATIONS_FILE


def test_the_tracked_combination_file_carries_no_prose():
    """Keys, and nothing that could be a wording.

    The file is TRACKED and the wording it would otherwise carry is source
    prose that may not be committed - the same split the room verdicts already
    live under. Every entry is put through the validator the loader uses, so a
    combination pasted in by hand with a clause where the key goes fails here
    rather than at the first compose.

    The falsifiable half is the fixture below, not this scan: today the file is
    empty on purpose, and a scan of an empty file is a test that cannot fail on
    its own.
    """
    loaded = json.loads(TRACKED_COMBINATIONS.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    for identifier, entry in loaded.items():
        validate_combination(entry, identifier)


def test_a_wording_where_a_row_key_belongs_is_refused():
    """The failure the tracked file is written against.

    A combination built out of words instead of rows goes on reproducing the
    old photograph the day one of its rows is reworded, and nothing says the row
    moved - which is the whole reason the combination is stored as references.
    """
    with pytest.raises(ValueError, match="not a row key"):
        validate_combination({"camera": "cam-01", "act": ACT, "room": "room-01"})
    with pytest.raises(ValueError, match="does not cut"):
        validate_combination({"camera": "cam-01", "wardrobe": "worn-01"})
    with pytest.raises(ValueError, match="records no rows"):
        validate_combination({})
    assert validate_combination({"camera": "cam-01", "room": "room-01"}) == {
        "camera": "cam-01",
        "room": "room-01",
    }


def test_a_combination_survives_its_rows_being_absent(tmp_path):
    """A reference, not a foreign key.

    The keys here name rows nothing in the catalogue carries, and the
    combination comes back whole. A loader that checked its rows and dropped
    what it could not find would delete the only record of what the source entry
    was - and it would do it silently, on the day somebody retired one row.
    """
    (tmp_path / MINED_COMBINATIONS_FILE).write_text(
        json.dumps(
            {
                "invented_fused_01": {
                    "camera": "mined-cam-01",
                    "act": "mined-act-01",
                    "room": "mined-room-01",
                }
            }
        ),
        encoding="utf-8",
    )
    loaded = load_mined_combinations(data_dir=tmp_path)
    assert loaded == {
        "invented_fused_01": {
            "camera": "mined-cam-01",
            "act": "mined-act-01",
            "room": "mined-room-01",
        }
    }


def test_no_combination_file_reads_as_no_combinations(tmp_path):
    """A fresh checkout has mined nothing, which is a real state and not an error."""
    assert load_mined_combinations(data_dir=tmp_path) == {}


# ── 8.12 The combination each source entry was split into ─────────────────


def test_every_mined_row_carries_the_key_its_combination_names_it_by():
    """The key is derived at the split, not handed out at storage time.

    The combination has to name the rows it was split into, so a key that only
    existed once the rows were written would leave the split with nothing to
    record. Derived from the identifier and the slot, so re-mining the same
    library twice names the same rows.
    """
    rows = split_fused_entry(FUSED_ENTRY, FULL_CUT)
    assert [row["key"] for row in rows] == [
        "mined-invented_fused_01-camera",
        "mined-invented_fused_01-act",
        "mined-invented_fused_01-room",
    ]
    assert row_key("Invented Fused 01", "act") == "mined-invented_fused_01-act"


def test_the_combination_records_only_the_parts_the_entry_carried():
    """What was mined, not what a full entry would have had.

    The camera-and-room fixture has no act in it, and its combination has no act
    key. A record that carried three slots regardless would name a row nothing
    ever wrote, and 8.13 would report the combination broken forever.
    """
    rows = split_fused_entry(CAMERA_AND_ROOM_ENTRY, CAMERA_AND_ROOM_CUT)
    assert combination_for(rows) == {
        "camera": "mined-invented_fused_02-camera",
        "room": "mined-invented_fused_02-room",
    }


def test_the_record_is_grouped_from_the_rows_the_split_produced(tmp_path):
    """One walk, and a round trip through the file.

    `record_combinations` groups the rows themselves rather than re-reading the
    entries: a second walk deciding what a combination holds is a second
    calculation of one fact, and the day it disagrees the record names rows the
    split never produced.
    """
    cut_map = validate_cut_map(
        {"invented_fused_01": FULL_CUT, "invented_fused_02": CAMERA_AND_ROOM_CUT}
    )
    rows = split_fused_entries([FUSED_ENTRY, CAMERA_AND_ROOM_ENTRY], cut_map)
    recorded = record_combinations(rows)
    assert set(recorded) == {"invented_fused_01", "invented_fused_02"}
    assert recorded["invented_fused_01"]["act"] == "mined-invented_fused_01-act"

    save_mined_combinations(recorded, data_dir=tmp_path)
    assert load_mined_combinations(data_dir=tmp_path) == recorded

    written = (tmp_path / MINED_COMBINATIONS_FILE).read_text(encoding="utf-8")
    for row in rows:
        assert row["wording"] not in written, "the record wrote source prose"


def test_two_entries_that_build_one_row_key_stop_the_split():
    """A collision merges two photographs into one row, silently.

    `invented-fused-01` and `invented_fused_01` are different identifiers and one
    key. Stored, the second entry's rows overwrite the first's and its
    combination overwrites the other, and nothing anywhere says a photograph was
    lost.
    """
    twin = dict(FUSED_ENTRY, identifier="invented-fused-01")
    cut_map = validate_cut_map(
        {"invented_fused_01": FULL_CUT, "invented-fused-01": FULL_CUT}
    )
    with pytest.raises(ValueError, match="same row keys"):
        split_fused_entries([FUSED_ENTRY, twin], cut_map)


def test_writing_the_record_replaces_the_file_rather_than_merging_it(tmp_path):
    """A re-cut entry leaves no stale record behind.

    Merging would keep the combination the previous cut produced, and the
    operator would then hold two records of one photograph with no way to tell
    which cut the frames came from. The break that merges passed every other
    test in this file, which is why this one exists.
    """
    save_mined_combinations(
        {"invented_fused_01": {"camera": "mined-a-camera", "act": "mined-a-act"}},
        data_dir=tmp_path,
    )
    save_mined_combinations(
        {"invented_fused_02": {"camera": "mined-b-camera", "room": "mined-b-room"}},
        data_dir=tmp_path,
    )
    assert load_mined_combinations(data_dir=tmp_path) == {
        "invented_fused_02": {"camera": "mined-b-camera", "room": "mined-b-room"}
    }
