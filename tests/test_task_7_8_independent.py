"""Independent boundary probes for explicit resource refresh (task 7.8)."""
from __future__ import annotations

import json

import db
import main
import pytest

from test_authoring_operation_api import (
    _configure_assistant,
    _create_guided_session,
    _room_anchor,
    _start_body,
    _task76_adapted_ready_session,
)


@pytest.fixture(autouse=True)
def _keep_authoring_operations_deterministic(monkeypatch):
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", lambda view: None)


def _session_state(session_id: int) -> dict:
    return {
        "session": db.one("SELECT * FROM session WHERE id = ?", session_id),
        "session_plan": db.q(
            "SELECT * FROM session_plan WHERE session_id = ? ORDER BY id", session_id,
        ),
        "session_plan_approval": db.q(
            "SELECT * FROM session_plan_approval WHERE session_id = ? ORDER BY id",
            session_id,
        ),
        "authoring_operation": db.q(
            "SELECT * FROM authoring_operation WHERE session_id = ? ORDER BY operation_id",
            session_id,
        ),
        "prepared_take": db.q(
            "SELECT * FROM prepared_take WHERE session_id = ? ORDER BY id", session_id,
        ),
        "take_resource_adaptation": db.q(
            "SELECT * FROM take_resource_adaptation WHERE session_id = ? ORDER BY id",
            session_id,
        ),
    }


def _refresh(client, session_id: int, body: dict):
    return client.post(
        f"/api/sessions/{session_id}/plan/refresh-resources",
        json=body,
    )


def test_refresh_request_is_strict_and_no_drift_preserves_active_operation(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=revision, take_ids=["take-001"]),
    )
    assert started.status_code == 202, started.text
    before = _session_state(session_id)
    operation = before["authoring_operation"][0]
    assert operation["state"] == "active"

    for body in (
        {},
        {"expected_revision": True},
        {"expected_revision": 1.0},
        {"expected_revision": "1"},
        {"expected_revision": 0},
        {"expected_revision": revision, "extra": "refuse"},
    ):
        response = _refresh(client, session_id, body)
        assert response.status_code == 422, (body, response.text)
        assert _session_state(session_id) == before

    response = _refresh(client, session_id, {"expected_revision": revision})
    assert response.status_code == 200, response.text
    assert response.json()["plan_revision"] == revision
    assert response.json()["refreshed"] is False
    assert _session_state(session_id) == before


def test_refresh_uses_only_the_adaptations_consumed_by_a_ready_snapshot(
    client, seeded, monkeypatch,
):
    session_id, consumed, _ = _task76_adapted_ready_session(
        client, seeded, monkeypatch,
    )
    plan = main.session_plan.get_draft(session_id)["plan"]
    selected = plan["selected_resources"][0]

    # A current, reviewed adaptation that this ready snapshot never consumed
    # must not rewrite its resource-input dependency digest.
    main.resource_preparation.record_take_adaptation(
        session_id, 1, "take-001",
        {
            **selected,
            "resource_field": "label",
            "adapted_value": "A reviewed but unused room label.",
        },
    )
    unchanged = _refresh(client, session_id, {"expected_revision": 1})
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["refreshed"] is False
    assert unchanged.json()["plan_revision"] == 1

    # Changing the adaptation that was recorded in the ready evidence must
    # require a new revision and preparation.
    main.resource_preparation.record_take_adaptation(
        session_id, 1, "take-001",
        {
            **selected,
            "resource_field": consumed["resource_field"],
            "adapted_value": "A replacement reviewed scene adaptation.",
        },
    )
    changed = _refresh(client, session_id, {"expected_revision": 1})
    assert changed.status_code == 200, changed.text
    assert changed.json()["plan_revision"] == 2
    assert changed.json()["refreshed"] is True
    assert "take-001" in changed.json()["required_preparation"]


def test_authoring_ready_row_cannot_use_foreign_expert_evidence_for_no_drift(
    client, seeded, monkeypatch,
):
    session_id, _, _ = _task76_adapted_ready_session(
        client, seeded, monkeypatch,
    )
    row = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = 1 "
        "AND take_id = 'take-002'",
        session_id,
    )
    provenance = json.loads(row["provenance"])
    assert "authoring_evidence" in provenance

    foreign_expert_evidence = main.resource_preparation.build_pre_authoring_resource_input_evidence(
        session_id, 1, "take-002", {},
    )
    provenance.pop("authoring_evidence")
    provenance["resource_input_evidence"] = foreign_expert_evidence
    db.run(
        "UPDATE prepared_take SET provenance = ? WHERE id = ?",
        json.dumps(provenance, ensure_ascii=False, separators=(",", ":")),
        row["id"],
    )

    response = _refresh(client, session_id, {"expected_revision": 1})
    assert response.status_code == 200, response.text
    assert response.json()["plan_revision"] == 2
    assert response.json()["refreshed"] is True
    assert "take-002" in response.json()["required_preparation"]
    assert any(
        item["take_id"] == "take-002"
        and item["code"] == "resource_dependency_unverifiable"
        for item in response.json()["diagnostics"]
    )


def test_expert_ready_row_cannot_use_foreign_authoring_evidence_for_no_drift(
    client, seeded,
):
    created = client.post("/api/sessions", json={
        "model_id": seeded["model_id"],
        "name": "independent expert refresh evidence",
        "composition_mode": "resource-v1",
    })
    assert created.status_code == 200, created.text
    session_id = created.json()["id"]
    anchor = _room_anchor()
    plan = {
        "version": "resource-v1",
        "look": "",
        "initial_wardrobe": "",
        "takes": [{"take_id": "take-001", "label": "invented studio view"}],
        "selected_resources": [anchor],
        "wardrobe_changes": [],
    }
    saved = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": plan, "expected_revision": 0},
    )
    assert saved.status_code == 200, saved.text
    begun = client.post(
        f"/api/sessions/{session_id}/plan/preparations/begin",
        json={"plan_revision": 1, "take_id": "take-001"},
    )
    assert begun.status_code == 200, begun.text
    completed = client.post(
        f"/api/sessions/{session_id}/plan/preparations/complete",
        json={
            "plan_revision": 1,
            "take_id": "take-001",
            "final_prompt": "An invented prepared studio view.",
            "effective_state": {"look": "", "wardrobe": ""},
            "mapping_version": "test-map-v1",
            "compiler_version": "test-compiler-v1",
            "provenance": {"source": "invented expert snapshot"},
        },
    )
    assert completed.status_code == 200, completed.text

    row = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = 1 "
        "AND take_id = 'take-001'",
        session_id,
    )
    provenance = json.loads(row["provenance"])
    expert_evidence = provenance.pop("resource_input_evidence")
    provenance["selected_resource_revisions"] = expert_evidence[
        "selected_resource_revisions"
    ]
    provenance["authoring_evidence"] = {
        "resource_projection": expert_evidence["resource_projection"],
        "effective_resource_input_digest": expert_evidence[
            "effective_resource_input_digest"
        ],
    }
    db.run(
        "UPDATE prepared_take SET provenance = ? WHERE id = ?",
        json.dumps(provenance, ensure_ascii=False, separators=(",", ":")),
        row["id"],
    )

    response = _refresh(client, session_id, {"expected_revision": 1})
    assert response.status_code == 200, response.text
    assert response.json()["plan_revision"] == 2
    assert response.json()["refreshed"] is True
    assert "take-001" in response.json()["required_preparation"]
    assert any(
        item["take_id"] == "take-001"
        and item["code"] == "resource_dependency_unverifiable"
        for item in response.json()["diagnostics"]
    )
