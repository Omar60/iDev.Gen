# Getting started

## Requirements

- A running **ComfyUI** you can reach over HTTP (default `http://127.0.0.1:8188`).
- **Python 3.11+** and, to build the interface, **Node 22+**.
- The LoRA you want to shoot with, already visible to ComfyUI.

iDev.Gen never loads models itself: it queues prompts in ComfyUI and organises
what comes back. Whatever your ComfyUI can generate, iDev.Gen can shoot.

## First run

```bash
start.bat
```

That creates the virtualenv, installs dependencies, builds the frontend and
opens <http://127.0.0.1:8777>. On Linux or macOS, do the same by hand:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend install && npm --prefix frontend run build
.venv/bin/python -m uvicorn main:app --app-dir backend --port 8777
```

## Setup

Open **Setup** in the top bar and press **Detect from ComfyUI**. It asks the
running instance which path it was started from and proposes that install's
`output/` and `models/loras/` folders. Check them, then Save.

| Field | Notes |
|---|---|
| ComfyUI API | Where ComfyUI listens. Change it if you run it on another port or host. |
| ComfyUI output folder | **Required.** Finished images are moved out of here into the session folder. Sessions refuse to run until it exists. |
| LoRA folder | Optional. Only used to show the `<name>.preview.jpeg` thumbnail that model managers store next to each LoRA. |
| Data folder | Database and sessions. Point it at a drive with room; changing it needs a restart. |
| Prompt assistant | Optional, and off until it has an endpoint. **Find an assistant** probes the ports Ollama, LM Studio and llama.cpp listen on, fills the URL in and lists the models that endpoint has — biggest first, and the vision box lists only the ones that can actually read a photo. It writes the look and the takes; see [sessions](sessions.md#writing-the-prompts). |

Three buttons say where it runs — **On this machine**, **OpenAI**, **MiniMax** —
and all three do the same thing: fill the URL in. On this machine probes the
ports Ollama, LM Studio and llama.cpp use; the other two are the provider's base,
and want an API key next to it. Then *List its models*. Any other
OpenAI-compatible endpoint works too — type its base URL over the top and the
buttons stop being lit, which is not an error.

A hosted endpoint answers in a second or two and leaves the GPU to ComfyUI, which
a local model shares with it. Of MiniMax's models only *MiniMax-M3* reads a
photo, so that is the one for the vision box if you want
*📷 Wardrobe from a photo…* or the anchor read.
An endpoint that does not list its models at all is not a problem either: the
boxes stay typeable.

Settings are written to `config.json`, which stays out of git because it holds
paths specific to your machine. Advanced settings can also be edited there:

| Setting | Notes |
|---|---|
| `room_libraries` | List of room libraries (each with `name`, `seed_file`, `enabled`, and `weight`). Defaults to `candid-rooms-seed.json` (the shipped nine candid rooms). `weight` multiplies onto each room's own weight when a room is drawn at random: `0` means never drawn and still pickable by hand, `enabled: false` means not there at all. |
| `room_word_budget` | Optional, default `200`. The longest room, in words, a compose will accept; over it the run is refused naming both numbers. **It refuses nothing anybody has** and is meant to: sessions 395-400 varied room length alone and the camera arrived 7/10 at 0 words rising to 10/10 at 176, which is the opposite of a length cost. The longest room in the imported corpus is 89 words. Keep it as a tripwire for an absurd input — a refusal beats a silent truncation. |
| `checkpoints` | Optional. Sampler settings per base model, keyed by the filename ComfyUI reports: `steps`, `cfg`, `sampler`, `scheduler`. A session picking that checkpoint is handed them; blank or unknown fields are ignored rather than reset. Nothing here is app behaviour — it is a fact about the files on your machine, and it is meant to be edited by hand. |

The **Rooms** screen fills this list in for you: it takes a source directory
and a translation map, writes one room seed file per library and registers each
one here in the same operation. It writes nothing at all if any string in the
upload is missing from the map, and lists the uncovered strings with the entry
and the field each came from. The Setup screen carries `room_libraries` back
untouched when you save, so a saved path does not delete what an import
registered.

A library whose entries fuse a camera position, an act and a room into one
string is not imported here: it is cut into rows first by
`scripts/mine_perspective_scenes.py` when preparing measured catalogue rows and
room seeds, and only the room part of each entry reaches a seed file. (This
decomposition applies strictly to measured catalogue and legacy seed
destinations; see below for the resource database path).

## Resource import API and CLI

Resource import is separate from the legacy room-seed route. The SQLite resource
database path (`/api/resources/...`) preserves accepted source entries in their
original language as private local storage in the SQLite database, keeping nested
structures and original strings intact without flattening. The original payload
is stored separately from English translations and derived field coverage.

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

The app exposes:

- `GET /api/resources/libraries` for library and revision metadata.
- `GET /api/resources/revisions/{library_key}/{source_id}/{content_digest}`
  for one exact immutable revision.
- `POST /api/resources/import/preview` with selected `{path, library_key}`
  pairs. This writes no resource libraries or revisions, but creates private
  local attestation metadata required for the one-time commit.
- `POST /api/resources/import/commit` with the serialized preview returned by
  preview. The commit rechecks every source fingerprint and is atomic.

The CLI uses the same service boundary as the app:

```bash
python scripts/import_resources.py preview --selection PATH=LIBRARY_KEY \
  --preview-out preview.json --report-out report.json
python scripts/import_resources.py commit --preview preview.json \
  --report-out report.json
```

Set `IDEVGEN_DATA_DIR` and `IDEVGEN_CONFIG` as usual. The report artifact is a
safe summary of counts, identifiers, classifications and digests; it does not
contain source prose or machine paths. The serialized preview is an internal
commit input with a one-time local attestation: its secret stays in the
configured data directory, it expires after one day, and a successful commit
consumes it. A changed source requires a fresh preview. Re-importing a modified
source creates an immutable new revision (`content_digest`); entries that
disappear upstream are reported as missing in the import report rather than
deleting previous revision history. Resource readiness is translation-pending
until required English fields are available; importing data does not
automatically make it generation-ready.

## Reaching the app from a phone

The default `start.bat` binds loopback only — a public repository should not,
by default, listen on the network. To open the app on a phone on the same
network, run **`start-lan.bat`** instead. It prints a warning that the whole
app is being exposed with no authentication, then binds every interface.
Anyone on that network can read the photographs, delete sessions and queue
generations; use it on a trusted network only. See the [slideshow](slideshow.md)
page for the full-screen mode and the settings that ride in the URL.

When the server starts, the line that begins `Serving on http://` carries the
address a phone should load. A Windows Firewall prompt is expected the first
time; allow it for the virtualenv's `python.exe` only — see
[troubleshooting](troubleshooting.md) if it was dismissed.

## The first session, end to end

1. **Workflows → Import workflow…** — pick a workflow exported from ComfyUI with
   *Workflow → Export (API)*. Check the mapping table, save. See
   [Workflows](workflows.md).
2. **Models → + New model** — name the character, pick its LoRA, set the trigger
   word, the base prompt and the default size/steps/cfg, and select the workflow.
3. **+ New session** — open resource planning with this model selected, choose
   an exact resource revision marked `ready`, and press **Start session**. The
   resulting draft explicitly uses `composition_mode: "resource-v1"`.
   Choose **Legacy session** instead to open the previous composer, then write
   the **look** once (hair, makeup, place, light:
   "hair down, soft natural makeup, on a beach at golden hour") and the
   **wardrobe** the shoot starts in ("white linen midi dress, thin straps,
   square neckline"), then the **shots** that vary them ("full body, walking",
   "close-up, eyes to camera") and how many variations of each. The wardrobe
   rides on every take and each take can change its own, so a shoot that
   undresses is one session and not several — see
   [sessions](sessions.md#the-wardrobe). Omitting `composition_mode` from the
   API still creates a legacy session, and existing legacy sessions are not
   migrated. If resource planning is disabled, use the explicit legacy entry.
4. **Run**. Photos land in the gallery one at a time; rate them with the stars,
   reject the bad ones, press **⟳** on a good one to shoot more like it.

Files end up in `<data folder>/sessions/<session id>/`, named after the shot and
its look.
