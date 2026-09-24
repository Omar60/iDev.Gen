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
from backend import session_plan, workflow_binding


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


def _counts() -> tuple[int, int, int]:
    return tuple(
        int(db.one(f"SELECT COUNT(*) AS n FROM {table}")["n"])
        for table in ("session", "session_plan", "guided_session_request")
    )


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
    assert session["name"] == session["look"] == session["wardrobe"] == ""
    assert session["workflow_id"] == seeded["workflow_id"]
    assert json.loads(session["settings"])["composition_mode"] == "resource-v1"
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


def test_guided_request_normalization_replays_defaults_key_order_and_uuid_form(client, seeded):
    request_id = str(uuid.uuid4())
    anchor = _room_anchor()
    first_body = _body(seeded, anchor, request_id=request_id)
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
