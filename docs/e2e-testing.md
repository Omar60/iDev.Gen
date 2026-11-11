# Browser E2E harness

The opt-in Playwright suite builds the production React bundle and serves it from
an isolated FastAPI process. Its 22 cases comprise the A1 smoke, three A2a
ModelDetail cases, ten A2b session/guard cases, and eight A2c Review cases.
A2b passed its Linux validation; the new A2c cases still require clean-Linux
browser validation. A2a creates and removes synthetic models through real
handlers and tests real PATCH requests. The suite does not run ComfyUI, a GPU,
or generation, and is not a required job in `.github/workflows/ci.yml`.

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
settings or network allowlists were changed. The injection also blocked the
current A2c Windows preflight; no functional Chromium case ran there.

The A2b session fixture creates synthetic A/B sessions through real handlers;
Submit and Prepare use bounded writes, request barriers, audits and SQLite
snapshots. Their ten cases cover five Submit cases, four Prepare cases and one
egress-guard regression. Runtime Review, Approval and Run writes are denied.

## A2c Review coverage and Linux validation

`frontend/e2e/session-review.e2e.mjs` adds eight cases against the real Review
GET routes: persisted plan review and explicit take refresh; late A responses on
B or after A-to-B-to-A; late plan/take review after a real revision change and
reload; stage reentry through Takes with an older request pending; and recovery
from stale-revision 409 or transport failure only after explicit reload. The
cases assert request IDs/revisions/order, complete temporary plan and preparation
rows, exact control/runtime audits, and zero runtime product writes. Review is
GET-only; Approval is a separate action and is never invoked.

The opt-in Review fixture starts with two unapproved synthetic plans. Its single
owned revision control advances A from revision 1 to 2 through the real save and
prepare handlers; the control is audited separately from Review reads and B is
asserted unchanged. Review reads must leave full SQLite rows unchanged. The
fixture continues to reject product POSTs, including Approval, Submit, Prepare
and Run. No API response is manufactured.

Current local checks list 22 Playwright cases but collection is not execution.
Pertinent Python/API/harness/privacy checks passed 292 with one Windows symlink
skip; frontend passed 1,007, Node passed seven, and the sanitized build passed
with the existing chunk-size warning. Windows preflight compared expected 417
HTML bytes with 906 delivered bytes and stopped before test bodies; teardown was
graceful with no kill, exit 0, drained streams, absent PID, reusable port and no
new roots. No Linux A2c browser run has occurred.

For A2c acceptance, use a clean Linux environment with Node 22, Python 3.12,
project development requirements, and the Chromium version pinned by
`frontend/package-lock.json`. From the repository root:

```sh
set -euo pipefail
validation_dir="$(mktemp -d)"
export TMPDIR="$validation_dir/tmp"
mkdir -p "$TMPDIR"
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
npm ci --prefix frontend
export PLAYWRIGHT_BROWSERS_PATH=0
npm --prefix frontend run e2e:install -- --with-deps
test "$(node -p 'process.versions.node.split(".")[0]')" = 22
test "$(.venv/bin/python -c 'import sys; print(sys.version_info[:2] == (3, 12))')" = True
node -e "console.log(JSON.stringify(require('./frontend/node_modules/playwright-core/browsers.json').browsers.find(({ name }) => name === 'chromium')))"
npm --prefix frontend test
.venv/bin/python -m pytest -ra
.venv/bin/python -m pytest -ra tests/test_no_personal_data.py tests/test_shoot_checks.py
npm --prefix frontend run build
node frontend/e2e/build-production.mjs
node --test frontend/e2e/owned-root.node-check.mjs
node --input-type=module -e "import setup from './frontend/e2e/global-setup.mjs'; const stop = await setup({ workers: 1 }); await stop();" 2>&1 | tee "$validation_dir/preflight.log"
node frontend/node_modules/@playwright/test/cli.js test --config=frontend/playwright.config.js session-review.e2e.mjs --workers=1 --retries=0 --max-failures=1 --output="$validation_dir/review-results" 2>&1 | tee "$validation_dir/review.log"
node frontend/node_modules/@playwright/test/cli.js test --config=frontend/playwright.config.js --workers=1 --retries=0 --max-failures=1 --output="$validation_dir/full-1-results" 2>&1 | tee "$validation_dir/full-1.log"
node frontend/node_modules/@playwright/test/cli.js test --config=frontend/playwright.config.js --workers=1 --retries=0 --max-failures=1 --output="$validation_dir/full-2-results" 2>&1 | tee "$validation_dir/full-2.log"
node frontend/node_modules/@playwright/test/cli.js test --config=frontend/playwright.config.js --workers=1 --retries=0 --max-failures=1 --output="$validation_dir/full-3-results" 2>&1 | tee "$validation_dir/full-3.log"
git diff --check
```

The targeted run must pass Review 8/8; each of the next three invocations must
pass all 22 cases. Each CLI invocation creates a fresh server and test contexts;
these are separate runs, not Playwright retries. Before proceeding past any run,
audit its log for exactly one owned server and
`E2E_SERVER_EXIT termination=graceful forced=false code=0 signal=none streams_drained=true`,
`E2E_SERVER_STOPPED clean=true` and `E2E_ROOT_REMOVED clean=true`. Independently
confirm that PID is absent, its listener is absent from `/proc/net/tcp*`, and no
owned `idevgen-e2e-*` roots remain in the isolated temporary directory. Stop on
any HTML mismatch, blocked unexpected request, missing audit or leaked resource.
The local branch has no A2c workflow; no GitHub Actions run or publication is
claimed.

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
