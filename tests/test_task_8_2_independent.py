"""Independent acceptance probes for Task 8.2 shared-choice CAS behavior."""
from __future__ import annotations

import asyncio
import uuid

import db
import main
import pytest

from test_authoring_operation_api import (
    _configure_assistant,
    _create_guided_session,
    _install_fake_structured_assistant,
    _start_body,
)


@pytest.fixture(autouse=True)
def _disable_background_suggestion_dispatch(monkeypatch):
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", lambda view: None)


def _plan_state(session_id: int) -> dict:
    plan = db.one(
        "SELECT plan_revision, plan_json, updated_at FROM session_plan WHERE session_id = ?",
        session_id,
    )
    return {
        "plan": (plan["plan_revision"], plan["plan_json"], plan["updated_at"]),
        "operations": db.one(
            "SELECT COUNT(*) AS n FROM authoring_operation WHERE session_id = ?",
            session_id,
        )["n"],
        "approvals": db.one(
            "SELECT COUNT(*) AS n FROM session_plan_approval WHERE session_id = ?",
            session_id,
        )["n"],
        "prepared": db.one(
            "SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ?",
            session_id,
        )["n"],
        "shots": db.one(
            "SELECT COUNT(*) AS n FROM shot WHERE session_id = ?",
            session_id,
        )["n"],
    }


def _disable_assistant(monkeypatch) -> None:
    monkeypatch.setitem(main.CONFIG, "llm_url", "")
    monkeypatch.setitem(main.CONFIG, "llm_model", "")
    monkeypatch.setitem(main.CONFIG, "llm_key", "")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)


@pytest.mark.parametrize("mode", ["manual", "automatic"])
def test_explicit_empty_shared_decisions_are_saved_with_user_origin_in_either_mode(
    client, seeded, monkeypatch, mode,
):
    _disable_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, mode=mode)
    before = client.get(f"/api/sessions/{session_id}/plan").json()
    assert before["plan"]["authoring"]["mode"] == mode
    assert before["plan"]["look"] == ""
    assert before["plan"]["initial_wardrobe"] == ""
    assert before["shared_summary"]["look"] == {"value": "", "origin": "none"}
    assert before["shared_summary"]["initial_wardrobe"] == {
        "value": "",
        "origin": "none",
    }

    saved = client.post(
        f"/api/sessions/{session_id}/plan",
        json={
            "plan": before["plan"],
            "expected_revision": revision,
            "shared_decisions": ["look", "initial_wardrobe"],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["plan_revision"] == revision + 1

    after = client.get(f"/api/sessions/{session_id}/plan").json()
    assert after["plan_revision"] == revision + 1
    assert after["plan"]["look"] == ""
    assert after["plan"]["initial_wardrobe"] == ""
    assert after["plan"]["authoring"]["shared_state"] == {
        "look": {"origin": "user", "evidence_id": None},
        "initial_wardrobe": {"origin": "user", "evidence_id": None},
    }
    assert after["shared_summary"]["look"] == {"value": "", "origin": "user"}
    assert after["shared_summary"]["initial_wardrobe"] == {
        "value": "",
        "origin": "user",
    }
    assert after["plan"]["authoring"]["evidence"] == []

    persisted = _plan_state(session_id)
    assert persisted["operations"] == 0
    assert persisted["approvals"] == 0
    assert persisted["prepared"] == 0
    assert persisted["shots"] == 0

    stale = client.post(
        f"/api/sessions/{session_id}/plan",
        json={
            "plan": before["plan"],
            "expected_revision": revision,
            "shared_decisions": ["look"],
        },
    )
    assert stale.status_code == 409, stale.text
    assert _plan_state(session_id) == persisted


@pytest.mark.parametrize(
    "decisions",
    [
        "look",
        ["unknown"],
        ["look", "look"],
        ["look", 1],
        ["look", True],
        [None],
    ],
)
def test_shared_decisions_reject_non_lists_unknown_duplicates_and_coercions_without_writes(
    client, seeded, monkeypatch, decisions,
):
    _disable_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, mode="manual")
    plan = client.get(f"/api/sessions/{session_id}/plan").json()["plan"]
    before = _plan_state(session_id)

    response = client.post(
        f"/api/sessions/{session_id}/plan",
        json={
            "plan": plan,
            "expected_revision": revision,
            "shared_decisions": decisions,
        },
    )

    assert response.status_code == 422, response.text
    assert _plan_state(session_id) == before


def test_shared_decision_is_only_valid_for_a_value_that_remains_empty(
    client, seeded, monkeypatch,
):
    _disable_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, mode="manual")
    plan = client.get(f"/api/sessions/{session_id}/plan").json()["plan"]
    plan["look"] = "An explicit look value."
    before = _plan_state(session_id)

    response = client.post(
        f"/api/sessions/{session_id}/plan",
        json={
            "plan": plan,
            "expected_revision": revision,
            "shared_decisions": ["look"],
        },
    )

    assert response.status_code == 422, response.text
    assert _plan_state(session_id) == before


def test_generic_plan_save_cannot_forge_user_origin_for_unchanged_empty_value(
    client, seeded, monkeypatch,
):
    _disable_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, mode="manual")
    plan = client.get(f"/api/sessions/{session_id}/plan").json()["plan"]
    plan["authoring"]["shared_state"]["look"] = {
        "origin": "user",
        "evidence_id": None,
    }
    before = _plan_state(session_id)

    response = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": plan, "expected_revision": revision},
    )

    assert response.status_code == 409, response.text
    assert _plan_state(session_id) == before


def test_suggestions_require_explicit_start_and_acceptance_and_active_retry_reuses_one_claim(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, mode="automatic")
    assert _plan_state(session_id)["operations"] == 0
    proposals = {
        "look": "A proposed soft studio look.",
        "initial_wardrobe": "A proposed blue jacket.",
    }
    assistant_calls = _install_fake_structured_assistant(monkeypatch, [proposals])
    url = f"/api/sessions/{session_id}/plan/authoring/operations"
    body = _start_body(
        "shared_suggestions",
        request_id=str(uuid.uuid4()),
        revision=revision,
    )

    started = client.post(url, json=body)
    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]
    assert assistant_calls == []

    replay = client.post(url, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["operation_id"] == operation_id
    assert assistant_calls == []

    asyncio.run(main._run_shared_suggestion_operation(session_id, operation_id))
    assert len(assistant_calls) == 1
    status = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    )
    assert status.status_code == 200, status.text
    assert status.json()["state"] == "succeeded"
    assert status.json()["result"]["items"] == [
        {"target": "look", "result": proposals["look"]},
        {"target": "initial_wardrobe", "result": proposals["initial_wardrobe"]},
    ]

    pending = client.get(f"/api/sessions/{session_id}/plan").json()
    assert pending["plan_revision"] == revision
    assert pending["plan"]["look"] == ""
    assert pending["plan"]["initial_wardrobe"] == ""
    assert pending["plan"]["authoring"]["shared_state"] == {
        "look": {"origin": "none", "evidence_id": None},
        "initial_wardrobe": {"origin": "none", "evidence_id": None},
    }
    assert pending["shared_summary"]["look"] == {"value": "", "origin": "none"}
    assert pending["shared_summary"]["initial_wardrobe"] == {
        "value": "",
        "origin": "none",
    }
    state = _plan_state(session_id)
    assert state["operations"] == 1
    assert state["approvals"] == 0
    assert state["prepared"] == 0
    assert state["shots"] == 0
