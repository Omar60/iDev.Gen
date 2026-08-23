"""Build one render session that asks nine camera forms of the very same photograph.

`CAMERA_POSITIONS` holds eight forms because eight is what sessions 227 and 228
measured the sampler to obey, not because eight is a design. This is the batch
that asks whether it can be nine or twelve: one line fixed by hand, only the
camera clause swapped, three seeds shared by every arm, judged blind afterwards
with `scripts/judge_camera.py`.

Three arms are controls with a known answer (front 3/3, overhead 6/6, floor 2/3
in session 230), so a flat result is readable as the rig failing rather than the
forms failing. The six new ones each test one thing:

  * the mirror       - only the LEFT shoulder is in the catalogue, and its mirror
                       is a different photograph, not a different wording.
  * the front turn   - nothing sits between `directly in front of her` and the
                       side clause, and a three-quarter turn is what Krea 2
                       renders anyway when asked for a profile
                       ([[idevgen-profile-is-a-base-model-limit]]). Asked for
                       directly it should be the cheapest new family there is.
  * four tails       - the verified heights carry no horizontal component at all.
                       Session 228's law is that the vertical has to be the HEAD
                       of the phrase; these keep the verified head word for word
                       and hang a horizontal on the end, which is the one shape
                       that law does not forbid and nobody has shot.

Usage: python scripts/shoot_camera_forms.py [--base URL] [--dry-run]
The session is created as a draft and NOT run — start it from the app, or POST
/api/sessions/{id}/run, after reading the shot list it prints.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

# Session 231's line with every side-specific word taken out of the pose, the
# subject and the expression, so the camera clause is the only thing in the
# photograph that names a direction. Everything else is 231 verbatim, including
# the room — it has depth, which is this project's framing control.
LOOK = (
    "She wears her hair loose and straightened down past her shoulders, with a light "
    "coating of natural-looking makeup and a soft pink tint on her lips. She is in a small "
    "lived-in room with a worn beige sofa, the carpeted floor running toward a "
    "half-curtained window that lets in weak grey daylight, a bed against the far wall and "
    "a bedside lamp."
)

# One framing for every arm. Full-length on purpose: the judge is asked how far
# her torso is turned and where the camera stood, and a waist-up crop throws away
# half of what answers either question.
FRAMING = "a full-length photograph, head to feet"

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

# label, the camera clause, and what the clause is for. The three controls come
# first so a run read in order fails loudly at the top if the rig is wrong.
ARMS = [
    ("C1-front", "Taken from directly in front of her",
     "control, 3/3 in session 230"),
    ("C2-overhead", "Overhead camera directly above her",
     "control, 6/6 in session 230"),
    ("C3-floor", "Low-angle shot from the floor at her feet",
     "control, and the weak one - 2/3, the low angle came back mild"),
    ("N1-shoulder-right", "Taken from behind her right shoulder, her back three-quarters to the camera",
     "the mirror of the only shoulder form in the catalogue"),
    ("N2-front-threequarter", "Taken from her right front, her body turned three-quarters toward the camera",
     "the family that does not exist yet, between front and side"),
    ("N3-floor-behind", "Low-angle shot from the floor behind her",
     "verified height, horizontal tail"),
    ("N4-floor-front", "Low-angle shot from the floor in front of her",
     "verified height, horizontal tail"),
    ("N5-overhead-behind", "Overhead camera directly above her and behind her head",
     "verified height, horizontal tail"),
    ("N6-highdown-right", "High camera looking steeply down at her from her right side",
     "verified height, horizontal tail"),
]

# Session 231's three, so a photograph from this batch can be laid beside one of
# its arms on the same noise.
SEEDS = [399966242, 111222333, 777888999]

SETTINGS = {
    "width": 832, "height": 1216, "steps": 8, "cfg": 1, "lora_strength": 1,
    "checkpoint": "moodyKrea2Mix_v70.safetensors", "kind": "shoot",
    "sampler": "euler_ancestral", "scheduler": "beta",
    # The look is written into every prompt below, so the session must not
    # prepend it a second time. This is how 231 was built.
    "use_look": False,
}


def prompt_for(camera: str) -> str:
    return f"zchar_jir.\n\n{LOOK}\n\nAngle & Framing:\n{camera}, {FRAMING}.\n{REST}"


def create_session(base: str, name: str, shots: list, settings: dict) -> dict:
    """The draft, posted once. Shared with `shoot_candid_cameras.py`.

    No retry, deliberately: a reset can arrive after the server has already
    inserted the session, and retrying this call is what made sessions 232 and
    233 as duplicate drafts.
    """
    body = {"model_id": 1, "workflow_id": 8, "name": name,
            "look": "", "wardrobe": "", "settings": settings, "shots": shots}
    req = urllib.request.Request(base + "/api/sessions",
                                 json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--dry-run", action="store_true", help="print the arms and write nothing")
    args = ap.parse_args()

    shots = [
        {"label": f"{label}-s{i + 1}", "prompt": prompt_for(camera), "verbatim": True,
         "seed": seed, "count": 1}
        for label, camera, _ in ARMS
        for i, seed in enumerate(SEEDS)
    ]

    for label, camera, why in ARMS:
        print(f"{label:<22} {camera}\n{'':<22} ({why})")
    print(f"\n{len(ARMS)} arms x {len(SEEDS)} seeds = {len(shots)} photographs")

    if args.dry_run:
        print("\n--- the prompt of the first arm ---")
        print(prompt_for(ARMS[0][1]))
        return 0

    out = create_session(args.base, "CAMERA FORMS - 6 formas nuevas contra 3 controles, 3 seeds",
                         shots, SETTINGS)
    print(f"\nsession {out['id']} created as a draft, {len(shots)} pending")
    print(f"run it with: curl -X POST {args.base}/api/sessions/{out['id']}/run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
