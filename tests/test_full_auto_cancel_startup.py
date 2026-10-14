"""Restart recovery for durable Full Auto cancellation draining."""
from __future__ import annotations

import asyncio
import uuid

import db
import main
from backend import resource_full_auto
import test_full_auto_preparation_coordinator as preparation


_STARTUP_SCHEDULER = main._schedule_startup_full_auto_runs


def _cancel_to_requested(client, seeded, monkeypatch) -> tuple[int, str]:
    session_id, run_id, _revision, _targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    current = resource_full_auto.get_run(run_id)
    cancelled = resource_full_auto.cancel_run(
        run_id, str(uuid.uuid4()), current["revision"], current["plan_revision"],
        work_pending=True, expected_session_id=session_id,
    )
    assert cancelled.view["state"] == "cancel_requested"
    return session_id, run_id


def test_startup_resumes_cancel_requested_after_prior_lease_expires(
    client, seeded, monkeypatch,
):
    session_id, run_id = _cancel_to_requested(client, seeded, monkeypatch)
    db.run(
        "UPDATE full_auto_run SET lease_started_at = ?, lease_expires_at = ? WHERE run_id = ?",
        "2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00", run_id,
    )
    scheduled = []
    monkeypatch.setattr(
        main, "_schedule_startup_full_auto_runs", _STARTUP_SCHEDULER,
    )
    monkeypatch.setattr(
        main, "_schedule_full_auto_cancellation",
        lambda *args: scheduled.append(args),
    )
    monkeypatch.setattr(
        main, "_schedule_full_auto_run",
        lambda *_args: (_ for _ in ()).throw(AssertionError("cancellation resumed the active coordinator")),
    )

    async def recover_startup():
        main._schedule_startup_full_auto_runs()
        task = main._FULL_AUTO_STARTUP_CANCELLATION_TASKS[run_id]
        await task

    asyncio.run(recover_startup())

    assert len(scheduled) == 1
    scheduled_run_id, scheduled_session_id, claim = scheduled[0]
    assert (scheduled_run_id, scheduled_session_id) == (run_id, session_id)
    assert claim.requires_reconciliation is True
    row = db.one(
        "SELECT state, phase, cancel_intent FROM full_auto_run WHERE run_id = ?",
        run_id,
    )
    assert row == {"state": "cancel_requested", "phase": "reconcile", "cancel_intent": 1}


def test_startup_does_not_reconcile_blocked_cancel_intent(
    client, seeded, monkeypatch,
):
    session_id, run_id = _cancel_to_requested(client, seeded, monkeypatch)
    claim = resource_full_auto.claim_run(run_id)
    resource_full_auto.reconcile_cancellation(
        claim,
        resource_full_auto.CancellationEvidence(
            "unknown", db.now(), 1, blocker_code="comfy_unavailable",
        ),
    )
    scheduled = []
    monkeypatch.setattr(
        main, "_schedule_startup_full_auto_runs", _STARTUP_SCHEDULER,
    )
    monkeypatch.setattr(
        main, "_schedule_full_auto_cancellation",
        lambda *args: scheduled.append(args),
    )

    main._schedule_startup_full_auto_runs()

    assert scheduled == []
    row = db.one(
        "SELECT state, cancel_intent FROM full_auto_run WHERE run_id = ? AND session_id = ?",
        run_id, session_id,
    )
    assert row == {"state": "blocked", "cancel_intent": 1}
