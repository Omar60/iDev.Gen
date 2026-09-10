## Context

The archived `adopt-resource-session-planning` change established the authoritative persistence and preparation model: immutable source revisions, resource readiness, `resource-v1` session plans, stable take IDs, explicit review, deterministic prompt preparation, optional writer synthesis, prepared snapshots and submission through the existing serial runner. The archived `complete-resource-translations` change established operational translation sidecars and the two-phase translation workflow. Their resulting `resource-store`, `session-plan` and `resource-prompts` specifications are the baseline for this change.

The current UI exposes those primitives directly. `Resources.jsx` asks for `File path` and `Library Key`, and translation mapping asks for a path even though the translation routes already accept direct map content. `buildSessionDraftPayload()` creates one empty take, while `SessionView.jsx` presents camera, framing, pose and expression as ordinary text inputs. The resource preparation layer already treats those four names as the closed set of take-level descriptive choices and supports deterministic manual completion plus optional assistant synthesis, but the normal UI does not present those as two deliberate authoring modes and the HTTP/UI path does not supply a real configured writer for automatic preparation.

A browser file picker cannot solve resource import by copying a local path into the existing field. Browsers intentionally do not provide a backend-usable absolute filesystem path. Selected source bytes must cross an explicit application boundary. Translation maps are different: the backend already accepts direct `translation_map` content and already binds normalized map content into its attestation, so that existing content boundary should be reused rather than duplicated with unnecessary staging.

Session continuity also must not depend on an LLM remembering prior calls. The authoritative plan must persist the session's scene anchor and authoring intent. Automatic mode may vary only unlocked take-level choices while receiving the same fixed scene/look/wardrobe state plus bounded prior-take context. Manual mode uses the same persisted continuity state without any assistant dependency.

## Goals / Non-Goals

**Goals:**
- Make the primary resource import path work through browser-selected JSON files with no typed filesystem path.
- Preserve preview/commit integrity, source fingerprinting, atomic persistence and inspectability without exposing private server staging paths to the browser.
- Infer library identity deterministically where safe and require intervention only for genuine ambiguity.
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
- Remove the existing path-based API/CLI resource import interface.
- Remove the existing path-based translation API needed by local automation.
- Guess through ambiguous library-key collisions.
- Let the assistant silently choose a different scene, place or light between takes.
- Make an LLM a prerequisite for `resource-v1` sessions.
- Guarantee exact behavior or rendering parity with an external compiled application.
- Change legacy session composition, measured catalogue semantics, runner concurrency or reference-graph rules.
- Automatically approve, submit or run a session immediately after authoring.

## Decisions

### 1. Browser-selected resource files cross a private staging boundary

The normal UI uses `<input type="file">` with multi-selection for source JSON. The frontend sends each selected file's display name plus contents to a dedicated backend import boundary. The server validates request/file size before parsing and creates private staged inputs under the configured application data directory or another existing private temporary location.

The browser-supplied filename is metadata only. It is never treated as a trusted filesystem path. The backend never attempts to open a client-side path such as a browser `fakepath`.

Staged inputs receive server-generated opaque identifiers and a content digest. A physical staging path is private server implementation state: it SHALL NOT be returned to the browser, embedded in a browser-facing preview payload, or required on a browser commit request. The browser-facing flow uses only opaque stage/preview identifiers and safe report data.

Preview operates on staged bytes. Commit must be bound to the same staged content digest/attestation that preview inspected. If staged content is missing, expired or does not match, commit refuses and requires a fresh selection/preview. The implementation may keep canonical path-backed preview state server-side or adapt staged inputs into an equivalent byte-backed form, but it must not leak the private staged path merely because the current internal fingerprint model contains a path.

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

The commit action is labelled as the user operation (`Import`) rather than exposing implementation terminology. It is enabled only for a fresh valid preview. Full outcome classifications, source IDs, digests, coverage details and attestation-related diagnostics remain available in an expandable technical-details section, but private server paths do not.

Changing file selection, inferred key override or other preview-affecting input invalidates the prior preview.

### 4. Translation maps reuse the existing direct-content boundary

The Resources UI replaces the normal translation-map path field with a JSON file picker. The frontend reads and parses the selected JSON and sends the resulting content through the existing `translation_map` request shape. The backend's current normalization, field authorization, canonical map digest, HMAC attestation, library fingerprint and stale-preview checks remain authoritative.

No second translation staging subsystem is introduced merely to support the picker. If request-size enforcement needs a route-level guard, it is added around the existing direct-content boundary rather than changing translation identity semantics. The path-based `map_path` API remains for local automation/expert use but is not a normal UI prerequisite.

Selecting or changing a translation map invalidates the previous translation preview. Apply remains bound to the exact normalized map digest and current library fingerprint already carried by the translation attestation.

### 5. Resource session creation persists authoring mode, intent and one scene anchor

Starting a resource session opens a guided creation surface whose normal inputs are:

- character model;
- one ready scene revision used as the session scene anchor;
- requested take count;
- authoring mode: `automatic` or `manual`;
- optional short session brief;
- constant look and initial wardrobe when the resource does not already supply/derive the desired constants;
- variation policy for camera, framing, pose and expression.

The normal flow designates exactly one selected `rooms` or `fused_scenes` revision as the `scene_anchor`. The anchor is an exact immutable `(library_key, source_id, content_digest)` triple and must also be present in the plan's `selected_resources`. Advanced/expert plans may continue to carry additional selected resources under the existing contract, but the normal workflow does not silently combine competing scene-defining revisions or let the assistant replace the scene anchor.

The plan persists a normalized `authoring` block so reload/retry does not depend on transient frontend state. Its authoritative information is:

- `mode`: `automatic` or `manual`;
- `brief`: the optional session brief;
- `scene_anchor`: the exact selected resource triple;
- `variation_policy`: one entry for each of `camera`, `framing`, `pose`, `expression`, declaring whether the dimension varies or is fixed and, when fixed, its resolved value.

The exact JSON representation may be normalized by the implementation, but these semantics must round-trip through backend validation, frontend normalization and plan CAS saves without loss. Changing authoring mode, brief, scene anchor, variation mode or a fixed value is an explicit plan mutation and invalidates affected ungenerated preparation under the existing revision rules.

Draft creation is independent of assistant availability. Both modes generate exactly the requested number of stable take IDs and persist the same scene/fixed state before any optional assistant call. Manual mode proceeds directly to explicit take editing and deterministic preparation. Automatic mode may invoke the configured assistant to fill unlocked take choices.

The default variation policy allows all four creative dimensions to vary. In manual mode, fixed dimensions require explicit values before preparation. In automatic mode, a fixed dimension may also be established once by the assistant. If the assistant establishes a fixed value, the orchestrator persists it through plan CAS before preparing any take under that resolved authoring revision, so later takes and retries read the same value instead of asking the model again.

### 6. Session continuity is structural; automatic authoring supplies bounded variation

The scene anchor, character identity, look, initial/effective wardrobe and other fixed plan state are authoritative across the session in both modes. Place and light represented by the scene anchor remain constant for the normal session. Moving to a different scene/place/light is a new session or an explicit plan revision before generation; it is not an automatic per-take variation.

Manual mode needs no assistant context. The user supplies the closed take-level fields directly and the existing deterministic preparation path consumes them.

Automatic mode uses the assistant only for unlocked `camera`, `framing`, `pose` and `expression` choices. Each request receives a bounded `authoring_context` derived from persisted state rather than conversational memory. The context includes the persisted session brief, scene-anchor identity and authorized descriptive state, current take ID/ordinal/total, variation policy/resolved fixed values, and bounded summaries of already finalized earlier take choices so the model can avoid accidental repetition while maintaining the same session.

Prior-take summaries contain only the closed take-choice fields and stable take IDs, not generated images, full historical prompts or arbitrary conversation. If the implementation limits the number of prior summaries, truncation is deterministic.

The assistant never becomes the source of truth for continuity. A malformed response, retry or different model cannot change stored anchor or fixed state because output remains restricted to unlocked take-choice fields and every accepted result passes existing validation.

### 7. Automatic preparation reuses the existing assistant transport and resource contracts

The configured assistant remains the OpenAI-compatible transport already owned by `backend.enhance`; this change does not add another endpoint/provider setting.

The resource authoring adapter reuses `backend.enhance` connection configuration, authentication, timeout/retry behavior and OpenAI-compatible `/chat/completions` transport. It may add a structured helper that returns validated field objects directly. It SHALL NOT route resource writer output through the historical `clean_fields()` `{label, prompt}` flattening and then attempt to reconstruct `camera`, `framing`, `pose` or `expression` from prose.

Automatic preparation extends the deterministic resource-writer request with the bounded persisted `authoring_context` described above and requests only the unlocked fields among `camera`, `framing`, `pose` and `expression`. Assistant output is passed through the existing closed allowlist, non-empty-string, placeholder and fixed-state validation rules before it can become a prepared snapshot.

Assistant-authored values are stored as assistant synthesis provenance, including the exact bounded writer input/context and validated output. They must not be mislabeled as manual completion merely to reuse an endpoint shape. Manual authoring continues to use the existing manual provenance semantics and never fabricates assistant provenance.

Automatic authoring may operate take-by-take or in bounded batches, but persistence is incremental: completed prepared takes survive interruption and retry resumes only incomplete/invalidated work using persisted authoring state and existing recovery semantics. A finalized ready/generated snapshot is never rewritten merely because authoring is requested again.

### 8. The UI makes both authoring modes usable

The normal creation surface offers Automatic and Manual explicitly. Automatic may be recommended when an assistant is configured, but Manual remains visible and usable regardless of assistant configuration. If no assistant is configured, Automatic is disabled or explains why synthesis is unavailable; this does not block creation in Manual mode.

In automatic mode, the normal Takes view summarizes each prepared take from its effective prepared state. Camera, framing, pose and expression are visible for review without appearing as four mandatory blank fields. An Edit/Advanced action exposes the underlying values for deliberate overrides.

In manual mode, camera, framing, pose and expression are the normal authoring inputs because they are required user work, not hidden expert diagnostics. After deterministic preparation, manual takes use the same effective-state Review presentation as automatic takes.

Saving any explicit override writes the choice into the session plan through existing plan mutation/CAS rules, invalidates preparation according to current resource-plan semantics, and requires re-preparation/re-review where appropriate.

### 9. Authoring mode never authorizes generation

Successful automatic or manual preparation transitions the UI to review. Neither mode calls review approval, prepared-take submission or session run endpoints automatically.

Existing blockers remain authoritative: unresolved resource conflicts, unresolved placeholders, missing workflow, dirty plan state, stale revision and incomplete preparation remain visible and prevent progression exactly as they do now.

### 10. Legacy and expert workflows remain isolated

Legacy sessions retain their current creation/editor path, measured Catalogue/Compose/Fill/Judge controls and catalogue gates. The new simple resource workflow never makes those controls prerequisites for `resource-v1`.

Advanced resource inspection continues to expose revision identity, readiness, field roles, translations, coverage and raw payload information. Existing expert path-based resource import and multi-resource plan capabilities remain available. Simplification means changing defaults and hierarchy, not deleting diagnostics or expert controls needed to understand failures.

## Risks / Trade-offs

- **Uploaded content duplicates path-based import mechanics.** Mitigate by adapting staged bytes into the existing parser/import service and keeping one canonical report/commit implementation.
- **A serialized preview could leak a private staged path if the existing path-based serializer is reused blindly.** Keep stage paths server-side and expose only opaque identifiers plus the path-free report.
- **Library inference can merge unrelated resources if too permissive.** Fail closed on ambiguity and expose an explicit advanced override; never silently suffix or merge.
- **Persisted authoring metadata widens the plan contract.** Keep it normalized, versioned by the existing plan revision, covered by CAS, and test backend/frontend round trips so fields cannot disappear silently.
- **Automatic assistant calls can be slow or unavailable.** Persist the plan before calls, prepare incrementally and resume incomplete takes. Manual mode remains fully usable without an assistant.
- **Independent assistant calls can repeat themselves.** Supply the same authoritative scene state plus bounded prior finalized take choices; never solve repetition by letting the model change the scene anchor.
- **Structured assistant transport currently flattens field output for historical callers.** Add/reuse a field-preserving helper while keeping existing callers unchanged.
- **Different primary controls by authoring mode can drift.** Keep both modes on the same persisted plan and preparation contracts; only the source of take choices differs.

## Migration Plan

1. Add the private staged browser-file resource import boundary while retaining path-based import unchanged.
2. Add deterministic library inference and adapt staged inputs into the canonical preview/commit service without exposing private staging paths.
3. Switch the Resources source-import UI to file selection and human-readable preview, retaining technical details and advanced key override.
4. Switch translation-map UI to selected JSON content using the existing direct `translation_map` contract and attestation semantics.
5. Extend the `session-plan` contract to persist authoring mode, brief, scene anchor and variation policy, then add stable multi-take draft creation independent of assistant availability.
6. Expose first-class manual resource authoring through the existing deterministic/manual-completion preparation path.
7. Add the field-preserving `backend.enhance` adapter/orchestration and extend automatic resource writer input with bounded authoring context.
8. Add automatic multi-take preparation/recovery while keeping scene/fixed-state continuity authoritative.
9. Rework the resource Takes/Review UI so automatic and manual modes each expose the appropriate authoring controls and converge on the same review/generation gates.
10. Update README and resource/session documentation, then verify legacy flows and all repository gates.

Acceptance includes two demonstrations using invented English fixtures only. Automatic: browser-select sources, import after preview, choose one ready scene anchor, create twelve automatic takes from persisted intent, interrupt/resume, verify continuity while take choices vary, override one choice, re-prepare/review and confirm no implicit generation. Manual/no-LLM: with no assistant configured, create a multi-take manual resource session from the same guided creation flow, fill take choices explicitly, prepare/review them and confirm the normal explicit submission/generation path remains fully usable. Neither demonstration requires GPU, running ComfyUI or network for automated verification.
