# Browser E2E harness

The opt-in Playwright suite builds the production React bundle and serves it from
an isolated FastAPI process. Its 30 cases comprise the A1 smoke, three A2a
ModelDetail cases, ten A2b session/guard cases, and eight each for A2c Review
and Approval. A2a creates and removes synthetic models through real handlers
and tests real PATCH requests. The suite does not run ComfyUI, a GPU, or
generation. The general Ubuntu workflow in `.github/workflows/ci.yml` runs the
backend suite and frontend production build. The separate permanent Windows
workflow in `.github/workflows/e2e-windows.yml` runs this E2E suite for pull
requests targeting `main` and pushes to `main`, using
`scripts/e2e_harness/validate-windows.ps1` for Windows lifecycle and process
audits. A1–A3 are complete; historical A1–A2 evidence below remains scoped to
its recorded SHA.

## A3 acceptance — 2026-11-11

A3 is integrated into `main` at `d235127578683138b2e1e7df9c3a03e861ab43f1`.
PR #2 merged by fast-forward at `ce9f495e`; the temporary A2c workflow was
removed in `d235127`. The CI and Windows E2E workflows remain separate. Their
automatic triggers do not establish that either workflow is configured as a
required status check in branch protection; verify that repository setting
separately.

[General CI run 43121838365](https://github.com/Omar60/iDev.Gen/actions/runs/43121838365)
and [Windows E2E run 43121838436](https://github.com/Omar60/iDev.Gen/actions/runs/43121838436)
both passed on push to `main` at `d235127` (attempt 1). General CI passed the
backend suite (3,729 passed, one skipped) and frontend build. Windows passed
Python (3,729 passed, one skipped), frontend 1,007/1,007, Node lifecycle 7/7,
privacy/source controls 46/46, build, HTML preflight, and diff-check. The
Python skip was the opt-in 500-take synthesis benchmark.

The Playwright inventory was exactly Smoke 1, ModelDetail 3, Submit 5, Prepare
4, Guard 1, Review 8, and Approval 8. Each of three independent passes passed
30/30 with one worker, zero retries, no skips or flaky results, and a new
FastAPI server, SQLite database, identity, and temporary root. The symlink test
was `executed-passed`, not skipped. `E2E_HTML_PREFLIGHT clean=true`. The smoke
test verified blocked browser HTTP, redirect and WebSocket probes with zero
sibling-listener hits; backend HTTPX connections used synthetic-provider hosts
only; the automatic fixture passed for every spec. External Windows
lifecycle audits passed for preflight and every pass: server identity matched
the harness; exit was graceful with code 0, `forced=false`, and drained
streams; `E2E_SERVER_STOPPED` and `E2E_ROOT_REMOVED` reported `clean=true`; the
harness-owned PID and port were absent; no marked temporary roots or
harness-owned Chromium processes remained.

The runner was Windows Server 2025 with Node 22.23.4, Python 3.12.10,
Playwright 1.65.0, and Chromium 158.0.8127.18 (revision 1254). The workflow
installs locked Node dependencies with `npm ci` and the Playwright-pinned
Chromium build without `--with-deps`. Failure-only artifacts contain synthetic
logs and traces and are retained for three days.

Known limits: the opt-in 500-take benchmark was skipped; Python dependency
ranges are still floating; hosted runner images and patch versions can change;
non-blocking dependency and Actions warnings remain; and E2E does not exercise
real providers, ComfyUI, a GPU, or image generation.

## Historical A2 acceptance evidence — 2026-11-11

Accepted Windows validation at `c365c47` used Node 22.23.4, Playwright 1.65.0
and pinned Chromium 158.0.8127.18
(revision 1254). Four independent Chromium passes each passed 30/30 (120/120
total), including Review 8/8 and Approval 8/8 in every pass. Frontend passed
1,007/1,007, Node checks 7/7 and privacy/source checks 46/46; the build passed.
HTML integrity and clean teardown passed in all four runs.

[Historical Linux run 42965352388](https://github.com/Omar60/iDev.Gen/actions/runs/42965352388)
passed at `d67482658fa9a2a4f8c8c93433e09fc37dfdf248` only. It used Node 22.23.4,
Python 3.12 and Playwright-pinned Chromium. The three independent passes each
passed 30/30 (90/90 total), including Review 8/8 and Approval 8/8 per pass.
Python passed 3,729 tests with one opt-in 500-take synthesis benchmark skipped;
frontend passed 1,007/1,007, Node 7/7, privacy/source 46/46, build and
diff-check. Each server had intact HTML, active egress guards, graceful exit 0,
`forced=false`, drained streams, `clean=true` stop/root markers, absent owned
PID/listener and no remaining owned temporary roots. This Linux result applies
only to `d674826`.

[Linux Actions run 43036836646](https://github.com/Omar60/iDev.Gen/actions/runs/43036836646)
for `c365c47` had two installation attempts blocked by DNS resolution of an
Ubuntu package host during apt dependency installation. Both stopped before the
test suites, so they produced no Linux E2E result for c365.

Commit `c365c47` was published to `e2e-a2c` and removed the temporary A2b
workflow. Check Git for current branch refs, ancestry and integration status;
this dated evidence is not a live status report.

At the time of the `c365c47` snapshot, the temporary
`.github/workflows/e2e-a2c-validation.yml` ran only on pushes to `e2e-a2c`, with
a matching job-level ref check, exactly 30 tests per pass, three fresh-server
passes, one worker, and zero retries. It was later removed from `main` in
`d235127` after the permanent Windows workflow was accepted. The retired A2b
workflow remains in Git history, and its decisions and original procedure are
preserved in [the historical A2b guide](e2e-a2b-validation.md).

## Requirements and commands

- Node.js 22.x; use a Node 22 release.
- Python 3.11+ with `backend/requirements.txt` installed in the repository
  `.venv`. A valid `.venv` is required: the harness reads `pyvenv.cfg`,
  resolves its base interpreter, runs it with `-S`, and supplies only that
  virtual environment's `site-packages` through `PYTHONPATH`. It has no
  system-Python fallback or interpreter override.
- [`frontend/package-lock.json`](../frontend/package-lock.json) pins
  `@playwright/test`, `playwright`, and
  `playwright-core` to 1.65.0. `npm run e2e:install` installs the matching
  Chromium build; the current version is Chromium for Testing 158.0.8127.18
  (revision 1254). Set `PLAYWRIGHT_BROWSERS_PATH=0` to keep that browser beside
  the frontend package.

From the repository root, create the Python environment if needed, then run the
frontend commands. Use a Python 3.11+ launcher available on your system.

PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Set-Location frontend
$env:PLAYWRIGHT_BROWSERS_PATH = '0'
npm ci
npm run e2e:install
npm run e2e
```

Linux:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
cd frontend
export PLAYWRIGHT_BROWSERS_PATH=0
npm ci
npm run e2e:install
npm run e2e
```

`npm run e2e` runs the isolated production-build helper, seven owned-root/lifecycle/integrity Node
checks, then Playwright with one Chromium worker. The resolved
Playwright worker count must be exactly one; an override fails before the
temporary root is created or the backend starts. Browser installation is a
separate, one-time step for each Playwright version.

The build helper creates a fresh owned temporary directory for Vite's `envDir`
and a temporary config that imports the repository Vite config with that empty
directory override. It runs Vite in production mode with the `runner` config
loader. The child environment contains only `PATH`, temporary-directory
variables, and required Windows variables; parent `VITE_*` values and project
`.env` files are not available to the build. Vite still writes its production
bundle to `frontend/dist`; the temporary config and environment directory are
removed afterward.

## Isolation and limits

Before Python imports the application, Playwright global setup creates a unique
OS temporary root with a random owner marker. The synthetic config, SQLite
database, session files, staging, LoRA, output, and runtime identity all live
under it. The Python process validates the root and config/data overrides,
seeds synthetic records, then verifies the imported app paths still resolve
inside that root. No real `config.json` or `data/` is used.

The server owns an ephemeral `127.0.0.1` TCP port bound before Uvicorn starts;
it requests `SO_EXCLUSIVEADDRUSE` where the platform provides it and does not
probe or reuse a chosen port. Readiness verifies the child PID, launching
parent PID, port, random nonce, and root token; `/__e2e/identity` then verifies
the child PID, port, nonce, and root token. The server runs one worker.

The ASGI boundary defaults to read-only access. It permits `GET`, `HEAD`, and
`OPTIONS` for `/api/components`, `/api/config`, `/api/comfy/loras`,
`/api/comfy/models`, `/api/comfy/status`, `/api/models`, `/api/sessions`,
`/api/wardrobe`, and `/api/workflows`; `/api/components` also accepts `?all=1`.
While the A2a model fixture is active, it additionally permits `GET /api/rooms`
and `GET`/`PATCH` only for its two owned model IDs. The test-only
`/__e2e/model-detail` route accepts `GET`, `POST`, and `DELETE` to inspect,
create, and remove those synthetic models. Creation and deletion use the real
model handlers; PATCH reaches the real API handler and records its payload and
status. All other product writes remain denied. `GET /__e2e/identity` is the
health route. `GET /__e2e/redirect?port=<1-65535>` emits a controlled 302 to a
sibling loopback listener for the redirect-blocking probe. Static access is
limited to `/`, `/index.html`, and files below `/assets/`, without query
strings. Other methods, paths, and queries return 403 before product handlers
run. The harness also denies outside-root config and file-preview requests
without exposing a synthetic sentinel.

The backend's raw socket guard denies every DNS lookup, socket
`connect`/`connect_ex`/`create_connection`, and UDP `sendto`/`sendmsg`, including
loopback. The already-bound listener remains available to Uvicorn; HTTPX
provider calls use synthetic ComfyUI and assistant transports, and inherited
proxy variables are removed. Playwright blocks service workers, cross-origin
HTTP, all HTTP redirects, and every WebSocket. Expected denial probes are
checked separately from unexpected blocked requests. The browser also probes a
sibling ephemeral `127.0.0.1` listener over HTTP, WebSocket, and a redirect;
the fixture checks that its TCP connection, HTTP request, redirect, and
WebSocket upgrade counters all remain zero.

Before functional tests, `preflight.mjs` compares the root HTML response
byte-for-byte with the production `index.html`. Its SHA256 is saved in runtime
state; the browser guard checks every document response against those build
bytes and that reference before fulfillment. A mismatch aborts delivery without
stripping content or relaxing cross-origin, redirect or WebSocket denial.

Normal teardown asks the owned child to stop. `lifecycle.mjs` records received
stdout/stderr and process events in order, waits for child close and both stream
readers to drain, and distinguishes graceful, forced and failed termination.
Graceful success requires the clean-stop marker, exit zero, no signal and no
forced termination. Owned-root removal has a separate result and remains subject
to owner-marker, direct-child and link/reparse-point checks. An unclosed child
or undrained streams preserve the root; an already-exited PID is not signaled.

The Python harness selects SelectorEventLoop only on Windows, before installing
the egress guard. A blocking stdin thread signals an asyncio event through
`call_soon_threadsafe`; a ten-second deadline bounds actual server completion
after stop. Timeout or failure cannot emit `clean=true`. Linux retains its normal
event loop. This choice belongs to the one-worker test harness, not the product
backend; do not introduce asyncio subprocesses here. These are harness-level
checks, not an OS sandbox or network namespace.

## A2b Linux acceptance and historical D2 Windows block

A2b passed Linux run 42942686174 at validated base
`cf063991826ea895179f2643486571e7eb3e7b10`: full Python 3,727 passed with one
opt-in skip, frontend 1,007 passed, seven Node checks passed, and three fresh
Chromium runs each passed all 14 A1/A2a/A2b cases with clean lifecycle audits.
This validates the A2b tree only; it does not validate A2c's Review cases or
changed fixture helpers.

The earlier A2b-D2 Windows run was blocked before browser tests by altered HTML:
the build was 417 bytes and the delivered response 906 bytes. Preflight rejected
the response unchanged. Teardown was graceful (`forced=false`, exit 0, no signal,
streams drained, `clean=true`), the owned PID was absent, the exclusive port was
reusable, and no new temporary roots remained. An earlier Windows attempt also
recorded unexpected cross-origin requests and a Proactor shutdown error; their
exact source and causal relationship were not established. D2's Selector and
lifecycle correction did not remove or allow injected content. No security
settings or network allowlists were changed. That earlier A2c Windows attempt
stopped at preflight; the later A2c-b Windows run passed locally, as recorded
below.

The A2b session fixture creates synthetic A/B sessions through real handlers;
Submit and Prepare use bounded writes, request barriers, audits and SQLite
snapshots. Their ten cases cover five Submit cases, four Prepare cases and one
egress-guard regression. Runtime Review, Approval and Run writes are denied.

## A2c Review and Approval coverage

`frontend/e2e/session-review.e2e.mjs` adds eight cases against the real Review
GET routes: persisted plan review and explicit take refresh; late A responses on
B or after A-to-B-to-A; late plan/take review after a real revision change and
reload; stage reentry through Takes with an older request pending; and recovery
from stale-revision 409 or transport failure only after explicit reload. The
cases assert request IDs/revisions/order, complete temporary plan and preparation
rows, exact control/runtime audits, and zero runtime product writes. Review is
GET-only; it does not invoke Approval.

The opt-in Review fixture starts with two unapproved synthetic plans. Its single
owned revision control advances A from revision 1 to 2 through the real save and
prepare handlers; the control is audited separately from Review reads and B is
asserted unchanged. Review reads must leave full SQLite rows unchanged. The
fixture continues to reject product POSTs, including Approval, Submit, Prepare
and Run. No API response is manufactured.

`frontend/e2e/session-approval.e2e.mjs` adds eight Approval cases for explicit
revision-bound approval; A-to-B-to-A navigation and late-response isolation;
revision changes and out-of-order responses; unmount; and recovery after stale
revision or transport failure only through explicit reload or retry. The cases
verify approval rows and reviewed revisions against SQLite snapshots while
plans, prepared takes and shots remain unchanged.

A2c-a Linux Actions run 42953660419 passed all 22 cases in each of three fresh
Chromium passes, including Review 8/8 each time, with clean server and root
lifecycle audits. This validates Review, not Approval.

A2c-b passed locally on Windows: all eight Approval cases and the full 30-case
Chromium inventory passed with the real React build, FastAPI app and SQLite,
`workers=1`, and `retries=0`. HTML preflight and the active egress guard passed.
The pertinent Python harness/API checks passed 5, frontend passed 1,007, Node
lifecycle checks passed 7, privacy/source checks passed 46, and normal and
sanitized builds passed; the sanitized build retained the existing large-chunk
warning. Lifecycle reported graceful exit 0, `forced=false`, drained streams,
and `clean=true` for stop and root; all owned PIDs were absent and ports free.
This local functional evidence does not establish Linux validation.

Subsequent Linux run 42965352388 validated Approval 8/8 and Review 8/8 in each
of three complete 30/30 passes at `d674826`, as recorded above. Its workflow
audits all seven scenario files and exact collection/pass totals. This closes
A2c-b's Linux acceptance; it does not apply to later SHAs.

## Historical A2a Windows verification

The accepted A2a Windows run used Node.js 22.23.4, npm 10.9.9, Python 3.12.14,
Playwright 1.65.0, and Chromium for Testing 158.0.8127.18 (revision 1254).
`npm --prefix frontend run e2e` passed three Node safety checks and four
Chromium tests: the A1 smoke and all three A2a cases. To repeat just the new
cases three times from the repository root, set `PLAYWRIGHT_BROWSERS_PATH=0`
and run:

```sh
node frontend/node_modules/@playwright/test/cli.js test --config=frontend/playwright.config.js model-detail.e2e.mjs --repeat-each=3
```

That run passed nine tests with one worker and no retries. The full frontend
suite passed 1,007 tests across 67 files. Relevant Python checks passed 285
tests with one skip because Windows symlink privilege was unavailable. The
sanitized production build passed with its existing chunk-size warning. Final
Chromium shutdown checks confirmed a clean-stop marker, no owned process,
successful exclusive port rebinds, and no new owned temporary roots in
before/after inventories. Loopback HTTP, redirect, and WebSocket probes left
the sibling listener counters at zero. A two-worker override was rejected
before temporary-root creation or Python startup.

### Historical A1-only checks (not rerun for A2a)

The original A1 run confirmed a synthetic parent `VITE_*` sentinel was absent
from `dist`. A1 lifecycle checks confirmed an intentional browser-test failure
returned exit 1 while emitting the clean-stop marker, removing the owned process
and port, and leaving no owned temporary roots. Cleanup also passed parent PID
mismatch, the full 60-second startup timeout, spawn failure, and early exit. An
occupied test port was refused without contact; its owner listener remained
intact.

For that historical acceptance, only Windows was executed; the Linux commands
above are documented but were not run. Model and Legacy drafts are verified
within the current SPA
ModelDetail instance; reloads and unmounts are outside the guarantee. The delayed
response cases cover successful committed PATCH responses, not failed requests,
offline behavior, or restarts. No real providers, ComfyUI service, GPU, or
generation were exercised.
