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

Settings are written to `config.json`, which stays out of git because it holds
paths specific to your machine.

## The first session, end to end

1. **Workflows → Import workflow…** — pick a workflow exported from ComfyUI with
   *Workflow → Export (API)*. Check the mapping table, save. See
   [Workflows](workflows.md).
2. **Models → + New model** — name the character, pick its LoRA, set the trigger
   word, the base prompt and the default size/steps/cfg, and select the workflow.
3. **+ New session** — write the **look** once (wardrobe, hair, styling, place:
   "white summer dress, hair down, on a beach at golden hour"), then the
   **shots** that vary it ("full body, walking", "close-up, eyes to camera")
   and how many variations of each.
4. **Run**. Photos land in the gallery one at a time; rate them with the stars,
   reject the bad ones, press **⟳** on a good one to shoot more like it.

Files end up in `<data folder>/sessions/<session id>/`, named after the shot and
its look.
