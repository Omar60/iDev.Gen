"""Independent adversarial checks for Full Auto cancellation boundaries."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
import time
import uuid

import httpx
import pytest

import db
import main
from backend import resource_full_auto
import runner as runner_mod
import test_full_auto_assistant_slots as assistant_slots
import test_full_auto_submission_independent as submission


_REAL_CANCELLATION_SCHEDULER = main._schedule_full_auto_cancellation


@pytest.fixture(autouse=True)
def _configured_assistant(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "cancellation-independent-test")
    monkeypatch.setitem(main.CONFIG, "llm_key", "cancellation-independent-key")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)


def _control_body(run_id: str, *, request_id: str | None = None) -> dict:
    run = resource_full_auto.get_run(run_id)
    return {
        "request_id": request_id or str(uuid.uuid4()),
        "expected_run_revision": run["revision"],
        "expected_plan_revision": run["plan_revision"],
    }


def _execution(client, seeded, monkeypatch, make_runner, *, photo_count: int = 4):
    session_id, run_id, revision, targets, owner = submission._approved_run(
        client, seeded, monkeypatch, photo_count=photo_count,
    )
    _view, execute_owner = main._run_full_auto_submission(owner)
    runner, fake = make_runner()
    return session_id, run_id, revision, targets, execute_owner, runner, fake


def test_cancel_before_runner_admission_keeps_linked_ids_for_explicit_continuation(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, revision, targets, _owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    links_before = db.q(
        "SELECT take_id, shot_id FROM full_auto_submission_receipt "
        "WHERE run_id = ? ORDER BY target_index",
        run_id,
    )
    assert len(links_before) == len(targets)
    assert db.q(
        "SELECT * FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    ) == []

    cancelled = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
        json=_control_body(run_id),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"
    assert db.q(
        "SELECT id FROM shot WHERE session_id = ? AND status = 'cancelled' ORDER BY id",
        session_id,
    ) == [{"id": row["shot_id"]} for row in links_before]
    assert db.q(
        "SELECT * FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    ) == []
    assert fake.attempts == 0

    resumed = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=_control_body(run_id),
    )
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["state"] == "waiting_runner"
    assert db.q(
        "SELECT take_id, shot_id FROM full_auto_submission_receipt "
        "WHERE run_id = ? ORDER BY target_index",
        run_id,
    ) == links_before
    assert db.q(
        "SELECT id FROM shot WHERE session_id = ? AND status = 'pending' ORDER BY id",
        session_id,
    ) == [{"id": row["shot_id"]} for row in links_before]

    new_owner = resource_full_auto.claim_run(run_id)

    async def continue_same_targets():
        assert runner.start(session_id, _full_auto_claim=new_owner)
        await runner._task

    asyncio.run(continue_same_targets())
    assert fake.attempts == len(targets)
    assert db.q(
        "SELECT take_id, shot_id FROM full_auto_submission_receipt "
        "WHERE run_id = ? ORDER BY target_index",
        run_id,
    ) == links_before
    assert db.one("SELECT state FROM full_auto_run WHERE run_id = ?", run_id) == {
        "state": "succeeded",
    }
    assert db.one(
        "SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id,
    ) == {"n": len(targets)}


def test_cancel_during_assistant_post_keeps_physical_fence_and_discards_late_response(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = assistant_slots._create_run(
        client, seeded, photo_count=4,
    )
    owner, operation_id = assistant_slots._start_child(
        session_id, run_id, revision, targets,
    )
    fields = assistant_slots._fields_by_take(session_id, revision, targets)[targets[0]]
    entered = threading.Event()
    loop = asyncio.new_event_loop()
    release = asyncio.Event()

    async def respond(_request):
        entered.set()
        await release.wait()
        return assistant_slots._assistant_response(fields)

    assistant_slots._install_mock_transport(monkeypatch, respond)
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()
    task = asyncio.run_coroutine_threadsafe(
        main._run_prepare_takes_operation(
            session_id, operation_id, _full_auto_claim=owner,
        ),
        loop,
    )
    try:
        assert entered.wait(timeout=5), "the authorized assistant request did not start"
        slot = db.one(
            "SELECT state, inflight_reservation_id FROM full_auto_assistant_slot "
            "WHERE run_id = ? AND take_id = ?",
            run_id, targets[0],
        )
        assert slot is not None and slot["state"] == "active"
        reservation_id = slot["inflight_reservation_id"]
        assert reservation_id is not None
        assert db.one(
            "SELECT settled_at FROM full_auto_assistant_reservation WHERE reservation_id = ?",
            reservation_id,
        )["settled_at"] is None

        cancelled = client.post(
            f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
            json=_control_body(run_id),
        )
        assert cancelled.status_code == 202, cancelled.text
        assert cancelled.json()["state"] == "cancel_requested"
        quarantined = db.one(
            "SELECT state, inflight_reservation_id FROM full_auto_assistant_slot "
            "WHERE run_id = ? AND take_id = ?",
            run_id, targets[0],
        )
        assert quarantined == {
            "state": "quarantined",
            "inflight_reservation_id": reservation_id,
        }
    finally:
        loop.call_soon_threadsafe(release.set)
        task.result(timeout=10)
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=5)

    reservation = db.one(
        "SELECT settled_at FROM full_auto_assistant_reservation WHERE reservation_id = ?",
        reservation_id,
    )
    assert reservation["settled_at"] is not None
    assert not resource_full_auto._local_assistant_dispatch_live(reservation_id)
    assert db.q(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ?",
        session_id, revision,
    ) == []
    assert resource_full_auto.get_run(run_id)["state"] == "cancel_requested"


def test_http_cancel_with_pending_assistant_uses_real_scheduler_and_settles(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = assistant_slots._create_run(
        client, seeded, photo_count=4,
    )
    owner, operation_id = assistant_slots._start_child(
        session_id, run_id, revision, targets,
    )
    fields = assistant_slots._fields_by_take(session_id, revision, targets)[targets[0]]
    entered = threading.Event()
    loop = asyncio.new_event_loop()
    release = asyncio.Event()

    async def respond(_request):
        entered.set()
        await release.wait()
        return assistant_slots._assistant_response(fields)

    assistant_slots._install_mock_transport(monkeypatch, respond)
    monkeypatch.setattr(
        main, "_schedule_full_auto_cancellation", _REAL_CANCELLATION_SCHEDULER,
    )
    monkeypatch.setattr(
        resource_full_auto, "RUNNER_HEARTBEAT_INTERVAL", timedelta(milliseconds=20),
    )
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()
    task = asyncio.run_coroutine_threadsafe(
        main._run_prepare_takes_operation(
            session_id, operation_id, _full_auto_claim=owner,
        ),
        loop,
    )
    try:
        assert entered.wait(timeout=5), "the authorized assistant request did not start"
        reservation = db.one(
            "SELECT reservation_id FROM full_auto_assistant_reservation "
            "WHERE run_id = ? AND settled_at IS NULL",
            run_id,
        )
        assert reservation is not None

        response = client.post(
            f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
            json=_control_body(run_id),
        )
        assert response.status_code == 202, response.text
        assert response.json()["state"] == "cancel_requested"
        assert db.one(
            "SELECT settled_at FROM full_auto_assistant_reservation WHERE reservation_id = ?",
            reservation["reservation_id"],
        )["settled_at"] is None

        loop.call_soon_threadsafe(release.set)
        task.result(timeout=10)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if resource_full_auto.get_run(run_id)["state"] == "cancelled":
                break
            time.sleep(0.01)

        delayed_view = resource_full_auto.get_run(run_id)
        assert delayed_view["state"] == "cancelled", json.dumps({
            "view": delayed_view,
            "attempts": db.q(
                "SELECT attempt_id, dispatch_state, completion_state, prompt_id, error "
                "FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
            ),
            "shots": db.q(
                "SELECT id, status, filename, error FROM shot WHERE session_id = ? ORDER BY id",
                session_id,
            ),
            "runner_claim": db.one("SELECT * FROM global_runner_claim WHERE singleton = 1"),
            "scheduler_task": str(main._FULL_AUTO_CANCELLATION_TASKS.get(run_id)),
        }, default=str, indent=2)
        assert db.one(
            "SELECT settled_at FROM full_auto_assistant_reservation WHERE reservation_id = ?",
            reservation["reservation_id"],
        )["settled_at"] is not None
        assert db.q(
            "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ?",
            session_id, revision,
        ) == []
    finally:
        if not task.done():
            loop.call_soon_threadsafe(release.set)
            task.result(timeout=10)
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=5)


def test_explicit_resume_observing_a_verified_running_prompt_stays_cancel_requested(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    queued = asyncio.Event()
    original_queue = fake.queue_prompt

    async def record_queue(*args, **kwargs):
        prompt_id = await original_queue(*args, **kwargs)
        queued.set()
        return prompt_id

    async def remain_running(_prompt_id):
        return None

    scheduled = []
    monkeypatch.setattr(
        main, "_schedule_full_auto_cancellation",
        lambda scheduled_run, sid, claim: scheduled.append((scheduled_run, sid, claim)),
    )
    remote_reads = {"queue": 0, "history": 0}
    original_queue_state = fake.queue_state
    original_history_entries = fake.history_entries

    async def count_queue_state():
        remote_reads["queue"] += 1
        return await original_queue_state()

    async def count_history_entries():
        remote_reads["history"] += 1
        return await original_history_entries()

    fake.queue_prompt = record_queue
    fake.history = remain_running
    fake.queue_state = count_queue_state
    fake.history_entries = count_history_entries
    monkeypatch.setattr(runner_mod, "POLL_SECONDS", 0.001)

    async def start_and_cancel():
        assert runner.start(session_id, _full_auto_claim=owner)
        await asyncio.wait_for(queued.wait(), timeout=5)
        unrelated_prompt_id = "unrelated-running-prompt"
        fake.queue_records.append([
            99, unrelated_prompt_id, fake.queue_records[0][2],
            {"client_id": "unrelated-client"}, [],
        ])
        cancelled = await asyncio.to_thread(
            client.post,
            f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
            json=_control_body(run_id),
        )
        await asyncio.wait_for(runner._task, timeout=5)
        return cancelled, unrelated_prompt_id

    cancelled, unrelated_prompt_id = asyncio.run(start_and_cancel())
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["state"] == "cancel_requested"
    assert fake.attempts == 1
    assert len(fake.queue_records) == 2
    assert fake.interrupted == 0

    assert len(scheduled) == 1
    repeated = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
        json=_control_body(run_id),
    )
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()["state"] == "cancel_requested"
    assert len(scheduled) == 1
    # Full Auto observes the queue once before dispatch; cancellation itself
    # must not trigger another queue or history read.
    assert remote_reads == {"queue": 1, "history": 0}
    with pytest.raises(resource_full_auto.FullAutoRunError) as manual_edit:
        resource_full_auto.require_session_mutation_allowed(session_id)
    assert manual_edit.value.code == "full_auto_session_owned"
    manual_retry = client.post(f"/api/sessions/{session_id}/retry")
    assert manual_retry.status_code == 409, manual_retry.text
    owner_error = manual_retry.json()["detail"]
    assert owner_error["code"] == "full_auto_session_owned"
    assert owner_error["current"]["run_id"] == run_id
    assert owner_error["current"]["state"] == "cancel_requested"

    # Let the abandoned local leases expire while the remote prompt remains live.
    expired_clock = datetime.fromisoformat(db.now().replace("Z", "+00:00")) + timedelta(seconds=61)
    monkeypatch.setattr(db, "now", lambda: expired_clock.isoformat(timespec="seconds"))
    monkeypatch.setattr(main, "runner", runner)
    resumed = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=_control_body(run_id),
    )
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["state"] == "cancel_requested"
    assert len(scheduled) == 2
    scheduled_run, scheduled_session, claim = scheduled[-1]
    assert (scheduled_run, scheduled_session) == (run_id, session_id)
    assert claim.requires_reconciliation is True

    attempt = db.one(
        "SELECT attempt_id, prompt_id FROM full_auto_execution_attempt WHERE run_id = ?",
        run_id,
    )
    queue_by_id = {entry[1]: entry for entry in fake.queue_records}
    assert attempt is not None and attempt["prompt_id"] in queue_by_id
    assert queue_by_id[attempt["prompt_id"]][3]["idevgen_attempt"]["attempt_id"] == attempt["attempt_id"]
    assert unrelated_prompt_id in queue_by_id
    observations = []
    observed = asyncio.Event()
    original_reconcile = resource_full_auto.reconcile_cancellation

    def capture_observation(reconcile_claim, evidence):
        result = original_reconcile(reconcile_claim, evidence)
        observations.append(evidence)
        observed.set()
        return result

    monkeypatch.setattr(resource_full_auto, "reconcile_cancellation", capture_observation)

    async def reconcile_one_observation():
        task = asyncio.create_task(main._reconcile_full_auto_cancellation(
            run_id, session_id, claim,
        ))
        await asyncio.wait_for(observed.wait(), timeout=5)
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(reconcile_one_observation())
    current = resource_full_auto.get_run(run_id)
    assert observations and observations[0].outcome == "running", observations
    assert current["state"] == "cancel_requested", current
    assert current["cancel_intent"] is True
    assert fake.attempts == 1
    assert {entry[1] for entry in fake.queue_records} == {
        attempt["prompt_id"], unrelated_prompt_id,
    }
    assert fake.interrupted == 0


def test_stale_cancellation_reconciler_cannot_overwrite_new_fenced_settlement(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    queued = asyncio.Event()
    runner_history_entered = asyncio.Event()
    release_runner_history = asyncio.Event()
    cancel_history_entered = asyncio.Event()
    release_cancel_history = asyncio.Event()
    original_queue = fake.queue_prompt
    original_history = fake.history
    original_history_entries = fake.history_entries
    scheduled = []

    async def record_queue(*args, **kwargs):
        prompt_id = await original_queue(*args, **kwargs)
        queued.set()
        return prompt_id

    async def hold_runner_history(prompt_id):
        runner_history_entered.set()
        await release_runner_history.wait()
        return await original_history(prompt_id)

    history_entry_reads = 0

    async def hold_old_reconciliation_history():
        nonlocal history_entry_reads
        history_entry_reads += 1
        if history_entry_reads == 1:
            cancel_history_entered.set()
            await release_cancel_history.wait()
        return await original_history_entries()

    fake.queue_prompt = record_queue
    fake.history = hold_runner_history
    fake.history_entries = hold_old_reconciliation_history
    monkeypatch.setattr(main, "runner", runner)
    monkeypatch.setattr(runner_mod, "POLL_SECONDS", 0.001)
    monkeypatch.setattr(
        resource_full_auto, "RUNNER_HEARTBEAT_INTERVAL", timedelta(seconds=30),
    )
    monkeypatch.setattr(
        main, "_schedule_full_auto_cancellation",
        lambda scheduled_run, sid, claim: scheduled.append((scheduled_run, sid, claim)),
    )

    async def race_newer_fenced_settlement():
        assert runner.start(session_id, _full_auto_claim=owner)
        await asyncio.wait_for(queued.wait(), timeout=5)
        await asyncio.wait_for(runner_history_entered.wait(), timeout=5)

        cancelled = await asyncio.to_thread(
            client.post,
            f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
            json=_control_body(run_id),
        )
        assert cancelled.status_code == 202, cancelled.text
        assert cancelled.json()["state"] == "cancel_requested"
        assert len(scheduled) == 1
        old_claim = scheduled[0][2]

        # Let the original owner lose its runner lease after the request was
        # admitted. The fake provider completes the prompt, but the stale
        # runner must leave its durable shot journal unresolved for recovery.
        stale_time = "2000-01-01T00:00:00+00:00"
        global_claim = db.one(
            "SELECT owner_kind FROM global_runner_claim WHERE singleton = 1",
        )
        if global_claim["owner_kind"] is not None:
            db.run(
                "UPDATE global_runner_claim SET lease_started_at = ?, lease_expires_at = ? "
                "WHERE singleton = 1",
                stale_time, stale_time,
            )
        release_runner_history.set()
        await asyncio.wait_for(runner._task, timeout=5)

        old_task = asyncio.create_task(
            runner.reconcile_full_auto_cancellation(session_id, old_claim),
        )
        await asyncio.wait_for(cancel_history_entered.wait(), timeout=5)
        attempt = db.one(
            "SELECT attempt_id, prompt_id, completion_state FROM full_auto_execution_attempt "
            "WHERE run_id = ?",
            run_id,
        )
        assert attempt is not None and attempt["prompt_id"]
        assert attempt["completion_state"] == "pending"

        # A replacement reconciler claims the same run after fencing the old
        # lease. Expire only the old reconciler's runner claim while it is
        # awaiting provider evidence, then let the new owner recover the real
        # completed provider output.
        db.run(
            "UPDATE full_auto_run SET lease_started_at = ?, lease_expires_at = ? "
            "WHERE run_id = ?",
            stale_time, stale_time, run_id,
        )
        old_runner_claim = db.one(
            "SELECT owner_kind FROM global_runner_claim WHERE singleton = 1",
        )
        assert old_runner_claim["owner_kind"] == "full_auto", old_runner_claim
        db.run(
            "UPDATE global_runner_claim SET lease_started_at = ?, lease_expires_at = ? "
            "WHERE singleton = 1",
            stale_time, stale_time,
        )
        completed = fake.prompt_records[attempt["prompt_id"]]
        assert completed["status"]["completed"] is True
        newer_claim = resource_full_auto.claim_run(run_id)
        assert newer_claim.requires_reconciliation is True
        assert newer_claim.epoch == old_claim.epoch
        assert newer_claim.fencing_token > old_claim.fencing_token

        replacement = await runner.reconcile_full_auto_cancellation(
            session_id, newer_claim,
        )
        assert (replacement[0], replacement[2]) == ("settled", None), replacement
        settled = resource_full_auto.reconcile_cancellation(
            replacement[1],
            resource_full_auto.CancellationEvidence(
                "settled", db.now(), 0,
                attempt_ids=replacement[3], shot_ids=replacement[4],
            ),
        )
        assert settled["state"] == "cancelled"

        final_before_stale_returns = {
            "run": db.one(
                "SELECT state, epoch, fencing_token, cancel_intent FROM full_auto_run "
                "WHERE run_id = ?", run_id,
            ),
            "attempt": db.one(
                "SELECT completion_state, filename FROM full_auto_execution_attempt "
                "WHERE attempt_id = ?", attempt["attempt_id"],
            ),
            "shot": db.one(
                "SELECT status, filename, error FROM shot WHERE id = "
                "(SELECT shot_id FROM full_auto_execution_attempt WHERE attempt_id = ?)",
                attempt["attempt_id"],
            ),
        }
        release_cancel_history.set()
        old_result = await asyncio.wait_for(old_task, timeout=5)
        assert old_result[0] == "unknown", old_result
        final_after_stale_returns = {
            "run": db.one(
                "SELECT state, epoch, fencing_token, cancel_intent FROM full_auto_run "
                "WHERE run_id = ?", run_id,
            ),
            "attempt": db.one(
                "SELECT completion_state, filename FROM full_auto_execution_attempt "
                "WHERE attempt_id = ?", attempt["attempt_id"],
            ),
            "shot": db.one(
                "SELECT status, filename, error FROM shot WHERE id = "
                "(SELECT shot_id FROM full_auto_execution_attempt WHERE attempt_id = ?)",
                attempt["attempt_id"],
            ),
        }
        assert final_after_stale_returns == final_before_stale_returns
        return attempt

    try:
        attempt = asyncio.run(race_newer_fenced_settlement())
    finally:
        release_cancel_history.set()
        release_runner_history.set()
        if runner._task is not None and not runner._task.done():
            asyncio.run(runner._task)

    final_attempt = db.one(
        "SELECT completion_state, filename FROM full_auto_execution_attempt WHERE attempt_id = ?",
        attempt["attempt_id"],
    )
    final_shot = db.one(
        "SELECT status, filename FROM shot WHERE id = "
        "(SELECT shot_id FROM full_auto_execution_attempt WHERE attempt_id = ?)",
        attempt["attempt_id"],
    )
    assert resource_full_auto.get_run(run_id)["state"] == "cancelled"
    assert final_attempt["completion_state"] == "completed"
    assert final_shot["status"] == "done"
    assert final_shot["filename"] == final_attempt["filename"]
    assert (runner.sessions_dir / str(session_id) / final_shot["filename"]).is_file()
    assert fake.attempts == 1
    assert fake.interrupted == 0


def test_stale_cancellation_claim_is_revalidated_before_initial_transaction_writes(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    queued = asyncio.Event()
    history_entered = asyncio.Event()
    release_history = asyncio.Event()
    original_queue = fake.queue_prompt
    original_history = fake.history
    scheduled = []

    async def record_queue(*args, **kwargs):
        prompt_id = await original_queue(*args, **kwargs)
        queued.set()
        return prompt_id

    async def hold_history(prompt_id):
        history_entered.set()
        await release_history.wait()
        return await original_history(prompt_id)

    fake.queue_prompt = record_queue
    fake.history = hold_history
    monkeypatch.setattr(main, "runner", runner)
    monkeypatch.setattr(runner_mod, "POLL_SECONDS", 0.001)
    monkeypatch.setattr(
        resource_full_auto, "RUNNER_HEARTBEAT_INTERVAL", timedelta(seconds=30),
    )
    monkeypatch.setattr(
        main, "_schedule_full_auto_cancellation",
        lambda scheduled_run, sid, claim: scheduled.append((scheduled_run, sid, claim)),
    )
    original_transaction = db.transaction
    interception = {"armed": False, "hits": 0}
    replacement_claims = []
    prewrite_snapshot = []

    def gated_transaction():
        if interception["armed"]:
            interception["armed"] = False
            interception["hits"] += 1
            stale_time = "2000-01-01T00:00:00+00:00"
            db.run(
                "UPDATE full_auto_run SET lease_started_at = ?, lease_expires_at = ? "
                "WHERE run_id = ?",
                stale_time, stale_time, run_id,
            )
            replacement = resource_full_auto.claim_run(run_id)
            replacement_claims.append(replacement)
            prewrite_snapshot.append({
                "run": db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id),
                "session": db.one("SELECT * FROM session WHERE id = ?", session_id),
                "shots": db.q(
                    "SELECT * FROM shot WHERE session_id = ? ORDER BY id", session_id,
                ),
                "attempts": db.q(
                    "SELECT * FROM full_auto_execution_attempt WHERE run_id = ? "
                    "ORDER BY attempt_number", run_id,
                ),
            })
        return original_transaction()

    async def race_claim_expiry_before_transaction():
        assert runner.start(session_id, _full_auto_claim=owner)
        await asyncio.wait_for(queued.wait(), timeout=5)
        await asyncio.wait_for(history_entered.wait(), timeout=5)
        cancelled = await asyncio.to_thread(
            client.post,
            f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
            json=_control_body(run_id),
        )
        assert cancelled.status_code == 202, cancelled.text
        assert cancelled.json()["state"] == "cancel_requested"
        assert len(scheduled) == 1
        old_claim = scheduled[0][2]

        # Model a late stale writer that reopens one target after Cancel's
        # transaction. The reconciliation transaction must not cancel it
        # after its claim is replaced in the check-to-lock gap.
        pending = db.one(
            "SELECT id FROM shot WHERE session_id = ? AND status = 'cancelled' "
            "ORDER BY id LIMIT 1",
            session_id,
        )
        assert pending is not None
        db.run(
            "UPDATE shot SET status = 'pending', finished_at = '' WHERE id = ?",
            pending["id"],
        )
        db.run("UPDATE session SET status = 'running' WHERE id = ?", session_id)

        monkeypatch.setattr(db, "transaction", gated_transaction)
        interception["armed"] = True
        with pytest.raises(resource_full_auto.FullAutoRunError) as stale:
            await runner.reconcile_full_auto_cancellation(session_id, old_claim)
        assert stale.value.code in {"run_owner_stale", "run_lease_expired"}
        assert interception["hits"] == 1
        assert len(replacement_claims) == 1
        assert replacement_claims[0].fencing_token > old_claim.fencing_token
        assert prewrite_snapshot == [{
            "run": db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id),
            "session": db.one("SELECT * FROM session WHERE id = ?", session_id),
            "shots": db.q(
                "SELECT * FROM shot WHERE session_id = ? ORDER BY id", session_id,
            ),
            "attempts": db.q(
                "SELECT * FROM full_auto_execution_attempt WHERE run_id = ? "
                "ORDER BY attempt_number", run_id,
            ),
        }]
        assert db.one("SELECT status FROM shot WHERE id = ?", pending["id"]) == {
            "status": "pending",
        }
        assert db.one("SELECT status FROM session WHERE id = ?", session_id) == {
            "status": "running",
        }

    try:
        asyncio.run(race_claim_expiry_before_transaction())
    finally:
        monkeypatch.setattr(db, "transaction", original_transaction)
        release_history.set()
        if runner._task is not None and not runner._task.done():
            asyncio.run(runner._task)


def test_real_delayed_cancellation_scheduler_drains_without_interrupting_unrelated_job(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    queued = asyncio.Event()
    history_entered = asyncio.Event()
    original_queue = fake.queue_prompt
    original_history = fake.history

    async def record_queue(*args, **kwargs):
        prompt_id = await original_queue(*args, **kwargs)
        queued.set()
        return prompt_id

    async def remain_running(_prompt_id):
        history_entered.set()
        return None

    fake.queue_prompt = record_queue
    fake.history = remain_running
    monkeypatch.setattr(main, "runner", runner)
    reconciliations = []
    scheduler_reconciliations = []
    first_running_observation = asyncio.Event()
    original_reconcile = runner.reconcile_full_auto_cancellation
    original_scheduler_reconcile = main._reconcile_full_auto_cancellation
    delayed_schedules = []
    rescheduled_cancellations = []
    original_delay_scheduler = main._schedule_full_auto_cancellation_after_delay

    def record_rescheduled_cancellation(scheduled_run, sid, delayed_claim):
        rescheduled_cancellations.append((scheduled_run, sid, delayed_claim))
        return _REAL_CANCELLATION_SCHEDULER(scheduled_run, sid, delayed_claim)

    def record_delayed_schedule(scheduled_run, sid, delayed_claim):
        delayed_schedules.append((scheduled_run, sid, delayed_claim))
        return original_delay_scheduler(scheduled_run, sid, delayed_claim)

    async def record_scheduler_reconciliation(*args, **kwargs):
        scheduler_reconciliations.append("started")
        try:
            return await original_scheduler_reconcile(*args, **kwargs)
        finally:
            scheduler_reconciliations.append("finished")

    monkeypatch.setattr(
        main, "_reconcile_full_auto_cancellation", record_scheduler_reconciliation,
    )
    monkeypatch.setattr(
        main, "_schedule_full_auto_cancellation_after_delay", record_delayed_schedule,
    )

    async def record_reconciliation(*args, **kwargs):
        result = await original_reconcile(*args, **kwargs)
        reconciliations.append(result)
        if result[0] == "running":
            first_running_observation.set()
        return result

    monkeypatch.setattr(runner, "reconcile_full_auto_cancellation", record_reconciliation)
    monkeypatch.setattr(runner_mod, "POLL_SECONDS", 0.001)
    monkeypatch.setattr(
        resource_full_auto, "RUNNER_HEARTBEAT_INTERVAL", timedelta(milliseconds=20),
    )
    monkeypatch.setattr(
        resource_full_auto, "RUN_LEASE", timedelta(seconds=3),
    )
    monkeypatch.setattr(
        resource_full_auto, "RUNNER_LEASE", timedelta(seconds=3),
    )
    http_scheduler_calls = []
    monkeypatch.setattr(
        main, "_schedule_full_auto_cancellation",
        lambda scheduled_run, sid, claim: http_scheduler_calls.append(
            (scheduled_run, sid, claim),
        ),
    )

    async def start_cancel_and_drain():
        assert runner.start(session_id, _full_auto_claim=owner)
        await asyncio.wait_for(queued.wait(), timeout=5)
        await asyncio.wait_for(history_entered.wait(), timeout=5)
        response = await asyncio.to_thread(
            client.post,
            f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
            json=_control_body(run_id),
        )
        assert response.status_code == 202, response.text
        assert response.json()["state"] == "cancel_requested"
        assert len(http_scheduler_calls) == 1
        scheduled_run, scheduled_session, claim = http_scheduler_calls[0]
        assert (scheduled_run, scheduled_session) == (run_id, session_id)

        unrelated_prompt_id = "unrelated-comfy-job"
        fake.queue_records.append([
            77, unrelated_prompt_id, {"unrelated": {"class_type": "ExternalJob"}},
            {"client_id": "external-client"}, [],
        ])
        await asyncio.wait_for(runner._task, timeout=5)
        stale_runner_claim = db.one(
            "SELECT owner_kind FROM global_runner_claim WHERE singleton = 1",
        )
        if stale_runner_claim["owner_kind"] is not None:
            db.run(
                "UPDATE global_runner_claim SET lease_started_at = ?, lease_expires_at = ? "
                "WHERE singleton = 1",
                "1999-12-31T23:59:59+00:00", "2000-01-01T00:00:00+00:00",
            )
        monkeypatch.setattr(
            main, "_schedule_full_auto_cancellation", record_rescheduled_cancellation,
        )
        _REAL_CANCELLATION_SCHEDULER(run_id, session_id, claim)
        await asyncio.wait_for(first_running_observation.wait(), timeout=5)
        assert resource_full_auto.get_run(run_id)["state"] == "cancel_requested"
        assert fake.attempts == 1
        assert fake.interrupted == 0

        # Model the provider completing the already-owned prompt between the
        # first verified-running observation and the delayed read-only retry.
        attempt = db.one(
            "SELECT prompt_id FROM full_auto_execution_attempt WHERE run_id = ?",
            run_id,
        )
        assert attempt is not None and attempt["prompt_id"]
        completed_history = await original_history(attempt["prompt_id"])
        assert completed_history["status"]["completed"] is True
        fake.history = original_history

        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            if resource_full_auto.get_run(run_id)["state"] == "cancelled":
                break
            await asyncio.sleep(0.01)
        delayed_view = resource_full_auto.get_run(run_id)
        assert delayed_view["state"] == "cancelled", {
            "state": delayed_view["state"],
            "scheduler_reconciliations": scheduler_reconciliations,
            "delayed_schedules": len(delayed_schedules),
            "rescheduled_cancellations": len(rescheduled_cancellations),
            "reconciliations": [(row[0], row[2]) for row in reconciliations],
        }
        assert reconciliations[0][0] == "running", reconciliations
        assert any(row[0] == "settled" for row in reconciliations[1:]), reconciliations
        assert len(scheduler_reconciliations) == 4, scheduler_reconciliations
        assert len(delayed_schedules) == 1, delayed_schedules
        assert len(rescheduled_cancellations) == 1, rescheduled_cancellations

        attempt = db.one(
            "SELECT attempt_id, prompt_id, completion_state, filename "
            "FROM full_auto_execution_attempt WHERE run_id = ?",
            run_id,
        )
        shot = db.one("SELECT status, filename FROM shot WHERE id = "
                      "(SELECT shot_id FROM full_auto_execution_attempt WHERE run_id = ?)", run_id)
        assert attempt is not None and attempt["prompt_id"]
        assert attempt["completion_state"] == "completed"
        assert shot["status"] == "done"
        assert shot["filename"] == attempt["filename"]
        assert (runner.sessions_dir / str(session_id) / shot["filename"]).is_file()
        assert fake.attempts == 1
        assert fake.interrupted == 0
        assert unrelated_prompt_id in {row[1] for row in fake.queue_records}

    try:
        asyncio.run(start_cancel_and_drain())
    finally:
        if runner._task is not None and not runner._task.done():
            asyncio.run(runner._task)
        main._FULL_AUTO_CANCELLATION_TASKS.pop(run_id, None)


def test_cancel_outage_uses_three_probe_boundaries_and_fresh_heartbeat_claim(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    queued = asyncio.Event()
    original_queue = fake.queue_prompt
    history_mode = {"outage": False}
    outage_reads: list[datetime] = []
    history_observations: list[bool] = []

    async def record_queue(*args, **kwargs):
        prompt_id = await original_queue(*args, **kwargs)
        queued.set()
        return prompt_id

    original_history_entries = fake.history_entries

    async def history_entries():
        history_observations.append(history_mode["outage"])
        if history_mode["outage"]:
            outage_reads.append(datetime.fromisoformat(db.now().replace("Z", "+00:00")))
            raise httpx.ReadTimeout("synthetic ComfyUI history outage")
        return await original_history_entries()

    fake.queue_prompt = record_queue
    fake.history_entries = history_entries
    monkeypatch.setattr(runner_mod, "POLL_SECONDS", 0.001)

    async def start_and_cancel():
        assert runner.start(session_id, _full_auto_claim=owner)
        await asyncio.wait_for(queued.wait(), timeout=5)
        cancelled = await asyncio.to_thread(
            client.post,
            f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
            json=_control_body(run_id),
        )
        await asyncio.wait_for(runner._task, timeout=5)
        return cancelled

    cancelled = asyncio.run(start_and_cancel())
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["state"] == "cancel_requested"
    history_mode["outage"] = True

    original_now = db.now
    now = (
        datetime.fromisoformat(original_now().replace("Z", "+00:00"))
        .astimezone(timezone.utc) + timedelta(seconds=61)
    )

    def fake_now():
        return now.isoformat(timespec="seconds")

    real_sleep = asyncio.sleep

    async def advance_sleep(delay, *args, **kwargs):
        nonlocal now
        if delay >= 0.5:
            await real_sleep(0.02)
            now += timedelta(seconds=delay)
            await real_sleep(0)
        else:
            await real_sleep(delay)

    monkeypatch.setattr(db, "now", fake_now)
    monkeypatch.setattr(runner_mod.asyncio, "sleep", advance_sleep)
    monkeypatch.setattr(resource_full_auto, "RUN_LEASE", timedelta(seconds=3))
    monkeypatch.setattr(resource_full_auto, "RUNNER_LEASE", timedelta(seconds=3))
    monkeypatch.setattr(resource_full_auto, "RUN_HEARTBEAT_INTERVAL", timedelta(seconds=1))
    monkeypatch.setattr(
        resource_full_auto, "RUNNER_HEARTBEAT_INTERVAL", timedelta(milliseconds=10),
    )
    scheduled = []
    monkeypatch.setattr(main, "runner", runner)
    monkeypatch.setattr(
        main, "_schedule_full_auto_cancellation",
        lambda scheduled_run, sid, claim: scheduled.append((scheduled_run, sid, claim)),
    )
    resumed = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=_control_body(run_id),
    )
    assert resumed.status_code == 202, resumed.text
    assert len(scheduled) == 1
    scheduled_run, scheduled_session, claim = scheduled[0]
    assert (scheduled_run, scheduled_session) == (run_id, session_id)
    before_reconcile = db.one(
        "SELECT phase, resume_phase, state FROM full_auto_run WHERE run_id = ?", run_id,
    )
    assert before_reconcile == {
        "phase": "reconcile", "resume_phase": "execute", "state": "cancel_requested",
    }
    assert db.one(
        "SELECT run_id FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == {"run_id": run_id}
    assert db.one(
        "SELECT status FROM shot WHERE id = (SELECT shot_id FROM full_auto_execution_attempt "
        "WHERE run_id = ? ORDER BY attempt_number LIMIT 1)", run_id,
    ) == {"status": "running"}

    raw_outcomes = []
    original_reconcile = runner.reconcile_full_auto_cancellation

    async def capture_reconcile(*args, **kwargs):
        result = await original_reconcile(*args, **kwargs)
        raw_outcomes.append(result)
        return result

    runner.reconcile_full_auto_cancellation = capture_reconcile
    asyncio.run(main._reconcile_full_auto_cancellation(run_id, session_id, claim))
    latest_claim = raw_outcomes[0][1]
    assert latest_claim.lease_expires_at > claim.lease_expires_at
    assert len(outage_reads) == 4, {
        "reads": outage_reads,
        "runner_outcome": [
            (item[0], item[2], item[3], item[4]) for item in raw_outcomes
        ],
        "runner_context": {
            "run": db.one(
                "SELECT state, phase, resume_phase, cancel_intent FROM full_auto_run WHERE run_id = ?",
                run_id,
            ),
            "admission": db.one(
                "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id,
            ),
            "shots": db.q(
                "SELECT id, status FROM shot WHERE session_id = ? ORDER BY id", session_id,
            ),
            "attempts": db.q(
                "SELECT shot_id, run_epoch, dispatch_state, completion_state "
                "FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
            ),
        },
    }  # initial read, then the durable 5/15/30-second probes
    offsets = [int((item - outage_reads[0]).total_seconds()) for item in outage_reads]
    assert offsets == [0, 5, 20, 50]
    current = resource_full_auto.get_run(run_id)
    assert current["state"] == "blocked", current
    assert current["cancel_intent"] is True
    assert current["blocker_code"] == "comfy_unavailable"
    row = db.one(
        "SELECT comfy_probe_count, comfy_probe_due_at FROM full_auto_run WHERE run_id = ?",
        run_id,
    )
    assert row == {"comfy_probe_count": 3, "comfy_probe_due_at": None}
    evidence = json.loads(db.one(
        "SELECT evidence_json FROM full_auto_run WHERE run_id = ?", run_id,
    )["evidence_json"])
    assert evidence["attempt_ids"]
    assert evidence["shot_ids"]
    assert fake.attempts == 1
    assert fake.interrupted == 0

    # Reopen the actual SQLite file like a backend restart. Startup must leave
    # pending cancellation alone, preserving the exhausted probe budget.
    old_connection = db.conn()
    database_path = Path(old_connection.execute(
        "PRAGMA database_list",
    ).fetchone()["file"])
    old_connection.commit()
    db.connect(database_path)
    old_connection.close()
    assert db.one(
        "SELECT state, cancel_intent, comfy_probe_count, comfy_probe_due_at "
        "FROM full_auto_run WHERE run_id = ?",
        run_id,
    ) == {
        "state": "blocked", "cancel_intent": 1,
        "comfy_probe_count": 3, "comfy_probe_due_at": None,
    }
    startup_runs = []
    monkeypatch.setattr(main, "_schedule_full_auto_run", startup_runs.append)
    main._schedule_startup_full_auto_runs()
    assert startup_runs == []

    # Repeating Cancel after exhaustion is idempotent and must not poll. An
    # explicit Resume is a one-shot read-only observation, even while the
    # original grant has expired and planning is disabled.
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    expires_at = db.one(
        "SELECT authorization_expires_at FROM full_auto_run_receipt WHERE run_id = ?",
        run_id,
    )["authorization_expires_at"]
    now = datetime.fromisoformat(expires_at.replace("Z", "+00:00")) + timedelta(seconds=1)
    monkeypatch.setattr(main, "runner", runner)
    resumed_claims = []
    monkeypatch.setattr(
        main, "_schedule_full_auto_cancellation",
        lambda scheduled_run, sid, resume_claim: resumed_claims.append(
            (scheduled_run, sid, resume_claim),
        ),
    )
    history_before_repeat_cancel = len(history_observations)
    repeated_cancel = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
        json=_control_body(run_id),
    )
    assert repeated_cancel.status_code == 202, repeated_cancel.text
    assert repeated_cancel.json()["state"] == "blocked"
    assert len(history_observations) == history_before_repeat_cancel
    assert db.one(
        "SELECT comfy_probe_count FROM full_auto_run WHERE run_id = ?", run_id,
    ) == {"comfy_probe_count": 3}

    history_mode["outage"] = True
    before_manual_outage_resume = len(history_observations)
    resumed = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=_control_body(run_id),
    )
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["state"] == "blocked"
    assert len(resumed_claims) == 1
    scheduled_run, scheduled_session, resumed_claim = resumed_claims[0]
    assert (scheduled_run, scheduled_session) == (run_id, session_id)
    assert resumed_claim.requires_reconciliation is True
    outage_before_manual_resume = len(outage_reads)
    delayed_reconciliations = []
    monkeypatch.setattr(
        main, "_schedule_full_auto_cancellation_after_delay",
        lambda scheduled_run, sid, delayed_claim: delayed_reconciliations.append(
            (scheduled_run, sid, delayed_claim),
        ),
    )
    asyncio.run(main._reconcile_full_auto_cancellation(
        run_id, session_id, resumed_claim,
    ))
    assert len(history_observations) == before_manual_outage_resume + 1
    assert len(outage_reads) == outage_before_manual_resume + 1
    assert resource_full_auto.get_run(run_id)["state"] == "blocked"
    assert db.one(
        "SELECT cancel_intent, comfy_probe_count, comfy_probe_due_at "
        "FROM full_auto_run WHERE run_id = ?", run_id,
    ) == {"cancel_intent": 1, "comfy_probe_count": 3, "comfy_probe_due_at": None}
    assert delayed_reconciliations == []
    assert fake.attempts == 1
    assert fake.interrupted == 0

    # Once ComfyUI recovers, another explicit Resume can observe the existing
    # attempt's result without re-authorizing or starting another generation.
    history_mode["outage"] = False
    before_recovered_resume = len(history_observations)
    resumed_again = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=_control_body(run_id),
    )
    assert resumed_again.status_code == 202, resumed_again.text
    assert resumed_again.json()["state"] == "blocked"
    assert len(resumed_claims) == 2
    resumed_claim = resumed_claims[1][2]

    attempt = db.one(
        "SELECT attempt_id, prompt_id FROM full_auto_execution_attempt WHERE run_id = ?",
        run_id,
    )
    assert attempt is not None and attempt["prompt_id"]
    awaitable_result = asyncio.run(fake.history(attempt["prompt_id"]))
    assert awaitable_result is not None
    asyncio.run(main._reconcile_full_auto_cancellation(
        run_id, session_id, resumed_claim,
    ))
    assert len(history_observations) == before_recovered_resume + 1
    post_restart_view = resource_full_auto.get_run(run_id)
    assert post_restart_view["state"] == "cancelled", json.dumps({
        "view": post_restart_view,
        "attempts": db.q(
            "SELECT attempt_id, dispatch_state, completion_state, prompt_id, error "
            "FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
        ),
        "shots": db.q(
            "SELECT id, status, filename, error FROM shot WHERE session_id = ? ORDER BY id",
            session_id,
        ),
        "runner_claim": db.one("SELECT * FROM global_runner_claim WHERE singleton = 1"),
        "stored_state": db.one(
            "SELECT state, blocker_code, lease_started_at, lease_expires_at, "
            "comfy_probe_count, comfy_probe_due_at FROM full_auto_run WHERE run_id = ?",
            run_id,
        ),
    }, default=str, indent=2)
    final_attempt = db.one(
        "SELECT completion_state, filename FROM full_auto_execution_attempt WHERE attempt_id = ?",
        attempt["attempt_id"],
    )
    final_shot = db.one(
        "SELECT id, status, filename FROM shot WHERE id = "
        "(SELECT shot_id FROM full_auto_execution_attempt WHERE attempt_id = ?)",
        attempt["attempt_id"],
    )
    assert final_attempt["completion_state"] == "completed"
    assert final_shot["status"] == "done"
    assert final_shot["filename"] == final_attempt["filename"]
    assert (runner.sessions_dir / str(session_id) / final_shot["filename"]).is_file()
    assert fake.attempts == 1
    assert fake.interrupted == 0
