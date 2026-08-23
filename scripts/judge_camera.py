"""Ask the vision model where the camera stood, without telling it what was asked.

Usage: python scripts/judge_camera.py 230 [--repeat 1] [--base http://127.0.0.1:8777]

Why blind: the interesting number is not whether a photograph can be read as
matching its line, it is whether someone who never saw the line reads it the same
way. A judge that is shown the prompt agrees with the prompt.

So the judge is handed one photograph and one fixed question with a closed
vocabulary, and nothing else — no prompt, no wardrobe, no shoot, not even the
knowledge that a camera position was asked for. The comparison against what the
line asked happens here, after the answer is in.

It goes through the app's own `/api/enhance` with `shot_id`, which is the path
the app already uses for the vision model: the photo never leaves the machine
except the way the app already sends it, and no key is handled here.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request

# The catalogue, by the opening words of the clause the writer was handed. Kept
# here rather than imported from kinds.js because this reads a shoot that has
# already been written: what matters is what the line says, not what the plan
# meant. See CAMERA_POSITIONS in frontend/src/kinds.js.
ASKED = [
    # Longest first: `overhead camera directly above her and behind her head`
    # must not be eaten by the bare form it extends.
    ("overhead", "overhead camera directly above her and behind her head"),
    ("overhead", "overhead camera directly above her"),
    ("overhead", "high camera looking steeply down at her from her right side"),
    ("overhead", "high camera looking steeply down at her"),
    ("floor", "low-angle shot from the floor at her feet"),
    ("floor", "low-angle shot from the floor behind her"),
    ("floor", "low-angle shot from the floor in front of her"),
    ("shoulder", "taken from behind her left shoulder"),
    ("shoulder", "taken from behind her right shoulder"),
    ("behind", "taken from directly behind her"),
    ("side", "taken from her right side"),
    ("side", "taken from her left side"),
    ("front", "taken from her right front"),
    ("front", "taken from directly in front of her"),
    # The candid forms, from `shoot_candid_cameras.py`. They name where the PHONE
    # is rather than where a camera stands, which is the whole question, so the
    # family here is what the mount asks for and not what any verified head word
    # promises.
    ("overhead", "phone propped on a high shelf across the room, looking down at her"),
    ("overhead", "phone held above her in his hand, looking straight down at her"),
    ("floor", "phone set down on the carpet at her feet, tipped up toward her"),
    ("front", "phone held out at arm's length in front of her face"),
    ("front", "mirror selfie, the phone up in her right hand"),
    ("front", "taken from an arm's length in front of her face"),
    # Session 247, the behind and shoulder families in the phone vocabulary.
    ("behind", "phone propped on the shelf behind her, facing her back"),
    ("behind", "phone in his hand behind her, pointed at her back"),
    ("behind", "phone held out behind her at arm's length, pointed back at her"),
    ("shoulder", "phone in his hand just behind her left shoulder, pointed past it"),
    ("shoulder", "mirror selfie with her back to the mirror, looking over her shoulder"),
    ("shoulder", "phone propped on a shelf behind her left shoulder"),
    ("shoulder", "phone propped on a shelf behind her right shoulder"),
    ("shoulder", "phone in his hand just behind her right shoulder, pointed past it"),
]

# What the same clauses ask of the HORIZONTAL alone, for `--question side`. A
# form whose height is verified carries no horizontal at all - `Overhead camera
# directly above her` is answered `overhead` by the position question whether the
# camera ended up over her face or over her heels, so the one thing a tail form
# is testing is the one thing that question cannot see. `None` means the clause
# asks nothing horizontal and the arm is scored on the position question only.
SIDE_ASKED = [
    ("behind", "overhead camera directly above her and behind her head"),
    (None, "overhead camera directly above her"),
    ("side", "high camera looking steeply down at her from her right side"),
    (None, "high camera looking steeply down at her"),
    ("front", "low-angle shot from the floor at her feet"),
    ("behind", "low-angle shot from the floor behind her"),
    ("front", "low-angle shot from the floor in front of her"),
    ("behind", "taken from behind her left shoulder"),
    ("behind", "taken from behind her right shoulder"),
    ("behind", "taken from directly behind her"),
    ("side", "taken from her right side"),
    ("side", "taken from her left side"),
    ("front", "taken from her right front"),
    ("front", "taken from directly in front of her"),
]

# One word back, from a closed list, with the list defined in terms of what is
# visible rather than in the words the prompt used — a judge handed the prompt's
# own vocabulary can match it on the words alone.
QUESTION = """Look at this photograph and answer with ONE word and nothing else.

Where was the camera in relation to the woman when it was taken?

front — you see the front of her body and her face is toward the lens
side — you see her from the left or the right, her body roughly in profile
shoulder — you see her from behind and slightly to one side, her back three-quarters turned
behind — you see her back, the camera directly behind her
overhead — the camera is clearly above her looking down, a high angle
floor — the camera is clearly below her looking up, a low angle

If the camera is both high or low AND to one side, answer with the height:
overhead or floor. Answer with exactly one of: front, side, shoulder, behind,
overhead, floor."""

WORDS = ("front", "side", "shoulder", "behind", "overhead", "floor")

# The second question, for the one family the first cannot resolve. Asked for a
# full profile the sampler renders a three-quarter turn, and the six words above
# have no name for that, so the judge is forced to call it front or side and the
# miss is invisible. This asks only how far her body is turned, and it is the
# question to use when every arm asks for the same position and what is being
# compared is the wording. `TURN_WORDS` is ordered longest-first so `threequarter`
# is not eaten by `three` matching somewhere earlier.
TURN = """Look at this photograph and answer with ONE word and nothing else.

How far is the woman's body turned away from the camera? Judge it by her torso,
not by her face.

facing — her chest is square to the camera, both shoulders equally visible
threequarter — her body is turned part way, one shoulder nearer the camera than
  the other, but both her breasts and the front of her chest are still visible
profile — her body is turned a full ninety degrees, her chest pointing at the
  edge of the frame, her far shoulder hidden behind the near one
back — she is turned away, you see her back

Answer with exactly one of: facing, threequarter, profile, back."""

TURN_WORDS = ("threequarter", "three-quarter", "profile", "facing", "back")

# The third question, and the one a tail form is shot for. The position question
# is told to answer with the HEIGHT whenever a photograph is both high and to one
# side, which is right when the catalogue's off-eye forms carry no horizontal -
# and blind to the only thing that changes when one is hung on the end. So this
# asks the horizontal alone and says nothing about height. Left and right are one
# word on purpose: a judge that has to tell her left from her right is answering a
# harder question than the one being asked.
SIDE = """Look at this photograph and answer with ONE word and nothing else.

Ignore how high or low the camera is. Going around her, which way was it facing
her from?

front - you see the front of her body, her chest and the front of her legs
side - you see her from her left or her right, neither her front nor her back
  squarely toward the lens
behind - you see her back, the camera is behind her

Answer with exactly one of: front, side, behind."""

SIDE_WORDS = ("front", "side", "behind")

# The fourth question, and it exists for `candid` alone. That manner's line says
# the phone is almost never in the picture - `a gadget floating in them is as
# broken as forty studio ones` - and its camera clauses are the one place the
# word `phone` is unavoidable, because the position IS the phone. So every
# candid form has to be scored twice: did it reach the position, and did it
# paint the device. Nothing here says what was asked for, the same way the other
# three say nothing.
DEVICE = """Look at this photograph and answer with ONE word and nothing else.

Is a phone, a camera or any other handheld device visible anywhere in the image,
including in a mirror or in her hand?

yes - a phone or camera is visible somewhere in the image
no - no phone or camera is visible

Answer with exactly one of: yes, no."""

DEVICE_WORDS = ("yes", "no")

# The two candid clauses that put the phone in her hand facing her, where the
# manner allows it to be seen. Every other clause - including the ones where the
# phone is doing the photographing from a shelf, the carpet or his hand - asks
# for no device in the frame at all, so `no` is the default and this list is the
# exception.
# The fifth question, for the kiss frame. The eyes are the whole reason that
# frame is planned in code rather than asked for in prose - a kiss blown at the
# camera renders easily and the eyes come back open - so this asks the eyes alone
# and says nothing about lips, hands or where the camera is.
KISS = """Look at this photograph and answer with ONE word and nothing else.

What are the woman's eyes doing?

closed - both of her eyes are shut
wink - one eye is shut and the other is open
open - both of her eyes are open

Answer with exactly one of: closed, wink, open."""

KISS_WORDS = ("closed", "wink", "open")

# What the line asked of the eyes, read off the wording KISS_FRAMES hands over.
KISS_ASKED = [
    ("closed", "her eyes are completely closed"),
    ("wink", "she is winking"),
    ("open", "both eyes open and looking straight at the lens"),
]

DEVICE_YES = (
    "phone held out at arm's length in front of her face",
    "mirror selfie, the phone up in her right hand",
    "mirror selfie with her back to the mirror, looking over her shoulder",
)


def asked_of(prompt: str, table: list | None = None) -> str | None:
    """The answer the LINE asked for, read off the line itself.

    `None` back from the side table is not a failure to match: it is a clause
    that asks nothing horizontal, and the caller skips the photograph rather than
    scoring it against an expectation nobody wrote.
    """
    low = " ".join(prompt.split()).lower()
    for family, opening in (table or ASKED):
        if opening in low:
            return family
    return "?"


def post(base: str, path: str, body: dict | None = None, tries: int = 6) -> dict:
    """Retried with a growing wait, because a judging pass is seventy calls to a
    hosted model and the connection resets constantly — one run died on the first
    photograph, a second died on the ninth after three resets in a row, and every
    one of those calls succeeded on a later attempt. Six tries and a backoff, not
    three and a flat five seconds: the resets arrive in bursts, so what a retry
    has to outlast is the burst.

    ONLY for calls that can be made twice. A reset can arrive after the server
    has already acted, so a retried POST that creates something creates it again:
    retrying `POST /api/sessions` through here made two extra draft sessions
    before the one that came back. Pass `tries=1` for anything that writes."""
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(tries):
        req = urllib.request.Request(base + path, data, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read().decode())
        except Exception as exc:  # noqa: BLE001 - any transport failure is worth one more go
            if attempt == tries - 1:
                raise
            print(f"    retrying after {type(exc).__name__}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def get(base: str, path: str) -> dict:
    # Through `post`'s retry with no body: the resets come from the LOCAL backend
    # while it is running a render queue, not from the hosted judge, so a plain
    # read of the session is exactly as likely to be dropped as a judging call.
    return post(base, path, None)


def judge(base: str, shot_id: int, question: str = QUESTION, words: tuple = WORDS) -> str:
    lines = post(base, "/api/enhance",
                 {"instruction": question, "shot_id": shot_id, "n": 1})["lines"]
    said = (lines[0].get("prompt") if lines else "") or ""
    # The model answers with the word, sometimes inside a sentence. First hit wins,
    # and an answer with none of the six is kept as itself so it shows up as a miss
    # rather than being silently scored.
    hit = re.search("|".join(words), said.lower())
    return hit.group(0) if hit else f"unreadable:{said[:40]!r}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", type=int)
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--question", choices=("position", "turn", "side", "device", "kiss"),
                    default="position",
                    help="position = which side of her the camera stands on, heights winning "
                         "over horizontals; turn = how far her body is turned, which is the only "
                         "way to see a three-quarter rendered for a profile; side = the "
                         "horizontal alone, ignoring height, which is the only way to see "
                         "whether the tail of an off-eye form landed; device = whether a phone "
                         "was painted into the photograph, which every candid form has to be "
                         "scored on as well as on its position")
    ap.add_argument("--repeat", type=int, default=1,
                    help="judge each photograph this many times; the judge has its own "
                         "variance and one pass cannot see it")
    args = ap.parse_args()

    shots = [s for s in get(args.base, f"/api/sessions/{args.session}")["shots"] if s.get("filename")]
    if not shots:
        print("no finished photographs in that session")
        return 1

    turn = args.question == "turn"
    question, words = {
        "turn": (TURN, TURN_WORDS),
        "side": (SIDE, SIDE_WORDS),
        "device": (DEVICE, DEVICE_WORDS),
        "kiss": (KISS, KISS_WORDS),
    }.get(args.question, (QUESTION, WORDS))

    hits, rows, skipped = 0, [], 0
    for shot in shots:
        # On the turn question every arm asks for the same thing and the label
        # carries which wording asked for it, so the line is not what is compared.
        if turn:
            want = "profile"
        elif args.question == "kiss":
            want = asked_of(shot["prompt"], KISS_ASKED)
        elif args.question == "device":
            low = " ".join(shot["prompt"].split()).lower()
            want = "yes" if any(c in low for c in DEVICE_YES) else "no"
        elif args.question == "side":
            want = asked_of(shot["prompt"], SIDE_ASKED)
        else:
            want = asked_of(shot["prompt"])
        # A clause with no horizontal in it is not scored on the horizontal.
        if want is None:
            skipped += 1
            continue
        saw = [judge(args.base, shot["id"], question, words) for _ in range(args.repeat)]
        # A photograph counts as obeyed when the majority of passes agree with the
        # line. At --repeat 1 that is simply the one answer.
        agreed = sum(1 for s in saw if s == want)
        ok = agreed * 2 > len(saw)
        hits += ok
        rows.append((shot.get("shot_label") or shot["shot_index"] + 1, want, saw, ok))
        print(f'{str(rows[-1][0]):>4} | asked {want:<9} | saw {", ".join(saw):<28} | {"OK" if ok else "--"}')

    print(f"\nobeyed {hits}/{len(rows)}"
          + (f" ({skipped} skipped: the clause asks nothing horizontal)" if skipped else ""))
    by_family: dict[str, list[int]] = {}
    for _, want, _, ok in rows:
        by_family.setdefault(want, []).append(ok)
    for family, oks in sorted(by_family.items()):
        print(f"  {family:<9} {sum(oks)}/{len(oks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
