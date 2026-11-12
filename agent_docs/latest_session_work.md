# Latest Session Work

## CI-W2 Handoff — 2026-11-12

Prepared from `main@1cc55b275c9fb27700165c73a52b2fbb7fc4c978` on
`ci/windows-first`, after D4c acceptance and RoomImport closure. The branch
removes Ubuntu CI and keeps only E2E Windows / `isolated-e2e`, pinned to
`windows-2025`, for PRs targeting any branch and pushes only to `main`.
`E2E_DIFF_BASE` still selects the PR target's base SHA or the preceding main
commit; full history and the ancestry guard are preserved. No product code,
tests, dependencies, A3 inventory or audit thresholds change.

Publication is limited to the branch and a PR targeting `main`. PR Windows
acceptance, main integration and required-status protection are distinct facts;
this handoff does not claim merge or protection. CI-W3 requires checking the
published PR's exact SHA and `isolated-e2e` result. Earlier records below retain
their original Linux/Windows results and SHAs.

## A3 Closure Snapshot — 2026-11-11

A3 is technically closed on `main` at `d235127578683138b2e1e7df9c3a03e861ab43f1`.
PR #2 merged by fast-forward at `ce9f495e`; the temporary A2c workflow was
removed in `d235127`, and `.github/workflows/e2e-windows.yml` is the permanent
Windows E2E workflow. No functional code changes were needed for A3.

- [General CI run 43121838365](https://github.com/Omar60/iDev.Gen/actions/runs/43121838365):
  SUCCESS; Python 3,729 passed, one skipped; frontend build SUCCESS.
- [Windows E2E run 43121838436](https://github.com/Omar60/iDev.Gen/actions/runs/43121838436):
  SUCCESS on push to `main`, attempt 1. Python 3,729 passed, one skipped;
  frontend 1,007/1,007; Node 7/7; privacy/source 46/46; Playwright 30/30 in
  three independent passes; symlink coverage `executed-passed`.
- Windows Server 2025; Node 22.23.4; Python 3.12.10; Playwright 1.65.0;
  Chromium 158.0.8127.18, revision 1254.
- Build, HTML preflight, egress guard, Windows lifecycle audits and diff-check
  passed. Each pass had one worker and zero retries; harness-owned PIDs and
  ports, marked temporary roots, and harness-owned Chromium processes were
  absent after teardown.
- The Python skip was the opt-in 500-take synthesis benchmark. Python
  dependency ranges remain floating. Automatic workflow triggers do not prove
  that Windows E2E is required by branch protection.

Current E2E architecture and full acceptance details are in
[`docs/e2e-testing.md`](../docs/e2e-testing.md).

## Historical A2d Integration Snapshot — before A3 closure (2026-11-11)

As of this snapshot, `c365c47c95a5bde0e9d45c303a447bce743a6142` was published
to `e2e-a2c` and removed the temporary A2b workflow. The current branch refs,
ancestry and integration state must be checked in Git; this dated record is not
a live status source. E2E phases A1–A2c are complete; Linux and Windows
acceptance evidence is recorded separately below by SHA.

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

## Accepted Windows Evidence — c365c47 — 2026-11-11

The accepted Windows validation used Node 22.23.4, Playwright 1.65.0 and its
pinned Chromium 158.0.8127.18 (revision 1254). Four independent E2E passes each
passed 30/30, totaling 120/120; Review and Approval passed 8/8 in every pass.
Frontend passed 1,007/1,007, Node checks 7/7 and privacy/source checks 46/46.
The build passed, the served HTML matched it, and every server shut down
cleanly with `forced=false`, drained streams, absent owned processes/listeners
and no remaining temporary roots.

## Historical Linux Evidence — d674826 — 2026-11-10

[Run 42965352388](https://github.com/Omar60/iDev.Gen/actions/runs/42965352388)
passed at `d674826`: complete Python 3,729 passed with one opt-in benchmark
skipped, frontend 1,007/1,007, Node 7/7, privacy/source 46/46, build and
diff-check. Three independent Chromium passes each passed 30/30, including
Review 8/8 and Approval 8/8, totaling 90/90. HTML was intact; every owned server
exited gracefully with code 0, `forced=false`, drained streams, `clean=true`
stop/root markers, absent PID/listener and zero remaining owned roots.

The later [Linux run 43036836646](https://github.com/Omar60/iDev.Gen/actions/runs/43036836646)
for `c365c47` failed on both attempts during apt dependency installation after
Ubuntu package-host DNS resolution failed. The E2E suites did not start, so
this run produced no Linux test result for c365.

The A2b workflow is retired; its guide and Git history retain the original
decisions and procedure. The A2c workflow is preserved unchanged and triggers
only on push to `e2e-a2c`. A3 will decide whether and how to add permanent
Windows CI. E2E is not a required main CI gate. Node 22.x is required.
Functional source, tests, harness, dependencies, `project_diary.md` and main CI
were outside the documentary A2d changes. Current commands, inventory,
evidence and platform limits are in `docs/e2e-testing.md`.
