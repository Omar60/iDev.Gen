# Latest Session Work

## A2c-a Review E2E Handoff

Deployment `idevgen-410-a2ca`, Medium, implements only Review E2E and its
indispensable synthetic fixture support. Local branch `e2e-a2c` starts at
`cf063991826ea895179f2643486571e7eb3e7b10`. Leave changes unstaged/uncommitted;
do not publish, merge, modify main or launch another task.

## Contract and Implementation

Real Review routes are GET `/api/sessions/{id}/plan/review?plan_revision=N`
and GET `/api/sessions/{id}/plan/takes/{take_id}/review?plan_revision=N`.
They return persisted preparation, final prompt and current revision; stale
revision requests return 409. Approval is a distinct POST and is forbidden in
the Review fixture, along with Prepare, Submit and Run product writes.

`session-review.e2e.mjs` adds eight scenarios: persisted preparation and explicit
refresh; late A delivered on B or after A-to-B-to-A; late aggregate/direct take
review after revision advance and session reload; Review stage reentry with an
older read pending; 409 and aborted-read recovery only after explicit reload.
Exact review counts/order, request bodies, IDs/revisions, full SQLite plan and
prepared rows, untouched B, and zero implicit operations are asserted. Session
changes leave Review through the real Takes stage before loading the next ID,
while retaining the old response barrier; direct Review-to-Review jumps are not
a new A2c-a claim. Revision-race cases reload through an A-to-B-to-A route cycle,
so they exercise session epoch and revision fencing together.

`session-fixture.mjs` opts into `{review:true}` only for these tests. The existing
session-flow control creates unapproved distinguishable A/B plans, then permits
one explicit PATCH control `{session_id,expected_revision:1}` to advance only
an owned session to revision 2 through real save/prepare services. Controls are
audited separately from runtime GETs. No API response is manufactured. The
server routes this fixture PATCH without logging it as a model PATCH. Normal
A2b mode, browser egress, identity, HTML preflight and lifecycle remain enforced.

`test_e2e_session_flow.py` exercises real ASGI handlers, persisted prompt identity,
read-only effects, forbidden writes, invalid controls, unchanged B, stale 409,
current revision 2, exact audit counts and restoration of baseline counts.

## Current Evidence and Limits

Windows Node22.23.4/Python3.12.14: pertinent API/harness/privacy Python 292 passed,
one symlink skip; frontend 1,007 passed in 67 files; Node seven passed; sanitized
build passed. Collection-only metadata outside the repository listed all22
cases without starting a server or executing browser tests. Syntax checks passed.

The isolated real preflight failed on the known environmental HTML injection:
417 expected bytes versus 906 received. The response was rejected unchanged.
Teardown reported graceful, forced=false, code0, no signal, drained streams,
clean stop and removed root. Independent owned PID/port/root checks passed.
No functional browser body was executed. No product defect is established.

## Required Linux Validation

Use a fresh clean Linux environment, Node22/Python3.12, project requirements,
`npm ci`, hermetic pinned Playwright Chromium and active guards. Run build,
Node7, exact-HTML preflight, full Python/frontend/privacy/source checks, then
Review8 and the full22 suite. Repeat the full suite in three independent CLI
runs, one worker, zero retries, fresh server/context ownership each run; never
repeat A1 in one server. Audit each owned PID/listener/root and require graceful
exit0 without kill. Assert the actual cases, not just a successful process exit.

The workflow still triggers only on `e2e-a2b-validation`; this local branch is
not published and no CI change was made. Any Linux publication/execution needs
separate user authorization. A2c-a functional acceptance remains HOLD until
that evidence exists; the implementation is ready for Linux validation.
