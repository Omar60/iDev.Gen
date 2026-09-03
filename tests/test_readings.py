"""Tests for the reading vocabulary store: CRUD, Literal slot validation,
bidirectional collision checks, and scoped delete checks.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest
import db


ROOT = Path(__file__).resolve().parents[1]


def _make_session(client, name="test session", manner="directed", checkpoint="ckpt"):
    db.run("INSERT INTO model (name, trigger, created_at) VALUES (?, 't', 'now')", f"model-{name}")
    mid = db.one("SELECT id FROM model WHERE name=?", f"model-{name}")["id"]
    return db.run(
        "INSERT INTO session (model_id, name, manner, checkpoint, created_at) VALUES (?, ?, ?, ?, 'now')",
        mid, name, manner, checkpoint,
    )


def test_reading_slot_literal_validation(client):
    """Task 1.2: ReadingIn.slot is typed as Literal['camera', 'act', 'framing']
    and rejects invalid values at the boundary with 422.
    """
    res = client.post("/api/readings", json={
        "slot": "invalid_slot",
        "manner": "directed",
        "key": "k1",
        "label": "Label 1",
    })
    assert res.status_code == 422


def test_get_readings_union_and_scoping(client):
    """Task 1.2: GET /api/readings union and scoping."""
    sid_a = _make_session(client, name="Session A", manner="directed")
    sid_b = _make_session(client, name="Session B", manner="directed")

    # Add base reading
    r_base = client.post("/api/readings", json={
        "slot": "camera",
        "manner": "directed",
        "key": "r_base",
        "label": "Base camera reading",
    }).json()

    # Add session reading for Session A:
    r_sess_a = client.post("/api/readings", json={
        "slot": "camera",
        "manner": "directed",
        "key": "r_sess_a",
        "label": "Session A camera reading",
        "session_id": sid_a,
    }).json()

    # Query for Session A (both base and session A)
    res_a = client.get(f"/api/readings?slot=camera&session_id={sid_a}")
    assert res_a.status_code == 200
    keys_a = [r["key"] for r in res_a.json()]
    assert "r_base" in keys_a
    assert "r_sess_a" in keys_a

    # Query for Session B (only base)
    res_b = client.get(f"/api/readings?slot=camera&session_id={sid_b}")
    assert res_b.status_code == 200
    keys_b = [r["key"] for r in res_b.json()]
    assert "r_base" in keys_b
    assert "r_sess_a" not in keys_b

    # Query without session_id (only base)
    res_none = client.get("/api/readings?slot=camera")
    assert res_none.status_code == 200
    keys_none = [r["key"] for r in res_none.json()]
    assert "r_base" in keys_none
    assert "r_sess_a" not in keys_none


def test_post_reading_session_collision_with_base_and_multi_session(client):
    """Task 1.3: A session-scoped reading whose key already exists in base
    for the same slot and manner is refused 422 naming the key, nothing is written,
    and the same key in two different sessions is accepted.
    """
    sid_a = _make_session(client, name="Session A", manner="directed")
    sid_b = _make_session(client, name="Session B", manner="directed")

    # Add base reading
    client.post("/api/readings", json={
        "slot": "camera",
        "manner": "directed",
        "key": "front",
        "label": "Frontal view",
    })

    # Try to add session reading with same key as base -> 422
    res_dup = client.post("/api/readings", json={
        "slot": "camera",
        "manner": "directed",
        "key": "front",
        "label": "Session frontal",
        "session_id": sid_a,
    })
    assert res_dup.status_code == 422
    assert "front" in res_dup.json()["detail"]
    assert "base" in res_dup.json()["detail"]

    # Nothing written for session A under "front"
    readings_a = client.get(f"/api/readings?slot=camera&session_id={sid_a}").json()
    assert len([r for r in readings_a if r["key"] == "front" and r["session_id"] == sid_a]) == 0

    # Same key in two different sessions is accepted
    res_a = client.post("/api/readings", json={
        "slot": "camera",
        "manner": "directed",
        "key": "custom_angle",
        "label": "Custom angle for A",
        "session_id": sid_a,
    })
    assert res_a.status_code == 200

    res_b = client.post("/api/readings", json={
        "slot": "camera",
        "manner": "directed",
        "key": "custom_angle",
        "label": "Custom angle for B",
        "session_id": sid_b,
    })
    assert res_b.status_code == 200


def test_post_reading_base_collision_with_earlier_session_reading(client):
    """Task 1.3b: Refuse collision in the other direction too - a base reading
    whose key a session reading already holds is refused 422 naming the key.
    """
    sid = _make_session(client, name="Session Early", manner="directed")

    # Insert session reading FIRST
    res_sess = client.post("/api/readings", json={
        "slot": "act",
        "manner": "directed",
        "key": "special_pose",
        "label": "Special session pose",
        "session_id": sid,
    })
    assert res_sess.status_code == 200

    # Try to insert base reading with same key SECOND -> 422
    res_base = client.post("/api/readings", json={
        "slot": "act",
        "manner": "directed",
        "key": "special_pose",
        "label": "Special base pose",
    })
    assert res_base.status_code == 422
    assert "special_pose" in res_base.json()["detail"]
    assert "session" in res_base.json()["detail"]


def test_delete_reading_scoped_reference_check(client):
    """Task 1.4: DELETE /api/readings/{id} refuses with the count of answers
    referencing it, and an unreferenced reading is deleted.
    The scan is scoped by the reading's own scope.
    """
    sid_a = _make_session(client, name="Session A", manner="directed")
    sid_b = _make_session(client, name="Session B", manner="directed")

    # Create base reading
    r_base = client.post("/api/readings", json={
        "slot": "camera",
        "manner": "directed",
        "key": "base_cam",
        "label": "Base Cam",
    }).json()

    # Create session reading for session A
    r_sess_a = client.post("/api/readings", json={
        "slot": "camera",
        "manner": "directed",
        "key": "sess_a_cam",
        "label": "Session A Cam",
        "session_id": sid_a,
    }).json()

    # Plant a shot in session A referencing base_cam
    db.run(
        "INSERT INTO shot (session_id, prompt, components, verdicts, created_at) "
        "VALUES (?, 'p', '{}', ?, 'now')",
        sid_a, json.dumps({"camera": "base_cam"}),
    )

    # Plant a shot in session B referencing base_cam
    db.run(
        "INSERT INTO shot (session_id, prompt, components, verdicts, created_at) "
        "VALUES (?, 'p', '{}', ?, 'now')",
        sid_b, json.dumps({"camera": "base_cam"}),
    )

    # Deleting base_cam fails because 2 shots reference it across directed manner
    del_base_fail = client.delete(f"/api/readings/{r_base['id']}")
    assert del_base_fail.status_code == 422
    assert "2 stored answers" in del_base_fail.json()["detail"]

    # Plant a shot in session B referencing sess_a_cam key (different session)
    db.run(
        "INSERT INTO shot (session_id, prompt, components, verdicts, created_at) "
        "VALUES (?, 'p', '{}', ?, 'now')",
        sid_b, json.dumps({"camera": "sess_a_cam"}),
    )

    # Deleting r_sess_a (scoped to session A) succeeds because session A has 0 shots referencing it
    del_sess_ok = client.delete(f"/api/readings/{r_sess_a['id']}")
    assert del_sess_ok.status_code == 200

    # Create unreferenced base reading and delete it
    r_unref = client.post("/api/readings", json={
        "slot": "framing",
        "manner": "directed",
        "key": "unref_framing",
        "label": "Unreferenced framing",
    }).json()
    del_unref_ok = client.delete(f"/api/readings/{r_unref['id']}")
    assert del_unref_ok.status_code == 200


def test_judge_pass_missing_reading_refusal_precheck_on_shots_and_controls(client, seeded):
    """Tasks 2.2, 2.3, 2.3b: Pre-check refuses 422 naming missing families
    across BOTH unjudged shots and control shots, serving nothing at all.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "refusal pass",
        "manner": "directed", "checkpoint": "ckpt1", "shots": [],
    }).json()["id"]

    # Compose shot with camera side-level (family 'side-level')
    cam = {"key": "side-level", "wordings": [{"key": "side-level", "text": "side"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride"}]}
    framing = {"key": "full-length", "wordings": [{"key": "full-length", "text": "full"}]}

    s1 = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": cam, "act": act, "framing": framing, "mode": "exploratory",
    }).json()["ids"][0]
    db.run("UPDATE shot SET status='done' WHERE id=?", s1)

    # Delete base reading for side-level (family 'side-level')
    db.run("DELETE FROM reading WHERE slot='camera' AND manner='directed' AND key='side-level'")

    # Pre-check fails naming side-level
    res_refuse = client.get(f"/api/sessions/{sid}/judge-pass?slot=camera")
    assert res_refuse.status_code == 422
    assert "side-level" in res_refuse.json()["detail"]
    assert "shots" not in res_refuse.json()

    # Add back reading for side-level
    client.post("/api/readings", json={
        "slot": "camera", "manner": "directed", "key": "side-level", "label": "Side level",
    })

    # Now test Task 2.3b: control shot whose family has no reading
    s2 = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": {"key": "front-direct", "wordings": [{"key": "front-direct", "text": "front"}]},
        "act": act, "framing": framing, "mode": "exploratory",
    }).json()["ids"][0]
    db.run("UPDATE shot SET status='done', verdicts=? WHERE id=?", json.dumps({"camera": "front-direct"}), s2)

    # Delete reading for front (family 'front')
    db.run("DELETE FROM reading WHERE slot='camera' AND manner='directed' AND key='front'")

    # Even though s2 is a CONTROL shot, missing reading for 'front' refuses the pass
    res_ctrl_refuse = client.get(f"/api/sessions/{sid}/judge-pass?slot=camera")
    assert res_ctrl_refuse.status_code == 422
    assert "front" in res_ctrl_refuse.json()["detail"]


def test_judge_scoring_and_wording_vs_concept_key_isolation(client, seeded):
    """Tasks 3.1, 3.1b, 3.2, 3.3: Score hits via _reduce, ensure concept_key lookup
    only (no wording collision), preserve wrong readings on row, and ignore unasked slots.
    """
    # Task 3.1b: Insert component with concept_key='pose_x', family='fam_x', wording='standing'
    # And another component with concept_key='standing', family='fam_y', wording='w_y'
    db.run(
        """INSERT INTO component (concept_key, slot, manner, family, wording, judge_label, created_at)
           VALUES ('pose_x', 'act', 'directed', 'fam_x', 'standing', 'Pose X', 'now')"""
    )
    db.run(
        """INSERT INTO component (concept_key, slot, manner, family, wording, judge_label, created_at)
           VALUES ('standing', 'act', 'directed', 'fam_y', 'w_y', 'Standing', 'now')"""
    )

    # Readings for act
    client.post("/api/readings", json={"slot": "act", "manner": "directed", "key": "fam_x", "label": "Fam X"})
    client.post("/api/readings", json={"slot": "act", "manner": "directed", "key": "fam_y", "label": "Fam Y"})

    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "scoring session",
        "manner": "directed", "checkpoint": "ckpt1", "shots": [],
    }).json()["id"]

    # Shot drawn with concept pose_x
    shot_id = db.run(
        """INSERT INTO shot (session_id, prompt, components, status, created_at)
           VALUES (?, 'p', ?, 'done', 'now')""",
        sid,
        json.dumps({
            "camera": {"wording": "none"},
            "act": {"concept": "pose_x", "wording": "standing"},
            "framing": {"wording": "none"},
        }),
    )

    # Answering 'fam_y' (which is the family of concept_key 'standing') must NOT hit pose_x
    # (even though pose_x has wording='standing')
    res_wrong = client.post(f"/api/shots/{shot_id}/judge", json={"act": "fam_y"}).json()
    assert res_wrong["arrived"] == 0
    assert res_wrong["judged"] == 1

    # Verdict preserved on row (Task 3.2)
    shot_row = db.one("SELECT verdicts FROM shot WHERE id=?", shot_id)
    assert json.loads(shot_row["verdicts"]) == {"act": "fam_y"}

    # Shot 2 drawn with pose_x: answering fam_x is a hit (Task 3.1)
    shot_id_2 = db.run(
        """INSERT INTO shot (session_id, prompt, components, status, created_at)
           VALUES (?, 'p', ?, 'done', 'now')""",
        sid,
        json.dumps({
            "camera": {"wording": "none"},
            "act": {"concept": "pose_x", "wording": "standing"},
            "framing": {"wording": "none"},
        }),
    )
    res_hit = client.post(f"/api/shots/{shot_id_2}/judge", json={"act": "fam_x"}).json()
    assert res_hit["arrived"] == 1
    assert res_hit["judged"] == 2

    # Task 3.3: Answering an unasked slot (camera='none') counts toward no cell
    shot_id_3 = db.run(
        """INSERT INTO shot (session_id, prompt, components, status, created_at)
           VALUES (?, 'p', ?, 'done', 'now')""",
        sid,
        json.dumps({
            "camera": {"wording": "none"},
            "act": {"concept": "pose_x", "wording": "standing"},
            "framing": {"wording": "none"},
        }),
    )
    res_unasked = client.post(f"/api/shots/{shot_id_3}/judge", json={"camera": "front"}).json()
    assert res_unasked["judged"] == 2  # judged does not increment on unasked slot


def test_control_agreement_across_vocabularies_and_cannot_tell(client, seeded):
    """Task 3.4: Control agreement compares answers through _reduce so legacy
    component answers agree with new reading keys of the same family, and
    bool(stored_val) ensures empty 'cannot tell' answers agree on nothing.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "control session",
        "manner": "directed", "checkpoint": "ckpt1", "shots": [],
    }).json()["id"]

    # Component with concept_key='mid-shot-edges', family='head-to-knees'
    db.run(
        """INSERT INTO component (concept_key, slot, manner, family, wording, judge_label, created_at)
           VALUES ('mid-shot-edges', 'framing', 'directed', 'head-to-knees', 'mid shot edges', 'Head to knees', 'now')"""
    )
    # Reading for framing 'head-to-knees'
    client.post("/api/readings", json={
        "slot": "framing", "manner": "directed", "key": "head-to-knees", "label": "Head to knees",
    })

    # Control shot 1: stored answer is legacy component key 'mid-shot-edges'
    s1 = db.run(
        """INSERT INTO shot (session_id, prompt, components, verdicts, status, created_at)
           VALUES (?, 'p', ?, ?, 'done', 'now')""",
        sid,
        json.dumps({"camera": {"wording": "none"}, "act": {"wording": "none"}, "framing": {"wording": "mid shot edges"}}),
        json.dumps({"framing": "mid-shot-edges"}),
    )

    # Re-judged with new reading key 'head-to-knees' -> AGREES!
    res1 = client.post(f"/api/shots/{s1}/judge", json={"control": True, "framing": "head-to-knees"}).json()
    assert res1["control"] is True
    assert res1["agreed"] is True
    assert res1["stored"] == "mid-shot-edges"
    assert res1["answered"] == "head-to-knees"

    # Control shot 2: stored answer is empty string "" (cannot tell)
    s2 = db.run(
        """INSERT INTO shot (session_id, prompt, components, verdicts, status, created_at)
           VALUES (?, 'p', ?, ?, 'done', 'now')""",
        sid,
        json.dumps({"camera": {"wording": "none"}, "act": {"wording": "none"}, "framing": {"wording": "mid shot edges"}}),
        json.dumps({"framing": ""}),
    )

    # Re-judged with "" -> DISAGREES because two 'cannot tell' agree on nothing (bool(stored_val) is False)
    res2 = client.post(f"/api/shots/{s2}/judge", json={"control": True, "framing": ""}).json()
    assert res2["control"] is True
    assert res2["agreed"] is False
    assert res2["stored"] == ""
    assert res2["answered"] == ""



def test_a_component_key_in_another_slot_does_not_steal_the_reduction(client, seeded, db_conn=None):
    """The scoring reduction is scoped to the SLOT it is scoring.

    The live catalogue holds a CAMERA whose `concept_key` is `close-up` (one of
    the fifteen shot-size camera terms) and a FRAMING whose family is `close-up`.
    Unscoped, `_family_of("close-up")` found the camera row, reduced the framing
    answer to the camera's family `close`, and a photograph that arrived was
    recorded as a miss. Measured on session 319 the day it shipped: seven
    `close-up` and five `waist-up` answers scored 0, four of them exact hits.
    """
    import json

    import db

    db.run(
        """INSERT INTO component (concept_key, slot, manner, family, wording, judge_label, created_at)
           VALUES ('close-up', 'camera', 'directed', 'close', 'close-up', 'Close camera', 'now')"""
    )
    db.run(
        """INSERT INTO component (concept_key, slot, manner, family, wording, judge_label, created_at)
           VALUES ('crop-close-up', 'framing', 'directed', 'close-up', 'close-up crop', 'The face fills the frame', 'now')"""
    )
    client.post("/api/readings", json={"slot": "framing", "manner": "directed",
                                       "key": "close-up", "label": "Her face fills the frame."})

    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "slot scoping",
        "manner": "directed", "checkpoint": "ckpt1", "shots": [],
    }).json()["id"]
    shot_id = db.run(
        """INSERT INTO shot (session_id, prompt, components, status, created_at)
           VALUES (?, 'p', ?, 'done', 'now')""",
        sid,
        json.dumps({
            "camera": {"wording": "none"},
            "act": {"wording": "none"},
            "framing": {"concept": "crop-close-up", "wording": "crop-close-up"},
        }),
    )

    res = client.post(f"/api/shots/{shot_id}/judge", json={"framing": "close-up"}).json()
    assert res["arrived"] == 1, "the family the line asked for is the family in the frame"
    assert res["judged"] == 1


def test_the_readings_seed_imports_and_never_rewords_an_existing_key(client):
    """The vocabulary belongs in the repo, and a re-import must not edit it.

    A judging pass refuses a slot whose photographed families have no reading, so
    a fresh database cannot judge anything until the readings exist — candid had
    none at all, and writing the twelve it needed by hand was what stood between
    a shot session and any number about it.

    The second half is the one that matters after the first import: a label is
    the question a stored verdict was answered against. Re-importing a seed whose
    text has moved on must leave the stored question alone, or every verdict
    recorded before the edit silently answers a question nobody asked. So an
    existing (slot, manner, key) is SKIPPED, not updated.
    """
    seed = json.loads((ROOT / "data" / "readings-seed.json").read_text(encoding="utf-8"))
    assert seed, "the seed is empty"

    first = client.post("/api/readings/import", json=seed).json()
    assert first["added"] == len(seed), first

    one = seed[0]
    stored = client.get(f"/api/readings?slot={one['slot']}&manner={one['manner']}").json()
    assert any(r["key"] == one["key"] and r["label"] == one["label"] for r in stored), stored

    reworded = [dict(one, label="something else entirely")]
    again = client.post("/api/readings/import", json=reworded).json()
    assert again == {"added": 0, "skipped": 1}, again
    after = client.get(f"/api/readings?slot={one['slot']}&manner={one['manner']}").json()
    assert any(r["key"] == one["key"] and r["label"] == one["label"] for r in after), after


# The (manner, slot) pairs whose vocabulary is CLAIMED complete. A pair listed
# here must have a reading for every family its components can be composed from,
# or a judging pass over it records every photograph of the missing family as a
# miss. Directed's `camera` and `act` are deliberately absent: the 49-camera
# import of 2026-08-28 left eighteen families with no reading, and that is a
# known hole rather than a regression this test should be red about.
COMPLETE_VOCABULARIES = (
    ("candid", "act"), ("candid", "camera"), ("candid", "framing"),
    ("selfie", "act"), ("selfie", "camera"), ("selfie", "framing"),
    ("directed", "framing"),
)


# Directed's camera slot is the one vocabulary that is complete ON PURPOSE only
# in part. Six of its twenty families get no reading, and each for a reason that
# writing one would make worse rather than better.
CAMERA_FAMILIES_WITH_NO_READING = {
    # Crop terms sitting in the camera slot: `close-up`, `medium shot`,
    # `full body`, `long shot` and their kin, fifteen rows of them. They are the
    # FRAMING slot's question. A camera reading for them would let a camera cell
    # be scored on a crop, which is the confusion and not the cure — and the
    # dangerous half, a camera and a framing claiming different crops in one
    # line, is refused by the crop law now.
    "close", "medium", "full", "wide",
    # 35mm against 85mm is not answerable from the photograph by anybody.
    "lens",
    # A pan, a tilt and a tracking shot cannot exist in a still image at all.
    "movement",
    # The three from the older `camera-candidates` vocabulary that are not camera
    # positions either: where she sits in the frame, how hard the optics
    # foreshorten her, and the register of the file. All observable, none of them
    # the question this slot asks — a camera cell scored on the rule of thirds
    # measures the composition. `register` cannot have one honest reading at all:
    # it holds `a ring light reflected in her eyes`, which a judge can see, next
    # to `shot on a Canon EOS`, which nobody can.
    "composition", "geometry", "register",
}


def test_directed_cameras_without_a_reading_are_exactly_the_ones_that_cannot_have_one():
    """Every directed camera family has a reading except the six that cannot.

    Pins both halves. A new position family arriving with no reading is a real
    gap and fails here; somebody "completing" the vocabulary by writing a camera
    reading for `close` or `movement` also fails here, with the reason on the
    line. The 49 cameras were in the live store and in NO seed file until
    2026-09-03 — the store and the repo held two disjoint directed camera
    vocabularies, overlap zero — so this reads the seeds.
    """
    families, keys = set(), set()
    for path in (ROOT / "data").glob("*-seed.json"):
        items = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "readings-seed.json":
            keys |= {i["key"] for i in items
                     if i["slot"] == "camera" and i["manner"] == "directed"}
        else:
            families |= {i["family"] for i in items if isinstance(i, dict)
                         and i.get("slot") == "camera" and i.get("manner") == "directed"
                         and i.get("family")}
    assert families, "no directed camera components in the seeds"

    unreadable = sorted(families - keys)
    assert set(unreadable) <= CAMERA_FAMILIES_WITH_NO_READING, (
        f"directed camera families with no reading that are not on the exempt list: "
        f"{sorted(set(unreadable) - CAMERA_FAMILIES_WITH_NO_READING)}")
    written = sorted(CAMERA_FAMILIES_WITH_NO_READING & keys)
    assert not written, (
        f"a reading was written for {written}, which the camera slot cannot answer: "
        f"a crop term belongs to the framing slot, a focal length is not visible, "
        f"and a camera movement does not exist in a still photograph")


@pytest.mark.parametrize("manner,slot", COMPLETE_VOCABULARIES)
def test_a_complete_vocabulary_has_a_reading_for_every_family_it_can_compose(manner, slot):
    """Every family this manner can photograph in this slot has a reading.

    `judge-pass` refuses a slot whose photographed families have no reading -
    the right answer would not be on the list, so every photograph of that
    family would be recorded as a miss. Candid had NINE framing components and
    ZERO framing readings, which is why 33 candid cells were judged on camera
    and act only; selfie had zero readings in all three slots and could not be
    judged at all.

    Read from the files and not from a live store on purpose: this is what a
    fresh clone can do, and the store had been ahead of the files for a day.
    """
    families, keys = set(), set()
    for path in (ROOT / "data").glob("*-seed.json"):
        items = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "readings-seed.json":
            keys |= {i["key"] for i in items if i["slot"] == slot and i["manner"] == manner}
        else:
            families |= {i["family"] for i in items if isinstance(i, dict)
                         and i.get("slot") == slot and i.get("manner") == manner and i.get("family")}
    assert families, f"no {manner}/{slot} components in the seeds"
    missing = sorted(families - keys)
    assert not missing, (
        f"{manner}/{slot} families with no reading: {missing}; a judging pass over "
        f"them records every photograph as a miss")


def test_no_two_person_act_sits_in_a_family_whose_reading_says_she_is_alone():
    """An act that needs a second person cannot be scored in a solo family.

    `wall` ("she is standing with her front to the wall ... he is behind her")
    sat in family `standing`, whose reading ends "She is the only person in the
    picture." A judge answering that reading correctly can never agree with the
    line, so every photograph of `wall` was a recorded miss no matter what came
    back — the same shape as two framing families with one picture, and just as
    invisible: the number looks like a measurement.

    Scoped to manners that HAVE a vocabulary for the slot. Selfie has no
    readings at all yet, and a manner with nothing to judge with is a gap
    somebody knows about, not a contradiction.
    """
    acts, readings = [], {}
    for path in (ROOT / "data").glob("*-seed.json"):
        for item in json.loads(path.read_text(encoding="utf-8")):
            if not isinstance(item, dict):
                continue
            # `needs` also carries 'access', which is a solo act with a wardrobe
            # requirement. Only 'him' puts a second person in the frame.
            if item.get("slot") == "act" and (item.get("needs") or "").strip() == "him":
                acts.append(item)
    for item in json.loads((ROOT / "data" / "readings-seed.json").read_text(encoding="utf-8")):
        if item["slot"] == "act":
            readings[(item["manner"], item["key"])] = item["label"]

    manners_with_a_vocabulary = {m for m, _ in readings}
    for act in acts:
        manner, family = act["manner"], act["family"]
        if manner not in manners_with_a_vocabulary:
            continue
        label = readings.get((manner, family))
        if label is None:
            continue
        assert "only person" not in label.lower(), (
            f"{manner}/{act['concept_key']} needs {act['needs']!r} but sits in family "
            f"{family!r}, whose reading says she is alone: {label!r}")


def test_a_vocabulary_that_asks_two_questions_refuses_a_pass_that_names_neither(client, seeded):
    """One menu, one answer -- so the readings on it have to be mutually
    exclusive, and directed's cameras were not: they hold where the camera stood
    AND how high it was, and both are true of every photograph. Session 382
    measured what that costs, a camera side-on in 10 of 10 read `hip-level` 6
    and `side-level` 1.

    So an axis-tagged vocabulary refuses a pass that does not name one, rather
    than serving the menu that produced that number. A pass that names an axis
    gets only those readings, and only the photographs whose drawn family is on
    that axis: the rest asked a different question and are not misses.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "two questions",
        "manner": "directed", "checkpoint": "ckpt1", "shots": [],
    }).json()["id"]
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride"}]}
    framing = {"key": "full-length", "wordings": [{"key": "full-length", "text": "full"}]}
    shot = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": {"key": "side-level", "wordings": [{"key": "side-level", "text": "side"}]},
        "act": act, "framing": framing, "mode": "exploratory",
    }).json()["ids"][0]
    db.run("UPDATE shot SET status='done' WHERE id=?", shot)
    db.run("DELETE FROM reading WHERE slot='camera' AND manner='directed'")
    client.post("/api/readings", json={
        "slot": "camera", "manner": "directed", "key": "side-level",
        "label": "The camera is to one side of her.",
    })

    # Untagged, the pass serves as it always has.
    base = client.get(f"/api/sessions/{sid}/judge-pass?slot=camera")
    assert base.status_code == 200, base.json()

    db.run("UPDATE reading SET axis='position' WHERE slot='camera' AND manner='directed' AND key='side-level'")
    client.post("/api/readings", json={
        "slot": "camera", "manner": "directed", "key": "hip-level",
        "label": "The lens is level with her hips.", "axis": "height",
    })

    refused = client.get(f"/api/sessions/{sid}/judge-pass?slot=camera")
    assert refused.status_code == 422, refused.json()
    detail = refused.json()["detail"]
    assert "position" in detail and "height" in detail
    assert "shots" not in refused.json()

    on_axis = client.get(f"/api/sessions/{sid}/judge-pass?slot=camera&axis=position").json()
    assert [r["key"] for r in on_axis["readings"]] == ["side-level"]
    assert on_axis["shots"] == [shot]

    # The same photograph is not a miss on the other question, it is not asked.
    off_axis = client.get(f"/api/sessions/{sid}/judge-pass?slot=camera&axis=height").json()
    assert [r["key"] for r in off_axis["readings"]] == ["hip-level"]
    assert off_axis["shots"] == []

    # A family with NO reading anywhere still refuses, axis or not: that is the
    # safety net, and narrowing it to one axis would turn it into a silent skip.
    db.run("DELETE FROM reading WHERE slot='camera' AND manner='directed' AND key='side-level'")
    missing = client.get(f"/api/sessions/{sid}/judge-pass?slot=camera&axis=height")
    assert missing.status_code == 422
    assert "side-level" in missing.json()["detail"]
