# Project Progress

## Goal

Implement the active OpenSpec change `simplify-resource-session-workflow` one
independently reviewable task at a time, using an external executor for routine
production and retaining architecture, integration, and acceptance in the main
agent.

## Overall Progress

OpenSpec is a valid `spec-driven` change with 10 of 72 tasks complete. Task 1.1
was accepted in `616a6d54d1ddbb2ec97444b0d2dea2a0e16fe8fd`, Task 1.2 was
accepted in `a51abc3f2feca82d8cc2fcbfa332f0cd8777f059`, and Task 1.3 passed
independent review and its aggregate final gate. Tasks 1.4, 1.5, and 1.6
have now passed independent acceptance and are formally closed. Task 2.1 has
also passed independent acceptance and is formally closed. Task 2.2 has now
passed independent acceptance and is formally closed. Task 2.3 has now passed
independent acceptance and is formally closed. The completed
1.1-1.6 integration gate covers the browser selection lifecycle, canonical
import/replay, compatibility, safe public projections, real frontend contract
coverage, concurrency, recovery, and payload-free library filtering. Task 2.1
adds the shared actual-streamed-body limit boundary described below.

## Current Position

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
boundary and sealed manual persistence; the reproducible collection is 2,046
tests.

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

## Next Milestone

Task 2.5 is the next pending task. Do not begin it automatically; prepare its
bounded handoff only when explicitly requested.
