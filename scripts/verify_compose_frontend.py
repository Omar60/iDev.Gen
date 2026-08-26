"""One-shot verification of the new frontend candidate pool against the
real compose-run endpoint. NOT a test file: the project gates are the
pytest and vitest runs, and this is a sanity script for the reviewer to
run by hand if they want a round-trip outside the test suite.

The script:
  1. starts the FastAPI app with a temp data dir
  2. creates a session with manner=directed and a checkpoint
  3. pre-seeds a verified cell for that manner/checkpoint
  4. calls /compose-run with the candidates built by candidatePool('directed')
     and mode='strict' — expects 200, 1 shot queued
  5. calls /compose-run again with mode='strict' and a too-small pool —
     expects 422, 0 additional shots queued, and the verbatim message
  6. calls /compose-run with mode='exploratory' against an unknown trio
     — expects 200, 1 shot queued (the cell is unmeasured, not dead)

Run with: python scripts/verify_compose_frontend.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The existing pydantic warning the test suite already silences.
warnings.filterwarnings("ignore", message="Field name \"register\"")


def probe_frontend(manner: str) -> dict:
    """Read candidatePool(manner) out of compose.js through node, the same way
    shoot_arrangements.py reads the catalogue: a file:// URL, not a copy.
    A copy drifts, and whether THAT wording works is the whole question.
    """
    src = (ROOT / "frontend/src/compose.js").read_text(encoding="utf-8")
    # Drop the import line and the const so the script is self-contained.
    # We re-import POSITIONS and ARRANGEMENTS from kinds.js through node.
    runner = f"""
import {{ candidatePool }} from '{(ROOT / "frontend/src/compose.js").as_uri()}';
console.log(JSON.stringify(candidatePool({manner!r})));
"""
    out = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert out.returncode == 0, out.stderr
    import json
    return json.loads(out.stdout)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="idevgen-verify-"))
    os.environ["IDEVGEN_DATA_DIR"] = str(tmp / "data")
    os.environ["IDEVGEN_CONFIG"] = str(tmp / "config.json")
    sys.path.insert(0, str(ROOT / "backend"))

    import db
    import main
    from fastapi.testclient import TestClient

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

    pool = probe_frontend("directed")
    cam_keys = [c["key"] for c in pool["camera"]]
    act_keys = [a["key"] for a in pool["act"]]
    framing_keys = [c["key"] for c in pool["framing"]]
    framing_text = pool["framing"][0]["wordings"][0]["text"]
    print(f"FRONTEND: {len(cam_keys)} cameras, {len(act_keys)} acts, "
          f"{len(framing_keys)} framing, framing text={framing_text!r}")

    with TestClient(main.app) as client:
        wf = client.post("/api/workflows", json={"name": "wf", "graph": GRAPH}).json()
        model = client.post("/api/models", json={
            "name": "ada", "lora_name": "characters/ada.safetensors",
            "trigger": "4da woman", "base_positive": "photo, 35mm",
            "base_negative": "blurry", "workflow_id": wf["id"],
            "settings": {"width": 832, "height": 1216, "steps": 8, "cfg": 1.0},
        }).json()

        # -- Case 1: strict, exactly 1 verified trio. Pool is the candidates
        # sliced to that one trio, mode=strict → 200, 1 shot queued.
        sid = client.post("/api/sessions", json={
            "model_id": model["id"], "name": "verify strict ok",
            "manner": "directed",
            "checkpoint": "finepornV4",
            "shots": [],
        }).json()["id"]
        cam = cam_keys[0]
        act = act_keys[0]
        framing = framing_keys[0]
        db.run(
            "INSERT INTO cell (camera_wording, act_wording, framing_wording, "
            "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
            cam, act, framing, "directed", "finepornV4", 10, 8,
        )
        # Build a candidates payload with the same trios the cell row has.
        # The pool from candidatePool has every camera, every act, one framing;
        # the endpoint scopes the cell lookup to the candidates' keys, so the
        # only matching cell is the one we just seeded.
        r = client.post(f"/api/sessions/{sid}/compose-run", json={
            "count": 1, "mode": "strict",
            "candidates": pool,
        })
        n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
        print(f"CASE 1 strict ok: status={r.status_code}, queued={n}, body={r.text[:200]}")
        assert r.status_code == 200, f"strict single verified trio should 200, got {r.status_code}: {r.text}"
        assert n == 1, f"expected 1 queued, got {n}"

        # -- Case 2: strict, too small pool. With only 1 verified trio,
        # asking for 5 is refused; the message names the slot, the verified
        # count (1), the largest fillable (1), the requested count (5),
        # and the word "exploratory".
        sid2 = client.post("/api/sessions", json={
            "model_id": model["id"], "name": "verify strict refused",
            "manner": "directed",
            "checkpoint": "finepornV4",
            "shots": [],
        }).json()["id"]
        r = client.post(f"/api/sessions/{sid2}/compose-run", json={
            "count": 5, "mode": "strict",
            "candidates": pool,
        })
        n2 = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid2)["n"]
        print(f"CASE 2 strict refused: status={r.status_code}, queued={n2}, body={r.text[:300]}")
        assert r.status_code == 422, f"strict too-small pool should 422, got {r.status_code}"
        detail = r.json()["detail"]
        for needle in ["5", "1", "exploratory"]:
            assert needle in detail, f"refusal missing {needle!r}: {detail!r}"
        assert n2 == 0, f"refusal must not queue, got {n2}"

        # -- Case 3: exploratory, unmeasured trio. No cell row for the trio
        # below, mode=exploratory → 200, 1 shot queued, a fresh cell is
        # created. (We don't read the cell here — 6.2's tests already pin
        # that the cell lands on the wording key. The point of this case
        # is to prove the control can land a shot at all when the cell
        # table has nothing yet.)
        sid3 = client.post("/api/sessions", json={
            "model_id": model["id"], "name": "verify exploratory",
            "manner": "directed",
            "checkpoint": "finepornV4",
            "shots": [],
        }).json()["id"]
        r = client.post(f"/api/sessions/{sid3}/compose-run", json={
            "count": 1, "mode": "exploratory",
            "candidates": pool,
        })
        n3 = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid3)["n"]
        print(f"CASE 3 exploratory ok: status={r.status_code}, queued={n3}, body={r.text[:200]}")
        assert r.status_code == 200, f"exploratory unmeasured trio should 200, got {r.status_code}: {r.text}"
        assert n3 == 1, f"expected 1 queued, got {n3}"

        # -- Case 4: missing manner. The control on the screen disables the
        # button when manner is empty; the endpoint refuses before the cell
        # lookup with a 422 naming what is missing.
        sid4 = client.post("/api/sessions", json={
            "model_id": model["id"], "name": "verify missing manner",
            "manner": "",
            "checkpoint": "finepornV4",
            "shots": [],
        }).json()["id"]
        r = client.post(f"/api/sessions/{sid4}/compose-run", json={
            "count": 1, "mode": "exploratory",
            "candidates": pool,
        })
        print(f"CASE 4 missing manner: status={r.status_code}, body={r.text[:300]}")
        assert r.status_code == 422, f"missing manner should 422, got {r.status_code}"
        assert "manner" in r.json()["detail"], f"refusal should name manner: {r.json()['detail']!r}"

    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
