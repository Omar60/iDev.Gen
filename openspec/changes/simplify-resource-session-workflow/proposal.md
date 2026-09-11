## Why

Resource import and session authoring expose backend paths and empty technical fields instead of helping users turn mined resources into a photographic session. The preparation engine supports immutable inputs and optional synthesis, but the normal HTTP/UI workflow does not connect that synthesis to a configured assistant.

## What Changes

- Make browser file selection the normal source and translation-map input, preserving exact bytes and canonical preview/commit.
- Default library identity from a valid source declaration, with explicit compatibility targeting for existing differently keyed libraries and supported sources without an envelope.
- Guide resources from imported through pending translation to ready. Offer editable assistant translation proposals or manual translations/map selection, always previewed and explicitly applied.
- Reduce initial session inputs to character, scene, photo count (1-500 for guided creation) and optional brief. Default to automatic when configured and put manual editing, variation policy and overrides under Advanced.
- Add a reusable personal look library: create looks in the UI, import/export JSON, or upload a photo for an editable vision-assisted proposal. Separate constant appearance from ordered garment-based outfits.
- Offer explicit, previewed wardrobe progression from a saved outfit, applied as existing scoped take changes while remaining constant by default.
- Persist session and initial plan atomically. Resolve optional look/wardrobe suggestions once in a shared-state summary before preparing takes.
- Use ready structured room resources in the simple automatic path; retain fused descriptions through an explicitly reviewed advanced path without automatic decomposition.
- Connect structured assistant output to existing preparation validation and provenance for unlocked camera, framing, pose and expression.
- Persist incremental progress, prevent concurrent duplicate authoring, support cancellation/recovery and verified snapshot-and-adaptation copy-forward into current revisions, and define downstream invalidation after edits. Detect mutable resource drift at review/submission and provide explicit dependency refresh without creative edits.
- Stop at review and retain explicit approval, submission and generation. Limit automatic batches to twenty takes, not sessions to twenty photos.

Resource Delete/Restore, unrequested wardrobe progression, automatic fused-scene decomposition and a second provider stack are outside this change. Resource lifecycle management can be proposed separately.

## Capabilities

### New Capabilities

- `resource-workflow-ux`: file ingestion, actionable readiness, simple creation and understandable preparation/review controls.

### Modified Capabilities

- `resource-store`: browser identity compatibility, reviewed translation proposals and reusable user-authored look records with portable imports and stable snapshots.
- `session-plan`: atomic guided creation, persisted intent, saved-look snapshots, explicit garment progression, shared-state resolution and dependency-aware recovery.
- `resource-prompts`: structured assistant integration, bounded context, honest conflict handling and replayable provenance.

## Impact

Backend: resource import/service/translation/preparation, session-plan persistence, guided HTTP boundaries and existing assistant transport. Frontend: Resources, resource draft helpers, a personal Looks surface and SessionView. Reuse the existing garment/outfit catalogue and its authored removal order; do not add clothing as a measured-cell dimension. Reuse existing dependencies and preserve legacy creation, catalogue gates, exact revisions and runner/reference semantics.

Implementation updates README and matching resource/session documentation. Tests use invented English fixtures, fake assistants and FakeComfy without GPU, network or running ComfyUI. No private corpus or configuration enters tracked artifacts. All implementation tasks remain pending.
