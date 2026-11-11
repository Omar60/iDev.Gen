# Browser E2E harness

The opt-in Playwright suite builds the production React bundle and serves it from
an isolated FastAPI process. It includes the A1 app-render/isolation smoke and
three A2a ModelDetail navigation cases, and implemented A2b Submit/Prepare
cases whose independent acceptance is on HOLD. A2a creates and removes synthetic
models in the temporary database through the real model handlers and tests real model
PATCH requests. It does not run ComfyUI, a GPU, or generation, and is not a
required job in `.github/workflows/ci.yml`.

## Requirements and commands

- Node.js 22.x.
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

## A2b-D2 lifecycle correction and environment HOLD

Deployment `idevgen-410-a2bd2` completes the bounded lifecycle/integrity correction;
functional A2b acceptance remains HOLD. Windows verification passed seven Node
checks, 291 pertinent Python checks with one symlink-privilege skip, and 1,007
frontend tests across 67 files. The sanitized production build passed with the
existing chunk-size warning. Checks cover intact/altered HTML, 4,096 stdout lines
plus the clean marker and stderr tail, forced timeout and nonzero exit even with
a marker, and Python normal/timeout/failure shutdown paths.

The real `npm --prefix frontend run e2e -- --max-failures=1` command correctly
failed at integrity preflight: build HTML was 417 bytes, received HTML 906 bytes.
No functional test body ran. The same run reported graceful termination,
`forced=false`, code zero, no signal, drained streams, `E2E_SERVER_STOPPED clean=true`
and `E2E_ROOT_REMOVED clean=true`. Independent checks confirmed the owned PID
absent, exclusive port rebind successful and no new temporary roots.

D1 had located injected HTML in Node HTTP bytes before Chromium/React. Its
before-fix Proactor reproduction retained two active connections and required
forced termination after fifteen seconds; Selector closed cleanly despite the
same HTML injection. The original socket-error trigger and any causal link to
the injector remain unproven. D2 establishes corrected lifecycle behavior and
rejection of altered HTML; it does not establish a clean-HTML Chromium smoke,
a full functional E2E pass or passing repetitions. Those need a clean runner.
No user software, proxy, AdGuard settings or network allowlists were changed.
Windows executed; Linux, real providers, GPU and generation remain unverified.

## A2b session coverage and historical verification

`SessionFlowFixture` in `scripts/e2e_harness/session_flow.py` and
`frontend/e2e/session-fixture.mjs` create two owned synthetic resource-v1
sessions through real handlers. Each starts at plan revision 1 with prior
approval, two ready takes and one incomplete take. Setup uses real preparation
and approval validation; runtime allows only bounded Submit/Prepare POSTs and
owned reads. Review, Approval and Run writes remain denied. Request audits and
SQLite snapshots expose payloads, revisions, pending shots and preparation links.
Cleanup rejects deletion warnings or surviving owned session folders and checks
baseline counts. No product code changed for Submit/Prepare.

- `session-submit.e2e.mjs`: five cases cover double-click uniqueness, late A on B,
  A-to-B-to-A while pending, and separate session/plan reconciliation barriers.
- `session-prepare.e2e.mjs`: four cases cover superseding reloads, selection
  reconciliation, failed session/plan GETs, and an unchanged real B response
  delivered to an A request after committed Submit. Recovery remains explicit.
- `session-guard.e2e.mjs`: one regression copies/freezes response-release options
  before validation, then mutates the caller's options and checks the owned
  sibling listener receives no TCP or HTTP contact. Request barriers support
  before-send and after-response phases; transport failures abort requests.

Before D2, executor selfchecks reported Submit 5/5, guard 1/1 and fixture Python 2 passed.
A1/A2a 4/4 passed before the final shared-helper hardening; this is not a final
aggregate pass. An initial seed returned the expected 422 for four missing
choices; only valid synthetic fixture choices were corrected. An initial Prepare
launch produced four errors before test bodies because that command omitted
`PLAYWRIGHT_BROWSERS_PATH=0`; no browser was installed.

Independent verification then used Node 22.23.4 and the hermetic browser path and
ran only Prepare: exit 1, four FAILED, zero skipped, plus a global teardown
error. All four bodies and session cleanup reached their functional assertions,
but the browser isolation guard rejected respectively 2, 2, 2 and 4 unexpected
cross-origin requests. Traces contain AdGuard content-script indicators; no
matching code was found in application sources or the build. Environment
contamination is indicated, but the exact origin is unproven. Requests remained
blocked; do not allow the unexpected host or disable guards.

That historical global teardown lacked `E2E_SERVER_STOPPED clean=true` and repeatedly reported
asyncio Proactor WinError 10022; causality remains unresolved. Independent
cleanup confirmed the owned PID absent, its port freely rebound exclusively and
no new owned temporary roots, with no baseline cleanup failure or unrelated
process closure. Eventual cleanup does not pass the clean-shutdown gate.
Evidence remains outside the repository as `prepare.log`, `trace-summary.txt`,
`playwright-results`, `roots-before.txt` and `roots-after.txt`.

Those four cases remain historical failures; no functional Submit/Prepare defect
was demonstrated. The original user stop left full E2E, repetitions, full
frontend, final build and Python/privacy gates unexecuted in that deployment.
The D2 results above supersede its lifecycle status and add the stated checks;
they do not revalidate these functional cases. A2c remains on HOLD.

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
