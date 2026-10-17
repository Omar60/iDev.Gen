"""Independent acceptance for Full Auto's external ComfyUI queue boundary."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta
import json
import time
import uuid

import pytest

import db
import main
import runner as runner_mod
from backend import guided_sessions, resource_full_auto
import test_full_auto_submission_independent as submission


_SCHEDULE_FULL_AUTO_RUN = main._schedule_full_auto_run
_SCHEDULE_FULL_AUTO_RUN_AFTER_DELAY = main._schedule_full_auto_run_after_delay
_SCHEDULE_STARTUP_FULL_AUTO_RUNS = main._schedule_startup_full_auto_runs


@pytest.fixture(autouse=True)
def _configured_assistant(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "external-queue-test")
    monkeypatch.setitem(main.CONFIG, "llm_key", "external-queue-test-key")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)


def _approved_execution(client, seeded, monkeypatch, *, photo_count: int = 1):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    session_id, run_id, _revision, targets, submit_owner = submission._approved_run(
        client, seeded, monkeypatch, photo_count=max(4, photo_count),
    )
    _view, execute_owner = main._run_full_auto_submission(submit_owner)
    return session_id, run_id, targets, execute_owner


def _external_prompt(prompt_id: str = "other-client-job") -> list:
    return [17, prompt_id, {}, {"client_id": "other-client"}, []]


async def _eventually(predicate, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition did not become true before timeout")
        await asyncio.sleep(0.001)


def _view_keys() -> set[str]:
    return {
        "run_id", "session_id", "revision", "epoch", "plan_revision", "state",
        "phase", "cancel_intent", "blocker_code", "waiting_reason", "created_at",
        "updated_at",
    }


@pytest.mark.parametrize("external_state", ["queue_pending", "queue_running"])
def test_external_queue_wait_is_safe_and_poll_deadline_survives_restart(
    client, seeded, monkeypatch, make_runner, external_state,
):
    session_id, run_id, targets, execute_owner = _approved_execution(
        client, seeded, monkeypatch,
    )
    runner, fake = make_runner()
    monkeypatch.setattr(main, "runner", runner)
    resource_full_auto.advance_run(execute_owner, "waiting_runner")

    now = datetime.fromisoformat(db.now())
    clock = [now]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    monkeypatch.setattr(main, "_FULL_AUTO_COORDINATOR_RETRY_SECONDS", 0.001)
    external = _external_prompt()
    external_queues = {"queue_running": [], "queue_pending": []}
    external_queues[external_state].append(copy.deepcopy(external))
    admission_reads: list[float] = []

    async def queue_state():
        if fake.attempts == 0:
            admission_reads.append(time.monotonic())
        return {
            "queue_running": copy.deepcopy(fake.queue_records + external_queues["queue_running"]),
            "queue_pending": copy.deepcopy(external_queues["queue_pending"]),
        }

    fake.queue_state = queue_state
    monkeypatch.setattr(main, "_schedule_full_auto_run", _SCHEDULE_FULL_AUTO_RUN)
    requested_delays: list[float] = []

    def schedule_after_delay(run_id: str, delay: float) -> None:
        requested_delays.append(delay)
        _SCHEDULE_FULL_AUTO_RUN_AFTER_DELAY(
            run_id, 2.1 if delay >= 15.0 else delay,
        )

    monkeypatch.setattr(main, "_schedule_full_auto_run_after_delay", schedule_after_delay)

    async def observe_wait_and_restart():
        main._schedule_full_auto_run(run_id)
        await _eventually(lambda: bool(admission_reads))
        await _eventually(
            lambda: (
                (row := db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)) is not None
                and row["state"] == "waiting_runner"
                and row["lease_expires_at"] is None
                and resource_full_auto.get_run(run_id)["waiting_reason"] == "external_queue_busy"
            ),
        )
        await asyncio.sleep(0.02)
        assert len(admission_reads) == 1

        run_view = resource_full_auto.get_run(run_id)
        assert set(run_view) == _view_keys()
        assert run_view["waiting_reason"] == "external_queue_busy"
        row = db.one("SELECT evidence_json FROM full_auto_run WHERE run_id = ?", run_id)
        evidence = json.loads(row["evidence_json"])
        assert evidence == {
            "kind": "runner_wait",
            "reason": "external_queue_busy",
            "next_poll_at": (clock[0] + timedelta(seconds=15)).isoformat(timespec="seconds"),
        }
        public = json.dumps(run_view, sort_keys=True)
        assert "next_poll_at" not in public
        assert external[1] not in public

        runner_slot = db.one("SELECT * FROM global_runner_claim WHERE singleton = 1")
        assert runner_slot["owner_kind"] is None
        assert fake.attempts == 0
        assert db.q(
            "SELECT * FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
        ) == []
        assert db.q(
            "SELECT status FROM shot WHERE session_id = ? ORDER BY id", session_id,
        ) == [{"status": "pending"}] * len(targets)
        assert external_queues[external_state] == [external]
        assert fake.interrupted == 0

        # Stop the in-process deferred timer to simulate process loss, then let
        # startup rediscover the durable waiter and preserve its poll deadline.
        deferred = main._FULL_AUTO_RUN_TASKS[run_id]
        assert deferred is not None and not deferred.done()
        deferred.cancel()
        with pytest.raises(asyncio.CancelledError):
            await deferred
        await asyncio.sleep(0)
        assert run_id not in main._FULL_AUTO_RUN_TASKS
        before_restart_revision = run_view["revision"]
        monkeypatch.setattr(
            main, "_schedule_startup_full_auto_runs", _SCHEDULE_STARTUP_FULL_AUTO_RUNS,
        )
        _SCHEDULE_STARTUP_FULL_AUTO_RUNS()
        await _eventually(
            lambda: (
                (row := db.one("SELECT revision, lease_expires_at FROM full_auto_run WHERE run_id = ?", run_id))
                is not None
                and row["revision"] > before_restart_revision
                and row["lease_expires_at"] is None
            ),
        )
        assert len(admission_reads) == 1
        assert 15.0 in requested_delays
        await asyncio.sleep(2.02)
        assert len(admission_reads) == 1

        external_queues["queue_running"].clear()
        external_queues["queue_pending"].clear()
        clock[0] = datetime.fromisoformat(evidence["next_poll_at"])
        await _eventually(
            lambda: len(admission_reads) == 2,
            timeout=5,
        )
        await _eventually(
            lambda: resource_full_auto.get_run(run_id)["state"] == "succeeded",
            timeout=20,
        )
        await _eventually(lambda: run_id not in main._FULL_AUTO_RUN_TASKS)

    asyncio.run(observe_wait_and_restart())
    assert len(admission_reads) == 2
    assert fake.attempts == len(targets) == 4
    assert resource_full_auto.get_run(run_id)["state"] == "succeeded"
    assert db.q(
        "SELECT status FROM shot WHERE session_id = ?", session_id,
    ) == [{"status": "done"}] * len(targets)
    assert fake.interrupted == 0


def test_external_busy_after_first_shot_keeps_admission_and_fifo_order(
    client, seeded, monkeypatch, make_runner,
):
    session_a, run_a, targets_a, owner_a = _approved_execution(
        client, seeded, monkeypatch, photo_count=5,
    )
    session_b, run_b, targets_b, owner_b = _approved_execution(
        client, seeded, monkeypatch, photo_count=4,
    )
    runner, fake = make_runner()
    monkeypatch.setattr(main, "runner", runner)
    monkeypatch.setattr(main, "_schedule_full_auto_run", _SCHEDULE_FULL_AUTO_RUN)
    monkeypatch.setattr(main, "_FULL_AUTO_COORDINATOR_RETRY_SECONDS", 0.001)
    monkeypatch.setattr(runner_mod, "POLL_SECONDS", 0.001)

    requested_delays: list[float] = []

    def schedule_after_delay(run_id: str, delay: float) -> None:
        requested_delays.append(delay)
        # Keep the real deferred-task and task-registry path, while shortening
        # the persisted 15-second poll for this focused scheduler boundary.
        _SCHEDULE_FULL_AUTO_RUN_AFTER_DELAY(
            run_id, 2.1 if delay >= 15.0 else delay,
        )

    monkeypatch.setattr(main, "_schedule_full_auto_run_after_delay", schedule_after_delay)

    now = datetime.fromisoformat(db.now())
    clock = [now]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    resource_full_auto.advance_run(
        owner_a, "waiting_runner", now_text=clock[0].isoformat(timespec="seconds"),
    )

    external_pending: list[list] = []
    queue_reads: list[str] = []
    posted_prefixes: list[str] = []
    original_prompt = fake.queue_prompt
    original_history = fake.history
    first_completion = [True]

    async def queue_state():
        queue_reads.append("read")
        own = copy.deepcopy(fake.queue_records)
        return {
            "queue_running": [],
            "queue_pending": copy.deepcopy(external_pending) + own,
        }

    async def queue_prompt(graph, client_id, **kwargs):
        posted_prefixes.append(graph["8"]["inputs"]["filename_prefix"])
        return await original_prompt(graph, client_id, **kwargs)

    async def history(prompt_id):
        result = await original_history(prompt_id)
        if first_completion[0] and result is not None:
            first_completion[0] = False
            external_pending.append(_external_prompt("external-after-first-shot"))
        return result

    fake.queue_state = queue_state
    fake.queue_prompt = queue_prompt
    fake.history = history

    async def coordinate_both():
        main._schedule_full_auto_run(run_a)
        await _eventually(
            lambda: (
                resource_full_auto.get_run(run_a)["waiting_reason"] == "external_queue_busy"
                and db.one(
                    "SELECT COUNT(*) AS n FROM shot WHERE session_id = ? AND status = 'done'",
                    session_a,
                )["n"] == 1
            ),
        )
        admission_before = db.one(
            "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_a,
        )
        assert external_pending == [_external_prompt("external-after-first-shot")]
        assert fake.interrupted == 0
        assert admission_before is not None
        assert db.one(
            "SELECT owner_kind FROM global_runner_claim WHERE singleton = 1",
        )["owner_kind"] is None

        clock[0] += timedelta(seconds=1)
        resource_full_auto.advance_run(
            owner_b, "waiting_runner", now_text=clock[0].isoformat(timespec="seconds"),
        )
        main._schedule_full_auto_run(run_b)
        await _eventually(
            lambda: resource_full_auto.get_run(run_b)["waiting_reason"] == "runner_busy",
        )
        reads_before_due = len(queue_reads)
        await asyncio.sleep(2.02)
        assert len(queue_reads) == reads_before_due == 2
        assert fake.attempts == 1
        scheduled_a = main._FULL_AUTO_RUN_TASKS.get(run_a)
        assert scheduled_a is not None and not scheduled_a.done()
        assert 15.0 in requested_delays

        external_pending.clear()
        clock[0] = now + timedelta(seconds=15)
        await _eventually(lambda: len(queue_reads) > reads_before_due, timeout=5)
        await _eventually(
            lambda: (
                resource_full_auto.get_run(run_a)["state"] == "succeeded"
                and resource_full_auto.get_run(run_b)["state"] == "succeeded"
            ),
        )
        await _eventually(
            lambda: run_a not in main._FULL_AUTO_RUN_TASKS
            and run_b not in main._FULL_AUTO_RUN_TASKS,
        )

        admission_after = db.one(
            "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_a,
        )
        assert admission_after == admission_before

    asyncio.run(coordinate_both())
    assert len(posted_prefixes) == len(targets_a) + len(targets_b) == 9
    assert [int(prefix.split("/")[1]) for prefix in posted_prefixes] == [
        *([session_a] * len(targets_a)), *([session_b] * len(targets_b)),
    ]
    assert fake.attempts == len(targets_a) + len(targets_b) == 9
    for run_id, session_id, expected_count in (
        (run_a, session_a, 5), (run_b, session_b, 4),
    ):
        attempts = db.q(
            "SELECT * FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY shot_id",
            run_id,
        )
        assert len(attempts) == expected_count
        assert len({attempt["shot_id"] for attempt in attempts}) == expected_count
        assert all(attempt["attempt_number"] == 1 for attempt in attempts)
        assert db.q(
            "SELECT status FROM shot WHERE session_id = ? ORDER BY id", session_id,
        ) == [{"status": "done"}] * expected_count
        assert resource_full_auto.get_run(run_id)["state"] == "succeeded"
    assert fake.interrupted == 0


@pytest.mark.parametrize("settlement", ["running_observed", "running_missed"])
def test_external_race_uses_one_normal_priority_post_and_excludes_queue_time_from_timeout(
    client, seeded, monkeypatch, make_runner, settlement,
):
    session_id, run_id, _targets, execute_owner = _approved_execution(
        client, seeded, monkeypatch,
    )
    runner, fake = make_runner()
    monkeypatch.setattr(main, "runner", runner)
    monkeypatch.setattr(main, "_FULL_AUTO_COORDINATOR_RETRY_SECONDS", 0.001)
    monkeypatch.setattr(runner_mod, "POLL_SECONDS", 0.001)
    monkeypatch.setattr(runner_mod, "CANCELLATION_QUEUE_POLL_SECONDS", 0.005)
    monkeypatch.setattr(runner_mod, "SHOT_TIMEOUT", 0.25)
    resource_full_auto.advance_run(execute_owner, "waiting_runner")

    external_running = []
    race_arrival = [False]
    own_running = [False]
    history_ready = [False]
    pending_seen = asyncio.Event()
    own_running_seen = asyncio.Event()
    queue_reads: list[dict] = []
    prompt_kwargs: list[dict] = []
    original_prompt = fake.queue_prompt
    original_history = fake.history

    async def queue_state():
        result = {"queue_running": copy.deepcopy(external_running), "queue_pending": []}
        if fake.queue_records:
            own = copy.deepcopy(fake.queue_records)
            if own_running[0]:
                own_running_seen.set()
                result["queue_running"].extend(own)
            else:
                pending_seen.set()
                result["queue_pending"].extend(own)
        queue_reads.append(copy.deepcopy(result))
        return result

    async def queue_prompt(graph, client_id, **kwargs):
        # The external job arrives after the app's empty observation but before
        # the server accepts this single normal-priority prompt.
        if not race_arrival[0]:
            race_arrival[0] = True
            external_running.append(_external_prompt("external-race-winner"))
        prompt_kwargs.append(copy.deepcopy(kwargs))
        return await original_prompt(graph, client_id, **kwargs)

    async def history(prompt_id):
        if not history_ready[0]:
            return None
        return await original_history(prompt_id)

    fake.queue_state = queue_state
    fake.queue_prompt = queue_prompt
    fake.history = history

    async def observe_race_and_settle():
        coordinator = asyncio.create_task(main._coordinate_full_auto_run(run_id))
        await asyncio.wait_for(pending_seen.wait(), timeout=5)
        await asyncio.sleep(0.35)
        assert not coordinator.done()
        assert fake.attempts == 1
        assert resource_full_auto.get_run(run_id)["state"] == "active"

        if settlement == "running_observed":
            own_running[0] = True
            await asyncio.wait_for(own_running_seen.wait(), timeout=2)
            await asyncio.sleep(0.05)
            history_ready[0] = True
        else:
            # The prompt completes between observations; its history is still
            # sufficient evidence even though no running snapshot was seen.
            fake.queue_records.clear()
            history_ready[0] = True

        await _eventually(
            lambda: (
                resource_full_auto.get_run(run_id)["state"] == "waiting_runner"
                and resource_full_auto.get_run(run_id)["waiting_reason"] == "external_queue_busy"
            ),
        )
        coordinator.cancel()
        with pytest.raises(asyncio.CancelledError):
            await coordinator

    asyncio.run(observe_race_and_settle())
    assert fake.attempts == 1
    assert len(prompt_kwargs) == 1
    assert not {"priority", "front", "number", "queue_position"} & set(prompt_kwargs[0])
    assert external_running == [_external_prompt("external-race-winner")]
    assert fake.interrupted == 0
    attempt = db.one(
        "SELECT dispatch_state, completion_state FROM full_auto_execution_attempt WHERE run_id = ?",
        run_id,
    )
    assert attempt == {"dispatch_state": "acknowledged", "completion_state": "completed"}
    assert db.one(
        "SELECT status FROM shot WHERE session_id = ?", session_id,
    )["status"] == "done"
    waiting = resource_full_auto.get_run(run_id)
    assert waiting["state"] == "waiting_runner"
    assert waiting["waiting_reason"] == "external_queue_busy"
    assert db.q(
        "SELECT status FROM shot WHERE session_id = ? ORDER BY id", session_id,
    ) == [{"status": "done"}] + [{"status": "pending"}] * (len(_targets) - 1)
    if settlement == "running_missed":
        assert not own_running_seen.is_set()


def test_owned_running_attempt_still_times_out_without_replay_or_settlement(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, targets, execute_owner = _approved_execution(
        client, seeded, monkeypatch,
    )
    runner, fake = make_runner()
    monkeypatch.setattr(main, "runner", runner)
    poll_seconds = 0.02
    shot_timeout = 0.12
    fake_clock = [0.0]
    observed_at: list[float] = []

    class FakeLoopClock:
        def time(self) -> float:
            return fake_clock[0]

    class RunnerAsyncioProxy:
        def get_running_loop(self):
            return FakeLoopClock()

        def __getattr__(self, name):
            return getattr(asyncio, name)

    monkeypatch.setattr(runner_mod, "POLL_SECONDS", poll_seconds)
    monkeypatch.setattr(runner_mod, "CANCELLATION_QUEUE_POLL_SECONDS", 0.005)
    monkeypatch.setattr(runner_mod, "SHOT_TIMEOUT", shot_timeout)
    monkeypatch.setattr(runner_mod, "asyncio", RunnerAsyncioProxy())
    resource_full_auto.advance_run(execute_owner, "waiting_runner")

    async def queue_state():
        if fake.attempts == 0 or not fake.queue_records:
            return {"queue_running": [], "queue_pending": []}
        if not observed_at:
            observed_at.append(fake_clock[0])
        return {
            "queue_running": copy.deepcopy(fake.queue_records),
            "queue_pending": [],
        }

    async def never_complete(_prompt_id):
        fake_clock[0] += poll_seconds
        return None

    fake.queue_state = queue_state
    fake.history = never_complete

    async def wait_for_timeout():
        coordinator = asyncio.create_task(main._coordinate_full_auto_run(run_id))
        await asyncio.wait_for(coordinator, timeout=3)

    asyncio.run(wait_for_timeout())
    elapsed_after_owned_running = fake_clock[0] - observed_at[0]
    run_view = resource_full_auto.get_run(run_id)
    assert run_view["state"] == "blocked"
    assert run_view["blocker_code"] == "execution_outcome_unknown"
    assert elapsed_after_owned_running >= shot_timeout, (
        f"owned execution timed out after {elapsed_after_owned_running:.4f}s; "
        f"configured timeout is {shot_timeout:.4f}s"
    )
    assert fake.attempts == 1
    attempt = db.one(
        "SELECT dispatch_state, completion_state FROM full_auto_execution_attempt WHERE run_id = ?",
        run_id,
    )
    assert attempt == {"dispatch_state": "acknowledged", "completion_state": "pending"}
    assert db.q(
        "SELECT status FROM shot WHERE session_id = ? ORDER BY id", session_id,
    ) == [{"status": "running"}] + [{"status": "pending"}] * (len(targets) - 1)
    assert fake.interrupted == 0


@pytest.mark.parametrize("change", ["cancel", "stale_owner", "expiry", "disablement"])
def test_authority_change_during_external_queue_read_prevents_dispatch(
    client, seeded, monkeypatch, make_runner, change,
):
    if change == "expiry":
        monkeypatch.setattr(
            guided_sessions, "_FULL_AUTO_AUTHORIZATION_LIFETIME", timedelta(seconds=5),
        )
    if change == "disablement":
        monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    session_id, run_id, _targets, execute_owner = _approved_execution(
        client, seeded, monkeypatch,
    )
    runner, fake = make_runner()
    monkeypatch.setattr(main, "runner", runner)
    monkeypatch.setattr(main, "_FULL_AUTO_COORDINATOR_RETRY_SECONDS", 0.001)
    resource_full_auto.advance_run(execute_owner, "waiting_runner")

    entered = asyncio.Event()
    release = asyncio.Event()
    queue_calls = []

    async def delayed_queue_state():
        queue_calls.append(True)
        entered.set()
        await release.wait()
        return {"queue_running": [], "queue_pending": []}

    fake.queue_state = delayed_queue_state

    async def invalidate_while_observing():
        coordinator = asyncio.create_task(main._coordinate_full_auto_run(run_id))
        await asyncio.wait_for(entered.wait(), timeout=5)
        if change == "cancel":
            current = resource_full_auto.get_run(run_id)
            result = resource_full_auto.cancel_run(
                run_id, str(uuid.uuid4()), current["revision"], current["plan_revision"],
                work_pending=False, expected_session_id=session_id,
            )
            assert result.view["state"] == "cancelled"
        elif change == "stale_owner":
            db.run(
                "UPDATE full_auto_run SET fencing_token = fencing_token + 1 WHERE run_id = ?",
                run_id,
            )
        elif change == "expiry":
            expired_at = datetime.fromisoformat(db.now()) + timedelta(seconds=6)
            monkeypatch.setattr(
                db, "now", lambda: expired_at.isoformat(timespec="seconds"),
            )
        else:
            monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
        release.set()
        await asyncio.wait_for(coordinator, timeout=5)

    asyncio.run(invalidate_while_observing())
    assert len(queue_calls) == 1
    assert fake.attempts == 0
    assert db.q(
        "SELECT * FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    ) == []
    assert db.q(
        "SELECT status FROM shot WHERE session_id = ?", session_id,
    )[0]["status"] != "done"
    assert fake.interrupted == 0


def test_waiting_reason_projection_is_closed_and_legacy_action_replay_defaults_null(
    client, seeded, monkeypatch,
):
    session_id, run_id, _targets, execute_owner = _approved_execution(
        client, seeded, monkeypatch,
    )
    before = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    with pytest.raises(resource_full_auto.FullAutoRunError) as invalid_reason:
        resource_full_auto.advance_run(
            execute_owner, "waiting_runner", waiting_reason="clear_remote_queue",
        )
    assert invalid_reason.value.code == "invalid_waiting_reason"
    assert db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id) == before

    current = resource_full_auto.get_run(run_id)
    request_id = str(uuid.uuid4())
    result = resource_full_auto.cancel_run(
        run_id, request_id, current["revision"], current["plan_revision"],
        work_pending=False, expected_session_id=session_id,
    )
    assert result.view["waiting_reason"] is None
    original_one = db.one

    def read_legacy_receipt(sql, *args):
        receipt = original_one(sql, *args)
        if receipt is not None and "FROM full_auto_run_action_receipt" in sql:
            legacy_view = json.loads(receipt["response_json"])
            legacy_view.pop("waiting_reason", None)
            receipt["response_json"] = json.dumps(
                legacy_view, sort_keys=True, separators=(",", ":"),
            )
        return receipt

    monkeypatch.setattr(db, "one", read_legacy_receipt)
    replay = resource_full_auto.cancel_run(
        run_id, request_id, current["revision"], current["plan_revision"],
        work_pending=False, expected_session_id=session_id,
    )
    assert replay.replayed is True
    assert set(replay.view) == _view_keys() | {"action", "request_id"}
    assert replay.view["waiting_reason"] is None
    assert replay.view["state"] == "cancelled"


@pytest.mark.parametrize("invalid_reason", [[], {}])
def test_malformed_persisted_waiting_reason_is_rejected_as_safe_status_error(
    client, seeded, monkeypatch, invalid_reason,
):
    _session_id, run_id, _targets, execute_owner = _approved_execution(
        client, seeded, monkeypatch,
    )
    resource_full_auto.advance_run(execute_owner, "waiting_runner", waiting_reason="runner_busy")
    db.run(
        "UPDATE full_auto_run SET evidence_json = ? WHERE run_id = ?",
        json.dumps({"kind": "runner_wait", "reason": invalid_reason}), run_id,
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as malformed:
        resource_full_auto.get_run(run_id)
    assert malformed.value.code == "run_state_invalid"


@pytest.mark.parametrize("delay_point", ["admission_read", "final_authority_check"])
def test_external_queue_wait_refuses_owner_that_expires_before_final_write(
    client, seeded, monkeypatch, delay_point,
):
    _session_id, run_id, _targets, execute_owner = _approved_execution(
        client, seeded, monkeypatch,
    )
    clock = [datetime.fromisoformat(db.now())]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    if delay_point == "admission_read":
        original_one = db.one

        def expire_after_admission_read(sql, *args):
            row = original_one(sql, *args)
            if "SELECT admitted_at FROM full_auto_runner_admission" in sql:
                clock[0] += timedelta(seconds=61)
            return row

        monkeypatch.setattr(db, "one", expire_after_admission_read)
    else:
        original_authority_check = resource_full_auto._current_authority_issue
        checks = [0]

        def expire_during_final_authority_check(row, timestamp):
            checks[0] += 1
            issue = original_authority_check(row, timestamp)
            if checks[0] == 2:
                clock[0] += timedelta(seconds=61)
            return issue

        monkeypatch.setattr(
            resource_full_auto, "_current_authority_issue", expire_during_final_authority_check,
        )
    with pytest.raises(resource_full_auto.FullAutoRunError) as expired:
        resource_full_auto.wait_for_external_runner_queue(execute_owner)
    assert expired.value.code == "run_lease_expired"
    row = db.one("SELECT state, lease_expires_at FROM full_auto_run WHERE run_id = ?", run_id)
    assert row["state"] == "active"
    assert row["lease_expires_at"] is not None


@pytest.mark.parametrize("control", ["cancel", "blocked"])
def test_deferred_runner_reentry_stops_after_cancel_or_block(
    client, seeded, monkeypatch, control,
):
    session_id, run_id, _targets, execute_owner = _approved_execution(
        client, seeded, monkeypatch,
    )
    monkeypatch.setattr(main, "_schedule_full_auto_run", _SCHEDULE_FULL_AUTO_RUN)
    monkeypatch.setattr(
        main, "_schedule_full_auto_run_after_delay", _SCHEDULE_FULL_AUTO_RUN_AFTER_DELAY,
    )
    unexpected_start = asyncio.Event()

    async def should_not_start(_run_id):
        unexpected_start.set()

    monkeypatch.setattr(main, "_coordinate_full_auto_run", should_not_start)

    async def stop_before_due():
        main._schedule_full_auto_run_after_delay(run_id, 0.01)
        delayed = main._FULL_AUTO_RUN_TASKS[run_id]
        if control == "cancel":
            current = resource_full_auto.get_run(run_id)
            result = resource_full_auto.cancel_run(
                run_id, str(uuid.uuid4()), current["revision"], current["plan_revision"],
                work_pending=False, expected_session_id=session_id,
            )
            assert result.view["state"] == "cancelled"
        else:
            monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
            blocked = resource_full_auto.advance_run(
                execute_owner, "blocked", blocker_code="resource_planning_disabled",
            )
            assert blocked["state"] == "blocked"
        await asyncio.wait_for(delayed, timeout=2)
        await asyncio.sleep(0)
        assert not unexpected_start.is_set()
        assert run_id not in main._FULL_AUTO_RUN_TASKS

    asyncio.run(stop_before_due())


def test_deferred_runner_reentry_blocks_expired_authorization_without_dispatch(
    client, seeded, monkeypatch, make_runner,
):
    start = datetime.fromisoformat(db.now())
    clock = [start]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    monkeypatch.setattr(
        guided_sessions, "_FULL_AUTO_AUTHORIZATION_LIFETIME", timedelta(seconds=5),
    )
    session_id, run_id, _targets, execute_owner = _approved_execution(
        client, seeded, monkeypatch,
    )
    runner, fake = make_runner()
    monkeypatch.setattr(main, "runner", runner)
    monkeypatch.setattr(main, "_schedule_full_auto_run", _SCHEDULE_FULL_AUTO_RUN)
    monkeypatch.setattr(
        main, "_schedule_full_auto_run_after_delay", _SCHEDULE_FULL_AUTO_RUN_AFTER_DELAY,
    )
    resource_full_auto.advance_run(execute_owner, "waiting_runner", waiting_reason="runner_busy")

    async def expire_before_due():
        main._schedule_full_auto_run_after_delay(run_id, 0.01)
        delayed = main._FULL_AUTO_RUN_TASKS[run_id]
        clock[0] = start + timedelta(seconds=6)
        await asyncio.wait_for(delayed, timeout=3)
        await _eventually(lambda: resource_full_auto.get_run(run_id)["state"] == "blocked")
        await _eventually(lambda: run_id not in main._FULL_AUTO_RUN_TASKS)

    asyncio.run(expire_before_due())
    view = resource_full_auto.get_run(run_id)
    assert view["blocker_code"] == "authorization_expired"
    assert fake.attempts == 0
    assert db.q(
        "SELECT * FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    ) == []
    assert db.q("SELECT status FROM shot WHERE session_id = ?", session_id)[0]["status"] == "pending"
    assert fake.interrupted == 0


def test_runner_recovery_dependency_drift_blocks_without_crashing_coordinator(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _targets, execute_owner = _approved_execution(
        client, seeded, monkeypatch,
    )
    runner, fake = make_runner()
    monkeypatch.setattr(main, "runner", runner)
    resource_full_auto.admit_runner_start(session_id, execute_owner)
    resource_full_auto.advance_run(
        execute_owner, "waiting_runner", waiting_reason="runner_busy",
    )
    db.run(
        "UPDATE model SET lora_strength = 0.5 WHERE id = "
        "(SELECT model_id FROM session WHERE id = ?)",
        session_id,
    )

    asyncio.run(asyncio.wait_for(main._coordinate_full_auto_run(run_id), timeout=5))

    view = resource_full_auto.get_run(run_id)
    assert view["state"] == "blocked"
    assert view["blocker_code"] == "dependency_changed"
    status = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert status.status_code == 200, status.text
    assert "compare_scope" in status.json()["blocker"]["remedies"]
    comparison = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert comparison.status_code == 409
    assert comparison.json()["detail"]["code"] == "full_auto_continuity_requires_new_session"
    assert fake.attempts == 0
    assert db.q(
        "SELECT status FROM shot WHERE session_id = ? ORDER BY id", session_id,
    ) == [{"status": "pending"}] * len(_targets)
    assert db.q(
        "SELECT * FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    ) == []


def test_delayed_task_cleanup_does_not_erase_its_new_coordinator(
    client, seeded, monkeypatch,
):
    _session_id, run_id, _targets, _execute_owner = _approved_execution(
        client, seeded, monkeypatch,
    )
    monkeypatch.setattr(main, "_schedule_full_auto_run", _SCHEDULE_FULL_AUTO_RUN)
    monkeypatch.setattr(
        main, "_schedule_full_auto_run_after_delay", _SCHEDULE_FULL_AUTO_RUN_AFTER_DELAY,
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_coordinator(_run_id):
        entered.set()
        await release.wait()

    monkeypatch.setattr(main, "_coordinate_full_auto_run", hold_coordinator)

    async def verify_registry_identity():
        main._schedule_full_auto_run_after_delay(run_id, 0.01)
        delayed = main._FULL_AUTO_RUN_TASKS[run_id]
        await asyncio.wait_for(entered.wait(), timeout=2)
        coordinator = main._FULL_AUTO_RUN_TASKS[run_id]
        assert coordinator is not delayed
        await asyncio.sleep(0.02)
        assert delayed.done()
        assert main._FULL_AUTO_RUN_TASKS.get(run_id) is coordinator
        assert not coordinator.done()
        release.set()
        await asyncio.wait_for(coordinator, timeout=2)
        await asyncio.sleep(0)
        assert run_id not in main._FULL_AUTO_RUN_TASKS

    asyncio.run(verify_registry_identity())
