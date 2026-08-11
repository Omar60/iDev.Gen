"""iDev.Gen — photo sessions for LoRA character models on top of ComfyUI.

Hierarchy: Model (character) -> Session -> Shots. The model is the identity, the
session is one look — wardrobe, hair, styling — held constant, and the shots are
the takes that vary: pose, angle, framing, corner of the place. Launching a
session queues its shots one at a time in ComfyUI, and every finished file is
moved into the session folder.
"""
from __future__ import annotations

import json
import logging
import os
import random
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
from comfy import SLOTS, Comfy, detect_map
from runner import Runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path(__file__).resolve().parent.parent


CONFIG_PATH = Path(os.environ.get("IDEVGEN_CONFIG") or ROOT / "config.json")


def load_config() -> dict:
    """config.json is local (machine-specific paths, kept out of git). When it is
    missing it is created from config.example.json so a fresh clone still boots
    — with empty paths, which the Setup screen then fills in."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text((ROOT / "config.example.json").read_text(encoding="utf-8"), encoding="utf-8")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


CONFIG = load_config()
DATA_DIR = Path(os.environ.get("IDEVGEN_DATA_DIR") or CONFIG.get("data_dir", "data"))
if not DATA_DIR.is_absolute():
    DATA_DIR = ROOT / DATA_DIR
SESSIONS_DIR = DATA_DIR / "sessions"
COMFY_OUTPUT = Path(CONFIG.get("comfy_output_dir") or "")
LORA_DIR = Path(CONFIG.get("lora_dir") or "")


def output_dir_ok() -> bool:
    return bool(CONFIG.get("comfy_output_dir")) and COMFY_OUTPUT.is_dir()


comfy = Comfy(CONFIG["comfy_url"])
runner = Runner(comfy, SESSIONS_DIR, COMFY_OUTPUT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect(DATA_DIR / "idevgen.db")
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    # A session left 'running' by a crash owns no job: nothing is polling it.
    db.run("UPDATE session SET status='failed' WHERE status='running'")
    db.run("UPDATE shot SET status='failed', error='interrupted when the app closed' WHERE status='running'")
    yield


app = FastAPI(title="iDev.Gen", lifespan=lifespan)


# ------------------------------------------------------------------ schemas

class ModelIn(BaseModel):
    name: str
    lora_name: str = ""
    trigger: str = ""
    lora_strength: float = 1.0
    base_positive: str = ""
    base_negative: str = ""
    workflow_id: int | None = None
    settings: dict = Field(default_factory=dict)
    notes: str = ""


class WorkflowIn(BaseModel):
    name: str
    graph: dict
    node_map: dict = Field(default_factory=dict)


class ShotIn(BaseModel):
    """One take of the session's look: a pose, an angle, a corner of the place."""
    label: str = ""
    prompt: str
    negative: str = ""
    count: int = 1
    # "More like this" hands back a prompt that ALREADY carries trigger, base and
    # look. Composing it again would duplicate all three, so a verbatim take is
    # queued exactly as given.
    verbatim: bool = False
    # 0 = follow the session's seed mode. Set it to reshoot a keeper on its own
    # seed, which is the only way to change one word and see just that change.
    seed: int = 0


class SessionIn(BaseModel):
    model_id: int
    name: str
    look: str = ""              # wardrobe, hair, styling — constant for the shoot
    shots: list[ShotIn] = Field(default_factory=list)
    workflow_id: int | None = None
    settings: dict = Field(default_factory=dict)
    seed_mode: str = "random"   # random | fixed
    seed: int = 0


class ShotPatch(BaseModel):
    rating: int | None = None
    rejected: bool | None = None


class ConfigIn(BaseModel):
    comfy_url: str
    comfy_output_dir: str = ""
    lora_dir: str = ""
    data_dir: str = "data"


# ------------------------------------------------------------------ setup

@app.get("/api/config")
def read_config():
    return {**CONFIG, "output_dir_ok": output_dir_ok(),
            "lora_dir_ok": bool(LORA_DIR.name) and LORA_DIR.is_dir(),
            "data_dir_resolved": str(DATA_DIR)}


@app.post("/api/config/detect")
async def detect_config():
    """Ask the running ComfyUI where it lives and propose its folders.

    `/system_stats` reports the argv it was launched with, and argv[0] is the
    path to main.py — that one fact locates output/ and models/loras for any
    install layout (portable, git clone, model manager), which is why nothing
    here is hardcoded to a particular distribution."""
    try:
        stats = await comfy.stats()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"ComfyUI is not responding at {comfy.url}: {exc}")
    argv = (stats.get("system") or {}).get("argv") or []
    if not argv:
        raise HTTPException(422, "ComfyUI did not report its launch path; fill the folders in by hand")
    root = Path(argv[0]).resolve().parent
    proposals = {"comfy_output_dir": root / "output", "lora_dir": root / "models" / "loras"}
    return {"comfy_root": str(root),
            **{k: {"path": str(v), "exists": v.is_dir()} for k, v in proposals.items()}}


@app.patch("/api/config")
def save_config(c: ConfigIn):
    """Write config.json and apply it live. Paths are validated here because a
    wrong output folder does not fail until a whole session has been generated."""
    global CONFIG, COMFY_OUTPUT, LORA_DIR

    for field, value in (("comfy_output_dir", c.comfy_output_dir), ("lora_dir", c.lora_dir)):
        if value and not Path(value).is_dir():
            raise HTTPException(400, f"{field}: '{value}' is not an existing folder")

    data_dir_changed = c.data_dir != CONFIG.get("data_dir")
    CONFIG = c.model_dump()
    CONFIG_PATH.write_text(json.dumps(CONFIG, indent=2) + "\n", encoding="utf-8")

    comfy.url = c.comfy_url.rstrip("/")
    COMFY_OUTPUT = Path(c.comfy_output_dir or "")
    LORA_DIR = Path(c.lora_dir or "")
    runner.comfy_output_dir = COMFY_OUTPUT
    # DATA_DIR is not swapped live: the database is already open on the old one.
    return {"ok": True, "restart_required": data_dir_changed}


# ------------------------------------------------------------------ comfy

@app.get("/api/comfy/status")
async def comfy_status():
    try:
        stats = await comfy.stats()
    except Exception as exc:  # noqa: BLE001
        return {"online": False, "error": str(exc)[:300], "url": comfy.url,
                "output_dir_ok": output_dir_ok(), "output_dir": str(COMFY_OUTPUT)}
    dev = (stats.get("devices") or [{}])[0]
    return {
        "online": True,
        "url": comfy.url,
        "output_dir_ok": output_dir_ok(),
        "output_dir": str(COMFY_OUTPUT),
        "version": stats.get("system", {}).get("comfyui_version"),
        "device": dev.get("name"),
        "vram_free": dev.get("vram_free"),
        "vram_total": dev.get("vram_total"),
        "busy": runner.busy,
        "running_session": runner.current_session(),
    }


@app.get("/api/comfy/loras")
async def comfy_loras():
    try:
        return {"loras": await comfy.loras()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"ComfyUI is not responding: {exc}")


@app.get("/api/comfy/models")
async def comfy_models():
    """Base models available for the checkpoint slot."""
    try:
        return await comfy.base_models()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"ComfyUI is not responding: {exc}")


@app.get("/api/loras/preview")
def lora_preview(name: str):
    """The preview image stored next to the .safetensors, if there is one.

    Model managers drop a `<name>.preview.jpeg` beside each file; when no such
    sibling exists the card simply shows no thumbnail."""
    if not LORA_DIR.name:
        raise HTTPException(404, "lora_dir is not configured")
    base = (LORA_DIR / name.replace("\\", "/")).resolve()
    if LORA_DIR.resolve() not in base.parents:
        raise HTTPException(400, "path outside lora_dir")
    for suffix in (".preview.jpeg", ".preview.png", ".preview.jpg", ".jpeg", ".png"):
        candidate = base.with_suffix("").with_name(base.stem + suffix)
        if candidate.exists():
            return FileResponse(candidate)
    raise HTTPException(404, "no preview")


# ------------------------------------------------------------------ workflows

@app.get("/api/workflows")
def list_workflows():
    rows = db.q("SELECT id, name, node_map, is_template, created_at FROM workflow ORDER BY name")
    return [db.jload(r, "node_map") for r in rows]


@app.get("/api/workflows/{wid}")
def get_workflow(wid: int):
    row = db.one("SELECT * FROM workflow WHERE id=?", wid)
    if not row:
        raise HTTPException(404, "workflow not found")
    return db.jload(row, "graph", "node_map")


def _require_api_graph(graph: dict) -> dict:
    """An API graph is {id: {class_type, inputs}}. The 'Save' JSON is
    {nodes: [...], links: [...]} and would blow up inside detect_map, so the
    format is validated BEFORE anything touches it — in all three routes that
    accept a graph."""
    if not isinstance(graph, dict) or not graph:
        raise HTTPException(400, "empty graph")
    if not all(isinstance(n, dict) and "class_type" in n for n in graph.values()):
        raise HTTPException(400, "This JSON is not in API format (use Export (API) in ComfyUI)")
    return graph


@app.post("/api/workflows/detect")
def detect_workflow(payload: dict):
    graph = _require_api_graph(payload.get("graph") or payload)
    return {"node_map": detect_map(graph), "slots": SLOTS, "nodes": _node_summary(graph)}


@app.post("/api/workflows")
def create_workflow(w: WorkflowIn):
    _require_api_graph(w.graph)
    node_map = w.node_map or detect_map(w.graph)
    wid = db.run(
        "INSERT INTO workflow (name, graph, node_map, created_at) VALUES (?,?,?,?)",
        w.name, json.dumps(w.graph), json.dumps(node_map), db.now(),
    )
    return {"id": wid, "node_map": node_map}


@app.patch("/api/workflows/{wid}")
def update_workflow(wid: int, w: WorkflowIn):
    _require_api_graph(w.graph)
    db.run("UPDATE workflow SET name=?, graph=?, node_map=? WHERE id=?",
           w.name, json.dumps(w.graph), json.dumps(w.node_map), wid)
    return {"ok": True}


@app.delete("/api/workflows/{wid}")
def delete_workflow(wid: int):
    db.run("DELETE FROM workflow WHERE id=?", wid)
    return {"ok": True}


def _node_summary(graph: dict) -> list[dict]:
    """Patchable widgets per node, for the frontend's mapping editor."""
    out = []
    for nid, node in graph.items():
        if not isinstance(node, dict):
            continue
        widgets = [k for k, v in (node.get("inputs") or {}).items() if not isinstance(v, list)]
        if widgets:
            out.append({"id": nid, "class_type": node.get("class_type", "?"),
                        "title": (node.get("_meta") or {}).get("title", ""), "widgets": widgets})
    return sorted(out, key=lambda n: int(n["id"]) if n["id"].isdigit() else 0)


# ------------------------------------------------------------------ models

@app.get("/api/models")
def list_models():
    rows = db.q("""
        SELECT m.*, (SELECT COUNT(*) FROM session s WHERE s.model_id=m.id) AS session_count
        FROM model m ORDER BY m.name
    """)
    return [db.jload(r, "settings") for r in rows]


@app.get("/api/models/{mid}")
def get_model(mid: int):
    row = db.one("SELECT * FROM model WHERE id=?", mid)
    if not row:
        raise HTTPException(404, "model not found")
    row = db.jload(row, "settings")
    row["sessions"] = db.q(
        """SELECT s.*, (SELECT COUNT(*) FROM shot WHERE session_id=s.id) AS shot_count,
                  (SELECT COUNT(*) FROM shot WHERE session_id=s.id AND status='done') AS done_count,
                  (SELECT id FROM shot WHERE session_id=s.id AND status='done' AND rejected=0
                    ORDER BY rating DESC, id LIMIT 1) AS cover_shot_id
           FROM session s WHERE s.model_id=? ORDER BY s.id DESC""", mid)
    return row


@app.post("/api/models")
def create_model(m: ModelIn):
    mid = db.run(
        """INSERT INTO model (name, lora_name, trigger, lora_strength, base_positive,
                              base_negative, workflow_id, settings, notes, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        m.name, m.lora_name, m.trigger, m.lora_strength, m.base_positive,
        m.base_negative, m.workflow_id, json.dumps(m.settings), m.notes, db.now(),
    )
    return {"id": mid}


@app.patch("/api/models/{mid}")
def update_model(mid: int, m: ModelIn):
    db.run(
        """UPDATE model SET name=?, lora_name=?, trigger=?, lora_strength=?, base_positive=?,
                            base_negative=?, workflow_id=?, settings=?, notes=? WHERE id=?""",
        m.name, m.lora_name, m.trigger, m.lora_strength, m.base_positive,
        m.base_negative, m.workflow_id, json.dumps(m.settings), m.notes, mid,
    )
    return {"ok": True}


@app.delete("/api/models/{mid}")
def delete_model(mid: int):
    db.run("DELETE FROM model WHERE id=?", mid)
    return {"ok": True}


# ------------------------------------------------------------------ sessions

@app.get("/api/sessions")
def list_sessions():
    return db.q("""
        SELECT s.*, m.name AS model_name,
               (SELECT COUNT(*) FROM shot WHERE session_id=s.id) AS shot_count,
               (SELECT COUNT(*) FROM shot WHERE session_id=s.id AND status='done') AS done_count
        FROM session s JOIN model m ON m.id=s.model_id ORDER BY s.id DESC
    """)


@app.get("/api/sessions/{sid}")
def get_session(sid: int):
    row = db.one("SELECT * FROM session WHERE id=?", sid)
    if not row:
        raise HTTPException(404, "session not found")
    row = db.jload(row, "settings")
    row["model"] = db.jload(db.one("SELECT * FROM model WHERE id=?", row["model_id"]), "settings")
    row["shots"] = db.q("SELECT * FROM shot WHERE session_id=? ORDER BY id", sid)
    row["running"] = runner.busy and runner.current_session() == sid
    return row


@app.post("/api/sessions")
def create_session(s: SessionIn):
    model = db.one("SELECT * FROM model WHERE id=?", s.model_id)
    if not model:
        raise HTTPException(404, "model not found")
    if not (s.workflow_id or model["workflow_id"]):
        raise HTTPException(400, "neither the session nor the model has a workflow assigned")

    settings = {"width": 1024, "height": 1024, "steps": 8, "cfg": 1.0,
                "lora_strength": model["lora_strength"]}
    settings.update(json.loads(model["settings"] or "{}"))
    settings.update(s.settings)

    sid = db.run(
        "INSERT INTO session (model_id, name, look, workflow_id, settings, created_at) VALUES (?,?,?,?,?,?)",
        s.model_id, s.name, s.look, s.workflow_id, json.dumps(settings), db.now(),
    )
    _expand_shots(sid, model, s.look, s.shots, s.seed_mode, s.seed)
    return {"id": sid}


@app.post("/api/sessions/{sid}/shots")
def add_shots(sid: int, payload: dict):
    """Add takes to an existing session (reshoot, extend the batch).

    The look is NOT re-read from the payload: it belongs to the session, and a
    shoot whose wardrobe changed halfway is two sessions.
    """
    session = db.one("SELECT * FROM session WHERE id=?", sid)
    if not session:
        raise HTTPException(404, "session not found")
    model = db.one("SELECT * FROM model WHERE id=?", session["model_id"])
    shots = [ShotIn(**item) for item in payload.get("shots", [])]
    added = _expand_shots(sid, model, session["look"], shots,
                          payload.get("seed_mode", "random"), payload.get("seed", 0))
    if session["status"] in ("done", "cancelled", "failed"):
        db.run("UPDATE session SET status='draft' WHERE id=?", sid)
    return {"added": added}


def _expand_shots(sid: int, model: dict, look: str, shots: list[ShotIn],
                  seed_mode: str, seed: int) -> int:
    """One take x N variations = N pending shot rows."""
    start = db.one("SELECT COALESCE(MAX(shot_index), -1) AS m FROM shot WHERE session_id=?", sid)["m"] + 1
    added = 0
    for offset, take in enumerate(shots):
        prompt = take.prompt if take.verbatim else _compose(model, look, take.prompt)
        negative = take.negative or model["base_negative"]
        for i in range(max(1, take.count)):
            # Fixed seed + i: N variations on the very same seed would be N
            # identical copies. Fixed means reproducible across takes, not flat
            # within one. A take's own seed wins over the session's mode.
            if take.seed:
                shot_seed = take.seed + i
            elif seed_mode == "fixed" and seed:
                shot_seed = seed + i
            else:
                shot_seed = random.randint(1, 2**31 - 1)
            db.run(
                """INSERT INTO shot (session_id, shot_index, shot_label, prompt, negative, seed, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                sid, start + offset, take.label or f"shot {start + offset + 1}",
                prompt, negative, shot_seed, db.now(),
            )
            added += 1
    return added


def _compose(model: dict, look: str, prompt: str) -> str:
    """trigger + the model's base prompt + the session's look + this take.

    The look sits between them because that is the whole point of a session: the
    wardrobe and styling are identical in every frame, and only the pose, the
    angle or the corner of the place changes. An explicit `{trigger}` in the take
    wins over prepending it.
    """
    parts = []
    if "{trigger}" in prompt:
        prompt = prompt.replace("{trigger}", model["trigger"])
    elif model["trigger"]:
        parts.append(model["trigger"])
    if model["base_positive"]:
        parts.append(model["base_positive"])
    if look:
        parts.append(look)
    parts.append(prompt)
    return ", ".join(p.strip(" ,") for p in parts if p.strip(" ,"))


@app.post("/api/sessions/{sid}/run")
async def run_session(sid: int):
    # async on purpose: `runner.start` calls `asyncio.create_task`, and a sync
    # route runs in the threadpool, where there is no event loop.
    if not db.one("SELECT id FROM session WHERE id=?", sid):
        raise HTTPException(404, "session not found")
    if not output_dir_ok():
        raise HTTPException(
            400,
            f"'comfy_output_dir' does not point to an existing folder ({COMFY_OUTPUT or 'not configured'}). "
            "Edit config.json and set ComfyUI's output folder.",
        )
    _require_mapped_choices(sid)
    pending = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=? AND status='pending'", sid)["n"]
    if not pending:
        raise HTTPException(400, "no pending shots")
    try:
        runner.start(sid)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "pending": pending}


def _require_mapped_choices(sid: int) -> None:
    """Refuse to run when an explicit choice would be silently ignored.

    "Unmapped keeps the workflow's own value" is what lets exotic graphs work,
    but for a base model or a LoRA the user *picked* it turns into the worst
    outcome there is: a whole session generated with the wrong model or the wrong
    character, with nothing on screen saying so. Silence is the bug.
    """
    session = db.jload(db.one("SELECT * FROM session WHERE id=?", sid), "settings")
    model = db.one("SELECT * FROM model WHERE id=?", session["model_id"])
    wf_id = session["workflow_id"] or model["workflow_id"]
    wf = db.jload(db.one("SELECT * FROM workflow WHERE id=?", wf_id), "node_map") if wf_id else None
    if not wf:
        raise HTTPException(400, "the session has no workflow assigned")

    node_map = wf["node_map"]
    chosen = (
        ("checkpoint", session["settings"].get("checkpoint"), "base model"),
        ("lora_name", model["lora_name"], "LoRA"),
    )
    for slot, value, label in chosen:
        if value and slot not in node_map:
            raise HTTPException(400, (
                f"Workflow '{wf['name']}' does not map the {label} slot, so '{value}' "
                f"would be ignored and the run would use the workflow's own. Open the "
                f"workflow, map {label}, and save — or clear the {label} choice."
            ))


@app.post("/api/sessions/{sid}/retry")
def retry_failed(sid: int):
    db.run("UPDATE shot SET status='pending', error='' WHERE session_id=? AND status IN ('failed','cancelled')", sid)
    count = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=? AND status='pending'", sid)["n"]
    return {"pending": count}


@app.post("/api/sessions/{sid}/cancel")
def cancel_session(sid: int):
    runner.cancel(sid)
    return {"ok": True}


@app.delete("/api/sessions/{sid}")
def delete_session(sid: int):
    if runner.current_session() == sid:
        raise HTTPException(409, "the session is running")
    db.run("DELETE FROM session WHERE id=?", sid)
    folder = SESSIONS_DIR / str(sid)
    if not folder.exists():
        return {"ok": True}
    try:
        shutil.rmtree(folder)
    except OSError as exc:
        # Swallowing this is not harmless: SQLite reuses the id of a deleted row,
        # so a folder that survives would hand its photos to the next session
        # under the same number, shown as if they were its own.
        logging.warning("session %s: folder not removed: %s", sid, exc)
        return {"ok": True, "warning": (
            f"The session is gone but its folder could not be deleted ({exc.strerror or exc}). "
            f"Delete {folder} by hand, or its photos will show up in a later session.")}
    return {"ok": True}


# ------------------------------------------------------------------ shots

@app.patch("/api/shots/{shot_id}")
def patch_shot(shot_id: int, p: ShotPatch):
    if p.rating is not None:
        db.run("UPDATE shot SET rating=? WHERE id=?", max(0, min(5, p.rating)), shot_id)
    if p.rejected is not None:
        db.run("UPDATE shot SET rejected=? WHERE id=?", int(p.rejected), shot_id)
    return db.one("SELECT * FROM shot WHERE id=?", shot_id)


@app.get("/api/shots/{shot_id}/image")
def shot_image(shot_id: int):
    shot = db.one("SELECT * FROM shot WHERE id=?", shot_id)
    if not shot or not shot["filename"]:
        raise HTTPException(404, "no image")
    path = SESSIONS_DIR / str(shot["session_id"]) / shot["filename"]
    if not path.exists():
        raise HTTPException(404, "file not found")
    return FileResponse(path)


@app.delete("/api/shots/{shot_id}")
def delete_shot(shot_id: int):
    shot = db.one("SELECT * FROM shot WHERE id=?", shot_id)
    if shot and shot["filename"]:
        (SESSIONS_DIR / str(shot["session_id"]) / shot["filename"]).unlink(missing_ok=True)
    db.run("DELETE FROM shot WHERE id=?", shot_id)
    return {"ok": True}


# ------------------------------------------------------------------ static

DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
