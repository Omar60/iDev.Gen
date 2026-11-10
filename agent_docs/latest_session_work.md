# Latest Session Work

## Current Handoff: Isolated E2E A1, Acceptance on Hold

Deployment `idevgen_410_a1_e2e_20261109` began at synchronized clean
`main@b613474`. The user selected Heavy and authorized only 4.10/A1. Main
owns architecture/acceptance and these deployment-state documents; A1-EXEC
owns production/tooling, A1-TEST independent verification, A1-EXP/A1-NET
bounded discovery, and A1-DOC public documentation and closure reporting.

## Delivered Architecture

The opt-in command `npm --prefix frontend run e2e` uses Node 22 and pinned
Playwright 1.65.0 / Chromium 158.0.8127.18 revision 1254. It builds real React
with a sanitized environment and owned empty Vite envDir, validates the local
project venv, creates token-marked temporary app paths, and starts a separate
loopback FastAPI process on an exclusively bound ephemeral socket.

Windows uses the venv's base interpreter with `-S` and explicit venv packages
to preserve one directly owned PID. Readiness and health bind PID, parent PID,
port, nonce and root token. The backend receives synthetic config/data before
import. A test-only read allowlist blocks product writes and path escapes;
HTTPX providers are synthetic and raw DNS/TCP/UDP are denied, including local
services. Browser contexts reject unexpected HTTP, redirects and WebSockets,
block service workers, and use owned loopback listeners for negative probes.
Resolved worker overrides are rejected before root/process creation.

Teardown requests a stop, waits for the owned process and clean-stop marker,
and validates ownership and link/reparse boundaries before removing its root.
These are process-level controls, not an OS sandbox. A2 must deliberately
extend the read-only allowlist and its safety tests before testing write flows.

## Independent Evidence

Full normal-xdist Python passed 3,715 tests with three skips and 18 warnings,
exit zero in 472.54 seconds. The final full frontend run used confirmed Node
22.23.4 and passed 997 of 999 tests in 67 files. Both failures are existing
Task 8.2 `Generate shared suggestions` assertions, reproduced in a complete
clean `b613474` source export with the same Node/dependencies. Earlier Judge
failures did not recur in final runs; they remain unclassified, not repaired.
Focused final A1 Python passed eight tests with one Windows symlink skip.

Independent final E2E passed the sanitized production build, three Node checks
and Chromium smoke (one test, one worker). Synthetic loopback negative probes
received no connections. Worker overrides failed before Python/root creation.
An intentionally failed browser test still produced clean shutdown; independent
checks also exercised occupied-port refusal, PID-parent mismatch, the real
60-second startup timeout, spawn ENOENT and early exit. Own PID/port/root audits
were empty and the unrelated synthetic listener remained intact. A temporary
early-exit checker originally expected stop on an already-exited child; its
corrected rerun passed and both logs remain. Logs remain outside the repository. Five npm advisories (two moderate, three high) and the existing
Vite large-chunk warning remain. Windows executed; Linux commands are documented
without claiming a Linux execution.

## Continuation

HOLD A2 while the required full frontend gate remains non-green. Do not expand
A1 into unrelated UI corrections, staging, commit, push or mandatory CI work.
The public runbook is `docs/e2e-testing.md`. Main accepts the A1 harness checks
within their documented read-only scope; overall acceptance and A2 remain on
hold for the frontend gate. Archivist owns the closing documentation/Git and
exact token report. No acceptance commit is authorized.
