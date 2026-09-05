"""What each source library carries, and where its entries are allowed to land.

A source library is declared here or it is not imported. There is no config
key, environment variable or caller argument that declares one - the same rule
the deny-list in `asset_guard` has, and for the same reason: a declaration an
upload can add to is not a declaration.

A DESTINATION is a file this app reads at runtime. Exactly one kind of
destination exists for imported material today: a room seed file in the data
directory, named by an entry in the room registry (`backend/room_registry.py`).
Nothing else in this app is written by an import, so nothing else is declared
here. Naming a table nothing writes to would be a declaration that cannot be
wrong, which is not a declaration either.

A library whose material has nowhere to go therefore declares no destination at
all, and a file of that library is refused with the reason it names. Two cases
carry that today:

- `amateurs` and `celebrities` are body profiles and real people's names. This
  project does not adopt them; the reason is `asset_guard.SIGNAL_NOT_ADOPTED`,
  which is deliberately readable apart from a deny-list refusal.
- `perspective_scenes` fuses a camera clause, an act and a room into one entry.
  It reaches no destination as it stands, because a room seed row carrying a
  camera position is a room that overrules the line's camera. Splitting it is
  its own work, with its own curated map, and the destinations it lands in are
  declared when it can land in them.

The library a file belongs to is read from the FILE NAME and from nothing
inside the file. Source material does not get to say what it is: a file whose
contents claim `"library": "general_scenes"` is still refused when nothing
declares its name.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.asset_guard import SIGNAL_NOT_ADOPTED

# The kind of material a source library carries.
KIND_ROOMS: str = "rooms"
KIND_FUSED_SCENES: str = "fused_scenes"
KIND_BODY_PROFILES: str = "body_profiles"
KIND_IDENTITIES: str = "identities"

# Refusal reasons. `SIGNAL_NOT_ADOPTED` is imported rather than respelled so
# the file-level refusal and the entry-level one are one word, not two.
REASON_UNDECLARED: str = "undeclared"
REASON_NOT_SPLIT: str = "not_split_yet"

# Every source library this project can be handed, the kind of material it
# carries, and every destination its entries reach. `reason` is present only on
# a library that reaches no destination, and says why there is nowhere to write.
SOURCE_LIBRARIES: dict[str, dict[str, Any]] = {
    "general_scenes": {
        "kind": KIND_ROOMS,
        "destinations": ("general-scenes-rooms-seed.json",),
    },
    "workplace_scenes": {
        "kind": KIND_ROOMS,
        "destinations": ("workplace-scenes-rooms-seed.json",),
    },
    "medical_scenes": {
        "kind": KIND_ROOMS,
        "destinations": ("medical-scenes-rooms-seed.json",),
    },
    "sm_scenes": {
        "kind": KIND_ROOMS,
        "destinations": ("sm-scenes-rooms-seed.json",),
    },
    "special_scenes": {
        "kind": KIND_ROOMS,
        "destinations": ("special-scenes-rooms-seed.json",),
    },
    # Declared as rooms like its siblings. Its school-set entries are refused
    # one by one by the guard's own signals, which is an entry decision and not
    # a library one - the library name says where the file lands, never whether
    # a given entry is carried.
    "school_scenes": {
        "kind": KIND_ROOMS,
        "destinations": ("school-scenes-rooms-seed.json",),
    },
    "perspective_scenes": {
        "kind": KIND_FUSED_SCENES,
        "destinations": (),
        "reason": REASON_NOT_SPLIT,
    },
    "amateurs": {
        "kind": KIND_BODY_PROFILES,
        "destinations": (),
        "reason": SIGNAL_NOT_ADOPTED,
    },
    "celebrities": {
        "kind": KIND_IDENTITIES,
        "destinations": (),
        "reason": SIGNAL_NOT_ADOPTED,
    },
}


class SourceRefused(Exception):
    """A source file that will not be imported, and why.

    Raised rather than returned: a refusal a caller can forget to read is a
    refusal that imports.
    """

    def __init__(self, reason: str, library: str) -> None:
        super().__init__(
            f"Source library {library!r} is refused: {reason}"
        )
        self.reason = reason
        self.library = library


def library_name_for_file(path: Path | str) -> str:
    """The library a file belongs to, read from its name alone."""
    return Path(path).stem.strip().lower()


def declaration_for(library: str) -> dict[str, Any] | None:
    """The declaration for a library name, or None when nothing declares it."""
    found = SOURCE_LIBRARIES.get(str(library).strip().lower())
    return dict(found) if found is not None else None


def declare_source_file(path: Path | str) -> dict[str, Any]:
    """Return the declaration a source file imports under, or refuse it.

    Reads the file's name and nothing else - no bytes are read and nothing is
    written, whatever the answer. Raises `SourceRefused` naming the library it
    could not identify, or the reason a declared library reaches no
    destination.
    """
    library = library_name_for_file(path)
    declared = declaration_for(library)
    if declared is None:
        raise SourceRefused(REASON_UNDECLARED, library)
    if not declared["destinations"]:
        raise SourceRefused(declared.get("reason", REASON_UNDECLARED), library)
    declared["library"] = library
    return declared
