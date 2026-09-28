from __future__ import annotations

import pytest

import db
import main
from backend import authoring_operations, resource_preparation, session_plan
from test_authoring_operation_api import (
    _configure_assistant,
    _create_guided_session,
    _enable_background_suggestion_dispatch,
    _install_fake_structured_assistant,
    _start_body,
    _wait_for_operation_state,
    _context_from_call,
)


def test_reuse_response_revalidates_snapshot_inside_fenced_persistence(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, photo_count=1)
    calls = _install_fake_structured_assistant(monkeypatch, [{
        "camera": "eye-level framing",
        "framing": "full body",
        "pose": "standing beside a window",
        "expression": "calm",
    }])

    first = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=revision, take_ids=["take-001"]),
    )
    assert first.status_code == 202, first.text
    first_id = first.json()["operation_id"]
    assert _wait_for_operation_state(
        client, session_id, first_id, "succeeded",
    )["state"] == "succeeded"
    assert calls and db.one(
        "SELECT status FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, revision, "take-001",
    )["status"] == session_plan.PREPARED_TAKE_STATUS_READY

    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", lambda view: None)
    second = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=revision, take_ids=["take-001"]),
    )
    assert second.status_code == 202, second.text
    claim = authoring_operations.load_worker_claim(session_id, second.json()["operation_id"])
    ticket = authoring_operations.renew_operation_lease(claim)
    row = session_plan._prepared_take_row(session_id, revision, "take-001")
    cache = {}

    resource_preparation.validate_authoring_prepared_evidence(
        session_id, revision, "take-001", row=row,
        _predecessor_validation_cache=cache,
    )
    db.run("UPDATE prepared_take SET provenance = '{}' WHERE id = ?", row["id"])
    operation_before = db.one(
        "SELECT state, fencing_token, completed_json, remaining_json, result_json "
        "FROM authoring_operation WHERE operation_id = ?",
        claim.operation_id,
    )

    with pytest.raises(session_plan.AuthoringEvidenceInvalid):
        authoring_operations.persist_operation_response(
            claim, ticket, [{"target": "take-001", "result": {}}],
            reuse_take_snapshot=True,
            _predecessor_validation_cache=cache,
        )

    assert db.one(
        "SELECT state, fencing_token, completed_json, remaining_json, result_json "
        "FROM authoring_operation WHERE operation_id = ?",
        claim.operation_id,
    ) == operation_before
    assert db.one(
        "SELECT status, provenance FROM prepared_take WHERE id = ?", row["id"],
    ) == {"status": session_plan.PREPARED_TAKE_STATUS_READY, "provenance": "{}"}


def test_partial_prepare_failure_resumes_without_reauthoring_completed_take(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, photo_count=3)

    def output(index):
        return {
            "camera": f"camera choice {index}",
            "framing": f"framing choice {index}",
            "pose": f"pose choice {index}",
            "expression": f"expression choice {index}",
        }

    calls = _install_fake_structured_assistant(monkeypatch, [
        output(1),
        {"camera": "incomplete", "framing": "full body", "pose": "standing"},
        output(2),
        output(3),
    ])
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(
            revision=revision,
            take_ids=["take-001", "take-002", "take-003"],
        ),
    )
    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]
    failed = _wait_for_operation_state(client, session_id, operation_id, "failed")
    assert failed["progress"]["requested"] == ["take-001", "take-002", "take-003"]
    assert failed["progress"]["completed"] == ["take-001"]
    assert failed["progress"]["failed"]["take_id"] == "take-002"
    assert isinstance(failed["progress"]["failed"]["error"], str)
    assert failed["progress"]["failed"]["error"]
    assert failed["progress"]["remaining"] == ["take-003"]
    assert [item["target"] for item in failed["result"]["items"]] == ["take-001"]
    first_snapshot = db.one(
        "SELECT id, final_prompt, effective_state, mapping_version, compiler_version, "
        "provenance, status, linked_shot_id, created_at, updated_at "
        "FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, revision, "take-001",
    )
    assert first_snapshot["status"] == session_plan.PREPARED_TAKE_STATUS_READY
    assert db.one(
        "SELECT id FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, revision, "take-002",
    ) is None
    assert [_context_from_call(call)["take_id"] for call in calls] == [
        "take-001", "take-002",
    ]

    resumed = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}/resume",
        json={"expected_revision": revision},
    )
    assert resumed.status_code == 202, resumed.text
    completed = _wait_for_operation_state(client, session_id, operation_id, "succeeded")
    assert completed["progress"] == {
        "requested": ["take-001", "take-002", "take-003"],
        "completed": ["take-001", "take-002", "take-003"],
        "failed": None,
        "remaining": [],
    }
    assert [_context_from_call(call)["take_id"] for call in calls] == [
        "take-001", "take-002", "take-002", "take-003",
    ]
    assert db.one(
        "SELECT id, final_prompt, effective_state, mapping_version, compiler_version, "
        "provenance, status, linked_shot_id, created_at, updated_at "
        "FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, revision, "take-001",
    ) == first_snapshot
    assert db.one(
        "SELECT COUNT(*) AS n FROM prepared_take "
        "WHERE session_id = ? AND plan_revision = ? AND status = ?",
        session_id, revision, session_plan.PREPARED_TAKE_STATUS_READY,
    )["n"] == 3
