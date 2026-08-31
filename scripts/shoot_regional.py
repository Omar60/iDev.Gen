"""Ask whether `Krea2Regional` puts the second body in the frame.

[[idevgen-two-people-limit]] is the standing question: the man is missing from
most renders, and four wordings written to fix it all died on re-test. Every
one of them was a wording — the model was still free to compose one person.
This pack routes each region's image tokens to its own prompt in a single pass,
so his block gets a piece of the canvas whether the model wants to draw him or
not.

The line is `shoot_depth_control.TWO_PROMPT` verbatim, so the plain arm here is
the same photograph that batch shot and the numbers are comparable.

Three arms, three shared seeds:

  RG-A-plain   the ordinary graph, the whole line               his body: baseline
  RG-B-inert   the regional node, one full-canvas empty region  isolates the node
  RG-C-two     him in the upper region, her in the lower one    the question

B is what makes C readable: if the patched model alone changes the photograph,
C's result is the wrapper and not the routing. The arrangement is vertical
because the act is — he stands, she kneels at his hips — so the regions are a
top band and a bottom band, not left and right.

Usage: python scripts/shoot_regional.py [--seeds N] [--arm LABEL] [--dry-run]
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
from shoot_depth_control import (SEEDS, SETTINGS, TWO_PROMPT,  # noqa: E402
                                 baseline_graph)

COMFY_URL = "http://127.0.0.1:8188"
OUT = ROOT / "data" / "regional-probe"
SEEDS_B = [246813579, 555444333, 987123456]
# Six more, for the arm that has to survive a real n before anything is written
# down. Nothing above has been shot on these.
SEEDS_C = [135792468, 864209753, 202512345, 777000111, 448822660, 913571113]

# The mask canvas is arbitrary: the pack resizes every mask to the latent grid
# (`_latent_mask`), so these numbers only have to hold the proportions.
CANVAS_W, CANVAS_H = 512, 896
HIM_H, HER_H, HER_Y = 610, 420, 476   # bands overlap; exclusive_masks resolves it

# The same line, split where its own blocks already are. The base keeps the
# scene and the framing and says where each body goes — the README's
# `layout_in_base`, which is the lever that makes the model compose into the
# boxes instead of fighting them.
BASE = """zchar_jir.

Two people are in the frame, a young woman and a man, both of them whole in the shot.

Angle & Framing:
a full-length photograph, head to feet, taken from in front of them.

Layout:
In the upper part of the image, standing: the man. In the lower part of the image, kneeling at the height of his hips: the young woman."""

HIM = """He stands upright with his feet apart and his weight even on both of them, his legs and his bare chest and his head all inside the frame, his arms hanging at his sides."""

HER = """She kneels upright on the carpet in front of him and gives him a blowjob, her mouth on his cock, her knees together on the floor and her back straight. She wears the sheer black stockings on her thighs and nothing else, her hair loose and straightened down past her shoulders. Her eyes are closed and her face is calm."""

# RG-D fixes the two things the C renders showed. The act moves OUT of her
# region and into the shared base: naming his cock inside her band is what made
# the model draw one there and invent a second man to own it, 3/3. And the
# camera stops being impossible — he faces the lens, she faces him, so from in
# front of them she cannot reach him without turning frontal, which is the
# contradiction [[idevgen-position-collapse-is-contradiction]] says collapses.
# From behind her, both bodies keep the pose the line gives them.
BASE_D = """zchar_jir.

Two people are in the frame, a young woman and a man, both of them whole in the shot.

Act:
She kneels on the carpet in front of him and gives him a blowjob, her mouth on his cock, her face at the height of his hips.

Angle & Framing:
a full-length photograph, head to feet, taken from behind her over her shoulder, her back to the camera.

Layout:
In the upper part of the image, standing and facing the camera: the man. In the lower part of the image, kneeling with her back to the camera: the young woman."""

HER_D = """She kneels upright on the carpet, her knees together on the floor and her back straight, her shoulders and the back of her head toward the camera. She wears the sheer black stockings on her thighs and nothing else, her hair loose and straightened down past her shoulders."""

# RG-E keeps everything D fixed and moves the camera to the side. From behind
# her the mouth is occluded by her own head, so no judge can ever count the
# act; side-on it is the one camera where both the geometry is possible and
# the contact is visible. The known risk is [[idevgen-profile-is-a-base-model-limit]]:
# Krea 2 has no ninety-degree profile, so this may come back three-quarter.
# That is a base-model limit, not a regional one, and it is the same either way
# for both bodies.
BASE_E = BASE_D.replace(
    "taken from behind her over her shoulder, her back to the camera",
    "taken from their left side, the two of them side-on to the camera").replace(
    "In the upper part of the image, standing and facing the camera: the man. "
    "In the lower part of the image, kneeling with her back to the camera: the young woman.",
    "In the upper part of the image, standing side-on: the man. "
    "In the lower part of the image, kneeling side-on at the height of his hips: the young woman.")

HER_E = """She kneels upright on the carpet seen from her left side, her knees together on the floor and her back straight, her face turned toward him. She wears the sheer black stockings on her thighs and nothing else, her hair loose and straightened down past her shoulders."""


# ---------------------------------------------------------------- the dead pose
#
# The blowjob above is a pose this sampler already knows — the plain graph shot
# it 3/3, which is exactly why the pack could not show a gain on it. The
# question the pack was installed for is an arrangement the words CANNOT reach:
# `under` is 0/12 on finepornV4 and 0/12 on the Krea mix, collapsing every time
# into the sampler's dominant mode for two bodies, her upright astride facing
# the lens ([[idevgen-anchor-beats-collapse]]).
#
# There is already a wording that beats it on finepornV4 — the bed-edge anchor,
# a third object whose geometry the collapse cannot satisfy, 3/3 there and
# never measured on this base. So this is a 2x2: anchor or no anchor, regions
# or no regions. If the regional arm lands where the plain one collapses, the
# pack reaches a pose words do not. If only the anchor arms land, the anchor is
# the whole story again and the pack is dead twice.
#
# The act text of both arms is `CANDIDATES` in `shoot_arrangements.py`, verbatim.
UNDER_PLAIN = ("She is on her back with her legs open and he is over her between "
               "them, the two of them joined, two people in frame.")
UNDER_EDGE = ("She is lying on her back across the edge of the bed with her knees "
              "up against his chest, he is standing on the floor at the edge of "
              "the bed between her legs, the two of them joined, two people in "
              "frame.")

# The camera is the side, the one E and F proved this base can hold, and the one
# [[idevgen-anchor-beats-collapse]] says is the only family that reads a
# horizontal body against a vertical one.
UNDER_BASE = """zchar_jir.

Two people are in the frame, a young woman and a man, both of them whole in the shot.

Act:
{act}

Angle & Framing:
a full-length photograph, taken from their left side, the two of them side-on to the camera.

Layout:
In the upper part of the image: the man, above her. In the lower part of the image: the young woman, her body horizontal."""

# The regional split for this pose. Neither block names the other body's parts —
# that is what fabricated a third man in RG-C.
UNDER_HIM = ("He is above her with his weight on his arms, his chest over her and "
             "his knees on either side of her hips.")
UNDER_HER = ("She lies on her back with her shoulders flat and her legs open, her "
             "body horizontal across the frame, the sheer black stockings on her "
             "thighs and nothing else.")

# ---- the detailed pair, C2/D2 -------------------------------------------------
#
# The first 2x2 left every orientation implicit and the two arms drifted apart on
# one seed set and together on the next. So each body now gets its posture, what
# it is resting on, and which way it faces, in that order, and the two arms share
# the text word for word — the routing is the only difference left.
#
# The band the routing gives each body is written into that body's own sentence:
# he is the upper band and is described from the floor up, she is the lower band
# and is described lying across it. When the words and the bands disagree the
# model sews the impossible together rather than refusing it — that is what
# U-B rendered, torsos doubled and a cock at her face.
UNDER_EDGE_2 = (
    "He is standing on the floor at the side of the bed, upright on both feet, "
    "his hips against the edge of the mattress and his body vertical, facing her "
    "and facing across the frame. She is lying on her back across the edge of the "
    "bed with her back flat on the mattress and her body horizontal, her hips at "
    "the very edge in front of him and her knees drawn up against his chest, her "
    "head away from him deeper on the bed. The two of them are joined, two people "
    "in frame.")

UNDER_HIM_2 = ("He stands on the floor at the edge of the bed, upright on both "
               "feet, his legs and his hips and his chest and his head stacked "
               "vertically one above the other, turned to face across the frame.")

UNDER_HER_2 = ("She lies on her back across the edge of the mattress, her body "
               "horizontal from one side of the frame to the other, her shoulders "
               "flat, her knees up and her hips at the edge of the bed, the sheer "
               "black stockings on her thighs and nothing else.")


def _encode(graph: dict, nid: str, text: str) -> None:
    graph[nid] = {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": ["822", 1], "text": text}}


def _band(graph: dict, nid: str, h: int, y: int) -> str:
    """A full-width band `h` tall at `y`, as a mask node id."""
    graph[nid] = {"class_type": "SolidMask",
                  "inputs": {"value": 1.0, "width": CANVAS_W, "height": h}}
    graph[nid + "c"] = {"class_type": "MaskComposite",
                        "inputs": {"destination": ["920", 0], "source": [nid, 0],
                                   "x": 0, "y": y, "operation": "add"}}
    return nid + "c"


def regional_graph(regions: list[tuple[str, str]]) -> tuple[dict, dict]:
    """Workflow 8 with `Krea2ApplyRegional` between the LoRA and the sampler.

    `regions` is [(prompt, mask node id)] in front-to-back order.
    """
    graph, node_map = baseline_graph()
    graph = json.loads(json.dumps(graph))
    graph["920"] = {"class_type": "SolidMask",
                    "inputs": {"value": 0.0, "width": CANVAS_W, "height": CANVAS_H}}
    prev = None
    for i, (text, mask) in enumerate(regions):
        enc, reg = f"96{i}", f"94{i}"
        _encode(graph, enc, text)
        graph[reg] = {"class_type": "Krea2RegionalPrompt",
                      "inputs": {"conditioning": [enc, 0], "mask": [mask, 0]}}
        if prev:
            graph[reg]["inputs"]["prev_regions"] = [prev, 0]
        prev = reg
    graph["950"] = {"class_type": "Krea2ApplyRegional", "inputs": {
        "model": ["822", 0], "conditioning": ["627", 0], "regions": [prev, 0],
        "restrict_img_attn": False, "exclusive_masks": True,
        "adaptive_masks": "off", "adaptive_steps": 2, "adaptive_threshold": 0.45,
        "base_loras_exclude_regions": False, "region_lock_strength": 0.0,
        "region_lock_start": 0.35, "region_lock_end": 0.85,
        "restrict_end_percent": 1.0}}
    graph["599"]["inputs"]["model"] = ["950", 0]
    graph["599"]["inputs"]["positive"] = ["950", 1]
    return graph, node_map


def arms() -> list[tuple[str, str, object]]:
    """label -> (base prompt, graph builder or None for the plain graph)."""
    def inert():
        graph, node_map = regional_graph([("", "921")])
        graph["921"] = {"class_type": "SolidMask",
                        "inputs": {"value": 1.0, "width": CANVAS_W,
                                   "height": CANVAS_H}}
        return graph, node_map

    def two(her: str = HER, him: str = HIM):
        graph, node_map = regional_graph([(him, "930c"), (her, "931c")])
        _band(graph, "930", HIM_H, 0)
        _band(graph, "931", HER_H, HER_Y)
        return graph, node_map

    return [
        ("RG-A-plain", TWO_PROMPT, None),
        ("RG-B-inert", TWO_PROMPT, inert),
        ("RG-C-two", BASE, two),
        ("RG-D-shared", BASE_D, lambda: two(HER_D)),
        ("RG-E-side", BASE_E, lambda: two(HER_E)),
        # E's own text with no routing at all: base, his block and her block
        # concatenated into one prompt on the plain graph. Same words, same
        # order, one region instead of three — so a difference here is the
        # routing and nothing else. Without it "the profile arrived" is a claim
        # about a wording nobody has shot on the plain graph.
        ("RG-F-control", f"{BASE_E}\n\nhim:\n{HIM}\n\nSubject:\n{HER_E}", None),
        # The 2x2 on the dead arrangement. The plain arms carry both blocks in
        # one prompt so the words are the same on both sides of the routing.
        ("U-A-plain", f"{UNDER_BASE.format(act=UNDER_PLAIN)}\n\nhim:\n{UNDER_HIM}"
                      f"\n\nSubject:\n{UNDER_HER}", None),
        ("U-B-region", UNDER_BASE.format(act=UNDER_PLAIN),
         lambda: two(UNDER_HER, UNDER_HIM)),
        ("U-C-anchor", f"{UNDER_BASE.format(act=UNDER_EDGE)}\n\nhim:\n{UNDER_HIM}"
                       f"\n\nSubject:\n{UNDER_HER}", None),
        ("U-D-both", UNDER_BASE.format(act=UNDER_EDGE),
         lambda: two(UNDER_HER, UNDER_HIM)),
        # The same pair again with every posture and orientation spelled out,
        # six fresh seeds, and identical text on both sides of the routing.
        ("U-C2-anchor", f"{UNDER_BASE.format(act=UNDER_EDGE_2)}\n\nhim:\n{UNDER_HIM_2}"
                        f"\n\nSubject:\n{UNDER_HER_2}", None),
        ("U-D2-both", UNDER_BASE.format(act=UNDER_EDGE_2),
         lambda: two(UNDER_HER_2, UNDER_HIM_2)),
    ]


async def run(comfy: Comfy, client_id: str, label: str, prompt: str,
              build, seed: int) -> Path | None:
    graph, node_map = build() if build else baseline_graph()
    values = dict(SETTINGS, positive=prompt, seed=seed, filename_prefix=label)
    patched = apply_map(graph, node_map, values)
    prompt_id = await comfy.queue_prompt(patched, client_id)
    for _ in range(600):
        history = await comfy.history(prompt_id)
        if history:
            break
        await asyncio.sleep(1)
    else:
        print(f"  {label}-s{seed}: timed out")
        return None
    images = output_images(history)
    if not images:
        print(f"  {label}-s{seed}: no image — {json.dumps(history.get('status', {}))[:400]}")
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
    ap.add_argument("--arm", default="", help="run one arm by label")
    # Three seeds nobody has shot yet, for confirming an arm on fresh noise
    # rather than on the three every arm above was tuned against.
    ap.add_argument("--seed-set", choices=("a", "b", "c"), default="a")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    pool = {"a": SEEDS, "b": SEEDS_B, "c": SEEDS_C}[args.seed_set]
    seeds = pool[:args.seeds]
    todo = [a for a in arms() if not args.arm or a[0] == args.arm]

    for label, prompt, build in todo:
        print(f"  {label:<12} {'regional' if build else 'plain graph':<12} "
              f"{prompt.splitlines()[0]}")
    OUT.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        for label, _, build in todo:
            if build:
                (OUT / f"{label}.json").write_text(
                    json.dumps(build()[0], indent=1), encoding="utf-8")
        return 0

    comfy, client_id = Comfy(COMFY_URL), str(uuid.uuid4())
    made = 0
    for label, prompt, build in todo:
        for seed in seeds:
            dest = await run(comfy, client_id, label, prompt, build, seed)
            if dest:
                made += 1
                print(f"  {dest.name}")
    print(f"\n{made}/{len(todo) * len(seeds)} rendered into {OUT}")
    return 0 if made == len(todo) * len(seeds) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
