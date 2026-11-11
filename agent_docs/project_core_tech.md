# Project Core Technologies

## Languages and Runtimes

- Python 3.11+ for the backend and command-line scripts.
- JavaScript/JSX with Node 22+ for the frontend toolchain.

## Frameworks and Libraries

- FastAPI with Uvicorn provides the HTTP application server.
- Pydantic defines backend request models; HTTPX provides asynchronous HTTP
  calls to ComfyUI and optional assistant endpoints.
- Pillow handles image inspection and image-related application utilities.
- React 19 with React DOM renders the UI. Vite builds and serves it, and
  Vitest runs frontend tests.
- SQLite is accessed through Python's standard-library `sqlite3`; the project
  intentionally has no ORM.

## Build, Test, and Development Tools

- `start.bat` bootstraps `.venv`, installs backend requirements, builds the
  frontend when needed, and starts Uvicorn on port 8777.
- `npm --prefix frontend run dev` provides frontend development serving.
- Backend verification is `python -m pytest`; frontend verification is
  `npm --prefix frontend test`; frontend production output is checked with
  `npm --prefix frontend run build`.
- The opt-in Playwright E2E suite is `npm --prefix frontend run e2e`; it uses a
  sanitized Vite production build and isolated FastAPI harness. Its test-only
  ModelDetail fixture permits scoped writes to synthetic models. The A2b session
  fixture uses real handlers for synthetic resource-v1 plans, bounded
  Submit/Prepare writes, request audits, SQLite snapshots and response barriers.
  A2c Review is GET-only: its fixture starts unapproved, exposes one separately
  audited revision-advance control through real handlers, and checks complete
  persisted rows remain unchanged by reads. `session-approval.e2e.mjs` adds
  eight cases for the separate, revision-bound Approval action. A2c-a Review
  passed Linux validation; A2c-b Approval has local Windows evidence but no
  Linux result, and the current Linux workflow still audits 22 cases.
  Byte-exact HTML preflight and SHA256-bound document checks reject altered
  responses. Lifecycle monitoring distinguishes graceful, forced and failed
  outcomes, drains process streams and separately audits owned-root removal; the
  Windows Selector loop has a bounded shutdown deadline, while Linux retains its
  normal loop. A2b passed Linux run 42942686174. Setup, current evidence and
  limits are in `docs/e2e-testing.md`; canonical deployment state is in
  `agent_docs/project_progress.md` and `agent_docs/latest_session_work.md`.
- GitHub Actions runs the backend suite on Python 3.12 after installing the
  frontend lockfile with Node 22 and `npm ci`; a separate Node 22 job runs
  `npm ci` and builds the frontend.

## External Services and Infrastructure

- A reachable ComfyUI instance performs image generation and exposes its HTTP
  API. iDev.Gen does not load models itself.
- An OpenAI-compatible LLM endpoint is optional and powers prompt/vision
  assistance when configured.
- The application uses local filesystem paths for configuration, SQLite data,
  session images, ComfyUI output, and optional LoRA thumbnails.

## Important Technical Constraints

- `config.json` contains machine-specific paths and remains untracked;
  `IDEVGEN_CONFIG` and `IDEVGEN_DATA_DIR` overrides must remain supported.
- ComfyUI paths are configuration-driven; code must not assume one ComfyUI
  distribution layout.
- Tests must not require a GPU, running ComfyUI, or network access; use the
  repository's fakes and temporary paths.
- The application is local and unauthenticated. Loopback binding is the safe
  default; LAN binding is an explicit, warned-about mode.
- The runner processes one session serially, and legacy session behavior must
  remain isolated from `resource-v1` behavior.
