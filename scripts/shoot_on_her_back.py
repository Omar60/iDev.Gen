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

from shoot_directed_poses import BENCH, SETTINGS, create_session, prompt_for

PHONE = ("She lies on her back on the bed with one arm above her head and the "
         "other holding her phone over her chest, her back arched.")

# The phone removed and the hand given somewhere plain to be. Everything else --
# the bed, the arm above her head, the arch, the word order -- is untouched, so
# the pair differs by the phone and by nothing else.
NO_PHONE = ("She lies on her back on the bed with one arm above her head and the "
            "other resting on her chest, her back arched.")

ARMS = [("phone", PHONE), ("no-phone", NO_PHONE)]

SEEDS = [770901001, 770901002, 770901003, 770901004, 770901005,
         770901006, 770901007, 770901008, 770901009, 770901010]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()

    bench = BENCH["candid"]
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

    shots = [
        {"label": f"{label}-s{i + 1}", "prompt": prompt_for(wording, bench),
         "verbatim": True, "seed": seed, "count": 1}
        for label, wording in ARMS
        for i, seed in enumerate(SEEDS)
    ]

    for label, wording in ARMS:
        print(f"{label:9} {wording}")
    print(f"\n{len(ARMS)} arms x {len(SEEDS)} shared seeds = {len(shots)} photographs")

    if args.dry_run:
        print("\n--- the first line, in full ---")
        print(shots[0]["prompt"])
        return 0

    out = create_session(args.base, "ON HER BACK - phone vs no phone, 10 paired seeds",
                         shots, manner=bench["manner"])
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
