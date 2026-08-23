"""Build one render session that asks who is holding the camera, with the act in frame.

Session 264 wrote a whole `selfie` shoot and rated it by hand: five photographs
read as taken by her, and all five came from two camera forms - the arm's length
and the mirror. Everything else read as a third person or a stand. But that shoot
gave each form two to four photographs, each with whatever framing the writer
happened to pair with it, so it cannot say whether a form failed or whether its
framing did.

This is the arm, on the 227/228/244 protocol: one line fixed by hand, only the
camera clause swapped, three seeds shared by every arm, judged blind afterwards.
What it changes from `shoot_candid_cameras.py` is the photograph itself - that
one is a dressed woman alone in a room, and the question here only exists with
two bodies and the act in the frame. A camera form that reads as hers with her
standing on a carpet may not survive a second body arriving.

Two of the arms are the pair that decides the wording of every close form: S1
names the phone and S5 is the same position with the device word taken out.
Session 264 measured the device word as painting no device at all, so both are
legal and the question is which one reads as hers more often.

The framing is waist-up in every arm and that is not a free choice: a full-length
photograph taken at arm's length is a contradiction, and this sampler renders a
contradiction as neither ([[idevgen-position-collapse-is-contradiction]]). It
does cost one thing, and the run cannot answer it: in 264 the `own hand at her
chest` form painted the phone at full-length and nothing at waist-up, one
photograph each.

Usage: python scripts/shoot_selfie_cameras.py [--base URL] [--dry-run] [--only S1,S5]
The session is created as a draft and NOT run - read the shot list it prints,
then POST /api/sessions/{id}/run.

Judge it with:
    python scripts/judge_camera.py <id> --question holder --repeat 3
    python scripts/judge_camera.py <id> --question act --repeat 3
"""
from __future__ import annotations

import argparse
import sys

from shoot_camera_forms import SEEDS, SETTINGS, create_session
from shoot_candid_cameras import CANDID_CAPTURE, ROOM

LOOK = ROOM + CANDID_CAPTURE

FRAMING = "a waist-up photograph"

# finepornV4, as sessions 155, 161 and 264 were shot. The Krea 2 mix is the base
# every camera question up to now was asked on, and it is the wrong one here: the
# act has to render for the question to mean anything.
EXPLICIT_SETTINGS = SETTINGS | {
    "checkpoint": "finepornV4INT8NVFP4BF16_v4Nvfp4.safetensors",
    "steps": 12, "sampler": "er_sde", "scheduler": "beta",
}

# Everything below the camera clause, fixed and identical in every arm. Nothing
# in it names a device, an arm reaching out, a direction or a photographer: the
# camera clause is the only thing in the photograph that says who is holding it.
# Her body is written without saying which side of her is seen, which is what
# session 248 found the frontal Subject block was costing every non-frontal
# camera.
REST = """
Pose:
She is astride him with her knees on either side of his hips and her weight down on him, \
the two of them joined, his hands on her waist.

Subject:
Her chest is bare, her stomach bare, her hips bare where his hands hold them, her thighs \
bare against his, her feet bare on the sheet.

Second Subject:
He is naked beneath her, his chest bare, his stomach bare, his hips under hers, his \
thighs bare on the bed.

Outfit & Texture:
Nude.

Technique:
Grainy, flat and overexposed, the shadows gone to noise, colour washed out.

Expression:
Her mouth is open on a sound and her eyes are half-shut."""

# Session 265 is why this exists. With the pose above and only the camera clause
# moving, `Phone held out at arm's length in front of her face` read as hers in
# one photograph of three - and in session 264, where the same clause earned all
# three of the user's hand-rated fives, every one of those lines had the writer
# put her arm out toward the lens in the POSE, because the manner asks for it in
# `act`. So the arm is the candidate for what actually buys the read, and the
# camera clause may be buying nothing on its own. `--arm-out` adds it and changes
# nothing else.
ARM_OUT = (" Her free arm is stretched out toward the camera, her near hand and forearm "
           "large at the edge of the frame.")

REST_ARM = REST.replace("his hands on her waist.", "his hands on her waist." + ARM_OUT)

# label, the camera clause, and what the clause is for. The two controls come
# first so a run read in order fails loudly at the top if the rig is wrong: both
# were `someone` 3/3 in session 264 and anything else here is the rig.
ARMS = [
    ("C1-front", "Taken from directly in front of her",
     "control, someone 3/3 in 264"),
    ("C2-overhead", "Overhead camera directly above her",
     "control, someone 3/3 in 264"),
    ("S1-armslength-phone", "Phone held out at arm's length in front of her face",
     "the form all three of 264's hand-rated fives came from, with the device word"),
    ("S2-mirror", "Mirror selfie, the phone up in her right hand and visible in the mirror",
     "the other form that scored, and the only one that really paints a device"),
    ("S3-own-hand-low", "Phone held low in her own hand at her chest, angled down along her own "
                        "body",
     "a `pov` form, herself 3/3 to the judge in 264 and left unrated by hand"),
    # Shortened on purpose, and the shortening is itself a finding: the shipped
    # catalogue wording ends `as she lies on her back`, which is a POSE inside a
    # camera clause, and it contradicts every pose but one. Here it would fight
    # the fixed line above, so what is shot is the form without its passenger -
    # which is what [[idevgen-height-carries-no-passengers]] says should be the
    # only part that works anyway. If it renders, the catalogue entry loses its
    # tail.
    ("S4-above-her-face", "Phone held above her face in her own outstretched hand, looking down "
                          "at her",
     "the other `pov` form with its pose clause cut, and the one 264 read as someone 2/3"),
    ("S5-armslength-bare", "Taken from an arm's length in front of her face",
     "S1's position with the device word taken out - the pair is the point"),
]


def prompt_for(camera: str, rest: str = REST) -> str:
    return f"zchar_jir.\n\n{LOOK}\n\nAngle & Framing:\n{camera}, {FRAMING}.\n{rest}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--dry-run", action="store_true", help="print the arms and write nothing")
    ap.add_argument("--arm-out", action="store_true",
                    help="put her free arm out toward the lens in the POSE, which is "
                         "what the writer does and what session 265 removed")
    ap.add_argument("--only", default="",
                    help="comma-separated arm labels by their prefix (`S1,S5` for the pair)")
    args = ap.parse_args()

    # Matched on the prefix before the first dash and nothing else: `--only S1`
    # matching S10 through S15 built a 21-photograph session out of a
    # 3-photograph question once already.
    wanted = {p.strip().upper() for p in args.only.split(",") if p.strip()}
    arms = [a for a in ARMS if not wanted or a[0].split("-")[0].upper() in wanted]
    if wanted and not arms:
        print(f"no arm matches {sorted(wanted)}")
        return 1

    shots = []
    for label, camera, why in arms:
        print(f"{label:<22} {why}")
        print(f"    {camera}")
        for seed in SEEDS:
            shots.append({"label": label,
                          "prompt": prompt_for(camera, REST_ARM if args.arm_out else REST),
                          "seed": seed, "count": 1})

    print(f"\n{len(arms)} arms x {len(SEEDS)} seeds = {len(shots)} photographs")
    if args.dry_run:
        print("\n--- the first prompt ---")
        print(shots[0]["prompt"])
        return 0

    out = create_session(args.base,
                         "SELFIE CAMERAS - who is holding it, with the act in frame"
                         + (" [arm out]" if args.arm_out else ""),
                         shots, EXPLICIT_SETTINGS)
    print(f"\nsession {out['id']} created as a draft, {len(shots)} pending")
    print(f"run it with: curl -X POST {args.base}/api/sessions/{out['id']}/run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
