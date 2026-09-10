## Context

`adopt-resource-session-planning` established the correct persistence and preparation model: immutable source revisions, resource readiness, `resource-v1` session plans, stable take IDs, explicit review, deterministic prompt preparation, optional writer synthesis, prepared snapshots and submission through the existing serial runner. `complete-resource-translations` adds operational translation sidecars and a two-phase translation workflow.

The current UI exposes those primitives directly. `Resources.jsx` asks for `File path` and `Library Key`, and translation mapping asks for a path. `buildSessionDraftPayload()` creates one empty take, while `SessionView.jsx` presents camera, framing, pose and expression as ordinary text inputs. The resource preparation layer already treats those four names as the closed set of take-level descriptive choices and can validate assistant output, but the HTTP/UI path does not supply a real configured writer during normal preparation.

A browser file picker cannot solve the import problem by copying a local path into the existing field. Browsers intentionally do not provide a backend-usable absolute filesystem path. The selected `File` bytes must cross an explicit application boundary.

## Goals / Non-Goals

**Goals:**
- Make the primary import path work through browser-selected JSON files with no typed filesystem path.
- Preserve preview/commit integrity, source fingerprinting, atomic persistence and inspectability.
- Infer library identity deterministically where safe and require intervention only for genuine ambiguity.
- Make translation-map selection follow the same local-file UX.
- Let a user create a multi-take resource session from high-level intent without manually authoring camera, framing, pose or expression.
- Reuse the configured OpenAI-compatible prompt-assistant transport rather than introduce a second provider/configuration stack.
- Preserve `resource_preparation` validation, writer provenance and immutable prepared snapshots for automatically authored takes.
- Keep generated choices visible and overridable without making the advanced representation the default UI.
- Keep review, submission and runner start explicit.

**Non-Goals:**
- Import resources automatically on page load.
- Scan arbitrary user directories or execute code from imported resources.
- Remove the existing path-based API/CLI import interface.
- Guess through ambiguous library-key collisions.
- Guarantee exact behavior or rendering parity with an external compiled application.
- Change legacy session composition, measured catalogue semantics, runner concurrency or reference-graph rules.
- Automatically approve, submit or run a session immediately after authoring.
- Require an LLM for the application to remain usable; manual completion remains a supported fallback.

## Decisions

### 1. Browser-selected files cross a staging boundary

The normal UI uses `<input type="file">` with multi-selection for source JSON. The frontend reads the selected files and sends their filename plus contents to a dedicated backend import boundary. The server validates request/file size before parsing and creates private staged inputs under the configured application data directory or another existing private temporary location.

The browser-supplied filename is metadata only. It is never treated as a trusted filesystem path. The backend never attempts to open a client-side path such as a browser `fakepath`.

Staged inputs receive server-generated opaque identifiers and a content digest. Preview operates on the staged bytes. Commit must be bound to the same staged content digest/attestation that preview inspected. If staged content is missing, expired or does not match, commit refuses and requires a fresh selection/preview.

Staging is temporary operational state, not resource history. Successful commit removes eligible staged content. Failed/abandoned staged inputs are cleaned conservatively without touching committed resource revisions.

The existing path-based `ResourceSelectionIn`/service and CLI remain for expert local automation. The simplified upload boundary adapts into the same parser/import service rather than creating a second interpretation of source JSON.

### 2. Library identity is inferred, not requested by default

For each selected source file the backend derives a candidate `library_key` using deterministic evidence in this order:

1. A safe source-declared library identifier exposed by a supported envelope/adapter.
2. The normalized file stem when no supported declared identifier exists.

Inference normalizes to the repository's accepted safe key shape and reports the source of the decision. The UI shows the friendly inferred name but does not require the user to edit it.

If two selected files resolve to the same key but represent incompatible libraries, or an inferred key would collide with existing library identity in a way the import service cannot prove is the same logical library, preview refuses that item as ambiguous. An advanced override lets an expert provide an explicit key and re-preview. The system never silently invents a suffix that would make re-import identity unstable and never silently merges ambiguous inputs.

### 3. Preview/commit stay two-phase while the primary UI becomes one workflow

The safety contract remains two-phase. Selecting or changing source files automatically triggers or enables preview. The primary screen translates the report into user-facing states such as ready to import, unchanged, updated and needs attention.

The commit action is labelled as the user operation (`Import`) rather than exposing implementation terminology. It is enabled only for a fresh valid preview. Full outcome classifications, source IDs, digests, coverage details and attestation-related diagnostics remain available in an expandable technical-details section.

Changing file selection, inferred key override or other preview-affecting input invalidates the prior preview.

### 4. Translation maps use selected contents, not typed paths

The Resources UI replaces the normal translation-map path field with a JSON file picker. Selected translation-map content crosses a private staging/request boundary analogous to source import and is previewed/applied through the existing translation validation and attestation semantics.

The path-based translation API can remain for local automation, but it is not the normal UI prerequisite. Selecting a new translation map invalidates any prior translation preview.

### 5. Resource session creation asks for intent, not prompt internals

Starting a resource session opens a guided creation surface whose normal inputs are:

- character model;
- one or more ready resource revisions supported by the current resource-plan contract;
- take count;
- optional short session brief;
- constant look and initial wardrobe when the resource does not already supply/derive the desired constants;
- variation policy for camera, framing, pose and expression.

The default variation policy allows all four creative dimensions to vary. A disabled dimension is fixed by an explicit user value or by a single assistant-authored choice shared across the session; the implementation must not silently leave a requested fixed dimension inconsistent across takes.

Creating the draft generates exactly the requested number of stable take IDs before lengthy assistant calls and persists the draft first. The existing plan CAS/revision rules remain authoritative.

### 6. Automatic preparation reuses the existing assistant transport and resource contracts

The configured assistant remains the OpenAI-compatible transport already owned by `backend.enhance`; this change does not add another endpoint/provider setting.

Automatic preparation adapts the deterministic request produced from resource preparation into the assistant's structured JSON mode for only the unlocked fields among `camera`, `framing`, `pose` and `expression`. Assistant output is then passed back through the existing closed allowlist, non-empty-string, placeholder and fixed-state validation rules before it can become a prepared snapshot.

The implementation may introduce an orchestration module/endpoint to bridge the asynchronous assistant transport and the resource preparation layer. It must not duplicate or weaken `resource_preparation.validate_writer_output`, fixed-state precedence, conflict handling, final-prompt composition or writer provenance.

Assistant-authored values are stored as assistant synthesis provenance, including the exact bounded input/output required by the existing resource writer contract. They must not be mislabeled as manual completion merely to reuse an endpoint shape.

Automatic authoring may operate take-by-take or in bounded batches, but persistence is incremental: completed prepared takes survive interruption and retry resumes only incomplete/invalidated work using the existing recovery semantics. A finalized ready/generated snapshot is never rewritten merely because authoring is requested again.

### 7. Generated choices are the primary review representation; raw inputs are advanced controls

The normal Takes view summarizes each prepared take from its effective prepared state. Camera, framing, pose and expression are visible for review without appearing as four mandatory blank fields.

An Advanced/Edit action exposes the underlying per-take fields. Saving an explicit override writes the choice into the session plan through existing plan mutation/CAS rules, invalidates preparation according to current resource-plan semantics, and requires re-preparation/re-review where appropriate.

When the assistant is not configured, automatic authoring is unavailable with a clear explanation. The draft remains usable: advanced manual completion can supply the same closed descriptive fields and deterministic preparation continues without network or LLM access.

### 8. Automatic authoring stops before authorization to generate

Successful automatic authoring/preparation transitions the UI to review. It does not call review approval, prepared-take submission or session run endpoints automatically.

Existing blockers remain authoritative: unresolved resource conflicts, unresolved placeholders, missing workflow, dirty plan state, stale revision and incomplete preparation remain visible and prevent progression exactly as they do now.

### 9. Legacy and expert workflows remain isolated

Legacy sessions retain their current creation/editor path, measured Catalogue/Compose/Fill/Judge controls and catalogue gates. The new simple resource workflow never makes those controls prerequisites for `resource-v1`.

Advanced resource inspection continues to expose revision identity, readiness, field roles, translations, coverage and raw payload information. Simplification means changing defaults and hierarchy, not deleting diagnostics needed to understand failures.

## Risks / Trade-offs

- **Uploaded content duplicates path-based import mechanics.** Mitigate by adapting staged bytes into the existing parser/import service and keeping one canonical report/commit implementation.
- **Library inference can merge unrelated resources if too permissive.** Fail closed on ambiguity and expose an explicit advanced override; never silently suffix or merge.
- **Assistant calls can be slow or unavailable.** Persist the plan before calls, prepare incrementally, resume incomplete takes, and keep manual completion.
- **One assistant request for many takes may produce malformed or repetitive output.** Use bounded batching or per-take requests while preserving stable IDs and validate every take independently.
- **Moving technical fields behind advanced controls may hide useful failure evidence.** Keep concise user-facing errors with expandable exact diagnostics.
- **The active prerequisite changes are not yet archived.** Do not redefine their contracts here; archive/order changes carefully so `resource-workflow-ux` lands on their authoritative specification state.

## Migration Plan

1. Add the staged browser-file import boundary and deterministic library inference while retaining path-based import unchanged.
2. Switch the Resources source-import UI to file selection and human-readable preview, retaining technical details and advanced key override.
3. Add translation-map file selection using the same content/staging principle.
4. Add backend resource authoring orchestration using the existing configured assistant transport and resource preparation validation/provenance.
5. Add guided multi-take session creation and automatic preparation/recovery.
6. Rework the resource Takes/Review UI so synthesized choices are primary and manual fields are advanced overrides.
7. Update README and resource/session documentation, then verify legacy flows and all repository gates.

Acceptance demonstration: select multiple invented JSON resource files through the browser without typing paths or library keys, inspect the inferred identities and preview, commit them, start a ready resource with an invented character, request twelve takes with all four creative dimensions varying, interrupt after several completed preparations, resume, inspect the twelve synthesized take choices, override one take manually, re-prepare/review it, and confirm that no shot is submitted or run until the existing explicit review/submission/generation actions are used. Automated verification uses invented English fixtures only and requires no GPU, running ComfyUI or network.
