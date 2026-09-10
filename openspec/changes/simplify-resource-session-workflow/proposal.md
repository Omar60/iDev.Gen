## Why

The resource planning engine is functionally complete but its normal workflow still exposes implementation details that ordinary users should not have to understand. Resource import requires typing a backend-visible file path and a `library_key`, translation management requires another typed path, and a new `resource-v1` session opens with blank take-level camera, framing, pose and expression fields even though the resource preparation layer already supports both manual completion and optional assistant synthesis for unlocked choices.

The result is a technically capable system whose normal path still asks every user to operate low-level resource and take fields directly. The product goal is a guided workflow: users should choose local resource files, choose what kind of session they want, decide whether take authoring is automatic or manual, review the result, and generate. Paths, revision digests, library keys, field roles, preparation provenance and raw take fields remain useful for diagnosis and expert control, but they must not be prerequisites for normal use. A configured LLM improves authoring convenience; it is not a prerequisite for creating, editing, reviewing or generating a resource session.

## What Changes

- Replace typed-path-first resource import in the normal Resources UI with browser file selection. The browser-selected JSON content is transferred to a private backend staging boundary so preview and commit continue to operate on server-controlled bytes instead of relying on the browser to disclose a local filesystem path.
- Infer a stable target library identity when it is unambiguous, preferring an accepted source-declared library identifier where available and otherwise a normalized file stem. Refuse ambiguous collisions instead of silently merging unrelated inputs. Keep explicit `library_key` override available only in advanced import controls and keep the existing path-based API/CLI workflow for expert/local automation.
- Automatically preview selected files and present a user-oriented summary before persistence. Preserve the existing fingerprint/attestation and atomic commit guarantees behind the simplified flow; technical outcome tables remain available as expandable details rather than the primary screen.
- Apply the same file-selection principle to translation maps by reusing the existing direct `translation_map` content contract so normal translation management does not require typing a path visible to the backend.
- Add a guided resource-session creation flow around high-level choices: character, one ready scene anchor, take count, optional session brief, constant look/wardrobe choices, which creative dimensions may vary, and an explicit authoring mode (`automatic` or `manual`).
- Create the requested number of stable take IDs independently of assistant availability. In automatic mode, fill unlocked camera, framing, pose and expression choices through the configured prompt assistant using the existing resource preparation contracts, validation and provenance rules. In manual mode, create the same persisted draft and let the user fill the same closed take-choice fields without any LLM call.
- Keep the imported scene anchor, character, look and wardrobe authoritative across both authoring modes. Automatic authoring receives bounded persisted context for continuity but cannot silently change scene/place/light or other fixed session state.
- Make effective take choices reviewable before generation. Raw per-take editing remains available under advanced controls for manual authoring and deliberate overrides of generated choices.
- Keep review and generation explicit. Automatic authoring may prepare a draft, but SHALL NOT silently approve review, submit shots, start the runner, change fixed character/look/wardrobe/scene choices, or bypass unresolved conflicts. Manual authoring uses the same review/submission/generation gates.
- Preserve legacy session creation, measured Catalogue/Compose/Judge workflows, immutable resource revisions, prepared-take history, CAS plan revisions, and current queue/reference semantics.

## Capabilities

### New Capabilities

- `resource-workflow-ux`: simplify local resource ingestion and resource-session authoring while preserving the authoritative `resource-store`, `session-plan` and `resource-prompts` contracts.

## Dependencies

This change builds on the authoritative `resource-store`, `session-plan` and `resource-prompts` specifications established by the archived `adopt-resource-session-planning` change. Translation-map file selection also builds on the behavior established by the archived `complete-resource-translations` change.

## Impact

Backend: resource upload/staging boundary, library identity inference, simplified import orchestration, persisted authoring mode/intent, optional assistant adapter/orchestration for automatic resource take preparation, and cleanup/recovery behavior for staged files.

Frontend: Resources import/translation controls and the `resource-v1` session creation/editor workflow. Normal screens become task-oriented and expose automatic/manual authoring as explicit choices; technical metadata and raw take fields move behind advanced/detail controls where appropriate.

Testing: backend and frontend coverage for file selection payloads, inferred library identity, collision refusal, staged-content fingerprint preservation, automatic take synthesis, first-class no-LLM manual authoring, recovery, conflict gating and no implicit generation.

Documentation: README and matching resource/session documentation updated to describe browser import, scene continuity, automatic/manual authoring and retained advanced paths.

No source corpus, local machine paths, personal configuration, generated images or external-service credentials enter tracked artifacts. Automated tests require no GPU, running ComfyUI or network.
