"""Ask the Qwen multiangle edit for the cameras Krea 2 cannot render.

`QwenMultiangleCameraNode` turned out to be a phrasebook, not a channel: it maps
an angle to three words out of a closed vocabulary and hands them to workflow 5,
which this project imported and has been driving with hand-written prompts.
Read out of `custom_nodes/comfyui-qwenmultiangle/nodes.py`, the whole vocabulary
is eight horizontal views, four verticals and three distances.

This is a second pass over a finished photograph rather than a way to write the
first one, so it does not compete with the line - it competes with
[[idevgen-depth-control-channel]], and it costs no reference photograph and no
body drift.

The arms are the geometries the base model cannot reach by wording:
`right side view` is the profile, and `front-right quarter view` is the family
[[idevgen-canned-five-cameras]] records as missing. `front view eye-level` is
the control: asking for the angle the input already has should return the input.

Usage: python scripts/shoot_multiangle.py [--image NAME] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from comfy import Comfy, apply_map, output_images  # noqa: E402

COMFY_URL = "http://127.0.0.1:8188"
SRC = ROOT / "data" / "depth-probe"
OUT = ROOT / "data" / "multiangle-probe"
WORKFLOW_ID = 5

# label -> the three words. The trigger is the LoRA's, not this project's.
ARMS = [
    ("A-control-front", "<sks> front view eye-level shot medium shot"),
    ("B-profile-right", "<sks> right side view eye-level shot medium shot"),
    ("C-quarter-front", "<sks> front-right quarter view eye-level shot medium shot"),
    ("D-back", "<sks> back view eye-level shot medium shot"),
    ("E-low", "<sks> front view low-angle shot medium shot"),
    ("F-high", "<sks> front view high-angle shot medium shot"),
]

SEED = 399966242


def workflow() -> tuple[dict, dict]:
    c = sqlite3.connect(ROOT / "data" / "idevgen.db")
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT graph, node_map FROM workflow WHERE id=?", (WORKFLOW_ID,)).fetchone()
    return json.loads(r["graph"]), json.loads(r["node_map"])


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="B-baseline-s399966242.png",
                    help="the finished photograph to re-angle, inside data/depth-probe")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    for label, prompt in ARMS:
        print(f"  {label:<18} {prompt}")
    if args.dry_run:
        return 0

    src = SRC / args.image
    if not src.exists():
        print(f"missing input photograph: {src}")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)

    graph, node_map = workflow()
    comfy, client_id = Comfy(COMFY_URL), str(uuid.uuid4())
    name = await comfy.upload_image(src, args.image)
    made = 0

    for label, prompt in ARMS:
        patched = apply_map(graph, node_map, {
            "positive": prompt, "reference": name, "seed": SEED, "filename_prefix": label})
        prompt_id = await comfy.queue_prompt(patched, client_id)
        for _ in range(600):
            history = await comfy.history(prompt_id)
            if history:
                break
            await asyncio.sleep(1)
        else:
            print(f"  {label}: timed out")
            continue
        images = output_images(history)
        if not images:
            print(f"  {label}: no image — {json.dumps(history.get('status', {}))[:300]}")
            continue
        img = images[0]
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.get(f"{COMFY_URL}/view", params={
                "filename": img["filename"], "subfolder": img.get("subfolder", ""),
                "type": img.get("type", "output")})
            r.raise_for_status()
        dest = OUT / f"{label}.png"
        dest.write_bytes(r.content)
        made += 1
        print(f"  {dest.name}")

    print(f"\n{made}/{len(ARMS)} rendered into {OUT}")
    return 0 if made == len(ARMS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
