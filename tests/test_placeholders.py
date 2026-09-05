"""Tests for template holes in imported text.

Asserts that:
- A hole is filled from the entry's own field of that name.
- A hole nothing in the entry can fill leaves the entry unstored and reported
  by identifier and by placeholder name.
- The check reads the whole row, not the `place` field alone.
- A previously imported copy of a skipped entry does not survive the skip.
- No seed file on this machine carries an unresolved placeholder, and the
  failure names the row.

The corpus this change imports already shipped this defect once: three rooms
reached disk carrying `{pose}` and sent it to the sampler verbatim. So the
seed scan below is written against something that has happened, not against a
shape somebody guessed at.
"""
from __future__ import annotations

import json
from pathlib import Path

from backend.importer import (
    PLACEHOLDER_PATTERN,
    resolve_placeholders,
    unresolved_placeholders,
)

ROOT = Path(__file__).resolve().parents[1]


def test_a_hole_is_filled_from_the_entrys_own_field():
    entry = {
        "identifier": "invented_room_01",
        "prompt": "a sterile consultation room, {pose}, bright lighting",
        "pose": "bent forward over the examination table",
    }
    filled = resolve_placeholders(entry["prompt"], entry)
    assert filled == (
        "a sterile consultation room, bent forward over the examination "
        "table, bright lighting"
    )
    assert unresolved_placeholders(filled) == []


def test_a_hole_nothing_can_fill_is_left_standing_and_never_quietly_removed():
    """The braces stay put.

    A hole deleted is a sentence with a piece missing that still reads as
    English, which is worse than one that fails loudly: it enters the catalogue
    and gets judged as though somebody wrote it.
    """
    entry = {"identifier": "invented_room_02", "prompt": "a rehab room, {pose}"}
    for empty in ({}, {"pose": ""}, {"pose": "   "}, {"pose": ["bent forward"]}):
        candidate = dict(entry, **empty)
        left = resolve_placeholders(candidate["prompt"], candidate)
        assert left == "a rehab room, {pose}"
        assert unresolved_placeholders(left) == ["pose"]


def test_the_check_reads_the_whole_row_and_not_the_place_field_alone():
    """The three that shipped are all in `place`, which is why that is not the check."""
    row = {
        "key": "invented-room-03",
        "place": "a quiet room with a window",
        "label": "Clinic, {pose}",
        "guidance": {"notes": "works with {outfit}"},
    }
    # Sorted: the row is serialised with sorted keys, so the order the names
    # come back in is the field order and not the sentence order. Which names
    # are standing is the property; where they stood is not.
    assert sorted(unresolved_placeholders(row)) == ["outfit", "pose"]

    clean = {"key": "invented-room-04", "place": "a quiet room", "guidance": {}}
    assert unresolved_placeholders(clean) == []


def test_an_empty_brace_pair_is_not_a_placeholder():
    """`guidance` is always written, empty dict included.

    A pattern matching a bare `{}` would refuse every room that carries no
    guidance, which is most of them.
    """
    assert unresolved_placeholders({"guidance": {}}) == []
    assert PLACEHOLDER_PATTERN.findall("{} {1} { pose } {pose}") == ["pose"]


def test_no_seed_file_carries_an_unresolved_placeholder():
    """Every seed the data directory holds, named row by row when one fails.

    Scanned over what is actually on disk rather than over a fixed list: the
    imported seeds are untracked, so a fixed list would pass on a fresh clone
    for the same reason it would miss the defect on the machine that has them.
    A clone with nothing imported scans the tracked seeds and passes, which is
    the correct answer there - there is nothing yet to be wrong.
    """
    offenders: list[str] = []
    for seed in sorted((ROOT / "data").glob("*seed*.json")):
        try:
            rows = json.loads(seed.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(rows, list):
            rows = [rows]
        for row in rows:
            holes = unresolved_placeholders(row)
            if holes:
                row_id = ""
                if isinstance(row, dict):
                    row_id = str(row.get("key") or row.get("identifier") or "")
                offenders.append(f"{seed.name}: {row_id or row!r} carries {holes!r}")

    assert not offenders, "unresolved placeholders reached a seed:\n" + "\n".join(offenders)
