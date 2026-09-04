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

CONFIG_PATH = Path(os.environ.get("IDEVGEN_CONFIG") or ROOT / "config.json")


def load_app_config() -> dict:
    """Load configuration from IDEVGEN_CONFIG or config.json.

    If missing, falls back to config.example.json if available, or empty dict.
    """
    if not CONFIG_PATH.exists():
        example_path = ROOT / "config.example.json"
        if example_path.exists():
            return json.loads(example_path.read_text(encoding="utf-8"))
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def resolve_data_dir(
    data_dir: Path | str | None = None,
    config: dict | None = None,
) -> Path:
    """Resolve the data directory path.

    Never hardcode 'data/': honors explicit argument, IDEVGEN_DATA_DIR env var,
    or config['data_dir'], relative to ROOT if not absolute.
    """
    if data_dir is not None:
        p = Path(data_dir)
        return p if p.is_absolute() else ROOT / p
    env = os.environ.get("IDEVGEN_DATA_DIR")
    if env:
        p = Path(env)
        return p if p.is_absolute() else ROOT / p
    cfg = config if config is not None else load_app_config()
    p = Path(cfg.get("data_dir", "data"))
    return p if p.is_absolute() else ROOT / p


def get_room_libraries_config(config: dict | None = None) -> list[dict]:
    """Retrieve the declared room libraries from config or the shipped default.

    If 'room_libraries' key is explicitly present in config, its value is used
    (even if empty). If the key is omitted, DEFAULT_ROOM_LIBRARIES is used.
    """
    cfg = config if config is not None else load_app_config()
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


def is_room_seed_file(path: Path) -> bool:
    """Identify room seed files on disk.

    Rule: filename ends with '-seed.json' and contains 'room' (case-insensitive).
    This selects room seed files like 'candid-rooms-seed.json' while excluding
    all other seed types (cameras, acts, wardrobe, crops, readings, catalogue,
    directed looks).
    """
    name = path.name.lower()
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
