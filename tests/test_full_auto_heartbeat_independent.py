"""Independent Task 4.7 checks for lease-boundary races and runner regressions."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import inspect
import uuid

import pytest

import db
import main
from backend import authoring_operations, resource_full_auto
import test_full_auto_preparation_coordinator as preparation
import test_full_auto_preparation_independent as preparation_independent
import test_full_auto_submission_independent as submission
import test_full_auto_run_independent as run_independent


@pytest.fixture(autouse=True)
def _configured_full_auto(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "heartbeat-independent-test")
    monkeypatch.setitem(main.CONFIG, "llm_key", "heartbeat-independent-key")


def _advance_time_at_transaction_entry(monkeypatch, target, before, after):
    clock = [before]
    injected = []
    original_transaction = db.transaction

    def fake_now():
        return clock[0].isoformat(timespec="seconds")

    class DelayedTransaction:
        def __init__(self, *args, **kwargs):
            self.inner = original_transaction(*args, **kwargs)

        def __enter__(self):
            frame = inspect.currentframe()
            caller = frame.f_back if frame is not None else None
            if caller is not None and caller.f_code.co_name == target:
                clock[0] = after
                injected.append(caller.f_code.co_name)
            return self.inner.__enter__()

        def __exit__(self, exc_type, exc, traceback):
            return self.inner.__exit__(exc_type, exc, traceback)

    def delayed_transaction(*args, **kwargs):
        return DelayedTransaction(*args, **kwargs)

    monkeypatch.setattr(db, "now", fake_now)
    monkeypatch.setattr(db, "transaction", delayed_transaction)
    return clock, injected


def test_run_claim_renewal_fails_closed_after_transaction_wait(
    client, monkeypatch,
):
    run_id, _session_id = run_independent._seed_run("heartbeat-renew-lock-wait")
    owner = resource_full_auto.claim_run(run_id, now_text=run_independent.START)
    before_row = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    start = datetime.fromisoformat(owner.lease_started_at)
    clock, injected = _advance_time_at_transaction_entry(
        monkeypatch,
        "renew_run_claim",
        start + timedelta(seconds=59),
        start + timedelta(seconds=61),
    )

    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.renew_run_claim(owner)

    assert clock[0] == start + timedelta(seconds=61)
    assert injected == ["renew_run_claim"]
    assert error.value.code == "run_lease_expired"
    assert db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id) == before_row


def test_expired_parent_cannot_resume_child_after_transaction_wait(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    owner = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(owner)
    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": "prepare_takes",
        "take_ids": targets,
    })
    child, created = authoring_operations.start_operation(
        session_id,
        request,
        planning_enabled=True,
        assistant_available=True,
        _full_auto_claim=owner,
    )
    assert created is True
    worker_claim = authoring_operations.load_worker_claim(
        session_id, child["operation_id"],
        planning_enabled=True,
        _full_auto_claim=owner,
    )
    ticket = authoring_operations.renew_operation_lease(
        worker_claim,
        planning_enabled=True,
        _full_auto_claim=owner,
    )
    failed = authoring_operations.fail_operation_item(
        worker_claim, ticket, targets[0],
        planning_enabled=True,
        _full_auto_claim=owner,
    )
    assert failed["state"] == "failed"

    before_run = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    before_child = db.one(
        "SELECT * FROM authoring_operation WHERE operation_id = ?",
        child["operation_id"],
    )
    start = datetime.fromisoformat(owner.lease_started_at)
    clock, injected = _advance_time_at_transaction_entry(
        monkeypatch,
        "resume_operation",
        start + timedelta(seconds=59),
        start + timedelta(seconds=61),
    )

    with pytest.raises(authoring_operations.AuthoringOperationError) as error:
        authoring_operations.resume_operation(
            session_id,
            child["operation_id"],
            revision,
            planning_enabled=True,
            assistant_available=True,
            _full_auto_claim=owner,
        )

    assert clock[0] == start + timedelta(seconds=61)
    assert injected == ["resume_operation"]
    assert error.value.code == "run_lease_expired"
    assert db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id) == before_run
    assert db.one(
        "SELECT * FROM authoring_operation WHERE operation_id = ?",
        child["operation_id"],
    ) == before_child


def test_after_io_child_renewal_rejects_parent_expiry_during_transaction_wait(
    client, seeded, monkeypatch,
):
    session_id, run_id, _revision, targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    owner, child = preparation_independent._start_owned_child(
        session_id, run_id, targets,
    )
    worker_claim = authoring_operations.load_worker_claim(
        session_id, child["operation_id"],
        planning_enabled=True,
        _full_auto_claim=owner,
    )
    ticket = authoring_operations.renew_operation_lease(
        worker_claim,
        planning_enabled=True,
        _full_auto_claim=owner,
    )
    before_run = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    before_child = db.one(
        "SELECT * FROM authoring_operation WHERE operation_id = ?",
        child["operation_id"],
    )
    start = datetime.fromisoformat(owner.lease_started_at)
    clock, injected = _advance_time_at_transaction_entry(
        monkeypatch,
        "renew_operation_lease_after_io",
        start + timedelta(seconds=59),
        start + timedelta(seconds=61),
    )

    with pytest.raises(authoring_operations.AuthoringOperationError) as error:
        authoring_operations.renew_operation_lease_after_io(
            worker_claim,
            ticket,
            planning_enabled=True,
            _full_auto_claim=owner,
        )

    assert clock[0] == start + timedelta(seconds=61)
    assert injected == ["renew_operation_lease_after_io"]
    assert error.value.code == "full_auto_owner_stale"
    assert db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id) == before_run
    assert db.one(
        "SELECT * FROM authoring_operation WHERE operation_id = ?",
        child["operation_id"],
    ) == before_child


def test_pre_dispatch_child_renewal_rejects_parent_expiry_during_transaction_wait(
    client, seeded, monkeypatch,
):
    session_id, run_id, _revision, targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    owner, child = preparation_independent._start_owned_child(
        session_id, run_id, targets,
    )
    worker_claim = authoring_operations.load_worker_claim(
        session_id, child["operation_id"],
        planning_enabled=True,
        _full_auto_claim=owner,
    )
    before_run = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    before_child = db.one(
        "SELECT * FROM authoring_operation WHERE operation_id = ?",
        child["operation_id"],
    )
    start = datetime.fromisoformat(owner.lease_started_at)
    clock, injected = _advance_time_at_transaction_entry(
        monkeypatch,
        "renew_operation_lease",
        start + timedelta(seconds=59),
        start + timedelta(seconds=61),
    )

    with pytest.raises(authoring_operations.AuthoringOperationError) as error:
        authoring_operations.renew_operation_lease(
            worker_claim,
            planning_enabled=True,
            _full_auto_claim=owner,
        )

    assert clock[0] == start + timedelta(seconds=61)
    assert injected == ["renew_operation_lease"]
    assert error.value.code == "full_auto_owner_stale"
    assert db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id) == before_run
    assert db.one(
        "SELECT * FROM authoring_operation WHERE operation_id = ?",
        child["operation_id"],
    ) == before_child


def test_recovery_wait_does_not_steal_live_ten_minute_child_lease(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    owner = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(owner)
    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": "prepare_takes",
        "take_ids": targets,
    })
    child, created = authoring_operations.start_operation(
        session_id,
        request,
        planning_enabled=True,
        assistant_available=True,
        _full_auto_claim=owner,
    )
    assert created is True
    before_child = db.one(
        "SELECT * FROM authoring_operation WHERE operation_id = ?",
        child["operation_id"],
    )
    assert before_child["state"] == "active"
    expires_at = datetime.fromisoformat(before_child["lease_expires_at"])
    child_created = datetime.fromisoformat(before_child["created_at"])
    lease_duration = expires_at - child_created
    assert (
        timedelta(minutes=10, seconds=-1)
        <= lease_duration
        <= timedelta(minutes=10, seconds=1)
    )

    db.run(
        "UPDATE full_auto_run SET lease_started_at = ?, lease_expires_at = ? WHERE run_id = ?",
        "1999-12-31T23:58:00+00:00", "2000-01-01T00:00:00+00:00", run_id,
    )
    recovery_claim = resource_full_auto.claim_run(run_id)
    assert recovery_claim.requires_reconciliation is True
    monkeypatch.setattr(
        resource_full_auto, "RUN_HEARTBEAT_INTERVAL", timedelta(milliseconds=5),
    )

    async def wait_for_child_without_stealing():
        task = asyncio.create_task(
            main._recover_expired_full_auto_prepare_owner(recovery_claim),
        )
        await asyncio.sleep(0.04)
        assert not task.done()
        assert db.one(
            "SELECT * FROM authoring_operation WHERE operation_id = ?",
            child["operation_id"],
        ) == before_child
        assert db.one(
            "SELECT COUNT(*) AS n FROM authoring_operation WHERE full_auto_run_id = ?",
            run_id,
        )["n"] == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(wait_for_child_without_stealing())


def test_waiting_runner_has_no_runner_admission_owner(
    client, monkeypatch,
):
    run_id, session_id = run_independent._seed_run(
        "heartbeat-waiting-runner",
        authorization_expires_at="2026-11-01T00:00:00+00:00",
    )
    now_text = run_independent.START
    monkeypatch.setattr(db, "now", lambda: now_text)
    owner = resource_full_auto.claim_run(run_id, now_text=now_text)
    for phase in ("validate", "approve", "submit"):
        resource_full_auto.advance_run(
            owner, "active", phase=phase, now_text=now_text,
        )
    waiting = resource_full_auto.advance_run(
        owner, "waiting_runner", now_text=now_text,
    )
    assert waiting["state"] == "waiting_runner"
    assert db.one(
        "SELECT lease_expires_at FROM full_auto_run WHERE run_id = ?", run_id,
    )["lease_expires_at"] is None
    before_run = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    before_session = db.one("SELECT status FROM session WHERE id = ?", session_id)
    scheduler_owner = resource_full_auto.claim_run(
        run_id, now_text=now_text,
    )
    after_claim = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)

    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.admit_runner_start(session_id, scheduler_owner)

    assert error.value.code == "run_owner_stale"
    assert db.q(
        "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == []
    assert db.one("SELECT status FROM session WHERE id = ?", session_id) == before_session
    # The failed runner admission itself must not change the live coordinator claim.
    assert db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id) == after_claim
    assert before_run["lease_expires_at"] is None


def test_history_transport_error_retries_in_manual_and_full_auto_runner_paths(
    client, seeded, monkeypatch, make_runner,
):
    monkeypatch.setattr("runner.POLL_SECONDS", 0)
    monkeypatch.setattr(resource_full_auto, "COMFY_PROBE_DELAYS", (0, 0, 0))
    manual_runner, _manual_comfy = make_runner()
    manual_calls = []

    async def fail_once_then_settle(_prompt_id):
        manual_calls.append("history")
        if len(manual_calls) == 1:
            raise OSError("temporary history outage")
        return {"status": {"completed": True}, "outputs": {}}

    manual_runner.comfy.history = fail_once_then_settle
    assert asyncio.run(manual_runner._await_history("manual-prompt", 7001)) == {
        "status": {"completed": True}, "outputs": {},
    }
    assert len(manual_calls) == 2

    session_id, _run_id, _revision, _targets, submit_owner = submission._approved_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    _view, execute_owner = main._run_full_auto_submission(submit_owner)
    runner, comfy = make_runner()
    original_history = comfy.history
    owned_calls = []

    async def fail_once_then_use_fake_comfy(prompt_id):
        owned_calls.append(prompt_id)
        if len(owned_calls) == 1:
            raise OSError("temporary history outage")
        return await original_history(prompt_id)

    comfy.history = fail_once_then_use_fake_comfy

    async def run_owned_session():
        runner.start(session_id, _full_auto_claim=execute_owner)
        await runner._task

    asyncio.run(run_owned_session())
    assert len(owned_calls) == 5
    assert owned_calls[0] == owned_calls[1]
    assert len(set(owned_calls[1:])) == 4
    assert comfy.attempts == 4
    assert {
        row["status"]
        for row in db.q("SELECT status FROM shot WHERE session_id = ?", session_id)
    } == {"done"}
    assert db.one("SELECT status FROM session WHERE id = ?", session_id)["status"] == "done"
