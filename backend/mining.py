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

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from backend.cut_map import (
    CUT_SLOTS,
    CutMissingError,
    cut_for,
    missing_cuts,
    resolve_cut_map_path,
    validate_cut_against_entry,
)
# The untracked-destination check, borrowed rather than respelled: the judge
# labels sit beside the same source material as the cut map and under the same
# rule, and a second answer to "is this path tracked" is a second set of edge
# cases to get wrong.
from backend.cut_map import _is_tracked_location
from backend.extractor import IDENTIFIER_KEYS
from backend.importer import derive_multi_body
from backend.room_registry import resolve_data_dir


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


# The catalogue key a mined row is stored under, built from the source
# identifier and the slot it fills. Derived and not invented: the combination
# has to name the rows it was split into, and a key handed out at storage time
# would leave the split with nothing to record until the rows were written.
# Deterministic, so re-mining the same library twice names the same rows - which
# is what makes 8.15's duplicate report possible at all.
MINED_KEY_PREFIX: str = "mined"


def row_key(identifier: str, slot: str) -> str:
    """The key the mined row for this entry's `slot` is stored under."""
    if slot not in CUT_SLOTS:
        raise ValueError(
            f"Unknown slot {slot!r}: the slots are {list(CUT_SLOTS)!r}"
        )
    stem = normalise_family(identifier)
    if not stem:
        raise ValueError(
            f"Cannot build a row key from identifier {identifier!r}: a mined row "
            f"with no key cannot be named by the combination it belongs to"
        )
    return f"{MINED_KEY_PREFIX}-{stem}-{slot}"


DIGEST_LENGTH: int = 16


def wording_digest(wording: str) -> str:
    """A fingerprint of a row's wording, and not the wording.

    The combination stores keys and no prose (8.10), so it cannot notice on its
    own that a row it names now says something else. This is what it stores
    instead: a hash is not text, it reproduces nothing, and it is exactly enough
    to answer the one question 8.13 asks - is this still the row the entry was
    split into.

    Whitespace-trimmed before hashing because every writer of a wording trims
    it: the catalogue PATCH strips, the seed import strips, and a digest that
    moved on a trailing space would report a row broken that nobody touched.
    """
    return hashlib.sha256(wording.strip().encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def combination_for(rows: Iterable[dict[str, str]]) -> dict[str, dict[str, str]]:
    """The combination a set of rows from ONE entry composes back into.

    A key and a fingerprint per slot, which is the storage rule 8.10 sets: still
    no prose, and now enough to tell a row that was reworded from the row the
    entry was actually split into. Built from the rows the split produced, so a
    part the entry never carried is absent here too - the combination records
    what was mined, not what a full entry would have had.
    """
    return {
        row["slot"]: {"key": row["key"], "digest": wording_digest(row["wording"])}
        for row in rows
        if row.get("key")
    }


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
            "key": row_key(key, slot),
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

    by_row_key: dict[str, set[str]] = {}
    for key in identifiers:
        by_row_key.setdefault(row_key(key, CUT_SLOTS[0]), set()).add(key)
    collisions = sorted(sorted(v) for v in by_row_key.values() if len(v) > 1)
    if collisions:
        raise ValueError(
            f"Two source entries build the same row keys: {collisions!r}. A mined "
            f"row is keyed on its identifier, so a collision silently merges two "
            f"photographs into one row and one combination overwrites the other"
        )

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


# The combinations a mined source entry was split into, kept beside the parts.
#
# Mining separates a camera, an act and a room that ONE AUTHOR wrote to agree
# with each other, and the agreement is what made the entry render - session 392
# shot four of these and got the camera right in every family. Keeping the parts
# without keeping the combination trades a working photograph for three rows
# that have never been seen together.
#
# Row KEYS and nothing else. The file is tracked, and the wording it would
# otherwise carry is source prose that may not be committed - the same split the
# room verdicts already live under, for the same reason: keeping our own record
# in one file with somebody else's text loses the record to a licensing
# decision. It also makes the record survive its rows: a combination is a
# reference, not a foreign key, and a row retired or reworded leaves the
# combination standing to be reported broken (8.13) rather than silently
# deleted.
MINED_COMBINATIONS_FILE: str = "mined-combinations-seed.json"

_KEY_SHAPED = re.compile("^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DIGEST_SHAPED = re.compile(f"^[0-9a-f]{{{DIGEST_LENGTH}}}$")


def validate_combination(
    entry: Any,
    identifier: str = "",
) -> dict[str, dict[str, str]]:
    """One recorded combination, checked to be keys and not prose.

    A value with a space in it is a wording somebody pasted where the key
    belongs, and it is refused rather than stored: stored, it would put source
    text into a tracked file, and it would compose a combination out of words
    instead of out of rows - so the day a row is reworded, the combination would
    go on reproducing the old photograph and nothing would say the row moved.

    A slot outside `CUT_SLOTS` is refused for the cut map's reason: a misspelt
    `camera` is a part of the combination nothing downstream ever asks for.

    Each slot holds a key AND a wording digest, and both are required. The
    digest is not prose - it reproduces nothing and reads as nothing - and it is
    the only thing that lets a stored reference tell the row it was split into
    from a row somebody has since reworded. A reference without one composes a
    photograph under the identifier of a different one.
    """
    where = f" for {identifier!r}" if identifier else ""
    if not isinstance(entry, dict):
        raise TypeError(
            f"Combination{where} must be a dict, got {type(entry).__name__}"
        )
    unknown = sorted(k for k in entry if k not in CUT_SLOTS)
    if unknown:
        raise ValueError(
            f"Combination{where} names slots this importer does not cut: "
            f"{unknown!r}. The slots are {list(CUT_SLOTS)!r}"
        )
    out: dict[str, dict[str, str]] = {}
    for slot in CUT_SLOTS:
        if slot not in entry or entry[slot] is None:
            continue
        value = entry[slot]
        if not isinstance(value, dict):
            raise ValueError(
                f"Combination{where} slot {slot!r} must be a row reference or "
                f"absent, got {type(value).__name__}"
            )
        unknown_fields = sorted(k for k in value if k not in ("key", "digest"))
        if unknown_fields:
            raise ValueError(
                f"Combination{where} slot {slot!r} names {unknown_fields!r}. A row "
                f"reference is a key and a digest, and nothing else is stored here"
            )
        key = value.get("key")
        digest = value.get("digest")
        if not isinstance(key, str) or not _KEY_SHAPED.match(key.strip()):
            raise ValueError(
                f"Combination{where} slot {slot!r} is not a row key: {key!r}. "
                f"A combination records the KEYS of the rows it was split into, "
                f"never their wording"
            )
        if not isinstance(digest, str) or not _DIGEST_SHAPED.match(digest.strip()):
            raise ValueError(
                f"Combination{where} slot {slot!r} carries no wording digest: "
                f"{digest!r}. Without one the combination cannot tell the row it "
                f"was split into from a row somebody has since reworded"
            )
        out[slot] = {"key": key.strip(), "digest": digest.strip()}
    if not out:
        raise ValueError(
            f"Combination{where} records no rows. An entry that names nothing "
            f"reproduces nothing"
        )
    return out


def load_mined_combinations(
    data_dir: Any = None,
    config: dict | None = None,
) -> dict[str, dict[str, dict[str, str]]]:
    """Every recorded combination, keyed by the source identifier it came from.

    An absent or unreadable file reads as no combinations at all, the way the
    room verdicts do: nothing has been mined yet is a real state, and it is the
    state of a fresh checkout.

    The rows a combination names are NOT looked up here. A combination whose
    rows are absent comes back whole - it is a reference to be reported broken,
    and a loader that dropped it would delete the only record of what the source
    entry was.
    """
    path = resolve_data_dir(data_dir=data_dir, config=config) / MINED_COMBINATIONS_FILE
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {
        str(key): validate_combination(value, str(key))
        for key, value in loaded.items()
    }


def record_combinations(
    rows: Iterable[dict[str, str]],
) -> dict[str, dict[str, dict[str, str]]]:
    """The combination each source entry was split into, keyed by identifier.

    Grouped from the rows themselves rather than recomputed from the entries: a
    second walk over the source deciding what a combination holds is a second
    calculation of one fact, and the day it disagrees the record names rows the
    split never produced.
    """
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["source_identifier"], []).append(row)
    return {key: combination_for(group) for key, group in grouped.items()}


def save_mined_combinations(
    combinations: dict[str, dict[str, dict[str, str]]],
    data_dir: Any = None,
    config: dict | None = None,
) -> Path:
    """Write the recorded combinations, validated, and return the path.

    Every entry goes through `validate_combination` on the way out as well as on
    the way in: the file is tracked, and the check that keeps source prose out of
    it is worth nothing if only the reader runs it.

    The whole file is replaced rather than merged. A merge would keep a
    combination whose source entry has been re-cut, and the operator would then
    have two records of one photograph with no way to tell which cut produced
    the frames.
    """
    checked = {
        str(key): validate_combination(value, str(key))
        for key, value in combinations.items()
    }
    path = resolve_data_dir(data_dir=data_dir, config=config) / MINED_COMBINATIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({k: checked[k] for k in sorted(checked)}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def combination_breakage(
    combination: dict[str, dict[str, str]],
    rows: dict[str, Any],
) -> list[str]:
    """Why this recorded combination can no longer reproduce its source entry.

    Empty is whole. Anything else is the list of reasons, one per slot, and the
    caller composes NOTHING when it is non-empty: a combination is the record of
    three parts one author wrote to agree, and two of them plus a substitute is
    a photograph nobody measured wearing the identifier of one somebody did.

    Pure, and reported per slot rather than at the first fault, so the operator
    sees the whole shortfall in one refusal - the same rule the cut map and the
    family declaration already answer under. `rows` maps a slot to the catalogue
    row it resolves to today, or to None where nothing carries the key; a slot
    the caller left out of `rows` is one it cannot resolve, and it is not
    judged.

    Retired and absent are reported apart although both refuse: a retired row is
    still on disk and the operator's next move is to restore it, and an absent
    one is gone and their next move is to re-run the import. One message for
    both leaves them guessing which.
    """
    reasons: list[str] = []
    for slot in CUT_SLOTS:
        reference = combination.get(slot)
        if not reference:
            continue
        if slot not in rows:
            # Not handed a row for this slot means not judged here, which is
            # not the same as handed None. The caller decides which slots it
            # can resolve - the room is not a component and the compose route
            # cannot look one up - and a slot missing from `rows` reported as
            # absent would refuse every combination that names a room.
            continue
        key = reference["key"]
        row = rows[slot]
        if row is None:
            reasons.append(
                f"the {slot} row {key!r} is not in the catalogue any more"
            )
            continue
        if row.get("retired_at"):
            reasons.append(f"the {slot} row {key!r} has been retired")
            continue
        if wording_digest(row.get("wording") or "") != reference["digest"]:
            reasons.append(
                f"the {slot} row {key!r} has been reworded since the entry was "
                f"split, so it no longer says what the source entry said"
            )
    return reasons


# -- The judge labels of the mined rows -----------------------------------

# A mined row cannot be put to a blind judge without one: `judge_label` is the
# sentence the judge is asked to confirm, and the schema refuses a component
# without it. It cannot be derived - this module parses no prose, and a label
# built by cutting the wording down would be the wording again, which the
# schema also refuses. So it is curated, one per mined row, and read from here.
#
# UNTRACKED, beside the source material, the way the cut map is. The file is
# keyed per source entry and its values are prose ABOUT that entry, and this
# change's rule for such a file is that it does not enter git - the tracked
# combination store gets away with being tracked precisely because it holds
# keys and no words. The concepts this project owns are recorded in their own
# tracked file (8.16); this is not that file.
MINED_LABELS_FILE: str = "mined-judge-labels.json"


class JudgeLabelMissingError(ValueError):
    """Mined rows that cannot be stored because nobody has labelled them.

    Carries the whole shortfall rather than the first name, the rule
    `CutMissingError` and `FamilyUndeclaredError` already answer under: a
    library of 55 entries labelled one re-run at a time is 55 re-runs.

    The two halves are reported apart because the operator's next move differs.
    A row with NO label needs one written. A row whose label is its own wording
    has one and it is unusable: the schema refuses `judge_label = wording`, and
    a judge shown the prompt's own words is being asked whether the prompt says
    what it says rather than whether the photograph shows it.
    """

    def __init__(self, unlabelled: list[str], echoed: list[str]) -> None:
        self.unlabelled = list(unlabelled)
        self.echoed = list(echoed)
        parts = []
        if self.unlabelled:
            parts.append(f"no judge label for {sorted(self.unlabelled)!r}")
        if self.echoed:
            parts.append(
                f"the judge label repeats the wording for {sorted(self.echoed)!r}"
            )
        super().__init__(
            "; ".join(parts)
            + f". Write them in {MINED_LABELS_FILE}, keyed by row key: a mined row "
            f"with no usable label cannot be put to a blind judge, which is the "
            f"only way it stops being unverified"
        )


def validate_mined_labels(data: Any) -> dict[str, str]:
    """The labels file, checked to be row keys against non-empty sentences."""
    if not isinstance(data, dict):
        raise ValueError(
            f"The judge labels must be a mapping of row key to label, got "
            f"{type(data).__name__}"
        )
    out: dict[str, str] = {}
    for key, label in data.items():
        if not isinstance(label, str) or not label.strip():
            raise ValueError(
                f"Judge label for {key!r} is empty. A row with a blank label is a "
                f"row the schema refuses and a judge cannot be asked about"
            )
        out[str(key)] = label.strip()
    return out


def resolve_mined_labels_path(source_dir: Any, relative_path: Any) -> Path:
    """Resolve the labels file beside the source material, as the cut map does."""
    return resolve_cut_map_path(source_dir, relative_path)


def load_mined_labels(path: Any) -> dict[str, str]:
    """Every curated judge label, keyed by mined row key.

    An absent file is no labels at all, which is the state before anybody has
    labelled anything - and it refuses every row at `component_rows` rather
    than here, where the message can name the rows.
    """
    target = Path(path)
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid JSON in judge labels {target}: {exc}") from exc
    return validate_mined_labels(data)


def save_mined_labels(labels: dict[str, str], path: Any) -> Path:
    """Validate and write the judge labels to an untracked path.

    The refusal is a check and not a sentence in a docstring, for the reason
    `save_cut_map` has one: a rule nothing executes is a rule that holds until
    the first hurry.
    """
    target = Path(path)
    if _is_tracked_location(target):
        raise ValueError(
            f"The judge labels would be tracked by git at {target}: they are prose "
            f"about the source entries and must live at an untracked path beside "
            f"the source material"
        )
    validated = validate_mined_labels(labels)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({k: validated[k] for k in sorted(validated)}, indent=2,
                   ensure_ascii=True),
        encoding="utf-8",
    )
    return target


def component_rows(
    rows: Iterable[dict[str, str]],
    labels: dict[str, str],
) -> list[dict[str, str]]:
    """The mined camera and act rows in the shape `/api/components/import` eats.

    The store is the catalogue's own import route and not a second writer: a
    mined row is a candidate row like any other, and a second insert path would
    be a second set of rules about what a component is.

    A ROOM row is not a component - the catalogue's three slots are the three
    dimensions of a cell and a room is the session's look - so it is dropped
    here rather than refused: an entry carrying one is ordinary, and its room
    reaches a photograph through the room library.

    `family`, `faces` and `cameras` are left EMPTY on purpose. Each of them is a
    measurement - which family the camera belongs to, which way she faces, which
    cameras can see the act - and a mined row has been measured at nothing. A
    guess here would be read downstream as a reading somebody took.
    """
    out: list[dict[str, str]] = []
    unlabelled: list[str] = []
    echoed: list[str] = []
    for row in rows:
        if row["slot"] == "room":
            continue
        label = (labels.get(row["key"]) or "").strip()
        if not label:
            unlabelled.append(row["key"])
            continue
        if label == row["wording"].strip():
            echoed.append(row["key"])
            continue
        out.append({
            "concept_key": row["key"],
            "slot": row["slot"],
            "manner": row["manner"],
            "wording": row["wording"],
            "judge_label": label,
            "family": "",
            "faces": "",
            "cameras": "",
            "needs": row.get("needs", ""),
        })
    if unlabelled or echoed:
        raise JudgeLabelMissingError(unlabelled, echoed)
    return out


# -- The camera concepts the mining introduces -----------------------------

# What the source libraries are being mined FOR. Their cameras are held by a
# participant, and two of the positions that follow from that are positions this
# project's camera catalogue does not carry in any manner - not as a wording, not
# under another name, and not as a treatment that happens to look like one.
#
# Recorded here and not in a seed file: it is a finding about OUR catalogue,
# written in our own words, and it is read by a test rather than by the app. A
# JSON file would need a loader, a validator and a place in the registry checks
# to say what a tuple says.
#
# Each entry names the rows it is NEAREST to, and that is what makes the claim
# accountable rather than an assertion: the near misses are named so a reader can
# check them, and a test asserts they still exist. When one of these concepts is
# added to the catalogue under its key, it stops being new and comes out of here
# - which the same test enforces from the other side.
NEW_CAMERA_CONCEPTS: tuple[dict[str, Any], ...] = (
    {
        "key": "feet-first-low-pov",
        "judge_label": "From below her feet, looking up the length of her body",
        "nearest": ("worms-eye", "ground-level", "floor-low-angle", "low-angle"),
        "why_new": (
            "The catalogue's low cameras say how HIGH the lens is and not where "
            "around her it stands: `ground-level` looks across the floor, "
            "`worms-eye` looks steeply up, and both are silent about the axis. "
            "This one is on the axis of her body at its foot end, which is the "
            "position that foreshortens her along its whole length - and "
            "`cand-foreshortening` names that as a TREATMENT, an effect to look "
            "for, with no camera standing anywhere."
        ),
    },
    {
        "key": "overhead-over-kneeling",
        "judge_label": "Looking down from standing height at someone kneeling below the lens",
        "nearest": ("overhead-direct", "top-down", "high-angle", "overhead-term"),
        "why_new": (
            "Every overhead the catalogue carries looks down at HER as the whole "
            "subject, from a ceiling, a shelf or an arm - `top-down` is straight "
            "down, `overhead-direct` is directly above her. This one is a "
            "participant's own eyeline at standing height onto a body kneeling "
            "below it, which is a height and a relation between two people rather "
            "than an angle onto one, and the catalogue has no camera that says "
            "where the second body is."
        ),
    },
)
