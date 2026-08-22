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
    ("overhead", "overhead camera directly above her"),
    ("overhead", "high camera looking steeply down at her"),
    ("floor", "low-angle shot from the floor at her feet"),
    ("shoulder", "taken from behind her left shoulder"),
    ("behind", "taken from directly behind her"),
    ("side", "taken from her right side"),
    ("side", "taken from her left side"),
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


def asked_of(prompt: str) -> str:
    """The family the LINE asked for, read off the line itself."""
    low = " ".join(prompt.split()).lower()
    for family, opening in ASKED:
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
    ap.add_argument("--question", choices=("position", "turn"), default="position",
                    help="position = which side of her the camera stands on; turn = how far her "
                         "body is turned, which is the only way to see a three-quarter rendered "
                         "for a profile")
    ap.add_argument("--repeat", type=int, default=1,
                    help="judge each photograph this many times; the judge has its own "
                         "variance and one pass cannot see it")
    args = ap.parse_args()

    shots = [s for s in get(args.base, f"/api/sessions/{args.session}")["shots"] if s.get("filename")]
    if not shots:
        print("no finished photographs in that session")
        return 1

    turn = args.question == "turn"
    question, words = (TURN, TURN_WORDS) if turn else (QUESTION, WORDS)

    hits, rows = 0, []
    for shot in shots:
        # On the turn question every arm asks for the same thing and the label
        # carries which wording asked for it, so the line is not what is compared.
        want = "profile" if turn else asked_of(shot["prompt"])
        saw = [judge(args.base, shot["id"], question, words) for _ in range(args.repeat)]
        # A photograph counts as obeyed when the majority of passes agree with the
        # line. At --repeat 1 that is simply the one answer.
        agreed = sum(1 for s in saw if s == want)
        ok = agreed * 2 > len(saw)
        hits += ok
        rows.append((shot.get("shot_label") or shot["shot_index"] + 1, want, saw, ok))
        print(f'{str(rows[-1][0]):>4} | asked {want:<9} | saw {", ".join(saw):<28} | {"OK" if ok else "--"}')

    print(f"\nobeyed {hits}/{len(shots)}")
    by_family: dict[str, list[int]] = {}
    for _, want, _, ok in rows:
        by_family.setdefault(want, []).append(ok)
    for family, oks in sorted(by_family.items()):
        print(f"  {family:<9} {sum(oks)}/{len(oks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
