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
from pathlib import Path

import pytest

import db
import main

# A room whose own text puts a nurse in the frame, and one that does not.
# `multi_body` is stored by the importer (it derives the words once, at import)
# so a seed written by hand carries it the same way an imported one does.
# The room seeds the BUILD carries, named rather than globbed. `data/` on a
# working machine also holds the imported libraries, which are untracked - a
# glob would make this suite read a different number of rooms on every machine
# and fail on somebody's private import. The imported corpus was measured once,
# at import: 396 rooms, median 17 words, longest 89.
TRACKED_ROOM_SEEDS = (
    Path("data/candid-rooms-seed.json"),
    Path("data/directed-looks-seed.json"),
)

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


def _seed_trios(n: int = 1, at: int = 0) -> dict:
    """`n` verified trios and the candidate list that names them.

    `n` matters: a run asks for a count, and a pool smaller than the count is
    refused by the draw itself with a 422 of its own. A room-gate test whose
    pool is too small asserts 422 and passes on a refusal that never reached
    the gate - which is exactly what the first version of the 7.7 test did.
    """
    keys = [(f"cam-{at}-{i}", f"act-{at}-{i}", f"frame-{at}-{i}") for i in range(n)]
    for cam, act, framing in keys:
        db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
               "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
               cam, act, framing, "directed", "finepornV4", 10, 8)
    return {
        "camera":  [{"key": k, "wordings": [{"key": k, "text": f"camera {k}"}]} for k, _, _ in keys],
        "act":     [{"key": k, "wordings": [{"key": k, "text": f"act {k}"}]} for _, k, _ in keys],
        "framing": [{"key": k, "wordings": [{"key": k, "text": f"framing {k}"}]} for _, _, k in keys],
    }


def _seed_one_trio() -> dict:
    return _seed_trios(1)


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


def test_the_refusal_over_the_budget_carries_the_count_and_the_budget(client, seeded, monkeypatch):
    """7.5. Both numbers, asserted as literals.

    A limit stated without the measurement is untunable: "this room is too
    long" leaves the operator guessing whether they are over by a word or by a
    hundred, and the only way to find out is to shorten and re-submit until it
    passes. The two numbers together are what make the refusal a next step.

    The budget is dropped to a number the fixture room exceeds rather than the
    room being grown past 120, because the shipped default is deliberately
    above the whole corpus and a test that needed a 121-word room would be
    testing a room nobody has.
    """
    long_place = " ".join(["A"] + ["word"] * 29)   # 30 words
    room = {**ALONE, "key": "ms-long-room-01", "place": long_place}
    seed = main.DATA_DIR / "medical-scenes-rooms-seed.json"
    main.DATA_DIR.mkdir(parents=True, exist_ok=True)
    seed.write_text(json.dumps([room], indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(main, "CONFIG", {**main.CONFIG, "room_word_budget": 20,
                                         "room_libraries": [{"name": "medical_scenes",
                                                             "seed_file": seed.name,
                                                             "enabled": True, "weight": 1.0}]})
    try:
        candidates = _seed_one_trio()
        sid = _session(client, seeded, room["key"], long_place)
        r = client.post(f"/api/sessions/{sid}/compose-run",
                        json={"count": 1, "candidates": candidates, "with_him": False})
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert "30" in detail, detail
        assert "20" in detail, detail
        assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"] == 0
    finally:
        seed.unlink(missing_ok=True)


def test_the_shipped_budget_refuses_no_room_the_app_carries(client, seeded):
    """The default is documented as refusing nothing, and this is that sentence
    as an assertion. It is the line that fails the day somebody tunes the
    number down without varying room length alone first - which is 7.9's job,
    and the whole reason the default is a tripwire rather than a constraint."""
    # `main.ROOM_WORD_BUDGET` and not a literal: a test carrying its own copy of
    # the number passes whatever the app enforces, which is the shape that let
    # a deliberate break of the default go green here once already.
    budget = main.CONFIG.get("room_word_budget") or main.ROOM_WORD_BUDGET
    for path in TRACKED_ROOM_SEEDS:
        for stored in json.loads(path.read_text(encoding="utf-8")):
            words = len((stored.get("place") or "").split())
            assert words <= budget, (path.name, stored["key"], words, budget)


def test_every_seeded_room_composes_its_own_text_byte_for_byte(client, seeded):
    """7.6. The property, over every room the build carries.

    Not one room and not a fixture: the failure this is written against is a
    composer that shortens SOME rooms - the long ones, the ones with a
    semicolon, the ones whose prose ends without a full stop - and a test over
    a single hand-written room would never see it. A room that reaches the
    prompt one character short is a room shot under text nobody wrote and
    measured under a verdict that no longer describes it.
    """
    rooms = [r for path in TRACKED_ROOM_SEEDS
             for r in json.loads(path.read_text(encoding="utf-8"))]
    assert rooms, "no tracked room seeds to compose"

    seed = main.DATA_DIR / "candid-rooms-seed.json"
    main.DATA_DIR.mkdir(parents=True, exist_ok=True)
    seed.write_text(json.dumps(rooms, indent=2) + "\n", encoding="utf-8")
    saved = main.CONFIG
    main.CONFIG = {**main.CONFIG, "room_libraries": [
        {"name": "candid", "seed_file": seed.name, "enabled": True, "weight": 1.0}]}
    try:
        for at, stored in enumerate(rooms):
            candidates = {
                slot: [{"key": f"{slot}-{at}", "wordings": [{"key": f"{slot}-{at}",
                                                             "text": f"{slot} text"}]}]
                for slot in ("camera", "act", "framing")
            }
            db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
                   "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   f"camera-{at}", f"act-{at}", f"framing-{at}",
                   "directed", "finepornV4", 10, 8)
            sid = _session(client, seeded, stored["key"], stored["place"])
            r = client.post(f"/api/sessions/{sid}/compose-run",
                            json={"count": 1, "candidates": candidates,
                                  # Declared, so a room that names other people is
                                  # accepted here: this asks what the composer does
                                  # with the text, not what the gate does with it.
                                  "with_him": True})
            assert r.status_code == 200, (stored["key"], r.text)
            prompt = db.one("SELECT prompt FROM shot WHERE session_id=?", sid)["prompt"]
            assert stored["place"] in prompt, (stored["key"], prompt)
    finally:
        main.CONFIG = saved
        seed.unlink(missing_ok=True)


def test_a_refused_run_leaves_the_shots_that_were_already_there(client, seeded, rooms_on_disk):
    """7.7. Unchanged, not zero.

    `db.run` commits per INSERT, so the way this breaks is a gate that runs
    inside the queueing loop: k shots land, the k+1th is refused, and the
    operator is handed a 422 over a run that half happened. Asserting `== 0`
    on an empty session would pass on exactly that code, because the first
    photograph is the one that fires the gate. So the session already has
    shots, and the number after the refusal has to be the number before it.

    Both gates are checked from the same session, one after the other, because
    "refuse before queueing" is a property of the check's POSITION and not of
    either rule - a second gate added below the loop would pass a test that
    only exercised the first.
    """
    # Five trios for a run of five: a pool smaller than the count is refused by
    # the draw before the room is ever looked at, and this test would then be
    # asserting a 422 it did not mean.
    candidates = _seed_trios(5)
    look = f"Photographed plainly. {CROWDED['place']}"
    sid = _session(client, seeded, CROWDED["key"], look)

    # Two photographs the operator already has: one written, one composed with
    # the second body declared.
    client.post(f"/api/sessions/{sid}/shots", json={"shots": [{"prompt": "a written take"}]})
    client.post(f"/api/sessions/{sid}/compose-run",
                json={"count": 1, "candidates": candidates, "with_him": True})
    before = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert before == 2, before

    # The multi-body gate, over a run of five.
    r = client.post(f"/api/sessions/{sid}/compose-run",
                    json={"count": 5, "candidates": candidates, "with_him": False})
    assert r.status_code == 422, r.text
    # Named, so this is known to be the room's refusal and not the draw's.
    assert CROWDED["label"] in r.json()["detail"], r.json()["detail"]
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"] == before

    # And the budget gate, over the same session and the same run.
    saved = main.CONFIG
    main.CONFIG = {**main.CONFIG, "room_word_budget": 3}
    try:
        r = client.post(f"/api/sessions/{sid}/compose-run",
                        json={"count": 5, "candidates": candidates, "with_him": True})
        assert r.status_code == 422, r.text
        assert "budget" in r.json()["detail"], r.json()["detail"]
        assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"] == before
    finally:
        main.CONFIG = saved


def test_the_report_says_which_rooms_a_run_would_be_refused_in_and_queues_nothing(
        client, seeded, rooms_on_disk):
    """7.8. The same answers as the run, ahead of it, at no cost.

    Queueing nothing is asserted over the whole shot table and not over one
    session, because the report is not sent from a session - a call that
    queued anything would queue it somewhere this test does not know to look.

    The reason string is compared to the gate's own: the report exists to be
    trusted, and a report that recomputed the rules would be a second opinion
    free to clear a room the run refuses.
    """
    candidates = _seed_trios(1)
    sid = _session(client, seeded, CROWDED["key"], CROWDED["place"])
    before = db.one("SELECT COUNT(*) AS n FROM shot")["n"]

    r = client.post("/api/rooms/preflight", json={"with_him": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert db.one("SELECT COUNT(*) AS n FROM shot")["n"] == before

    refused = {row["key"]: row["reason"] for row in body["refused"]}
    assert CROWDED["key"] in refused, body
    assert ALONE["key"] not in refused, body
    assert "nurse" in refused[CROWDED["key"]]
    assert refused[CROWDED["key"]] == main.room_refusal(
        {**CROWDED}, with_him=False)

    # And the run agrees with it, which is the property that matters.
    run = client.post(f"/api/sessions/{sid}/compose-run",
                      json={"count": 1, "candidates": candidates, "with_him": False})
    assert run.status_code == 422, run.text
    assert run.json()["detail"] == refused[CROWDED["key"]]

    # Declaring him clears the room, in the report and in the run alike.
    cleared = client.post("/api/rooms/preflight", json={"with_him": True}).json()
    assert cleared["refused"] == [], cleared


def test_the_look_tooltip_quotes_the_budget_the_code_enforces():
    """10.4. The screen that warns about the look's length has to quote the
    number the app actually refuses on, and the two are written in different
    files by different hands.

    It quoted `~85 composed words` for months against a budget of 200, which was
    not a stale number so much as a stale FINDING: sessions 395-400 measured the
    camera arriving 7/10 with no room at all and 10/10 at 176 words, so the trend
    the tooltip warned about runs the other way. Read out of `main` rather than
    typed here, for the reason the gate's own test gives - a test carrying its
    own copy of the number is green whatever the app enforces.
    """
    import pathlib
    import re

    tooltip = (pathlib.Path(__file__).resolve().parents[1]
               / "frontend/src/views/ModelDetail.jsx").read_text(encoding="utf-8")
    quoted = re.search(r"room word budget is (\d+)", tooltip)
    assert quoted, "the look tooltip no longer says what the room word budget is"
    assert int(quoted.group(1)) == main.ROOM_WORD_BUDGET, (
        f"the tooltip says {quoted.group(1)} and the app refuses at {main.ROOM_WORD_BUDGET}")
    # The retracted claim is gone, not merely outnumbered by the new one.
    assert "~85 composed words" not in tooltip
