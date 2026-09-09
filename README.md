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
five jobs the app can actually guide:

| Kind | What it shoots |
|---|---|
| **Photoshoot** | New photos from the look. Text to image, no reference. |
| **Photo edit** | Instructions on one photo: wardrobe off, a new pose, another background. Four expressions come as chips, in the words they were measured in. |
| **Camera angles** | The camera walked around one photo with an angle LoRA. The vocabulary is closed, so the takes are built from a picker instead of typed. |
| **Scene + subject** | Two reference photos into one frame — a character and a garment, a character and a place. |
| **Guided paint** | New photos from the look, with a photograph steering one thing — the clothes, or the body's orientation. Not an edit: the character and the checkpoint stay the session's. |

The kind picks the right workflow (tag your graphs once on the *Workflows*
screen), starts the takes with the right defaults, and prints the one rule that
decides whether that kind works — for angles, *anchor on the widest frame you
have*. See [sessions](docs/sessions.md#session-kinds).

A guided take only works where the line stays silent about the thing the
photograph is there to carry: with the garments written, a wardrobe reference
lands 0 of 9 at every strength, and struck out of the line 3 of 3. See
[sessions](docs/sessions.md#guided-takes).

The kinds are one workflow, not five. **→** on any finished photo continues with
it — the same session switched to that kind, or a fresh session with the photo
copied in as its reference. The photo never leaves the app.

## Composing from the catalogue

A take can also be **dealt** instead of written: one camera, one act and one
framing out of the measured component catalogue, joined into a line with no
writer request. That is what makes a photograph *evidence* — a written line is a
new sentence every time, so a bad frame says nothing about which part was wrong,
while a composed one is three catalogue entries and can be scored by the blind
judge. `Compose` deals variety; `Fill` takes one cell to its threshold. Trios
that contradict themselves are refused before anything is queued — the frame
reaches the lowest part of the body the line names, so a `waist-up` framing in a
line that names her feet is not a crop, it is a contradiction.

A compose runs in one of two modes. **strict** draws only cells the judge has
verified; **exploratory**, the default, also draws cells nothing has been judged
on yet. Neither draws a dead one. A run that cannot fill the count asked for is
**refused rather than padded with repeats**: every check runs before any
insertion, so a request queues all of its photographs or none, and the 422 names
the largest count that would have worked. On a young catalogue strict is the
mode that queues nothing, which is the table being honest and not the feature
being broken. See [sessions](docs/sessions.md#composing-from-the-catalogue).

## Resource libraries

The resource path keeps complete accepted entries and immutable revisions in the
local SQLite database without changing the legacy Rooms import. The SQLite
resource database path (`/api/resources/...`) preserves accepted source entries
in their original language as private local storage, keeping nested structures
and original strings intact without flattening. The original payload is stored
separately from English translations and derived field coverage.

Lacking a required English translation does not discard or silently translate
an accepted resource; instead, its preparation readiness remains pending until
translated. Source payloads and private translations remain local and untracked;
tracked code, UI, documentation, and tests remain strictly English-only.

Fused scene resources can be stored complete in the resource database. Decomposing
a fused scene into separate camera, act, and room components is required only
when targeting legacy room seeds or measured component catalogue rows;
decomposition is not a prerequisite for resource storage or resource-session
preparation. Unresolved template placeholders in a stored fused scene remain
retained in the source payload, but block final prompt preparation until
explicitly resolved.

Resource-based session drafts (`resource-v1`) do not depend on the measured
component catalogue or catalogue cell uniqueness. Deliberate repetition of
cameras or poses is fully supported, and an empty measured catalogue does not
block resource draft creation or preparation.

The app exposes library and exact-revision inspection at
`/api/resources/libraries` and
`/api/resources/revisions/{library_key}/{source_id}/{content_digest}`. Revision
details include provenance, digests, translation, coverage and readiness; a
digest is required so an older revision is never silently replaced by a newer
one.

Imports are two explicit operations at `/api/resources/import/preview` and
`/api/resources/import/commit`. Preview reads and classifies selected JSON files
without writing resource libraries or revisions; it does create private local
attestation metadata required for the one-time commit. Commit consumes the
serialized preview, rechecks the source fingerprints and writes the accepted
set atomically. A changed source requires a fresh preview, and a refresh creates
a new revision while missing entries remain reported rather than deleted.
Required English translations gate readiness; accepted untranslated data remains
inspectable and pending.

The same service is available from the CLI:

```bash
python scripts/import_resources.py preview --selection PATH=LIBRARY_KEY \
  --preview-out preview.json --report-out report.json
python scripts/import_resources.py commit --preview preview.json \
  --report-out report.json
```

The CLI and app produce the same safe report. Report artifacts contain counts,
identifiers, classifications and digests, not source prose or machine paths.
The private preview file carries a one-time local attestation; its signing
secret stays in the configured data directory and is never serialized. It
expires after one day and is consumed by a successful commit, so do not move
or edit the preview between preview and commit.

The web UI provides a dedicated **Resources** view (`#/resources`):
- **Inventory Browser**: Filter imported resources by free-text and data kind,
  inspect readiness, exact immutable revision identities
  (`library_key` + `source_id` + `content_digest`), and classified field roles
  (`descriptive input`, `selection metadata`, `writer guidance`, `auxiliary data`,
  `unused data`).
- **Import Preview & Commit**: Input source file selections, run a preview to
  verify all outcomes (`new`, `unchanged`, `updated`, `unresolved`, auxiliary,
  duplicates, missing), and commit verified sets into SQLite.
- **Start Session**: Ready and mapped revisions can start a `resource-v1`
  session draft bound to an explicitly chosen character model, with no CLI or
  external scripts needed.

### Database backup and operational rollback

Before migrations or schema upgrades, create a verified WAL-consistent snapshot of the SQLite database:

```bash
python scripts/backup_db.py
# or specify an explicit target:
python scripts/backup_db.py -o data/backups/manual-backup.db
```

The backup utility uses SQLite's online backup API, validates schema integrity (`PRAGMA integrity_check`), and writes atomically. Note that database backups store database records and metadata; session images live in `<data folder>/sessions/`.

To operationally disable the resource planning feature without destructive schema rollbacks:
- Set `"resource_planning_enabled": false` in `config.json`, or export `IDEVGEN_RESOURCE_PLANNING_ENABLED=0`.
- All legacy sessions, resource revisions, plan history, and finished shots remain fully readable and intact.
- Persistent writes and draft preparations in `resource-v1` mode are cleanly refused with HTTP 503.
- Re-enabling the flag (`true` or `1`) restores write capabilities immediately without data loss.

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
| `room_libraries` | Optional. List of room libraries for session looks (each entry has `name`, `seed_file`, `enabled`, and `weight`). Defaults to `candid-rooms-seed.json` (the shipped nine rooms). An import adds its own entry here in the same operation that writes the seed. The `weight` multiplies onto each room's own weight in the random draw — how much of the draw the whole library takes, against how often one of its rooms comes up among its neighbours. At `0` the library is never drawn from and its rooms stay pickable by hand, which `enabled: false` does not: that hides them. |
| `room_word_budget` | Optional, default `200`. The longest room, in words, a compose will accept; over it the run is refused naming both numbers. **It refuses nothing anybody has** and is meant to: sessions 395-400 varied room length alone and the camera arrived 7/10 at 0 words rising to 10/10 at 176, which is the opposite of a length cost. The longest room in the imported corpus is 89 words. Keep it as a tripwire for an absurd input — a refusal beats a silent truncation. |
| `checkpoints` | Optional. Sampler settings per base model, keyed by the filename ComfyUI reports: `steps`, `cfg`, `sampler`, `scheduler`. A session picking that checkpoint is handed them; blank or unknown fields are ignored rather than reset. Nothing here is app behaviour — it is a fact about the files on your machine, and it is meant to be edited by hand. |

## Rooms

**Rooms** describes the legacy seed import path that populates `room_libraries`
and measured catalogue rows. It is distinct from the SQLite resource database
path (`/api/resources/...`). Give it the folder holding the source JSON and the
path to the translation map beside it; nothing is guessed and no example path is
shipped, because both are paths on your own machine.

The import is all or nothing and translation-first. Refused libraries, refused
entries and the translation lookup all run over the whole upload before a single
seed file is touched, so an upload carrying one string the map does not cover
writes nothing and comes back with every uncovered string listed by entry and
field — a worklist for the translation map, not a warning. A successful import
writes each seed file and registers it in `room_libraries` in the same operation:
either half alone is the state the registry check refuses.

A **fused** library — one whose entries name a camera position, an act and a
room in a single string — is refused by Rooms and imported by
`scripts/mine_perspective_scenes.py` instead when preparing measured catalogue
rows and room seeds. Stored whole in that legacy context, such an entry is a
room that overrules the line's camera, so it is cut into one row per part first:
the camera and the act land in the component catalogue as unverified rows and
only the room part reaches a seed file. Where every cut falls is read from a
curated map beside the source material, never guessed, and the mining also needs
a family per entry and a judge label per row — three untracked files the operator
writes, all of them beside the corpus and none of them in this repository. The
combination each entry was split into is recorded in
`data/mined-combinations-seed.json` as row keys and fingerprints, no prose, so
the photograph the entry produced can be composed again by name. (This
curated decomposition is required strictly for measured catalogue and legacy
seed outputs; the resource database path stores accepted fused resources intact
without mandatory decomposition.)

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
- **📷 Wardrobe from a photo…** reads the clothes out of a photo into the
  wardrobe box — never the look, and never the person, because the character
  comes from the LoRA and another face written here fights it in every frame;
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
camera angles, scene + subject, guided paint): a session of that kind then
offers it, and offers nothing else. Untagged graphs stay offered everywhere, so an existing
setup keeps working untouched.

## Resource session plan preparation

`resource-v1` session plans persist preparation one take at a time. A caller
first records a take as `pending`, then finalizes that same
`(session_id, plan_revision, take_id)` as `ready` with its complete prompt,
effective state, mapping/compiler versions, and provenance snapshot. Reopening
the plan reports `ready` and `generated` takes as completed and `pending` or
missing takes as resumable; it does not regenerate anything during the read.

Completed or invalidated snapshots are history. Repeating the exact completed
snapshot is idempotent, while an attempt to replace it with different data is
refused. Persistence failures are returned as errors and leave interrupted work
pending without discarding already completed snapshots.

`POST /api/sessions/{sid}/plan/preparations/submit` submits a `ready` snapshot
to existing shot creation and the serial queue, transitioning the take to
`generated` and recording its linked shot id. Submissions are unique per
prepared revision: retries return the existing shot without creating duplicates.
The shot carries the frozen prompt directly without re-composition. Queue
execution in ComfyUI still requires launching the session with
`POST /api/sessions/{sid}/run`.

Graph-kind rules govern reference takes and generation submission:
- **Text-to-image** (`reference: false`): submits the full frozen prompt
  without re-composing trigger, base prompt, look, or wardrobe, preserving the
  session checkpoint and character LoRA.
- **Reference edit** (`kind != 'guide'`): runs bare instructions without base
  model, character LoRA, look, or wardrobe, allowing the editing workflow's
  own nodes and reference photo to govern.
- **Guided paint** (`kind == 'guide'`): paints from noise, retaining the full
  composed prompt, session checkpoint, and character LoRA while using the
  reference photo for conditioning.

**Instructional continuity is not pixel-level continuity.** Persisting exact
source revisions, deterministic effective wardrobes, and frozen final prompts
guarantees reproducible instructions and state history across takes, but does
not guarantee pixel-identical images or exact physical garment geometry (button
count, seam placement, fabric drape) across renders. Words describe attributes,
not exact pixels. Visual reference workflows remain available when an existing
photograph must be held while modifying specific elements, but neither reference
workflows nor text-to-image prompts provide pixel-perfect continuity or parity
with external compiled rendering runtimes. Legacy sessions do not use these
plan-preparation routes. See [sessions](docs/sessions.md#resource-plan-preparation).

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

Prompt components live in the database across three slots (**camera**, **act**,
**framing**) and three manners (**directed**, **candid**, **selfie**). The
**Catalogue** screen (`#/catalogue`) manages them and shows the evidence on each
row — `arrived N of M`, the cell state, contradictions counted apart. The
**Judge** screen (`#/judge`) scores photographs blind against neutral labels,
never against the prompt wording.

The store ships **empty**: press *Import Measured Catalogue* once, or composing
and creating legacy sessions refuse until it holds something. `resource-v1`
session drafts do not require the measured catalogue and are not subject to this
refusal or to catalogue cell uniqueness.

The detail — the reading vocabulary, the two scopes, the pass refusal, the
camera families an act carries — is in [judging](docs/judging.md) and
[sessions](docs/sessions.md#component-catalogue--judging).

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
- the rest cover what was measured into the app rather than written into it:
  the catalogue store and its seed, the cell evidence, the crop law, the camera
  plan, the readings, the arrangements and the kiss frames, the database
  migrations, and that no personal data or tracked image reaches the repository.

The frontend has its own: `npm --prefix frontend test`.

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
[Judging](docs/judging.md) ·
[Catalogue measurements](docs/catalogue-measurements.md) ·
[Asking for candidates](docs/catalogue-candidate-prompt.md) ·
[Slideshow](docs/slideshow.md) ·
[Troubleshooting](docs/troubleshooting.md) ·
[Known limitations](docs/known-limitations.md)

Contributing: [CONTRIBUTING.md](CONTRIBUTING.md). Working rules for the codebase,
for humans and AI agents alike: [AGENTS.md](AGENTS.md).

## Status

MVP. Out of scope for now: multi-LoRA combos and a look library reusable across
sessions — see [known limitations](docs/known-limitations.md).

