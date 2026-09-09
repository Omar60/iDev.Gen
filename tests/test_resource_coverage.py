"""Tests for the resource coverage and adoption report (task 6.3).

Asserts that:
- Structural ledger observations cross-reference cleanly with preparation mappings.
- Dispositions distinguish usable scenes, auxiliary pipeline data, and pending items.
- Every discovered file and field observation is accounted for (exact reconciliation).
- Any unmapped field marks the resource as pending and prevents adoption completion.
- Intentionally unused fields with explicit reasons count as supported without becoming prompt inputs.
- Writer guidance naming rules (*_anchor, mood_*) are recognized and not unmapped.
- Fused scenes mark compiled behavior as unverified without claiming parity.
- Auxiliary files remain auxiliary and are never classified as scene resources.
- Not-adopted, undeclared, malformed, and unknown-shape files remain pending with explicit reasons.
- Empty ledgers result in conservative coverage_complete=False and adoption_complete=False.
- The serialized report contains only structural data: no absolute paths, no personal data,
  no CJK glyphs, no raw values or outer identity keys.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

from backend.resource_ledger import (
    CoverageLedger,
    EntryShape,
    FieldObservation,
    FileEntry,
    ROLE_DESCRIPTIVE_INPUT,
    ROLE_IDENTIFIER,
    ROLE_SELECTION_METADATA,
    ROLE_UNUSED,
    ROLE_WRITER_GUIDANCE,
    SHAPE_OBJECT,
    STATUS_AUXILIARY,
    STATUS_KNOWN_SHAPE,
    STATUS_MALFORMED,
    STATUS_NOT_ADOPTED,
    STATUS_PENDING_MAPPING,
    STATUS_REFUSED,
    STATUS_UNDECLARED,
    STATUS_UNKNOWN_SHAPE,
    TYPE_NUMBER,
    TYPE_STRING,
    inventory_source_dir,
)
from backend.resource_prompts import (
    KIND_CUT_MAP,
    KIND_FUSED_SCENES,
    KIND_MINED_FAMILIES,
    KIND_MINED_LABELS,
    KIND_ROOMS,
    KIND_TRANSLATION_MAP,
    ROLE_AUXILIARY_DATA,
    ROLE_IDENTITY,
    ROLE_INTENTIONALLY_UNUSED,
)
from backend.resource_coverage import (
    ALL_DISPOSITIONS,
    COMPILED_BEHAVIOR_UNVERIFIED,
    DISPOSITION_AUXILIARY,
    DISPOSITION_PENDING,
    DISPOSITION_USABLE,
    RETENTION_AUXILIARY_PIPELINE,
    RETENTION_NOT_ADOPTED,
    RETENTION_UNDECLARED,
    RETENTION_UNSUPPORTED_SHAPE,
    RETENTION_USABLE_SCENE,
    ResourceCoverageReport,
    build_coverage_report,
    save_coverage_report,
)

ROOT = Path(__file__).resolve().parent.parent


# -- Invented test fixtures --------------------------------------------------


def _make_dummy_file_entry(
    file_id: str,
    file_stem: str,
    *,
    status: str = STATUS_KNOWN_SHAPE,
    reason: str = "",
    declared_kind: str = KIND_ROOMS,
    auxiliary: bool = False,
    auxiliary_kind: str = "",
    fields: tuple[tuple[str, str, str], ...] = (),
) -> FileEntry:
    observations = tuple(
        FieldObservation(
            name=name,
            structural_type=stype,
            role=role,
            consumer_evidence=("test_module.test_func",),
            notes="",
        )
        for name, stype, role in fields
    )
    return FileEntry(
        file_id=file_id,
        file_stem=file_stem,
        content_digest="deadbeef12345678",
        top_level_shape=SHAPE_OBJECT,
        declared_kind=declared_kind,
        status=status,
        reason=reason,
        auxiliary=auxiliary,
        auxiliary_kind=auxiliary_kind,
        entry_count=1,
        entry_shapes=(
            EntryShape(
                path="items[0]",
                top_level_keys=tuple(f[0] for f in fields),
                field_types={f[0]: f[1] for f in fields},
            ),
        ),
        field_observations=observations,
        structural_notes=(),
    )


# -- 1. Usable scenes with full mappings -------------------------------------


def test_usable_rooms_resource_with_all_mapped_fields():
    """A known_shape rooms library where all fields have mappings becomes usable."""
    entry = _make_dummy_file_entry(
        "test_rooms_01",
        "general_scenes",
        declared_kind=KIND_ROOMS,
        fields=(
            ("id", TYPE_STRING, ROLE_IDENTIFIER),
            ("label", TYPE_STRING, ROLE_DESCRIPTIVE_INPUT),
            ("scene_theme", TYPE_STRING, ROLE_DESCRIPTIVE_INPUT),
            ("weight", TYPE_NUMBER, ROLE_SELECTION_METADATA),
            ("lighting_hint", TYPE_STRING, ROLE_UNUSED),
        ),
    )
    ledger = CoverageLedger(entries=(entry,), source_dir="test_sources", generated_at="")
    report = build_coverage_report(ledger)

    assert report.total_files == 1
    assert report.usable_files == 1
    assert report.auxiliary_files == 0
    assert report.pending_files == 0
    assert report.coverage_complete is True
    assert report.adoption_complete is True

    rep = report.entries[0]
    assert rep.coverage_disposition == DISPOSITION_USABLE
    assert rep.disposition_reason == ""
    assert rep.retention == RETENTION_USABLE_SCENE
    assert rep.compiled_behavior == ""
    assert len(rep.unmapped_fields) == 0

    field_map = {f.name: f for f in rep.field_observations}
    assert field_map["id"].mapping_role == ROLE_IDENTITY
    assert field_map["id"].supported is True
    assert field_map["label"].mapping_role == "descriptive_input"
    assert field_map["scene_theme"].mapping_role == "descriptive_input"
    assert field_map["weight"].mapping_role == "selection_metadata"
    assert field_map["lighting_hint"].mapping_role == ROLE_INTENTIONALLY_UNUSED
    assert field_map["lighting_hint"].supported is True
    assert field_map["lighting_hint"].reason != ""


# -- 2. Unmapped field blocks adoption and keeps resource pending -----------


def test_known_shape_with_unmapped_field_becomes_pending():
    """An unmapped field prevents usable disposition and adoption completion."""
    entry = _make_dummy_file_entry(
        "test_rooms_02",
        "workplace_scenes",
        declared_kind=KIND_ROOMS,
        fields=(
            ("id", TYPE_STRING, ROLE_IDENTIFIER),
            ("label", TYPE_STRING, ROLE_DESCRIPTIVE_INPUT),
            ("invented_unknown_field", TYPE_STRING, ROLE_UNUSED),
        ),
    )
    ledger = CoverageLedger(entries=(entry,), source_dir="test_sources", generated_at="")
    report = build_coverage_report(ledger)

    assert report.total_files == 1
    assert report.usable_files == 0
    assert report.pending_files == 1
    assert report.coverage_complete is True
    assert report.adoption_complete is False
    assert report.unmapped_field_observations == 1

    rep = report.entries[0]
    assert rep.coverage_disposition == DISPOSITION_PENDING
    assert "invented_unknown_field" in rep.disposition_reason
    assert "invented_unknown_field" in rep.unmapped_fields

    unmapped_field = [f for f in rep.field_observations if f.name == "invented_unknown_field"][0]
    assert unmapped_field.mapping_role == "unmapped"
    assert unmapped_field.supported is False
    assert unmapped_field.reason != ""


# -- 3. Intentionally unused fields count as covered -------------------------


def test_intentionally_unused_fields_with_reasons_are_supported():
    """Fields marked intentionally_unused with non-empty reasons count as supported."""
    entry = _make_dummy_file_entry(
        "test_sm_01",
        "sm_scenes",
        declared_kind=KIND_ROOMS,
        fields=(
            ("id", TYPE_STRING, ROLE_IDENTIFIER),
            ("label", TYPE_STRING, ROLE_DESCRIPTIVE_INPUT),
            ("scene_theme", TYPE_STRING, ROLE_DESCRIPTIVE_INPUT),
            ("play_axis", TYPE_STRING, ROLE_UNUSED),
            ("privacy_level", TYPE_STRING, ROLE_UNUSED),
        ),
    )
    ledger = CoverageLedger(entries=(entry,), source_dir="test_sources", generated_at="")
    report = build_coverage_report(ledger)

    rep = report.entries[0]
    assert rep.coverage_disposition == DISPOSITION_USABLE
    assert report.adoption_complete is True

    f_play = [f for f in rep.field_observations if f.name == "play_axis"][0]
    assert f_play.mapping_role == ROLE_INTENTIONALLY_UNUSED
    assert f_play.supported is True
    assert f_play.reason != ""


# -- 4. Writer guidance naming rules (*_anchor, mood_*) ----------------------


def test_writer_guidance_name_rules_are_recognized():
    """Fields matching *_anchor or mood_* rules are mapped as writer_guidance."""
    entry = _make_dummy_file_entry(
        "test_guidance_01",
        "general_scenes",
        declared_kind=KIND_ROOMS,
        fields=(
            ("id", TYPE_STRING, ROLE_IDENTIFIER),
            ("label", TYPE_STRING, ROLE_DESCRIPTIVE_INPUT),
            ("scene_theme", TYPE_STRING, ROLE_DESCRIPTIVE_INPUT),
            ("camera_anchor", TYPE_STRING, ROLE_WRITER_GUIDANCE),
            ("mood_relaxed", TYPE_STRING, ROLE_WRITER_GUIDANCE),
        ),
    )
    ledger = CoverageLedger(entries=(entry,), source_dir="test_sources", generated_at="")
    report = build_coverage_report(ledger)

    rep = report.entries[0]
    assert rep.coverage_disposition == DISPOSITION_USABLE
    assert len(rep.unmapped_fields) == 0

    field_map = {f.name: f for f in rep.field_observations}
    assert field_map["camera_anchor"].mapping_role == "writer_guidance"
    assert field_map["camera_anchor"].supported is True
    assert field_map["mood_relaxed"].mapping_role == "writer_guidance"
    assert field_map["mood_relaxed"].supported is True


# -- 5. Fused scenes compiled behavior unverified ----------------------------


def test_fused_scenes_compiled_behavior_is_unverified():
    """Fused scenes entries are marked as unverified compiled behavior with no parity claims."""
    entry = _make_dummy_file_entry(
        "test_fused_01",
        "perspective_scenes",
        declared_kind=KIND_FUSED_SCENES,
        fields=(
            ("id", TYPE_STRING, ROLE_IDENTIFIER),
            ("prompt", TYPE_STRING, ROLE_DESCRIPTIVE_INPUT),
            ("weight", TYPE_NUMBER, ROLE_SELECTION_METADATA),
        ),
    )
    ledger = CoverageLedger(entries=(entry,), source_dir="test_sources", generated_at="")
    report = build_coverage_report(ledger)

    rep = report.entries[0]
    assert rep.coverage_disposition == DISPOSITION_USABLE
    assert rep.compiled_behavior == COMPILED_BEHAVIOR_UNVERIFIED

    # Check that prompt is descriptive_input
    f_prompt = [f for f in rep.field_observations if f.name == "prompt"][0]
    assert f_prompt.mapping_role == "descriptive_input"
    assert f_prompt.supported is True


# -- 6. Auxiliary resources remain separate from scenes ----------------------


def test_auxiliary_record_and_scalar_resources():
    """Auxiliary files are classified with auxiliary disposition and pipeline retention."""
    trans_entry = _make_dummy_file_entry(
        "test_trans_01",
        "translation_map",
        status=STATUS_AUXILIARY,
        declared_kind="",
        auxiliary=True,
        auxiliary_kind=KIND_TRANSLATION_MAP,
        fields=(
            ("source", TYPE_STRING, ROLE_UNUSED),
            ("translation", TYPE_STRING, ROLE_UNUSED),
            ("fields", TYPE_STRING, ROLE_UNUSED),
        ),
    )
    families_entry = _make_dummy_file_entry(
        "test_families_01",
        "mined_families",
        status=STATUS_AUXILIARY,
        declared_kind="",
        auxiliary=True,
        auxiliary_kind=KIND_MINED_FAMILIES,
        fields=(
            ("value", TYPE_STRING, ROLE_UNUSED),
        ),
    )
    ledger = CoverageLedger(
        entries=(trans_entry, families_entry),
        source_dir="test_sources",
        generated_at="",
    )
    report = build_coverage_report(ledger)

    assert report.total_files == 2
    assert report.usable_files == 0
    assert report.auxiliary_files == 2
    assert report.pending_files == 0
    assert report.coverage_complete is True
    assert report.adoption_complete is True

    rep_trans = report.entries[0]
    assert rep_trans.coverage_disposition == DISPOSITION_AUXILIARY
    assert rep_trans.retention == RETENTION_AUXILIARY_PIPELINE
    for f in rep_trans.field_observations:
        assert f.mapping_role == ROLE_AUXILIARY_DATA
        assert f.supported is True

    rep_fam = report.entries[1]
    assert rep_fam.coverage_disposition == DISPOSITION_AUXILIARY
    assert rep_fam.retention == RETENTION_AUXILIARY_PIPELINE
    for f in rep_fam.field_observations:
        assert f.mapping_role == ROLE_AUXILIARY_DATA
        assert f.supported is True


# -- 7. Not adopted files remain pending with reason -------------------------


def test_not_adopted_resource_remains_pending():
    """Files with status not_adopted remain pending, retain schema, and keep reasons."""
    entry = _make_dummy_file_entry(
        "test_amateurs_01",
        "amateurs",
        status=STATUS_NOT_ADOPTED,
        reason="not_adopted",
        declared_kind="body_profiles",
        fields=(
            ("body_shape", TYPE_STRING, ROLE_UNUSED),
            ("description", TYPE_STRING, ROLE_DESCRIPTIVE_INPUT),
        ),
    )
    ledger = CoverageLedger(entries=(entry,), source_dir="test_sources", generated_at="")
    report = build_coverage_report(ledger)

    assert report.total_files == 1
    assert report.usable_files == 0
    assert report.pending_files == 1
    assert report.coverage_complete is True
    assert report.adoption_complete is False

    rep = report.entries[0]
    assert rep.coverage_disposition == DISPOSITION_PENDING
    assert rep.disposition_reason == "not_adopted"
    assert rep.retention == RETENTION_NOT_ADOPTED


# -- 8. Malformed, undeclared, unknown_shape, pending_mapping ----------------


@pytest.mark.parametrize(
    "status,reason,declared_kind",
    [
        (STATUS_MALFORMED, "corrupt JSON syntax", "rooms"),
        (STATUS_UNDECLARED, "undeclared source library", ""),
        (STATUS_UNKNOWN_SHAPE, "unexpected root container", "rooms"),
        (STATUS_PENDING_MAPPING, "mapping not completed", "rooms"),
        (STATUS_REFUSED, "refused by guard", "rooms"),
    ],
)
def test_various_pending_statuses(status, reason, declared_kind):
    """Any non-OK status is reported as pending with its explicit reason."""
    entry = _make_dummy_file_entry(
        f"test_{status}_01",
        f"stem_{status}",
        status=status,
        reason=reason,
        declared_kind=declared_kind,
        fields=(("id", TYPE_STRING, ROLE_IDENTIFIER),),
    )
    ledger = CoverageLedger(entries=(entry,), source_dir="test_sources", generated_at="")
    report = build_coverage_report(ledger)

    assert report.total_files == 1
    assert report.usable_files == 0
    assert report.pending_files == 1
    assert report.coverage_complete is True
    assert report.adoption_complete is False

    rep = report.entries[0]
    assert rep.coverage_disposition == DISPOSITION_PENDING
    assert rep.disposition_reason == reason


# -- 9. Empty ledger handling (conservative) ---------------------------------


def test_empty_ledger_is_conservatively_incomplete():
    """An empty ledger cannot claim complete coverage or complete adoption."""
    ledger = CoverageLedger(entries=(), source_dir="empty_sources", generated_at="")
    report = build_coverage_report(ledger)

    assert report.total_files == 0
    assert report.coverage_complete is False
    assert report.adoption_complete is False


# -- 10. Exact count reconciliation ------------------------------------------


def test_exact_count_and_field_reconciliation():
    """Every file and field observation reconciles 1:1 with the input ledger."""
    entry1 = _make_dummy_file_entry(
        "file_01",
        "general_scenes",
        declared_kind=KIND_ROOMS,
        fields=(
            ("id", TYPE_STRING, ROLE_IDENTIFIER),
            ("label", TYPE_STRING, ROLE_DESCRIPTIVE_INPUT),
        ),
    )
    entry2 = _make_dummy_file_entry(
        "file_02",
        "celebrities",
        status=STATUS_NOT_ADOPTED,
        reason="not_adopted",
        declared_kind="identities",
        fields=(
            ("value", TYPE_STRING, ROLE_UNUSED),
        ),
    )
    ledger = CoverageLedger(entries=(entry1, entry2), source_dir="mixed", generated_at="")
    report = build_coverage_report(ledger)

    assert report.total_files == 2
    assert len(report.entries) == 2
    assert report.total_field_observations == 3
    assert report.mapped_field_observations + report.unmapped_field_observations == 3
    assert {e.file_id for e in report.entries} == {"file_01", "file_02"}


# -- 11. Serialization and privacy -------------------------------------------


def test_save_report_and_privacy_checks(tmp_path):
    """The saved JSON report carries no personal data, no CJK glyphs, and no machine paths."""
    entry1 = _make_dummy_file_entry(
        "file_01",
        "general_scenes",
        declared_kind=KIND_ROOMS,
        fields=(
            ("id", TYPE_STRING, ROLE_IDENTIFIER),
            ("label", TYPE_STRING, ROLE_DESCRIPTIVE_INPUT),
        ),
    )
    ledger = CoverageLedger(entries=(entry1,), source_dir="fixtures", generated_at="2026-09-09T00:00:00Z")
    report = build_coverage_report(ledger)

    out_file = tmp_path / "coverage-report.json"
    saved_path = save_coverage_report(report, out_file)
    assert saved_path.exists()

    raw_text = saved_path.read_text(encoding="utf-8")
    loaded = json.loads(raw_text)

    assert loaded["source_dir"] == "fixtures"
    assert loaded["summary"]["total_files"] == 1
    assert loaded["summary"]["usable_files"] == 1

    # Privacy assertions
    win_user_re = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!<)[A-Za-z0-9._-]+", re.I)
    unix_home_re = re.compile(r"/(?:home|Users)/(?!<)[A-Za-z0-9._-]+")
    email_re = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    token_re = re.compile(r"\b(?:sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,})\b")
    cjk_re = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

    assert not win_user_re.search(raw_text)
    assert not unix_home_re.search(raw_text)
    assert not email_re.search(raw_text)
    assert not token_re.search(raw_text)
    assert not cjk_re.search(raw_text)


# -- 12. CLI execution tests and aux-path parsing ----------------------------

sys.path.insert(0, str(ROOT / "scripts"))
from report_resource_coverage import _parse_aux_path


def test_parse_aux_path_windows_and_posix_formats():
    """_parse_aux_path correctly handles Windows drive letters, explicit kinds, and separators."""
    # Windows absolute path without kind: drive colon must not be treated as kind separator
    path, kind = _parse_aux_path(r"C:\fixtures\auxiliary.json")
    assert path == Path(r"C:\fixtures\auxiliary.json")
    assert kind == ""

    # Windows absolute path with explicit :KIND
    path, kind = _parse_aux_path(r"C:\fixtures\auxiliary.json:translation_map")
    assert path == Path(r"C:\fixtures\auxiliary.json")
    assert kind == "translation_map"

    # Windows absolute path with =KIND separator
    path, kind = _parse_aux_path(r"C:\fixtures\auxiliary.json=cut_map")
    assert path == Path(r"C:\fixtures\auxiliary.json")
    assert kind == "cut_map"

    # POSIX absolute path with :KIND
    path, kind = _parse_aux_path("/fixtures/auxiliary.json:mined_families")
    assert path == Path("/fixtures/auxiliary.json")
    assert kind == "mined_families"

    # POSIX absolute path without kind
    path, kind = _parse_aux_path("/fixtures/auxiliary.json")
    assert path == Path("/fixtures/auxiliary.json")
    assert kind == ""

    # Relative path with :KIND
    path, kind = _parse_aux_path("relative/cuts.json:cut_map")
    assert path == Path("relative/cuts.json")
    assert kind == "cut_map"

    # Preserves unrecognized kinds so the downstream ledger can report them
    path, kind = _parse_aux_path(r"C:\fixtures\auxiliary.json:unrecognized_custom_kind")
    assert path == Path(r"C:\fixtures\auxiliary.json")
    assert kind == "unrecognized_custom_kind"


def test_cli_report_generation(tmp_path):
    """The report_resource_coverage.py script runs from CLI and produces valid output."""
    src_dir = tmp_path / "invented_src"
    src_dir.mkdir()

    rooms_file = src_dir / "general_scenes.json"
    rooms_file.write_text(
        json.dumps({
            "library": "general_scenes",
            "items": [
                {
                    "identifier": "inv_01",
                    "label": "Sunny garden",
                    "scene_theme": "Peaceful garden outside",
                    "weight": 1.0,
                }
            ],
        }),
        encoding="utf-8",
    )

    out_file = tmp_path / "cli_output.json"
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "report_resource_coverage.py"),
        str(src_dir),
        "--output",
        str(out_file),
    ]

    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    assert res.returncode == 0, f"CLI failed: {res.stderr}"
    assert out_file.exists()

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["summary"]["total_files"] == 1
    assert data["summary"]["usable_files"] == 1
    assert data["summary"]["coverage_complete"] is True
    assert data["summary"]["adoption_complete"] is True


def test_cli_report_generation_with_untyped_aux_path(tmp_path):
    """An absolute Windows auxiliary path without a kind reaches inventory without truncation."""
    src_dir = tmp_path / "invented_src_with_aux"
    src_dir.mkdir()

    rooms_file = src_dir / "general_scenes.json"
    rooms_file.write_text(
        json.dumps({
            "library": "general_scenes",
            "items": [
                {
                    "identifier": "inv_01",
                    "label": "Sunny garden",
                    "scene_theme": "Peaceful garden outside",
                    "weight": 1.0,
                }
            ],
        }),
        encoding="utf-8",
    )

    aux_dir = tmp_path / "external_aux"
    aux_dir.mkdir()
    aux_file = aux_dir / "translation_map.json"
    aux_file.write_text(
        json.dumps({
            "invented source string": {
                "source": "invented source string",
                "translation": "invented english translation",
                "fields": ["label", "scene_theme"],
            }
        }),
        encoding="utf-8",
    )

    out_file = tmp_path / "cli_aux_output.json"
    # Pass untyped absolute path (on Windows this has drive letter C:\...)
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "report_resource_coverage.py"),
        str(src_dir),
        "--aux-path",
        str(aux_file),
        "--output",
        str(out_file),
    ]

    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    assert res.returncode == 0, f"CLI with aux-path failed: {res.stderr}"
    assert out_file.exists()

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["summary"]["total_files"] == 2
    assert data["summary"]["usable_files"] == 1
    assert data["summary"]["auxiliary_files"] == 1
    assert data["summary"]["coverage_complete"] is True
    assert data["summary"]["adoption_complete"] is True

    aux_entry = [e for e in data["entries"] if e["file_stem"] == "translation_map"][0]
    assert aux_entry["coverage_disposition"] == "auxiliary"
    assert aux_entry["auxiliary_kind"] == "translation_map"
    assert not aux_entry["file_id"].startswith("C-") and not aux_entry["file_id"] == "C"
