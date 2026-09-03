"""Which of the new directed poses does this checkpoint refuse outright?

Written 2026-09-03, the day the poses were. Two screens, `--screen floor` and
`--screen glamour`, and the second exists because the first was wrong about
something the code could not catch.

`floor` (session 365) enumerated what CARRIES THE BODY'S WEIGHT -- standing,
seated, kneeling, all-fours, crouching. That axis produces a gymnastics
catalogue, and the photographs were correct and ugly. Its three best frames
were `bed-all-fours`, `bed-edge` and `table-hands-flat`: bed, edge, table.

`glamour` (session 366) enumerates the repertoire of a nude shoot instead --
the line of the body, the body on a bed, the body against a surface that
pushes back, and the hands as the subject. 9 of its 14 rows carry furniture or
a wall on the strength of that result, and none names a foot.

The enumeration is the authorship, and it is where the first batch went wrong.
Three models handed the same cell list returned the same geometry reconjugated
([[idevgen-minimax-as-author-rejected]]), so the axis is never the outside
model's contribution to make.

Either way this is a SCREEN, not a measurement: one photograph per row, n=1.
[[idevgen-writer-run-noise]] is the standing reason to say so out loud: the
writer's own spread at n=25 is 5-6 points, so a single frame settles nothing
about a row that renders. What n=1 *can* do is find the rows the sampler refuses
flatly -- the way six wordings at three seeds came back 18/18 sitting up
([[idevgen-on-her-back-unreachable]]) -- and those need no second opinion. A row
that comes back right here has earned an anchor cell of ten; a row that comes
back as a different photograph is a candidate for deletion, not for a re-shoot.

## The bench

Everything but the act is held at one value, which is session 308's protocol
([[idevgen-the-bench-and-the-floor]]) and the reason a failure is attributable:

    trigger    zchar_jir
    wardrobe   the camisole and knickers of session 364 -- or nothing at all
               under `--no-wardrobe`, which is the only honest bench for a row
               that needs access: a hand cupped over her breast drawn over a
               written camisole is the contradiction the gate exists to stop
    camera     `side view`   -- from the catalogue, and deliberately the weak form
    framing    `full body`   -- the only crop legal for rows naming heels and ankles
    checkpoint Moody-Krea-Mix-premium, which is the bench ([[idevgen-premium-is-the-bench]])
    workflow   8, the 25-second preview graph

The camera is a two-word term because that is what directed's catalogue holds --
25 of its 49 cameras are shot sizes and the rest are terms, not positions
([[idevgen-directed-catalogue-gaps]]). A weak camera is the right choice here
anyway: it leaves the act as the only clause with anything to say about the
body, and [[idevgen-crop-terms-as-cameras]] is why the framing cannot be
anything tighter than the whole body when a row names her heels.

`full body` is a term of art and the crop law reads the lowest named part, so
every one of these lines is legal by construction. Nothing is composed: the
prompts are verbatim, which also means the `furniture` gate is not exercised
here -- that has its own unit test, and mixing the two would make a missing
photograph ambiguous between a refusal and a filter.

## Neither screen is a sample

Both pick the rows most likely to DIE, because a screen earns its keep by
deleting. `floor` took all three `crouching` rows (a family the catalogue did
not have; all three came back as one photograph, so it lives with one row) and
the geometries that ask for something hard. `glamour` carries
`bed-back-arched`, which is the seventh wording asking this checkpoint to keep
her shoulders down -- the first six were sat up 18/18
([[idevgen-on-her-back-unreachable]]) -- on the theory that a mattress under
the shoulders is a different condition from a floor.

Judge both by LOOKING. Each photograph asks its own question, so
`judge_camera.py` has nothing to ask: one blind question over a dozen
unrelated geometries is the failure [[idevgen-judge-question-design]] is
written from.

Usage: python scripts/shoot_directed_poses.py [--screen floor|glamour]
                                              [--no-wardrobe] [--dry-run] [--run]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request

TRIGGER = "zchar_jir"
WARDROBE = ("She wears a cream cotton jersey camisole with narrow straps and a "
            "softly rounded neckline, and black cotton knickers.")
CAMERA = "side view"
FRAMING = "full body"

SETTINGS = {
    "width": 832, "height": 1216, "steps": 8, "cfg": 1, "lora_strength": 1,
    "checkpoint": "Moody-Krea-Mix-premium_00002__clean_nvfp4.safetensors",
    "kind": "shoot",
    # The wardrobe is written into every prompt below, so the session must not
    # prepend a look as well.
    "use_look": False,
}

# The concept keys, read out of the seed file at run time so this script cannot
# drift from the catalogue the way the seeds drifted from the store
# ([[idevgen-seed-files-drift]]). A key that is not in the file is a typo here
# and stops the run.
SCREEN = [
    # the new family, all of it
    "crouching-tiptoe",
    "crouching-flat",
    "crouching-one-hand-down",
    # the floor geometries with something to refuse
    "lying-front-arched",
    "sitting-balanced",
    "shoulders-down-hips-up",
    "lying-legs-vertical",
    "standing-one-leg",
    # furniture: does a bed or a chair build at all
    "bed-edge",
    "seat-front-edge",
    "bed-all-fours",
    "table-hands-flat",
]

# The second screen, 2026-09-03: the first batch enumerated what carries the
# body's weight and came back as a gymnastics catalogue. This one is the
# repertoire of a nude shoot -- the line of the body, the body on a bed, the body
# against a surface that pushes back, and the hands as the subject. 9 of its 14
# rows carry furniture or a wall, because bed-all-fours, bed-edge and
# table-hands-flat were the three best frames of session 365.
GLAMOUR = [
    "standing-arch-hands-nape",
    "standing-hip-thrown",
    "kneeling-turned-hand-behind",
    "standing-hand-nape-profile",
    "bed-front-shoulder-down",
    "bed-kneeling-arched-look-back",
    "bed-side-hand-on-hip",
    "bed-edge-folded-forward",
    "bed-back-arched",
    # `wall-hips-forward` was retired after 366: it rendered her FACING the
    # wall, which is `wall-facing-forearms`, and a symmetric pair of palms is
    # what did it. Its geometry lives on as Grok's `wall-casual-arch`.
    "wall-facing-forearms",
    "table-edge-gripped",
    "arm-across-chest",
    "hands-cupping",
]

# Grok's casual axis, 2026-09-03, tried BEFORE being written into the catalogue:
# these are wordings, not concept keys, so nothing is imported until a frame
# earns it. Its list of 14 had 6 that duplicate rows already shot; what is here
# is the 5 genuinely new geometries plus 3 that move the discriminant exactly
# where mine failed in 366 (one hand instead of two, no arch, no look back).
#
# Three of its defects fixed in translation: "mirada baja" is the expression
# field and not the act, the subject is named in the first clause the way every
# directed row does, and no line names a foot.
GROK = {
    "couch-slouched-one-leg":
        "The young woman sits slumped low into a couch with her back lightly "
        "arched, one shoulder dropped below the other, one hand on her thigh and "
        "the other resting on her chest.",
    "couch-leaning-forward":
        "The young woman sits on the edge of a couch with her torso tipped "
        "forward and both elbows planted on her knees, her shoulders rounded and "
        "her head hanging low.",
    "floor-sitting-lean-back":
        "The young woman sits on the floor with her back resting against a wall, "
        "her knees bent up in front of her, her chest lifted and one hand flat on "
        "her belly.",
    "floor-lying-hip-up":
        "The young woman lies on her side on the floor with her upper hip lifted "
        "high, one arm stretched out along the floor and the other hand at her waist.",
    "doorframe-lean":
        "The young woman leans in a doorway with one shoulder against the frame "
        "and one hip pushed out, one hand at her waist and the other arm hanging loose.",
    # the three re-tries
    "standing-hand-in-hair":
        "The young woman stands with her weight shifted onto one hip, one hand "
        "tangled in her hair and the other trailing down her belly.",
    "bed-stomach-propped":
        "The young woman lies on her front on a bed propped on both forearms with "
        "her chest lifted off the mattress, her back arched and her face turned "
        "toward her shoulder.",
    "wall-casual-arch":
        "The young woman stands with her back against a wall and her weight on "
        "one hip, her chest carried forward and one hand sliding up the side of her torso.",
}

# Candid is a DIFFERENT BENCH, not a different act list. Its look describes the
# room (directed's does not exist at all), its wardrobe writes her undressed in
# so many words, its cameras are sentences rather than two-word terms, and its
# rows say "She" where directed names the subject. Screening candid lines on
# directed's bench would measure directed with couches in it.
#
# Taken verbatim off session 357, the most recent real candid shoot.
CANDID_LOOK = (
    "Small sensor, everything at every distance equally in focus and nothing "
    "softened, sensor noise in the shadows, washed-out colour, slight motion blur, "
    "off-center and slightly tilted framing, no studio lighting and no colour "
    "grading. She wears her hair loose, with a few strands pushed behind one ear. "
    "A bare ceiling bulb lights the room from overhead and the window is black "
    "against it. Bare floorboards run away underfoot, a low table stands between "
    "her and the camera with a mug on it, and the far wall carries a half-open "
    "wardrobe door.")

BENCH = {
    "directed": {"manner": "directed", "look": "", "wardrobe": WARDROBE,
                 "camera": CAMERA, "framing": FRAMING},
    # The room in the look is the point of interest here: a row that names a
    # couch has to build one over floorboards, a low table and a wardrobe door
    # that the look already put in the frame.
    "candid":   {"manner": "candid", "look": CANDID_LOOK,
                 "wardrobe": "She wears nothing at all.",
                 "camera": "Taken from directly in front of her",
                 "framing": "full body"},
}

def _candid_lines():
    """Grok's 25, kept beside the script until a frame earns them a row."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "candidates"))
    from candid_lines import LINES
    return {k: w for k, (w, _needs) in LINES.items()}


SCREENS = {"floor": SCREEN, "glamour": GLAMOUR, "grok": GROK,
           "candid": _candid_lines}

# One seed per photograph, fixed so the run is repeatable as a LIST even though
# the pipeline is not deterministic per frame ([[idevgen-block-format-beats-framing]]:
# same seed is not the same photograph). They are here to be written down, not
# to make two runs comparable frame by frame.
SEEDS = [811224000 + i for i in range(1, 31)]


def acts_from_seed(path: str = "data/directed-acts-seed.json") -> dict[str, dict]:
    with open(path, encoding="utf-8") as fh:
        return {a["concept_key"]: a for a in json.load(fh)}


def prompt_for(act_wording: str, bench: dict, wardrobe: str | None = None) -> str:
    """The line directed actually composes: trigger, wardrobe, camera, act, framing.

    Read off a real composed shot rather than from the composer, so the bench is
    the shape that ships. The take goes last, where `_compose` documents that it
    has to stay.
    """
    worn = wardrobe if wardrobe is not None else bench["wardrobe"]
    parts = [f"{TRIGGER}."]
    for piece in (bench["look"], worn, f'{bench["camera"]}.'):
        if piece:
            parts.append(piece)
    parts.append(f'{act_wording} {bench["framing"]}.')
    return " ".join(parts)


def create_session(base: str, name: str, shots: list, manner: str = "directed") -> dict:
    body = {"model_id": 1, "workflow_id": 8, "name": name, "manner": manner,
            "checkpoint": SETTINGS["checkpoint"], "look": "", "wardrobe": "",
            "settings": SETTINGS, "shots": shots}
    req = urllib.request.Request(base + "/api/sessions",
                                 json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--dry-run", action="store_true", help="print the shots and write nothing")
    ap.add_argument("--run", action="store_true", help="start the session after creating it")
    ap.add_argument("--screen", default="floor", choices=sorted(SCREENS),
                    help="which batch of rows to shoot")
    ap.add_argument("--no-wardrobe", action="store_true",
                    help="write no clothing at all: the only honest bench for a row that needs access")
    args = ap.parse_args()

    bench = BENCH["candid" if args.screen == "candid" else "directed"]
    screen = SCREENS[args.screen]
    if callable(screen):
        screen = screen()
    if isinstance(screen, dict):
        # inline wordings: a candidate gets a photograph before it gets a row
        acts = {k: {"wording": w, "family": "?", "needs": "?", "judge_label": "(untried candidate)"}
                for k, w in screen.items()}
        keys = list(screen)
    else:
        acts, keys = acts_from_seed(), screen
        missing = [k for k in keys if k not in acts]
        if missing:
            raise SystemExit(f"not in the seed file: {missing}")
    assert len(keys) <= len(SEEDS), f"{len(keys)} rows, {len(SEEDS)} seeds"
    wardrobe = "" if args.no_wardrobe else None

    shots = [
        {"label": key, "prompt": prompt_for(acts[key]["wording"], bench, wardrobe),
         "verbatim": True, "seed": seed, "count": 1}
        for key, seed in zip(keys, SEEDS)
    ]

    for key in keys:
        a = acts[key]
        print(f"{key:26} {a['family']:10} needs={a['needs'] or '-':9} {a['judge_label']}")
    print(f"\n{len(shots)} photographs, one per row, manner {bench['manner']}, "
          f"camera {bench['camera']!r} framing {bench['framing']!r}")

    if args.dry_run:
        print("\n--- the first line, in full ---")
        print(shots[0]["prompt"])
        return 0

    out = create_session(
        args.base,
        f"POSE SCREEN {args.screen} - {len(shots)} rows, n=1 each",
        shots, manner=bench["manner"])
    sid = out["id"]
    print(f"\nsession {sid} created as a draft, {len(shots)} pending")
    if not args.run:
        print(f"run it with: curl -X POST {args.base}/api/sessions/{sid}/run")
        return 0

    req = urllib.request.Request(f"{args.base}/api/sessions/{sid}/run", b"",
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        print("run:", r.read().decode()[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
