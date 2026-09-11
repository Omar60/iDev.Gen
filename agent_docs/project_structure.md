# Project Structure

Verified baseline from the repository layout and application entry points.

## Directory Layout

- `backend/` contains the Python application and domain modules.
- `frontend/src/` contains the React UI, hash-based routing, views, helpers,
  and frontend tests. `frontend/dist/` is generated build output.
- `tests/` contains the backend pytest suite and ComfyUI test doubles.
- `scripts/` contains import, backup, mining, analysis, and operational tools.
- `docs/` contains user-facing guidance; `README.md` is the short project
  overview and documentation index.
- `openspec/` contains the main specifications and change artifacts.
- `data/` and `config.json` are local runtime state and configuration and are
  not repository deliverables.

## Modules and Responsibilities

- `backend/main.py` defines the FastAPI application, request models, HTTP API,
  configuration handling, and static frontend serving.
- `backend/db.py` owns SQLite schema, migrations, transactions, and queries.
- `backend/comfy.py` wraps the ComfyUI HTTP API and detects/applies workflow
  node mappings. `backend/runner.py` queues and serializes session shots and
  moves completed images into session folders.
- Session authoring is split across `session_plan.py`,
  `resource_preparation.py`, and prompt/resource modules. Resource import,
  immutable storage, readiness, and translations are handled by the
  `resource_*` modules.
- Legacy room imports and catalogue preparation use `importer.py`,
  `room_registry.py`, `extractor.py`, `cut_map.py`, `mining.py`, and
  `translation_map.py`. Catalogue composition, judging, wardrobe, and image
  handling are exposed through the API and corresponding frontend views.

## Main Interfaces and Integration Boundaries

- The browser communicates with the backend through `/api/...` endpoints.
- The backend persists application metadata in SQLite under the configured data
  directory and stores session images in its sessions subdirectory.
- ComfyUI is an external image-generation service reached over HTTP. Workflow
  graphs are stored in ComfyUI API format together with a node map.
- The optional prompt assistant uses an OpenAI-compatible HTTP endpoint through
  `backend/enhance.py`; it is not required for local operation.

## Tests and Supporting Assets

- Backend tests run from `tests/` with pytest. `tests/conftest.py` provides a
  `FakeComfy` that records graphs and writes representative output files, so
  tests do not require a GPU, ComfyUI, or network access.
- Frontend behavior tests are colocated with helpers under `frontend/src/` and
  run with Vitest.
- `config.example.json`, shipped seed files, and workflow fixtures provide
  reproducible defaults without containing machine-specific configuration.
