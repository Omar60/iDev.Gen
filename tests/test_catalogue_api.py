"""API tests for the component catalogue, defects, and empty catalogue refusals."""
from __future__ import annotations

import json
from pathlib import Path
import pytest
import db


ROOT = Path(__file__).resolve().parents[1]


def _make_model(client):
    res = client.post("/api/models", json={
        "name": "Test Model",
        "lora_name": "test.safetensors",
        "trigger": "test_trigger",
        "workflow_id": 1,
    })
    assert res.status_code == 200
    return res.json()["id"]


def _make_workflow(client):
    res = client.post("/api/workflows", json={
        "name": "Test Workflow",
        "graph": {"4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "test_ckpt.safetensors"}}},
        "node_map": {"checkpoint": "4.inputs.ckpt_name"},
    })
    assert res.status_code == 200
    return res.json()["id"]


def test_get_components_filters_retired(client):
    """GET /api/components returns non-retired rows by default, and all rows with all=1."""
    # Start clean
    db.run("DELETE FROM component")
    c1 = client.post("/api/components", json={
        "concept_key": "c1",
        "slot": "camera",
        "manner": "directed",
        "family": "front",
        "faces": "front",
        "wording": "Camera wording 1",
        "judge_label": "Camera label 1",
    }).json()
    c2 = client.post("/api/components", json={
        "concept_key": "c2",
        "slot": "camera",
        "manner": "directed",
        "family": "front",
        "faces": "front",
        "wording": "Camera wording 2",
        "judge_label": "Camera label 2",
    }).json()

    # Retire c2
    client.post(f"/api/components/{c2['id']}/retire")

    # Default: only c1
    res = client.get("/api/components")
    assert res.status_code == 200
    ids = [r["id"] for r in res.json()]
    assert ids == [c1["id"]]

    # With all=1: both c1 and c2
    res_all = client.get("/api/components?all=1")
    assert res_all.status_code == 200
    ids_all = [r["id"] for r in res_all.json()]
    assert c1["id"] in ids_all and c2["id"] in ids_all


def test_component_crud_and_validations(client):
    """POST /api/components and PATCH /api/components/{id} validations."""
    db.run("DELETE FROM component")

    # Empty judge_label -> 422
    res = client.post("/api/components", json={
        "concept_key": "c1", "slot": "camera", "manner": "directed",
        "wording": "Some wording", "judge_label": "",
    })
    assert res.status_code == 422

    # judge_label == wording -> 422
    res = client.post("/api/components", json={
        "concept_key": "c1", "slot": "camera", "manner": "directed",
        "wording": "Identical text", "judge_label": "Identical text",
    })
    assert res.status_code == 422

    # Invalid slot -> 422
    res = client.post("/api/components", json={
        "concept_key": "c1", "slot": "invalid_slot", "manner": "directed",
        "wording": "Some wording", "judge_label": "Some label",
    })
    assert res.status_code == 422

    # Successful creation
    res = client.post("/api/components", json={
        "concept_key": "c1", "slot": "camera", "manner": "directed",
        "family": "front", "faces": "front",
        "wording": "Unique wording", "judge_label": "Unique label",
    })
    assert res.status_code == 200
    comp = res.json()
    assert comp["concept_key"] == "c1"
    assert comp["retired_at"] is None

    # Duplicate (slot, manner, wording) -> 422
    res = client.post("/api/components", json={
        "concept_key": "c2", "slot": "camera", "manner": "directed",
        "wording": "Unique wording", "judge_label": "Different label",
    })
    assert res.status_code == 422

    # PATCH
    patch_res = client.patch(f"/api/components/{comp['id']}", json={
        "judge_label": "Updated label",
    })
    assert patch_res.status_code == 200
    assert patch_res.json()["judge_label"] == "Updated label"

    # PATCH invalid wording == judge_label -> 422
    patch_err = client.patch(f"/api/components/{comp['id']}", json={
        "judge_label": "Unique wording",
    })
    assert patch_err.status_code == 422


def test_retire_and_restore(client):
    """POST /api/components/{id}/retire and restore."""
    db.run("DELETE FROM component")
    c = client.post("/api/components", json={
        "concept_key": "c1", "slot": "camera", "manner": "directed",
        "wording": "W1", "judge_label": "L1",
    }).json()

    ret_res = client.post(f"/api/components/{c['id']}/retire")
    assert ret_res.status_code == 200
    assert ret_res.json()["retired_at"] is not None

    rest_res = client.post(f"/api/components/{c['id']}/restore")
    assert rest_res.status_code == 200
    assert rest_res.json()["retired_at"] is None


def test_delete_component_with_and_without_evidence(client):
    """DELETE /api/components/{id} refuses if cell evidence or judged shots exist."""
    db.run("DELETE FROM component")
    db.run("DELETE FROM cell")
    c = client.post("/api/components", json={
        "concept_key": "c1", "slot": "camera", "manner": "directed",
        "wording": "Camera wording test", "judge_label": "Camera label test",
    }).json()

    # Add cell evidence with judged > 0, keyed the way the composer keys it:
    # the CONCEPT KEY in the slot's column, never the wording text. The first
    # version of this test wrote the wording there, which is a row the
    # application cannot produce — it passed against a guard that could never
    # fire in production.
    db.run(
        "INSERT INTO cell (camera_wording, act_wording, framing_wording, manner, checkpoint, judged, arrived, contradicted) "
        "VALUES (?, 'act', 'framing', 'directed', 'ckpt', 10, 8, 0)",
        "c1",
    )

    # Delete should return 422
    del_res = client.delete(f"/api/components/{c['id']}")
    assert del_res.status_code == 422
    assert "retire" in del_res.json()["detail"].lower()

    # Clear cell evidence
    db.run("DELETE FROM cell")

    # A judged shot is evidence too, and it outlives a cell wipe. Same shape
    # the composer writes: the concept key under the component's own slot.
    mid = db.run("INSERT INTO model (name, lora_name, trigger, created_at) "
                 "VALUES ('m', 'characters/ada.safetensors', 't', ?)", db.now())
    sid = db.run("INSERT INTO session (model_id, name, created_at) VALUES (?, 's', ?)",
                 mid, db.now())
    db.run(
        "INSERT INTO shot (session_id, shot_index, prompt, components, verdicts, created_at) "
        "VALUES (?, 0, 'p', ?, ?, ?)",
        sid,
        '{"camera": {"concept": "c1", "wording": "Camera wording test"}}',
        '{"camera": ""}', db.now(),
    )
    del_shot = client.delete(f"/api/components/{c['id']}")
    assert del_shot.status_code == 422
    assert "retire" in del_shot.json()["detail"].lower()
    db.run("DELETE FROM shot")

    # Now delete succeeds
    del_ok = client.delete(f"/api/components/{c['id']}")
    assert del_ok.status_code == 200
    assert del_ok.json() == {"ok": True}


def test_import_components_idempotent(client):
    """POST /api/components/import is idempotent and imports seed catalogue."""
    db.run("DELETE FROM component")
    res1 = client.post("/api/components/import")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["added"] > 0
    assert data1["skipped"] == 0

    res2 = client.post("/api/components/import")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["added"] == 0
    assert data2["skipped"] == data1["added"]


def test_judge_endpoint_defect_and_validations(client):
    """POST /api/shots/{id}/judge with defect, choice, and both."""
    db.run("DELETE FROM component")
    db.run("DELETE FROM cell")
    client.post("/api/components/import")

    # Create model, workflow, session, composed shot
    wf_id = _make_workflow(client)
    mid = _make_model(client)
    s_res = client.post("/api/sessions", json={
        "model_id": mid,
        "name": "Judge Defect Test Session",
        "workflow_id": wf_id,
        "manner": "directed",
        "checkpoint": "test_ckpt.safetensors",
        "shots": [],
    })
    assert s_res.status_code == 200
    sid = s_res.json()["id"]

    comp_res = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": {"key": "Taken from directly in front of her"},
        "act": {"key": "She is astride him with her knees on either side of his hips and her weight down on him, the two of them joined, two people in frame."},
        "framing": {"key": "a three-quarter photograph from the knees up"},
        "mode": "exploratory",
        "count": 1,
    })
    assert comp_res.status_code == 200

    shot = db.one("SELECT * FROM shot WHERE session_id=?", sid)
    assert shot is not None

    # Case 1: Both answer choice and defect sent -> 422
    err_res = client.post(f"/api/shots/{shot['id']}/judge", json={
        "camera": "Taken from directly in front of her",
        "defect": "contradiction",
        "slot": "camera",
    })
    assert err_res.status_code == 422
    assert "Cannot specify both" in err_res.json()["detail"]

    # Case 2: Defect only -> records contradiction, increments contradicted
    ok_res = client.post(f"/api/shots/{shot['id']}/judge", json={
        "defect": "contradiction",
        "slot": "camera",
    })
    assert ok_res.status_code == 200
    data = ok_res.json()
    assert data["contradicted"] == 1
    assert data["judged"] == 1
    assert data["arrived"] == 0

    shot_updated = db.one("SELECT verdicts FROM shot WHERE id=?", shot["id"])
    verdicts = json.loads(shot_updated["verdicts"])
    assert verdicts["camera"] == ""
    assert verdicts["camera_defect"] == "contradiction"


def test_empty_catalogue_refusals(client):
    """Empty catalogue refusals for compose and session creation."""
    db.run("DELETE FROM component")
    wf_id = _make_workflow(client)
    mid = _make_model(client)

    # 1. Create written session with manner 'directed' when camera catalogue is empty -> 422
    s_err = client.post("/api/sessions", json={
        "model_id": mid,
        "name": "Empty Catalogue Session",
        "workflow_id": wf_id,
        "manner": "directed",
        "checkpoint": "test_ckpt.safetensors",
        "shots": [{"prompt": "written shot 1"}],
    })
    assert s_err.status_code == 422
    assert "camera catalogue is empty" in s_err.json()["detail"]
    assert db.one("SELECT COUNT(*) AS n FROM shot")["n"] == 0

    # 2. Add only cameras
    client.post("/api/components", json={
        "concept_key": "front-direct", "slot": "camera", "manner": "directed",
        "family": "front", "faces": "front",
        "wording": "Front Cam", "judge_label": "Front Cam Label",
    })

    # Now written session creation succeeds
    s_ok = client.post("/api/sessions", json={
        "model_id": mid,
        "name": "Session with Camera",
        "workflow_id": wf_id,
        "manner": "directed",
        "checkpoint": "test_ckpt.safetensors",
        "shots": [{"prompt": "written shot 1"}],
    })
    assert s_ok.status_code == 200
    sid = s_ok.json()["id"]

    # 3. Compose shot when act catalogue is empty -> 422 naming act slot and manner
    c_err = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": {"key": "Front Cam"},
        "act": {"key": "Some Act"},
        "framing": {"key": "Some Framing"},
        "mode": "exploratory",
        "count": 1,
    })
    assert c_err.status_code == 422
    assert "act catalogue is empty for manner 'directed'" in c_err.json()["detail"]
    assert "import" in c_err.json()["detail"]


def test_an_acts_camera_families_round_trip_through_the_store(client):
    """The camera families an arrangement can be seen from are a column, not a
    guess the frontend makes from `family`.

    Keyed off `family` in an if-chain over three literals, every act added
    through the catalogue screen came back with an empty list and the camera
    plan skipped it — the one thing the screen exists for produced acts
    `fitCameras` ignored.
    """
    db.run("DELETE FROM component")
    made = client.post("/api/components", json={
        "concept_key": "spooning", "slot": "act", "manner": "directed",
        "family": "spooning", "faces": "back",
        "wording": "They are lying on their sides, he is behind her.",
        "judge_label": "Both lying on their sides, he is behind her",
        "cameras": ["shoulder", "overhead"],
    })
    assert made.status_code == 200
    assert made.json()["cameras"] == ["shoulder", "overhead"]

    listed = [c for c in client.get("/api/components").json() if c["slot"] == "act"]
    assert listed[0]["cameras"] == ["shoulder", "overhead"]

    comp_id = made.json()["id"]
    assert client.patch(f"/api/components/{comp_id}",
                        json={"cameras": ["mirror"]}).json()["cameras"] == ["mirror"]
    # A patch that does not mention cameras leaves them alone.
    assert client.patch(f"/api/components/{comp_id}",
                        json={"faces": "front"}).json()["cameras"] == ["mirror"]


def test_the_imported_acts_carry_the_families_they_were_measured_on(client):
    """`reverse` renders from behind her shoulder and nowhere else (3/3 there,
    1/3 from the mirror and the overhead). If the import drops that list, the
    camera plan stops moving the photograph and the arrangement comes back as a
    different one — measured, session 267.
    """
    db.run("DELETE FROM component")
    assert client.post("/api/components/import").status_code == 200
    acts = {c["concept_key"]: c["cameras"]
            for c in client.get("/api/components").json()
            if c["slot"] == "act" and c["manner"] == "directed"}
    assert acts["reverse"] == ["shoulder"]
    assert acts["wall"] == ["mirror", "shoulder"]
    assert acts["astride"] == ["front", "overhead", "mirror", "pov"]


def test_components_carry_their_evidence_with_contradictions_apart(client):
    """GET /api/components reports judged, arrived and contradicted per row.

    Nothing in the app showed a cell's counts, so the contradiction answer was
    recorded and then invisible — a defect the operator can only act on by
    reading the database. `contradicted` rides beside the miss count rather
    than inside it: a cell that failed by contradiction and one that failed by
    rendering some other component are two findings with two repairs.
    """
    db.run("DELETE FROM component")
    db.run("DELETE FROM cell")
    made = client.post("/api/components", json={
        "concept_key": "shoulder-left", "slot": "camera", "manner": "directed",
        "wording": "Taken from behind her left shoulder", "judge_label": "From behind her shoulder",
    }).json()

    # Unmeasured: zeros, and `unknown` — the state a cell with no rows reads.
    fresh = [c for c in client.get("/api/components").json() if c["id"] == made["id"]][0]
    assert (fresh["judged"], fresh["arrived"], fresh["contradicted"]) == (0, 0, 0)
    assert fresh["state"] == "unknown"

    # Ten judged, none arrived, seven of the misses contradictions.
    db.run(
        "INSERT INTO cell (camera_wording, act_wording, framing_wording, manner, checkpoint, "
        "judged, arrived, contradicted) VALUES ('shoulder-left', 'a', 'f', 'directed', 'ckpt', 10, 0, 7)"
    )
    measured = [c for c in client.get("/api/components").json() if c["id"] == made["id"]][0]
    assert (measured["judged"], measured["arrived"], measured["contradicted"]) == (10, 0, 7)
    assert measured["state"] == "dead"

    # A cell under another manner is not this component's evidence.
    db.run(
        "INSERT INTO cell (camera_wording, act_wording, framing_wording, manner, checkpoint, "
        "judged, arrived, contradicted) VALUES ('shoulder-left', 'a', 'f', 'candid', 'ckpt', 10, 10, 0)"
    )
    still = [c for c in client.get("/api/components").json() if c["id"] == made["id"]][0]
    assert (still["judged"], still["arrived"], still["contradicted"]) == (10, 0, 7)
