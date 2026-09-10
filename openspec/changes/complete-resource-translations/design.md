## Context

The resource planning subsystem introduced in `adopt-resource-session-planning` allows users to import complete source resources into SQLite (`resource_library` and `asset_revision` tables). When imported, foreign-language revisions are recorded with an initial empty translation sidecar (`asset_revision.translation`) and evaluate to `pending` readiness.
However, there was no end-to-end operational pipeline to match, preview, and apply translation maps to these revisions, nor to update individual revision translations. Furthermore, preparation must strictly consume authorized English translations from the sidecar without falling back to the source payload for required fields.

## Goals / Non-Goals

**Goals:**
- Provide deterministic matching of external translation maps to library revisions using exact source strings.
- Support both dictionary (`dict[source_string, translation_entry]`) and list (`list[translation_entry]`) map formats, rejecting duplicate sources in list form while preserving legal literal keys (`"items"`, `"translation_map"`).
- Enforce strict upfront authorization against `ROLE_DESCRIPTIVE_INPUT` fields only.
- Enforce canonical translation sidecar storage for declared semantic families (`label`, `scene_theme`, `tags`, `prompt`) and independent field names.
- Ensure `asset_revision.payload` is strictly immutable.
- Enforce scalar-only string contracts for required descriptive fields (`label` and `scene_theme` in `rooms`, `prompt` in `fused_scenes`), with zero fallback to payload.
- Enforce 1-to-1 positional and length matching for optional descriptive lists, pre-validating source lists structurally before matching to fail closed on non-string items.
- Provide pure, non-throwing sidecar inspection for readiness reporting that preserves inspectability of invalid persisted sidecars with `coverage["sidecar_error"]` and canonical `pending_fields`.
- Provide a two-phase `preview` -> `apply` workflow guarded by an active database-directory-scoped HMAC-SHA256 key (`.resource-translation-preview-key`) to prevent TOCTOU race conditions.
- Detect TOCTOU drift (metadata, fingerprint shifts, or deleted library) returning HTTP 409 Conflict.
- Guarantee atomic transactions under `BEGIN IMMEDIATE` with complete rollback on failure.
- Decouple translation sidecar updates from derived coverage repairs to eliminate redundant SQL writes while keeping `Preview.would_update == Apply.updated`.
- Support non-destructive validated merge updates on revision translations with redundant SQL write suppression on no-op updates.
- Filter untranslated optional fields containing non-English characters during prompt preparation while preserving clean English prose.
- Expose translation preview and apply operations in the frontend Resources UI.

**Non-Goals:**
- Calling external translation APIs, LLMs, or network services.
- Translating non-descriptive fields (identity, selection metadata, auxiliary data).
- Modifying historical finalized prompts in `prepared_take`.
- Single-revision translation editing in the Resources UI (managed via HTTP API).

## Decisions

### 1. Canonical Storage, Semantic Families & Input Shapes

Translation sidecars store values under canonical names for declared alias families:
- `label`: `("label", "name", "title", "display_name")` -> `"label"`
- `scene_theme`: `("scene_theme", "theme", "theme_text")` -> `"scene_theme"`
- `tags`: `("tags", "tag")` -> `"tags"`
- `prompt`: `("prompt",)` -> `"prompt"`
- `id`: `("id", "identifier", "key")` -> `"id"` (identity, not translated)

Independent descriptive fields (`props`, `objects`, `furniture`, `uniform_fit`, `text`, `description`) retain their own field names. Raw alias keys are never written to `asset_revision.translation`. Duplicate alias translations with identical values are accepted; conflicting alias translations within a single map entry or update are rejected.

**Input Shapes:**
The normalization boundary accepts:
1. `dict[source_string, translation_entry]`
2. `list[translation_entry]`

List-form input rejects duplicate `source` strings before conversion. No internal envelopes (`{"translation_map": ...}` or `{"items": ...}`) are assumed or unwrapped; the HTTP route in `main.py` extracts the map from the request payload. Literal source strings `"translation_map"` and `"items"` remain valid map keys and are not discarded.

**Authorization & Value Shapes:**
Only source-backed fields classified as `ROLE_DESCRIPTIVE_INPUT` may be translated. Unauthorized fields fail closed with HTTP 422.
Required descriptive fields are scalar-only:
- `rooms`: `label`, `scene_theme`
- `fused_scenes`: `prompt`
Both source and translation values must be scalar `str`.

Optional descriptive translations support:
- `source str -> translation str`
- `source list[str] -> translation list[str]` (same length, same positional order, valid English items).
Malformed optional source lists containing non-string items fail closed before map matching, even if no map item matches. Incomplete optional lists are omitted if containing non-English prose.

### 2. Readiness Evaluation, Sidecar Inspection, and Pending Keys

Readiness evaluation is decoupled into:
1. `inspect_translation_sidecar()`: Pure, non-throwing inspection for readiness reporting. Evaluates existing translations without throwing exceptions on contract violations. Invalid sidecars record the error in `coverage["sidecar_error"]` and evaluate to `STATUS_PENDING`.
2. `validate_and_canonicalize_existing_translation()`: Strict validation wrapper for operational boundaries (prompt preparation, revision update), failing closed with `ValueError`.

Keys in `pending_fields` and `coverage["missing_translations"]` are strictly canonical (`"label"`, `"scene_theme"`, `"prompt"`). When an alias was used in the source payload (e.g. `theme`), the reason string explicitly records `(source_field = 'theme')`, and `coverage["fields"]` records `source_field: "theme"`.
Ordinary untranslated optional fields do not block readiness. However, an invalid persisted translation sidecar entry (including for optional fields) violates the contract and forces overall `STATUS_PENDING`.

### 3. Preparation Strictness & Provenance

Prompt preparation strictly retrieves required descriptive inputs from `asset_revision.translation`. If a required field lacks a valid English translation in the sidecar, preparation raises `PreparationFieldError`. There is zero fallback to the source payload for required fields.
Optional fields with translations are included in `descriptive_inputs`. Optional fields without translations are omitted if they contain non-English characters (`contains_non_english`), or included if they contain pure English text.
Historical snapshots in `prepared_take` remain immutable once finalized.

### 4. Two-Phase Preview -> Apply, Attestation & Transaction Boundaries

Bulk translation map application is split into:
1. `preview`: Read-only with respect to resource tables and database state. Lazily initializes a 32-byte local HMAC key (`.resource-translation-preview-key`) scoped to the active SQLite database directory if missing. Validates the entire map upfront, computes matching diffs and candidate readiness, and issues an HMAC-SHA256 attestation token binding `library_key`, `library_id`, `kind`, `created_at`, map digest, library fingerprint, and expiry timestamp.
2. `apply`: Verifies token signature, re-computes library fingerprint and map digest inside a database transaction under `BEGIN IMMEDIATE`.
   - If the library was deleted after preview, raises `TranslationConflictError` (HTTP 409).
   - If library metadata or revision fingerprints drifted, raises `TranslationConflictError` (HTTP 409).
   - The library fingerprint binds the exact raw persisted translation sidecars (`rev["translation"]`) with deterministic JSON serialization, explicitly excluding coverage so Apply can repair stale coverage without invalidating the preview token.
   - Updates `translation` only when changed, and updates `coverage` only when changed. Stale coverage is repaired without inflating `updated` counts, maintaining `Preview.would_update == Apply.updated`.
   - Rolls back all writes if any error occurs.

Single-revision updates perform atomic read, validation, merge, readiness recomputation, and write inside `with db.transaction():` under `BEGIN IMMEDIATE`, suppressing redundant SQL updates on no-op changes.
