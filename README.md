# iDev.Gen

Photo sessions for LoRA character models, on top of ComfyUI.

**Model (character) → Session → Shots**, the way a real shoot works:

- the **model** is the identity — its LoRA, trigger word, strength and base prompt;
- the **session** is one *look* — wardrobe, hair, styling, setting — held
  identical across every frame;
- the **shots** are the takes that vary: pose, angle, framing, corner of the
  place, with as many variations of each as you want.

Launching a session queues its shots one at a time in ComfyUI, and every
finished image is **moved** out of ComfyUI's output into the session folder.

## Run it

```bash
start.bat
```

Opens <http://127.0.0.1:8777>. The first run creates the virtualenv, installs
dependencies and builds the frontend.

Development (frontend hot reload on port 5273):

```bash
npm --prefix frontend run dev
```

## Setup

Open **Setup** in the top bar on first run. *Detect from ComfyUI* asks the
running instance which path it was launched from and fills the folders in, so
nothing is tied to a particular ComfyUI distribution — portable, git clone or a
model manager all work. Fix anything it gets wrong and save.

Settings land in `config.json`, which is **not in git** (it holds absolute paths
from your machine) and is created from `config.example.json` on first start.
Editing that file by hand is still fine; restart afterwards.

| Key | What it is |
|---|---|
| `comfy_url` | ComfyUI's API. |
| `comfy_output_dir` | The folder ComfyUI saves images into. Required — sessions refuse to run until it points somewhere real, and the top bar says so. |
| `lora_dir` | ComfyUI's LoRA root. Optional: it only powers the `<name>.preview.jpeg` thumbnails model managers store next to each file. |
| `data_dir` | Where the database and the sessions live. Relative to the repo unless absolute. Put it on a drive with room: each session is hundreds of MB. Changing it needs a restart. |

## Workflows

Import your workflow in **API format** (`Workflow → Export (API)` in ComfyUI) and
map which widget drives each slot: base model (so one workflow per family is
enough — the checkpoint is picked per character and per session from a
dropdown), positive/negative prompt, seed, steps, cfg,
width/height, LoRA and its strength, and the filename prefix. The mapping is
auto-detected on import (it follows conditioning links, so it works even when the
prompt goes through `FluxGuidance` and friends) and can be fixed by hand in the
table. **Anything left unmapped keeps the workflow's own value** — so a workflow
full of exotic nodes still works even if only the prompt is driven.

## How a run works

- The queue is **serial**: one photo at a time, one active session. One GPU.
- A shot is written to the database **before** being queued: a failure leaves a
  row with its error, never an orphan job in ComfyUI.
- The filename prefix is forced to `idevgen/<session>/<shot>`, so nothing
  collides with what you generate by hand in ComfyUI.
- Cancel interrupts the running job and marks the rest as cancelled; *Retry*
  puts them back in the queue.

## Tests

```bash
.venv\Scripts\python.exe -m pytest
```

Neither ComfyUI nor a GPU is needed: `tests/conftest.py` swaps the client for a
double that writes the PNG where `SaveImage` would, so the queued graph and the
file move are genuinely verified.

- `test_workflow_map.py` — mapping detection (including prompts behind
  `FluxGuidance` and samplers with non-standard names), widget types, and that a
  connected input is never patched.
- `test_api.py` — look expansion, prompt composition, seeds, validation,
  rating, cascading deletes.
- `test_setup.py` — config detection from ComfyUI's launch path, saving and
  applying it live, and refusing folders that do not exist.
- `test_runner.py` — a full run, the real values in the queued graph, and the
  failure paths: rejected prompt, execution error, missing file, cancellation,
  retry, two sessions at once.

Dev dependencies: `pip install -r backend/requirements-dev.txt`.
CI in `.github/workflows/ci.yml` runs the suite and builds the frontend.

## Cleanup

```bash
powershell -ExecutionPolicy Bypass -File scripts\clean.ps1
```

Removes only what can be regenerated (`__pycache__`, `.pytest_cache`,
`frontend/dist`). `-Deep` adds `node_modules` and `.venv`. `-Data` also deletes
`data/` — sessions and images included — and asks you to type `DELETE` first.

## Publishing the repo

Kept out of git by design: `config.json` (paths from your machine), `data/`
(database, sessions, images) and build artifacts. What ships is the code,
`config.example.json` and this README.

## License

MIT — see [LICENSE](LICENSE).

## Documentation

[Getting started](docs/getting-started.md) ·
[Workflows](docs/workflows.md) ·
[Sessions](docs/sessions.md) ·
[Troubleshooting](docs/troubleshooting.md) ·
[Known limitations](docs/known-limitations.md)

Contributing: [CONTRIBUTING.md](CONTRIBUTING.md). Working rules for the codebase,
for humans and AI agents alike: [AGENTS.md](AGENTS.md).

## Status

MVP. Out of scope for now: multi-LoRA combos, contact sheet / selection export,
and a look library reusable across sessions — see
[known limitations](docs/known-limitations.md).
