# Project Progress

## Current Position: A2d Integration Preparation

A1, A2a, A2b and A2c are closed at validated branch
`e2e-a2c@d67482658fa9a2a4f8c8c93433e09fc37dfdf248`. A2d-a/D1 reconciles
documentation and retires only the temporary A2b workflow using the Light route.
Integration into `origin/main@b6134746965d1e6344ebce2c0e30dcbf28adef81`
is pending; the eight existing commits remain intact.

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

## Verification and Next Boundary

[Linux run 42965352388](https://github.com/Omar60/iDev.Gen/actions/runs/42965352388)
passed at exact SHA `d674826`: complete Python 3,729 passed, one opt-in 500-take
benchmark skipped; frontend 1,007/1,007; Node 7/7; privacy/source 46/46; build and
diff-check passed. Three independent Chromium passes each passed 30/30,
including Review 8/8 and Approval 8/8: 90/90 total. Every pass retained intact
HTML and active guards, graceful exit 0 with `forced=false`, drained streams,
`clean=true` stop/root markers, absent owned PID/listener and zero residual roots.

The retained A2c workflow audits exactly 30 cases per pass, with one worker and
zero retries, and triggers only on push to `e2e-a2c`. The A2b workflow is retired;
its guide remains historical. A3 will decide replacement with permanent CI;
E2E remains outside the required main CI gates. Node 22.x is required.

Next: separately authorize the cleanup commit/publication, validate its new SHA
on Linux, then decide integration. The accepted run proves `d674826` only and
does not validate the subsequent cleanup. Existing dependency, bundle and
GitHub Actions warnings remain outside this documentation cleanup.
