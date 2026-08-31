"""Ask whether `Krea2PromptWeight` can take a thing out of the photograph.

The negative prompt is inert on this pipeline at CFG 1
([[idevgen-amateur-look-levers]]), which leaves no way to say "not that" — a
concept only ever gets added. This node claims per-token weighting through the
Qwen3-VL encoder, `(word:-1)` to remove and `(word:1.5)` to emphasize, and it
was sitting in ComfyUI unused the whole time.

The devil-horn headband is the test object: it is named in the line and it
renders in every photograph the bench has ever produced, so its absence is not
a judgement call.

Three arms, three shared seeds:

  A-plain     the ordinary graph                       horns expected 3/3
  B-node      the weight node, no weight syntax        isolates the node itself
  C-minus     the weight node, (devil-horn headband:-1) the question

B is what makes C readable: if the node alone already changes the photograph,
then C's result is the node and not the weight.

Usage: python scripts/shoot_prompt_weight.py [--seeds N] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from comfy import Comfy, apply_map, output_images  # noqa: E402
from shoot_depth_control import PROMPT, SEEDS, SETTINGS, baseline_graph  # noqa: E402

COMFY_URL = "http://127.0.0.1:8188"
OUT = ROOT / "data" / "weight-probe"
WEIGHT_BODY = json.loads((ROOT / "data" / "krea2-prompt-weight-workflow.json")
                         .read_text(encoding="utf-8"))

REMOVE = "(devil-horn headband:-1)"
ARMS = [
    ("A-plain", False, ""),
    ("B-node", True, ""),
    ("C-minus", True, REMOVE),
]


async def run(comfy: Comfy, client_id: str, label: str, weighted: bool,
              suffix: str, seed: int) -> Path | None:
    if weighted:
        graph, node_map = WEIGHT_BODY["graph"], WEIGHT_BODY["node_map"]
    else:
        graph, node_map = baseline_graph()
    text = f"{PROMPT}\n\n{suffix}" if suffix else PROMPT
    patched = apply_map(graph, node_map,
                        dict(SETTINGS, positive=text, seed=seed, filename_prefix=label))
    prompt_id = await comfy.queue_prompt(patched, client_id)
    for _ in range(600):
        history = await comfy.history(prompt_id)
        if history:
            break
        await asyncio.sleep(1)
    else:
        print(f"  {label}-{seed}: timed out")
        return None
    images = output_images(history)
    if not images:
        print(f"  {label}-{seed}: no image — {json.dumps(history.get('status', {}))[:300]}")
        return None
    img = images[0]
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.get(f"{COMFY_URL}/view", params={
            "filename": img["filename"], "subfolder": img.get("subfolder", ""),
            "type": img.get("type", "output")})
        r.raise_for_status()
    dest = OUT / f"{label}-s{seed}.png"
    dest.write_bytes(r.content)
    return dest


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=len(SEEDS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    seeds = SEEDS[:args.seeds]

    for label, weighted, suffix in ARMS:
        print(f"  {label:<10} {'weight node' if weighted else 'plain graph':<12} {suffix or '-'}")
    if args.dry_run:
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    comfy, client_id = Comfy(COMFY_URL), str(uuid.uuid4())
    made = 0
    for label, weighted, suffix in ARMS:
        for seed in seeds:
            dest = await run(comfy, client_id, label, weighted, suffix, seed)
            if dest:
                made += 1
                print(f"  {dest.name}")
    print(f"\n{made}/{len(ARMS) * len(seeds)} rendered into {OUT}")
    return 0 if made == len(ARMS) * len(seeds) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
