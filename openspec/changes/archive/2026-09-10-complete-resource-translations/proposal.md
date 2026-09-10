## Why

Resource revisions imported from non-English sources are stored in `resource_library` and `asset_revision` tables with an initial `pending` status, but there was no operational end-to-end mechanism to preview, match, validate and apply English translations. Furthermore, prompt preparation risked consuming untranslated or un-canonicalized values instead of verified English translations.

## What Changes

- Introduce an atomic, two-phase bulk translation flow (`preview` -> `apply`) for imported resource libraries, protected against TOCTOU race conditions with HMAC-SHA256 attestation tokens signed by a private key (`.resource-translation-preview-key`) scoped to the active SQLite database directory.
- Accept translation maps as direct dictionaries (`dict[source_string, translation_entry]`) and list forms (`list[translation_entry]`) with duplicate source detection, without internal envelope requirements, preserving legal literal keys such as `"items"` and `"translation_map"`.
- Enforce strict upfront translation authorization against `ROLE_DESCRIPTIVE_INPUT` fields only, rejecting unauthorized target fields with HTTP 422.
- Enforce canonical translation sidecar storage in `asset_revision.translation` for declared semantic families (`label`, `scene_theme`, `tags`, `prompt`) and independent field names, rejecting raw aliases and ambiguous alias collisions while permitting consistent duplicate alias entries.
- Enforce scalar-only string contracts for required descriptive fields (`label` and `scene_theme` for `rooms`; `prompt` for `fused_scenes`), failing closed if non-string values appear.
- Validate optional descriptive lists as 1-to-1 ordered `list[str] -> list[str]` mappings, pre-validating source list items structurally before matching to fail closed on non-string items.
- Eliminate payload fallbacks for required descriptive fields during prompt preparation (raising `PreparationFieldError` if absent). Untranslated optional non-English prose is omitted to avoid foreign script pollution, while clean English prose is preserved.
- Establish a decoupled readiness inspection (`inspect_translation_sidecar()`) that remains non-throwing and inspectable for invalid sidecars, surfacing `coverage["sidecar_error"]` and forcing pending status, while keeping `pending_fields` canonical.
- Detect TOCTOU drift in bulk apply (metadata changes, fingerprint shifts, or deleted libraries) and return HTTP 409 Conflict.
- Execute bulk apply and single-revision updates within atomic database transactions under `BEGIN IMMEDIATE`, recomputing readiness, repairing stale coverage without count discrepancies (`Preview.would_update == Apply.updated`), and suppressing redundant SQL writes on unchanged translation or coverage.
- Integrate translation management into the frontend Resources UI with live readiness indicators, sidecar error banners, and preview/apply controls.

## Capabilities

### New Capabilities

- `resource-store`: add validated translation persistence, atomic bulk updates with coverage repair, library state fingerprinting, and DB-scoped attestation.
- `resource-prompts`: formalize canonical semantic families, alias resolution, strict sidecar consumption without payload fallback, structural pre-validation for descriptive fields, and non-English optional field omission during preparation.

## Impact

Backend: translation service module (`backend/resource_translation.py`), store update methods in `backend/resource_store.py`, prompt preparation and readiness updates, and new API routes.
Frontend: API client extensions and UI preview/apply controls with live ready/pending counts in `Resources.jsx`.
Testing: comprehensive automated tests in `tests/test_resource_translation.py` and updated fixtures across existing suites.
Documentation: updated `README.md` and `docs/getting-started.md`.
No personal data, no machine paths, no external network or LLM dependencies.
