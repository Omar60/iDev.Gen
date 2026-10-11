"""Independent adversarial checks for Full Auto ownership boundaries."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import db
import main
import pytest
import resource_store
import session_plan
from backend import authoring_operations, resource_full_auto
from test_full_auto_preparation_coordinator import _create_run
from test_workflow_binding import (
    _prepare_approved_authoring_session,
)


def _active_run(client, seeded, monkeypatch, *, photo_count=4):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "independent-ownership-test")
    monkeypatch.setitem(main.CONFIG, "llm_key", "independent-ownership-test-key")
    return _create_run(client, seeded, photo_count=photo_count)


def _session_rows(session_id: int) -> dict:
    return {
        "plan": db.one("SELECT * FROM session_plan WHERE session_id = ?", session_id),
        "prepared": db.q(
            "SELECT * FROM prepared_take WHERE session_id = ? ORDER BY id", session_id,
        ),
        "operations": db.q(
            "SELECT * FROM authoring_operation WHERE session_id = ? ORDER BY rowid",
            session_id,
        ),
        "approval": db.one(
            "SELECT * FROM session_plan_approval WHERE session_id = ?", session_id,
        ),
        "shots": db.q("SELECT * FROM shot WHERE session_id = ? ORDER BY id", session_id),
        "adaptations": db.q(
            "SELECT * FROM take_resource_adaptation WHERE session_id = ? ORDER BY id",
            session_id,
        ),
    }


def _assert_safe_owner_error(detail: dict, run_id: str, *, state="active"):
    assert detail["code"] == "full_auto_session_owned"
    current = detail["current"]
    assert current["run_id"] == run_id
    assert current["state"] == state
    assert not {
        "lease_expires_at", "lease_started_at", "fencing_token", "claim",
        "_signature", "authorization_json",
    }.intersection(current)
    assert "private" not in json.dumps(detail).lower()


def test_prepare_suggest_approve_and_submit_are_blocked_directly_and_over_http(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = _active_run(
        client, seeded, monkeypatch,
    )
    now = db.now()
    db.run(
        "INSERT INTO prepared_take "
        "(session_id, plan_revision, take_id, final_prompt, effective_state, "
        "mapping_version, compiler_version, provenance, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '{}', ?, ?, '{}', 'ready', ?, ?)",
        session_id, revision, targets[0], "Synthetic ready snapshot.",
        "verification-v1", "verification-v1", now, now,
    )

    before = _session_rows(session_id)
    with pytest.raises(resource_full_auto.FullAutoRunError) as prepare:
        session_plan.begin_preparation(session_id, revision, targets[1])
    assert prepare.value.code == "full_auto_session_owned"

    for kind in ("prepare_takes", "shared_suggestions"):
        payload = {
            "request_id": str(uuid.uuid4()),
            "expected_revision": revision,
            "kind": kind,
        }
        if kind == "prepare_takes":
            payload["take_ids"] = [targets[1]]
        request = authoring_operations.normalize_start_request(payload)
        with pytest.raises(authoring_operations.AuthoringOperationError) as refused:
            authoring_operations.start_operation(
                session_id, request,
                planning_enabled=True, assistant_available=True,
            )
        assert refused.value.code == "full_auto_session_owned"

        response = client.post(
            f"/api/sessions/{session_id}/plan/authoring/operations", json=payload,
        )
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        _assert_safe_owner_error(detail, run_id)

    with pytest.raises(resource_full_auto.FullAutoRunError) as approve:
        session_plan.approve_plan_review(session_id, revision)
    assert approve.value.code == "full_auto_session_owned"
    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approved.status_code == 409, approved.text
    _assert_safe_owner_error(approved.json()["detail"], run_id)

    with pytest.raises(resource_full_auto.FullAutoRunError) as submit:
        session_plan.submit_prepared_take(session_id, revision, targets[0])
    assert submit.value.code == "full_auto_session_owned"
    submitted = client.post(
        f"/api/sessions/{session_id}/plan/preparations/submit-selected",
        json={"plan_revision": revision, "take_ids": [targets[0]]},
    )
    assert submitted.status_code == 409, submitted.text
    _assert_safe_owner_error(submitted.json()["detail"], run_id)

    forged_payload = {
        "request_id": str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": "prepare_takes",
        "take_ids": [targets[1]],
        "_full_auto_claim": {"run_id": run_id, "fencing_token": 1},
    }
    forged = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=forged_payload,
    )
    assert forged.status_code == 422, forged.text
    assert forged.json()["detail"]["code"] == "extra_field_forbidden"
    assert _session_rows(session_id) == before
    run = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert run["state"] == "active" and run["phase"] == "prepare"


def test_owned_capability_is_session_phase_target_and_fence_bound(
    client, seeded, monkeypatch,
):
    first_session, first_run, first_revision, first_targets = _active_run(
        client, seeded, monkeypatch, photo_count=21,
    )
    second_session, _second_run, second_revision, second_targets = _active_run(
        client, seeded, monkeypatch,
    )
    owner = resource_full_auto.claim_run(first_run)
    resource_full_auto.initialize_prepare_progress(owner)
    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": first_revision,
        "kind": "prepare_takes",
        "take_ids": first_targets[:20],
    })
    operation, created = authoring_operations.start_operation(
        first_session, request, planning_enabled=True, assistant_available=True,
        _full_auto_claim=owner,
    )
    assert created is True
    before = _session_rows(first_session)

    with pytest.raises(resource_full_auto.FullAutoRunError) as wrong_session:
        session_plan.begin_preparation(
            second_session, second_revision, second_targets[0],
            _full_auto_claim=owner,
            _full_auto_operation_id=operation["operation_id"],
        )
    assert wrong_session.value.code == "run_owner_stale"

    with pytest.raises(resource_full_auto.FullAutoRunError) as wrong_phase:
        session_plan.approve_plan_review(
            first_session, first_revision, _full_auto_claim=owner,
        )
    assert wrong_phase.value.code == "run_owner_stale"

    with pytest.raises(resource_full_auto.FullAutoRunError) as wrong_target:
        session_plan.begin_preparation(
            first_session, first_revision, first_targets[20],
            _full_auto_claim=owner,
            _full_auto_operation_id=operation["operation_id"],
        )
    assert wrong_target.value.code == "run_target_not_owned"

    forged = type(owner)(**{**owner.__dict__, "_signature": "forged"})
    with pytest.raises(resource_full_auto.FullAutoRunError) as forged_owner:
        session_plan.begin_preparation(
            first_session, first_revision, first_targets[0],
            _full_auto_claim=forged,
            _full_auto_operation_id=operation["operation_id"],
        )
    assert forged_owner.value.code == "run_owner_stale"

    resource_full_auto.advance_run(owner, "blocked", blocker_code="verification_fence")
    with pytest.raises(resource_full_auto.FullAutoRunError) as stale_owner:
        session_plan.begin_preparation(
            first_session, first_revision, first_targets[0],
            _full_auto_claim=owner,
            _full_auto_operation_id=operation["operation_id"],
        )
    assert stale_owner.value.code == "run_owner_stale"
    assert _session_rows(first_session) == before
    assert db.q(
        "SELECT * FROM prepared_take WHERE session_id IN (?, ?) ORDER BY id",
        first_session, second_session,
    ) == []
    current = resource_full_auto.get_run(first_run)
    assert current["state"] == "blocked"
    assert db.one(
        "SELECT fencing_token FROM full_auto_run WHERE run_id = ?", first_run,
    )["fencing_token"] == owner.fencing_token + 1


def test_reapproval_rechecks_current_resource_evidence_through_service_and_http(
    client, seeded,
):
    session_id = _prepare_approved_authoring_session(client, seeded)
    plan_row = db.one(
        "SELECT plan_json FROM session_plan WHERE session_id = ?", session_id,
    )
    plan = json.loads(plan_row["plan_json"])
    selected = plan["selected_resources"][0]
    library = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        selected["library_key"],
    )
    revision = resource_store.get_revision(
        library_id=library["id"],
        source_id=selected["source_id"],
        content_digest=selected["content_digest"],
    )
    assert revision is not None
    changed_translation = dict(revision["translation"])
    changed_translation["scene_theme"] = "A changed live resource description."
    db.run(
        "UPDATE asset_revision SET translation = ? WHERE id = ?",
        json.dumps(changed_translation, ensure_ascii=False), revision["id"],
    )

    before = {
        "plan": db.one("SELECT * FROM session_plan WHERE session_id = ?", session_id),
        "approval": db.one(
            "SELECT * FROM session_plan_approval WHERE session_id = ?", session_id,
        ),
        "prepared": db.q(
            "SELECT * FROM prepared_take WHERE session_id = ? ORDER BY id", session_id,
        ),
        "shots": db.q("SELECT * FROM shot WHERE session_id = ? ORDER BY id", session_id),
    }
    with pytest.raises(session_plan.AuthoringEvidenceInvalid):
        session_plan.approve_plan_review(session_id, 1)
    response = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": 1},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == session_plan.AUTHORING_EVIDENCE_PUBLIC_MESSAGE
    assert {
        "plan": db.one("SELECT * FROM session_plan WHERE session_id = ?", session_id),
        "approval": db.one(
            "SELECT * FROM session_plan_approval WHERE session_id = ?", session_id,
        ),
        "prepared": db.q(
            "SELECT * FROM prepared_take WHERE session_id = ? ORDER BY id", session_id,
        ),
        "shots": db.q("SELECT * FROM shot WHERE session_id = ? ORDER BY id", session_id),
    } == before
    assert db.one(
        "SELECT run_id FROM full_auto_run WHERE session_id = ?", session_id,
    ) is None


def _iso_after(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat(timespec="seconds")


def test_manual_edit_racing_pending_transfer_cancel_never_crosses_the_cas_fence(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, _targets = _active_run(
        client, seeded, monkeypatch,
    )
    plan = session_plan.get_draft(session_id)["plan"]
    plan_before = db.one(
        "SELECT * FROM session_plan WHERE session_id = ?", session_id,
    )
    barrier = threading.Barrier(2)

    def manual_edit():
        barrier.wait(timeout=5)
        try:
            session_plan.save_draft(session_id, plan, revision)
        except resource_full_auto.FullAutoRunError as exc:
            return exc.code
        return "committed"

    def transfer_cancel():
        barrier.wait(timeout=5)
        result = resource_full_auto.cancel_run(
            run_id, "racing-manual-transfer", 1, revision,
            work_pending=True, now_text=db.now(),
        )
        return result.view

    with ThreadPoolExecutor(max_workers=2) as pool:
        edit_future = pool.submit(manual_edit)
        cancel_future = pool.submit(transfer_cancel)
        edit_result = edit_future.result(timeout=15)
        cancel_result = cancel_future.result(timeout=15)

    assert edit_result == "full_auto_session_owned"
    assert cancel_result["state"] == "cancel_requested"
    assert cancel_result["cancel_intent"] is True
    assert db.one(
        "SELECT * FROM session_plan WHERE session_id = ?", session_id,
    ) == plan_before
    run_row = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert run_row["state"] == "cancel_requested"
    assert run_row["fencing_token"] == 2
    assert db.q(
        "SELECT request_id, action FROM full_auto_run_action_receipt "
        "WHERE run_id = ? ORDER BY rowid", run_id,
    ) == [{"request_id": "racing-manual-transfer", "action": "cancel"}]


def test_manual_transfer_outage_keeps_owner_until_controlled_fake_observes_settlement(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, revision, targets = _active_run(
        client, seeded, monkeypatch,
    )
    shot_id = db.run(
        "INSERT INTO shot (session_id, shot_index, prompt, status, created_at) "
        "VALUES (?, 0, ?, 'pending', ?)",
        session_id, "Synthetic pending transfer shot.", db.now(),
    )
    runner, comfy_fake = make_runner()
    cancel_at = db.now()
    cancel = resource_full_auto.cancel_run(
        run_id, "independent-manual-transfer", 1, revision,
        work_pending=True, now_text=cancel_at,
    )
    assert cancel.view["state"] == "cancel_requested"
    claim = resource_full_auto.claim_run(
        run_id, now_text=_iso_after(cancel_at, 1),
    )

    class ControlledFakeBackendObservation:
        """A local fake attempt ledger; no ComfyUI request is made by this test."""

        observations = ["unavailable", "completed"]

        @classmethod
        def observe(cls, *, shot_id: int, observed_at: str):
            status = cls.observations.pop(0)
            if status == "completed":
                db.run(
                    "UPDATE shot SET status = 'done', filename = ?, finished_at = ? "
                    "WHERE id = ? AND status = 'pending'",
                    "synthetic-retained/output.png", observed_at, shot_id,
                )
            return {
                "status": status,
                "shot": db.one("SELECT * FROM shot WHERE id = ?", shot_id),
            }

    def evidence_from_fake_observation(observed_at: str):
        observed = ControlledFakeBackendObservation.observe(
            shot_id=shot_id, observed_at=observed_at,
        )
        if observed["status"] == "unavailable":
            assert observed["shot"]["status"] == "pending"
            return resource_full_auto.CancellationEvidence(
                "unknown", observed_at, 1,
                blocker_code="comfy_unavailable",
                attempt_ids=("synthetic-attempt",), shot_ids=(str(shot_id),),
            )
        assert observed["status"] == "completed"
        assert observed["shot"]["status"] == "done"
        assert observed["shot"]["filename"] == "synthetic-retained/output.png"
        return resource_full_auto.CancellationEvidence(
            "settled", observed_at, 0,
            attempt_ids=("synthetic-attempt",),
            shot_ids=(str(shot_id),),
        )

    unknown_at = _iso_after(cancel_at, 2)
    blocked = resource_full_auto.reconcile_cancellation(
        claim, evidence_from_fake_observation(unknown_at), now_text=unknown_at,
    )
    assert blocked["state"] == "blocked" and blocked["cancel_intent"] is True
    with pytest.raises(resource_full_auto.FullAutoRunError) as still_owned:
        resource_full_auto.require_session_mutation_allowed(session_id)
    assert still_owned.value.code == "full_auto_session_owned"

    before_refused_mutations = {
        "run": db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id),
        "plan": db.one("SELECT * FROM session_plan WHERE session_id = ?", session_id),
        "shots": db.q("SELECT * FROM shot WHERE session_id = ? ORDER BY id", session_id),
        "prepared": db.q(
            "SELECT * FROM prepared_take WHERE session_id = ? ORDER BY id", session_id,
        ),
    }
    with pytest.raises(resource_full_auto.FullAutoRunError) as retry:
        runner.retry(session_id)
    assert retry.value.code == "full_auto_session_owned"
    response = client.post(f"/api/sessions/{session_id}/retry")
    assert response.status_code == 409, response.text
    _assert_safe_owner_error(response.json()["detail"], run_id, state="blocked")
    assert {
        "run": db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id),
        "plan": db.one("SELECT * FROM session_plan WHERE session_id = ?", session_id),
        "shots": db.q("SELECT * FROM shot WHERE session_id = ? ORDER BY id", session_id),
        "prepared": db.q(
            "SELECT * FROM prepared_take WHERE session_id = ? ORDER BY id", session_id,
        ),
    } == before_refused_mutations

    reconcile_at = _iso_after(cancel_at, 3)
    read_only = resource_full_auto.resume_run(
        run_id, "independent-cancel-reconcile", blocked["revision"], revision,
        eligibility=None, now_text=reconcile_at,
    )
    assert read_only.view["state"] == "blocked"
    assert read_only.claim is not None and read_only.claim.requires_reconciliation
    settled_at = _iso_after(cancel_at, 4)
    settled = resource_full_auto.reconcile_cancellation(
        read_only.claim, evidence_from_fake_observation(settled_at),
        now_text=settled_at,
    )
    assert settled["state"] == "cancelled" and settled["cancel_intent"] is True
    assert resource_full_auto.require_session_mutation_allowed(session_id) is None
    assert runner._task is None
    settled_shot = db.one("SELECT status, filename FROM shot WHERE id = ?", shot_id)
    assert settled_shot == {
        "status": "done", "filename": "synthetic-retained/output.png",
    }
    assert comfy_fake.attempts == 0 and comfy_fake.graphs == []
    assert resource_full_auto.get_run(run_id)["state"] == "cancelled"
