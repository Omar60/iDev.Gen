"""Build one render session that asks six candid camera forms of the same photograph.

`CAMERA_POSITIONS` is the DIRECTED catalogue: it is written in the vocabulary of
someone standing behind a camera, and `cameraPlan` hands it out only when the
manner is `directed`. `candid` has no plan and cannot borrow that one, because
its positions are not where a photographer stands - they are where a phone was
put down. Nobody has shot them, so there is nothing to build a plan out of.

This is that shoot. The 227/228/244 protocol: one line fixed by hand, only the
camera clause swapped, three seeds shared by every arm, judged blind afterwards
with `scripts/judge_camera.py`.

Three arms are controls in the directed vocabulary with a known answer (front
3/3, overhead 6/6, floor 2-3/3 in sessions 230 and 244), so a flat result reads
as the rig failing rather than the forms failing. They are shot in the CANDID
look, which is the one thing this session changes about them - if a verified
form stops working under the phone-snapshot look, that is the finding and the
six new arms are unreadable without it.

The six new ones ask two things:

  * does a mount reach a height?  `Phone propped on a high shelf ... looking down`
    and `Phone set down on the carpet at her feet` name where the phone IS, not
    where the camera is, and the verified head word (`Overhead camera`,
    `Low-angle shot from the floor`) is gone. Session 244 established the
    vertical has to be the head of the phrase; these test whether a mount can be
    that head. N2 keeps `looking straight down` on a phone in his hand, which is
    the candid form the manner's own line already suggests.
  * is the device word load-bearing?  N4 and N6 are the same position, the
    signature candid frame, with and without the word `phone`. The manner
    forbids naming a device except when it is really in the frame - at arm's
    length it IS in the frame, so both are legal, and which one renders decides
    how the catalogue words every other close form.

The framing is waist-up in every arm, and that is not a free choice: a
full-length photograph taken at arm's length is a contradiction, and this
sampler renders a contradiction as neither
([[idevgen-position-collapse-is-contradiction]]).

Usage: python scripts/shoot_candid_cameras.py [--base URL] [--dry-run]
The session is created as a draft and NOT run - start it from the app, or POST
/api/sessions/{id}/run, after reading the shot list it prints.

Judge it with:
    python scripts/judge_camera.py <id> --question position --repeat 3
    python scripts/judge_camera.py <id> --question device --repeat 3
"""
from __future__ import annotations

import argparse
import sys

from shoot_camera_forms import SEEDS, SETTINGS, create_session

# Session 231's room, the same one the directed camera work used - it has depth,
# which is this project's framing control - plus MANNER.candid.look word for
# word. The capture quality belongs to the look in the app and it belongs to the
# look here, so these photographs are lit and shot the way a candid session is.
ROOM = (
    "She wears her hair loose and straightened down past her shoulders, with a light "
    "coating of natural-looking makeup and a soft pink tint on her lips. She is in a small "
    "lived-in room with a worn beige sofa, the carpeted floor running toward a "
    "half-curtained window that lets in weak grey daylight, a bed against the far wall and "
    "a bedside lamp."
)

CANDID_CAPTURE = (
    " phone camera snapshot, small sensor, everything at every distance "
    "equally in focus and nothing softened, sensor noise in the shadows, washed-out colour, "
    "slight motion blur, off-center and slightly tilted framing, no studio lighting and no "
    "colour grading"
)

# Session 247 came back 0/24 with BOTH directed controls falling - `Taken from
# directly behind her` and `Taken from behind her left shoulder` read `front` in
# every pass, and they are catalogue forms. Session 245 had already lost the
# floor the same way. Either the candid capture clause is eating every position
# that is not front or overhead, or this fixed line is - its Subject block names
# what is visible on her front, and objects outrank the camera in this project.
# `--look directed` drops the capture clause and changes nothing else, which is
# the only way to tell those two apart.
LOOKS = {"candid": ROOM + CANDID_CAPTURE, "directed": ROOM}
LOOK = LOOKS["candid"]

# Waist-up in every arm. A full-length photograph at arm's length cannot exist,
# and an arm that asks for one is testing the contradiction, not the position.
FRAMING = "a waist-up photograph, from the top of her head to her waist"

# Session 245 came back floor 0/6 - and the control fell with it, the same
# `Low-angle shot from the floor at her feet` that was 3/3 in session 244. Two
# things had changed at once: the candid look, and the crop, because 244 shot
# every arm full-length and this one is waist-up. Framing owns the scene in this
# project, so the crop is at least as likely a killer as the look, and the floor
# arms are unreadable until they are separated. `--framing full --only C3,N3`
# reshoots exactly those two against the same seeds.
FRAMINGS = {
    "waist": FRAMING,
    "full": "a full-length photograph, head to feet",
}

# Everything below the camera clause, fixed. No word in it names a direction, a
# height or a device, so the camera clause is the only thing in the photograph
# that says where the phone was.
REST = """
Pose:
She stands on the carpet with her weight settled on one hip and her shoulders a little \
uneven, one hand loose at her side with the fingers half curled, her head held level.

Subject:
The black knit sweater covers her chest and torso with the black satin bra visible at \
the bust where the neck of it has slipped, her bare shoulder uncovered on one side. Her \
waist is in the high-waisted charcoal denim.

Outfit & Texture:
She wears the black knit sweater with long sleeves over the black satin bra, and the \
fitted high-waisted charcoal denim pants.

Technique:
Grainy, flat and overexposed, the shadows gone to noise, colour washed out.

Expression:
Her mouth is a little open and her eyes are half-lidded, caught mid-word rather than \
held."""

# Session 248, six photographs: with the candid capture clause dropped and NOTHING
# else changed, `Taken from directly behind her` is still 0/3 and the shoulder is
# 1/3. So the capture clause is not what flattened session 247 - the line is. The
# Subject block above names her chest, her bust and the bra at her neckline, which
# is a description of her FRONT, and in this project an object outranks the camera
# ([[idevgen-position-collapse-is-contradiction]]). A camera behind her and a
# subject written from in front of her is the same contradiction as a face the
# body hides.
#
# This one names the same garments without saying which side of her they are seen
# from. It is the arm that says whether the catalogue work can proceed at all.
REST_NEUTRAL = REST.replace(
    "The black knit sweater covers her chest and torso with the black satin bra visible at "
    "the bust where the neck of it has slipped, her bare shoulder uncovered on one side. Her "
    "waist is in the high-waisted charcoal denim.",
    "She is in the black knit sweater with one shoulder bare where it has slipped, the black "
    "satin bra beneath it, and the high-waisted charcoal denim at her waist.")

SUBJECTS = {"front": REST, "neutral": REST_NEUTRAL}

# Session 253 shot a whole 24-photograph candid shoot, written by the real writer
# with the plan in hand, and it obeyed 13 of 24 where the hand-fixed arms were
# 3/3 a form. Two differences between an arm and a real line are testable, and
# one of them is a tail on the framing:
#
#   * the careless-framing clause, shipped in `9c06cdd`, which the writer now
#     puts in 15.8 lines of 25. Two of the three frontal misses carried one and
#     both came back `overhead`. `a stretch of empty room above her head` may be
#     tipping the camera up, which a text measurement can never see.
#   * the crop. The writer paired `Phone propped on a high shelf across the room`
#     with `a full-length photograph` all three times and it was 0/3, where the
#     same clause waist-up was 3/3 in session 245. `--framing full --only N1`
#     asks that one and needs no tail.
TAILS = {
    "none": "",
    "careless": ", she is off to the left of the frame and a stretch of empty room above her head",
}

# label, the camera clause, and what the clause is for. The three controls come
# first so a run read in order fails loudly at the top if the rig is wrong.
ARMS = [
    ("C1-front", "Taken from directly in front of her",
     "control, 3/3 in sessions 230 and 244 - under the candid look here"),
    ("C2-overhead", "Overhead camera directly above her",
     "control, 6/6 in 230, 3/3 in 244 - under the candid look here"),
    ("C3-floor", "Low-angle shot from the floor at her feet",
     "control, and the weak one - 2/3 in 230, 3/3 in 244"),
    ("N1-shelf-high", "Phone propped on a high shelf across the room, looking down at her",
     "a mount as the head of the phrase, asking for the overhead"),
    ("N2-hand-overhead", "Phone held above her in his hand, looking straight down at her",
     "the candid overhead the manner's own line suggests"),
    ("N3-carpet-floor", "Phone set down on the carpet at her feet, tipped up toward her",
     "a mount as the head of the phrase, asking for the floor"),
    ("N4-armslength-phone", "Phone held out at arm's length in front of her face",
     "the signature candid frame, with the device word"),
    ("N5-mirror", "Mirror selfie, the phone up in her right hand and visible in the mirror",
     "the one form the manner already writes out in full"),
    ("N6-armslength-bare", "Taken from an arm's length in front of her face",
     "N4's position with the device word taken out - the pair is the point"),
    # The second batch, session 247: the two families 245 never asked for. It has
    # its own two controls because 245 is exactly why - the floor control fell
    # under the candid look, so a directed form is not verified here until it has
    # been shot here. Every new arm hangs a horizontal on a mount, which is the
    # shape [[idevgen-height-carries-no-passengers]] found dead when the head was
    # a verified HEIGHT: a mount is not a height, and 245 showed a mount can lead
    # a phrase, so nobody knows what a mount does with a horizontal.
    ("C4-behind", "Taken from directly behind her",
     "control, the directed behind - under the candid look here"),
    ("C5-shoulder", "Taken from behind her left shoulder, her back three-quarters to the camera",
     "control, the directed shoulder - under the candid look here"),
    ("N7-shelf-behind", "Phone propped on the shelf behind her, facing her back",
     "a mount with a horizontal, asking for behind"),
    ("N8-hand-behind", "Phone in his hand behind her, pointed at her back",
     "his hand rather than a surface, asking for behind"),
    ("N9-hand-shoulder", "Phone in his hand just behind her left shoulder, pointed past it",
     "his hand, asking for the shoulder three-quarter"),
    ("N10-mirror-behind", "Mirror selfie with her back to the mirror, looking over her shoulder "
                          "at the phone",
     "the mirror form turned around - the only one of these a phone really does"),
    ("N11-armslength-behind", "Phone held out behind her at arm's length, pointed back at her",
     "the reversed selfie, asking for behind with no surface and no second person"),
    ("N12-shelf-shoulder", "Phone propped on a shelf behind her left shoulder",
     "a mount with a horizontal and no verb - the shortest form that could work"),
    # Session 251. The shoulder is the one family candid keeps in the
    # photographer's wording, and CANDID_POSITIONS has only its left side: the
    # right is verified for `directed` (session 244) and has never been shot under
    # this look. Shot with `--subject neutral`, which is what the left needed.
    ("N13-shoulder-right", "Taken from behind her right shoulder, her back three-quarters to the "
                           "camera",
     "the mirror of the only shoulder candid has - a seventh form if it renders"),
    # Session 252. The phone wording of the shoulder died on the LEFT, twice -
    # `Phone in his hand just behind her left shoulder` 0/3 in 249 and `Phone
    # propped on a shelf behind her left shoulder` 0/3 in 250 - which is where
    # the rule that a mount reaches a height and never a horizontal comes from.
    # Session 244 found that the two shoulders are a different photograph and not
    # a different wording, so the right is not assumed to inherit the left's
    # failure any more than it inherited its success.
    ("N14-shelf-shoulder-right", "Phone propped on a shelf behind her right shoulder",
     "the mount form of the shoulder, on the side that has never been asked"),
    ("N15-hand-shoulder-right", "Phone in his hand just behind her right shoulder, pointed past it",
     "the hand form, same side - the pair N9/N12 mirrored"),
]


def prompt_for(camera: str, framing: str = FRAMING, look: str = LOOK, rest: str = REST,
               tail: str = "") -> str:
    return f"zchar_jir.\n\n{look}\n\nAngle & Framing:\n{camera}, {framing}{tail}.\n{rest}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--dry-run", action="store_true", help="print the arms and write nothing")
    ap.add_argument("--framing", choices=tuple(FRAMINGS), default="waist")
    ap.add_argument("--tail", choices=tuple(TAILS), default="none",
                    help="`careless` hangs the shipped careless-framing clause on the framing")
    ap.add_argument("--subject", choices=tuple(SUBJECTS), default="front",
                    help="`neutral` stops the Subject block naming which side of her the "
                         "garments are seen from")
    ap.add_argument("--look", choices=tuple(LOOKS), default="candid",
                    help="`directed` drops the candid capture clause and changes nothing else")
    ap.add_argument("--only", default="", help="comma-separated arm labels, by their prefix "
                                               "(`C3,N3` for the two floor arms)")
    args = ap.parse_args()

    framing = FRAMINGS[args.framing]
    look = LOOKS[args.look]
    rest = SUBJECTS[args.subject]
    tail = TAILS[args.tail]
    wanted = tuple(w.strip() for w in args.only.split(",") if w.strip())
    # By the CODE before the first dash, not by prefix: `--only N1` matched N1,
    # N10 through N15 and built a 21-photograph session out of a 3-photograph
    # question (session 255, left unrun).
    arms = [a for a in ARMS if not wanted or a[0].split("-")[0] in wanted]
    if not arms:
        print(f"no arm matches --only {args.only}")
        return 1

    shots = [
        {"label": f"{label}-s{i + 1}", "prompt": prompt_for(camera, framing, look, rest, tail), "verbatim": True,
         "seed": seed, "count": 1}
        for label, camera, _ in arms
        for i, seed in enumerate(SEEDS)
    ]

    for label, camera, why in arms:
        print(f"{label:<22} {camera}\n{'':<22} ({why})")
    print(f"\n{len(arms)} arms x {len(SEEDS)} seeds = {len(shots)} photographs, "
          f"{args.framing} crop, {args.look} look, {args.subject} subject, {args.tail} tail")

    if args.dry_run:
        print("\n--- the prompt of the first arm ---")
        print(prompt_for(arms[0][1], framing, look, rest, tail))
        return 0

    out = create_session(
        args.base,
        "CANDID CAMERAS - 6 formas de telefono contra 3 controles, 3 seeds"
        + (f" [{args.framing}/{args.look}/{args.subject}/{args.tail}, {args.only}]"
           if wanted or args.framing != "waist" or args.look != "candid" or args.subject != "front" or args.tail != "none" else ""),
        shots, SETTINGS)
    print(f"\nsession {out['id']} created as a draft, {len(shots)} pending")
    print(f"run it with: curl -X POST {args.base}/api/sessions/{out['id']}/run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
