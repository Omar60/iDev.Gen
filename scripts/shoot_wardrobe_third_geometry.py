"""Which half of the wardrobe rule is the law? A THIRD geometry decides.

Two geometries are measured and they disagree
([[idevgen-on-her-back-reached]]):

    on her back   a garment on her CHEST is 2/10, the same 20 words below the
                  waist are 10/10                      -- a waist SPLIT
    the profile   camisole 0/10, long-knickers 0/10, unwritten 9/10
                  ([[idevgen-profile-is-a-base-model-limit]])  -- ANY garment

The common half is "a written garment costs a hard geometry". The waist split is
`on her back` only, so far. A third geometry says which half generalises:

    unwritten high, knickers high, camisole low  ->  the split is real and the
                                                     profile is the odd one out
    unwritten high, both dressed low             ->  ANY garment is the law and
                                                     `on her back` is the odd one
    all three high                               ->  the wardrobe is inert here;
                                                     the cost is a property of
                                                     the pose, not of garments
    unwritten low                                ->  the run says NOTHING, see
                                                     below

## The fourth outcome is bought off, not prayed away

Session 378 could not buy off a dead control in advance and said so. Here it is
cheap: shoot the CONTROL ARM FIRST on both candidates (`--screen`, 20
photographs) and only spend the dressed arms on whichever geometry the empty
wardrobe actually renders (`--act KEY`, 20 more). A candidate whose control dies
is a candidate that was never a geometry, and it costs ten frames to find out
instead of thirty.

Both candidates are floor geometries with the torso DOWN, which is `on her
back`'s own family and the family that has failed hardest here
([[idevgen-on-her-back-unreachable]]): `lying-legs-vertical` came back sitting up
in session 365, and `shoulders-down-hips-up` has never been shot.

## The bench is directed's, with the look that shipped

378/379 ran an EMPTY look because directed had none. It has one now
(`data/directed-looks-seed.json`, session 381: hair agreement 10/10, the profile
10/10 full body, and it builds the studio it names). It is read out of the seed
file rather than retyped, and it is held constant across every arm, so the only
thing that moves between arms is the wardrobe sentence.

The act wordings are read out of `data/directed-acts-seed.json` for the same
reason ([[idevgen-seed-files-drift]]).

## Read it by LOOKING

One question per act, counted over ten photographs:

    lying-legs-vertical      are her shoulders on the floor AND both legs
                             pointing up -- not sitting, not a shallow recline
    shoulders-down-hips-up   are her shoulders on the floor AND her hips clear
                             of it

A judge is the wrong instrument for a screen of two unrelated geometries
([[idevgen-judge-question-design]]), and at n=10 the verified/dead bar sits
inside the judge's own noise anyway ([[idevgen-judge-menu-one-question]]).

Usage: python scripts/shoot_wardrobe_third_geometry.py --screen [--run]
       python scripts/shoot_wardrobe_third_geometry.py --act KEY [--run]
"""
from __future__ import annotations

import argparse
import json
import sys

from shoot_directed_poses import (BENCH, WARDROBE, acts_from_seed,
                                  create_session, prompt_for)
from shoot_on_her_back import LONG_KNICKERS

CANDIDATES = ["lying-legs-vertical", "shoulders-down-hips-up"]

# label -> the wardrobe sentence. The control is shot by `--screen`, so `--act`
# spends its twenty photographs on the two arms that argue with each other.
DRESSED = [("camisole", WARDROBE), ("long-knickers", LONG_KNICKERS)]

SEEDS = [770904001 + i for i in range(10)]

# `--empty-look`: the arm that decides whether the other three measured anything.
#
# The premise of the whole run is that `lying-legs-vertical` is a HARD geometry,
# and that premise rests on session 365, where it came back sitting up. But 365
# was n=1, it wrote the camisole, AND it ran an empty look, because directed had
# none yet. Every arm here carries directed's look. So if the look is what buys
# the geometry, the three arms above measured a wardrobe against a pose that was
# never hard, and the reading "the wardrobe is inert here" is worth nothing.
#
# This is 365's exact condition at n=10: the camisole, the empty look, nothing
# else moved.
#
#     high  ->  365's single frame was noise, the geometry was never hard, and
#               the inert-wardrobe reading is about an easy pose
#     low   ->  the LOOK is what carries this geometry, which is a bigger finding
#               than the wardrobe rule and makes the camisole arm above a
#               look result rather than a wardrobe one


def directed_look(path: str = "data/directed-looks-seed.json") -> str:
    with open(path, encoding="utf-8") as fh:
        looks = json.load(fh)
    shipped = [row for row in looks if row["manner"] == "directed"]
    assert len(shipped) == 1, [row["key"] for row in shipped]
    return shipped[0]["look"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--screen", action="store_true",
                    help="the control arm on both candidates, 10 seeds each")
    ap.add_argument("--act", help="the two dressed arms on one act key, 10 seeds each")
    ap.add_argument("--empty-look", action="store_true",
                    help="with --act: one arm of ten, the camisole on an EMPTY look")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()
    if bool(args.screen) == bool(args.act):
        ap.error("pass exactly one of --screen or --act")

    acts = acts_from_seed()
    bench = {**BENCH["directed"], "look": directed_look()}

    # The pair is worthless if the two written wardrobes differ in length, because
    # length moves photographs here ([[idevgen-line-length-is-the-price]]), and
    # worthless again if the arm that is meant to name nothing above the waist
    # names something above it.
    assert abs(len(LONG_KNICKERS.split()) - len(WARDROBE.split())) <= 1, LONG_KNICKERS
    for word in ("camisole", "top", "bra", "neckline", "strap", "chest",
                 "breast", "shoulder"):
        assert word not in LONG_KNICKERS.lower(), word
    assert "camisole" in WARDROBE and "neckline" in WARDROBE

    if args.screen:
        # One act per arm, one empty wardrobe: this asks whether the geometry
        # exists at all, and nothing else.
        arms = [(key, key, "", bench) for key in CANDIDATES]
        name = ("THE THIRD GEOMETRY - the control arm on both candidates, "
                "no wardrobe written, 10 seeds each")
    elif args.empty_look:
        assert args.act in CANDIDATES, (args.act, CANDIDATES)
        # Session 365's condition: directed's camisole and the empty look
        # directed had before session 381. The wardrobe is the camisole arm's,
        # so the pair differs by the look and by nothing else.
        arms = [(f"{args.act}-empty-look", args.act, WARDROBE,
                 {**bench, "look": ""})]
        name = (f"THE THIRD GEOMETRY - {args.act} with the camisole on an "
                "EMPTY look, session 365's condition, 10 seeds")
    else:
        assert args.act in CANDIDATES, (args.act, CANDIDATES)
        arms = [(f"{args.act}-{label}", args.act, worn, bench)
                for label, worn in DRESSED]
        name = (f"THE THIRD GEOMETRY - {args.act} dressed, "
                "camisole against long-knickers, 10 seeds each")

    for _label, key, _worn, _b in arms:
        assert key in acts, key

    for label, key, worn, arm_bench in arms:
        print(f"{label:32} wardrobe {len(worn.split()):2} words  {worn[:52]!r}")
        print(f"{'':32} look     {len(arm_bench['look'].split()):3} words")
        print(f"{'':32} act      {acts[key]['wording'][:70]!r}")

    shots = [
        {"label": f"{label}-s{i + 1}",
         "prompt": prompt_for(acts[key]["wording"], arm_bench, wardrobe=worn),
         "verbatim": True, "seed": seed, "count": 1}
        for label, key, worn, arm_bench in arms
        for i, seed in enumerate(SEEDS)
    ]
    print(f"\n{len(arms)} arms x {len(SEEDS)} shared seeds = {len(shots)} photographs")

    if args.dry_run:
        print("\n--- one line per arm, in full ---")
        for i in range(len(arms)):
            print(f"\n{shots[i * len(SEEDS)]['label']}")
            print(shots[i * len(SEEDS)]["prompt"])
        return 0

    out = create_session(args.base, name, shots, manner=bench["manner"])
    sid = out["id"]
    print(f"\nsession {sid} created as a draft, {len(shots)} pending")
    if not args.run:
        print(f"run it with: curl -X POST {args.base}/api/sessions/{sid}/run")
        return 0

    import urllib.request
    req = urllib.request.Request(f"{args.base}/api/sessions/{sid}/run", b"",
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        print("run:", r.read().decode()[:120])
    return 0


if __name__ == "__main__":
    sys.exit(main())
