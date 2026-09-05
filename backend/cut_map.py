"""The curated cut map for fused source entries.

A fused source entry names a camera position, an act and a room in one clause
of English with no delimiter to parse on. This module holds the shape of the
hand-written map that says where the cuts fall, one entry per source
identifier: the substring that becomes the camera candidate, the one that
becomes the act, the one that becomes the room, and nothing for the parts that
become nothing.

The map itself lives untracked beside the translation map, because its values
quote source prose. What ships tracked is this shape, the code that reads it,
and the invented fixtures its tests run against.

The importer cuts nothing on its own. An identifier missing from the map raises
`CutMissingError` and stops the import - the same rule the translation map
already sets, and for the same reason: a guessed cut is not a crash, it is a
plausible row that enters the catalogue and spends a measurement run on a
fragment nobody wrote.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from backend.translation_map import (
    _is_tracked_location,
    resolve_translation_map_path as resolve_beside_source,
)

# The three slots a fused entry is cut into. A wardrobe clause is named by
# none of them: this project's wardrobe is a catalogue of its own, and a mined
# fragment does not enter it.
CUT_SLOTS: tuple[str, ...] = ("camera", "act", "room")


class CutMissingError(ValueError):
    """Raised when a fused entry has no cut written for it.

    Carries the identifiers so a caller can name every entry the map is short
    of at once, rather than stopping the operator once per re-run.
    """

    def __init__(self, identifiers: list[str]) -> None:
        self.identifiers = [str(i) for i in identifiers]
        if len(self.identifiers) <= 1:
            first = self.identifiers[0] if self.identifiers else ""
            msg = (
                f"No cut written for fused entry {first!r}: add it to the cut "
                f"map. Fused entries are never cut by guess."
            )
        else:
            listed = ", ".join(repr(i) for i in self.identifiers)
            msg = (
                f"No cut written for {len(self.identifiers)} fused entries: "
                f"{listed}. Fused entries are never cut by guess."
            )
        super().__init__(msg)


def validate_cut_entry(entry: Any, identifier: str | None = None) -> dict[str, str]:
    """Validate one cut and return it with its omitted slots left out.

    A slot may be absent, or present as None, which is how the map says the
    entry carries no such part. A slot present as an empty string is refused
    rather than read as absent: that is a slot somebody started writing and did
    not finish, and treating it as "becomes nothing" loses the cut silently.

    A key outside `CUT_SLOTS` is refused for the same reason. A misspelt
    `camera` would otherwise be a camera cut that produces no camera row, and
    nothing downstream would ever ask for it.
    """
    where = f" for {identifier!r}" if identifier else ""
    if not isinstance(entry, dict):
        raise TypeError(f"Cut entry{where} must be a dict, got {type(entry).__name__}")

    unknown = sorted(k for k in entry if k not in CUT_SLOTS)
    if unknown:
        raise ValueError(
            f"Cut entry{where} names slots this importer does not cut: "
            f"{unknown!r}. The slots are {list(CUT_SLOTS)!r}"
        )

    cut: dict[str, str] = {}
    for slot in CUT_SLOTS:
        if slot not in entry:
            continue
        value = entry[slot]
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(
                f"Cut entry{where} slot {slot!r} must be a string or absent, "
                f"got {type(value).__name__}"
            )
        if not value.strip():
            raise ValueError(
                f"Cut entry{where} slot {slot!r} is empty. Leave the slot out "
                f"to say the entry carries no such part"
            )
        # Stored byte for byte: a cut is a substring of the source entry, and a
        # strip() here would stop it being one.
        cut[slot] = value

    if not cut:
        raise ValueError(
            f"Cut entry{where} cuts nothing. An entry that becomes no row does "
            f"not belong in the map"
        )
    return cut


def validate_cut_map(map_data: Any) -> dict[str, dict[str, str]]:
    """Validate a whole cut map keyed by source identifier."""
    if not isinstance(map_data, dict):
        raise TypeError(f"Cut map must be a dict, got {type(map_data).__name__}")

    normalized: dict[str, dict[str, str]] = {}
    for key, entry in map_data.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(
                f"Cut map keys must be non-empty source identifiers, got {key!r}"
            )
        normalized[key] = validate_cut_entry(entry, identifier=key)
    return normalized


def _strings_in(value: Any) -> Iterator[str]:
    """Every string anywhere inside a source entry.

    One recursion over all four shapes rather than a walk that handles lists
    inline: a list inside a list is exactly the shape that made the extractor's
    two walks disagree and put source prose into a seed file.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for sub in value.values():
            yield from _strings_in(sub)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _strings_in(item)


def validate_cut_against_entry(
    identifier: str,
    cut: dict[str, str],
    entry: Any,
) -> dict[str, str]:
    """Refuse a cut that is not a substring of the source entry it claims.

    This is the check that keeps the map a pair of scissors rather than a
    second author. A cut that is not found in the entry is a fragment somebody
    typed, and a typed fragment is a row this project would judge as though the
    source had written it.

    Compared against each of the entry's own strings whole, never against them
    joined: joining invents a boundary, and a cut spanning the end of one field
    and the start of the next would then pass as a substring of a string that
    exists nowhere in the source.
    """
    haystacks = list(_strings_in(entry))
    validated = validate_cut_entry(cut, identifier=identifier)
    for slot, fragment in validated.items():
        if not any(fragment in text for text in haystacks):
            raise ValueError(
                f"Cut for {identifier!r} slot {slot!r} is not a substring of "
                f"that source entry: {fragment!r}"
            )
    return validated


def cut_for(cut_map: dict[str, dict[str, str]], identifier: str) -> dict[str, str]:
    """The cut written for one fused entry, or a stop.

    There is no fallback and no default, on purpose. Every caller of this
    function is about to write catalogue rows, and the only alternative to a
    written cut is a guessed one.
    """
    key = str(identifier)
    if key not in cut_map:
        raise CutMissingError([key])
    return cut_map[key]


def missing_cuts(cut_map: dict[str, dict[str, str]], identifiers: Any) -> list[str]:
    """The fused identifiers the map is short of, in the order given.

    Handed to `CutMissingError` by a caller that wants the whole shortfall
    named in one message rather than one identifier per failed run.
    """
    seen: set[str] = set()
    missing: list[str] = []
    for identifier in identifiers:
        key = str(identifier)
        if key in cut_map or key in seen:
            continue
        seen.add(key)
        missing.append(key)
    return missing


def resolve_cut_map_path(source_dir: Path | str, relative_path: Path | str) -> Path:
    """Resolve the cut map's path beside the source material.

    The same resolution the translation map uses, and deliberately the same
    function: both files sit beside the same material under the same rules, and
    a second copy of that logic here is a second set of edge cases to get wrong.
    """
    return resolve_beside_source(source_dir, relative_path)


def load_cut_map(path: Path | str) -> dict[str, dict[str, str]]:
    """Load and validate the cut map from its untracked JSON file."""
    target_path = Path(path)
    if not target_path.is_file():
        raise FileNotFoundError(f"Cut map file not found: {target_path}")

    raw_text = target_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in cut map {target_path}: {exc}") from exc
    return validate_cut_map(data)


def save_cut_map(cut_map: dict[str, Any], path: Path | str) -> Path:
    """Validate and write the cut map to an untracked JSON file.

    Refuses a destination this repository would track. The cuts are source
    prose by definition - they are substrings of it - so "untracked" has to be
    something the code checks rather than something a docstring says.
    """
    target_path = Path(path)
    if _is_tracked_location(target_path):
        raise ValueError(
            f"Cut map would be tracked by git at {target_path}: its cuts are "
            f"source prose and must live at an untracked path beside the "
            f"source material"
        )
    validated = validate_cut_map(cut_map)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(validated, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    return target_path
