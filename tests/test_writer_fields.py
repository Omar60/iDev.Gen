"""The writer's field set, and the two names that stay out of it.

Phase 9. The shoot line arrives in fields rather than in one string, and the
list is bound in three places at once: `SHOOT_FIELDS` in the frontend, which is
what the writer is asked for; `BLOCK_HEADINGS` in the backend, which is what the
joiner puts above each one; and the JSON skeleton inside the instruction, which
is the example the writer actually copies.

`tests/test_enhance.py` already binds the first two and holds `technique` out of
the third. This file is about what must NOT be a field: the look defines the
hair and the makeup for the whole session, and a writer that names either one
answers a question that has already been answered.
"""
from __future__ import annotations

import json
import pathlib
import re

import enhance

KINDS = (pathlib.Path(__file__).resolve().parents[1] / "frontend/src/kinds.js").read_text(
    encoding="utf-8")


def _shoot_fields() -> list[str]:
    listed = re.search(r"SHOOT_FIELDS\s*=\s*\[(.*?)\]", KINDS, re.S)
    assert listed, "SHOOT_FIELDS is no longer a literal array in kinds.js"
    return re.findall(r"'([^']+)'", listed.group(1))


def _skeleton_keys() -> list[str]:
    skeleton = re.search(r"THE ELEVEN FIELDS\.(.*?)one object per photograph", KINDS, re.S)
    assert skeleton, "the JSON skeleton is no longer introduced as THE ELEVEN FIELDS"
    return [k for k in re.findall(r'"(\w+)":', skeleton.group(1)) if k != "photographs"]


def test_hair_and_makeup_are_not_fields_anywhere_the_writer_can_see():
    """Three lists and the same absence in all of them.

    The session's look carries the hair and the makeup and the app prepends it to
    every line of the shoot, so a field for either one is a second answer to a
    question already answered - and the two answers are written by different
    things, so they drift within one session.

    Checked on the KEY and never on the word: the instruction has to be able to
    SAY `hair` in order to forbid writing it, and a test that banned the word
    would delete the prohibition it exists to protect.
    """
    for name in ("hair", "makeup"):
        assert name not in _shoot_fields(), f"{name} is a field the writer is asked for"
        assert name not in enhance.BLOCK_HEADINGS, f"{name} has a heading in the joiner"
        assert name not in _skeleton_keys(), f"{name} is in the JSON skeleton"


def test_the_instruction_still_forbids_writing_the_hair_and_the_makeup():
    """The absence above is only half of it.

    A field nobody asks for is still written when nothing says not to: the writer
    fills a photograph with what a photograph has in it. So the prohibition is
    the other half, and it is asserted here rather than assumed - the sentence
    that carries it also names the room and the light, and a later pass that
    trims the sentence for length would take the hair with it.
    """
    forbidden = re.search(r"Never write the hair, the makeup, the room or the light",
                          KINDS)
    assert forbidden, "the instruction no longer forbids writing the hair and the makeup"


def test_a_composed_line_answers_the_hair_once(client, seeded):
    """End to end: the look says it, the line carries it, and nothing repeats it.

    The composed prompt is the look plus the drawn components, and the components
    come from the catalogue - so this is the check that no part of the composed
    path adds a second hair clause. It is the observable form of the rule above:
    the writer's instruction can forbid whatever it likes, and what matters is
    what reaches the sampler.
    """
    look = "Her hair is tied back in a loose knot, and the light is a bare bulb overhead."
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "hair once", "look": look,
        "manner": "directed", "checkpoint": "test-checkpoint", "shots": [],
    }).json()["id"]

    import db
    for concept, slot, wording in (("cam-hair", "camera", "Taken from her left side"),
                                   ("act-hair", "act", "she stands with her weight on one hip"),
                                   ("frame-hair", "framing", "full body")):
        db.run(
            "INSERT INTO component (concept_key, slot, manner, family, faces, wording, "
            "judge_label, cameras, needs, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            concept, slot, "directed", "", "", wording, f"{concept} judge", "", "", db.now())

    def part(key, text):
        return {"key": key, "wordings": [{"key": key, "text": text}]}

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": part("cam-hair", "Taken from her left side"),
        "act": part("act-hair", "she stands with her weight on one hip"),
        "framing": part("frame-hair", "full body"),
        "count": 1, "mode": "exploratory"})
    assert r.status_code == 200, r.text

    prompt = db.one("SELECT prompt FROM shot WHERE session_id=?", sid)["prompt"]
    assert prompt.lower().count("hair") == 1, prompt
