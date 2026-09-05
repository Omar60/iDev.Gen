"""7.9. Does the LENGTH of the room clause cost the camera, on its own?

The word budget in `ConfigIn.room_word_budget` ships at a number that refuses
nothing, and this is the arm that is supposed to replace the guess with a
measurement. Two sessions have already pushed against the idea that a long line
costs the camera and neither found it:

* Session 391: a 242-word line kept its camera 3/3 while a 224-word line lost
  it, so total length is not the predictor. What it really found was the
  open-garment reading.
* Session 394, paired and much stronger: one line shot twice, once as written
  and once with 87 to 187 words of whole blocks deleted, same seed, everything
  else byte-identical - 9/16 against 9/16.

Neither varied the ROOM alone, which is the quantity the budget polices. A
200-word room string is a different thing from seven deleted blocks: the room is
one clause, it describes nobody, and it sits between the capture clause and the
body. So this arm moves the room and holds every other word still.

## The instrument

The composition is the project's one CALIBRATED cell - session 382's anchor,
judged blind at five passes and standing at judged 10, arrived 9:

    camera  side-view   act  wall-facing-forearms   framing  crop-full-body

Composed through the app rather than written verbatim, which is not a style
choice: a verbatim line leaves `shot.components` empty and
`POST /api/shots/{id}/judge` refuses it, so a verbatim arm can never be judged
against the catalogue's own vocabulary. Each arm is one session whose LOOK is
the ladder rung, and `POST /api/sessions/{sid}/compose` with `count` queues that
one trio N times.

The wardrobe is muted in every arm. On this geometry any written garment is
0/10 ([[idevgen-on-her-back-reached]]), and a garment clause is also a length
that is not the room's.

## The ladder

One room, `general-rooftop-laundry`, cut and extended by whole clauses. Cutting
is what session 394 did to a line; doing it to a room keeps the PLACE the same
while the word count moves, which is the only way "length alone" means anything.
The room was picked for being ordinary and for naming no body part: the corpus's
long rooms are abstract chambers whose text describes her ("body floating",
"legs drifting apart"), and those would fight the act rather than measure it.

    control   directed's shipped look, the studio - the instrument's own baseline
    000       no room at all: the capture clause and nothing else
    018       two clauses  - just under the corpus median of 17
    053       the room as the corpus holds it
    090       extended     - the longest room in the corpus is 89 words
    180       extended     - twice the longest room, the absurd input a tripwire is for

The clauses past 53 words are written here, in the source's register: a plain
comma list of things in the place, no body, no camera, no framing. They are the
only invented text in the arm and they are marked as such below.

## What is measured

The camera slot, at `--axis position` - directed's camera vocabulary mixes
horizontal position with height, and asked as one menu it answers the wrong
question ([[idevgen-first-anchor-cell]]). Judge with:

    python scripts/judge_cell.py --session <id> --slot camera --axis position --repeat 5

with a control arm, or do not quote the number ([[idevgen-judging-run-traps]]).

Usage: python scripts/shoot_room_length.py [--base URL] [--dry-run] [--run] [--count N]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

BASE_DEFAULT = "http://127.0.0.1:8777"

CHECKPOINT = "Moody-Krea-Mix-premium_00002__clean_nvfp4.safetensors"
SETTINGS = {
    "width": 832, "height": 1216, "steps": 8, "cfg": 1, "lora_strength": 1,
    "checkpoint": CHECKPOINT, "kind": "shoot",
}

# The anchor cell, session 382. Keys only: the wordings are read off the
# catalogue at run time, so this arm cannot drift from what the cell holds.
TRIO = {"camera": "side-view", "act": "wall-facing-forearms", "framing": "crop-full-body"}

# directed's register, prepended to every rung exactly as `composeLook` does.
REGISTER = ("Full-frame camera on a tripod, sharp where it is focused and softly out of "
            "focus behind, deep clean shadows, true colour and no grain, the frame square "
            "and level. She wears her hair loose, with a few strands pushed behind one ear.")

# The shipped studio, for the control arm: the look the anchor cell was measured
# under, byte for byte from `data/directed-looks-seed.json`.
STUDIO = ("A single large softbox stands close to one side of her and a white fill card "
          "faces it from the other. Bare studio floor runs away underfoot, a roll of "
          "seamless paper is clamped to a stand behind her, and a folded reflector leans "
          "against the far wall.")

# `general-rooftop-laundry`, byte for byte from the imported seed, split on its
# own commas. Cumulative prefixes of this list are the short rungs.
ROOM_CLAUSES = [
    "cluttered rooftop of an old apartment building",
    "maze of laundry lines with flapping white bedsheets and colorful clothes",
    "tangled electric wires against the sunset sky",
    "weathered water tanks and concrete walls",
    "dust particles dancing in the warm golden hour light",
    "distant cityscape of crowded rooftops",
    "a sense of semi-hidden exposure and windy freedom",
]

# INVENTED, in the source's register, to reach the long rungs. Same place, more
# of it: plain nouns, no body, no camera, no framing, no light direction that
# could stand in for the camera's own.
EXTRA_CLAUSES = [
    "chipped green paint peeling from the parapet edge",
    "a plastic laundry basket tipped on its side",
    "wooden clothes pegs scattered across the concrete",
    "a satellite dish bolted to a rusted bracket",
    "puddles left from the morning rain drying unevenly",
    "an air conditioning unit dripping onto the slab",
    "coils of blue rope looped over a hook",
    "a stack of terracotta plant pots against the tank",
    "faded chalk marks left by children on the floor",
    "a metal stair door propped open with a brick",
    "pigeon feathers caught in the corner of the parapet",
    "a folding drying rack leaning shut against the wall",
    "cigarette ends collected in a tin lid",
    "cracked grout running between the floor slabs",
    "a length of garden hose coiled by the drain",
    "sun-bleached plastic chairs stacked two high",
    "a broom with worn bristles standing in the corner",
    "washing powder residue crusted around the tap",
    "a mop bucket half full of grey water",
    "torn plastic sheeting tied to the railing",
    "brick dust gathered where the wall has weathered",
    "a bicycle wheel with no tyre hung on a nail",
    "an empty birdcage rusted at the hinges",
    "old newspaper pressed flat under a loose slab",
    "paint tins stacked beside the water tank",
    "a garden trowel left in a bag of soil",
    "loose gravel swept into a low ridge",
    "a wooden crate with one side broken in",
]


def _room_of(words: int) -> str:
    """The rung whose room is closest to `words` words, built from whole clauses.

    Whole clauses, never a truncation mid-sentence: a room cut mid-clause is a
    different KIND of text, and the arm would then be measuring grammar as well
    as length. The count each rung actually lands on is printed, and it is the
    count the result is recorded against - the target is what the ladder aims
    at, not what it claims to have hit.
    """
    if words <= 0:
        return ""
    clauses: list[str] = []
    for clause in ROOM_CLAUSES + EXTRA_CLAUSES:
        if len(" ".join(clauses + [clause]).split()) > words and clauses:
            break
        clauses.append(clause)
    return ", ".join(clauses)


def arms(targets: list[int]) -> list[tuple[str, str]]:
    """`(label, look)` per arm, the control first."""
    out = [("control-studio", f"{REGISTER} {STUDIO}")]
    for target in targets:
        room = _room_of(target)
        look = f"{REGISTER} {room}." if room else REGISTER
        out.append((f"room-{target:03d}", look))
    return out


def _post(base: str, path: str, payload) -> dict:
    req = urllib.request.Request(base + path, json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=60) as r:
        return json.loads(r.read().decode())


def catalogue_trio(base: str) -> dict:
    """The three components in the shape `POST /compose` takes, read off the
    catalogue so the arm shoots the cell's own wordings and not a copy of them."""
    listed = _get(base, "/api/components")
    rows = listed["components"] if isinstance(listed, dict) else listed
    picked: dict = {}
    for slot, key in TRIO.items():
        match = [c for c in rows
                 if c.get("concept_key") == key and c.get("slot") == slot
                 and (c.get("manner") in ("directed", None, "") or slot == "camera")
                 and not c.get("retired_at")]
        if not match:
            raise SystemExit(f"the catalogue has no {slot} {key!r}: import it before shooting")
        # directed's copy when there is one, otherwise whatever the slot holds.
        row = next((c for c in match if c.get("manner") == "directed"), match[0])
        picked[slot] = {"key": key,
                        "wordings": [{"key": key, "text": row["wording"]}]}
    return picked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE_DEFAULT)
    ap.add_argument("--model-id", type=int, default=None,
                    help="the model to shoot; defaults to the only one, or refuses")
    ap.add_argument("--count", type=int, default=10,
                    help="photographs per arm (the judging protocol's minimum is 10)")
    ap.add_argument("--targets", default="0,18,53,90,180",
                    help="room lengths in words, comma separated")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()

    targets = [int(t) for t in args.targets.split(",") if t.strip()]
    plan = arms(targets)

    for label, look in plan:
        room_words = len(look.split()) - len(REGISTER.split())
        print(f"{label:16} look {len(look.split()):4}w   room {room_words:4}w")

    if args.dry_run:
        print("\n--- the longest arm, in full ---")
        print(plan[-1][1])
        return 0

    models = _get(args.base, "/api/models")
    model_id = args.model_id or (models[0]["id"] if len(models) == 1 else None)
    if model_id is None:
        raise SystemExit(f"pass --model-id: {[(m['id'], m['name']) for m in models]}")

    trio = catalogue_trio(args.base)
    print(f"\ntrio from the catalogue: "
          + ", ".join(f"{s}={trio[s]['wordings'][0]['text'][:28]!r}" for s in TRIO))

    made = []
    for label, look in plan:
        sid = _post(args.base, "/api/sessions", {
            "model_id": model_id, "name": f"ROOM LENGTH 7.9 - {label}",
            "look": look, "wardrobe": "", "settings": dict(SETTINGS),
            "manner": "directed", "checkpoint": CHECKPOINT, "shots": [],
        })["id"]
        queued = _post(args.base, f"/api/sessions/{sid}/compose",
                       {**trio, "count": args.count, "mute_wardrobe": True})
        made.append((label, sid, queued["count"]))
        print(f"  session {sid:5} {label:16} {queued['count']} queued")

    if not args.run:
        print("\nrun each with: "
              + "; ".join(f"curl -X POST {args.base}/api/sessions/{sid}/run"
                          for _l, sid, _n in made))
        return 0
    for label, sid, _n in made:
        out = _post(args.base, f"/api/sessions/{sid}/run", {})
        print(f"  run {label}: {json.dumps(out)[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
