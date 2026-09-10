## Why

The resource planning engine is functionally complete but its normal workflow still exposes implementation details that ordinary users should not have to understand. Resource import requires typing a backend-visible file path and a `library_key`, translation management requires another typed path, and a new `resource-v1` session opens with blank take-level camera, framing, pose and expression fields even though the resource preparation layer already defines assistant synthesis for unlocked choices.

The result is a technically capable system whose easiest path is still manual. The product goal is the opposite: users should choose local resource files, choose what kind of session they want, review the result, and generate. Paths, revision digests, library keys, field roles, preparation provenance and manual take fields remain useful for diagnosis and expert control, but they must not be prerequisites for normal use.

## What Changes

- Replace typed-path-first resource import in the normal Resources UI with browser file selection. The browser-selected JSON content is transferred to a private backend staging boundary so preview and commit continue to operate on server-controlled bytes instead of relying on the browser to disclose a local filesystem path.
- Infer a stable target library identity when it is unambiguous, preferring an accepted source-declared library identifier where available and otherwise a normalized file stem. Refuse ambiguous collisions instead of silently merging unrelated inputs. Keep explicit `library_key` override available only in advanced import controls and keep the existing path-based API/CLI workflow for expert/local automation.
- Automatically preview selected files and present a user-oriented summary before persistence. Preserve the existing fingerprint/attestation and atomic commit guarantees behind the simplified flow; technical outcome tables remain available as expandable details rather than the primary screen.
- Apply the same file-selection principle to translation maps so normal translation management does not require typing a path visible to the backend.
- Add a guided resource-session creation flow around high-level choices: character, resource selection, take count, optional session brief, constant look/wardrobe choices, and which creative dimensions may vary.
- Create the requested number of stable take IDs automatically and fill unlocked camera, framing, pose and expression choices through the configured prompt assistant using the existing resource preparation contracts, validation and provenance rules.
- Make assistant-authored take choices reviewable before generation. Manual per-take editing remains available under advanced controls and becomes the fallback when no assistant is configured or when a user deliberately overrides a generated choice.
- Keep review and generation explicit. Automatic authoring may create and prepare a draft, but SHALL NOT silently approve review, submit shots, start the runner, change fixed character/look/wardrobe choices, or bypass unresolved conflicts.
- Preserve legacy session creation, measured Catalogue/Compose/Judge workflows, immutable resource revisions, prepared-take history, CAS plan revisions, and current queue/reference semantics.

## Capabilities

### New Capabilities

- `resource-workflow-ux`: simplify local resource ingestion and resource-session authoring while preserving the resource-store, session-plan and resource-prompts contracts introduced by the active resource planning changes.

## Dependencies

This change builds on the active `adopt-resource-session-planning` change and its `resource-store`, `session-plan` and `resource-prompts` contracts. Translation-map file selection also builds on `complete-resource-translations`. These changes should be archived or otherwise treated as the authoritative prerequisite behavior before this change is archived into the main specification set.

## Impact

Backend: resource upload/staging boundary, library identity inference, simplified import orchestration, assistant adapter/orchestration for resource take preparation, and cleanup/recovery behavior for staged files.

Frontend: Resources import/translation controls and the `resource-v1` session creation/editor workflow. Normal screens become task-oriented; technical metadata and manual fields move behind advanced/detail controls.

Testing: backend and frontend coverage for file selection payloads, inferred library identity, collision refusal, staged-content fingerprint preservation, automatic take synthesis, manual fallback, recovery, conflict gating and no implicit generation.

Documentation: README and matching resource/session documentation updated to describe the simple path and the retained advanced path.

No source corpus, local machine paths, personal configuration, generated images or external-service credentials enter tracked artifacts. Automated tests require no GPU, running ComfyUI or network.
