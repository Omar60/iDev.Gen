"""Tests for the source-library coverage ledger (task 1.1).

Asserts that:
- Every discovered JSON file produces exactly one ledger entry.
- Auxiliary maps (translation, cut, families, labels) are recorded
  distinctly and never as scenes, and the shape-inference path is gone.
- Operator-selected auxiliary files outside `source_dir` are
  inventoried and receive exactly one ledger entry.
- A field first appearing after entry eight is recorded (the whole
  file is walked).
- File IDs are unique even when two files share a stem and bytes.
- Field shapes and roles are recorded; source values and prose are not.
- Every non-OK status carries a non-empty `reason`.
- Malformed and unknown shapes are reported rather than guessed.
- The generated ledger is structural-only: no absolute paths, no source
  prose, no personal data, no CJK glyphs.
- The ledger's identifier, role, status and shape names are a closed
  set, so a private fork cannot silently slip in.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.resource_ledger import (
    ALL_ROLES,
    ALL_STATUSES,
    ALL_TYPES,
    AUXILIARY_MAPS,
    CoverageLedger,
    FileEntry,
    PROVISIONAL_FIELD_ROLES,
    ROLE_AUXILIARY_DATA,
    ROLE_DESCRIPTIVE_INPUT,
    ROLE_IDENTIFIER,
    ROLE_SELECTION_METADATA,
    ROLE_UNUSED,
    ROLE_WRITER_GUIDANCE,
    SHAPE_ARRAY,
    SHAPE_EMPTY,
    SHAPE_MALFORMED,
    SHAPE_OBJECT,
    SHAPE_SCALAR,
    STATUS_AUXILIARY,
    STATUS_KNOWN_SHAPE,
    STATUS_MALFORMED,
    STATUS_NOT_ADOPTED,
    STATUS_PENDING_MAPPING,
    STATUS_REFUSED,
    STATUS_UNDECLARED,
    STATUS_UNKNOWN_SHAPE,
    STATUSES_REQUIRING_REASON,
    TYPE_BOOLEAN,
    TYPE_DICT,
    TYPE_LIST,
    TYPE_NULL,
    TYPE_NUMBER,
    TYPE_STRING,
    inventory_file,
    inventory_source_dir,
    save_ledger,
)

ROOT = Path(__file__).resolve().parents[1]


# -- Invented fixtures. All English, no personal data, no absolute paths. ---

GENERAL_SCENE_FILE = {
    "library": "general_scenes",
    "items": [
        {
            "identifier": "invented_gs_01",
            "label": "invented label 1",
            "theme_text": "invented theme for the first entry",
            "tags": ["indoor", "private"],
            "props": "chair, table",
            "weight": 1.0,
            "notes": "invented guidance text",
        },
        {
            "identifier": "invented_gs_02",
            "label": "invented label 2",
            "theme_text": "invented theme for the second entry",
            "tags": ["outdoor"],
            "weight": 2.0,
            "mood_light": "soft",
        },
    ],
}

WORKPLACE_FILE = {
    "library": "workplace_scenes",
    "items": [
        {
            "identifier": "invented_ws_01",
            "label": "invented workplace label",
            "theme_text": "invented workplace theme",
            "tags": ["office", "indoor"],
            "weight": 1.0,
        },
    ],
}

PERSPECTIVE_FILE = {
    "library": "perspective_scenes",
    "items": [
        {
            "identifier": "invented_ps_01",
            "label": "invented fused label",
            "prompt": (
                "low angle from the foot of the bed, "
                "kneeling upright with both hands behind her head, "
                "a narrow attic room with a sloped ceiling"
            ),
            "source_family": "rear_entry_pov",
            "weight": 1.0,
        },
    ],
}

TRANSLATION_MAP_FILE = {
    "invented source string a": {
        "source": "invented source string a",
        "translation": "invented english string a",
        "fields": ["label", "theme_text"],
    },
    "invented source string b": {
        "source": "invented source string b",
        "translation": "invented english string b",
        "fields": ["label"],
    },
}

CUT_MAP_FILE = {
    "invented_ps_01": {
        "camera": "low angle from the foot of the bed",
        "act": "kneeling upright with both hands behind her head",
        "room": "a narrow attic room with a sloped ceiling",
    },
}

MINED_FAMILIES_FILE = {
    "invented_ps_01": "rear_entry_pov",
    "invented_ps_02": "fisheye_pov",
}

MINED_LABELS_FILE = {
    "mined-invented-ps-01-camera": "invented judge label for the camera row",
    "mined-invented-ps-01-act": "invented judge label for the act row",
}

UNDECLARED_FILE = {
    "library": "invented_undeclared",
    "items": [
        {
            "identifier": "invented_und_01",
            "label": "invented undeclared label",
            "theme": "invented undeclared theme",
        },
    ],
}

NOT_ADOPTED_FILE = {
    "library": "amateurs",
    "items": [
        {
            "identifier": "invented_am_01",
            "label": "invented amateur label",
            "theme": "invented amateur theme",
        },
    ],
}

# A file shaped like a translation map but with a stem the manifest
# does not name. The old code guessed it as auxiliary; the new code
# refuses the guess and reports the file as undeclared.
TRANSLATION_SHAPED_UNDECLARED = {
    "some_unguessable_key": {
        "source": "an unguessable source string",
        "translation": "an unguessable translation",
        "fields": ["label"],
    },
}


def _write_json(path: Path, payload: dict) -> None:
    """Write a JSON file using ASCII escapes for any non-ASCII."""
    text = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def _source_dir(tmp_path: Path) -> Path:
    """A source directory carrying one file per known shape."""
    source = tmp_path / "source"
    source.mkdir()
    _write_json(source / "general_scenes.json", GENERAL_SCENE_FILE)
    _write_json(source / "workplace_scenes.json", WORKPLACE_FILE)
    _write_json(source / "perspective_scenes.json", PERSPECTIVE_FILE)
    _write_json(source / "translation_map.json", TRANSLATION_MAP_FILE)
    _write_json(source / "cuts.json", CUT_MAP_FILE)
    _write_json(source / "mined_families.json", MINED_FAMILIES_FILE)
    _write_json(source / "mined_judge_labels.json", MINED_LABELS_FILE)
    _write_json(source / "invented_undeclared.json", UNDECLARED_FILE)
    _write_json(source / "amateurs.json", NOT_ADOPTED_FILE)
    return source


# -- 1. Every discovered file produces exactly one ledger entry. -----------


def test_every_discovered_file_receives_exactly_one_entry(tmp_path):
    """One `FileEntry` per file on disk; no file is dropped, no file appears twice."""
    source = _source_dir(tmp_path)
    ledger = inventory_source_dir(source)
    file_ids = ledger.file_ids()

    expected_stems = {
        "general_scenes", "workplace_scenes", "perspective_scenes",
        "translation_map", "cuts", "mined_families", "mined_judge_labels",
        "invented_undeclared", "amateurs",
    }
    actual_stems = {entry.file_stem for entry in ledger.entries}
    assert actual_stems == expected_stems
    assert len(file_ids) == len(set(file_ids)) == len(expected_stems)
    for entry in ledger.entries:
        assert "\\" not in entry.file_id
        assert "/" not in entry.file_id
        assert ":" not in entry.file_id


# -- 2. Auxiliary maps are recorded distinctly and not as scenes. --------


def test_auxiliary_maps_are_recorded_as_auxiliary_not_scenes(tmp_path):
    """Auxiliary maps never carry a scene kind; their status is `auxiliary`."""
    source = _source_dir(tmp_path)
    ledger = inventory_source_dir(source)

    by_stem = {entry.file_stem: entry for entry in ledger.entries}
    for aux in ("translation_map", "cuts", "mined_families", "mined_judge_labels"):
        entry = by_stem[aux]
        assert entry.status == STATUS_AUXILIARY, aux
        assert entry.auxiliary is True, aux
        assert entry.auxiliary_kind, aux
        assert entry.declared_kind == "", aux

    for declared in ("general_scenes", "workplace_scenes", "perspective_scenes"):
        entry = by_stem[declared]
        assert entry.status == STATUS_KNOWN_SHAPE, declared
        assert entry.auxiliary is False, declared


def test_auxiliary_kind_is_one_of_the_documented_kinds():
    """The auxiliary kinds in the ledger are exactly the ones the module declares."""
    declared_kinds = {spec["kind"] for spec in AUXILIARY_MAPS}
    assert declared_kinds == {"translation_map", "cut_map", "mined_families", "mined_labels"}


def test_translation_shaped_file_with_unrecognised_stem_is_not_guessed_as_auxiliary(tmp_path):
    """A file shaped like a translation map but with an unknown stem stays undeclared.

    This is the regression for review finding #2: the old code guessed
    a translation-map-shape into auxiliary regardless of identity. The
    new code refuses to guess and reports the file as `undeclared`,
    leaving the operator to name it explicitly via `aux_kinds` if it
    is in fact an auxiliary.
    """
    source = tmp_path / "source"
    source.mkdir()
    _write_json(source / "totally_unknown_name.json", TRANSLATION_SHAPED_UNDECLARED)

    ledger = inventory_source_dir(source)
    by_stem = {e.file_stem: e for e in ledger.entries}
    entry = by_stem["totally_unknown_name"]
    assert entry.status == STATUS_UNDECLARED
    assert entry.auxiliary is False
    assert entry.auxiliary_kind == ""


# -- 3. The whole file is walked, no early stop. ---------------------------


def _library_with_field_only_in_late_entries() -> dict:
    """A library whose eleventh entry alone carries the field `late_field`.

    The walk is whole-file. A field that appears only in entry eleven
    is still recorded, because the inventory's job is field coverage
    of the source, not a representative sample.
    """
    items = []
    for i in range(10):
        items.append({
            "identifier": f"invented_late_{i:02d}",
            "label": f"invented label for entry {i}",
            "theme_text": f"invented theme for entry {i}",
        })
    items.append({
        "identifier": "invented_late_10",
        "label": "invented label for the late entry",
        "theme_text": "invented theme for the late entry",
        "late_field": "invented late prose that must not leak into the ledger",
    })
    return {"library": "general_scenes", "items": items}


def test_a_field_first_appearing_after_entry_eight_is_recorded(tmp_path):
    """The walk reaches the eleventh entry; a field only there is recorded."""
    source = tmp_path / "source"
    source.mkdir()
    _write_json(source / "general_scenes.json", _library_with_field_only_in_late_entries())
    ledger = inventory_source_dir(source)
    entry = next(e for e in ledger.entries if e.file_stem == "general_scenes")

    by_name = {f.name: f for f in entry.field_observations}
    assert "late_field" in by_name, (
        "the walk stopped before the eleventh entry; a field first "
        "appearing after entry eight was lost"
    )
    assert by_name["late_field"].structural_type == TYPE_STRING
    # The value of `late_field` is not in the ledger.
    serialised = json.dumps(entry.to_dict(), ensure_ascii=True)
    assert "invented late prose that must not leak into the ledger" not in serialised


def test_entry_count_reflects_the_walked_total(tmp_path):
    """`entry_count` is the number of entries the walk reached, not a sample."""
    source = tmp_path / "source"
    source.mkdir()
    payload = _library_with_field_only_in_late_entries()
    _write_json(source / "general_scenes.json", payload)
    ledger = inventory_source_dir(source)
    entry = next(e for e in ledger.entries if e.file_stem == "general_scenes")
    assert entry.entry_count == 11


# -- 4. Auxiliary files outside source_dir are inventoried. ----------------


def test_auxiliary_paths_outside_source_dir_are_inventoried(tmp_path):
    """A translation map outside `source_dir`, named via `aux_paths`, gets one entry."""
    source = _source_dir(tmp_path)
    # Auxiliary maps that live OUTSIDE source_dir. The operator names
    # them explicitly and tells the ledger which kind each one is.
    aux_dir = tmp_path / "curated"
    aux_dir.mkdir()
    translation_path = aux_dir / "translations.json"
    cuts_path = aux_dir / "perspective-cuts.json"
    families_path = aux_dir / "families.json"
    labels_path = aux_dir / "judge-labels.json"
    _write_json(translation_path, TRANSLATION_MAP_FILE)
    _write_json(cuts_path, CUT_MAP_FILE)
    _write_json(families_path, MINED_FAMILIES_FILE)
    _write_json(labels_path, MINED_LABELS_FILE)

    ledger = inventory_source_dir(
        source,
        aux_paths=(translation_path, cuts_path, families_path, labels_path),
        aux_kinds={
            translation_path: "translation_map",
            cuts_path: "cut_map",
            families_path: "mined_families",
            labels_path: "mined_labels",
        },
    )

    # The four aux_paths from outside source_dir each become one
    # auxiliary entry. The source_dir's own aux files (translation_map,
    # cuts, mined_families, mined_judge_labels) are also auxiliary
    # entries - eight total.
    aux_entries = [e for e in ledger.entries if e.auxiliary]
    assert len(aux_entries) == 8, (
        "expected 4 aux from source_dir + 4 aux from aux_paths; got "
        f"{len(aux_entries)}"
    )

    # The four operator-supplied aux paths got their operator-chosen
    # kind, regardless of stem.
    aux_by_stem = {e.file_stem: e for e in aux_entries}
    assert aux_by_stem["translations"].auxiliary_kind == "translation_map"
    assert aux_by_stem["perspective-cuts"].auxiliary_kind == "cut_map"
    assert aux_by_stem["families"].auxiliary_kind == "mined_families"
    assert aux_by_stem["judge-labels"].auxiliary_kind == "mined_labels"

    # No file_id is shared between entries, and no file_id carries
    # an absolute path.
    for aux in aux_entries:
        assert "\\" not in aux.file_id
        assert "/" not in aux.file_id
    assert len({e.file_id for e in aux_entries}) == len(aux_entries)


def test_every_aux_path_gets_exactly_one_ledger_entry(tmp_path):
    """Naming the same aux path twice still produces one entry, not two."""
    source = _source_dir(tmp_path)
    aux_path = tmp_path / "translations.json"
    _write_json(aux_path, TRANSLATION_MAP_FILE)

    ledger = inventory_source_dir(
        source,
        aux_paths=(aux_path, aux_path),
        aux_kinds={aux_path: "translation_map"},
    )
    translations_entries = [
        e for e in ledger.entries
        if e.file_stem == "translations" and e.auxiliary_kind == "translation_map"
    ]
    assert len(translations_entries) == 1


def test_every_selected_input_appears_exactly_once(tmp_path):
    """No file is double-counted: source_dir and aux_paths together yield one entry per file."""
    source = _source_dir(tmp_path)
    # Take an aux path that happens to be inside source_dir, with the
    # right auxiliary stem. The ledger must give it exactly one entry.
    inside = source / "translation_map.json"
    ledger = inventory_source_dir(
        source,
        aux_paths=(inside,),
        aux_kinds={inside: "translation_map"},
    )
    by_stem = {e.file_stem: e for e in ledger.entries}
    # Exactly one entry for the translation_map stem.
    matching = [e for e in ledger.entries if e.file_stem == "translation_map"]
    assert len(matching) == 1


def test_relative_source_dir_with_absolute_aux_path_dedupes(tmp_path, monkeypatch):
    """The same physical file referenced from `source_dir` (relative) and
    from `aux_paths` (absolute) collapses to one entry. The cwd is fixed
    for the call so a later `chdir` cannot change the answer.
    """
    # A source directory that contains exactly one auxiliary file
    # and one scene file. The auxiliary file is the one whose identity
    # is asserted twice: once via the source walk (relative spelling
    # `source/translation_map.json`) and once via aux_paths (absolute
    # spelling `<tmp_path>/source/translation_map.json`).
    source = tmp_path / "source"
    source.mkdir()
    _write_json(source / "general_scenes.json", GENERAL_SCENE_FILE)
    source_aux = source / "translation_map.json"
    _write_json(source_aux, TRANSLATION_MAP_FILE)
    abs_source_aux = source_aux.resolve()

    # Switch cwd to tmp_path so the relative `source_dir` ("source")
    # resolves to `<tmp_path>/source` for the call. A later chdir would
    # not be able to change the answer, because the resolution happens
    # inside `inventory_source_dir` and the canonical form is captured
    # before the call returns.
    monkeypatch.chdir(tmp_path)

    ledger = inventory_source_dir(
        "source",  # relative source_dir
        aux_paths=(abs_source_aux,),  # absolute aux path to the SAME file
        aux_kinds={abs_source_aux: "translation_map"},
    )
    # The translation map is exactly one entry, even though the source
    # walk would have found it on its own and the operator also named
    # it via aux_paths.
    translation_entries = [
        e for e in ledger.entries
        if e.auxiliary_kind == "translation_map"
    ]
    assert len(translation_entries) == 1
    # And its file_id is the content-derived form, not the disambiguated one.
    file_id = translation_entries[0].file_id
    assert re.match(r"^translation_map-[0-9a-f]{8}$", file_id), file_id


def test_absolute_source_dir_with_relative_aux_path_dedupes(tmp_path, monkeypatch):
    """The mirror: `source_dir` is absolute, `aux_paths` references the
    same file via a relative path. The two spellings collapse.
    """
    source = tmp_path / "source"
    source.mkdir()
    _write_json(source / "general_scenes.json", GENERAL_SCENE_FILE)
    # A translation map inside source_dir.
    source_aux = source / "translation_map.json"
    _write_json(source_aux, TRANSLATION_MAP_FILE)

    # Switch cwd so a relative aux path resolves to the same file.
    monkeypatch.chdir(source)

    ledger = inventory_source_dir(
        source.resolve(),  # absolute source_dir
        aux_paths=("translation_map.json",),  # relative aux path
        aux_kinds={"translation_map.json": "translation_map"},
    )
    translation_entries = [
        e for e in ledger.entries if e.auxiliary_kind == "translation_map"
    ]
    # One entry only: the source walk discovered the file via rglob
    # and the relative aux path resolves to the same canonical form.
    assert len(translation_entries) == 1
    # And the file_id is the content-derived id, not the disambiguated one.
    file_id = translation_entries[0].file_id
    assert re.match(r"^translation_map-[0-9a-f]{8}$", file_id), file_id


def test_aux_paths_outside_source_dir_are_retained(tmp_path):
    """A relative aux path resolving OUTSIDE source_dir is still inventoried.

    The earlier test proved dedup when the same file is in both lists.
    This test proves a distinct aux file outside source_dir is NOT
    dropped - the inventory retains it.
    """
    source = _source_dir(tmp_path)
    # An aux file in a completely separate directory.
    other = tmp_path / "elsewhere"
    other.mkdir()
    aux = other / "translations.json"
    _write_json(aux, TRANSLATION_MAP_FILE)

    ledger = inventory_source_dir(
        source,
        aux_paths=(aux,),
        aux_kinds={aux: "translation_map"},
    )
    # The aux file appears as an entry.
    aux_entries = [
        e for e in ledger.entries
        if e.auxiliary_kind == "translation_map" and e.file_stem == "translations"
    ]
    assert len(aux_entries) == 1
    assert aux_entries[0].status == STATUS_AUXILIARY


def test_safe_resolve_handles_nonexistent_paths(tmp_path):
    """A path that does not exist is still resolvable; the canonical form
    is what the dedup set keys on, not whether the file is on disk.
    """
    from backend.resource_ledger import _safe_resolve
    rel = tmp_path / "does_not_exist.json"
    abs_ = rel.resolve()
    assert _safe_resolve(rel) == abs_
    assert _safe_resolve(abs_) == abs_


# -- Keyed-record and scalar-keyed-collection patterns ---------------------


# Invented English-only fixtures shaped like the real operator corpus:
# a dict whose values are all dicts (a keyed record collection) and a
# dict whose values are scalars (a scalar keyed collection). The keys
# are deliberately non-identity so a regression cannot accidentally
# assert the wrong thing.
INVENTED_KEYED_RECORD_COLLECTION = {
    "invented_profile_alpha": {
        "body_shape": "invented body shape for alpha",
        "description": "invented description for alpha",
        "display_name": "invented alpha display name",
    },
    "invented_profile_beta": {
        "body_shape": "invented body shape for beta",
        "description": "invented description for beta",
        "display_name": "invented beta display name",
    },
    "invented_profile_gamma": {
        "body_shape": "invented body shape for gamma",
        "description": "invented description for gamma",
        "display_name": "invented gamma display name",
    },
}

INVENTED_SCALAR_KEYED_COLLECTION = {
    "invented_key_one": "invented scalar value one",
    "invented_key_two": "invented scalar value two",
    "invented_key_three": "invented scalar value three",
}


def test_keyed_record_collection_records_inner_schema_without_outer_keys(tmp_path):
    """A dict of records is recorded by the inner schema, with the
    OUTER keys (the identity-like strings) absent from every output.
    """
    source = tmp_path / "source"
    source.mkdir()
    # The file is named with a stem NOT in the manifest, so it lands
    # as undeclared. The schema and structural notes are still
    # recorded; the privacy rule applies regardless of the status.
    _write_json(source / "invented_profiles.json", INVENTED_KEYED_RECORD_COLLECTION)

    ledger = inventory_source_dir(source)
    entry = next(e for e in ledger.entries if e.file_stem == "invented_profiles")
    # The inner schema is recorded: the shared keys are observed.
    by_name = {f.name: f for f in entry.field_observations}
    for expected in ("body_shape", "description", "display_name"):
        assert expected in by_name, expected
        assert by_name[expected].structural_type in (
            "string", "list", "number", "boolean", "null", "dict"
        )
    # The outer (identity-like) keys are NEVER emitted, anywhere.
    serialised = json.dumps(entry.to_dict(), ensure_ascii=True)
    for forbidden in (
        "invented_profile_alpha", "invented_profile_beta", "invented_profile_gamma",
    ):
        assert forbidden not in serialised, f"identity key leaked: {forbidden!r}"
    # And the path uses the placeholder, not any real key.
    for shape in entry.entry_shapes:
        assert "invented_profile_" not in shape.path
        assert "<id-key>" in shape.path
    # A structural note records the pattern.
    assert any("keyed_record_collection" in n for n in entry.structural_notes)


def test_scalar_keyed_collection_records_empty_schema_with_placeholder_path(tmp_path):
    """A dict of scalars is recorded as an empty entry shape with a
    structural note, never as one entry per scalar value, and the
    outer keys (the identity-like strings) are not emitted.
    """
    source = tmp_path / "source"
    source.mkdir()
    _write_json(source / "invented_scalar_map.json", INVENTED_SCALAR_KEYED_COLLECTION)

    ledger = inventory_source_dir(source)
    entry = next(e for e in ledger.entries if e.file_stem == "invented_scalar_map")
    # The empty entry shape is recorded.
    assert len(entry.entry_shapes) >= 1
    empty_shape = next(s for s in entry.entry_shapes if s.top_level_keys == ())
    assert empty_shape.field_types == {}
    # The outer (identity-like) keys are not emitted.
    serialised = json.dumps(entry.to_dict(), ensure_ascii=True)
    for forbidden in (
        "invented_key_one", "invented_key_two", "invented_key_three",
    ):
        assert forbidden not in serialised, f"identity key leaked: {forbidden!r}"
    # Path uses the placeholder, not any real key.
    for shape in entry.entry_shapes:
        assert "invented_key_" not in shape.path
        assert "<id-key>" in shape.path
    # Structural note records the pattern.
    assert any("scalar_keyed_collection" in n for n in entry.structural_notes)


def test_not_adopted_files_still_record_their_structural_schema(tmp_path):
    """A file the manifest refuses (amateurs/celebrities in production)
    still gets its structural schema recorded. The user requirement is
    that not-adopted files are not silently reduced to an empty
    observation merely because they do not contain entry-marker
    keys.
    """
    source = tmp_path / "source"
    source.mkdir()
    # The manifest names `amateurs` as a `not_adopted` library; we
    # write a fixture file with that stem and a keyed-record shape,
    # and assert the schema is recorded.
    _write_json(source / "amateurs.json", INVENTED_KEYED_RECORD_COLLECTION)

    ledger = inventory_source_dir(source)
    entry = next(e for e in ledger.entries if e.file_stem == "amateurs")
    assert entry.status == STATUS_NOT_ADOPTED
    # The schema is still recorded, with field observations.
    by_name = {f.name: f for f in entry.field_observations}
    assert "body_shape" in by_name
    assert "description" in by_name
    assert "display_name" in by_name
    # And the outer identity-like keys are not in the output.
    serialised = json.dumps(entry.to_dict(), ensure_ascii=True)
    for forbidden in (
        "invented_profile_alpha", "invented_profile_beta", "invented_profile_gamma",
    ):
        assert forbidden not in serialised


# Invented English-only fixture: a dict whose values are uniformly
# lists, the same shape the celebrities corpus uses for its `z`
# collection. The outer keys are deliberately non-identity so a
# regression cannot accidentally assert the wrong thing.
INVENTED_LIST_KEYED_COLLECTION = {
    "invented_id_alpha": ["invented trait one", "invented trait two"],
    "invented_id_beta": ["invented trait three", "invented trait four"],
    "invented_id_gamma": ["invented trait five", "invented trait six"],
}


def test_list_keyed_collection_records_element_type_without_outer_keys(tmp_path):
    """A dict whose values are all lists is recorded as a single
    entry shape with one synthetic `value` field whose type matches
    the list element type. The OUTER (identity-like) keys are
    NEVER emitted anywhere.
    """
    source = tmp_path / "source"
    source.mkdir()
    _write_json(source / "invented_list_map.json", INVENTED_LIST_KEYED_COLLECTION)

    ledger = inventory_source_dir(source)
    entry = next(e for e in ledger.entries if e.file_stem == "invented_list_map")
    # A structural note records the pattern.
    assert any("list_keyed_collection" in n for n in entry.structural_notes)
    # The outer (identity-like) keys are NEVER emitted, anywhere.
    serialised = json.dumps(entry.to_dict(), ensure_ascii=True)
    for forbidden in (
        "invented_id_alpha", "invented_id_beta", "invented_id_gamma",
    ):
        assert forbidden not in serialised, f"identity key leaked: {forbidden!r}"
    # And the path uses the placeholder, not any real key.
    for shape in entry.entry_shapes:
        assert "invented_id_" not in shape.path
        assert "<id-key>" in shape.path


def test_unmatched_field_names_carry_a_pending_reason(tmp_path):
    """A field with no role in the provisional table is reported as
    unused with a non-empty note explaining why: that is the explicit
    pending reason the inventory's contract requires.
    """
    source = tmp_path / "source"
    source.mkdir()
    _write_json(source / "general_scenes.json", {
        "library": "general_scenes",
        "items": [
            {
                "identifier": "invented_pending_01",
                "label": "invented label for pending test",
                "an_invented_field_with_no_role": "invented prose that must not leak",
            }
        ],
    })
    ledger = inventory_source_dir(source)
    entry = next(e for e in ledger.entries if e.file_stem == "general_scenes")
    by_name = {f.name: f for f in entry.field_observations}
    obs = by_name["an_invented_field_with_no_role"]
    assert obs.role == "unused"
    assert obs.notes, "an unmatched field must carry a pending reason"
    serialised = json.dumps(entry.to_dict(), ensure_ascii=True)
    assert "invented prose that must not leak" not in serialised


def test_perspective_scenes_structurally_recorded_without_parity_claim(tmp_path):
    """The fused library is recorded as a known shape with its
    fields; the ledger never claims compiled behavior parity.
    """
    source = tmp_path / "source"
    source.mkdir()
    _write_json(source / "perspective_scenes.json", PERSPECTIVE_FILE)
    ledger = inventory_source_dir(source)
    entry = next(e for e in ledger.entries if e.file_stem == "perspective_scenes")
    assert entry.status == STATUS_KNOWN_SHAPE
    by_name = {f.name: f for f in entry.field_observations}
    # The fused entry's field is recorded structurally.
    assert "prompt" in by_name
    assert by_name["prompt"].structural_type == "string"
    # And no claim of parity appears anywhere in the entry.
    assert "parity" not in json.dumps(entry.to_dict(), ensure_ascii=True).lower()


def test_an_aux_path_unknown_kind_is_named_not_guessed(tmp_path):
    """Operator-selected aux kind outside the documented set is reported as malformed."""
    source = _source_dir(tmp_path)
    aux_path = tmp_path / "translations.json"
    _write_json(aux_path, TRANSLATION_MAP_FILE)
    ledger = inventory_source_dir(
        source,
        aux_paths=(aux_path,),
        aux_kinds={aux_path: "an_invented_kind"},
    )
    aux_entry = next(
        e for e in ledger.entries
        if e.file_stem == "translations"
    )
    assert aux_entry.status == STATUS_MALFORMED
    assert aux_entry.reason
    assert aux_entry.auxiliary_kind == "an_invented_kind"


# -- 5. File IDs are unique even on identical stem + content. -------------


def test_duplicate_stem_and_content_get_distinct_file_ids(tmp_path):
    """Two files sharing stem and bytes get distinct file_ids via positional suffix."""
    source = tmp_path / "source"
    source.mkdir()
    # A copy of the same JSON content under the same stem, in a
    # different subdirectory. rglob sees both.
    sub = source / "subdir"
    sub.mkdir()
    _write_json(source / "general_scenes.json", GENERAL_SCENE_FILE)
    _write_json(sub / "general_scenes.json", GENERAL_SCENE_FILE)

    ledger = inventory_source_dir(source)
    general = [e for e in ledger.entries if e.file_stem == "general_scenes"]
    assert len(general) == 2
    # Same content -> same digest; disambiguator must separate them.
    digests = {e.content_digest for e in general}
    assert len(digests) == 1
    file_ids = {e.file_id for e in general}
    assert len(file_ids) == 2
    # The first occurrence keeps the content-derived id; the second
    # gets a `-2` suffix. The pattern is `<stem>-<8 hex>` or
    # `<stem>-<8 hex>-<seq>`.
    first, second = sorted(file_ids)
    assert re.match(r"^general_scenes-[0-9a-f]{8}$", first), first
    assert re.match(r"^general_scenes-[0-9a-f]{8}-2$", second), second


# -- 6. Field shapes and roles; no source values or prose. ----------------


def test_field_shapes_are_recorded_without_source_values(tmp_path):
    """Field structural types come from the value's Python type, not its value."""
    source = _source_dir(tmp_path)
    ledger = inventory_source_dir(source)
    general = next(e for e in ledger.entries if e.file_stem == "general_scenes")

    by_name = {f.name: f for f in general.field_observations}
    assert by_name["label"].structural_type == TYPE_STRING
    assert by_name["theme_text"].structural_type == TYPE_STRING
    assert by_name["tags"].structural_type == TYPE_LIST
    assert by_name["weight"].structural_type == TYPE_NUMBER
    serialised = json.dumps(general.to_dict(), ensure_ascii=True)
    for forbidden in (
        "invented label 1", "invented label 2",
        "invented theme for the first entry",
        "invented theme for the second entry",
        "invented workplace label",
        "invented workplace theme",
    ):
        assert forbidden not in serialised, f"prose leaked: {forbidden!r}"


def test_field_roles_match_the_provisional_table():
    """A field name in the table gets the documented role and consumer evidence."""
    for field_name, info in PROVISIONAL_FIELD_ROLES.items():
        assert info["role"] in ALL_ROLES, field_name
        assert isinstance(info["evidence"], tuple), field_name
        for ref in info["evidence"]:
            assert "." in ref, f"{field_name}: bad evidence {ref!r}"
            assert "/" not in ref and "\\" not in ref, field_name


def test_anchor_suffix_and_mood_prefix_are_writer_guidance():
    """The two name rules `is_guidance_field` enforces get a role and notes."""
    from backend.resource_ledger import _role_for_field
    info = _role_for_field("action_anchor")
    assert info["role"] == ROLE_WRITER_GUIDANCE
    assert "name suffix rule" in info["notes"]

    info = _role_for_field("mood_warm")
    assert info["role"] == ROLE_WRITER_GUIDANCE
    assert "name prefix rule" in info["notes"]

    info = _role_for_field("an_invented_field_name")
    assert info["role"] == ROLE_UNUSED
    assert info["evidence"] == ()


def test_consumer_evidence_uses_module_function_form(tmp_path):
    """Consumer evidence references do not contain paths, URLs or absolute file locations."""
    source = _source_dir(tmp_path)
    ledger = inventory_source_dir(source)
    for entry in ledger.entries:
        for field in entry.field_observations:
            for ref in field.consumer_evidence:
                assert "\\" not in ref
                assert "/" not in ref
                assert ":" not in ref
                assert " " not in ref


# -- 7. Every non-OK status carries a non-empty reason. -------------------


def test_every_non_ok_status_has_a_non_empty_reason(tmp_path):
    """A reason is non-empty for every status in STATUSES_REQUIRING_REASON."""
    source = _source_dir(tmp_path)
    ledger = inventory_source_dir(source)
    for entry in ledger.entries:
        if entry.status in STATUSES_REQUIRING_REASON:
            assert entry.reason, f"{entry.file_stem}: status {entry.status} has no reason"
        else:
            assert entry.reason == "", f"{entry.file_stem}: unexpected reason for OK status"


def test_malformed_and_unknown_shapes_are_reported_not_guessed(tmp_path):
    """A malformed JSON and a top-level scalar are named, not silently accepted."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "general_scenes.json").write_text("not json at all", encoding="utf-8")
    (source / "workplace_scenes.json").write_text("\"a bare string at the top\"", encoding="utf-8")

    ledger = inventory_source_dir(source)
    by_stem = {e.file_stem: e for e in ledger.entries}

    assert by_stem["general_scenes"].status == STATUS_MALFORMED
    assert by_stem["general_scenes"].reason

    assert by_stem["workplace_scenes"].status == STATUS_UNKNOWN_SHAPE
    assert by_stem["workplace_scenes"].reason


def test_an_unparseable_auxiliary_is_reported_as_malformed(tmp_path):
    """A translation-map-shaped file with a missing key is malformed, not auxiliary."""
    source = tmp_path / "source"
    source.mkdir()
    broken = {
        "k1": {"source": "a", "fields": ["label"]},
    }
    _write_json(source / "translation_map.json", broken)
    ledger = inventory_source_dir(source)
    entry = next(e for e in ledger.entries if e.file_stem == "translation_map")
    assert entry.status == STATUS_MALFORMED
    assert entry.reason
    assert entry.auxiliary_kind == "translation_map"


def test_an_empty_file_is_pending(tmp_path):
    """An empty file is reported as `pending_mapping`."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "general_scenes.json").write_text("", encoding="utf-8")
    ledger = inventory_source_dir(source)
    entry = next(e for e in ledger.entries if e.file_stem == "general_scenes")
    assert entry.status == STATUS_PENDING_MAPPING
    assert entry.reason


def test_undeclared_and_not_adopted_files_are_named(tmp_path):
    """An undeclared file is reported as `undeclared`; a not-adopted as `not_adopted`."""
    source = _source_dir(tmp_path)
    ledger = inventory_source_dir(source)
    by_stem = {e.file_stem: e for e in ledger.entries}

    undeclared = by_stem["invented_undeclared"]
    assert undeclared.status == STATUS_UNDECLARED
    assert undeclared.reason

    not_adopted = by_stem["amateurs"]
    assert not_adopted.status == STATUS_NOT_ADOPTED
    assert not_adopted.reason


# -- 8. Inventory mechanics -----------------------------------------------


def test_inventory_file_rejects_a_directory_as_a_path(tmp_path):
    """A directory passed where a file is wanted is reported as malformed."""
    source = _source_dir(tmp_path)
    nested_dir = source / "nested"
    nested_dir.mkdir()
    entry = inventory_file(nested_dir)
    assert entry.status == STATUS_MALFORMED
    assert entry.reason


def test_inventory_source_dir_rejects_a_non_directory(tmp_path):
    """A path that is not a directory raises NotADirectoryError at the call."""
    with pytest.raises(NotADirectoryError):
        inventory_source_dir(tmp_path / "does_not_exist")


def test_inventory_source_dir_requires_a_path():
    """A None or empty source_dir is refused at the call."""
    with pytest.raises(ValueError, match="required"):
        inventory_source_dir(None)
    with pytest.raises(ValueError, match="required"):
        inventory_source_dir("")


def test_ledger_totals_match_the_statuses_present(tmp_path):
    """`by_status()` returns one count per declared status, plus a total."""
    source = _source_dir(tmp_path)
    ledger = inventory_source_dir(source)
    totals = ledger.by_status()
    for status in ALL_STATUSES:
        assert status in totals
    assert totals["__total__"] == len(ledger.entries)
    assert totals[STATUS_KNOWN_SHAPE] >= 3
    assert totals[STATUS_AUXILIARY] >= 4
    assert totals[STATUS_UNDECLARED] >= 1
    assert totals[STATUS_NOT_ADOPTED] >= 1


# -- 9. Generated artifacts are structural-only and safe. ------------------


def test_saved_ledger_carries_no_absolute_paths_or_prose(tmp_path):
    """The JSON the ledger writes contains no absolute paths, no CJK, no source prose."""
    source = _source_dir(tmp_path)
    ledger = inventory_source_dir(source)
    target = tmp_path / "ledger.json"
    save_ledger(ledger, target)
    text = target.read_text(encoding="utf-8")

    for pattern in (
        r"[A-Za-z]:[\\/]+Users[\\/]+",
        r"/(?:home|Users)/",
        r"[A-Za-z]:[\\/]",
    ):
        assert not re.search(pattern, text), f"path pattern {pattern!r} matched"

    cjk = re.compile("[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u309f"
                     "\u30a0-\u30ff\u31f0-\u31ff\uac00-\ud7af\u1100-\u11ff"
                     "\u3130-\u318f]")
    assert not cjk.search(text), "CJK character in ledger"

    for forbidden in (
        "invented label 1", "invented label 2",
        "invented theme for the first entry",
        "invented workplace label",
        "low angle from the foot of the bed",
        "invented judge label for the camera row",
    ):
        assert forbidden not in text, f"prose leaked: {forbidden!r}"


def test_saved_ledger_is_loadable_json_with_expected_shape(tmp_path):
    """The saved ledger is JSON and its top-level keys are the documented ones."""
    source = _source_dir(tmp_path)
    ledger = inventory_source_dir(source)
    target = tmp_path / "ledger.json"
    save_ledger(ledger, target)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert set(loaded.keys()) == {"source_dir", "generated_at", "entries", "totals"}
    for entry in loaded["entries"]:
        for required in (
            "file_id", "file_stem", "content_digest", "top_level_shape",
            "declared_kind", "status", "reason", "auxiliary",
            "auxiliary_kind", "entry_count", "entry_shapes",
            "field_observations",
        ):
            assert required in entry


# -- 10. The public surface is closed. -------------------------------------


def test_statuses_roles_and_types_are_the_documented_sets():
    """A private status or role would silently fork the ledger."""
    assert set(ALL_STATUSES) == {
        STATUS_KNOWN_SHAPE, STATUS_AUXILIARY, STATUS_MALFORMED,
        STATUS_UNDECLARED, STATUS_NOT_ADOPTED, STATUS_UNKNOWN_SHAPE,
        STATUS_PENDING_MAPPING, STATUS_REFUSED,
    }
    assert set(ALL_ROLES) == {
        ROLE_IDENTIFIER, ROLE_SELECTION_METADATA, ROLE_DESCRIPTIVE_INPUT,
        ROLE_WRITER_GUIDANCE, ROLE_AUXILIARY_DATA, ROLE_UNUSED,
    }
    assert set(ALL_TYPES) == {
        TYPE_STRING, TYPE_NUMBER, TYPE_BOOLEAN, TYPE_NULL, TYPE_LIST, TYPE_DICT,
    }


def test_no_field_observation_carries_an_absolute_path(tmp_path):
    """Field observations are also structural-only - no values, no paths, no prose."""
    source = _source_dir(tmp_path)
    ledger = inventory_source_dir(source)
    for entry in ledger.entries:
        for field in entry.field_observations:
            assert "\\" not in field.name
            assert "/" not in field.name
            assert ":" not in field.name
            assert field.structural_type in ALL_TYPES
            assert field.role in ALL_ROLES


def test_file_id_is_stem_plus_digest_prefix_with_optional_disambiguator(tmp_path):
    """The file_id format is `<stem>-<8 hex>` or `<stem>-<8 hex>-<seq>`."""
    source = _source_dir(tmp_path)
    ledger = inventory_source_dir(source)
    pattern = re.compile(r"^[a-z0-9_]+-[0-9a-f]{8}(?:-\d+)?$")
    for entry in ledger.entries:
        if entry.status == STATUS_MALFORMED and entry.content_digest == "":
            continue
        assert pattern.match(entry.file_id), entry.file_id
        # The digest the file_id is built from matches the entry's
        # content_digest field's first 8 hex chars.
        if "-" in entry.file_id[len(entry.file_stem) + 1:]:
            suffix = entry.file_id[len(entry.file_stem) + 1:]
        else:
            suffix = entry.file_id[len(entry.file_stem) + 1:]
        assert suffix.startswith(entry.content_digest[:8])


def test_perspective_scenes_is_known_shape_with_prompt_field(tmp_path):
    """The fused library is `known_shape`, not `refused`: the inventory reads shapes."""
    source = _source_dir(tmp_path)
    ledger = inventory_source_dir(source)
    entry = next(e for e in ledger.entries if e.file_stem == "perspective_scenes")
    assert entry.status == STATUS_KNOWN_SHAPE
    by_name = {f.name: f for f in entry.field_observations}
    assert "prompt" in by_name
    assert by_name["prompt"].structural_type == TYPE_STRING
    serialised = json.dumps(entry.to_dict(), ensure_ascii=True)
    assert "low angle from the foot of the bed" not in serialised


# -- 11. Resource-prompt preparation mapping (task 1.2) --------------------
#
# Task 1.2 defines the SUPPORTED field mappings for resource-based prompt
# preparation. The inventory above classifies what is OBSERVED; this
# section classifies what is SUPPORTED for the prompt path, declares
# required versus optional fields, declares the text `weight`
# adaptation explicitly, marks the fused-scenes compiled behavior as
# unverified, and refuses to let an unknown or unmapped field reach
# the prompt silently. All fixtures are invented English-only data;
# none of the operator's source prose, identifiers, or paths reaches
# the assertions below.

from backend import resource_prompts as rp  # noqa: E402  (group import below)


# -- 11.1 Complete field classification -----------------------------------


def test_preparation_mapping_classifies_each_observed_field_for_its_own_kind(tmp_path):
    """Every observed field is classified for the resource kind that owns it.

    The fixtures exercise rooms, fused scenes, and all four auxiliary
    schemas. A field cannot satisfy the contract merely because it appears
    in another kind's mapping: the effective kind is the declared scene kind
    or, for an auxiliary entry, its auxiliary kind.
    """
    ledger = inventory_source_dir(_source_dir(tmp_path))
    unmapped: list[str] = []
    for entry in ledger.entries:
        kind = entry.declared_kind or entry.auxiliary_kind
        if kind not in rp.ALL_PREPARATION_KINDS:
            continue
        for field in entry.field_observations:
            info = rp.classify_field(kind, field.name)
            if info["role"] == "unmapped":
                unmapped.append(f"{kind}.{field.name}")
    assert not unmapped, (
        "observed fields without a mapping for their own kind: "
        + ", ".join(sorted(unmapped))
    )


def test_every_entry_in_the_preparation_mapping_has_a_valid_role():
    """Each field entry carries a role from the documented set; no
    private role can silently fork the contract.

    The six roles are the union the spec under
    `openspec/.../specs/resource-prompts` calls out by name. The
    `unmapped` sentinel that `classify_field` returns is NOT a
    membership role: it is a return-only marker for "this field is
    not in the mapping", and it must never appear as the `role` of
    an entry in `PREPARATION_FIELD_MAPPING`.
    """
    bad: list[str] = []
    for kind, mapping in rp.PREPARATION_FIELD_MAPPING.items():
        for name, info in mapping.items():
            role = info.get("role")
            if role not in rp.ALL_PREPARATION_ROLES:
                bad.append(f"{kind}.{name} has role {role!r}")
    assert not bad, "field entries with a non-documented role:\n" + "\n".join(bad)


def test_every_scene_kind_lists_every_field_in_one_of_the_six_roles():
    """The six roles are exhaustive: every field in the scene
    mappings is in exactly one of `identity`, `selection_metadata`,
    `descriptive_input`, `writer_guidance`, `auxiliary_data`, or
    `intentionally_unused`. The auxiliary kinds use
    `auxiliary_data` only.
    """
    for kind in (rp.KIND_ROOMS, rp.KIND_FUSED_SCENES):
        for name, info in rp.PREPARATION_FIELD_MAPPING[kind].items():
            assert info["role"] in rp.ALL_PREPARATION_ROLES, (
                f"{kind}.{name}: {info['role']!r} is not in "
                f"{rp.ALL_PREPARATION_ROLES}"
            )
    for kind in (
        rp.KIND_TRANSLATION_MAP, rp.KIND_CUT_MAP,
        rp.KIND_MINED_FAMILIES, rp.KIND_MINED_LABELS,
    ):
        for name, info in rp.PREPARATION_FIELD_MAPPING[kind].items():
            assert info["role"] == rp.ROLE_AUXILIARY_DATA, (
                f"auxiliary kind {kind!r}.{name}: expected only "
                f"auxiliary_data, got {info['role']!r}"
            )


# -- 11.2 Required versus optional preparation fields ---------------------


def test_required_fields_for_rooms_are_documented():
    """The rooms contract requires `id`, `label`, and `scene_theme`.

    Each is the only canonical way the preparation builds a room
    prompt: an `id` for the take's provenance, a `label` (or one of
    its aliases) for the visible name, and a `scene_theme` (or one of
    its aliases) for the prose the session looks at. A name is
    missing -> the entry cannot be prepared and the operator gets a
    field-specific reason.
    """
    assert rp.required_fields_for_kind(rp.KIND_ROOMS) == ("id", "label", "scene_theme")


def test_required_fields_for_fused_scenes_are_documented():
    """The fused-scenes contract requires `id` and `prompt`.

    `prompt` is the source's own free prose and the only
    descriptive input the spec pins as required. Its compiled
    behavior is explicitly unverified; the contract says the
    string is preserved intact, not auto-split.
    """
    assert rp.required_fields_for_kind(rp.KIND_FUSED_SCENES) == ("id", "prompt")


def test_auxiliary_kinds_have_no_required_fields():
    """Auxiliary schemas are pipeline data and never block preparation."""
    for kind in (
        rp.KIND_TRANSLATION_MAP, rp.KIND_CUT_MAP,
        rp.KIND_MINED_FAMILIES, rp.KIND_MINED_LABELS,
    ):
        assert rp.required_fields_for_kind(kind) == (), kind


def test_missing_required_field_is_reported_with_a_field_specific_reason():
    """A required field that is missing produces a reason naming the field and the kind."""
    reasons = rp.validate_resource_entry(
        rp.KIND_ROOMS, {"label": "sample", "scene_theme": "sample"}
    )
    assert any("'id'" in r and "missing" in r for r in reasons), reasons


# -- 11.3 Explicit optional reasons ---------------------------------------


def test_every_intentionally_unused_field_has_a_non_empty_reason():
    """The contract documents every unused field with a reason.

    A field with role `intentionally_unused` and no reason would
    be a silent omission: the field is not in the prompt and the
    operator has no way to know why. The test catches that.
    """
    bad: list[str] = []
    for kind, mapping in rp.PREPARATION_FIELD_MAPPING.items():
        for name, info in mapping.items():
            if info.get("role") != rp.ROLE_INTENTIONALLY_UNUSED:
                continue
            reason = info.get("reason", "")
            if not reason or not reason.strip():
                bad.append(f"{kind}.{name}")
    assert not bad, "intentionally_unused fields without a reason:\n" + "\n".join(bad)


def test_classify_returns_the_documented_reason_for_unused_fields():
    """`classify_field` returns the same reason the mapping declares."""
    for name in rp.intentionally_unused_fields_for_kind(rp.KIND_ROOMS):
        info = rp.classify_field(rp.KIND_ROOMS, name)
        assert info["role"] == rp.ROLE_INTENTIONALLY_UNUSED, name
        assert info.get("reason"), name


# -- 11.4 Safe auxiliary handling -----------------------------------------


def test_auxiliary_kinds_return_no_prompt_inputs():
    """An auxiliary file is pipeline data, never a prompt input.

    The check covers the four auxiliary kinds and accepts both
    record-shape values (translation_map, cut_map) and scalar
    values (mined_families, mined_labels). The result is always
    an empty dict, regardless of the value.
    """
    assert rp.extract_prompt_inputs(rp.KIND_TRANSLATION_MAP, {
        "source": "a", "translation": "b", "fields": ["label"]
    }) == {}
    assert rp.extract_prompt_inputs(rp.KIND_CUT_MAP, {
        "camera": "a", "act": "b", "room": "c"
    }) == {}
    assert rp.extract_prompt_inputs(rp.KIND_MINED_FAMILIES, "rear_entry_pov") == {}
    assert rp.extract_prompt_inputs(rp.KIND_MINED_LABELS, "mined-row-label") == {}


def test_auxiliary_record_with_an_unknown_field_is_reported_not_silently_used():
    """A translation_map record with a non-documented top-level key
    is reported with a field-specific reason, not silently treated
    as auxiliary data.
    """
    record = {
        "source": "a", "translation": "b", "fields": ["label"],
        "invented_extra_field": "invented value that the contract does not name",
    }
    reasons = rp.validate_resource_entry(rp.KIND_TRANSLATION_MAP, record)
    assert any("'invented_extra_field'" in r for r in reasons), reasons


def test_mined_families_scalar_must_be_a_non_empty_string():
    """A scalar auxiliary value is auxiliary data when it is a
    non-empty string; an empty or non-string value is reported.
    """
    assert rp.validate_resource_entry(rp.KIND_MINED_FAMILIES, "rear_entry_pov") == []
    assert rp.validate_resource_entry(rp.KIND_MINED_LABELS, "mined-row-label") == []
    assert rp.validate_resource_entry(rp.KIND_MINED_FAMILIES, "") != []
    assert rp.validate_resource_entry(rp.KIND_MINED_FAMILIES, 42) != []


def test_an_auxiliary_data_field_on_a_scene_is_reported():
    """A scene entry that carries an auxiliary record field is
    reported as a role mismatch, not silently dropped.
    """
    reasons = rp.validate_resource_entry(
        rp.KIND_ROOMS,
        {"id": "r1", "label": "x", "scene_theme": "y", "source": "a"},
    )
    assert any("'source'" in r and "auxiliary" in r for r in reasons), reasons


# -- 11.5 No compiled-parity claims ---------------------------------------


def test_weight_text_adaptation_is_explicit_and_forbids_prompt_emphasis():
    """The `weight` field is a numeric selection parameter, not a text
    emphasis. The constant pins the rule and the test pins the rule.
    """
    text = rp.WEIGHT_TEXT_ADAPTATION
    assert isinstance(text, str) and text.strip(), "weight adaptation must be a non-empty string"
    # The text must forbid translating weight into a prompt-weight syntax.
    assert "MUST NOT" in text or "must not" in text.lower(), text
    assert "weight" in text.lower()
    # It must reference the selection role the inventory already records.
    assert "selection" in text.lower() or "outlier" in text.lower(), text


def test_fused_scenes_compiled_behavior_is_marked_unverified():
    """The perspective (fused_scenes) compiled behavior is explicitly
    unverified: the constant names the unverified status and the test
    pins it.
    """
    text = rp.FUSED_SCENES_COMPILED_BEHAVIOR
    assert isinstance(text, str) and text.strip(), "compiled-behavior note must be a non-empty string"
    assert "unverified" in text.lower() or "not verifiable" in text.lower(), text
    # The contract explicitly disclaims auto-decomposition of the
    # source prose (the prose is preserved intact, not split). The
    # contract's negative form ("does not auto-split") is what we
    # require; the module-level parity-claim check further down
    # covers positive claims like "achieves parity" or
    # "guarantees parity".
    lower = text.lower()
    assert "does not auto-split" in lower or "does NOT auto-split" in text, text
    assert "not claim parity" in lower or "does not claim parity" in lower, text


def test_perspective_compiled_behavior_is_unverified_in_classify():
    """The 'prompt' field's mapping entry points at the unverified note
    rather than claiming a compiled role.
    """
    info = rp.classify_field(rp.KIND_FUSED_SCENES, "prompt")
    assert info["role"] == rp.ROLE_DESCRIPTIVE_INPUT
    assert "FUSED_SCENES_COMPILED_BEHAVIOR" in info.get("notes", ""), info


def test_module_does_not_claim_parity_for_perspective_or_compiled_behavior():
    """A grep over the module's source for `parity` finds only the
    unverified declarations, not any positive claim. The contract
    marks the compiled behavior unverified; it does not promise it.
    """
    module_text = (Path(__file__).resolve().parent.parent /
                   "backend" / "resource_prompts.py").read_text(encoding="utf-8")
    lower = module_text.lower()
    # The word "parity" appears in the unverified declarations; it
    # never appears in a positive form like "achieves parity" or
    # "matches parity" or "guarantees parity".
    for forbidden in (
        "achieves parity", "matches parity", "guarantees parity",
        "byte-for-byte parity", "byte for byte parity",
    ):
        assert forbidden not in lower, forbidden


# -- 11.6 Unknown fields never enter the prompt silently -------------------


def test_unknown_field_is_blocked_from_silent_prompt_entry():
    """A field the mapping does not name cannot reach the prompt.

    `extract_prompt_inputs` returns only fields whose role is
    `descriptive_input` for the given kind. A name that is in the
    entry but not in the mapping is dropped by construction, and
    the `unmapped` sentinel is not a `descriptive_input` role.
    """
    entry = {
        "id": "r1", "label": "sample", "scene_theme": "sample",
        "invented_unknown_field": "this MUST NOT enter the prompt",
    }
    inputs = rp.extract_prompt_inputs(rp.KIND_ROOMS, entry)
    assert "invented_unknown_field" not in inputs
    # And the validator reports it with a field-specific reason.
    reasons = rp.validate_resource_entry(rp.KIND_ROOMS, entry)
    assert any("'invented_unknown_field'" in r for r in reasons), reasons


def test_classify_marks_unknown_field_with_an_unmapped_sentinel():
    """`classify_field` returns `unmapped` for a name the contract
    does not cover, with a non-empty reason.
    """
    info = rp.classify_field(rp.KIND_ROOMS, "an_invented_field_with_no_role")
    assert info["role"] == "unmapped", info
    assert info.get("reason"), info


def test_writer_guidance_name_rules_are_accepted_for_unmapped_names():
    """The inventory's `is_guidance_field` suffix and prefix rules
    promote a name the mapping does not cover to `writer_guidance`.
    The rule is the only way an unmapped name can avoid the
    `unmapped` sentinel, and it explicitly does not promote the
    name into the prompt.
    """
    assert rp.classify_field(rp.KIND_ROOMS, "camera_anchor")["role"] == rp.ROLE_WRITER_GUIDANCE
    assert rp.classify_field(rp.KIND_ROOMS, "mood_warm")["role"] == rp.ROLE_WRITER_GUIDANCE
    assert rp.classify_field(rp.KIND_FUSED_SCENES, "action_anchor")["role"] == rp.ROLE_WRITER_GUIDANCE
    # And the writer-guidance role is NOT a prompt input:
    assert "camera_anchor" not in rp.extract_prompt_inputs(
        rp.KIND_ROOMS, {"id": "r1", "label": "x", "scene_theme": "y", "camera_anchor": "z"}
    )


def test_perspective_entry_with_unmapped_field_is_reported_not_silently_used():
    """A fused-scene entry that carries an unmapped field is
    reported with a field-specific reason and the field is not
    added to the prompt input set.
    """
    entry = {
        "id": "f1", "prompt": "invented prose for the take",
        "label": "sample", "invented_unknown": "MUST NOT enter the prompt",
    }
    inputs = rp.extract_prompt_inputs(rp.KIND_FUSED_SCENES, entry)
    assert "invented_unknown" not in inputs
    assert "prompt" in inputs
    reasons = rp.validate_resource_entry(rp.KIND_FUSED_SCENES, entry)
    assert any("'invented_unknown'" in r for r in reasons), reasons


# -- 11.7 Privacy: the new module carries no private corpus identifiers ---


# A small subset of the patterns the repo's no-personal-data scan
# applies, repeated here so the new module has a focused guard in
# the same file as the contract tests. The repo-wide scan still
# covers every tracked file, including this one; the focused guard
# pins the new module's behaviour with a message that names the
# module under test, which the repo-wide scan does not.
_RESOURCE_PROMPTS_PATTERNS = {
    "windows user path": re.compile(
        r"[A-Za-z]:[\\/]+Users[\\/]+(?!<)[A-Za-z0-9._-]+", re.I
    ),
    "unix home path": re.compile(r"/(?:home|Users)/(?!<)[A-Za-z0-9._-]+"),
    "email address": re.compile(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    ),
    "api token": re.compile(
        r"\b(?:sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,})\b"
    ),
}


# A closed set of operator-side identifiers the new module must never
# reference in its source: the file stems the private ledger recorded
# for the operator's selected source libraries, plus the structural
# outer keys the same ledger used for the not-adopted collections.
# The list is structural: file_stem spellings and the two
# `structural_notes` patterns the coverage ledger recorded. They are
# not real names and they are not personal data; the assertion is
# that the new module's tracked source never copies them.
PRIVATE_CORPUS_MARKERS: tuple[str, ...] = (
    "amateurs", "celebrities", "perspective_scenes", "school_scenes",
    "medical_scenes", "workplace_scenes", "general_scenes",
    "keyed_record_collection", "list_keyed_collection",
    "scalar_keyed_collection",
)


def test_module_source_carries_no_private_corpus_markers():
    """The new module's source code never names the operator's
    file_stems or the structural patterns the private ledger
    recorded. The contract is structural, not corpus-specific.
    """
    module_text = (Path(__file__).resolve().parent.parent /
                   "backend" / "resource_prompts.py").read_text(encoding="utf-8")
    for marker in PRIVATE_CORPUS_MARKERS:
        assert marker not in module_text, (
            f"private corpus marker {marker!r} found in "
            f"backend/resource_prompts.py"
        )


def test_module_source_carries_no_absolute_paths_or_personal_data():
    """The same scan the repo applies to every tracked file is
    restricted to the new module: no Windows user paths, no Unix
    home paths, no email-shaped strings, no API tokens.
    """
    module_text = (Path(__file__).resolve().parent.parent /
                   "backend" / "resource_prompts.py").read_text(encoding="utf-8")
    for label, pattern in _RESOURCE_PROMPTS_PATTERNS.items():
        match = pattern.search(module_text)
        assert not match, (
            f"{label} pattern matched in backend/resource_prompts.py: "
            f"{match.group(0)!r}"
        )
