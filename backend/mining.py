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
from backend.importer import derive_multi_body


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


# What a mined act needs before it can be photographed, in the vocabulary the
# act catalogue already uses: `needs` is '' for pure geometry and 'him' for an
# act with a second person in it (`backend/main.py:298`).
NEEDS_SECOND_BODY: str = "him"
NEEDS_NOTHING: str = ""

# Words that might be a second body and might be her. `derive_multi_body`
# leaves these out on purpose - over a ROOM's prose they are as likely to be
# the subject as somebody else, and a rule wrong half the time trains the
# operator to switch the gate off. An ACT's wording is the other case: it
# describes what is happening in the photograph, so a hand that is not hers or
# a "someone" is a second body far more often than not.
#
# They do not decide anything on their own - they make the reading UNCERTAIN,
# and an uncertain reading sets the requirement. The two errors are not
# symmetrical: set in error, it only narrows the pool of a run that never
# switched the second body on; omitted in error, it deals a two-body act into a
# single-body photograph, which is a failure this repo has already shot.
UNCERTAIN_SECOND_BODY_WORDS: tuple[str, ...] = (
    "someone", "somebody", "another", "partner", "his", "they", "them", "their",
)

_UNCERTAIN_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in UNCERTAIN_SECOND_BODY_WORDS) + r")\b"
)



class ActRequirementUndeterminedError(ValueError):
    """Raised when a mined act's requirement cannot be read at all.

    Carries the whole shortfall for `CutMissingError`'s reason: one identifier
    per re-run over a library of 55 is not a report.
    """

    def __init__(self, shortfall: Iterable[tuple[str, str]]) -> None:
        self.shortfall = [(str(i), str(w)) for i, w in shortfall]
        self.identifiers = [i for i, _ in self.shortfall]
        listed = ", ".join(f"{i!r} (act {w!r})" for i, w in self.shortfall)
        count = len(self.shortfall)
        super().__init__(
            f"The second-body requirement cannot be determined for {count} mined "
            f"{'act' if count == 1 else 'acts'}: {listed}. Nothing was written. "
            f"An act with no word in it has no reading to floor, and a guessed "
            f"requirement is a two-body act dealt into a single-body photograph."
        )


# A wording the reading can work on holds at least one run of letters. A cut of
# punctuation - ", " typed into the map where the clause was meant to go - is a
# substring of the entry and passes every check the cut map makes, and it
# reaches this module as an act nobody wrote.
# ponytail: a letters check, not a grammar. If a real act ever arrives written
# only in digits, the upgrade is to read the source's own language field.
_HAS_A_WORD = re.compile("[A-Za-z]{2,}")


def second_body_families() -> frozenset[str]:
    """The families whose acts always involve a second body.

    Read OFF the manner declaration rather than listed again beside it: a
    family is mined into `pov` precisely because its camera is held by a
    participant, and a participant is a body in the photograph that is not
    hers. Two lists saying that would be two calculations of one fact, which is
    the bug this repo has now found several times.
    """
    return frozenset(
        family
        for family, manner in SOURCE_FAMILY_MANNERS.items()
        if manner == MANNER_POV
    )


def needs_for_act(
    wording: str,
    source_family: str = "",
    identifier: str = "",
) -> str:
    """What a photograph must provide before this mined act can be drawn.

    Three readings, in the order that matters, and any one of them is enough:

    - the family floor, so an act of a participant-camera family carries the
      requirement even where its own wording never spells the second body out;
    - the catalogue's own reading of the wording, `derive_multi_body`, imported
      rather than respelled so the rooms and the mined acts cannot drift into
      two answers about the same words;
    - an uncertain word, which SETS the requirement rather than omitting it.

    '' is returned only when the family does not floor it, the catalogue's
    reading finds nobody, and nothing in the wording is ambiguous.

    A wording with no word in it is refused before any of the three run,
    INCLUDING for a floored family: the floor says what a family always needs,
    not what a row needs, and there is no row here to need it. Reported and not
    written, because a guessed requirement is the error that deals a two-body
    act into a single-body photograph.
    """
    text = wording or ""
    if not _HAS_A_WORD.search(text):
        raise ActRequirementUndeterminedError([(identifier, text)])
    if normalise_family(source_family) in second_body_families():
        return NEEDS_SECOND_BODY
    if derive_multi_body(text):
        return NEEDS_SECOND_BODY
    if _UNCERTAIN_PATTERN.search(text.lower()):
        return NEEDS_SECOND_BODY
    return NEEDS_NOTHING


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
    rows: list[dict[str, str]] = []
    for slot in CUT_SLOTS:
        if slot not in validated:
            continue
        row = {
            "slot": slot,
            "wording": validated[slot],
            "source_identifier": key,
            "manner": manner,
        }
        # Only the act declares what the photograph must provide. A camera or a
        # room carrying `needs` would narrow a pool for a requirement nothing
        # about it asks for.
        if slot == "act":
            row["needs"] = needs_for_act(row["wording"], family, key)
        rows.append(row)
    return rows


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
    entry is named at once. So is the act whose requirement cannot be read -
    the rows of the entries around it are built in memory and then dropped,
    which is what "reported and not written" means here.
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
    undetermined: list[tuple[str, str]] = []
    for entry, key, family in zip(listed, identifiers, families):
        try:
            rows.extend(
                split_fused_entry(
                    entry, cut_for(cut_map, key), identifier=key, source_family=family
                )
            )
        except ActRequirementUndeterminedError as exc:
            # Collected, not re-raised here: the loop is the only place the act
            # wording exists, and stopping on the first one hands the operator
            # a library of 55 one identifier at a time.
            undetermined.extend(exc.shortfall)
    if undetermined:
        raise ActRequirementUndeterminedError(undetermined)
    return rows
