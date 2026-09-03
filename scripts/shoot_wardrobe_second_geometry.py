"""Does the chest-garment rule survive a SECOND geometry?

Sessions 372-377 measured one pose. `on her back` renders 2/10 with a written
camisole and 10/10 with the same number of words written below the waist
([[idevgen-on-her-back-reached]]). That is worth the whole catalogue if it
generalises and worth one row if it does not, and everything measured so far is
the same pose.

This is the arm that answers it. The geometry is the ninety-degree profile,
which is the strongest candidate for three reasons:

* it is the geometry this project has written off hardest -- nine wordings at
  0/24, six suspects eliminated, filed as a base-model limit
  ([[idevgen-profile-is-a-base-model-limit]]; read its three retractions);
* `wall-facing-forearms` delivered a clean one in session 366 -- and 366 is the
  session that wrote NO WARDROBE at all
  ([[idevgen-empty-wardrobe-changes-the-genre]]). Same shape as today's result,
  arrived at from the other side;
* session 366 was n=1, so the control arm here is worth having anyway.

## Three arms, ten shared seeds, one key moved

The three corners of session 376's square, on a different pose:

    unwritten      no clothing sentence at all   -- 366's condition, the control
    camisole       directed's cream camisole     -- 19 words, above the waist
    long-knickers  the same words below the hip  -- matched length, nothing above

Predictions, written before the run so the read cannot drift to fit:

    unwritten high, long-knickers high, camisole low  ->  the rule generalises
    all three high                                    ->  the wardrobe is inert
                                                          here and the rule is a
                                                          property of that pose
    all three low                                     ->  366's frame was n=1 and
                                                          the run says nothing
                                                          about the wardrobe

The third outcome is the real risk and there is no way to buy it off in advance;
the control is what tells us we are in it.

## The answer, sessions 378 and 379

    unwritten      9/10      long-knickers  0/10
    camisole       0/10      top-only       0/10

None of the three predictions. **Any written garment kills this geometry**,
above the waist or below it, so the chest rule does not generalise -- the waist
split is a property of `on her back` and not a law. `--top-only` was added after
the first three arms to ask whether the black knickers common to both dead arms
were the cause; they were not. The full reading is in
`docs/catalogue-measurements.md`.

## The bench is session 366's, not candid's

Deliberately. The profile was SEEN on directed's bench -- empty look, `side
view`, `full body`, premium, workflow 8 -- and moving to candid's would change
the look and the camera alongside the wardrobe, which is two experiments in one
session. The act wording is read out of `data/directed-acts-seed.json` rather
than retyped, so this script cannot drift from the catalogue
([[idevgen-seed-files-drift]]).

Known and accepted: an empty look means the character will slip
([[idevgen-where-2026-09-03-stopped]] item 2). The question is the angle of her
torso, not the colour of her hair, so the drift costs nothing here -- but it is
why this bench is not the one to quote about identity.

Read it by LOOKING at all thirty and counting one thing: is her far shoulder
hidden behind the near one. A three-quarter turn is NOT a profile -- that is the
distinction every previous pass on this geometry has died on, and the judge can
be pulled by a profile FACE on a three-quarter torso.

Usage: python scripts/shoot_wardrobe_second_geometry.py [--base URL] [--dry-run] [--run]
"""
from __future__ import annotations

import argparse
import sys

from shoot_directed_poses import (BENCH, WARDROBE, acts_from_seed,
                                  create_session, prompt_for)
from shoot_on_her_back import LONG_KNICKERS

ACT_KEY = "wall-facing-forearms"

# label -> the wardrobe sentence, which is the only thing that moves.
ARMS = [
    ("unwritten", ""),
    ("camisole", WARDROBE),
    ("long-knickers", LONG_KNICKERS),
]

# `--top-only`: the first three arms came back 9/10, 0/10, 0/10, which reads as
# "any written garment kills the profile" and refutes the chest rule outright.
# But the two dead arms share something the reading skipped: BLACK COTTON
# KNICKERS. Directed's wardrobe ends `and black cotton knickers`, so the
# camisole arm wrote them too, and both dead arms came back with her buttocks as
# the subject of the frame -- rear three-quarter, cropped at the thigh.
#
# So the two accounts still standing are:
#
#     any written garment at all             -> a camisole alone also dies
#     the knickers make the buttocks the      -> a camisole alone renders the
#     subject and the body turns to show them    profile, and the knickers did it
#
# This is directed's wardrobe with the knickers clause removed and nothing else
# reworded. It is 15 words against 19 and 20, which is the length confound
# again -- but the control is ZERO words and it WON, so length does not order
# these arms and cannot explain a death at 15.
TOP_ONLY = ("She wears a cream cotton jersey camisole with narrow straps and a "
            "softly rounded neckline.")

# `--identity`: the identity drift has had a lever named for a day and nobody has
# pulled it. Session 373 counted blondes against candid's look -- 10/10 with the
# full look, 7/10 with its room deleted, 2/10 with no look at all -- so the look
# is what holds the character. Directed's look is EMPTY, and directed is where
# the drift has been loudest all week.
#
# This arm costs ten photographs because the CONTROL IS ALREADY SHOT: session
# 378's `unwritten` arm is this same act, these same ten seeds and an empty look,
# and it came back blonde 7 of 10. So: paste candid's look in, change nothing
# else, count blondes again.
#
# Two things to say out loud before reading it. The look also writes a room and
# an amateur-technique register, so a difference is attributable to THE LOOK and
# not to any one sentence in it -- session 373 already split those and this arm
# does not. And the look says `She wears her hair loose` where the control frames
# mostly carry a ponytail, so the hairSTYLE moves too; the question is the
# COLOUR.
#
# The answer, session 380: blonde 10/10 against the control's 7/10, and the
# profile 10/10 -- full body with her feet in frame, against 9/10 and a tighter
# frame with no look. The look costs the geometry nothing.
#
# RETRACTION, same day, from the person who trained the LoRA: Jiroko is blonde
# AND can be brunette, both are in the character, so a brunette frame is not
# drift and this arm never measured identity. What the counts measure is whether
# the ten frames of a session AGREE -- which is the requirement a session
# actually has, one look held constant or it is not one shoot. Read every number
# in this file as colour agreement, not as the right woman.

# `--directed-look`: session 380 says the fix for the drift is a look, but the
# look it used is CANDID'S, and candid's first sentence is an amateur-technique
# register -- small sensor, sensor noise, washed-out colour, no studio lighting.
# Shipping that text to directed would not give directed a look, it would turn
# directed into candid. So the shipped text has to be written, and this arm is
# what decides which one.
#
# Session 373 split candid's look once: full look 10/10 blonde, room deleted
# 7/10, no look 2/10. Room deleted is the technique register PLUS the hair
# sentence, so most of the effect is in those two and the room is worth the last
# three points. What that split cannot say is which of the two carries it, and
# the answer decides how much prose directed needs:
#
#     hair-only at 10/10   ->  ship one sentence, invent no register
#     hair-only low and
#     directed-look high   ->  the register is doing the work and directed needs
#                              its own, written in its own voice
#
# The answer, session 381: `hair-only` agrees 9/10 -- on BRUNETTE -- and crops at
# the knee; `directed-look` agrees 10/10 on blonde, renders the profile 10/10
# full body, and builds the softbox, tripod, paper roll and reflector it names.
# One sentence is enough to make a session one session; the register and the room
# are what buy the framing and the studio. `directed-look` is the text shipped in
# `data/directed-looks-seed.json`.
#
# Both arms carry the same hair sentence, taken verbatim from candid's look, so
# the pair differs by the register and the room and by nothing else.
HAIR = "She wears her hair loose, with a few strands pushed behind one ear."

# Candid's look in structure -- register, hair, light, room -- and directed's in
# every word: a tripod instead of a phone, shaped light instead of a bare bulb,
# and a room that is a working studio rather than a bedroom at night. The room is
# there because 373 priced it at three points and because directed's own code
# comment says a directed shoot with no room reads as posed in a void.
DIRECTED_LOOK = (
    "Full-frame camera on a tripod, sharp where it is focused and softly out of "
    "focus behind, deep clean shadows, true colour and no grain, the frame "
    "square and level. " + HAIR + " A single large softbox stands close to one "
    "side of her and a white fill card faces it from the other. Bare studio "
    "floor runs away underfoot, a roll of seamless paper is clamped to a stand "
    "behind her, and a folded reflector leans against the far wall.")

DIRECTED_LOOK_ARMS = [("hair-only", HAIR), ("directed-look", DIRECTED_LOOK)]

# Fresh seeds, shared across the three arms. Shared seeds do not make the same
# photograph here ([[idevgen-block-format-beats-framing]]), they only hold the
# sampler's starting point constant across the pair.
SEEDS = [770903001 + i for i in range(10)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--top-only", action="store_true",
                    help="one arm of ten: the camisole with the knickers clause removed")
    ap.add_argument("--identity", action="store_true",
                    help="one arm of ten: candid's look on directed's bench, against 378's unwritten control")
    ap.add_argument("--directed-look", action="store_true",
                    help="two arms of ten: the hair sentence alone, and a look written in directed's own voice")
    args = ap.parse_args()

    act = acts_from_seed()[ACT_KEY]
    wording = act["wording"]
    bench = BENCH["directed"]

    # The arm is worthless if the two written wardrobes differ in length, because
    # length moves photographs here ([[idevgen-line-length-is-the-price]]) and the
    # whole point is to separate length from the chest.
    assert abs(len(LONG_KNICKERS.split()) - len(WARDROBE.split())) <= 1, LONG_KNICKERS
    for word in ("camisole", "top", "bra", "neckline", "strap", "chest",
                 "breast", "shoulder"):
        assert word not in LONG_KNICKERS.lower(), word
    assert "camisole" in WARDROBE and "neckline" in WARDROBE

    if args.directed_look:
        # Both arms must carry candid's hair sentence verbatim and an empty
        # wardrobe, so the pair differs by the register and the room alone and
        # the control stays session 378's `unwritten` arm.
        assert HAIR in BENCH["candid"]["look"], HAIR
        assert HAIR in DIRECTED_LOOK
        for word in ("small sensor", "sensor noise", "washed-out", "motion blur",
                     "no studio lighting"):
            assert word not in DIRECTED_LOOK.lower(), word
        arms = [(label, "", {**bench, "look": look})
                for label, look in DIRECTED_LOOK_ARMS]
    elif args.identity:
        # The control is session 378's `unwritten` arm, so this arm must carry
        # the same empty wardrobe: the look is the only thing that moves.
        arms = [("candid-look", "", {**bench, "look": BENCH["candid"]["look"]})]
    elif args.top_only:
        # The arm is only worth shooting if it is directed's own camisole clause
        # with the knickers gone and NOTHING else reworded.
        assert WARDROBE.startswith(TOP_ONLY[:-1]), TOP_ONLY
        assert "knickers" in WARDROBE and "knickers" not in TOP_ONLY
        arms = [("top-only", TOP_ONLY, bench)]
    else:
        arms = [(label, worn, bench) for label, worn in ARMS]

    # An arm may differ from directed's bench in ONE key. A second difference is
    # two experiments in one session and there is no reading that separates them.
    for label, _worn, b in arms:
        differs = [k for k, v in b.items() if BENCH["directed"][k] != v]
        assert len(differs) <= 1, (label, differs)

    print(f"act  {ACT_KEY}: {wording}\n")
    for label, worn, b in arms:
        print(f"{label:14} wardrobe {len(worn.split()):2} words, "
              f"look {len(b['look'].split()):3} words  {(b['look'] or worn)[:56]!r}")

    shots = [
        {"label": f"{label}-s{i + 1}",
         "prompt": prompt_for(wording, arm_bench, wardrobe=worn),
         "verbatim": True, "seed": seed, "count": 1}
        for label, worn, arm_bench in arms
        for i, seed in enumerate(SEEDS)
    ]
    print(f"\n{len(arms)} arms x {len(SEEDS)} shared seeds = {len(shots)} photographs")

    if args.dry_run:
        print("\n--- one line per arm, in full ---")
        for i in range(len(arms)):
            print(f"\n{shots[i * len(SEEDS)]['label']}")
            print(shots[i * len(SEEDS)]["prompt"])
        return 0

    if args.directed_look:
        name = "DIRECTED'S LOOK - the hair sentence alone, and a look in directed's voice, 10 seeds each"
    elif args.identity:
        name = "IDENTITY - candid's look on directed's bench, 10 seeds, control is 378 unwritten"
    elif args.top_only:
        name = "THE PROFILE vs THE WARDROBE - the camisole without its knickers, 10 seeds"
    else:
        name = "THE PROFILE vs THE WARDROBE - unwritten, camisole, long-knickers, 10 seeds each"
    out = create_session(args.base, name, shots, manner=bench["manner"])
    sid = out["id"]
    print(f"\nsession {sid} created as a draft, {len(shots)} pending")
    if not args.run:
        print(f"run it with: curl -X POST {args.base}/api/sessions/{sid}/run")
        return 0

    import json
    import urllib.request
    req = urllib.request.Request(f"{args.base}/api/sessions/{sid}/run", b"",
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        print("run:", r.read().decode()[:120])
    return 0


if __name__ == "__main__":
    sys.exit(main())
