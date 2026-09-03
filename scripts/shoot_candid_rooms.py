"""Does a new room get built, and what does it cost the pose?

Candid has one look describing one room, and session 368 showed it is the
strongest single clause in the project: it arrived identically in all 25 frames,
and it is why eight rows naming a couch built no couch. So a couch comes from a
catalogue of ROOMS, and a new room multiplies the 79 acts that already exist
rather than adding one.

Nine arms, one photograph each, everything but the room held constant:

    8 candidate rooms + the SHIPPED room, which is the control

The control is not optional. `_look_for` records that the same eight takes
rendered their position at 50 and 63 composed words and stopped at 87, and that
the 24 words which crossed that line were the look's room sentence -- the clause
that describes nobody. Measured on moodyKrea2Mix_v70 in sessions 174-179, not on
premium, and session 368 rendered plenty of poses at ~140 composed words, so the
ceiling may have moved. Either way a room is paid for in words, and the shipped
room is the only honest reference for what a room costs here.

The act is held at `standing-hip-out-hand-waist`: it names no furniture and no
place, so anything that appears around her came from the room and not from her.
It is also one that rendered in 368, so a failure here is the room's doing.

Read it by looking, and count two things per frame:

  1. is the room the one the line describes, or candid's shipped bedroom again?
  2. is she still standing with a hip pushed out, or did the room eat the pose?

Usage: python scripts/shoot_candid_rooms.py [--base URL] [--dry-run] [--run]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from candidates.candid_rooms import ROOMS
from shoot_directed_poses import CANDID_LOOK, TRIGGER, create_session

# The constant half of candid's look: the capture clause and the hair. Every
# room is spliced behind these, so the arms differ by the room alone. Taken by
# splitting the shipped look at the sentence where its room begins.
_ROOM_STARTS = "A bare ceiling bulb lights the room"
assert _ROOM_STARTS in CANDID_LOOK
CAPTURE = CANDID_LOOK[:CANDID_LOOK.index(_ROOM_STARTS)].strip()
SHIPPED_ROOM = CANDID_LOOK[CANDID_LOOK.index(_ROOM_STARTS):].strip()

WARDROBE = "She wears nothing at all."
CAMERA = "Taken from directly in front of her"
FRAMING = "full body"

# Names no furniture and no place, and rendered in session 368.
ACT = ("She stands with one hip pushed far out, one hand at her waist pressing "
       "it back and the other sliding up her belly.")

SETTINGS = {
    "width": 832, "height": 1216, "steps": 8, "cfg": 1, "lora_strength": 1,
    "checkpoint": "Moody-Krea-Mix-premium_00002__clean_nvfp4.safetensors",
    "kind": "shoot", "use_look": False,
}

SEED = 660903001


def prompt_for(room: str) -> str:
    return f"{TRIGGER}. {CAPTURE} {room} {WARDROBE} {CAMERA}. {ACT} {FRAMING}."


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()

    arms = [("shipped", SHIPPED_ROOM)] + [(k, t) for k, (t, _o) in ROOMS.items()]
    shots = [{"label": key, "prompt": prompt_for(room), "verbatim": True,
              "seed": SEED + i, "count": 1}
             for i, (key, room) in enumerate(arms)]

    for (key, room), shot in zip(arms, shots):
        print(f"{key:14} {len(shot['prompt'].split()):3}w composed   {room[:58]}...")
    print(f"\n{len(shots)} photographs, one per room, act and everything else held")

    if args.dry_run:
        print("\n--- the shipped arm, in full ---")
        print(shots[0]["prompt"])
        return 0

    out = create_session(args.base, "CANDID ROOMS - 8 candidates against the shipped room",
                         shots, manner="candid")
    sid = out["id"]
    print(f"\nsession {sid} created as a draft, {len(shots)} pending")
    if not args.run:
        print(f"run it with: curl -X POST {args.base}/api/sessions/{sid}/run")
        return 0
    req = urllib.request.Request(f"{args.base}/api/sessions/{sid}/run", b"",
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        print("run:", r.read().decode()[:120])
    return 0


if __name__ == "__main__":
    sys.exit(main())
