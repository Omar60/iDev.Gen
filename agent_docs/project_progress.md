# Project Progress

## Goal

Implement the active OpenSpec change `simplify-resource-session-workflow` one
independently reviewable task at a time while retaining architecture,
integration, and acceptance in the main agent.

## Current Deployment

Task 9.13 passed independent acceptance under Heavy deployment
`srsw-9-13-20261005` with internal Codex implementation and verification.
Progress is 64 of 72 tasks complete. Two integrated HTTP journeys demonstrate
independent reuse across characters/scenes, immutable library versions,
reviewed progression, scoped overrides and stable-ID reorder, repeatable
prepared prompts, source-clothing adaptation and complete snapshot freeze
after real approval/submission. No production code changed and no independent
review remains pending. The full Python gate and focused/privacy/control
checks passed; the full run showed one skip without a reason in its quiet
output. Frontend passed 581 tests in 30 files and build passed with its existing
bundle-size warning. Strict OpenSpec validation and diff checks passed.
The user authorized the isolated local acceptance commit. Task 10.1 and later
tasks remain unstarted; no external handoff or push is authorized.

## Overall Progress

OpenSpec is a valid `spec-driven` change with 64 of 72 tasks complete. Task 1.1
was accepted in `616a6d54d1ddbb2ec97444b0d2dea2a0e16fe8fd`, Task 1.2 was
accepted in `a51abc3f2feca82d8cc2fcbfa332f0cd8777f059`, and Task 1.3 passed
independent review and its aggregate final gate. Tasks 1.4, 1.5, and 1.6
have now passed independent acceptance and are formally closed. Task 2.1 has
also passed independent acceptance and is formally closed. Task 2.2 has now
passed independent acceptance and is formally closed. Task 2.3 has now passed
independent acceptance and is formally closed. Task 2.5 has now passed
independent acceptance and is formally closed. Its offensive A-L matrix covers
forged final prompts, effective state and take choices, assistant evidence and
digests, automatic manual-completion rejection, locked/resource-derived
overrides, raw compatibility, and pre-authoring provenance boundaries. The
completed
1.1-1.6 integration gate covers the browser selection lifecycle, canonical
import/replay, compatibility, safe public projections, real frontend contract
coverage, concurrency, recovery, and payload-free library filtering. Task 2.1
adds the shared actual-streamed-body limit boundary described below.

Task 3.1 has now passed independent acceptance and is formally closed. Selected
translation-map content is resolved server-side from `selection_id`, `file_id`,
and `expected_revision`; the staged file is the exclusive byte authority and is
opened/read once into `raw_bytes` for integrity checks, candidate inspection,
UTF-8 decoding, and JSON parsing. The selected preview/apply paths delegate to
the historical bulk functions, preserving semantic authorization, canonical
digest, attestation, and library-fingerprint checks. `RequestLimitRoute`
protects both routes with the real 10 MiB boundary, including pre-Pydantic 413
responses for missing or false `Content-Length` and exact-boundary coverage.
Malformed selected maps fail with sanitized controlled errors. The closure was
verified against 2,063 tests.

Task 3.2 has now passed independent acceptance and is formally closed. The
resource-library translation rows are a safe source-backed projection: they do
not expose raw payloads, paths, or internal fingerprints. The frontend builds a
direct `translation_map` with `buildTranslationMapFromRows(rows)`, preserving
scalar/list contracts and independent scalar list entries. Required
already-English strings receive explicit identity translations, while optional
English strings do not. Compatible duplicate sources merge deterministically;
incompatible translation or shape duplicates produce explicit diagnostics and
never use last-write-wins. Valid sidecars are preserved, corrupt sidecars are
diagnosed without editable rows, and public diagnostics are sanitized.
Manual Preview and Apply use the canonical bulk translation endpoints, keeping
attestation, map digest, and library fingerprint authoritative. The direct
revision shortcut and single-revision endpoint are not used. Edits, reloads,
and Apply errors invalidate preview authorization, and late responses cannot
restore stale authorization. The Task 3.1 selected-map flow and
`map_path`/direct-map compatibility remain intact. The verified totals are
2,072 backend tests and 362 frontend tests.

Task 3.3 has now passed independent acceptance and is formally closed. `POST
/api/resources/libraries/{library_key}/translations/proposals` generates
optional translation proposals for at most twenty source entries per action:
one scalar is one entry and every list item is an independent entry. The
request is identity-only (`source_id`, `content_digest`, canonical `field`,
`source_shape`, and `list_index`); source text and eligibility are resolved
server-side from the canonical projection. Only pending, authorized
`ROLE_DESCRIPTIVE_INPUT` rows are eligible. Existing translations are not
implicitly overwritten, and `identity_required` rows do not consume the
assistant.

The endpoint reuses `backend.enhance.run_structured(...)`. Provider keys
(`entry-0` through `entry-N`), canonical ordering, list ordering, and repeated
source identities are server-owned. The schema rejects unsolicited or
malformed fields, including string/float/bool coercion for `list_index`; schema,
stale, and ineligible diagnostics are sanitized. Provider output must be an
exact key-to-English-string object; extra, missing, malformed, or non-English
output rejects the complete proposal with no partial acceptance.

Proposal generation is write-free: no sidecar, attestation, implicit Preview,
implicit Apply, or direct revision shortcut. The reviewed flow is Suggest to
editable/reviewable manual rows, explicit Preview through canonical attestation,
then explicit Apply. Manual edits, row reloads, global or cross-library reloads,
and a second Suggest fence stale responses; global reload clears `proposalBusy`.
With `resource_planning_enabled=false`, generation returns HTTP 503 before body,
schema, source, or provider processing and does not call `run_structured`.
Tasks 3.1 and 3.2 remain compatible.

Task 3.4 has now passed independent acceptance and is formally closed. The
Resource Browser separates the authoritative Imported outcome from Needs
translation, Ready, and Needs source correction, with direct Create session,
Translate, and Inspect source/Re-import actions. Readiness refreshes after
Apply and import. Safe readiness projection omits raw payloads and arbitrary
coverage/translation markers; invalid persisted sidecars remain pending and
inspectable. Imported identity is derived only from `commit_result.files[].accepted`
using the complete `(library_key, source_id, content_digest)` identity.

Proposal, manual-preview, and map-preview responses are fenced against stale
edits and reloads. The map path stays editable during Preview and Apply;
`mapOperation` allows an A-to-B edit, a new Preview while A is pending, ignores
late A, preserves B authorization, and keeps Apply in flight while blocking
incompatible actions. Successful Apply still requires the authoritative
readiness refresh.

Task 3.5 has now passed independent acceptance and is formally closed. This
test-only task verifies manual source-backed translation without a map or
assistant, distinguishes missing translation from invalid source, diagnoses
invalid sidecars, and preserves manual recovery after provider failure. It
rejects malformed provider output atomically and rejects unauthorized, stale,
and ineligible proposals before provider invocation, including the 20/21 entry
bound. Compatible duplicate sources merge deterministically while conflicting
duplicates block preview. Existing stale attestation/library and selected-map
coverage checks remain intact. Real HTTP bodies above 10 MiB, including missing
or false `Content-Length`, fail before parsing/writes. The workflow is write-free
until explicit Apply and performs no automatic Apply, session, or generation.
No product defect was found. Group 3, Actionable translation readiness, is
complete. At that checkpoint Task 4.1 was pending and Task 4 had not started.

Task 4.1 has now passed independent acceptance and is formally closed. The
backend validates the complete closed authoring-v1 schema and keeps
`workflow_binding`, `evidence`, `look_snapshot`, and `wardrobe_progression`
server-owned. Generic plan saves reconcile each changed shared-state value
inside the revision CAS, reset only changed-field metadata to `user`, and
preserve historical evidence. Frontend normalization and save payloads retain
authoring data, empty take arrays, and missing or invalid IDs without
fabricating IDs. Closed-schema, ownership, compatibility, and synchronized
concurrent-CAS regressions pass. Task 4.2 was pending at that checkpoint.

Task 4.2 has now passed independent acceptance and is formally closed. A
reusable strict integer validator enforces authoring counts from 1 through 500;
the reusable brief validator accepts up to 2,000 characters. Growth is checked
against the persisted plan inside the winning CAS. Historical authoring plans
above 500 may remain or shrink but cannot grow, while expert/pre-authoring and
legacy behavior remains compatible. Twenty is not a plan cap. A regression
ensures `10 ** 5000` raises `PlanValidationError` without integer formatting.
The focused and full Python suites, privacy/shoot checks, strict OpenSpec
validation, and diff checks passed.

Task 4.3 has now passed independent acceptance and is formally closed. The
server resolves the effective primary workflow from the explicit Advanced
override or character default, checks existence and shared primary/reference
compatibility, and freezes a server-owned `workflow_id`, `kind`, `graph_digest`,
and `node_map_digest` in authoring-v1. Missing workflow selection fails with
`workflow_required`; missing or changed live binding state fails with
`workflow_changed`. Binding drift is checked at preparation, approval, and
submission, including transactional write boundaries. Later model-default
changes do not affect a session, its primary workflow cannot be replaced, and
`reference_workflow_id` remains outside the primary binding. Pre-authoring and
legacy behavior remains compatible. Task 4.4 is pending.

Task 4.4 has passed independent acceptance and is formally closed.
`POST /api/sessions/guided` uses a closed normalized request,
a canonical digest excluding `request_id`, an exact ready rooms anchor, and the
server-resolved frozen workflow binding. One serialized transaction persists
the request record, session, revision-one authoring plan, canonical conflicts,
stable take IDs, and exact replay response. New creation returns `201`, matching
replay returns `200`, changed content returns `409`, and concurrency/rollback
tests prove one creation with no orphans. The final backend suite passed with
2,256 tests.

Task 4.5 has passed independent acceptance and is formally closed. Resource-v1
session detail, list, and free-text search project `plan.look` and
`plan.initial_wardrobe` without mirroring them into legacy session columns.
Missing or invalid resource plans expose a stable diagnostic and never fall
back to stale legacy constants. Generic session PATCH rejects explicit look or
wardrobe changes with `409 plan_field_required`, while legacy search and
wardrobe PATCH behavior remains unchanged. The full backend suite passed with
2,263 tests.

Task 4.6 has passed independent acceptance. `GET /api/sessions/{sid}/plan`
projects an assistant-free shared summary for authoring-v1 from the validated
plan's effective look and wardrobe values and their separate origins. Selected
room and fused-scene descriptions use exact resource revisions and the same
authorized translation resolver as preparation; empty additional constraints
leave those descriptions visible. Missing or invalid scene authority produces
a fixed, actionable diagnostic without falling back to raw payloads. SessionView
shows this saved summary before preparation in manual and automatic modes.

Task 4.7 has passed independent acceptance. The plan CAS freezes the scene
anchor, variation policy, workflow binding, and complete saved-look snapshot
after a take is linked to a queued shot, alongside the established constants.
Rejected edits leave the plan, prepared rows, and linked shots unchanged.
Brief edits and explicit scoped wardrobe changes remain available. The final
full Python gate passed with 2,271 tests.

Task 4.8 has passed independent acceptance. Real API tests cover automatic/manual
mode changes without an assistant and reject explicit take choices that conflict
with fixed variation policy before a plan revision is written. Existing API
coverage confirms closed authoring round trips, workflow resolution and drift,
plan-owned session projections, stale CAS, and plans above twenty takes. The
independent full Python suite passed with 2,276 tests; frontend tests passed
with 430 tests. Task 5.1 remains pending.

## Prior Position

Task 2.4 is complete. The automatic/manual/pre-authoring authority matrix is
enforced: public raw begin/complete returns 409 for every plan with authoring
metadata; direct and bulk automatic preparation are blocked; and direct Python
`finalize_take_preparation` rejects automatic plans at the domain boundary.
Automatic plans can reach ready only through the future fenced `prepare_takes`
operation. Manual authoring retains the validated manual preparation boundary
and historical unset/unlocked-field checks; pre-authoring expert plans retain
historical begin/complete and preparation; legacy composition is unchanged.
Malformed authoring fails closed, `schema_version` requires the strict integer
`1`, bulk preflight prevents partial mutation, and rejections precede
persistence and assistant work. Task 2.4 adds the server-owned evidence
boundary and sealed manual persistence; its accepted baseline was 2,046 tests.
Task 2.5 brings the reproducible collection to 2,049 tests, and no product code
changed.

Task 2.4 has passed independent acceptance and is formally closed. Its
server-owned authoring evidence boundary derives the final prompt, effective
state, versions, projections, provenance, duplicate sentinels, and digests.
Manual preparation persists only an internally sealed result; raw
`complete_preparation` remains available only for pre-authoring plans, while
automatic preparation remains reserved for a future fenced operation. The
read-only evidence validator fails closed on malformed or non-object evidence,
strict integer violations, and closed-shape mismatches. Review, Recovery,
Approve, and Submit reject invalid evidence, and the HTTP diagnostic is fixed
and sanitized. The accepted closure was verified against 2,046 collected
tests.

Task 2.1 remains complete. Its shared actual-streamed-body limit boundary enforces
the authoritative 10 MiB body limit from actual bytes before JSON/Pydantic or
multipart parsing, including when Content-Length is missing or false. It uses
an opt-in RequestLimitRoute, replays a verified body without a second network
read, aborts incomplete disconnects before downstream work, and leaves legacy
endpoints unaffected. The browser selection integration gate has independent
backend and frontend contract coverage for stale revisions and previews,
expiry/tombstone/purge, cancel/commit conflicts, duplicate inputs, crash
recovery, safe response shapes, payload-free library filtering, and the
canonical SafeCommitReport boundary. Task 1.5's allowlisted SelectionView and
revision/epoch fencing, Task 1.4's browser compatibility adapter, and Task
1.3's canonical preview, commit, replay, concurrency, and atomicity contracts
remain intact.

## Current Position

Tasks 9.1-9.13 have passed independent acceptance. The current test-only
deployment is described above and in `latest_session_work.md`. Task 10.1 is
the next milestone and remains unstarted. No push or further implementation
is authorized.

Tasks 1.1 through 6.5 and Tasks 7.1-7.10 are complete.
Task 5.2 adds atomic start and read-only status for the shared authoring claim.
The closed public view excludes fencing and request internals; exact request
replay, cross-kind exclusion, ordered targets, configuration gates, and
origin-aware shared suggestions were independently verified. Task 6.3 now
dispatches an assistant worker for new and resumed suggestion claims. Task 5.3
adds a ten-minute backend lease renewal and a signed
input ticket. Its short response transaction verifies the current owner, lease,
plan revision, request, effective inputs, resource revisions, and workflow
binding before saving operation result and ordered progress together. Independent
review reproduced translation changes during a call and confirmed stale output
is discarded for both operation kinds. The full Python suite passed with 2,308
tests; strict OpenSpec, privacy/control, and diff checks passed. Concrete remote
execution and prepared-take snapshot integration remain in later tasks. Task 5.4
adds cancel/resume routes and startup/lazy recovery. Cancellation fences late
results; resume retains ordered completed results only while the durable input
digest still matches the current plan, resources, and workflow. Plan CAS and
feature disablement cancel active ownership. Migrated operations lacking an
original input digest cannot be resumed safely. Independent adversarial tests,
the full Python suite, privacy/control checks, strict OpenSpec validation, and
diff checks passed; README and the session guide describe the new controls.

Task 5.5 adds operation-specific acceptance for succeeded shared suggestions.
One transaction checks the current operation inputs and plan revision, writes
reviewed values and exact safe assistant evidence through plan CAS, and stores
the acceptance receipt for identical replay. Different content and stale plans
fail without partial writes. The independent full Python suite passed with
2,333 tests; privacy/control, strict OpenSpec, and diff checks passed.

Task 5.6 adds operation-contract regressions for cross-session request IDs,
simultaneous mixed-kind starts, stale owners after a new fence, and recovery of
partial progress after reopening SQLite. Existing tests cover response-loss
replay, cancellation, plan edits, unavailable assistants, and failure/resume.
Independent verification passed 2,335 Python tests, 48 privacy/control tests,
strict OpenSpec validation, and `git diff --check`. Shared suggestion execution
arrived in Task 6.3; take snapshot integration remains in Task 6.4.

Task 6.1 makes the fused-scene choice explicit in Resources. The existing
guided API restricts simple anchors to exact ready `rooms` revisions. Ready
`fused_scenes` offer the expert editor with their exact revision preserved or
a switch to the room filter; no prose decomposition or assistant work occurs.
Independent review found no required correction. The full Python/frontend
suites, frontend build, privacy/control checks, strict OpenSpec validation,
and diff check passed before task closure.

Task 6.2 makes Review show complete authorized fused descriptions beside
approved adaptations. Adaptation recording uses the same translation-aware
resolver as preparation and persists the exact effective `source_value`.
Review and finalization reject approvals whose source value changed without
rewriting historical rows; the expert raw completion path also refuses a stale
approval before creating a ready snapshot. Independent verification passed
2,337 Python tests, 434 frontend tests, the frontend build, 46 privacy/control
tests, strict OpenSpec validation, and diff checks.

Task 6.3 connects new and resumed `shared_suggestions` operations to the
configured assistant. It requests only missing look/wardrobe choices with model
context, brief, and authorized scene descriptions. Complete validated proposals
and exact non-secret request evidence remain pending until reviewed, idempotent
plan CAS acceptance; explicit empty and manual choices stay authoritative.
Model `trigger` and `base_positive` are bound to the operation fingerprint so
an in-flight change discards stale output. Independent acceptance passed 2,344
Python tests, privacy checks, strict OpenSpec validation, and diff checks.

Task 6.4 connects `prepare_takes` to structured assistant output and server
preparation. Only unlocked camera, framing, pose and expression fields enter the
request or output; the ready snapshot and operation progress commit together
under the current fence. Snapshot provenance binds the actual assistant request
and validated output to the committed operation result. Cancellation, stale
inputs and invalid output leave no ready snapshot. Independent acceptance passed
2,350 Python tests, 46 privacy/control tests, strict OpenSpec validation and
diff checks. Task 7.9 adds duplicate detection below.

Task 6.5 passed independent acceptance. Integrated operation tests reject
locked, malformed, partial and placeholder output without ready snapshots or
shared-state mutation. Terminal replay retains exact assistant evidence without
a second call. Fused camera/pose and location conflicts remain visible in the
complete prompt and authorized description; the UI identifies conflict checks
as structural and requests semantic review. The full gates passed: 2,357 Python
tests, 435 frontend tests, frontend build, strict OpenSpec validation and diff
check.

Task 7.1 passed independent acceptance. Automatic writer context includes
persisted shared, scene, workflow and policy inputs and at most five preceding
finalized four-field summaries, without a total-count dependency. Exact context
and predecessor identities/revisions are replayable evidence. Invalid
predecessor evidence blocks descendant recovery and approval, and type-sensitive
JSON comparison rejects forged boolean IDs and ordinals. Independent gates
passed 2,365 Python tests, 46 privacy/control tests, strict OpenSpec validation
and diff checks.

Task 7.2 passed independent acceptance. Each automatic operation handles at
most twenty takes and commits each snapshot with its fenced progress. A second
operation prepared takes 21-40 without changing or reauthoring the first batch.
Current ready/generated snapshots are validated and reused without another
assistant call; partial failure and Resume preserve completed takes. The full
Python suite passed with 2,370 tests, privacy/control checks passed with 46,
and strict OpenSpec validation and diff checks passed. The frontend operation
controls remain assigned to Task 8.4.

Task 7.3 passed independent acceptance. Automatic plan edits invalidate
ungenerated preparations from the earliest changed take position, while global
creative inputs invalidate all affected automatic work. Removing take ten keeps
earlier unchanged rows intact; manual plans retain input-based invalidation.
Plan CAS still revokes review and fences old operations, while generated history
and completed provenance remain unchanged. Independent verification passed
2,380 Python tests, 46 privacy/control checks, strict OpenSpec validation and
diff checks. Verified copy-forward into the new revision remains Task 7.5.

Task 7.4 passed independent acceptance. Finalized takes record a versioned
digest and canonical projection of authorized resource descriptions and
consumed adaptations. Read-only review and recovery reject changed or missing
evidence for unlinked ready rows without backfilling older snapshots; linked
history remains visible from its saved evidence. The full Python suite passed
2,389 tests, privacy/control checks passed 46, and strict OpenSpec and diff
checks passed. Transactional Submit dependency validation remains Task 7.7.

Task 7.5 passed independent acceptance. Plan CAS now copies only verified,
ready, unlinked authoring snapshots to the new revision, preserving the exact
prompt and original synthesis evidence while binding destination and lineage.
Changed resource inputs, unverifiable evidence and linked history do not copy.
Recovery, fresh approval and submission of a current copy were verified;
old-revision submission remains stale. Independent gates
passed 2,394 Python tests, 46 privacy/control checks, strict OpenSpec validation
and diff checks.

Task 7.6 passed independent acceptance. Plan CAS copies or reuses only consumed,
still-applicable adaptation rows under the new revision. Exact source and
adapted values, source/destination row identities, and a canonical adaptation
digest are verified through snapshot lineage; conflicting destination rows
roll back the entire save. Missing or stale approvals leave the take needing
fresh preparation. Recovery, explicit approval, and submission of an adapted
copy passed. The final full Python suite passed 2,401 tests and 46
privacy/control checks passed. One existing concurrent-CAS test failed in an
earlier full run, then passed alone and in the final full run; monitor for
intermittence. Strict OpenSpec validation and diff checks passed.

Task 7.7 passed independent acceptance. Review and recovery identify stale
unlinked snapshots; approval and domain submission revalidate workflow and
resource inputs under their serialized write transactions. A stale selected
batch member prevents all shot and link writes. Stored approval becomes
ineffective after drift, while already-linked history and idempotent retry
remain intact. Independent gates passed 2,403 Python tests, 46 privacy/control
checks, strict OpenSpec validation and diff checks. Frontend files were not
changed.

## Next Milestone

Task 7.8 passed independent acceptance under the Heavy internal Codex route.
Explicit resource refresh uses CAS, preserves the creative plan and linked
history, writes nothing without drift, and copies only verified unaffected
work. Missing required evidence cannot use another plan kind's namespace.

Task 7.9 passed independent acceptance under deployment
`simplify-rsw-7_9-20260930`. Automatic preparation freezes normalized four-field
duplicate comparisons against one verified representative per take/copy lineage
and genuine generated history. It excludes the candidate lineage and stale or
invalidated unlinked rows, preserves stored output and deliberate repeats, and
shows matching takes in Review. A newly flagged snapshot revokes earlier
approval transactionally; explicit approval after review permits submission,
while identical reuse preserves later approval. The final full suite passed
2,423 Python tests; frontend tests (436), build, privacy/control checks, strict
OpenSpec validation and diff checks passed.

Task 7.10 passed independent acceptance under deployment `srsw-710-20260930`
on the Heavy internal Codex route. Integrated authoring-v1 regressions cover
copy-forward, reviewed adaptations, isolated input drift, canonical translation
races, zero-write batch refusal, refresh and real linked history. Plan save and
refresh now report copy conflicts as HTTP 409. Shared operation validation
refuses earlier-revision submitted take IDs before new assistant work while
preserving current snapshot reuse and terminal replay. Independent verification
passed 2,439 Python tests and 51 privacy/control tests, strict OpenSpec validation
and diff checks. One existing concurrent-CAS test failed in the first full run,
then passed alone and in the final full run without weaker assertions.

Task 8.1 passed independent acceptance under deployment `srsw-81-20261001`,
using internal Codex workers on the Heavy route. Ready rooms now open a guided
form preserving character and exact scene, with twelve photos by default,
optional brief, and mode/workflow under Advanced. Unknown creation outcomes
block alternative creation; late responses cannot redirect after unmount.
Verification passed 2,439 Python tests, 450 frontend tests, frontend build,
46 privacy/control checks, strict OpenSpec validation and diff checks. No
independent review remains pending for Task 8.1. Complete guided creation
response-loss retry UX remains assigned to Task 8.3.

Task 8.2 passed independent acceptance under deployment `srw-8-2-20261002`
on the Heavy internal Codex route. Advanced exposes shared overrides and all
four fixed/vary policies in guided creation and session editing; raw take
fields open directly for Manual and older plans. Saved shared summaries precede
preparation. Reviewed suggestions use the existing operation acceptance CAS,
and explicit empty decisions use a strict additive `shared_decisions` option
on plan save in either authoring mode. Pending writes protect local edits;
session epochs and authoritative readback prevent stale or false notices.
Independent gates passed 2,456 Python tests, 461 frontend tests, 46
privacy/control checks, frontend build, strict OpenSpec validation and diff
checks. No independent review remains pending for Task 8.2. Automatic
preparation UI controls remain Task 8.4; the broader browser walkthrough
remains Task 8.6.

Task 8.3 passed independent acceptance under deployment `srw-8-3-20261002`
on the Heavy internal Codex route. Normal guided creation uses the atomic
endpoint and retries an unknown outcome with the exact frozen UUID and body,
even after inventory changes. Only validated stored responses with HTTP 201
or 200 allow navigation. A stable 409 idempotency conflict requires an explicit
new attempt; stable validation errors allow correction, while unreadable
responses remain unknown. Synchronous duplicate sends and late unmounted
responses are guarded. The pre-authoring expert path remains compatible.
Independent verification passed 2,456 Python tests, 483 frontend tests,
twelve independent UI probes, 46 privacy/control checks, frontend build,
strict OpenSpec validation and diff checks. The Python full gate preceded
frontend-only changes; final privacy/control checks cover the final source.
No independent review remains pending for Task 8.3.

Task 8.4 passed independent acceptance under deployment `srw-8-4-20261002`
on the Heavy internal Codex route. SessionView connects automatic ordered
batches of at most twenty to persisted operations, adopts active work across
tabs and kinds, and exposes progress plus explicit Cancel/Resume. Unknown
starts retain their UUID/body across retries and reopening; even a well-formed
5xx remains unknown. Same-ID Resume reloads newly persisted preparation and
reviews. GET polling cannot renew ownership or trigger generation. Resources
offers Retry cleanup status on the existing SelectionView warning.
Independent gates passed 2,456 Python tests, 492 frontend tests, 46
privacy/control checks, frontend build, strict OpenSpec validation and diff
checks. No independent review remains pending for Task 8.4.

Task 8.5 passed independent acceptance under the Heavy internal Codex route.
Resource session cards and detail expose plan-owned constants, while Review
prioritizes saved effective choices, including explicit empty values. Prompt,
writer evidence, duplicate and dependency diagnostics remain inspectable.
Resource refresh uses the existing explicit CAS without preparing, approving,
submitting or running. A confirmation explains downstream impact before saving.
Linked stable IDs from any revision retain their historical choices and remain
excluded from preparation targets. Submit Test Selection queues only reviewed
selected takes; Run remains separate.

Independent verification passed 2,456 Python tests, 520 frontend tests,
46 privacy/control checks, the frontend build, strict OpenSpec validation and
diff checks. One historical-state defect was repaired and independently
rechecked. The final frontend-only repair did not change backend source, so the
completed full Python gate was retained. No independent review remains pending
for Task 8.5. Documentation and its authorized isolated acceptance commit close
that deployment.

Task 8.6 passed independent acceptance under deployment
`task86-ui-contract-20261003` on the Heavy internal Codex route. Real backend
response captures now drive eight rendered component contract tests and an
exact regeneration check. The captured manual batch contract exposed and fixed
the prepared-count notice. An isolated built-frontend walkthrough verified
real two-tab ownership, partial failure/resume, manual preparation and missing
workflow remedies without implicit approval, submission or generation.
Independent gates passed 2,457 Python tests, 528 frontend tests in 22 files,
46 privacy/control checks, fresh fixture comparison, frontend build, strict
noninteractive OpenSpec validation and diff checks. No independent review
remains pending. The authorized isolated acceptance commit closes Task 8.6;
Task 9.1 was next at that checkpoint. No push was authorized.
Task 9.1 passed independent acceptance under deployment
`srsw-next-20261003-01` on the Heavy internal Codex route. The manual Looks
editor separates appearance from an optional ordered outfit and hides technical
keys. Atomic saved-look versions preserve complete historical definitions,
legacy keys and exact content digests; stale edits cannot allocate partial
records. A stale list response after Save was repaired and independently
rechecked. Final verification passed 2,466 Python tests, 536 frontend tests in
23 files, frontend build, privacy/control checks, strict OpenSpec validation
and diff checks. An unchanged concurrent plan-CAS test also failed on the clean
baseline before the final full run passed; its logic and assertions were not
changed. No independent review remains pending for Task 9.1. The user authorized
the isolated acceptance commit. Archivist owns the closing Git receipt and
token report. Task 9.2 was next at that checkpoint.

Task 9.2 passed independent acceptance under Heavy deployment
`simplify-resource-session-next-20261003`. Pure `portable-look-v1` parsing
validates the complete closed envelope and preserves untrusted origin
annotations; canonical ordering binds full portable equality while the session
snapshot retains its separate appearance/outfit digest. Read-only export uses
immutable saved definitions and local keys, with null provenance for the
current storage. The new route supports all schema-legal key characters.
Independent verification passed 2,480 Python tests, 69 focused looks and
privacy/control checks, strict OpenSpec validation and diff checks. No frontend
files changed and no independent review remains pending for Task 9.2.

Task 9.3 passed independent acceptance under Heavy deployment
`sr-sw-9-3-20261003` with internal Codex implementation and verification.
Portable import preview/commit binds exact reviewed content, mappings and
destinations, then atomically writes catalogue rows, immutable look versions
and pre-remap receipts. Original-envelope and local-export equality reuse
intact saved versions without additional writes. Storage overflow and a
reviewed-destination mismatch were repaired and independently rechecked;
save-copy remains available when no higher SQLite version is representable.
The final complete Python suite passed 2,512 tests; privacy/control checks,
strict OpenSpec validation and diff checks passed. Earlier CAS race failures
did not recur in the final full run; no unrelated repair was made. No frontend
files changed and no independent product review remains pending. The user
authorized the isolated local acceptance commit; its Git receipt and the Heavy
token report accompany the closing handoff.
Task 9.4 passed independent acceptance under Heavy deployment
`simplify-workflow-next-20261004`. The existing bounded Looks import endpoints
now accept legacy garments/outfits as outfit-only input. Complete preflight and
transactional revalidation preserve legacy keys, expose explicit save-copy
remapping, and retain an immutable canonical-input receipt for verified replay.
The import creates no saved look; explicit named conversion uses the existing
look-create route. The legacy wardrobe import is unchanged.

Final verification passed 2,537 Python tests, 60 focused import/compatibility
tests, 46 privacy/control checks, strict OpenSpec validation, compilation and
diff checks. An earlier full run failed the unchanged concurrent plan-CAS test;
its isolated run and the final full gate passed without assertion or source
changes. No independent product review remains pending. Documentation and the
authorized isolated acceptance commit closed Task 9.4. No push is authorized.

Task 9.5 passed independent acceptance under Heavy deployment
`srsw_9_5_20261004`. Private one-image photo staging verifies actual
JPEG/PNG/WebP containers, complete Pillow decode, 10 MiB file bytes and 25 MP.
Fixed expiry, bounded recovery and post-commit save/cancel cleanup preserve
manual saved looks and expose retryable cleanup warnings. A durable publishing
owner precedes filesystem writes; recovery covers partial and published bytes.
Selection performs no inference, and extraction/browser controls remain later
tasks. Final verification passed 2,599 Python tests with one native-symlink
test skipped for Windows permissions, plus 46 privacy/control checks, strict
OpenSpec validation and diff checks. Portable confinement tests passed. No
independent product review remains pending. The authorized isolated local
commit closes this deployment; Task 9.6 and subsequent tasks remain unstarted.

Task 9.6 passed independent acceptance under Heavy deployment
`srsw_9_6_20261004`. Shared image requests require the configured text assistant
and an explicit vision model, with no text-model fallback. Setup proposes only
detected visual models, stores a visual text model explicitly, preserves local
operator declarations and fences stale discovery. Missing capability returns
`409 vision_unavailable`; provider refusal and unusable visual output return
`502 vision_request_failed` without look writes. Text behavior remains compatible.

Final independent gates passed 2,643 Python tests, 542 frontend tests in 25
files, frontend build, 46 privacy/control checks, strict OpenSpec validation
and diff checks. One native-symlink test was skipped for Windows permissions;
the build retains its bundle-size warning. No independent review remains
pending. The authorized isolated acceptance commit closes Task 9.6; Task 9.7
remains unstarted and will connect staged photos to reviewed extraction.

Task 9.7 passed independent acceptance under Heavy deployment
`simplify-next-20261004`. Explicit photo extraction produces editable appearance,
visible garment candidates and unresolved details. Reviewed saving requires
content and removal-order confirmation plus a correction or omission for every
unresolved detail. A stage-bound proposal cannot bypass review through manual
save. Look, durable redacted evidence and stage receipt commit atomically before
cleanup; exact save retries work after cleanup. Image metadata replaces binary
request content, and portable export retains only the safe photo annotation.

Final independent verification passed 2,666 Python tests with one native-symlink
test skipped and three warnings, 13 independent acceptance probes, 46 privacy
and control-character checks, strict OpenSpec validation and diff checks. No
frontend files changed. An implementation rerun failed the existing concurrent
plan-CAS test after an earlier full green run; isolated and final independent
full runs passed without modifying that module or weakening assertions. No
independent review remains pending. The authorized isolated local commit closes
only Task 9.7; Task 9.8 remains unstarted and no push is authorized.
