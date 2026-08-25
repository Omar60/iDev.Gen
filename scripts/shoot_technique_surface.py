"""Does naming a surface in the `technique` field put that surface in the room?

`MANNER.candid.line` forbids the field naming anything in the room - `no wall,
no window, no bed, no furniture` - and justifies it with `a technique clause
that names a corner of one invents a different room`. Every measurement behind
that ban is a WRITER defect: an example reading `empty room down one side` came
back reworded as `empty bedspread down one side`, and the ban has been in force
since without anyone shooting it.

The painter's half has never been asked, and it is the half the rule claims.
Measured 2026-08-24, ten runs of the shipped instruction, the field still names
a surface in 13% of lines - so if naming one is harmless the rule is costing
prompt for nothing, and if it is not, 13% of a candid shoot is being furnished
by a clause about grain.

One line fixed by hand, only the Technique block swapped, the eight seeds of
sessions 277 and 278 so every arm across the three sessions is readable against
the others.

  * `none`    - body anchors only, no surface named. The control.
  * `inlook`  - plus a surface the LOOK already put in the room, the carpet.
    Naming it cannot invent anything; what it can do is drag it into frame.
  * `outlook` - VOID. It named a wooden table, on the reasoning that the look
    has a sofa, a bed, a carpet, a window and a lamp and no table. But a look
    that asks for a BEDSIDE LAMP gets a bedside table painted under it: session
    279's control answered `a table is visible` in 7 photographs of 8, the
    outlook arm in 8 of 8, and the question could not tell the arms apart. An
    absent object has to be one the model will not supply unasked, and
    plausible furniture is exactly what it supplies.
  * `foreign` - plus a white tiled kitchen counter, which this model will not
    paint into a carpeted bedroom on its own. It is the arm that carries the
    claim, and it is deliberately the EASIEST case for the effect to show: if a
    clause about grain cannot drag in a kitchen, it is not furnishing anything.

Usage: python scripts/shoot_technique_surface.py [--base URL] [--dry-run]
                                                 [--only none,foreign]
The session is created as a draft and NOT run.

Judge it with:
    python scripts/judge_camera.py <id> --question kitchen --repeat 3
(`--question furniture` is what void arm `outlook` was judged with.)
"""
from __future__ import annotations

import argparse
import sys

from shoot_camera_forms import SETTINGS, create_session
from shoot_candid_cameras import FRAMING, REST
from shoot_technique_anchor import CAMERA, LOOK, SEEDS, SHIPPED

# The same body-anchored clause in all three, so the arms differ by the surface
# and by nothing else. It is the shipped example list's own wording.
BODY = ("Blurred down her forearm where her hand moved at her side, the grain heavy "
        "in the shadow under her jaw")

ARMS = [
    ("none", f"Technique:\n{BODY}.",
     "body anchors only - the control"),
    ("inlook", f"Technique:\n{BODY}, the carpet beside her running into noise.",
     "plus a surface the look already put in the room"),
    ("outlook", f"Technique:\n{BODY}, the edge of the wooden table beside her gone to noise.",
     "VOID, session 279 - a bedside lamp makes the model paint a bedside table, so "
     "the control answered `a table is visible` 7 times in 8 and the question could "
     "not discriminate"),
    ("foreign", f"Technique:\n{BODY}, the white tiled kitchen counter beside her gone to noise.",
     "a surface the model will not paint on its own - the arm that carries the claim"),
    ("plausible", f"Technique:\n{BODY}, a stretch of empty bedspread above her head.",
     "INCONCLUSIVE, session 282 - 0 of 8, bedding never reached the top of the frame. "
     "But `above her head` is impossible geometry for a waist-up frontal of a standing "
     "woman, and this sampler renders a contradiction as neither, so the arm cannot "
     "tell a harmless clause from an impossible one"),
    ("behind", f"Technique:\n{BODY}, the bedspread behind her gone to noise.",
     "the same plausible surface with a placement the line permits - the look puts a "
     "bed against the far wall, so behind her is where it already is. Judged with "
     "--question bedsize: the control shows a bed at the edge, and the question is "
     "whether naming it brings it forward"),
]


def prompt_for(technique: str) -> str:
    rest = REST.replace(SHIPPED, technique)
    if technique not in rest:
        raise SystemExit("the shipped Technique block moved - fix SHIPPED in shoot_technique_anchor")
    return f"zchar_jir.\n\n{LOOK}\n\nAngle & Framing:\n{CAMERA}, {FRAMING}.\n{rest}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--dry-run", action="store_true", help="print the arms and write nothing")
    ap.add_argument("--only", default="", help="comma-separated arm labels")
    args = ap.parse_args()

    # The look must not already contain the thing `outlook` names, or the arm is
    # measuring nothing. Checked rather than trusted: the look is imported.
    if "table" in LOOK.lower():
        raise SystemExit("the look now has a table in it - pick another absent surface")

    wanted = tuple(w.strip() for w in args.only.split(",") if w.strip())
    arms = [a for a in ARMS if not wanted or a[0] in wanted]
    if not arms:
        print(f"no arm matches --only {args.only}")
        return 1

    shots = [
        {"label": f"{label}-s{i + 1}", "prompt": prompt_for(technique), "verbatim": True,
         "seed": seed, "count": 1}
        for label, technique, _ in arms
        for i, seed in enumerate(SEEDS)
    ]

    for label, technique, why in arms:
        print(f"{label:<8} {technique.splitlines()[-1]}")
        print(f"{'':<8} ({why})")
    print(f"\n{len(arms)} arms x {len(SEEDS)} seeds = {len(shots)} photographs, "
          f"same seeds and same line as sessions 277 and 278")

    if args.dry_run:
        print("\n--- the prompt of the last arm ---")
        print(prompt_for(arms[-1][1]))
        return 0

    out = create_session(
        args.base,
        "TECHNIQUE SURFACE - does naming one paint it, 3 arms x 8 seeds",
        shots, SETTINGS)
    print(f"\nsession {out['id']} created as a draft, {len(shots)} pending")
    print(f"run it with: curl -X POST {args.base}/api/sessions/{out['id']}/run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
