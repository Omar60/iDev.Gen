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

import re
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


# The manner a mined row is shot in, declared ONCE PER SOURCE FAMILY and never
# decided per row. The source's four fused families are three whose camera is
# held by somebody in the photograph and one whose camera is a device left in
# the room.
#
# The participant families cannot enter `directed`: its instruction says
# somebody is photographing her, which those entries contradict, and a dead
# verdict would then mean the mismatch rather than the row. They enter `pov`,
# which is `directed` inherited with nothing but its identity changed (8.5), so
# no instruction prose is invented before anything is measured.
#
# The fisheye family needs no new manner: an unattended camera in the room is
# what `candid` already describes.
MANNER_POV: str = "pov"
MANNER_CANDID: str = "candid"

SOURCE_FAMILY_MANNERS: dict[str, str] = {
    "rear_entry_pov": MANNER_POV,
    "stockings_pov": MANNER_POV,
    "facial_pov": MANNER_POV,
    "fisheye_pov": MANNER_CANDID,
}

# The keys an entry may name its own family under. Same shape as the
# extractor's `IDENTIFIER_KEYS` and for the same reason: the source spells one
# fact several ways, and a walk that knows one spelling attributes a row to
# nothing.
SOURCE_FAMILY_KEYS: tuple[str, ...] = ("source_family", "family", "category")

_NOT_ALNUM = re.compile(r"[^a-z0-9]+")


class FamilyUndeclaredError(ValueError):
    """Raised when a fused entry's family declares no manner.

    Covers both halves of the same failure - an entry naming no family at all,
    and one naming a family nothing declares - because the operator's next move
    is the same either way: write the declaration. Carries the whole shortfall
    so a library of 55 is not one identifier per re-run, which is the rule
    `CutMissingError` already sets.

    A ValueError so a caller catching the import's refusal type stops on it too.
    """

    def __init__(self, shortfall: Iterable[tuple[str, str]]) -> None:
        self.shortfall = [(str(i), str(f)) for i, f in shortfall]
        self.identifiers = [i for i, _ in self.shortfall]
        listed = ", ".join(
            f"{i!r} (family {f!r})" if f else f"{i!r} (no family named)"
            for i, f in self.shortfall
        )
        count = len(self.shortfall)
        super().__init__(
            f"No manner declared for {count} fused "
            f"{'entry' if count == 1 else 'entries'}: {listed}. A mined row's "
            f"manner is declared per source family, never guessed per row."
        )


def normalise_family(name: Any) -> str:
    """A family name reduced to the spelling the declaration is keyed on.

    The source writes its families as labels - `rear-entry POV`, `Fisheye POV` -
    and a declaration keyed on one capitalisation is a declaration that misses
    the same family written the other way. Lowercased, with every run of
    non-alphanumerics becoming a single underscore.
    """
    if not isinstance(name, str):
        return ""
    return _NOT_ALNUM.sub("_", name.strip().lower()).strip("_")


def family_for(entry: Any) -> str:
    """The family an entry names, normalised, or "" when it names none."""
    if not isinstance(entry, dict):
        raise TypeError(f"A source entry must be a dict, got {type(entry).__name__}")
    for key in SOURCE_FAMILY_KEYS:
        family = normalise_family(entry.get(key))
        if family:
            return family
    return ""


def manner_for_family(family: Any, identifier: str = "") -> str:
    """The manner a family's rows are mined into, or refuse the entry.

    Never defaulted. A family nothing declares is a family somebody added to
    the source since this map was written, and guessing `directed` for it is
    the one answer the design rules out.
    """
    normalised = normalise_family(family)
    manner = SOURCE_FAMILY_MANNERS.get(normalised)
    if manner is None:
        raise FamilyUndeclaredError([(identifier, normalised)])
    return manner


def split_fused_entry(
    entry: Any,
    cut: dict[str, str],
    identifier: str | None = None,
    source_family: str | None = None,
) -> list[dict[str, str]]:
    """One row per part the cut names, in slot order, and no row for the rest.

    The wording is the cut byte for byte, which the cut map has already checked
    is a substring of this entry. Nothing here trims, joins or re-spaces it: a
    row that reaches the catalogue one character off is a row judged under text
    nobody wrote.

    A part the entry does not carry produces no row at all, rather than a row
    with an empty wording. An empty row would be drawn, composed and judged
    like any other, and its verdict would be about a blank.

    Every row carries the manner its family declares. The manner is read once
    per entry, so the rows of one entry cannot disagree about which manner they
    were mined into - a camera judged in one manner and the act beside it in
    another is two matrices describing one photograph.
    """
    key = identifier if identifier is not None else identifier_for(entry)
    family = normalise_family(source_family) if source_family else family_for(entry)
    manner = manner_for_family(family, key)
    validated = validate_cut_against_entry(key, cut, entry)
    return [
        {
            "slot": slot,
            "wording": validated[slot],
            "source_identifier": key,
            "manner": manner,
        }
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

    The family shortfall is collected the same way and for the same reason: an
    entry whose family declares no manner stops the upload, and every such
    entry is named at once.
    """
    listed = list(entries)
    identifiers = [identifier_for(entry) for entry in listed]

    absent = missing_cuts(cut_map, identifiers)
    if absent:
        raise CutMissingError(absent)

    families = [family_for(entry) for entry in listed]
    undeclared = [
        (key, family)
        for key, family in zip(identifiers, families)
        if family not in SOURCE_FAMILY_MANNERS
    ]
    if undeclared:
        raise FamilyUndeclaredError(undeclared)

    rows: list[dict[str, str]] = []
    for entry, key, family in zip(listed, identifiers, families):
        rows.extend(
            split_fused_entry(
                entry, cut_for(cut_map, key), identifier=key, source_family=family
            )
        )
    return rows
