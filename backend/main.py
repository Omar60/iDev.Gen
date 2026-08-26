"""iDev.Gen — photo sessions for LoRA character models on top of ComfyUI.

Hierarchy: Model (character) -> Session -> Shots. The model is the identity, the
session is one look — wardrobe, hair, styling — held constant, and the shots are
the takes that vary: pose, angle, framing, corner of the place. Launching a
session queues its shots one at a time in ComfyUI, and every finished file is
moved into the session folder.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import mimetypes
import os
import random
import shutil
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

import db
import enhance
from comfy import REFERENCE_SLOTS, SLOTS, Comfy, detect_map, graph_checkpoint
from runner import Runner, slug

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
    # t2i|edit|angles|scene, or empty. Not validated here on purpose: it is a
    # label the session screen filters by, and a wrong one costs a re-pick, not a
    # broken run — the reference preflight still checks what actually matters.
    kind: str = ""


class ShotIn(BaseModel):
    """One take of the session's look: a pose, an angle, a corner of the place."""
    label: str = ""
    prompt: str
    negative: str = ""
    count: int = 1
    # What is worn in THIS take. None = the session's wardrobe, which is the
    # common case and what holds a shoot together. A string wins over it, and that
    # is the whole point: a session's wardrobe cannot be taken off by a take that
    # asks for it, because the sentence that dressed her is prepended to the very
    # take that undresses her and the photo comes back wearing neither. Writing
    # the garments *per take* is what lets one shoot walk from a jacket to nothing
    # with each frame stating its own truth. `""` is a take that names no clothes
    # at all.
    wardrobe: str | None = None
    # "More like this" hands back a prompt that ALREADY carries trigger, base and
    # look. Composing it again would duplicate all three, so a verbatim take is
    # queued exactly as given.
    verbatim: bool = False
    # Edit the session's reference photo instead of painting from noise. The take's
    # prompt is then an instruction ("remove the jacket"), so it is queued as given
    # — see `_expand_shots`.
    reference: bool = False
    # None = follow the session. How hard the result is pulled towards the
    # reference: high holds the frame still so a garment edit lands cleanly, low
    # lets the pose move. Per take because finding that number means shooting the
    # same prompt at four values and comparing them side by side.
    reference_strength: float | None = None
    # 0 = follow the session's seed mode. Set it to reshoot a keeper on its own
    # seed, which is the only way to change one word and see just that change.
    seed: int = 0


class SessionIn(BaseModel):
    model_id: int
    name: str
    look: str = ""              # hair, makeup, place, light — constant for the shoot
    wardrobe: str = ""          # the garments: the default every take starts from
    shots: list[ShotIn] = Field(default_factory=list)
    workflow_id: int | None = None
    reference_workflow_id: int | None = None
    anchor_shot_ids: list[int] = Field(default_factory=list)
    settings: dict = Field(default_factory=dict)
    seed_mode: str = "random"   # random | fixed
    seed: int = 0
    # The session's manner and checkpoint: the two non-trio dimensions
    # the cell table is keyed on. 3.2 reads them off the row to check
    # the cell for (trio, manner, checkpoint) in strict mode. Both
    # default to empty on the body — the operator who wants strict
    # draws has to declare them, because the alternative (guessing)
    # is the failure mode the cell model exists to avoid. `manner`
    # comes from the editor's <select> (lifted at create), and
    # `checkpoint` is derived at create from settings.checkpoint or
    # the workflow's loader (graph_checkpoint). An empty value after
    # that is refused before the cell lookup, naming what is missing.
    manner: str = ""
    checkpoint: str = ""


class ComposeIn(BaseModel):
    """One composed shot: a camera, an act and a framing, all drawn from
    the catalogue. Each is a (key, wordings) pair; the composer takes
    the first wording of each and joins them the same way the writer's
    `_compose` joins a take, so the queued line is byte-for-byte
    identical to one a writer would produce from the same components.

    The draw is deterministic for 3.1: the caller passes the
    components, and the composer joins them. 3.2 makes the draw
    respect cell state (strict, verified-only), 6.1 makes
    unknown drawable in exploratory mode.

    There is no `mode` field on purpose. Strict is the only legal
    mode today; encoding it as a string field on the payload would
    open a door that is shut by the type definition today (an if
    over a free string is "anything but strict" passes through) and
    would invite a second mode that does not exist yet. 6.1 opens
    the seam when the second mode exists, with its own Literal type
    and its own test. Until then, the strict check runs
    unconditionally on the trio's cell.
    """
    camera: dict
    act: dict
    framing: dict


class SessionPatch(BaseModel):
    name: str | None = None
    # The wardrobe the *next* takes start from. The look is not here on purpose:
    # it is the one thing a session holds constant, and a shoot whose hair and
    # place changed halfway is two sessions. The wardrobe is the half that was
    # always meant to move.
    wardrobe: str | None = None
    # 0 clears it back to the model's default, the same way the reference one
    # clears to "text to image only".
    workflow_id: int | None = None
    reference_workflow_id: int | None = None
    anchor_shot_ids: list[int] | None = None
    # Merged into the session's settings, not replacing them: the panel sends the
    # one dial it changed.
    settings: dict | None = None
    # The whole tag list, in its new shape. The route normalizes the values
    # (trim, drop empties, dedupe case-insensitively) and stores the cleaned
    # version, so a PATCH of "Balcony" then "balcony" lands as one tag.
    tags: list[str] | None = None


class ShotPatch(BaseModel):
    rating: int | None = None
    rejected: bool | None = None


class ConfigIn(BaseModel):
    comfy_url: str
    comfy_output_dir: str = ""
    lora_dir: str = ""
    data_dir: str = "data"
    # The optional prompt assistant. Every key belongs here even though all four
    # are optional: `save_config` writes `model_dump()` over config.json, so a key
    # missing from this schema is a key deleted on the next save.
    llm_url: str = ""
    llm_model: str = ""
    llm_vision_model: str = ""
    llm_key: str = ""
    # {"<checkpoint filename>": {steps, cfg, sampler, scheduler}} — what a base
    # model asks for, so picking it fills them in instead of being retyped from a
    # web page. Here and not in the repo: it is the user's local model library,
    # not app behaviour, and the filenames are this machine's. Free-form on
    # purpose — an unknown key inside a profile is ignored, not rejected, so the
    # file can be edited by hand ahead of the app.
    checkpoints: dict[str, dict] = Field(default_factory=dict)


# ------------------------------------------------------------------ setup

@app.get("/api/config")
def read_config():
    return {**CONFIG, "output_dir_ok": output_dir_ok(),
            "lora_dir_ok": bool(LORA_DIR.name) and LORA_DIR.is_dir(),
            "llm_ok": enhance.configured(CONFIG),
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
    """Base models for the checkpoint slot, and the sampler/scheduler options."""
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
    """The list carries `base_model`: the checkpoint each graph loads by itself.

    Read off the graph rather than stored, because it is not a second fact — a
    graph tuned for one model already names it in its loader. It is what lets
    picking a base model pick the workflow written for it. The graph itself is
    not returned: it is megabytes, and only the detail route needs it.
    """
    rows = db.q("SELECT id, name, graph, node_map, kind, is_template, created_at "
                "FROM workflow ORDER BY name")
    out = []
    for r in rows:
        r = db.jload(r, "graph", "node_map")
        r["base_model"] = graph_checkpoint(r.pop("graph") or {}, r["node_map"])
        out.append(r)
    return out


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
        "INSERT INTO workflow (name, graph, node_map, kind, created_at) VALUES (?,?,?,?,?)",
        w.name, json.dumps(w.graph), json.dumps(node_map), w.kind, db.now(),
    )
    return {"id": wid, "node_map": node_map}


@app.patch("/api/workflows/{wid}")
def update_workflow(wid: int, w: WorkflowIn):
    _require_api_graph(w.graph)
    db.run("UPDATE workflow SET name=?, graph=?, node_map=?, kind=? WHERE id=?",
           w.name, json.dumps(w.graph), json.dumps(w.node_map), w.kind, wid)
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
def list_sessions(q: str = "", tag: str = ""):
    """Every session, newest first, with a free-text and a tag filter.

    `q` is a case-insensitive substring of the session's name, look or wardrobe
    - the three things a user can read and search by. `tag` is a whole tag, not a
    substring: a query of `night` lists the session tagged `night` and not the
    one tagged `nightclub`. Both given, both must hold.

    The cover photograph is the highest-rated, non-rejected, done shot - the
    same frame the model detail page picks, so the library's row shows one
    photograph per session without a request per row.
    """
    where: list[str] = []
    params: list = []
    if q:
        # LOWER on the column and the query, LIKE wrapping: case-insensitive
        # substring. look and wardrobe default to '' so LOWER on them is safe.
        like = f"%{q.lower()}%"
        where.append("(LOWER(s.name) LIKE ? OR LOWER(s.look) LIKE ? OR LOWER(s.wardrobe) LIKE ?)")
        params.extend([like, like, like])
    if tag:
        # Whole-tag match: `json_each` turns the array into rows, LOWER on both
        # sides makes it case-insensitive, EXISTS keeps the predicate cheap.
        where.append("EXISTS (SELECT 1 FROM json_each(s.tags) WHERE LOWER(value) = LOWER(?))")
        params.append(tag)
    sql = """
        SELECT s.*, m.name AS model_name,
               (SELECT COUNT(*) FROM shot WHERE session_id=s.id) AS shot_count,
               (SELECT COUNT(*) FROM shot WHERE session_id=s.id AND status='done') AS done_count,
               (SELECT id FROM shot WHERE session_id=s.id AND status='done' AND rejected=0
                 ORDER BY rating DESC, id LIMIT 1) AS cover_shot_id
        FROM session s JOIN model m ON m.id=s.model_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY s.id DESC"
    # settings AND tags come decoded: the gallery needs the user's raw tag list
    # and the clone-picker reads cloned_from off the same payload.
    return [db.jload(r, "settings", "tags") for r in db.q(sql, *params)]


@app.get("/api/sessions/{sid}")
def get_session(sid: int):
    row = db.one("SELECT * FROM session WHERE id=?", sid)
    if not row:
        raise HTTPException(404, "session not found")
    row = db.jload(row, "settings", "anchor_shot_ids", "tags")
    row["model"] = db.jload(db.one("SELECT * FROM model WHERE id=?", row["model_id"]), "settings")
    row["shots"] = [db.jload(x, "reference_shot_ids")
                    for x in db.q("SELECT * FROM shot WHERE session_id=? ORDER BY id", sid)]
    row["running"] = runner.busy and runner.current_session() == sid
    return row


def _resolve_session_checkpoint(workflow_id, settings, explicit=""):
    """The cell table is keyed on (manner, checkpoint) and 3.2 refuses a
    compose when the checkpoint is empty. The session's effective
    checkpoint is, in order: an explicit value from the caller (the
    body's `checkpoint` field on create, or a stored row's prior
    value), the user-picked override in `settings.checkpoint`, and the
    workflow's own loader via `graph_checkpoint` (which reads
    `ckpt_name` / `unet_name` from the graph, comfy.py:35-38). The
    operator is not asked to type what the system already names.

    Called from `create_session` (where `explicit` is the body's
    `checkpoint` field) and `update_session` (where `explicit` is the
    row's stored value and a re-derivation runs when the source —
    `workflow_id` or `settings.checkpoint` — changed). The two sites
    MUST go through this function: copying the rule into the PATCH
    is how the cell table gets a stale key and the strict check
    starts approving draws against a checkpoint the session no
    longer runs on. That is the bypass 3.2 exists to prevent.

    The two inputs have different lives and the call site is what
    makes them so: the body's `explicit` is a create-time value that
    is NOT re-read on a PATCH (update_session always re-derives, and
    a workflow swap or a settings.checkpoint move loses the row's
    `explicit` to whichever of the next two sources is non-empty).
    A persistent override that survives a PATCH has to go through
    `settings.checkpoint`, the second source below. A session row
    that wants to keep a specific checkpoint across a workflow swap
    has to put it in settings, not on the body field.
    """
    if explicit:
        return explicit
    override = (settings.get("checkpoint") or "").strip()
    if override:
        return override
    if not workflow_id:
        return ""
    wf = db.one("SELECT graph, node_map FROM workflow WHERE id=?", workflow_id)
    if not wf:
        return ""
    wf = db.jload(wf, "graph", "node_map")
    return graph_checkpoint(wf.get("graph") or {}, wf.get("node_map"))


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

    # The session's effective checkpoint: explicit body field, then
    # settings.checkpoint, then the workflow's own loader. The function
    # is the same one the PATCH uses; see _resolve_session_checkpoint
    # for why that matters.
    checkpoint = _resolve_session_checkpoint(
        s.workflow_id or model["workflow_id"], settings, s.checkpoint)

    sid = db.run(
        """INSERT INTO session (model_id, name, look, wardrobe, workflow_id,
                                reference_workflow_id, anchor_shot_ids, settings,
                                manner, checkpoint, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        s.model_id, s.name, s.look, s.wardrobe, s.workflow_id, s.reference_workflow_id,
        json.dumps(_valid_anchors(s.anchor_shot_ids)), json.dumps(settings),
        s.manner, checkpoint, db.now(),
    )
    _expand_shots(sid, model, _look_for(settings, s.look), s.wardrobe, s.shots, s.seed_mode, s.seed)
    return {"id": sid}


@app.patch("/api/sessions/{sid}")
def update_session(sid: int, p: SessionPatch):
    """Rename, or fix what the session shoots with: its workflows, its reference
    photo, the settings the run preflight checks.

    Marking an anchor is a normal part of a shoot: the first take is painted from
    noise, and once one is worth keeping the rest of the session edits it. The
    workflows and the base model are here for the same reason, learned the hard
    way: you find out a graph is in the wrong slot when Run is refused, and a
    session is by then an imported photo and seventy takes. Refusing to edit it
    makes "delete and start over" the only cure for a dropdown.
    """
    row = db.one("SELECT * FROM session WHERE id=?", sid)
    if not row:
        raise HTTPException(404, "session not found")
    if p.name is not None:
        db.run("UPDATE session SET name=? WHERE id=?", p.name, sid)
    if p.wardrobe is not None:
        # Only the takes queued after this see it: a shot row already holds its
        # composed prompt, and rewriting those would change photos already shot.
        db.run("UPDATE session SET wardrobe=? WHERE id=?", p.wardrobe, sid)
    if p.workflow_id is not None:
        db.run("UPDATE session SET workflow_id=? WHERE id=?", p.workflow_id or None, sid)
    if p.settings is not None:
        db.run("UPDATE session SET settings=? WHERE id=?",
               json.dumps({**json.loads(row["settings"] or "{}"), **p.settings}), sid)
    # Re-derive the session's effective checkpoint when the source of
    # truth changed: a workflow swap, a settings.checkpoint override
    # move, or both. Without this, the cell table key stays on the old
    # checkpoint and the strict check approves draws against a
    # checkpoint the session no longer runs on — the bypass 3.2
    # exists to prevent. The re-derivation goes through the same
    # function as create_session, so the two sites cannot drift.
    if p.workflow_id is not None or (p.settings is not None and "checkpoint" in p.settings):
        # The model's workflow is the fallback when the session clears
        # its own (a 0 / null workflow_id is "the model's", the same
        # rule create_session applies).
        model = db.one("SELECT workflow_id FROM model WHERE id=?", row["model_id"])
        effective_wf_id = p.workflow_id if p.workflow_id is not None else row["workflow_id"]
        effective_wf_id = effective_wf_id or (model["workflow_id"] if model else None)
        merged_settings = {**json.loads(row["settings"] or "{}"), **(p.settings or {})}
        new_checkpoint = _resolve_session_checkpoint(
            effective_wf_id, merged_settings)
        if new_checkpoint != (row["checkpoint"] or ""):
            db.run("UPDATE session SET checkpoint=? WHERE id=?", new_checkpoint, sid)
    if p.reference_workflow_id is not None:
        db.run("UPDATE session SET reference_workflow_id=? WHERE id=?",
               p.reference_workflow_id or None, sid)
    if p.anchor_shot_ids is not None:
        db.run("UPDATE session SET anchor_shot_ids=? WHERE id=?",
               json.dumps(_valid_anchors(p.anchor_shot_ids)), sid)
    if p.tags is not None:
        # Stored cleaned, not echoed back, so the frontend renders the same
        # thing the database holds. A PATCH that asks for a duplicate in a
        # different case lands as one tag.
        db.run("UPDATE session SET tags=? WHERE id=?",
               json.dumps(_clean_tags(p.tags)), sid)
    return db.jload(db.one("SELECT * FROM session WHERE id=?", sid),
                    "settings", "anchor_shot_ids", "tags")


def _valid_anchors(ids: list[int]) -> list[int]:
    """Anchors must be finished shots that still have their file.

    Rejected here rather than at run time: an anchor pointing at a deleted photo
    would only surface after the queue had already started."""
    if len(ids) > len(REFERENCE_SLOTS):
        raise HTTPException(400, f"at most {len(REFERENCE_SLOTS)} reference photos")
    for shot_id in ids:
        shot = db.one("SELECT status, filename FROM shot WHERE id=?", shot_id)
        if not shot or shot["status"] != "done" or not shot["filename"]:
            raise HTTPException(400, f"shot {shot_id} has no finished photo to use as a reference")
    return ids


def _clean_tags(tags: list[str]) -> list[str]:
    """Trim, drop empties, dedupe case-insensitively, keep first occurrence's case.

    The dedupe key is lowercased; the kept form is the first one the user typed,
    so a PATCH of "Balcony" then "balcony" is one tag spelled "Balcony". An
    empty string after stripping is the input the route accepts and silently
    discards - it is a typo waiting to happen, not a value to store.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in tags or []:
        if not isinstance(raw, str):
            continue
        t = raw.strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


class SessionClone(BaseModel):
    name: str = ""
    # Merged over the source's settings. This is the whole point of the route:
    # the base model and what it asks for — steps, and the sampler/scheduler pair,
    # which no two finetunes of the same family agree on. Everything else a clone
    # might want changed — denoise, LoRA strength — is already a PATCH away on the
    # new session, and needs no key here because the dict is free-form.
    settings: dict = Field(default_factory=dict)
    # The graph to shoot the copy with, when the model wants its own. Zero and
    # None both mean "the source's", so a plain copy stays a plain copy: a
    # per-model graph carries its own sampler and steps, which is precisely
    # what a sweep across checkpoints could not express before.
    workflow_id: int | None = None


@app.post("/api/sessions/{sid}/clone")
def clone_session(sid: int, c: SessionClone):
    """Shoot this whole session again with one thing changed — the base model,
    and the graph written for it if there is one.

    Same look, same wardrobe, same takes, same seeds: what comes back differs
    only by what was changed, which is the only way to compare two checkpoints
    on a shoot rather than on one lucky frame. Comparing by hand means retyping
    forty composed prompts and forty seeds.

    The shots are copied as `pending`: their prompt is already composed, so the
    clone repaints exactly the same text. An *imported* photo cannot be
    repainted — nothing generated it — so its file is copied instead and it
    lands finished, the same way `import?from_shot=` carries a photo across.
    `prompt_id` is what tells the two apart: a shot that never went through
    ComfyUI has none.
    """
    src = db.one("SELECT * FROM session WHERE id=?", sid)
    if not src:
        raise HTTPException(404, "session not found")
    settings = {**json.loads(src["settings"] or "{}"), **c.settings}
    # Which shoot this is a copy of, so the gallery can put the two side by side.
    # Always the *root*: a clone of a clone joins the same family rather than
    # starting a chain nothing walks, and comparing is then one flat query.
    # In `settings` and not in a column of its own — same reason `kind` lives
    # there: it is read whole with the session and needs no migration.
    settings["cloned_from"] = settings.get("cloned_from") or sid

    new_id = db.run(
        """INSERT INTO session (model_id, name, look, wardrobe, workflow_id,
                                reference_workflow_id, anchor_shot_ids, settings, tags, created_at)
           VALUES (?,?,?,?,?,?,'[]',?,?,?)""",
        src["model_id"], c.name or f"{src['name']} (copy)", src["look"], src["wardrobe"],
        c.workflow_id or src["workflow_id"], src["reference_workflow_id"],
        json.dumps(settings),
        # Tags travel with the shoot they describe: a clone of "Balcony" is
        # still a "Balcony" session. Re-stored verbatim (already cleaned on
        # write), so the JSON column never holds a value the list route would
        # not find.
        src["tags"] or "[]", db.now(),
    )

    ids: dict[int, int] = {}
    for shot in db.q("SELECT * FROM shot WHERE session_id=? ORDER BY id", sid):
        imported = shot["status"] == "done" and not shot["prompt_id"] and shot["filename"]
        new_shot = db.run(
            """INSERT INTO shot (session_id, shot_index, shot_label, prompt, negative,
                                 use_reference, reference_strength, seed, status,
                                 origin_shot_id, created_at, finished_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            new_id, shot["shot_index"], shot["shot_label"], shot["prompt"], shot["negative"],
            shot["use_reference"], shot["reference_strength"], shot["seed"],
            "done" if imported else "pending",
            # The original take, never the row copied from: a clone of a clone
            # pairs with the whole family, and the pair then survives a reshoot
            # (↺) on either side, which rolls a new seed by design.
            shot["origin_shot_id"] or shot["id"], db.now(), db.now() if imported else "",
        )
        ids[shot["id"]] = new_shot
        if imported:
            name = f"{new_shot:05d}_{slug(shot['shot_label'])}{Path(shot['filename']).suffix}"
            folder = SESSIONS_DIR / str(new_id)
            folder.mkdir(parents=True, exist_ok=True)
            # The copy is deliberate, as in `import?from_shot=`: the two sessions
            # own their files, and deleting either must not blank the other.
            shutil.copyfile(SESSIONS_DIR / str(sid) / shot["filename"], folder / name)
            db.run("UPDATE shot SET filename=? WHERE id=?", name, new_shot)

    # Anchors follow the copies. One that was generated here is `pending` in the
    # clone and has no file yet — it is earlier in the queue than the takes that
    # edit it, so it has one by the time they run, which is the same order the
    # source session shot them in.
    anchors = [ids[a] for a in json.loads(src["anchor_shot_ids"] or "[]") if a in ids]
    db.run("UPDATE session SET anchor_shot_ids=? WHERE id=?", json.dumps(anchors), new_id)
    return {"id": new_id, "shots": len(ids)}


@app.post("/api/sessions/{sid}/shots")
def add_shots(sid: int, payload: dict):
    """Add takes to an existing session (reshoot, extend the batch).

    The look is NOT re-read from the payload: it belongs to the session, and a
    shoot whose hair, place and light changed halfway is two sessions. The
    wardrobe is read from the session too — as the *default* the takes start
    from, which each take is still free to override with its own.
    """
    session = db.one("SELECT * FROM session WHERE id=?", sid)
    if not session:
        raise HTTPException(404, "session not found")
    model = db.one("SELECT * FROM model WHERE id=?", session["model_id"])
    shots = [ShotIn(**item) for item in payload.get("shots", [])]
    added = _expand_shots(sid, model,
                          _look_for(json.loads(session["settings"] or "{}"), session["look"]),
                          session["wardrobe"], shots,
                          payload.get("seed_mode", "random"), payload.get("seed", 0))
    if session["status"] in ("done", "cancelled", "failed"):
        db.run("UPDATE session SET status='draft' WHERE id=?", sid)
    return {"added": added}


@app.post("/api/sessions/{sid}/compose")
def compose_shot_endpoint(sid: int, c: ComposeIn):
    """Compose a single shot from drawn components and queue it, with
    no writer request. The components are recorded on the shot in
    the `components` column; the queued line joins identically to one
    a writer would produce from the same three components, because
    `compose_shot` and `_compose` go through the same `_sentences`
    join (see `test_a_composed_shot_joins_identically_to_a_written_one`).

    The check below is strict and unconditional: the trio's cell
    must be verified for the session's manner and checkpoint, or
    the compose is refused with 422. A trio verified on a different
    checkpoint is not enough — the cell is the trio plus the
    session's two non-trio dimensions, and the lookup is exact.
    Unknown and dead cells are refused the same way. The 422
    message names the trio, the session's manner and checkpoint,
    and the cell state the lookup found, so the caller can see
    whether the gap is a missing measurement (unknown) or a failed
    one (dead).

    There is no `mode` field on the payload. Strict is the only
    legal mode today; encoding it as a string would let a wrong
    value bypass the check (an if over a free string is a door open
    by default), and there is no second mode to switch to. 6.1
    opens the seam when the second mode exists, with a Literal
    type on `mode` and a test for the new case.
    """
    session = db.one("SELECT * FROM session WHERE id=?", sid)
    if not session:
        raise HTTPException(404, "session not found")

    # The cell table is the only home for "is this trio drawable
    # for this session". A session that has no manner or no
    # checkpoint cannot have any cell that matches: the lookup
    # below would silently find zero rows, and zero rows would
    # silently read as "not verified". Refuse loudly before the
    # lookup, naming what the session is missing.
    missing = [name for name, value in (("manner", session["manner"]),
                                        ("checkpoint", session["checkpoint"]))
               if not value]
    if missing:
        raise HTTPException(
            422,
            f"compose refused: session is missing {', '.join(missing)}; "
            f"set them on the session before composing",
        )
    cell = db.one(
        "SELECT judged, arrived FROM cell "
        "WHERE camera_wording=? AND act_wording=? AND framing_wording=? "
        "AND manner=? AND checkpoint=?",
        c.camera["key"], c.act["key"], c.framing["key"],
        session["manner"], session["checkpoint"],
    )
    if not cell:
        raise HTTPException(
            422,
            f"compose refused: cell "
            f"({c.camera['key']}, {c.act['key']}, {c.framing['key']}, "
            f"{session['manner']}, {session['checkpoint']}) "
            f"has no measurement (unknown)",
        )
    state = db.cell_state(cell["judged"], cell["arrived"])
    if state != "verified":
        raise HTTPException(
            422,
            f"compose refused: cell "
            f"({c.camera['key']}, {c.act['key']}, {c.framing['key']}, "
            f"{session['manner']}, {session['checkpoint']}) "
            f"is {state}, not verified",
        )

    shot_id = compose_and_queue_shot(sid, c.camera, c.act, c.framing)
    return {"id": shot_id}


class ComposeRunIn(BaseModel):
    """A run of N composed shots, drawn from the caller's candidate
    pool per slot. The pool is the catalogue slice the operator
    can see for the session's manner; the backend validates that
    the verified-trio subset of that pool is large enough to fill
    `count` distinct shots before any insertion, and refuses the
    whole run otherwise.

    The pre-check is what stops a "shorter run, delivered" — a loop
    that queues k shots and refuses at k+1 would leave k rows,
    because `db.run` commits per INSERT. The check runs up front
    and a refusal queues nothing.

    There is no `mode` field, the same way there is none on the
    one-shot payload. Strict is the only legal mode today. 6.1
    opens the seam with a Literal["strict", "exploratory"] on this
    payload when the second mode exists, and the test for the new
    case lands there; until then, the absence of a `mode` field is
    the mode, the same way it is on `ComposeIn`.
    """
    count: int = Field(..., ge=1)
    candidates: dict  # {"camera": [...], "act": [...], "framing": [...]}, each a list of {key, wordings:[{key, text}]}


_SLOT_COLS = (
    ("camera", "camera_wording"),
    ("act", "act_wording"),
    ("framing", "framing_wording"),
)
_SLOT_ORDER = ("camera", "act", "framing")


def _candidate_keys(slot: str, candidates: dict) -> list[str]:
    return [c["key"] for c in candidates.get(slot, []) if isinstance(c, dict) and c.get("key")]


def _verified_trio_pool(manner: str, checkpoint: str, candidates: dict) -> set[tuple[str, str, str]]:
    """The strict pool: the set of `(camera, act, framing)` trios
    that are verified for `(manner, checkpoint)` AND whose three
    keys all sit in the caller's candidate lists. A "verified
    component" alone is not enough — a cell is the trio (the
    schema went to five columns for this reason, design.md:130
    and decision C: "the unit of evidence is a cell"), and a
    component verified alone can still fail in combination
    (design.md:326-329). Counting DISTINCT per slot independently
    reads as N×M×K trios when only some of them are rows in the
    table, and the zipped picker then draws trios that nobody
    verified. The 3.3 success path is "draw N trios that are
    cells", not "draw N×1 cameras and N×1 acts and N×1 framings
    and zip".

    The verified predicate mirrors `db.cell_state` exactly
    (`judged >= 10 AND arrived*10 >= judged*8`). The literal
    `none` is filtered on every slot: it represents measurements
    that did not break out a slot, not a real catalogue key, and
    letting it into the pool would inflate the count with
    something no compose can draw.
    """
    cam_keys = _candidate_keys("camera", candidates)
    act_keys = _candidate_keys("act", candidates)
    framing_keys = _candidate_keys("framing", candidates)
    if not (cam_keys and act_keys and framing_keys):
        return set()
    cam_ph = ",".join("?" for _ in cam_keys)
    act_ph = ",".join("?" for _ in act_keys)
    framing_ph = ",".join("?" for _ in framing_keys)
    rows = db.q(
        f"SELECT camera_wording, act_wording, framing_wording FROM cell "
        f"WHERE manner = ? AND checkpoint = ? "
        f"AND camera_wording IN ({cam_ph}) AND camera_wording != 'none' "
        f"AND act_wording IN ({act_ph}) AND act_wording != 'none' "
        f"AND framing_wording IN ({framing_ph}) AND framing_wording != 'none' "
        f"AND judged >= 10 AND arrived*10 >= judged*8",
        manner, checkpoint, *cam_keys, *act_keys, *framing_keys,
    )
    return {(r["camera_wording"], r["act_wording"], r["framing_wording"]) for r in rows}


def _min_slot_within(pool: set[tuple[str, str, str]]) -> tuple[str, int]:
    """The slot the 422 message names when the pool runs short.
    "The slot that ran out" only exists as a per-slot count when
    the pool is per-slot; under the trio model the pool is rows,
    not values, and the spec still asks to name a slot. The
    reasonable read is the slot whose number of distinct values
    INSIDE the verified trios is the smallest — the dimension
    the operator would have to broaden to grow the pool. Ties go
    to camera, then act, then framing.

    Returns (slot_name, count). For an empty pool the slot is
    `camera` with 0: the per-slot count is undefined when there
    are no trios, and the convention keeps the message shape
    stable across "the pool is empty" and "the pool is smaller
    than the request".
    """
    if not pool:
        return "camera", 0
    distinct = {
        "camera":  {t[0] for t in pool},
        "act":     {t[1] for t in pool},
        "framing": {t[2] for t in pool},
    }
    counts = {s: len(distinct[s]) for s in _SLOT_ORDER}
    slot = min(_SLOT_ORDER, key=lambda s: (counts[s], _SLOT_ORDER.index(s)))
    return slot, counts[slot]


@app.post("/api/sessions/{sid}/compose-run")
def compose_run_endpoint(sid: int, c: ComposeRunIn):
    """Queue a run of N composed shots, or refuse the whole run with
    422. The run-level is the same draw as 3.1 (one composed shot
    per call) but the caller asks for N at once and the backend
    draws, instead of passing the three components in.

    The pool is the SET of verified trios for the session's
    manner and checkpoint, filtered to the candidates' keys. Not
    DISTINCT counts per slot — those would read as 3 verified
    cameras × 3 verified acts = 9 trios when only 3 of them are
    actually cells in the table, and a zipped picker would draw
    trios that nobody verified. The cell is the trio (the schema
    went to five columns for this reason); the pool is the set
    of rows that pass the verified predicate, and the picker
    draws from that set.

    The 422 names four literals the user pinned: the slot
    (the one whose number of distinct values within the
    verified trios is the smallest), its verified count, the
    largest fillable count, and the word "exploratory". A
    refusal is a usable answer — "ask for 3 instead of 5, or
    switch to exploratory" — rather than a dead end that
    bisects by hand.

    The check runs BEFORE any insertion. `db.run` commits per
    INSERT, so a loop that queues k and refuses at k+1 would
    leave k rows — a shorter run, delivered. The pre-check is
    the loop-closed test: with the pre-check in place, a
    refusal queues nothing, and the assertion `n_shots == 0`
    after a 422 is the proof.

    There is no `mode` field on the payload. Strict is the
    only legal mode today; encoding it as a string would let a
    wrong value bypass the check (an if over a free string is
    a door open by default), and there is no second mode to
    switch to. 6.1 opens the seam when the second mode exists,
    with a Literal type on `mode` and a test for the new case.
    Until then, the strict check runs unconditionally and a
    refusal names the exploratory mode as the path the
    operator can take, not as a mode this endpoint accepts.
    """
    session = db.one("SELECT * FROM session WHERE id=?", sid)
    if not session:
        raise HTTPException(404, "session not found")

    # Same pre-check as 3.2: a session that has no manner or no
    # checkpoint cannot have any cell that matches, and "no cell
    # matches" is a different shape from "the pool is too small"
    # — the former is a session-level problem, the latter is a
    # request-level one. Refuse the session-level one first,
    # naming what is missing.
    missing = [name for name, value in (("manner", session["manner"]),
                                        ("checkpoint", session["checkpoint"]))
               if not value]
    if missing:
        raise HTTPException(
            422,
            f"compose refused: session is missing {', '.join(missing)}; "
            f"set them on the session before composing",
        )

    pool = _verified_trio_pool(session["manner"], session["checkpoint"], c.candidates)

    # The check and the draw are the same calculation. Greedy
    # on a shuffled pool, repeated over a handful of shuffles:
    # take a trio only if none of its three components has been
    # used yet, stop at `count`; keep the best result across
    # shuffles; stop early when a shuffle reaches `count`. The
    # largest fillable is `len(best_chosen)` by construction —
    # not the result of one shuffle's luck, which is the
    # failure the previous 3.3 had: the pool (c1,a1,f1),
    # (c1,a2,f2), (c2,a1,f3) with count=2 has a maximum of 2,
    # but a single shuffle that starts with (c1,a1,f1) blocks
    # both other trios (a1 is used, c1 is used) and the greedy
    # reports 1. Twenty calls on the same data gave 9 of "200,
    # 2 shots" and 11 of "422, largest fillable is 1" — the
    # operator refused would retry without changing anything
    # and get 200, which is the bug the multi-shuffle pass
    # fixes.
    #
    # ponytail: greedy with retries is an approximation of
    # the tripartite matching (maximum independent set of
    # trios under the "no component repeated" constraint). A
    # real matching would find the ceiling on one pass; greedy
    # with retries can still fall short of it on a pathological
    # pool (e.g., a pool where every shuffle wastes the same
    # trio). With the pool sizes this project actually
    # measures — a handful of verified trios per session —
    # the gap is zero or one, and a real tripartite matching
    # lands here if the ceilings ever matter. N_SHUFFLES is
    # tuned so the probability of all-bad on the user's probe
    # pool is ~(1/3)^10 ≈ 1.7e-5, well below the 20-call
    # test's flake budget.
    N_SHUFFLES = 10
    best_chosen: list[tuple[str, str, str]] = []
    for _ in range(N_SHUFFLES):
        shuffled = list(pool)
        random.shuffle(shuffled)
        chosen: list[tuple[str, str, str]] = []
        used = {"camera": set(), "act": set(), "framing": set()}
        for cam, act, framing in shuffled:
            if (cam in used["camera"]
                    or act in used["act"]
                    or framing in used["framing"]):
                continue
            chosen.append((cam, act, framing))
            used["camera"].add(cam)
            used["act"].add(act)
            used["framing"].add(framing)
            if len(chosen) == c.count:
                break
        if len(chosen) > len(best_chosen):
            best_chosen = chosen
        if len(best_chosen) == c.count:
            break

    if len(best_chosen) < c.count:
        # The slot named is the per-slot min of the POOL — the
        # dimension the operator would broaden to grow the
        # pool. The largest fillable is `len(best_chosen)`,
        # the best result across N_SHUFFLES greedy passes.
        # They can differ: the pool says "2 cameras
        # available" and every shuffle delivered 1 because
        # each one happened to waste the only trio that would
        # have unblocked the second camera. The message
        # carries both so the operator sees the shortfall
        # the multi-shuffle pass hit, not the per-slot ceiling
        # the pool promised.
        # the pool promised.
        min_slot, min_count = _min_slot_within(pool)
        raise HTTPException(
            422,
            f"compose refused: {min_slot} slot has {min_count} verified "
            f"values within the trio pool, largest fillable is "
            f"{len(best_chosen)} (of {c.count} requested); use "
            f"exploratory mode to compose with unverified cells",
        )

    by_key: dict[str, dict[str, dict]] = {
        slot: {x["key"]: x for x in c.candidates.get(slot, []) if isinstance(x, dict) and x.get("key")}
        for slot in _SLOT_ORDER
    }

    # 3.4 dedup: refuse the run on the FIRST collision, before
    # any INSERT (db.run auto-commits, a check that fires at k+1
    # would leave k rows — the same loop-closed property 3.3
    # pins on the pool-too-small refusal). Two distinct tuples
    # can join to the same composed line (the wink/finger
    # pattern: two act candidates with the same wording text,
    # different keys), and the tuple check does not catch it —
    # the line check is the only thing that does. Within the
    # same run, a tuple dedup would let a wink/finger pair
    # through too, so the line check also fires against the
    # in-loop set the greedy has already chosen.
    existing = db.q("SELECT components, prompt FROM shot WHERE session_id=?", sid)
    existing_tuples: set[tuple[str, str, str]] = set()
    existing_lines: set[str] = set()
    for row in existing:
        existing_lines.add(row["prompt"])
        # components='{}' is this schema's marker for "no trio
        # here" (the writer drew the line, not the composer —
        # 3.1 leaves the column at the empty default on a
        # written shot). A row without a tuple cannot collide
        # on the tuple axis; the line check below still runs
        # against it, and a written line that the composer
        # would join to is a real repeat. The two checks
        # therefore have different scopes: tuple check on
        # composed rows only, line check on every row.
        comps = db.jload(row, "components")["components"]
        if not comps:
            continue
        existing_tuples.add((
            comps.get("camera", {}).get("wording", ""),
            comps.get("act", {}).get("wording", ""),
            comps.get("framing", {}).get("wording", ""),
        ))

    # The dedup check composes each candidate to compare on
    # the line axis, which needs the model and the session's
    # look and wardrobe. Read them once, the same way
    # `compose_and_queue_shot` does on the insert path.
    model = db.one("SELECT * FROM model WHERE id=?", session["model_id"])
    if not model:
        raise HTTPException(404, "model not found")
    settings = json.loads(session["settings"] or "{}")
    look = session["look"] if settings.get("use_look", True) else ""
    wardrobe = session["wardrobe"]

    seen_tuples: set[tuple[str, str, str]] = set()
    seen_lines: set[str] = set()
    for cam_key, act_key, framing_key in best_chosen:
        cam = by_key["camera"][cam_key]
        act = by_key["act"][act_key]
        framing = by_key["framing"][framing_key]
        line = compose_shot(model, look, wardrobe, cam, act, framing)
        trio = (cam_key, act_key, framing_key)
        if trio in existing_tuples or trio in seen_tuples:
            # The tuple is already enqueued (or would be, by an
            # earlier trio in this same run). The composed line
            # is identical in either case — the two checks fire
            # on the same data — and the operator's question is
            # "did I re-draw the same trio?". Name the trio in
            # the message so the operator sees WHICH trio they
            # asked to re-queue, not just that they did.
            raise HTTPException(
                422,
                f"compose refused: tuple already enqueued in this "
                f"session: {trio}",
            )
        if line in existing_lines or line in seen_lines:
            # The composed line is already in the session. The
            # tuple is distinct (otherwise the previous branch
            # would have fired), so this is the wink/finger
            # shape: two keys, one wording text, one joined
            # line. Name the line so the operator can see what
            # collided and pull the duplicate candidate out of
            # the next attempt.
            raise HTTPException(
                422,
                f"compose refused: line already enqueued in this "
                f"session: {line}",
            )
        seen_tuples.add(trio)
        seen_lines.add(line)

    shot_ids: list[int] = []
    for cam_key, act_key, framing_key in best_chosen:
        shot_ids.append(compose_and_queue_shot(
            sid, by_key["camera"][cam_key], by_key["act"][act_key], by_key["framing"][framing_key],
        ))
    return {"ids": shot_ids, "count": len(shot_ids)}


def _expand_shots(sid: int, model: dict, look: str, wardrobe: str, shots: list[ShotIn],
                  seed_mode: str, seed: int) -> int:
    """One take x N variations = N pending shot rows."""
    start = db.one("SELECT COALESCE(MAX(shot_index), -1) AS m FROM shot WHERE session_id=?", sid)["m"] + 1
    added = 0
    for offset, take in enumerate(shots):
        # A reference take is NOT composed, and that is the whole point: the anchor
        # photo already carries the trigger, the base prompt and the look, so the
        # take is an instruction ("remove the jacket"). Prepending the look again
        # would restate the very garment the instruction removes, and a positive
        # that both describes and denies a jacket keeps the jacket.
        raw = take.verbatim or take.reference
        worn = wardrobe if take.wardrobe is None else take.wardrobe
        prompt = take.prompt if raw else _compose(model, look, worn, take.prompt)
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
                """INSERT INTO shot (session_id, shot_index, shot_label, prompt, negative,
                                     use_reference, reference_strength, seed, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                sid, start + offset, take.label or f"shot {start + offset + 1}",
                prompt, negative, int(take.reference), take.reference_strength,
                shot_seed, db.now(),
            )
            added += 1
    return added


def _sentences(*parts: str) -> str:
    """Join written-out pieces into one paragraph, one full stop between each.

    Not `", ".join` — that was right when every piece was a bag of keywords, and
    wrong now that each one is a sentence: a comma splice between two sentences is
    read as one long clause, and the relations inside them start leaking into each
    other. A piece that already ends in its own punctuation keeps it.
    """
    out = []
    for part in parts:
        part = part.strip().strip(",").strip()
        if not part:
            continue
        out.append(part if part[-1] in ".!?" else f"{part}.")
    # A piece written in headed blocks keeps its shape: run together by single
    # spaces the headings stop reading as headings at all.
    return ("\n\n" if any("\n" in p for p in out) else " ").join(out)


def _look_for(settings: dict, look: str) -> str:
    """The look, unless this session has it switched off.

    `use_look` lives in `settings` and not in a column for the same reason `kind`
    does: it is read whole with the session and needs no migration. Absent means
    on, so every session that already exists keeps composing the way it did.

    Off does NOT clear the column - the text stays, and the toggle is a toggle.
    Measured 2026-08-17 on a six-rung ladder (sessions 174-179): the same eight
    takes rendered the position they describe at 50 and 63 composed words and
    stopped at 87, and the 24 words that crossed that line were the look's room
    sentence, which describes nobody. Above ~85 composed words this sampler keeps
    the coarse facts and picks its own composition, so a session that cares more
    about the pose than about a constant place is better off without one.
    """
    return look if settings.get("use_look", True) else ""


def _compose(model: dict, look: str, wardrobe: str, prompt: str) -> str:
    """trigger + the model's base prompt + the look + the wardrobe + this take.

    The take goes LAST, where it has always gone. It was briefly moved to the
    front on the theory that a framing buried behind eighty words of wardrobe is
    a framing already spent — six seeds gave two full-length frames that way
    against none the other. Two frames is not evidence; the sessions that
    actually came back right, twenty-one of them, put the pose last behind a
    fixed block, and a forty-frame run with the take in front still came back
    flat. Moved back, and the note left here so it does not get moved again on
    the same hunch.

    The look is the half of a session that is genuinely constant — hair, makeup,
    the place, the light — and it sits before everything the take says, because
    that is what makes twenty frames one shoot.

    The wardrobe sits right after it and is written into every prompt rather than
    stated once, so that a take can *change* it. Stating it once is what made
    undressing impossible: the session's sentence dressed her in the same prompt
    that asked for the jacket off, and a positive that both describes and denies a
    jacket keeps the jacket. Repeated per take, each frame states its own truth,
    and the words the takes leave untouched are identical from frame to frame,
    which is what held the wardrobe together in the first place.

    What did survive the experiment is the joining: full stops, not commas. The
    encoder is a language model, a comma splice between two written-out pieces
    reads as one run-on clause, and their relations start bleeding into each
    other. Measured, one outfit at six seeds: written as sentences the hem held
    six times of six and the harness repeated six of six; as comma fragments,
    three of six and a different harness every frame.

    An explicit `{trigger}` in the take wins over prepending it.
    """
    if "{trigger}" in prompt:
        prompt = prompt.replace("{trigger}", model["trigger"])
        return _sentences(model["base_positive"], look, wardrobe, prompt)
    return _sentences(model["trigger"], model["base_positive"], look, wardrobe, prompt)


def compose_shot(model: dict, look: str, wardrobe: str,
                 camera: dict, act: dict, framing: dict) -> str:
    """Compose a line from drawn components, no writer request.

    The camera, act and framing are catalogue entries with at least
    one wording each. The composed line is what `_compose` would
    produce if the writer wrote the same three pieces in the take
    position: trigger + base + look + wardrobe + the three components,
    joined with full stops via `_sentences`.

    The composer and the writer go through the SAME join function
    on purpose: the composed line is byte-for-byte identical to a
    written one for the same components, and the test
    `test_a_composed_shot_joins_identically_to_a_written_one`
    pins that. Group 4 measures the composer's render rate against
    the writer's, and the comparison is only valid if the two produce
    the same line for the same input.
    """
    take = _sentences(
        camera["wordings"][0]["text"],
        act["wordings"][0]["text"],
        framing["wordings"][0]["text"],
    )
    return _compose(model, look, wardrobe, take)


def compose_and_queue_shot(sid: int, camera: dict, act: dict, framing: dict) -> int:
    """Compose a single shot from drawn components and queue it.

    Returns the shot id. The three drawn components are recorded on
    the row in the `components` column as (concept, wording) pairs
    per slot, where the JSON key is the slot name and the value is
    `{concept, wording}`: `concept` is the catalogue key of the
    concept (front-direct, astride, full-length) and `wording` is
    the catalogue key of the wording that was drawn. For every
    concept in the catalogue today they are the same key, because
    1.1 left each concept with a single wording; a future "let me
    add a second wording" will land here as a different `wording`
    value, and the cell the photograph counts toward (6.2) is keyed
    by the trio, not by the concept.

    A written shot leaves the column at its empty default '{}',
    which is the marker 3.6 uses to tell a composed session from
    a written one.

    The session's `look` and `wardrobe` are read here, not from the
    payload: a composed shot is part of the session, and the look
    and wardrobe are the session's halves — same reason `add_shots`
    reads them from the row.
    """
    session = db.one("SELECT * FROM session WHERE id=?", sid)
    if not session:
        raise HTTPException(404, "session not found")
    model = db.one("SELECT * FROM model WHERE id=?", session["model_id"])
    if not model:
        raise HTTPException(404, "model not found")
    settings = json.loads(session["settings"] or "{}")
    look = session["look"] if settings.get("use_look", True) else ""
    wardrobe = session["wardrobe"]
    prompt = compose_shot(model, look, wardrobe, camera, act, framing)
    # The (concept, wording) pair per slot, not just the wording.
    # Today every concept has a single wording and the two keys
    # coincide, so the cell the photograph counts toward is keyed
    # by the trio (camera, act, framing) directly. The slot is
    # the JSON key, not a value, because the cell is the trio and
    # the slot is the part of the trio this entry names.
    components = json.dumps({
        "camera":  {"concept": camera["key"],  "wording": camera["wordings"][0]["key"]},
        "act":     {"concept": act["key"],     "wording": act["wordings"][0]["key"]},
        "framing": {"concept": framing["key"], "wording": framing["wordings"][0]["key"]},
    })
    shot_index = db.one("SELECT COALESCE(MAX(shot_index), -1) AS m FROM shot WHERE session_id=?", sid)["m"] + 1
    shot_id = db.run(
        """INSERT INTO shot (session_id, shot_index, shot_label, prompt, negative,
                              components, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        sid, shot_index, f"composed {shot_index + 1}", prompt,
        model["base_negative"], components, db.now(),
    )
    return shot_id


IMPORT_MAX_BYTES = 40 * 1024 * 1024
# Sniffed from the bytes, never from the filename: the extension is the
# uploader's claim, the magic number is the file.
_IMAGE_MAGIC = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"))


def _image_suffix(data: bytes) -> str:
    for magic, suffix in _IMAGE_MAGIC:
        if data.startswith(magic):
            return suffix
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


@app.post("/api/sessions/{sid}/import")
async def import_photo(sid: int, request: Request, label: str = "", from_shot: int = 0):
    """Bring a photo into a session as a finished shot.

    It lands as an ordinary shot, so everything already built works on it — the
    gallery, the star rating, and above all marking it as a reference. A photo
    that arrives some other way would need its own path through all of that.

    The body is the raw file. One route does not justify a multipart dependency,
    and a browser can POST a File as the body unchanged.

    `from_shot` copies a photo the app already has instead of reading the body:
    continuing a shoot in a fresh session — the keeper of a photoshoot walked
    around with the angle graph — otherwise means downloading the photo and
    uploading it straight back. The copy is deliberate: the two sessions own
    their files, and deleting either one must not blank the other's gallery.
    """
    if not db.one("SELECT id FROM session WHERE id=?", sid):
        raise HTTPException(404, "session not found")

    if from_shot:
        src = db.one("SELECT * FROM shot WHERE id=?", from_shot)
        if not src or not src["filename"]:
            raise HTTPException(404, "that shot has no photo to copy")
        path = SESSIONS_DIR / str(src["session_id"]) / src["filename"]
        if not path.exists():
            raise HTTPException(404, "file not found")
        data = path.read_bytes()
        label = label or src["shot_label"]
    else:
        data = await request.body()
    if not data:
        raise HTTPException(400, "empty upload")
    if len(data) > IMPORT_MAX_BYTES:
        raise HTTPException(413, f"the image is over {IMPORT_MAX_BYTES // (1024 * 1024)} MB")
    suffix = _image_suffix(data)
    if not suffix:
        raise HTTPException(400, "that file is not a PNG, JPEG or WebP image")

    index = db.one("SELECT COALESCE(MAX(shot_index), -1) AS m FROM shot WHERE session_id=?", sid)["m"] + 1
    shot_id = db.run(
        """INSERT INTO shot (session_id, shot_index, shot_label, prompt, status, created_at, finished_at)
           VALUES (?,?,?,?,'done',?,?)""",
        sid, index, (label or "imported")[:60], "imported photo", db.now(), db.now(),
    )
    # The name is ours, built the same way the runner builds it. Nothing from the
    # upload reaches the path, so there is no traversal to defend against.
    name = f"{shot_id:05d}_{slug(label or 'imported')}{suffix}"
    folder = SESSIONS_DIR / str(sid)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
    except OSError as exc:
        db.run("DELETE FROM shot WHERE id=?", shot_id)
        raise HTTPException(500, f"could not save the image: {exc}")
    db.run("UPDATE shot SET filename=? WHERE id=?", name, shot_id)

    # An imported photo in a session that edits photos and has none marked is the
    # photo you imported it to edit. Same default as `runner._adopt_anchor`, same
    # reason: it is visible in the gallery and 📎 changes it, so it is a default
    # and not a decision. Without it the import is followed by a refused Run whose
    # fix is one unexplained click away.
    session = db.one("SELECT anchor_shot_ids FROM session WHERE id=?", sid)
    edits = db.one("SELECT id FROM shot WHERE session_id=? AND use_reference=1", sid)
    if edits and not json.loads(session["anchor_shot_ids"] or "[]"):
        db.run("UPDATE session SET anchor_shot_ids=? WHERE id=?", json.dumps([shot_id]), sid)
    return {"id": shot_id, "filename": name}


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

    # Only takes painted from noise load this graph, so a session whose pending
    # takes are all reference edits never touches it — a base model it does not
    # map is not being ignored, there is nothing to ignore it. Refusing there
    # asks for the wrong fix (map the slot, or clear the choice) on the wrong
    # graph. The reference check below still runs: that is the one that applies.
    if db.one("SELECT id FROM shot WHERE session_id=? AND status='pending' AND use_reference=0", sid):
        wf_id = session["workflow_id"] or model["workflow_id"]
        wf = db.jload(db.one("SELECT * FROM workflow WHERE id=?", wf_id), "node_map") if wf_id else None
        if not wf:
            raise HTTPException(400, "the session has no workflow assigned")

        node_map = wf["node_map"]
        # Every one of these is opt-in: left empty the workflow's own value runs
        # and there is nothing to ignore. Set and unmapped is the silent-drop the
        # docstring above is about, so it refuses rather than shoots the session
        # with a sampler nobody picked.
        chosen = (
            ("checkpoint", session["settings"].get("checkpoint"), "base model"),
            ("lora_name", model["lora_name"], "LoRA"),
            ("sampler", session["settings"].get("sampler"), "sampler"),
            ("scheduler", session["settings"].get("scheduler"), "scheduler"),
        )
        for slot, value, label in chosen:
            if value and slot not in node_map:
                raise HTTPException(400, (
                    f"Workflow '{wf['name']}' does not map the {label} slot, so '{value}' "
                    f"would be ignored and the run would use the workflow's own. Open the "
                    f"workflow, map {label}, and save — or clear the {label} choice."
                ))

    _require_usable_reference(session)


def _require_usable_reference(session: dict) -> None:
    """Same rule, applied to the reference takes: refuse rather than ignore.

    A reference take whose `reference` slot is unmapped is the worst failure this
    app can produce — every photo comes back painted from noise, ignoring the
    anchor, with nothing on screen saying the reference was dropped.

    The reference workflow is deliberately NOT checked for the base model or the
    LoRA. An editing graph loads its own model, and the character comes from the
    anchor photo rather than from the LoRA, so a Kontext or Qwen-Image-Edit
    workflow legitimately has neither slot.
    """
    sid = session["id"]
    pending = db.one(
        "SELECT COUNT(*) AS n FROM shot WHERE session_id=? AND status='pending' AND use_reference=1", sid)["n"]
    if not pending:
        return
    if not session["reference_workflow_id"]:
        raise HTTPException(400, (
            f"{pending} take(s) edit a reference photo, but the session has no reference "
            f"workflow. Assign one — an img2img or instruction-editing graph."))
    # A session that shoots its anchor and then edits it is one run, not two: the
    # first take to come out becomes the reference (see `runner._adopt_anchor`).
    # So this only refuses the case that cannot work at all — nothing to edit, and
    # nothing queued that would produce something to edit.
    anchors = json.loads(session["anchor_shot_ids"] or "[]")
    if not anchors:
        will_shoot = db.one(
            "SELECT COUNT(*) AS n FROM shot WHERE session_id=? AND status='pending' AND use_reference=0",
            sid)["n"]
        if not will_shoot:
            raise HTTPException(400, (
                f"{pending} take(s) edit a reference photo, but none is set and no take "
                f"would produce one. Mark a finished photo as the reference from the "
                f"gallery, or add a take that is not a reference edit."))
    wf = db.jload(db.one("SELECT * FROM workflow WHERE id=?", session["reference_workflow_id"]), "node_map")
    if not wf:
        raise HTTPException(400, "the session's reference workflow no longer exists")
    if REFERENCE_SLOTS[0] not in wf["node_map"]:
        raise HTTPException(400, (
            f"Workflow '{wf['name']}' does not map the reference image slot, so the "
            f"reference would be ignored and every take would be generated from noise. "
            f"Open the workflow, map the reference image to its LoadImage, and save."))

    # Reference slots have to match exactly, and this is the one place the app's
    # "unmapped keeps the workflow's own value" rule must not apply. Too few marked
    # and a slot keeps whatever filename the graph shipped with — an unrelated
    # photo, silently mixed into every take. Too many and the extra uploads and is
    # ignored. Both look like the reference simply had no effect.
    mapped = [slot for slot in REFERENCE_SLOTS if slot in wf["node_map"]]
    if anchors and len(anchors) != len(mapped):
        detail = (f"{len(anchors)} reference photo(s) are marked, but workflow "
                  f"'{wf['name']}' reads {len(mapped)}.")
        if len(anchors) > len(mapped):
            detail += (f" The extra one would be uploaded and ignored. Unmark it, or map "
                       f"{REFERENCE_SLOTS[len(mapped)]} to another LoadImage in the workflow.")
        else:
            detail += (f" The unfilled slot would keep the filename the workflow was saved "
                       f"with and mix that photo into every take. Mark {len(mapped)} reference "
                       f"photos, or unmap {REFERENCE_SLOTS[len(anchors)]}.")
        raise HTTPException(400, detail)


@app.post("/api/sessions/{sid}/retry")
def retry_failed(sid: int):
    db.run("UPDATE shot SET status='pending', error='' WHERE session_id=? AND status IN ('failed','cancelled')", sid)
    count = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=? AND status='pending'", sid)["n"]
    return {"pending": count}


@app.post("/api/sessions/{sid}/cancel")
def cancel_session(sid: int):
    runner.cancel(sid)
    return {"ok": True}


@app.get("/api/sessions/{sid}/export")
def export_session(sid: int, min_rating: int = 1):
    if not db.one("SELECT id FROM session WHERE id=?", sid):
        raise HTTPException(404, "session not found")

    all_shots = db.q(
        """SELECT id, rating, filename, status, rejected, shot_index FROM shot
           WHERE session_id=? ORDER BY shot_index, id""", sid)

    # One take is N rows sharing a shot_index, so the index alone does not name a
    # file. The variation number is counted over every row of the take, not over
    # the ones being written: counted while writing, a photo would change name
    # between an export at one star and the same export at four.
    variation = {}
    counts: dict[int, int] = {}
    for shot in all_shots:
        counts[shot["shot_index"]] = counts.get(shot["shot_index"], 0) + 1
        variation[shot["id"]] = counts[shot["shot_index"]]

    buf = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buf, "w") as zf:
        for shot in all_shots:
            if shot["status"] != "done" or shot["rating"] < min_rating or shot["rejected"]:
                continue
            if not shot["filename"]:
                continue
            path = SESSIONS_DIR / str(sid) / shot["filename"]
            if not path.exists():
                continue
            ext = Path(shot["filename"]).suffix
            entry_name = (f"{shot['shot_index']:05d}_{variation[shot['id']]:02d}"
                          f"_rating{shot['rating']}{ext}")
            zf.write(path, arcname=entry_name)
            added += 1

    if not added:
        raise HTTPException(400, f"no shots meet the threshold of {min_rating}")

    buf.seek(0)
    filename = f"session_{sid}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


def _fit_label(text: str, font, max_w: int) -> str:
    """Trim a filename from the front until it fits inside its cell.

    A ComfyUI filename is comfortably wider than a 300px cell, and Pillow does
    not wrap or clip: the text simply runs over the neighbouring photograph. The
    tail is the half worth keeping — the counter at the end is what tells two
    variations of one take apart.
    """
    def width(s: str) -> int:
        box = font.getbbox(s)
        return box[2] - box[0]

    if width(text) <= max_w:
        return text
    while text and width("..." + text) > max_w:
        text = text[1:]
    return "..." + text


@app.get("/api/sessions/{sid}/contact-sheet")
def contact_sheet(sid: int, min_rating: int = 1):
    if not db.one("SELECT id FROM session WHERE id=?", sid):
        raise HTTPException(404, "session not found")

    all_shots = db.q(
        """SELECT id, rating, filename, status, rejected, shot_index FROM shot
           WHERE session_id=? ORDER BY shot_index, id""", sid)

    selected = []
    for shot in all_shots:
        if shot["status"] != "done" or shot["rating"] < min_rating or shot["rejected"]:
            continue
        if not shot["filename"]:
            continue
        path = SESSIONS_DIR / str(sid) / shot["filename"]
        if not path.exists():
            continue
        selected.append((shot, path))

    if not selected:
        raise HTTPException(400, f"no shots meet the threshold of {min_rating}")

    cols = min(4, len(selected))
    rows = (len(selected) + cols - 1) // cols

    thumb_w, thumb_h = 300, 300
    label_h = 28
    cell_padding = 10
    cell_w = thumb_w + cell_padding * 2
    cell_h = thumb_h + label_h + cell_padding * 2

    margin = 20
    gap = 16

    sheet_w = margin * 2 + cols * cell_w + (cols - 1) * gap
    sheet_h = margin * 2 + rows * cell_h + (rows - 1) * gap

    sheet = Image.new("RGB", (sheet_w, sheet_h), color=(15, 17, 21))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()

    for idx, (shot, path) in enumerate(selected):
        c = idx % cols
        r = idx // cols
        cell_x = margin + c * (cell_w + gap)
        cell_y = margin + r * (cell_h + gap)

        draw.rectangle(
            [cell_x, cell_y, cell_x + cell_w - 1, cell_y + cell_h - 1],
            fill=(23, 26, 33),
            outline=(42, 47, 58),
        )

        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            img_x = cell_x + cell_padding + (thumb_w - img.width) // 2
            img_y = cell_y + cell_padding + (thumb_h - img.height) // 2
            sheet.paste(img, (img_x, img_y))

        label = _fit_label(shot["filename"], font, cell_w - 2 * cell_padding)
        bbox = font.getbbox(label)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        text_x = cell_x + max(0, (cell_w - text_w) // 2)
        text_y = cell_y + cell_padding + thumb_h + (label_h - text_h) // 2
        draw.text((text_x, text_y), label, fill=(230, 232, 238), font=font)

    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    buf.seek(0)
    filename = f"session_{sid}_contact_sheet.png"
    return StreamingResponse(
        buf,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )



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


@app.post("/api/shots/{shot_id}/reshoot")
def reshoot_shot(shot_id: int):
    """Refuse this photo and shoot the same take again, in its place.

    Not "more like this": that queues a new row and keeps the photo you refused,
    which is right when the frame is good and you want more of it. This one is
    for the frame that came back wrong — the row is reused, so a shoot stays one
    card per take and the reject does not sit in the gallery being scrolled past.

    The seed is cleared on purpose. Shooting the same prompt on the same noise
    reproduces the same picture, which is the one outcome this button exists to
    avoid; `⚖` is where a pinned seed belongs.
    """
    shot = db.one("SELECT * FROM shot WHERE id=?", shot_id)
    if not shot:
        raise HTTPException(404, "shot not found")
    if shot["status"] == "running":
        raise HTTPException(409, "the shot is still generating")
    # An anchor with no file fails every reference take behind it, and
    # `_valid_anchors` refuses to point at one — so this is refused here rather
    # than left to surface once the queue has already started.
    session = db.one("SELECT * FROM session WHERE id=?", shot["session_id"])
    if shot_id in json.loads(session["anchor_shot_ids"] or "[]"):
        raise HTTPException(409, (
            "this photo is the session's reference — the takes that edit it would have "
            "nothing to edit. Unpin it (📌), or pick another reference first."))
    # ponytail: an *unpinned* photo some edit already ran against stays reachable
    # through that edit's before/after wipe, which then has nothing to show on its
    # left half. Refusing those too would block reshooting any photo the session
    # ever edited; add the check if a broken wipe turns up in practice.
    if shot["filename"]:
        (SESSIONS_DIR / str(shot["session_id"]) / shot["filename"]).unlink(missing_ok=True)
    db.run("""UPDATE shot SET status='pending', filename='', prompt_id='', error='',
                              seed=0, rejected=0, finished_at='' WHERE id=?""", shot_id)
    # Same reopening as adding takes: a finished session with something queued in
    # it is not finished, and the status is what the Run button reads.
    if session["status"] in ("done", "cancelled", "failed"):
        db.run("UPDATE session SET status='draft' WHERE id=?", session["id"])
    return db.one("SELECT * FROM shot WHERE id=?", shot_id)


@app.post("/api/sessions/{sid}/reshoot-below")
def reshoot_below(sid: int, min_rating: int):
    """Refuse every weak frame in one click, on the same terms as the per-shot
    reshoot above.

    `min_rating` is required, unlike the export's, and that asymmetry is the
    point: the export only reads, so a missing threshold can default to 1, but
    this route deletes photographs. Left to default, a call that names no
    threshold would delete every unrated photo in the session.

    A long shoot does not need a long argument: the user has already rated (or
    rejected, or not) the photos, and the action takes the threshold they would
    type if they were patient. The single-shot button's rules are re-used rather
    than restated — refuse only what is finished, never an anchor, with its file
    gone and its seed cleared — and the skipped shots come back as a count so
    the screen can say what it did without the user counting cards.

    A session left with something queued in it is not finished; the status is
    the one the Run button reads, so a `done` (or `cancelled`, or `failed`)
    session whose frames are re-queued reopens to `draft`, exactly like the
    per-shot version. A bulk reshoot that touches nothing refuses with a 400
    rather than silently return an empty count, so a click that "did nothing"
    never goes through.
    """
    session = db.one("SELECT * FROM session WHERE id=?", sid)
    if not session:
        raise HTTPException(404, "session not found")
    anchors = set(json.loads(session["anchor_shot_ids"] or "[]"))
    # `rating < min_rating` catches rating 0 (unrated) and every numeric value
    # below the threshold — both are candidates by the spec, because refusing
    # a frame and reshooting it are the same judgement.
    below = db.q("""SELECT id, status, filename FROM shot
                    WHERE session_id=? AND rating<? ORDER BY id""",
                 sid, min_rating)
    re_queued = 0
    skipped = 0
    for shot in below:
        # Reuse the per-shot refusals: a running shot is generating (its image
        # does not exist yet, and aborting the queue is not the action), an
        # anchor's file is what every reference take behind it edits, and
        # pending / failed / cancelled shots just have no photo to refuse.
        if shot["status"] != "done" or shot["id"] in anchors:
            skipped += 1
            continue
        if shot["filename"]:
            (SESSIONS_DIR / str(sid) / shot["filename"]).unlink(missing_ok=True)
        db.run("""UPDATE shot SET status='pending', filename='', prompt_id='', error='',
                                  seed=0, rejected=0, finished_at='' WHERE id=?""",
               shot["id"])
        re_queued += 1
    if re_queued == 0:
        # No work was done in the loop above (every row took the `continue`),
        # so no file is deleted and no row changed — the 400 is a true no-op.
        raise HTTPException(400, f"no shots in this session are below {min_rating}")
    if session["status"] in ("done", "cancelled", "failed"):
        db.run("UPDATE session SET status='draft' WHERE id=?", sid)
    return {"re_queued": re_queued, "skipped": skipped}


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


# ------------------------------------------------------------------ photos (cross-session)

@app.get("/api/photos")
def list_photos(min_rating: int = 0):
    """Finished, un-rejected photographs across every session, at or above a
    rating threshold.

    The slideshow needs "every photograph, regardless of session, that meets the
    bar" — and the only routes that pick photographs today are reached through
    a session id. The threshold is inclusive: a 4 is listed at `min_rating=4`,
    a 3 is not. A threshold of 0 lists the never-rated, because that is the
    useful one the first time the slideshow is opened (the design measured
    6,356 of 6,380 finished, un-rejected photographs as unrated on a real
    database, and the 13 keepers it grows into is what `min_rating=4` answers).

    Read-only by design: this is the slideshow's input, the slideshow shows
    photographs and changes nothing, and the route follows that. No schema
    change, no column touched.

    The session a photograph belongs to does not narrow the list — a query of
    `min_rating=4` answers the same set whether or not the caller knows the
    session ids. Each entry carries the shot id, the session id and the
    session name, which is what the screen needs to render one card per
    photograph without a request per row.
    """
    rows = db.q(
        """SELECT shot.id AS id, shot.session_id AS session_id, session.name AS session_name
             FROM shot JOIN session ON session.id = shot.session_id
            WHERE shot.status = 'done'
              AND shot.rejected = 0
              AND shot.filename != ''
              AND shot.rating >= ?
            ORDER BY shot.id""",
        min_rating,
    )
    return rows


# ------------------------------------------------------------------ prompt help

@app.post("/api/llm/models")
async def llm_models(payload: dict):
    """What the assistant can run, and where it is.

    With no `url` it probes the ports a local assistant listens on, so the
    endpoint is a button rather than a thing to look up — the same reason
    `/api/config/detect` exists for ComfyUI. Proposes; saving is still the
    user's click.

    POST rather than GET because a hosted endpoint lists nothing without its API
    key, and a key does not belong in a query string.
    """
    return await enhance.discover(payload.get("url") or "", payload.get("key") or "")


@app.post("/api/enhance")
async def enhance_prompt(p: enhance.EnhanceIn):
    """Ask the optional LLM for a take, a look or an angle line.

    Suggestion only: it answers with text for a box on screen, and touches
    neither the database nor the queue. Composition still happens in
    `_expand_shots`, so what comes back is the take's own line — never the
    trigger, the base prompt or the look, which is exactly what the caller tells
    the model is already in the prompt.
    """
    return {"lines": await enhance.run(CONFIG, p, _shot_data_uri(p.shot_id))}


def _shot_data_uri(shot_id: int | None) -> str:
    """A finished photo as a data: URI, for the vision path. A photo the app owns
    beats one sent in the body: `shot_id` is a photo we can name, the body is
    whatever the browser read off the disk."""
    if not shot_id:
        return ""
    shot = db.one("SELECT * FROM shot WHERE id=?", shot_id)
    if not shot or not shot["filename"]:
        raise HTTPException(400, "that shot has no photo to look at")
    path = SESSIONS_DIR / str(shot["session_id"]) / shot["filename"]
    if not path.exists():
        raise HTTPException(400, "that shot's file is missing")
    data = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


# ------------------------------------------------------------------ static

DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
