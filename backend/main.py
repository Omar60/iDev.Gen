"""iDev.Gen — photo sessions for LoRA character models on top of ComfyUI.

Hierarchy: Model (character) -> Session -> Shots. The model is the identity, the
session is one look — wardrobe, hair, styling — held constant, and the shots are
the takes that vary: pose, angle, framing, corner of the place. Launching a
session queues its shots one at a time in ComfyUI, and every finished file is
moved into the session folder.
"""
from __future__ import annotations

import base64
import collections
import heapq
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
from typing import Literal

import crop
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
    """One composed shot, repeated `count` times: a camera, an act
    and a framing, all drawn from the catalogue. Each is a
    (key, wordings) pair; the composer takes the first wording of
    each and joins them the same way the writer's `_compose` joins
    a take, so the queued line is byte-for-byte identical to one
    a writer would produce from the same components.

    `count` defaults to 1, and the original 3.1 single-shot
    behaviour is `count=1` with no callers passing anything else.
    8.5 added the field: filling a cell to its `judged=10`
    threshold from the screen needs N photographs of the SAME
    trio on the same session, and the alternative — the frontend
    calling `/compose` N times in a loop — cannot honour this
    repo's rule that a refused run queues NOTHING. A frontend
    loop that fails on the seventh call leaves six rows behind
    because `db.run` auto-commits per INSERT, and the cell check
    would have to fire on every loop iteration instead of once.
    The pre-check still runs ONCE, before any insert, in
    `compose_shot_endpoint`: a dead cell is refused in both
    modes, an unknown cell is refused in strict and drawn in
    exploratory. N rows or zero rows, never some.

    The draw is deterministic for 3.1: the caller passes the
    components, and the composer joins them. 3.2 makes the draw
    respect cell state (strict, verified-only), 6.1 makes
    unknown drawable in exploratory mode.

    `mode` is a `Literal["strict", "exploratory"]` (not a free
    string) on purpose. A free string would be a door the type
    definition does not shut today: an `if mode != "strict"` over a
    free string is "anything but strict" passes through, and a
    payload carrying `mode: "anything"` would slip past the check
    and the strict line would still draw. pydantic rejects an
    unknown mode at the boundary, before the cell lookup runs.
    `mode: "exploratory"` widens the draw to include cells that
    have not been measured yet (`unknown`), and excludes `dead`
    cells in both modes — a cell measured 0 of 12 stays out of the
    pool no matter what the caller says. 6.1 is the task that
    opened the second mode.

    Each slot takes ONE component or a LIST of them. A list on more
    than one slot is the cross product: every combination is a cell
    of its own and gets `count` photographs. That is one call and
    not a loop of calls for the same reason `count` is a field —
    every cell is checked before any row is inserted, so a batch
    that would be refused on its ninth combination queues nothing
    at all rather than leaving eight behind.
    """
    camera: dict | list[dict]
    act: dict | list[dict]
    framing: dict | list[dict]
    count: int = Field(1, ge=1)
    mode: Literal["strict", "exploratory"] = "strict"
    # Hand the wardrobe to a reference instead of writing it: the shot's line
    # is composed without the session's wardrobe. Off by default, because a
    # line that says nothing about clothing renders her undressed (measured
    # 2026-08-31: nude 3/3 with no reference attached). There is no twin
    # switch for the pose — an empty wording is already a catalogue component
    # (the `none` control arm), and the wardrobe is the one piece of the line
    # no slot can silence.
    mute_wardrobe: bool = False
    # Shoot this take through the session's reference graph, the same switch
    # `ShotIn.reference` is on the written path. Separate from `mute_wardrobe`
    # on purpose: guiding the body while the line still writes the clothes is a
    # real take, and one flag carrying both meanings is how `use_reference`
    # grew three of them.
    reference: bool = False


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
    # The photographs THIS take is guided by, overriding the session's 📎 pick.
    # An empty list clears it back to "follow the session", which is why the
    # field is `list | None` and not `list`: None is "leave it alone", `[]` is
    # a decision. A shot that has already run keeps whatever it ran with —
    # `runner._reference_values` stamps the column at queue time, and repainting
    # the history would break the before/after pairing that column exists for.
    reference_shot_ids: list[int] | None = None


class ComponentIn(BaseModel):
    concept_key: str
    slot: str
    manner: str
    family: str = ""
    faces: str = ""
    wording: str
    judge_label: str
    # An act's compatible camera families, strongest first. See the column
    # comment in db.py: this is what `fitCameras` walks.
    cameras: list[str] = Field(default_factory=list)
    # What the act needs before it can be photographed: '' for pure geometry,
    # 'him' for an act with a second person in it. See the column comment in
    # db.py — it is a requirement, not a place in the arc.
    needs: str = ""


class ComponentPatch(BaseModel):
    concept_key: str | None = None
    slot: str | None = None
    manner: str | None = None
    family: str | None = None
    faces: str | None = None
    wording: str | None = None
    judge_label: str | None = None
    cameras: list[str] | None = None
    needs: str | None = None


class ReadingIn(BaseModel):
    slot: Literal["camera", "act", "framing"]
    manner: str
    key: str
    label: str
    session_id: int | None = None


class JudgeShotIn(BaseModel):
    """One judging pass's answer for one shot, per slot.

    Each slot records what the judge saw in the photograph:

    - A catalogue key (e.g. ``"astride"``): the judge picked that
      wording from the slot's whole list.
    - ``""``: the judge answered "none or cannot tell" — the spec
      scenario `The judge cannot tell`.
    - ``None`` (or the key absent): the question was not asked on
      this pass. A 5.2 pass asks one question across a batch, so
      the slots the pass did not ask stay at ``None`` and the
      endpoint does not count them.
    - ``defect``: optional defect classification (e.g. ``"contradiction"``).

    The endpoint translates the answers into a per-slot delta the
    cell update carries: each non-``None`` slot increments
    ``judged`` by 1, and a non-empty answer that equals the drawn
    wording also increments ``arrived`` by 1. The endpoint is
    idempotent — a re-judge of a shot that already has verdicts is
    a 409, not a silent double-count.

    6.2 is the task that introduced this shape. 5.2 (the judging
    screen) is the one that builds the payload and posts it here.
    """
    camera: str | None = None
    act: str | None = None
    framing: str | None = None
    slot: str | None = None
    defect: str | None = None
    control: bool = False


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


# ---------------------------------------------------------------- components

# `cameras` is one comma-separated string in the column and a list everywhere
# else. Stored flat because it is a short ordered list of single words and a
# join table for it would be a second table nothing else ever reads; converted
# at the boundary so no caller has to know that.
def _cameras_text(values) -> str:
    return ",".join(v.strip() for v in (values or []) if v and v.strip())


def _component_out(row: dict | None) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    out["cameras"] = [c for c in (out.get("cameras") or "").split(",") if c]
    return out


def _evidence_by_component() -> dict[tuple[str, str, str], dict]:
    """Every component's cell counts, keyed (slot, manner, concept_key).

    Three GROUP BY queries, one per slot, and not one query per component: the
    catalogue screen lists the whole store at once.

    `contradicted` is carried separately from the misses it is part of. A cell
    that failed by contradiction and one that failed by rendering some other
    component are two different findings with two different repairs, and a
    screen that shows only `judged` and `arrived` tells the operator to
    re-measure the same defect (`component-matrix` delta, "A photograph that
    contradicts itself is its own answer").
    """
    out: dict[tuple[str, str, str], dict] = {}
    for slot, column in (("camera", "camera_wording"),
                         ("act", "act_wording"),
                         ("framing", "framing_wording")):
        rows = db.q(
            f"SELECT {column} AS key, manner, SUM(judged) AS judged, "
            "SUM(arrived) AS arrived, SUM(contradicted) AS contradicted "
            f"FROM cell GROUP BY {column}, manner"
        )
        for r in rows:
            out[(slot, r["manner"], r["key"])] = {
                "judged": r["judged"] or 0,
                "arrived": r["arrived"] or 0,
                "contradicted": r["contradicted"] or 0,
            }
    return out


@app.get("/api/components")
def list_components(all: bool = False):
    """List components from the store. Returns non-retired components by default,
    or all components (including retired) if all=1.

    Each row carries the evidence recorded against it — `judged`, `arrived`,
    `contradicted` — summed over its cells, so the catalogue screen can show
    what a wording is worth beside the wording itself.
    """
    if all:
        rows = db.q("SELECT * FROM component ORDER BY slot, manner, id")
    else:
        rows = db.q("SELECT * FROM component WHERE retired_at IS NULL ORDER BY slot, manner, id")
    evidence = _evidence_by_component()
    out = []
    for r in rows:
        c = _component_out(r)
        counts = evidence.get((c["slot"], c["manner"], c["concept_key"]),
                              {"judged": 0, "arrived": 0, "contradicted": 0})
        c.update(counts)
        c["state"] = db.cell_state(counts["judged"], counts["arrived"])
        out.append(c)
    return out


@app.post("/api/components")
def create_component(c: ComponentIn):
    """Create a new prompt component in the store."""
    slot = c.slot.strip()
    if slot not in ("camera", "act", "framing"):
        raise HTTPException(422, "slot must be camera, act, or framing")
    manner = c.manner.strip()
    if not manner:
        raise HTTPException(422, "manner cannot be empty")
    wording = c.wording.strip()
    if not wording:
        raise HTTPException(422, "wording cannot be empty")
    judge_label = c.judge_label.strip()
    if not judge_label:
        raise HTTPException(422, "judge_label cannot be empty")
    if judge_label == wording:
        raise HTTPException(422, "judge_label cannot equal wording")

    dup = db.one("SELECT id FROM component WHERE slot=? AND manner=? AND wording=?", slot, manner, wording)
    if dup:
        raise HTTPException(422, f"Component already exists for slot {slot!r}, manner {manner!r}, and wording {wording!r}")

    comp_id = db.run(
        "INSERT INTO component (concept_key, slot, manner, family, faces, wording, judge_label, cameras, needs, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        c.concept_key.strip(), slot, manner, c.family.strip(), c.faces.strip(), wording, judge_label,
        _cameras_text(c.cameras), c.needs.strip(), db.now(),
    )
    return _component_out(db.one("SELECT * FROM component WHERE id=?", comp_id))


@app.patch("/api/components/{comp_id}")
def update_component(comp_id: int, p: ComponentPatch):
    """Update editable fields on a component."""
    existing = db.one("SELECT * FROM component WHERE id=?", comp_id)
    if not existing:
        raise HTTPException(404, "component not found")

    concept_key = p.concept_key.strip() if p.concept_key is not None else existing["concept_key"]
    slot = p.slot.strip() if p.slot is not None else existing["slot"]
    manner = p.manner.strip() if p.manner is not None else existing["manner"]
    family = p.family.strip() if p.family is not None else existing["family"]
    faces = p.faces.strip() if p.faces is not None else existing["faces"]
    wording = p.wording.strip() if p.wording is not None else existing["wording"]
    judge_label = p.judge_label.strip() if p.judge_label is not None else existing["judge_label"]
    cameras = _cameras_text(p.cameras) if p.cameras is not None else (existing["cameras"] or "")
    needs = p.needs.strip() if p.needs is not None else (existing["needs"] or "")

    if slot not in ("camera", "act", "framing"):
        raise HTTPException(422, "slot must be camera, act, or framing")
    if not manner:
        raise HTTPException(422, "manner cannot be empty")
    if not wording:
        raise HTTPException(422, "wording cannot be empty")
    if not judge_label:
        raise HTTPException(422, "judge_label cannot be empty")
    if judge_label == wording:
        raise HTTPException(422, "judge_label cannot equal wording")

    dup = db.one(
        "SELECT id FROM component WHERE slot=? AND manner=? AND wording=? AND id <> ?",
        slot, manner, wording, comp_id,
    )
    if dup:
        raise HTTPException(422, f"Component already exists for slot {slot!r}, manner {manner!r}, and wording {wording!r}")

    db.run(
        "UPDATE component SET concept_key=?, slot=?, manner=?, family=?, faces=?, wording=?, "
        "judge_label=?, cameras=?, needs=? WHERE id=?",
        concept_key, slot, manner, family, faces, wording, judge_label, cameras, needs, comp_id,
    )
    return _component_out(db.one("SELECT * FROM component WHERE id=?", comp_id))


@app.post("/api/components/{comp_id}/retire")
def retire_component(comp_id: int):
    """Retire a component so it is hidden from composition without breaking historic evidence."""
    existing = db.one("SELECT * FROM component WHERE id=?", comp_id)
    if not existing:
        raise HTTPException(404, "component not found")
    db.run("UPDATE component SET retired_at=? WHERE id=?", db.now(), comp_id)
    return _component_out(db.one("SELECT * FROM component WHERE id=?", comp_id))


@app.post("/api/components/{comp_id}/restore")
def restore_component(comp_id: int):
    """Restore a retired component."""
    existing = db.one("SELECT * FROM component WHERE id=?", comp_id)
    if not existing:
        raise HTTPException(404, "component not found")
    db.run("UPDATE component SET retired_at=NULL WHERE id=?", comp_id)
    return _component_out(db.one("SELECT * FROM component WHERE id=?", comp_id))


@app.delete("/api/components/{comp_id}")
def delete_component(comp_id: int):
    """Delete a component only if no cell evidence or judged shots exist for it."""
    existing = db.one("SELECT * FROM component WHERE id=?", comp_id)
    if not existing:
        raise HTTPException(404, "component not found")

    # What the cell stores in its three `*_wording` columns is the CONCEPT KEY,
    # not the wording text: `compose_shot_endpoint` keys the row on
    # `c.camera["key"]`. Comparing against `component.wording` here matched
    # nothing, ever, so this guard was dead from the day it was written and a
    # measured component deleted with a 200. The column names are historical;
    # the values in them are keys.
    #
    # Scoped to the component's own slot and manner, because a key is only
    # unique within those: an act called `wall` and a camera called `wall`
    # would otherwise shield each other from deletion.
    key = existing["concept_key"]
    slot_column = {"camera": "camera_wording",
                   "act": "act_wording",
                   "framing": "framing_wording"}[existing["slot"]]
    cell_hit = db.one(
        f"SELECT 1 FROM cell WHERE {slot_column}=? AND manner=? AND judged > 0",
        key, existing["manner"],
    )
    if cell_hit:
        raise HTTPException(
            422,
            "Component has cell evidence recorded against it; retire it instead of deleting.",
        )

    # The cell check above is the main one, but cells do not outlive a wipe and
    # judged shots do: the migration that added `contradicted` emptied the
    # table. A shot carries its trio as {"<slot>": {"concept": key, ...}}, so
    # the concept key under this component's own slot is what identifies it.
    # Two patterns because the JSON is written by more than one hand and only
    # one of them uses `json.dumps` defaults (`": "` with the space). Matching
    # the key alone, unscoped by slot, can only ever over-refuse — and a
    # refusal is recoverable where a wrong delete is not.
    shot_hit = db.one(
        "SELECT 1 FROM shot WHERE verdicts <> '' "
        "AND (components LIKE ? OR components LIKE ?)",
        f'%"concept": "{key}"%', f'%"concept":"{key}"%',
    )
    if shot_hit:
        raise HTTPException(
            422,
            "Component has judged shots recorded against it; retire it instead of deleting.",
        )

    db.run("DELETE FROM component WHERE id=?", comp_id)
    return {"ok": True}


@app.post("/api/components/import")
def import_components(items: list[dict] | None = None):
    """Import measured components from JSON or data/catalogue-seed.json."""
    if items is None:
        seed_path = ROOT / "data" / "catalogue-seed.json"
        if not seed_path.exists():
            raise HTTPException(404, "data/catalogue-seed.json not found")
        items = json.loads(seed_path.read_text(encoding="utf-8"))

    added = 0
    skipped = 0
    now_ts = db.now()
    for item in items:
        slot = item["slot"]
        manner = item["manner"]
        concept_key = item["concept_key"]
        wording = item["wording"]
        judge_label = item.get("judge_label", "")
        family = item.get("family", "")
        faces = item.get("faces", "")
        cameras = _cameras_text(item.get("cameras"))
        needs = (item.get("needs") or "").strip()

        existing = db.one(
            "SELECT id FROM component WHERE slot=? AND manner=? AND (concept_key=? OR wording=?)",
            slot, manner, concept_key, wording,
        )
        if existing:
            skipped += 1
        else:
            db.run(
                "INSERT INTO component (concept_key, slot, manner, family, faces, wording, judge_label, cameras, needs, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                concept_key, slot, manner, family, faces, wording, judge_label, cameras, needs, now_ts,
            )
            added += 1
    return {"added": added, "skipped": skipped}


# ------------------------------------------------------------------ readings

@app.get("/api/readings")
def list_readings(slot: str | None = None, manner: str | None = None, session_id: int | None = None):
    """List readings from the store.

    When session_id is provided, returns the union of base readings (session_id IS NULL)
    and session readings (session_id = ?). When omitted, returns base readings only.
    Filters by slot and manner if provided.
    """
    params = []
    where = []
    if session_id is not None:
        where.append("(session_id IS NULL OR session_id = ?)")
        params.append(session_id)
    else:
        where.append("session_id IS NULL")

    if slot:
        where.append("slot = ?")
        params.append(slot)
    if manner:
        where.append("manner = ?")
        params.append(manner)

    where_clause = " WHERE " + " AND ".join(where) if where else ""
    return db.q(f"SELECT * FROM reading{where_clause} ORDER BY slot, manner, id", *params)


@app.post("/api/readings")
def create_reading(r: ReadingIn):
    """Create a new reading in the store."""
    manner = r.manner.strip()
    if not manner:
        raise HTTPException(422, "manner cannot be empty")
    key = r.key.strip()
    if not key:
        raise HTTPException(422, "key cannot be empty")
    label = r.label.strip()
    if not label:
        raise HTTPException(422, "label cannot be empty")

    slot = r.slot

    if r.session_id is not None:
        sess = db.one("SELECT id FROM session WHERE id=?", r.session_id)
        if not sess:
            raise HTTPException(404, "session not found")
        # 1. Collision with base reading
        base_dup = db.one("SELECT id FROM reading WHERE slot=? AND manner=? AND key=? AND session_id IS NULL",
                          slot, manner, key)
        if base_dup:
            raise HTTPException(
                422,
                f"Reading key {key!r} already exists in base scope for {slot}/{manner}",
            )
        # 2. Collision within this session
        sess_dup = db.one("SELECT id FROM reading WHERE slot=? AND manner=? AND key=? AND session_id=?",
                          slot, manner, key, r.session_id)
        if sess_dup:
            raise HTTPException(
                422,
                f"Reading key {key!r} already exists for session {r.session_id}",
            )
    else:
        # Base reading:
        # 1. Collision with another base reading
        base_dup = db.one("SELECT id FROM reading WHERE slot=? AND manner=? AND key=? AND session_id IS NULL",
                          slot, manner, key)
        if base_dup:
            raise HTTPException(
                422,
                f"Reading key {key!r} already exists in base scope for {slot}/{manner}",
            )
        # 2. Collision with ANY session reading of this slot and manner (Task 1.3b)
        sess_dup = db.one("SELECT session_id FROM reading WHERE slot=? AND manner=? AND key=? AND session_id IS NOT NULL",
                          slot, manner, key)
        if sess_dup:
            raise HTTPException(
                422,
                f"Reading key {key!r} already exists in session {sess_dup['session_id']} for {slot}/{manner}",
            )

    reading_id = db.run(
        "INSERT INTO reading (slot, manner, session_id, key, label, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        slot, manner, r.session_id, key, label, db.now(),
    )
    return db.one("SELECT * FROM reading WHERE id=?", reading_id)


@app.post("/api/readings/import")
def import_readings(items: list[dict] | None = None):
    """Import base readings from JSON, or from data/readings-seed.json.

    A judging pass refuses a slot whose photographed families have no reading —
    the correct answer would not be on the list, so every photograph of that
    family would be recorded as a miss. Candid had none at all, and writing the
    twelve it needed by hand was what stood between a shot session and any
    number about it. The vocabulary belongs in the repo for the same reason the
    component catalogue does: it is what somebody measured with, and a fresh
    database that cannot judge is a fresh database that cannot measure.

    Idempotent on (slot, manner, key) in the base scope, like
    `/api/components/import`: an existing key is SKIPPED and its label left
    alone. That is deliberate — a label is the question a stored verdict was
    answered against, and re-importing a seed must never quietly re-word the
    question under answers already given. Editing one is a decision, not an
    import.
    """
    if items is None:
        seed_path = ROOT / "data" / "readings-seed.json"
        if not seed_path.exists():
            raise HTTPException(404, "data/readings-seed.json not found")
        items = json.loads(seed_path.read_text(encoding="utf-8"))

    added = skipped = 0
    for item in items:
        slot, manner = item["slot"], item["manner"].strip()
        key, label = item["key"].strip(), item["label"].strip()
        if slot not in ("camera", "act", "framing"):
            raise HTTPException(422, f"slot must be camera, act, or framing, got {slot!r}")
        if not (manner and key and label):
            raise HTTPException(422, f"slot, manner, key and label are all required: {item}")
        if db.one("SELECT id FROM reading WHERE slot=? AND manner=? AND key=?", slot, manner, key):
            skipped += 1
            continue
        db.run("INSERT INTO reading (slot, manner, session_id, key, label, created_at) "
               "VALUES (?, ?, NULL, ?, ?, ?)", slot, manner, key, label, db.now())
        added += 1
    return {"added": added, "skipped": skipped}


@app.delete("/api/readings/{reading_id}")
def delete_reading(reading_id: int):
    """Delete a reading only if no stored verdict references it."""
    existing = db.one("SELECT * FROM reading WHERE id=?", reading_id)
    if not existing:
        raise HTTPException(404, "reading not found")

    slot = existing["slot"]
    key = existing["key"]

    if existing["session_id"] is not None:
        # Session reading: check shots in this session only
        rows = db.q("SELECT verdicts FROM shot WHERE session_id=? AND verdicts <> ''", existing["session_id"])
    else:
        # Base reading: check shots in every session of this manner
        rows = db.q(
            "SELECT s.verdicts FROM shot s "
            "JOIN session sess ON s.session_id = sess.id "
            "WHERE sess.manner=? AND s.verdicts <> ''",
            existing["manner"],
        )

    count = 0
    for row in rows:
        try:
            verdicts = json.loads(row["verdicts"])
            if verdicts.get(slot) == key:
                count += 1
        except Exception:
            pass

    if count > 0:
        raise HTTPException(
            422,
            f"Reading {key!r} is referenced by {count} stored answer{'s' if count != 1 else ''}; cannot delete.",
        )

    db.run("DELETE FROM reading WHERE id=?", reading_id)
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

    if s.manner and s.shots:
        cam_count = db.one(
            "SELECT COUNT(*) AS n FROM component WHERE slot='camera' AND manner=? AND retired_at IS NULL",
            s.manner,
        )["n"]
        if cam_count == 0:
            raise HTTPException(
                422,
                f"session creation refused: camera catalogue is empty for manner {s.manner!r}; "
                f"import the measured catalogue via /api/components/import or add components before creating sessions",
            )
        kiss_map = {"directed": "front-direct", "candid": "front-arm-length", "selfie": "front-arm-length"}
        kiss_cam_key = kiss_map.get(s.manner, "front-direct")
        has_kiss_cam = db.one(
            "SELECT 1 FROM component WHERE slot='camera' AND manner=? AND concept_key=? AND retired_at IS NULL",
            s.manner, kiss_cam_key,
        )
        if not has_kiss_cam:
            is_kiss_session = any(
                "kiss" in (shot.prompt or "").lower() or "kiss" in (shot.take or "").lower()
                for shot in s.shots
            ) if s.shots else False
            if is_kiss_session:
                raise HTTPException(
                    422,
                    f"session creation refused: kiss frame requires camera {kiss_cam_key!r} in catalogue for manner {s.manner!r}",
                )

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
    # The clone's origin is the source's: a clone of a `'composed'`
    # session is a `'composed'` session, a clone of a `'mixed'`
    # session is a `'mixed'` session, a clone of a `'written'`
    # session is a `'written'` session. A draft (`''` default,
    # the source has no shots yet) clones as a draft — the
    # first insertion on the clone is what stamps the column.
    if src["origin"]:
        db.run("UPDATE session SET origin=? WHERE id=?", src["origin"], new_id)

    ids: dict[int, int] = {}
    for shot in db.q("SELECT * FROM shot WHERE session_id=? ORDER BY id", sid):
        imported = shot["status"] == "done" and not shot["prompt_id"] and shot["filename"]
        new_shot = db.run(
            """INSERT INTO shot (session_id, shot_index, shot_label, prompt, negative,
                                 use_reference, reference_strength, seed, status,
                                 components, mute_wardrobe, origin_shot_id,
                                 created_at, finished_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            new_id, shot["shot_index"], shot["shot_label"], shot["prompt"], shot["negative"],
            shot["use_reference"], shot["reference_strength"], shot["seed"],
            "done" if imported else "pending",
            # The components JSON is copied byte-for-byte: a
            # composed source is a composed clone, and the
            # trio is what 6.2 will read off the clone's
            # shots to count the reshoot toward the right
            # cell. A written source carries `'{}'` here
            # (3.1's marker for "no trio"), and the clone
            # carries the same. A future "let me skip the
            # column for old clones" lands here as a
            # regression on this INSERT.
            shot["components"] or "{}",
            # A clone of a shot that handed its wardrobe to a reference is a
            # shot that hands its wardrobe to a reference: the line was
            # composed without the garment and the copy carries the same line,
            # so the flag has to travel with it or the row stops explaining
            # its own prompt.
            shot["mute_wardrobe"],
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
    """Compose `count` photographs of one trio from the catalogue
    and queue them, with no writer request. `count=1` is the 3.1
    single-shot case; `count>1` is the 8.5 fill-cell case
    (queue N photographs of the same trio on the same session,
    so an operator can take a cell to its `judged=10` threshold
    without a script). The components are recorded on every row
    in the `components` column; the queued line joins identically
    to one a writer would produce from the same three components,
    because `compose_shot` and `_compose` go through the same
    `_sentences` join (see
    `test_a_composed_shot_joins_identically_to_a_written_one`).

    The cell lookup is exact: a cell is the trio plus the session's
    two non-trio dimensions, and a trio verified on a different
    checkpoint is not enough. The 422 message names the trio, the
    session's manner and checkpoint, and the cell state the lookup
    found, so the caller can see whether the gap is a missing
    measurement (unknown) or a failed one (dead). The check runs
    ONCE, before any insert: a dead cell is refused in both modes,
    an unknown cell is refused in strict and drawn in exploratory,
    and the pre-check makes "k rows committed, k+1 refused"
    impossible. N rows or zero rows, never some. Seed handling is
    the runner's: every row is stored with `seed=0` and the runner
    rolls a fresh `random.randint` per shot
    (`backend/runner.py:117`), so N identical prompts DO render N
    different photographs.

    `mode` selects what is drawable. In `strict`, only `verified`
    cells pass — `unknown` and `dead` are refused. In
    `exploratory`, `verified` and `unknown` pass; `dead` is refused
    in both modes, because a dead cell carries a measurement the
    table is asking the operator to honour. The Literal on the
    payload closes the door an `if mode != "strict"` over a free
    string would open: a wrong value never reaches this branch.
    6.1 is the task that opened the second mode.
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
    # The cell is keyed on (trio, manner, checkpoint). The session
    # is the only home for the two non-trio dimensions, and a
    # session that has neither cannot match any cell. The
    # pre-check names what is missing rather than silently
    # failing the cell lookup — the same shape 3.2 and 3.3
    # already pin on their 422s.
    missing = [name for name, value in (("manner", session["manner"]),
                                        ("checkpoint", session["checkpoint"]))
               if not value]
    if missing:
        raise HTTPException(
            422,
            f"compose refused: session is missing {', '.join(missing)}; "
            f"set them on the session before composing",
        )

    for slot_name in ("camera", "act", "framing"):
        slot_count = db.one(
            "SELECT COUNT(*) AS n FROM component WHERE slot=? AND manner=? AND retired_at IS NULL",
            slot_name, session["manner"],
        )["n"]
        if slot_count == 0:
            raise HTTPException(
                422,
                f"compose refused: {slot_name} catalogue is empty for manner {session['manner']!r}; "
                f"import the measured catalogue via /api/components/import or add components before composing",
            )

    # One component or a list of them per slot; a list on more than
    # one slot is the cross product. Normalising here rather than in
    # the model keeps the union in one place and leaves every branch
    # below written against a list.
    def as_list(v):
        return v if isinstance(v, list) else [v]

    combos = [(cam, act, fr)
              for cam in as_list(c.camera)
              for act in as_list(c.act)
              for fr in as_list(c.framing)]

    # Every cell is checked BEFORE any insert. A batch refused on its
    # ninth combination queues nothing rather than leaving eight
    # behind — the same "N rows or zero rows" rule `count` already
    # keeps for one cell, held across the whole cross product.
    settings = json.loads(session["settings"] or "{}")
    look = session["look"] if settings.get("use_look", True) else ""
    for cam, act, fr in combos:
        # `_slot_concept_wording_text` and not `cam["key"]`: a caller that names
        # its components the way the catalogue does — `concept_key` — used to get
        # a bare KeyError here, which is a 500 and a reset connection AFTER the
        # loop had already inserted rows for the combinations before it. The
        # helper accepts both spellings and every other line in this file already
        # goes through it.
        cam_key, _, cam_text = _slot_concept_wording_text(cam)
        act_key, _, act_text = _slot_concept_wording_text(act)
        fr_key, _, fr_text = _slot_concept_wording_text(fr)
        trio = (f"({cam_key}, {act_key}, {fr_key}, "
                f"{session['manner']}, {session['checkpoint']})")
        # A named slot with no wording text is a corrupted measurement, not a
        # control. `_slot_concept_wording_text` falls back to "" when the caller
        # sends `{"concept_key": "front-direct"}` and no wording, and the empty
        # text is then dropped by the `_sentences` join — so the queued line has
        # no camera in it while the cell row is keyed `front-direct`. Ten
        # photographs of nothing, filed under a trio. The `none` control arm is
        # the one legitimate empty: it is keyed `none` and the cell records it
        # as such.
        for slot_name, key, text in (("camera", cam_key, cam_text),
                                     ("act", act_key, act_text),
                                     ("framing", fr_key, fr_text)):
            if not text.strip() and key != "none":
                raise HTTPException(
                    422,
                    f"compose refused: cell {trio} names {slot_name} {key!r} with no wording text; "
                    f"send the wording (e.g. {{'key': {key!r}, 'wordings': [{{'key': {key!r}, "
                    f"'text': '...'}}]}}) or use the 'none' control arm",
                )
        # The crop law, before the cell lookup: a trio whose framing claims a crop
        # above something the rest of the line names cannot measure its framing,
        # because the photograph is cut where the anatomy is. Refused in both
        # modes and in the pre-check, so a batch queues nothing rather than
        # queueing the combinations before the contradiction.
        # A muted wardrobe is not on the line, so it does not name a body part
        # for the crop law to reach: the check reads what is sent, not what the
        # session holds. Two calculations that disagree here refuse a legal trio.
        clash = crop.conflict(fr_text, cam_text, act_text,
                              "" if c.mute_wardrobe else session["wardrobe"], look)
        if clash:
            raise HTTPException(422, f"compose refused: cell {trio} contradicts itself — {clash}")
        cell = db.one(
            "SELECT judged, arrived FROM cell "
            "WHERE camera_wording=? AND act_wording=? AND framing_wording=? "
            "AND manner=? AND checkpoint=?",
            cam_key, act_key, fr_key,
            session["manner"], session["checkpoint"],
        )
        if not cell:
            # No row means the trio was never measured (the
            # measurement did not name a component of the trio at
            # all). In strict mode, this is a 422 — the cell is
            # unknown. In exploratory mode, the trio is drawable
            # if every other candidate is also unmeasured, but
            # the `none` filter is a pool-level concern, not a
            # one-shot one: the one-shot endpoint has no pool, it
            # looks the trio up directly, and an absent cell is
            # an unknown trio. Exploratory mode widens the pool
            # to include these, so the same call with
            # `mode=exploratory` queues the shot.
            if c.mode == "strict":
                raise HTTPException(
                    422,
                    f"compose refused: cell {trio} "
                    f"has no measurement (unknown); switch to exploratory "
                    f"mode to compose from unmeasured cells",
                )
            continue
        state = db.cell_state(cell["judged"], cell["arrived"])
        if state == "dead":
            # Dead in both modes. A measured 0 of 12 is a result,
            # not a gap, and "let me draw it anyway" would
            # contradict the measurement the cell table is
            # asking the operator to honour. The 422 message
            # names the cell and the state, the same shape 3.2
            # carries.
            raise HTTPException(
                422,
                f"compose refused: cell {trio} "
                f"is {state}, not drawable in any mode",
            )
        if state != "verified" and c.mode == "strict":
            # Unknown on a row exists when judged < 10 but a row
            # is present. Strict refuses; exploratory accepts
            # the same row. The 422 names the cell and the state
            # and suggests the wider mode, the same way the
            # "no row at all" branch does.
            raise HTTPException(
                422,
                f"compose refused: cell {trio} "
                f"is {state}, not verified; switch to exploratory "
                f"mode to compose from unmeasured cells",
            )

    # All checks have passed. Queue `count` rows of every combination.
    # `db.run` commits per INSERT inside `compose_and_queue_shot`; the
    # runner rolls a fresh `random.randint` seed per row, so the same
    # trio renders N different photographs.
    shot_ids = [
        compose_and_queue_shot(sid, cam, act, fr, c.mute_wardrobe, c.reference)
        for cam, act, fr in combos
        for _ in range(c.count)
    ]
    return {"ids": shot_ids, "count": len(shot_ids), "cells": len(combos)}


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

    `mode` is a `Literal["strict", "exploratory"]` (not a free
    string) on purpose, the same way it is on `ComposeIn` and
    `ComposeSessionIn`. A free string would let a wrong value
    bypass the check; the Literal narrows it to the two modes the
    pool builders know how to build. 6.1 opens the seam.
    """
    count: int = Field(..., ge=1)
    candidates: dict  # {"camera": [...], "act": [...], "framing": [...]}, each a list of {key, wordings:[{key, text}]}
    mode: Literal["strict", "exploratory"] = "strict"
    # Hand the wardrobe to a reference instead of writing it: the shot's line
    # is composed without the session's wardrobe. Off by default, because a
    # line that says nothing about clothing renders her undressed (measured
    # 2026-08-31: nude 3/3 with no reference attached). There is no twin
    # switch for the pose — an empty wording is already a catalogue component
    # (the `none` control arm), and the wardrobe is the one piece of the line
    # no slot can silence.
    mute_wardrobe: bool = False
    # Same switch as `ComposeIn.reference`, same reason it is its own flag.
    reference: bool = False
    # The per-photograph clauses no catalogue row carries: how the frame is
    # careless (`FRAMING_SLIPS`) and how the photograph was taken
    # (`TECHNIQUE_DEFECTS`). Dealt by the caller, one string per photograph, in
    # the order the shots are queued; a short list (or the empty default every
    # directed session sends) leaves the remaining lines without one.
    #
    # Dealt by the CALLER and not here because the lists and the spreader are the
    # written path's, in `frontend/src/kinds.js` — the same rows, the same
    # no-two-running rule. A second copy of either in Python is a second answer
    # to "what does candid deal", and this repo has one home per fact.
    #
    # They are part of the line, so they are part of what names her lowest part:
    # `an elbow or a knee runs out of the edge of the frame` puts her knees in
    # the line, and the crop law reads them out of the draw's context the same
    # way it reads the wardrobe (`backend/crop.py`).
    extras: list[str] = Field(default_factory=list)
    # The wardrobe each photograph is composed with, in the order the shots are
    # queued: the arc of a shoot that undresses. An index the list does not reach
    # falls back to the session's wardrobe, so the default (an empty list) is
    # every photograph in the session's clothes — what a composed run was before
    # this existed.
    #
    # The states are spread over the run by the caller
    # (`enhance.js:spread`), the same function that spreads them over written
    # takes: K states, N photographs, and the wardrobe holds still between two
    # photographs of one stage. A stage is a state of the whole photograph, and
    # the catalogue's acts carry no stage of their own yet — so the arc dresses
    # whatever body the draw dealt, and a late state can land on an early act.
    # That is the known ceiling of a composed arc, not a bug in this field.
    wardrobes: list[str] = Field(default_factory=list)
    # Whether a second person is in the room for this run. Off by default, and
    # off means an act that needs him is not drawable: composed session 330 dealt
    # `She is astride him ... two people in frame` to a photograph whose wardrobe
    # state was a vest and knickers, three times in nine, because the act pool
    # holds every act of the manner and the draw had no way to know which stage
    # of the arc it was filling.
    #
    # On means "he is here", not "only him": the pool is everything again. An
    # operator who wants nothing but the explicit acts already has a way to say
    # so — `candidates` is theirs to narrow, and a run that sends three acts
    # draws from three acts.
    with_him: bool = False
    # Whether she is undressed for this run. Off by default, and off means an act
    # that needs her bare is not drawable — the explicit solo acts (a hand between
    # her legs, a toy, holding herself open) are photographs of a body with
    # nothing on it, and dealing one to a stage still in a sweatshirt is the same
    # contradiction `with_him` was added to stop.
    #
    # Independent of `with_him`: the acts that need him say so in their own
    # wording and do not need this flag as well.
    bare: bool = False


_SLOT_COLS = (
    ("camera", "camera_wording"),
    ("act", "act_wording"),
    ("framing", "framing_wording"),
)
_SLOT_ORDER = ("camera", "act", "framing")


def _candidate_keys(slot: str, candidates: dict) -> list[str]:
    return [c["key"] for c in candidates.get(slot, []) if isinstance(c, dict) and c.get("key")]


def _wording_texts(candidates: dict) -> dict[str, dict[str, str]]:
    """{slot: {key: wording text}} for the caller's candidate lists."""
    out: dict[str, dict[str, str]] = {}
    for slot in _SLOT_ORDER:
        by_key = {}
        for c in candidates.get(slot, []) or []:
            if isinstance(c, dict) and c.get("key"):
                by_key[c["key"]] = _slot_concept_wording_text(c)[2]
        out[slot] = by_key
    return out


def _without_crop_conflicts(pool: set[tuple[str, str, str]], candidates: dict,
                            context: str) -> set[tuple[str, str, str]]:
    """The pool with the self-contradicting trios taken out.

    A trio whose framing claims a crop above what the rest of the line names is
    not drawable in either mode: the photograph is cut where the anatomy is, so
    the cell measures the anatomy and not the framing. See `backend/crop.py` for
    the sessions.

    It is done INSIDE the pool and not as a check after the draw, which is the
    lesson group 3 learned four times over: a constraint checked after the draw is
    a constraint the draw does not have, and the run is then refused with a
    "largest fillable" number that is not the largest fillable.
    """
    text = _wording_texts(candidates)
    return {(cam, act, fr) for cam, act, fr in pool
            if not crop.conflict(text["framing"].get(fr, ""),
                                 text["camera"].get(cam, ""),
                                 text["act"].get(act, ""),
                                 context)}


def _camera_family(candidate: dict) -> str:
    """The family a camera candidate belongs to.

    Read off the candidate and not the database: the pool is built from what the
    caller sent, and a family looked up behind the caller's back is a second
    answer to "what is this camera". The frontend sends it in both places the
    catalogue holds it (`positionsFor` puts it on the row AND on the wording), so
    both are accepted.
    """
    wording = (candidate.get("wordings") or [{}])[0]
    return candidate.get("family") or wording.get("family") or ""


def _act_camera_families(candidate: dict) -> set[str]:
    """The camera families an act is written for, or an empty set for "any".

    Empty is the same "no opinion" `fitCameras` reads it as on the written path
    (`frontend/src/kinds.js`): an act that names no family is drawable from
    anywhere, which is what every act in `directed` that is pure geometry wants.
    The API serves the column as an array; a raw seed row carries it as a
    comma-separated string, and both are accepted for the same reason
    `arrangements()` accepts both.
    """
    value = candidate.get("cameras")
    if isinstance(value, str):
        value = [v for v in value.split(",") if v]
    return {v for v in (value or []) if v}


def _without_camera_mismatch(pool: set[tuple[str, str, str]],
                             candidates: dict) -> set[tuple[str, str, str]]:
    """The pool with the trios whose camera cannot see their act taken out.

    An act carries the camera families it is written for, strongest first
    (`component.cameras`). It was read by the written path only — `fitCameras`
    moves a planted arrangement's camera into a family that can see it — and the
    composer drew the camera and the act independently, so a phone held at arm's
    length in front of her face landed on an act whose both hands are flat on the
    floor. Two clauses of one line describing different photographs.

    Empty list on the act is "any camera", not "no camera". The `none` control
    arm carries no list either and stays drawable, which is what it is for.

    The DRAW is filtered and the deliberate fill-cell pick is not. The crop law
    is arithmetic — the photograph is cut where the anatomy is, whatever anybody
    intended — but "this camera can see this act" is a judgement somebody wrote
    into the catalogue, and refusing it on `/compose` would take away the only
    way to measure whether the judgement is right.

    In the pool and not after the draw, for the reason the crop filter is:
    a constraint checked after the draw is a constraint the draw does not have,
    and the "largest fillable" number in the 422 would be a number no compose can
    reach.
    """
    families = {c["key"]: _camera_family(c)
                for c in candidates.get("camera", []) or [] if c.get("key")}
    wants = {c["key"]: _act_camera_families(c)
             for c in candidates.get("act", []) or [] if c.get("key")}
    return {(cam, act, fr) for cam, act, fr in pool
            if not wants.get(act) or families.get(cam, "") in wants[act]}


def _drawable(pool: set[tuple[str, str, str]], candidates: dict,
              context: str) -> set[tuple[str, str, str]]:
    """The pool with every trio that cannot be photographed as written removed:
    the crop contradictions first, then the cameras that cannot see their act.

    One function because both filters belong to the same question — "is this trio
    a photograph at all" — and because a caller that runs one and forgets the
    other is exactly the bug this repo keeps finding twice.
    """
    return _without_camera_mismatch(
        _without_crop_conflicts(pool, candidates, context), candidates)


def _trio_pool(
    manner: str,
    checkpoint: str,
    candidates: dict,
    mode: Literal["strict", "exploratory"],
    context: str = "",
) -> set[tuple[str, str, str]]:
    """The drawable trio pool for `mode`. The unit of evidence is
    a cell identified by the trio plus manner and checkpoint
    (design.md:130 and decision C: "the unit of evidence is a
    cell"), and a component verified alone can still fail in
    combination (design.md:326-329). Counting DISTINCT per slot
    independently reads as N×M×K trios when only some of them
    are rows in the table, and the zipped picker then draws
    trios that nobody verified. The 3.3 success path is "draw N
    trios that are cells", not "draw N×1 cameras and N×1 acts
    and N×1 framings and zip".

    `mode` selects which cells are in the pool:

    - `strict`: only `verified` cells, predicate
      `judged >= 10 AND arrived*10 >= judged*8`. Mirrors
      `db.cell_state` exactly — the same definition; the pool
      builder is the SQL form of the same rule.
    - `exploratory`: every candidate trio EXCEPT the `dead`
      ones. Not a `SELECT` over the table: a cell that was
      never measured has no row at all, and `judged < 10` is
      the definition of `unknown` — so "unknown" is mostly
      the cells the table has never heard of. Selecting from
      `cell` would have made exploratory able to explore only
      what somebody already measured, which is the opposite
      of the mode. The pool is therefore the product of the
      candidate keys minus the dead rows, and the whole point
      is to grow the matrix: every composed shot feeds its
      cell and the threshold lands the verdict (6.2). A `dead`
      cell stays out — measured 0 of 12 is a result, not a
      gap, and "let me draw it anyway" would skip what the
      measurement said.

    The literal `none` is filtered on every slot in both modes:
    it represents measurements that did not break out a slot,
    not a real catalogue key, and letting it into the pool
    would inflate the count with something no compose can
    draw. The filter is part of the pool builder, not a
    post-filter: a 422 on a `none` cell would read as "the
    pool is empty" rather than "the row exists but is not
    drawable", and the operator deserves to know which it
    is. (This mirrors what `_draw_n_trio_shots` already
    relied on.)
    """
    cam_keys = _candidate_keys("camera", candidates)
    act_keys = _candidate_keys("act", candidates)
    framing_keys = _candidate_keys("framing", candidates)
    if not (cam_keys and act_keys and framing_keys):
        return set()
    cam_ph = ",".join("?" for _ in cam_keys)
    act_ph = ",".join("?" for _ in act_keys)
    framing_ph = ",".join("?" for _ in framing_keys)
    # The two modes ask the table opposite questions, so they read it
    # in opposite directions. Strict asks "which rows are verified" and
    # a row is required. Exploratory asks "which trios are NOT dead",
    # and a trio with no row is exactly the thing it exists to reach —
    # so the pool starts from the candidates and the table is used to
    # SUBTRACT. Doing it as one `SELECT` with a looser predicate was
    # the first shape and it was wrong: it made exploratory able to
    # draw only cells somebody had already measured, while the
    # one-shot `/compose` endpoint queued an unmeasured trio happily.
    # Two calculations that were supposed to agree and did not, which
    # is the same shape as every group-3 bug.
    state_pred = ("AND judged >= 10 AND arrived*10 >= judged*8" if mode == "strict"
                  else "AND judged >= 10 AND arrived*10 < judged*8")
    rows = db.q(
        f"SELECT camera_wording, act_wording, framing_wording FROM cell "
        f"WHERE manner = ? AND checkpoint = ? "
        f"AND camera_wording IN ({cam_ph}) AND camera_wording != 'none' "
        f"AND act_wording IN ({act_ph}) AND act_wording != 'none' "
        f"AND framing_wording IN ({framing_ph}) AND framing_wording != 'none' "
        f"{state_pred}",
        manner, checkpoint, *cam_keys, *act_keys, *framing_keys,
    )
    matched = {(r["camera_wording"], r["act_wording"], r["framing_wording"]) for r in rows}
    if mode == "strict":
        return _drawable(matched, candidates, context)
    # ponytail: the full product, which is len(cam) * len(act) * len(framing)
    # trios. The candidate lists are a session's picks, tens at the very
    # most, so this is thousands of tuples at the ceiling and the draw
    # already walks the pool. If a caller ever passes whole catalogues,
    # push the `none` filter and the dead subtraction into SQL instead.
    return _drawable(
        {(cam, act, framing)
         for cam in cam_keys if cam != "none"
         for act in act_keys if act != "none"
         for framing in framing_keys if framing != "none"} - matched,
        candidates, context)


def _spreadable_slots(pool: set[tuple[str, str, str]]) -> set[str]:
    """The slots the no-repeat rule can be applied to: the ones the pool
    offers more than one distinct value for.

    A slot with a single value in the pool cannot be spread over, and
    holding it to "no component twice in a run" caps every run at one
    photograph. That is not hypothetical: the compose control shipped in
    group 8 sends one fixed framing wording (one framing per manner),
    so `count=4` was refused with "framing slot has 1 drawable values,
    largest fillable is 1" and nothing was queued.

    An empty pool has no spreadable slot, and the ceiling for an empty
    pool is zero either way.
    """
    if not pool:
        return set()
    distinct = {"camera": {t[0] for t in pool},
                "act": {t[1] for t in pool},
                "framing": {t[2] for t in pool}}
    return {slot for slot in _SLOT_ORDER if len(distinct[slot]) > 1}


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
    # Only the spreadable slots bound the run — the draw skips a repeat
    # on those alone, so a one-value slot must not be reported as the
    # dimension that ran out. When NO slot is spreadable the pool holds
    # exactly one trio and the ceiling is that one photograph; the slot
    # named is `camera` by the same tie-break the spreadable case uses.
    spreadable = _spreadable_slots(pool)
    if not spreadable:
        return "camera", len(pool)
    counts = {s: len(distinct[s]) for s in _SLOT_ORDER if s in spreadable}
    slot = min(counts, key=lambda s: (counts[s], _SLOT_ORDER.index(s)))
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

    3.5 (`compose_session_endpoint`) is a sibling: same draw,
    same dedup, then a reordering pass that spreads camera
    families across consecutive shots. The shared body lives
    in `_draw_n_trio_shots`; the two endpoints differ only at
    the post-draw step (3.5 adds `_skip_for_spread` as the
    caller's skip predicate, 3.3 passes no skip).
    """
    by_key, best_chosen = _draw_n_trio_shots(sid, c.count, c.candidates, mode=c.mode,
                                             mute_wardrobe=c.mute_wardrobe,
                                             extras=c.extras, wardrobes=c.wardrobes,
                                             with_him=c.with_him, bare=c.bare)
    shot_ids: list[int] = []
    for at, (cam_key, act_key, framing_key) in enumerate(best_chosen):
        shot_ids.append(compose_and_queue_shot(
            sid, by_key["camera"][cam_key], by_key["act"][act_key], by_key["framing"][framing_key],
            c.mute_wardrobe, c.reference, _extra_at(c.extras, at),
            _wardrobe_at(c.wardrobes, at),
        ))
    return {"ids": shot_ids, "count": len(shot_ids)}


def _skip_for_spread(
    trio: tuple[str, str, str],
    by_key: dict,
    family_counts: dict,
    max_per_family: int,
) -> bool:
    """The family-spread skip predicate `_draw_n_trio_shots`
    takes for 3.5. The bound is `max_per_family = ceil(count/2)`,
    the classical "reorganize string" feasibility condition:
    no family may exceed `ceil(N/2)` in the chosen set, or no
    ordering places no two consecutive photographs in different
    families.

    A trio whose family is `None` (a non-spread slot, the act
    or the framing today) is exempt — the spread does not
    bind it. A trio whose family count is already at the
    bound is skipped, so the next shuffle iteration that
    walks the same pool still has that trio available (it is
    only the greedy's set that loses it).

    The function is the per-trio form of the 3.3 set-level
    accept `_spread_is_feasible`. Both read the same bound
    off `count`; the per-trio form is what lets the bound be
    enforced in the draw itself, and the set-level form
    becomes the `_spread_worst_family` / `_reorder_to_spread_families`
    defensive check on the caller's path. The shape of the
    pre-check is the same — a refusal that names the family,
    the count, and the bound — but the draw never returns an
    invalid set for the caller's check to refuse.
    """
    fam = _spread_family_of(trio, by_key)
    if fam is None:
        return False
    return family_counts.get(fam, 0) >= max_per_family


def _draw_n_trio_shots(
    sid: int,
    count: int,
    candidates: dict,
    mode: Literal["strict", "exploratory"] = "strict",
    skip: "Callable | None" = None,
    mute_wardrobe: bool = False,
    extras: list[str] | None = None,
    wardrobes: list[str] | None = None,
    with_him: bool = False,
    bare: bool = False,
) -> tuple[dict, list[tuple[str, str, str]]]:
    """The shared draw used by `compose_run_endpoint` (3.3) and
    `compose_session_endpoint` (3.5). Returns `(by_key,
    best_chosen)` after the session is validated, the
    mode-dependent pool is built, the multi-shuffle greedy has
    run, and the 3.4 dedup pre-check has passed.

    The function never inserts; the caller inserts. That is
    the property the two endpoints share: the draw is
    side-effect-free until the caller decides what to do with
    the chosen trios (3.3 inserts in `best_chosen` order, 3.5
    reorders for the family spread and inserts in the
    reordered order).

    `mode` selects which cells are in the pool. `strict` is
    verified cells only; `exploratory` is verified plus
    unmeasured (`unknown`) cells, with `dead` cells excluded
    in both modes. The Literal type on the payload closes the
    door an `if mode != "strict"` over a free string would
    open: a wrong value never reaches the pool builder.

    `skip` is the caller's per-trio skip predicate, called
    AFTER the no-repeat check on each candidate trio. It is
    how the family-spread constraint (3.5) is part of the
    draw, not a filter on the draw's result. The greedy
    skips a trio if `skip(trio, by_key, family_counts,
    max_per_family)` returns True; the chosen set is then
    always valid against the constraint, and the post-draw
    accept/filter is gone. This is the rule 3.3 named —
    "the check and the draw are the same calculation" —
    carried to its logical end: the family bound is enforced
    in the loop, not after it.

    The four 422 paths that may fire on the way:

    1. `404 session not found`
    2. `422 session is missing {manner,checkpoint}` — the
       same pre-check 3.2's one-shot endpoint runs. A
       session that has no non-trio dimensions cannot match
       any cell, and "no cell matches" is a different shape
       from "the pool is too small" — refuse the
       session-level one first.
    3. `422 {min_slot} slot has {min_count} drawable
       values, largest fillable is {len(best_chosen)} (of
       {count} requested)` — the pool-too-small refusal
       from 3.3, the multi-shuffle ceiling the operator
       sees rather than one shuffle's luck. The message
       names the mode suggestion in strict mode only:
       exploratory is the way to grow the pool, and a
       request that is already in exploratory has no
       "switch to a wider mode" step to take.
    4. `422 tuple already enqueued` / `422 line already
       enqueued` — the 3.4 dedup, which runs BEFORE the
       caller inserts and refuses the whole run on the
       first collision. The dedup's tuple/line SETS are the
       in-loop half: the first candidate seeds them, the
       second one fires. Both checks are run for both
       endpoints, and 3.5 inherits them unchanged.

    ponytail: the 3.3 multi-shuffle greedy is what makes
    `len(best_chosen)` the right answer to "what is the
    largest fillable". A single shuffle can fall short on
    pathological pools (e.g., the (c1,a1,f1) / (c1,a2,f2)
    / (c2,a1,f3) probe), and the ceiling is what the
    operator wants to read in the 422. N_SHUFFLES=10 keeps
    the probability of all-bad on the user's probe below the
    20-call test's flake budget.

    ponytail: dedup is a pre-check, not a skip-and-fill. A
    loop that walks `best_chosen` and replaces a collision
    on the fly is a second calculation layered on top of
    the greedy, and 3.3 closed that door — "the check and
    the draw are the same calculation". The 3.5 caller
    sees the same refusal; the reordering it does next is
    independent of the dedup, because dedup is a property
    of the SET of trios (order-independent) and the
    reordering is a property of the LIST of trios
    (order-dependent).

    ponytail: 3.5 used to pass `accept=_spread_is_feasible`
    as a post-draw filter, which made the test
    `test_a_session_compose_draws_a_spreadable_set_when_the_pool_is_larger_than_the_count`
    flake at ~2 runs in 8. The flake shape was: 10 shuffles,
    none found a 4-trio set whose family counts satisfied
    `max <= ceil(N/2)`, so `best_accepted` stayed empty and
    `best_chosen` (the longest invalid draw) was returned,
    the caller's spread reorder raised 422, and the test
    read it as a verdict mismatch. With `skip` running
    inside the greedy, the chosen set is always valid
    against the constraint, and the only way the run
    refuses is the pool-too-small path. The flake budget
    drops to "no test exercises the shape anymore".
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

    # The stage, applied to the CANDIDATES and not to the pool: narrow the list
    # first and every number downstream is honest by construction â€” the pool, the
    # no-repeat ceiling, and the "largest fillable" the 422 names. Filtering the
    # pool instead would leave the refusal quoting a count that includes acts this
    # run was never allowed to draw.
    # What this run provides, against what each act needs. An act that needs
    # nothing is always drawable; one that needs him, or needs her bare, is
    # drawable only when the run says so. The run is the only thing that knows â€”
    # the act carries a requirement, not a place in the arc.
    provides = {""}
    if with_him:
        provides.add("him")
    if bare:
        provides.add("nude")
    candidates = dict(candidates,
                      act=[a for a in (candidates.get("act") or [])
                           if (a.get("needs") or "").strip() in provides])

    settings = json.loads(session["settings"] or "{}")
    # The wardrobe and the look are part of the composed line, so they are part of
    # what names her lowest part: a session in stockings has no crop above the feet
    # available to it, whatever the act says (session 318).
    # The dealt extras go in for the same reason the wardrobe does: a slip that
    # says a knee runs out of the frame names her knees, and a framing claiming a
    # crop above them is a trio that measures the anatomy instead of the framing.
    # In the pool and not after the draw — a constraint checked after the draw is
    # a constraint the draw does not have.
    # EVERY state the run may write, not only the session's: the pool is drawn
    # once for the whole run, so a trio has to survive every wardrobe any of its
    # photographs could be composed with. Stockings in the last state of the arc
    # take the tight crops out of the pool for the whole run — conservative on
    # purpose, and the alternative is a per-photograph pool, which is a second
    # draw and a second answer to "what is drawable".
    # A run that deals a wardrobe to EVERY photograph never writes the session's,
    # so reading it into the crop context refuses trios no line of this run could
    # contradict — an arc whose first state names leggings would take every
    # framing above the knee out of the pool for the whole shoot, including the
    # photographs that are down to a vest. The context has to be what the run may
    # WRITE, which is the same rule the draw already keeps: two calculations that
    # disagree refuse a legal trio.
    deals_every_row = len(wardrobes or ()) >= count
    context = _sentences("" if (mute_wardrobe or deals_every_row) else (session["wardrobe"] or ""),
                         session["look"] if settings.get("use_look", True) else "",
                         *(extras or []),
                         *(() if mute_wardrobe else (wardrobes or ())))
    pool = _trio_pool(session["manner"], session["checkpoint"], candidates, mode, context)

    # The check and the draw are the same calculation. Greedy
    # on a shuffled pool, repeated over a handful of shuffles:
    # take a trio only if none of its three components has been
    # used yet AND the caller's `skip` (the family-spread
    # constraint for 3.5, no-op for 3.3) lets it through; stop
    # at `count`; keep the best result across shuffles; stop
    # early when a shuffle reaches `count`. The largest
    # fillable is `len(best_chosen)` by construction — not the
    # result of one shuffle's luck, which is the failure the
    # previous 3.3 had: the pool (c1,a1,f1), (c1,a2,f2),
    # (c2,a1,f3) with count=2 has a maximum of 2, but a single
    # shuffle that starts with (c1,a1,f1) blocks both other
    # trios (a1 is used, c1 is used) and the greedy reports 1.
    # Twenty calls on the same data gave 9 of "200, 2 shots"
    # and 11 of "422, largest fillable is 1" — the operator
    # refused would retry without changing anything and get
    # 200, which is the bug the multi-shuffle pass fixes.
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
    # Built before the draw, not after it: `skip` is the
    # caller's per-trio skip, and the camera family read
    # happens off the candidate catalogue (`by_key`).
    by_key: dict[str, dict[str, dict]] = {
        slot: {x["key"]: x for x in candidates.get(slot, []) if isinstance(x, dict) and x.get("key")}
        for slot in _SLOT_ORDER
    }
    # The rule 3.4 wrote is "do not repeat when you had somewhere else to go",
    # and the ceiling it produced was the smallest slot: `directed` has six acts,
    # so no run of that manner could ever be longer than six photographs, over a
    # pool of two thousand drawable trios. The generalisation is round-robin, and
    # it is done as PASSES over the shuffled pool rather than as a cap computed
    # from `count`: pass 1 repeats nothing (the old rule exactly), pass 2 allows
    # a second use of each value, and so on until the run is full or a pass adds
    # nothing.
    #
    # Passes and not `ceil(count / values)`, because a cap read off the request
    # makes the answer move when the request does: asked for 50 the draw filled
    # 31, and asked for 31 it filled 20, because the smaller request tightened
    # the cap that produced the number. Under passes the picks are a PREFIX that
    # does not depend on `count` at all — only where it stops — so a request for
    # the largest fillable the 422 names always succeeds.
    #
    # The one-value exemption `_spreadable_slots` bought is still needed and is
    # still its own thing. A slot the pool offers one value for is exempt from
    # the cap entirely rather than being spent in pass 1: letting the pass raise
    # it would raise every other slot with it, and three cameras, three acts and
    # ONE framing would come back as three photographs sharing two cameras. The
    # rule is unchanged — "a one-value slot is not a repeat, it is the only
    # road" — and only the slots that HAVE a choice are held to the pass.
    #
    # The trio is still drawn at most once — `taken` — so a run of N fills N
    # DISTINCT cells however many passes it took. That is the property the matrix
    # needs; "no framing twice" never was one.
    spreadable = _spreadable_slots(pool)
    # `max_per_family` is the bound the family-spread skip
    # keys on. `ceil(count/2)` is the classical "reorganize
    # string" feasibility condition, with the cap of count
    # itself (N=0 has no bound, N=1 has trivially no two
    # adjacent families). 3.3 passes `skip=None` and the
    # bound is unused; 3.5 passes `_skip_for_spread`, which
    # reads it from `count`.
    max_per_family = (count + 1) // 2

    N_SHUFFLES = 10
    best_chosen: list[tuple[str, str, str]] = []
    for _ in range(N_SHUFFLES):
        shuffled = list(pool)
        random.shuffle(shuffled)
        chosen: list[tuple[str, str, str]] = []
        taken: set[tuple[str, str, str]] = set()
        used: dict[str, dict[str, int]] = {"camera": {}, "act": {}, "framing": {}}
        # The family-skip needs the family counts updated as
        # the greedy picks, so a 3rd front is skipped on a
        # 4-trio draw before it would otherwise enter the
        # set. `family_counts` only carries families (None
        # is the value the spread slot's non-camera trios
        # get, and they are exempt).
        family_counts: dict[object, int] = {}
        cap = 0
        while len(chosen) < count:
            cap += 1
            grew = False
            for cam, act, framing in shuffled:
                if len(chosen) == count:
                    break
                trio = (cam, act, framing)
                if trio in taken:
                    continue
                if any(used[slot].get(value, 0) >= cap
                       for slot, value in (("camera", cam), ("act", act), ("framing", framing))
                       if slot in spreadable):
                    continue
                if skip is not None and skip(trio, by_key, family_counts, max_per_family):
                    continue
                chosen.append(trio)
                taken.add(trio)
                grew = True
                for slot, value in (("camera", cam), ("act", act), ("framing", framing)):
                    used[slot][value] = used[slot].get(value, 0) + 1
                # Update the family counts only on a non-None
                # family, so a None trio does not enter the dict
                # and the count stays the same for the rest of
                # the shuffle.
                fam = _spread_family_of(trio, by_key)
                if fam is not None:
                    family_counts[fam] = family_counts.get(fam, 0) + 1
            # A pass that adds nothing is the end of the road: the next one
            # raises the cap on values the pool has no fresh trio for either.
            if not grew:
                break
        if len(chosen) > len(best_chosen):
            best_chosen = chosen
        if len(best_chosen) == count:
            break

    if len(best_chosen) < count:
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
        min_slot, min_count = _min_slot_within(pool)
        # The mode determines what the operator can do next.
        # Strict says "switch to exploratory" because the
        # pool only had verified cells; exploratory is the
        # same path the operator is on, so the suggestion
        # is to revisit the candidates — the pool is empty
        # of any drawable cell, which means every candidate
        # trio is either dead (refused in any mode) or
        # outside the catalogue filter.
        if mode == "strict":
            tail = "; use exploratory mode to compose with unmeasured cells"
        else:
            tail = "; every candidate trio is either dead or outside the catalogue, no further draw is possible"
        # The pool size is in the message because the slot count on its own reads
        # as the ceiling and is not one: `directed` has six acts and two thousand
        # drawable trios, and "act slot has 6" over a refusal to draw 31 sent the
        # operator looking for a catalogue hole that was not there.
        raise HTTPException(
            422,
            f"compose refused: {min_slot} slot has {min_count} drawable "
            f"values within the trio pool of {len(pool)}, largest fillable is "
            f"{len(best_chosen)} (of {count} requested){tail}",
        )

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

    return by_key, best_chosen


def _spread_family_of(trio: tuple[str, str, str], by_key: dict) -> object:
    """The family the spread constraint keys on for a given
    trio, or `None` if the trio is not in a spread slot. Today
    the only spread slot is `camera` (its wordings carry
    `family` in `frontend/src/kinds.js:1671-1690`); `act` and
    `framing` have no family, so their trios return `None` and
    the spread is exempt on them. A future "let me add a
    family to act" lands here as a second branch — the function
    already returns a generic family key, not a hard-coded
    "camera".

    The check is on `wordings[0].get("family")` because today
    every concept has a single wording. A future "let me add
    a second wording with a different family" lands here as a
    question for the catalogue reshape, not for the spread:
    the family is a property of the concept in the catalogue,
    and wordings of one concept share a family by
    construction. The check that answers "is this slot a
    spread slot" is the same field the check that answers
    "what is the family of this trio" reads, so the two
    questions are one answer.
    """
    cam_key = trio[0]
    cam = by_key["camera"].get(cam_key)
    if not cam:
        return None
    wordings = cam.get("wordings") or []
    if not wordings:
        return None
    family = wordings[0].get("family")
    return family if family else None


def _spread_worst_family(
    trios: list[tuple[str, str, str]],
    by_key: dict,
) -> tuple[object, int, int, int]:
    """The most numerous family among `trios`, its count, how
    many trios carry a family at all, and the `ceil(n/2)`
    bound a spread has to stay under. Returns `(None, 0, 0,
    0)` when no trio is in a spread slot.

    One place holds the rule, because two callers ask the
    same question for different reasons: `_reorder_to_spread_families`
    needs the family and the count to name them in its 422,
    and `_spread_is_feasible` needs the yes/no to keep a
    shuffle out of the draw. A copy of `> ceil(n/2)` in the
    second caller is the drift that lets the draw accept a
    set the reorder then refuses.
    """
    counts: dict[object, int] = {}
    for t in trios:
        fam = _spread_family_of(t, by_key)
        if fam is None:
            continue
        counts[fam] = counts.get(fam, 0) + 1
    n = sum(counts.values())
    if not counts:
        return None, 0, 0, 0
    worst_fam = max(counts, key=lambda f: counts[f])
    return worst_fam, counts[worst_fam], n, (n + 1) // 2


def _spread_is_feasible(trios: list[tuple[str, str, str]], by_key: dict) -> bool:
    """Whether SOME ordering of `trios` puts no two
    consecutive photographs in the same family — the
    classical "reorganize string" condition, `max(count per
    family) <= ceil(n/2)`.

    This is the `accept` the 3.5 endpoint hands to the draw.
    It is a property of the SET, which is why it can decide
    a shuffle before the order exists: the reorder that runs
    afterwards is the construction, this is the existence
    question the construction needs answered first.
    """
    worst_fam, worst_count, _n, ceil_n2 = _spread_worst_family(trios, by_key)
    return worst_fam is None or worst_count <= ceil_n2


def _reorder_to_spread_families(
    trios: list[tuple[str, str, str]],
    by_key: dict,
) -> list[tuple[str, str, str]]:
    """Reorder `trios` so no two consecutive photographs share
    a family in the spread slots, or raise 422 if no such
    reordering exists. The classical "reorganize string"
    feasibility condition: `max(count per family) <=
    ceil(N/2)`. If the chosen trios violate it, no
    permutation satisfies the spread and the run is refused
    — same shape as the 3.3 pool-too-small and 3.4 dedup
    refusals, the same loop-closed property
    (`n_shots == 0` after the 422), the same message
    discipline (the four facts the operator needs to act).

    The pass:

    1. Group the trios by their family (via
       `_spread_family_of`, which returns `None` for trios in
       non-spread slots). Trios with `None` family carry no
       spread constraint and the function does not need to
       interleave them — they are appended to the end in
       input order. The interleaving is on the family-bearing
       trios only.
    2. Feasibility check on the family-bearing trios: if
       `max(count) > ceil(N/2)`, refuse with the family and
       its count named in the message, plus the ceil bound
       and the conclusion that no reordering works.
    3. Heap-based "reorganize string": each step pop the
       most-numerous remaining family, and if it equals the
       previous pick, defer to the second-most-numerous. This
       is the standard online construction; the feasibility
       check above guarantees the second-most is always
       available when the most equals the previous family
       (otherwise step 2 would have refused).

    The 422 message names four facts: the family, its count
    in the chosen trios, the ceil bound, and the conclusion
    "no ordering places no two consecutive photographs in
    different families". A future "let me soften the
    message" that drops one of the four fails the assertion
    that names the dropped fact, the same shape 3.3 and 3.4
    pin on their 422 messages.

    The function is pure: it does not insert, it does not
    write, it raises on the only error path. The caller
    inserts in the returned order, the same way
    `compose_run_endpoint` inserts in `best_chosen` order.
    """
    if len(trios) <= 1:
        return list(trios)

    # Split: family-bearing trios (the ones the constraint
    # applies to) and non-family trios (constraint-exempt).
    # Non-family trios carry no family field, so they cannot
    # be ordered relative to a family; they go at the end in
    # the order the greedy chose them, and the spread
    # property on the family-bearing prefix is what the
    # operator sees.
    family_trios: list[tuple[tuple[str, str, str], object]] = []
    other_trios: list[tuple[str, str, str]] = []
    for t in trios:
        fam = _spread_family_of(t, by_key)
        if fam is None:
            other_trios.append(t)
        else:
            family_trios.append((t, fam))

    # Group by family.
    buckets: dict[object, list[tuple[str, str, str]]] = {}
    for t, fam in family_trios:
        buckets.setdefault(fam, []).append(t)

    worst_fam, worst_count, n, ceil_n2 = _spread_worst_family(trios, by_key)
    if worst_fam is not None and worst_count > ceil_n2:
        raise HTTPException(
            422,
            f"compose refused: family {worst_fam!r} has {worst_count} "
            f"entries in the chosen trios, larger than ceil({n}/2)={ceil_n2}; "
            f"no ordering places no two consecutive photographs in different "
            f"families, the spread cannot be satisfied",
        )

    # Max-heap keyed on (-count, family). Python's tuple
    # comparison gives a stable order on ties (the family
    # value), and the function is deterministic given
    # `trios` — the multi-shuffle pass above is the only
    # source of variance, and the 3.3 ceiling (N_SHUFFLES)
    # keeps the verdict stable.
    heap = [(-len(v), f) for f, v in buckets.items()]
    heapq.heapify(heap)

    out: list[tuple[str, str, str]] = []
    prev_fam: object = None
    while heap:
        neg_count, fam = heap[0]
        if fam == prev_fam:
            # Top equals the previous family. Defer: pop
            # both top entries, take the second, push the
            # first back. If the heap has only one entry
            # here the feasibility check above has missed
            # the case, but the N==0 branch and the
            # worst_count > ceil_n2 branch already cover
            # it; an "only one family left and it equals
            # prev" would mean N=1 (already returned at
            # the top of the function), so the heap has at
            # least two entries when the top conflicts
            # with prev.
            if len(heap) < 2:
                # Defensive: the feasibility check would
                # have refused before this point. An
                # extra check here keeps the function
                # correct under future refactors.
                raise HTTPException(
                    422,
                    f"compose refused: family {fam!r} is the only family "
                    f"left and equals the previous one, no reordering spreads",
                )
            heapq.heappop(heap)
            neg_count2, fam2 = heapq.heappop(heap)
            out.append(buckets[fam2].pop())
            if buckets[fam2]:
                heapq.heappush(heap, (neg_count2, fam2))
            heapq.heappush(heap, (neg_count, fam))
            prev_fam = fam2
        else:
            heapq.heappop(heap)
            out.append(buckets[fam].pop())
            if buckets[fam]:
                heapq.heappush(heap, (neg_count, fam))
            prev_fam = fam

    return out + other_trios


class ComposeSessionIn(BaseModel):
    """A session-sized compose: same draw as 3.3, with the
    family-spread reordering on top. The shape mirrors
    `ComposeRunIn` so the frontend can build both from the
    same picker state, and the only new rule (the family
    spread) is the operator-visible difference.

    `count` is on the body, not the path: the run-level
    endpoint asks for a run, this one asks for a session,
    and the request shape is what carries the intent. A
    request with `count=1` and a request with `count=40`
    are the same kind of request, and the family spread
    applies to both (it is trivially satisfied at N=1).

    `mode` is the same `Literal["strict", "exploratory"]`
    `ComposeIn` and `ComposeRunIn` carry, and the same reasoning
    applies: a free string would be a door open by default, the
    Literal narrows it to the two modes the pool builders know
    how to build. 6.1 is the task that opened the second mode.
    """
    count: int = Field(..., ge=1)
    candidates: dict  # same shape as ComposeRunIn.candidates
    mode: Literal["strict", "exploratory"] = "strict"
    # Hand the wardrobe to a reference instead of writing it: the shot's line
    # is composed without the session's wardrobe. Off by default, because a
    # line that says nothing about clothing renders her undressed (measured
    # 2026-08-31: nude 3/3 with no reference attached). There is no twin
    # switch for the pose — an empty wording is already a catalogue component
    # (the `none` control arm), and the wardrobe is the one piece of the line
    # no slot can silence.
    mute_wardrobe: bool = False
    # Same switch as `ComposeIn.reference`, same reason it is its own flag.
    reference: bool = False
    # The per-photograph clauses no catalogue row carries: how the frame is
    # careless (`FRAMING_SLIPS`) and how the photograph was taken
    # (`TECHNIQUE_DEFECTS`). Dealt by the caller, one string per photograph, in
    # the order the shots are queued; a short list (or the empty default every
    # directed session sends) leaves the remaining lines without one.
    #
    # Dealt by the CALLER and not here because the lists and the spreader are the
    # written path's, in `frontend/src/kinds.js` — the same rows, the same
    # no-two-running rule. A second copy of either in Python is a second answer
    # to "what does candid deal", and this repo has one home per fact.
    #
    # They are part of the line, so they are part of what names her lowest part:
    # `an elbow or a knee runs out of the edge of the frame` puts her knees in
    # the line, and the crop law reads them out of the draw's context the same
    # way it reads the wardrobe (`backend/crop.py`).
    extras: list[str] = Field(default_factory=list)
    # The wardrobe each photograph is composed with, in the order the shots are
    # queued: the arc of a shoot that undresses. An index the list does not reach
    # falls back to the session's wardrobe, so the default (an empty list) is
    # every photograph in the session's clothes — what a composed run was before
    # this existed.
    #
    # The states are spread over the run by the caller
    # (`enhance.js:spread`), the same function that spreads them over written
    # takes: K states, N photographs, and the wardrobe holds still between two
    # photographs of one stage. A stage is a state of the whole photograph, and
    # the catalogue's acts carry no stage of their own yet — so the arc dresses
    # whatever body the draw dealt, and a late state can land on an early act.
    # That is the known ceiling of a composed arc, not a bug in this field.
    wardrobes: list[str] = Field(default_factory=list)
    # Whether a second person is in the room for this run. Off by default, and
    # off means an act that needs him is not drawable: composed session 330 dealt
    # `She is astride him ... two people in frame` to a photograph whose wardrobe
    # state was a vest and knickers, three times in nine, because the act pool
    # holds every act of the manner and the draw had no way to know which stage
    # of the arc it was filling.
    #
    # On means "he is here", not "only him": the pool is everything again. An
    # operator who wants nothing but the explicit acts already has a way to say
    # so — `candidates` is theirs to narrow, and a run that sends three acts
    # draws from three acts.
    with_him: bool = False
    # Whether she is undressed for this run. Off by default, and off means an act
    # that needs her bare is not drawable — the explicit solo acts (a hand between
    # her legs, a toy, holding herself open) are photographs of a body with
    # nothing on it, and dealing one to a stage still in a sweatshirt is the same
    # contradiction `with_him` was added to stop.
    #
    # Independent of `with_him`: the acts that need him say so in their own
    # wording and do not need this flag as well.
    bare: bool = False


@app.post("/api/sessions/{sid}/compose-session")
def compose_session_endpoint(sid: int, c: ComposeSessionIn):
    """Compose a whole session with the same draw as 3.3 plus
    the family-spread ordering constraint 3.5 adds. Sibling of
    `compose_run_endpoint`, not a replacement: 3.3 is "give
    me N from this pool" and 3.5 is "give me a session of N
    that spreads the slot's family across consecutive
    photographs".

    The draw is the shared helper — same verified-trio pool,
    same multi-shuffle greedy, same 3.4 dedup. The new step
    is the reorder: a 422 if no reordering exists, the
    reordered list otherwise. The 422 message names the
    family, its count, the ceil bound, and the conclusion
    the operator needs to act.

    The check is BEFORE any INSERT (db.run auto-commits;
    a check that fires at k+1 would leave k rows — the same
    loop-closed property 3.3 and 3.4 pin on their 422s).
    With the pre-check in place, a refusal queues nothing,
    and the assertion `n_shots == 0` after a 422 is the
    proof.

    The reorder is a property of the LIST of trios, not the
    SET, and the 3.4 dedup is a property of the SET, not
    the LIST. The two checks commute: dedup runs first
    (its 422 names the duplicated tuple or line), the
    spread runs second (its 422 names the unsplittable
    family). Both run BEFORE any INSERT, the caller sees
    whichever fires first.
    """
    # The spread is handed to the draw as its accept predicate,
    # The family spread is part of the draw itself, not
    # applied to the draw's result: a shuffle whose chosen
    # set has no valid ordering is not a winner. Without
    # this, the same pool and count returned 200 or 422
    # depending on which trios the shuffle happened to take —
    # 3.3's rule, "the check and the draw are the same
    # calculation", with the family as the check. The
    # per-trio skip is `_skip_for_spread`; the bound it
    # reads off `count` is the same `ceil(N/2)` the reorder
    # uses, so the chosen set is always re-orderable and the
    # `_reorder_to_spread_families` feasibility branch is
    # defensive (it is unreachable on the 3.5 path with the
    # skip in place, but the function keeps the assertion
    # for a future caller that drops the skip).
    by_key, best_chosen = _draw_n_trio_shots(
        sid, c.count, c.candidates, mode=c.mode, skip=_skip_for_spread,
        mute_wardrobe=c.mute_wardrobe, extras=c.extras, wardrobes=c.wardrobes,
        with_him=c.with_him, bare=c.bare)
    ordered = _reorder_to_spread_families(best_chosen, by_key)
    shot_ids: list[int] = []
    # The extra is dealt to the PHOTOGRAPH, so it follows the queue order and not
    # the draw order: the reorder above spreads the camera families across
    # consecutive photographs, and the slip and the defect are spread across
    # consecutive photographs too. Zipping them before the reorder would hand
    # photograph 1 the extra dealt to whatever trio the draw happened to put
    # first.
    for at, (cam_key, act_key, framing_key) in enumerate(ordered):
        shot_ids.append(compose_and_queue_shot(
            sid, by_key["camera"][cam_key], by_key["act"][act_key], by_key["framing"][framing_key],
            c.mute_wardrobe, c.reference, _extra_at(c.extras, at),
            _wardrobe_at(c.wardrobes, at),
        ))
    return {"ids": shot_ids, "count": len(shot_ids)}


def _expand_shots(sid: int, model: dict, look: str, wardrobe: str, shots: list[ShotIn],
                  seed_mode: str, seed: int) -> int:
    """One take x N variations = N pending shot rows."""
    ref_kind = (db.one("SELECT w.kind AS kind FROM session s "
                       "LEFT JOIN workflow w ON w.id = s.reference_workflow_id "
                       "WHERE s.id=?", sid) or {})["kind"] or ""
    start = db.one("SELECT COALESCE(MAX(shot_index), -1) AS m FROM shot WHERE session_id=?", sid)["m"] + 1
    added = 0
    for offset, take in enumerate(shots):
        # A take that EDITS a reference is not composed, and that is the whole
        # point: the anchor photo already carries the trigger, the base prompt
        # and the look, so the take is an instruction ("remove the jacket").
        # Prepending the look again would restate the very garment the
        # instruction removes, and a positive that both describes and denies a
        # jacket keeps the jacket.
        #
        # A take that is GUIDED by a reference is the opposite case and has to
        # be composed like any other: it paints from noise, so the trigger, the
        # base prompt and the look are the only things that put her in the room
        # at all. Sent bare it renders the reference photograph's own room —
        # measured on session 324, which came back in the source's monochrome
        # studio instead of the session's bedroom. The test is on the graph's
        # kind for the same reason the runner's model-slot drop is.
        raw = take.verbatim or (take.reference and ref_kind != "guide")
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
    # The session's origin is stamped by the write path, every
    # time: a written shot is one data point for `'written'`,
    # and the column moves to `'mixed'` if a future compose
    # lands on the same session. The helper is only called
    # when at least one shot was actually added — `create_session`
    # calls `_expand_shots` with `shots=[]` to keep the create
    # path single, and a no-op expand must NOT stamp `'written'`
    # on a draft (a draft that has no shots yet reads as `''`,
    # the column default). The same logic gates
    # `import_photo`'s call, but `import_photo` always adds
    # one row, so the gate is a no-op there.
    if added > 0:
        _update_session_origin(sid, "written")
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


def _extra_at(extras: list[str], at: int) -> str:
    """The clause dealt to photograph `at`, or nothing.

    A caller that deals none (every directed session) sends an empty list, and a
    caller that deals fewer than it queues leaves the rest bare rather than
    wrapping round: a run whose last three photographs repeat the first three
    slips is a run whose spread was undone by the padding.
    """
    return extras[at] if 0 <= at < len(extras) else ""


def _wardrobe_at(wardrobes: list[str], at: int) -> str | None:
    """The wardrobe dealt to photograph `at`, or None for the session's.

    Not `_extra_at`: there, a missing clause and an empty clause are the same
    photograph, and here they are not. `None` means nobody dealt this row a
    wardrobe and the session's own is written; `""` means the arc reached a state
    with nothing written about clothing, which renders her undressed.
    """
    return wardrobes[at] if 0 <= at < len(wardrobes) else None


def _slot_concept_wording_text(slot_dict: dict) -> tuple[str, str, str]:
    concept = slot_dict.get("concept_key") or slot_dict.get("key", "")
    if "wordings" in slot_dict and slot_dict["wordings"]:
        wording_key = slot_dict["wordings"][0].get("key", slot_dict["wordings"][0].get("text", ""))
        text = slot_dict["wordings"][0].get("text", wording_key)
    else:
        wording_key = slot_dict.get("wording") or slot_dict.get("wording_key") or slot_dict.get("key", "")
        text = slot_dict.get("wording") or slot_dict.get("text") or wording_key
    return concept, wording_key, text


def compose_shot(model: dict, look: str, wardrobe: str,
                 camera: dict, act: dict, framing: dict,
                 mute_wardrobe: bool = False, extra: str = "") -> str:
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
    _, _, cam_text = _slot_concept_wording_text(camera)
    _, _, act_text = _slot_concept_wording_text(act)
    _, _, fr_text = _slot_concept_wording_text(framing)
    # `extra` is the part of the take no catalogue row carries: how the frame is
    # careless and how the photograph was taken, dealt by the caller from the
    # same lists and the same spreader the written path uses
    # (`frontend/src/compose.js:extrasFor`). It is empty on every directed shot
    # and on every fill-cell row, and `_sentences` drops an empty piece — so a
    # composed line without it is byte-for-byte what it always was, which is what
    # `test_a_composed_shot_joins_identically_to_a_written_one` still pins.
    #
    # It is joined LAST because both clauses it carries are trailing ones in a
    # written line too: the slip sits behind the framing inside the `camera`
    # field, and `technique` is the second-to-last of the seven keys.
    take = _sentences(cam_text, act_text, fr_text, extra)
    # A reference only delivers what the line does not already write, so a take
    # that hands the wardrobe to a reference has to stop saying it. Dropping it
    # here rather than at queue time keeps the stored line honest — it is what
    # was actually sent — and lets the dedup check below compare the real lines.
    return _compose(model, look, "" if mute_wardrobe else wardrobe, take)


def compose_and_queue_shot(sid: int, camera: dict, act: dict, framing: dict,
                           mute_wardrobe: bool = False,
                           reference: bool = False, extra: str = "",
                           wardrobe: str | None = None) -> int:
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
    # The session's wardrobe is the default and not the law: a shoot that walks
    # somewhere is the same clothes coming off in stages, and the stage is a
    # property of the PHOTOGRAPH. `None` is "the session's", which is what every
    # caller before the arc existed passes; a dealt empty string is a decision
    # (nothing written about clothing) and not a missing value, which is why the
    # parameter is `str | None` and not `str`.
    worn = session["wardrobe"] if wardrobe is None else wardrobe
    prompt = compose_shot(model, look, worn, camera, act, framing, mute_wardrobe, extra)
    # The (concept, wording) pair per slot, not just the wording.
    # Today every concept has a single wording and the two keys
    # coincide, so the cell the photograph counts toward is keyed
    # by the trio (camera, act, framing) directly. The slot is
    # the JSON key, not a value, because the cell is the trio and
    # the slot is the part of the trio this entry names.
    cam_c, cam_w, _ = _slot_concept_wording_text(camera)
    act_c, act_w, _ = _slot_concept_wording_text(act)
    fr_c, fr_w, _ = _slot_concept_wording_text(framing)
    components = json.dumps({
        "camera":  {"concept": cam_c,  "wording": cam_w},
        "act":     {"concept": act_c,     "wording": act_w},
        "framing": {"concept": fr_c, "wording": fr_w},
    })
    shot_index = db.one("SELECT COALESCE(MAX(shot_index), -1) AS m FROM shot WHERE session_id=?", sid)["m"] + 1
    shot_id = db.run(
        """INSERT INTO shot (session_id, shot_index, shot_label, prompt, negative,
                              components, mute_wardrobe, use_reference, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        sid, shot_index, f"composed {shot_index + 1}", prompt,
        model["base_negative"], components, int(mute_wardrobe), int(reference), db.now(),
    )
    # The session's origin moves to `'composed'` on the first
    # compose, or to `'mixed'` if the session already has a
    # written shot (3.4's spec scenario). The helper is the
    # single place this state machine lives, so the rule
    # cannot drift between the three write paths.
    _update_session_origin(sid, "composed")
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


def _update_session_origin(sid: int, kind: str) -> None:
    """Stamp `session.origin` for the path that just wrote a shot.

    The session's origin has three values: `''` (draft, no shots
    yet — the column default), `'written'`, `'composed'`, and
    `'mixed'`. Every shot insertion calls this helper with the
    path it came from (`'written'` for `_expand_shots` and
    `import_photo`, `'composed'` for `compose_and_queue_shot`).
    The state machine:

    - `''` -> `kind` (a draft that just took its first shot).
    - `kind` stays put (the path is the only one so far).
    - The OTHER kind -> `'mixed'` (3.4's spec scenario: a
      session that carries both kinds of rows exists, and the
      column has to reflect that without losing the per-row
      information).
    - `'mixed'` stays put (a `'mixed'` session does not
      regress to a single kind just because the next insertion
      matches the first).

    The single statement reads the current value, applies the
    state machine, and writes the new one. `db.run` commits per
    call, and the next shot's call re-reads what the previous
    call wrote, so the loop is safe under concurrent
    insertions in a future that needs it. The read-modify-
    write is in one `db.run` rather than a Python-side
    read-then-write to keep the column a single source of
    truth, even at the cost of one extra round trip.
    """
    assert kind in ("written", "composed"), kind
    db.run(
        "UPDATE session SET origin = CASE "
        "  WHEN origin = '' THEN ? "
        "  WHEN origin = ? THEN ? "
        "  WHEN origin = 'mixed' THEN 'mixed' "
        "  ELSE 'mixed' "
        "END "
        "WHERE id = ?",
        kind, kind, kind, sid,
    )


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
    # An imported photo is a written shot by definition (no
    # trio on the row), so the same helper `_expand_shots`
    # runs: the session's origin moves to `'written'` if it
    # was empty, stays put if it was already `'written'`, and
    # flips to `'mixed'` if a compose landed here earlier
    # (3.4's spec scenario).
    _update_session_origin(sid, "written")

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
    # A take that names its own reference photographs is answerable on its own,
    # so the session-level checks below are about the takes that do NOT — the
    # ones still following the 📎 pick. Split here rather than at each check,
    # so a session where every take brings its own reference is not refused for
    # having no session anchor.
    waiting = db.q("SELECT id, reference_shot_ids FROM shot WHERE session_id=? "
                   "AND status='pending' AND use_reference=1", sid)
    own = [json.loads(r["reference_shot_ids"] or "[]") for r in waiting]
    pending = sum(1 for picked in own if not picked)
    if not waiting:
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
    if not anchors and pending:
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
    # A take's own pick answers to the same count rule as the session's: a slot
    # left unfilled keeps whatever filename the graph was saved with, and that
    # photograph is then mixed into the take with nothing on screen saying so.
    for row, picked in zip(waiting, own):
        if picked and len(picked) != len(mapped):
            raise HTTPException(400, (
                f"Take {row['id']} names {len(picked)} reference photo(s) but workflow "
                f"'{wf['name']}' reads {len(mapped)}. Name {len(mapped)}, or clear the "
                f"take's own pick to follow the session."))
    if anchors and pending and len(anchors) != len(mapped):
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
    shot = db.one("SELECT status FROM shot WHERE id=?", shot_id)
    if not shot:
        raise HTTPException(404, "shot not found")
    if p.rating is not None:
        db.run("UPDATE shot SET rating=? WHERE id=?", max(0, min(5, p.rating)), shot_id)
    if p.rejected is not None:
        db.run("UPDATE shot SET rejected=? WHERE id=?", int(p.rejected), shot_id)
    if p.reference_shot_ids is not None:
        if len(p.reference_shot_ids) > len(REFERENCE_SLOTS):
            raise HTTPException(400, f"at most {len(REFERENCE_SLOTS)} reference photos")
        # Every id has to be a photograph that exists and has a file, checked
        # here rather than at run time: a typo caught now is a red field, and
        # caught in the runner it is a failed shot in the middle of a run.
        # Only while it is still waiting. `runner._reference_values` stamps this
        # column at queue time precisely so the row records what the take ACTUALLY
        # ran against; repainting it afterwards would break the before/after pairing
        # the column exists for, and the docstring on `ShotPatch` promises it does
        # not happen. It said so and nothing enforced it.
        if shot["status"] != "pending":
            raise HTTPException(
                409, f"shot {shot_id} is {shot['status']}, not pending — a take that has "
                     f"run keeps the reference it ran with")
        for ref in p.reference_shot_ids:
            row = db.one("SELECT filename FROM shot WHERE id=?", ref)
            if not row or not row["filename"]:
                raise HTTPException(400, f"shot {ref} has no photograph to guide with")
        db.run("UPDATE shot SET reference_shot_ids=? WHERE id=?",
               json.dumps(p.reference_shot_ids), shot_id)
    return db.one("SELECT * FROM shot WHERE id=?", shot_id)


@app.get("/api/sessions/{sid}/judge-pass")
def get_judge_pass(sid: int, slot: str):
    """Photographs in this session ready for a judging pass on one slot.

    Returns {"shots": [id, ...], "controls": [id, ...], "readings": [...]} for the session.
    - "shots": photographs that are done, un-rejected, composed from
      components (not '{}'), and have NO stored answer yet for this slot
      (including shots where verdicts='').
    - "controls": photographs meeting the same criteria that ALREADY have
      a stored answer for this slot.
    - "readings": the reading union for this slot and session.

    Each list carries ONLY integer shot IDs — no prompt, no components,
    no wording, no reference image, and no label. The screen must not
    show what the photograph was composed from, because an operator
    shown the expected answer will find it (spec.md:104-107).

    `shots` is the photographs whose line ASKED for this slot, plus a
    fifth as many drawn from the ones that asked nothing. The pass used
    to serve every photograph in the session for every slot, which on a
    bench that isolates one slot at a time meant 70 of 100 answers
    measured no cell at all — real work behind no measurement. The
    negatives that remain are there to keep the deck from having a single
    expected answer; they still measure nothing, and `judge_shot` counts
    them toward nothing.

    `readings` is the vocabulary the screen builds its forced choice
    from: the base readings for the slot and manner plus this session's
    own. It replaced a `families` list computed from the deck, which
    could only ever offer the outcomes something ASKED for — a
    photograph that came back frontal when the line asked for a side
    view had no answer but "none or cannot tell", and the measurement
    said the ask failed without ever saying what arrived instead.

    Before serving the deck, every component family photographed in it
    — across the unjudged shots AND the controls — must have a
    reading. When one does not, the pass is refused at 422 naming the
    families with none: the correct answer would not be on the list, so
    every photograph of that family would be recorded as a miss. The
    controls are in the check because a control re-presents a
    photograph shot before the vocabulary existed.

    A slot whose catalogue is EMPTY for the session's manner returns
    422: there is nothing to offer the judge. A slot holding one
    family is not refused — the screen offers one choice per
    reading plus "None or cannot tell", which is a yes/no question,
    and it is answerable because the floor is measured. The older
    rule refused fewer than two COMPONENTS, which was the right
    refusal while the screen offered one choice per wording.
    """
    if slot not in ("camera", "act", "framing"):
        raise HTTPException(
            422,
            f"invalid slot {slot!r}; expected 'camera', 'act' or 'framing'",
        )
    session = db.one("SELECT id, manner FROM session WHERE id=?", sid)
    if not session:
        raise HTTPException(404, "session not found")

    # Counted for THIS session's manner, which is the unit the forced choice is
    # drawn from: the store can hold six framings across three manners and
    # still offer nothing to the operator.
    #
    # The old rule refused a slot holding fewer than two components, on the
    # grounds that a forced choice over a list of one is not a question. It is
    # one, now that the screen offers one choice per READING and not per
    # wording: a single reading plus "None or cannot tell" is a yes/no
    # question, and it is answerable because the floor is measured (the empty
    # prompt renders frontal 10 of 10 on this checkpoint, so "did a side view
    # arrive?" has a real negative). Zero components is still not a question.
    n = db.one("SELECT COUNT(*) AS n FROM component "
               "WHERE slot=? AND manner=? AND retired_at IS NULL",
               slot, session["manner"])["n"]
    if n == 0:
        raise HTTPException(
            422,
            f"judge-pass refused: the {slot} catalogue is empty for manner "
            f"{session['manner']!r}; there is nothing to offer the judge",
        )

    readings = db.q(
        "SELECT * FROM reading WHERE slot=? AND manner=? AND (session_id IS NULL OR session_id=?) "
        "ORDER BY slot, manner, id",
        slot, session["manner"], sid,
    )
    reading_keys = {r["key"] for r in readings}

    rows = db.q(
        "SELECT id, components, verdicts FROM shot "
        "WHERE session_id=? AND status='done' AND (rejected=0 OR rejected IS NULL) "
        "ORDER BY shot_index, id",
        sid,
    )
    shots = []
    negatives = []
    controls = []
    photographed_families = set()

    for r in rows:
        if not r["components"] or r["components"] == "{}":
            continue
        comps = json.loads(r["components"])
        slot_comp = comps.get(slot) or {}
        drawn = slot_comp.get("concept") or slot_comp.get("wording", "none")
        v = json.loads(r["verdicts"]) if r["verdicts"] else {}

        if drawn != "none":
            fam_row = db.one(
                "SELECT family FROM component WHERE concept_key=? AND manner=?",
                drawn, session["manner"],
            )
            family = (fam_row["family"] if fam_row else "") or drawn
            if family:
                photographed_families.add(family)

        if v.get(slot) is not None:
            controls.append(r["id"])
        elif drawn == "none":
            # The line asked nothing of this slot, so the photograph measures
            # no cell. A few of them ride along as negatives (below); the rest
            # are work with no measurement behind it.
            negatives.append(r["id"])
        else:
            shots.append(r["id"])

    missing = [f for f in sorted(photographed_families) if f not in reading_keys]
    if missing:
        raise HTTPException(
            422,
            f"judge-pass refused: no reading for family/families {', '.join(missing)} "
            f"in {slot} catalogue for manner {session['manner']!r}; add readings before judging",
        )

    # A deck of nothing but photographs that DID ask for the slot has one
    # expected answer, and an operator who notices that answers on autopilot.
    # A fifth of the deck is drawn from the photographs that asked nothing:
    # their correct answer is "none or cannot tell", and getting one wrong is
    # the signal that the pass stopped being a measurement. The sample is a
    # stride over the ids rather than a random draw so an interrupted pass
    # resumes with the same deck.
    wanted = -(-len(shots) // 5)
    if wanted and negatives:
        stride = max(1, len(negatives) // wanted)
        picked = negatives[::stride][:wanted]
        # Scattered through the deck, not appended and not evenly spaced: a
        # run at the end is a pattern and so is every fifth photograph, and a
        # pattern is the answer. Seeded on the session and the slot so an
        # interrupted pass resumes with the deck it started with.
        rand = random.Random(f"{sid}:{slot}")
        for shot_id in picked:
            shots.insert(rand.randrange(len(shots) + 1), shot_id)

    return {"shots": shots, "controls": controls, "readings": readings}


@app.post("/api/shots/{shot_id}/judge")
def judge_shot(shot_id: int, j: JudgeShotIn):
    """Record the judging screen's answer against the shot's trio and
    update the cell's (judged, arrived) counts.

    The cell is the unit of evidence (design.md decision C): a
    (camera_wording, act_wording, framing_wording, manner, checkpoint)
    row holding the counts that 2.2 turns into a verdict. The shot
    carries the trio in ``components`` (3.1 wrote it) and the
    session carries manner and checkpoint (3.2 read them at create),
    so the row the increment lands on is fixed by the data: the
    drawn trio plus the session's two non-trio dimensions.

    The answer is a READING key, not a catalogue key. A reading's key
    is a component family, so a hit is still "the family the line
    asked for is the family in the frame" — the change is the set of
    answers offered, not the meaning of a hit. Answers recorded
    before readings existed are component keys; they still reduce to
    their family and still score the same way.

    The per-slot delta is what the judge's answer implies:

    - A non-``None`` slot the line ASKED for (its drawn wording is
      not ``none``) increments ``judged`` by 1, once per photograph.
      The question was asked and an answer was given — the slot was
      measured against the photograph. A slot drawn as ``none``
      asked for nothing, so answering it measures nothing and
      counts nothing.
    - An answer whose family is the drawn component's family also
      increments ``arrived`` by 1. The reading the judge picked is
      the act/camera/framing the line asked for, which is what
      ``arrived`` means.
    - An empty string ``""`` is "none or cannot tell" — the spec
      scenario `The judge cannot tell`. It counts as judged (the
      question was answered) but not arrived.
    - A reading of another family is the same as ``""``: judged+1,
      arrived unchanged. The shot was measured, the slot did not
      arrive — and the reading is preserved in ``shot.verdicts``, so
      the row says what DID arrive and not merely that the ask
      failed (the spec scenario `A wrong answer is kept`).

    The cell is keyed on the three WORDING keys, not on the
    concept keys and not on the reading. ``components`` carries
    both wording and concept per slot, and a
    future "let me add a second wording" lands here as a
    different ``wording`` value while ``concept`` stays put. The
    test that pins this distinction plants a shot whose
    ``concept != wording`` and checks the increment lands on the
    wording key.

    Idempotence. The ``verdicts`` column is the marker: a non-empty
    value means a judge already answered, the second call returns
    409 and the cell counts are unchanged. The cell's CHECK
    (``arrived BETWEEN 0 AND judged``) is the upstream safety net:
    a code change that drops the column check and tries to
    double-count would surface as ``IntegrityError`` on the UPSERT
    rather than as a wrong number. Two failures, not one.

    What is NOT in the endpoint:

    - A written shot has no trio (``components='{}'``) and no cell
      to count toward. Refused with 422 rather than silently
      counted as judged.
    - A session that has no manner or no checkpoint cannot match
      any cell. Refused with 422 naming what is missing, the
      same pre-check 3.2 / 3.3 already run.
    - The cell state is derived from the new counts via
      ``db.cell_state`` (the only definition of
      verified/dead/unknown). The response carries the new state
      so the operator sees the flip when the threshold is reached.

    5.2 (the judging screen) is what builds the payload: one
    answer per shot, the slot the pass asked plus the others at
    ``None``. The endpoint accepts every shape 5.2 can build
    because the per-slot delta is the only thing the cell update
    reads.
    """
    shot = db.one("SELECT * FROM shot WHERE id=?", shot_id)
    if not shot:
        raise HTTPException(404, "shot not found")
    if shot["components"] == "{}" or not shot["components"]:
        # A written shot has no trio, and the cell is keyed on the
        # trio. "Let me count the rating instead" would conflate
        # photo quality with the act the line asked for — they
        # are different facts (design.md:296-308), and counting
        # rating is the silent-substitution trap 6.2 names.
        raise HTTPException(
            422,
            "judge refused: shot has no components (written shot), "
            "there is no cell to count this photo toward",
        )

    # The shot's stored verdicts are the idempotence marker. The
    # empty default '' means "not yet judged"; a non-empty value
    # means a judge already answered. A re-judge is refused at
    # 409, not silently double-counted. The column check runs
    # BEFORE the cell update so the cell counts are not even
    # read for a refused call (the UPSERT would still no-op on
    # the same cell, but a refusal that does no work is a
    # cleaner log line than a refusal that touches a row).
    # Answers already on the row, from earlier passes. 5.2 asks ONE
    # question per pass over a whole batch, so a photograph is
    # judged for its camera on one pass and for its act on another;
    # a per-SHOT marker would refuse the second pass and the act
    # would never be measured. The marker is therefore per slot.
    already = json.loads(shot["verdicts"]) if shot["verdicts"] else {}
    already = {slot: ans for slot, ans in already.items() if ans is not None}

    session = db.one("SELECT * FROM session WHERE id=?", shot["session_id"])
    if not session:
        raise HTTPException(404, "session not found")

    missing = [name for name, value in (("manner", session["manner"]),
                                        ("checkpoint", session["checkpoint"]))
               if not value]
    if missing:
        raise HTTPException(
            422,
            f"judge refused: session is missing {', '.join(missing)}; "
            f"set them on the session before judging",
        )

    # The trio is the wording keys per slot, not the concept keys.
    # A concept can have several wordings (1.1's reshape), and the
    # cell is on the wording. Reading `concept` here would land
    # the increment on a row that does not exist, and the SQLite
    # UPSERT would silently create one with the wrong key —
    # a "let me use concept to look up the cell" bug is exactly
    # the trap the test for wording-vs-concept is written to catch.
    comps = json.loads(shot["components"])
    try:
        cam_w = comps["camera"]["wording"]
        act_w = comps["act"]["wording"]
        framing_w = comps["framing"]["wording"]
    except (KeyError, TypeError):
        raise HTTPException(
            422,
            f"judge refused: shot components are not in the trio shape "
            f"({shot['components']!r}); the cell needs the three wording keys",
        )

    # The per-slot delta. The answers map to slots in the same
    # order the components do. A non-None answer is a counted
    # measurement; an answer whose family is the drawn family is
    # a "the slot arrived". An empty string is "none or cannot
    # tell" — counted but not arrived. A reading of another
    # family is the same — counted but not arrived, and the
    # reading is preserved on the row as what arrived instead.
    defect_slot = j.slot
    if j.defect:
        if j.defect != "contradiction":
            raise HTTPException(422, f"unsupported defect {j.defect!r}")
        if defect_slot:
            if defect_slot not in ("camera", "act", "framing"):
                raise HTTPException(422, f"invalid slot {defect_slot!r}")
            explicit_ans = getattr(j, defect_slot)
            if explicit_ans:
                raise HTTPException(422, "Cannot specify both a component choice and a defect")
            answers = {defect_slot: ""}
        else:
            slots_with_ans = {s: getattr(j, s) for s in ("camera", "act", "framing") if getattr(j, s) is not None}
            if any(v != "" for v in slots_with_ans.values()):
                raise HTTPException(422, "Cannot specify both a component choice and a defect")
            if not slots_with_ans:
                raise HTTPException(422, "judge refused: slot must be specified when reporting a defect")
            defect_slot = next(iter(slots_with_ans.keys()))
            answers = {defect_slot: ""}
    else:
        answers = {slot: ans for slot, ans in
                   (("camera", j.camera), ("act", j.act), ("framing", j.framing))
                   if ans is not None}
        if not answers and j.slot is not None:
            if j.slot not in ("camera", "act", "framing"):
                raise HTTPException(422, f"invalid slot {j.slot!r}")
            answers = {j.slot: ""}

    drawn = {"camera": cam_w, "act": act_w, "framing": framing_w}
    # The cell is keyed on the wording (above); a HIT is decided on the
    # concept's family. `drawn` and `drawn_concept` are therefore both kept:
    # the first says which row the counts land on, the second says what the
    # line actually asked the judge to see. A shot written before `concept`
    # was stored falls back to its wording, which reduces to the same family
    # for every component whose key is its family.
    drawn_concept = {
        slot: (comps.get(slot) or {}).get("concept") or (comps.get(slot) or {}).get("wording", "none")
        for slot in ("camera", "act", "framing")
    }

    if not answers:
        # No question was asked on this pass: every slot is
        # None. A pass that asks nothing measures nothing, and
        # a 200 with no cell update is a silent no-op. Refused
        # at 422 so the call that "did nothing" never goes
        # through, the same shape `reshoot` below already
        # pins on its 400.
        raise HTTPException(
            422,
            "judge refused: at least one slot must be answered (not None); "
            "an empty pass measures nothing",
        )

    # A reading's key IS a component family, so an answer and a drawn
    # component meet on the same ground once both are reduced. `_family_of`
    # is the lookup for a COMPONENT key; a reading key is not a component
    # key, so `_reduce` falls back to the value itself — which is the family,
    # by the rule that a reading is keyed on one. The fallback is also what
    # makes a verdict stored before readings existed still score: it is a
    # component key, and it reduces to its family.
    def _family_of(key: str, slot: str) -> str:
        # Scoped to the SLOT, and that is not defensive tidiness: the live
        # catalogue holds a camera whose concept_key is `close-up` (one of the
        # fifteen shot-size cameras) AND a framing whose family is `close-up`.
        # Unscoped, a framing answer of `close-up` reduced to the CAMERA's family
        # `close`, compared unequal to the framing's `close-up`, and a photograph
        # that arrived was recorded as a miss. Measured on session 319: seven
        # `close-up` and five `waist-up` answers all scored 0, including four that
        # were exact hits.
        if not key or key == "none":
            return ""
        row = db.one("SELECT family FROM component WHERE concept_key=? AND manner=? AND slot=?",
                     key, session["manner"], slot)
        return (row["family"] if row else "") or ""

    def _reduce(v: str, slot: str) -> str:
        return _family_of(v, slot) or v

    if j.control:
        # A control photograph is re-presented to check the judge's agreement
        # against stored verdicts (spec.md:132-144, task 5.3). It writes NOTHING
        # to the database (not shot.verdicts, not the cell table) and returns the
        # comparison against the stored verdict for the answered slot.
        # One slot per control call. A pass asks one question across a
        # batch (spec.md:118), so a control answering two slots is a
        # caller bug — and taking the first of the dict and dropping
        # the rest returns an agreement for one slot while a
        # disagreement on the other is silently lost. A measuring
        # instrument does not lose measurements quietly.
        if len(answers) > 1:
            raise HTTPException(
                422,
                f"judge refused: a control answers one slot per call, got "
                f"{', '.join(sorted(answers))}",
            )
        slot, answered_val = next(iter(answers.items()))
        if slot not in already or already[slot] is None:
            raise HTTPException(
                422,
                f"judge refused: control shot {shot_id} has no stored verdict for slot {slot!r}",
            )
        stored_val = already[slot]
        agreed = bool(stored_val) and _reduce(stored_val, slot) == _reduce(answered_val, slot)
        return {
            "control": True,
            "slot": slot,
            "agreed": agreed,
            "stored": stored_val,
            "answered": answered_val,
        }

    # A slot already answered is not answered again. 5.3 asks for
    # exactly this ("a disagreement does not overwrite the stored
    # verdict"): the re-presented photograph feeds the agreement
    # rate, it does not rewrite the row. A NEW slot on the same
    # photograph is not a re-judgement and passes through.
    repeats = sorted(set(answers) & set(already))
    if repeats:
        raise HTTPException(
            409,
            f"judge refused: shot {shot_id} already has an answer for "
            f"{', '.join(repeats)} (verdicts: {shot['verdicts']}); "
            f"a stored verdict is never overwritten",
        )

    # `judged` counts PHOTOGRAPHS, not answers. The spec says it in
    # three places — specs/component-matrix/spec.md:47 and :70, and
    # `db.cell_state` itself ("at least 10 photographs judged") —
    # and the seeded rows are photograph counts too (astride 9/12 is
    # 9 photographs of 12). Counting +1 per answered SLOT was the
    # first shape of this endpoint and it reached `judged=3` on one
    # photograph, so a cell flipped to `verified` on four of them.
    # The threshold exists because a measurement below n=10 does not
    # survive its own noise; letting three answers stand in for
    # three photographs would have retired it silently.
    # ...and a photograph is counted for a cell only once a pass has answered
    # a slot the line ASKED for. The bench isolates one slot and leaves the
    # rest at `none`: a camera pass over a framing photograph answers a slot
    # the trio requested nothing on, and counting it took the framing cells to
    # `judged=10, arrived=0` — `dead` — before framing had been judged at all.
    # A trio that asks for nothing (the floor) is never counted: there is no
    # request to verify, and its photographs are read by eye.
    asked_before = any(drawn[slot] != "none" for slot in already)
    asked_now = any(drawn[slot] != "none" for slot in answers)
    judged_delta = 1 if asked_now and not asked_before else 0
    contradicted_delta = 1 if j.defect else 0

    # `arrived` is a property of the photograph, so it is derived
    # from ALL the answers the row now carries, not from this pass
    # alone: the photograph arrived if every slot answered so far
    # is the one the line asked for. An empty string ("none or
    # cannot tell") and a reading of another family are both misses;
    # the reading is kept on the row for the operator to read as what
    # arrived instead. Because a later pass can turn a hit into a miss,
    # the delta is the difference between the two states, which is
    # -1, 0 or +1.
    def _hit(slot: str, ans: str) -> bool:
        # A hit is the FAMILY the line asked for, not the wording key. The
        # judge sees a photograph, and a concept's wordings are synonyms:
        # three labels for one geometry make the forced choice a 1-in-3
        # guess and `arrived` a measure of the guess, not of the line. The
        # screen offers one choice per reading for the same reason.
        # The cell is still keyed on the WORDING, so a wording that renders
        # and one that does not still separate — by the counts they land on.
        # Exact-key equality stays as the first branch: it is what an answer
        # recorded before readings existed falls back to, and it keeps every
        # verdict stored before this change scoring the same way.
        if not ans:
            return False
        slot_comp = comps.get(slot) or {}
        if ans == slot_comp.get("wording") or ans == slot_comp.get("concept"):
            return True
        return _reduce(ans, slot) == _reduce(drawn_concept[slot], slot)

    def _all_arrived(seen: dict) -> int:
        # A slot drawn as `none` asked for NOTHING, so it cannot fail. The
        # measuring bench isolates one slot at a time and leaves the other two
        # at `none` (a camera cell is `(side-level, none, none)`), which makes
        # "nothing was asked here" the common case, not the odd one. Counting
        # the judge's correct "none or cannot tell" on such a slot as a miss
        # is what a pass over one slot did to the cells another pass had
        # already filled: the act row stood at 10 of 10, the camera pass
        # answered `none` on a camera nobody asked for, and every act cell
        # dropped to 0. The `none` slots are skipped; a trio that asked for
        # nothing at all (the floor) has nothing to arrive and stays at 0.
        asked = {slot: ans for slot, ans in seen.items() if drawn[slot] != "none"}
        return int(bool(asked) and all(_hit(slot, ans) for slot, ans in asked.items()))

    arrived_delta = _all_arrived({**already, **answers}) - _all_arrived(already)

    # The cell UPSERT. The ON CONFLICT clause names the PRIMARY
    # KEY (the same five columns the spec keys on), and the
    # SET adds the per-slot delta to the existing counts. The
    # CHECK `arrived BETWEEN 0 AND judged` enforces the
    # invariant the function below reads, and a code change
    # that double-counts would surface here as IntegrityError
    # (the user names this in the task: "the cell table's
    # CHECK rejects `arrived > judged` at insert time"). The
    # delta is `arrived_delta <= judged_delta` by construction
    # — a slot that arrives is a slot that was judged, and
    # the loop above guarantees it.
    key = (cam_w, act_w, framing_w, session["manner"], session["checkpoint"])
    # The branch is on whether the ROW EXISTS, not on whether the photograph
    # carries earlier answers. They stopped being the same question once a
    # pass over a slot the line never asked for counts nothing: such a pass
    # leaves answers on the shot and no cell row behind, and "an earlier
    # answer means the row is there" would then UPDATE nothing and lose the
    # measurement the NEXT pass takes.
    row_exists = db.one(
        "SELECT 1 AS present FROM cell WHERE camera_wording=? AND act_wording=? "
        "AND framing_wording=? AND manner=? AND checkpoint=?", *key)
    if row_exists:
        # `arrived_delta` can be -1 here — which an UPSERT cannot
        # carry: SQLite validates the row the INSERT proposes
        # BEFORE the conflict is resolved, so `VALUES (..., 0, -1)`
        # trips `CHECK arrived BETWEEN 0 AND judged` even though the
        # row the UPDATE would produce is legal. A plain UPDATE is
        # both correct and the smaller statement.
        db.run(
            "UPDATE cell SET judged = judged + ?, arrived = arrived + ?, "
            "contradicted = contradicted + ? "
            "WHERE camera_wording=? AND act_wording=? AND framing_wording=? "
            "AND manner=? AND checkpoint=?",
            judged_delta, arrived_delta, contradicted_delta, *key,
        )
    elif judged_delta or arrived_delta or contradicted_delta:
        db.run(
            "INSERT INTO cell (camera_wording, act_wording, framing_wording, "
            "manner, checkpoint, judged, arrived, contradicted) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(camera_wording, act_wording, framing_wording, manner, checkpoint) "
            "DO UPDATE SET judged = judged + excluded.judged, "
            "arrived = arrived + excluded.arrived, "
            "contradicted = contradicted + excluded.contradicted",
            *key, judged_delta, arrived_delta, contradicted_delta,
        )

    # Record the verdicts on the row. Empty default '' is the
    # "not yet judged" marker the idempotence check reads; a
    # non-empty value here is a successful judgement. The JSON
    # is round-tripped through `json.dumps` so the operator
    # can read what was answered and 5.2 can resume an
    # interrupted pass.
    # MERGED with what earlier passes stored, never replaced: the row
    # accumulates one answer per slot across passes, and an
    # already-answered slot never reaches this line (the 409 above).
    merged_verdicts = {**already, **answers}
    if j.defect:
        merged_verdicts[f"{defect_slot}_defect"] = j.defect
    verdicts_json = json.dumps(merged_verdicts)
    db.run("UPDATE shot SET verdicts=? WHERE id=?", verdicts_json, shot_id)

    # The cell's new state, derived from the new counts via
    # `db.cell_state`. The function is the only definition of
    # verified/dead/unknown — this endpoint never invents a
    # second rule. The response carries the new (judged,
    # arrived, state) so the caller sees the flip when the
    # n=10 threshold is crossed (the spec scenario
    # `An exploratory draw is recorded`).
    cell = db.one(
        "SELECT judged, arrived, contradicted FROM cell "
        "WHERE camera_wording=? AND act_wording=? AND framing_wording=? "
        "AND manner=? AND checkpoint=?",
        cam_w, act_w, framing_w, session["manner"], session["checkpoint"],
    )
    new_state = db.cell_state(cell["judged"], cell["arrived"]) if cell else "unknown"
    return {
        "cell": (cam_w, act_w, framing_w, session["manner"], session["checkpoint"]),
        "judged": cell["judged"] if cell else judged_delta,
        "arrived": cell["arrived"] if cell else arrived_delta,
        "contradicted": cell["contradicted"] if cell else contradicted_delta,
        "state": new_state,
    }


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
