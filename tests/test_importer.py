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
- All created files are pure ASCII with no illegal control bytes or trailing whitespace.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.asset_guard import SIGNAL_NOT_ADOPTED
from backend.room_registry import verify_registry_disk_agreement
from backend.importer import (
    TranslationMissingError,
    derive_room_key,
    import_source,
)
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


def test_import_path_refuses_to_write_seed_rows_without_consulting_guard(tmp_path: Path):
    """4.3a: Verify that the import path consults the guard and refuses to write seed rows
    for entries matching refusal signals.

    An unguarded stub that writes rows directly to the destination seed file without
    consulting the guard will fail this test by writing the refused entry.
    """
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

    # general_scenes is a declared room library whose destination is general-scenes-rooms-seed.json
    source_payload = {
        "library": "general_scenes",
        "items": [
            {
                "identifier": "quiet_lounge_01",
                "label": zh_label,
                "theme": "spacious quiet lounge with low table and sofa",
            },
            {
                "identifier": "school_classroom_01",
                "label": "classroom",
                "theme": "a classroom with blackboard and wooden desks",
            },
        ],
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

    # Guard consultation assertions:
    # 1. The refused school entry MUST NOT be written to the seed file
    assert "school_classroom_01" not in written_identifiers, (
        "Refused entry 'school_classroom_01' was written to seed file without consulting guard"
    )
    # 2. The accepted entry MUST be written
    assert "quiet_lounge_01" in written_identifiers
    # 3. Only the accepted room entry was written
    assert len(seed_rows) == 1

    # Report assertions:
    assert report["refused"] >= 1
    assert "school_classroom_01" in report["refused_identifiers"]
    assert report["accepted"] >= 1
    assert report["written"] == 1


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


def test_merge_updates_text_and_preserves_verdict_and_orphaned_rows(tmp_path: Path):
    """Verify that merge updates room text while preserving verdicts and sample sizes,
    and keeps orphaned rows when an upstream entry disappears.
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
            "key": "sm-chamber-01",
            "identifier": "sm_chamber_01",
            "label": "Old Chamber Label",
            "manner": "candid",
            "look": "old theme text",
            "verdict": "verified: 12/12",
            "sample_size": 12,
        },
        {
            "key": "sm-vanished-row",
            "identifier": "sm_vanished_row",
            "label": "Vanished Room",
            "manner": "candid",
            "look": "vanished room text",
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
    assert ch["look"] == "updated theme text with stone walls"
    assert ch["verdict"] == "verified: 12/12"
    assert ch["sample_size"] == 12

    # 2. sm_dungeon_02 was created
    assert "sm_dungeon_02" in rows_by_id
    assert rows_by_id["sm_dungeon_02"]["verdict"] == "unverified"

    # 3. sm_vanished_row was not deleted (preserved as orphaned)
    assert "sm_vanished_row" in rows_by_id
    assert rows_by_id["sm_vanished_row"]["verdict"] == "verified: 5/5"


def test_empty_header_entries_skipped_and_reported(tmp_path: Path):
    """Fact 2 & 5.11: Verify that entries carrying no theme text (such as file-level
    header leftovers) are skipped from room seed writing and accounted for in the report.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    map_file = source_dir / "translation_map.json"
    map_file.write_text("{}", encoding="utf-8")

    source_payload = {
        "library": "special_scenes",
        "version": 1,
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


def test_importer_files_are_pure_ascii_and_contain_no_control_bytes():
    """Verify that all created/modified files are pure ASCII with no illegal control
    bytes, no literal backspaces, no trailing whitespace, and LF endings.
    """
    files = [
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
    assert rows[0]["look"] == "living room, oak floor, low table"
    assert zh_theme not in rows[0]["look"]

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
