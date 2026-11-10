# Project Progress

## Current Deployment: A1 Implemented, Acceptance on Hold

Deployment `idevgen_410_a1_e2e_20261109` implements only the user-defined
4.10/A1 isolated E2E harness from synchronized clean `main@b613474`.
Main owns architecture and acceptance; A1-EXEC implementation, A1-TEST
independent verification, A1-EXP/A1-NET bounded research, and A1-DOC public
documentation and the pending Heavy closure report. No active OpenSpec change
was selected. No staging, commit, push or A2 work is authorized.

## Implemented Boundary

Pinned Playwright/Chromium runs against the production React build served by a
separate owned FastAPI process. A sanitized Vite build uses an empty temporary
envDir. Backend configuration, SQLite, sessions, staging, LoRA and output paths
are synthetic and temporary before application import. The test-only wrapper
permits only bounded reads; raw DNS/TCP/UDP and unexpected browser HTTP,
redirects and WebSockets are denied. One server/worker and fresh browser
contexts preserve the A1 boundary. Product backend, UI and CI remain unchanged.

## Independent Verification

- Full Python with normal xdist: 3,715 passed, three skipped, 18 warnings,
  exit zero in 472.54 seconds; Node 22 was first in PATH for subprocesses.
- Final full frontend with Node 22.23.4: 997 passed, two failed, 999 total in
  67 files. Clean `b613474` reproduces the same two Task 8.2 failures.
- Focused A1 Python: eight passed, one Windows symlink-capability skip.
- Final independent E2E: sanitized build, three Node checks and Chromium smoke passed (one test, one worker).

- Independent lifecycle checks proved clean shutdown after intentional test
  failure, occupied-port refusal, startup mismatch, real 60-second timeout,
  spawn error and early exit. Owned PID, port and temporary-root audits were
  empty; the occupied synthetic listener remained intact.

## Stop Boundary

A2 remains HOLD because the full frontend gate is not green. Earlier Judge
failures did not recur in the final current/baseline runs; no cause or repair
is claimed. Preserve the build chunk warning and five npm advisories. Do not
repair unrelated UI tests, advance A2, add a mandatory E2E CI gate or commit.
Public commands and harness limits are in `docs/e2e-testing.md`.
