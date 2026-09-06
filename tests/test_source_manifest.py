"""Tests for the source library declarations.

Asserts that:
- A file no declaration names is refused, and the refusal writes nothing.
- A file cannot declare itself by its contents: the name is read, not the bytes.
- A declared library that reaches no destination is refused with its own
  reason, readable apart from an undeclared file, and writes nothing.
- Every declared destination is a room seed file the room registry can carry,
  and no two libraries write the same one.
- The libraries declared as not adopted are the guard's own list, not a second
  copy of it.
- No caller argument and no environment variable declares an undeclared file,
  and the module reads nothing it could be configured by.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import backend.asset_guard as asset_guard
import backend.source_manifest as source_manifest
from backend.asset_guard import SIGNAL_NOT_ADOPTED
from backend.room_registry import is_room_seed_file
from backend.source_manifest import (
    KIND_BODY_PROFILES,
    KIND_FUSED_SCENES,
    KIND_IDENTITIES,
    KIND_ROOMS,
    REASON_UNDECLARED,
    SOURCE_LIBRARIES,
    SourceRefused,
    declaration_for,
    declare_source_file,
)

ROOT = Path(__file__).resolve().parents[1]

# Invented names. No real source library is called any of these, so a fixture
# file never lands on a declaration by accident - a fixture written as
# `amateurs.json` is read as the library of that name by everything that takes
# a library from a file name.
UNDECLARED_STEM = "pantry_props_catalogue"


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under root as path -> bytes, for a before/after comparison."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _source_dir(tmp_path: Path, stem: str, payload: dict) -> tuple[Path, Path]:
    """A source directory holding one JSON file, beside a room seed file."""
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "invented-rooms-seed.json").write_text(
        json.dumps([{"key": "quiet_pantry", "text": "a narrow pantry"}]),
        encoding="utf-8",
    )
    json_file = source_dir / f"{stem}.json"
    json_file.write_text(json.dumps(payload), encoding="utf-8")
    return json_file, tmp_path


def test_an_undeclared_file_is_refused_and_writes_nothing(tmp_path):
    """A file matching no declaration is refused by name, and nothing is written."""
    json_file, root = _source_dir(
        tmp_path,
        UNDECLARED_STEM,
        {"items": [{"identifier": "pantry_01", "label": "a narrow pantry"}]},
    )
    before = _tree(root)

    with pytest.raises(SourceRefused) as exc_info:
        declare_source_file(json_file, writes=KIND_ROOMS)

    assert exc_info.value.reason == REASON_UNDECLARED
    assert exc_info.value.library == UNDECLARED_STEM
    # The refusal names what it could not identify.
    assert UNDECLARED_STEM in str(exc_info.value)

    assert _tree(root) == before


def test_a_file_cannot_declare_itself_by_its_contents(tmp_path):
    """The library is read from the file name; the file's own claim is ignored.

    A source file saying which library it is would be the material deciding
    what may be done with it, which is the bypass shape this repo keeps
    closing.
    """
    json_file, root = _source_dir(
        tmp_path,
        UNDECLARED_STEM,
        {
            "library": "general_scenes",
            "items": [{"identifier": "pantry_02", "label": "a narrow pantry"}],
        },
    )
    before = _tree(root)

    with pytest.raises(SourceRefused) as exc_info:
        declare_source_file(json_file, writes=KIND_ROOMS)
    assert exc_info.value.reason == REASON_UNDECLARED
    assert exc_info.value.library == UNDECLARED_STEM

    assert _tree(root) == before


def test_a_library_reaching_no_destination_is_refused_with_its_own_reason(tmp_path):
    """A library this project does not adopt is refused on its own reason."""
    expected = {
        "amateurs": SIGNAL_NOT_ADOPTED,
        "celebrities": SIGNAL_NOT_ADOPTED,
    }
    for stem, reason in expected.items():
        json_file, root = _source_dir(
            tmp_path / stem,
            stem,
            {"items": [{"identifier": f"{stem}_01", "label": "invented row"}]},
        )
        before = _tree(root)

        with pytest.raises(SourceRefused) as exc_info:
            declare_source_file(json_file, writes=KIND_ROOMS)
        assert exc_info.value.reason == reason, f"wrong reason for {stem}"
        # Distinct from a file nothing declares.
        assert exc_info.value.reason != REASON_UNDECLARED

        assert _tree(root) == before, f"{stem} wrote something"


def test_a_declared_room_library_names_its_destinations(tmp_path):
    """A declared source returns the destinations it is written to, and no others."""
    json_file, _ = _source_dir(
        tmp_path,
        "general_scenes",
        {"items": [{"identifier": "gs_01", "label": "invented row"}]},
    )
    declared = declare_source_file(json_file, writes=KIND_ROOMS)
    assert declared["library"] == "general_scenes"
    assert declared["kind"] == KIND_ROOMS
    assert declared["destinations"] == ("general-scenes-rooms-seed.json",)


def test_every_declared_destination_is_a_room_seed_the_registry_can_carry():
    """The only destination that exists is a room seed file the registry names.

    A destination the registry cannot recognise is a destination nothing can
    read, so the declaration is checked against the registry's own rule rather
    than against a second spelling of it here.
    """
    # The kinds whose entries land in a room seed. The fused library is one of
    # them: only the ROOM part of a cut entry lands, and it lands in a seed like
    # any other room. Which importer may write it is the declaration's `kind`,
    # asserted where that rule is used, not here.
    reaches_a_room_seed = (KIND_ROOMS, KIND_FUSED_SCENES)
    seen: dict[str, str] = {}
    for name, declared in SOURCE_LIBRARIES.items():
        destinations = declared["destinations"]
        if declared["kind"] in reaches_a_room_seed:
            assert destinations, f"{name} reaches a room seed and names no destination"
        else:
            assert destinations == (), f"{name} names a destination it cannot reach"
            assert declared.get("reason"), f"{name} reaches nothing and says no reason"
        for dest in destinations:
            assert is_room_seed_file(Path(dest)), f"{name}: {dest} is not a room seed file"
            assert dest not in seen, f"{dest} is declared by {seen.get(dest)} and {name}"
            seen[dest] = name


def test_not_adopted_libraries_are_the_guard_s_own_list():
    """One list, read twice: the manifest does not keep a second copy."""
    from_manifest = {
        name
        for name, declared in SOURCE_LIBRARIES.items()
        if declared.get("reason") == SIGNAL_NOT_ADOPTED
    }
    assert from_manifest == set(asset_guard.NOT_ADOPTED_LIBRARIES)


def test_no_caller_argument_declares_an_undeclared_file(tmp_path):
    """No keyword a caller invents adds a declaration, and the table is read-only."""
    json_file, _ = _source_dir(
        tmp_path,
        UNDECLARED_STEM,
        {"items": [{"identifier": "pantry_03", "label": "a narrow pantry"}]},
    )
    bypass_args = ("declarations", "kind", "destinations", "force", "allow_undeclared")
    for arg in bypass_args:
        with pytest.raises(TypeError) as exc_info:
            declare_source_file(json_file, writes=KIND_ROOMS, **{arg: True})
        assert arg in str(exc_info.value), f"expected '{arg}' in {exc_info.value}"

    # A caller holding a returned declaration cannot edit the table through it.
    handed_back = declaration_for("general_scenes")
    handed_back["destinations"] = ()
    handed_back["kind"] = "anything"
    assert declaration_for("general_scenes")["kind"] == KIND_ROOMS
    assert declaration_for("general_scenes")["destinations"] == (
        "general-scenes-rooms-seed.json",
    )
    assert declaration_for(UNDECLARED_STEM) is None


def test_environment_variables_cannot_declare_a_source(tmp_path, monkeypatch):
    """No environment variable declares a file or removes a declaration."""
    json_file, _ = _source_dir(
        tmp_path,
        UNDECLARED_STEM,
        {"items": [{"identifier": "pantry_04", "label": "a narrow pantry"}]},
    )
    declared_file, _ = _source_dir(
        tmp_path / "declared",
        "general_scenes",
        {"items": [{"identifier": "gs_02", "label": "invented row"}]},
    )
    bypass_envs = [
        ("IDEVGEN_SOURCE_LIBRARIES", UNDECLARED_STEM),
        ("IDEVGEN_ALLOW_UNDECLARED", "1"),
        ("IDEVGEN_IMPORT_FORCE", "true"),
        ("IDEVGEN_MANIFEST", "0"),
        ("IDEVGEN_SOURCE_MANIFEST", "{}"),
    ]
    for var, val in bypass_envs:
        monkeypatch.setenv(var, val)
        with pytest.raises(SourceRefused):
            declare_source_file(json_file, writes=KIND_ROOMS)
        assert declare_source_file(declared_file, writes=KIND_ROOMS)["destinations"] == (
            "general-scenes-rooms-seed.json",
        ), f"failed for {var}={val}"


def test_manifest_imports_nothing_that_can_be_configured():
    """The declaration is decided by its own table and by nothing else.

    Same property the guard's import-list test asserts, and asserted the same
    way: anything the manifest could be told what to declare by has to appear
    in the import list first.
    """
    allowed_imports = {"__future__", "pathlib", "typing", "backend"}
    source = (ROOT / "backend" / "source_manifest.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= allowed_imports, f"manifest imports {sorted(imported - allowed_imports)}"

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "open" not in called, "manifest must not call open()"
    assert "print" not in called, "manifest must not call print()"

    attrs = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for reader in ("read_text", "read_bytes", "load", "loads", "getenv"):
        assert reader not in attrs, f"manifest calls {reader}"
    for writer in ("write_text", "write_bytes", "mkdir", "unlink", "dump", "dumps"):
        assert writer not in attrs, f"manifest calls {writer}"


def test_the_fused_library_reaches_a_room_seed_and_only_through_its_own_importer(tmp_path):
    """It has a destination now, and the KIND is what keeps the room importer out.

    Its entries land in a room seed like every other library's - but only the
    ROOM part of each one, after the entry has been cut into a camera, an act
    and a room. The room importer writing it whole is the defect the split
    exists to prevent: a room row carrying a camera position is a room that
    overrules the line's camera.

    An empty destination list used to say that, and it said it by claiming the
    material reaches nowhere, which stopped being true the day the split
    shipped. Saying it on the kind is the honest form, and it is also the
    stricter one: it refuses the room importer while letting the mining path
    through, where an empty list refused both.
    """
    json_file, root = _source_dir(
        tmp_path,
        "perspective_scenes",
        {"items": [{"identifier": "ps_01", "label": "invented row"}]},
    )
    before = _tree(root)

    with pytest.raises(SourceRefused) as exc_info:
        declare_source_file(json_file, writes=KIND_ROOMS)
    assert exc_info.value.reason == source_manifest.REASON_WRONG_IMPORTER
    assert exc_info.value.reason != REASON_UNDECLARED
    assert _tree(root) == before, "a refusal wrote something"

    declared = declare_source_file(json_file, writes=KIND_FUSED_SCENES)
    assert declared["library"] == "perspective_scenes"
    assert declared["destinations"] == ("perspective-scenes-rooms-seed.json",)


def test_the_kind_a_caller_writes_can_only_narrow(tmp_path):
    """`writes` admits what the caller can write; it declares nothing.

    It cannot name an undeclared library into existence and it cannot lift the
    not-adopted refusal, whatever kind is passed - which is what separates it
    from the bypass keywords the test above refuses. Every kind is tried against
    both, because a narrowing argument that happens to widen for one value is a
    bypass with a whitelist.
    """
    undeclared, _ = _source_dir(
        tmp_path / "a", UNDECLARED_STEM,
        {"items": [{"identifier": "pantry_05", "label": "a narrow pantry"}]},
    )
    not_adopted, _ = _source_dir(
        tmp_path / "b", "amateurs",
        {"items": [{"identifier": "am_01", "label": "invented row"}]},
    )
    for kind in (KIND_ROOMS, KIND_FUSED_SCENES, KIND_BODY_PROFILES, KIND_IDENTITIES, ""):
        with pytest.raises(SourceRefused) as undeclared_refusal:
            declare_source_file(undeclared, writes=kind)
        assert undeclared_refusal.value.reason == REASON_UNDECLARED, kind
        with pytest.raises(SourceRefused) as adopted_refusal:
            declare_source_file(not_adopted, writes=kind)
        assert adopted_refusal.value.reason == SIGNAL_NOT_ADOPTED, kind


def test_a_caller_that_does_not_say_what_it_writes_is_refused_by_the_signature(tmp_path):
    """`writes` has no default, and that is the whole of its safety.

    A default makes it optional, and an importer that forgot to say would take
    whatever the default happened to be - which for any value at all is one
    importer silently claiming another's material. Refused at the call rather
    than inside it: the mistake is a caller that never thought about the
    question, and there is no runtime check for not having thought.
    """
    json_file, _ = _source_dir(
        tmp_path,
        "general_scenes",
        {"items": [{"identifier": "gs_03", "label": "invented row"}]},
    )
    with pytest.raises(TypeError) as exc_info:
        declare_source_file(json_file)
    assert "writes" in str(exc_info.value)
