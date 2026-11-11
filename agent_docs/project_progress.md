# Project Progress

## Dated Verification Snapshot — 2026-11-11

By this date, `c365c47c95a5bde0e9d45c303a447bce743a6142` had been published to
`e2e-a2c`; it records the documentation reconciliation and removal of the
temporary A2b workflow. Consult Git for current refs, ancestry and main
integration status rather than treating this snapshot as live state. E2E
phases A1–A2c are complete; accepted Linux and Windows evidence applies to the
specific SHAs recorded below.

A2c-a Review previously passed Linux run 42953660419 with three 22/22 passes.
Subsequent run 42965352388 includes Approval and validates the complete 30-case
inventory, as detailed below.

## Implemented Boundary

Eight new Approval cases use the real Review UI, FastAPI routes and SQLite:
explicit approval and persisted revision; pending A approval through A-to-B-to-A;
late A approval on B; late success after revision change; newer success before an
older stale 409; response after view unmount; stale 409 followed by explicit
reload; and transport failure followed by explicit user retry. Exact POST
identity, payload, status and order, exact Review GET order, SQLite approval
rows/counts and unchanged plans/preparations/shots are asserted. The previous
22 scenarios and their assertions were preserved when adding Approval.

The opt-in Approval fixture starts both owned sessions unapproved and allows only
an exact `{plan_revision}` POST to the owned `/plan/review/approve` endpoint.
FastAPI handles every allowed request. Other product writes remain blocked, and
the fixture exposes raw approval rows for persistence checks. No production
source, contract or dependency changed for Approval.

## Historical Linux Evidence — d674826 — 2026-11-10

[Linux run 42965352388](https://github.com/Omar60/iDev.Gen/actions/runs/42965352388)
passed at exact SHA `d674826`: complete Python 3,729 passed, one opt-in 500-take
benchmark skipped; frontend 1,007/1,007; Node 7/7; privacy/source 46/46; build and
diff-check passed. Three independent Chromium passes each passed 30/30,
including Review 8/8 and Approval 8/8: 90/90 total. Every pass retained intact
HTML and active guards, graceful exit 0 with `forced=false`, drained streams,
`clean=true` stop/root markers, absent owned PID/listener and zero residual roots.

## Accepted Windows Evidence — c365c47 — 2026-11-11

Windows validation used Node 22.23.4, Playwright 1.65.0 and pinned Chromium
158.0.8127.18 (revision 1254). Four independent passes each passed 30/30
(120/120 total), with Review and Approval 8/8 per pass. Frontend passed
1,007/1,007, Node 7/7 and privacy/source 46/46; the build passed. HTML integrity
and clean teardown passed in every run.

Linux [Actions run 43036836646](https://github.com/Omar60/iDev.Gen/actions/runs/43036836646)
for c365 failed both attempts during dependency installation because Ubuntu
package-host DNS resolution failed during apt; the test suites never started.
It provides no Linux E2E result for c365.

The retained A2c workflow audits exactly 30 cases per pass, with one worker and
zero retries, and triggers only on push to `e2e-a2c`. The A2b workflow is retired;
its guide remains historical. A3 is the separate phase for deciding permanent
Windows CI. E2E remains outside the required main CI gates. Node.js 22.x is
required.

Existing dependency, bundle and GitHub Actions warnings were outside this
documentation change. For current integration state, consult Git.
