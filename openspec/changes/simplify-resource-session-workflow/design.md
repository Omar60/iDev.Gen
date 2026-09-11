## Context

Resources currently sends paths and explicit library keys. Creation writes session and one empty-take plan separately. Preparation routes pass no writer. The preparation writer is synchronous; backend.enhance is asynchronous and its historical structured output is flattened into prose.

The parser supports envelopes, lists, individual entries and auxiliary maps. Existing keys are caller-supplied. Required descriptive fields need authorized translations even when source text is English. Fused prose remains a complete descriptive input and may already specify pose, camera and wardrobe. Structural output validation cannot prove semantic continuity.

## Goals / Non-Goals

**Goals:** a complete file-to-reviewed-session journey, few normal inputs, actionable failures, existing contracts and reproducible results.

**Non-Goals:** resource Delete/Restore or purge; wardrobe progression without explicit user selection and approval; automatic fused-scene decomposition; semantic guarantees based on JSON keys; a second provider stack; implicit generation; a twenty-photo session cap.

## Decisions

### 1. Selected bytes reuse canonical import

Use a native multi-file input and raw uploads. Create a server-owned selection_id before uploads; its manifest owns file IDs, byte counts, effective targets, selection_revision and fixed expires_at. The lifecycle is open selection -> uploaded files -> preview -> committed or expired/cancelled. Reserve file count and bytes atomically across concurrent uploads, count actual streamed bytes, and roll back reservations for rejected/incomplete uploads. Every upload/removal/target change increments selection_revision and invalidates its preview. Preview and commit bind selection_id, revision, complete manifest digests and targets; cross-selection file IDs are rejected. Repeated commit returns the recorded result rather than importing twice. Atomically claim the selection/revision before canonical commit; two simultaneous requests cannot both enter import. The second receives active-operation status or the same committed result. Persist consumption/result consistently with import success; recovery after a crash must discover committed outcome rather than re-enter canonical commit. A rollback may release ownership for a fresh attempt but never allow simultaneous owners. The browser ignores responses for a superseded selection/revision. Expiry is 24 hours from selection creation, not extended by each upload. Stage exact bytes behind opaque server identifiers; never open a client path. Enforce 20 files, 10 MiB per file and 50 MiB per selection before expensive parsing, including aggregate limits across uploads. Expire staging after 24 hours and clean eligible content after commit. Keep physical stage paths out of browser payloads.

Reuse the canonical parser, fingerprints, attestation, duplicate accounting and atomic accepted-set commit. Keep path-backed canonical reports server-side with a safe browser projection. File/target changes invalidate preview; late preview responses cannot re-enable Import for a newer selection.

Rejected alternatives: fakepath forwarding, frontend source parse/reserialize, and a second importer.

### 2. Identity defaults with explicit compatibility

Use valid top-level library as the normal target. Keys have 1-128 characters, no surrounding whitespace, ASCII controls, slash or backslash, and are not dot or dot-dot. Preserve spelling exactly. Filenames never determine identity.

An existing exact key is authoritative. If that key does not exist but canonical source ID/digest pairs overlap another stored library, preview requires explicit selection of an existing target or creation of a separate library. Multiple candidates remain an explicit choice. Similar names do not authorize merging.

Supported lists, single entries and auxiliary maps without an envelope remain uploadable. Missing/invalid declarations expose an advanced existing-library selector or explicit new key; do not force editing JSON. Choosing a historical target never renames it or mutates the source. Bind declared and effective identity to preview/commit and show the distinction. No persistent alias registry is needed. The browser adapter also recognizes envelope-like objects with an items list without a usable library field after explicit target selection. Reuse the is_source_envelope _ENTRY_CONTENT_MARKERS boundary and, for this relaxed recognition only, allow no top-level keys except items and optional library; unknown keys such as random_payload remain unresolved. Already-supported canonical envelopes retain existing parser behavior. Extract items in memory for parsing while retaining original staged bytes/fingerprint and parser accounting. Ambiguous objects also carrying entry-content fields remain unresolved; do not silently discard them. Apply the same adapter at preview and canonical commit revalidation without rewriting source files or changing legacy parser/API defaults.

For auxiliary payloads matching multiple kinds (notably mined_families/mined_labels), require an explicit Advanced kind selection from the structurally matched candidates. Do not use first-shape-wins or filenames as authority. Record the chosen kind in the selection manifest/attestation and revalidate it against the same bytes at commit. Unsupported choices fail. Canonical service plumbing may accept explicit validated adapter context, but old callers without that context keep their current classification. This must preserve the existing attestation revalidation rather than patch only the browser report.

This compatibility exception replaces the previous unconditional ban on browser overrides. Unchanged path API/CLI remains available.

### 3. Readiness provides a next action

Show imported, needs translation, ready and needs source correction separately. Name blocked fields and link to their action.

Selected translation maps and editable source-backed rows both produce a direct translation_map and use the existing library bulk preview/apply endpoints with attestation. The single-revision translation endpoint is not an allowed shortcut for this UI. Enforce 10 MiB on total HTTP body bytes before JSON/Pydantic parsing using a bounded receive/stream boundary; check Content-Length early when present but count actual bytes even when missing or false. Reject excess with 413 before parsing or writes. Editable rows provide a no-LLM path. Suggest translations optionally uses the configured assistant for authorized descriptive fields only, preserving canonical keys and list order. Already-English required strings still need explicit sidecar entries; proposals may provide identity translations.

Proposals never write sidecars. Users edit/review, preview through canonical authorization, then explicitly apply against map digest and library fingerprint. Batch proposals at twenty source entries per action. Invalid/unsolicited fields are refused; stale responses after selection/library changes are discarded. Unknown mappings and malformed source fields require correction rather than a translation guess. Import commit never starts translation calls.

### 4. Four initial inputs and advanced options

Show character, one ready room scene, integer photo count from 1 through 500 (default twelve) and optional brief up to 2,000 characters. Preselect known character/scene. Default automatic when configured, otherwise manual with an inline Configure assistant action; users may explicitly select either mode.

Advanced contains look/wardrobe overrides, fixed/vary policy and raw take editing. Internally default all four dimensions to vary. A fixed dimension requires a non-empty user value. Assistant resolution of unspecified fixed dimensions is deferred; it is unnecessary for the default flow.

Twenty is the maximum takes per automatic preparation action, not a plan count cap. Larger sessions continue in consecutive batches. The 500-photo creation budget prevents accidental allocation of huge initial plans; validate it before building take IDs. Apply the same cap to new/growing authoring-v1 plans, while existing legacy/expert plans lacking authoring retain their established limits and remain readable/editable. Reject booleans, fractions and numeric strings as counts. Preserve valid existing manual/expert plans without authoring metadata.

### 5. Shared state is established once

Implement the closed authoring-v1 wire contract in specs/session-plan/spec.md, including nullable authoring.look_snapshot and authoring.wardrobe_progression from task 3.1. Both backend validation and frontend normalization currently reconstruct the old shape and must preserve every specified member. Stored evidence is server-owned; request echoes do not authorize fabrication or edits. Pending suggestions remain operation state, not accepted plan fields. Generic POST /plan may only echo evidence, look_snapshot and wardrobe_progression unchanged. Dedicated CAS operations accept a suggestion by server-issued operation ID and edited text, apply a saved preset by key/version, or apply a server-computed progression preview. They alone create/replace server-owned blocks after validation. Generic explicit edits to look/initial_wardrobe may update those strings; the server recomputes their origin metadata as user and keeps prior evidence historical. It does not accept client-invented origins. Frontend normalization of persisted plans must preserve empty take arrays and invalid/missing take IDs for validation, never fabricate take-001. IDs are allocated only by guided creation or explicit Add take. The simple automatic anchor is one ready stored rooms revision also present in selected_resources.

Character identity comes from the chosen model. Authorized scene descriptions supply place/light; never split opaque prose. Explicit look/initial wardrobe take precedence. Empty overrides mean no added constraint, not erasure of source descriptions.

An optional Suggest shared choices action proposes missing look/wardrobe once through the configured assistant using model context, authorized scene descriptions and brief. Show suggestions before accepting through plan CAS. Record exact assistant input/output, accepted values and any user edits with accurate origins. Users may explicitly keep empty additional constraints. Manual mode shows the same summary without calling an assistant. No take preparation consumes unaccepted suggestions.

Existing plan look/initial_wardrobe remain authoritative; metadata records origin/acceptance rather than competing values. Shared summary confirmation is required only for suggestions or conflicts needing a decision. A saved look can supply accepted appearance and initial garments. An explicitly requested progression is previewed and materialized through the existing scoped-change workflow described below; a brief alone never changes clothing.

### 6. Fused scenes keep a reviewed advanced path

Ready fused_scenes remain importable and inspectable but are excluded from simple automatic anchor choices. Provide Use advanced editor and Choose a structured scene. Preserve existing manual/expert fused workflows.

Show the entire authorized description beside effective shared/take choices. Contradictions require a separately stored user-approved adaptation or a different resource before finalization. Never split prose or drop its prompt automatically. Do not claim all contradictions are detectable.

Room descriptions can also contain arbitrary prose. Keep existing conflict gates, bounded writer instructions and final-prompt review. A valid four-key response proves structure only. Acceptance includes a fused camera/pose conflict and a writer placing another location inside pose; no structural-validator result may be advertised as semantic approval.

### 7. Atomic creation and existing transport

A dedicated transactional guided boundary validates character/workflow, count, brief, exact anchor and policy and persists session plus stable take IDs before assistant calls. Failures leave no orphan. Existing routes remain unchanged.

Backend/frontend normalization retains authoring metadata. Older plans lacking it keep existing behavior and are not silently migrated. Async orchestration calls a field-preserving backend.enhance helper with existing settings/authentication/timeouts. Historical callers keep flattened output. Structured results pass through preparation validation and assistant provenance, never manual_completion disguised as assistant output.

Take output is limited to unlocked camera/framing/pose/expression. Fixed policy and explicit take values must agree or fail visibly. Neither can silently override the other, and per-take output cannot mutate resources, adaptations or shared state.

### 8. Ordered context and downstream edits

Process takes in stable plan order. Context contains persisted brief, anchor/authorized descriptions, shared state, policy, take ID/ordinal and at most five immediately preceding finalized summaries containing only take ID and the four fields. Save exact context and predecessor snapshot identities/revisions. Total take count is UI/progress metadata only: do not send it to the writer or include it in creative input digests. Ordinal remains binding. Deleting take ten of twelve therefore permits equivalent takes one through nine to copy forward; changing brief or another real input can still invalidate them. A take count mentioned explicitly by the user in the brief remains part of that brief, not a hidden count dependency. Exclude images, arbitrary conversation and full historical prompts.

Brief, anchor, shared-state or policy changes invalidate affected ungenerated automatic work. A take edit/removal/reorder conservatively invalidates later ungenerated automatic preparations from the earliest changed position, plus existing direct/wardrobe invalidation. This avoids a general dependency graph. Reuse unaffected earlier ready snapshots through the new verified copy-forward procedure below; existing recovery alone does not do this. Manual work keeps existing input-based invalidation.

Mode-only changes revoke review and cancel in-flight work but preserve completed choices and original provenance; future authoring follows the new mode. Generated/queued snapshots remain immutable. Extend generated continuity freezing to anchor/policy and the entire look_snapshot, including null state, preset ID/version/digest, appearance, garment order, wording and aside. After the first generated/linked take, only canonical-JSON-identical snapshot echoes are allowed; another preset/version requires another session even if plan.look and initial_wardrobe strings happen to match. Existing explicit scoped wardrobe changes remain available. Brief changes may affect future work without rewriting history. Explain affected results before save.

Five predecessors support local variety, not global uniqueness. Compare normalized tuples against one representative per logical take and verified copy-forward lineage: prefer current ready state, exclude stale/invalidated unlinked rows, and include genuine linked/generated history once per lineage. Exclude the candidate itself and all its ancestors/copies. A separate genuinely generated result in another lineage remains a comparison candidate; do not deduplicate by matching text alone. Never scan all historical rows as independent choices or send full history to the writer. Flag exact repeats for explicit review, never silently drop them or retry indefinitely. Deliberate repeats and fixed-camera sessions remain valid.

#### Verified copy-forward across plan revisions

Today recover_preparation only calls current-revision rows completed, and submit_prepared_take requires that same revision. Add copy-forward during the plan CAS transaction before obsolete-row invalidation. Only a ready, unlinked, non-invalidated snapshot can be copied. Compare all relevant inputs against the destination: take choices, effective wardrobe/shared state, exact resources and authorized translation/adaptation content, character/workflow binding, mapping/compiler versions, plus automatic brief/policy and original bounded context including ordinal and predecessor identities. Predecessor equivalence follows the recorded source lineage and content digest, not the newly allocated copy row ID. Changed or unverifiable inputs require new preparation. Mode-only changes may retain assistant results when those creative inputs are identical; original synthesis provenance remains assistant-authored.

Create one equivalent ready row under (session_id, new_plan_revision, take_id), preserving final_prompt byte-for-byte and original effective/provenance evidence. Add a copy_forward provenance block naming source prepared ID/revision, destination revision and the validated input digest. Never rewrite original writer requests to claim a new call. Destination binding metadata may identify the new revision while original synthesis retains its real revision. A unique destination prevents duplicate copies; an incompatible existing destination fails the transaction. Plan save, copies, consumed adaptation copies and invalidations commit or roll back together. For each eligible ready take, revalidate every adaptation actually consumed by its provenance against the destination resource field, source/translation value and fixed state. Recording adaptations must reuse _prepare_resource() or its shared effective-description resolver: source_value is the exact authorized descriptive input before adaptation, not the raw payload. Recheck exact source_value equality at the shared applicability boundary for review, finalization and copy-forward. Stale legacy approvals need fresh review under a new plan revision when immutable row identity prevents replacement; do not rewrite historical evidence. Copy only those adaptations into new take_resource_adaptation rows at the destination revision, preserving source_value/adapted_value and recording source/destination row identity and adaptation_digest in copy_forward.adaptations, distinct from the asset revision content_digest. An identical already-present destination adaptation is reused; a conflicting one aborts the entire CAS. Missing or inapplicable approvals make that take ineligible for ready copy-forward and require fresh review/preparation. Do not inherit unrelated adaptations or change loader queries to read arbitrary prior revisions. Update the schema comments/docstrings and tests during implementation: no automatic inheritance remains the default, with this verified transactional carry-forward as the sole exception. Original rows and original snapshot evidence are never rewritten. Revoke review; current copies need approval before submission. Tests must exercise actual recover -> approve -> submit, not just unchanged old rows.

Already linked/generated snapshots remain history and are displayed as already submitted; never copy them to ready or synthesize them again during automatic resume. Pending/invalidated rows are not copied. Old-revision submission remains stale even when a copy exists. Missing historical linked shots are diagnosed rather than replaced. This is additive recovery work, not relaxed submission validation.

### 9. Ownership, cancellation and recovery

Use a backend-enforced exclusive authoring claim per session shared by shared-state suggestions and automatic take preparation. Record operation identity, plan revision, ownership and recoverable lease/progress. No transaction spans a remote call. Concurrent requests return active progress rather than launch another call. Verify ownership and revision before accepting each response; renewed leases and fencing prevent expired owners from committing.

Persist each completed take. Stop on failure with completed/failed/remaining state. Cancel stops new scheduling and discards a late in-flight response. Reopening restores committed progress and offers Resume. After crash/expiry, completed snapshots are reused; incomplete remote calls may need retry. Exactly-once remote billing after response-before-persistence crashes is not promised.

Plan edits revoke old ownership; late output cannot overwrite new revisions. Bounded sequential requests and persisted progress need no new queue framework.

### 10. Personal looks reuse appearance and garment building blocks

A personal look is a named reusable preset containing optional appearance text (hair, makeup and styling), an optional outfit referencing an ordered set of garment records, and provenance. It does not contain character identity, camera, scene, place or lighting. Those remain selected separately. The normal session screen offers an optional Saved look picker in the shared summary, not another mandatory initial field.

Reuse garment/outfit concepts and catalogue reads, but do not delegate Looks writes to /api/wardrobe/import. That legacy endpoint writes as it iterates and skips existing keys without content equality checks. Add a Looks preflight that validates the entire payload and resolves conflicts, then rechecks state and performs all writes in one transaction. Reuse lower-level persistence only where it can honor that transaction; leave legacy import semantics unchanged. Add the smallest persistent saved-look wrapper needed to associate appearance and an outfit. Keep personal looks separate from immutable mined asset_revision payloads. A garment has stable identity, complete worn wording and optional moved-aside wording. Do not require rigid body-part slots: one-piece garments and multiple layers must work. Removable accessories belong with garments, never in constant appearance. Clothing accidentally embedded in free appearance text must be editable and flagged for review; do not claim perfect semantic detection.

Create forms hide technical keys and let users name a look, add/reuse garments, reorder their removal order and save. Store new immutable preset versions (or new immutable records linked to a logical look); editing never mutates a previously used snapshot. Existing legacy garment/outfit keys are not reworded: saving changed clothing uses new records/keys. Do not expand this work into general catalogue deletion or lifecycle management.

Selecting a look copies its appearance, garment wordings/order and stable preset ID/version/digest into the plan with origin metadata. The copied content is authoritative for that session, not a live catalogue lookup. An appearance-only preset leaves wardrobe unchanged; absence of an outfit is not a garment-free instruction. Existing explicit overrides require an explicit Replace or Keep decision; selecting a preset never silently discards them. Future preset edits affect only new selections. Updating an existing session requires an explicit CAS application with ordinary invalidation/freeze rules.

### 11. Text, JSON and photo inputs converge on the same editor

Support manual creation and browser-selected JSON import/export. Use the closed portable-look-v1 envelope and conflict rules in specs/resource-store/spec.md, converting its garment_keys to the complete ordered snapshot defined by session-plan; no machine paths, embedded photos, credentials or session data. Export text plus provenance-safe identifiers, not private image locations. Accept existing garments/outfits JSON as an outfit-only import, then allow saving it as a named look. Prevalidate all references, duplicate keys and content before any write. Identical re-import is a no-op; differing content under the same logical identity requires explicit new-version/save-copy resolution, never silent overwrite or skip. Preview and commit are bound to the reviewed content. Limit actual streamed HTTP body bytes to 10 MiB before JSON/Pydantic deserialization on all look preview/import/commit endpoints receiving JSON content, including the legacy garment/outfit input accepted here. Count bytes even when Content-Length is missing or false; return 413 before parsing or writes.

Photo import accepts one JPEG, PNG or WebP at a time, at most 10 MiB decoded file bytes, through a validated upload boundary with opaque IDs. Reuse the configured vision transport/model, but add actual Pillow verification/decoding for this boundary. Existing enhance._check_image checks data-URI spelling and estimated size only. Verify supported file format, dimensions (at most 25 megapixels), actual bytes and complete decode before inference; reject malformed/truncated and oversized images. Pillow is already a backend dependency. Filename is metadata only. Selecting a photo stages it privately and shows Extract look; only that explicit action sends it to the configured assistant. No provider/configuration stack is added.

Extraction proposes editable appearance and individual visible garments, and leaves the removal order for user confirmation. Do not infer unseen garments, identify the person or import background/pose/camera as look identity. Unknown details are marked unresolved for review; users may omit them explicitly. No vision-capable assistant means extraction is unavailable with an explanation, while the photo preview and manual form remain usable. Vision failure or invalid structured output saves no look. Extracted text uses the existing English output convention.

Save only after explicit review; persist a redacted request projection: exact text messages/structure, model identifier and non-secret generation parameters, replacing the image content part with its SHA-256 digest, media type, byte count and dimensions. Preserve validated output and user corrections. Never persist a raw data URI, image bytes, endpoint credentials or secret headers in provenance. This records the request evidence excluding binary content, not the exact wire request; replay does not re-run inference after the image expires. Staged photos expire after 24 hours and are removed after save/cancel when no longer needed. A saved look remains reusable without the image or another assistant call. This is text extraction, not a generation reference: never auto-attach the photo to ComfyUI or promise visual clothing/identity fidelity.

### 12. Explicit progression materializes existing wardrobe changes

Keep wardrobe constant by default. Offer Plan clothing changes for a selected snapshotted outfit. The user confirms garment removal order, selects the final stage and start/end take boundaries, and previews every resulting wardrobe. Use a pure derivation accepting the complete look_snapshot.outfit as an argument. Do not call arcFor(outfitKey): it reads live globals and silently filters unknown keys. Reject incomplete, duplicate or inconsistent snapshot garment identities before deriving; no missing garment may disappear silently. Reuse only established outfit arc semantics, including an optional moved-aside stage only when the final garment has such wording. No model decides order or invents garments. Do not persist a reusable preset's derived stage list; derive it from its snapshotted garments.

For a requested sequence of K stages across M selected consecutive takes, require M >= K (for K > 1) and preview stage floor(i * (K - 1) / (M - 1)) for zero-based take offset i. K = 1 means constant clothing. Fewer takes require choosing fewer stages or a wider interval; do not silently skip a garment state. Before the interval keep the initial wardrobe; after it carry the selected final stage. The user may edit boundaries before applying and need not choose a fully removed final state.

Apply the reviewed preview in one plan CAS mutation as initial_wardrobe plus minimal from_here events keyed to stable take IDs; keep the existing this_take option for local overrides. Existing events require an explicit replace/merge review and no duplicate take events. The approved explicit events, not a live auto-distribution rule, remain authoritative after reordering or adding takes; re-run the planner only on user request. Store the source preset snapshot and approved planner inputs as provenance, not a second effective-wardrobe truth.

Each take prompt states only garments still worn, repeating unchanged garment wording exactly. Do not append the original full outfit elsewhere in look or take synthesis. A deliberately empty outfit uses an explicit no-clothing sentence; an empty string keeps its existing no-description meaning. This prevents removal from becoming a contradictory positive description. Shared appearance stays constant. Existing source-clothing conflicts require adaptation or another resource. Use the exact canonical wearing rule in specs/resource-prompts/spec.md for both initial_wardrobe and all stages, including deterministic punctuation and the explicit empty-state sentence. Backend application and frontend preview must agree byte-for-byte; reuse existing arc semantics without importing legacy catalogue measurement gates into resource-v1.

### 13. Review ends preparation

Show effective photo choices and status with Edit/Prepare/Resume; put prompts and provenance in expandable details. No revision IDs or four-field typing are normal prerequisites. Reuse explicit review approval, submission and runner gates with labels distinguishing text preparation, queueing and generation. Offer a reviewed test subset before the full session. Preparation never approves, submits or runs.

## Risks / Trade-offs

- Arbitrary prose may contradict shared state: preserve visible review and honest limits.
- Suggestions add assistant calls: make them explicit, bounded and reviewed before writes.
- Conservative invalidation repeats some work: disclose affected takes and preserve history.
- Simple rooms-only anchors exclude some mined scenes: keep the advanced path discoverable.
- Historical renames cannot always be inferred: retain explicit compatibility targeting.
- Ownership adds recovery state: test races, cancellation, expiry and stale responses.
- Photo extraction can omit or misdescribe garments: require editable review and never treat an inferred item or removal order as accepted.
- Preset edits could change old sessions: copy exact content on selection and verify sessions remain unchanged after library edits.

## Migration Plan

1. Deliver selected-byte import and compatibility targeting.
2. Add actionable readiness, translation maps and reviewed manual/assistant translation proposals.
3. Add backward-compatible authoring state, atomic creation and shared-state resolution.
4. Connect structured transport, ownership/recovery and ordered preparation.
5. Add reusable looks, reviewed text/JSON/photo inputs and snapshotted outfit progression through existing scoped changes.
6. Rework normal UI, retain expert paths, update README/docs and run acceptance gates.

Delete/Restore remains a separate future proposal. Changes are additive; rollback disables new entry points without deleting source revisions or snapshots. Older binaries must not save plans carrying unknown authoring fields; inspect stored snapshots until a compatible version is restored.
