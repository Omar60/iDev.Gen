"""API and persistence tests for atomic, idempotent guided session creation."""
from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import threading
import uuid

import pytest

import db
import main
import resource_store
from backend import guided_sessions, session_plan, workflow_binding
from test_automation_profile_readiness_independent import (
    _profile as _automation_profile,
    _publish as _publish_automation_profile,
    _saved_look as _automation_saved_look,
)


def _room_anchor(*, kind: str = "rooms", ready: bool = True) -> dict[str, str]:
    library_key = f"guided-test-{uuid.uuid4().hex}"
    source_id = "scene-001"
    label = "invented studio"
    theme = "Soft light enters an empty studio from a high window."
    library_id = resource_store.ensure_library(library_key, kind=kind)
    revision_id = resource_store.record_revision(
        library_id,
        source_id,
        {"id": source_id, "label": label, "scene_theme": theme},
        translation={"label": label, "scene_theme": theme} if ready else {},
    )
    revision = resource_store.get_revision(revision_id=revision_id)
    assert revision is not None
    return {
        "library_key": library_key,
        "source_id": source_id,
        "content_digest": revision["content_digest"],
    }


def _body(seeded, anchor, **overrides):
    payload = {
        "request_id": str(uuid.uuid4()),
        "character_id": seeded["model_id"],
        "scene_anchor": anchor,
        "photo_count": 2,
    }
    payload.update(overrides)
    return payload


def _saved_look(
    client, *, name: str = "guided-saved-look", aside: str = "",
) -> dict:
    response = client.post("/api/looks", json={
        "name": name,
        "appearance": "A soft, natural studio appearance.",
        "garments": [{
            "wording": "A charcoal jacket.",
            "aside": aside,
        }],
    })
    assert response.status_code == 200, response.text
    return response.json()


def _counts() -> tuple[int, int, int]:
    return tuple(
        int(db.one(f"SELECT COUNT(*) AS n FROM {table}")["n"])
        for table in ("session", "session_plan", "guided_session_request")
    )


_FULL_AUTO_CREATION_TABLES = (
    "session",
    "session_plan",
    "session_plan_approval",
    "prepared_take",
    "session_profile_evidence",
    "session_profile_coverage_revision",
    "session_take_coverage_evidence",
    "full_auto_run_receipt",
    "full_auto_run",
    "full_auto_run_action_receipt",
    "guided_session_request",
    "authoring_operation",
    "shot",
)


def _creation_snapshot() -> dict[str, list[dict]]:
    return {
        table: db.q(f"SELECT * FROM {table} ORDER BY rowid")
        for table in _FULL_AUTO_CREATION_TABLES
    }


def _forbid_full_auto_side_effects(monkeypatch) -> None:
    def unexpected(*_args, **_kwargs):
        pytest.fail("guided Full Auto creation must not call an assistant or start generation")

    monkeypatch.setattr(main.enhance, "_request_completion", unexpected)
    monkeypatch.setattr(main.enhance, "run_structured", unexpected)
    monkeypatch.setattr(main.runner, "start", unexpected)
    monkeypatch.setattr(main.comfy, "queue_prompt", unexpected)


def _full_auto_payload(client, seeded, anchor) -> tuple[dict, dict, dict]:
    workflow = db.one("SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"])
    created_workflow = client.post("/api/workflows", json={
        "name": f"guided-full-auto-{uuid.uuid4().hex}",
        "graph": json.loads(workflow["graph"]),
        "kind": "t2i",
    })
    assert created_workflow.status_code == 200, created_workflow.text
    db.run(
        "UPDATE model SET workflow_id = ? WHERE id = ?",
        created_workflow.json()["id"], seeded["model_id"],
    )

    saved = _automation_saved_look()
    published = _publish_automation_profile(saved, _automation_profile(saved))
    base = _body(
        seeded,
        anchor,
        photo_count=4,
        saved_look={
            "look_key": saved["key"],
            "version": saved["version"],
            "content_digest": saved["content_digest"],
        },
        full_auto={
            "authorization_version": 1,
            "generate_without_pauses": True,
            "profile_revision": published["profile_revision"],
            "profile_digest": published["profile_digest"],
            "progression": "all_stages_full_interval",
            "framing_intent": "vary",
        },
    )
    base.pop("request_id")
    consent = client.post("/api/sessions/guided/full-auto/preflight", json=base)
    assert consent.status_code == 200, consent.text
    payload = {
        **base,
        "request_id": str(uuid.uuid4()),
        "full_auto": {
            **base["full_auto"],
            "scope_digest": consent.json()["scope_digest"],
            "scope_issued_at": consent.json()["scope_issued_at"],
        },
    }
    return payload, saved, published


def _assert_complete_full_auto_creation(
    before: dict[str, list[dict]], payload: dict, saved: dict,
    published: dict, response_bytes: bytes,
) -> None:
    after = _creation_snapshot()
    expected_additions = {
        "session": 1,
        "session_plan": 1,
        "session_plan_approval": 0,
        "prepared_take": 0,
        "session_profile_evidence": 1,
        "session_profile_coverage_revision": 1,
        "session_take_coverage_evidence": 4,
        "full_auto_run_receipt": 1,
        "full_auto_run": 1,
        "full_auto_run_action_receipt": 0,
        "guided_session_request": 1,
        "authoring_operation": 0,
        "shot": 0,
    }
    for table, added in expected_additions.items():
        assert after[table][:len(before[table])] == before[table], table
        assert len(after[table]) == len(before[table]) + added, table

    result = json.loads(response_bytes)
    assert set(result) == {"session_id", "plan_revision", "plan", "full_auto_run_id"}
    assert result["plan_revision"] == 1
    assert result["full_auto_run_id"]
    sid = result["session_id"]
    plan_row = db.one("SELECT * FROM session_plan WHERE session_id = ?", sid)
    plan = json.loads(plan_row["plan_json"])
    assert plan == result["plan"]
    assert plan["look"] == saved["appearance"]
    assert plan["authoring"]["look_snapshot"] == {
        "look_id": saved["key"],
        "version": saved["version"],
        "content_digest": saved["content_digest"],
        "appearance": saved["appearance"],
        "outfit": saved["outfit"],
    }
    progression = plan["authoring"]["wardrobe_progression"]
    assert progression is not None
    stages = session_plan._saved_look_wardrobe_stages(saved["outfit"])
    assert progression["stage_indices"] == list(range(len(stages))) == [0, 1, 2, 3]
    take_ids = {take["take_id"] for take in plan["takes"]}
    assert take_ids == {f"take-{index:03d}" for index in range(1, 5)}
    assert all(take.get("wardrobe_coverage") for take in plan["takes"])

    profile_evidence = db.one(
        "SELECT * FROM session_profile_evidence WHERE session_id = ? AND plan_revision = 1",
        sid,
    )
    assert profile_evidence["look_key"] == saved["key"]
    assert profile_evidence["look_version"] == saved["version"]
    assert profile_evidence["look_content_digest"] == saved["content_digest"]
    assert profile_evidence["profile_revision"] == published["profile_revision"]
    assert profile_evidence["profile_digest"] == published["profile_digest"]
    coverage_revision = db.one(
        "SELECT * FROM session_profile_coverage_revision WHERE session_id = ? AND plan_revision = 1",
        sid,
    )
    assert set(json.loads(coverage_revision["derived_take_ids_json"])) == take_ids
    coverage_rows = db.q(
        "SELECT take_id, stage_index FROM session_take_coverage_evidence "
        "WHERE session_id = ? AND plan_revision = 1 ORDER BY take_id",
        sid,
    )
    assert {row["take_id"] for row in coverage_rows} == take_ids
    assert {row["stage_index"] for row in coverage_rows} == set(range(4))

    receipt = db.one(
        "SELECT * FROM full_auto_run_receipt WHERE run_id = ?",
        result["full_auto_run_id"],
    )
    assert receipt["session_id"] == sid
    assert receipt["scope_digest"] == payload["full_auto"]["scope_digest"]
    assert receipt["plan_digest"] == resource_store.canonical_digest(plan)
    run = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", result["full_auto_run_id"])
    assert run["session_id"] == sid
    assert run["state"] == "active"
    assert run["phase"] == "prepare"
    assert run["revision"] == 1
    assert run["epoch"] == 1
    assert run["plan_revision"] == 1
    assert run["fencing_token"] == 1
    assert run["lease_expires_at"] is None
    request = db.one(
        "SELECT * FROM guided_session_request WHERE request_id = ?",
        payload["request_id"],
    )
    assert request["session_id"] == sid
    assert request["request_digest"] == guided_sessions.normalize_request(payload).body_digest
    assert request["response_json"].encode("utf-8") == response_bytes


def _error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert set(detail) == {"code", "message"}
    assert detail["code"] == code
    assert isinstance(detail["message"], str) and detail["message"]
    return detail


def test_guided_creation_returns_closed_plan_and_persists_one_of_each(client, seeded, monkeypatch):
    anchor = _room_anchor()
    payload = _body(seeded, anchor)
    before = _counts()
    calls = []

    async def unexpected_assistant_call(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("guided creation must not call an assistant")

    monkeypatch.setattr(main.enhance, "_request_completion", unexpected_assistant_call)
    response = client.post("/api/sessions/guided", json=payload)

    assert response.status_code == 201, response.text
    result = response.json()
    assert set(result) == {"session_id", "plan_revision", "plan"}
    assert result["plan_revision"] == 1
    plan = result["plan"]
    assert set(plan) == {
        "version", "look", "initial_wardrobe", "takes", "selected_resources",
        "wardrobe_changes", "authoring", "conflicts",
    }
    assert plan["version"] == "resource-v1"
    assert plan["look"] == "" and plan["initial_wardrobe"] == ""
    assert plan["conflicts"] == [] and plan["wardrobe_changes"] == []
    assert plan["selected_resources"] == [anchor]
    assert [take["take_id"] for take in plan["takes"]] == ["take-001", "take-002"]
    authoring = plan["authoring"]
    assert set(authoring) == {
        "schema_version", "mode", "brief", "scene_anchor", "workflow_binding",
        "variation_policy", "shared_state", "evidence", "look_snapshot",
        "wardrobe_progression",
    }
    assert authoring["schema_version"] == 1
    assert authoring["mode"] == "automatic"
    assert authoring["brief"] == ""
    assert authoring["scene_anchor"] == anchor
    assert authoring["workflow_binding"]["workflow_id"] == seeded["workflow_id"]
    assert authoring["workflow_binding"] == workflow_binding.build_workflow_binding(
        seeded["workflow_id"],
    )
    assert authoring["variation_policy"] == {
        dimension: {"mode": "vary"}
        for dimension in ("camera", "framing", "pose", "expression")
    }
    assert authoring["shared_state"] == {
        "look": {"origin": "none", "evidence_id": None},
        "initial_wardrobe": {"origin": "none", "evidence_id": None},
    }
    assert authoring["evidence"] == []
    assert authoring["look_snapshot"] is None
    assert authoring["wardrobe_progression"] is None
    assert calls == []

    session = db.one("SELECT * FROM session WHERE id = ?", result["session_id"])
    assert session is not None
    assert session["name"] == "Session 1"
    assert session["look"] == session["wardrobe"] == ""
    assert session["workflow_id"] == seeded["workflow_id"]
    assert json.loads(session["settings"])["composition_mode"] == "resource-v1"
    assert json.loads(session["settings"])["width"] == 832
    assert json.loads(session["settings"])["height"] == 1216
    stored_plan = db.one("SELECT * FROM session_plan WHERE session_id = ?", result["session_id"])
    assert stored_plan is not None and stored_plan["plan_revision"] == 1
    assert json.loads(stored_plan["plan_json"]) == plan
    stored_request = db.one(
        "SELECT * FROM guided_session_request WHERE session_id = ?", result["session_id"],
    )
    assert stored_request is not None
    assert stored_request["request_id"] == payload["request_id"]
    assert stored_request["response_json"].encode("utf-8") == response.content
    assert _counts() == tuple(value + 1 for value in before)

    second_response = client.post(
        "/api/sessions/guided", json=_body(seeded, anchor),
    )
    assert second_response.status_code == 201, second_response.text
    second_session = db.one(
        "SELECT name FROM session WHERE id = ?", second_response.json()["session_id"],
    )
    assert second_session == {"name": "Session 2"}
    assert _counts() == tuple(value + 2 for value in before)


@pytest.mark.parametrize("field", ["saved_look", "full_auto"])
def test_guided_creation_rejects_explicit_null_optional_contract_objects(
    client, seeded, field,
):
    payload = _body(seeded, _room_anchor())
    payload[field] = None
    before = _counts()

    response = client.post("/api/sessions/guided", json=payload)

    _error(response, 422, "invalid_request")
    assert _counts() == before


def test_profile_readiness_error_identifies_safe_aside_region_and_stage(client):
    saved = _saved_look(client, aside="The charcoal jacket rests on a chair.")
    garment_key = saved["outfit"]["garments"][0]["key"]

    current = guided_sessions._profile_error_current(
        [{
            "code": "coverage_unknown",
            "garment_key": garment_key,
            "state": "aside",
            "region": "feet",
        }],
        saved,
    )

    assert current == {
        "remedy": "update_automation_profile_in_looks",
        "manual_available": True,
        "affected": [{
            "code": "coverage_unknown",
            "field": "garment_coverage",
            "region": "feet",
            "state": "aside",
            "garment_key": garment_key,
            "stage_indices": [1],
        }],
    }


def test_guided_creation_persists_canonical_room_conflicts(client, seeded):
    anchor = _room_anchor()
    payload = _body(
        seeded,
        anchor,
        look="Soft editorial portrait lighting.",
        initial_wardrobe="A navy wool jacket and a cream shirt.",
    )

    response = client.post("/api/sessions/guided", json=payload)

    assert response.status_code == 201, response.text
    result = response.json()
    plan = result["plan"]
    expected_conflicts = session_plan.detect_resource_constant_conflicts(plan)
    assert expected_conflicts
    assert plan["conflicts"] == expected_conflicts
    assert {
        (conflict["resource_field"], conflict["resource_value"])
        for conflict in expected_conflicts
    } == {
        ("label", "invented studio"),
        ("scene_theme", "Soft light enters an empty studio from a high window."),
    }
    assert all(
        conflict["plan_look"] == payload["look"]
        and conflict["plan_initial_wardrobe"] == payload["initial_wardrobe"]
        for conflict in expected_conflicts
    )

    stored_plan = db.one(
        "SELECT plan_json FROM session_plan WHERE session_id = ?",
        result["session_id"],
    )
    assert stored_plan is not None
    persisted = json.loads(stored_plan["plan_json"])
    assert persisted["conflicts"] == expected_conflicts
    assert persisted == plan

    retry = client.post("/api/sessions/guided", json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.content == response.content
    assert _counts() == (1, 1, 1)


def test_guided_retry_replays_exact_stored_response_after_response_loss(client, seeded):
    payload = _body(seeded, _room_anchor())
    first = client.post("/api/sessions/guided", json=payload)
    assert first.status_code == 201, first.text
    lost_body = first.content

    retry = client.post("/api/sessions/guided", json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.content == lost_body
    assert _counts() == (1, 1, 1)


def test_guided_canvas_override_is_session_scoped_and_bound_to_idempotency(client, seeded):
    payload = _body(seeded, _room_anchor(), width=768, height=1360)
    model_before = dict(db.one("SELECT settings FROM model WHERE id = ?", seeded["model_id"]))

    created = client.post("/api/sessions/guided", json=payload)

    assert created.status_code == 201, created.text
    session_id = created.json()["session_id"]
    session_settings = json.loads(db.one(
        "SELECT settings FROM session WHERE id = ?", session_id,
    )["settings"])
    assert (session_settings["width"], session_settings["height"]) == (768, 1360)
    assert dict(db.one("SELECT settings FROM model WHERE id = ?", seeded["model_id"])) == model_before

    replay = client.post("/api/sessions/guided", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.content == created.content
    before = _counts()

    changed = {**payload, "width": 832, "height": 1216}
    _error(client.post("/api/sessions/guided", json=changed), 409, "idempotency_conflict")
    assert _counts() == before


def test_guided_canvas_accepts_custom_dimensions_on_existing_eight_pixel_step(client, seeded):
    response = client.post(
        "/api/sessions/guided",
        json=_body(seeded, _room_anchor(), width=776, height=1360),
    )
    assert response.status_code == 201, response.text
    settings = json.loads(db.one(
        "SELECT settings FROM session WHERE id = ?", response.json()["session_id"],
    )["settings"])
    assert (settings["width"], settings["height"]) == (776, 1360)


@pytest.mark.parametrize("canvas", [
    {"width": 768},
    {"height": 1360},
    {"width": None, "height": None},
    {"width": "768", "height": 1360},
    {"width": 768.0, "height": 1360},
    {"width": True, "height": 1360},
    {"width": 2**63, "height": 1360},
    {"width": 768, "height": 1356},
    {"width": 0, "height": 1360},
    {"width": 7, "height": 1360},
])
def test_guided_rejects_invalid_or_incomplete_canvas_without_writes(client, seeded, canvas):
    before = _counts()
    response = client.post(
        "/api/sessions/guided",
        json=_body(seeded, _room_anchor(), **canvas),
    )
    _error(response, 422, "invalid_request")
    assert _counts() == before


def test_guided_request_normalization_replays_defaults_key_order_and_uuid_form(client, seeded):
    request_id = str(uuid.uuid4())
    anchor = _room_anchor()
    first_body = _body(seeded, anchor, request_id=request_id)
    normalized = guided_sessions.normalize_request(first_body)
    legacy_body = {
        "character_id": seeded["model_id"],
        "workflow_id": None,
        "scene_anchor": anchor,
        "photo_count": 2,
        "brief": "",
        "mode": "automatic",
        "variation_policy": {
            "camera": {"mode": "vary"},
            "framing": {"mode": "vary"},
            "pose": {"mode": "vary"},
            "expression": {"mode": "vary"},
        },
        "look": "",
        "initial_wardrobe": "",
    }
    assert normalized.body == legacy_body
    assert normalized.body_digest == resource_store.canonical_digest(legacy_body)

    first = client.post("/api/sessions/guided", json=first_body)
    assert first.status_code == 201, first.text

    expanded = {
        **first_body,
        "workflow_id": None,
        "brief": "",
        "mode": "automatic",
        "variation_policy": {
            "expression": {"mode": "vary"},
            "pose": {"mode": "vary"},
            "framing": {"mode": "vary"},
            "camera": {"mode": "vary"},
        },
        "look": "",
        "initial_wardrobe": "",
    }
    expanded["request_id"] = f" {{{request_id.upper()}}} "
    reordered = dict(reversed(list(expanded.items())))
    replay = client.post("/api/sessions/guided", json=reordered)
    assert replay.status_code == 200, replay.text
    assert replay.content == first.content
    assert _counts() == (1, 1, 1)


def test_guided_manual_saved_look_uses_exact_version_without_profile_or_run(client, seeded):
    saved = _saved_look(client)
    newer = client.post(
        f"/api/looks/{saved['key']}/versions",
        json={
            "expected_version": saved["version"],
            "name": "later look version",
            "appearance": "A newer appearance that was not selected.",
        },
    )
    assert newer.status_code == 200, newer.text
    assert newer.json()["version"] == saved["version"] + 1
    payload = _body(
        seeded,
        _room_anchor(),
        mode="manual",
        saved_look={
            "look_key": saved["key"],
            "version": saved["version"],
            "content_digest": saved["content_digest"],
        },
    )

    response = client.post("/api/sessions/guided", json=payload)

    assert response.status_code == 201, response.text
    result = response.json()
    assert "full_auto_run_id" not in result
    plan = result["plan"]
    assert plan["look"] == saved["appearance"]
    assert plan["initial_wardrobe"] == session_plan.compose_saved_look_wardrobe(saved["outfit"])
    assert plan["authoring"]["look_snapshot"] == {
        "look_id": saved["key"],
        "version": saved["version"],
        "content_digest": saved["content_digest"],
        "appearance": saved["appearance"],
        "outfit": saved["outfit"],
    }
    assert plan["authoring"]["wardrobe_progression"] is None
    assert plan["wardrobe_changes"] == []
    assert db.one(
        "SELECT 1 FROM session_profile_evidence WHERE session_id = ?",
        result["session_id"],
    ) is None
    assert db.one(
        "SELECT 1 FROM full_auto_run_receipt WHERE session_id = ?",
        result["session_id"],
    ) is None


def test_preflight_and_creation_have_separate_closed_full_auto_shapes(seeded):
    common = {
        "character_id": seeded["model_id"],
        "scene_anchor": _room_anchor(),
        "photo_count": 2,
        "saved_look": {
            "look_key": "invented-look",
            "version": 1,
            "content_digest": "a" * 64,
        },
        "full_auto": {
            "authorization_version": 1,
            "generate_without_pauses": True,
            "profile_revision": 1,
            "profile_digest": "b" * 64,
            "progression": "all_stages_full_interval",
            "framing_intent": "vary",
        },
    }

    preflight = guided_sessions.normalize_preflight_request(common)
    assert "request_id" not in preflight
    assert "scope_digest" not in preflight["full_auto"]
    assert "scope_issued_at" not in preflight["full_auto"]

    with pytest.raises(guided_sessions.GuidedSessionError):
        guided_sessions.normalize_preflight_request({**common, "request_id": str(uuid.uuid4())})
    with pytest.raises(guided_sessions.GuidedSessionError):
        guided_sessions.normalize_preflight_request({
            **common,
            "full_auto": {**common["full_auto"], "scope_digest": "c" * 64},
        })
    with pytest.raises(guided_sessions.GuidedSessionError):
        guided_sessions.normalize_request({
            **common,
            "request_id": str(uuid.uuid4()),
        })

    creation = guided_sessions.normalize_request({
        **common,
        "request_id": str(uuid.uuid4()),
        "full_auto": {
            **common["full_auto"],
            "scope_digest": "c" * 64,
            "scope_issued_at": "2026-10-09T00:00:00+00:00",
        },
    })
    assert creation.body["full_auto"]["scope_digest"] == "c" * 64


def test_full_auto_canvas_refuses_connected_and_misbound_dimension_inputs():
    settings = {"width": 832, "height": 1216}
    graph = {
        "5": {"inputs": {"width": ["4", 0], "height": 512}},
    }
    node_map = {
        "width": "5.inputs.width",
        "height": "5.inputs.height",
    }
    original = json.loads(json.dumps(graph))

    with pytest.raises(guided_sessions.GuidedSessionError) as connected:
        guided_sessions._validate_full_auto_canvas(
            {"width": 832, "height": 1216}, settings, graph, node_map,
        )
    assert connected.value.code == "workflow_compatibility_invalid"
    assert graph == original

    graph["5"]["inputs"]["width"] = 512
    with pytest.raises(guided_sessions.GuidedSessionError) as misbound:
        guided_sessions._validate_full_auto_canvas(
            {"width": 832, "height": 1216}, settings, graph,
            {"width": "5.inputs.height", "height": "5.inputs.height"},
        )
    assert misbound.value.code == "workflow_compatibility_invalid"


def _preflight_body(payload: dict) -> dict:
    body = json.loads(json.dumps(payload))
    body.pop("request_id")
    body["full_auto"].pop("scope_digest")
    body["full_auto"].pop("scope_issued_at")
    return body


def test_full_auto_refuses_t2i_generation_reference_and_preserves_manual_path(
    client, seeded, monkeypatch,
):
    payload, _saved, _published = _full_auto_payload(
        client, seeded, _room_anchor(),
    )
    workflow_id = db.one(
        "SELECT workflow_id FROM model WHERE id = ?", seeded["model_id"],
    )["workflow_id"]
    graph = json.loads(db.one(
        "SELECT graph FROM workflow WHERE id = ?", workflow_id,
    )["graph"])
    graph["9001"] = {
        "class_type": "LoadImage",
        "inputs": {"image": "reference.png"},
    }
    graph["9002"] = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["9001", 0], "vae": ["1", 2]},
    }
    graph["6"]["inputs"]["latent_image"] = ["9002", 0]
    reference_workflow = client.post("/api/workflows", json={
        "name": "guided-full-auto-reference-workflow",
        "graph": graph,
        "kind": "t2i",
    })
    assert reference_workflow.status_code == 200, reference_workflow.text
    workflow_id = reference_workflow.json()["id"]
    node_map = json.loads(db.one(
        "SELECT node_map FROM workflow WHERE id = ?", workflow_id,
    )["node_map"])
    node_map.pop("reference", None)
    db.run(
        "UPDATE model SET workflow_id = ? WHERE id = ?",
        workflow_id, seeded["model_id"],
    )
    db.run(
        "UPDATE workflow SET node_map = ? WHERE id = ?",
        json.dumps(node_map), workflow_id,
    )

    before = _creation_snapshot()
    _forbid_full_auto_side_effects(monkeypatch)
    response = client.post(
        "/api/sessions/guided/full-auto/preflight",
        json=_preflight_body(payload),
    )
    detail = _error(response, 422, "full_auto_workflow_unsupported")
    assert "generation reference" in detail["message"]
    assert _creation_snapshot() == before

    manual = _preflight_body(payload)
    manual.pop("full_auto")
    manual["request_id"] = str(uuid.uuid4())
    manual["mode"] = "manual"
    created = client.post("/api/sessions/guided", json=manual)
    assert created.status_code == 201, created.text
    assert db.one(
        "SELECT workflow_id FROM session WHERE id = ?",
        created.json()["session_id"],
    )["workflow_id"] == workflow_id


@pytest.mark.parametrize(
    ("slot", "settings_field", "selected_value"),
    [
        ("checkpoint", "checkpoint", "base.safetensors"),
        ("lora_name", None, None),
        ("sampler", "sampler", "euler"),
        ("scheduler", "scheduler", "normal"),
    ],
)
def test_full_auto_refuses_connected_selected_settings(
    client, seeded, monkeypatch, slot, settings_field, selected_value,
):
    payload, _saved, _published = _full_auto_payload(
        client, seeded, _room_anchor(),
    )
    workflow_id = db.one(
        "SELECT workflow_id FROM model WHERE id = ?", seeded["model_id"],
    )["workflow_id"]
    workflow = db.one(
        "SELECT graph, node_map FROM workflow WHERE id = ?", workflow_id,
    )
    graph = json.loads(workflow["graph"])
    node_map = json.loads(workflow["node_map"])
    node_id, _inputs, field = node_map[slot].split(".")
    graph[node_id]["inputs"][field] = ["1", 0]
    db.run(
        "UPDATE workflow SET graph = ? WHERE id = ?",
        json.dumps(graph), workflow_id,
    )
    if settings_field is not None:
        model = db.one("SELECT settings FROM model WHERE id = ?", seeded["model_id"])
        settings = json.loads(model["settings"] or "{}")
        settings[settings_field] = selected_value
        db.run(
            "UPDATE model SET settings = ? WHERE id = ?",
            json.dumps(settings), seeded["model_id"],
        )

    before = _creation_snapshot()
    _forbid_full_auto_side_effects(monkeypatch)
    response = client.post(
        "/api/sessions/guided/full-auto/preflight",
        json=_preflight_body(payload),
    )
    detail = _error(response, 422, "workflow_compatibility_invalid")
    assert "connects" in detail["message"]
    assert _creation_snapshot() == before


def test_full_auto_refuses_selected_setting_mapped_to_incompatible_widget_type(
    client, seeded, monkeypatch,
):
    model = db.one(
        "SELECT settings FROM model WHERE id = ?", seeded["model_id"],
    )
    settings = json.loads(model["settings"] or "{}")
    settings["checkpoint"] = "models/selected.safetensors"
    db.run(
        "UPDATE model SET settings = ? WHERE id = ?",
        json.dumps(settings), seeded["model_id"],
    )
    payload, _saved, _published = _full_auto_payload(
        client, seeded, _room_anchor(),
    )
    workflow_id = db.one(
        "SELECT workflow_id FROM model WHERE id = ?", seeded["model_id"],
    )["workflow_id"]
    row = db.one(
        "SELECT node_map FROM workflow WHERE id = ?", workflow_id,
    )
    node_map = json.loads(row["node_map"])
    assert "checkpoint" in node_map and "seed" in node_map
    node_map["checkpoint"] = node_map["seed"]
    db.run(
        "UPDATE workflow SET node_map = ? WHERE id = ?",
        json.dumps(node_map), workflow_id,
    )

    before = _creation_snapshot()
    _forbid_full_auto_side_effects(monkeypatch)
    response = client.post(
        "/api/sessions/guided/full-auto/preflight",
        json=_preflight_body(payload),
    )
    detail = _error(response, 422, "workflow_compatibility_invalid")
    assert "incompatible widget" in detail["message"]
    assert _creation_snapshot() == before


def test_full_auto_accepts_selected_setting_mapped_to_custom_string_widget(
    client, seeded, monkeypatch,
):
    model = db.one(
        "SELECT settings FROM model WHERE id = ?", seeded["model_id"],
    )
    settings = json.loads(model["settings"] or "{}")
    settings["checkpoint"] = "models/selected.safetensors"
    db.run(
        "UPDATE model SET settings = ? WHERE id = ?",
        json.dumps(settings), seeded["model_id"],
    )
    payload, _saved, _published = _full_auto_payload(
        client, seeded, _room_anchor(),
    )
    workflow_id = db.one(
        "SELECT workflow_id FROM model WHERE id = ?", seeded["model_id"],
    )["workflow_id"]
    workflow = db.one(
        "SELECT graph, node_map FROM workflow WHERE id = ?", workflow_id,
    )
    graph = json.loads(workflow["graph"])
    graph["9001"] = {
        "class_type": "CustomCheckpointNode",
        "inputs": {"checkpoint_name": "models/workflow.safetensors"},
    }
    node_map = json.loads(workflow["node_map"])
    node_map["checkpoint"] = "9001.inputs.checkpoint_name"
    db.run(
        "UPDATE workflow SET graph = ?, node_map = ? WHERE id = ?",
        json.dumps(graph), json.dumps(node_map), workflow_id,
    )

    before = _creation_snapshot()
    _forbid_full_auto_side_effects(monkeypatch)
    response = client.post(
        "/api/sessions/guided/full-auto/preflight",
        json=_preflight_body(payload),
    )
    assert response.status_code == 200, response.text
    assert response.json()["summary"]["workflow"]["workflow_id"] == workflow_id
    assert _creation_snapshot() == before


def test_guided_manual_mode_fixed_policy_and_user_overrides_persist_without_assistant(client, seeded):
    anchor = _room_anchor()
    policy = {
        "camera": {"mode": "fixed", "value": "35mm portrait lens", "value_origin": "user"},
        "framing": {"mode": "vary"},
        "pose": {"mode": "vary"},
        "expression": {"mode": "vary"},
    }
    response = client.post("/api/sessions/guided", json=_body(
        seeded,
        anchor,
        mode="manual",
        variation_policy=policy,
        look="A soft, natural look.",
        initial_wardrobe="A charcoal jacket.",
    ))
    assert response.status_code == 201, response.text
    plan = response.json()["plan"]
    assert plan["authoring"]["mode"] == "manual"
    assert plan["authoring"]["variation_policy"] == policy
    assert plan["look"] == "A soft, natural look."
    assert plan["initial_wardrobe"] == "A charcoal jacket."
    assert plan["authoring"]["shared_state"] == {
        "look": {"origin": "user", "evidence_id": None},
        "initial_wardrobe": {"origin": "user", "evidence_id": None},
    }


def test_authoring_mode_switches_both_ways_without_configured_assistant(
    client, seeded, monkeypatch,
):
    no_assistant = {**main.CONFIG, "llm_url": "", "llm_model": ""}
    monkeypatch.setattr(main, "CONFIG", no_assistant)
    assert not main.enhance.configured(main.CONFIG)
    assistant_calls = []

    async def unexpected_assistant_call(*args, **kwargs):
        assistant_calls.append((args, kwargs))
        raise AssertionError("mode changes must not invoke the assistant")

    monkeypatch.setattr(main.enhance, "_request_completion", unexpected_assistant_call)

    created = client.post(
        "/api/sessions/guided", json=_body(seeded, _room_anchor()),
    )
    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    plan = created.json()["plan"]
    assert plan["authoring"]["mode"] == "automatic"

    for expected_revision, mode in ((1, "manual"), (2, "automatic")):
        candidate = json.loads(json.dumps(plan))
        candidate["authoring"]["mode"] = mode
        saved = client.post(
            f"/api/sessions/{sid}/plan",
            json={"plan": candidate, "expected_revision": expected_revision},
        )
        assert saved.status_code == 200, saved.text
        assert set(saved.json()) == {"plan_revision", "conflicts"}
        assert saved.json() == {
            "plan_revision": expected_revision + 1,
            "conflicts": [],
        }

        fetched = client.get(f"/api/sessions/{sid}/plan")
        assert fetched.status_code == 200, fetched.text
        body = fetched.json()
        assert body["plan_revision"] == expected_revision + 1
        assert body["plan"]["authoring"]["mode"] == mode
        assert set(body["plan"]["authoring"]) == session_plan.REQUIRED_AUTHORING_KEYS
        plan = body["plan"]

    assert assistant_calls == []


@pytest.mark.parametrize(("dimension", "fixed_value", "conflicting_value"), [
    ("camera", "35mm portrait lens", "85mm telephoto lens"),
    ("framing", "waist-up framing", "full-body framing"),
    ("pose", "seated pose", "standing pose"),
    ("expression", "calm expression", "wide smile"),
])
def test_plan_api_rejects_take_choice_conflicting_with_fixed_policy(
    client, seeded, dimension, fixed_value, conflicting_value,
):
    anchor = _room_anchor()
    policy = {
        key: (
            {"mode": "fixed", "value": fixed_value, "value_origin": "user"}
            if key == dimension else {"mode": "vary"}
        )
        for key in ("camera", "framing", "pose", "expression")
    }
    created = client.post(
        "/api/sessions/guided",
        json=_body(seeded, anchor, variation_policy=policy),
    )
    assert created.status_code == 201, created.text
    result = created.json()
    sid = result["session_id"]
    plan = result["plan"]

    matching = json.loads(json.dumps(plan))
    matching["takes"][0][dimension] = fixed_value
    accepted = client.post(
        f"/api/sessions/{sid}/plan",
        json={"plan": matching, "expected_revision": 1},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json() == {"plan_revision": 2, "conflicts": []}

    before_plan = dict(db.one(
        "SELECT * FROM session_plan WHERE session_id = ?", sid,
    ))
    conflicting = client.get(f"/api/sessions/{sid}/plan").json()["plan"]
    conflicting["takes"][0][dimension] = conflicting_value
    rejected = client.post(
        f"/api/sessions/{sid}/plan",
        json={"plan": conflicting, "expected_revision": 2},
    )

    assert rejected.status_code == 422, rejected.text
    body = rejected.json()
    assert set(body) == {"detail"}
    detail = body["detail"]
    assert isinstance(detail, str)
    assert f"authoring.variation_policy.{dimension}" in detail
    assert "take-001" in detail
    assert dict(db.one(
        "SELECT * FROM session_plan WHERE session_id = ?", sid,
    )) == before_plan
    current = client.get(f"/api/sessions/{sid}/plan").json()
    assert current["plan_revision"] == 2
    assert current["plan"]["takes"][0][dimension] == fixed_value
    assert db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid) == []


def test_guided_reused_request_id_with_changed_body_writes_nothing(client, seeded):
    payload = _body(seeded, _room_anchor())
    created = client.post("/api/sessions/guided", json=payload)
    assert created.status_code == 201, created.text
    before = _counts()

    changed = {**payload, "brief": "A different brief."}
    conflict = client.post("/api/sessions/guided", json=changed)
    _error(conflict, 409, "idempotency_conflict")
    assert _counts() == before


def test_guided_concurrent_same_request_id_creates_once(client, seeded):
    payload = _body(seeded, _room_anchor())
    start = threading.Barrier(2)

    def post_once():
        start.wait(timeout=10)
        return client.post("/api/sessions/guided", json=payload)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = [future.result(timeout=20) for future in [
            pool.submit(post_once), pool.submit(post_once),
        ]]

    assert sorted(response.status_code for response in responses) == [200, 201]
    assert responses[0].content == responses[1].content
    assert _counts() == (1, 1, 1)


def test_guided_transaction_failure_rolls_back_claim_session_and_plan(client, seeded, monkeypatch):
    payload = _body(seeded, _room_anchor())
    before = _counts()
    original_run = db.run

    def fail_after_request_insert(sql, *args):
        result = original_run(sql, *args)
        if "INSERT INTO guided_session_request" in sql:
            raise sqlite3.OperationalError("injected transaction failure")
        return result

    monkeypatch.setattr(db, "run", fail_after_request_insert)
    with pytest.raises(sqlite3.OperationalError, match="injected transaction failure"):
        client.post("/api/sessions/guided", json=payload)
    assert _counts() == before
    assert db.one(
        "SELECT 1 FROM guided_session_request WHERE request_id = ?",
        payload["request_id"],
    ) is None


@pytest.mark.parametrize(("table", "occurrence"), [
    ("session", 1),
    ("session_plan", 1),
    ("session_profile_evidence", 1),
    ("session_profile_coverage_revision", 1),
    *(('session_take_coverage_evidence', index) for index in range(1, 5)),
    ("full_auto_run_receipt", 1),
    ("full_auto_run", 1),
    ("guided_session_request", 1),
], ids=[
    "session", "plan", "profile-evidence", "coverage-revision",
    "coverage-take-1", "coverage-take-2", "coverage-take-3", "coverage-take-4",
    "authorization-receipt", "run-state", "guided-response",
])
def test_full_auto_failure_after_each_creation_write_rolls_back_all_rows(
    client, seeded, monkeypatch, table, occurrence,
):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "guided-full-auto-test-provider")
    _forbid_full_auto_side_effects(monkeypatch)
    payload, _saved, _published = _full_auto_payload(
        client, seeded, _room_anchor(),
    )
    before = _creation_snapshot()
    original_run = db.run
    writes_seen = 0

    def fail_after_write(sql, *args):
        nonlocal writes_seen
        result = original_run(sql, *args)
        normalized_sql = " ".join(sql.split()).upper()
        if normalized_sql.startswith(f"INSERT INTO {table.upper()} "):
            writes_seen += 1
            if writes_seen == occurrence:
                raise sqlite3.OperationalError("injected Full Auto write failure")
        return result

    monkeypatch.setattr(db, "run", fail_after_write)
    with pytest.raises(sqlite3.OperationalError, match="injected Full Auto write failure"):
        client.post("/api/sessions/guided", json=payload)

    assert writes_seen == occurrence
    assert _creation_snapshot() == before


@pytest.mark.parametrize("entrypoint", ["http", "service"])
def test_full_auto_concurrent_same_request_id_replays_one_complete_session(
    client, seeded, monkeypatch, entrypoint,
):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "guided-full-auto-test-provider")
    _forbid_full_auto_side_effects(monkeypatch)
    payload, saved, published = _full_auto_payload(
        client, seeded, _room_anchor(),
    )
    request = guided_sessions.normalize_request(payload)
    candidate_calls = []
    original_saved_look = session_plan.build_saved_look_application_candidate
    original_progression = session_plan.build_full_auto_wardrobe_progression_candidate

    def record_saved_look(*args, **kwargs):
        candidate_calls.append("saved_look")
        return original_saved_look(*args, **kwargs)

    def record_progression(*args, **kwargs):
        candidate_calls.append("progression")
        return original_progression(*args, **kwargs)

    monkeypatch.setattr(
        session_plan, "build_saved_look_application_candidate", record_saved_look,
    )
    monkeypatch.setattr(
        session_plan, "build_full_auto_wardrobe_progression_candidate", record_progression,
    )
    before = _creation_snapshot()
    start = threading.Barrier(2)

    def create_once():
        start.wait(timeout=10)
        if entrypoint == "http":
            response = client.post("/api/sessions/guided", json=payload)
            return response.status_code, response.content
        response_json, was_new = guided_sessions.create_or_replay(
            request, config=main.CONFIG,
        )
        return (201 if was_new else 200), response_json.encode("utf-8")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = [future.result(timeout=20) for future in [
            pool.submit(create_once), pool.submit(create_once),
        ]]

    assert sorted(status for status, _content in responses) == [200, 201]
    assert responses[0][1] == responses[1][1]
    assert candidate_calls == ["saved_look", "progression"]

    # A retry after an unobserved successful response returns the same bytes.
    replay = client.post("/api/sessions/guided", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.content == responses[0][1]
    _assert_complete_full_auto_creation(
        before, payload, saved, published, replay.content,
    )


def test_full_auto_replays_exact_response_after_commit_response_loss(
    client, seeded, monkeypatch,
):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "guided-full-auto-test-provider")
    _forbid_full_auto_side_effects(monkeypatch)
    payload, saved, published = _full_auto_payload(
        client, seeded, _room_anchor(),
    )
    before = _creation_snapshot()
    original_create = guided_sessions.create_or_replay
    lost_response = []

    def commit_then_lose_response(*args, **kwargs):
        lost_response.append(original_create(*args, **kwargs))
        raise RuntimeError("simulated lost response after commit")

    monkeypatch.setattr(guided_sessions, "create_or_replay", commit_then_lose_response)
    with pytest.raises(RuntimeError, match="simulated lost response after commit"):
        client.post("/api/sessions/guided", json=payload)

    assert lost_response
    monkeypatch.setattr(guided_sessions, "create_or_replay", original_create)
    replay = client.post("/api/sessions/guided", json=payload)
    assert replay.status_code == 200, replay.text
    _assert_complete_full_auto_creation(
        before, payload, saved, published, replay.content,
    )


def test_guided_accepts_500_stable_takes_and_rejects_overflow_or_coercion_before_writes(
    client, seeded,
):
    anchor = _room_anchor()
    accepted = client.post(
        "/api/sessions/guided", json=_body(seeded, anchor, photo_count=500),
    )
    assert accepted.status_code == 201, accepted.text
    takes = accepted.json()["plan"]["takes"]
    assert len(takes) == 500
    assert [take["take_id"] for take in takes] == [f"take-{i:03d}" for i in range(1, 501)]
    assert len({take["take_id"] for take in takes}) == 500

    before = _counts()
    for invalid_count in (501, 0, "12", True, 12.0):
        response = client.post(
            "/api/sessions/guided",
            json=_body(seeded, anchor, photo_count=invalid_count),
        )
        _error(response, 422, "invalid_request")
        assert _counts() == before


def test_guided_rejects_overlong_brief_and_open_policy_shapes_before_writes(client, seeded):
    anchor = _room_anchor()
    before = _counts()
    overlong = client.post(
        "/api/sessions/guided",
        json=_body(seeded, anchor, brief="x" * 2001),
    )
    _error(overlong, 422, "invalid_request")

    open_policy = {
        "camera": {"mode": "vary", "value": "ignored"},
        "framing": {"mode": "vary"},
        "pose": {"mode": "vary"},
        "expression": {"mode": "vary"},
    }
    invalid_policy = client.post(
        "/api/sessions/guided",
        json=_body(seeded, anchor, variation_policy=open_policy),
    )
    _error(invalid_policy, 422, "invalid_request")
    assert _counts() == before


def test_guided_closed_body_and_request_errors_use_stable_envelope(client, seeded):
    payload = _body(seeded, _room_anchor())
    extra = client.post(
        "/api/sessions/guided", json={**payload, "unexpected": True},
    )
    _error(extra, 422, "extra_field_forbidden")

    missing = {key: value for key, value in payload.items() if key != "scene_anchor"}
    _error(client.post("/api/sessions/guided", json=missing), 422, "missing_field")

    malformed_anchor = {**payload, "scene_anchor": {**payload["scene_anchor"], "extra": "x"}}
    _error(
        client.post("/api/sessions/guided", json=malformed_anchor),
        422,
        "invalid_request",
    )

    malformed = client.post(
        "/api/sessions/guided", content="{", headers={"content-type": "application/json"},
    )
    _error(malformed, 422, "invalid_json")
    assert _counts() == (0, 0, 0)


def test_guided_requires_exact_ready_rooms_revision(client, seeded):
    for anchor in (
        _room_anchor(kind="fused_scenes"),
        _room_anchor(ready=False),
        {**_room_anchor(), "content_digest": "b" * 64},
    ):
        before = _counts()
        response = client.post("/api/sessions/guided", json=_body(seeded, anchor))
        _error(response, 422, "scene_anchor_not_ready")
        assert _counts() == before


def test_guided_workflow_default_override_and_actionable_errors(client, seeded):
    anchor = _room_anchor()
    default_body = _body(seeded, anchor)
    default_result = client.post("/api/sessions/guided", json=default_body)
    assert default_result.status_code == 201, default_result.text
    default_value = default_result.json()
    default_session = db.one(
        "SELECT workflow_id FROM session WHERE id = ?", default_value["session_id"],
    )
    assert default_session["workflow_id"] == seeded["workflow_id"]
    assert default_value["plan"]["authoring"]["workflow_binding"]["workflow_id"] == seeded["workflow_id"]

    workflow_row = db.one("SELECT graph, node_map FROM workflow WHERE id = ?", seeded["workflow_id"])
    override_workflow = client.post("/api/workflows", json={
        "name": "guided-override-workflow",
        "graph": json.loads(workflow_row["graph"]),
        "node_map": json.loads(workflow_row["node_map"]),
    })
    assert override_workflow.status_code == 200, override_workflow.text
    override_id = override_workflow.json()["id"]
    override_result = client.post(
        "/api/sessions/guided",
        json=_body(seeded, anchor, workflow_id=override_id),
    )
    assert override_result.status_code == 201, override_result.text
    override_value = override_result.json()
    override_session = db.one(
        "SELECT workflow_id FROM session WHERE id = ?", override_value["session_id"],
    )
    assert override_session["workflow_id"] == override_id
    assert override_value["plan"]["authoring"]["workflow_binding"]["workflow_id"] == override_id

    db.run("UPDATE model SET workflow_id = NULL WHERE id = ?", seeded["model_id"])
    no_workflow = client.post("/api/sessions/guided", json=_body(seeded, anchor))
    detail = _error(no_workflow, 422, "workflow_required")
    assert "assign a workflow" in detail["message"]
    assert "Advanced workflow override" in detail["message"]

    before_missing_override = _counts()
    missing_override = client.post(
        "/api/sessions/guided",
        json=_body(seeded, anchor, workflow_id=999999),
    )
    detail = _error(missing_override, 422, "workflow_compatibility_invalid")
    assert "select an existing Advanced workflow" in detail["message"]
    assert _counts() == before_missing_override

    db.run("UPDATE workflow SET node_map = '{}' WHERE id = ?", override_id)
    incompatible = client.post(
        "/api/sessions/guided",
        json=_body(seeded, anchor, workflow_id=override_id),
    )
    detail = _error(incompatible, 422, "workflow_compatibility_invalid")
    assert "does not map the LoRA slot" in detail["message"]


def test_guided_disabled_feature_flag_returns_503_without_writes(client, seeded, monkeypatch):
    monkeypatch.setattr(main, "is_resource_planning_enabled", lambda: False)
    before = _counts()
    response = client.post(
        "/api/sessions/guided", json=_body(seeded, _room_anchor()),
    )
    _error(response, 503, "resource_planning_disabled")
    assert _counts() == before
