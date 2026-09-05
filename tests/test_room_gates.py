"""The gates a room has to pass before a run is queued.

7.3 and 7.4. The room is the one thing in a composed line that the operator
picked rather than the draw, and it is composed into EVERY photograph of the
session - so a room that puts other people in the frame is not a per-photograph
problem the act pool can solve. It is a run-level refusal.

The two tasks are one test each over the same fixture, which is deliberate:
they are the two halves of one rule, and the second is what stops the first
from being implemented as "rewrite the room until it is safe".
"""
from __future__ import annotations

import json

import pytest

import db
import main

# A room whose own text puts a nurse in the frame, and one that does not.
# `multi_body` is stored by the importer (it derives the words once, at import)
# so a seed written by hand carries it the same way an imported one does.
CROWDED = {
    "key": "ms-exam-room-01",
    "label": "Examination room",
    "place": "An examination room with a paper-covered couch, a nurse at the counter behind her.",
    "multi_body": ["nurse"],
    "manners": [],
    "offers": [],
    "tags": [],
    "guidance": {},
    "authored": [],
    "weight": 1.0,
    "identifier": "medical_exam_room_01",
    "source_library": "medical_scenes",
}
ALONE = {
    "key": "ms-scrub-room-01",
    "label": "Scrub room",
    "place": "A scrub room with a steel sink and one strip light overhead.",
    "multi_body": [],
    "manners": [],
    "offers": [],
    "tags": [],
    "guidance": {},
    "authored": [],
    "weight": 1.0,
    "identifier": "medical_scrub_room_01",
    "source_library": "medical_scenes",
}


@pytest.fixture
def rooms_on_disk():
    """Both rooms in a registered library, and the globals put back afterwards.

    The seed is written into the live `DATA_DIR` and named in the live
    `CONFIG`, because that is the pair `available_rooms` reads - a fixture that
    monkeypatched the reader would test the gate against a room the app cannot
    actually serve.
    """
    saved = main.CONFIG
    seed = main.DATA_DIR / "medical-scenes-rooms-seed.json"
    main.DATA_DIR.mkdir(parents=True, exist_ok=True)
    seed.write_text(json.dumps([CROWDED, ALONE], indent=2) + "\n", encoding="utf-8")
    main.CONFIG = {**main.CONFIG, "room_libraries": [
        {"name": "medical_scenes", "seed_file": seed.name, "enabled": True, "weight": 1.0},
    ]}
    yield
    main.CONFIG = saved
    seed.unlink(missing_ok=True)


def _session(client, seeded, room_key: str, look: str) -> int:
    return client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": f"in {room_key or 'nowhere'}",
        "look": look, "room_key": room_key,
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]


def _seed_one_trio() -> dict:
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "cam-a", "act-a", "frame-a", "directed", "finepornV4", 10, 8)
    return {slot: [{"key": key, "wordings": [{"key": key, "text": f"{slot} text"}]}]
            for slot, key in (("camera", "cam-a"), ("act", "act-a"), ("framing", "frame-a"))}


def test_a_crowded_room_refuses_a_run_with_nobody_else_in_it(client, seeded, rooms_on_disk):
    """7.3. The refusal names the room and the words responsible.

    Both, asserted separately. The room alone is a no with no next step - the
    operator reads a paragraph they wrote and cannot see which half of it the
    app objected to. The words alone name a problem with no location, and a
    session can only have one room but an operator has many.

    The count is asserted after the refusal for the same reason 3.3 asserts it:
    `db.run` commits per INSERT, so a gate that fired mid-loop would leave a
    shorter run behind and call it a refusal.
    """
    candidates = _seed_one_trio()
    sid = _session(client, seeded, CROWDED["key"], f"Photographed plainly. {CROWDED['place']}")

    r = client.post(f"/api/sessions/{sid}/compose-run",
                    json={"count": 1, "candidates": candidates, "with_him": False})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert CROWDED["label"] in detail, detail
    assert "nurse" in detail, detail
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"] == 0


def test_the_same_room_composes_unchanged_once_the_run_declares_him(client, seeded, rooms_on_disk):
    """7.4. The gate is a yes-or-no and never an editor.

    The room text is asserted BYTE for byte inside the composed prompt, which
    is the half worth writing: a gate that "handles" a crowded room by dropping
    the clause about the nurse would pass a test that only checked the run was
    accepted, and would silently shoot a different room than the one the
    operator picked and the verdicts were measured on.
    """
    candidates = _seed_one_trio()
    look = f"Photographed plainly. {CROWDED['place']}"
    sid = _session(client, seeded, CROWDED["key"], look)

    r = client.post(f"/api/sessions/{sid}/compose-run",
                    json={"count": 1, "candidates": candidates, "with_him": True})
    assert r.status_code == 200, r.text
    prompt = db.one("SELECT prompt FROM shot WHERE session_id=?", sid)["prompt"]
    assert CROWDED["place"] in prompt, prompt


def test_a_room_with_nobody_in_it_and_a_detached_session_both_pass(client, seeded, rooms_on_disk):
    """The two ordinary states, so the gate is known to be a gate and not a
    wall. A single-subject room carries no words to object to; a session with
    no key makes no claim about a room at all, which is what 7.2's detach
    leaves behind and what every session written before the column looks like.
    """
    candidates = _seed_one_trio()
    for key in (ALONE["key"], ""):
        sid = _session(client, seeded, key, "Photographed plainly. A room.")
        r = client.post(f"/api/sessions/{sid}/compose-run",
                        json={"count": 1, "candidates": candidates, "with_him": False})
        assert r.status_code == 200, (key, r.text)
