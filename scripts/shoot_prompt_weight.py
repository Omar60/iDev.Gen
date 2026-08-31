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
SOURCES = ROOT / "data" / "depth-sources"


def load(name: str) -> dict:
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


WEIGHT_BODY = load("krea2-prompt-weight-workflow.json")
NEGPIP_BODY = load("krea2-negpip-workflow.json")
REBALANCE_BODY = load("krea2-rebalance-workflow.json")

# label -> (graph body or None for the plain graph, text suffix, image budget)
# Each set keeps its own inert-node arm: a node that changes the photograph on
# its own makes every other arm in the set unreadable.
SETS = {
    "weight": [
        ("A-plain", None, "", None),
        ("B-node", WEIGHT_BODY, "", None),
        ("C-minus", WEIGHT_BODY, "(devil-horn headband:-1)", None),
    ],
    "negpip": [
        ("NP-A-plain", None, "", None),
        ("NP-B-node", NEGPIP_BODY, "", None),
        ("NP-C-minus", NEGPIP_BODY, "(devil-horn headband:-1)", None),
    ],
    # The reference goes through the text encoder here, with a token budget
    # instead of a strength. The profile is the source because it is the one
    # geometry with a known answer: no wording reaches it, the depth control
    # does, and it costs the body to do so.
    "rebalance": [
        ("RB-A-plain", None, "", None),
        ("RB-low", REBALANCE_BODY, "", "low"),
        ("RB-normal", REBALANCE_BODY, "", "normal"),
        ("RB-high", REBALANCE_BODY, "", "high"),
        ("RB-max", REBALANCE_BODY, "", "max"),
    ],
}


async def run(comfy: Comfy, client_id: str, label: str, body: dict | None,
              suffix: str, budget: str | None, seed: int, source: str) -> Path | None:
    graph, node_map = (body["graph"], body["node_map"]) if body else baseline_graph()
    text = f"{PROMPT}\n\n{suffix}" if suffix else PROMPT
    values = dict(SETTINGS, positive=text, seed=seed, filename_prefix=label)
    if budget:
        values["reference"] = await comfy.upload_image(SOURCES / source, source)
        values["reference_strength"] = budget
    patched = apply_map(graph, node_map, values)
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
    ap.add_argument("--set", default="weight", choices=sorted(SETS))
    ap.add_argument("--source", default="profile_90.jpg")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    seeds = SEEDS[:args.seeds]
    arms = SETS[args.set]

    for label, body, suffix, budget in arms:
        print(f"  {label:<12} {(body or {}).get('name', 'plain graph'):<38} "
              f"{suffix or budget or '-'}")
    if args.dry_run:
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    comfy, client_id = Comfy(COMFY_URL), str(uuid.uuid4())
    made = 0
    for label, body, suffix, budget in arms:
        for seed in seeds:
            dest = await run(comfy, client_id, label, body, suffix, budget, seed, args.source)
            if dest:
                made += 1
                print(f"  {dest.name}")
    print(f"\n{made}/{len(arms) * len(seeds)} rendered into {OUT}")
    return 0 if made == len(arms) * len(seeds) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
