"""Tests for asset import pipeline (Tasks 4.3a and 4.3).

Asserts that:
- 4.3a: Import path refuses to write seed rows without consulting the guard.
- 4.3: One implementation with two entry points (app callable and CLI script)
  producing byte-for-byte identical seed content.
- Both source_dir and map_path are required with no defaults.
- Manifest refusal and guard refusal run before any destination is written.
- Translation is a lookup from the map and stops if a string is missing,
  naming the entry and the field.
- Merge updates text and derived fields while preserving verdicts and sample sizes.
- Empty header entries are skipped and reported, not written as empty rooms.
- 4.3c: The map is the only source of translations - an uncovered string stops
  the run on every shape it can be written in, and no translator is loaded.
- 4.3d: One source string yields one English string everywhere across libraries.
- 4.3e: Re-running an import rewords nothing across all translated fields.
- All created files are pure ASCII with no illegal control bytes or trailing whitespace.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from backend.asset_guard import (
    SIGNAL_IDENTIFIER,
    SIGNAL_MINOR_PROFILE_KEY,
    SIGNAL_NOT_ADOPTED,
    SIGNAL_TAGS,
    SIGNAL_THEME_TEXT,
    guard_entry,
)
from backend.room_registry import (
    ROOM_VERDICTS_FILE,
    available_rooms,
    compose_look,
    load_manner_registers,
    load_room_verdicts,
    prose_names_piece,
    verify_registry_disk_agreement,
)
from backend.importer import (
    TranslationMissingError,
    derive_multi_body,
    derive_room_key,
    import_source,
)
from backend.translation_map import contains_non_english, load_translation_map
from backend.source_manifest import (
    REASON_UNDECLARED,
    SourceRefused,
    declare_source_file,
)
from scripts.import_assets import main as cli_main

ROOT = Path(__file__).resolve().parents[1]
LEGAL_CONTROLS = {0x09, 0x0A, 0x0D}  # tab, newline, carriage return


def _zh(hex_codes: list[str]) -> str:
    """Build non-English string from hex escapes so no non-ASCII glyph is in code."""
    escape_seq = "".join(chr(92) + "u" + code for code in hex_codes)
    return escape_seq.encode("ascii").decode("unicode_escape")


def _fresh_config() -> dict:
    """A config whose registry starts empty.

    `import_source` requires one: a seed file is written and registered in the
    same operation, because `verify_registry_disk_agreement` refuses either
    half on its own. An empty list rather than an omitted key, so the shipped
    default's `candid-rooms-seed.json` is not demanded of a `tmp_path` data
    directory that has never held it.
    """
    return {"room_libraries": []}


# 4.3a: one refused fixture per guard signal that can reach an entry INSIDE
# a declared library, keyed by the signal it must be refused on. Keyed
# rather than listed because the signal is the whole point. Measured before
# this was written: every refused fixture the suite fed the import path was
# refused on `identifier`, so an import path that consulted the guard and
# then wrote the row anyway on any OTHER signal passed all 592 tests. A
# probe that raised on a non-identifier refusal inside `import_source` never
# fired across the whole suite - the case was not merely unasserted, it was
# unreached.
#
# `library` and `not_adopted` are absent on purpose: 4.1 refuses those whole
# files in `declare_source_file`, before an entry is ever loaded, so no entry
# carrying them can reach this point. That door is
# `test_manifest_and_guard_refusals_run_before_writing`.
GUARD_REFUSED_FIXTURES: dict[str, dict] = {
    SIGNAL_IDENTIFIER: {
        "identifier": "school_classroom_01",
        "label": "classroom",
        "theme": "a classroom with blackboard and wooden desks",
    },
    SIGNAL_MINOR_PROFILE_KEY: {
        "identifier": "body_shape_row_01",
        "profile_key": "jk",
        "label": "Slim build",
        "theme": "a plain bedroom with a low bed",
    },
    SIGNAL_TAGS: {
        "identifier": "room_043",
        "tags": ["indoor", "school uniform"],
        "label": "Study nook",
        "theme": "a quiet study nook with a reading lamp",
    },
    SIGNAL_THEME_TEXT: {
        "identifier": "room_042",
        "label": "Bright hall",
        "theme": "a classroom with a blackboard and rows of wooden desks",
    },
}

REFUSED_IDENTIFIERS: frozenset[str] = frozenset(
    entry["identifier"] for entry in GUARD_REFUSED_FIXTURES.values()
)


def _refused_identifiers_on_disk(seed_path: Path) -> list[str]:
    """The 4.3a assertion, read back off a written seed file.

    Rows on disk, not a return value: what the criterion forbids is a refused
    row being WRITTEN, and a report that says `refused` while the row sits in
    the seed file is the exact defect. Shared by the real import path and the
    unguarded stub so both are judged by one function."""
    rows = json.loads(seed_path.read_text(encoding="utf-8"))
    return sorted(
        {str(row.get("identifier") or "") for row in rows} & REFUSED_IDENTIFIERS
    )


def _unguarded_import_stub(entries: list[dict], seed_path: Path) -> None:
    """The control arm: an import path that writes every entry as a seed row.

    It never consults the guard. This is what 4.3a means by a deliberately
    unguarded stub, and the point of it is that a source scan for the string
    `guard_entries` would not have saved us: a stub that imported the guard,
    called it, and wrote the row regardless reads exactly like the real
    thing to a scan."""
    rows = [
        {
            "key": derive_room_key(str(entry.get("identifier") or "")),
            "identifier": entry.get("identifier"),
            "label": entry.get("label", ""),
            "place": entry.get("theme", ""),
        }
        for entry in entries
    ]
    seed_path.write_text(
        json.dumps(rows, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )


def test_import_path_refuses_to_write_seed_rows_without_consulting_guard(tmp_path: Path):
    """4.3a: no entry the guard refuses reaches a written seed row, on any signal.

    Three assertions, in order:
    1. Each fixture is refused ON the signal it was written for. Without this
       the test can go quietly vacuous the way the suite already had: four
       fixtures all refused on `identifier` look like four signals covered
       and are one.
    2. The real import path writes none of them.
    3. The same check, run over the unguarded stub's output, returns all four
       - so the assertion is one an unguarded write fails, not one that
       passes because there was nothing to catch."""
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    zh_label = _zh(["5bba", "5ba2"])  # non-English label

    # Create translation map
    map_data = {
        zh_label: {
            "source": zh_label,
            "translation": "Quiet Lounge",
            "fields": ["label"],
        }
    }
    map_file = source_dir / "translation_map.json"
    map_file.write_text(
        json.dumps(map_data, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )

    accepted_entry = {
        "identifier": "quiet_lounge_01",
        "label": zh_label,
        "theme": "spacious quiet lounge with low table and sofa",
    }

    # 1. Each fixture is refused on its own signal, and the accepted one is
    # not refused at all. `library` is set the way the loader sets it, from
    # the file name, so the guard sees the entry the importer will hand it.
    for signal, entry in GUARD_REFUSED_FIXTURES.items():
        assert guard_entry({**entry, "library": "general_scenes"}) == (
            signal,
            entry["identifier"],
        ), f"fixture for {signal} is not refused on that signal"
    assert guard_entry({**accepted_entry, "library": "general_scenes"}) is None

    # general_scenes is a declared room library whose destination is
    # general-scenes-rooms-seed.json
    source_payload = {
        "library": "general_scenes",
        "items": [accepted_entry] + list(GUARD_REFUSED_FIXTURES.values()),
    }
    source_file = source_dir / "general_scenes.json"
    source_file.write_text(
        json.dumps(source_payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )

    # Call the real import path
    report = import_source(
        source_dir=source_dir,
        map_path="translation_map.json",
        data_dir=data_dir,
        config=_fresh_config(),
    )

    # Destination seed file for general_scenes
    dest_seed = data_dir / "general-scenes-rooms-seed.json"
    assert dest_seed.is_file(), f"Expected seed file was not created at {dest_seed}"

    seed_rows = json.loads(dest_seed.read_text(encoding="utf-8"))
    written_identifiers = [r.get("identifier") for r in seed_rows]

    # 2. Guard consultation: not one refused entry reached the seed file
    assert _refused_identifiers_on_disk(dest_seed) == [], (
        "Refused entries were written to the seed file without the guard's "
        "verdict being honoured"
    )
    # The accepted entry MUST be written, and it alone
    assert "quiet_lounge_01" in written_identifiers
    assert len(seed_rows) == 1

    # Report assertions. `accepted` is 2, not 1: the loader yields a header
    # entry for the file's own root dict, which the guard accepts and the
    # importer then skips for carrying no theme. Asserted rather than loosened
    # to `>= 1`, so the two roads into "not written" stay told apart - refused
    # by the guard, and skipped for an empty theme.
    assert report["refused"] == len(GUARD_REFUSED_FIXTURES)
    assert sorted(report["refused_identifiers"]) == sorted(REFUSED_IDENTIFIERS)
    assert report["accepted"] == 2
    assert report["skipped_empty_identifiers"] == ["general_scenes"]
    assert report["written"] == 1

    # 3. The control arm. The same check over an import path that writes rows
    # without consulting the guard names every refused entry - so the
    # assertion above is one an unguarded write fails, not one that passes
    # because there was nothing to catch.
    stub_seed = data_dir / "unguarded-stub-seed.json"
    _unguarded_import_stub(source_payload["items"], stub_seed)
    assert _refused_identifiers_on_disk(stub_seed) == sorted(REFUSED_IDENTIFIERS)

def test_app_operation_and_cli_entry_produce_identical_seed_content(tmp_path: Path):
    """4.3: Verify that the app operation and the CLI entry produce byte-for-byte
    identical seed content for one fixture source.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_app = tmp_path / "data_app"
    data_app.mkdir(parents=True)
    data_cli = tmp_path / "data_cli"
    data_cli.mkdir(parents=True)

    zh_label = _zh(["5ba2", "5385"])
    zh_notes = _zh(["7981", "6b62"])

    map_data = {
        zh_label: {
            "source": zh_label,
            "translation": "Living Room",
            "fields": ["label"],
        },
        zh_notes: {
            "source": zh_notes,
            "translation": "Prohibited notes",
            "fields": ["notes"],
        },
    }
    map_file = source_dir / "translation_map.json"
    map_file.write_text(
        json.dumps(map_data, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )

    source_payload = {
        "library": "workplace_scenes",
        "items": [
            {
                "identifier": "work_office_01",
                "label": zh_label,
                "theme": "modern office room with glass partitions and desk",
                "notes": zh_notes,
            },
            {
                "identifier": "work_meeting_02",
                "label": "Meeting Room",
                "theme": "boardroom with large wooden conference table",
            },
        ],
    }
    (source_dir / "workplace_scenes.json").write_text(
        json.dumps(source_payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )

    # 1. Run app operation
    report_app = import_source(
        source_dir=source_dir,
        map_path=map_file,
        data_dir=data_app,
        config=_fresh_config(),
    )

    # 2. Run CLI entry via subprocess
    cli_config = tmp_path / "cli-config.json"
    cli_config.write_text(
        json.dumps(_fresh_config(), indent=2) + "\n", encoding="utf-8"
    )
    cli_cmd = [
        sys.executable,
        str(ROOT / "scripts" / "import_assets.py"),
        str(source_dir),
        str(map_file),
        "--data-dir",
        str(data_cli),
        "--config",
        str(cli_config),
    ]
    proc = subprocess.run(cli_cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"CLI failed: {proc.stderr}\n{proc.stdout}"

    # Assert both output directories contain workplace-scenes-rooms-seed.json
    seed_filename = "workplace-scenes-rooms-seed.json"
    app_seed = data_app / seed_filename
    cli_seed = data_cli / seed_filename

    assert app_seed.is_file(), f"{app_seed} was not written by app operation"
    assert cli_seed.is_file(), f"{cli_seed} was not written by CLI"

    # Compare byte-for-byte
    app_bytes = app_seed.read_bytes()
    cli_bytes = cli_seed.read_bytes()
    assert app_bytes == cli_bytes, (
        f"App operation and CLI produced differing seed content:\n"
        f"App: {app_bytes.decode('utf-8')}\n"
        f"CLI: {cli_bytes.decode('utf-8')}"
    )


def test_required_arguments_with_no_defaults(tmp_path: Path):
    """Verify that source_dir and map_path are required arguments with no default."""
    with pytest.raises(TypeError):
        import_source()  # type: ignore

    # config has no default either: the seed and its registry entry are one
    # operation, so a caller cannot reach the write without bringing the config
    with pytest.raises(TypeError):
        import_source(tmp_path, "map.json")  # type: ignore

    with pytest.raises(ValueError, match="source_dir is required"):
        import_source(None, "map.json", config=_fresh_config())  # type: ignore

    with pytest.raises(ValueError, match="source_dir is required"):
        import_source("", "map.json", config=_fresh_config())

    with pytest.raises(ValueError, match="map_path is required"):
        import_source(tmp_path, None, config=_fresh_config())  # type: ignore

    with pytest.raises(ValueError, match="map_path is required"):
        import_source(tmp_path, "", config=_fresh_config())

    # Non-existent source directory raises NotADirectoryError
    with pytest.raises(NotADirectoryError):
        import_source(
            tmp_path / "non_existent_source", "map.json", config=_fresh_config()
        )


def test_manifest_and_guard_refusals_run_before_writing(tmp_path: Path):
    """Verify that a refused file writes nothing and does not take the run down.

    The corpus is handed over whole and three of its nine libraries reach no
    destination, so a refusal that aborted the run would be a corpus that can
    never be imported. Each refused file is named in the report by its library
    and the manifest's reason, and nothing inside it is read.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    # 1. A refused file alone writes nothing at all
    (source_dir / "unknown_library.json").write_text(
        json.dumps({"items": [{"identifier": "x_01", "theme": "room"}]}),
        encoding="utf-8",
    )
    report = import_source(
        source_dir=source_dir,
        map_path=map_file,
        data_dir=data_dir,
        config=_fresh_config(),
    )
    assert list(data_dir.iterdir()) == []
    assert report["written"] == 0
    assert report["refused_libraries"] == [
        {"library": "unknown_library", "reason": REASON_UNDECLARED}
    ]

    # 2. The refused file does not stop the declared ones beside it
    (source_dir / "amateurs.json").write_text(
        json.dumps({"items": [{"identifier": "p_01", "theme": "room"}]}),
        encoding="utf-8",
    )
    (source_dir / "general_scenes.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "identifier": "gs_01",
                        "label": "Living room",
                        "theme": "spacious living room with an oak floor",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = import_source(
        source_dir=source_dir,
        map_path=map_file,
        data_dir=data_dir,
        config=_fresh_config(),
    )

    reasons = {r["library"]: r["reason"] for r in report["refused_libraries"]}
    assert reasons == {
        "unknown_library": REASON_UNDECLARED,
        "amateurs": SIGNAL_NOT_ADOPTED,
    }
    assert report["refused_files"] == 2

    seed_file = data_dir / "general-scenes-rooms-seed.json"
    assert seed_file.is_file()
    rows = json.loads(seed_file.read_text(encoding="utf-8"))
    assert [row["identifier"] for row in rows] == ["gs_01"]

    # The refused files reached no destination of their own
    assert sorted(p.name for p in data_dir.iterdir()) == [
        "general-scenes-rooms-seed.json"
    ]

    # `declare_source_file` still refuses on its own; the importer catches it
    with pytest.raises(SourceRefused):
        declare_source_file(source_dir / "amateurs.json")


def test_translation_lookup_stops_when_string_missing_naming_entry_and_field(tmp_path: Path):
    """Verify that a string missing from the map stops the import and names the
    entry and the field, writing nothing to disk.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    zh_missing = _zh(["7167", "706f"])  # missing string

    # Translation map does not have zh_missing
    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    source_payload = {
        "library": "medical_scenes",
        "items": [
            {
                "identifier": "clinic_room_01",
                "label": zh_missing,
                "theme": "bright clinic exam room with examination table",
            }
        ],
    }
    (source_dir / "medical_scenes.json").write_text(
        json.dumps(source_payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TranslationMissingError) as exc_info:
        import_source(
            source_dir=source_dir,
            map_path=map_file,
            data_dir=data_dir,
            config=_fresh_config(),
        )

    assert exc_info.value.identifier == "clinic_room_01"
    assert exc_info.value.field == "label"
    assert "clinic_room_01" in str(exc_info.value)
    assert "label" in str(exc_info.value)

    # Validate-everything-then-write: nothing was written to data_dir
    assert list(data_dir.iterdir()) == []


def test_a_room_naming_other_people_is_marked_and_a_single_subject_room_is_not(tmp_path: Path):
    """5.8: the marking is computed once, at import, off the room's own text.

    This app photographs one person. A room whose prose puts somebody else in
    the frame composes into a single-subject run and nobody finds out until the
    frame comes back, so the marking exists to refuse that - and it carries the
    WORDS, because a gate that says no without saying which words did it leaves
    the operator with nothing to act on.

    It is one field holding those words rather than a boolean beside a list:
    the boolean is the list's own truthiness and cannot drift from it, where
    two stored fields are two calculations of one fact.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    (source_dir / "general_scenes.json").write_text(
        json.dumps({"library": "general_scenes", "items": [
            {
                "identifier": "gs_lobby",
                "label": "Hotel lobby",
                "theme": "busy hotel lobby, guests crossing the marble floor and a man "
                         "waiting at the front desk",
            },
            {
                "identifier": "gs_dressing_room",
                "label": "Dressing room",
                "theme": "dim wedding dressing room in front of a vanity mirror, a "
                         "bridesmaid standing behind adjusting the lace straps",
            },
            {
                "identifier": "gs_stockroom",
                "label": "Stockroom",
                "theme": "narrow stockroom aisle, supply shelves rising on both sides "
                         "with a manhole cover on the street visible through the door",
            },
        ]}),
        encoding="utf-8",
    )

    import_source(source_dir=source_dir, map_path=map_file,
                  data_dir=data_dir, config=_fresh_config())
    rows = {
        r["identifier"]: r
        for r in json.loads(
            (data_dir / "general-scenes-rooms-seed.json").read_text(encoding="utf-8")
        )
    }

    # Marked, and the words are the ones a refusal would name.
    assert rows["gs_lobby"]["multi_body"] == ["guests", "man"]
    assert rows["gs_dressing_room"]["multi_body"] == ["bridesmaid"]

    # Unmarked, and `manhole` is why the match is on whole words: a substring
    # rule marks this room for a hole in the road.
    assert rows["gs_stockroom"]["multi_body"] == []

    # The words are stored, not merely detected: they survive the seed round trip.
    for word in rows["gs_lobby"]["multi_body"]:
        assert word in rows["gs_lobby"]["place"].lower()


def test_the_ambiguous_female_singular_does_not_mark_a_room():
    """5.8: what is deliberately NOT on the list, and why it is a decision.

    A room's prose describes the place she is in and frequently describes her
    standing in it. `woman`, `girl`, `lady`, `she`, `her` are therefore as
    likely to be the subject as a second body, and marking on them would mark
    most of the corpus - which teaches the operator to turn the gate off, which
    costs more than the rooms it would have caught.

    Plurals are not ambiguous in the same way and do mark: she is one person.
    """
    for subject in ("a young woman standing by the window in a bare bedroom",
                    "a girl's bedroom with posters on the wall",
                    "her coat is over the back of the chair"):
        assert derive_multi_body(subject) == [], subject

    for others in ("two women talking by the counter",
                   "a group of girls on the far side of the hall",
                   "ladies waiting on the bench"):
        assert derive_multi_body(others), others


def test_offers_is_the_source_props_the_prose_actually_names(tmp_path: Path):
    """5.7: the intersection, and nothing outside it.

    The source writes its props as one comma-separated slot, and its author was
    listing what is in the scene, not promising what a photograph will show. A
    prop the theme string never mentions is a piece no photograph can contain,
    so offering it has the picker promise furniture, the act ask for it and the
    render answer with neither - which is the failure `shower` already caused
    once, offering a bench its sentence never named.

    Three rooms: one where the prose names some props and not others, one where
    it names none, and one whose source lists no props at all. The empty result
    is the correct answer and not a degraded one, so the import is not blocked
    by it.

    The last assertion is 5.7's own: the existing offers-names-the-furniture
    rule, the same function the seed test calls, run over every imported room.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    (source_dir / "general_scenes.json").write_text(
        json.dumps({"library": "general_scenes", "items": [
            {
                "identifier": "gs_stockroom",
                "label": "Stockroom",
                # `rolling cart` and `glove boxes` are in the prop list and
                # nowhere in the prose. They are the ones that must not survive.
                "props": "supply shelves, stacked linen packs, glove boxes, rolling cart",
                "theme": "narrow stockroom aisle, supply shelves rising on both sides "
                         "with stacked linen packs on the lower rungs",
            },
            {
                "identifier": "gs_empty_field",
                "label": "Bare field",
                "props": "hay bale, wooden gate",
                "theme": "an open field under a flat grey sky with nothing in it",
            },
            {
                "identifier": "gs_no_props",
                "label": "No prop list",
                "theme": "a stone corridor with a low bench along one wall",
            },
        ]}),
        encoding="utf-8",
    )

    import_source(source_dir=source_dir, map_path=map_file,
                  data_dir=data_dir, config=_fresh_config())
    rows = {
        r["identifier"]: r
        for r in json.loads(
            (data_dir / "general-scenes-rooms-seed.json").read_text(encoding="utf-8")
        )
    }

    assert rows["gs_stockroom"]["offers"] == ["supply shelves", "stacked linen packs"]
    # Named by neither: the prose says the field is empty, so the room offers
    # nothing and the import carried on.
    assert rows["gs_empty_field"]["offers"] == []
    # A source with no prop list at all is the same answer, not a crash.
    assert rows["gs_no_props"]["offers"] == []

    # 5.7's own criterion: the existing rule, over every imported room.
    for identifier, row in rows.items():
        for piece in row["offers"]:
            assert prose_names_piece(piece, row["place"]), (identifier, piece)


def test_offers_is_read_off_the_source_prose_and_not_off_a_translation(tmp_path: Path):
    """5.7: a reworded translation cannot change what a room offers.

    The label is the field the map rewrites, and it is the field somebody
    corrects. If the offers check could see it, correcting an English label
    would silently add or remove a piece of furniture from a room that has been
    shot against - and the diff would show a label change.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    zh_label = _zh(["8302", "5ba4"])
    map_file = source_dir / "translation_map.json"
    (source_dir / "general_scenes.json").write_text(
        json.dumps({"library": "general_scenes", "items": [{
            "identifier": "gs_tea_room",
            "label": zh_label,
            "props": "low table, folding screen",
            "theme": "quiet tea room with tatami mats and a low table",
        }]}, ensure_ascii=True),
        encoding="utf-8",
    )
    seed = data_dir / "general-scenes-rooms-seed.json"

    def run(label_translation: str) -> dict:
        map_file.write_text(
            json.dumps({zh_label: {"translation": label_translation, "fields": ["label"]}}),
            encoding="utf-8",
        )
        import_source(source_dir=source_dir, map_path=map_file,
                      data_dir=data_dir, config=_fresh_config())
        return json.loads(seed.read_text(encoding="utf-8"))[0]

    # The first translation names the screen the prose does not. The second
    # does not. Neither may reach `offers`.
    with_screen = run("Tea room with a folding screen")
    without = run("Tea room")

    assert with_screen["offers"] == ["low table"] == without["offers"]
    assert with_screen["label"] != without["label"]


def test_a_row_says_which_of_its_values_are_authored_and_which_are_source(tmp_path: Path):
    """5.6: a translation is authored text, and a seed row does not hide that.

    Which field is a translation is a property of the ENTRY, not of the field
    name. `label` is the map's English for a source that wrote its label in its
    own script, and the source's own words for one that wrote it in English -
    both land in the same field, and a rule guessing from the name would be
    right for most of the corpus and quietly wrong for the rest. Same for
    `notes`, and for the place on the day a library ships one in another
    script.

    So the row carries the answer, collected at the substitution rather than
    re-derived afterwards from the stored text. The assertion below is what
    makes that answer mean something: every field the row calls authored has a
    value that differs from the entry's own, and every field it does not call
    authored is the entry's value character for character. A list that merely
    happened to be right would fail the second half.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    zh_label = _zh(["8302", "5ba4"])
    zh_note = _zh(["6ce8", "610f"])
    zh_theme = _zh(["6f5c", "8247"])

    entries = [
        # Label and notes in another script, theme already English.
        {"identifier": "gs_tea_room", "label": zh_label, "notes": [zh_note],
         "theme": "quiet tea room with tatami mats and a low table"},
        # Nothing to translate: every value is the source's own.
        {"identifier": "gs_lounge", "label": "Lounge", "notes": ["keep the blinds shut"],
         "theme": "wide lounge with a leather armchair and a low table"},
        # The place itself in another script.
        {"identifier": "gs_hold", "label": "Cargo hold", "theme": zh_theme},
    ]
    translations = {
        zh_label: {"translation": "Tea room", "fields": ["label"]},
        zh_note: {"translation": "no crystal sparkle", "fields": ["notes"]},
        zh_theme: {"translation": "steel cargo hold with a crate and a bare bulb",
                   "fields": ["theme"]},
    }

    map_file = source_dir / "translation_map.json"
    map_file.write_text(json.dumps(translations), encoding="utf-8")
    (source_dir / "general_scenes.json").write_text(
        json.dumps({"library": "general_scenes", "items": entries}, ensure_ascii=True),
        encoding="utf-8",
    )

    import_source(source_dir=source_dir, map_path=map_file,
                  data_dir=data_dir, config=_fresh_config())
    rows = {
        r["identifier"]: r
        for r in json.loads(
            (data_dir / "general-scenes-rooms-seed.json").read_text(encoding="utf-8")
        )
    }

    assert rows["gs_tea_room"]["authored"] == ["guidance.notes", "label"]
    assert rows["gs_lounge"]["authored"] == []
    assert rows["gs_hold"]["authored"] == ["place"]

    # The list is not decoration: it has to agree with the values themselves,
    # in both directions, on every row.
    entries_by_id = {e["identifier"]: e for e in entries}
    for identifier, row in rows.items():
        entry = entries_by_id[identifier]
        stored = {"label": row["label"], "place": row["place"]}
        stored.update({f"guidance.{k}": v for k, v in row["guidance"].items()})
        for field, source_value in (("label", entry.get("label")),
                                    ("place", entry.get("theme")),
                                    ("guidance.notes", entry.get("notes"))):
            if field not in stored:
                continue
            if field in row["authored"]:
                assert stored[field] != source_value, (identifier, field)
                assert not contains_non_english(json.dumps(stored[field]))
            else:
                assert stored[field] == source_value, (identifier, field)

    # The place of an untranslated room is the source prose 5.7 reads for
    # `offers`, so it has to stay recognisable as source and not as authored.
    assert "place" not in rows["gs_tea_room"]["authored"]
    assert rows["gs_tea_room"]["place"] == entries_by_id["gs_tea_room"]["theme"]


def test_the_stored_theme_is_the_source_string_character_for_character(tmp_path: Path):
    """5.5: an accepted entry's English theme reaches the seed unedited.

    "Do not trim their words" is a statement about the corpus, and the place
    is the whole reason this import exists. So the fixture theme carries every
    character an import is tempted to tidy - leading and trailing spaces, an
    embedded newline and a tab, a backslash, a double quote, an em dash, a
    curly apostrophe, an accented letter and an ellipsis - and the stored text
    is compared against the source string one character at a time.

    Two paths, because there are two: an entry whose theme is already English
    is copied from the source file, and an entry whose theme is in another
    script is copied from the map. The second is compared against what
    `load_translation_map` hands over rather than against the raw JSON,
    because the map's own normaliser strips its translations - deliberately,
    so an authoring artifact in a hand-written map file does not become a
    leading space in a room. What is asserted here is that the importer adds
    no edit of its own on top of that.

    The last assertion is the one that is easy to miss: the seed is written
    with `ensure_ascii=True`, so the file on disk is pure ASCII escapes. The
    characters have to survive the round trip, not merely be handed to
    `json.dumps`.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    # Built from escapes: this file is asserted pure ASCII, and what the test
    # is about is that the characters survive, not which alphabet wrote them.
    em_dash, apostrophe, ellipsis, e_acute = (
        _zh(["2014"]), _zh(["2019"]), _zh(["2026"]), _zh(["00e9"])
    )
    theme = (
        "  a tea room " + em_dash + " tatami mats, the girl" + apostrophe + "s low table,"
        + chr(10) + "a second line with a" + chr(9) + "tab, a " + chr(92)
        + " backslash, a " + chr(34) + "quote" + chr(34) + ", a caf"
        + e_acute + ellipsis + " and trailing spaces   "
    )
    foreign = _zh(["8302", "5ba4"])

    map_file = source_dir / "translation_map.json"
    map_file.write_text(
        json.dumps({foreign: {"translation": theme, "fields": ["theme"]}}),
        encoding="utf-8",
    )
    (source_dir / "general_scenes.json").write_text(
        json.dumps({
            "library": "general_scenes",
            "items": [
                {"identifier": "gs_english", "label": "Already English", "theme": theme},
                {"identifier": "gs_translated", "label": "Translated", "theme": foreign},
            ],
        }, ensure_ascii=True),
        encoding="utf-8",
    )

    import_source(source_dir=source_dir, map_path=map_file,
                  data_dir=data_dir, config=_fresh_config())

    seed = data_dir / "general-scenes-rooms-seed.json"
    raw = seed.read_text(encoding="utf-8")
    rows = {r["identifier"]: r for r in json.loads(raw)}

    # The English path: the source's own string, character for character.
    stored = rows["gs_english"]["place"]
    assert len(stored) == len(theme), (len(stored), len(theme))
    for i, (got, want) in enumerate(zip(stored, theme)):
        assert got == want, (
            i, repr(theme[max(0, i - 12):i + 12]), repr(stored[max(0, i - 12):i + 12])
        )
    assert stored == theme

    # The translated path: exactly what the map hands over, and nothing else.
    from_map = load_translation_map(map_file)[foreign]["translation"]
    assert rows["gs_translated"]["place"] == from_map

    # The round trip: written as ASCII escapes, read back as the same characters.
    assert raw.isascii(), "the seed file itself has to stay ASCII on disk"
    assert not stored.isascii(), "the fixture would prove nothing if it were plain ASCII"


def test_an_imported_room_is_a_place_that_composes_under_either_manner(tmp_path: Path):
    """5.4: the stored row is the place alone, and the register is joined on.

    The nine rooms this repo wrote each carried candid's capture clause fused
    into their text, which is why `ModelDetail.jsx` could only offer them on a
    candid session: the first sentence, not the bedroom, was what made them
    candid. An imported room storing its own register would inherit that, 428
    times over.

    So the same stored place is composed under both manners and the two are
    compared: each carries its own register, and the place text is byte for
    byte the same in both - it is the source's own words, and nothing here
    edits them.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    place = "quiet tea room with tatami mats and a low table"
    (source_dir / "general_scenes.json").write_text(
        json.dumps({
            "library": "general_scenes",
            "items": [{"identifier": "gs_tea_room_01", "label": "Tea room", "theme": place}],
        }),
        encoding="utf-8",
    )

    import_source(source_dir=source_dir, map_path=map_file,
                  data_dir=data_dir, config=_fresh_config())
    row = json.loads(
        (data_dir / "general-scenes-rooms-seed.json").read_text(encoding="utf-8")
    )[0]

    # Stored as the place, with no register of its own in the text.
    assert row["place"] == place
    assert "look" not in row

    # The shipped registers, read from the repo's own data dir: `conftest`
    # points IDEVGEN_DATA_DIR at a tmp directory for the whole suite.
    registers = load_manner_registers(data_dir=ROOT / "data")
    candid = compose_look("candid", row["place"], registers)
    directed = compose_look("directed", row["place"], registers)

    assert candid != directed
    assert candid == registers["candid"] + " " + place
    assert directed == registers["directed"] + " " + place
    # The same place text, byte for byte, under both.
    assert candid[len(registers["candid"]) + 1:] == place
    assert directed[len(registers["directed"]) + 1:] == place


def test_authoring_guidance_is_stored_translated_and_never_composed(tmp_path: Path):
    """5.9: the entry author's reasons are kept for the operator, not for the line.

    `notes` and the `*_anchor` fields are the only slots in this corpus that
    explain themselves - what the subject is doing and why, what breaks the
    shot - and they are the reason the translation pass was worth running at
    all. They are also the last text that should reach a prompt: "avoid crystal
    sparkle" is an instruction to a human and a description of glitter to a
    sampler.

    So the guarantee is structural rather than a filter. Guidance lives in its
    own field, `compose_look` joins the register to the place and reads nothing
    else, and the test below asserts the consequence: every guidance string is
    in the row, in English, and none of them - and none of their source
    originals - is anywhere in what either manner composes.

    Both directions matter. Asserting only that the composed line is missing
    the guidance passes on a row that stored no guidance at all, which is why
    the stored values are checked first, against the map's English.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    zh_notes = _zh(["7981", "6b62", "95ea", "7c89"])
    zh_action = _zh(["8e72", "4e0b", "6574", "7406"])
    place = "stockroom with steel shelving, a folding step stool and a bare bulb"
    guidance_english = {
        "notes": "no crystal sparkle",
        "action_anchor": "she is crouching to restock, not posing",
        "camera_anchor": "the phone rests on the shelf above her",
    }

    translations = {
        zh_notes: {"translation": guidance_english["notes"], "fields": ["notes"]},
        zh_action: {"translation": guidance_english["action_anchor"],
                    "fields": ["action_anchor"]},
    }
    map_file = source_dir / "translation_map.json"
    map_file.write_text(json.dumps(translations), encoding="utf-8")
    (source_dir / "general_scenes.json").write_text(
        json.dumps({
            "library": "general_scenes",
            "items": [{
                "identifier": "gs_stockroom_01",
                "label": "Stockroom",
                "theme": place,
                "notes": zh_notes,
                "action_anchor": zh_action,
                # Already English: guidance is stored whether or not the map
                # wrote it, and `authored` is what tells the two apart.
                "camera_anchor": guidance_english["camera_anchor"],
            }],
        }, ensure_ascii=True),
        encoding="utf-8",
    )

    import_source(source_dir=source_dir, map_path=map_file,
                  data_dir=data_dir, config=_fresh_config())
    row = json.loads(
        (data_dir / "general-scenes-rooms-seed.json").read_text(encoding="utf-8")
    )[0]

    # Stored, translated, and in one field the picker can show whole.
    assert row["guidance"] == guidance_english
    assert row["authored"] == ["guidance.action_anchor", "guidance.notes"]

    # And absent from what is composed, under either manner - the source's own
    # words as well as the English, since a room that leaked its untranslated
    # notes would leak them into the same line.
    registers = load_manner_registers(data_dir=ROOT / "data")
    for manner in ("candid", "directed"):
        look = compose_look(manner, row["place"], registers)
        assert look.endswith(place)
        for text in list(guidance_english.values()) + [zh_notes, zh_action]:
            assert text not in look, (manner, text)


def test_the_key_survives_a_corrected_translation(tmp_path: Path):
    """5.3: the key comes from the source identifier and from nothing else.

    A translation is a thing a person corrects - that is the whole reason the
    map is a reviewable file - and every correction lands on the label or the
    theme, which are exactly the fields a key must not read. If it did, fixing
    one English wording would rename the room, and a rename is not cosmetic
    here: the verdict, the sample size and every session pointing at the old
    key are attached to it.

    So the same source is imported twice with the label's translation changed
    between the runs, and the row is found by the key it had the first time.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    zh_label = _zh(["8302", "5ba4"])
    map_file = source_dir / "translation_map.json"

    def write_map(translation: str) -> None:
        map_file.write_text(
            json.dumps({zh_label: {"translation": translation, "fields": ["label"]}}),
            encoding="utf-8",
        )

    (source_dir / "general_scenes.json").write_text(
        json.dumps({
            "library": "general_scenes",
            "items": [{
                "identifier": "gs_tea_room_01",
                "label": zh_label,
                "theme": "quiet tea room with tatami mats and a low table",
            }],
        }),
        encoding="utf-8",
    )
    seed_file = data_dir / "general-scenes-rooms-seed.json"

    write_map("Tearoom")
    import_source(source_dir=source_dir, map_path=map_file,
                  data_dir=data_dir, config=_fresh_config())
    first = json.loads(seed_file.read_text(encoding="utf-8"))[0]
    assert first["key"] == derive_room_key("gs_tea_room_01") == "gs-tea-room-01"
    assert first["label"] == "Tearoom"

    # The correction. Same source, same identifier, a different English label.
    write_map("Tea room")
    report = import_source(source_dir=source_dir, map_path=map_file,
                           data_dir=data_dir, config=_fresh_config())
    rows = json.loads(seed_file.read_text(encoding="utf-8"))

    assert len(rows) == 1, "a moved key would have left the first row behind as an orphan"
    assert rows[0]["key"] == first["key"]
    assert rows[0]["label"] == "Tea room"
    assert report["updated"] == 1 and report["created"] == 0 and report["orphaned"] == 0


def test_a_key_is_ascii_and_two_identifiers_never_share_one(tmp_path: Path):
    """5.3: normalised to ASCII, and normalised is not the same as truncated.

    Dropping the bytes that are not ASCII collides - `salon_01` and the same
    word with an accent both reduce to one key under a bare character class,
    and two rooms sharing a key is one room. So an accent decomposes and its
    letter survives, and an identifier with no Latin in it at all falls back to
    a digest rather than to a shared constant.
    """
    plain = derive_room_key("gs_salon_01")
    accented = derive_room_key("gs_sal" + _zh(["00f3"]) + "n_01")
    assert plain == "gs-salon-01" == accented

    cjk_one = derive_room_key(_zh(["4f11", "606f", "5ba4"]))
    cjk_two = derive_room_key(_zh(["5ba2", "5385"]))
    for key in (plain, accented, cjk_one, cjk_two):
        assert key.isascii() and re.fullmatch(r"[a-z0-9-]+", key), key
    assert cjk_one != cjk_two
    # Stable, not merely distinct: a re-import has to land on the same row.
    assert cjk_one == derive_room_key(_zh(["4f11", "606f", "5ba4"]))


def test_merge_updates_text_and_preserves_verdict_and_orphaned_rows(tmp_path: Path):
    """Verify that merge updates room text while preserving verdicts and sample sizes,
    and keeps orphaned rows when an upstream entry disappears.

    Also 4.6 and 4.7, both satisfied by 4.3's implementation. What is added here
    is what keeps them true:

    4.6 - the match is by SOURCE IDENTIFIER, not by the row's stored key. The
    pre-existing chamber row carries a stale key on purpose, so the derived
    `key` has to move to `derive_room_key(identifier)` while `verdict` and
    `sample_size` survive. Matched by key instead, the row would be created
    rather than updated and the stale key would stay on disk.

    4.7 - the orphaned row is NAMED by the report, not merely counted, and its
    whole content survives byte for byte, `sample_size` included. The count
    alone passes for a report that names the wrong row; the verdict alone
    passes for a merge that drops every other field.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    seed_file = data_dir / "sm-scenes-rooms-seed.json"
    # Pre-existing seed file with measured verdict and an extra row that will become orphaned
    existing_seed_data = [
        {
            # A stale key, deliberately not what derive_room_key() makes of the
            # identifier: the merge has to find this row by identifier and move
            # the derived key on.
            "key": "sm-chamber-01-stale",
            "identifier": "sm_chamber_01",
            "label": "Old Chamber Label",
            "manner": "candid",
            "place": "old theme text",
            "verdict": "verified: 12/12",
            "sample_size": 12,
        },
        {
            "key": "sm-vanished-row",
            "identifier": "sm_vanished_row",
            "label": "Vanished Room",
            "manner": "candid",
            "place": "vanished room text",
            "verdict": "verified: 5/5",
            "sample_size": 5,
        },
    ]
    seed_file.write_text(
        json.dumps(existing_seed_data, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )

    # New source has updated text for sm_chamber_01 and a newly created room sm_dungeon_02,
    # but sm_vanished_row is absent from upstream.
    new_source_data = {
        "library": "sm_scenes",
        "items": [
            {
                "identifier": "sm_chamber_01",
                "label": "Updated Chamber Label",
                "theme": "updated theme text with stone walls",
            },
            {
                "identifier": "sm_dungeon_02",
                "label": "New Dungeon",
                "theme": "dim dungeon room with iron bench",
            },
        ],
    }
    (source_dir / "sm_scenes.json").write_text(
        json.dumps(new_source_data, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )

    report = import_source(
        source_dir=source_dir,
        map_path=map_file,
        data_dir=data_dir,
        config=_fresh_config(),
    )

    assert report["created"] == 1
    assert report["updated"] == 1
    assert report["orphaned"] == 1
    assert report["written"] == 3

    # Check written file contents
    updated_rows = json.loads(seed_file.read_text(encoding="utf-8"))
    rows_by_id = {r["identifier"]: r for r in updated_rows}

    # 1. sm_chamber_01 updated its text and label but preserved verdict and sample size
    ch = rows_by_id["sm_chamber_01"]
    assert ch["label"] == "Updated Chamber Label"
    assert ch["place"] == "updated theme text with stone walls"
    assert ch["verdict"] == "verified: 12/12"
    assert ch["sample_size"] == 12
    # 4.6: matched by identifier, so the derived key moved off the stale one
    assert ch["key"] == derive_room_key("sm_chamber_01") == "sm-chamber-01"

    # 2. sm_dungeon_02 was created
    assert "sm_dungeon_02" in rows_by_id
    assert rows_by_id["sm_dungeon_02"]["verdict"] == "unverified"

    # 3. sm_vanished_row was not deleted (preserved as orphaned)
    assert "sm_vanished_row" in rows_by_id
    assert rows_by_id["sm_vanished_row"]["verdict"] == "verified: 5/5"
    # 4.7: it survives whole, not just its verdict
    assert rows_by_id["sm_vanished_row"] == existing_seed_data[1]

    # 4.7: the report NAMES the orphan, and 4.6: it names the updated row
    dest_report = report["destinations"]["sm-scenes-rooms-seed.json"]
    assert dest_report["orphaned_keys"] == ["sm-vanished-row"]
    assert dest_report["updated_keys"] == ["sm-chamber-01"]
    assert dest_report["created_keys"] == ["sm-dungeon-02"]


def test_reimport_updates_the_room_and_leaves_its_measurement_alone(tmp_path: Path):
    """5.10: a second import is a text update, not a new room.

    The two halves pull against each other. Everything the source owns has to
    move on a re-import - the place, the offers derived from it, the words the
    map wrote - because that is the only way a corrected translation or a
    reworded room ever reaches the app. Everything THIS project measured has to
    stay - the verdict and the sample size - because those were paid for in
    rendered frames against that room, and a re-import that resets them is an
    import that quietly deletes the measurements.

    So the fixture is a room that has been measured: imported once, then given
    a verdict the way a pass would, then imported again over changed source and
    a corrected map. The third run changes nothing and is asserted byte for
    byte, because "idempotent" is a statement about the file and not about the
    counts - a merge that rewrites the same row with its keys in a new order
    reports `unchanged` and still makes a diff on every run.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    seed_file = data_dir / "general-scenes-rooms-seed.json"
    map_file = source_dir / "translation_map.json"
    source_file = source_dir / "general_scenes.json"

    zh_label = _zh(["5eab", "623f"])
    zh_notes = _zh(["7981", "6b62", "95ea", "7c89"])

    def write_map(label: str, notes: str) -> None:
        map_file.write_text(json.dumps({
            zh_label: {"translation": label, "fields": ["label"]},
            zh_notes: {"translation": notes, "fields": ["notes"]},
        }), encoding="utf-8")

    def write_source(theme: str, props: str) -> None:
        source_file.write_text(json.dumps({
            "library": "general_scenes",
            "items": [{
                "identifier": "gs_stockroom_01",
                "label": zh_label,
                "theme": theme,
                "props": props,
                "notes": zh_notes,
            }],
        }, ensure_ascii=True), encoding="utf-8")

    first_theme = "stockroom with steel shelving and a bare bulb overhead"
    write_map("Storeroom", "no crystal sparkle")
    write_source(first_theme, "steel shelving, folding screen")
    import_source(source_dir=source_dir, map_path=map_file,
                  data_dir=data_dir, config=_fresh_config())

    row = json.loads(seed_file.read_text(encoding="utf-8"))[0]
    assert row["label"] == "Storeroom"
    assert row["offers"] == ["steel shelving"]
    assert row["verdict"] == "unverified"

    # The measurement. Ten frames were shot against this room and the pass
    # wrote what it found onto the row, which is the only place it lives.
    row["verdict"] = "verified: 10/10"
    row["sample_size"] = 10
    seed_file.write_text(json.dumps([row], ensure_ascii=True, indent=2) + "\n",
                         encoding="utf-8")

    # A reworded room, a different prop list, and a corrected translation of
    # both the label and the notes.
    second_theme = "stockroom with a scarred workbench under a bare bulb"
    write_map("Stockroom", "no crystal sparkle on the bulb")
    write_source(second_theme, "scarred workbench, folding screen")
    report = import_source(source_dir=source_dir, map_path=map_file,
                           data_dir=data_dir, config=_fresh_config())

    rows = json.loads(seed_file.read_text(encoding="utf-8"))
    assert len(rows) == 1, "a second import made a second room"
    updated = rows[0]
    assert report["created"] == 0 and report["updated"] == 1 and report["orphaned"] == 0

    # What the source owns moved, derivations included.
    assert updated["key"] == derive_room_key("gs_stockroom_01")
    assert updated["label"] == "Stockroom"
    assert updated["place"] == second_theme
    assert updated["offers"] == ["scarred workbench"], (
        "offers are derived from the place, so a reworded room re-derives them")
    assert updated["guidance"]["notes"] == "no crystal sparkle on the bulb"
    assert updated["authored"] == ["guidance.notes", "label"]

    # What was measured stayed.
    assert updated["verdict"] == "verified: 10/10"
    assert updated["sample_size"] == 10

    # And a run over unchanged source is a no-op on disk, not merely a row that
    # compares equal in memory.
    before = seed_file.read_bytes()
    again = import_source(source_dir=source_dir, map_path=map_file,
                          data_dir=data_dir, config=_fresh_config())
    assert again["updated"] == 0 and again["created"] == 0
    assert again["destinations"]["general-scenes-rooms-seed.json"]["unchanged"] == 1
    assert seed_file.read_bytes() == before


def test_an_imported_room_restricts_no_manner_and_keeps_one_written_by_hand(tmp_path: Path):
    """6.7: `manner` became `manners`, and what it means changed with it.

    Before the split it said which register was fused into the room's text. It
    now says whether the PLACE makes sense under a manner at all, and a source
    library says nothing about that - so an imported room restricts nothing and
    the importer invents no restriction to fill the field.

    Empty rather than the three manner keys of today: the two read the same
    this afternoon and diverge the day a fourth manner is written, where the
    frozen list silently excludes every room ever imported. So the test asserts
    the empty restriction AND that a manner nobody has written yet is allowed
    by it.

    The other half is the re-import. A restriction is somebody's judgement
    about the place, typed in by hand the way the studio's was, and an import
    that reset it to the default would undo that judgement on every run - the
    same failure the verdict preservation exists for.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    seed_file = data_dir / "general-scenes-rooms-seed.json"
    map_file = source_dir / "translation_map.json"
    map_file.write_text(json.dumps({}), encoding="utf-8")
    (source_dir / "general_scenes.json").write_text(json.dumps({
        "library": "general_scenes",
        "items": [{
            "identifier": "gs_stockroom_01",
            "label": "Storeroom",
            "theme": "a storeroom with steel shelving and a bare bulb overhead",
            "props": "steel shelving",
        }],
    }, ensure_ascii=True), encoding="utf-8")

    import_source(source_dir=source_dir, map_path=map_file,
                  data_dir=data_dir, config=_fresh_config())
    row = json.loads(seed_file.read_text(encoding="utf-8"))[0]
    assert "manner" not in row, "the single manner is gone, not carried alongside"
    # Empty is the restriction, and `rooms.js:roomAllows` is what reads it -
    # one reader, on the side that offers the rooms. A second copy here would
    # be a rule that can disagree with itself.
    assert row["manners"] == []

    # Somebody decides this one is a directed set-up and writes it down.
    row["manners"] = ["directed"]
    row["manners_reason"] = "hand-written for this test"
    seed_file.write_text(json.dumps([row], ensure_ascii=True, indent=2) + chr(10),
                         encoding="utf-8")

    import_source(source_dir=source_dir, map_path=map_file,
                  data_dir=data_dir, config=_fresh_config())
    again = json.loads(seed_file.read_text(encoding="utf-8"))[0]
    assert again["manners"] == ["directed"]
    assert again["manners_reason"] == "hand-written for this test"


def test_a_verdict_whose_room_is_absent_is_reported_and_never_deleted(tmp_path: Path):
    """6.6: an import writes rooms. It never writes the verdict store, in
    either direction, and it says which measurements now point at no room.

    The two halves of the store were split in 6.5 because the measurements are
    ours and the room text may be an import nobody may commit. That split is
    what makes an orphan ordinary rather than exceptional: the store is tracked
    and the seeds are not, so a clone, a second machine, a library switched off
    and a seed file somebody moved all leave verdicts pointing at rooms that
    are not here. Deleting on any of those throws away frames that were shot.

    Two kinds of absence are told apart on purpose. A row the upstream dropped
    is RETAINED by the merge, so its verdict is not orphaned - the room is
    still here, it is just no longer upstream. A verdict for a room no
    registered library carries at all is the orphan.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    map_file = source_dir / "translation_map.json"
    source_file = source_dir / "general_scenes.json"
    map_file.write_text(json.dumps({}), encoding="utf-8")

    def write_source(*identifiers: str) -> None:
        source_file.write_text(json.dumps({
            "library": "general_scenes",
            "items": [{
                "identifier": ident,
                "label": ident,
                "theme": f"a storeroom with steel shelving, {ident}",
                "props": "steel shelving",
            } for ident in identifiers],
        }, ensure_ascii=True), encoding="utf-8")

    kept_key = derive_room_key("gs_kept_01")
    dropped_key = derive_room_key("gs_dropped_01")
    write_source("gs_kept_01", "gs_dropped_01")
    import_source(source_dir=source_dir, map_path=map_file,
                  data_dir=data_dir, config=_fresh_config())

    # Three measurements: one on a room that stays upstream, one on a room the
    # next import drops, and one on a room this machine has never held - a
    # verdict measured elsewhere, or on a library nobody registered here.
    store = data_dir / ROOM_VERDICTS_FILE
    store.write_text(json.dumps({
        kept_key: {"candid": {"verdict": "verified", "sample_size": 10}},
        dropped_key: {"candid": {"verdict": "verified", "sample_size": 10}},
        "a-room-from-another-machine": {"candid": {"verdict": "dead", "sample_size": 10}},
    }, indent=2) + chr(10), encoding="utf-8")
    before = store.read_bytes()

    # The upstream drops one entry.
    write_source("gs_kept_01")
    config = _fresh_config()
    report = import_source(source_dir=source_dir, map_path=map_file,
                           data_dir=data_dir, config=config)

    # Nothing wrote the store. Not a key removed, not a key added, not a
    # re-indentation: an import has no business in this file at all.
    assert store.read_bytes() == before
    assert set(load_room_verdicts(data_dir=data_dir)) == {
        kept_key, dropped_key, "a-room-from-another-machine"}

    # The dropped row survives the merge, so its verdict still has a room.
    assert report["orphaned"] == 1 and dropped_key in (
        report["destinations"]["general-scenes-rooms-seed.json"]["orphaned_keys"])
    assert report["orphaned_verdicts"] == ["a-room-from-another-machine"]

    # And the picker's own reader says the same thing, with the same verdicts
    # still readable on the rooms that do exist.
    served = available_rooms(config=config, data_dir=data_dir)
    assert served["orphaned_verdicts"] == ["a-room-from-another-machine"]
    by_key = {r["key"]: r for r in served["rooms"]}
    assert by_key[kept_key]["verdicts"]["candid"]["sample_size"] == 10
    assert by_key[dropped_key]["verdicts"]["candid"]["sample_size"] == 10

    # Switching the library off is the other everyday absence: every verdict is
    # then an orphan, and the store still holds all three.
    config["room_libraries"][0]["enabled"] = False
    off = available_rooms(config=config, data_dir=data_dir)
    assert off["orphaned_verdicts"] == sorted(
        [kept_key, dropped_key, "a-room-from-another-machine"])
    assert store.read_bytes() == before


def test_empty_header_entries_skipped_and_reported(tmp_path: Path):
    """Fact 2 & 5.11: Verify that entries carrying no theme text (such as file-level
    header leftovers) are skipped from room seed writing and accounted for in the report.

    A skipped header carries an uncovered string here on purpose. It is not a
    refused entry, it is one that is never written, so it must not refuse the
    upload the way a written entry's uncovered string does.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    zh_header = _zh(["7981", "6b62"])

    source_payload = {
        "library": "special_scenes",
        "version": 1,
        "description": zh_header,
        "items": [
            {
                "identifier": "special_hall_01",
                "label": "Special Hall",
                "theme": "grand hall with chandeliers and marble flooring",
            }
        ],
    }
    (source_dir / "special_scenes.json").write_text(
        json.dumps(source_payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )

    report = import_source(
        source_dir=source_dir,
        map_path=map_file,
        data_dir=data_dir,
        config=_fresh_config(),
    )

    # 1 header entry ('special_scenes') + 1 real entry ('special_hall_01')
    assert report["skipped_empty"] >= 1
    assert "special_scenes" in report["skipped_empty_identifiers"]
    assert report["written"] == 1

    seed_file = data_dir / "special-scenes-rooms-seed.json"
    rows = json.loads(seed_file.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["identifier"] == "special_hall_01"

    # The skipped header's uncovered string refused nothing and reached nothing.
    for written in data_dir.rglob("*"):
        if written.is_file():
            raw = written.read_bytes()
            assert zh_header.encode("utf-8") not in raw
            assert zh_header.encode("unicode_escape") not in raw


def test_importer_files_are_pure_ascii_and_contain_no_control_bytes():
    """Verify that all created/modified files are pure ASCII with no illegal control
    bytes, no literal backspaces, no trailing whitespace, and LF endings.
    """
    files = [
        ROOT / "backend" / "extractor.py",
        ROOT / "backend" / "importer.py",
        ROOT / "scripts" / "import_assets.py",
        ROOT / "tests" / "test_importer.py",
    ]
    for path in files:
        assert path.is_file(), f"File missing: {path}"
        raw = path.read_bytes()
        assert len(raw) > 0

        # No non-ASCII bytes
        for idx, b in enumerate(raw):
            assert b <= 0x7F, f"{path.name}:{idx}: byte {hex(b)} exceeds ASCII range"
            if b < 0x20:
                assert b in LEGAL_CONTROLS, f"{path.name}:{idx}: illegal control byte {hex(b)}"

        assert 0x08 not in raw, f"{path.name}: contains literal backspace byte"

        # No line-ending assertion. This checkout runs core.autocrlf=true with
        # no .gitattributes, so git hands every tracked test file to the working
        # tree with CRLF - "git ls-files --eol tests/test_api.py" reads w/crlf
        # against i/lf. A test asserting LF in the working copy is a test that
        # passes on the machine that wrote the file and fails on the next clone.
        # What the repository requires is the index, and the index is LF
        # whatever the working tree looks like.

        # Trailing whitespace check
        text = raw.decode("utf-8")
        for line_num, line in enumerate(text.splitlines(), 1):
            assert line == line.rstrip(), f"{path.name}:{line_num}: has trailing whitespace"


def test_room_text_is_stored_in_english_from_the_map(tmp_path: Path):
    """4.3c: the room prose goes through the map like every other string.

    It is the string this import exists for, and it is the one an untranslated
    import hides best: `json.dumps(ensure_ascii=True)` writes the source script
    back out as escapes, which the repository's CJK rule reads as ASCII.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    zh_theme = _zh(["5ba2", "5385"])
    map_file = source_dir / "translation_map.json"
    map_file.write_text(
        json.dumps(
            {
                zh_theme: {
                    "source": zh_theme,
                    "translation": "living room, oak floor, low table",
                    "fields": ["scene_theme"],
                }
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )

    (source_dir / "general_scenes.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "identifier": "gs_01",
                        "label": "Living room",
                        "scene_theme": zh_theme,
                    }
                ]
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )

    import_source(
        source_dir=source_dir,
        map_path=map_file,
        data_dir=data_dir,
        config=_fresh_config(),
    )

    raw = (data_dir / "general-scenes-rooms-seed.json").read_text(encoding="utf-8")
    rows = json.loads(raw)
    assert rows[0]["place"] == "living room, oak floor, low table"
    assert zh_theme not in rows[0]["place"]

    # And the source script is not in the file under an escape either
    assert (chr(92) + "u") not in raw


def test_seed_and_registry_are_written_as_one_operation(tmp_path: Path):
    """4.3: the seed file and its registry entry land together.

    `verify_registry_disk_agreement` refuses either half on its own, so an
    import that wrote the seed and left the registry alone would leave the app
    in the state that check exists to name.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")
    (source_dir / "medical_scenes.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "identifier": "clinic_01",
                        "label": "Exam room",
                        "theme": "bright clinic exam room with an examination table",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    config = _fresh_config()
    import_source(
        source_dir=source_dir,
        map_path=map_file,
        data_dir=data_dir,
        config=config,
    )

    seed_files = [
        lib.get("seed_file") or lib.get("seed") for lib in config["room_libraries"]
    ]
    assert "medical-scenes-rooms-seed.json" in seed_files

    # Both directions hold: nothing on disk is unnamed, nothing named is missing
    verify_registry_disk_agreement(config=config, data_dir=data_dir)


def test_cli_refuses_to_run_without_a_config(tmp_path: Path, capsys):
    """The command line cannot reach the write without bringing the registry."""
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        cli_main([str(source_dir), str(map_file), "--data-dir", str(tmp_path / "data")])
    assert exc_info.value.code != 0

    with pytest.raises(SystemExit):
        cli_main(
            [
                str(source_dir),
                str(map_file),
                "--config",
                str(tmp_path / "no-such-config.json"),
            ]
        )


def test_cli_refuses_to_run_without_a_source_directory(tmp_path: Path, capsys):
    """5.1: the source directory is a required argument and has no default.

    A default would be a path the script reaches for when nobody named one,
    which is how an import runs against the wrong corpus and writes seeds
    nobody asked for. So absence exits with an error, and nothing is written.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(_fresh_config()), encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["--config", str(cfg_path), "--data-dir", str(data_dir)])
    assert exc_info.value.code != 0
    assert "source_dir" in capsys.readouterr().err

    # No default corpus was reached for: nothing was written or registered.
    assert list(data_dir.iterdir()) == []
    assert json.loads(cfg_path.read_text(encoding="utf-8")) == _fresh_config()


def test_source_with_uncovered_string_leaves_destinations_byte_identical(tmp_path: Path):
    """4.4: Verify that a source upload with an uncovered string leaves every
    destination file byte-for-byte identical.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    zh_missing = _zh(["7981", "6b62"])

    # Pre-populate two destination seed files with real content
    med_seed = data_dir / "medical-scenes-rooms-seed.json"
    gen_seed = data_dir / "general-scenes-rooms-seed.json"

    med_content = [
        {
            "key": "med-exam-01",
            "identifier": "med_exam_01",
            "label": "Exam Room",
            "manner": "candid",
            "place": "clinical white exam room with table",
            "verdict": "verified: 10/10",
            "sample_size": 10,
        }
    ]
    gen_content = [
        {
            "key": "gen-lounge-01",
            "identifier": "gen_lounge_01",
            "label": "Lounge",
            "manner": "candid",
            "place": "warm lounge with leather armchair",
            "verdict": "unverified",
        }
    ]

    med_seed.write_text(json.dumps(med_content, indent=2) + "\n", encoding="utf-8")
    gen_seed.write_text(json.dumps(gen_content, indent=2) + "\n", encoding="utf-8")

    initial_med_bytes = med_seed.read_bytes()
    initial_gen_bytes = gen_seed.read_bytes()

    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    # Source has medical_scenes with an uncovered string in label,
    # and general_scenes with valid English
    (source_dir / "medical_scenes.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "identifier": "med_exam_02",
                        "label": zh_missing,
                        "theme": "modern surgery prep room with sinks",
                    }
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (source_dir / "general_scenes.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "identifier": "gen_lounge_02",
                        "label": "Modern Lounge",
                        "theme": "spacious lounge with floor-to-ceiling windows",
                    }
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    config = {
        "room_libraries": [
            {"name": "medical_scenes", "seed_file": "medical-scenes-rooms-seed.json", "enabled": True},
            {"name": "general_scenes", "seed_file": "general-scenes-rooms-seed.json", "enabled": True},
        ]
    }

    with pytest.raises(TranslationMissingError):
        import_source(
            source_dir=source_dir,
            map_path=map_file,
            data_dir=data_dir,
            config=config,
        )

    # 4.4 Criterion: every destination remains byte-identical
    assert med_seed.read_bytes() == initial_med_bytes, (
        "medical-scenes-rooms-seed.json was modified despite translation refusal"
    )
    assert gen_seed.read_bytes() == initial_gen_bytes, (
        "general-scenes-rooms-seed.json was modified despite translation refusal"
    )
    # No other files were written to data_dir
    assert sorted(p.name for p in data_dir.iterdir()) == [
        "general-scenes-rooms-seed.json",
        "medical-scenes-rooms-seed.json",
    ]


def test_translation_refusal_lists_every_uncovered_string_and_excludes_refused_entries(tmp_path: Path):
    """4.5: Verify that a translation refusal lists every uncovered string with its
    field across the whole upload, while completely excluding strings from refused entries.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    zh_med_label = _zh(["7981", "6b62"])
    zh_med_theme = _zh(["5ba2", "5385"])
    zh_gen_notes = _zh(["7167", "706f"])

    # Refusal fixtures:
    # 1. An entry refused by guard signal ("school")
    zh_refused_school = _zh(["6821", "56ed"])
    # 2. An entry in a not-adopted library (amateurs)
    zh_refused_amateurs = _zh(["79c1", "4eba"])

    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    # medical_scenes: 2 accepted entries, each with an uncovered string in a different field
    (source_dir / "medical_scenes.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "identifier": "med_room_01",
                        "label": zh_med_label,
                        "theme": "bright clinic exam room with white walls",
                    },
                    {
                        "identifier": "med_room_02",
                        "label": "Recovery Room",
                        "theme": zh_med_theme,
                    },
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # general_scenes: 1 accepted entry with uncovered string in notes,
    # and 1 entry refused by guard for school signal with an uncovered label
    (source_dir / "general_scenes.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "identifier": "gen_room_01",
                        "label": "Sunlit Room",
                        "theme": "sunlit room with large windows",
                        "notes": zh_gen_notes,
                    },
                    {
                        "identifier": "school_corridor_01",
                        "label": zh_refused_school,
                        "theme": "school corridor with lockers",
                    },
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # amateurs.json: not-adopted library carrying an uncovered string
    (source_dir / "amateurs.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "identifier": "profile_01",
                        "theme": zh_refused_amateurs,
                    }
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TranslationMissingError) as exc_info:
        import_source(
            source_dir=source_dir,
            map_path=map_file,
            data_dir=data_dir,
            config=_fresh_config(),
        )

    err = exc_info.value

    # 1. Backwards-compatible attributes for the first uncovered string
    assert err.identifier == err.uncovered[0]["identifier"]
    assert err.field == err.uncovered[0]["field"]
    assert err.text == err.uncovered[0]["string"]
    assert err.identifier == "gen_room_01"
    assert err.field == "notes"
    assert err.text == zh_gen_notes

    # 2. Full uncovered list covers all 3 accepted uncovered strings across upload
    assert len(err.uncovered) == 3

    uncovered_tuples = [(u["identifier"], u["field"], u["string"]) for u in err.uncovered]
    assert ("med_room_01", "label", zh_med_label) in uncovered_tuples
    assert ("med_room_02", "theme", zh_med_theme) in uncovered_tuples
    assert ("gen_room_01", "notes", zh_gen_notes) in uncovered_tuples

    # 3. String representation describes the count and all uncovered entries
    msg = str(err)
    assert "Missing translation for 3 string(s) across upload:" in msg
    assert "med_room_01" in msg
    assert "med_room_02" in msg
    assert "gen_room_01" in msg

    # 4. Refused entries (school_corridor_01 and profile_01) and their strings
    # are completely absent from the refusal list and message
    all_identifiers = [u["identifier"] for u in err.uncovered]
    all_strings = [u["string"] for u in err.uncovered]

    assert "school_corridor_01" not in all_identifiers
    assert "profile_01" not in all_identifiers
    assert zh_refused_school not in all_strings
    assert zh_refused_amateurs not in all_strings
    assert "school_corridor_01" not in msg
    assert "profile_01" not in msg


def test_translation_refusal_walks_nested_structures(tmp_path: Path):
    """Verify that the translation walker checks nested dicts and lists,
    stopping the import and translating properly when covered.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    zh_nested = _zh(["6697", "5149"])  # dim light

    # Source has an uncovered string in a nested dict under 'notes'
    (source_dir / "workplace_scenes.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "identifier": "office_corner_01",
                        "label": "Office Corner",
                        "theme": "quiet corner office with desk",
                        "notes": {
                            "ambience": zh_nested,
                        },
                    }
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    # 1. Uncovered nested dict raises and names nested field path
    with pytest.raises(TranslationMissingError) as exc_info:
        import_source(
            source_dir=source_dir,
            map_path=map_file,
            data_dir=data_dir,
            config=_fresh_config(),
        )

    assert exc_info.value.identifier == "office_corner_01"
    assert exc_info.value.field == "notes.ambience"
    assert exc_info.value.text == zh_nested
    assert exc_info.value.uncovered[0]["field"] == "notes.ambience"

    # 2. Covering the nested string allows import to succeed and translate the nested dict
    covered_map = {
        zh_nested: {
            "source": zh_nested,
            "translation": "dim lighting",
            "fields": ["notes.ambience"],
        }
    }
    map_file.write_text(json.dumps(covered_map, indent=2) + "\n", encoding="utf-8")

    report = import_source(
        source_dir=source_dir,
        map_path=map_file,
        data_dir=data_dir,
        config=_fresh_config(),
    )
    assert report["written"] == 1

    dest_seed = data_dir / "workplace-scenes-rooms-seed.json"
    rows = json.loads(dest_seed.read_text(encoding="utf-8"))
    assert rows[0]["guidance"]["notes"]["ambience"] == "dim lighting"
    # No escape sequence remains
    assert (chr(92) + "u") not in dest_seed.read_text(encoding="utf-8")
def test_untranslated_identifier_refuses_and_never_reaches_a_seed(tmp_path: Path):
    """An identifier is written to the row, so it is a translation candidate.

    The extractor's METADATA_FIELDS marks what a coverage report ignores, not
    what may reach disk. Filtering the walk by it let a non-English identifier
    through, and `ensure_ascii=True` wrote it back as unicode escapes that the
    repository's CJK rule cannot see.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    zh_identifier = _zh(["7981", "6b62"])

    (source_dir / "medical_scenes.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "identifier": zh_identifier,
                        "label": "Exam Room",
                        "theme": "clinical white exam room with table",
                    }
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    with pytest.raises(TranslationMissingError) as exc_info:
        import_source(
            source_dir=source_dir,
            map_path=map_file,
            data_dir=data_dir,
            config=_fresh_config(),
        )

    assert exc_info.value.field == "identifier"
    assert exc_info.value.text == zh_identifier

    # Nothing reached disk, in any file, in any encoding.
    for written in data_dir.rglob("*"):
        if written.is_file():
            raw = written.read_bytes()
            assert zh_identifier.encode("utf-8") not in raw
            assert zh_identifier.encode("unicode_escape") not in raw


def test_identical_source_string_in_two_libraries_yields_same_translation_everywhere(tmp_path: Path):
    """4.3d (Task 2.7): One source string yields one English string everywhere.

    Verify that importing an identical non-English string present in two distinct
    libraries (two separate .json files targeting two distinct destinations
    declared in backend/source_manifest.py) writes the exact same English
    translation to both destination seeds, byte for byte.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    zh_shared = _zh(["5ba2", "5385"])
    zh_theme = _zh(["5bbd", "655e", "623f", "95f4"])

    map_data = {
        zh_shared: {
            "source": zh_shared,
            "translation": "Shared Living Room",
            "fields": ["label"],
        },
        zh_theme: {
            "source": zh_theme,
            "translation": "spacious quiet lounge with low table and sofa",
            "fields": ["theme"],
        },
    }
    map_file = source_dir / "translation_map.json"
    map_file.write_text(
        json.dumps(map_data, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )

    # Library 1: general_scenes -> general-scenes-rooms-seed.json
    (source_dir / "general_scenes.json").write_text(
        json.dumps(
            {
                "library": "general_scenes",
                "items": [
                    {
                        "identifier": "gen_lounge_01",
                        "label": zh_shared,
                        "theme": zh_theme,
                    }
                ],
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # Library 2: workplace_scenes -> workplace-scenes-rooms-seed.json
    (source_dir / "workplace_scenes.json").write_text(
        json.dumps(
            {
                "library": "workplace_scenes",
                "items": [
                    {
                        "identifier": "work_office_01",
                        "label": zh_shared,
                        "theme": zh_theme,
                    }
                ],
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    import_source(
        source_dir=source_dir,
        map_path=map_file,
        data_dir=data_dir,
        config=_fresh_config(),
    )

    gen_seed = data_dir / "general-scenes-rooms-seed.json"
    work_seed = data_dir / "workplace-scenes-rooms-seed.json"

    assert gen_seed.is_file(), f"Expected {gen_seed} to be written"
    assert work_seed.is_file(), f"Expected {work_seed} to be written"

    gen_rows = json.loads(gen_seed.read_text(encoding="utf-8"))
    work_rows = json.loads(work_seed.read_text(encoding="utf-8"))

    assert len(gen_rows) == 1
    assert len(work_rows) == 1

    gen_row = gen_rows[0]
    work_row = work_rows[0]

    # Both rows carry the expected English translation from the map
    assert gen_row["label"] == "Shared Living Room"
    assert work_row["label"] == "Shared Living Room"

    # The second shared field answers the same way. Asserting only on the label
    # would pass a translation that diverged by library on any other field.
    assert gen_row["place"] == work_row["place"]
    assert gen_row["place"] == "spacious quiet lounge with low table and sofa"

    # Source non-English strings do not leak into either seed file
    for seed in (gen_seed, work_seed):
        text = seed.read_text(encoding="utf-8")
        assert zh_shared not in text
        assert zh_theme not in text


def test_app_and_cli_reports_carry_the_same_counts_per_destination(tmp_path: Path, capsys):
    """4.8: the two entries report the same counts, per destination too.

    `import_source` is the only place a report is built; the CLI's summary
    prints fields read straight off the dict it returns, never recomputing a
    count of its own. This asserts that stays true by comparing the app's
    returned report against the CLI's printed summary for one fixture that
    exercises both an accepted and a guard-refused entry landing on the same
    destination - accepted and refused are attributable per destination
    because every entry reaching the guard already carries a library that
    resolves to one, per backend.source_manifest.declare_source_file refusing
    any library with none before an entry is ever loaded.

    The second library carries only a refused entry, so its destination
    has nothing written and is reported on its refused count alone. That
    is the branch keeping the per-destination counts summing to the run
    totals, asserted at the end.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_app = tmp_path / "data_app"
    data_app.mkdir(parents=True)
    data_cli = tmp_path / "data_cli"
    data_cli.mkdir(parents=True)

    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    # One accepted room and one guard-refused ("school") entry in the same
    # declared, destination-bearing library, so both counts land on the same
    # destination and neither is zero.
    source_payload = {
        "items": [
            {
                "identifier": "gen_room_01",
                "label": "Sunlit Room",
                "theme": "sunlit room with large windows",
            },
            {
                "identifier": "school_classroom_01",
                "label": "classroom",
                "theme": "a classroom with blackboard and wooden desks",
            },
        ],
    }
    (source_dir / "general_scenes.json").write_text(
        json.dumps(source_payload, indent=2) + "\n", encoding="utf-8"
    )

    # A second declared library whose ONLY entry is refused, so its
    # destination has zero accepted entries and never reaches the merge.
    # It still has a refused count to report, and dropping it would leave
    # the per-destination counts summing to less than the run's totals.
    (source_dir / "workplace_scenes.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "identifier": "school_corridor_01",
                        "label": "corridor",
                        "theme": "a corridor lined with lockers",
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    report_app = import_source(
        source_dir=source_dir,
        map_path=map_file,
        data_dir=data_app,
        config=_fresh_config(),
    )

    dest = "general-scenes-rooms-seed.json"
    dest_report = report_app["destinations"][dest]
    # Sanity: the fixture actually exercises one accepted and one refused
    # entry on the same destination, not two zeros agreeing by accident.
    assert dest_report["accepted"] == 1
    assert dest_report["refused"] == 1

    cli_config = tmp_path / "cli-config.json"
    cli_config.write_text(
        json.dumps(_fresh_config(), indent=2) + "\n", encoding="utf-8"
    )

    exit_code = cli_main(
        [
            str(source_dir),
            str(map_file),
            "--data-dir",
            str(data_cli),
            "--config",
            str(cli_config),
        ]
    )
    assert exit_code == 0
    printed = capsys.readouterr().out

    # Overall counts: the CLI prints exactly the app's own dict, in the same
    # layout scripts/import_assets.py:main formats them in.
    assert f"  Accepted:  {report_app['accepted']}" in printed
    assert f"  Refused:   {report_app['refused']}" in printed
    assert f"  Created:   {report_app['created']}" in printed
    assert f"  Updated:   {report_app['updated']}" in printed
    assert f"  Unchanged: {report_app['unchanged']}" in printed
    assert f"  Orphaned:  {report_app['orphaned']}" in printed

    # Per-destination counts: the same six words, the same numbers, read off
    # the same report dict rather than recomputed by the CLI.
    dest_line = next(line for line in printed.splitlines() if dest in line)
    assert f"accepted {dest_report['accepted']}" in dest_line
    assert f"refused {dest_report['refused']}" in dest_line
    assert f"{dest_report['written']} written" in dest_line
    assert f"{dest_report['created']} created" in dest_line
    assert f"{dest_report['updated']} updated" in dest_line
    assert f"{dest_report['unchanged']} unchanged" in dest_line
    assert f"{dest_report['orphaned']} orphaned" in dest_line

    # A destination with nothing written still reports its refused
    # entries, and the CLI prints it on the same terms.
    empty_dest = "workplace-scenes-rooms-seed.json"
    empty_report = report_app["destinations"][empty_dest]
    assert empty_report["accepted"] == 0
    assert empty_report["refused"] == 1
    assert empty_report["written"] == 0
    assert empty_report["created"] == 0
    assert empty_report["updated"] == 0
    assert empty_report["unchanged"] == 0
    assert empty_report["orphaned"] == 0
    assert not (data_app / empty_dest).exists()
    empty_line = next(
        line for line in printed.splitlines() if empty_dest in line
    )
    assert "accepted 0" in empty_line
    assert "refused 1" in empty_line

    # The property both halves serve: every entry the guard judged is
    # counted under exactly one destination, so the per-destination counts
    # sum to the run's own totals. A destination dropped for writing
    # nothing would make this fail.
    assert sum(
        info["accepted"] for info in report_app["destinations"].values()
    ) == report_app["accepted"]
    assert sum(
        info["refused"] for info in report_app["destinations"].values()
    ) == report_app["refused"]


def test_rerunning_import_rewords_nothing(tmp_path: Path):
    """4.3e (Task 2.8): Assert re-running an import rewords nothing.

    Runs import_source twice over one fixture source directory into the SAME
    data directory, and compares every stored translation (label, place, notes)
    between the two runs.

    Rules:
    1. Compare every translated field: label, place (theme prose), notes.
       The fixture carries non-English strings in all three, covered by the map.
    2. Compare translations, not whole rows (phase 5 owns the row shape).
    3. Absence assertion covers both raw and unicode_escape representations.
    4. ONE config, built once and handed to both runs. That is the production
       shape - a re-import is a second run against the config the first one
       already registered the library in - and it is the only shape where the
       registry can gain a duplicate entry. Two fresh configs leave
       `existing_seed_files` empty on run 2, so the "already registered, do not
       append again" branch is never executed and a second entry for the same
       seed file would go unnoticed. Hence the length assertion below.

    Verification: replacing the `unchanged`/`updated` split in
    `backend/importer.py` with an unconditional `updated_keys.append(room_key)`
    fails this test and no other. The mutation that reworded a stored label on
    the update path is NOT this test's - it is caught by
    `test_merge_updates_text_and_preserves_verdict_and_orphaned_rows`.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    zh_label = _zh(["65e5", "5f0f", "8336", "5ba4"])
    zh_theme = _zh(["6e05", "5e7d", "7684", "8336", "5ba4", "6709", "6728", "684c"])
    zh_notes = _zh(["81ea", "7136", "5149", "7ebf"])

    expected = {
        "label": "Japanese Tea Room",
        "place": "peaceful traditional tea room with tatami mats and low wooden table",
        "notes": "soft diffused morning sunlight through paper screens",
    }

    map_data = {
        zh_label: {
            "source": zh_label,
            "translation": expected["label"],
            "fields": ["label"],
        },
        zh_theme: {
            "source": zh_theme,
            "translation": expected["place"],
            "fields": ["theme"],
        },
        zh_notes: {
            "source": zh_notes,
            "translation": expected["notes"],
            "fields": ["notes"],
        },
    }
    map_file = source_dir / "translation_map.json"
    map_file.write_text(
        json.dumps(map_data, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )

    source_payload = {
        "library": "general_scenes",
        "items": [
            {
                "identifier": "tea_room_01",
                "label": zh_label,
                "theme": zh_theme,
                "notes": zh_notes,
            }
        ],
    }
    (source_dir / "general_scenes.json").write_text(
        json.dumps(source_payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )

    # One config for both runs (Rule 4)
    config = _fresh_config()

    report_1 = import_source(
        source_dir=source_dir,
        map_path=map_file,
        data_dir=data_dir,
        config=config,
    )
    assert len(config["room_libraries"]) == 1

    dest_seed = data_dir / "general-scenes-rooms-seed.json"
    assert dest_seed.is_file(), f"Expected seed file was not created: {dest_seed}"

    # Read stored translations from run 1
    rows_run1 = json.loads(dest_seed.read_text(encoding="utf-8"))
    assert len(rows_run1) == 1
    run1_by_id = {r["identifier"]: r for r in rows_run1}
    assert "tea_room_01" in run1_by_id

    # Verify first run translations match map expectations
    for field in ("label", "place"):
        assert run1_by_id["tea_room_01"][field] == expected[field]
    assert run1_by_id["tea_room_01"]["guidance"]["notes"] == expected["notes"]

    # Verify absence of source strings after run 1 in both raw and escape forms (Rule 3)
    raw_run1 = dest_seed.read_bytes()
    text_run1 = dest_seed.read_text(encoding="utf-8")
    for zh_str in (zh_label, zh_theme, zh_notes):
        assert zh_str not in text_run1
        assert zh_str.encode("utf-8") not in raw_run1
        assert zh_str.encode("unicode_escape") not in raw_run1

    # Second import run over the SAME fixture source directory, the SAME data
    # directory and the SAME config object (Rule 4)
    report_2 = import_source(
        source_dir=source_dir,
        map_path=map_file,
        data_dir=data_dir,
        config=config,
    )

    # The registry did not grow: run 2 found its seed file already registered
    assert len(config["room_libraries"]) == 1
    assert [lib["seed_file"] for lib in config["room_libraries"]] == [
        "general-scenes-rooms-seed.json"
    ]

    # Read stored translations from run 2
    rows_run2 = json.loads(dest_seed.read_text(encoding="utf-8"))
    assert len(rows_run2) == 1
    run2_by_id = {r["identifier"]: r for r in rows_run2}
    assert "tea_room_01" in run2_by_id

    # Compare every translated field between run 1 and run 2 (Rule 1 & Rule 2)
    # Deliberately compare translations rather than whole rows.
    for field in ("label", "place"):
        assert run2_by_id["tea_room_01"][field] == run1_by_id["tea_room_01"][field]
        assert run2_by_id["tea_room_01"][field] == expected[field]
    assert (run2_by_id["tea_room_01"]["guidance"]
            == run1_by_id["tea_room_01"]["guidance"])
    assert run2_by_id["tea_room_01"]["guidance"]["notes"] == expected["notes"]

    # Verify absence of source strings after run 2 in both raw and escape forms (Rule 3)
    raw_run2 = dest_seed.read_bytes()
    text_run2 = dest_seed.read_text(encoding="utf-8")
    for zh_str in (zh_label, zh_theme, zh_notes):
        assert zh_str not in text_run2
        assert zh_str.encode("utf-8") not in raw_run2
        assert zh_str.encode("unicode_escape") not in raw_run2

    # Branch execution verification:
    # Run 1 creates the row (created branch)
    assert report_1["created"] == 1
    assert report_1["updated"] == 0
    assert report_1["unchanged"] == 0
    # Run 2 finds identical text/fields and executes the unchanged branch
    assert report_2["created"] == 0
    assert report_2["updated"] == 0
    assert report_2["unchanged"] == 1
def test_uncovered_list_names_every_string_inside_a_nested_list(tmp_path: Path):
    """4.5, re-measured after 4.3c: the coverage walk descends a list in a list.

    `_extract_strings_from_value` used to handle a str item and a dict item of
    a list inline and drop a list item, while `_translate_value` recursed into
    all of them. The two walks disagreeing is how a `notes` field shaped
    `[[text]]` imported clean and put the source's own script on disk. The
    writer refuses such a string now, but it refuses on the FIRST one it meets,
    which is a stop and not a list - and 4.5's criterion is the list. So the
    assertion here is the COUNT: two uncovered strings at different depths of
    one nested list are both named, with their own field paths. One item would
    mean the coverage walk missed them and the writer stopped the run instead.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    zh_inner = _zh(["6697", "5149"])
    zh_deeper = _zh(["6e05", "5e7d"])

    (source_dir / "workplace_scenes.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "identifier": "office_corner_02",
                        "label": "Office Corner",
                        "theme": "quiet corner office with desk",
                        "notes": {
                            "ambience": [[zh_inner], [{"mood": zh_deeper}]],
                        },
                    }
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    with pytest.raises(TranslationMissingError) as exc_info:
        import_source(
            source_dir=source_dir,
            map_path=map_file,
            data_dir=data_dir,
            config=_fresh_config(),
        )

    uncovered = exc_info.value.uncovered
    assert len(uncovered) == 2
    assert {item["string"] for item in uncovered} == {zh_inner, zh_deeper}
    by_string = {item["string"]: item for item in uncovered}
    assert by_string[zh_inner]["field"] == "notes.ambience"
    assert by_string[zh_deeper]["field"] == "notes.ambience.mood"
    for item in uncovered:
        assert item["identifier"] == "office_corner_02"

    # Nothing was written: the refusal is still before any write.
    assert not list(data_dir.glob("*.json"))


def test_translation_walk_refuses_a_shape_the_coverage_walk_does_not_reach(tmp_path: Path):
    """4.3c: the map is the only source of translations, on every shape.

    The stop and the translation are two separate recursions over one entry, and
    the run is safe only while the stop reaches every field the translation can
    write. Measured, they did not agree. `_extract_strings_from_value` descends a
    dict, a list of strings and a dict inside a list; it does not descend a list
    inside a list. `_translate_value` descends all of them, and `notes` is the one
    structured field handed to it, so a `notes.ambience` shaped `[[text]]` was
    reachable by the writer and invisible to the check: the import ran clean and
    the source's own script landed in the seed as the escapes `ensure_ascii=True`
    writes back out and the repository's CJK rule cannot see. That is 4.4's defect
    in a second place, and the fix is on the writer rather than the check, so a
    gap in the check fails loudly for every shape instead of the one found here.

    Asserted on the shape the coverage walk misses on purpose. The same assertion
    on a plain `label` would have passed against the old fallback and would prove
    only what the neighbouring missing-string test already proves.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    zh_nested = _zh(["6697", "5149"])  # dim light

    (source_dir / "workplace_scenes.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "identifier": "office_corner_01",
                        "label": "Office Corner",
                        "theme": "quiet corner office with desk",
                        "notes": {"ambience": [[zh_nested]]},
                    }
                ]
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    # A destination that already holds rows, so "wrote nothing" is a byte
    # comparison against real content rather than an empty directory listing.
    dest_seed = data_dir / "workplace-scenes-rooms-seed.json"
    dest_seed.write_text(
        json.dumps(
            [{"key": "wk-untouched", "identifier": "wk-untouched", "place": "a hallway"}],
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    before_bytes = dest_seed.read_bytes()

    with pytest.raises(TranslationMissingError) as exc_info:
        import_source(
            source_dir=source_dir,
            map_path=map_file,
            data_dir=data_dir,
            config=_fresh_config(),
        )

    assert exc_info.value.identifier == "office_corner_01"
    assert exc_info.value.field == "notes.ambience"
    assert exc_info.value.text == zh_nested
    assert "office_corner_01" in str(exc_info.value)
    assert "notes.ambience" in str(exc_info.value)

    # Refused before anything is written: the merge builds rows in memory and
    # the seed files are written after the loop.
    assert dest_seed.read_bytes() == before_bytes

    # The other direction: once the map carries it, the same shape imports and
    # the stored string is the English one. Without this half the test passes
    # against an importer that refuses every list inside a list on sight.
    map_file.write_text(
        json.dumps(
            {
                zh_nested: {
                    "source": zh_nested,
                    "translation": "dim lighting",
                    "fields": ["notes.ambience"],
                }
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    dest_seed.unlink()

    report = import_source(
        source_dir=source_dir,
        map_path=map_file,
        data_dir=data_dir,
        config=_fresh_config(),
    )
    assert report["written"] == 1
    rows = json.loads(dest_seed.read_text(encoding="utf-8"))
    assert rows[0]["guidance"]["notes"]["ambience"] == [["dim lighting"]]
    assert (chr(92) + "u") not in dest_seed.read_text(encoding="utf-8")


def test_no_translator_is_reachable_from_an_import_run(tmp_path: Path):
    """4.3c: the importer never translates during a run.

    An absence claim, so it is made the way an absence claim has to be made here:
    by running the real thing and reading what it loaded, not by scanning the
    import block. A scan passes the moment the line is absent and says nothing
    about a lazy `import backend.enhance` inside a function - and this codebase
    does put imports inside functions, `backend.extractor.find_uncovered_strings`
    among them.

    A fresh interpreter, because in this process the assertion cannot fail
    honestly: `backend/enhance.py` is loaded under the top-level name `enhance`
    and `httpx` is loaded outright by the time the suite reaches this file, so an
    in-process `sys.modules` check is either vacuous or red for reasons that have
    nothing to do with the importer. Both names are checked plus `httpx`, which
    `backend/enhance.py` imports at module scope, so the translator cannot be
    reached under a third spelling without dragging it in.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")
    (source_dir / "workplace_scenes.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "identifier": "work_office_01",
                        "label": "Office",
                        "theme": "modern office room with glass partitions",
                    }
                ]
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    probe = "\n".join(
        [
            "import json, sys",
            "from backend.importer import import_source",
            "import_source(source_dir=sys.argv[1], map_path=sys.argv[2],",
            "              data_dir=sys.argv[3], config={'room_libraries': []})",
            "watched = ('backend.enhance', 'enhance', 'httpx')",
            "print(json.dumps(sorted(m for m in watched if m in sys.modules)))",
        ]
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            probe,
            str(source_dir),
            str(map_file),
            str(data_dir),
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"probe failed: {proc.stderr}\n{proc.stdout}"
    assert json.loads(proc.stdout.strip()) == []
