"""Shoot the four kiss frames, to check the wording in `KISS_FRAMES` renders.

The photograph this exists for - a kiss blown at the camera with the eyes shut -
was chased through several shoots and never arrived, then arrived first try from
a prompt written by hand. `KISS_FRAMES` is that prompt taken apart and put into
the shoot line's fields, and taking a prompt apart is exactly the step that can
lose what made it work. So: four flavours, two shared seeds, judged blind with
`scripts/judge_camera.py --question kiss`, which asks the eyes and nothing else.

The wording is read out of `kinds.js` through node rather than copied here. A
copy is a copy that drifts, and the whole question is whether THAT wording works.

Usage: python scripts/shoot_kiss_frames.py [--manner candid] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from shoot_camera_forms import SETTINGS, create_session
from shoot_candid_cameras import CANDID_CAPTURE, ROOM

ROOT = Path(__file__).resolve().parents[1]

# Two of session 231's three seeds: eight photographs is enough to see whether a
# gesture renders, and a third seed a flavour buys nothing this cannot say.
SEEDS = [399966242, 111222333]

FRAMING = "a waist-up photograph, from the top of her head to her waist"

REST = """
Pose:
She stands in the middle of the room with her weight on one hip, her free arm bent up toward \
the camera she is holding.

Subject:
She is in the black knit sweater with one shoulder bare where it has slipped, the black satin \
bra beneath it, and the high-waisted charcoal denim at her waist.

Outfit & Texture:
She wears the black knit sweater with long sleeves over the black satin bra, and the fitted \
high-waisted charcoal denim pants.

Technique:
Grainy, flat and overexposed, the shadows gone to noise, colour washed out."""


def kiss_frames() -> list[dict]:
    """`KISS_FRAMES` and `KISS_CAMERA`, read from the module that owns them."""
    # A file:// URL, not a bare path: node's ESM loader reads `C:` as a protocol.
    probe = ("import { KISS_FRAMES, KISS_CAMERA } from "
             f"'{(ROOT / 'frontend/src/kinds.js').as_uri()}';"
             "console.log(JSON.stringify({ KISS_FRAMES, KISS_CAMERA }))")
    out = subprocess.run(["node", "--input-type=module", "-e", probe],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--manner", choices=("candid", "directed"), default="candid")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    read = kiss_frames()
    frames, camera = read["KISS_FRAMES"], read["KISS_CAMERA"][args.manner]
    look = ROOM + (CANDID_CAPTURE if args.manner == "candid" else "")

    def prompt_for(frame: dict) -> str:
        act = f"\n\nAct:\n{frame['hand']}" if frame["hand"] else ""
        return (f"zchar_jir.\n\n{look}\n\nAngle & Framing:\n{camera}, {FRAMING}."
                f"{REST}{act}\n\nExpression:\n{frame['face']}")

    shots = [{"label": f"{f['key']}-s{i + 1}", "prompt": prompt_for(f), "verbatim": True,
              "seed": seed, "count": 1}
             for f in frames for i, seed in enumerate(SEEDS)]

    for f in frames:
        print(f"{f['key']:<8} {f['face'][:96]}")
    print(f"\n{len(frames)} flavours x {len(SEEDS)} seeds = {len(shots)} photographs, "
          f"{args.manner}")

    if args.dry_run:
        print("\n--- the first prompt ---")
        print(prompt_for(frames[0]))
        return 0

    out = create_session(args.base, f"KISS FRAMES - 4 variantes, {args.manner}",
                         shots, {**SETTINGS, "use_look": False})
    print(f"\nsession {out['id']} created as a draft, {len(shots)} pending")
    print(f"run it with: curl -X POST {args.base}/api/sessions/{out['id']}/run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
