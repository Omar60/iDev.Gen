"""Room library registry tests (Phase 3, tasks 3.1 to 3.5).

Asserts:
- App starts with no registry key in config and lists the same nine rooms.
- Registry's shipped entry names the same file ModelDetail.jsx imports (extracted dynamically).
- Toggling 'enabled' removes and restores rooms in both directions while keeping the library readable.
- Registry and disk agree in both directions; fixtures prove each failure direction.
- Documented default in config.example.json equals the code default.
- Edge branches: empty registry, multi-library registry, and weight zero.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

import room_registry
from room_registry import (
    DEFAULT_ROOM_LIBRARIES,
    get_registered_rooms,
    get_room_libraries_config,
    ROOM_VERDICTS_FILE,
    is_room_seed_file,
    load_room_libraries,
    verify_registry_disk_agreement,
)

ROOT = Path(__file__).resolve().parent.parent
REPO_DATA = ROOT / "data"
SHIPPED_ROOM_FILE = "candid-rooms-seed.json"


def test_app_starts_with_no_registry_key_and_lists_nine_rooms():
    """3.1 / 3.2: With no 'room_libraries' key in config, the registry defaults
    to the shipped candid library and lists the same nine rooms as candid-rooms-seed.json.
    """
    empty_config: dict = {}
    libs = load_room_libraries(config=empty_config, data_dir=REPO_DATA)
    assert len(libs) == 1
    shipped_lib = libs[0]
    assert shipped_lib["name"] == "candid"
    assert shipped_lib["seed_file"] == SHIPPED_ROOM_FILE
    assert shipped_lib["enabled"] is True
    assert shipped_lib["weight"] == 1.0

    contributed = get_registered_rooms(config=empty_config, data_dir=REPO_DATA)
    expected_rooms = json.loads((REPO_DATA / SHIPPED_ROOM_FILE).read_text(encoding="utf-8"))
    assert len(contributed) == 9
    assert len(contributed) == len(expected_rooms)
    assert [r["key"] for r in contributed] == [r["key"] for r in expected_rooms]


def test_registry_shipped_entry_names_same_file_modeldetail_imports():
    """3.2: The registry's shipped entry must name the exact file ModelDetail.jsx imports.
    The filename is parsed dynamically from ModelDetail.jsx rather than hardcoding
    the string, ensuring the test will fail if either side drifts.
    """
    jsx_path = ROOT / "frontend" / "src" / "views" / "ModelDetail.jsx"
    assert jsx_path.is_file(), f"ModelDetail.jsx not found at {jsx_path}"
    jsx_text = jsx_path.read_text(encoding="utf-8")

    # Match: import candidRooms from '../../../data/candid-rooms-seed.json'
    # Pattern looks for any import of *Rooms from a JSON file.
    match = re.search(r"import\s+\w*[Rr]ooms?\s+from\s+['\"][^'\"]*?([^/\\'\"]+\.json)['\"]", jsx_text)
    assert match is not None, "Could not find room seed JSON import in ModelDetail.jsx"
    jsx_imported_file = match.group(1)

    shipped_entry = DEFAULT_ROOM_LIBRARIES[0]
    assert shipped_entry["seed_file"] == jsx_imported_file, (
        f"Registry shipped seed_file {shipped_entry['seed_file']!r} does not match "
        f"ModelDetail.jsx import {jsx_imported_file!r}"
    )


def test_toggling_enabled_removes_and_restores_rooms_and_stays_readable(tmp_path: Path):
    """3.3: A disabled library contributes no rooms to the picker or a draw,
    and stays readable. Toggling enabled removes and restores its rooms in both
    directions, and the disabled library remains listed and inspectable.
    """
    fixture_rooms = [
        {"key": "test-room-1", "label": "Test Room 1", "manner": "candid", "place": "A room.", "offers": "chair", "verdict": "unverified"},
        {"key": "test-room-2", "label": "Test Room 2", "manner": "candid", "place": "Another room.", "offers": "table", "verdict": "unverified"},
    ]
    fixture_seed = "fixture-rooms-seed.json"
    (tmp_path / fixture_seed).write_text(json.dumps(fixture_rooms), encoding="utf-8")

    # Step 1: Library is enabled
    config_enabled = {
        "room_libraries": [
            {"name": "fixture", "seed_file": fixture_seed, "enabled": True, "weight": 1.0}
        ]
    }
    libs_enabled = load_room_libraries(config=config_enabled, data_dir=tmp_path)
    assert len(libs_enabled) == 1
    assert libs_enabled[0]["name"] == "fixture"
    assert libs_enabled[0]["enabled"] is True
    assert len(libs_enabled[0]["rooms"]) == 2
    assert get_registered_rooms(config=config_enabled, data_dir=tmp_path) == fixture_rooms

    # Step 2: Toggle to disabled -> rooms removed, library still readable
    config_disabled = {
        "room_libraries": [
            {"name": "fixture", "seed_file": fixture_seed, "enabled": False, "weight": 1.0}
        ]
    }
    libs_disabled = load_room_libraries(config=config_disabled, data_dir=tmp_path)
    assert len(libs_disabled) == 1
    assert libs_disabled[0]["name"] == "fixture"
    assert libs_disabled[0]["enabled"] is False
    # Contributed rooms must be empty
    assert libs_disabled[0]["rooms"] == []
    # But library stays readable: metadata and raw rooms can still be inspected
    assert len(libs_disabled[0]["raw_rooms"]) == 2
    assert get_registered_rooms(config=config_disabled, data_dir=tmp_path) == []

    # Step 3: Toggle back to enabled -> rooms restored
    libs_restored = load_room_libraries(config=config_enabled, data_dir=tmp_path)
    assert len(libs_restored) == 1
    assert libs_restored[0]["enabled"] is True
    assert len(libs_restored[0]["rooms"]) == 2
    assert get_registered_rooms(config=config_enabled, data_dir=tmp_path) == fixture_rooms


def test_shipped_registry_and_disk_agree_both_directions():
    """3.4: On the shipped checkout, every registered library exists in data/,
    and every room seed file the repository TRACKS in data/ is registered.

    Direction 2 is asked of `git ls-files`, not of the directory: 6.1 writes
    imported rooms to an untracked path, and the operator's own `data/` is
    where they land. Swept off the disk this test would go red on any machine
    that has run an import — the property that matters is that nothing SHIPS
    a room seed the registry does not name.
    """
    registered = {lib["seed_file"] for lib in DEFAULT_ROOM_LIBRARIES}

    # Direction 1: every registered library exists on disk.
    for seed_file in registered:
        assert (REPO_DATA / seed_file).is_file(), (
            f"Registry names {seed_file!r}, which is not in {REPO_DATA}"
        )

    out = subprocess.run(["git", "ls-files", "--", "data"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git repository")
    tracked = [Path(line).name for line in out.stdout.splitlines() if line.strip()]

    # Direction 2: every tracked room seed is named by the registry.
    unregistered = [n for n in tracked if is_room_seed_file(Path(n)) and n not in registered]
    assert unregistered == [], (
        f"Tracked room seed files no registry entry names: {unregistered}"
    )

    # 6.5's store is named for rooms and is not a library of them: it holds the
    # measurements, keyed by room key. Read as a room seed it is unregistered on
    # every checkout, and direction 2 above then refuses every import while it
    # sits on disk - which is how this was found. Asserted here because a rename
    # of the file would take the exclusion with it, silently.
    assert ROOM_VERDICTS_FILE in tracked
    assert not is_room_seed_file(Path(ROOM_VERDICTS_FILE))


def test_disk_agreement_fails_when_registry_names_missing_file(tmp_path: Path):
    """3.4 Direction 1 failure: A registry entry naming a file that does not exist fails."""
    config = {
        "room_libraries": [
            {"name": "phantom", "seed_file": "nonexistent-rooms-seed.json", "enabled": True, "weight": 1.0}
        ]
    }
    with pytest.raises(FileNotFoundError) as exc_info:
        verify_registry_disk_agreement(config=config, data_dir=tmp_path)
    assert "phantom" in str(exc_info.value)
    assert "nonexistent-rooms-seed.json" in str(exc_info.value)


def test_disk_agreement_fails_when_room_seed_file_is_unregistered(tmp_path: Path):
    """3.4 Direction 2 failure: A room seed file that no entry names fails."""
    # Write registered room seed
    registered_file = "registered-rooms-seed.json"
    (tmp_path / registered_file).write_text("[]", encoding="utf-8")

    # Write orphaned room seed (matches *room*-seed.json naming rule)
    orphaned_file = "unregistered-rooms-seed.json"
    (tmp_path / orphaned_file).write_text("[]", encoding="utf-8")

    config = {
        "room_libraries": [
            {"name": "registered", "seed_file": registered_file, "enabled": True, "weight": 1.0}
        ]
    }
    with pytest.raises(ValueError) as exc_info:
        verify_registry_disk_agreement(config=config, data_dir=tmp_path)
    assert "Room seed file on disk is not named in registry" in str(exc_info.value)
    assert orphaned_file in str(exc_info.value)


def test_room_seed_sweep_rule_selects_exact_room_files():
    """3.4: The sweep rule - ends with '-seed.json' and carries 'room' - selects
    room seed files and nothing else.

    Asserted over named cases rather than over a count of what happens to be in
    `data/` today: phase 5.12 imports one library per seed file, and a count is
    a test that goes red for every one of them without a room being involved.
    """
    selected = [n for n in (
        SHIPPED_ROOM_FILE,
        "bathroom-rooms-seed.json",
        "catalogue-seed.json",
        "camera-candidates-seed.json",
        "candid-acts-seed.json",
        "wardrobe-seed.json",
        "directed-looks-seed.json",
        "rooms.json",
        "candid-rooms-seed.json.bak",
    ) if is_room_seed_file(Path(n))]
    assert selected == [SHIPPED_ROOM_FILE, "bathroom-rooms-seed.json"], (
        f"Room seed sweep rule selected {selected}"
    )


def test_documented_default_equals_shipped_default():
    """3.5: The documented default in config.example.json must equal the shipped
    DEFAULT_ROOM_LIBRARIES in code, compared in a test rather than by eye.
    """
    example_path = ROOT / "config.example.json"
    assert example_path.is_file(), f"config.example.json not found at {example_path}"
    example_cfg = json.loads(example_path.read_text(encoding="utf-8"))

    assert "room_libraries" in example_cfg, "config.example.json missing 'room_libraries' key"
    example_libraries = example_cfg["room_libraries"]

    assert example_libraries == DEFAULT_ROOM_LIBRARIES, (
        f"Documented default {example_libraries!r} does not match code default {DEFAULT_ROOM_LIBRARIES!r}"
    )


def test_edge_branch_empty_registry(tmp_path: Path):
    """Edge branch: Explicit empty registry ('room_libraries': []) produces no libraries and no rooms."""
    config = {"room_libraries": []}
    libs = load_room_libraries(config=config, data_dir=tmp_path)
    assert libs == []
    rooms = get_registered_rooms(config=config, data_dir=tmp_path)
    assert rooms == []


def test_edge_branch_two_libraries(tmp_path: Path):
    """Edge branch: Multiple libraries are loaded and aggregated."""
    room_a = [{"key": "a1", "label": "A1", "manner": "candid", "place": "A1 look", "offers": "bench", "verdict": "u"}]
    room_b = [{"key": "b1", "label": "B1", "manner": "candid", "place": "B1 look", "offers": "stool", "verdict": "u"}]

    (tmp_path / "lib-a-rooms-seed.json").write_text(json.dumps(room_a), encoding="utf-8")
    (tmp_path / "lib-b-rooms-seed.json").write_text(json.dumps(room_b), encoding="utf-8")

    config = {
        "room_libraries": [
            {"name": "lib_a", "seed_file": "lib-a-rooms-seed.json", "enabled": True, "weight": 2.0},
            {"name": "lib_b", "seed_file": "lib-b-rooms-seed.json", "enabled": True, "weight": 3.5},
        ]
    }
    libs = load_room_libraries(config=config, data_dir=tmp_path)
    assert len(libs) == 2
    assert libs[0]["name"] == "lib_a"
    assert libs[0]["weight"] == 2.0
    assert libs[1]["name"] == "lib_b"
    assert libs[1]["weight"] == 3.5

    all_rooms = get_registered_rooms(config=config, data_dir=tmp_path)
    assert len(all_rooms) == 2
    assert [r["key"] for r in all_rooms] == ["a1", "b1"]


def test_edge_branch_zero_weight(tmp_path: Path):
    """Edge branch: A weight of 0 is preserved as 0.0 and not overridden by default weight."""
    fixture_rooms = [{"key": "z1", "label": "Z1", "manner": "candid", "place": "Z1 look", "offers": "chair", "verdict": "u"}]
    seed_file = "zero-rooms-seed.json"
    (tmp_path / seed_file).write_text(json.dumps(fixture_rooms), encoding="utf-8")

    config = {
        "room_libraries": [
            {"name": "zero_weight_lib", "seed_file": seed_file, "enabled": True, "weight": 0}
        ]
    }
    libs = load_room_libraries(config=config, data_dir=tmp_path)
    assert len(libs) == 1
    assert libs[0]["weight"] == 0.0


def test_seed_file_not_a_json_list_raises_value_error(tmp_path: Path):
    """A seed file containing JSON that is not a list raises ValueError."""
    seed_file = "invalid-rooms-seed.json"
    (tmp_path / seed_file).write_text('{"not": "a list"}', encoding="utf-8")
    config = {
        "room_libraries": [
            {"name": "invalid", "seed_file": seed_file, "enabled": True, "weight": 1.0}
        ]
    }
    with pytest.raises(ValueError) as exc_info:
        load_room_libraries(config=config, data_dir=tmp_path)
    assert "must contain a JSON list of rooms" in str(exc_info.value)


def test_enabled_only_false_includes_disabled_library_rooms(tmp_path: Path):
    """When enabled_only=False, disabled library rooms are included in lib['rooms']."""
    fixture_rooms = [{"key": "r1", "label": "R1", "manner": "candid", "place": "R1 look", "offers": "bench", "verdict": "u"}]
    seed_file = "disabled-rooms-seed.json"
    (tmp_path / seed_file).write_text(json.dumps(fixture_rooms), encoding="utf-8")
    config = {
        "room_libraries": [
            {"name": "disabled_lib", "seed_file": seed_file, "enabled": False, "weight": 1.0}
        ]
    }
    libs = load_room_libraries(config=config, data_dir=tmp_path, enabled_only=False)
    assert len(libs) == 1
    assert libs[0]["enabled"] is False
    assert libs[0]["rooms"] == fixture_rooms


def test_resolve_data_dir_handles_relative_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """resolve_data_dir correctly resolves relative paths against ROOT and honors IDEVGEN_DATA_DIR."""
    from room_registry import resolve_data_dir

    # Explicit relative path
    resolved_rel = resolve_data_dir(data_dir="custom_data")
    assert resolved_rel == ROOT / "custom_data"

    # Environment variable override
    monkeypatch.setenv("IDEVGEN_DATA_DIR", str(tmp_path / "env_data"))
    resolved_env = resolve_data_dir()
    assert resolved_env == tmp_path / "env_data"

    # Config override when env var is unset
    monkeypatch.delenv("IDEVGEN_DATA_DIR", raising=False)
    resolved_cfg = resolve_data_dir(config={"data_dir": "cfg_data"})
    assert resolved_cfg == ROOT / "cfg_data"

