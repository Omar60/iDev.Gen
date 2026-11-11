# Latest Session Work

## A2b-D2 Handoff: Lifecycle Corrected, Functional E2E Still HOLD

Deployment `idevgen-410-a2bd2`, Medium, starts at
`main@a9bbc2e0a60724eee78198e06aafd41270d6ae4d` with fourteen unstaged A2b files.
Main captured all original hashes and byte copies before editing, outside the
repository. No staging, commit, push, product/dependency/CI change is authorized.
The six A2b scenario/fixture assets outside the authorized shared guard/server
edits must keep their original hashes. Documentation is updated for this handoff.

## Cause and Implementation

D1 identified injected tags already present in Node HTTP response bytes before
Chromium/React. The build has 417 bytes; received HTML is contaminated. In the
before-fix lifecycle reproduction CPython Proactor raised WinError10022 before
closing/detaching a transport; Server.wait_closed retained active=2. After stop,
Node killed its owned process at fifteen seconds. The original OS trigger for
that socket error and any causal connection to the injector remain unproven.

- `scripts/e2e_harness/server.py`: choose Windows Selector only, construct loop
  before the existing guard, retain blocking stdin thread and signal stop with
  call_soon_threadsafe. A real deadline bounds server completion after stop.
  Timeout or controlled failure cannot print clean=true.
- `frontend/e2e/lifecycle.mjs`: capture both streams and received event order,
  await child close plus reader drain, record forced termination and code/signal.
  Graceful success requires marker + exit0 + no signal/kill. Do not signal a PID
  whose process has already exited while its pipes remain open.
- `frontend/e2e/global-setup.mjs`: use that observer, report server outcome and
  owned-root removal independently, and reject contaminated HTML in preflight.
- `frontend/e2e/preflight.mjs` plus `fixtures.mjs`: compare exact original build
  bytes; bind the per-document guard to the recorded SHA256 before fulfillment.
  Never strip injected tags, alter the response to pass, or permit another host.
- `frontend/e2e/owned-root.node-check.mjs` and `tests/test_e2e_lifecycle.py`:
  intact/altered HTML, drained output, normal stdin shutdown, forced timeout and
  controlled failure cannot masquerade as a clean exit.

A read-only audit found no required corrections. Its minor finding, duplicated
stdout lines in the startup parser, was removed; the shared observer owns capture.

## Executed Verification

Node 22.23.4, Python 3.12.14, existing pinned Playwright/Chromium, Windows.

- Existing/new Node checks: seven passed.
- Python pertinent command includes test_api, test_e2e_lifecycle,
  test_e2e_session_flow, test_e2e_model_detail, test_e2e_harness_safety,
  test_e2e_harness_independent, test_no_personal_data and test_shoot_checks:
  291 passed, one skip (Windows directory symlink privilege unavailable).
- Full frontend: 1,007 passed across 67 files.
- `npm --prefix frontend run e2e -- --max-failures=1` ran the sanitized real
  build and Node checks, then failed intentionally at HTML integrity preflight:
  expected417 bytes, received906. No functional test body was executed.
- That error still produced termination=graceful, forced=false, code=0,
  signal=none, streams_drained=true, E2E_SERVER_STOPPED clean=true and
  E2E_ROOT_REMOVED clean=true. Independent PID/port/root checks passed.

Raw logs, before.json and original byte copies are retained outside the repo in
an owned temporary A2b-D2 evidence directory. D1 before-fix traces/timelines and
original A2b failure evidence remain separately preserved. Build chunk-size and
existing Python warnings remain; no dependencies were upgraded.

## Continuation and Limits

Lifecycle and integrity correction are ready for review; functional A2b remains
HOLD until a clean environment supplies unaltered HTML. Do not permit the
injected origin, disable the guard, reconfigure user software, or manufacture a
clean response. A clean-HTML Chromium smoke and the full A1/A2a/A2b scenarios
and repetitions remain unexecuted in D2. Linux was not run. Selector's Windows
socket/subprocess limitations fit this one-worker harness with stdin in a thread;
do not apply that choice to the product backend or add asyncio subprocesses here.
No real providers, generation or production data were used. Stop without
staging/commit/push or starting the next task.
