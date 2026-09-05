"""Splitting a fused source entry into separate candidate rows.

A `perspective_scenes` entry names a camera position, an act and a room in one
string. Stored whole it is a room seed row carrying a camera position, which is
a room that overrules the line's camera; and a good frame cannot be attributed
to a component, which is what the whole component matrix is for.

So the entry is cut into one row per part, each carrying the identifier the
source gave it. Where the cuts fall is read from the curated cut map and from
nowhere else - this module parses no prose and finds no boundaries of its own.
A heuristic over "leaning back against the mirror wall looking up into the
lens" produces fragments that read as rows and are not, and a wrong cut is not
a crash: it is a plausible row that enters the catalogue and spends a
measurement run on a fragment nobody wrote.
"""
from __future__ import annotations

from typing import Any, Iterable

from backend.cut_map import (
    CUT_SLOTS,
    CutMissingError,
    cut_for,
    missing_cuts,
    validate_cut_against_entry,
)
from backend.extractor import IDENTIFIER_KEYS


def identifier_for(entry: Any) -> str:
    """The identifier the source gave an entry, read from its own keys.

    The same keys the extractor and the guard read, imported rather than
    respelled: three walks over the same entries disagreeing about what an
    entry is called is how a row gets attributed to nothing.
    """
    if not isinstance(entry, dict):
        raise TypeError(f"A source entry must be a dict, got {type(entry).__name__}")
    for key in IDENTIFIER_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value
    raise ValueError(
        f"Fused entry carries no identifier under any of {list(IDENTIFIER_KEYS)!r}: "
        f"a mined row with nothing to attribute it to cannot be diagnosed later"
    )


def split_fused_entry(
    entry: Any,
    cut: dict[str, str],
    identifier: str | None = None,
) -> list[dict[str, str]]:
    """One row per part the cut names, in slot order, and no row for the rest.

    The wording is the cut byte for byte, which the cut map has already checked
    is a substring of this entry. Nothing here trims, joins or re-spaces it: a
    row that reaches the catalogue one character off is a row judged under text
    nobody wrote.

    A part the entry does not carry produces no row at all, rather than a row
    with an empty wording. An empty row would be drawn, composed and judged
    like any other, and its verdict would be about a blank.
    """
    key = identifier if identifier is not None else identifier_for(entry)
    validated = validate_cut_against_entry(key, cut, entry)
    return [
        {"slot": slot, "wording": validated[slot], "source_identifier": key}
        for slot in CUT_SLOTS
        if slot in validated
    ]


def split_fused_entries(
    entries: Iterable[Any],
    cut_map: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    """Split every fused entry, or stop before any row is produced.

    The shortfall is collected across the whole upload and raised once. Stopping
    at the first missing cut would hand the operator one identifier per run, and
    the corpus's fused library is 55 entries.

    Nothing is returned when anything is missing - not the rows that could have
    been cut. A partial mining is a catalogue that half describes a library, and
    the half nobody notices is the half that was skipped.
    """
    listed = list(entries)
    identifiers = [identifier_for(entry) for entry in listed]

    absent = missing_cuts(cut_map, identifiers)
    if absent:
        raise CutMissingError(absent)

    rows: list[dict[str, str]] = []
    for entry, key in zip(listed, identifiers):
        rows.extend(split_fused_entry(entry, cut_for(cut_map, key), identifier=key))
    return rows
