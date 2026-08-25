"""Does the `technique` field have to be WRITTEN, or can code just pick from a menu?

Measured 2026-08-24 over 250 candid lines: the field's vocabulary is the closed
menu of eight in `MANNER.candid.line` and nothing else, but the writer spends
61% of lines tying those words to the photograph in front of it - `the window
side a stop too bright and hard shadow on her far shoulder`, `softly out of
focus where her hand moved`. The other 39% is bare recombination that
`spreadOver` would emit in one line, the way `cameraPlan` and `kissPlan`
already do.

That matters because if the anchoring buys nothing, the field stops being
asked for at all: it comes out of the prompt, it arrives 100% of the time by
construction instead of 77% by persuasion, the seven-key skeleton experiment
([[idevgen-seven-keys-header-load-bearing]]) is moot, and `technique` becomes a
per-session lever instead of eight phrases hardcoded for every candid shoot.

Three arms, one line fixed by hand, only the Technique block swapped, eight
seeds shared by all three. The 227/228/244 protocol.

  * `none`  - no Technique block at all. The deeper control: if this reads the
    same as the other two, the field is decoration and the honest change is to
    delete it rather than to generate it. It is NOT an arm without defects -
    the look still carries `sensor noise in the shadows, washed-out colour,
    slight motion blur`, because the look is what the app prepends to every
    candid line and deleting the field would not touch it. That is the point:
    this arm is what shipping without the field actually looks like.
  * `bare`  - the four defects named flatly, which is exactly what code would
    emit. Same four defects as `anchor`, so the pair differs by the anchoring
    and by nothing else.
  * `anchor`- the same four defects tied to this photograph's hand, sofa and
    window, in the writer's own manner.

What each outcome decides:

    none == bare == anchor   the field does nothing; delete it
    none <  bare == anchor   code generates it; delete the bullet and the menu
    none <  bare <  anchor   the writer earns it; ship the seven-key skeleton

The room, the capture clause, the crop and everything below the camera come
from `shoot_candid_cameras`, so this session is comparable to 245-262 and the
line is one already known to render.

Usage: python scripts/shoot_technique_anchor.py [--base URL] [--dry-run]
The session is created as a draft and NOT run - start it from the app, or POST
/api/sessions/{id}/run, after reading the shot list it prints.

Judge it with:
    python scripts/judge_camera.py <id> --question blur  --repeat 3
    python scripts/judge_camera.py <id> --question grain --repeat 3
"""
from __future__ import annotations

import argparse
import sys

from shoot_camera_forms import SETTINGS, create_session
from shoot_candid_cameras import CANDID_CAPTURE, FRAMING, REST, ROOM

LOOK = ROOM + CANDID_CAPTURE

# Eight, not the three of the camera work. The camera questions had an effect
# the size of a whole position; this one is asking whether a clause lands in a
# particular place, which is the smaller signal of the two.
SEEDS = [399966242, 111222333, 777888999, 424242424,
         135791113, 246813579, 909090909, 555444333]

# One camera, and a frontal one on purpose: every arm here is about where a
# DEFECT lands, so nothing in the photograph may be in doubt about where her
# body is. A behind-the-shoulder camera would put the position and the blur in
# the same question ([[idevgen-position-collapse-is-contradiction]]).
CAMERA = "Taken from directly in front of her at her eye level"

# The Technique block `REST` ships with, replaced per arm.
SHIPPED = ("Technique:\nGrainy, flat and overexposed, the shadows gone to noise, "
           "colour washed out.")

# The four defects are IDENTICAL across `bare` and `anchor` - grain, washed
# colour, motion blur, noise in the shadows - and only their attachment moves.
# An arm that also changed which defects were named would be measuring the menu
# and not the anchoring.
BARE = ("Technique:\nGrainy, colour washed out, slightly blurred, the shadows "
        "gone to noise.")

ANCHOR = ("Technique:\nGrainy, colour washed out, slightly blurred where her hand "
          "moved at her side, the shadows along the sofa behind her gone to noise.")

ARMS = [
    ("none", "",
     "no Technique block at all - the control that says whether the field does anything"),
    ("bare", BARE,
     "the four defects named flatly, which is what spreadOver would emit"),
    ("anchor", ANCHOR,
     "the same four tied to her hand and the sofa, in the writer's manner"),
]


def prompt_for(technique: str) -> str:
    # `REST` opens with a newline, so the block is spliced rather than appended.
    rest = REST.replace(SHIPPED, technique) if technique else REST.replace(SHIPPED + "\n\n", "")
    if technique and technique not in rest:
        raise SystemExit("the shipped Technique block moved - fix SHIPPED in this script")
    return f"zchar_jir.\n\n{LOOK}\n\nAngle & Framing:\n{CAMERA}, {FRAMING}.\n{rest}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--dry-run", action="store_true", help="print the arms and write nothing")
    ap.add_argument("--only", default="", help="comma-separated arm labels")
    args = ap.parse_args()

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
        print(f"{label:<8} {technique.splitlines()[-1] if technique else '(no block)'}")
        print(f"{'':<8} ({why})")
    print(f"\n{len(arms)} arms x {len(SEEDS)} seeds = {len(shots)} photographs")

    if args.dry_run:
        print("\n--- the prompt of the first arm ---")
        print(prompt_for(arms[0][1]))
        return 0

    out = create_session(
        args.base,
        "TECHNIQUE ANCHOR - none vs bare vs anchored, 8 seeds",
        shots, SETTINGS)
    print(f"\nsession {out['id']} created as a draft, {len(shots)} pending")
    print(f"run it with: curl -X POST {args.base}/api/sessions/{out['id']}/run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
