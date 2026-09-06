"""Shoot the same two rooms twice: once the way AmazingDraw assembles a line,
once the way this repo composes one. Twelve photographs, three shared seeds.

AmazingDraw is another ComfyUI console driving this same Moody graph. Its
pipeline is a scene library plus an LLM director:

  1. it picks a scene library by weight, then one entry inside it by weight, and
     hands the card exactly ONE string from that entry - `scene_theme`. The
     entry's `pose_hint`, `prop_hint` and `lighting_hint` never reach the
     prompt; they are advice its director model reads while writing the slots.
  2. the director fills twelve optional slots plus `body_shape` and closes with
     a `story_elevation` sentence.
  3. the slots are joined into one flat comma-separated English line.

Arm A replays that: the scene entries below are verbatim from its libraries, and
the slot text is what a director writes from those hints. The join order is the
slot order its own code lists (`card_cli_commands.optional_slots`, with the two
required slots first and the narrative last) - the joiner itself is compiled, so
that ordering is a reconstruction and the one thing here that is not verbatim.

Arm B is the same room, the same clothes and the same act, written in this
repo's blocks with a catalogue camera and a catalogue framing.

The two arms differ in one structural way, which is the question: arm A carries
its camera position only inside the pose sentence, because the scene entry has
no camera field at all. Session 251 measured that 83 frames came back frontal
when no line said which side of her the camera was on
([[idevgen-camera-position-rule]]), so a method that leaves the position to
whatever the pose sentence happens to say is worth one afternoon of frames.

The wedding room is the harder half. Its `scene_theme` names a second body (a
bridesmaid) inside the room string, and the two-person attractor is measured
here ([[idevgen-two-person-attractor]]): a line has to name both bodies and say
two are in frame or the sampler answers with its own default. Arm A hands that
job to a subordinate clause in the middle of a room description.

Usage: python scripts/shoot_amazingdraw_method.py [--base URL] [--dry-run]
The session is created as a draft and NOT run - start it from the app, or POST
/api/sessions/{id}/run, after reading the shot list it prints.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

TRIGGER = "zchar_jir"

# ── Arm A: the flat line, assembled the way the other console assembles one ──
#
# `scene_theme` is copied character for character from its libraries so the one
# field it actually ships is not quietly improved here. Everything after it is
# director text written from that entry's hints.

SCENE_THEME_SUPPLY = (
    "medical supply room, boxed gloves and gauze shelves, hidden erotic tension "
    "among sterile inventory aisles"
)

SCENE_THEME_WEDDING = (
    "inside a luxurious dimly lit wedding dressing room, bride standing in front of a "
    "grand glowing vanity mirror, wearing an exquisite white lace wedding gown that has "
    "been unzipped and draped dangerously low, wide open back down to her lower waist, "
    "exposing smooth bare back skin and the soft side curve of her breast, bridesmaid "
    "standing behind gently adjusting the delicate white lace straps, soft vanity bulb "
    "reflections, scattered silk flower petals and champagne glasses on the marble "
    "tabletop, cinematic and intimate ambiance"
)

# The twelve slots plus the two required ones, in the order its code lists them:
# body_shape, scene_theme, then lighting, clothing, pose, expression_gaze,
# style_quality, makeup_hair, accessories, imperfections, tattoo, props, pet,
# liquids - and story_elevation last. Empty slots are simply absent, which is
# what its cards do too.
ARM_A = {
    "supply": [
        # body_shape - the `library-petite` profile, run through its own
        # exposure filter: `half_nude` with an upper-body focus is the case
        # where `_strip_genital_detail_keep_anchor` drops the lower-body words.
        "young East Asian girl, slender intellectual figure, small perky breasts with "
        "delicate soft rosy-pink nipples, pale skin, visible collarbones, quiet library "
        "beauty",
        SCENE_THEME_SUPPLY,
        # lighting
        "narrow stockroom strip light, shadow pockets between shelves",
        # clothing
        "white lab coat hanging open and unbuttoned, nothing underneath, coat sleeves "
        "pushed up to the elbows",
        # pose - written from the entry's pose_hint, which is the only place the
        # camera position can enter an arm-A line at all
        "crouching by the lower shelf in the narrow aisle, looking back over her right "
        "shoulder toward the camera behind her",
        # expression_gaze
        "looking back at viewer, lips softly parted, caught mid-turn",
        # style_quality
        "candid DSLR photography, casual lifestyle snapshot, photorealistic",
        # makeup_hair
        "no-makeup look, clean skin, hair tied back in a low ponytail",
        # accessories
        "thin wire-frame glasses",
        # imperfections
        "faint red pressure line on her forearm from the shelf edge, small mole on her "
        "left shoulder blade",
        # props
        "supply shelves, stacked linen packs, glove boxes, rolling cart",
        # story_elevation
        "she stayed in the stockroom long after her shift ended and turns at a sound in "
        "the corridor, the strip light catching one side of her face",
    ],
    "wedding": [
        # body_shape - the `college-voluptuous` profile, same exposure filter
        "young East Asian college girl, perfect hourglass figure, large full breasts "
        "with perky soft rosy-tan nipples, smooth bare back, porcelain skin",
        SCENE_THEME_WEDDING,
        "warm golden vanity bulbs casting soft glow on her bare spine curves, cinematic "
        "side lighting on the lace",
        "white lace wedding gown unzipped and pushed down off both shoulders, the open "
        "back running to her lower waist, gown held to her chest from the front",
        "standing with her back to the camera in front of the vanity, head turned "
        "slightly to look at her own reflection in the glowing mirror",
        "looking at her reflection, calm and unhurried, lips closed",
        "35mm film camera photography, cinematic color grading, photorealistic",
        "bridal updo with loose strands at the nape, soft romantic makeup",
        "pearl drop earrings, thin gold bracelet",
        "faint zipper mark down her spine, goosebumps along her bare arms",
        "grand vanity mirror with bright round bulbs, marble makeup table, crystal "
        "champagne glasses, scattered silk flower petals",
        "the gown came down twenty minutes before the ceremony and nobody has said yet "
        "whether it is going back on",
    ],
}

# ── Arm B: the same two photographs, composed the way this repo composes ──
#
# Catalogue wordings, verbatim: the camera clause and the crop are rows, not
# prose. The room moves into the look, where this app keeps the place and the
# light, and the act, the wardrobe and the expression each get their own block.

LOOK_SUPPLY = (
    "She wears her hair tied back in a low ponytail, with no makeup on her clean skin "
    "and thin wire-frame glasses. She is in a hospital supply stockroom, a narrow aisle "
    "running between tall metal shelves stacked with boxed gloves and gauze and folded "
    "linen, a rolling cart parked against one shelf, a single strip light overhead "
    "leaving pockets of shadow between the racks."
)

LOOK_WEDDING = (
    "She wears her hair in a bridal updo with loose strands fallen at the nape, with "
    "soft romantic makeup. She is in a dim wedding dressing room, a tall vanity mirror "
    "ringed with warm round bulbs on the far wall, a marble table beneath it holding "
    "crystal champagne glasses and scattered silk flower petals, the rest of the room "
    "falling away into shadow."
)

# Row `shoulder-right`, verified 3/3 in session 251, and row `crop-full-body`.
CAMERA_SUPPLY = "Taken from behind her right shoulder, her back three-quarters to the camera"
# Row `behind-direct`, verified 3/3.
CAMERA_WEDDING = "Taken from directly behind her"
FRAMING = "full body"

REST_SUPPLY = """
Pose:
She is crouching low in the narrow aisle beside the bottom shelf, her weight on the \
balls of both feet and her knees folded up in front of her, her left hand flat on the \
floor for balance and her right hand resting on her right knee, her head turned back \
over her right shoulder toward the camera.

Subject:
The white lab coat hangs open down her front, its two panels falling to either side of \
her bare torso, the sleeves pushed up to her elbows. Nothing is worn underneath it. The \
hem of the coat pools on the floor around her folded knees.

Outfit & Texture:
She wears the open white lab coat with its sleeves pushed to the elbows and the thin \
wire-frame glasses, and nothing else.

Expression:
Her expression is caught mid-turn and alert, her lips softly parted."""

REST_WEDDING = """
Pose:
She stands still on the floor in front of the vanity with her weight even on both feet, \
her back straight, her head turned a little to her left so her face is in three-quarter \
profile toward the mirror, both her hands holding the front of the gown up against her \
chest.

Subject:
The white lace wedding gown is unzipped down to her lower waist and pushed off both \
shoulders, its open back leaving her spine and shoulder blades bare from the nape to \
the small of her back, the loosened straps hanging at her upper arms. She holds the \
front of the gown to her chest with both hands. The skirt of the gown falls closed to \
the floor.

Outfit & Texture:
She wears the white lace wedding gown unzipped to the lower waist and pushed off both \
shoulders, held to her chest at the front, with the pearl drop earrings and a thin gold \
bracelet.

Expression:
Her expression is calm and unhurried, her lips closed."""

ARM_B = {
    "supply": (LOOK_SUPPLY, CAMERA_SUPPLY, REST_SUPPLY),
    "wedding": (LOOK_WEDDING, CAMERA_WEDDING, REST_WEDDING),
}

# Session 231's three, so a frame from this batch can be laid beside one of its
# arms on the same noise.
SEEDS = [399966242, 111222333, 777888999]

SETTINGS = {
    "width": 832, "height": 1216, "steps": 8, "cfg": 1, "lora_strength": 1,
    "checkpoint": "moodyKrea2Mix_v70.safetensors", "kind": "shoot",
    "sampler": "euler_ancestral", "scheduler": "beta",
    # Both arms write their own room, so the session must not prepend one.
    "use_look": False,
}


def prompt_arm_a(room: str) -> str:
    """One flat comma-separated line, which is the shape that console ships."""
    return TRIGGER + ", " + ", ".join(ARM_A[room])


def prompt_arm_b(room: str) -> str:
    look, camera, rest = ARM_B[room]
    return f"{TRIGGER}.\n\n{look}\n\nAngle & Framing:\n{camera}, {FRAMING}.\n{rest}"


ARMS = [
    ("A-supply-flat", "supply", prompt_arm_a,
     "their method: one flat line, camera only inside the pose clause"),
    ("B-supply-blocks", "supply", prompt_arm_b,
     "ours: catalogue camera row, catalogue crop, blocks"),
    ("A-wedding-flat", "wedding", prompt_arm_a,
     "their method, and the room string names a second body"),
    ("B-wedding-blocks", "wedding", prompt_arm_b,
     "ours: one body, camera row `behind-direct`"),
]


def create_session(base: str, name: str, shots: list, settings: dict, look: str = "") -> dict:
    """The draft, posted once. Same idiom as `shoot_camera_forms.py`.

    No retry, deliberately: a reset can arrive after the server has already
    inserted the session, and retrying this call is what made sessions 232 and
    233 as duplicate drafts.
    """
    body = {"model_id": 1, "workflow_id": 8, "name": name,
            "look": look, "wardrobe": "", "settings": settings, "shots": shots}
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
        {"label": f"{label}-s{i + 1}", "prompt": build(room), "verbatim": True,
         "seed": seed, "count": 1}
        for label, room, build, _ in ARMS
        for i, seed in enumerate(SEEDS)
    ]

    for label, room, build, why in ARMS:
        words = len(build(room).split())
        print(f"{label:<20} {words:>4} words   ({why})")
    print(f"\n{len(ARMS)} arms x {len(SEEDS)} seeds = {len(shots)} photographs")

    if args.dry_run:
        for label, room, build, _ in ARMS:
            print(f"\n--- {label} ---\n{build(room)}")
        return 0

    out = create_session(args.base,
                         "AMAZINGDRAW METHOD - flat director line vs composed blocks, 2 rooms, 3 seeds",
                         shots, SETTINGS)
    print(f"\nsession {out['id']} created as a draft, {len(shots)} pending")
    print(f"run it with: curl -X POST {args.base}/api/sessions/{out['id']}/run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
