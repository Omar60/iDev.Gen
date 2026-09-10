## Context

The archived `adopt-resource-session-planning` change established the authoritative persistence and preparation model: immutable source revisions, resource readiness, `resource-v1` session plans, stable take IDs, explicit review, deterministic prompt preparation, optional writer synthesis, prepared snapshots and submission through the existing serial runner. The archived `complete-resource-translations` change established operational translation sidecars and the two-phase translation workflow. Their resulting `resource-store`, `session-plan` and `resource-prompts` specifications are the baseline for this change.

The current UI exposes those primitives directly. `Resources.jsx` asks for `File path` and `Library Key`, and translation mapping asks for a path even though the translation routes already accept direct map content. `buildSessionDraftPayload()` creates one empty take, while `SessionView.jsx` presents camera, framing, pose and expression as ordinary text inputs. The resource preparation layer already treats those four names as the closed set of take-level descriptive choices and supports deterministic manual completion plus optional assistant synthesis, but the normal UI does not present those as two deliberate authoring modes and the HTTP/UI path does not supply a real configured writer for automatic preparation.

The supported source envelope already carries its logical library identity in the top-level `library` field. For the browser-selected workflow that content identity is authoritative; a browser file name is not a source of library identity and an externally typed `library_key` would introduce a second source of truth.

A browser file picker cannot solve resource import by copying a local path into the existing field. Browsers intentionally do not provide a backend-usable absolute filesystem path. Selected source bytes must cross an explicit application boundary. Translation maps are different: the backend already accepts direct `translation_map` content and already binds normalized map content into its attestation, so that existing content boundary should be reused rather than duplicated with unnecessary staging.

Imported resource revisions are intentionally immutable and may be referenced by session plans, prepared snapshots and generated evidence. A user-visible Delete action therefore cannot be implemented as destructive deletion of accepted revisions without undermining reproducibility. Resource removal must be modeled as mutable inventory visibility state around immutable history.

Session continuity also must not depend on an LLM remembering prior calls. The authoritative plan must persist the session's scene anchor and authoring intent. Automatic mode may vary only unlocked take-level choices while receiving the same fixed scene/look/wardrobe state plus bounded prior-take context. Manual mode uses the same persisted continuity state without any assistant dependency.

## Goals / Non-Goals

**Goals:**
- Make the primary resource import path work through browser-selected JSON files with no typed filesystem path or typed library identity.
- Use the supported source file's top-level `library` value as the authoritative browser-import `library_key`.
- Preserve exact source bytes, preview/commit integrity, source fingerprinting, atomic persistence and inspectability without exposing private server staging paths to the browser.
- Give imported resources a safe Delete/Restore lifecycle without deleting immutable revision history or breaking historical evidence.
- Make translation-map selection follow the same local-file UX by reusing the existing direct-content translation contract.
- Let a user create a multi-take resource session from high-level intent and choose explicitly between automatic and manual take authoring.
- Keep complete resource-session creation, editing, preparation, review and generation available with no configured LLM.
- Persist enough authoring state to resume either authoring path after interruption without reconstructing intent from transient UI state.
- Keep one explicit scene anchor authoritative for the normal session workflow so place/light continuity does not depend on assistant behavior.
- Reuse the configured OpenAI-compatible prompt-assistant transport for automatic authoring rather than introduce a second provider/configuration stack.
- Preserve `resource_preparation` validation, manual completion semantics, writer provenance and immutable prepared snapshots.
- Keep effective take choices visible and overridable while presenting the appropriate authoring controls for the selected mode.
- Keep review, submission and runner start explicit.

**Non-Goals:**
- Import resources automatically on page load.
- Scan arbitrary user directories or execute code from imported resources.
- Derive browser-import library identity from a file name, browser path or UI-entered key.
- Remove the existing path-based API/CLI resource import interface used by expert/local automation.
- Remove the existing path-based translation API needed by local automation.
- Physically purge accepted resource revisions or prepared/generated history.
- Let the assistant silently choose a different scene, place or light between takes.
- Make an LLM a prerequisite for `resource-v1` sessions.
- Guarantee exact behavior or rendering parity with an external compiled application.
- Change legacy session composition, measured catalogue semantics, runner concurrency or reference-graph rules.
- Automatically approve, submit or run a session immediately after authoring.

## Decisions

### 1. Browser-selected resource files cross a private raw-byte staging boundary

The normal UI uses `<input type="file">` with multi-selection for source JSON. The frontend sends each selected file as raw bytes plus a display label through the existing raw-upload style used elsewhere by the application. The frontend SHALL NOT parse and reserialize source JSON before staging because whitespace/encoding changes would break the requirement that preview and commit bind to the same source content.

The browser-supplied filename is metadata only. It is never treated as a trusted filesystem path and never participates in library identity. The backend never attempts to open a client-side path such as a browser `fakepath`.

Browser source selection is bounded to 20 files, 10 MiB per file and 50 MiB total staged source bytes for one import selection. The backend enforces the authoritative limits; frontend validation may fail earlier for usability but is not a security/integrity boundary. Oversized input is refused before expensive parsing or persistence with a clear client-facing limit error.

Staged inputs receive server-generated opaque identifiers and a content digest. A physical staging path is private server implementation state: it SHALL NOT be returned to the browser, embedded in a browser-facing preview payload, or required on a browser commit request. The browser-facing flow uses only opaque stage/preview identifiers and safe report data.

Preview operates on staged bytes. Commit must be bound to the same staged content digest/attestation that preview inspected. If staged content is missing, expired or does not match, commit refuses and requires a fresh selection/preview. The implementation may keep canonical path-backed preview state server-side or adapt staged inputs into an equivalent byte-backed form, but it must not leak the private staged path merely because the current internal fingerprint model contains a path.

Staging is temporary operational state, not resource history. Its lifetime is 24 hours, aligned with the existing preview-attestation lifetime unless a shorter lifetime is already required by repository policy. Successful commit removes eligible staged content. Expired staged state is cleaned conservatively. Cleanup must never touch committed resource revisions.

The existing path-based `ResourceSelectionIn`/service and CLI remain for expert local automation. The simplified upload boundary adapts into the same parser/import service rather than creating a second interpretation of source JSON.

### 2. The source-declared `library` value is authoritative for browser import

For every browser-selected source file, the backend parses the supported envelope far enough to obtain its top-level `library` field before canonical preview proceeds. That declared value is the sole source of `library_key` in the browser workflow.

The declared value must be a string of 1 through 128 characters, must have no leading/trailing whitespace, ASCII control characters, `/` or `\\`, and must not be `.` or `..`. The accepted value is then used exactly as declared: no case folding, slugification, file-stem fallback, automatic suffixing or other silent identity rewrite occurs.

If `library` is missing, empty, malformed or invalid, browser preview refuses the file with a specific identity error. The user fixes the source file rather than supplying a second external identity. The normal and advanced browser UI therefore have no `library_key` override.

Two selected files declaring the same valid `library` value explicitly identify the same logical library. Canonical duplicate-identifier/content rules still determine whether their entries can coexist; the browser-import identity layer does not invent separate libraries to avoid those canonical conflicts.

Re-import stability follows exact declared identity. A later browser import declaring the same `library` targets the same `resource_library` identity. A different declared value is a different library identity even if the file name is unchanged.

The existing path-based API/CLI keeps its current explicit `library_key` contract for compatibility. This change does not retroactively redefine legacy callers or make their keys derive from file content.

### 3. Preview/commit stay two-phase while the primary UI becomes one workflow

The safety contract remains two-phase. Selecting or changing source files automatically triggers or enables preview. The primary screen translates the report into user-facing states such as ready to import, unchanged, updated, restored library and needs attention.

The commit action is labelled as the user operation (`Import`) rather than exposing implementation terminology. It is enabled only for a fresh valid preview. Full outcome classifications, source IDs, digests, coverage details and attestation-related diagnostics remain available in an expandable technical-details section, but private server paths do not.

Changing file selection or any staged content invalidates the prior preview. Because library identity is content-declared, there is no browser-side key edit that can mutate preview identity.

The browser-safe preview payload is a distinct transport representation from the internal canonical preview whenever the latter contains physical paths. The browser may receive opaque preview identity and safe report data; commit resolves authoritative canonical preview state server-side.

### 4. Translation maps reuse the existing direct-content boundary

The Resources UI replaces the normal translation-map path field with a JSON file picker. The frontend reads and parses the selected JSON and sends the resulting content through the existing `translation_map` request shape. The backend's current normalization, field authorization, canonical map digest, HMAC attestation, library fingerprint and stale-preview checks remain authoritative.

The browser-selected/direct-content translation request is limited to 10 MiB at the HTTP boundary. No second translation staging subsystem is introduced merely to support the picker. The path-based `map_path` API remains for local automation/expert use but is not a normal UI prerequisite.

Selecting or changing a translation map invalidates the previous translation preview. Apply remains bound to the exact normalized map digest and current library fingerprint already carried by the translation attestation.

### 5. Resource deletion is logical inventory state, not revision destruction

The Resources surface supports Delete and Restore for an imported logical library and for an individual logical source entry. Accepted `asset_revision` rows remain immutable and are not physically removed by these operations.

Library deletion is represented by mutable deletion state on the logical library, such as nullable `resource_library.deleted_at`. Individual source-entry deletion is represented separately from immutable revisions, for example by a small logical state/tombstone relation keyed by `(library_id, source_id)` with nullable deletion time. The exact schema may vary, but deletion SHALL NOT be implemented by rewriting or deleting historical revision payloads.

Normal inventory, new resource selection and any new preparation that would consume a deleted library/entry exclude or block logically deleted resources. Existing session plans and prepared/generated history retain their exact triples. Deleted revisions remain resolvable for historical inspection and replay of already finalized prepared/generated snapshots. An existing draft that has not yet finalized preparation and references a now-deleted resource is blocked with a readable deleted-resource reason until that resource is restored; deletion does not silently substitute another revision.

Delete and Restore are transactional, idempotent operations. Deleting a library hides all of its entries through parent state without manufacturing per-entry tombstones. Restoring the library clears only library deletion state; any entries that were individually deleted before or after library deletion remain individually deleted until explicitly restored.

If a browser import declares a `library` matching a soft-deleted library, preview reports that the existing library will be restored/reused. Successful atomic import clears the library deletion state and commits any accepted revisions in the same transaction. It never creates a second library row with the same key. Re-import does not silently clear individual source-entry tombstones; those remain hidden and are reported as such until explicitly restored.

A soft-deleted target counts as deleted for translation preview/apply conflict semantics. Hard purge is outside this change.

### 6. Guided session creation persists one authoritative plan atomically

Starting a resource session opens a guided creation surface whose normal inputs are:

- character model;
- one ready, non-deleted scene revision used as the session scene anchor;
- requested take count from 1 through 20;
- authoring mode: `automatic` or `manual`;
- optional session brief up to 2,000 characters;
- constant look and initial wardrobe when the resource does not already supply/derive the desired constants;
- variation policy for camera, framing, pose and expression.

The normal flow designates exactly one selected `rooms` or `fused_scenes` revision as the `scene_anchor`. The anchor is an exact immutable `(library_key, source_id, content_digest)` triple, must also be present in `selected_resources`, must resolve to a current stored revision and must be ready and non-deleted before guided creation succeeds. Advanced/expert plans may continue to carry additional selected resources under the existing contract, but the normal workflow does not silently combine competing scene-defining revisions or let the assistant replace the scene anchor.

The guided creation backend validates all initial inputs before any assistant call and creates the session plus its initial `resource-v1` plan in one authoritative transactional operation. A failed validation or plan persistence must leave neither a partially initialized guided session nor an orphaned session row. Existing legacy session-creation routes remain unchanged.

The transaction commits exactly the requested number of stable take IDs and authoring state. Assistant synthesis begins only after successful persistence and therefore always operates against a real current plan revision.

### 7. The plan owns authoring mode, intent, fixed-value origin and generated-state continuity

The plan persists a normalized `authoring` block. Its semantic shape is:

```json
{
  "mode": "automatic | manual",
  "brief": "optional string",
  "scene_anchor": {
    "library_key": "...",
    "source_id": "...",
    "content_digest": "..."
  },
  "variation_policy": {
    "camera": {"mode": "vary"},
    "framing": {"mode": "vary"},
    "pose": {"mode": "fixed", "value": "...", "value_origin": "user | assistant"},
    "expression": {"mode": "vary"}
  }
}
```

All four variation dimensions are present exactly once. `vary` entries carry no fixed value. A user-supplied fixed value records `value_origin: "user"`. In automatic mode a fixed dimension may begin unresolved; before per-take preparation the assistant may establish it once. An assistant-established fixed value records `value_origin: "assistant"` plus enough persisted resolution provenance to retain the exact bounded assistant input/context and validated output used to establish that value. The implementation may normalize the exact provenance nesting, but it must be persisted before take preparation and must not be mislabeled as manual.

Manual guided creation requires an explicit value for every fixed dimension. Automatic guided creation may persist an unresolved fixed dimension only when it is eligible for assistant resolution. If no assistant is configured, such unresolved fixed state blocks synthesis with a readable reason but does not invalidate the persisted automatic draft.

The default variation policy is `vary` for all four dimensions. The session brief is optional; when present it is at most 2,000 characters. The exact authoring semantics must round-trip through backend validation, frontend normalization and plan CAS saves without loss.

Changing `authoring.mode`, brief, scene anchor, variation mode or fixed value is an explicit plan CAS mutation. Existing invalidation rules apply to affected ungenerated preparations. Once any take for the session has reached immutable generated state, the scene anchor and variation-policy/fixed-value state are frozen along with the existing generated-session constants because changing them could contradict already generated output. Authoring mode and brief may still change for future ungenerated authoring only when the existing plan/invalidation rules can do so without rewriting generated snapshots.

The assistant cannot mutate character identity, selected resources, scene anchor, look, wardrobe, adaptations or fixed values as a side effect of per-take output.

### 8. Session continuity is structural; automatic authoring supplies bounded variation

The scene anchor, character identity, look, initial/effective wardrobe and other fixed plan state are authoritative across the session in both modes. Place and light represented by the scene anchor remain constant for the normal session. Moving to a different scene/place/light is a new session or an explicit plan revision before generation; it is not an automatic per-take variation.

Manual mode needs no assistant context. The user supplies the closed take-level fields directly and the existing deterministic preparation path consumes them.

Automatic mode uses the assistant only for unlocked `camera`, `framing`, `pose` and `expression` choices. Each request receives a bounded `authoring_context` derived from persisted state rather than conversational memory. The context includes the persisted session brief, scene-anchor identity and authorized descriptive state, current take ID/ordinal/total, variation policy/resolved fixed values, and summaries of at most the five immediately preceding finalized earlier takes in stable session order.

Prior-take summaries contain only stable take ID plus the closed camera/framing/pose/expression choices. Generated images, arbitrary conversation, full historical prompts and unrelated session history are excluded. When more than five earlier finalized takes exist, the five most recent preceding takes in plan order are selected deterministically.

The assistant never becomes the source of truth for continuity. A malformed response, retry or different model cannot change stored anchor or fixed state because output remains restricted to unlocked take-choice fields and every accepted result passes existing validation.

### 9. Automatic preparation reuses the existing assistant transport and resource contracts

The configured assistant remains the OpenAI-compatible transport already owned by `backend.enhance`; this change does not add another endpoint/provider setting.

The resource authoring adapter reuses `backend.enhance` connection configuration, authentication, timeout/retry behavior and OpenAI-compatible `/chat/completions` transport. It adds or reuses a field-preserving structured helper that returns structured field objects directly. It SHALL NOT route resource writer output through the historical `clean_fields()` `{label, prompt}` flattening and then reconstruct `camera`, `framing`, `pose` or `expression` from prose.

Because the assistant transport is asynchronous while the existing deterministic resource writer contract is synchronous, the orchestration boundary must be explicit. The implementation may add an async resource-authoring service/endpoint that calls the field-preserving assistant transport and then passes structured values through the existing resource preparation validation/finalization primitives. It must not duplicate the HTTP transport or weaken `resource_preparation` validation merely to bridge async/sync code.

Automatic preparation requests only currently unlocked fields. Assistant output passes through the existing closed allowlist, non-empty-string, placeholder and fixed-state validation rules before it can become a prepared snapshot.

Assistant-authored per-take values retain assistant synthesis provenance including the exact bounded writer input/context and validated output. Assistant-established fixed values retain equivalent resolution provenance in authoritative persisted authoring state before any take consumes them. Manual authoring continues to use existing manual provenance semantics and never fabricates assistant provenance.

Automatic authoring may operate take-by-take or in bounded batches, but persistence is incremental: completed prepared takes survive interruption and retry resumes only incomplete/invalidated work using persisted authoring state and existing recovery semantics. A finalized ready/generated snapshot is never rewritten merely because authoring is requested again.

### 10. Automatic and Manual remain first-class persisted modes

The normal creation surface offers Automatic and Manual explicitly. Manual remains visible and usable regardless of assistant configuration.

Automatic is a valid persisted plan mode independently of current assistant availability. The UI may indicate that synthesis is unavailable and disable the synthesis action when no assistant is configured, but it must not reject or rewrite an otherwise valid automatic draft merely because configuration is absent. The user can later configure the assistant or explicitly switch the same plan to Manual through CAS.

In automatic mode, the normal Takes view summarizes each prepared take from its effective prepared state. Camera, framing, pose and expression are visible for review without appearing as four mandatory blank fields. An Edit/Advanced action exposes the underlying values for deliberate overrides.

In manual mode, camera, framing, pose and expression are the normal authoring inputs because they are required user work. After deterministic preparation, manual takes use the same effective-state Review presentation as automatic takes.

Saving any explicit override writes the choice into the session plan through existing plan mutation/CAS rules, invalidates preparation according to current resource-plan semantics, and requires re-preparation/re-review where appropriate.

### 11. Authoring never authorizes generation

Successful automatic or manual preparation transitions the UI to review. Neither mode calls review approval, prepared-take submission or session run endpoints automatically.

Existing blockers remain authoritative: unresolved resource conflicts, unresolved placeholders, missing workflow, dirty plan state, stale revision, deleted referenced resources where new preparation is required, and incomplete preparation remain visible and prevent progression exactly as their lower-level contracts require.

### 12. Legacy and expert workflows remain isolated

Legacy sessions retain their current creation/editor path, measured Catalogue/Compose/Fill/Judge controls and catalogue gates. The new simple resource workflow never makes those controls prerequisites for `resource-v1`.

Advanced resource inspection continues to expose revision identity, readiness, field roles, translations, coverage and raw payload information. Existing expert path-based resource import and multi-resource plan capabilities remain available. Simplification changes defaults and hierarchy; it does not delete diagnostics or expert controls needed to understand failures.

## Risks / Trade-offs

- **Raw uploaded content duplicates path-based import mechanics.** Mitigate by adapting staged bytes into the existing parser/import service and keeping one canonical report/commit implementation.
- **A serialized preview could leak a private staged path if the existing path-based serializer is reused blindly.** Keep stage paths server-side and expose only opaque identifiers plus path-free report data.
- **Source-declared identity may be malformed.** Fail closed and require the source JSON to be corrected; do not invent a browser-side identity fallback.
- **Soft deletion adds mutable state around immutable revisions.** Keep deletion metadata separate from revision payloads and test every normal lookup versus historical exact lookup deliberately.
- **Re-import can surprise users if it silently restores hidden entries.** Re-import restores only a matching deleted library; individual entry tombstones remain explicit until Restore.
- **Persisted authoring metadata widens the plan contract.** Keep it normalized, covered by CAS, and test backend/frontend round trips so fields cannot disappear silently.
- **Automatic assistant calls can be slow or unavailable.** Persist the plan before calls, prepare incrementally and resume incomplete takes. Manual mode remains fully usable without an assistant.
- **Independent assistant calls can repeat themselves.** Supply the same authoritative scene state plus the previous five finalized take choices; never solve repetition by letting the model change the scene anchor.
- **Structured assistant transport currently flattens field output for historical callers.** Add/reuse a field-preserving helper while keeping existing callers unchanged.
- **Atomic guided creation touches both session and plan creation.** Put the new guided boundary behind a dedicated transactional service/endpoint and leave legacy creation unchanged.
- **Different primary controls by authoring mode can drift.** Keep both modes on the same persisted plan and preparation contracts; only the source of take choices differs.

## Migration Plan

1. Add the private raw-byte staged browser-file resource import boundary and exact request/staging limits while retaining path-based import unchanged.
2. Make top-level source `library` authoritative for browser imports, validate it fail-closed, and adapt staged inputs into canonical preview/commit without filename-derived identity or browser key override.
3. Switch the Resources source-import UI to multi-file selection and a path-free user-oriented preview.
4. Add resource library/source-entry soft-delete state, delete/restore APIs and filtered inventory/historical-resolution behavior, including deleted-library re-import restoration semantics.
5. Switch translation-map UI to selected JSON content using the existing direct `translation_map` contract and 10 MiB request limit.
6. Extend the `session-plan` contract to persist normalized authoring mode, brief, scene anchor, variation policy, fixed-value origin/provenance and generated-state freeze semantics.
7. Add the atomic guided session+initial-plan creation boundary with 1-20 take validation and stable take IDs before assistant work.
8. Expose first-class manual resource authoring through the existing deterministic/manual-completion preparation path.
9. Add the field-preserving `backend.enhance` adapter/orchestration, assistant fixed-value resolution provenance and the bounded five-take authoring context.
10. Add automatic multi-take preparation/recovery while keeping scene/fixed-state continuity authoritative.
11. Rework Resources/Takes/Review UI so import, delete/restore, automatic and manual paths expose the appropriate normal controls and converge on the same explicit review/generation gates.
12. Update README and resource/session documentation, then verify legacy flows and all repository gates.

Acceptance includes three demonstrations using invented English fixtures only. Browser resource management: select source files that declare `library`, preview/import without typed paths or keys, delete and restore an entry and library, confirm normal selection excludes deleted state, re-import a deleted library and confirm the same logical library is restored while immutable historical revisions remain. Automatic: choose one ready scene anchor, create twelve automatic takes atomically from persisted intent, interrupt/resume, verify continuity while take choices vary with at most five prior summaries, override one choice, re-prepare/review and confirm no implicit generation. Manual/no-LLM: with no assistant configured, create a multi-take manual resource session from the same guided creation flow, fill take choices explicitly, prepare/review them and confirm the normal explicit submission/generation path remains fully usable. Neither demonstration requires GPU, running ComfyUI or network for automated verification.
