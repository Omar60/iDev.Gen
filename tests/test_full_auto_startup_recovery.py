"""Task 5.4 acceptance for Full Auto dispatch and startup recovery."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

import db
import main
from backend import resource_full_auto
import test_full_auto_preparation_coordinator as preparation
import test_full_auto_submission_independent as submission
import test_runner


_STARTUP_SCHEDULER = main._schedule_startup_full_auto_runs
_FULL_AUTO_SCHEDULER = main._schedule_full_auto_run


class _SimulatedProcessExit(BaseException):
    pass


@pytest.fixture(autouse=True)
def _configured_full_auto(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "startup-recovery-test")
    monkeypatch.setitem(main.CONFIG, "llm_key", "startup-recovery-test-key")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)


def _expire_owners(run_id: str) -> resource_full_auto.RunClaim:
    stale = "2000-01-01T00:00:00+00:00"
    db.run(
        "UPDATE full_auto_run SET lease_started_at=?, lease_expires_at=? WHERE run_id=?",
        stale, stale, run_id,
    )
    db.run(
        "UPDATE global_runner_claim SET owner_kind=NULL, session_id=NULL, run_id=NULL, "
        "owner_nonce=NULL, lease_started_at=NULL, lease_expires_at=NULL WHERE singleton=1",
    )
    main._recover_interrupted_runner_work()
    return resource_full_auto.claim_run(run_id)


def _execute_run(client, seeded, monkeypatch, *, photo_count=4):
    session_id, run_id, revision, targets, owner = submission._approved_run(
        client, seeded, monkeypatch, photo_count=photo_count,
    )
    _view, owner = main._run_full_auto_submission(owner)
    return session_id, run_id, targets, owner


def _finish_reconciliation(runner, session_id: int, claim):
    safe, claim, blocker = asyncio.run(
        runner.reconcile_full_auto_attempts(session_id, claim),
    )
    assert safe is True, blocker
    view, resumed = resource_full_auto.reconcile_expired_lease(
        claim,
        resource_full_auto.LeaseReconciliationEvidence("safe_to_continue", 0),
    )
    assert (view["state"], view["phase"]) == ("active", "execute")
    assert resumed is not None
    return resumed


def test_guided_creation_schedules_only_after_run_commit(client, seeded, monkeypatch):
    scheduled = []

    def observe_commit(run_id: str) -> None:
        assert not db.conn().in_transaction
        assert db.one("SELECT state, phase FROM full_auto_run WHERE run_id=?", run_id) == {
            "state": "active", "phase": "prepare",
        }
        scheduled.append(run_id)

    monkeypatch.setattr(main, "_schedule_full_auto_run", observe_commit)
    _session_id, run_id, _revision, _targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    assert scheduled == [run_id]


def test_guided_creation_stays_consistent_during_concurrent_status_polls(
    client, seeded, monkeypatch, make_runner,
):
    runner, fake = make_runner()
    monkeypatch.setattr(main, "runner", runner)
    monkeypatch.setattr(main, "comfy", fake)
    provider = {}
    scheduled = []

    original_writer = main.enhance.run_structured

    async def delayed_writer(*args, **kwargs):
        # Keep realistic remote-I/O time between the coordinator's short DB
        # transactions while the API status route polls the shared connection.
        await asyncio.sleep(0.01)
        return await original_writer(*args, **kwargs)

    monkeypatch.setattr(main.enhance, "run_structured", delayed_writer)

    def dispatch_after_commit(run_id: str) -> None:
        committed = db.one(
            "SELECT session_id, plan_revision, state, phase FROM full_auto_run WHERE run_id=?",
            run_id,
        )
        assert not db.conn().in_transaction
        assert committed is not None
        assert (committed["state"], committed["phase"]) == ("active", "prepare")
        targets = [take["take_id"] for take in json.loads(db.one(
            "SELECT plan_json FROM session_plan WHERE session_id=?",
            committed["session_id"],
        )["plan_json"])["takes"]]
        provider["fake"] = preparation._install_provider(
            monkeypatch, committed["session_id"], committed["plan_revision"], targets,
        )
        scheduled.append(run_id)
        _FULL_AUTO_SCHEDULER(run_id)

    monkeypatch.setattr(main, "_schedule_full_auto_run", dispatch_after_commit)
    session_id, run_id, _revision, targets = preparation._create_run(
        client, seeded, photo_count=4,
    )

    deadline = time.monotonic() + 20
    polls = 0
    while time.monotonic() < deadline:
        response = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
        assert response.status_code == 200, response.text
        status = response.json()
        assert status["run_id"] == run_id
        assert status["state"] in {
            "active", "waiting_authoring", "waiting_runner", "succeeded",
        }
        polls += 1
        if status["state"] == "succeeded":
            break
        time.sleep(0.002)

    assert status["state"] == "succeeded", status
    assert polls > 1
    assert scheduled == [run_id]
    assert provider["fake"]["calls"] == provider["fake"]["dispatch_order"]
    assert provider["fake"]["pending"] == []
    assert fake.attempts == len(targets)
    assert {row["status"] for row in db.q(
        "SELECT status FROM shot WHERE session_id=?", session_id,
    )} == {"done"}


def test_startup_discovers_runs_and_preserves_journaled_work_outside_global_slot(
    client, seeded, monkeypatch,
):
    session_id, run_id, _targets, owner = _execute_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    runner_claim = resource_full_auto.acquire_runner_claim(
        session_id, run_claim=owner,
    )
    assert runner_claim is not None
    resource_full_auto.admit_runner_start(session_id, owner)
    shot = db.one("SELECT id FROM shot WHERE session_id=? ORDER BY id LIMIT 1", session_id)
    db.run("UPDATE session SET status='running' WHERE id=?", session_id)

    legacy_session = test_runner._session(client, count=1, tag="startup-legacy")
    legacy_shot = db.one(
        "SELECT id FROM shot WHERE session_id=? ORDER BY id LIMIT 1", legacy_session,
    )
    db.run("UPDATE session SET status='running' WHERE id=?", legacy_session)
    db.run("UPDATE shot SET status='running' WHERE id=?", legacy_shot["id"])
    db.run(
        "UPDATE global_runner_claim SET owner_kind=NULL, session_id=NULL, run_id=NULL, "
        "owner_nonce=NULL, lease_started_at=NULL, lease_expires_at=NULL WHERE singleton=1",
    )

    scheduled = []
    monkeypatch.setattr(main, "_schedule_full_auto_run", scheduled.append)
    monkeypatch.setattr(main, "_schedule_startup_full_auto_runs", _STARTUP_SCHEDULER)
    main._recover_interrupted_runner_work()
    main._schedule_startup_full_auto_runs()
    assert run_id in scheduled

    assert db.one("SELECT status FROM session WHERE id=?", session_id)["status"] == "running"
    assert db.one("SELECT status FROM shot WHERE id=?", shot["id"])["status"] == "pending"
    assert db.one("SELECT status FROM session WHERE id=?", legacy_session)["status"] == "failed"
    assert db.one("SELECT status FROM shot WHERE id=?", legacy_shot["id"])["status"] == "failed"


def test_startup_resumes_waiting_authoring_from_saved_prepare_phase(
    client, seeded, monkeypatch, make_runner,
):
    runner, fake = make_runner()
    monkeypatch.setattr(main, "runner", runner)
    monkeypatch.setattr(main, "comfy", fake)
    session_id, run_id, revision, targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    provider = preparation._install_provider(
        monkeypatch, session_id, revision, targets,
    )
    owner = resource_full_auto.claim_run(run_id)
    waiting = resource_full_auto.advance_run(owner, "waiting_authoring")
    assert (waiting["state"], waiting["phase"]) == ("waiting_authoring", "prepare")
    monkeypatch.setattr(main, "_schedule_full_auto_run", _FULL_AUTO_SCHEDULER)
    monkeypatch.setattr(main, "_schedule_startup_full_auto_runs", _STARTUP_SCHEDULER)

    async def discover_and_wait():
        main._schedule_startup_full_auto_runs()
        task = main._FULL_AUTO_RUN_TASKS[run_id]
        await task

    asyncio.run(discover_and_wait())

    assert db.one("SELECT state FROM full_auto_run WHERE run_id=?", run_id)["state"] == "succeeded"
    assert provider["calls"] == provider["dispatch_order"]
    assert provider["pending"] == []
    assert fake.attempts == len(targets)
    assert {row["status"] for row in db.q(
        "SELECT status FROM shot WHERE session_id=?", session_id,
    )} == {"done"}


def test_startup_resumes_waiting_runner_before_saved_submit_phase(
    client, seeded, monkeypatch, make_runner,
):
    runner, fake = make_runner()
    monkeypatch.setattr(main, "runner", runner)
    monkeypatch.setattr(main, "comfy", fake)
    session_id, run_id, _revision, targets, owner = submission._approved_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    waiting = resource_full_auto.advance_run(owner, "waiting_runner")
    assert (waiting["state"], waiting["phase"]) == ("waiting_runner", "submit")
    monkeypatch.setattr(main, "_schedule_full_auto_run", _FULL_AUTO_SCHEDULER)
    monkeypatch.setattr(main, "_schedule_startup_full_auto_runs", _STARTUP_SCHEDULER)

    async def discover_and_wait():
        main._schedule_startup_full_auto_runs()
        task = main._FULL_AUTO_RUN_TASKS[run_id]
        await task

    asyncio.run(discover_and_wait())

    assert db.one("SELECT state FROM full_auto_run WHERE run_id=?", run_id)["state"] == "succeeded"
    assert db.one(
        "SELECT COUNT(*) AS count FROM full_auto_submission_receipt WHERE run_id=?", run_id,
    )["count"] == len(targets)
    assert fake.attempts == len(targets)
    assert {row["status"] for row in db.q(
        "SELECT status FROM shot WHERE session_id=?", session_id,
    )} == {"done"}


def test_startup_releases_waiting_runner_only_after_global_owner_releases(
    client, seeded, monkeypatch, make_runner,
):
    runner, fake = make_runner()
    monkeypatch.setattr(main, "runner", runner)
    monkeypatch.setattr(main, "comfy", fake)
    monkeypatch.setattr(main, "_FULL_AUTO_COORDINATOR_RETRY_SECONDS", 0.001)
    session_id, run_id, targets, owner = _execute_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    legacy_session = test_runner._session(client, count=1, tag="startup-runner-owner")
    legacy_claim = resource_full_auto.acquire_runner_claim(legacy_session)
    assert legacy_claim is not None
    waiting = resource_full_auto.advance_run(owner, "waiting_runner")
    assert (waiting["state"], waiting["phase"]) == ("waiting_runner", "execute")
    waiting_runner_ready_at = db.one(
        "SELECT runner_ready_at FROM full_auto_run WHERE run_id=?", run_id,
    )["runner_ready_at"]
    waiting_revision = int(waiting["revision"])
    monkeypatch.setattr(main, "_schedule_full_auto_run", _FULL_AUTO_SCHEDULER)
    monkeypatch.setattr(main, "_schedule_startup_full_auto_runs", _STARTUP_SCHEDULER)

    async def discover_and_release():
        main._schedule_startup_full_auto_runs()
        task = main._FULL_AUTO_RUN_TASKS[run_id]

        async def release_when_waiter_is_preserved():
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                row = db.one(
                    "SELECT state, phase, revision, runner_ready_at, lease_expires_at "
                    "FROM full_auto_run WHERE run_id=?",
                    run_id,
                )
                if int(row["revision"]) >= waiting_revision + 2 and row["lease_expires_at"] is None:
                    assert (row["state"], row["phase"]) == ("waiting_runner", "execute")
                    assert row["runner_ready_at"] == waiting_runner_ready_at
                    slot = db.one(
                        "SELECT owner_kind, owner_nonce FROM global_runner_claim WHERE singleton=1",
                    )
                    assert (slot["owner_kind"], slot["owner_nonce"]) == (
                        "manual", legacy_claim.owner_nonce,
                    )
                    assert fake.attempts == 0
                    assert resource_full_auto.release_runner_claim(legacy_claim) is True
                    return
                await asyncio.sleep(0.001)
            raise AssertionError("The startup coordinator did not preserve the waiting runner claim.")

        await asyncio.gather(task, release_when_waiter_is_preserved())

    asyncio.run(discover_and_release())

    assert db.one("SELECT state FROM full_auto_run WHERE run_id=?", run_id)["state"] == "succeeded"
    assert fake.attempts == len(targets)
    assert len(fake.graphs) == len(targets)
    assert db.one(
        "SELECT status FROM session WHERE id=?", session_id,
    )["status"] == "done"


def test_crash_before_intent_resumes_authorized_pending_shot_without_duplicate_post(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, targets, owner = _execute_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    runner, fake = make_runner()
    original_begin = resource_full_auto.begin_execution_attempt

    def crash_before_intent(*_args, **_kwargs):
        raise _SimulatedProcessExit()

    monkeypatch.setattr(resource_full_auto, "begin_execution_attempt", crash_before_intent)

    async def crash():
        assert runner.start(session_id, _full_auto_claim=owner)
        with pytest.raises(_SimulatedProcessExit):
            await runner._task

    asyncio.run(crash())
    shot = db.one(
        "SELECT id, status FROM shot WHERE session_id=? ORDER BY id LIMIT 1", session_id,
    )
    assert shot["status"] == "running"
    assert db.one(
        "SELECT 1 FROM full_auto_execution_attempt WHERE shot_id=?", shot["id"],
    ) is None
    assert fake.attempts == 0

    claim = _expire_owners(run_id)
    resumed = _finish_reconciliation(runner, session_id, claim)
    monkeypatch.setattr(resource_full_auto, "begin_execution_attempt", original_begin)

    async def continue_run():
        assert runner.start(session_id, _full_auto_claim=resumed, _full_auto_recovery=True)
        await runner._task

    asyncio.run(continue_run())
    assert fake.attempts == len(targets)
    assert db.one("SELECT state FROM full_auto_run WHERE run_id=?", run_id)["state"] == "succeeded"
    assert db.one("SELECT status FROM shot WHERE id=?", shot["id"])["status"] == "done"


def test_crash_after_ack_reconciles_queue_identity_without_reposting(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, targets, owner = _execute_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    runner, fake = make_runner()
    original_history = fake.history
    first = True

    async def crash_after_ack(prompt_id: str):
        nonlocal first
        if first:
            first = False
            raise _SimulatedProcessExit()
        return await original_history(prompt_id)

    fake.history = crash_after_ack

    async def crash():
        assert runner.start(session_id, _full_auto_claim=owner)
        with pytest.raises(_SimulatedProcessExit):
            await runner._task

    asyncio.run(crash())
    attempt = db.one(
        "SELECT dispatch_state, completion_state, prompt_id "
        "FROM full_auto_execution_attempt WHERE run_id=?", run_id,
    )
    assert attempt["dispatch_state"] == "acknowledged"
    assert attempt["completion_state"] == "pending"
    assert attempt["prompt_id"] == "pid-1"

    claim = _expire_owners(run_id)
    resumed = _finish_reconciliation(runner, session_id, claim)

    async def continue_run():
        assert runner.start(session_id, _full_auto_claim=resumed, _full_auto_recovery=True)
        await runner._task

    asyncio.run(continue_run())
    assert fake.attempts == len(targets)
    assert len(fake.graphs) == len(targets)
    assert db.one("SELECT state FROM full_auto_run WHERE run_id=?", run_id)["state"] == "succeeded"


@pytest.mark.parametrize("planning_enabled", [True, False])
def test_crash_after_output_move_settles_by_attempt_metadata_without_reposting(
    client, seeded, monkeypatch, make_runner, planning_enabled,
):
    session_id, run_id, targets, owner = _execute_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    runner, fake = make_runner()
    original_finish = resource_full_auto.finish_execution_attempt

    def crash_after_output_move(attempt_id, claim, runner_claim, **kwargs):
        assert kwargs["outcome"] == "completed"
        attempt = db.one(
            "SELECT completion_state, expected_filename FROM full_auto_execution_attempt "
            "WHERE attempt_id=?", attempt_id,
        )
        assert attempt == {
            "completion_state": "output_available",
            "expected_filename": kwargs["filename"],
        }
        assert (runner.sessions_dir / str(session_id) / attempt["expected_filename"]).is_file()
        raise _SimulatedProcessExit()

    monkeypatch.setattr(
        resource_full_auto, "finish_execution_attempt", crash_after_output_move,
    )

    async def crash():
        assert runner.start(session_id, _full_auto_claim=owner)
        with pytest.raises(_SimulatedProcessExit):
            await runner._task

    asyncio.run(crash())
    attempt = db.one(
        "SELECT attempt_id, completion_state, expected_filename FROM full_auto_execution_attempt "
        "WHERE run_id=?", run_id,
    )
    assert attempt["completion_state"] == "output_available"
    assert db.one(
        "SELECT status, filename FROM shot WHERE id=?",
        db.one("SELECT shot_id FROM full_auto_execution_attempt WHERE run_id=?", run_id)["shot_id"],
    ) == {"status": "running", "filename": ""}

    recovery_claim = _expire_owners(run_id)
    if not planning_enabled:
        monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    monkeypatch.setattr(resource_full_auto, "finish_execution_attempt", original_finish)

    safe, recovery_claim, blocker = asyncio.run(
        runner.reconcile_full_auto_attempts(session_id, recovery_claim),
    )
    assert safe is True, blocker
    view, resumed = resource_full_auto.reconcile_expired_lease(
        recovery_claim,
        resource_full_auto.LeaseReconciliationEvidence("safe_to_continue", 0),
    )
    assert db.one(
        "SELECT status, filename FROM shot WHERE id=?",
        db.one("SELECT shot_id FROM full_auto_execution_attempt WHERE run_id=?", run_id)["shot_id"],
    ) == {"status": "done", "filename": attempt["expected_filename"]}

    if not planning_enabled:
        assert view["state"] == "blocked"
        assert view["blocker_code"] == "resource_planning_disabled"
        assert resumed is None
        assert fake.attempts == 1
        assert len(fake.graphs) == 1
        assert db.one(
            "SELECT completion_state, filename FROM full_auto_execution_attempt WHERE run_id=?",
            run_id,
        ) == {"completion_state": "completed", "filename": attempt["expected_filename"]}
        return

    assert (view["state"], view["phase"]) == ("active", "execute")
    assert resumed is not None

    async def continue_run():
        assert runner.start(session_id, _full_auto_claim=resumed, _full_auto_recovery=True)
        await runner._task

    asyncio.run(continue_run())
    assert fake.attempts == len(targets)
    assert len(fake.graphs) == len(targets)
    assert db.one(
        "SELECT completion_state, filename FROM full_auto_execution_attempt WHERE run_id=?",
        run_id,
    ) == {"completion_state": "completed", "filename": attempt["expected_filename"]}
    assert db.one("SELECT state FROM full_auto_run WHERE run_id=?", run_id)["state"] == "succeeded"
    assert len(list((runner.sessions_dir / str(session_id)).glob("*.png"))) == len(targets)


def test_intent_with_empty_queue_and_history_blocks_instead_of_assuming_unsent(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _targets, owner = _execute_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    runner, fake = make_runner()

    async def lost_post_response(*_args, **_kwargs):
        raise _SimulatedProcessExit()

    fake.queue_prompt = lost_post_response

    async def crash():
        assert runner.start(session_id, _full_auto_claim=owner)
        with pytest.raises(_SimulatedProcessExit):
            await runner._task

    asyncio.run(crash())
    claim = _expire_owners(run_id)
    safe, claim, blocker = asyncio.run(
        runner.reconcile_full_auto_attempts(session_id, claim),
    )
    assert safe is False
    assert blocker == "execution_outcome_unknown"
    view, next_claim = resource_full_auto.reconcile_expired_lease(
        claim,
        resource_full_auto.LeaseReconciliationEvidence(
            "unknown", 1, "execution_outcome_unknown",
        ),
    )
    assert next_claim is None
    assert view["state"] == "blocked"
    assert fake.attempts == 0
    assert db.one(
        "SELECT dispatch_state, completion_state FROM full_auto_execution_attempt WHERE run_id=?",
        run_id,
    ) == {"dispatch_state": "outcome_unknown", "completion_state": "pending"}
