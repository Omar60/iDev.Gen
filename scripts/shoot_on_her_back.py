"""Does the phone put her on her back, or was one frame lucky?

Session 368 produced the first photograph of this character lying on her back
in nine attempts. Eight wordings had failed before it — six at three seeds in
[[idevgen-on-her-back-unreachable]], `lying-legs-vertical` in 365, and
`bed-back-arched` in 366, which put a mattress under her shoulders on the theory
that a floor was the problem and still came back on her front.

The one that worked:

    She lies on her back on the bed with one arm above her head and the other
    holding her phone over her chest, her back arched.

The hypothesis is that the PHONE gives the posture a reason. Lying back to look
at a phone held over your chest is a thing a body does; "lie back and arch" is a
thing only a model does. If that is right it is worth far more than one pose:
give an unreachable geometry a mundane reason and it may arrive.

## Two arms, paired seeds

    phone      the line exactly as session 368 shot it
    no-phone   the same line with the phone removed and nothing else changed

The control is the whole experiment. Ten photographs of the winning line alone
would say whether the ROW is reliable and nothing about WHY, and this project
has lost afternoons to exactly that ([[idevgen-judging-run-traps]]: the control
arm catches every one). If both arms lie her on her back, the phone is innocent
and the cause is elsewhere — candid's voice, its look, or its frontal camera —
and that is a different and equally useful answer.

The ten seeds are SHARED between the arms, which is the only thing pairing can
buy here: the pipeline is not deterministic, so a shared seed is not the same
photograph ([[idevgen-block-format-beats-framing]]), but it does hold the
sampler's starting point constant across the pair.

Bench: candid's, identical to session 368 — its look, `She wears nothing at
all.`, `Taken from directly in front of her`, `full body`, premium, workflow 8.

Read it by looking at all twenty and counting one thing: are her shoulder blades
against the mattress? A blind judge has nothing to add to a question that
literal.

Usage: python scripts/shoot_on_her_back.py [--base URL] [--dry-run] [--run]
"""
from __future__ import annotations

import argparse
import sys

from shoot_directed_poses import (BENCH, SETTINGS, WARDROBE, create_session,
                                  prompt_for)

PHONE = ("She lies on her back on the bed with one arm above her head and the "
         "other holding her phone over her chest, her back arched.")

# The phone removed and the hand given somewhere plain to be. Everything else --
# the bed, the arm above her head, the arch, the word order -- is untouched, so
# the pair differs by the phone and by nothing else.
NO_PHONE = ("She lies on her back on the bed with one arm above her head and the "
            "other resting on her chest, her back arched.")

ARMS = [("phone", PHONE), ("no-phone", NO_PHONE)]

# `--camera`: the one arm that closes [[idevgen-on-her-back-reached]]. Both arms
# above came back 10/10, so the phone is innocent and the cause is one of the
# four things that differ from the eight failures. The camera is the suspect --
# every failure carried directed's two-word `side view`, this line carries a
# sentence putting the lens in front of her, and the hierarchy
# `objects > camera > body geometry` says the camera can outrank the pose.
#
# So: session 368's line, candid's look, wardrobe, framing and seeds, unchanged,
# with the camera swapped for `side view` and NOTHING else touched. If she comes
# back on her front, the camera decides body orientation and every geometry
# written off as unreachable is worth re-shooting.
SIDE_VIEW_CAMERA = "side view"

# `--look`: the camera arm came back 9/10 on her back, so the camera is not what
# separates this line from the eight that failed. Three suspects are left and the
# look is the first to take: [[idevgen-rooms-are-the-lever]] and
# [[idevgen-empty-wardrobe-changes-the-genre]] both say the room is what moved a
# geometry no wording reached, and directed's look -- the bench all eight failures
# ran on -- is empty.
#
# Two arms, because emptying the look moves TWO things at once: candid's room AND
# its amateur-technique register. `no-room` keeps the technique and the hair and
# drops only the two sentences that furnish the room, so a difference between the
# arms says which half carries it. `no-look` is the directed condition exactly.
def look_arms(look: str) -> list[tuple[str, str]]:
    """candid's look, and the same look with the room taken out of it."""
    sentences = look.split(". ")
    assert len(sentences) == 4, sentences
    # sentences 2 and 3 are the only ones that describe the room: the bulb and
    # the black window, then the floorboards, the low table and the wardrobe
    # door. 0 is the technique register, 1 is her hair.
    no_room = ". ".join(sentences[:2]) + "."
    for word in ("floorboards", "table", "wardrobe door", "ceiling bulb", "window"):
        assert word not in no_room, word
    assert no_room.startswith(sentences[0])
    return [("no-room", no_room), ("no-look", "")]

# `--wardrobe`: the camera and the look are both cleared, so of the four things
# that differ from the eight failures only the voice and the wardrobe are left.
# The wardrobe goes first because deleting the written clothes is the single
# biggest move this project has measured on a photograph
# ([[idevgen-empty-wardrobe-changes-the-genre]]).
#
# Two arms, because "directed's wardrobe" was two conditions and the eight
# failures used both: `camisole` writes the cream camisole and black knickers of
# session 364, `unwritten` writes no clothing sentence at all -- which is NOT the
# same as candid's `She wears nothing at all.`, a sentence that says she is nude.
WARDROBE_ARMS = [("camisole", WARDROBE), ("unwritten", "")]

# `--garment`: session 374 said a written camisole sits her up 8 times in 10.
# The mechanism proposed for it was that a garment has to be SEEN, and this one
# can only be seen on a torso turned toward the lens -- its narrow straps and its
# softly rounded neckline are features of the FRONT of a chest. If that is right
# the cure is to stop asking for anything that must be looked at, and the
# geometry comes back.
#
# Two arms against 374's `camisole` cell, which is the third corner of the same
# square:
#
#     camisole (374)   upper garment + the features that must be seen   2/10
#     plain            upper garment, no features                        ?
#     knickers         no upper garment, no features                     ?
#
# plain at 10/10 means the DESCRIPTIVE CLAUSE is what does it, and every row in
# the catalogue that names a neckline is suspect. plain at 2/10 with knickers at
# 10/10 means any garment above the waist does it, whatever it is called. Both
# low means any written garment at all, and the wardrobe is simply incompatible
# with this geometry.
#
# Confound to say out loud: each arm is also SHORTER than the last, and line
# length moves photographs here ([[idevgen-line-length-is-the-price]]). It cannot
# be held constant while removing words, so a positive result on `plain` is the
# one that separates the two -- it drops seven words, `knickers` drops eleven.
KNICKERS = "She wears black cotton knickers."
CAMISOLE_PLAIN = ("She wears a cream cotton jersey camisole and black cotton "
                  "knickers.")
GARMENT_ARMS = [("plain", CAMISOLE_PLAIN), ("knickers", KNICKERS)]

# `--long-knickers`: session 376 came back 2/10, 4/10, 10/10 for wardrobes of 19,
# 11 and 5 words. That reads as "the chest garment is what sits her up", but the
# three cells are also monotone in LENGTH, and length moves photographs here
# ([[idevgen-line-length-is-the-price]]), so the run cannot tell the two apart.
#
# This is the arm that can: the knickers written to the camisole's own length and
# syntax -- `with narrow X and a softly Y` -- with nothing above the waist and no
# body part below the hip, so the crop law is not disturbed
# ([[idevgen-crop-terms-as-cameras]]).
#
#     stays on her back  ->  the CHEST garment is the cause, length is innocent
#     sits her up        ->  LENGTH was doing the work and the camisole was never
#                            special, which also retracts the reading of 374
LONG_KNICKERS = ("She wears black cotton knickers with a narrow elastic "
                 "waistband and a softly scalloped hem, cut high over her hips.")

# `--voice`: the last of the four. The wardrobe is the one that flipped her
# (session 374, 2/10 with a written camisole), so this arm is no longer looking
# for the cause -- it is asking whether there is a SECOND one. Directed names its
# subject in the first clause of every row; candid says `She`. Nothing else in
# the line moves.
VOICE = ("The young woman lies on her back on the bed with one arm above her "
         "head and the other holding her phone over her chest, her back arched.")

SEEDS = [770901001, 770901002, 770901003, 770901004, 770901005,
         770901006, 770901007, 770901008, 770901009, 770901010]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--camera", action="store_true",
                    help="one arm of ten: the winning line with `side view` substituted")
    ap.add_argument("--look", action="store_true",
                    help="two arms of ten: the look with its room removed, and no look at all")
    ap.add_argument("--long-knickers", action="store_true",
                    help="one arm of ten: knickers written to the camisole's length, to separate length from the chest")
    ap.add_argument("--garment", action="store_true",
                    help="two arms of ten: the camisole without its seen features, and knickers alone")
    ap.add_argument("--voice", action="store_true",
                    help="one arm of ten: directed's subject phrase in place of `She`")
    ap.add_argument("--wardrobe", action="store_true",
                    help="two arms of ten: directed's camisole, and no clothing sentence at all")
    args = ap.parse_args()

    bench = BENCH["candid"]
    if args.camera:
        arms = [("side-view", PHONE, {**bench, "camera": SIDE_VIEW_CAMERA})]
        name = "ON HER BACK - the camera arm, `side view`, 10 seeds"
        print(f"camera {bench['camera']!r}  ->  {arms[0][2]['camera']!r}")
        print("the line is session 368's, unchanged\n")
    elif args.long_knickers:
        arms = [("long-knickers", PHONE, {**bench, "wardrobe": LONG_KNICKERS})]
        name = "ON HER BACK - knickers at the camisole's length, 10 seeds"
        # The arm is worthless if it is shorter than the cell it argues with, or
        # if it names anything above the waist or below the hip.
        assert abs(len(LONG_KNICKERS.split()) - len(WARDROBE.split())) <= 1, LONG_KNICKERS
        for word in ("camisole", "top", "bra", "neckline", "strap", "chest",
                     "breast", "shoulder", "knee", "ankle", "foot", "feet"):
            assert word not in LONG_KNICKERS.lower(), word
        assert "knickers" in LONG_KNICKERS
        print(f"{'(374)':14} {len(WARDROBE.split()):2} words  {WARDROBE!r}")
        print(f"{'(376 short)':14} {len(KNICKERS.split()):2} words  {KNICKERS!r}")
        print(f"{'long-knickers':14} {len(LONG_KNICKERS.split()):2} words  {LONG_KNICKERS!r}")
        print("\nthe line, the look, the camera and the framing are session 368's\n")
    elif args.garment:
        arms = [(label, PHONE, {**bench, "wardrobe": worn})
                for label, worn in GARMENT_ARMS]
        name = "ON HER BACK - the garment arm, camisole without its features, and knickers alone"
        # `plain` must be directed's wardrobe with the two seen-features clauses
        # removed and nothing else reworded, or it measures a different garment.
        for clause in ("with narrow straps", "softly rounded neckline"):
            assert clause in WARDROBE and clause not in CAMISOLE_PLAIN, clause
        assert CAMISOLE_PLAIN.startswith("She wears a cream cotton jersey camisole")
        assert CAMISOLE_PLAIN.endswith("black cotton knickers.")
        assert "camisole" not in KNICKERS
        for label, _w, b in arms:
            print(f"{label:9} {len(b['wardrobe'].split()):2} words  {b['wardrobe']!r}")
        print(f"{'(374)':9} {len(WARDROBE.split()):2} words  {WARDROBE!r}")
        print("\nthe line, the look, the camera and the framing are session 368's\n")
    elif args.voice:
        arms = [("young-woman", VOICE, bench)]
        name = "ON HER BACK - the voice arm, directed's subject phrase, 10 seeds"
        # The two lines differ by the subject phrase and by nothing else.
        assert VOICE.endswith(PHONE[len("She lies"):]), VOICE
        assert VOICE.startswith("The young woman lies")
        print(f"{'candid':12} {PHONE[:24]!r}...")
        print(f"{'young-woman':12} {VOICE[:24]!r}...")
        print("\nthe look, the wardrobe, the camera and the framing are session 368's\n")
    elif args.wardrobe:
        arms = [(label, PHONE, {**bench, "wardrobe": worn})
                for label, worn in WARDROBE_ARMS]
        name = "ON HER BACK - the wardrobe arm, camisole and unwritten, 10 seeds each"
        print(f"{'candid':9} {bench['wardrobe']!r}")
        for label, _w, b in arms:
            print(f"{label:9} {b['wardrobe']!r}")
        print("\nthe line, the look, the camera and the framing are session 368's\n")
    elif args.look:
        arms = [(label, PHONE, {**bench, "look": look})
                for label, look in look_arms(bench["look"])]
        name = "ON HER BACK - the look arm, room removed and look removed, 10 seeds each"
        for label, _w, b in arms:
            words = len(b["look"].split())
            print(f"{label:9} look {words:3} words: {b['look'][:64]!r}")
        print("\nthe line, the camera, the wardrobe and the framing are session 368's\n")
    else:
        arms = [(label, wording, bench) for label, wording in ARMS]
        name = "ON HER BACK - phone vs no phone, 10 paired seeds"
        # The two lines must differ by the phone alone; a stray edit is the whole
        # experiment gone, and it is cheaper to assert it than to notice it later.
        HEAD = ("She lies on her back on the bed with one arm above her head and "
                "the other ")
        TAIL = ", her back arched."
        for line in (PHONE, NO_PHONE):
            assert line.startswith(HEAD) and line.endswith(TAIL), line
        assert "phone" not in NO_PHONE.lower()
        # What actually differs, printed so the diff is on the record rather than
        # taken on trust: the hand's clause and nothing else.
        print(f"phone arm    ...{PHONE[len(HEAD):-len(TAIL)]!r}")
        print(f"no-phone arm ...{NO_PHONE[len(HEAD):-len(TAIL)]!r}\n")

    # An arm may differ from candid's bench in ONE key. A second difference is
    # two experiments in one session and there is no reading that separates them.
    for label, _wording, b in arms:
        differs = [k for k, v in b.items() if BENCH["candid"][k] != v]
        assert len(differs) <= 1, (label, differs)

    shots = [
        {"label": f"{label}-s{i + 1}", "prompt": prompt_for(wording, b),
         "verbatim": True, "seed": seed, "count": 1}
        for label, wording, b in arms
        for i, seed in enumerate(SEEDS)
    ]

    for label, wording, _b in arms:
        print(f"{label:9} {wording}")
    print(f"\n{len(arms)} arms x {len(SEEDS)} shared seeds = {len(shots)} photographs")

    if args.dry_run:
        print("\n--- the first line, in full ---")
        print(shots[0]["prompt"])
        return 0

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
