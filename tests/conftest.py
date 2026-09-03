"""Shared fixtures. No test touches ComfyUI or the GPU: the client is replaced by
a double (`FakeComfy`) that writes the PNG ComfyUI would have written.

`IDEVGEN_DATA_DIR` is set BEFORE importing `main`, which resolves its paths at
import time — hence it lives up here and not inside a fixture.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TMP = Path(tempfile.mkdtemp(prefix="idevgen-tests-"))
os.environ["IDEVGEN_DATA_DIR"] = str(TMP / "data")
# Point the config at a throwaway file: the Setup route WRITES it, and a test
# run must never overwrite the developer's own config.json.
os.environ["IDEVGEN_CONFIG"] = str(TMP / "config.json")

import db  # noqa: E402
import main  # noqa: E402
from runner import Runner  # noqa: E402

# Minimal but realistic API graph: the positive conditioning goes through
# FluxGuidance, which is what breaks a naive mapping detection.
GRAPH = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"}},
    "2": {"class_type": "LoraLoader", "inputs": {
        "lora_name": "old.safetensors", "strength_model": 0.5, "strength_clip": 1.0,
        "model": ["1", 0], "clip": ["1", 1]}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello", "clip": ["2", 1]}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "ugly", "clip": ["2", 1]}},
    "9": {"class_type": "FluxGuidance", "inputs": {"guidance": 3.5, "conditioning": ["3", 0]}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    "6": {"class_type": "KSampler", "inputs": {
        "seed": 1, "steps": 20, "cfg": 8.0, "sampler_name": "euler", "scheduler": "normal",
        "denoise": 1.0, "model": ["2", 0], "positive": ["9", 0], "negative": ["4", 0],
        "latent_image": ["5", 0]}},
    "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": ["7", 0]}},
}

# A reference graph: it edits a photo instead of painting one, so there is no
# EmptyLatentImage and the size comes from the image. Shaped as plain Flux
# img2img, which is also what Kontext and Qwen-Image-Edit look like from here:
# the anchor arrives through a LoadImage either way. SaveImage keeps id "8" so
# `FakeComfy` reads the prefix from the same place in both graphs.
EDIT_GRAPH = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "edit.safetensors"}},
    "2": {"class_type": "LoadImage", "inputs": {"image": "example.png", "upload": "image"}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
    "5": {"class_type": "VAEEncode", "inputs": {"pixels": ["2", 0], "vae": ["1", 2]}},
    "6": {"class_type": "KSampler", "inputs": {
        "seed": 1, "steps": 20, "cfg": 8.0, "sampler_name": "euler", "scheduler": "normal",
        "denoise": 1.0, "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0],
        "latent_image": ["5", 0]}},
    "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": ["7", 0]}},
}


class FakeComfy:
    """ComfyUI double: records the queued graphs and creates the output file at
    the path `filename_prefix` dictates, exactly like the real SaveImage."""

    def __init__(self, output_dir: Path, *, fail_queue=False, fail_first=0,
                 no_images=False, write_file=True):
        self.output_dir = output_dir
        self.fail_queue = fail_queue
        self.fail_first = fail_first        # reject the first N prompts, then behave
        self.attempts = 0
        self.no_images = no_images
        self.write_file = write_file
        self.graphs: list[dict] = []
        self.uploads: list[tuple[str, str]] = []
        self.interrupted = 0

    async def upload_image(self, path, name: str) -> str:
        self.uploads.append((str(path), name))
        return f"idevgen/{name}"

    async def queue_prompt(self, graph: dict, client_id: str) -> str:
        self.attempts += 1
        if self.fail_queue or self.attempts <= self.fail_first:
            raise RuntimeError("Prompt outputs failed validation")
        self.graphs.append(graph)
        return f"pid-{len(self.graphs)}"

    async def history(self, prompt_id: str) -> dict:
        if self.no_images:
            # Shaped like a real ComfyUI failure: the useful sentence is buried in
            # a dict that also carries the traceback and the whole prompt.
            return {"status": {"completed": False, "messages": [["execution_error", {
                        "node_id": "9", "node_type": "KSampler",
                        "exception_type": "RuntimeError",
                        "exception_message": "Given normalized_shape=[2560]",
                        "traceback": ["  File \"execution.py\", line 545"],
                        "current_inputs": {"model": ["a very long dump"]}}]]},
                    "outputs": {}}
        prefix = self.graphs[-1]["8"]["inputs"]["filename_prefix"]
        subfolder, _, stem = prefix.rpartition("/")
        filename = f"{stem}_00001_.png"
        if self.write_file:
            folder = self.output_dir / subfolder
            folder.mkdir(parents=True, exist_ok=True)
            (folder / filename).write_bytes(b"\x89PNG fake")
        return {
            "status": {"completed": True},
            "outputs": {"8": {"images": [{"filename": filename, "subfolder": subfolder, "type": "output"}]}},
        }

    async def interrupt(self) -> None:
        self.interrupted += 1


@pytest.fixture
def client():
    """TestClient with the real lifespan (creates the database) and empty tables."""
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        yield c
    for table in ("shot", "session", "model", "workflow", "cell", "component", "reading",
                  "garment", "outfit"):
        db.run(f"DELETE FROM {table}")


@pytest.fixture
def comfy_output(tmp_path) -> Path:
    d = tmp_path / "comfy-output"
    d.mkdir()
    return d


@pytest.fixture
def make_runner(comfy_output, tmp_path):
    def _make(**kwargs) -> tuple[Runner, FakeComfy]:
        fake = FakeComfy(comfy_output, **kwargs)
        return Runner(fake, tmp_path / "sessions", comfy_output), fake
    return _make


@pytest.fixture
def seeded(client):
    """A ready workflow + model; returns their ids."""
    # The measured catalogue, from the file the app ships. NOT conditional on
    # the file existing: a suite that seeds when the file is there and quietly
    # does not when it is missing is two different suites, and the second one
    # is the one a fresh clone runs. Missing file is a hard error here.
    #
    # Plain INSERT, never `INSERT OR IGNORE`: the component table's CHECK is
    # what stops a wording from being its own judge label, and OR IGNORE turns
    # every one of those rejections into a silently absent row.
    seed_file = ROOT / "data" / "catalogue-seed.json"
    items = json.loads(seed_file.read_text(encoding="utf-8"))
    seen_readings = set()
    for item in items:
        db.run(
            """INSERT INTO component (concept_key, slot, manner, family, faces, wording, judge_label, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            item["concept_key"], item["slot"], item["manner"], item.get("family", ""),
            item.get("faces", ""), item["wording"], item["judge_label"], db.now(),
        )
        fam = item.get("family", "")
        if fam and (item["slot"], item["manner"], fam) not in seen_readings:
            seen_readings.add((item["slot"], item["manner"], fam))
            db.run(
                """INSERT INTO reading (slot, manner, session_id, key, label, created_at)
                   VALUES (?, ?, NULL, ?, ?, ?)""",
                item["slot"], item["manner"], fam, item.get("judge_label") or fam, db.now(),
            )
    wf = client.post("/api/workflows", json={"name": "wf", "graph": GRAPH}).json()
    model = client.post("/api/models", json={
        "name": "ada", "lora_name": "characters/ada.safetensors", "trigger": "4da woman",
        "base_positive": "photo, 35mm", "base_negative": "blurry", "workflow_id": wf["id"],
        "settings": {"width": 832, "height": 1216, "steps": 8, "cfg": 1.0},
    }).json()
    return {"workflow_id": wf["id"], "model_id": model["id"], "node_map": wf["node_map"]}
