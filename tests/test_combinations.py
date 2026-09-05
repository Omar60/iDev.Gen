"""Composing a recorded combination, and never drawing one.

8.11. Mining separates a camera, an act and a room that one author wrote to
agree with each other, and the agreement is what made the source entry render.
The combination is kept beside the parts so the photograph can be shot again;
these are the two halves of what "kept" means.

- It composes from the RECORD: the caller names the source entry and nothing
  else, and the app resolves the keys to catalogue rows.
- It is never DEALT: the combination store is not a source of candidates, so a
  run whose caller did not offer these rows cannot produce them, and a run with
  nothing in its pool is refused rather than filled from the record.
"""
from __future__ import annotations

import json

import pytest

import db
import main
from backend.mining import (
    MINED_COMBINATIONS_FILE,
    record_combinations,
    save_mined_combinations,
    split_fused_entry,
)

IDENTIFIER = "invented_fused_combo_01"
CAMERA_KEY = "mined-cam-01"
ACT_KEY = "mined-act-01"
ROOM_KEY = "mined-room-01"
CAMERA_TEXT = "low angle from the foot of the bed"
ACT_TEXT = "kneeling upright with both hands behind her head"


@pytest.fixture
def combination_on_disk():
    """The record in the live data directory, and the file removed afterwards.

    Written where `load_mined_combinations` reads, because a fixture that
    monkeypatched the loader would test the route against a record the app
    cannot actually read.
    """
    path = main.DATA_DIR / MINED_COMBINATIONS_FILE
    main.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({IDENTIFIER: {"camera": CAMERA_KEY, "act": ACT_KEY, "room": ROOM_KEY}}),
        encoding="utf-8",
    )
    yield
    path.unlink(missing_ok=True)


def _rows(manner: str = "directed") -> None:
    for concept, slot, wording in ((CAMERA_KEY, "camera", CAMERA_TEXT),
                                   (ACT_KEY, "act", ACT_TEXT)):
        db.run(
            "INSERT INTO component (concept_key, slot, manner, family, faces, wording, "
            "judge_label, cameras, needs, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            concept, slot, manner, "", "", wording, f"{concept} judge", "", "", db.now(),
        )


def _session(client, seeded, room_key: str = ROOM_KEY, manner: str = "directed") -> int:
    return client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "combination",
        "look": "A narrow attic room with a sloped ceiling.", "room_key": room_key,
        "manner": manner, "checkpoint": "test-checkpoint", "shots": [],
    }).json()["id"]


def test_a_recorded_combination_composes_from_its_record_alone(client, seeded, combination_on_disk):
    """The caller names the entry; the app supplies the words.

    The payload carries one field. A route that took the rows would be hand
    assembly with a lookup in front of it, and it would compose whatever was
    typed rather than what the source entry was - which is the whole point of
    keeping the combination at all.

    The composed line is asserted on the WORDINGS and the stored components on
    the KEYS, because those are two different failures: a route that queued the
    right keys with the wrong text, and one that queued the right text
    attributed to nothing.
    """
    _rows()
    sid = _session(client, seeded)

    r = client.post(f"/api/sessions/{sid}/compose-combination", json={"identifier": IDENTIFIER})
    assert r.status_code == 200, r.text

    shot = db.one("SELECT * FROM shot WHERE session_id=?", sid)
    assert CAMERA_TEXT in shot["prompt"], shot["prompt"]
    assert ACT_TEXT in shot["prompt"], shot["prompt"]
    components = json.loads(shot["components"])
    assert components["camera"]["concept"] == CAMERA_KEY
    assert components["act"]["concept"] == ACT_KEY
    # No framing: the source library has no crop field, which is what session
    # 391 measured. A combination that invented one would compose a photograph
    # the entry never was.
    assert components["framing"]["concept"] == ""


def test_a_recorded_combination_is_never_dealt_by_a_session_draw(client, seeded, combination_on_disk):
    """The store is not a candidate source, asserted on the POOL SIZE.

    Both trios are verified cells here - the session's own and the mined pair
    against the same framing - and only the own one is offered as a candidate.
    A run asking for TWO distinct trios out of a pool of one is refused, and
    that refusal is the observable difference: a draw that read the combination
    store would have two trios and would queue them.

    The first version of this test asserted the drawn shot was not the mined
    pair, and a break that fed the whole store into the candidate list PASSED
    it - the mined trio had no verified cell, so the cell gate was hiding the
    fact that nothing else was. See [[idevgen-test-that-cannot-fail]].
    """
    _rows()
    sid = _session(client, seeded)
    for cam, act in (("cam-own", "act-own"), (CAMERA_KEY, ACT_KEY)):
        db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, manner, "
               "checkpoint, judged, arrived) VALUES (?,?,?,?,?,?,?)",
               cam, act, "frame-own", "directed", "test-checkpoint", 10, 8)
    candidates = {
        "camera": [{"key": "cam-own", "wordings": [{"key": "cam-own", "text": "from her left"}]}],
        "act": [{"key": "act-own", "wordings": [{"key": "act-own", "text": "she leans on the wall"}]}],
        "framing": [{"key": "frame-own", "wordings": [{"key": "frame-own", "text": "full body"}]}],
    }

    one = client.post(f"/api/sessions/{sid}/compose-run",
                      json={"count": 1, "candidates": candidates})
    assert one.status_code == 200, one.text
    drawn = [json.loads(row["components"]) for row in
             db.q("SELECT components FROM shot WHERE session_id=?", sid)]
    assert drawn, "the run queued nothing, so it proves nothing about what it draws"
    for shot in drawn:
        assert shot["camera"]["concept"] != CAMERA_KEY, shot
        assert shot["act"]["concept"] != ACT_KEY, shot

    two = _session(client, seeded)
    refused = client.post(f"/api/sessions/{two}/compose-run",
                          json={"count": 2, "candidates": candidates})
    assert refused.status_code == 422, refused.text
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", two)["n"] == 0


def test_a_combination_is_refused_in_a_session_filled_from_another_room(client, seeded, combination_on_disk):
    """The room is the session's look, and this route does not write it.

    `SessionPatch` cannot reach the look on purpose (7.2), so the combination
    cannot put its own room into the session. Composing it anyway would drop the
    entry's camera and act into somebody else's place and call the result the
    source's photograph.

    Nothing is queued, asserted separately: a refusal that has already written a
    row is not a refusal.
    """
    _rows()
    sid = _session(client, seeded, room_key="some-other-room")
    r = client.post(f"/api/sessions/{sid}/compose-combination", json={"identifier": IDENTIFIER})
    assert r.status_code == 422, r.text
    assert ROOM_KEY in r.json()["detail"]
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"] == 0


def test_a_combination_naming_a_row_the_catalogue_lost_is_refused(client, seeded, combination_on_disk):
    """Never composed from the rows that are left.

    Two rows of three still compose a plausible line, and it is not the
    photograph the record names - it is a shorter one nobody measured. The
    refusal names the missing key, because the operator's next move is to look
    it up.
    """
    db.run(
        "INSERT INTO component (concept_key, slot, manner, family, faces, wording, "
        "judge_label, cameras, needs, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        CAMERA_KEY, "camera", "directed", "", "", CAMERA_TEXT, "cam judge", "", "", db.now(),
    )
    sid = _session(client, seeded)
    r = client.post(f"/api/sessions/{sid}/compose-combination", json={"identifier": IDENTIFIER})
    assert r.status_code == 422, r.text
    assert ACT_KEY in r.json()["detail"]
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"] == 0


def test_a_combination_is_refused_in_a_session_of_another_manner(client, seeded, combination_on_disk):
    """The same words in another manner are another measurement.

    A cell is keyed on the manner, so a `pov` row composed into a directed
    session records a photograph in a matrix its rows do not belong to.
    """
    _rows(manner="pov")
    sid = _session(client, seeded)
    r = client.post(f"/api/sessions/{sid}/compose-combination", json={"identifier": IDENTIFIER})
    assert r.status_code == 422, r.text
    assert "pov" in r.json()["detail"]
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"] == 0


def test_an_unknown_combination_is_a_404(client, seeded, combination_on_disk):
    sid = _session(client, seeded)
    r = client.post(f"/api/sessions/{sid}/compose-combination",
                    json={"identifier": "nothing_recorded_here"})
    assert r.status_code == 404, r.text


# ── 8.12 The combination reproduces the source entry's photograph ─────────

SOURCE_CAMERA = "shot from the doorway at head height"
SOURCE_ACT = "standing with her back to the wall and both palms flat against it"
SOURCE_ROOM = "a bare hallway with one strip light overhead"
SOURCE_ENTRY = {
    "identifier": "invented_fused_reproduce_01",
    "family": "fisheye POV",
    "prompt": f"{SOURCE_CAMERA}, {SOURCE_ACT}, {SOURCE_ROOM}",
}
SOURCE_CUT = {"camera": SOURCE_CAMERA, "act": SOURCE_ACT, "room": SOURCE_ROOM}


def test_a_recorded_combination_reproduces_the_source_entry(client, seeded):
    """End to end: split, record, store the rows, compose, read the line.

    Nothing in this test types a key. The entry is cut, the rows carry the keys
    the split derived, the combination is recorded from those rows and written
    to the file the app reads, and the route is then asked for the entry by
    NAME. What comes back has to be the photograph the source entry was.

    The three source clauses are asserted BYTE FOR BYTE inside the composed
    prompt - the camera and the act because the rows carry them, the room
    because the session's look was filled from it. A reproduction that composed
    the right rows into the wrong words, or trimmed a clause on the way, is a
    photograph nobody measured wearing the identifier of one somebody did.
    """
    rows = split_fused_entry(SOURCE_ENTRY, SOURCE_CUT)
    by_slot = {row["slot"]: row for row in rows}
    for slot in ("camera", "act"):
        row = by_slot[slot]
        db.run(
            "INSERT INTO component (concept_key, slot, manner, family, faces, wording, "
            "judge_label, cameras, needs, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            row["key"], slot, row["manner"], "", "", row["wording"],
            f"{row['key']} judge", "", row.get("needs", ""), db.now(),
        )
    save_mined_combinations(record_combinations(rows), data_dir=main.DATA_DIR)

    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "reproduction",
        # The room row IS the look: that is how a mined room reaches a
        # photograph, and the route refuses a session filled from another one.
        "look": by_slot["room"]["wording"], "room_key": by_slot["room"]["key"],
        "manner": by_slot["camera"]["manner"], "checkpoint": "test-checkpoint",
        "shots": [],
    }).json()["id"]

    try:
        r = client.post(f"/api/sessions/{sid}/compose-combination",
                        json={"identifier": SOURCE_ENTRY["identifier"]})
        assert r.status_code == 200, r.text
        prompt = db.one("SELECT prompt FROM shot WHERE session_id=?", sid)["prompt"]
        assert SOURCE_CAMERA in prompt, prompt
        assert SOURCE_ACT in prompt, prompt
        assert SOURCE_ROOM in prompt, prompt
    finally:
        (main.DATA_DIR / MINED_COMBINATIONS_FILE).unlink(missing_ok=True)
