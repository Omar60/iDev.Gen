## Why

The previous asset adoption reduced source scenes to room seeds and measured components while retaining the old prompt composer. Users need to select complete resources and build a coherent photographic session whose identity, appearance and clothing persist across takes.

## What Changes

- Store complete accepted source entries, source revisions and separate translations in local SQLite, with an import preview accounting for every input. Preserve nested and presently unused fields instead of silently discarding them.
- Remove technical restrictions that require every resource to be a room, every fused scene to be split, and every new session to depend on the measured camera catalogue. Unknown structures remain inspectable but cannot generate until their field mapping is defined.
- Introduce resource-based session drafts: a chosen character, fixed look and initial wardrobe, selected source resources, ordered takes, explicit wardrobe changes, and recoverable preparation progress.
- Keep wardrobe constant by default. A deliberate change can affect one take or that take and following takes. Source suggestions and assistant output cannot override session choices silently.
- Prepare prompts from structured take state, with field provenance, conflict review and a saved final prompt. Reuse suitable source preparation concepts without claiming equivalence to unavailable compiled internals.
- Retain legacy sessions, the measured composer and existing reference workflows. No automatic conversion of previous prompts or evidence.
- Drop content safeguards from the import path. A character LoRA does not require special handling for prohibited content in a source entry: it follows the same import path as the rest. This proposal removes technical scene restrictions and the import-side refusal rule.
- **BREAKING for new resource imports only:** untranslated accepted payloads may be retained privately with a translation-pending status; they are not silently translated or made generation-ready. Legacy room imports retain their current translation behavior.

## Capabilities

### New Capabilities

- `resource-store`: complete local source storage, inventory, revisions, translations and field coverage.
- `session-plan`: persistent session choices, ordered takes, explicit changes, review and recovery.
- `resource-prompts`: structured preparation, source mappings, conflict handling and prompt provenance.

### Modified Capabilities

- `asset-refresh`: add a database import destination and preview/commit flow while preserving legacy import behavior.
- `asset-import-guard`: separate private source preservation from translation readiness.
- `perspective-mining`: allow intact source resources; keep decomposition optional and required only for measured component imports.
- `shot-composer`: isolate measured-catalogue prerequisites from the new resource path.
- `wardrobe`: scope existing derived outfit behavior to legacy composition and define explicit continuity for resource plans.

## Impact

Backend: database migrations, import adapters, source handling, session routes and prompt preparation. Frontend: resource browser, session draft and take review integrated with existing model/session screens. Existing generation runner, reference semantics and evidence remain reusable. README and matching session/import documentation must describe the new path and migration limits. No source corpus, personal configuration, names, machine paths or generated images enter tracked artifacts. No new framework or dependency on the external application runtime is planned.
