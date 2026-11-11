# Latest Session Work

## A2d Integration Preparation Handoff

A1, A2a, A2b and A2c are closed at
`e2e-a2c@d67482658fa9a2a4f8c8c93433e09fc37dfdf248`. A2d-a/D1 uses the
Light route to reconcile documentation and retire only the A2b workflow.
Integration into `origin/main@b6134746965d1e6344ebce2c0e30dcbf28adef81`
has not occurred. The eight existing commits remain intact.

## Contract and Implementation

Approval is the explicit `POST /api/sessions/{id}/plan/review/approve` with
`{plan_revision}`. The real FastAPI endpoint persists one approval row for the
current revision and returns 409 for a stale revision. Entering Review remains
read-only. `SessionView` already fences results by session, revision, request
sequence and mounted state; this task made no production changes.

`frontend/e2e/session-approval.e2e.mjs` adds eight Chromium cases: explicit
approval/persistence/no automatic approval; pending A through A-to-B-to-A; late A
response on B; stale success after revision advance; current success before an
older stale 409; response after unmount; stale 409 then explicit reload; and
transport abort then explicit retry. Assertions cover session/revision/payload,
response and read order, approval SQLite rows and counts, no unrelated writes,
and unchanged plan/preparation/shot rows. The previous 22 scenarios are
unchanged.

`session-fixture.mjs` adds an Approval-only option. `scripts/e2e_harness/session_flow.py`
creates two unapproved owned sessions and admits only the exact approval path
with an integer `plan_revision` of 1 or 2. Other runtime writes stay blocked.
`tests/test_e2e_session_flow.py` proves strict fixture inputs, real 200/409
responses, duplicate upsert, exact SQLite rows/audits, blocked Submit/Prepare/Run,
and cleanup. The browser harness used the real React build, FastAPI and SQLite;
barriers delayed actual HTTP requests/responses, with no frontend API mocks.

## Historical Local Approval Verification

- `tests/test_e2e_session_flow.py` plus
  `tests/test_api.py::test_api_plan_review_approval_lifecycle`: 5 passed.
- Frontend: 1,007/1,007; Node lifecycle checks: 7/7; privacy/source: 46/46.
- Normal and sanitized builds passed; existing bundle-size warning remains.
- Approval-only Chromium: 8/8. Full existing-plus-new inventory: 30/30,
  one worker and zero retries. HTML preflight passed; egress guard remained
  active; server exited code 0 with `forced=false`, drained streams, clean stop
  and removed root. Independently checked all owned PIDs absent and four ports
  free across attempts.
- Initial Approval runs exposed an unnecessary plan-text locator: rendered text
  was prefixed and duplicated. It was removed while retaining session/revision
  and SQLite assertions. No product defect was found.

## Accepted Linux Evidence and Next Boundary

[Run 42965352388](https://github.com/Omar60/iDev.Gen/actions/runs/42965352388)
passed at `d674826`: complete Python 3,729 passed with one opt-in benchmark
skipped, frontend 1,007/1,007, Node 7/7, privacy/source 46/46, build and
diff-check. Three independent Chromium passes each passed 30/30, including
Review 8/8 and Approval 8/8, totaling 90/90. HTML was intact; every owned server
exited gracefully with code 0, `forced=false`, drained streams, `clean=true`
stop/root markers, absent PID/listener and zero remaining owned roots.

The A2b workflow is retired; its guide and Git history retain the original
decisions and procedure. The A2c workflow is preserved unchanged and triggers
only on push to `e2e-a2c`. A3 will decide its replacement with permanent CI;
it is not a required main CI gate. Node 22.x is required (22.23.4 was validated).

This cleanup is local and awaits a separately authorized commit/publication
and Linux validation of the new exact SHA before integration. Run 42965352388
validates `d674826` only. Functional source, tests, harness, dependencies,
`project_diary.md` and main CI are outside this cleanup. Current commands,
inventory, evidence and platform limits are in `docs/e2e-testing.md`.
