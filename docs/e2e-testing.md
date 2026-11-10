# Browser E2E smoke

The opt-in Playwright smoke builds the production React bundle, serves it from a
fresh FastAPI process with one synthetic model and session, and checks that the
browser can render the app while the harness blocks external access. It does
not run ComfyUI, a GPU, generation, or product write flows. It is not a required
job in `.github/workflows/ci.yml`.

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

The ASGI boundary is read-only. It permits `GET`, `HEAD`, and `OPTIONS` only for
`/api/components`, `/api/config`, `/api/comfy/loras`, `/api/comfy/models`,
`/api/comfy/status`, `/api/models`, `/api/sessions`, `/api/wardrobe`, and
`/api/workflows`; `/api/components` also accepts `?all=1`. `GET` to
`/__e2e/identity` is the separate health route. The only other test route is
`GET /__e2e/redirect?port=<1-65535>`, which emits a controlled 302 to a sibling
loopback listener for the redirect-blocking probe. Static access is limited to
`/`, `/index.html`, and files below `/assets/`, without query strings. The
middleware returns 403 for other methods, paths, or queries before product
handlers run. The smoke also confirms outside-root config and file-preview
requests are denied without exposing a synthetic sentinel.

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

## Independent A1 verification

The independent Windows run used Node.js 22.23.4, npm 10.9.9, Playwright
1.65.0, and Chromium for Testing 158.0.8127.18 (revision 1254). The sanitized
build, all three Node safety checks, and Chromium smoke passed (one test, one
worker). A synthetic parent `VITE_*` sentinel was absent from `dist`; loopback
HTTP, redirect, and WebSocket probes left the sibling listener's TCP, request,
redirect, and upgrade counters at zero. A two-worker override was rejected
before temp-root creation or Python startup.

Lifecycle checks confirmed that an intentional browser-test failure returned
exit 1 while emitting the clean-stop marker, removing the owned process and
port, and leaving no owned temporary roots. Cleanup also passed parent PID
mismatch, the full 60-second startup timeout, spawn failure, and early exit.
An occupied test port was refused without contact; its owner listener remained
intact. Only Windows was executed; the Linux commands above are documented but
were not run. This narrow A1 smoke does not replace the full frontend suite or
verify later write/generation flows.

This smoke covers the initial A1 app-render and isolation checks only. Any later
A2 flow must explicitly extend the test-only path/method allowlist and its
negative safety checks for the endpoints it needs.
