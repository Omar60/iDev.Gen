"""Judge one session's cell blind, through the app's own vision path.

`judge_camera.py` asks one hand-written question with a hand-written vocabulary
and scores the answer here in the script. This asks the question the CATALOGUE
holds: `GET /api/sessions/{id}/judge-pass?slot=X` hands back the deck and the
readings for that slot, the model picks one, and the answer is posted to
`POST /api/shots/{id}/judge` so it lands on the cell. It is the same pass the
`#/judge` screen serves an operator, driven by the vision model instead.

Blind means blind: the model is handed one photograph and the reading labels,
lettered, and nothing else -- no prompt, no wardrobe, no session name, not even
which slot the labels describe beyond what the labels themselves say. A judge
shown the line agrees with the line.

## The control arm is not optional

[[idevgen-judging-run-traps]] cost this project three separate judge bugs that
all failed toward zero and one that failed toward a single answer, and the
control arm is what caught every one. `--control` takes shot ids from OTHER
sessions whose correct answer is KNOWN TO BE DIFFERENT, interleaved into the
same run and scored against what they should say. They are never posted: their
components are empty, the judge endpoint would refuse them, and a control that
writes to a cell is not a control.

Run it with controls or do not quote the number.

## One slot can ask more than one question

`--axis` picks which. Directed's camera vocabulary holds where the camera stood
AND how high it was, and both are true of every photograph: asked as one menu it
came back `hip-level` 6 against `side-level` 1 on photographs whose camera is
side-on in all ten. The endpoint serves one axis at a time and drops the
photographs whose drawn family belongs to another question, so what comes back
is a menu with one true answer in it. A slot with a single question -- every act
and framing vocabulary, and candid's cameras -- leaves it empty.

## The judge samples, so it is asked more than once

`backend/enhance.py` calls the model at `temperature: 0.8`, which is right for
the writer and wrong for a judge: **the same photograph asked twice gives
different answers.** It cost this script its first camera result -- a rehearsal
read `side-level` 7 / `over-shoulder` 3 and the recording run of the same deck,
same seed, same photographs read `side-level` 10.

So `--repeat` asks each photograph N times and takes the majority, which is what
`judge_camera.py` has always done (three passes a photograph). A photograph with
no majority is recorded as `unreadable` and posted for nothing, because a coin
toss is not a verdict.

**Three passes is not enough for a fine discrimination.** Four three-pass runs
over two decks of the same camera gave 7, 8, 9 and 9 of 10, with a DIFFERENT
photograph missing each time -- and the verified/dead bar is 8. A cell judged at
three passes near that bar is not a verdict; one of these was filed `dead` on
the low end of that spread and came back 10/10 at five. Default is 5. Three is
still fine where the controls never waver, which so far means act and framing.

## Two things held on purpose

The reading order is SHUFFLED per photograph on a fixed seed. A model that
always answers the first option scores the deck's order otherwise, and the
shuffle turns that failure into noise instead of a result.

The answer is clamped to the letters offered. Anything else is recorded as
`unreadable` and posted for nothing -- a judge that cannot answer is not a miss,
and scoring it as one is how a broken run reads as a dead cell.

Usage:
  python scripts/judge_cell.py 382 --slot act --control 9192 9200 --dry-run
  python scripts/judge_cell.py 382 --slot act --control 9192 9200 --post
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# `urllib` has dropped this connection mid-batch twice, on two different days
# ([[idevgen-judging-run-traps]]), and curl carried the identical payload
# through both times. So every call in this file is curl.
def call(base: str, method: str, path: str, body: dict | None = None,
         timeout: int = 180) -> dict:
    cmd = ["curl", "-s", "-m", str(timeout), "-X", method, base + path]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"_unparsed": out[:400]}


def ask(base: str, shot_id: int, readings: list[dict], rng: random.Random) -> str:
    """One photograph, one lettered menu, one letter back. Returns a reading key.

    The menu is shuffled per photograph so a model that favours the first option
    cannot score the deck's ordering, and the letter is mapped back here rather
    than the model being asked for the key -- a key is a word from the
    catalogue, and handing it over is handing over half the question.
    """
    menu = list(readings)
    rng.shuffle(menu)
    lines = "\n".join(f"{LETTERS[i]}. {r['label']}" for i, r in enumerate(menu))
    instruction = (
        "Look at this photograph. Below are statements about it. Exactly one of "
        "them describes what you see, or none of them does.\n\n"
        f"{lines}\n\n"
        "Answer with the single letter of the statement that is true of this "
        "photograph, or the word NONE if none of them is true. Answer with the "
        "letter alone and nothing else.")
    out = call(base, "POST", "/api/enhance",
               {"instruction": instruction, "shot_id": shot_id, "n": 1,
                "allowed": [LETTERS[i] for i in range(len(menu))] + ["NONE"]})
    said = ((out.get("lines") or [{}])[0].get("prompt") or "").strip().upper()
    if not said:
        return f"unreadable:{json.dumps(out)[:60]}"
    if said.startswith("NONE"):
        return ""            # "none or cannot tell", which the endpoint counts
    head = said[0]
    if head in LETTERS[:len(menu)]:
        return menu[LETTERS.index(head)]["key"]
    return f"unreadable:{said[:40]!r}"


def majority(passes: list[str]) -> str:
    """The answer more than half the passes agree on, or `unreadable`.

    A strict majority and not a plurality: two answers out of three is a judge
    that read the photograph, one-one-one is a coin toss, and recording a coin
    toss as a verdict is how a run comes back with numbers that look like
    results ([[idevgen-judge-question-design]]).
    """
    counts: dict[str, int] = {}
    for p in passes:
        counts[p] = counts.get(p, 0) + 1
    best, n = max(counts.items(), key=lambda kv: kv[1])
    if n * 2 <= len(passes):
        return f"unreadable:no majority in {passes}"
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", type=int)
    ap.add_argument("--slot", required=True, choices=("act", "camera", "framing"))
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--control", type=int, nargs="*", default=[],
                    help="shot ids from other sessions whose answer should DIFFER; asked, never posted")
    ap.add_argument("--expect", default="",
                    help="the reading key the controls should return, if they all share one")
    ap.add_argument("--axis", default="",
                    help="which question to ask, when the slot asks more than one "
                         "(directed camera: position | height | picture)")
    ap.add_argument("--repeat", type=int, default=5,
                    help="passes per photograph; the strict majority is the answer (the model samples at 0.8)")
    ap.add_argument("--seed", type=int, default=903)
    ap.add_argument("--post", action="store_true",
                    help="record the answers; without it the run is a read-only rehearsal")
    args = ap.parse_args()

    query = f"slot={args.slot}" + (f"&axis={args.axis}" if args.axis else "")
    deck = call(args.base, "GET", f"/api/sessions/{args.session}/judge-pass?{query}")
    if "shots" not in deck:
        print("judge-pass refused:", json.dumps(deck)[:300])
        return 1
    shots, readings = deck["shots"], deck["readings"]
    print(f"session {args.session}, slot {args.slot}"
          f"{', axis ' + args.axis if args.axis else ''}: {len(shots)} photographs, "
          f"{len(readings)} readings, {len(args.control)} controls")
    for r in readings:
        print(f"  {r['key']:18} {r['label'][:78]}")
    if not args.control:
        print("\nNO CONTROL ARM. Every judge bug this project has had failed toward "
              "one answer, and only a control caught it. Pass --control.")
    print()

    rng = random.Random(args.seed)
    # Controls are asked in the same run and in the same shuffle stream as the
    # real deck, not in a tidy block afterwards: a model whose answers drift over
    # a long run should drift across both.
    # A shot the deck already serves is not also a control. Passing the whole
    # deck as `--control` is how a cell gets asked a second time without being
    # recorded, and any photograph the pass left unjudged would otherwise be
    # asked twice in one run.
    controls = [sid for sid in args.control if sid not in set(shots)]
    order = [(sid, False) for sid in shots] + [(sid, True) for sid in controls]
    rng.shuffle(order)

    answers: dict[int, str] = {}
    for sid, is_control in order:
        passes = [ask(args.base, sid, readings, rng) for _ in range(args.repeat)]
        key = majority(passes)
        answers[sid] = key
        spread = "" if len(set(passes)) == 1 else f"   from {passes}"
        print(f"  shot {sid}{' (control)' if is_control else '':10} -> "
              f"{key or '(none)'}{spread}")

    real = [answers[s] for s in shots]
    tally: dict[str, int] = {}
    for key in real:
        tally[key or "(none)"] = tally.get(key or "(none)", 0) + 1
    print("\ndeck:", ", ".join(f"{k} {v}" for k, v in sorted(tally.items())))
    if controls:
        ctl = [answers[s] for s in controls]
        print("controls:", ", ".join(c or "(none)" for c in ctl))
        if args.expect:
            hits = sum(c == args.expect for c in ctl)
            print(f"controls answering {args.expect!r}: {hits}/{len(ctl)}")
        if set(ctl) <= set(real) and len(set(real)) == 1:
            print("CONTROL FAILED: the controls answered the same as the deck. "
                  "The judge is not discriminating and the deck number means nothing.")

    if not args.post:
        print("\nnothing recorded; re-run with --post to write these to the cell")
        return 0

    for sid in shots:
        key = answers[sid]
        if key.startswith("unreadable:"):
            print(f"  shot {sid}: not posted, {key}")
            continue
        out = call(args.base, "POST", f"/api/shots/{sid}/judge", {args.slot: key})
        print(f"  shot {sid}: posted {key or '(none)'} -> {json.dumps(out)[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
