# Browser E2E harness

The opt-in Playwright suite builds the production React bundle and serves it from
an isolated FastAPI process. It includes the A1 app-render/isolation smoke and
three A2a ModelDetail navigation cases. A2a creates and removes synthetic models
in the temporary database through the real model handlers and tests real model
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

`npm run e2e` runs the isolated production-build helper, three owned-root Node
safety checks, then Playwright with one Chromium worker. The resolved
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

Normal teardown asks the owned child to stop, waits for that exact process, and
removes the temporary root only after checking its owner marker, direct-child
location, and contents for links or reparse points. A successful teardown
also requires the server's clean-stop marker and a zero exit code. Startup
failures try to stop the child before cleanup. If that stop times out, the root
is preserved and its path appears in the error; failed ownership checks also
prevent deletion. These are harness-level checks, not an OS sandbox or network
namespace.

## Independent Windows verification

The verified Windows run used Node.js 22.23.4, npm 10.9.9, Python 3.12.14,
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

Only Windows was executed; the Linux commands above are documented but were not
run. Model and Legacy drafts are verified within the current SPA
ModelDetail instance; reloads and unmounts are outside the guarantee. The delayed
response cases cover successful committed PATCH responses, not failed requests,
offline behavior, or restarts. No real providers, ComfyUI service, GPU, or
generation were exercised.
