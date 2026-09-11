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
- GitHub Actions runs the backend suite and frontend build on Python 3.12 and
  Node 22.

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
