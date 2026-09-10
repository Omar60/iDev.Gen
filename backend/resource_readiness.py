"""Translation-pending readiness evaluation for accepted resource
revisions (task 2.4 of `adopt-resource-session-planning`).

This module is the small, dedicated surface that decides whether a
revision that has been written to the local store is *ready* for
prompt preparation, and where the verdict lives. The verdict is
derived entirely from the existing `payload` and `translation`
columns the persistence layer already exposes; no new columns, no
new tables, no rewriting of the original accepted object.

Three rules are pinned here and nowhere else:

  1. The original payload is never translated, rewritten, removed
     or filtered. The acceptance step (task 2.1) stored the complete
     accepted object as the `payload` column with the exact strings
     the source wrote, and the readiness layer reads that column
     without ever mutating it. The verdict the layer reaches lives
     in the separate `coverage` column the schema already declares.

  2. Readiness is a property of the translation and the required-
     field contract, not of the original payload. A revision whose
     original payload carries every required field is still
     pending until each required field has a non-empty English
     translation recorded in the `translation` column. The verdict
     is a separate, derived value; the original payload is not the
     verdict, and a future task that fills in the translation
     column changes the verdict without ever touching the payload.

  3. Optional and unmapped fields are inspectable but never block
     readiness. A field the `resource_prompts` contract declares
     as `intentionally_unused` is reported as retained-but-unused
     in the coverage record and is not a blocker. A field whose
     name the contract does not name at all is reported as
     unmapped and is not a blocker. The required-fields list is
     the single source of truth for what blocks readiness.

This module does NOT:

  * touch the `payload`, `source_id`, `content_digest`,
    `library_id`, or `created_at` columns (they are protected at
    the schema level by the `asset_revision_protect_immutable`
    trigger the migration added, and this module never tries to
    write them);
  * read, parse or process source files, walk directories, open
    HTTP routes, or call ComfyUI;
  * call out to a translation service, an LLM, or any external
    dependency. The translation map is what the operator (or a
    later task) writes into the `translation` column; readiness
    is purely a function of what is already there;
  * modify the parser, the persistence layer, the legacy room
    importer, the Compose / Fill / Judge code paths, or any
    existing session / shot / workflow table.

The contract is: a revision can always be read by source id and
content digest. A revision can be marked ready, or pending, or
updated. A pending revision is not a failed revision, and the
report it carries is the precise, field-by-field reason a future
operator can act on.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import db
import resource_prompts
import resource_store
import translation_map


# The verdict values the readiness evaluation returns. The set is
# closed on purpose: the spec calls for a binary ready/pending
# distinction, and "rejected" is not a state a parser-accepted
# entry can be in (refusal happens at parse time, not at the
# readiness layer).
STATUS_READY: str = "ready"
STATUS_PENDING: str = "pending"

ALL_STATUSES: tuple[str, ...] = (STATUS_READY, STATUS_PENDING)


# A field-by-field reason template. The reason is a non-empty
# human-readable string naming the field that is blocking
# readiness. The same string is what the operator sees in a UI
# and what a test reads off a ReadinessReport.
def _pending_reason_for(field_name: str, source_field: str | None = None) -> str:
    """The pending reason for a required field that lacks a
    valid English translation.

    A single template keeps the message style consistent across
    every blocked field, and a test that pins the template pins
    the style. If source_field is provided and differs from field_name,
    the reason explicitly indicates the source field that was used.
    """
    source_detail = f" (source_field = {source_field!r})" if source_field and source_field != field_name else ""
    return (
        f"required field {field_name!r}{source_detail} lacks a valid English "
        f"translation; the revision stays inspectable but is "
        f"not ready for prompt preparation until a non-empty "
        f"English value is recorded in the translation column"
    )


@dataclass
class FieldReadiness:
    """The readiness state of one field on a revision.

    `name` is the field name as the preparation contract names
    it; `role` is the role the contract assigns; `translated`
    is True when a non-empty English value is recorded for the
    field in the `translation` column. `reason` is non-empty
    only when the field is blocking readiness, and a future
    operator can read it to learn what to fix.
    """

    name: str
    role: str
    translated: bool
    reason: str = ""


@dataclass
class ReadinessReport:
    """The verdict for one revision.

    `status` is one of ALL_STATUSES. `pending_fields` is the
    ordered dict of field name -> reason for every required
    field whose English translation is missing or invalid; an
    empty dict means the revision is ready. `field_readiness`
    is the per-field verdict across every named field, so a UI
    or a test can list every field's role and translation
    status without re-running the evaluation. `coverage` is
    the dict the persistence layer should write into the
    `coverage` column; it carries the same per-field verdicts
    plus the unmapped-fields list the contract pins.
    """

    status: str
    pending_fields: dict[str, str] = field(default_factory=dict)
    field_readiness: list[FieldReadiness] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return self.status == STATUS_READY

    @property
    def is_pending(self) -> bool:
        return self.status == STATUS_PENDING

    @property
    def pending_field_names(self) -> list[str]:
        """The names of the blocking fields, in source order.

        Sorted alphabetically so a test or a UI message that
        lists the blocking fields is stable across runs. The
        sorted order is the order the verdict is reported in
        and the order the coverage dict records them under.
        """
        return sorted(self.pending_fields.keys())


# -- Translation validation -------------------------------------------------


@dataclass
class SidecarInspection:
    """The result of a pure, non-throwing translation sidecar inspection."""

    canonical_translation: dict[str, Any]
    errors: list[str]
    invalid_families: set[str] = field(default_factory=set)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


def is_valid_english_translation_scalar(value: Any) -> bool:
    """True when ``value`` is a non-empty English string.

    A valid English translation must be a string, non-empty after stripping,
    contain at least one ASCII letter, and not contain non-English characters.
    """
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return False
    if not any(("A" <= ch <= "Z") or ("a" <= ch <= "z") for ch in stripped):
        return False
    return not translation_map.contains_non_english(stripped)


_is_valid_english_translation = is_valid_english_translation_scalar


def is_valid_effective_translation_value(value: Any) -> bool:
    """True when ``value`` is a valid English scalar string or a non-empty list of valid English strings."""
    if isinstance(value, str):
        return is_valid_english_translation_scalar(value)
    if isinstance(value, list):
        if not value:
            return False
        return all(is_valid_english_translation_scalar(item) for item in value)
    return False


def resolve_source_field_and_value(canonical_field: str, payload: dict) -> tuple[str, Any]:
    """Resolve the source field name and value from payload for canonical_field.

    Raises ValueError if:
    - payload is not a dict.
    - payload contains no fields belonging to canonical_field's semantic family.
    - payload contains conflicting alias keys for the same family with different values.
    """
    if not isinstance(payload, dict):
        raise TypeError(f"Payload must be a dict, got {type(payload).__name__}")

    family = resource_prompts.alias_family_for_field(canonical_field)
    present_aliases = [(alias, payload[alias]) for alias in family if alias in payload]

    if not present_aliases:
        if canonical_field in payload:
            return canonical_field, payload[canonical_field]
        raise ValueError(
            f"Translation field for canonical {canonical_field!r} has no corresponding source field in payload"
        )

    first_name, first_val = present_aliases[0]
    if len(present_aliases) > 1:
        if not all(val == first_val for _, val in present_aliases[1:]):
            conflict_dict = {name: val for name, val in present_aliases}
            raise ValueError(
                f"Conflicting source aliases in payload for canonical field {canonical_field!r}: {conflict_dict!r}"
            )
    return first_name, first_val


def validate_translation_value_for_source(
    kind: str,
    payload: dict,
    canonical_field: str,
    translation_value: Any,
) -> None:
    """Validate translation_value against the source field and shape in payload.

    Raises ValueError if:
    - canonical_field is not a ROLE_DESCRIPTIVE_INPUT field for kind.
    - Source field resolution fails.
    - Field is required descriptive, but source or translation is not a scalar string.
    - Translation shape does not match source shape (scalar vs list).
    - Source shape is unsupported.
    - Translation value contains invalid English, empty strings, or mismatched list length.
    """
    info = resource_prompts.classify_field(kind, canonical_field)
    role = info.get("role")
    if role != resource_prompts.ROLE_DESCRIPTIVE_INPUT:
        raise ValueError(
            f"Disallowed field in translation sidecar: canonical {canonical_field!r} has role {role!r}; "
            "only descriptive input fields may have translations"
        )

    source_name, source_val = resolve_source_field_and_value(canonical_field, payload)
    is_required = bool(info.get("required"))

    if is_required:
        if not isinstance(source_val, str):
            raise ValueError(
                f"Required field {canonical_field!r} source value (from {source_name!r}) must be a scalar string, got {type(source_val).__name__}"
            )
        if not isinstance(translation_value, str):
            raise ValueError(
                f"Translation shape mismatch for required field {canonical_field!r}: translation value must be a scalar string, got {type(translation_value).__name__}"
            )
        if not is_valid_english_translation_scalar(translation_value):
            raise ValueError(
                f"Required field {canonical_field!r} translation value is not valid English: {translation_value!r}"
            )
        return

    if isinstance(source_val, str):
        if not isinstance(translation_value, str):
            raise ValueError(
                f"Translation shape mismatch for field {canonical_field!r}: source is scalar string, got {type(translation_value).__name__}"
            )
        if not is_valid_english_translation_scalar(translation_value):
            raise ValueError(
                f"Translation for field {canonical_field!r} is not valid English: {translation_value!r}"
            )
        return

    if isinstance(source_val, list):
        if not isinstance(translation_value, list):
            raise ValueError(
                f"Translation shape mismatch for field {canonical_field!r}: source is list, got {type(translation_value).__name__}"
            )
        if len(translation_value) != len(source_val):
            raise ValueError(
                f"Translation list length mismatch for field {canonical_field!r}: expected {len(source_val)}, got {len(translation_value)}"
            )
        for idx, item in enumerate(translation_value):
            if not isinstance(item, str) or not is_valid_english_translation_scalar(item):
                raise ValueError(
                    f"Item {idx} in translation list for field {canonical_field!r} is not valid English: {item!r}"
                )
        return

    raise ValueError(
        f"Unsupported source value type for field {canonical_field!r}: {type(source_val).__name__}"
    )


def inspect_translation_sidecar(
    kind: str,
    payload: Any,
    existing_translation: Any,
) -> SidecarInspection:
    """Pure, non-throwing inspection of an existing translation sidecar.

    Safely inspects sidecar entries, classifies roles, validates aliases,
    checks source-backing and structural constraints.

    Returns a SidecarInspection with usable canonical translations, deterministic
    errors, and invalid families. Never raises for expected validation errors.
    """
    if existing_translation is None:
        existing_translation = {}
    if not isinstance(existing_translation, dict):
        return SidecarInspection(
            canonical_translation={},
            errors=[f"Translation sidecar must be a JSON object, got {type(existing_translation).__name__}"],
            invalid_families=set(),
        )
    if not isinstance(payload, dict):
        return SidecarInspection(
            canonical_translation={},
            errors=[f"Resource payload must be a JSON object, got {type(payload).__name__}"],
            invalid_families=set(),
        )

    errors: list[str] = []
    invalid_families: set[str] = set()
    canonical_translation: dict[str, Any] = {}

    entries_by_family: dict[str, list[tuple[str, Any]]] = {}
    for raw_k, val in existing_translation.items():
        c_k = resource_prompts.canonical_field_name(str(raw_k))
        entries_by_family.setdefault(c_k, []).append((str(raw_k), val))

    for c_k, entries in sorted(entries_by_family.items()):
        info = resource_prompts.classify_field(kind, c_k)
        role = info.get("role")
        if role != resource_prompts.ROLE_DESCRIPTIVE_INPUT:
            for raw_k, _ in entries:
                errors.append(
                    f"Disallowed field in translation sidecar: {raw_k!r} has role {role!r}; "
                    "only descriptive input fields may have translations"
                )
            invalid_families.add(c_k)
            continue

        if len(entries) == 1:
            raw_k, raw_val = entries[0]
        else:
            first_val = entries[0][1]
            if all(val == first_val for _, val in entries[1:]):
                raw_k = entries[0][0]
                raw_val = first_val
            else:
                alias_dict = {k: v for k, v in entries}
                errors.append(
                    f"Conflicting alias entries in translation sidecar for canonical field {c_k!r} with different values: {alias_dict!r}"
                )
                invalid_families.add(c_k)
                continue

        try:
            validate_translation_value_for_source(kind, payload, c_k, raw_val)
            canonical_translation[c_k] = raw_val
        except (ValueError, TypeError) as exc:
            errors.append(str(exc))
            invalid_families.add(c_k)

    return SidecarInspection(
        canonical_translation=canonical_translation,
        errors=errors,
        invalid_families=invalid_families,
    )


def validate_and_canonicalize_existing_translation(
    kind: str,
    payload: Any,
    existing_translation: Any,
) -> dict[str, Any]:
    """Strict validation wrapper over inspect_translation_sidecar.

    Raises ValueError on any sidecar validation error. Returns usable canonical translations.
    """
    inspection = inspect_translation_sidecar(kind, payload, existing_translation)
    if inspection.errors:
        raise ValueError(inspection.errors[0])
    return inspection.canonical_translation


# -- Per-field readiness ----------------------------------------------------


def _field_readiness(
    field_name: str,
    role: str,
    usable_canonical: dict[str, Any],
) -> FieldReadiness:
    """The readiness verdict for one field.

    A field is translated when is_valid_effective_translation_value returns True
    for the value at usable_canonical[canonical_name].
    """
    canonical_name = resource_prompts.canonical_field_name(field_name)
    val = usable_canonical.get(canonical_name) if isinstance(usable_canonical, dict) else None
    if role == resource_prompts.ROLE_DESCRIPTIVE_INPUT:
        translated = is_valid_effective_translation_value(val)
    else:
        translated = is_valid_english_translation_scalar(val)
    if translated:
        return FieldReadiness(
            name=field_name, role=role, translated=True,
        )
    return FieldReadiness(
        name=field_name, role=role, translated=False,
    )


def _unmapped_field_names(kind: str, payload: dict) -> list[str]:
    """The top-level field names the preparation contract
    does not name for ``kind``.

    The list is the names whose role is the unmapped
    sentinel (or the auxiliary-data role for a scene kind,
    which is reported as a role mismatch, the same way
    `validate_resource_entry` reports it). The list is
    sorted so the coverage record is stable across runs.

    The check is structural: a name the contract never
    names cannot be a prompt input and cannot block
    readiness, but a UI that wants to show "this entry has
    a field the contract does not recognise" reads the
    coverage record and lists the names by reading this
    list off the verdict.
    """
    mapping = resource_prompts.mapping_for_kind(kind)
    if resource_prompts.is_auxiliary_kind(kind):
        auxiliary_record_keys = set(resource_prompts._AUXILIARY_RECORD_FIELDS)  # noqa: SLF001
    else:
        auxiliary_record_keys = set()
    names: list[str] = []
    for name in payload:
        if name in mapping:
            continue
        if (
            resource_prompts._is_anchor_suffix(name)  # noqa: SLF001
            or resource_prompts._is_mood_prefix(name)  # noqa: SLF001
        ):
            continue
        if name in auxiliary_record_keys:
            continue
        names.append(str(name))
    names.sort()
    return names


# -- The evaluation entry point ---------------------------------------------


def evaluate_readiness(
    kind: str,
    payload: Any,
    translation: Any | None,
) -> ReadinessReport:
    """The readiness verdict for ``payload`` under ``translation``.

    The evaluation is pure: it does not read the database, it
    does not mutate the arguments, and two calls with the same
    arguments return the same report. The payload is read
    structurally (the field names it carries and whether each
    required field has a value) and never as a string to be
    re-encoded.

    `kind` is the OpenSpec-declared kind (`rooms` or
    `fused_scenes`). An auxiliary kind returns a ready verdict
    with the empty pending list and a coverage record whose
    `pending_fields` and `missing_translations` are empty:
    auxiliary resources are pipeline data, never prompt input,
    and a future task that wants to evaluate them should be
    talking to a different layer (the `resource_prompts`
    contract says auxiliary kinds have no required fields).

    The readiness verdict is binary by spec: a revision is
    ready when every required *descriptive* field has a valid
    English translation, and pending otherwise. The
    `pending_fields` dict is the per-field reason an operator
    acts on.

    Required identity, selection-metadata, writer-guidance
    and auxiliary-data fields are NOT translation blockers:
    an identifier is what the source called the entry, a
    selection parameter is a number, and writer guidance is
    the author writing down how the shot works. None of
    these is "display or preparation prose" in the sense the
    OpenSpec uses, and the readiness layer's contract is
    about display and preparation prose only. A
    `required` flag on an identity field still applies to
    the structural validation `validate_resource_entry`
    does; readiness is the separate, narrower check on
    descriptive prose.
    """
    if not isinstance(payload, dict):
        raise TypeError(
            f"a readiness evaluation needs a dict payload, got "
            f"{type(payload).__name__}"
        )
    if translation is None:
        translation = {}
    if not isinstance(translation, dict):
        raise TypeError(
            f"a readiness evaluation needs a dict translation, got "
            f"{type(translation).__name__}"
        )

    # An auxiliary kind has no required fields. The verdict is
    # ready by construction; the coverage record still lists
    # what the entry carries so a UI can show it.
    if resource_prompts.is_auxiliary_kind(kind):
        coverage = {
            "fields": {},
            "missing_translations": [],
            "unmapped_fields": _unmapped_field_names(kind, payload),
        }
        return ReadinessReport(
            status=STATUS_READY,
            pending_fields={},
            field_readiness=[],
            coverage=coverage,
        )

    # 1. Inspect translation sidecar first (pure, non-throwing).
    inspection = inspect_translation_sidecar(kind, payload, translation)
    usable_canonical = inspection.canonical_translation

    mapping = resource_prompts.mapping_for_kind(kind)
    pending: dict[str, str] = {}
    per_field: list[FieldReadiness] = []
    coverage_fields: dict[str, dict[str, Any]] = {}

    # Required descriptive fields are the readiness blockers.
    # Other required fields (identity, selection metadata) are
    # recorded in the coverage for completeness but are not
    # pending reasons: an entry that is missing its identifier
    # does not even reach the parser (it lands in
    # `missing_identifier` instead), and a selection parameter
    # is not prose to translate.
    required_descriptive = [
        name for name, info in mapping.items()
        if info.get("required")
        and info.get("role") == resource_prompts.ROLE_DESCRIPTIVE_INPUT
    ]
    for field_name in required_descriptive:
        info = mapping.get(field_name, {})
        role = str(info.get("role", ""))

        family = resource_prompts.alias_family_for_field(field_name)
        source_field = None
        for alias in family:
            if alias in payload:
                source_field = alias
                break
        if source_field is None:
            source_field = field_name

        verdict = _field_readiness(field_name, role, usable_canonical)
        if not verdict.translated:
            pending[field_name] = _pending_reason_for(field_name, source_field=source_field)
        field_cov: dict[str, Any] = {
            "role": role,
            "translated": verdict.translated,
        }
        if source_field != field_name:
            field_cov["source_field"] = source_field
        coverage_fields[field_name] = field_cov
        per_field.append(verdict)


    # All other named fields from the static mapping. The role
    # and the translated flag both go into the coverage record;
    # an untranslated non-blocker is recorded but is not a
    # blocker.
    for field_name, info in mapping.items():
        if field_name in coverage_fields:
            continue
        role = str(info.get("role", ""))
        verdict = _field_readiness(field_name, role, usable_canonical)
        coverage_fields[field_name] = {
            "role": role,
            "translated": verdict.translated,
        }
        per_field.append(verdict)

    # Dynamic writer-guidance fields. The preparation contract
    # recognises two name patterns the static mapping does not
    # list by hand: the ``mood_*`` prefix and the ``*_anchor``
    # suffix. `classify_field` in `resource_prompts` accepts
    # them as `writer_guidance`; a UI or a reviewer reading
    # the readiness report needs to see them in
    # `coverage["fields"]` and `field_readiness` so the entry
    # is not silently reduced to the static mapping. They are
    # NOT in `unmapped_fields` (the contract recognises them),
    # and they are NOT blockers (writer guidance is not display
    # or preparation prose). The check is structural: any
    # payload name not already covered by the static mapping
    # is tested against the same name rules
    # `resource_prompts._is_anchor_suffix` and
    # `resource_prompts._is_mood_prefix` publish, and the
    # matching names are added with role `writer_guidance`.
    for field_name in payload:
        if field_name in coverage_fields:
            continue
        if not (
            resource_prompts._is_anchor_suffix(str(field_name))  # noqa: SLF001
            or resource_prompts._is_mood_prefix(str(field_name))  # noqa: SLF001
        ):
            continue
        verdict = _field_readiness(
            str(field_name), resource_prompts.ROLE_WRITER_GUIDANCE,
            usable_canonical,
        )
        coverage_fields[str(field_name)] = {
            "role": resource_prompts.ROLE_WRITER_GUIDANCE,
            "translated": verdict.translated,
        }
        per_field.append(verdict)

    coverage = {
        "fields": coverage_fields,
        "missing_translations": sorted(pending.keys()),
        "unmapped_fields": _unmapped_field_names(kind, payload),
    }
    if inspection.errors:
        coverage["sidecar_error"] = inspection.errors[0]

    if inspection.errors:
        status = STATUS_PENDING
    else:
        status = STATUS_READY if not pending else STATUS_PENDING

    return ReadinessReport(
        status=status,
        pending_fields=dict(sorted(pending.items())),
        field_readiness=per_field,
        coverage=coverage,
    )


# -- Persistence ------------------------------------------------------------


def _encode_coverage(coverage: dict) -> str:
    """Encode ``coverage`` for the `coverage` column.

    Mirrors the form `resource_store._encode_json_object`
    uses: JSON without ASCII escapes, compact separators, so
    a round trip through the database preserves the dict a
    test plants and reads back. The two layers share the
    same encoding so a coverage record written by readiness
    and a coverage record read by the legacy tests are
    structurally identical.
    """
    return json.dumps(coverage, ensure_ascii=False, separators=(",", ":"))


def set_revision_readiness(
    revision_id: int,
    coverage: dict,
) -> None:
    """Persist ``coverage`` to the row identified by
    ``revision_id``.

    The function writes ONLY the `coverage` column. The
    `asset_revision_protect_immutable` trigger the migration
    added (task 2.1) refuses UPDATEs that name any of the
    protected columns (`id`, `library_id`, `source_id`,
    `content_digest`, `payload`, `created_at`); this
    function's UPDATE statement names only `coverage`, so
    the trigger does not fire and the original payload and
    immutable provenance survive the write.

    `coverage` is validated as a dict before encoding. A
    non-dict value is refused with a TypeError, so a typo at
    the call site surfaces immediately and not as a silent
    JSON round trip through a string.
    """
    if not isinstance(revision_id, int) or isinstance(revision_id, bool):
        raise TypeError(
            f"revision_id must be an int, got {type(revision_id).__name__}"
        )
    if not isinstance(coverage, dict):
        raise TypeError(
            f"coverage must be a dict, got {type(coverage).__name__}"
        )
    db.run(
        "UPDATE asset_revision SET coverage = ? WHERE id = ?",
        _encode_coverage(coverage),
        revision_id,
    )


def set_revision_readiness_from_report(
    revision_id: int,
    report: ReadinessReport,
) -> None:
    """Persist ``report.coverage`` to the row identified by
    ``revision_id``.

    A thin wrapper around `set_revision_readiness` that
    reads the coverage dict off a `ReadinessReport`. The
    report is the canonical verdict a caller already has;
    this helper just writes it without forcing the caller
    to reach into the report's `.coverage` attribute.
    """
    set_revision_readiness(revision_id, report.coverage)


# -- Revision-level evaluation ----------------------------------------------


def _lookup_library_kind(library_id: int) -> str:
    """The kind the library registered with, or the empty
    string when the library is unknown.

    The `kind` column on `resource_library` is the source
    of truth for the OpenSpec-declared kind the parser
    classified the entries under. The asset_revision row
    does NOT carry the kind, so a readiness evaluation that
    starts from a revision has to read the kind off the
    parent library. A future schema change that denormalises
    the kind onto the revision is a compatibility move, not
    a contract change, and the readiness layer's interface
    stays the same.
    """
    row = db.one(
        "SELECT kind FROM resource_library WHERE id = ?", library_id,
    )
    return str(row["kind"]) if row else ""


def evaluate_revision_readiness(
    library_id: int,
    source_id: str,
    *,
    content_digest: str | None = None,
) -> ReadinessReport:
    """Read a revision out of the database and evaluate it.

    A convenience wrapper for the common case where a caller
    has the natural key and not a fetched revision dict. The
    function reads the row through `resource_store.get_revision`,
    which already decodes the JSON columns, runs
    `evaluate_readiness` over the result, and looks up the
    kind off the parent library (the schema does not
    denormalise the kind onto the asset_revision row). A
    missing revision raises KeyError, the same way `dict[key]`
    would; the test that pins the contract reads a planted
    revision only.

    The `content_digest` argument is optional: when None,
    the function evaluates the LATEST revision for the
    source entry, which is the lookup `get_revision`
    implements by default. A caller that has a precise
    digest in hand passes it to evaluate a specific
    revision, which is the case after a reimport that
    created a new revision alongside the prior one.

    A library whose `kind` is empty (a library registered
    without a kind label) falls back to a structural
    inference off the payload. The inference is the same
    one `_infer_kind_from_payload` uses; it exists so a
    revision written before the library's kind was filled
    in still produces a meaningful verdict.
    """
    if content_digest is None:
        revision = resource_store.get_revision(
            library_id=library_id, source_id=source_id,
        )
    else:
        revision = resource_store.get_revision(
            library_id=library_id, source_id=source_id,
            content_digest=content_digest,
        )
    if revision is None:
        raise KeyError(
            f"no asset_revision found for library_id={library_id!r} "
            f"source_id={source_id!r} content_digest={content_digest!r}"
        )
    kind = _lookup_library_kind(library_id)
    if not kind:
        kind = _infer_kind_from_payload(revision["payload"])
    return evaluate_readiness(
        kind=kind,
        payload=revision["payload"],
        translation=revision.get("translation") or {},
    )


def _infer_kind_from_payload(payload: dict) -> str:
    """A best-effort kind guess for a revision whose
    `kind` column was not populated.

    The persistence layer does NOT currently store the
    kind on the `asset_revision` row, so a revision that
    was written before this task does not carry a kind.
    The parser's structural test is reproduced here so a
    pre-existing revision still gets a meaningful kind for
    the readiness evaluation. A revision written by this
    task (and forward) should store its kind alongside the
    payload; the readiness layer is defensive against the
    older shape so a re-evaluation works on rows from
    before this task landed.

    The check mirrors `_classify_scene_kind` in
    `resource_parser`: a dict with a non-empty `prompt`
    and no other descriptive content is a fused scene;
    a dict with a label, theme, or list field is a
    rooms entry; anything else is reported as `rooms`
    (the conservative answer) so the readiness layer
    uses the rooms contract.
    """
    has_prompt = isinstance(payload.get("prompt"), str) and bool(payload["prompt"].strip())
    has_label = any(
        isinstance(payload.get(f), str) and bool(payload[f].strip())
        for f in ("label", "name", "title", "display_name")
    )
    has_theme = any(
        isinstance(payload.get(f), str) and bool(payload[f].strip())
        for f in ("scene_theme", "theme", "theme_text", "text", "description")
    )
    has_list = any(
        f in payload and payload[f] is not None
        for f in ("tags", "tag", "props", "objects", "furniture")
    )
    if has_prompt and not has_label and not has_theme and not has_list:
        return resource_prompts.KIND_FUSED_SCENES
    if has_label or has_theme or has_list:
        return resource_prompts.KIND_ROOMS
    return resource_prompts.KIND_ROOMS


__all__ = (
    "STATUS_READY", "STATUS_PENDING", "ALL_STATUSES",
    "FieldReadiness", "ReadinessReport",
    "SidecarInspection",
    "is_valid_english_translation_scalar",
    "is_valid_effective_translation_value",
    "resolve_source_field_and_value",
    "validate_translation_value_for_source",
    "inspect_translation_sidecar",
    "validate_and_canonicalize_existing_translation",
    "evaluate_readiness",
    "evaluate_revision_readiness",
    "set_revision_readiness",
    "set_revision_readiness_from_report",
)
