## Why

Resource revisions imported from non-English sources are currently stored in `asset_revision` with a `pending` status, but there is no operational end-to-end mechanism to preview, match, validate and apply English translations. Furthermore, prompt preparation currently risks consuming untranslated or un-canonicalized values instead of verified English translations.

## What Changes

- Introduce an atomic, two-phase bulk translation flow (`preview` -> `apply`) for imported resource libraries, protected against TOCTOU race conditions with HMAC-SHA256 attestation tokens.
- Enforce canonical translation sidecar storage in `asset_revision.translation` for declared semantic families (`label`, `scene_theme`, `tags`, `prompt`) and independent field names, rejecting raw aliases (`name`, `theme`, `tag`, etc.) and ambiguous alias collisions.
- Eliminate payload fallbacks for required descriptive fields: readiness requires an authorized English translation in the sidecar, and prompt preparation strictly consumes this sidecar (raising `PreparationFieldError` if absent).
- Stabilize readiness reporting with strictly canonical keys in `pending_fields` (e.g. `pending_fields["scene_theme"]`), recording the actual payload source field (`source_field = "theme"`) in reason strings and coverage metadata.
- Support non-destructive, validated merge updates for single revisions and batch maps, preserving untouched fields and idempotency.
- Filter untranslated optional descriptive fields containing non-English prose out of prepared prompt clauses to avoid foreign script pollution.
- Integrate translation management into the frontend Resources UI with live readiness indicators and preview/apply controls.

## Capabilities

### New Capabilities

- `resource-store`: add validated translation persistence, atomic bulk updates, and library state fingerprinting.
- `resource-prompts`: formalize canonical semantic families, alias resolution, strict sidecar consumption without payload fallback, and non-English optional field omission during preparation.

## Impact

Backend: new translation service module (`backend/resource_translation.py`), store update method in `backend/resource_store.py`, prompt preparation and readiness updates, and new API routes.
Frontend: API client extensions and UI preview/apply controls with live ready/pending counts in `Resources.jsx`.
Testing: comprehensive automated tests in `tests/test_resource_translation.py` and updated fixtures across existing suites.
Documentation: updated `README.md` and user guide.
No personal data, no machine paths, no external network or LLM dependencies.
