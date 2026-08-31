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
from shoot_depth_control import (CAMERA_PROFILE, PROMPT, SEEDS, SETTINGS,
                                 baseline_graph, prompt_for)  # noqa: E402

CAMERA_TAIL = "Overhead camera directly above her and behind her head"

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
ENHANCER_BODY = load("krea2-enhancer-workflow.json")
ATTENTION_BODY = load("krea2-attention-workflow.json")
KGREF_BODY = load("krea2-kgreference-workflow.json")
KGWARD_BODY = load("krea2-kgwardrobe-workflow.json")

def pose_silent(line: str) -> str:
    """The line with the written posture struck out as well.

    The geometry arm left the camera unwritten but kept a Pose block naming
    both feet, the back, the shoulders, both arms and the head — the same
    written contradiction that beat the wardrobe card until it was removed.
    A reference asked for a body orientation has to be asked with the body
    unwritten.
    """
    out = wardrobe_silent(line)
    pose = out.index("Pose:")
    subject = out.index("Subject:")
    assert pose < subject
    return out[:pose] + "Pose:\nShe stands on the carpet.\n\n" + out[subject:]


def wardrobe_silent(line: str) -> str:
    """The line with every garment struck out, so nothing written contradicts
    the reference. The wardrobe is spelled twice — once under Subject and once
    under Outfit & Texture — and a version that strikes only one of them would
    still be arguing with the card.
    """
    subject = line.index("Subject:")
    outfit = line.index("Outfit & Texture:")
    expression = line.index("Expression:")
    assert subject < outfit < expression
    return (line[:subject]
            + "Subject:\nA young woman, her body and skin plainly visible.\n\n"
            + line[expression:])


# The profile clause session 231 measured as unreachable: nine wordings, five
# LoRA strengths, 0/24. If "prompt adherence" is a real thing this node does,
# this is the clause it has to move.


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
    "enhancer": [
        ("EN-A-plain", None, "", None),
        ("EN-off", ENHANCER_BODY, "", 0.0),
        ("EN-1", ENHANCER_BODY, "", 1.0),
        ("EN-2", ENHANCER_BODY, "", 2.0),
    ],
    # `Krea 2 attention` multiplies the text projector weight by `strength`,
    # so 1.0 is the identity and AT-1 is an exact inert arm rather than an
    # approximate one. 3.0 is the value its README claims balance at.
    "attention": [
        ("AT-A-plain", None, "", None),
        ("AT-1", ATTENTION_BODY, "", 1.0),
        ("AT-2", ATTENTION_BODY, "", 2.0),
        ("AT-3", ATTENTION_BODY, "", 3.0),
    ],
    # The tail question. `Overhead camera directly above her and behind her
    # head` is obeyed on the height 3/3 and ignored on the tail 0/3 — both
    # halves are reachable, so this separates a dead node from a dead clause
    # the way the profile cannot.
    "tail": [
        ("TL-A-plain", None, "", None),
        ("TL-2", ATTENTION_BODY, "", 2.0),
        ("TL-3", ATTENTION_BODY, "", 3.0),
    ],
    # The reference stack, asked the question EncodeRebalance failed (0/4) and
    # the depth control answers (3/3): the profile, from a photograph of it,
    # with no camera clause in the line. Two controls, because the pack can
    # rewrite the prompt as well as the conditioning — A is the plain graph
    # and B is the node with the card's own strength at zero, which subtracts
    # the whole image delta and so encodes the text alone.
    "kgref": [
        ("KG-A-plain", None, "", None),
        ("KG-B-zero", KGREF_BODY, "", 0.0),
        ("KG-C-02", KGREF_BODY, "", 0.2),
        ("KG-D-1", KGREF_BODY, "", 1.0),
        ("KG-E-3", KGREF_BODY, "", 3.0),
    ],
    # The same stack with the card's role swapped to the wardrobe. Geometry
    # was 0/12 here and 0/4 on EncodeRebalance; this asks whether a reference
    # through the text encoder carries ANYTHING, using a source whose grey
    # t-shirt and black jeans cannot be confused with black lace lingerie.
    "kgward": [
        ("KW-B-zero", KGWARD_BODY, "", 0.0),
        ("KW-D-1", KGWARD_BODY, "", 1.0),
        ("KW-E-3", KGWARD_BODY, "", 3.0),
    ],
    # The wardrobe question again with the line silent about clothing, the way
    # it is silent about the camera. Removes the one excuse the KW arms had:
    # there, the reference was contradicting a written garment.
    "kgsilent": [
        ("KS-B-zero", KGWARD_BODY, "", 0.0),
        ("KS-E-3", KGWARD_BODY, "", 3.0),
    ],
    # Geometry with the body unwritten, which is what the wardrobe result says
    # the earlier 0/12 was missing.
    "kgpose": [
        ("KP-B-zero", KGREF_BODY, "", 0.0),
        ("KP-E-3", KGREF_BODY, "", 3.0),
    ],
    # EncodeRebalance again, this time with the body unwritten. Its 0/4 was
    # measured against a line that spelled the posture out, which the KG stack
    # showed is enough to beat a reference at any strength.
    "rbpose": [
        ("RP-plain", None, "", None),
        ("RP-low", REBALANCE_BODY, "", "low"),
        ("RP-normal", REBALANCE_BODY, "", "normal"),
        ("RP-high", REBALANCE_BODY, "", "high"),
        ("RP-max", REBALANCE_BODY, "", "max"),
    ],
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
    if budget is not None:
        # Only graphs with a reference slot want an image; the enhancer set puts
        # a plain float on the same slot name.
        if "reference" in node_map:
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

    global PROMPT
    if args.set in ("enhancer", "attention"):
        PROMPT = prompt_for(CAMERA_PROFILE)
    if args.set == "tail":
        PROMPT = prompt_for(CAMERA_TAIL)
    if args.set == "kgsilent":
        PROMPT = wardrobe_silent(PROMPT)
    if args.set in ("kgpose", "rbpose"):
        PROMPT = pose_silent(PROMPT)

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
