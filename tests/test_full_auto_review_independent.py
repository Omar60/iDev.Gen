"""Independent adversarial checks for Full Auto review boundaries (Task 4.4)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest

import db
import main
from backend import resource_full_auto, session_plan
import test_full_auto_preparation_coordinator as preparation


@pytest.fixture(autouse=True)
def _configured_assistant(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "task44-independent-test")
    monkeypatch.setitem(main.CONFIG, "llm_key", "synthetic-task44-key")


def _prepared_run(client, seeded, monkeypatch, *, photo_count=4):
    session_id, run_id, revision, targets = preparation._create_run(
        client, seeded, photo_count=photo_count,
    )
    owner = preparation._prepare_owned_batch(
        session_id, run_id, revision, targets, monkeypatch,
    )
    owner = resource_full_auto.renew_run_claim(owner)
    resource_full_auto.advance_run(owner, "active", phase="validate")
    return session_id, run_id, revision, targets, resource_full_auto.renew_run_claim(owner)


def _prepared_duplicate_run(client, seeded, monkeypatch):
    session_id, run_id, revision, targets = preparation._create_run(
        client, seeded, photo_count=4,
    )

    def duplicate_response(request, pending, calls, transaction_depths, fields_by_take, fail_take):
        assert request.url.path.endswith("/chat/completions")
        assert pending
        take_id = pending.pop(0)
        calls.append(take_id)
        transaction_depths.append(db._tx_depth)
        return json.dumps({field: f"same {field}" for field in fields_by_take[take_id]})

    monkeypatch.setattr(preparation, "_response_for_request", duplicate_response)
    owner = preparation._prepare_owned_batch(
        session_id, run_id, revision, targets, monkeypatch,
    )
    owner = resource_full_auto.renew_run_claim(owner)
    resource_full_auto.advance_run(owner, "active", phase="validate")
    return session_id, run_id, revision, targets, resource_full_auto.renew_run_claim(owner)


def _database_snapshot():
    snapshot = {}
    for item in db.q("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"):
        name = item["name"]
        snapshot[name] = db.q(f'SELECT * FROM "{name}"')
    return snapshot


def _one_second_after(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=1)).isoformat(timespec="seconds")


@pytest.mark.parametrize("malformation", ["bool", "float", "string", "extra"])
def test_duplicate_review_http_contract_rejects_coercion_and_extra_fields_without_writes(
    client, seeded, monkeypatch, malformation,
):
    session_id, _run_id, revision, _targets, _owner = _prepared_duplicate_run(
        client, seeded, monkeypatch,
    )
    review = client.get(f"/api/sessions/{session_id}/plan/review").json()[
        "full_auto_duplicate_review"
    ]
    assert review["state"] == "duplicate_review_required"
    payload = {
        "plan_revision": revision,
        "review_digest": review["review_digest"],
    }
    if malformation == "bool":
        payload["plan_revision"] = True
    elif malformation == "float":
        payload["plan_revision"] = float(revision)
    elif malformation == "string":
        payload["plan_revision"] = str(revision)
    else:
        payload["unexpected"] = "must be rejected"

    before = _database_snapshot()
    response = client.post(
        f"/api/sessions/{session_id}/plan/review/duplicates/accept",
        json=payload,
    )

    assert response.status_code == 422, response.text
    assert _database_snapshot() == before
    assert db.one(
        "SELECT run_id FROM full_auto_duplicate_review_acceptance WHERE session_id = ?",
        session_id,
    ) is None


def test_policy_approval_stays_distinct_across_cancel_and_manual_transfer_flag_gate(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, _targets, owner = _prepared_run(
        client, seeded, monkeypatch,
    )
    current_revision, plan = session_plan._load_current_resource_plan(session_id)
    assert current_revision == revision
    notices = plan.get("conflicts", [])
    assert notices
    assert all(
        item.get("kind") == "resource_descriptive_vs_plan_constants"
        for item in notices
    )
    approved, submit_owner = main._run_full_auto_review_and_approve(owner)
    assert approved["phase"] == "submit"
    assert submit_owner is not None
    policy_before = db.one(
        "SELECT * FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    )
    assert policy_before is not None
    assert json.loads(policy_before["evidence_json"])["approval_kind"] == "policy"
    assert db.one(
        "SELECT session_id FROM session_plan_approval WHERE session_id = ?", session_id,
    ) is None

    run = db.one("SELECT revision, plan_revision FROM full_auto_run WHERE run_id = ?", run_id)
    cancelled = resource_full_auto.cancel_run(
        run_id, str(uuid.uuid4()), run["revision"], run["plan_revision"],
        work_pending=False,
    )
    assert cancelled.view["state"] == "cancelled"

    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    blocked = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert blocked.status_code == 503
    assert db.one(
        "SELECT session_id FROM session_plan_approval WHERE session_id = ?", session_id,
    ) is None
    assert db.one(
        "SELECT * FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    ) == policy_before

    # An explicit later manual approval after transfer remains a separate row.
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    manual = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert manual.status_code == 200, manual.text
    assert db.one(
        "SELECT plan_revision FROM session_plan_approval WHERE session_id = ?", session_id,
    )["plan_revision"] == revision
    assert json.loads(db.one(
        "SELECT evidence_json FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    )["evidence_json"])["approval_kind"] == "policy"

    # A separately recorded human approval retains the existing manual-submit
    # behavior if the planning flag changes after that explicit approval.
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    manually_submitted = session_plan.submit_prepared_take(
        session_id, revision, _targets[0],
    )
    assert isinstance(manually_submitted["shot_id"], int)
    assert db.one(
        "SELECT id FROM shot WHERE id = ?", manually_submitted["shot_id"],
    ) is not None


def test_public_manual_approval_and_submission_routes_cannot_bypass_full_auto_gate(
    client, seeded, monkeypatch,
):
    session_id, _run_id, revision, targets, _owner = _prepared_duplicate_run(
        client, seeded, monkeypatch,
    )
    before = _database_snapshot()

    responses = [
        client.post(
            f"/api/sessions/{session_id}/plan/review/approve",
            json={"plan_revision": revision},
        ),
        client.post(
            f"/api/sessions/{session_id}/plan/preparations/submit",
            json={"plan_revision": revision, "take_id": targets[0]},
        ),
        client.post(
            f"/api/sessions/{session_id}/plan/preparations/submit-selected",
            json={"plan_revision": revision, "take_ids": targets[:2]},
        ),
    ]

    assert [response.status_code for response in responses] == [409, 409, 409]
    assert _database_snapshot() == before
    assert db.one(
        "SELECT run_id FROM full_auto_plan_approval WHERE session_id = ?", session_id,
    ) is None
    assert db.one(
        "SELECT session_id FROM session_plan_approval WHERE session_id = ?", session_id,
    ) is None
    assert db.q("SELECT id FROM shot WHERE session_id = ?", session_id) == []


def test_full_auto_linked_submission_replay_returns_same_shot_without_any_database_write(
    client, seeded, monkeypatch,
):
    session_id, _run_id, revision, targets, owner = _prepared_run(
        client, seeded, monkeypatch,
    )
    _view, owner = main._run_full_auto_review_and_approve(owner)
    assert owner is not None
    owner = resource_full_auto.renew_run_claim(owner)
    first = session_plan.submit_prepared_take(
        session_id, revision, targets[0], _full_auto_claim=owner,
    )
    before = _database_snapshot()
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)

    replay = session_plan.submit_prepared_take(
        session_id, revision, targets[0], _full_auto_claim=owner,
    )

    assert replay["shot_id"] == first["shot_id"]
    assert _database_snapshot() == before


def test_full_auto_submission_rechecks_real_workflow_drift_after_policy_approval(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets, owner = _prepared_run(
        client, seeded, monkeypatch,
    )
    _view, submit_owner = main._run_full_auto_review_and_approve(owner)
    assert submit_owner is not None
    approval = db.one(
        "SELECT * FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    )
    assert approval is not None

    session = db.one("SELECT workflow_id FROM session WHERE id = ?", session_id)
    assert session is not None and session["workflow_id"] is not None
    workflow_response = client.get(f"/api/workflows/{session['workflow_id']}")
    assert workflow_response.status_code == 200, workflow_response.text
    workflow = workflow_response.json()
    node_id = next(iter(workflow["graph"]))
    workflow["graph"][node_id]["class_type"] += "Task44Drift"
    changed = client.patch(
        f"/api/workflows/{session['workflow_id']}",
        json={
            "name": workflow["name"],
            "graph": workflow["graph"],
            "node_map": workflow["node_map"],
            "kind": workflow["kind"],
        },
    )
    assert changed.status_code == 200, changed.text

    # Renewing the run claim intentionally advances its lease fields. Take the
    # all-row baseline after that control write so it measures only submission.
    live_owner = resource_full_auto.renew_run_claim(submit_owner)
    before = _database_snapshot()
    with pytest.raises(resource_full_auto.FullAutoRunError) as stale:
        session_plan.submit_prepared_take(
            session_id, revision, targets[0], _full_auto_claim=live_owner,
        )

    assert stale.value.code == "workflow_changed"
    assert _database_snapshot() == before
    assert db.one(
        "SELECT * FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    ) == approval
    assert db.q("SELECT id FROM shot WHERE session_id = ?", session_id) == []


def test_full_auto_submission_refuses_when_any_target_is_missing_after_approval_without_writes(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets, owner = _prepared_run(
        client, seeded, monkeypatch,
    )
    _view, submit_owner = main._run_full_auto_review_and_approve(owner)
    assert submit_owner is not None
    approval = db.one(
        "SELECT * FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    )
    assert approval is not None

    # Model an actual target disappearing from the ready set after approval;
    # submitting another otherwise-ready target must recheck the whole set.
    db.run(
        "UPDATE prepared_take SET status = 'invalidated' "
        "WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, revision, targets[-1],
    )
    before = _database_snapshot()
    live_owner = resource_full_auto.renew_run_claim(submit_owner)
    with pytest.raises(resource_full_auto.FullAutoRunError) as missing:
        session_plan.submit_prepared_take(
            session_id, revision, targets[0], _full_auto_claim=live_owner,
        )

    assert missing.value.code == "full_auto_target_not_ready"
    assert _database_snapshot() == before
    assert db.one(
        "SELECT * FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    ) == approval
    assert db.q("SELECT id FROM shot WHERE session_id = ?", session_id) == []


def test_disabled_resource_planning_flag_blocks_full_auto_submit_service_without_writes(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets, owner = _prepared_run(
        client, seeded, monkeypatch,
    )
    _view, submit_owner = main._run_full_auto_review_and_approve(owner)
    assert submit_owner is not None
    assert db.one(
        "SELECT run_id FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    ) is not None

    live_owner = resource_full_auto.renew_run_claim(submit_owner)
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    before = _database_snapshot()
    try:
        session_plan.submit_prepared_take(
            session_id, revision, targets[0], _full_auto_claim=live_owner,
        )
    except session_plan.ResourcePlanningDisabled:
        pass
    else:
        after = _database_snapshot()
        changed_tables = [
            name for name in before
            if before[name] != after.get(name)
        ]
        pytest.fail(
            "Full Auto submission succeeded while resource planning was disabled; "
            f"changed tables: {changed_tables}"
        )

    assert _database_snapshot() == before


def test_duplicate_acceptance_is_invalidated_by_normal_plan_cas_edit(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, _targets, _owner = _prepared_duplicate_run(
        client, seeded, monkeypatch,
    )
    review = client.get(f"/api/sessions/{session_id}/plan/review").json()[
        "full_auto_duplicate_review"
    ]
    assert review["state"] == "duplicate_review_required"
    accepted = client.post(
        f"/api/sessions/{session_id}/plan/review/duplicates/accept",
        json={"plan_revision": revision, "review_digest": review["review_digest"]},
    )
    assert accepted.status_code == 200, accepted.text
    acceptance = db.one(
        "SELECT * FROM full_auto_duplicate_review_acceptance WHERE run_id = ?",
        run_id,
    )
    assert acceptance is not None
    assert acceptance["plan_revision"] == revision
    assert acceptance["target_digest"] == review["target_digest"]
    assert acceptance["snapshot_digest"] == review["snapshot_digest"]
    assert acceptance["duplicate_set_digest"] == review["duplicate_set_digest"]
    assert acceptance["review_digest"] == review["review_digest"]

    run = db.one(
        "SELECT revision, plan_revision FROM full_auto_run WHERE run_id = ?", run_id,
    )
    cancelled = resource_full_auto.cancel_run(
        run_id, str(uuid.uuid4()), run["revision"], run["plan_revision"],
        work_pending=False,
    )
    assert cancelled.view["state"] == "cancelled"

    current_revision, plan = session_plan._load_current_resource_plan(session_id)
    assert current_revision == revision
    plan["look"] += " with an explicit user edit"
    edited = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"expected_revision": revision, "plan": plan},
    )
    assert edited.status_code == 200, edited.text
    new_revision = edited.json()["plan_revision"]
    assert new_revision == revision + 1
    assert db.one(
        "SELECT run_id FROM full_auto_duplicate_review_acceptance WHERE run_id = ?",
        run_id,
    ) is None

    after_edit = session_plan.get_full_auto_duplicate_review_state(
        session_id, new_revision,
    )
    assert after_edit["plan_revision"] == new_revision
    assert after_edit["duplicate_acceptance_current"] is False
    assert after_edit["state"] != "ready"


@pytest.mark.parametrize(
    ("boundary", "expiry"),
    [
        ("approval", "lease"),
        ("approval", "authorization"),
        ("submission", "lease"),
        ("submission", "authorization"),
        ("duplicate_acceptance", "authorization"),
    ],
)
def test_full_auto_rechecks_expiry_after_aggregate_review_before_write(
    client, seeded, monkeypatch, boundary, expiry,
):
    if boundary == "duplicate_acceptance":
        session_id, run_id, revision, _targets, _owner = _prepared_duplicate_run(
            client, seeded, monkeypatch,
        )
        review = client.get(f"/api/sessions/{session_id}/plan/review").json()[
            "full_auto_duplicate_review"
        ]
        assert review["state"] == "duplicate_review_required"
        action = lambda: session_plan.accept_full_auto_duplicate_review(
            session_id, revision, review["review_digest"],
        )
    else:
        session_id, run_id, revision, targets, owner = _prepared_run(
            client, seeded, monkeypatch,
        )
        if boundary == "approval":
            resource_full_auto.advance_run(owner, "active", phase="approve")
            owner = resource_full_auto.renew_run_claim(owner)
            action = lambda: session_plan.approve_plan_review(
                session_id, revision, _full_auto_claim=owner,
            )
        else:
            _view, owner = main._run_full_auto_review_and_approve(owner)
            assert owner is not None
            action = lambda: session_plan.submit_prepared_take(
                session_id, revision, targets[0], _full_auto_claim=owner,
            )

    if boundary != "duplicate_acceptance" and expiry == "authorization":
        monkeypatch.setattr(
            resource_full_auto, "RUN_LEASE", timedelta(days=8),
        )
        owner = resource_full_auto.renew_run_claim(owner)
        if boundary == "approval":
            action = lambda: session_plan.approve_plan_review(
                session_id, revision, _full_auto_claim=owner,
            )
        else:
            action = lambda: session_plan.submit_prepared_take(
                session_id, revision, targets[0], _full_auto_claim=owner,
            )

    run = db.one("SELECT lease_expires_at FROM full_auto_run WHERE run_id = ?", run_id)
    receipt = db.one(
        "SELECT authorization_expires_at FROM full_auto_run_receipt WHERE run_id = ?",
        run_id,
    )
    assert receipt is not None
    if expiry == "lease":
        assert run["lease_expires_at"] is not None
        expires_after_review = _one_second_after(run["lease_expires_at"])
        expected_code = "run_lease_expired"
    else:
        expires_after_review = _one_second_after(receipt["authorization_expires_at"])
        if boundary != "duplicate_acceptance":
            assert run["lease_expires_at"] > expires_after_review
        expected_code = "authorization_expired"

    clock = {"value": db.now()}
    monkeypatch.setattr(db, "now", lambda: clock["value"])
    original_review = session_plan._full_auto_duplicate_review_evidence

    def expire_after_current_aggregate(*args, **kwargs):
        current = original_review(*args, **kwargs)
        clock["value"] = expires_after_review
        return current

    monkeypatch.setattr(
        session_plan, "_full_auto_duplicate_review_evidence",
        expire_after_current_aggregate,
    )
    policy_before = db.one(
        "SELECT * FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    )
    before = _database_snapshot()

    try:
        action()
    except resource_full_auto.FullAutoRunError as exc:
        refused = exc
    else:
        after = _database_snapshot()
        changed_tables = [
            table for table, rows in before.items() if after.get(table) != rows
        ]
        pytest.fail(
            f"expected {expected_code} after aggregate review crossed expiry; "
            f"action returned successfully; changed tables={changed_tables}"
        )

    assert refused.code == expected_code
    assert _database_snapshot() == before
    assert db.one(
        "SELECT * FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    ) == policy_before
    if boundary == "duplicate_acceptance":
        assert db.one(
            "SELECT run_id FROM full_auto_duplicate_review_acceptance WHERE run_id = ?",
            run_id,
        ) is None
    if boundary == "submission":
        assert db.q("SELECT id FROM shot WHERE session_id = ?", session_id) == []


@pytest.mark.parametrize("boundary", ["approval", "submission", "duplicate_acceptance"])
def test_full_auto_rechecks_feature_flag_after_aggregate_review_before_write(
    client, seeded, monkeypatch, boundary,
):
    if boundary == "duplicate_acceptance":
        session_id, _run_id, revision, _targets, _owner = _prepared_duplicate_run(
            client, seeded, monkeypatch,
        )
        review = client.get(f"/api/sessions/{session_id}/plan/review").json()[
            "full_auto_duplicate_review"
        ]
        assert review["state"] == "duplicate_review_required"
        action = lambda: session_plan.accept_full_auto_duplicate_review(
            session_id, revision, review["review_digest"],
        )
    else:
        session_id, run_id, revision, targets, owner = _prepared_run(
            client, seeded, monkeypatch,
        )
        if boundary == "approval":
            resource_full_auto.advance_run(owner, "active", phase="approve")
            owner = resource_full_auto.renew_run_claim(owner)
            action = lambda: session_plan.approve_plan_review(
                session_id, revision, _full_auto_claim=owner,
            )
        else:
            _view, owner = main._run_full_auto_review_and_approve(owner)
            assert owner is not None
            action = lambda: session_plan.submit_prepared_take(
                session_id, revision, targets[0], _full_auto_claim=owner,
            )

    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    original_review = session_plan._full_auto_duplicate_review_evidence

    def disable_after_current_aggregate(*args, **kwargs):
        current = original_review(*args, **kwargs)
        monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
        return current

    monkeypatch.setattr(
        session_plan, "_full_auto_duplicate_review_evidence",
        disable_after_current_aggregate,
    )
    before = _database_snapshot()

    with pytest.raises(session_plan.ResourcePlanningDisabled):
        action()

    assert _database_snapshot() == before
    if boundary == "approval":
        assert db.one(
            "SELECT run_id FROM full_auto_plan_approval WHERE session_id = ?",
            session_id,
        ) is None
    elif boundary == "submission":
        assert db.q("SELECT id FROM shot WHERE session_id = ?", session_id) == []
    else:
        assert db.one(
            "SELECT run_id FROM full_auto_duplicate_review_acceptance WHERE session_id = ?",
            session_id,
        ) is None


@pytest.mark.parametrize("control", ["cancelled", "expired", "fenced"])
def test_cancel_expiry_and_replacement_fence_cannot_reach_policy_approval(
    client, seeded, monkeypatch, control,
):
    session_id, run_id, _revision, _targets, old_owner = _prepared_run(
        client, seeded, monkeypatch,
    )
    run = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    if control == "cancelled":
        resource_full_auto.cancel_run(
            run_id, str(uuid.uuid4()), run["revision"], run["plan_revision"],
            work_pending=False,
        )
    elif control == "expired":
        expired_now = _one_second_after(run["lease_expires_at"])
        monkeypatch.setattr(db, "now", lambda: expired_now)
    else:
        replacement = resource_full_auto.claim_run(
            run_id, now_text=_one_second_after(run["lease_expires_at"]),
        )
        assert replacement.fencing_token != old_owner.fencing_token

    before = _database_snapshot()
    with pytest.raises(resource_full_auto.FullAutoRunError):
        main._run_full_auto_review_and_approve(old_owner)

    assert _database_snapshot() == before
    assert db.one(
        "SELECT run_id FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    ) is None
    assert db.one(
        "SELECT session_id FROM session_plan_approval WHERE session_id = ?", session_id,
    ) is None
    assert db.q("SELECT id FROM shot WHERE session_id = ?", session_id) == []
