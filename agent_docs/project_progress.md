# Project Progress

## Goal

Implement the active OpenSpec change `simplify-resource-session-workflow` one
independently reviewable task at a time, using an external executor for routine
production and retaining architecture, integration, and acceptance in the main
agent.

## Overall Progress

OpenSpec is a valid `spec-driven` change with 16 of 72 tasks complete. Task 1.1
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
complete. Task 4.1 remains pending and Task 4 has not been started.

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

Tasks 3.1 through 3.5 are complete and formally closed. Task 3.5 changes only
its focused tests and closure records; no product code changed, and the
canonical bulk Preview/Apply boundary remains authoritative. Group 3 is
complete, while no work on Task 4.1 or later tasks has started.

Task 3.2 is complete and formally closed. The accepted implementation changes
only the manual source-backed translation workflow and its focused tests;
`backend/resource_translation.py` and `backend/request_limits.py` remain
unchanged. The canonical bulk path remains the semantic authority, and Task 3.4
is now also complete; Group 3 is complete and Task 4.1 is next.

Task 3.5 closure gates were 2,121/2,121 backend tests, 418/418 frontend tests,
85 focused translation tests, 79 focused resource-service tests, 10 privacy
tests, 36 shoot tests, a successful frontend build, strict OpenSpec validation,
and a clean `git diff --check`.

## Next Milestone

Task 4.1 is the next pending task. Do not begin it automatically; prepare its
bounded handoff only when explicitly requested.
