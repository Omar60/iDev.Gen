"""Independent black-box acceptance for Full Auto guided input binding."""
from __future__ import annotations

import json
import uuid

import pytest

import db
import main
from backend import saved_looks
from test_guided_session_creation import (
    _automation_profile,
    _creation_snapshot,
    _forbid_full_auto_side_effects,
    _full_auto_payload,
    _room_anchor,
)


FULL_BODY_FRAMING = (
    "Full body, head to toe, both feet visible, with space above the head "
    "and below the feet."
)


def _assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert isinstance(detail["message"], str) and detail["message"]
    return detail


def _preflight_body(payload: dict) -> dict:
    body = {key: value for key, value in payload.items() if key != "request_id"}
    body["full_auto"] = {
        key: value
        for key, value in body["full_auto"].items()
        if key not in {"scope_digest", "scope_issued_at"}
    }
    return body


def _authorize(client, body: dict) -> tuple[dict, dict]:
    consent = client.post("/api/sessions/guided/full-auto/preflight", json=body)
    assert consent.status_code == 200, consent.text
    creation = {
        **body,
        "request_id": str(uuid.uuid4()),
        "full_auto": {
            **body["full_auto"],
            "scope_digest": consent.json()["scope_digest"],
            "scope_issued_at": consent.json()["scope_issued_at"],
        },
    }
    return consent.json(), creation


def _workflow(client, graph: dict, kind: str) -> int:
    response = client.post("/api/workflows", json={
        "name": f"task33-{kind or 'empty'}-{uuid.uuid4().hex}",
        "graph": graph,
        "kind": kind,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_authorization_and_creation_keep_explicit_workflow_canvas_and_exact_look(
    client, seeded, monkeypatch,
):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "task33-provider")
    _forbid_full_auto_side_effects(monkeypatch)
    payload, saved, _published = _full_auto_payload(client, seeded, _room_anchor())
    graph = json.loads(db.one(
        "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
    )["graph"])
    override_id = _workflow(client, graph, "t2i")
    default_consent, _default_request = _authorize(client, _preflight_body(payload))
    model_default = db.one("SELECT workflow_id FROM model WHERE id = ?", seeded["model_id"])["workflow_id"]
    assert default_consent["summary"]["workflow"]["workflow_id"] == model_default
    body = _preflight_body(payload)
    body.update({
        "workflow_id": override_id,
        "width": 768,
        "height": 1024,
        "variation_policy": {
            "camera": {"mode": "fixed", "value": "50mm portrait lens", "value_origin": "user"},
            "framing": {"mode": "fixed", "value": "three-quarter crop", "value_origin": "user"},
            "pose": {"mode": "vary"},
            "expression": {"mode": "fixed", "value": "quiet smile", "value_origin": "user"},
        },
    })
    consent, authorized = _authorize(client, body)

    newer = client.post(f"/api/looks/{saved['key']}/versions", json={
        "expected_version": saved["version"],
        "name": "A newer saved look",
        "appearance": "A different model in a different studio.",
    })
    assert newer.status_code == 200, newer.text
    assert newer.json()["version"] == saved["version"] + 1

    response = client.post("/api/sessions/guided", json=authorized)
    assert response.status_code == 201, response.text
    result = response.json()
    sid = result["session_id"]
    session = db.one("SELECT * FROM session WHERE id = ?", sid)
    session_settings = json.loads(session["settings"])
    plan = json.loads(db.one(
        "SELECT plan_json FROM session_plan WHERE session_id = ?", sid,
    )["plan_json"])
    receipt = db.one(
        "SELECT authorization_json FROM full_auto_run_receipt WHERE run_id = ?",
        result["full_auto_run_id"],
    )
    authorization = json.loads(receipt["authorization_json"])
    summary = consent["summary"]
    plan_binding = plan["authoring"]["workflow_binding"]

    assert session["workflow_id"] == override_id
    assert session["reference_workflow_id"] is None
    assert session_settings["width"] == summary["canvas"]["width"] == 768
    assert session_settings["height"] == summary["canvas"]["height"] == 1024
    assert summary["character_id"] == seeded["model_id"]
    assert summary["model_binding"]["model_id"] == seeded["model_id"]
    assert summary["workflow"]["workflow_id"] == override_id
    assert summary["workflow"]["kind"] == "t2i"
    assert summary["workflow"]["graph_digest"] == plan_binding["graph_digest"]
    assert summary["workflow"]["node_map_digest"] == plan_binding["node_map_digest"]
    assert summary["variation_policy"] == body["variation_policy"]
    assert summary["effective_framing"] == body["variation_policy"]["framing"]
    assert authorization["summary"] == summary
    assert plan_binding["workflow_id"] == override_id
    assert plan_binding["kind"] == "t2i"
    assert plan["authoring"]["variation_policy"] == body["variation_policy"]
    assert plan["authoring"]["look_snapshot"]["version"] == saved["version"]
    assert plan["authoring"]["look_snapshot"]["content_digest"] == saved["content_digest"]
    assert plan["look"] == saved["appearance"]
    assert all(take.get("reference") is not True for take in plan["takes"])


def test_default_workflow_inherited_canvas_and_exact_profile_revision_survive_drift(
    client, seeded, monkeypatch,
):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "task33-provider")
    _forbid_full_auto_side_effects(monkeypatch)
    payload, saved, published = _full_auto_payload(client, seeded, _room_anchor())
    body = _preflight_body(payload)
    body.pop("width", None)
    body.pop("height", None)
    body.pop("workflow_id", None)
    consent, authorized = _authorize(client, body)

    model_before = db.one("SELECT * FROM model WHERE id = ?", seeded["model_id"])
    inherited_settings = json.loads(model_before["settings"])
    assert "width" not in body and "height" not in body
    assert consent["summary"]["workflow"]["workflow_id"] == model_before["workflow_id"]
    assert consent["summary"]["canvas"] == {
        "width": inherited_settings["width"],
        "height": inherited_settings["height"],
    }

    next_profile = saved_looks.publish_automation_profile(
        saved["key"], saved["version"], saved["content_digest"],
        published["profile_revision"],
        _automation_profile(saved, revision=published["profile_revision"] + 1),
    )
    assert next_profile["profile_revision"] == published["profile_revision"] + 1
    newer_look = client.post(f"/api/looks/{saved['key']}/versions", json={
        "expected_version": saved["version"],
        "name": "A newer saved look",
        "appearance": "A different model in a different studio.",
    })
    assert newer_look.status_code == 200, newer_look.text

    created = client.post("/api/sessions/guided", json=authorized)
    assert created.status_code == 201, created.text
    result = created.json()
    session = db.one("SELECT * FROM session WHERE id = ?", result["session_id"])
    session_settings = json.loads(session["settings"])
    plan = json.loads(db.one(
        "SELECT plan_json FROM session_plan WHERE session_id = ?", result["session_id"],
    )["plan_json"])
    evidence = db.one(
        "SELECT * FROM session_profile_evidence WHERE session_id = ? AND plan_revision = 1",
        result["session_id"],
    )
    receipt = db.one(
        "SELECT authorization_json FROM full_auto_run_receipt WHERE run_id = ?",
        result["full_auto_run_id"],
    )

    assert session["workflow_id"] == model_before["workflow_id"]
    assert session_settings["width"] == inherited_settings["width"]
    assert session_settings["height"] == inherited_settings["height"]
    assert plan["authoring"]["look_snapshot"]["version"] == saved["version"]
    assert plan["authoring"]["look_snapshot"]["content_digest"] == saved["content_digest"]
    assert plan["look"] == saved["appearance"]
    assert evidence["profile_revision"] == published["profile_revision"]
    assert evidence["profile_digest"] == published["profile_digest"]
    stored_authorization = json.loads(receipt["authorization_json"])
    assert stored_authorization["summary"]["automation_profile"]["revision"] == published["profile_revision"]
    assert stored_authorization["summary"]["automation_profile"]["digest"] == published["profile_digest"]

    graph = json.loads(db.one(
        "SELECT graph FROM workflow WHERE id = ?", model_before["workflow_id"],
    )["graph"])
    changed_default = _workflow(client, graph, "t2i")
    drifted_settings = dict(inherited_settings)
    drifted_settings["width"] += 8
    db.run(
        "UPDATE model SET workflow_id = ?, base_positive = ?, settings = ? WHERE id = ?",
        changed_default, "a later model prompt", json.dumps(drifted_settings), seeded["model_id"],
    )
    library_id = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        body["scene_anchor"]["library_key"],
    )["id"]
    db.run(
        "DELETE FROM asset_revision WHERE library_id = ? AND source_id = ? AND content_digest = ?",
        library_id,
        body["scene_anchor"]["source_id"],
        body["scene_anchor"]["content_digest"],
    )
    assert db.one(
        "SELECT 1 FROM asset_revision WHERE library_id = ? AND source_id = ? AND content_digest = ?",
        library_id,
        body["scene_anchor"]["source_id"],
        body["scene_anchor"]["content_digest"],
    ) is None

    before_replay = _creation_snapshot()
    replay = client.post("/api/sessions/guided", json=authorized)
    assert replay.status_code == 200, replay.text
    assert replay.content == created.content
    assert _creation_snapshot() == before_replay


@pytest.mark.parametrize("kind", ["", "edit", "guide", "angles", "scene"])
def test_full_auto_preflight_refuses_every_non_t2i_kind_without_allocating(
    client, seeded, monkeypatch, kind,
):
    _forbid_full_auto_side_effects(monkeypatch)
    payload, _saved, _published = _full_auto_payload(client, seeded, _room_anchor())
    graph = json.loads(db.one(
        "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
    )["graph"])
    rejected_workflow = _workflow(client, graph, kind)
    body = _preflight_body(payload)
    if kind:
        body["workflow_id"] = rejected_workflow
    else:
        db.run("UPDATE model SET workflow_id = ? WHERE id = ?", rejected_workflow, seeded["model_id"])
        body.pop("workflow_id", None)

    before = _creation_snapshot()
    response = client.post("/api/sessions/guided/full-auto/preflight", json=body)
    _assert_error(response, 422, "full_auto_workflow_unsupported")
    assert _creation_snapshot() == before


def test_full_auto_does_not_guess_a_workflow_when_model_has_no_default(
    client, seeded, monkeypatch,
):
    _forbid_full_auto_side_effects(monkeypatch)
    payload, _saved, _published = _full_auto_payload(client, seeded, _room_anchor())
    model = db.one("SELECT * FROM model WHERE id = ?", seeded["model_id"])
    replacement = client.post("/api/models", json={
        "name": f"task33-no-default-{uuid.uuid4().hex}",
        "lora_name": model["lora_name"],
        "lora_strength": model["lora_strength"],
        "trigger": model["trigger"],
        "base_positive": model["base_positive"],
        "base_negative": model["base_negative"],
        "settings": json.loads(model["settings"]),
    })
    assert replacement.status_code == 200, replacement.text
    body = _preflight_body(payload)
    body["character_id"] = replacement.json()["id"]
    body.pop("workflow_id", None)

    before = _creation_snapshot()
    response = client.post("/api/sessions/guided/full-auto/preflight", json=body)
    _assert_error(response, 422, "workflow_required")
    assert _creation_snapshot() == before


@pytest.mark.parametrize("drift", ["character", "base", "lora", "settings", "graph", "map"])
def test_creation_rejects_bound_model_or_workflow_drift_without_writes(
    client, seeded, monkeypatch, drift,
):
    _forbid_full_auto_side_effects(monkeypatch)
    payload, _saved, _published = _full_auto_payload(client, seeded, _room_anchor())
    model = db.one("SELECT * FROM model WHERE id = ?", seeded["model_id"])
    workflow_id = model["workflow_id"]
    if drift == "character":
        replacement = client.post("/api/models", json={
            "name": f"task33-replacement-{uuid.uuid4().hex}",
            "lora_name": model["lora_name"],
            "lora_strength": model["lora_strength"],
            "trigger": model["trigger"],
            "base_positive": model["base_positive"],
            "base_negative": model["base_negative"],
            "workflow_id": workflow_id,
            "settings": json.loads(model["settings"]),
        })
        assert replacement.status_code == 200, replacement.text
        payload["character_id"] = replacement.json()["id"]
    elif drift == "base":
        db.run("UPDATE model SET base_positive = ? WHERE id = ?", "changed base prompt", seeded["model_id"])
    elif drift == "lora":
        db.run("UPDATE model SET lora_name = ?, lora_strength = ? WHERE id = ?", "characters/other.safetensors", 0.75, seeded["model_id"])
    elif drift == "settings":
        settings = json.loads(model["settings"])
        settings["steps"] += 1
        db.run("UPDATE model SET settings = ? WHERE id = ?", json.dumps(settings), seeded["model_id"])
    else:
        row = db.one("SELECT graph, node_map FROM workflow WHERE id = ?", workflow_id)
        if drift == "graph":
            graph = json.loads(row["graph"])
            graph["5"]["inputs"]["width"] = 768
            db.run("UPDATE workflow SET graph = ? WHERE id = ?", json.dumps(graph), workflow_id)
        else:
            node_map = json.loads(row["node_map"])
            node_map["width"] = "5.inputs.height"
            db.run("UPDATE workflow SET node_map = ? WHERE id = ?", json.dumps(node_map), workflow_id)

    before = _creation_snapshot()
    response = client.post("/api/sessions/guided", json=payload)
    _assert_error(response, 409, "authorization_scope_changed")
    assert _creation_snapshot() == before


@pytest.mark.parametrize("initial_framing", ["vary", "exact_fixed"])
def test_full_body_uses_exact_user_framing_and_preserves_other_policy_fields(
    client, seeded, monkeypatch, initial_framing,
):
    _forbid_full_auto_side_effects(monkeypatch)
    payload, _saved, _published = _full_auto_payload(client, seeded, _room_anchor())
    body = _preflight_body(payload)
    body["full_auto"]["framing_intent"] = "full_body"
    body["variation_policy"] = {
        "camera": {"mode": "vary"},
        "framing": (
            {"mode": "vary"}
            if initial_framing == "vary"
            else {"mode": "fixed", "value": FULL_BODY_FRAMING, "value_origin": "user"}
        ),
        "pose": {"mode": "fixed", "value": "one hand on a chair", "value_origin": "user"},
        "expression": {"mode": "vary"},
    }
    consent, authorized = _authorize(client, body)

    response = client.post("/api/sessions/guided", json=authorized)
    assert response.status_code == 201, response.text
    plan = response.json()["plan"]
    policy = plan["authoring"]["variation_policy"]
    assert policy == {
        "camera": {"mode": "vary"},
        "framing": {"mode": "fixed", "value": FULL_BODY_FRAMING, "value_origin": "user"},
        "pose": body["variation_policy"]["pose"],
        "expression": {"mode": "vary"},
    }
    assert consent["summary"]["effective_framing"] == policy["framing"]
    assert plan["authoring"]["variation_policy"]["pose"] == body["variation_policy"]["pose"]


def test_full_body_conflict_uses_exact_fixed_value_equality_without_semantic_guessing(
    client, seeded, monkeypatch,
):
    _forbid_full_auto_side_effects(monkeypatch)
    payload, _saved, _published = _full_auto_payload(client, seeded, _room_anchor())
    body = _preflight_body(payload)
    body["full_auto"]["framing_intent"] = "full_body"
    body["variation_policy"] = {
        "camera": {"mode": "vary"},
        "framing": {
            "mode": "fixed",
            "value": "Head to toe, with both feet visible.",
            "value_origin": "user",
        },
        "pose": {"mode": "vary"},
        "expression": {"mode": "vary"},
    }
    before = _creation_snapshot()
    response = client.post("/api/sessions/guided/full-auto/preflight", json=body)
    _assert_error(response, 422, "framing_conflict")
    assert _creation_snapshot() == before


def test_connected_canvas_slot_is_refused_at_preflight_without_allocating(
    client, seeded, monkeypatch,
):
    _forbid_full_auto_side_effects(monkeypatch)
    payload, _saved, _published = _full_auto_payload(client, seeded, _room_anchor())
    body = _preflight_body(payload)
    body.update({"width": 768, "height": 1024})
    workflow_id = db.one("SELECT workflow_id FROM model WHERE id = ?", seeded["model_id"])["workflow_id"]
    row = db.one("SELECT graph, node_map FROM workflow WHERE id = ?", workflow_id)
    graph = json.loads(row["graph"])
    graph["5"]["inputs"]["width"] = ["1", 0]
    db.run("UPDATE workflow SET graph = ? WHERE id = ?", json.dumps(graph), workflow_id)

    before = _creation_snapshot()
    response = client.post("/api/sessions/guided/full-auto/preflight", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "workflow_compatibility_invalid"
    assert _creation_snapshot() == before
    assert json.loads(db.one(
        "SELECT graph FROM workflow WHERE id = ?", workflow_id,
    )["graph"])["5"]["inputs"]["width"] == ["1", 0]


def test_full_auto_refuses_selected_checkpoint_mapped_to_unrelated_seed_input(
    client, seeded, monkeypatch,
):
    _forbid_full_auto_side_effects(monkeypatch)
    model = db.one("SELECT settings FROM model WHERE id = ?", seeded["model_id"])
    settings = json.loads(model["settings"] or "{}")
    settings["checkpoint"] = "models/selected.safetensors"
    db.run(
        "UPDATE model SET settings = ? WHERE id = ?",
        json.dumps(settings), seeded["model_id"],
    )
    payload, _saved, _published = _full_auto_payload(client, seeded, _room_anchor())
    workflow_id = db.one(
        "SELECT workflow_id FROM model WHERE id = ?", seeded["model_id"],
    )["workflow_id"]
    workflow = db.one(
        "SELECT node_map FROM workflow WHERE id = ?", workflow_id,
    )
    node_map = json.loads(workflow["node_map"])
    assert "seed" in node_map and "checkpoint" in node_map
    node_map["checkpoint"] = node_map["seed"]
    db.run(
        "UPDATE workflow SET node_map = ? WHERE id = ?",
        json.dumps(node_map), workflow_id,
    )

    before = _creation_snapshot()
    response = client.post(
        "/api/sessions/guided/full-auto/preflight",
        json=_preflight_body(payload),
    )
    _assert_error(response, 422, "workflow_compatibility_invalid")
    assert _creation_snapshot() == before
