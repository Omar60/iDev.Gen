"""Shoot one arrangement against each camera family that is supposed to see it.

Sessions 267 and 268 planted all six in written shoots and got one photograph
each: enough to say `ontop`, `away` and `standing` render and that `behind` never
does, and not enough to say anything about `back` and `side`. A written shoot is
also the wrong instrument for the question - every planted photograph came with
its own stage, its own wardrobe state and whatever framing the writer chose, so a
miss cannot be pinned on the arrangement.

So: the 227/228/244 protocol. One line fixed by hand, the `act` field taken from
`ARRANGEMENTS` word for word, three seeds shared, and the camera swapped through
the families that arrangement says can see it. What moves is the arrangement and
the camera; nothing else in the line does.

The wording is read out of `kinds.js` through node rather than copied here, the
way `shoot_kiss_frames.py` does it: a copy drifts, and whether THAT wording works
is the whole question.

`--only` takes arrangement keys. The default is the two that have never been
measured, plus `astride` as the control - it is 12 of 12 in sessions 265 and 266
and a flat result on it means the rig is wrong rather than the arrangements.

Usage: python scripts/shoot_arrangements.py [--only back,side] [--dry-run]

Judge it with:
    python scripts/judge_camera.py <id> --question arrangement --repeat 3
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from shoot_camera_forms import SEEDS, SETTINGS, create_session
from shoot_candid_cameras import CANDID_CAPTURE, ROOM

ROOT = Path(__file__).resolve().parents[1]

LOOK = ROOM + CANDID_CAPTURE

FRAMING = "a three-quarter photograph from the knees up"

EXPLICIT_SETTINGS = SETTINGS | {
    "checkpoint": "finepornV4INT8NVFP4BF16_v4Nvfp4.safetensors",
    "steps": 12, "sampler": "er_sde", "scheduler": "beta",
}

# Everything the arrangement does not decide, fixed. Neither body is placed here
# - where they are and which way they face is the `act` field, which is what is
# being measured - so this says only that they are naked, where the light is and
# what her face is doing.
REST = """
Subject:
Her chest is bare, her stomach bare, her hips bare, her thighs bare, her feet bare.

Second Subject:
He is naked with her, his chest bare, his stomach bare, his hips bare, his thighs bare.

Outfit & Texture:
Nude.

Technique:
Grainy, flat and overexposed, the shadows gone to noise, colour washed out.

Expression:
Her mouth is open on a sound and her eyes are half-shut."""


def catalogue() -> dict:
    """`ARRANGEMENTS` and the positions, read from the module that owns them."""
    # A file:// URL, not a bare path: node's ESM loader reads `C:` as a protocol.
    probe = ("import { ARRANGEMENTS, POSITIONS } from "
             f"'{(ROOT / 'frontend/src/kinds.js').as_uri()}';"
             "console.log(JSON.stringify({ ARRANGEMENTS, POSITIONS }))")
    out = subprocess.run(["node", "--input-type=module", "-e", probe],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--only", default="back,side,astride",
                    help="arrangement keys; the default is the two unmeasured ones "
                         "and the control")
    ap.add_argument("--manner", default="selfie",
                    help="whose camera catalogue the families are drawn from")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    read = catalogue()
    positions = read["POSITIONS"][args.manner]
    wanted = [k.strip() for k in args.only.split(",") if k.strip()]
    arrangements = [a for a in read["ARRANGEMENTS"] if a["key"] in wanted]
    missing = set(wanted) - {a["key"] for a in arrangements}
    if missing:
        print(f"no arrangement named {sorted(missing)} - it may have been taken out of the pool")
        return 1

    shots = []
    for a in arrangements:
        # One camera per allowed family, and the first form of each: which form
        # inside a family is a question the camera catalogue already answered.
        seen, cameras = set(), []
        for p in positions:
            if p["family"] in a["cameras"] and p["family"] not in seen:
                seen.add(p["family"])
                cameras.append(p)
        print(f"{a['key']:<9} {len(cameras)} cameras: {', '.join(sorted(seen))}")
        for p in cameras:
            label = f"{a['key']}-{p['family']}"
            prompt = (f"zchar_jir.\n\n{LOOK}\n\nAngle & Framing:\n{p['line']}, {FRAMING}.\n"
                      f"\nAct:\n{a['act']}\n{REST}")
            for seed in SEEDS:
                shots.append({"label": label, "prompt": prompt, "seed": seed, "count": 1})

    print(f"\n{len(shots)} photographs, {len(SEEDS)} seeds each")
    if args.dry_run:
        print("\n--- the first prompt ---")
        print(shots[0]["prompt"])
        return 0

    out = create_session(args.base, "ARRANGEMENTS - one line, the act and the camera swapped",
                         shots, EXPLICIT_SETTINGS)
    print(f"\nsession {out['id']} created as a draft, {len(shots)} pending")
    print(f"run it with: curl -X POST {args.base}/api/sessions/{out['id']}/run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
