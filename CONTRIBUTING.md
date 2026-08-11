# Contributing

iDev.Gen is a small, self-hosted tool. Issues, ideas and pull requests are
welcome.

Working rules for the codebase — including the invariants that break silently —
live in [AGENTS.md](AGENTS.md). Read that before your first change; it applies to
humans and AI agents alike.

## Before you write code

For anything bigger than a typo, open an issue first. It is the cheapest way to
find out that something is already in progress or does not fit the direction.

Bug reports are far more useful with: what you did, what happened, the shot's
error text from the gallery, and your ComfyUI version (the top bar shows it).

## Dev setup

You only need Python to work on the backend, only Node to work on the frontend.

### Backend

Python 3.11 or newer.

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.venv\Scripts\python.exe -m uvicorn main:app --app-dir backend --port 8777
```

On Linux/macOS use `.venv/bin/python` instead. `start.bat` does all of the above
on Windows, including building the frontend.

### Frontend

Node 22 or newer.

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

The dev server runs on port 5273 and proxies `/api` to the backend on 8777, so
run both. For a production check, `npm --prefix frontend run build` writes
`frontend/dist/`, which the backend serves at <http://127.0.0.1:8777>.

`frontend/dist/` is **not** committed — it is built at install time by
`start.bat` and by CI.

### Configuration

The app writes `config.json` on first start from `config.example.json`, and the
Setup screen fills in the folders by asking ComfyUI where it was launched from.
`config.json` is gitignored: it holds paths specific to your machine.

## Tests

```bash
.venv\Scripts\python.exe -m pytest
```

No GPU, no running ComfyUI, no network: a fake ComfyUI writes the PNG the real
`SaveImage` would. A fix lands with the test that would have caught the bug.

CI runs the same suite plus a frontend build on every push and pull request.

## Pull requests

- One topic per PR; conventional commit subject (`feat:`, `fix:`, `docs:`…).
- Tests green, and a new test for any behaviour you changed.
- No personal data anywhere — paths, names, emails and tokens are checked by
  `tests/test_no_personal_data.py`.
- Update `README.md` and the matching page in `docs/` when behaviour changes.
