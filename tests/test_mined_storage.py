"""Storing the mined camera and act rows, and what they are worth on arrival.

8.14. A mined row is a candidate somebody's library rendered and this project
has measured at nothing. Two things have to be true of it the moment it lands:
it is `unknown` - the catalogue's own word for a cell nobody has judged - and it
carries the sentence a blind judge would be shown, because a row that cannot be
put to a judge cannot stop being unknown.

The store is `/api/components/import`, the catalogue's own. A second insert path
for mined rows would be a second set of rules about what a component is, and the
two would disagree the first time one of them was edited.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import db
from backend.mining import (
    JudgeLabelMissingError,
    MINED_LABELS_FILE,
    component_rows,
    load_mined_labels,
    save_mined_labels,
    split_fused_entry,
)

ROOT = Path(__file__).resolve().parents[1]

CAMERA = "held low between his feet looking up the length of her"
ACT = "she is on her knees over him with both hands on his chest"
ROOM = "a bare bedroom with the curtain half drawn"
ENTRY = {
    "identifier": "invented_mined_store_01",
    "family": "rear entry POV",
    "prompt": f"{CAMERA}, {ACT}, {ROOM}",
}
CUT = {"camera": CAMERA, "act": ACT, "room": ROOM}
LABELS = {
    "mined-invented_mined_store_01-camera": "Camera on the floor, looking up at her from below",
    "mined-invented_mined_store_01-act": "She is kneeling over him, facing him",
}


def _mined_rows() -> list[dict]:
    return split_fused_entry(ENTRY, CUT)


def test_a_mined_row_arrives_unverified_and_labelled(client, seeded):
    """The two properties, asserted through the app rather than on the dict.

    `state` is the catalogue's own reading of a cell's counts, so asserting it
    here is asserting that nothing about the storage path made a mined row look
    measured. It is `unknown` and not a fourth word: the verdict vocabulary is
    three words bound to `db.cell_state`, and "unverified" as a state of its own
    would be a second vocabulary for one question.

    The judge label is asserted on the camera because that is the row the source
    library is being mined FOR, and a camera with no label is a camera that
    cannot be judged and therefore can never leave `unknown`.
    """
    rows = component_rows(_mined_rows(), LABELS)
    r = client.post("/api/components/import", json=rows)
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 2, r.json()

    stored = {c["concept_key"]: c for c in client.get("/api/components").json()}
    camera = stored["mined-invented_mined_store_01-camera"]
    act = stored["mined-invented_mined_store_01-act"]

    assert camera["state"] == "unknown", camera
    assert camera["judged"] == 0 and camera["arrived"] == 0, camera
    assert camera["judge_label"] == LABELS["mined-invented_mined_store_01-camera"]
    assert camera["wording"] == CAMERA
    assert act["state"] == "unknown", act
    # The manner its family declared, carried all the way to the store. A row
    # stored under `directed` would be measured in a matrix whose instruction
    # says somebody else is holding the camera.
    assert camera["manner"] == "pov" and act["manner"] == "pov"
    # The act's requirement survives the conversion: the family floor put `him`
    # on it, and a store that dropped it would deal a two-body act into a
    # single-body run.
    assert act["needs"] == "him", act


def test_a_mined_row_carries_no_measurement_it_has_not_had(client, seeded):
    """Empty family, faces and cameras, and that is a statement.

    Each of the three is a reading somebody took. Filled in by the importer they
    would be read downstream as measurements - `fitCameras` walks `cameras` to
    plan a shoot, and a family is what the camera matrix groups on - and the
    first mined row would arrive claiming three findings nobody has.
    """
    rows = component_rows(_mined_rows(), LABELS)
    client.post("/api/components/import", json=rows)
    camera = {c["concept_key"]: c for c in client.get("/api/components").json()}[
        "mined-invented_mined_store_01-camera"
    ]
    assert camera["family"] == "", camera
    assert camera["faces"] == "", camera
    assert camera["cameras"] == [], camera


def test_a_room_row_is_not_stored_as_a_component():
    """The catalogue has three slots and none of them is a room.

    The entry carries one and that is ordinary - it reaches a photograph as the
    session's look, through the room library. Refusing the entry over it would
    make every fused entry unstorable; storing it would put a room in a slot the
    schema's CHECK does not have.
    """
    rows = component_rows(_mined_rows(), LABELS)
    assert [row["slot"] for row in rows] == ["camera", "act"]


def test_an_unlabelled_mined_row_is_refused_by_name():
    """Nothing is stored, and the whole shortfall is named.

    A store that inserted the labelled rows and skipped the rest would leave one
    source entry mined into a camera with no act, and the operator would have to
    diff the catalogue against the library to find out. The two failures are
    reported apart because the repairs are different: one label has to be
    written, the other has to be rewritten.
    """
    rows = _mined_rows()
    with pytest.raises(JudgeLabelMissingError) as absent:
        component_rows(rows, {})
    assert absent.value.unlabelled == [
        "mined-invented_mined_store_01-camera",
        "mined-invented_mined_store_01-act",
    ]
    assert absent.value.echoed == []

    echo = dict(LABELS, **{"mined-invented_mined_store_01-camera": CAMERA})
    with pytest.raises(JudgeLabelMissingError) as repeated:
        component_rows(rows, echo)
    assert repeated.value.echoed == ["mined-invented_mined_store_01-camera"]
    assert "repeats the wording" in str(repeated.value)


def test_the_labels_file_round_trips_and_refuses_a_tracked_path(tmp_path):
    """Curated, and kept where the cut map is kept.

    The values are prose about the source entries, so the file is untracked -
    and that is a check rather than a sentence in a docstring, because a rule
    nothing executes is a rule that holds until the first hurry. An absent file
    is no labels at all, which is the state before anybody has labelled
    anything.
    """
    path = tmp_path / MINED_LABELS_FILE
    assert load_mined_labels(path) == {}
    save_mined_labels(LABELS, path)
    assert load_mined_labels(path) == LABELS

    tracked = ROOT / "backend" / "invented-labels-should-never-live-here.json"
    assert not tracked.exists()
    with pytest.raises(ValueError, match="would be tracked by git"):
        save_mined_labels(LABELS, tracked)
    assert not tracked.exists(), "a refused save must write nothing"
    with pytest.raises(ValueError, match="is empty"):
        save_mined_labels({"mined-x-camera": "   "}, tmp_path / "blank.json")


# -- 8.15 A mined row that the catalogue already carries -------------------


def test_a_mined_row_re_imported_creates_no_second_row(client, seeded):
    """Re-mining the same library twice is ordinary, and it must be a no-op.

    The row key is derived from the source identifier, so the second run names
    the same row - and the import matches on the key, reports it and leaves the
    stored row exactly as it is. A second row would split one cell's evidence
    across two names, and every consumer counting either would be counting half
    a measurement.
    """
    rows = component_rows(_mined_rows(), LABELS)
    first = client.post("/api/components/import", json=rows).json()
    assert first["added"] == 2 and first["duplicates"] == []

    again = client.post("/api/components/import", json=rows).json()
    assert again["added"] == 0, again
    assert {d["concept_key"] for d in again["duplicates"]} == {
        "mined-invented_mined_store_01-camera",
        "mined-invented_mined_store_01-act",
    }
    assert {d["matched_on"] for d in again["duplicates"]} == {"key"}
    stored = client.get("/api/components").json()
    assert len([c for c in stored if c["concept_key"].startswith("mined-")]) == 2


def test_a_mined_wording_the_catalogue_already_carries_is_reported(client, seeded):
    """The finding: one clause, two names.

    The catalogue row here was written by hand under its own key and says
    exactly what the mined camera says. Stored as a second row it would be
    judged separately, and the matrix would hold two cells for one photograph
    with the evidence split between them. So the import creates nothing and
    names the row it collided with, because the operator's next move is to point
    the source entry at that key.
    """
    client.post("/api/components/import", json=[{
        "concept_key": "hand-written-low-camera", "slot": "camera", "manner": "pov",
        "wording": CAMERA, "judge_label": "Low, from the floor", "family": "", "faces": "",
    }])
    before = len(client.get("/api/components").json())

    report = client.post("/api/components/import",
                         json=component_rows(_mined_rows(), LABELS)).json()
    collision = [d for d in report["duplicates"] if d["matched_on"] == "wording"]
    assert len(collision) == 1, report
    assert collision[0]["concept_key"] == "mined-invented_mined_store_01-camera"
    assert collision[0]["existing_key"] == "hand-written-low-camera"
    # The act is not a duplicate, so exactly one row was added.
    assert len(client.get("/api/components").json()) == before + 1


def test_the_same_wording_under_another_manner_is_not_a_duplicate(client, seeded):
    """A cell is keyed on the manner, so the same words there are another measurement.

    A duplicate report that ignored the manner would refuse to store a mined pov
    camera because a directed row happens to use the same clause - and the pov
    matrix would then have a hole where a measurable row belongs.
    """
    client.post("/api/components/import", json=[{
        "concept_key": "directed-low-camera", "slot": "camera", "manner": "directed",
        "wording": CAMERA, "judge_label": "Low, from the floor", "family": "", "faces": "",
    }])
    report = client.post("/api/components/import",
                         json=component_rows(_mined_rows(), LABELS)).json()
    assert report["added"] == 2, report
    assert report["duplicates"] == [], report


# -- 8.17 An unverified mined row and the strict composer ------------------

CAMERA_KEY = "mined-invented_mined_store_01-camera"
ACT_KEY = "mined-invented_mined_store_01-act"


def _pov_session(client, seeded) -> int:
    return client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "mined", "look": "A bare bedroom.",
        "manner": "pov", "checkpoint": "test-checkpoint", "shots": [],
    }).json()["id"]


def _mined_candidates() -> dict:
    return {
        "camera": [{"key": CAMERA_KEY, "wordings": [{"key": CAMERA_KEY, "text": CAMERA}]}],
        "act": [{"key": ACT_KEY, "wordings": [{"key": ACT_KEY, "text": ACT}]}],
        "framing": [{"key": "frame-mined",
                     "wordings": [{"key": "frame-mined", "text": "full body"}]}],
    }


def test_an_unverified_mined_camera_is_not_drawn_on_the_strict_path(client, seeded):
    """No new gate, and that is the finding.

    The strict pool is the verified cells and nothing else, and a mined row has
    no cell at all - so it is out of the strict draw by the rule that was
    already there, not by one written for mined rows. A second gate keyed on
    "is this row mined" would be a second calculation of what drawable means,
    and the two would disagree the first time either was edited.

    The exploratory half is what makes this a test of the MODE rather than of
    the rows: the same call with the wider mode queues the shot, so the refusal
    above is about the measurement and not about anything being wrong with a
    mined row.
    """
    client.post("/api/components/import", json=component_rows(_mined_rows(), LABELS))
    sid = _pov_session(client, seeded)

    strict = client.post(f"/api/sessions/{sid}/compose-run",
                         json={"count": 1, "candidates": _mined_candidates()})
    assert strict.status_code == 422, strict.text
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"] == 0

    wider = client.post(f"/api/sessions/{sid}/compose-run",
                        json={"count": 1, "mode": "exploratory",
                              "candidates": _mined_candidates()})
    assert wider.status_code == 200, wider.text
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"] == 1


def test_the_strict_draw_passes_over_the_mined_trio_for_the_measured_one(client, seeded):
    """Asserted on the POOL SIZE, because "it drew something else" can pass by luck.

    Both trios are candidates and only one is a verified cell. The strict run
    draws that one, and a run asking for TWO distinct trios out of a pool of one
    is refused - which is the observable difference between a pool that holds
    the mined trio and one that does not. See [[idevgen-test-that-cannot-fail]].
    """
    client.post("/api/components/import", json=component_rows(_mined_rows(), LABELS))
    client.post("/api/components/import", json=[
        {"concept_key": "measured-cam", "slot": "camera", "manner": "pov",
         "wording": "from across the room", "judge_label": "Across the room"},
        {"concept_key": "measured-act", "slot": "act", "manner": "pov",
         "wording": "she is sitting on the edge of the bed", "judge_label": "Sitting"},
    ])
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, manner, "
           "checkpoint, judged, arrived) VALUES (?,?,?,?,?,?,?)",
           "measured-cam", "measured-act", "frame-mined", "pov", "test-checkpoint", 10, 9)

    candidates = _mined_candidates()
    candidates["camera"].append(
        {"key": "measured-cam", "wordings": [{"key": "measured-cam", "text": "from across the room"}]})
    candidates["act"].append(
        {"key": "measured-act", "wordings": [{"key": "measured-act", "text": "she is sitting on the edge of the bed"}]})

    sid = _pov_session(client, seeded)
    one = client.post(f"/api/sessions/{sid}/compose-run",
                      json={"count": 1, "candidates": candidates})
    assert one.status_code == 200, one.text
    drawn = [json.loads(r["components"])
             for r in db.q("SELECT components FROM shot WHERE session_id=?", sid)]
    assert drawn, "the run queued nothing, so it proves nothing about what it draws"
    for shot in drawn:
        assert shot["camera"]["concept"] == "measured-cam", shot

    two = _pov_session(client, seeded)
    refused = client.post(f"/api/sessions/{two}/compose-run",
                          json={"count": 2, "candidates": candidates})
    assert refused.status_code == 422, refused.text
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", two)["n"] == 0


def test_a_mined_trio_part_way_through_judging_is_still_not_drawn(client, seeded):
    """Unverified is not only "never measured", and this is the half that catches it.

    Once 8.18 starts judging these rows the mined trio HAS a cell, and it is
    unknown until the tenth photograph. A strict pool that asked the table for a
    row rather than for a VERIFIED row would pass this trio the moment the first
    frame was judged - and it would read as measured while its sample was four.
    The two tests above cannot see that break, because a trio nobody has judged
    has no row for a loose predicate to match.
    """
    client.post("/api/components/import", json=component_rows(_mined_rows(), LABELS))
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, manner, "
           "checkpoint, judged, arrived) VALUES (?,?,?,?,?,?,?)",
           CAMERA_KEY, ACT_KEY, "frame-mined", "pov", "test-checkpoint", 4, 4)
    sid = _pov_session(client, seeded)

    strict = client.post(f"/api/sessions/{sid}/compose-run",
                         json={"count": 1, "candidates": _mined_candidates()})
    assert strict.status_code == 422, strict.text
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"] == 0

    # And the same cell at the threshold IS drawn, so the refusal above is the
    # sample size and not the row.
    db.run("UPDATE cell SET judged=10, arrived=9 WHERE camera_wording=?", CAMERA_KEY)
    verified = client.post(f"/api/sessions/{sid}/compose-run",
                           json={"count": 1, "candidates": _mined_candidates()})
    assert verified.status_code == 200, verified.text
