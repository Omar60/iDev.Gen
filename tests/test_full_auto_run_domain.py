from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

import db
from backend import resource_full_auto


START = "2026-10-01T12:00:00+00:00"
PLAN_JSON = '{"takes":[]}'


def _seed_run(
    tmp_path,
    *,
    session_name: str = "run",
    authorization_expires_at: str = "2026-10-08T12:00:00+00:00",
    policy_version: str = "resource-full-auto-v1",
) -> tuple[str, int, str]:
    conn = db.connect(tmp_path / f"{session_name}.sqlite3")
    model_id = conn.execute(
        "INSERT INTO model (name, created_at) VALUES (?, ?)",
        (f"model-{session_name}", START),
    ).lastrowid
    session_id = conn.execute(
        "INSERT INTO session (model_id, name, created_at) VALUES (?, ?, ?)",
        (model_id, session_name, START),
    ).lastrowid
    conn.execute(
        "INSERT INTO session_plan (session_id, mode, plan_revision, plan_json, created_at, updated_at) "
        "VALUES (?, 'resource-v1', 1, ?, ?, ?)",
        (session_id, PLAN_JSON, START, START),
    )
    run_id = f"run-{session_name}"
    authorization_json = json.dumps({
        "authorization_version": 1,
        "generate_without_pauses": True,
        "authorization_expires_at": authorization_expires_at,
        "summary": {"policy_version": policy_version},
    })
    conn.commit()
    with db.transaction():
        db.run(
            """INSERT INTO full_auto_run_receipt (
                   run_id, session_id, authorization_json, scope_digest,
                   dependencies_digest, plan_digest, scope_issued_at,
                   authorization_expires_at, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            run_id, session_id, authorization_json, "a" * 64, "b" * 64, "c" * 64,
            START, authorization_expires_at, START,
        )
        resource_full_auto.create_run_in_transaction(
            run_id, session_id, created_at=START,
        )
    return run_id, int(session_id), str(tmp_path / f"{session_name}.sqlite3")


def _snapshot(run_id: str) -> tuple[dict, dict | None, list[dict]]:
    return (
        db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id),
        db.one("SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id),
        db.q(
            "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ? ORDER BY rowid",
            run_id,
        ),
    )


def _resume_checks(**overrides) -> resource_full_auto.ResumeEligibility:
    values = {
        "current_plan_revision": 1,
        "unresolved_work": 0,
        "authorization_valid": True,
        "policy_supported": True,
        "budget_available": True,
        "session_exclusive": True,
        "scope_matches": True,
        "planning_enabled": True,
        "next_state": "active",
    }
    values.update(overrides)
    return resource_full_auto.ResumeEligibility(**values)


def _control(run_id: str, action: str, request_id: str, *, work_pending=None):
    row = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    function = resource_full_auto.cancel_run if action == "cancel" else resource_full_auto.resume_run
    kwargs = {"work_pending": work_pending} if action == "cancel" else {"eligibility": _resume_checks()}
    return function(
        run_id, request_id, int(row["revision"]), int(row["plan_revision"]),
        now_text=START, **kwargs,
    )


def test_migration_backfills_old_receipts_and_preserves_the_immutable_receipt(tmp_path):
    run_id, session_id, path = _seed_run(tmp_path)
    old_receipt = dict(db.one("SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id))
    conn = db.conn()
    conn.execute("DROP TABLE full_auto_run_action_receipt")
    conn.execute("DROP TABLE full_auto_run")
    conn.commit()
    conn.close()

    conn = db.connect(tmp_path / "run.sqlite3")
    run = dict(conn.execute("SELECT * FROM full_auto_run WHERE run_id = ?", (run_id,)).fetchone())
    receipt = dict(conn.execute(
        "SELECT * FROM full_auto_run_receipt WHERE run_id = ?", (run_id,),
    ).fetchone())
    assert run["session_id"] == session_id
    assert (run["state"], run["phase"], run["revision"], run["epoch"]) == (
        "active", "prepare", 1, 1,
    )
    assert run["plan_revision"] == 1 and run["fencing_token"] == 1
    assert receipt == old_receipt
    with pytest.raises(sqlite3.IntegrityError, match="creation receipt is immutable"):
        conn.execute(
            "UPDATE full_auto_run_receipt SET authorization_json = '{}' WHERE run_id = ?",
            (run_id,),
        )
    conn.close()


def test_run_claims_are_exclusive_expiry_fences_old_owners_and_requires_reconciliation(tmp_path):
    run_id, _session_id, _path = _seed_run(tmp_path)
    first = resource_full_auto.claim_run(run_id, now_text=START)
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.claim_run(run_id, now_text="2026-10-01T12:00:59+00:00")
    assert error.value.code == "run_already_claimed"

    expired_at = "2026-10-01T12:01:01+00:00"
    second = resource_full_auto.claim_run(run_id, now_text=expired_at)
    row = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert second.fencing_token == first.fencing_token + 1
    assert second.requires_reconciliation is True
    assert row["phase"] == "reconcile" and row["resume_phase"] == "prepare"
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.advance_run(first, "active", phase="validate", now_text=expired_at)
    assert error.value.code == "run_owner_stale"

    view, owner = resource_full_auto.reconcile_expired_lease(
        second,
        resource_full_auto.LeaseReconciliationEvidence("safe_to_continue", unresolved_work=0),
        now_text=expired_at,
    )
    assert view["state"] == "active" and view["phase"] == "prepare"
    assert owner is not None and owner.requires_reconciliation is False
    renewed = resource_full_auto.renew_run_claim(owner, now_text="2026-10-01T12:01:15+00:00")
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.renew_run_claim(owner, now_text="2026-10-01T12:01:16+00:00")
    assert error.value.code == "run_owner_stale"
    assert renewed.lease_expires_at == "2026-10-01T12:02:15+00:00"


@pytest.mark.parametrize(
    ("seed_options", "expected_code"),
    [
        ({"authorization_expires_at": "2026-09-30T12:00:00+00:00"}, "authorization_expired"),
        ({"policy_version": "resource-full-auto-v2"}, "policy_changed"),
    ],
)
def test_claim_blocks_expired_or_unsupported_persisted_authority(
    tmp_path, seed_options, expected_code,
):
    run_id, _session_id, _path = _seed_run(tmp_path, **seed_options)

    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.claim_run(run_id, now_text=START)

    assert error.value.code == expected_code
    run = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert run["state"] == "blocked"
    assert run["blocker_code"] == expected_code
    assert run["lease_expires_at"] is None
    assert run["lease_started_at"] is None


def test_phase_continuation_refuses_expired_authority_without_writes(tmp_path):
    run_id, _session_id, _path = _seed_run(
        tmp_path,
        session_name="phase-after-expiry",
        authorization_expires_at="2026-10-07T12:00:00+00:00",
    )
    owner = resource_full_auto.claim_run(
        run_id, now_text="2026-10-07T11:59:30+00:00",
    )
    before = _snapshot(run_id)

    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.advance_run(
            owner, "active", phase="validate", now_text="2026-10-07T12:00:01+00:00",
        )

    assert error.value.code == "authorization_expired"
    assert _snapshot(run_id) == before


def test_run_revision_epoch_plan_revision_and_fence_cannot_decrease(tmp_path):
    run_id, _session_id, _path = _seed_run(tmp_path)
    db.conn().execute(
        "UPDATE full_auto_run SET revision = 2, epoch = 2, plan_revision = 2, fencing_token = 2 "
        "WHERE run_id = ?",
        (run_id,),
    )
    for field, message in (
        ("revision", "revision cannot decrease"),
        ("epoch", "epoch cannot decrease"),
        ("plan_revision", "plan revision cannot decrease"),
        ("fencing_token", "fencing token cannot decrease"),
    ):
        with pytest.raises(sqlite3.IntegrityError, match=message):
            db.conn().execute(
                f"UPDATE full_auto_run SET {field} = 1 WHERE run_id = ?",
                (run_id,),
            )


def test_cancel_replay_is_historical_and_refused_transitions_write_nothing(tmp_path):
    run_id, _session_id, _path = _seed_run(tmp_path)
    before = _snapshot(run_id)
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.resume_run(
            run_id, "resume-early", 1, 1, eligibility=_resume_checks(), now_text=START,
        )
    assert error.value.code == "run_not_resumable"
    assert _snapshot(run_id) == before

    cancelled = _control(run_id, "cancel", "cancel-epoch-one", work_pending=False)
    assert cancelled.view["state"] == "cancelled"
    cancel_snapshot = _snapshot(run_id)
    assert len(cancel_snapshot[2]) == 1
    assert cancel_snapshot[1] == before[1]

    resumed = resource_full_auto.resume_run(
        run_id, "resume-epoch-two", cancelled.view["revision"], 1,
        eligibility=_resume_checks(), now_text="2026-10-01T12:00:01+00:00",
    )
    assert resumed.view["state"] == "active" and resumed.view["epoch"] == 2
    after_resume = _snapshot(run_id)
    assert after_resume[1] == before[1]
    replay = resource_full_auto.cancel_run(
        run_id, "cancel-epoch-one", 1, 1, work_pending=False,
        now_text="2026-10-01T12:00:02+00:00",
    )
    assert replay.replayed is True and replay.view == cancelled.view
    assert _snapshot(run_id) == after_resume

    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.cancel_run(
            run_id, "cancel-epoch-one", 3, 1, work_pending=False,
            now_text="2026-10-01T12:00:02+00:00",
        )
    assert error.value.code == "idempotency_conflict"
    assert _snapshot(run_id) == after_resume
    with pytest.raises(sqlite3.IntegrityError, match="action receipt is immutable"):
        db.conn().execute(
            "UPDATE full_auto_run_action_receipt SET response_json = '{}' "
            "WHERE run_id = ? AND request_id = ?",
            (run_id, "cancel-epoch-one"),
        )


def test_resume_fails_closed_on_each_current_eligibility_boundary(tmp_path):
    checks = [
        ("authorization_valid", False, "authorization_expired"),
        ("unresolved_work", 1, "run_work_unsettled"),
        ("policy_supported", False, "policy_changed"),
        ("budget_available", False, "budget_exhausted"),
        ("session_exclusive", False, "full_auto_run_conflict"),
        ("scope_matches", False, "authorization_scope_changed"),
        ("planning_enabled", False, "resource_planning_disabled"),
        ("current_plan_revision", 2, "plan_revision_stale"),
    ]
    for index, (field_name, value, code) in enumerate(checks):
        run_id, _sid, _path = _seed_run(tmp_path, session_name=f"eligibility-{index}")
        cancelled = _control(run_id, "cancel", f"cancel-{index}", work_pending=False)
        before = _snapshot(run_id)
        with pytest.raises(resource_full_auto.FullAutoRunError) as error:
            resource_full_auto.resume_run(
                run_id, f"resume-{index}", cancelled.view["revision"], 1,
                eligibility=_resume_checks(**{field_name: value}),
                now_text="2026-10-01T12:00:01+00:00",
            )
        assert error.value.code == code
        assert _snapshot(run_id) == before


def test_cancel_intent_blocks_terminal_claims_and_reconciliation_never_activates(tmp_path):
    run_id, _session_id, _path = _seed_run(tmp_path)
    cancelled = _control(run_id, "cancel", "cancel-pending", work_pending=True)
    assert cancelled.view["state"] == "cancel_requested"
    assert cancelled.view["cancel_intent"] is True
    claim = resource_full_auto.claim_run(run_id, now_text="2026-10-01T12:00:01+00:00")
    assert claim.requires_reconciliation is True
    blocked = resource_full_auto.reconcile_cancellation(
        claim,
        resource_full_auto.CancellationEvidence(
            "unknown", "2026-10-01T12:00:02+00:00", 1,
            blocker_code="comfy_unavailable", attempt_ids=("attempt-1",), shot_ids=("shot-1",),
        ),
        now_text="2026-10-01T12:00:02+00:00",
    )
    assert blocked["state"] == "blocked" and blocked["cancel_intent"] is True
    assert db.one("SELECT lease_expires_at FROM full_auto_run WHERE run_id = ?", run_id)["lease_expires_at"] is None

    action = resource_full_auto.resume_run(
        run_id, "reconcile-1", blocked["revision"], 1,
        eligibility=None, now_text="2026-10-01T12:00:03+00:00",
    )
    assert action.view["state"] == "blocked"
    assert action.claim is not None and action.claim.requires_reconciliation is True
    still_blocked = resource_full_auto.reconcile_cancellation(
        action.claim,
        resource_full_auto.CancellationEvidence(
            "unknown", "2026-10-01T12:00:04+00:00", 1,
        ),
        now_text="2026-10-01T12:00:04+00:00",
    )
    assert still_blocked["state"] == "blocked" and still_blocked["cancel_intent"] is True

    action = resource_full_auto.resume_run(
        run_id, "reconcile-2", still_blocked["revision"], 1,
        eligibility=None, now_text="2026-10-01T12:00:05+00:00",
    )
    assert db.one(
        "SELECT lease_started_at FROM full_auto_run WHERE run_id = ?", run_id,
    )["lease_started_at"] == "2026-10-01T12:00:05+00:00"
    running = resource_full_auto.reconcile_cancellation(
        action.claim,
        resource_full_auto.CancellationEvidence(
            "running", "2026-10-01T12:00:06+00:00", 1,
        ),
        now_text="2026-10-01T12:00:06+00:00",
    )
    assert running["state"] == "cancel_requested"
    assert running["cancel_intent"] is True

    settled = resource_full_auto.reconcile_cancellation(
        action.claim,
        resource_full_auto.CancellationEvidence(
            "settled", "2026-10-01T12:00:09+00:00", 0,
        ),
        now_text="2026-10-01T12:00:09+00:00",
    )
    assert settled["state"] == "cancelled" and settled["cancel_intent"] is True
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.advance_run(claim, "active", phase="validate", now_text="2026-10-01T12:00:10+00:00")
    assert error.value.code == "run_owner_stale"


def test_execution_transitions_require_ordered_phases_and_settled_outcomes(tmp_path):
    run_id, _session_id, _path = _seed_run(tmp_path)
    claim = resource_full_auto.claim_run(run_id, now_text=START)
    before = _snapshot(run_id)
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.advance_run(claim, "active", phase="execute", now_text=START)
    assert error.value.code == "run_phase_transition_refused"
    assert _snapshot(run_id) == before

    resource_full_auto.advance_run(claim, "active", phase="validate", now_text=START)
    # Phase changes keep the same lease capability while monotonically advancing.
    for phase in ("approve", "submit", "execute"):
        resource_full_auto.advance_run(claim, "active", phase=phase, now_text=START)
    before = _snapshot(run_id)
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.complete_run(
            claim, resource_full_auto.CompletionEvidence("all_succeeded", unresolved_work=1),
            now_text=START,
        )
    assert error.value.code == "run_work_unsettled"
    assert _snapshot(run_id) == before
    completed = resource_full_auto.complete_run(
        claim, resource_full_auto.CompletionEvidence("all_succeeded", unresolved_work=0),
        now_text=START,
    )
    assert completed["state"] == "succeeded"
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.resume_run(
            run_id, "resume-success", completed["revision"], 1,
            eligibility=_resume_checks(), now_text="2026-10-01T12:00:01+00:00",
        )
    assert error.value.code == "run_not_resumable"


def test_allowed_wait_block_cancel_and_resume_transitions(tmp_path):
    run_id, _session_id, _path = _seed_run(tmp_path)
    claim = resource_full_auto.claim_run(run_id, now_text=START)
    waiting_authoring = resource_full_auto.advance_run(claim, "waiting_authoring", now_text=START)
    assert waiting_authoring["state"] == "waiting_authoring"
    assert db.one("SELECT lease_expires_at FROM full_auto_run WHERE run_id = ?", run_id)["lease_expires_at"] is None
    claim = resource_full_auto.claim_run(run_id, now_text=START)
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.advance_run(claim, "waiting_runner", now_text=START)
    assert error.value.code == "run_transition_refused"
    active = resource_full_auto.advance_run(claim, "active", now_text=START)
    assert active["state"] == "active"
    waiting_runner = resource_full_auto.advance_run(claim, "waiting_runner", now_text=START)
    assert waiting_runner["state"] == "waiting_runner"
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.advance_run(claim, "waiting_authoring", now_text=START)
    assert error.value.code == "run_owner_stale"
    claim = resource_full_auto.claim_run(run_id, now_text=START)
    active = resource_full_auto.advance_run(claim, "active", now_text=START)
    assert active["state"] == "active"
    blocked = resource_full_auto.advance_run(
        claim, "blocked", blocker_code="source_changed", now_text=START,
    )
    assert blocked["state"] == "blocked" and blocked["blocker_code"] == "source_changed"
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.claim_run(run_id, now_text=START)
    assert error.value.code == "run_not_claimable"

    resumed = resource_full_auto.resume_run(
        run_id, "resume-blocked", blocked["revision"], 1,
        eligibility=_resume_checks(), now_text=START,
    )
    assert resumed.view["state"] == "active" and resumed.view["epoch"] == blocked["epoch"]
    cancelled = resource_full_auto.cancel_run(
        run_id, "cancel-active", resumed.view["revision"], 1,
        work_pending=True, now_text=START,
    )
    assert cancelled.view["state"] == "cancel_requested"
    claim = resource_full_auto.claim_run(run_id, now_text=START)
    assert claim.requires_reconciliation is True
    blocked_cancel = resource_full_auto.reconcile_cancellation(
        claim,
        resource_full_auto.CancellationEvidence(
            "unknown", START, 1, blocker_code="execution_outcome_unknown",
        ),
        now_text=START,
    )
    assert blocked_cancel["state"] == "blocked" and blocked_cancel["cancel_intent"] is True
    reconcile = resource_full_auto.resume_run(
        run_id, "resume-cancel-intent", blocked_cancel["revision"], 1,
        eligibility=None, now_text=START,
    )
    settled = resource_full_auto.reconcile_cancellation(
        reconcile.claim,
        resource_full_auto.CancellationEvidence("settled", START, 0),
        now_text=START,
    )
    assert settled["state"] == "cancelled" and settled["cancel_intent"] is True
    resumed_epoch = resource_full_auto.resume_run(
        run_id, "resume-cancelled", settled["revision"], 1,
        eligibility=_resume_checks(), now_text=START,
    )
    assert resumed_epoch.view["state"] == "active"
    assert resumed_epoch.view["epoch"] == settled["epoch"] + 1


@pytest.mark.parametrize(
    ("outcome", "expected_state"),
    [
        ("mixed", "completed_with_errors"),
        ("all_shots_failed", "failed"),
        ("internal_failure", "failed"),
    ],
)
def test_settled_terminal_outcomes_keep_their_distinct_states(tmp_path, outcome, expected_state):
    run_id, _session_id, _path = _seed_run(tmp_path, session_name=f"terminal-{outcome}")
    claim = resource_full_auto.claim_run(run_id, now_text=START)
    if outcome != "internal_failure":
        for phase in ("validate", "approve", "submit", "execute"):
            resource_full_auto.advance_run(claim, "active", phase=phase, now_text=START)
    completed = resource_full_auto.complete_run(
        claim,
        resource_full_auto.CompletionEvidence(outcome, unresolved_work=0),
        now_text=START,
    )
    assert completed["state"] == expected_state


@pytest.mark.parametrize("to_state", ["succeeded", "completed_with_errors", "failed", "cancelled", "cancel_requested"])
def test_direct_terminal_and_cancel_transitions_are_refused_without_writes(tmp_path, to_state):
    run_id, _session_id, _path = _seed_run(tmp_path, session_name=f"refuse-{to_state}")
    claim = resource_full_auto.claim_run(run_id, now_text=START)
    before = _snapshot(run_id)
    with pytest.raises(resource_full_auto.FullAutoRunError):
        resource_full_auto.advance_run(claim, to_state, now_text=START)
    assert _snapshot(run_id) == before


def test_competing_claims_and_identical_cancel_retries_serialize_transactionally(tmp_path):
    run_id, _session_id, _path = _seed_run(tmp_path)
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait(timeout=5)
        try:
            return resource_full_auto.claim_run(run_id, now_text=START)
        except resource_full_auto.FullAutoRunError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = [future.result(timeout=10) for future in [pool.submit(claim), pool.submit(claim)]]
    winners = [value for value in claims if isinstance(value, resource_full_auto.RunClaim)]
    assert len(winners) == 1
    assert sum(value == "run_already_claimed" for value in claims) == 1

    run_id, _session_id, _path = _seed_run(tmp_path, session_name="cancel-race")
    barrier = threading.Barrier(2)

    def cancel():
        barrier.wait(timeout=5)
        return resource_full_auto.cancel_run(
            run_id, "same-cancel", 1, 1, work_pending=False, now_text=START,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=10) for future in [pool.submit(cancel), pool.submit(cancel)]]
    assert sum(not value.replayed for value in results) == 1
    assert sum(value.replayed for value in results) == 1
    assert results[0].view == results[1].view
    assert len(db.q("SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id)) == 1
