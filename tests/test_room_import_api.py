"""The room import route and the config field that keeps its registry alive.

4.9. Three things are asserted here that no other test can reach:

- `POST /api/rooms/import` runs the one import implementation and writes the
  seed file AND the registry entry that names it. `import_source` mutates the
  config dict it is handed; the route is what persists it, and a seed on disk
  with no registry entry is exactly the state
  `room_registry.verify_registry_disk_agreement` exists to refuse.
- A missing translation comes back as 422 carrying the WHOLE uncovered list.
  One item would be a screen that sends the translator back for another round
  per string.
- `room_libraries` is on `ConfigIn`. `save_config` writes `model_dump()` over
  config.json, so a key absent from the schema is a key deleted on the next
  save - the first Setup save after an import would otherwise delete every
  registered library and break the agreement check.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import main
from backend.room_registry import (
    DEFAULT_ROOM_LIBRARIES,
    verify_registry_disk_agreement,
)


@pytest.fixture(autouse=True)
def _restore_config():
    """These tests rewrite live globals; put them back so test order stays free."""
    saved = (main.CONFIG, main.COMFY_OUTPUT, main.LORA_DIR, main.comfy.url,
             main.runner.comfy_output_dir)
    yield
    (main.CONFIG, main.COMFY_OUTPUT, main.LORA_DIR, main.comfy.url,
     main.runner.comfy_output_dir) = saved


def _zh(hex_codes: list[str]) -> str:
    """Build a non-English string from hex escapes, so no non-ASCII glyph is in
    this file. Same helper as `tests/test_importer.py`, for the same reason."""
    escape_seq = "".join(chr(92) + "u" + code for code in hex_codes)
    return escape_seq.encode("ascii").decode("unicode_escape")


def _write_source(source_dir: Path, items: list[dict], mapping: dict) -> Path:
    """One declared library plus its translation map. `workplace_scenes` is a
    declared library whose only destination is `workplace-scenes-rooms-seed.json`."""
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "workplace_scenes.json").write_text(
        json.dumps({"library": "workplace_scenes", "items": items},
                   ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    map_file = source_dir / "translation_map.json"
    map_file.write_text(
        json.dumps(mapping, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return map_file


def _empty_registry_config() -> dict:
    """A config whose registry starts empty, so a `tmp_path` data directory that
    has never held `candid-rooms-seed.json` is not asked to produce it."""
    return {"comfy_url": "http://127.0.0.1:8188", "comfy_output_dir": "",
            "lora_dir": "", "data_dir": "data", "room_libraries": []}


def test_the_route_writes_the_seed_and_persists_its_registry_entry(client, tmp_path):
    """The seed file and the registry entry that names it are one operation.

    Persisted, not merely in memory: the route writes config.json back. The
    proof is the agreement check itself, re-run against the config as it was
    READ OFF DISK - a registry the route only kept in a live global would pass
    every in-process assertion and lose the library on the next start.
    """
    main.CONFIG = _empty_registry_config()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    map_file = _write_source(tmp_path / "source", [
        {"identifier": "work_office_01", "label": "Office",
         "theme": "modern office room with glass partitions and a wide desk"},
        {"identifier": "work_meeting_02", "label": "Meeting Room",
         "theme": "boardroom with a large wooden conference table"},
    ], {})

    r = client.post("/api/rooms/import", json={
        "source_dir": str(tmp_path / "source"),
        "map_path": str(map_file),
        "data_dir": str(data_dir),
    })
    assert r.status_code == 200, r.text
    report = r.json()
    # Three accepted, two written: the file's own header is an entry the guard
    # accepts and the merge then skips for carrying no theme, which is what
    # `skipped_empty` reports. Asserted rather than rounded off, so a header
    # that started landing as an empty room would fail here.
    assert (report["accepted"], report["refused"]) == (3, 0)
    assert (report["skipped_empty"], report["skipped_empty_identifiers"]) == (
        1, ["workplace_scenes"])
    assert (report["created"], report["updated"], report["written"]) == (2, 0, 2)
    dest = report["destinations"]["workplace-scenes-rooms-seed.json"]
    assert dest["created_keys"] == ["work-office-01", "work-meeting-02"]

    seed = data_dir / "workplace-scenes-rooms-seed.json"
    assert seed.is_file()
    assert {row["identifier"] for row in json.loads(seed.read_text(encoding="utf-8"))} == {
        "work_office_01", "work_meeting_02"}

    on_disk = json.loads(main.CONFIG_PATH.read_text(encoding="utf-8"))
    assert [lib["seed_file"] for lib in on_disk["room_libraries"]] == [
        "workplace-scenes-rooms-seed.json"]
    # The whole point of writing the config: the pair agrees for the next start.
    verify_registry_disk_agreement(config=on_disk, data_dir=data_dir)


def test_a_missing_translation_is_422_carrying_every_uncovered_string(client, tmp_path):
    """Not the first item - the screen's job is to show the whole worklist.

    And nothing is written: the run is refused over the whole upload before any
    seed file is touched, so the data directory is still empty afterwards.
    """
    main.CONFIG = _empty_registry_config()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    first, second = _zh(["5ba2", "5385"]), _zh(["4f1a", "8bae"])
    map_file = _write_source(tmp_path / "source", [
        {"identifier": "work_office_01", "label": first,
         "theme": "modern office room with glass partitions and a wide desk"},
        {"identifier": "work_meeting_02", "label": second,
         "theme": "boardroom with a large wooden conference table"},
    ], {})

    before = main.CONFIG_PATH.read_bytes()
    r = client.post("/api/rooms/import", json={
        "source_dir": str(tmp_path / "source"),
        "map_path": str(map_file),
        "data_dir": str(data_dir),
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "translation_missing"
    assert [(u["identifier"], u["field"], u["string"]) for u in detail["uncovered"]] == [
        ("work_office_01", "label", first),
        ("work_meeting_02", "label", second),
    ]

    assert list(data_dir.iterdir()) == []
    assert main.CONFIG["room_libraries"] == []
    assert main.CONFIG_PATH.read_bytes() == before


def test_both_paths_are_required_with_no_default(client):
    """The CLI refuses a missing one; so does the route. A machine path is the
    operator's to type and there is no sensible one to guess."""
    assert client.post("/api/rooms/import", json={}).status_code == 422
    assert client.post("/api/rooms/import",
                       json={"source_dir": "somewhere"}).status_code == 422
    assert client.post("/api/rooms/import",
                       json={"map_path": "somewhere"}).status_code == 422


def test_a_bad_source_directory_is_refused_without_its_message_carrying_prose(
        client, tmp_path):
    main.CONFIG = _empty_registry_config()
    r = client.post("/api/rooms/import", json={
        "source_dir": str(tmp_path / "absent"), "map_path": str(tmp_path / "absent.json")})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str)


def test_a_registered_room_library_survives_a_setup_save(client, tmp_path):
    """The root cause 4.9 makes live.

    `save_config` writes `model_dump()` over config.json, so `room_libraries`
    has to be a field on `ConfigIn` or the first Setup save after an import
    deletes every library the import registered - leaving seed files on disk
    that no entry names, which `verify_registry_disk_agreement` refuses.
    """
    out = tmp_path / "out"
    out.mkdir()
    libs = [{"name": "workplace_scenes",
             "seed_file": "workplace-scenes-rooms-seed.json",
             "enabled": True, "weight": 1.0}]
    r = client.patch("/api/config", json={
        "comfy_url": "http://127.0.0.1:8188", "comfy_output_dir": str(out),
        "lora_dir": "", "data_dir": "data", "room_libraries": libs})
    assert r.status_code == 200

    assert main.CONFIG["room_libraries"] == libs
    assert json.loads(main.CONFIG_PATH.read_text(encoding="utf-8"))["room_libraries"] == libs
    assert client.get("/api/config").json()["room_libraries"] == libs


def test_a_save_that_omits_the_registry_writes_the_shipped_default(client, tmp_path):
    """The default is the shipped registry, not an empty list: a config that
    never carried the key is saved as what the app was already using, and the
    nine candid rooms do not vanish because somebody pressed Save."""
    out = tmp_path / "out"
    out.mkdir()
    client.patch("/api/config", json={
        "comfy_url": "http://127.0.0.1:8188", "comfy_output_dir": str(out),
        "lora_dir": "", "data_dir": "data"})
    assert main.CONFIG["room_libraries"] == DEFAULT_ROOM_LIBRARIES
def test_a_refusal_still_persists_the_registry_for_the_seed_it_wrote(client, tmp_path):
    """The route must not widen the disagreement it is refusing.

    `verify_registry_disk_agreement` runs at the END of `import_source`, after
    the seeds are written, and a direction-2 failure reaches the route as a
    ValueError. Drop the mutated config on that path and the seed the import
    just wrote is left on disk with nothing naming it - one more unregistered
    file for the next run to refuse over, and no way back out from inside the
    app. So the refusal stands and the registry half is persisted anyway.
    """
    main.CONFIG = _empty_registry_config()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # A room seed on disk that no registry entry names: the state direction 2
    # refuses, and the state a Setup save made before `room_libraries` reached
    # `ConfigIn` would have left behind.
    (data_dir / "stray-rooms-seed.json").write_text("[]", encoding="utf-8")
    map_file = _write_source(tmp_path / "source", [
        {"identifier": "work_office_01", "label": "Office",
         "theme": "modern office room with glass partitions and a wide desk"},
    ], {})

    r = client.post("/api/rooms/import", json={
        "source_dir": str(tmp_path / "source"),
        "map_path": str(map_file),
        "data_dir": str(data_dir),
    })
    assert r.status_code == 422
    assert "stray-rooms-seed.json" in r.text

    # The import got as far as writing its own seed before the check ran.
    seed = data_dir / "workplace-scenes-rooms-seed.json"
    assert seed.is_file()

    # And the entry naming it survived the refusal, on disk and not merely live.
    on_disk = json.loads(main.CONFIG_PATH.read_text(encoding="utf-8"))
    assert "workplace-scenes-rooms-seed.json" in [
        lib["seed_file"] for lib in on_disk["room_libraries"]]


def test_the_rooms_route_serves_what_is_on_disk_and_states_why_it_serves_nothing(
        client, tmp_path):
    """6.4: the picker reads the imported rooms at runtime, and an absent
    library is a sentence rather than an error.

    The seeds an import writes are untracked, so "the registry names a file
    that is not there" is the ordinary state of a fresh clone, a second machine
    and any checkout where nobody ran the import. `load_room_libraries` raises
    on exactly that, which is right where an import is being verified and wrong
    for a screen: raising here is a picker that will not open instead of a
    picker holding the nine tracked rooms.

    So all three states are asserted on one route: a registered library with no
    seed on disk, the same library once its seed exists, and one switched off.
    Each contributes no rooms for a DIFFERENT reason, and the reason is what the
    operator reads to know which.
    """
    saved_dir = main.DATA_DIR
    try:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        main.DATA_DIR = data_dir
        main.CONFIG = dict(_empty_registry_config(), room_libraries=[
            {"name": "workplace_scenes",
             "seed_file": "workplace-scenes-rooms-seed.json",
             "enabled": True, "weight": 1.0},
        ])

        # Nothing imported here yet.
        body = client.get("/api/rooms").json()
        assert body["rooms"] == []
        library = body["libraries"][0]
        assert library["name"] == "workplace_scenes" and library["rooms"] == 0
        assert "workplace-scenes-rooms-seed.json" in library["reason"]
        # The reason crosses the wire, so it carries no machine path.
        assert str(tmp_path) not in json.dumps(body)

        # The same registry, once the seed exists.
        (data_dir / "workplace-scenes-rooms-seed.json").write_text(json.dumps([
            {"key": "work-office-01", "label": "Office", "manner": "candid",
             "place": "modern office room with glass partitions and a wide desk"},
        ]), encoding="utf-8")
        body = client.get("/api/rooms").json()
        assert [r["key"] for r in body["rooms"]] == ["work-office-01"]
        assert body["libraries"][0]["reason"] == ""

        # And switched off: present on disk, offered to nobody, and said so.
        main.CONFIG["room_libraries"][0]["enabled"] = False
        body = client.get("/api/rooms").json()
        assert body["rooms"] == []
        assert "switched off" in body["libraries"][0]["reason"]
    finally:
        main.DATA_DIR = saved_dir
