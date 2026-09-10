## 1. Semantic Families & Readiness Contracts

- [x] 1.1 Declare canonical semantic families (`label`, `scene_theme`, `tags`, `prompt`, `id`) and export helper functions in `backend/resource_prompts.py`.
- [x] 1.2 Update `backend/resource_readiness.py` to evaluate readiness against canonical keys, format `pending_fields` with stable canonical keys and `source_field` annotations, and enforce strict English validation.

## 2. Translation Persistence & Service Layer

- [x] 2.1 Add `update_translation` to `backend/resource_store.py` respecting immutable column triggers.
- [x] 2.2 Implement `backend/resource_translation.py` with map validation, exact matching, canonicalization, HMAC-SHA256 attestation for preview, TOCTOU verification, and atomic apply with validated merge.

## 3. Preparation Enforcement & Integration

- [x] 3.1 Update `backend/resource_preparation.py` to strictly consume canonical sidecar translations for required descriptive fields without payload fallback, and omit untranslated non-English optional fields.
- [x] 3.2 Expose translation preview, apply, and revision update endpoints in `backend/main.py`.
- [x] 3.3 Add frontend API methods and update `Resources.jsx` with live badges, preview results, and apply execution.

## 4. Test Suite & Verification

- [x] 4.1 Implement comprehensive unit and integration tests in `tests/test_resource_translation.py`.
- [x] 4.2 Update existing test fixtures in `tests/test_resource_preparation.py` and `tests/test_resource_session_acceptance.py` to provide valid translation sidecars, verifying full regression suite passes.
- [x] 4.3 Validate with `npx --yes @fission-ai/openspec validate complete-resource-translations --strict`.

