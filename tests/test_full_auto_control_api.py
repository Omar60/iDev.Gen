"""Public session-scoped Full Auto discovery and control API checks."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import db
import main
from backend import resource_full_auto
from test_guided_session_creation import (
    _forbid_full_auto_side_effects,
    _full_auto_payload,
    _room_anchor,
)


_REAL_CANCELLATION_SCHEDULER = main._schedule_full_auto_cancellation


def _create_full_auto(client, seeded, monkeypatch) -> tuple[int, str]:
    _forbid_full_auto_side_effects(monkeypatch)
    payload, _saved, _published = _full_auto_payload(
        client, seeded, _room_anchor(),
    )
    response = client.post("/api/sessions/guided", json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    return body["session_id"], body["full_auto_run_id"]


def _plan_revision(session_id: int) -> int:
    return int(db.one(
        "SELECT plan_revision FROM session_plan WHERE session_id = ?",
        session_id,
    )["plan_revision"])


def _edit_brief(session_id: int, brief: str) -> int:
    row = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )
    plan = json.loads(row["plan_json"])
    plan["authoring"]["brief"] = brief
    revision = int(row["plan_revision"]) + 1
    db.run(
        "UPDATE session_plan SET plan_revision = ?, plan_json = ?, updated_at = ? WHERE session_id = ?",
        revision, json.dumps(plan, ensure_ascii=False, sort_keys=True),
        db.now(), session_id,
    )
    return revision


def _control_body(run_id: str, plan_revision: int, *, request_id: str | None = None) -> dict:
    run = db.one("SELECT revision FROM full_auto_run WHERE run_id = ?", run_id)
    return {
        "request_id": request_id or str(uuid.uuid4()),
        "expected_run_revision": int(run["revision"]),
        "expected_plan_revision": plan_revision,
    }


def _cancel_run(client, session_id: int, run_id: str) -> dict:
    body = _control_body(run_id, _plan_revision(session_id))
    response = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/cancel", json=body,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_session_discovery_empty_missing_and_scoped_safe_status(client, seeded, monkeypatch):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)

    status = client.get(f"/api/sessions/{session_id}/full-auto")
    assert status.status_code == 200, status.text
    run = status.json()["current_run"]
    assert run["run_id"] == run_id
    assert run["session_id"] == session_id
    assert (run["revision"], run["epoch"], run["phase"], run["state"]) == (
        1, 1, "prepare", "active",
    )
    assert run["authorization_version"] == 1
    assert run["policy_version"] == "resource-full-auto-v1"
    assert run["progress"]["target_count"] == 4
    assert run["allowances"]["assistant_calls_remaining_lower_bound"] == 8
    serialized = json.dumps(run, sort_keys=True).lower()
    for forbidden in (
        "prompt", "final_prompt", "path", "authorization_json",
        "fencing_token", "lease_expires_at", "api_key",
    ):
        assert forbidden not in serialized

    other_session_id = db.run(
        "INSERT INTO session (model_id, name, created_at) VALUES (?, ?, ?)",
        seeded["model_id"], "other-session", db.now(),
    )
    scoped = client.get(
        f"/api/sessions/{other_session_id}/full-auto/{run_id}",
    )
    assert scoped.status_code == 404
    assert scoped.json()["detail"]["code"] == "full_auto_run_not_found"

    empty = client.get(f"/api/sessions/{other_session_id}/full-auto")
    assert empty.status_code == 200
    assert empty.json() == {"current_run": None}

    missing = client.get("/api/sessions/999999/full-auto")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "session_not_found"


def test_cancel_uses_backend_evidence_closed_body_and_remains_available_disabled(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "false")
    body = _control_body(run_id, _plan_revision(session_id))
    before = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)

    forged = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
        json={**body, "work_pending": False},
    )
    assert forged.status_code == 422
    assert forged.json()["detail"]["code"] == "extra_field_forbidden"
    assert db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id) == before
    assert db.q("SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id) == []

    cancelled = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/cancel", json=body,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"
    assert db.one(
        "SELECT request_digest FROM full_auto_run_action_receipt WHERE run_id = ?",
        run_id,
    ) is not None

    status = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert status.status_code == 200
    assert status.json()["can_cancel"] is False
    assert status.json()["can_resume"] is False
    assert status.json()["authorization_expires_at"]

    preflight = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert preflight.status_code == 200, preflight.text

    resume = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=_control_body(run_id, _plan_revision(session_id)),
    )
    assert resume.status_code == 503
    assert resume.json()["detail"]["code"] == "resource_planning_disabled"


def test_failed_shot_stays_unresolved_and_reconciliation_never_fakes_settlement(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    db.run(
        """INSERT INTO shot (session_id, shot_index, shot_label, prompt, status, created_at)
           VALUES (?, 0, ?, ?, 'failed', ?)""",
        session_id, "take-001", "safe unresolved fixture", db.now(),
    )
    monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "false")

    before = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
    assert before["progress"]["unresolved_work_count"] == 1
    assert before["can_resume"] is False
    assert before["blocker"]["code"] == "run_work_unsettled"

    cancel_body = _control_body(run_id, _plan_revision(session_id))
    cancel = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
        json=cancel_body,
    )
    assert cancel.status_code == 202
    assert cancel.json()["state"] == "cancel_requested"
    assert cancel.json()["cancel_intent"] is True

    # The test fixture suppresses the background cancellation worker. Model its
    # lease expiring before explicit Resume takes a fresh read-only claim.
    db.run(
        "UPDATE full_auto_run SET lease_expires_at = ? WHERE run_id = ?",
        "2000-01-01T00:00:00+00:00", run_id,
    )
    reconcile_body = _control_body(run_id, _plan_revision(session_id))
    reconcile = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=reconcile_body,
    )
    assert reconcile.status_code == 202
    assert reconcile.json()["state"] == "cancel_requested"
    assert reconcile.json()["cancel_intent"] is True
    row = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert row["state"] == "cancel_requested"
    assert row["lease_expires_at"] is not None
    assert row["lease_started_at"] is not None
    assert json.loads(row["evidence_json"])["kind"] == "cancel"


def test_initial_cancel_schedules_owned_read_only_draining(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    db.run(
        "INSERT INTO shot (session_id, shot_index, prompt, status, created_at) "
        "VALUES (?, 0, ?, 'running', ?)",
        session_id, "Synthetic admitted work awaiting cancellation reconciliation.", db.now(),
    )
    scheduled = []
    monkeypatch.setattr(
        main, "_schedule_full_auto_cancellation",
        lambda *args: scheduled.append(args),
    )
    monkeypatch.setattr(
        main, "_schedule_full_auto_run",
        lambda *_args: pytest.fail("Cancel must never schedule the active coordinator"),
    )

    response = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
        json=_control_body(run_id, _plan_revision(session_id)),
    )

    assert response.status_code == 202, response.text
    assert response.json()["state"] == "cancel_requested"
    assert len(scheduled) == 1
    scheduled_run_id, scheduled_session_id, claim = scheduled[0]
    assert (scheduled_run_id, scheduled_session_id) == (run_id, session_id)
    assert claim.requires_reconciliation is True
    assert claim.run_id == run_id


def test_cancel_http_schedules_reconciliation_on_the_asgi_loop(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    db.run(
        "INSERT INTO shot (session_id, shot_index, prompt, status, created_at) "
        "VALUES (?, 0, ?, 'running', ?)",
        session_id, "Synthetic admitted work awaiting cancellation reconciliation.", db.now(),
    )
    scheduled = threading.Event()
    calls = []

    async def observe_cancellation(run_id, session_id, claim):
        calls.append((run_id, session_id, claim))
        scheduled.set()

    monkeypatch.setattr(main, "_schedule_full_auto_cancellation", _REAL_CANCELLATION_SCHEDULER)
    monkeypatch.setattr(main, "_reconcile_full_auto_cancellation", observe_cancellation)
    response = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
        json=_control_body(run_id, _plan_revision(session_id)),
    )

    assert response.status_code == 202, response.text
    assert scheduled.wait(timeout=2), "the cancellation worker was not started on the ASGI loop"
    assert len(calls) == 1
    assert calls[0][0:2] == (run_id, session_id)
    assert calls[0][2].requires_reconciliation is True


def test_resume_claims_and_schedules_only_read_only_cancel_reconciliation(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    current = resource_full_auto.get_run(run_id)
    pending = resource_full_auto.cancel_run(
        run_id, str(uuid.uuid4()), current["revision"], current["plan_revision"],
        work_pending=True, expected_session_id=session_id,
    )
    assert pending.view["state"] == "cancel_requested"
    scheduled = []
    monkeypatch.setattr(
        main, "_schedule_full_auto_cancellation",
        lambda *args: scheduled.append(args),
    )
    monkeypatch.setattr(
        main, "_schedule_full_auto_run",
        lambda *_args: pytest.fail("cancellation Resume must not schedule the active coordinator"),
    )

    response = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=_control_body(run_id, _plan_revision(session_id)),
    )

    assert response.status_code == 202, response.text
    assert response.json()["state"] == "cancel_requested"
    assert response.json()["cancel_intent"] is True
    assert len(scheduled) == 1
    scheduled_run, scheduled_session, claim = scheduled[0]
    assert (scheduled_run, scheduled_session) == (run_id, session_id)
    assert claim.requires_reconciliation is True
    row = db.one("SELECT state, phase, cancel_intent, lease_started_at FROM full_auto_run WHERE run_id = ?", run_id)
    assert row == {
        "state": "cancel_requested", "phase": "reconcile",
        "cancel_intent": 1, "lease_started_at": claim.lease_started_at,
    }


def test_resume_preflight_recomputes_scope_and_renewal_is_atomic_and_linked(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    _cancel_run(client, session_id, run_id)
    original_receipt = dict(db.one(
        "SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id,
    ))

    changed_plan_revision = _edit_brief(session_id, "A revised studio brief.")
    preflight_path = f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight"
    comparison = client.get(preflight_path)
    assert comparison.status_code == 200, comparison.text
    old_comparison = comparison.json()
    assert old_comparison["changed"] is True
    assert "plan" in old_comparison["changed_fields"]
    assert "_authorization" not in old_comparison
    assert "_dependencies_digest" not in old_comparison

    before_run = dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id))
    before_authorizations = db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ?", run_id,
    )
    before_actions = db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
    )
    body = _control_body(run_id, changed_plan_revision)
    body.update({
        "scope_digest": old_comparison["scope_digest"],
        "comparison_issued_at": old_comparison["comparison_issued_at"],
    })
    refused = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "authorization_scope_changed"
    assert dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)) == before_run
    assert db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ?", run_id,
    ) == before_authorizations
    assert db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
    ) == before_actions

    newer_plan_revision = _edit_brief(session_id, "A second revised studio brief.")
    stale_body = _control_body(run_id, newer_plan_revision)
    stale_body.update({
        "scope_digest": old_comparison["scope_digest"],
        "comparison_issued_at": old_comparison["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    stale = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=stale_body,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "resume_comparison_stale"
    assert db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ?", run_id,
    ) == before_authorizations
    assert db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
    ) == before_actions

    current_comparison = client.get(preflight_path)
    assert current_comparison.status_code == 200, current_comparison.text
    fresh = current_comparison.json()
    assert fresh["changed"] is True
    accept_body = _control_body(run_id, newer_plan_revision)
    accept_body.update({
        "scope_digest": fresh["scope_digest"],
        "comparison_issued_at": fresh["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    accepted = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=accept_body,
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["state"] == "waiting_authoring"
    assert accepted.json()["plan_revision"] == newer_plan_revision
    assert accepted.json()["epoch"] == 2
    assert db.one(
        "SELECT lease_expires_at FROM full_auto_run WHERE run_id = ?", run_id,
    )["lease_expires_at"] is None

    assert dict(db.one(
        "SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id,
    )) == original_receipt
    linked = db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    )
    assert len(linked) == 1
    assert linked[0]["authorization_sequence"] == 1
    assert linked[0]["comparison_digest"] == fresh["scope_digest"]
    current_grant = resource_full_auto.current_authorization(
        run_id, session_id=session_id,
    )
    assert current_grant["sequence"] == 1
    assert current_grant["plan_digest"] == linked[0]["plan_digest"]
    assert current_grant["scope_digest"] == fresh["new_scope"]["scope_digest"]
    assert current_grant["scope_issued_at"] == fresh["comparison_issued_at"]
    assert current_grant["authorization_expires_at"] == fresh["new_scope"]["authorization_expires_at"]

    altered_body = dict(accept_body)
    altered_body["comparison_issued_at"] = (
        datetime.fromisoformat(fresh["comparison_issued_at"])
        - timedelta(seconds=1)
    ).isoformat(timespec="seconds")
    conflict = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=altered_body,
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"

    anchor = json.loads(db.one(
        "SELECT request_json FROM full_auto_run_scope_input WHERE run_id = ?", run_id,
    )["request_json"])["scene_anchor"]
    monkeypatch.setattr(resource_full_auto.resource_planning, "is_enabled", lambda: False)
    db.run(
        "UPDATE resource_library SET kind = 'other' WHERE library_key = ?",
        anchor["library_key"],
    )

    replay = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=accept_body,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == accepted.json()
    assert db.one("SELECT revision FROM full_auto_run WHERE run_id = ?", run_id)["revision"] == accepted.json()["revision"]
    assert len(db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ?", run_id,
    )) == 1


def test_unchanged_resume_without_comparison_pair_replays_after_dependency_drift(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    _cancel_run(client, session_id, run_id)
    body = _control_body(run_id, _plan_revision(session_id))
    path = f"/api/sessions/{session_id}/full-auto/{run_id}/resume"
    resumed = client.post(path, json=body)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["epoch"] == 2
    assert db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ?", run_id,
    ) == []

    anchor = json.loads(db.one(
        "SELECT request_json FROM full_auto_run_scope_input WHERE run_id = ?", run_id,
    )["request_json"])["scene_anchor"]
    monkeypatch.setattr(resource_full_auto.resource_planning, "is_enabled", lambda: False)
    db.run(
        "UPDATE resource_library SET kind = 'other' WHERE library_key = ?",
        anchor["library_key"],
    )
    replay = client.post(path, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == resumed.json()


def test_changed_scope_comparison_deadline_survives_post_after_seconds(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    _cancel_run(client, session_id, run_id)
    plan_revision = _edit_brief(session_id, "A brief for stable deadline consent.")
    start = datetime.now(timezone.utc).replace(microsecond=0)
    current_time = {"value": start.isoformat(timespec="seconds")}
    monkeypatch.setattr(db, "now", lambda: current_time["value"])

    preflight = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert preflight.status_code == 200, preflight.text
    comparison = preflight.json()
    assert comparison["changed"] is True
    assert comparison["new_scope"]["authorization_expires_at"] == (
        datetime.fromisoformat(comparison["comparison_issued_at"])
        + timedelta(days=7)
    ).isoformat(timespec="seconds")

    before_run = dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id))
    before_authorizations = db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ?", run_id,
    )
    before_actions = db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
    )
    stale_body = _control_body(run_id, plan_revision)
    stale_body.update({
        "scope_digest": comparison["scope_digest"],
        "comparison_issued_at": comparison["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    current_time["value"] = (start + timedelta(seconds=301)).isoformat(timespec="seconds")
    stale = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=stale_body,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "resume_comparison_stale"
    assert dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)) == before_run
    assert db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ?", run_id,
    ) == before_authorizations
    assert db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
    ) == before_actions

    refreshed = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert refreshed.status_code == 200, refreshed.text
    comparison = refreshed.json()
    current_time["value"] = (start + timedelta(seconds=303)).isoformat(timespec="seconds")
    body = _control_body(run_id, plan_revision)
    body.update({
        "scope_digest": comparison["scope_digest"],
        "comparison_issued_at": comparison["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    accepted = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert accepted.status_code == 202, accepted.text
    grant = resource_full_auto.current_authorization(run_id, session_id=session_id)
    assert grant["scope_issued_at"] == comparison["comparison_issued_at"]
    assert grant["authorization_expires_at"] == comparison["new_scope"]["authorization_expires_at"]


def test_generated_history_rejects_changed_scope_comparison(client, seeded, monkeypatch):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    _cancel_run(client, session_id, run_id)
    take_id = "take-001"
    shot_id = db.run(
        """INSERT INTO shot (session_id, shot_index, shot_label, prompt, status, created_at)
           VALUES (?, ?, ?, ?, 'done', ?)""",
        session_id, 0, take_id, "safe generated history fixture", db.now(),
    )
    db.run(
        """INSERT INTO prepared_take
           (session_id, plan_revision, take_id, status, linked_shot_id, created_at, updated_at)
           VALUES (?, 1, ?, 'generated', ?, ?, ?)""",
        session_id, take_id, shot_id, db.now(), db.now(),
    )
    _edit_brief(session_id, "A changed brief after generated history.")
    before_actions = db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
    )

    response = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "full_auto_continuity_requires_new_session"
    assert db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ?", run_id,
    ) == []
    assert db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
    ) == before_actions
