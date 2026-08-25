"""The writer's own two forms of an anchored technique clause, against each other.

Session 277 asked whether anchoring does anything at all and got a clean yes:
`slightly blurred` landed the blur on her hand 0 times in 8, exactly what a
photograph with no technique clause scored, and `slightly blurred where her
hand moved at her side` landed it 5 times in 8 (p=0.013).

But 277's bare arm is not what the shipped writer actually produces. The old
example list already carried ONE anchored form - `slightly blurred where a
hand moved` - and that is the wording the writer copies: it has a where-clause,
it just does not say whose hand, where on the arm, or where the arm is. The
rewritten examples say all three. So the question 277 could not answer is
whether that extra specificity buys anything, or whether any where-clause at
all is enough.

Both clauses below are the WRITER'S OWN, lifted verbatim from runs of the two
instructions, not written by hand:

  * `loose`    from the shipped examples, 5 runs of 25
  * `specific` from the rewritten examples, same build otherwise

Only the blur half of each clause differs in kind; both name three or four
defects and both are the same length class, so the arms differ by how tightly
the blur is tied to her body and by very little else.

Why this is only two arms and not a rebuild of 277: of 125 lines the rewritten
examples produced, only 2 could be transplanted into a fixed pose at all. The
new form anchors to what she is DOING - `where her fingers worked at the
clasp`, `where her hand moved beneath the hem` - so it is coupled to its own
line by construction. That is a finding in itself and it is why the shipped
form has to be judged in the writer's own words rather than in a hand-built
grid.

The eight seeds and the fixed line are 277's, so its `anchor` and `none` arms
are readable against these two without reshooting them.

Usage: python scripts/shoot_technique_specificity.py [--base URL] [--dry-run]
The session is created as a draft and NOT run.

Judge it with:
    python scripts/judge_camera.py <id> --question blur --repeat 3
"""
from __future__ import annotations

import argparse
import sys

from shoot_camera_forms import SETTINGS, create_session
from shoot_technique_anchor import CAMERA, LOOK, SEEDS, SHIPPED
from shoot_candid_cameras import FRAMING, REST

# The writer's own words. `loose` is the commonest blur wording the shipped
# examples produce; `specific` is the one the rewritten examples produce that
# fits this pose - her hand is at her side in it, and so is the clause.
LOOSE = ("Technique:\nColour washed out, grainy, slightly blurred where a hand moved, "
         "the near side a stop too bright.")

SPECIFIC = ("Technique:\nBlurred down her forearm where her hand moved at her side, the "
            "grain heavy in the shadow under her jaw, the near side of her face a stop "
            "too bright.")

ARMS = [
    ("loose", LOOSE, "the shipped examples' own blur wording: a where-clause with no body in it"),
    ("specific", SPECIFIC, "the rewritten examples' own: forearm, hand, and where the hand is"),
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
    args = ap.parse_args()

    shots = [
        {"label": f"{label}-s{i + 1}", "prompt": prompt_for(technique), "verbatim": True,
         "seed": seed, "count": 1}
        for label, technique, _ in ARMS
        for i, seed in enumerate(SEEDS)
    ]

    for label, technique, why in ARMS:
        print(f"{label:<9} {technique.splitlines()[-1]}")
        print(f"{'':<9} ({why})")
    print(f"\n{len(ARMS)} arms x {len(SEEDS)} seeds = {len(shots)} photographs, "
          f"same seeds and same line as session 277")

    if args.dry_run:
        print("\n--- the prompt of the first arm ---")
        print(prompt_for(ARMS[0][1]))
        return 0

    out = create_session(
        args.base,
        "TECHNIQUE SPECIFICITY - loose vs specific anchor, writer's own words, 8 seeds",
        shots, SETTINGS)
    print(f"\nsession {out['id']} created as a draft, {len(shots)} pending")
    print(f"run it with: curl -X POST {args.base}/api/sessions/{out['id']}/run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
