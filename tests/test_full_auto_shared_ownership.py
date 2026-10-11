"""Task 4.3 service and HTTP fences for shared session mutations."""
from __future__ import annotations

import uuid

import db
import main
import pytest
from backend import (
    authoring_operations,
    resource_full_auto,
    resource_preparation,
    session_plan,
)
from test_full_auto_preparation_coordinator import _create_run


def _active_run(client, seeded, monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "full-auto-ownership-test")
    monkeypatch.setitem(main.CONFIG, "llm_key", "full-auto-ownership-test-key")
    return _create_run(client, seeded, photo_count=4)


def _safe_current(detail: dict, run_id: str) -> dict:
    current = detail.get("current")
    assert isinstance(current, dict)
    assert current["run_id"] == run_id
    assert current["state"] == "active"
    assert not {
        "lease_expires_at", "lease_started_at", "fencing_token", "claim",
        "_signature", "authorization_json",
    }.intersection(current)
    return current


def _valid_adaptation(session_id: int, revision: int, take_id: str) -> dict:
    preparation = resource_preparation.prepare_take_inputs(
        session_id, revision, take_id,
    )
    for entry in preparation["resource_inputs"]:
        descriptive = entry.get("descriptive_inputs", {})
        for field, source_value in descriptive.items():
            if isinstance(source_value, str):
                return {
                    "library_key": entry["library_key"],
                    "source_id": entry["source_id"],
                    "content_digest": entry["content_digest"],
                    "resource_field": field,
                    "adapted_value": "A reviewed replacement description.",
                }
    raise AssertionError("Full Auto fixture has no adaptable descriptive resource field")


def test_adaptation_and_plan_edit_are_blocked_at_service_and_http_boundaries(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = _active_run(client, seeded, monkeypatch)
    adaptation = _valid_adaptation(session_id, revision, targets[0])

    with pytest.raises(resource_full_auto.FullAutoRunError) as direct_adaptation:
        resource_preparation.record_take_adaptation(
            session_id, revision, targets[0], adaptation,
        )
    assert direct_adaptation.value.code == "full_auto_session_owned"
    assert direct_adaptation.value.current_run["run_id"] == run_id

    adaptation_response = client.post(
        f"/api/sessions/{session_id}/plan/takes/{targets[0]}/adaptations",
        json={"plan_revision": revision, "adaptation": adaptation},
    )
    assert adaptation_response.status_code == 409, adaptation_response.text
    adaptation_detail = adaptation_response.json()["detail"]
    assert adaptation_detail["code"] == "full_auto_session_owned"
    assert "Full Auto owns this session" in adaptation_detail["message"]
    _safe_current(adaptation_detail, run_id)
    assert db.one(
        "SELECT 1 FROM take_resource_adaptation WHERE session_id = ?", session_id,
    ) is None

    plan = session_plan.get_draft(session_id)["plan"]
    with pytest.raises(resource_full_auto.FullAutoRunError) as direct_save:
        session_plan.save_draft(session_id, plan, revision)
    assert direct_save.value.code == "full_auto_session_owned"

    save_response = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": plan, "expected_revision": revision},
    )
    assert save_response.status_code == 409, save_response.text
    save_detail = save_response.json()["detail"]
    assert save_detail["code"] == "full_auto_session_owned"
    _safe_current(save_detail, run_id)
    assert session_plan.current_revision(session_id) == revision


def test_refresh_reads_without_drift_but_blocks_revision_change_while_owned(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = _active_run(client, seeded, monkeypatch)
    unchanged = client.post(
        f"/api/sessions/{session_id}/plan/refresh-resources",
        json={"expected_revision": revision},
    )
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["refreshed"] is False

    now = db.now()
    db.run(
        "INSERT INTO take_resource_adaptation "
        "(session_id, plan_revision, take_id, library_key, source_id, "
        "content_digest, resource_field, source_value, adapted_value, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        session_id, revision, targets[0], "test-library", "test-source",
        "test-digest", "scene_theme", "Old authorized source",
        "Reviewed replacement", now, now,
    )
    monkeypatch.setattr(
        resource_preparation,
        "diagnose_resource_refresh_drift",
        lambda *_args, **_kwargs: ["resource_dependency_changed"],
    )
    before_plan = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )
    before_adaptations = db.q(
        "SELECT * FROM take_resource_adaptation WHERE session_id = ? ORDER BY id",
        session_id,
    )

    with pytest.raises(resource_full_auto.FullAutoRunError) as direct:
        session_plan.refresh_resources(
            session_id, revision, planning_enabled=True,
        )
    assert direct.value.code == "full_auto_session_owned"
    assert direct.value.current_run["run_id"] == run_id

    response = client.post(
        f"/api/sessions/{session_id}/plan/refresh-resources",
        json={"expected_revision": revision},
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "full_auto_session_owned"
    _safe_current(detail, run_id)
    assert db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    ) == before_plan
    assert db.q(
        "SELECT * FROM take_resource_adaptation WHERE session_id = ? ORDER BY id",
        session_id,
    ) == before_adaptations


def test_manual_runner_retry_and_cancel_routes_cannot_mutate_owned_session(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets = _active_run(
        client, seeded, monkeypatch,
    )
    now = db.now()
    db.run(
        "INSERT INTO shot (session_id, shot_index, prompt, status, created_at) "
        "VALUES (?, 0, ?, 'failed', ?)",
        session_id, "A synthetic failed shot.", now,
    )
    db.run(
        "INSERT INTO shot (session_id, shot_index, prompt, status, created_at) "
        "VALUES (?, 1, ?, 'pending', ?)",
        session_id, "A synthetic pending shot.", now,
    )
    failed_shot = db.one(
        "SELECT id, status FROM shot WHERE session_id = ? AND status = 'failed'",
        session_id,
    )
    pending_shot = db.one(
        "SELECT id, status FROM shot WHERE session_id = ? AND status = 'pending'",
        session_id,
    )
    runner, _comfy = make_runner()

    with pytest.raises(resource_full_auto.FullAutoRunError) as direct_start:
        runner.start(session_id)
    assert direct_start.value.code == "full_auto_session_owned"
    assert runner._task is None

    with pytest.raises(resource_full_auto.FullAutoRunError) as direct_retry:
        runner.retry(session_id)
    assert direct_retry.value.code == "full_auto_session_owned"
    assert db.one(
        "SELECT id, status FROM shot WHERE id = ?", failed_shot["id"],
    ) == failed_shot

    with pytest.raises(resource_full_auto.FullAutoRunError) as direct_cancel:
        runner.cancel(session_id)
    assert direct_cancel.value.code == "full_auto_session_owned"
    assert session_id not in runner._cancel

    monkeypatch.setattr(main, "output_dir_ok", lambda: True)
    monkeypatch.setattr(main, "_require_mapped_choices", lambda _session_id: None)
    run = client.post(f"/api/sessions/{session_id}/run")
    assert run.status_code == 409, run.text
    run_detail = run.json()["detail"]
    assert run_detail["code"] == "full_auto_session_owned"
    _safe_current(run_detail, run_id)
    assert db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"] != "running"
    assert db.one(
        "SELECT id, status FROM shot WHERE id = ?", pending_shot["id"],
    ) == pending_shot

    retry = client.post(f"/api/sessions/{session_id}/retry")
    assert retry.status_code == 409, retry.text
    retry_detail = retry.json()["detail"]
    assert retry_detail["code"] == "full_auto_session_owned"
    _safe_current(retry_detail, run_id)
    assert db.one(
        "SELECT id, status FROM shot WHERE id = ?", failed_shot["id"],
    ) == failed_shot

    cancel = client.post(f"/api/sessions/{session_id}/cancel")
    assert cancel.status_code == 409, cancel.text
    cancel_detail = cancel.json()["detail"]
    assert cancel_detail["code"] == "full_auto_session_owned"
    _safe_current(cancel_detail, run_id)
    assert db.one(
        "SELECT id, status FROM shot WHERE id = ?", failed_shot["id"],
    ) == failed_shot


def test_pause_fences_assistant_child_and_authorizes_one_normal_plan_cas(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = _active_run(client, seeded, monkeypatch)
    parent_claim = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(parent_claim)
    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": "prepare_takes",
        "take_ids": targets,
    })
    operation_view, created = authoring_operations.start_operation(
        session_id,
        request,
        planning_enabled=True,
        assistant_available=True,
        _full_auto_claim=parent_claim,
    )
    assert created is True
    operation_claim = authoring_operations.load_worker_claim(
        session_id, operation_view["operation_id"],
        _full_auto_claim=parent_claim,
    )
    ticket = authoring_operations.renew_operation_lease(
        operation_claim, _full_auto_claim=parent_claim,
    )

    paused, edit_claim = resource_full_auto.pause_for_manual_edit(
        run_id, resource_full_auto.get_run(run_id)["revision"], revision,
    )
    assert paused["state"] == "blocked"
    assert paused["blocker_code"] == "manual_edit_pause"
    operation = db.one(
        "SELECT state, fencing_token, result_json FROM authoring_operation "
        "WHERE operation_id = ?",
        operation_view["operation_id"],
    )
    assert operation["state"] == "cancelled"
    assert operation["fencing_token"] == operation_claim.fencing_token + 1
    assert operation["result_json"] is None

    with pytest.raises(authoring_operations.AuthoringOperationError) as late_response:
        authoring_operations.persist_operation_response(
            operation_claim,
            ticket,
            [
                {"target": target, "result": {"camera": "Late response."}}
                for target in targets
            ],
            planning_enabled=True,
            _full_auto_claim=parent_claim,
        )
    assert late_response.value.code == "full_auto_owner_stale"
    assert db.one(
        "SELECT state, result_json FROM authoring_operation WHERE operation_id = ?",
        operation_view["operation_id"],
    ) == {"state": "cancelled", "result_json": None}

    plan = session_plan.get_draft(session_id)["plan"]
    forged = type(edit_claim)(
        **{**edit_claim.__dict__, "_signature": "forged"},
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as forged_error:
        session_plan.save_draft(
            session_id, plan, revision, _full_auto_edit_claim=forged,
        )
    assert forged_error.value.code == "full_auto_edit_authority_stale"

    saved = session_plan.save_draft(
        session_id, plan, revision, _full_auto_edit_claim=edit_claim,
    )
    assert saved["plan_revision"] == revision + 1
    assert resource_full_auto.get_run(run_id)["state"] == "blocked"
    assert resource_full_auto.get_run(run_id)["blocker_code"] == "manual_edit_pause"
    before_resume = db.one(
        "SELECT state, revision, plan_revision, fencing_token, blocker_code "
        "FROM full_auto_run WHERE run_id = ?",
        run_id,
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as stale_pause:
        resource_full_auto.pause_for_manual_edit(
            run_id, paused["revision"], revision,
        )
    assert stale_pause.value.code == "plan_revision_stale"
    assert db.one(
        "SELECT state, revision, plan_revision, fencing_token, blocker_code "
        "FROM full_auto_run WHERE run_id = ?",
        run_id,
    ) == before_resume
    with pytest.raises(resource_full_auto.FullAutoRunError) as changed_scope:
        resource_full_auto.resume_run(
            run_id, "resume-after-manual-edit", paused["revision"], revision,
            eligibility=None,
        )
    assert changed_scope.value.code == "plan_revision_stale"
    assert db.one(
        "SELECT state, revision, plan_revision, fencing_token, blocker_code "
        "FROM full_auto_run WHERE run_id = ?",
        run_id,
    ) == before_resume
    with pytest.raises(resource_full_auto.FullAutoRunError) as reused:
        session_plan.save_draft(
            session_id,
            session_plan.get_draft(session_id)["plan"],
            revision + 1,
            _full_auto_edit_claim=edit_claim,
        )
    assert reused.value.code == "full_auto_edit_authority_stale"


def test_pause_refuses_even_an_idle_session_with_pending_shots(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, _targets = _active_run(
        client, seeded, monkeypatch,
    )
    db.run(
        "INSERT INTO shot (session_id, shot_index, prompt, status, created_at) "
        "VALUES (?, 0, ?, 'pending', ?)",
        session_id, "A synthetic pending shot.", db.now(),
    )
    before = db.one(
        "SELECT state, revision, fencing_token FROM full_auto_run WHERE run_id = ?",
        run_id,
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as blocked:
        resource_full_auto.pause_for_manual_edit(run_id, 1, revision)
    assert blocked.value.code == "run_work_unsettled"
    assert db.one(
        "SELECT state, revision, fencing_token FROM full_auto_run WHERE run_id = ?",
        run_id,
    ) == before
