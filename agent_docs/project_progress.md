# Project Progress

## Current Deployment: A2c-b Approval E2E

Deployment `a2c_b_approval_e2e`, Medium, starts at validated local branch
`e2e-a2c@4a796bee394593518eb4371d0ae43e608da2abf6`. Changes remain unstaged
and uncommitted. No push, PR, merge, production change, dependency change, or CI
edit was made.

A2c-a Review was independently validated in Linux run 42953660419 at this base:
three full Chromium passes of 22/22, Review 8/8 each, with clean graceful
lifecycle receipts. Those runs predate A2c-b.

## Implemented Boundary

Eight new Approval cases use the real Review UI, FastAPI routes and SQLite:
explicit approval and persisted revision; pending A approval through A-to-B-to-A;
late A approval on B; late success after revision change; newer success before an
older stale 409; response after view unmount; stale 409 followed by explicit
reload; and transport failure followed by explicit user retry. Exact POST
identity, payload, status and order, exact Review GET order, SQLite approval
rows/counts and unchanged plans/preparations/shots are asserted. The old 22
scenario files and their assertions are unchanged.

The opt-in Approval fixture starts both owned sessions unapproved and allows only
an exact `{plan_revision}` POST to the owned `/plan/review/approve` endpoint.
FastAPI handles every allowed request. Other product writes remain blocked, and
the fixture exposes raw approval rows for persistence checks. No production
source, contract, dependency or CI workflow changed.

## Verification and Next Boundary

Python harness plus existing API approval lifecycle: 5 passed. Frontend: 1,007
passed; Node lifecycle checks: 7 passed; privacy/source: 46 passed; normal and
sanitized builds passed with the existing large-chunk warning. The new Approval
file passed 8/8, and the full Chromium inventory passed 30/30 on Windows with
preflight HTML integrity and active egress guards. The server exited gracefully
with `forced=false`, code 0, drained streams, `clean=true` stop/root markers;
all four owned PIDs and listeners from the local attempts are absent.

The Linux workflow still audits 22 cases. Before Linux validation, update its
inventory from 22 to 30, add `session-approval.e2e.mjs: 8`, and update the
expected collection/failure summary to include Approval 8/8. The workflow was
left untouched, so A2c-b has no Linux run yet.

GO to the separately scoped Linux workflow-inventory update. HOLD on A2c-b
acceptance until a clean Linux run validates all 30 cases and lifecycle gates.
