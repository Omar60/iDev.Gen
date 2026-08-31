"""Ask whether a depth image reaches a geometry that no wording reaches.

Every camera and arrangement finding in this project is bounded by what words
do: the ninety-degree profile is a base-model limit, a horizontal hung off a
verified height is ignored 0/9, and the bench line with no camera clause comes
back frontal. `Krea2ControlApply` is a second channel — the geometry arrives as
a depth map instead of as a sentence — and this is the first look at it.

Four arms on the same line, the same three seeds, and a prompt that says nothing
at all about the camera, so the depth image is the only thing in the render that
knows where the camera stood:

  B-baseline  no control at all         - frontal is the known answer
  D-front     a frontal depth source    - control: it should look like B
  D-profile   a ninety-degree source    - the geometry no wording reaches
  D-high      a steep downward source   - the horizontal that dies at 0/9

D-front is what makes a positive readable: if every depth arm moves, including
the one whose source agrees with the words, the control is painting rather than
positioning.

Queued straight at ComfyUI, deliberately: no session, no draft, nothing written
to the database ([[idevgen-preview-drives-real-data]]). Wire it into the app
only if the channel turns out to work.

Usage: python scripts/shoot_depth_control.py [--seeds N] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
import pathlib
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from comfy import Comfy, apply_map, output_images  # noqa: E402

COMFY_URL = "http://127.0.0.1:8188"
SOURCES = ROOT / "data" / "depth-sources"
OUT = ROOT / "data" / "depth-probe"
DEPTH_BODY = json.loads((ROOT / "data" / "krea2-depth-control-workflow.json")
                        .read_text(encoding="utf-8"))

# Session 231's line as `shoot_camera_forms.py` fixed it, with the whole
# `Angle & Framing` block taken out. Nothing here names a direction.
LOOK = (
    "She wears her hair loose and straightened down past her shoulders, with a light "
    "coating of natural-looking makeup and a soft pink tint on her lips. She is in a small "
    "lived-in room with a worn beige sofa, the carpeted floor running toward a "
    "half-curtained window that lets in weak grey daylight, a bed against the far wall and "
    "a bedside lamp."
)
REST = """
Pose:
She stands still on the carpet with her weight carried evenly on both feet, her back \
straight and her shoulders level, her right hand resting at her side with the fingers \
slightly curled, her left arm hanging loose past her hip, her head held level.

Subject:
The black lace bralette sits across her chest and torso, its thin straps over her \
shoulders and the scalloped lace edge curving beneath her breasts. Her bare waist is \
uncovered between the bralette and the panties. The black lace high-cut panties rise \
over her hips, the ruffled lace edge tracing her hip line. The sheer black stockings \
run down her thighs to her feet.

Outfit & Texture:
She wears the black lace bralette with thin straps and scalloped edges, the black lace \
high-cut panties with ruffled edges, the sheer black stockings on her thighs, and the \
devil-horn headband clipped into her hair, nude from the waist.

Expression:
Her expression is calm and self-aware and still, her lips softly closed."""

PROMPT = f"zchar_jir.\n\n{LOOK}\n\nAngle & Framing:\na full-length photograph, head to feet.\n{REST}"

# label -> the depth source, or None for the uncontrolled arm.
ARMS = [
    ("B-baseline", None),
    ("D-front", "front_control.jpg"),
    ("D-profile", "profile_90.jpg"),
    ("D-high", "high_side.jpg"),
]

SEEDS = [399966242, 111222333, 777888999]

# The bench's settings, so a photograph here can be laid beside session 230/231.
SETTINGS = {"steps": 8, "cfg": 1, "lora_strength": 1, "sampler": "euler_ancestral",
            "scheduler": "beta", "checkpoint": "moodyKrea2Mix_v70.safetensors"}


def baseline_graph() -> tuple[dict, dict]:
    """Workflow 8 itself: the depth graph with the control chain cut back out.

    Taking the chain out of the same file rather than reading workflow 8 from
    the database is what keeps the two arms identical in every other node — a
    baseline re-fetched from elsewhere could differ by an edit nobody remembers.
    """
    g = json.loads(json.dumps(DEPTH_BODY["graph"]))
    g["599"]["inputs"]["model"] = ["822", 0]
    for nid in ("900", "901", "902", "903", "904"):
        del g[nid]
    node_map = {k: v for k, v in DEPTH_BODY["node_map"].items()
                if not v.split(".")[0] in ("900", "903")}
    return g, node_map


async def run_arm(comfy: Comfy, client_id: str, label: str, source: str | None,
                  seed: int, strength: float = 1.0) -> Path | None:
    if source is None:
        graph, node_map = baseline_graph()
        values = dict(SETTINGS, positive=PROMPT, seed=seed, filename_prefix=label)
    else:
        graph, node_map = DEPTH_BODY["graph"], DEPTH_BODY["node_map"]
        name = await comfy.upload_image(SOURCES / source, source)
        values = dict(SETTINGS, positive=PROMPT, seed=seed, filename_prefix=label,
                      reference=name, reference_strength=strength)

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
        print(f"  {label}-s{seed}: no image — {json.dumps(history.get('status', {}))[:300]}")
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
    ap.add_argument("--sweep", default="", metavar="A,B,C",
                    help="control strengths to sweep on --source instead of the four arms")
    ap.add_argument("--source", default="profile_90.jpg", help="the depth source the sweep uses")
    args = ap.parse_args()
    seeds = SEEDS[:args.seeds]

    if args.sweep:
        # At 1.0 the depth map carries the reference body, not just its geometry:
        # a taller or thinner source overrides the character LoRA's proportions.
        # This looks for a strength that keeps the geometry and gives the body back.
        strengths = [float(s) for s in args.sweep.split(",")]
        # The source belongs in the label: two sweeps over the same strengths
        # write the same filenames otherwise, and the second one silently
        # overwrites the first.
        stem = pathlib.Path(args.source).stem
        arms = [(f"{stem}-S{s}".replace(".", "_"), args.source, s) for s in strengths]
    else:
        arms = [(label, source, 1.0) for label, source in ARMS]

    missing = [s for _, s, _ in arms if s and not (SOURCES / s).exists()]
    if missing:
        print(f"missing depth sources: {missing}")
        return 1

    print(f"{len(arms)} arms x {len(seeds)} seeds = {len(arms) * len(seeds)} photographs")
    for label, source, strength in arms:
        print(f"  {label:<12} {source or 'no control':<20} strength {strength}")
    if args.dry_run:
        print("\n--- the line every arm shares ---")
        print(PROMPT)
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    comfy, client_id = Comfy(COMFY_URL), str(uuid.uuid4())
    made = 0
    for label, source, strength in arms:
        for seed in seeds:
            dest = await run_arm(comfy, client_id, label, source, seed, strength)
            if dest:
                made += 1
                print(f"  {dest.name}")
    print(f"\n{made}/{len(arms) * len(seeds)} rendered into {OUT}")
    return 0 if made == len(arms) * len(seeds) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
