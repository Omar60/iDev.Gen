"""Shoot one arrangement against each camera family that is supposed to see it.

Sessions 267 and 268 planted all six in written shoots and got one photograph
each: enough to say `ontop`, `away` and `standing` render and that `behind` never
does, and not enough to say anything about `back` and `side`. A written shoot is
also the wrong instrument for the question - every planted photograph came with
its own stage, its own wardrobe state and whatever framing the writer chose, so a
miss cannot be pinned on the arrangement.

So: the 227/228/244 protocol. One line fixed by hand, the `act` field taken from
`ARRANGEMENTS` word for word, three seeds shared, and the camera swapped through
the families that arrangement says can see it. What moves is the arrangement and
the camera; nothing else in the line does.

The wording is read out of `kinds.js` through node rather than copied here, the
way `shoot_kiss_frames.py` does it: a copy drifts, and whether THAT wording works
is the whole question.

`--only` takes arrangement keys. The default is the two that have never been
measured, plus `astride` as the control - it is 12 of 12 in sessions 265 and 266
and a flat result on it means the rig is wrong rather than the arrangements.

Usage: python scripts/shoot_arrangements.py [--only back,side] [--dry-run]

Judge it with:
    python scripts/judge_camera.py <id> --question arrangement --repeat 3
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# `backend.main` is imported lazily, inside `prompt_for`, so `--dry-run` and
# the script's CLI do not pay for the FastAPI app, the DB connection, or the
# config read on every invocation. The import is needed only at the point the
# line is built, and `tests/test_shoot_arrangements_compose.py` exercises the
# same import path with `IDEVGEN_DATA_DIR` set to a temp dir.
from shoot_camera_forms import SEEDS, SETTINGS, create_session
from shoot_candid_cameras import CANDID_CAPTURE, ROOM

LOOK = ROOM + CANDID_CAPTURE

FRAMING = "a three-quarter photograph from the knees up"

# The trigger the script has always used. A flat dict, not the app's model
# record: the script builds lines without a session, and the composer only
# reads `trigger` and `base_positive` off the dict (`backend/main.py:_compose`).
MODEL = {"trigger": "zchar_jir", "base_positive": ""}

EXPLICIT_SETTINGS = SETTINGS | {
    "checkpoint": "finepornV4INT8NVFP4BF16_v4Nvfp4.safetensors",
    "steps": 12, "sampler": "er_sde", "scheduler": "beta",
}

# `SETTINGS` is the Krea 2 mix at 8 steps, which is what every camera question in
# this project was asked on. It is here so `back` and `side` can be asked of a
# second base: they are 0 of 21 on finepornV4 across four cameras, and a failure
# on one checkpoint is a failure of that checkpoint until a second one says
# otherwise. Judge such a run with `--question act` as well - Krea 2 is not the
# base the act was ever measured on, and `no act at all` and `the wrong
# arrangement` are two different answers.
BASES = {"fineporn": EXPLICIT_SETTINGS, "krea": SETTINGS}

# Everything the arrangement does not decide, fixed. Neither body is placed here
# - where they are and which way they face is the `act` field, which is what is
# being measured - so this says only that they are naked, where the light is and
# what her face is doing.
REST = """
Subject:
Her chest is bare, her stomach bare, her hips bare, her thighs bare, her feet bare.

Second Subject:
He is naked with her, his chest bare, his stomach bare, his hips bare, his thighs bare.

Outfit & Texture:
Nude.

Technique:
Grainy, flat and overexposed, the shadows gone to noise, colour washed out.

Expression:
Her mouth is open on a sound and her eyes are half-shut."""


# THE ANCHOR ARMS. Candidate wordings that are NOT in `ARRANGEMENTS` and must not
# be until they are measured — `--only` takes their keys the same way.
#
# `back` and `side` were shot in exactly ONE wording each and died in it: 0 of 12
# and 0 of 9 on finepornV4, 0 of 12 and 0 of 8 on the Krea 2 mix, both collapsing
# into her upright on top facing the lens, which is this sampler's default for
# two bodies. The camera was swapped through four families and three; the SENTENCE
# never moved. That is the gap these arms are for.
#
# The hypothesis, and it is a different one from `write it again but harder`: the
# collapse is the sampler averaging towards its dominant mode, so what beats it is
# not more adjectives on the bodies but a THIRD THING in the sentence whose
# geometry the dominant mode cannot satisfy. A woman upright astride a man cannot
# also be lying across the edge of a bed with him standing on the floor: one of
# the two bodies is vertical and the other is horizontal, and the anchor is what
# says so. Naming the anchor is safe — a prop named in a line gets built 7 of 8,
# which is the finding that makes this affordable.
#
# So each dead arrangement gets its plain wording back as its OWN control and two
# anchored rewordings beside it, on the same cameras and the same seeds. If the
# plain one dies again and an anchored one lands, the anchor is the reason. If all
# three die together, the wording was never the problem and this arrangement is
# the base model's ceiling — which is the answer nobody has actually earned yet.
#
# The cameras are `side` and `overhead` on every arm on purpose, and `front` is
# deliberately absent: a frontal camera is the one that AGREES with the collapse
# (her upright, facing the lens), so a miss under it says nothing. A horizontal
# body against a vertical one is legible from the side and from above and nowhere
# else. Note `side` is a full profile, which Krea 2 cannot render at all — on that
# base the overhead half of each arm is the half that carries the answer.
CANDIDATES = [
    # The controls: the exact wording that died, so this rig can see it die again.
    {"key": "under-plain", "cameras": ["side", "overhead"],
     "act": "She is on her back with her legs open and he is over her between them, "
            "the two of them joined, two people in frame."},
    {"key": "spoon-plain", "cameras": ["side", "overhead"],
     "act": "They are both on their sides with him behind her and her upper leg lifted, "
            "the two of them joined, two people in frame."},
    # Her horizontal, him VERTICAL. The edge of the bed and the floor he stands on
    # are the anchor: the collapse has both bodies stacked on the same surface.
    {"key": "under-edge", "cameras": ["side", "overhead"],
     "act": "She is lying on her back across the edge of the bed with her knees up "
            "against his chest, he is standing on the floor at the edge of the bed "
            "between her legs, the two of them joined, two people in frame."},
    # The same shape hung on a piece of furniture that is not a bed, because a bed
    # is the surface the collapse already lives on and may be half the reason for it.
    {"key": "under-table", "cameras": ["side", "overhead"],
     "act": "She is lying on her back on the table with her shoulders flat on it, he "
            "is standing on the floor at the end of the table between her legs, the "
            "two of them joined, two people in frame."},
    # Both bodies horizontal, said as a fact about the mattress rather than as
    # `on their sides` — the phrase the collapse ignored twice.
    {"key": "spoon-flat", "cameras": ["side", "overhead"],
     "act": "They are both lying down flat along the mattress with their bodies "
            "horizontal, he is behind her back with his chest against it and her "
            "knees drawn up, the two of them joined, two people in frame."},
    # The pillow as the anchor, and her head on it as the thing that cannot be true
    # of a woman sitting upright.
    {"key": "spoon-pillow", "cameras": ["side", "overhead"],
     "act": "Her head is down on the pillow and her shoulder is flat on the mattress, "
            "he is lying along her back behind her with his arm over her, the two of "
            "them joined, two people in frame."},
]


def catalogue() -> dict:
    """`ARRANGEMENTS` and the positions, read from the module that owns them."""
    # A file:// URL, not a bare path: node's ESM loader reads `C:` as a protocol.
    probe = ("import { ARRANGEMENTS, POSITIONS } from "
             f"'{(ROOT / 'frontend/src/kinds.js').as_uri()}';"
             "console.log(JSON.stringify({ ARRANGEMENTS, POSITIONS }))")
    out = subprocess.run(["node", "--input-type=module", "-e", probe],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _act_concept(act: dict) -> dict:
    """Wrap an `ARRANGEMENTS` entry or a `CANDIDATES` entry in a single-wording
    concept. The catalogue entry already carries `wordings`; the `CANDIDATES`
    entries do not, and the composer reads `wordings[0]["text"]`
    (`backend/main.py:compose_shot`).
    """
    if "wordings" in act:
        return act
    return {"key": act["key"],
            "wordings": [{"key": act["key"], "text": act["act"]}]}


# The framing is carried as a per-shot string, the way `compose_shot` already
# accepts it in 3.1. The catalogue has no framing concept list of its own, and
# `FRAMING` is the constant this script has always used; a concept-shaped dict
# is what `compose_shot` expects, and the `key` is an arbitrary stable name
# (the composer reads text, not key).
_FRAMING_CONCEPT = {"key": "framing",
                    "wordings": [{"key": "framing", "text": FRAMING}]}


def _shot(label: str, prompt: str, seed: int) -> dict:
    """One take, shaped the way `/api/sessions` wants it.

    `verbatim`, like every other `shoot_*.py`: `prompt_for` already ran the
    line through the composer (4.1), and `_expand_shots` composes AGAIN unless
    the take says not to — which prepended the trigger a second time and stored
    1220 bytes where the composer stores 1208 (found in 4.2, session 300 against
    301). A function rather than a dict literal inside the loop so a test can
    call it without the catalogue and without the network.
    """
    return {"label": label, "prompt": prompt, "verbatim": True,
            "seed": seed, "count": 1}


def prompt_for(camera_concept: dict, act: dict, look: str = LOOK,
               wardrobe: str = REST) -> str:
    """Build the line through the composer.

    The composer (`backend/main.py:compose_shot`) joins the trio as a flat
    `_sentences(camera, act, framing)` and prefixes the look and the wardrobe
    in the order the design fixed (look, then wardrobe, then take). The
    hand-built control this replaces was an f-string with the framing and act
    blocks BEFORE the wardrobe and with the field headings (`Angle &
    Framing:`, `Act:`); that control is what `tests/test_shoot_arrangements_compose.py`
    pins against the composer's output.
    """
    from main import compose_shot  # lazy: see the import note up top
    return compose_shot(MODEL, look, wardrobe, camera_concept,
                        _act_concept(act), _FRAMING_CONCEPT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--only", default="under-plain,under-edge,under-table,"
                                      "spoon-plain,spoon-flat,spoon-pillow,astride",
                    help="arrangement keys, from ARRANGEMENTS or from CANDIDATES; the "
                         "default is the six anchor arms and the rig control")
    ap.add_argument("--manner", default="directed",
                    help="whose camera catalogue the families are drawn from; the anchor "
                         "arms want `directed`, the only one with a `side` in it")
    ap.add_argument("--base-model", choices=tuple(BASES), default="fineporn",
                    help="which checkpoint to ask it of; `krea` is the Krea 2 mix at 8 steps, "
                         "the base every camera question was asked on")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    read = catalogue()
    positions = read["POSITIONS"][args.manner]
    wanted = [k.strip() for k in args.only.split(",") if k.strip()]
    pool = read["ARRANGEMENTS"] + CANDIDATES
    arrangements = [a for a in pool if a["key"] in wanted]
    missing = set(wanted) - {a["key"] for a in arrangements}
    if missing:
        print(f"no arrangement named {sorted(missing)} - it may have been taken out of the pool")
        return 1

    shots = []
    for a in arrangements:
        # One camera per allowed family, and the first form of each: which form
        # inside a family is a question the camera catalogue already answered.
        # The catalogue reshape (1.1) moved `family` onto the wording; every
        # concept today has one wording, so the family's first is the family's
        # only and the loop matches the old behaviour.
        seen, cameras = set(), []
        for p in positions:
            family = p["wordings"][0]["family"]
            if family in a["cameras"] and family not in seen:
                seen.add(family)
                cameras.append(p)
        # A family this manner has no position for is a silent no-op: the arm
        # would just not be shot and the run would look complete. `side` is the
        # one that does it - it exists in `directed` alone.
        assert cameras, (f"{a['key']} wants {a['cameras']} and the `{args.manner}` "
                         f"catalogue has none of them")
        print(f"{a['key']:<13} {len(cameras)} cameras: {', '.join(sorted(seen))}")
        for p in cameras:
            family = p["wordings"][0]["family"]
            label = f"{a['key']}-{family}"
            # `p` is a position from `POSITIONS[manner]` and is already a camera
            # concept (`{key, slot, wordings, family}`); the composer reads
            # `wordings[0]["text"]` off it, so no rewrap is needed.
            prompt = prompt_for(p, a)
            for seed in SEEDS:
                shots.append(_shot(label, prompt, seed))

    print(f"\n{len(shots)} photographs, {len(SEEDS)} seeds each, on {args.base_model}")
    if args.dry_run:
        print("\n--- the first prompt ---")
        print(shots[0]["prompt"])
        return 0

    out = create_session(args.base,
                         "ARRANGEMENTS - one line, the act and the camera swapped"
                         f" [{args.base_model}]",
                         shots, BASES[args.base_model])
    print(f"\nsession {out['id']} created as a draft, {len(shots)} pending")
    print(f"run it with: curl -X POST {args.base}/api/sessions/{out['id']}/run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
