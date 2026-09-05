"""Room library registry: configuration, loading, and disk agreement.

Provides the central registry for room libraries: which libraries exist, where
their seed files live, their enabled states, and random-draw weights.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Default room libraries configuration shipped with the project.
# When an operator's config.json has no 'room_libraries' key, the app
# defaults to this entry so the nine shipped candid rooms remain available.
DEFAULT_ROOM_LIBRARIES: list[dict] = [
    {
        "name": "candid",
        "seed_file": "candid-rooms-seed.json",
        "enabled": True,
        "weight": 1.0,
    }
]

# The register a manner speaks in. It lives beside the room seeds because the
# rooms are the only thing that composes with it, and it lives in ONE file
# because it belongs to the manner and not to the room: before the split every
# candid room carried its own byte-identical copy of candid's capture clause,
# and every imported room would have had to be handed one.
MANNER_REGISTERS_FILE = "manner-registers-seed.json"


def load_manner_registers(data_dir: Path | str | None = None,
                          config: dict | None = None) -> dict[str, str]:
    """Every manner's register, keyed by manner. Absent file reads as none.

    A missing file is not an error here: it means no manner has a register, and
    `compose_look` then hands back the place alone. A room with no register is
    a room somebody has to write a register for; a room wearing another
    manner's register is the failure session 381 fixed.
    """
    path = resolve_data_dir(data_dir=data_dir, config=config) / MANNER_REGISTERS_FILE
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return {k: v for k, v in loaded.items() if isinstance(v, str)} if isinstance(loaded, dict) else {}


def prose_names_piece(piece: str, prose: str) -> bool:
    """Does this room's prose actually name the piece of furniture it offers.

    Spaces and hyphens are ignored on both sides so the field can be a key -
    `sink-edge`, `backseat` - while the prose stays prose. `edge` is dropped
    from the piece for the same reason and no other: `sink-edge` is the edge OF
    the sink, and the sentence that describes it says "the porcelain sink".

    One function rather than three copies, because it answers one question in
    three places: the importer keeps a source prop only when this is true of
    it, the seed test asserts it over the nine rooms this project wrote, and
    the import test asserts it over every imported room. Written out three
    times, the room that offers a piece nobody can photograph is the one the
    three copies disagree about.
    """
    flat = (prose or "").lower().replace(" ", "").replace("-", "")
    head = (piece or "").lower().replace(" ", "").replace("-", "").replace("edge", "")
    return bool(head) and head in flat


def compose_look(manner: str, place: str, registers: dict[str, str] | None = None) -> str:
    """The look a room composes to under a manner: the register, then the place.

    The place is never edited - not trimmed of its own words, not reworded -
    which is the verbatim-storage rule this import is built on. The only thing
    done to it here is the join, and `frontend/src/rooms.js:composeLook` does
    exactly the same one for the picker.
    """
    if registers is None:
        registers = load_manner_registers()
    return " ".join(p for p in (registers.get(manner, ""), (place or "").strip()) if p)


def resolve_data_dir(
    data_dir: Path | str | None = None,
    config: dict | None = None,
) -> Path:
    """Resolve the data directory path.

    Never hardcode 'data/': honors explicit argument, IDEVGEN_DATA_DIR env var,
    or config['data_dir'], relative to ROOT if not absolute.

    This module does not read config.json. `main` already loads it once at
    import, caches it in `CONFIG`, and rewrites it from `/setup` - a second
    reader here would answer from the file while the app answers from the
    cache, which is the whole gap `restart_required` exists to cover. The
    caller passes `main.CONFIG` and `main.DATA_DIR` in.
    """
    if data_dir is not None:
        p = Path(data_dir)
        return p if p.is_absolute() else ROOT / p
    env = os.environ.get("IDEVGEN_DATA_DIR")
    if env:
        p = Path(env)
        return p if p.is_absolute() else ROOT / p
    p = Path((config or {}).get("data_dir", "data"))
    return p if p.is_absolute() else ROOT / p


def get_room_libraries_config(config: dict | None = None) -> list[dict]:
    """Retrieve the declared room libraries from config or the shipped default.

    If 'room_libraries' key is explicitly present in config, its value is used
    (even if empty). If the key is omitted, DEFAULT_ROOM_LIBRARIES is used.
    """
    cfg = config or {}
    if "room_libraries" in cfg:
        raw = cfg["room_libraries"]
        if isinstance(raw, list):
            return raw
    return [dict(lib) for lib in DEFAULT_ROOM_LIBRARIES]


def normalize_library_entry(entry: dict) -> dict:
    """Normalize a raw library dictionary to standard fields and types."""
    seed_file = entry.get("seed_file") or entry.get("seed") or ""
    return {
        "name": str(entry.get("name", "")),
        "seed_file": str(seed_file),
        "seed": str(seed_file),
        "enabled": bool(entry.get("enabled", True)),
        "weight": float(entry.get("weight", 1.0)),
    }


def load_room_libraries(
    config: dict | None = None,
    data_dir: Path | str | None = None,
    enabled_only: bool = True,
) -> list[dict]:
    """Return the registered libraries and the rooms each contributes.

    A disabled library contributes no rooms to the picker or a draw
    (its 'rooms' list is empty when enabled_only=True), while remaining
    readable so that its metadata and underlying seed file can be inspected
    via 'raw_rooms'.
    """
    resolved_dir = resolve_data_dir(data_dir=data_dir, config=config)
    raw_entries = get_room_libraries_config(config)
    result = []
    for raw in raw_entries:
        lib = normalize_library_entry(raw)
        seed_path = resolved_dir / lib["seed_file"]
        if not seed_path.is_file():
            raise FileNotFoundError(
                f"Room library {lib['name']!r} specifies seed file {lib['seed_file']!r} which does not exist at {seed_path}"
            )
        seed_rooms = json.loads(seed_path.read_text(encoding="utf-8"))
        if not isinstance(seed_rooms, list):
            raise ValueError(
                f"Room library {lib['name']!r} seed file {lib['seed_file']!r} must contain a JSON list of rooms"
            )
        lib["raw_rooms"] = seed_rooms
        if lib["enabled"] or not enabled_only:
            lib["rooms"] = seed_rooms
        else:
            lib["rooms"] = []
        result.append(lib)
    return result


get_room_libraries = load_room_libraries


def get_registered_rooms(
    config: dict | None = None,
    data_dir: Path | str | None = None,
) -> list[dict]:
    """Return the flat list of rooms contributed across all enabled registered libraries."""
    libraries = load_room_libraries(config=config, data_dir=data_dir, enabled_only=True)
    rooms: list[dict] = []
    for lib in libraries:
        rooms.extend(lib["rooms"])
    return rooms


# What this project measured, kept apart from the text it was measured against.
# Tracked, because a verdict is our own work and the room text it was taken
# against may be an import nobody may commit: keeping the two in one file would
# lose every measurement to a licensing decision. Keyed by room key, then by the
# manner the run was shot under, because being ALLOWED in a manner is not being
# MEASURED in one and a room verified candid says nothing about directed.
ROOM_VERDICTS_FILE = "room-verdicts-seed.json"


def load_room_verdicts(data_dir: Path | str | None = None,
                       config: dict | None = None) -> dict[str, dict]:
    """Every stored verdict, keyed by room key and then by manner.

    An absent or unreadable file reads as no verdicts at all, the same way an
    absent register file does: no room has been measured is a real state, and
    it is the state of a checkout before anybody shoots one.
    """
    path = resolve_data_dir(data_dir=data_dir, config=config) / ROOM_VERDICTS_FILE
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {k: v for k, v in loaded.items() if isinstance(v, dict)}


def available_rooms(
    config: dict | None = None,
    data_dir: Path | str | None = None,
) -> dict:
    """Every room the picker can offer right now, and why a library offers none.

    `load_room_libraries` raises on a registered library whose seed is missing,
    and that is right where it is used: the registry and the disk disagreeing
    is a state an import must not leave behind. It is wrong for a picker. The
    imported seeds are untracked, so a fresh clone, a second machine and a
    checkout where nobody has run the import all have a config naming files
    that are not there - and a picker that raises on those is a screen that
    cannot open rather than a screen with nine rooms in it.

    So this one never raises. A library that contributes nothing contributes an
    empty list and a sentence saying why, and the sentence names the seed file
    rather than its path: a machine path in an API response is the same leak as
    a machine path in a tracked file, one hop further out.
    """
    resolved_dir = resolve_data_dir(data_dir=data_dir, config=config)
    # The measurements live in their own tracked file, so a room and its verdict
    # are joined here rather than stored together - the room text may be an
    # import that never reaches git, and the verdict may not go with it.
    verdicts = load_room_verdicts(data_dir=resolved_dir)
    rooms: list[dict] = []
    libraries: list[dict] = []
    for raw in get_room_libraries_config(config):
        lib = normalize_library_entry(raw)
        seed_file = lib["seed_file"]
        path = resolved_dir / seed_file
        loaded: list = []
        reason = ""
        if not lib["enabled"]:
            reason = "switched off in the room library registry"
        elif not seed_file:
            reason = "the registry entry names no seed file"
        elif not path.is_file():
            reason = f"no {seed_file} on disk; nothing has been imported into it here"
        else:
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                parsed = None
                reason = f"{seed_file} is not readable JSON"
            if isinstance(parsed, list):
                loaded = [dict(r, verdicts=verdicts.get(r.get("key"), {}))
                          for r in parsed if isinstance(r, dict)]
            elif parsed is not None:
                reason = f"{seed_file} does not hold a list of rooms"
        libraries.append({
            "name": lib["name"],
            "seed_file": seed_file,
            "enabled": lib["enabled"],
            "rooms": len(loaded),
            "reason": reason,
        })
        rooms.extend(loaded)
    # A verdict whose room no library offers right now. Reported, never
    # deleted: the store is tracked and the rooms it measures may not be, so
    # "the room is not here" is the ordinary state of a clone where nobody ran
    # the import, of a library switched off, and of a seed file somebody moved.
    # Deleting on any of those throws away frames that were actually shot, and
    # the room usually comes back.
    present = {r.get("key") for r in rooms}
    orphaned = sorted(k for k in verdicts if k not in present)
    return {"rooms": rooms, "libraries": libraries, "orphaned_verdicts": orphaned}


def is_room_seed_file(path: Path) -> bool:
    """Identify room seed files on disk.

    Rule: filename ends with '-seed.json' and contains 'room' (case-insensitive).
    This selects room seed files like 'candid-rooms-seed.json' while excluding
    all other seed types (cameras, acts, wardrobe, crops, readings, catalogue,
    directed looks).
    """
    name = path.name.lower()
    if name == ROOM_VERDICTS_FILE:
        # It is named for rooms and it is not a library of them: it holds this
        # project's measurements, keyed by room key. Registering it would put
        # the verdicts through the picker as rooms; leaving it unregistered
        # makes direction 2 of the agreement check refuse every import while it
        # sits on disk. So it is neither - it is not a room seed file.
        return False
    return name.endswith("-seed.json") and "room" in name


def verify_registry_disk_agreement(
    config: dict | None = None,
    data_dir: Path | str | None = None,
) -> None:
    """Assert that the registry and the disk agree in both directions:
    1. Every registered library names a seed file that exists on disk.
    2. Every room seed file on disk is named by a registered library.
    """
    resolved_dir = resolve_data_dir(data_dir=data_dir, config=config)
    raw_entries = get_room_libraries_config(config)
    registered_files: set[str] = set()

    # Direction 1: Registry -> Disk
    for raw in raw_entries:
        lib = normalize_library_entry(raw)
        seed_file = lib["seed_file"]
        seed_path = resolved_dir / seed_file
        if not seed_path.is_file():
            raise FileNotFoundError(
                f"Registry entry {lib['name']!r} names missing seed file: {seed_file} (looked in {resolved_dir})"
            )
        registered_files.add(seed_file)

    # Direction 2: Disk -> Registry
    if resolved_dir.is_dir():
        for p in resolved_dir.iterdir():
            if p.is_file() and is_room_seed_file(p):
                if p.name not in registered_files:
                    raise ValueError(
                        f"Room seed file on disk is not named in registry: {p.name}"
                    )
