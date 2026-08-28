# iDev.Gen

Photo sessions for LoRA character models, on top of ComfyUI.

**Model (character) → Session → Shots**, the way a real shoot works:

- the **model** is the identity — its LoRA, trigger word, strength and base prompt;
- the **session** is one *look* — hair, makeup, the place, the light — held
  identical across every frame, plus a **wardrobe** every take starts from;
- the **shots** are the takes that vary: pose, angle, framing, corner of the
  place, and what is worn, with as many variations of each as you want.

Launching a session queues its shots one at a time in ComfyUI, and every
finished image is **moved** out of ComfyUI's output into the session folder.

The wardrobe is written into **every take** rather than stated once above them,
and a take that sets its own wins. That is what lets one shoot open a jacket,
push a top up and end with none of it: stated once, the wardrobe would be
prepended to the very take asking for the jacket off, and a positive that both
describes and denies a jacket keeps the jacket. Written per take, each frame
states its own truth — and the pieces the takes leave alone stay word for word
identical, which is what holds a wardrobe together across twenty photos.

A shot can also be a **reference take**: instead of painting from noise it edits
a photo the session already produced, and its prompt is an instruction
(`remove the jacket`) carrying no trigger, base prompt or look. That is the way
to change one thing while *keeping the photograph* — same face, same pose, same
room. Needs a second workflow (img2img, FLUX.1 Kontext, Qwen-Image-Edit); see
[sessions](docs/sessions.md#reference-takes).

A session is created with a **kind**, which is what turns those two paths into
four jobs the app can actually guide:

| Kind | What it shoots |
|---|---|
| **Photoshoot** | New photos from the look. Text to image, no reference. |
| **Photo edit** | Instructions on one photo: wardrobe off, a new pose, another background. Four expressions come as chips, in the words they were measured in. |
| **Camera angles** | The camera walked around one photo with an angle LoRA. The vocabulary is closed, so the takes are built from a picker instead of typed. |
| **Scene + subject** | Two reference photos into one frame — a character and a garment, a character and a place. |

The kind picks the right workflow (tag your graphs once on the *Workflows*
screen), starts the takes with the right defaults, and prints the one rule that
decides whether that kind works — for angles, *anchor on the widest frame you
have*. See [sessions](docs/sessions.md#session-kinds).

The four are one workflow, not four. **→** on any finished photo continues with
it — the same session switched to that kind, or a fresh session with the photo
copied in as its reference. The photo never leaves the app.

## Library

Sessions are reachable through the model that owns them — **Library** lists
them across every model. Each session carries free-text **tags** edited on the
session view; the library has a search box (matches the name, look and
wardrobe) and the tags currently in use as chips. Tags survive a session being
cloned, and are matched whole: a query of `night` lists a `night` session and
not a `nightclub` one. See [sessions](docs/sessions.md#tags-and-the-library).

## Slideshow

**Slideshow** plays the keepers across every session in a random order, full
screen, advancing on a timer. Read-only: it shows photographs and changes
nothing. Three settings ride in the URL — `interval` (seconds, 1–60), the
inclusive `min_rating` threshold (0–5) and `lookahead` (1–10 photographs
decoded ahead of their turn). Defaults fall back to a working slideshow when a
value is absent, out of range or not a number.

The threshold and the interval also have pickers in the bar over the
photograph, so a phone never has to edit a query string; changing one writes it
back into the URL, so a home-screen shortcut keeps carrying the configuration.

`min_rating=0` is what makes the screen useful on day one: every finished,
un-rejected photograph is unrated at first, and the bar with thirteen
photographs in it is what the same screen looks like once a few sessions
have been rated. See [slideshow](docs/slideshow.md) for the full page.

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

### Reaching the app from a phone

`start.bat` binds loopback only, which is the right default — a copy of the
app on a public repository should not, by default, listen on the network.
To open the app on a phone on the same network, run **`start-lan.bat`**
instead: it prints a warning that the whole app is being exposed with no
authentication, then binds every interface. Anyone on that network can read
the photographs, delete sessions and queue generations; use it on a trusted
network only. The phone's address is shown when the server starts — type it
into the phone's browser. See [slideshow](docs/slideshow.md) for the
full-screen mode and the settings that ride in the URL.

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
| `llm_url` | Optional. An OpenAI-compatible endpoint for the prompt assistant — a local Ollama or LM Studio, or a hosted one (`https://api.minimax.io/v1` and friends). Empty turns the assistant off. *Find an assistant* in Setup probes the usual ports and fills it in. |
| `llm_model` | The model that writes. Setup lists what the endpoint has, biggest first. |
| `llm_vision_model` | Optional. Used when a photo is sent; falls back to `llm_model`. Setup lists only the models that can actually read one. |
| `llm_key` | Optional. Only a hosted endpoint needs one. |

## Writing the prompts

With an endpoint set, the app writes the text it has always asked you to write —
and writes it by the rules that are otherwise only in these docs:

- a **brief** — *“a rooftop at sunset, streetwear, standing, sitting and
  walking”* — fills the look, the wardrobe and as many takes as you ask for,
  none of them repeating what the two boxes already state;
- **🎲** writes the brief too, from the look and the wardrobe a photo was read
  into: a shoot that room and those clothes could plausibly be a frame of, and a
  different one every roll — how fast it moves and how it reads are picked here
  rather than left to a sampler that answers the same question the same way.
  **How far it goes is yours**, from the box beside it: clothed throughout,
  dressed to undressed, dressed to penetration, or explicit from the first
  photograph. The dice roll inside that choice and never across it;
- **🎬 The whole shoot** turns one sentence into a session that goes somewhere:
  *“starts dressed and undresses step by step, keeping the stockings on”* becomes
  N takes **in order** and the N wardrobes that walk beside them, each carrying
  over word for word what the one before it did not change and never naming a
  garment that has come off. Written in rounds of eight and stitched, because
  asked for forty at once an assistant answers thirty-two stubs and spends the
  whole arc by line nineteen — measured, not assumed. A take that still names a
  garment its own wardrobe has put down is outlined in red;
- **👗 Wardrobe per take** does the wardrobe half alone, on takes you already
  wrote;
- **✨** on a take rewrites that one line, as a description for a photoshoot take
  and as an instruction for an edit, and **↩** puts back what it said;
- **📷 Look and wardrobe from a photo…** reads a photo into both boxes — the
  hair, the place and the light into the look, the clothes into the wardrobe —
  and never the person, because the character comes from the LoRA and another
  face written here fights it in every frame;
- for **camera angles** it ticks the picker's chips instead of writing prose,
  because the LoRA's vocabulary is closed and prose it drops looks exactly like
  prose it read.

All of it is a suggestion in an editable box. Nothing is generated, queued or
changed by it — see [sessions](docs/sessions.md#writing-the-prompts).

## The canvas

**Canvas** on the model form and on the new-session panel picks the shape a
shoot is painted on: portrait 832x1216 (the default), portrait 4:5, square,
9:16 and 16:9. Width and height stay beside it for anything off the list.

A platform's own pixel size is deliberately not on that menu. **A delivery size
is a crop of a finished photograph, not a canvas** — cropping to 1080x1350 or
1080x1920 costs nothing and can be redone, while shooting it means re-running
the whole session to change a crop. Anything wider than 16:9 is not offered at
all: with a whole body in frame the sampler paints two of her. See
[sessions](docs/sessions.md#the-canvas).

## Workflows

Import your workflow in **API format** (`Workflow → Export (API)` in ComfyUI) and
map which widget drives each slot: base model (so one workflow per family is
enough — the checkpoint is picked per character and per session from a
dropdown), positive/negative prompt, seed, steps, cfg,
width/height, LoRA and its strength, the filename prefix, and — for a graph that
edits an existing photo — up to three reference images, denoise and reference
strength. The mapping is
auto-detected on import (it follows conditioning links, so it works even when the
prompt goes through `FluxGuidance` and friends) and can be fixed by hand in the
table. **Anything left unmapped keeps the workflow's own value** — so a workflow
full of exotic nodes still works even if only the prompt is driven.

Give each graph a **kind** while you are there (text to image, photo edit,
camera angles, scene + subject): a session of that kind then offers it, and
offers nothing else. Untagged graphs stay offered everywhere, so an existing
setup keeps working untouched.

## How a run works

- The queue is **serial**: one photo at a time, one active session. One GPU.
- A shot is written to the database **before** being queued: a failure leaves a
  row with its error, never an orphan job in ComfyUI.
- The filename prefix is forced to `idevgen/<session>/<shot>`, so nothing
  collides with what you generate by hand in ComfyUI.
- Cancel interrupts the running job and marks the rest as cancelled; *Retry*
  puts them back in the queue.
- **↺ Reshoot** on a finished photo deletes it and puts that same take back in
  the queue with a fresh seed — the row is reused, so the gallery keeps one card
  per take. It is refused on the session's reference photo, which the edits
  behind it need. Nothing else deletes a photo you did not ask to delete.
- **Reshoot below N★** runs ↺ for every finished shot under the threshold in
  one click — same rules, same row, same fresh seed, and a `done` session
  reopens to `draft`. The threshold follows the rating filter (4★ with
  *Picks only*, 1★ otherwise); the button names the count and asks first
  because the photos are deleted.
- **Download picks** and **Contact sheet** beside the rating filter export the
  session's selection — as a ZIP numbered in shooting order, or as a single
  grid image with every frame labelled by its file name.
- A run is refused rather than started when a choice would be silently ignored —
  a base model or LoRA the workflow does not map, a reference take with no
  reference photo. The checks apply to the graph that will actually run: a
  session whose pending takes are all edits never loads the first workflow.
- **⚙ Settings** on a session fixes the workflows and the base model after the
  fact, because that is when a wrong dropdown shows up — see
  [sessions](docs/sessions.md#fixing-a-session).
- **⧉ Clone** shoots the whole session again with the base model and the steps
  changed and everything else — look, wardrobe, takes, composed prompts, seeds —
  identical, so two checkpoints are compared on a shoot instead of on one lucky
  frame. Pick several models in the panel and each one gets its own copy, named
  after it, with its own step count. **Compare with…** above the gallery then
  puts a photo and its twin on one frame under a wipe, and offers only the
  copies of that shoot — nothing else holds the same takes. The pair is the
  take's id, not its seed, so it survives reshooting either side; the wipe says
  when the two seeds have parted. See
  [sessions](docs/sessions.md#cloning-a-session-onto-another-model).

## Component Catalogue & Judging

Prompt components are stored in the database across three slots (**camera**, **act**, **framing**) and three manners (**directed**, **candid**, **selfie**).

- **Catalogue (`#/catalogue`)**: View and manage components, edit prompt wordings and blind viewer `judge_label`s, and retire/restore or delete them. Each row shows the evidence recorded against it — `arrived N of M`, the cell state, and `contradicted` counted apart from the other misses — or `not measured` when no photograph has been judged against it. A component with evidence cannot be deleted, only retired.
- **Act components carry their cameras**: an `act` row lists the camera families it can be seen from, strongest first. An arrangement handed a camera that cannot see it renders as a different arrangement, so the camera plan moves those photographs onto one of the listed families. Left empty, the plan leaves the photograph where it was dealt.
- **Import Measured Catalogue**: On a fresh install, the store starts empty. The "Import Measured Catalogue" button (or `POST /api/components/import`) imports the measured set from `data/catalogue-seed.json`. Composing or creating a written session on an empty catalogue is refused until components are added or imported.
- **Judging (`#/judge`)**: Judge shots against neutral labels (`judge_label`) without seeing prompt wordings. The **Contradiction** option (key `C`) records a frame whose body and camera disagree — counted as a miss, and also counted as a contradiction so the two failures stay apart. Camera and act only: each manner carries a single framing, and a forced choice over one option is not a question.
- **Cell Backups**: Database migrations automatically dump legacy evidence rows to `data/cell-backup-<timestamp>.json`.

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
- `test_api.py` — look expansion, prompt composition (the per-take wardrobe
  overriding the session's, and a reference take carrying neither base nor look),
  seeds, validation, rating, cascading deletes.
- `test_setup.py` — config detection from ComfyUI's launch path, saving and
  applying it live, and refusing folders that do not exist.
- `test_runner.py` — a full run, the real values in the queued graph, reference
  takes going through the second workflow with the anchor uploaded, and the
  failure paths: rejected prompt, execution error, missing file, missing
  reference, cancellation, retry, two sessions at once.

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
[Slideshow](docs/slideshow.md) ·
[Troubleshooting](docs/troubleshooting.md) ·
[Known limitations](docs/known-limitations.md)

Contributing: [CONTRIBUTING.md](CONTRIBUTING.md). Working rules for the codebase,
for humans and AI agents alike: [AGENTS.md](AGENTS.md).

## Status

MVP. Out of scope for now: multi-LoRA combos and a look library reusable across
sessions — see [known limitations](docs/known-limitations.md).

