## Why

The resource planning engine is functionally complete but its normal workflow still exposes implementation details that ordinary users should not have to understand. Resource import requires typing a backend-visible file path and a `library_key`, translation management requires another typed path, and a new `resource-v1` session opens with blank take-level camera, framing, pose and expression fields even though the resource preparation layer already supports both manual completion and optional assistant synthesis for unlocked choices. Resources can also be imported but not deliberately removed from the normal inventory afterward.

The product goal is a guided workflow: users should choose local resource files, review what will be imported, manage imported resources, choose what kind of resource session they want, decide whether take authoring is automatic or manual, review the result, and generate. Paths, revision digests, field roles, preparation provenance and raw take fields remain useful for diagnosis and expert control, but they must not be prerequisites for normal use. The source file itself is authoritative for library identity through its supported top-level `library` value. A configured LLM improves authoring convenience; it is not a prerequisite for creating, editing, reviewing or generating a resource session.

## What Changes

- Replace typed-path-first resource import in the normal Resources UI with browser file selection. Browser-selected source bytes cross a private backend staging boundary so preview and commit operate on the exact same server-controlled content instead of relying on the browser to disclose a local filesystem path.
- Make the supported source envelope's top-level `library` field the authoritative `library_key` for browser-selected imports. The browser flow SHALL NOT derive identity from the file name, silently normalize to a different key, invent suffixes, or ask the user for an external key override. Missing or invalid declared library identity is a preview error. Keep the existing path-based API/CLI workflow and its explicit key contract for expert/local automation.
- Bound browser import operationally: at most 20 files per selection, at most 10 MiB per file, at most 50 MiB total staged source bytes, and a 24-hour staging lifetime. Expired or missing staging state requires a fresh selection/preview.
- Automatically preview selected files and present a user-oriented summary before persistence. Preserve the existing fingerprint/attestation and atomic commit guarantees behind the simplified flow; technical outcome tables remain available as expandable details rather than the primary screen.
- Apply the same file-selection principle to translation maps by reusing the existing direct `translation_map` content contract so normal translation management does not require typing a path visible to the backend. Bound the browser-selected/direct-content map request to 10 MiB.
- Add first-class logical deletion and restoration for imported libraries and individual logical source entries. Deletion removes resources from normal inventory, new selection and new preparation without physically erasing immutable revisions or breaking historical prepared/generated evidence. Restoration reverses that visibility state. Re-importing a source whose declared `library` matches a deleted library reuses and restores that same library identity; it does not create a duplicate library. Individual entry tombstones are not silently cleared by a library re-import.
- Add a guided resource-session creation flow around high-level choices: character, one ready scene anchor, take count from 1 through 20, optional session brief up to 2,000 characters, constant look/wardrobe choices, which creative dimensions may vary, and an explicit authoring mode (`automatic` or `manual`).
- Create the session row and initial authoritative `resource-v1` plan atomically for the guided flow so a failed plan validation/save cannot leave an orphaned guided session. Persist the requested stable take IDs and all authoring state before any assistant call.
- Persist authoring mode, brief, exact scene anchor and per-dimension variation state through backend validation, frontend normalization and plan CAS without loss. Once generated output exists, scene anchor and variation-policy/fixed-value continuity state that could invalidate generated output becomes frozen alongside the existing generated-session constants.
- In automatic mode, fill only unlocked camera, framing, pose and expression choices through the configured prompt assistant using the existing resource preparation contracts, validation and provenance rules. Automatic mode remains a valid persisted draft mode even when no assistant is configured; only synthesis is unavailable until the user configures an assistant or explicitly switches to manual.
- If automatic authoring establishes a previously unresolved fixed creative value, persist both the resolved value and its assistant origin/provenance before per-take preparation begins. Do not let an assistant-established value masquerade as a manual choice.
- Give automatic authoring bounded persisted continuity context: the current brief, scene anchor/fixed state, current take identity and at most the five immediately preceding finalized take-choice summaries in session order. Generated images, arbitrary conversation and full historical prompts are excluded.
- In manual mode, create the same persisted draft without any LLM call and let the user fill the same closed take-choice fields through the existing deterministic preparation path.
- Keep the imported scene anchor, character, look and wardrobe authoritative across both authoring modes. Automatic authoring cannot silently change place/light or other fixed session state.
- Make effective take choices reviewable before generation. Raw per-take editing remains available for manual authoring and deliberate overrides of generated choices.
- Keep review and generation explicit. Automatic or manual authoring SHALL NOT silently approve review, submit shots, start the runner, change fixed character/look/wardrobe/scene choices, or bypass unresolved conflicts.
- Preserve legacy session creation, measured Catalogue/Compose/Judge workflows, immutable resource revisions, prepared-take history, CAS plan revisions, path-based expert import, and current queue/reference semantics.

## Capabilities

### New Capabilities

- `resource-workflow-ux`: simplify local resource ingestion, management and `resource-v1` session authoring while preserving the authoritative lower-level contracts.

### Modified Capabilities

- `resource-store`: add non-destructive logical deletion/restoration semantics while retaining immutable revision history and historical exact-reference resolution.
- `session-plan`: persist the guided authoring contract, generated-state freeze rules and recoverable authoring state.
- `resource-prompts`: bound automatic authoring context and preserve field-level assistant output/provenance, including assistant-established fixed values.

## Dependencies

This change builds on the authoritative `resource-store`, `session-plan` and `resource-prompts` specifications established by the archived `adopt-resource-session-planning` change. Translation-map file selection also builds on the behavior established by the archived `complete-resource-translations` change.

## Impact

Backend: raw browser upload/staging boundary, source-declared library identity validation, simplified import orchestration, resource soft-delete/restore persistence and APIs, atomic guided session/plan creation, persisted authoring state, generated-state freeze checks, optional assistant adapter/orchestration for automatic resource take preparation, bounded context/provenance, and cleanup/recovery behavior for staged files.

Frontend: Resources import/translation/delete/restore controls and the `resource-v1` session creation/editor workflow. Normal screens become task-oriented and expose automatic/manual authoring as explicit choices; technical metadata and raw take fields move behind advanced/detail controls where appropriate.

Testing: backend and frontend coverage for raw file selection payloads, declared library identity, request limits, staged-content fingerprint preservation, deletion/restoration and historical references, atomic guided creation, authoring-state round trips, generated-state freeze behavior, automatic take synthesis, first-class no-LLM manual authoring, bounded context, assistant provenance, recovery, conflict gating and no implicit generation.

Documentation: README and matching resource/session documentation updated to describe browser import, declared library identity, resource deletion/restoration, scene continuity, automatic/manual authoring and retained advanced paths.

No source corpus, local machine paths, personal configuration, generated images or external-service credentials enter tracked artifacts. Automated tests require no GPU, running ComfyUI or network.
