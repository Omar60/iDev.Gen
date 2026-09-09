"""Coverage and adoption reporting for operator-selected source libraries.

Cross-references structural evidence observed by `backend.resource_ledger`
against the supported preparation mappings declared in `backend.resource_prompts`.

Key distinctions:
  * `backend.resource_ledger`: records what the source corpus OBSERVES.
  * `backend.resource_prompts`: declares what iDev.Gen SUPPORTS.
  * `backend.resource_coverage`: reports how much of the observed corpus
    is covered, usable, auxiliary, or pending adoption.

Every discovered file and field observation in the ledger is accounted for
without copying source prose or values into the report.

Dispositions:
  * usable: scene resource whose shape is accepted, kind is supported for
    preparation, and all observed fields have a declared mapping role.
  * auxiliary: recognized auxiliary pipeline resource (translation, cuts,
    families, judge labels); separated from scene prompt preparation.
  * pending: unsupported shape, undeclared, not_adopted, malformed,
    pending_mapping, refused, or any accepted shape carrying unmapped fields.

Coverage complete vs adoption complete:
  * `coverage_complete`: true when every discovered file and field observation
    is accounted for with explicit reasons for any unsupported elements.
  * `adoption_complete`: true ONLY when coverage is complete AND zero files or
    fields remain pending adoption or unmapped.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable

from backend.resource_ledger import (
    ALL_STATUSES,
    CoverageLedger,
    FieldObservation,
    FileEntry,
    STATUS_AUXILIARY,
    STATUS_KNOWN_SHAPE,
    STATUS_MALFORMED,
    STATUS_NOT_ADOPTED,
    STATUS_PENDING_MAPPING,
    STATUS_REFUSED,
    STATUS_UNDECLARED,
    STATUS_UNKNOWN_SHAPE,
)
from backend.resource_prompts import (
    ALL_PREPARATION_KINDS,
    ALL_PREPARATION_ROLES,
    FUSED_SCENES_COMPILED_BEHAVIOR,
    KIND_CUT_MAP,
    KIND_FUSED_SCENES,
    KIND_MINED_FAMILIES,
    KIND_MINED_LABELS,
    KIND_ROOMS,
    KIND_TRANSLATION_MAP,
    PREPARATION_FIELD_MAPPING,
    ROLE_AUXILIARY_DATA,
    ROLE_INTENTIONALLY_UNUSED,
    classify_field,
    is_auxiliary_kind,
    is_scalar_auxiliary_kind,
)


# -- Disposition constants ---------------------------------------------------

DISPOSITION_USABLE: str = "usable"
DISPOSITION_AUXILIARY: str = "auxiliary"
DISPOSITION_PENDING: str = "pending"

ALL_DISPOSITIONS: tuple[str, ...] = (
    DISPOSITION_USABLE,
    DISPOSITION_AUXILIARY,
    DISPOSITION_PENDING,
)


# -- Retention capability constants ------------------------------------------

RETENTION_USABLE_SCENE: str = "usable_scene_resource"
RETENTION_AUXILIARY_PIPELINE: str = "auxiliary_pipeline_data"
RETENTION_NOT_ADOPTED: str = "not_adopted"
RETENTION_UNSUPPORTED_SHAPE: str = "unsupported_shape"
RETENTION_UNDECLARED: str = "undeclared"
RETENTION_RETAINED_STRUCTURAL: str = "retained_structural"


# -- Compiled behavior notes -------------------------------------------------

COMPILED_BEHAVIOR_UNVERIFIED: str = "unverified"


# -- Data classes ------------------------------------------------------------


@dataclass(frozen=True)
class CoverageFieldResult:
    """Classification of one observed field under the preparation contract.

    `name` is the field name. `structural_type` is the JSON type observed by the
    ledger. `provisional_role` is the role assigned by the ledger inventory.
    `mapping_role` is the authoritative role from `resource_prompts.classify_field`.
    `supported` is True when `mapping_role != 'unmapped'`. `required` indicates
    whether preparation requires this field. `reason` is non-empty for unmapped
    or intentionally_unused fields. `consumer_evidence` is preserved from the ledger.
    """

    name: str
    structural_type: str
    provisional_role: str
    mapping_role: str
    supported: bool
    required: bool
    reason: str
    notes: str
    consumer_evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "structural_type": self.structural_type,
            "provisional_role": self.provisional_role,
            "mapping_role": self.mapping_role,
            "supported": self.supported,
            "required": self.required,
            "reason": self.reason,
            "notes": self.notes,
            "consumer_evidence": list(self.consumer_evidence),
        }


@dataclass(frozen=True)
class CoverageFileReport:
    """Coverage and adoption report for one discovered file.

    Preserves safe identifiers (`file_id`, `file_stem`, `content_digest`),
    structural shape, inventory status/reason, declared/auxiliary kinds,
    assigned `coverage_disposition` (`usable`, `auxiliary`, `pending`),
    retention capability, compiled behavior note, and all classified fields.
    No source prose or values are retained.
    """

    file_id: str
    file_stem: str
    content_digest: str
    inventory_status: str
    inventory_reason: str
    declared_kind: str
    auxiliary: bool
    auxiliary_kind: str
    top_level_shape: str
    entry_count: int
    coverage_disposition: str
    disposition_reason: str
    retention: str
    compiled_behavior: str
    field_observations: tuple[CoverageFieldResult, ...]
    unmapped_fields: tuple[str, ...]
    structural_notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "file_stem": self.file_stem,
            "content_digest": self.content_digest,
            "inventory_status": self.inventory_status,
            "inventory_reason": self.inventory_reason,
            "declared_kind": self.declared_kind,
            "auxiliary": self.auxiliary,
            "auxiliary_kind": self.auxiliary_kind,
            "top_level_shape": self.top_level_shape,
            "entry_count": self.entry_count,
            "coverage_disposition": self.coverage_disposition,
            "disposition_reason": self.disposition_reason,
            "retention": self.retention,
            "compiled_behavior": self.compiled_behavior,
            "field_observations": [f.to_dict() for f in self.field_observations],
            "unmapped_fields": list(self.unmapped_fields),
            "structural_notes": list(self.structural_notes),
        }


@dataclass(frozen=True)
class ResourceCoverageReport:
    """Comprehensive coverage and adoption report across all discovered files.

    Distinguishes complete coverage from complete adoption.
    """

    source_dir: str
    generated_at: str
    total_files: int
    usable_files: int
    auxiliary_files: int
    pending_files: int
    total_field_observations: int
    mapped_field_observations: int
    unmapped_field_observations: int
    intentionally_unused_field_observations: int
    coverage_complete: bool
    adoption_complete: bool
    inventory_totals: dict[str, int]
    entries: tuple[CoverageFileReport, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_dir": self.source_dir,
            "generated_at": self.generated_at,
            "summary": {
                "total_files": self.total_files,
                "usable_files": self.usable_files,
                "auxiliary_files": self.auxiliary_files,
                "pending_files": self.pending_files,
                "total_field_observations": self.total_field_observations,
                "mapped_field_observations": self.mapped_field_observations,
                "unmapped_field_observations": self.unmapped_field_observations,
                "intentionally_unused_field_observations": (
                    self.intentionally_unused_field_observations
                ),
                "coverage_complete": self.coverage_complete,
                "adoption_complete": self.adoption_complete,
            },
            "inventory_totals": dict(self.inventory_totals),
            "entries": [e.to_dict() for e in self.entries],
        }


# -- Core evaluation logic ---------------------------------------------------


def _classify_field_observation(
    obs: FieldObservation,
    *,
    kind: str,
    is_aux: bool,
) -> CoverageFieldResult:
    """Classify an observed field using the authoritative preparation contract."""
    if is_aux:
        if is_scalar_auxiliary_kind(kind):
            # Scalar auxiliary kinds have scalar string values, not prompt inputs.
            return CoverageFieldResult(
                name=obs.name,
                structural_type=obs.structural_type,
                provisional_role=obs.role,
                mapping_role=ROLE_AUXILIARY_DATA,
                supported=True,
                required=False,
                reason="",
                notes=obs.notes or "scalar auxiliary pipeline data",
                consumer_evidence=obs.consumer_evidence,
            )
        # Record auxiliary kinds (translation_map, cut_map)
        if kind in (KIND_TRANSLATION_MAP, KIND_CUT_MAP):
            res = classify_field(kind, obs.name)
            mapping_role = str(res.get("role", "unmapped"))
            supported = mapping_role != "unmapped"
            return CoverageFieldResult(
                name=obs.name,
                structural_type=obs.structural_type,
                provisional_role=obs.role,
                mapping_role=mapping_role,
                supported=supported,
                required=bool(res.get("required", False)),
                reason=str(res.get("reason", "")),
                notes=str(res.get("notes", obs.notes)),
                consumer_evidence=obs.consumer_evidence,
            )
        # Unrecognized auxiliary kind
        return CoverageFieldResult(
            name=obs.name,
            structural_type=obs.structural_type,
            provisional_role=obs.role,
            mapping_role="unmapped",
            supported=False,
            required=False,
            reason=f"unsupported auxiliary kind {kind!r}",
            notes=obs.notes,
            consumer_evidence=obs.consumer_evidence,
        )

    # Non-auxiliary entry
    if kind in (KIND_ROOMS, KIND_FUSED_SCENES):
        res = classify_field(kind, obs.name)
        mapping_role = str(res.get("role", "unmapped"))
        supported = mapping_role != "unmapped"
        return CoverageFieldResult(
            name=obs.name,
            structural_type=obs.structural_type,
            provisional_role=obs.role,
            mapping_role=mapping_role,
            supported=supported,
            required=bool(res.get("required", False)),
            reason=str(res.get("reason", "")),
            notes=str(res.get("notes", obs.notes)),
            consumer_evidence=obs.consumer_evidence,
        )

    # Undeclared or unadopted kind (e.g. body_profiles, identities, or empty)
    reason = (
        f"kind {kind!r} is not a supported preparation kind"
        if kind
        else "file has no declared preparation kind"
    )
    return CoverageFieldResult(
        name=obs.name,
        structural_type=obs.structural_type,
        provisional_role=obs.role,
        mapping_role="unmapped",
        supported=False,
        required=False,
        reason=reason,
        notes=obs.notes,
        consumer_evidence=obs.consumer_evidence,
    )


def _evaluate_file_entry(entry: FileEntry) -> CoverageFileReport:
    """Evaluate coverage disposition and field mapping for one FileEntry."""
    is_aux = bool(entry.auxiliary)
    effective_kind = entry.auxiliary_kind if is_aux else entry.declared_kind

    classified_fields: list[CoverageFieldResult] = []
    unmapped_field_names: list[str] = []

    for obs in entry.field_observations:
        c = _classify_field_observation(obs, kind=effective_kind, is_aux=is_aux)
        classified_fields.append(c)
        if not c.supported:
            unmapped_field_names.append(c.name)

    # Determine compiled behavior note
    if entry.declared_kind == KIND_FUSED_SCENES:
        compiled_behavior = COMPILED_BEHAVIOR_UNVERIFIED
    else:
        compiled_behavior = ""

    # Determine coverage disposition, reason, and retention capability
    coverage_disposition: str
    disposition_reason: str
    retention: str

    if entry.status not in (STATUS_KNOWN_SHAPE, STATUS_AUXILIARY):
        # Non-OK inventory status -> always pending
        coverage_disposition = DISPOSITION_PENDING
        disposition_reason = entry.reason or f"inventory status is {entry.status}"
        if entry.status == STATUS_NOT_ADOPTED:
            retention = RETENTION_NOT_ADOPTED
        elif entry.status in (STATUS_MALFORMED, STATUS_UNKNOWN_SHAPE):
            retention = RETENTION_UNSUPPORTED_SHAPE
        elif entry.status == STATUS_UNDECLARED:
            retention = RETENTION_UNDECLARED
        else:
            retention = RETENTION_RETAINED_STRUCTURAL

    elif is_aux:
        if entry.auxiliary_kind in (
            KIND_TRANSLATION_MAP,
            KIND_CUT_MAP,
            KIND_MINED_FAMILIES,
            KIND_MINED_LABELS,
        ):
            if unmapped_field_names:
                coverage_disposition = DISPOSITION_PENDING
                disposition_reason = (
                    f"auxiliary file has unmapped fields: "
                    f"{', '.join(unmapped_field_names)}"
                )
                retention = RETENTION_RETAINED_STRUCTURAL
            else:
                coverage_disposition = DISPOSITION_AUXILIARY
                disposition_reason = ""
                retention = RETENTION_AUXILIARY_PIPELINE
        else:
            coverage_disposition = DISPOSITION_PENDING
            disposition_reason = f"unsupported auxiliary kind {entry.auxiliary_kind!r}"
            retention = RETENTION_RETAINED_STRUCTURAL

    else:
        # Scene resource candidate with STATUS_KNOWN_SHAPE
        if entry.declared_kind not in (KIND_ROOMS, KIND_FUSED_SCENES):
            coverage_disposition = DISPOSITION_PENDING
            disposition_reason = (
                f"kind {entry.declared_kind!r} is not a supported scene kind for preparation"
            )
            retention = RETENTION_RETAINED_STRUCTURAL
        elif unmapped_field_names:
            coverage_disposition = DISPOSITION_PENDING
            disposition_reason = (
                f"resource has unmapped fields: {', '.join(unmapped_field_names)}"
            )
            retention = RETENTION_RETAINED_STRUCTURAL
        else:
            coverage_disposition = DISPOSITION_USABLE
            disposition_reason = ""
            retention = RETENTION_USABLE_SCENE

    return CoverageFileReport(
        file_id=entry.file_id,
        file_stem=entry.file_stem,
        content_digest=entry.content_digest,
        inventory_status=entry.status,
        inventory_reason=entry.reason,
        declared_kind=entry.declared_kind,
        auxiliary=entry.auxiliary,
        auxiliary_kind=entry.auxiliary_kind,
        top_level_shape=entry.top_level_shape,
        entry_count=entry.entry_count,
        coverage_disposition=coverage_disposition,
        disposition_reason=disposition_reason,
        retention=retention,
        compiled_behavior=compiled_behavior,
        field_observations=tuple(classified_fields),
        unmapped_fields=tuple(unmapped_field_names),
        structural_notes=entry.structural_notes,
    )


def build_coverage_report(
    ledger: CoverageLedger,
    *,
    generated_at: str | None = None,
) -> ResourceCoverageReport:
    """Build a ResourceCoverageReport from a CoverageLedger.

    Reconciles all discovered files and field observations against the
    preparation mapping contract without duplicating rules or dropping entries.
    """
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    file_reports = [_evaluate_file_entry(e) for e in ledger.entries]

    usable_count = sum(
        1 for r in file_reports if r.coverage_disposition == DISPOSITION_USABLE
    )
    auxiliary_count = sum(
        1 for r in file_reports if r.coverage_disposition == DISPOSITION_AUXILIARY
    )
    pending_count = sum(
        1 for r in file_reports if r.coverage_disposition == DISPOSITION_PENDING
    )

    total_fields = sum(len(r.field_observations) for r in file_reports)
    mapped_fields = sum(
        sum(1 for f in r.field_observations if f.supported)
        for r in file_reports
    )
    unmapped_fields = sum(
        sum(1 for f in r.field_observations if not f.supported)
        for r in file_reports
    )
    intentionally_unused_fields = sum(
        sum(1 for f in r.field_observations if f.mapping_role == ROLE_INTENTIONALLY_UNUSED)
        for r in file_reports
    )

    # Verify conservative coverage completeness:
    # 1. An empty ledger cannot claim complete coverage or adoption.
    # 2. Every discovered file appears exactly once.
    # 3. Every field observation in the ledger is accounted for.
    # 4. Every pending disposition carries an explicit reason.
    if not ledger.entries:
        coverage_complete = False
        adoption_complete = False
    else:
        files_accounted = len(file_reports) == len(ledger.entries)
        fields_accounted = total_fields == sum(
            len(e.field_observations) for e in ledger.entries
        )
        reasons_complete = all(
            bool(r.disposition_reason.strip())
            for r in file_reports
            if r.coverage_disposition == DISPOSITION_PENDING
        )
        coverage_complete = (
            files_accounted and fields_accounted and reasons_complete
        )
        adoption_complete = (
            coverage_complete
            and pending_count == 0
            and unmapped_fields == 0
        )

    return ResourceCoverageReport(
        source_dir=ledger.source_dir,
        generated_at=timestamp,
        total_files=len(file_reports),
        usable_files=usable_count,
        auxiliary_files=auxiliary_count,
        pending_files=pending_count,
        total_field_observations=total_fields,
        mapped_field_observations=mapped_fields,
        unmapped_field_observations=unmapped_fields,
        intentionally_unused_field_observations=intentionally_unused_fields,
        coverage_complete=coverage_complete,
        adoption_complete=adoption_complete,
        inventory_totals=ledger.by_status(),
        entries=tuple(file_reports),
    )


def save_coverage_report(
    report: ResourceCoverageReport,
    path: Path | str,
) -> Path:
    """Save the coverage report to a JSON file at `path`.

    Ensures parent directories exist. Formats with indent=2 and ensure_ascii=True.
    Returns the resolved output Path.
    """
    target = Path(path)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report.to_dict(), ensure_ascii=True, indent=2) + "\n"
    target.write_text(serialized, encoding="utf-8")
    return target


__all__ = [
    "ALL_DISPOSITIONS",
    "COMPILED_BEHAVIOR_UNVERIFIED",
    "CoverageFieldResult",
    "CoverageFileReport",
    "DISPOSITION_AUXILIARY",
    "DISPOSITION_PENDING",
    "DISPOSITION_USABLE",
    "RETENTION_AUXILIARY_PIPELINE",
    "RETENTION_NOT_ADOPTED",
    "RETENTION_RETAINED_STRUCTURAL",
    "RETENTION_UNDECLARED",
    "RETENTION_UNSUPPORTED_SHAPE",
    "RETENTION_USABLE_SCENE",
    "ResourceCoverageReport",
    "build_coverage_report",
    "save_coverage_report",
]
