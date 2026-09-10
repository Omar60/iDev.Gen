## Context

The resource planning subsystem introduced in `adopt-resource-session-planning` allows users to import complete source resources into SQLite (`asset_library` and `asset_revision` tables). When imported, foreign-language revisions are recorded with an initial empty translation sidecar (`asset_revision.translation`) and evaluate to `pending` readiness.
However, there is no end-to-end operational pipeline to match, preview, and apply translation maps to these revisions, nor to update individual revision translations. Furthermore, preparation must strictly consume authorized English translations from the sidecar without falling back to the source payload for required fields.

## Goals / Non-Goals

**Goals:**
- Provide deterministic matching of external translation maps to library revisions using exact source strings.
- Enforce canonical translation sidecar storage for declared semantic families (`label`, `scene_theme`, `tags`, `prompt`) and independent field names.
- Ensure `asset_revision.payload` is strictly immutable.
- Enforce that required descriptive fields require an authorized English translation in `asset_revision.translation`; zero fallback to payload.
- Provide stable canonical keys in `pending_fields` with `source_field` annotations in reason strings and coverage metadata.
- Provide a two-phase `preview` -> `apply` workflow guarded by HMAC-SHA256 attestation tokens to prevent TOCTOU race conditions.
- Guarantee atomic transactions and complete rollback on invalid input maps.
- Safely report unmatched valid entries without failing the batch.
- Support non-destructive validated merge updates on revision translations.
- Filter untranslated optional fields containing non-English characters during prompt preparation.
- Expose translation preview and apply operations in the frontend Resources UI.

**Non-Goals:**
- Calling external translation APIs, LLMs, or network services.
- Translating non-descriptive fields (identity, selection metadata, auxiliary data).
- Modifying historical finalized prompts in `prepared_take`.

## Decisions

### 1. Canonical Storage & Semantic Families

Translation sidecars store values under canonical names for declared alias families:
- `label`: `("label", "name", "title", "display_name")` -> `"label"`
- `scene_theme`: `("scene_theme", "theme", "theme_text")` -> `"scene_theme"`
- `tags`: `("tags", "tag")` -> `"tags"`
- `prompt`: `("prompt",)` -> `"prompt"`
- `id`: `("id", "identifier", "key")` -> `"id"` (identity, not translated)

Independent descriptive fields (`props`, `objects`, `furniture`, `uniform_fit`, `text`, `description`) retain their own field names. Raw alias keys are never written to `asset_revision.translation`. Conflicting alias translations within a single map entry or update are rejected.

### 2. Readiness Evaluation and Canonical Pending Keys

Readiness requires valid English translations in the translation sidecar for all required descriptive fields (`label` and `scene_theme` for `rooms`; `prompt` for `fused_scenes`).
Keys in `pending_fields` and `coverage["missing_translations"]` are always canonical (`"label"`, `"scene_theme"`, `"prompt"`).
When an alias was used in the source payload (e.g. `theme`), the reason string explicitly includes `(source_field = 'theme')`, and `coverage["fields"]` records `source_field: "theme"`.

### 3. Preparation Strictness & Provenance

Prompt preparation strictly retrieves required descriptive inputs from `asset_revision.translation`. If a required field lacks a valid English translation in the sidecar, preparation raises `PreparationFieldError`. There is zero fallback to the source payload for required fields.
Optional fields with translations are included in `descriptive_inputs`. Optional fields without translations are omitted if they contain non-English characters (`contains_non_english`), or included if they contain pure English text.
Historical snapshots in `prepared_take` remain immutable once finalized.

### 4. Two-Phase Preview -> Apply with TOCTOU Protection

Bulk translation map application is split into:
1. `preview`: Read-only, validates the entire map, computes matching diffs and candidate readiness, and issues an HMAC-SHA256 attestation token binding the library key, map hash, library revision fingerprint, and expiry timestamp.
2. `apply`: Verifies token signature, re-computes library fingerprint and map hash (raising HTTP 409 Conflict if drifted), and applies merged translations and recomputed coverage within a single atomic database transaction.
Invalid maps abort immediately with HTTP 422 and zero database writes. Valid unmatched entries are safely reported as informational.
