"""Task 4.1: the composer reproduces the arrangements script's hand-built line.

The script `scripts/shoot_arrangements.py` is one of eight hand-built controls
for the composer (design.md:373-376, migration plan step 4). 4.1 says: point
one at the composer and verify the line does not change. The change in the
script was the f-string for `prompt_for`, which used to put the framing and
act blocks BEFORE the wardrobe block and prefixed them with `Angle & Framing:`
and `Act:` headings; the composer puts the trio (camera, act, framing) LAST
and writes them flat, no headings. The test pins the new line against:

  1. a direct call to `compose_shot` with the same five pieces, so a future
     "let me reorder the join" inside `compose_shot` is caught here;
  2. a hand-built reference string written out as the same `_sentences`
     calls `_compose` makes, so the format the script's new `prompt_for`
     reproduces is visible in the test rather than implicit in a re-spelling
     of the composer's output.

The test does not need ComfyUI (no `queue_prompt`, no `TestClient`) or the
DB (no session is opened) — `compose_shot` is a pure join, and the script's
`prompt_for` is a thin wrapper around it. The conftest's `IDEVGEN_DATA_DIR`
is what keeps the import of `backend.main` from touching the developer's
real database; no test in this file opens a connection.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from main import _sentences, compose_shot  # noqa: E402

from shoot_arrangements import (  # noqa: E402
    FRAMING, LOOK, MODEL, REST, _act_concept, _shot, prompt_for,
)

# A representative trio, the one the script's `astride` arm shoots: the first
# family the arrangement accepts (`front`) and the first position of it.
# Held as constants so the test does not have to import the catalogue (the
# test is independent of the script's `cat()` call and of the network).
CAMERA = {"key": "front-direct",
          "slot": "camera",
          "wordings": [{"key": "front-direct",
                        "text": "Taken from directly in front of her",
                        "family": "front"}]}
ACT = {"key": "astride", "cameras": ["front", "overhead", "mirror", "pov"],
       "wordings": [{"key": "astride",
                     "text": ("She is astride him with her knees on either side of "
                              "his hips and her weight down on him, the two of them "
                              "joined, two people in frame.")}]}
FRAMING_CONCEPT = {"key": "framing",
                   "wordings": [{"key": "framing", "text": FRAMING}]}


def test_the_composer_reproduces_the_arrangements_script_hand_built_line():
    """`scripts/shoot_arrangements.prompt_for` (the script's new build path)
    produces the same line `compose_shot` produces for the same five pieces,
    and a hand-built reference written out as the same `_sentences` calls
    `_compose` makes. Three strings, all byte-equal: the script's line, the
    composer's line, and the hand-built reference.

    The hand-built reference is not a re-spelling of the composer's output —
    it is the same five `_sentences` calls the composer's `_compose`
    (`backend/main.py:1757`) and `compose_shot` (`backend/main.py:1796`) make
    in the same order, so a future "let me reorder the take" or "let me
    strip the trailing period" breaks the test on the spot, and the
    assertion that fails is the one named in tasks.md (a) and (b).
    """
    script_line = prompt_for(CAMERA, ACT, look=LOOK, wardrobe=REST)
    composer_line = compose_shot(MODEL, LOOK, REST, CAMERA,
                                 _act_concept(ACT), FRAMING_CONCEPT)

    take = _sentences(
        CAMERA["wordings"][0]["text"],
        ACT["wordings"][0]["text"],
        FRAMING,
    )
    hand_built = _sentences(MODEL["trigger"], MODEL["base_positive"],
                            LOOK, REST, take)

    assert script_line == composer_line, (
        f"script's prompt_for and compose_shot diverge:\n"
        f"  script:    {script_line!r}\n"
        f"  composer:  {composer_line!r}"
    )
    assert hand_built == composer_line, (
        f"the hand-built reference (the composer's order) and compose_shot "
        f"diverge — the join, the period, or the order has moved:\n"
        f"  hand_built: {hand_built!r}\n"
        f"  composer:   {composer_line!r}"
    )


def test_a_non_trivial_hand_built_line_really_is_what_the_composer_produces():
    """The line is 1208 bytes: a trigger, a look, a five-block wardrobe,
    and a flat take. A change to the join (the empty-piece skip, the
    `\n\n` rule in `_sentences`, the trailing period) or the order (look
    before wardrobe before take) moves the equality check above; this
    test pins the line length and the order at the boundaries so a silent
    regression to a shorter or empty output is caught before the equality
    test fires.

    The wardrobe has five sub-blocks (Subject, Second Subject, Outfit &
    Texture, Technique, Expression) separated by `\n\n` inside `REST`; the
    composer's join does not collapse them, so splitting on `\n\n` gives
    eight pieces, not four. The order at the four outer boundaries
    (trigger, look, wardrobe, take) is what this test pins, plus the
    length and the start/end substrings.
    """
    composer_line = compose_shot(MODEL, LOOK, REST, CAMERA,
                                 _act_concept(ACT), FRAMING_CONCEPT)

    assert composer_line.startswith("zchar_jir.\n\n"), (
        f"trigger is not the first block: {composer_line[:80]!r}")
    assert LOOK in composer_line, "LOOK not in line"
    assert REST in composer_line, "REST not in line"
    # The trio is flat: no `Angle & Framing:` or `Act:` headings, no
    # `\n` inside the take. The composer produces three sentences joined
    # by spaces because none of the three pieces has a `\n`.
    assert "Angle & Framing" not in composer_line, (
        "framing heading leaked into a composed line")
    assert "\nAct:" not in composer_line, "act heading leaked into a composed line"
    assert composer_line.endswith("a three-quarter photograph from the knees up."), (
        f"framing is not the last sentence: {composer_line[-80:]!r}")
    # Length pin: any future "let me drop a block" or "let me shorten the
    # look" moves this, before the equality test gets a chance to fire.
    assert len(composer_line) == 1208, len(composer_line)
    # Order pin: `LOOK` comes before `REST` and `REST` comes before the take
    # (the camera clause `Taken from directly in front of her`). A future
    # "let me put the wardrobe after the take" would invert the indices.
    assert composer_line.index(LOOK) < composer_line.index(REST) < (
        composer_line.index("Taken from directly in front of her")), (
        f"order drifted: LOOK at {composer_line.index(LOOK)}, REST at "
        f"{composer_line.index(REST)}, take at "
        f"{composer_line.index('Taken from directly in front of her')}")


def test_a_candidate_act_without_wordings_is_wrapped_into_a_concept():
    """The `CANDIDATES` block in `scripts/shoot_arrangements.py:110-142` holds
    arrangement entries without a `wordings` field — they carry an `act`
    text but not the catalogue's concept shape. `compose_shot` reads
    `act["wordings"][0]["text"]` (`backend/main.py:1814-1818`), so the
    script wraps each CANDIDATE in a single-wording concept before it goes
    in. This test exercises the wrap path (the `else` branch of
    `_act_concept`); the other test exercises the `if "wordings" in act`
    branch on an `ARRANGEMENTS` entry.

    The wrap is the half of `_act_concept` a future "let me also dereference
    the camera's family through the catalogue" or "let me lift the wrap up
    into the script's loop" can quietly break, and the only signal here is
    the same composer produces the same line for the wrapped and unwrapped
    forms. The assertion is byte-equal because the wrap is meant to be
    transparent.
    """
    # A CANDIDATE-shaped entry: no `wordings`, an `act` text, and the
    # `cameras` list the script uses to pick a camera. Held as a literal
    # so the test does not import the script's CANDIDATES list (the test is
    # independent of the script's CLI and of the catalogue).
    candidate = {"key": "under-plain",
                 "cameras": ["side", "overhead"],
                 "act": ("She is on her back with her legs open and he is over her "
                         "between them, the two of them joined, two people in frame.")}
    # The same `act` text inside an ARRANGEMENTS-shaped entry (with one
    # wording) — the form the catalogue stores. The two must produce the
    # same line through the composer: the wrap is a no-op, not a rewriter.
    catalogued = {"key": candidate["key"],
                  "wordings": [{"key": candidate["key"], "text": candidate["act"]}]}

    from_candidate = prompt_for(CAMERA, candidate, look=LOOK, wardrobe=REST)
    from_catalogue = prompt_for(CAMERA, catalogued, look=LOOK, wardrobe=REST)
    assert from_candidate == from_catalogue, (
        f"the candidate-wrap path and the catalogue path diverge:\n"
        f"  candidate: {from_candidate!r}\n"
        f"  catalogue: {from_catalogue!r}")
    # And the wrap lands the act text in the take, with the camera and the
    # framing in the same order. The act is the last block the script's
    # default `--only` lists first, and the take is the last block in the
    # line; the assertion is the act's `act` text appears in the take.
    assert candidate["act"] in from_candidate, (
        "the wrapped act text did not reach the take")


def test_the_script_stores_the_line_it_composed_and_not_the_line_composed_twice(client, seeded):
    """Task 4.2's finding, pinned where 4.1's test could not see it.

    4.1 proved `prompt_for` and `compose_shot` return the same string. That is
    a FUNCTION-level equality, and the script does not render its return value —
    it POSTs it. `_expand_shots` runs `_compose` over every take that does not
    say `verbatim`, so a line the script already composed got the trigger
    prepended a second time and reached the database 12 bytes longer than the
    composer's own (session 300 against 301). Every other `shoot_*.py` sends
    `verbatim: True`; `shoot_arrangements.py` was the one that did not.

    The second take is the control: without the flag the same prompt comes back
    changed, which is what makes the first assertion mean something.
    """
    line = prompt_for(CAMERA, ACT)
    r = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "arrangements storage",
        "look": LOOK, "wardrobe": "", "seed_mode": "fixed", "seed": 7,
        "shots": [_shot("verbatim", line, 7),
                  {"label": "composed-again", "prompt": line, "count": 1}],
    })
    assert r.status_code == 200
    stored = {x["shot_label"]: x["prompt"]
              for x in client.get(f"/api/sessions/{r.json()['id']}").json()["shots"]}

    # What the script sends is what the database holds, byte for byte.
    assert stored["verbatim"] == line

    # And without the flag it is not: the model's trigger arrives in front of a
    # line that already carries one.
    assert stored["composed-again"] != line
    assert stored["composed-again"].startswith("4da woman.")
    assert len(stored["composed-again"]) > len(line)
