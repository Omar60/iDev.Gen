"""AmazingDraw's whole system on this bench: its four camera families, its
ordinary rooms, its SM and abstract libraries - plus one control that answers
what broke our own lab coat.

Session 391 ran two of its rooms against two of ours and found the split: its
`scene_theme` builds a better room than our `look` does, and its two missing
fields (camera, crop) cost it 3/3 frontal frames and 6/6 knee-up crops. What 391
did not run is the library that holds the missing camera - `perspective_scenes`,
55 entries its own config ships at weight 0, in four families:

    rear-entry POV  rear view, 9 entries      stockings POV  low leg-focused POV, 14
    facial POV  facial POV, 20            fisheye POV  fisheye, 12

Those entries are a different shape from the rest of its libraries. They fuse
camera, act and room into one templated string with `{pose}`, `{clothing}` and
`{liquids}` holes in it, and 29 of the 55 carry such a hole. That is the whole
answer to "where is their camera field": there is no camera field, there is a
camera-shaped room. Arms P1-P4 shoot one entry from each family so the four can
be judged on where the camera actually landed.

Arms G1, G2, S1 and X1 are its ordinary draw - two general rooms, an SM booth
and an abstract space - to see whether 391's room advantage holds outside the
two rooms 391 happened to draw. G2 is the interesting one: its `scene_theme`
names a queue of shoppers, so it asks the two-person question again with a crowd
instead of a bridesmaid.

W-supply-short is the control and the only arm that is ours. It is
B-supply-blocks from 391 byte for byte, with one change: the wardrobe collapsed
from 45 words to their register. 391's lab coat came back folded into shapes no
garment makes, and the two candidate causes were the length of our Subject block
and the full-body crop spending its pixels elsewhere. Holding the crop and
changing only the length is what separates them.

Everything school-coded is out of the pool - 9 of the 55 perspective entries,
all of `school_scenes`, and every `jc-*`/`jk-*` profile in `amateurs.json` -
which is that app's own `restrict_roles` switch turned on.

Usage: python scripts/shoot_amazingdraw_full.py [--base URL] [--dry-run]
The session is created as a draft and NOT run - start it from the app, or POST
/api/sessions/{id}/run, after reading the shot list it prints.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

TRIGGER = "zchar_jir"

# ── Arm W: ours, from session 391, wardrobe shortened and nothing else ──

LOOK_SUPPLY = (
    "She wears her hair tied back in a low ponytail, with no makeup on her clean skin "
    "and thin wire-frame glasses. She is in a hospital supply stockroom, a narrow aisle "
    "running between tall metal shelves stacked with boxed gloves and gauze and folded "
    "linen, a rolling cart parked against one shelf, a single strip light overhead "
    "leaving pockets of shadow between the racks."
)
CAMERA_SUPPLY = "Taken from behind her right shoulder, her back three-quarters to the camera"
FRAMING = "full body"

# 391's Pose block verbatim. Only the two wardrobe blocks below it changed.
REST_SUPPLY_SHORT = """
Pose:
She is crouching low in the narrow aisle beside the bottom shelf, her weight on the \
balls of both feet and her knees folded up in front of her, her left hand flat on the \
floor for balance and her right hand resting on her right knee, her head turned back \
over her right shoulder toward the camera.

Subject:
She wears a white lab coat hanging open and unbuttoned with nothing underneath, its \
sleeves pushed up to the elbows.

Outfit & Texture:
The open white lab coat with its sleeves pushed to the elbows, and the thin wire-frame \
glasses.

Expression:
Her expression is caught mid-turn and alert, her lips softly parted."""


def prompt_control() -> str:
    return (f"{TRIGGER}.\n\n{LOOK_SUPPLY}\n\nAngle & Framing:\n"
            f"{CAMERA_SUPPLY}, {FRAMING}.\n{REST_SUPPLY_SHORT}")


# ── Arms P/G/S/X: their system, their slot order, their join ──
#
# `scene_theme` is verbatim from the libraries. The `{pose}`, `{clothing}` and
# `{liquids}` holes are filled the way its director fills them - from the
# entry's own `pose_hint` and `uniform_fit`, and for `{liquids}` from the
# deposition rule in its CHECK_PITFALLS (static adherence, face and upper chest,
# nothing at the mouth or the chin).
#
# Order: body_shape, scene_theme, then lighting, clothing, pose,
# expression_gaze, style_quality, makeup_hair, accessories, imperfections,
# tattoo, props, pet, liquids, and story_elevation last.

ARMS_A = {
    # ── rear-entry POV, entry `workplace-ceo-window-bend` ──
    "P1-rear-ceo-window": [
        # office-director, exposure `lower`: the filter drops the upper-body words
        "mature East Asian executive woman, age 31, elegant hourglass figure with "
        "defined waist and shapely hips, long toned legs, warm smooth skin, sleek "
        "shoulder-length hair",
        "luxurious executive office at night, bending over in front of the "
        "floor-to-ceiling glass window, hands flat on the cool glass, tight pencil "
        "skirt hiked up over her hips, looking back over her shoulder, back to viewer, "
        "hips and thighs as the natural focal point, ultra-thin oily black sheer "
        "stockings with a silky gloss on smooth thighs, cityscape night lights visible "
        "outside, natural skin texture, soft reflections on glass pane",
        "dim office accent lights, soft colorful glow from cityscape window lights "
        "casting reflections on skin",
        "office lady blouse still buttoned at the front, tight pencil skirt hiked up "
        "over her hips, black sheer stockings, heels",
        "bending forward from the hips with both hands flat on the glass, feet apart, "
        "back arched, looking back over her right shoulder toward the camera behind her",
        "looking back at viewer over her shoulder, a sly half-smile",
        "50mm lens photography, photorealistic, cinematic night interior",
        "sleek shoulder-length hair falling forward, sharp brows, deep red lips",
        "thin gold watch, black stiletto heels",
        "faint red pressure marks where her palms press the cold glass",
        "large floor-to-ceiling glass window, heavy office desk, rolling leather "
        "executive chair, plush beige wool carpet",
        "the last meeting ended an hour ago and the floor below is dark, and she is "
        "still at the window with the city on the other side of the glass",
    ],
    # ── facial POV, entry `workplace-ceo-desk` ──
    "P2-facial-ceo-desk": [
        # ol-rookie, exposure `upper`: the filter drops the lower-body words
        "young East Asian office lady, age 22, fresh graduate with soft slim figure, "
        "full C cup breasts, soft rosy-pink nipples, light soft pinkish-tan areolae, "
        "narrow waist",
        "luxurious executive office at night, kneeling on the plush wool carpet beside "
        "the heavy mahogany desk, looking up into the high-angle camera, high angle view "
        "from above (pov), cityscape night lights visible through floor-to-ceiling "
        "windows behind, a soft translucent deposit resting on one cheekbone and across "
        "her collarbone, chin and mouth clean",
        "dim office accent lights, soft colorful glow from cityscape window lights "
        "casting reflections on skin",
        "white office blouse unbuttoned and hanging open at the front, pencil skirt "
        "still on, sheer black pantyhose",
        "kneeling upright on the carpet beside the desk with her thighs together and "
        "her hands resting on her thighs, her chin lifted toward the camera above her",
        "looking up at viewer, eyes open and steady, lips closed",
        "85mm portrait lens, photorealistic, cinematic night interior",
        "tidy low ponytail with strands come loose, clean natural makeup",
        "company lanyard still around her neck",
        "flushed cheeks, faint carpet marks on her shins",
        "heavy mahogany office desk, rolling leather executive chair, plush beige wool "
        "carpet, large floor-to-ceiling glass window",
        # liquids - their own deposition rule
        "a soft translucent deposit sitting static on one cheekbone and a short thin "
        "line along the collarbone, nothing at the mouth, nothing on the chin",
        "her first month on the floor ended twenty minutes ago and nobody has come back "
        "up from the lobby",
    ],
    # ── stockings POV, entry `luxury-car-dash-silk` ──
    "P3-silk-car-dash": [
        # flight-attendant, exposure `lower`
        "young East Asian woman, age 25, slender toned figure with elegant posture, long "
        "slim legs, small beauty mark on left collarbone, silky straight black hair in a "
        "neat bun",
        "luxurious car interior at night, girl reclining deeply in the front passenger "
        "seat on the right, both legs raised high with feet resting against the "
        "windshield in the foreground, feet-first POV from dashboard level looking back "
        "toward her reclining body, no steering wheel in front of her, the steering "
        "wheel visible on the opposite left side, ultra-thin sheer black stockings "
        "stretching from her hips to the glass, streetlights through the windshield "
        "creating rhythmic moving highlights on her legs, leather seat texture in the "
        "background, a sense of high-end excitement",
        "passing streetlight bands sweeping across her legs, dashboard glow from below",
        "sheer black stockings, a short skirt pulled up at the hip, blouse open at the "
        "collar",
        "reclining deep in the passenger seat with both legs raised and her feet flat "
        "against the windshield, knees together, one hand on the door armrest",
        "looking down the length of her own legs toward the camera, calm and amused",
        "35mm film camera photography, cinematic color grading, photorealistic",
        "black hair in a neat bun with loose strands, light natural makeup",
        "thin gold anklet over the stocking",
        "a small ladder run starting at one knee",
        "leather passenger seat, windshield glass, glove compartment, steering wheel on "
        "the far left side",
        "the car has been parked with the engine off for a while and the street outside "
        "keeps moving without them",
    ],
    # ── fisheye POV, entry `fisheye-elevator-corner` ──
    "P4-fisheye-elevator": [
        # nurse-milk, exposure `half_nude`
        "young East Asian woman with warm gentle face, extremely large heavy E cup "
        "breasts with full round shape, smooth pale warm skin, slim waist contrasting "
        "the huge chest, thick soft thighs",
        "covert CCTV dome camera perspective embedded in the elevator ceiling corner, "
        "8mm fisheye lens, extreme barrel distortion, curved edges, warped perspective, "
        "bulging center, circular vignette, the entire cabin compressed into a spherical "
        "panopticon, two mirrored stainless steel walls facing each other creating an "
        "infinite reflection tunnel that repeats her figure, her real body and curved "
        "mirror doubles both visible in the warped sphere, girl leaning back against the "
        "main mirror wall looking up into the lens, one hand pressing fingertips to the "
        "glass touching her reflection, stainless steel handrail sweeping huge across "
        "the lower frame edge, warm orange digital floor indicator glowing at the top "
        "edge of the sphere above the door line, red and green call button lights as "
        "small glowing dots beside the handrail, a fitted office dress unzipped at the "
        "front, cool metallic LED atmosphere with warm digital accents, specular blooms "
        "from multiple mirror reflections, skin texture, forbidden private midnight "
        "elevator moment",
        "cool white elevator ceiling LEDs with specular highlights on metal walls, warm "
        "orange floor-indicator glow at the top edge of the sphere",
        "fitted dark office dress unzipped down the front and pulled off both shoulders, "
        "held at the waist",
        "leaning back against the mirror wall directly under the ceiling corner, chin "
        "lifted, looking straight up into the lens, one hand flat on the mirror beside "
        "her, hips angled",
        "looking up into the lens, aware of the camera, lips softly parted",
        "CCTV footage, security camera style, surveillance camera view, 8mm fisheye, "
        "photorealistic",
        "hair pinned up with strands fallen loose, soft everyday makeup",
        "hospital ID badge clipped at her waist",
        "faint fogged handprint left on the mirror beside her",
        "stainless steel handrail sweeping huge across the lower frame edge, mirrored "
        "cabin walls, floor button panel with glowing red and green call buttons",
        "the cabin has been sitting between floors for a minute and she has stopped "
        "pretending she does not know where the lens is",
    ],
    # ── general_scenes, `general-rainy-night-bus-stop` ──
    "G1-bus-stop-rain": [
        "young East Asian woman, age 25, slender toned figure with elegant posture, "
        "well-proportioned C cup round perky breasts, beautiful collarbone line, long "
        "slim legs, small beauty mark on left collarbone",
        "standing under a bus shelter on a rainy night, holding a transparent umbrella, "
        "vibrant neon advertisement light reflecting on wet asphalt and puddles, "
        "raindrops suspended in the glowing air, moody reflection, wet clothing clinging "
        "outline highlighting chest and hips curves",
        "vibrant multi-colored neon advertisement bokeh, headlights of oncoming cars "
        "illuminating suspended rain mist",
        "a thin white blouse soaked through and clinging, a dark skirt, bare legs, flat "
        "shoes",
        "standing under the shelter roof holding the umbrella handle with both hands, "
        "weight on one hip, turned a little toward the road",
        "looking off toward the oncoming headlights, calm, lips closed",
        "anamorphic lens cinematic photography, photorealistic, night street",
        "black hair loose and rain-damp, light natural makeup slightly run",
        "transparent vinyl umbrella, a small shoulder bag",
        "goosebumps along her forearms, wet hair stuck to her neck",
        "neon bus shelter ad billboard, wet metallic bench, puddles on the asphalt",
        "the last bus is late and the shelter roof has been leaking on her for ten "
        "minutes",
    ],
    # ── general_scenes, `general-crowded-checkout-line` - the crowd question ──
    "G2-checkout-queue": [
        "young East Asian college girl, perfect hourglass figure, large full breasts "
        "with perky soft rosy-tan nipples, wide hips, campus beauty",
        "long supermarket checkout queue, belt conveyor, impulse-buy rack, fluorescent "
        "cash register zone, shoppers stacked closely with baskets",
        "bright checkout strip light, scanner glow and glossy floor bounce",
        "an oversized knit cardigan slipping off one shoulder over a thin camisole, a "
        "short skirt",
        "standing in the long queue with the basket held low at her thigh, weight "
        "shifted to one hip, the crowd compressed close behind her",
        "looking down at the basket, faintly flustered, lips closed",
        "candid DSLR photography, casual lifestyle snapshot, photorealistic",
        "hair in a quick clip, no-makeup look",
        "shopping basket, phone in her free hand",
        "flushed ears, a strap mark on the bare shoulder",
        "checkout divider bar, conveyor belt, impulse-buy rack, other shoppers blurred "
        "close behind",
        "she has been in this queue eleven minutes and the person behind her has not "
        "left any room since the third",
    ],
    # ── sm_scenes, `sm-exhibition-curtain-booth` ──
    "S1-curtain-booth": [
        "young East Asian girl, healthy medium figure, medium breasts with gentle soft "
        "rosy-tan nipples, slightly puffy areolae, clean skin, warm caring face",
        "curtain booth, half-open drapes, layered visibility and intimate consensual "
        "viewing tension in an elegant SM club corner",
        "soft booth lamp, curtain-filtered side glow",
        "sheer mesh chiffon robe slipping off both shoulders, a collar with a small "
        "bell, satin heels",
        "standing framed in the curtain gap with one hand holding the drape open, weight "
        "on one leg, shoulders turned a little away",
        "looking toward the gap, chin slightly lowered, lips closed",
        "50mm lens photography, dramatic cinematic art portrait, photorealistic",
        "hair loose over one shoulder, soft evening makeup",
        "thin collar with a small bell, satin heels",
        "the collar sitting close enough to leave a faint line on her neck",
        "half-open drapes, small booth sofa, service table",
        "the curtain has been open exactly this far for a while and neither side has "
        "moved it",
    ],
    # ── special_scenes, `special-snowflake-space` - a room with no depth ──
    "X1-snowflake-space": [
        "mature East Asian woman, age 29, elegant slender figure with subtle curves, "
        "medium full breasts with soft natural rosy-tan nipples, delicate collarbones, "
        "long graceful neck, warm amber-toned smooth skin",
        "inside a vast hexagonal ice-crystal chamber formed by one giant snowflake "
        "structure, six thick translucent ice arms radiating far around the body as "
        "architecture not costume, ice-blue prism light refracting through facets at "
        "different depths, fine glittering snow crystals drifting slowly in mid-air at "
        "multiple depth layers around the figure, frost blooming only where skin truly "
        "touches an ice arm, elsewhere only a faint cold mist sheen on bare skin, breath "
        "vapor in freezing air, no wearable snowflake ornaments",
        "ice-blue refraction through the hexagonal crystal structure, fine drifting snow "
        "crystals catching pinpoint highlights at different depths",
        "nothing but a thin sheer wrap fallen to her elbows",
        "standing centered in the crystal chamber with one palm flat on a nearby ice "
        "arm, frost spreading from the contact point, her body turned a little toward "
        "the prism light",
        "looking toward the prism light, mouth softly open, breath vapor visible",
        "medium format photography, classic film look, photorealistic",
        "hair loose and dusted with fine crystals, bare face",
        "none",
        "goosebumps across her shoulders, skin drawn tight with cold",
        "a distant six-fold ice arm behind her with clear depth separation, a thin melt "
        "droplet on her collarbone",
        "she has been in the chamber long enough that the frost under her palm has "
        "stopped spreading",
    ],
}

# Their line is flat and comma-joined; ours is not. `verbatim` keeps the app from
# prepending anything to either.
SEEDS = [399966242, 111222333]
SEEDS_CONTROL = [399966242, 111222333, 777888999]

SETTINGS = {
    "width": 832, "height": 1216, "steps": 8, "cfg": 1, "lora_strength": 1,
    "checkpoint": "moodyKrea2Mix_v70.safetensors", "kind": "shoot",
    "sampler": "euler_ancestral", "scheduler": "beta",
    "use_look": False,
}

WHY = {
    "P1-rear-ceo-window": "rear-view family: the camera lives in the room string",
    "P2-facial-ceo-desk": "facial family, and a high-angle POV nobody has measured here",
    "P3-silk-car-dash": "leg-POV family: feet-first, the hardest geometry of the four",
    "P4-fisheye-elevator": "fisheye family, and two mirror walls asking for one body",
    "G1-bus-stop-rain": "an ordinary room, to see if 391's room advantage holds",
    "G2-checkout-queue": "its room string names a crowd - the two-person question again",
    "S1-curtain-booth": "the SM library, whose entries are short where general's are long",
    "X1-snowflake-space": "an abstract space with no depth, which is our framing control",
}


def prompt_arm_a(key: str) -> str:
    return TRIGGER + ", " + ", ".join(ARMS_A[key])


def create_session(base: str, name: str, shots: list, settings: dict, look: str = "") -> dict:
    """The draft, posted once. Same idiom as `shoot_camera_forms.py`.

    No retry, deliberately: a reset can arrive after the server has already
    inserted the session, and retrying this call is what made sessions 232 and
    233 as duplicate drafts. Session 391 hit exactly that reset and landed
    anyway - check the session list before running this twice.
    """
    body = {"model_id": 1, "workflow_id": 8, "name": name,
            "look": look, "wardrobe": "", "settings": settings, "shots": shots}
    req = urllib.request.Request(base + "/api/sessions",
                                 json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--dry-run", action="store_true", help="print the arms and write nothing")
    args = ap.parse_args()

    shots = [{"label": f"W-supply-short-s{i + 1}", "prompt": prompt_control(),
              "verbatim": True, "seed": seed, "count": 1}
             for i, seed in enumerate(SEEDS_CONTROL)]
    shots += [{"label": f"{key}-s{i + 1}", "prompt": prompt_arm_a(key),
               "verbatim": True, "seed": seed, "count": 1}
              for key in ARMS_A
              for i, seed in enumerate(SEEDS)]

    print(f"{'W-supply-short':<22} {len(prompt_control().split()):>4} words   "
          f"(control, ours: 391's line with a 45-word wardrobe cut to 18)")
    for key in ARMS_A:
        print(f"{key:<22} {len(prompt_arm_a(key).split()):>4} words   ({WHY[key]})")
    print(f"\n{len(ARMS_A)} of their arms x {len(SEEDS)} seeds "
          f"+ 1 control x {len(SEEDS_CONTROL)} = {len(shots)} photographs")

    if args.dry_run:
        print("\n--- the control ---\n" + prompt_control())
        for key in ARMS_A:
            print(f"\n--- {key} ---\n{prompt_arm_a(key)}")
        return 0

    out = create_session(args.base,
                         "AMAZINGDRAW FULL - 4 camera families, 4 rooms, and the wardrobe control",
                         shots, SETTINGS)
    print(f"\nsession {out['id']} created as a draft, {len(shots)} pending")
    print(f"run it with: curl -X POST {args.base}/api/sessions/{out['id']}/run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
