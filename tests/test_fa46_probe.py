"""Temporary independent probes for Full Auto control review; remove after execution."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import db
import main
import pytest
from backend import resource_full_auto
from test_full_auto_control_api import (
    _cancel_run,
    _control_body,
    _create_full_auto,
    _edit_brief,
    _plan_revision,
)


from test_full_auto_preparation_independent import (
    _StructuredAssistant,
    _create_run as _create_preparable_run,
    _forbid_downstream_generation,
    _install_transport,
)


def _rewrite_initial_authorization(run_id: str, mutate) -> None:
    trigger = db.one(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
        "AND name = 'full_auto_run_receipt_immutable'",
    )
    assert trigger is not None and isinstance(trigger["sql"], str)
    row = db.one(
        "SELECT authorization_json FROM full_auto_run_receipt WHERE run_id = ?",
        run_id,
    )
    authorization = json.loads(row["authorization_json"])
    mutate(authorization)
    db.run("DROP TRIGGER full_auto_run_receipt_immutable")
    try:
        db.run(
            "UPDATE full_auto_run_receipt SET authorization_json = ? WHERE run_id = ?",
            json.dumps(authorization, ensure_ascii=False, sort_keys=True), run_id,
        )
    finally:
        db.run(trigger["sql"])


def test_probe_changed_scope_digest_survives_seconds(client, seeded, monkeypatch):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    _cancel_run(client, session_id, run_id)
    revision = _edit_brief(session_id, "Changed brief for clock probe.")
    start = datetime.now(timezone.utc).replace(microsecond=0)
    current_time = {"value": start.isoformat(timespec="seconds")}
    monkeypatch.setattr(db, "now", lambda: current_time["value"])

    path = f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight"
    first = client.get(path)
    assert first.status_code == 200, first.text
    current_time["value"] = (start + timedelta(seconds=2)).isoformat(timespec="seconds")
    body = _control_body(run_id, revision)
    body.update({
        "scope_digest": first.json()["scope_digest"],
        "comparison_issued_at": first.json()["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    accepted = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert accepted.status_code == 202, accepted.text


def test_probe_resume_receipt_replays_after_dependency_drift(client, seeded, monkeypatch):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    _cancel_run(client, session_id, run_id)
    revision = _edit_brief(session_id, "Changed brief for replay probe.")
    path = f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight"
    start = datetime.now(timezone.utc).replace(microsecond=0)
    current_time = {"value": start.isoformat(timespec="seconds")}
    monkeypatch.setattr(db, "now", lambda: current_time["value"])
    comparison = client.get(path)
    assert comparison.status_code == 200, comparison.text
    body = _control_body(run_id, revision)
    body.update({
        "scope_digest": comparison.json()["scope_digest"],
        "comparison_issued_at": comparison.json()["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    accepted = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert accepted.status_code == 202, accepted.text

    anchor = json.loads(db.one(
        "SELECT request_json FROM full_auto_run_scope_input WHERE run_id = ?", run_id,
    )["request_json"])["scene_anchor"]
    db.run(
        "UPDATE resource_library SET kind = 'other' WHERE library_key = ?",
        anchor["library_key"],
    )
    current_time["value"] = (start + timedelta(seconds=301)).isoformat(timespec="seconds")
    replay = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == accepted.json()


def test_probe_expired_comparison_refuses_with_zero_writes(client, seeded, monkeypatch):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    _cancel_run(client, session_id, run_id)
    revision = _edit_brief(session_id, "A revised studio brief for expiry.")
    start = datetime.now(timezone.utc).replace(microsecond=0)
    current_time = {"value": start.isoformat(timespec="seconds")}
    monkeypatch.setattr(db, "now", lambda: current_time["value"])
    comparison = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert comparison.status_code == 200, comparison.text

    before_run = dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id))
    before_auth = db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    )
    before_receipts = db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ? ORDER BY request_id",
        run_id,
    )
    body = _control_body(run_id, revision)
    body.update({
        "scope_digest": comparison.json()["scope_digest"],
        "comparison_issued_at": comparison.json()["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    current_time["value"] = (start + timedelta(seconds=301)).isoformat(timespec="seconds")
    refused = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "resume_comparison_stale"
    assert dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)) == before_run
    assert db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    ) == before_auth
    assert db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ? ORDER BY request_id",
        run_id,
    ) == before_receipts


def test_probe_replayed_control_receipt_rejects_nested_sentinel(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    original_store = resource_full_auto._store_action

    def store_with_sentinel(run_id, request_id, action, digest, row, view, timestamp):
        saved_view = dict(view)
        saved_view["nested"] = {"final_prompt": "private_receipt_prompt_sentinel"}
        original_store(run_id, request_id, action, digest, row, saved_view, timestamp)

    monkeypatch.setattr(resource_full_auto, "_store_action", store_with_sentinel)
    body = _control_body(run_id, _plan_revision(session_id))
    path = f"/api/sessions/{session_id}/full-auto/{run_id}/cancel"
    first = client.post(path, json=body)
    assert first.status_code == 200, first.text
    assert "private_receipt_prompt_sentinel" not in first.text
    before_receipt = dict(db.one(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ? AND request_id = ?",
        run_id, body["request_id"],
    ))

    monkeypatch.setattr(resource_full_auto, "_store_action", original_store)
    replay = client.post(path, json=body)
    assert replay.status_code == 500, replay.text
    assert replay.json()["detail"]["code"] == "run_receipt_invalid"
    assert "private_receipt_prompt_sentinel" not in replay.text
    assert dict(db.one(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ? AND request_id = ?",
        run_id, body["request_id"],
    )) == before_receipt


def test_probe_public_status_rejects_persisted_blocker_sentinels(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    db.run(
        "UPDATE full_auto_run SET state = 'blocked', blocker_code = ? WHERE run_id = ?",
        "private_prompt_path_sentinel", run_id,
    )
    response = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "run_state_invalid"
    assert "private_prompt_path_sentinel" not in response.text.lower()


def test_probe_public_status_rejects_malformed_persisted_progress_ids(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    db.run(
        """INSERT INTO full_auto_prepare_progress
           (run_id, plan_revision, target_ids_json, completed_ids_json, updated_at)
           VALUES (?, 1, ?, '[]', ?)""",
        run_id, json.dumps(["private_api_key_sentinel"]), db.now(),
    )
    response = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert response.status_code == 500, response.text
    assert response.json()["detail"]["code"] == "run_state_invalid"
    assert "private_api_key_sentinel" not in response.text.lower()


def test_probe_partial_valid_preparation_can_resume_without_dispatch(
    client, seeded, monkeypatch,
):
    session_id, run_id, targets = _create_preparable_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    assistant = _StructuredAssistant(fail_at=targets[1])
    _install_transport(monkeypatch, assistant)
    _forbid_downstream_generation(monkeypatch)
    result = asyncio.run(main._run_full_auto_preparation(
        resource_full_auto.claim_run(run_id),
    ))
    assert result["state"] == "blocked"
    snapshot = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, targets[0],
    )
    assert snapshot is not None and snapshot["status"] == "ready"
    snapshot = dict(snapshot)
    assert [call["take_id"] for call in assistant.calls] == targets[:2]
    status = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert status.status_code == 200, status.text
    assert status.json()["progress"]["prepared_take_ids"] == [targets[0]]
    assert status.json()["progress"]["unresolved_work_count"] == 0
    assert status.json()["can_resume"] is True
    # The attempted/ready takes may conservatively consume their own slots;
    # unstarted remaining takes must retain their per-take lower-bound allowance.
    assert status.json()["allowances"]["assistant_calls_remaining_lower_bound"] == 4
    assert status.json()["allowances"]["image_attempts_remaining_lower_bound"] == 4
    before_grants = db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    )
    before_children = db.q(
        "SELECT * FROM full_auto_prepare_child WHERE run_id = ? ORDER BY batch_index",
        run_id,
    )
    body = _control_body(run_id, _plan_revision(session_id))
    resume = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert resume.status_code == 202, resume.text
    assert resume.json()["state"] == "waiting_authoring"
    after = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, targets[0],
    )
    assert dict(after) == snapshot
    assert [call["take_id"] for call in assistant.calls] == targets[:2]
    assert db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    ) == before_grants
    assert db.q(
        "SELECT * FROM full_auto_prepare_child WHERE run_id = ? ORDER BY batch_index",
        run_id,
    ) == before_children
    assert db.one("SELECT 1 FROM shot WHERE session_id = ?", session_id) is None
    after_resume = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}"
    )
    assert after_resume.status_code == 200, after_resume.text
    assert after_resume.json()["can_resume"] is False
    assert after_resume.json()["allowances"]["assistant_calls_remaining_lower_bound"] == 4
    assert after_resume.json()["allowances"]["image_attempts_remaining_lower_bound"] == 4


def test_probe_stale_control_cas_and_changed_replay_do_not_write(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    path = f"/api/sessions/{session_id}/full-auto/{run_id}/cancel"
    revision = _plan_revision(session_id)
    body = _control_body(run_id, revision)
    before_run = dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id))

    stale_run = client.post(
        path, json={**body, "expected_run_revision": body["expected_run_revision"] + 1},
    )
    assert stale_run.status_code == 409, stale_run.text
    assert stale_run.json()["detail"]["code"] == "run_revision_stale"
    stale_plan = client.post(
        path, json={**body, "expected_plan_revision": revision + 1},
    )
    assert stale_plan.status_code == 409, stale_plan.text
    assert stale_plan.json()["detail"]["code"] == "plan_revision_stale"
    assert dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)) == before_run
    assert db.q("SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id) == []

    cancelled = client.post(path, json=body)
    assert cancelled.status_code == 200, cancelled.text
    replay = client.post(path, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == cancelled.json()
    before_receipts = db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
    )
    conflict = client.post(
        path, json={**body, "expected_plan_revision": revision + 1},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
    ) == before_receipts


def test_probe_resume_preflight_does_not_expose_nested_persisted_summary_data(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    _cancel_run(client, session_id, run_id)
    _edit_brief(session_id, "Changed brief for nested summary probe.")
    sentinel = "private_preflight_api_key_sentinel"

    def add_nested_private_value(authorization):
        provider = authorization["summary"]["provider"]
        assert isinstance(provider, dict)
        provider["api_key"] = sentinel

    _rewrite_initial_authorization(run_id, add_nested_private_value)
    response = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert response.status_code in {200, 409}, response.text
    assert sentinel not in response.text
    if response.status_code == 409:
        assert response.json()["detail"]["code"] in {
            "run_state_invalid", "full_auto_authorization_invalid",
        }
    else:
        provider = response.json()["old_scope"]["summary"]["provider"]
        assert "api_key" not in provider


@pytest.mark.parametrize(
    ("corruption", "sentinel"),
    [
        ("authorization_version", "private_version_sentinel"),
        ("policy_version", "private_policy_sentinel"),
        ("policy_type", "private_policy_type_sentinel"),
        ("limits", "private_limits_sentinel"),
    ],
)
def test_probe_public_status_rejects_malformed_saved_authorization(
    client, seeded, monkeypatch, corruption, sentinel,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)

    def corrupt(authorization):
        if corruption == "authorization_version":
            authorization["authorization_version"] = True
        elif corruption == "policy_version":
            authorization["summary"]["policy_version"] = sentinel
        elif corruption == "policy_type":
            authorization["summary"]["policy_version"] = {"value": sentinel}
        else:
            photo_count = authorization["summary"]["photo_count"]
            authorization["summary"]["limits"]["assistant_calls_total"] = photo_count * 2 - 1

    _rewrite_initial_authorization(run_id, corrupt)
    response = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "run_state_invalid"
    assert sentinel not in response.text


@pytest.mark.parametrize("corruption", ["action", "run_id", "session_id"])
def test_probe_historical_control_receipt_validates_its_identity(
    client, seeded, monkeypatch, corruption,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    body = _control_body(run_id, _plan_revision(session_id))
    path = f"/api/sessions/{session_id}/full-auto/{run_id}/cancel"
    cancelled = client.post(path, json=body)
    assert cancelled.status_code == 200, cancelled.text
    saved = dict(db.one(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ? AND request_id = ?",
        run_id, body["request_id"],
    ))
    view = json.loads(saved["response_json"])
    if corruption == "action":
        saved["action"] = "resume"
    elif corruption == "run_id":
        view["run_id"] = "different-run-identity-sentinel"
    else:
        view["session_id"] = session_id + 10_000
    saved["response_json"] = json.dumps(view, ensure_ascii=False, sort_keys=True)
    db.run(
        "DELETE FROM full_auto_run_action_receipt WHERE run_id = ? AND request_id = ?",
        run_id, body["request_id"],
    )
    db.run(
        """INSERT INTO full_auto_run_action_receipt
           (run_id, request_id, action, request_digest, epoch, response_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        saved["run_id"], saved["request_id"], saved["action"],
        saved["request_digest"], saved["epoch"], saved["response_json"],
        saved["created_at"],
    )
    corrupted_receipt = dict(db.one(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ? AND request_id = ?",
        run_id, body["request_id"],
    ))

    replay = client.post(path, json=body)
    assert replay.status_code == 409, replay.text
    assert replay.json()["detail"]["code"] == "run_receipt_invalid"
    assert "different-run-identity-sentinel" not in replay.text
    assert dict(db.one(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ? AND request_id = ?",
        run_id, body["request_id"],
    )) == corrupted_receipt


def test_probe_resume_consumes_exact_linked_grant_for_status_and_comparison(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    _cancel_run(client, session_id, run_id)
    root_receipt = dict(db.one(
        "SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id,
    ))
    root_grant = resource_full_auto.current_authorization(
        run_id, session_id=session_id,
    )
    revision = _edit_brief(session_id, "First accepted changed scope.")
    path = f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight"
    comparison_response = client.get(path)
    assert comparison_response.status_code == 200, comparison_response.text
    comparison = comparison_response.json()
    body = _control_body(run_id, revision)
    body.update({
        "scope_digest": comparison["scope_digest"],
        "comparison_issued_at": comparison["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    resumed = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert resumed.status_code == 202, resumed.text
    assert dict(db.one(
        "SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id,
    )) == root_receipt

    linked_rows = db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    )
    assert len(linked_rows) == 1
    linked = linked_rows[0]
    current = resource_full_auto.current_authorization(run_id, session_id=session_id)
    assert current["sequence"] == 1
    assert linked["previous_authorization_digest"] == root_grant["authorization_digest"]
    assert linked["comparison_digest"] == comparison["scope_digest"]
    assert linked["scope_digest"] == comparison["new_scope"]["scope_digest"]
    assert linked["scope_issued_at"] == comparison["comparison_issued_at"]
    assert linked["authorization_expires_at"] == comparison["new_scope"]["authorization_expires_at"]
    assert current["authorization_digest"] == resource_full_auto._authorization_digest(
        current["authorization"], current,
    )

    status = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert status.status_code == 200, status.text
    assert status.json()["authorization_expires_at"] == current["authorization_expires_at"]
    latest_comparison = client.get(path)
    assert latest_comparison.status_code == 200, latest_comparison.text
    latest = latest_comparison.json()
    assert latest["old_scope"]["scope_digest"] == current["scope_digest"]
    assert latest["old_scope"]["authorization_expires_at"] == current["authorization_expires_at"]
    assert latest["old_scope"]["summary"] == current["authorization"]["summary"]
    assert latest["changed"] is False


def test_probe_generated_history_refuses_continuity_change_after_grant_renewal(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    cancelled = _cancel_run(client, session_id, run_id)
    assert cancelled["state"] == "cancelled"
    first_revision = _edit_brief(session_id, "Accepted scope before generated history.")
    preflight_path = f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight"
    comparison_response = client.get(preflight_path)
    assert comparison_response.status_code == 200, comparison_response.text
    comparison = comparison_response.json()
    body = _control_body(run_id, first_revision)
    body.update({
        "scope_digest": comparison["scope_digest"],
        "comparison_issued_at": comparison["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    resumed = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["state"] == "waiting_authoring"
    current = resource_full_auto.current_authorization(run_id, session_id=session_id)
    assert current["sequence"] == 1

    shot_id = db.run(
        """INSERT INTO shot (session_id, shot_index, shot_label, prompt, status, created_at)
           VALUES (?, 0, ?, ?, 'done', ?)""",
        session_id, "take-001", "safe generated history fixture", db.now(),
    )
    db.run(
        """INSERT INTO prepared_take
           (session_id, plan_revision, take_id, status, linked_shot_id, created_at, updated_at)
           VALUES (?, ?, ?, 'generated', ?, ?, ?)""",
        session_id, first_revision, "take-001", shot_id, db.now(), db.now(),
    )
    _edit_brief(session_id, "Changed continuity after linked grant renewal.")
    before_run = dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id))
    before_linked = db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    )
    before_receipts = db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ? ORDER BY request_id",
        run_id,
    )
    before_root = dict(db.one(
        "SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id,
    ))

    refused = client.get(preflight_path)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "full_auto_continuity_requires_new_session"
    assert dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)) == before_run
    assert db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    ) == before_linked
    assert db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ? ORDER BY request_id",
        run_id,
    ) == before_receipts
    assert dict(db.one(
        "SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id,
    )) == before_root

    discovery = client.get(f"/api/sessions/{session_id}/full-auto")
    assert discovery.status_code == 200, discovery.text
    assert discovery.json()["current_run"]["run_id"] == run_id
    assert discovery.json()["current_run"]["state"] == resumed.json()["state"]


def test_probe_resume_feature_flag_environment_overrides_config_without_writes(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    _cancel_run(client, session_id, run_id)
    path = f"/api/sessions/{session_id}/full-auto/{run_id}/resume"
    body = _control_body(run_id, _plan_revision(session_id))
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    before_run = dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id))
    before_receipts = db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
    )
    disabled = client.post(path, json=body)
    assert disabled.status_code == 503, disabled.text
    assert disabled.json()["detail"]["code"] == "resource_planning_disabled"
    assert dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)) == before_run
    assert db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
    ) == before_receipts

    monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "true")
    resumed = client.post(path, json=body)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["state"] == "waiting_authoring"
    assert db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ? AND request_id = ?",
        run_id, body["request_id"],
    )


def test_probe_unsupported_saved_policy_blocks_resume_without_writes(
    client, seeded, monkeypatch,
):
    session_id, run_id = _create_full_auto(client, seeded, monkeypatch)
    _cancel_run(client, session_id, run_id)
    monkeypatch.setattr(
        resource_full_auto, "_SUPPORTED_POLICY_VERSION", "resource-full-auto-v999",
    )

    status = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert status.status_code == 200, status.text
    assert status.json()["policy_version"] == "resource-full-auto-v1"
    assert status.json()["blocker"]["code"] == "policy_changed"
    assert status.json()["can_resume"] is False

    before_run = dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id))
    before_grants = db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    )
    before_receipts = db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
    )
    resumed = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=_control_body(run_id, _plan_revision(session_id)),
    )
    assert resumed.status_code == 409, resumed.text
    assert resumed.json()["detail"]["code"] == "policy_changed"
    assert dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)) == before_run
    assert db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    ) == before_grants
    assert db.q(
        "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
    ) == before_receipts
