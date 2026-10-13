"""Independent acceptance for the shared manual and Full Auto runner claim."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading

import httpx
import pytest

import db
import main
import runner as runner_mod
from backend import resource_full_auto
from comfy import ComfyError
from conftest import GRAPH
import test_full_auto_run_independent as run_independent
import test_full_auto_submission_independent as submission
import session_plan


def _manual_session(client, tag: str, *, count: int = 1) -> int:
    workflow = client.post(
        "/api/workflows", json={"name": f"runner-{tag}", "graph": GRAPH},
    ).json()
    model = client.post("/api/models", json={
        "name": f"runner-{tag}",
        "lora_name": "characters/ada.safetensors",
        "trigger": "4da woman",
        "base_negative": "blurry",
        "workflow_id": workflow["id"],
        "settings": {"width": 832, "height": 1216, "steps": 8, "cfg": 1.0},
    }).json()
    session = client.post("/api/sessions", json={
        "model_id": model["id"],
        "name": f"runner-{tag}",
        "seed_mode": "fixed",
        "seed": 77,
        "shots": [{"label": "test", "prompt": "standing", "count": count}],
    }).json()
    return int(session["id"])


class _RemoteQueueGate:
    """Hold accepted fake remote work until the test releases it."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.active = 0
        self.maximum_active = 0
        self.calls = 0

    async def queue_prompt(self, _graph, _client_id):
        with self.lock:
            self.active += 1
            self.calls += 1
            self.maximum_active = max(self.maximum_active, self.active)
            prompt_id = f"controlled-{self.calls}"
            self.entered.set()
        return prompt_id

    async def history(self, _prompt_id):
        settled = await asyncio.to_thread(self.release.wait, 20)
        if not settled:
            raise TimeoutError("controlled fake queue was not released")
        with self.lock:
            self.active -= 1
        return {"status": {"completed": True}, "outputs": {}}


def _gate_claimed_execution(runner, admitted: threading.Event) -> None:
    """Keep the losing contender out of the shared DB until both starts resolve."""
    run_claimed_session = runner._run_claimed_session

    async def wait_until_admitted(session_id, runner_claim, full_auto_claim):
        if not await asyncio.to_thread(admitted.wait, 5):
            raise TimeoutError("competing runner starts did not both resolve")
        await run_claimed_session(session_id, runner_claim, full_auto_claim)

    runner._run_claimed_session = wait_until_admitted


def _approved_execute_owner(client, seeded, monkeypatch, *, photo_count: int = 1):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "runner-claim-test")
    monkeypatch.setitem(main.CONFIG, "llm_key", "runner-claim-test-key")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    session_id, run_id, _revision, targets, submit_owner = submission._approved_run(
        client, seeded, monkeypatch, photo_count=photo_count,
    )
    _view, execute_owner = main._run_full_auto_submission(submit_owner)
    return session_id, run_id, targets, execute_owner


def test_distinct_runner_instances_racing_manual_starts_dispatch_only_one(
    client, make_runner,
):
    session_a = _manual_session(client, "race-a")
    session_b = _manual_session(client, "race-b")
    runner_a, _fake_a = make_runner()
    runner_b, _fake_b = make_runner()
    remote = _RemoteQueueGate()
    runner_a.comfy.queue_prompt = remote.queue_prompt
    runner_a.comfy.history = remote.history
    runner_b.comfy.queue_prompt = remote.queue_prompt
    runner_b.comfy.history = remote.history
    initial_slot = db.one("SELECT * FROM global_runner_claim WHERE singleton = 1")
    assert initial_slot is not None and initial_slot["owner_kind"] is None

    start_barrier = threading.Barrier(3)
    results_lock = threading.Lock()
    results: list[tuple[bool, str | None, str | None]] = []
    results_ready = threading.Event()
    _gate_claimed_execution(runner_a, results_ready)
    _gate_claimed_execution(runner_b, results_ready)

    def launch(runner, session_id: int) -> None:
        async def start_and_wait():
            start_barrier.wait(timeout=5)
            try:
                started = runner.start(session_id)
                code = None
            except runner_mod.RunnerBusyError as exc:
                started = False
                code = exc.code
                error = None
            except Exception as exc:
                started = False
                code = None
                error = f"{type(exc).__name__}: {exc}"
            else:
                error = None
            with results_lock:
                results.append((started, code, error))
                if len(results) == 2:
                    results_ready.set()
            if started:
                await runner._task

        asyncio.run(start_and_wait())

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(launch, runner_a, session_a),
            pool.submit(launch, runner_b, session_b),
        ]
        try:
            start_barrier.wait(timeout=5)
            assert results_ready.wait(5)
            assert remote.entered.wait(5)
            with results_lock:
                assert sorted(results, key=lambda item: item[0]) == [
                    (False, "runner_busy", None), (True, None, None),
                ]
        finally:
            remote.release.set()
            for future in futures:
                future.result(timeout=10)

    assert remote.calls == 1
    assert remote.maximum_active == 1
    assert remote.active == 0
    assert {
        row["status"] for row in db.q(
            "SELECT status FROM shot WHERE session_id IN (?, ?)", session_a, session_b,
        )
    } == {"failed", "pending"}


def test_full_auto_wait_does_not_consume_runner_or_queue_manual_work(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, targets, execute_owner = _approved_execute_owner(
        client, seeded, monkeypatch, photo_count=4,
    )
    manual_session = _manual_session(client, "waiter-contender")
    manual_slot = resource_full_auto.acquire_runner_claim(manual_session)
    assert manual_slot is not None and manual_slot.owner_kind == "manual"

    full_auto_runner, full_auto_fake = make_runner()
    assert full_auto_runner.start(session_id, _full_auto_claim=execute_owner) is False
    waiting = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert waiting["state"] == "waiting_runner"
    assert waiting["phase"] == "execute"
    assert waiting["lease_expires_at"] is None
    assert waiting["runner_ready_at"] is not None
    assert db.q(
        "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == []
    assert full_auto_fake.attempts == 0
    assert full_auto_fake.interrupted == 0
    slot = db.one("SELECT * FROM global_runner_claim WHERE singleton = 1")
    assert (slot["owner_kind"], slot["owner_nonce"]) == (
        "manual", manual_slot.owner_nonce,
    )

    assert resource_full_auto.release_runner_claim(manual_slot) is True
    route_runner, route_fake = make_runner()
    monkeypatch.setattr(main, "runner", route_runner)
    monkeypatch.setattr(main, "output_dir_ok", lambda: True)
    response = client.post(f"/api/sessions/{manual_session}/run")
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "runner_busy"
    assert route_fake.attempts == 0
    assert db.one(
        "SELECT status FROM shot WHERE session_id = ?", manual_session,
    )["status"] == "pending"

    resumed_owner = resource_full_auto.claim_run(run_id)

    async def dispatch_waiter():
        assert full_auto_runner.start(session_id, _full_auto_claim=resumed_owner) is True
        await full_auto_runner._task

    asyncio.run(dispatch_waiter())
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    )["n"] == 1
    assert full_auto_fake.attempts == len(targets)
    assert {
        row["status"] for row in db.q(
            "SELECT status FROM shot WHERE session_id = ?", session_id,
        )
    } == {"done"}


def test_manual_start_cannot_win_race_against_eligible_full_auto_waiter(
    client, seeded, monkeypatch, make_runner, caplog,
):
    session_id, run_id, targets, execute_owner = _approved_execute_owner(
        client, seeded, monkeypatch, photo_count=4,
    )
    blocking_session = _manual_session(client, "priority-race-blocker")
    blocking_claim = resource_full_auto.acquire_runner_claim(blocking_session)
    assert blocking_claim is not None

    full_auto_runner, full_auto_fake = make_runner()
    assert full_auto_runner.start(session_id, _full_auto_claim=execute_owner) is False
    waiting = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert waiting["state"] == "waiting_runner"
    assert waiting["runner_ready_at"] is not None
    assert db.q("SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id) == []
    assert resource_full_auto.release_runner_claim(blocking_claim) is True
    waiting_owner = resource_full_auto.claim_run(run_id)

    remote = _RemoteQueueGate()
    full_auto_runner.comfy.queue_prompt = remote.queue_prompt
    full_auto_runner.comfy.history = remote.history

    owner_checks = []
    original_require_owner = runner_mod.Runner._require_runner_owner

    def record_owner_check(check_session_id, heartbeat):
        try:
            result = original_require_owner(check_session_id, heartbeat)
        except Exception as exc:
            owner_checks.append(f"{type(exc).__name__}:{getattr(exc, 'code', str(exc))}")
            raise
        owner_checks.append("ok")
        return result

    monkeypatch.setattr(
        runner_mod.Runner, "_require_runner_owner", staticmethod(record_owner_check),
    )
    shot_dispatches = []
    original_run_shot = full_auto_runner._run_shot

    async def record_run_shot(check_session_id, shot, heartbeat=None):
        shot_dispatches.append(int(shot["id"]))
        return await original_run_shot(check_session_id, shot, heartbeat)

    monkeypatch.setattr(full_auto_runner, "_run_shot", record_run_shot)
    manual_session = _manual_session(client, "priority-race-manual")
    manual_runner, manual_fake = make_runner()
    started_at = db.now()
    start_snapshot = {
        "run": db.one(
            "SELECT state, phase, lease_expires_at FROM full_auto_run WHERE run_id = ?",
            run_id,
        ),
        "owner": db.one(
            "SELECT owner_kind, session_id, run_id, owner_nonce, fencing_token, "
            "lease_expires_at FROM global_runner_claim WHERE singleton = 1",
        ),
    }

    start_barrier = threading.Barrier(3)
    results_lock = threading.Lock()
    results: dict[str, tuple[bool, str | None, str | None]] = {}
    task_outcomes: dict[str, str] = {}
    results_ready = threading.Event()
    _gate_claimed_execution(full_auto_runner, results_ready)
    _gate_claimed_execution(manual_runner, results_ready)

    def launch(kind: str, runner, session_to_start: int, run_claim=None) -> None:
        async def start_and_wait():
            start_barrier.wait(timeout=5)
            try:
                started = runner.start(session_to_start, _full_auto_claim=run_claim)
                code = None
                error = None
            except runner_mod.RunnerBusyError as exc:
                started = False
                code = exc.code
                error = None
            except Exception as exc:
                started = False
                code = None
                error = f"{type(exc).__name__}: {exc}"
            with results_lock:
                results[kind] = (started, code, error)
                if len(results) == 2:
                    results_ready.set()
            if started:
                try:
                    await runner._task
                except Exception as exc:
                    task_outcomes[kind] = f"{type(exc).__name__}: {exc}"
                    raise
                else:
                    task_outcomes[kind] = "completed"

        asyncio.run(start_and_wait())

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(launch, "full_auto", full_auto_runner, session_id, waiting_owner),
            pool.submit(launch, "manual", manual_runner, manual_session),
        ]
        try:
            start_barrier.wait(timeout=5)
            assert results_ready.wait(5)
            with results_lock:
                assert results == {
                    "full_auto": (True, None, None),
                    "manual": (False, "runner_busy", None),
                }
                if not remote.entered.wait(15):
                    end_snapshot = {
                        "run": db.one(
                            "SELECT state, phase, lease_expires_at FROM full_auto_run WHERE run_id = ?",
                            run_id,
                        ),
                        "owner": db.one(
                            "SELECT owner_kind, session_id, run_id, owner_nonce, fencing_token, "
                            "lease_expires_at FROM global_runner_claim WHERE singleton = 1",
                        ),
                        "admission": db.one(
                            "SELECT * FROM full_auto_runner_admission WHERE run_id = ?",
                            run_id,
                        ),
                        "shots": db.q(
                            "SELECT id, status, prompt_id, error FROM shot WHERE session_id = ?",
                            session_id,
                        ),
                        "session": db.one(
                            "SELECT status FROM session WHERE id = ?", session_id,
                        ),
                    }
                    pytest.fail(
                        "Full Auto won admission but did not reach the controlled queue; "
                        f"started_at={started_at!r}, ended_at={db.now()!r}, "
                        f"start={start_snapshot!r}, end={end_snapshot!r}, "
                        f"owner_checks={owner_checks!r}, shot_dispatches={shot_dispatches!r}, "
                        f"task_outcomes={task_outcomes!r}, "
                        f"task_done={full_auto_runner._task.done() if full_auto_runner._task else None}, "
                        f"captured_logs={caplog.text!r}"
                    )
        finally:
            remote.release.set()
            for future in futures:
                future.result(timeout=30)

    assert remote.calls == len(targets)
    assert remote.maximum_active == 1
    assert remote.active == 0
    assert full_auto_fake.attempts == 0
    assert manual_fake.attempts == 0
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    )["n"] == 1
    assert db.one(
        "SELECT status FROM shot WHERE session_id = ?", manual_session,
    )["status"] == "pending"


def test_blocked_cancelled_and_expired_waiters_do_not_keep_priority(client, monkeypatch):
    start = "2026-10-01T12:00:00+00:00"
    clock = [datetime.fromisoformat(start)]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    _model_id, manual_session = run_independent._seed_session("runner-invalid-waiters")

    def make_waiter(name: str, expires_at: str):
        run_id, session_id = run_independent._seed_run(
            name, authorization_expires_at=expires_at,
        )
        owner = resource_full_auto.claim_run(run_id, now_text=start)
        for phase in ("validate", "approve", "submit", "execute"):
            resource_full_auto.advance_run(owner, "active", phase=phase, now_text=start)
        resource_full_auto.advance_run(owner, "waiting_runner", now_text=start)
        return run_id, session_id

    blocked_id, _ = make_waiter("runner-blocked-waiter", "2026-10-08T12:00:00+00:00")
    blocked_owner = resource_full_auto.claim_run(blocked_id, now_text=start)
    resource_full_auto.advance_run(
        blocked_owner, "blocked", blocker_code="dependency_missing", now_text=start,
    )

    cancelled_id, _ = make_waiter("runner-cancelled-waiter", "2026-10-08T12:00:00+00:00")
    cancelled = db.one("SELECT revision, plan_revision FROM full_auto_run WHERE run_id = ?", cancelled_id)
    resource_full_auto.cancel_run(
        cancelled_id, "cancel-runner-waiter", cancelled["revision"],
        cancelled["plan_revision"], work_pending=False, now_text=start,
    )

    expired_id, _ = make_waiter("runner-expired-waiter", "2026-10-01T12:00:30+00:00")
    clock[0] += timedelta(seconds=31)
    before = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", expired_id)

    claim = resource_full_auto.acquire_runner_claim(manual_session)

    assert claim is not None and claim.owner_kind == "manual"
    assert resource_full_auto.release_runner_claim(claim) is True
    assert db.one("SELECT state FROM full_auto_run WHERE run_id = ?", blocked_id)["state"] == "blocked"
    assert db.one("SELECT state FROM full_auto_run WHERE run_id = ?", cancelled_id)["state"] == "cancelled"
    assert db.one("SELECT * FROM full_auto_run WHERE run_id = ?", expired_id) == before


def test_stale_release_cannot_clear_reused_numeric_session_identity(client):
    old_session = _manual_session(client, "reused-old")
    old_claim = resource_full_auto.acquire_runner_claim(old_session)
    assert old_claim is not None
    assert resource_full_auto.release_runner_claim(old_claim) is True

    deleted = client.delete(f"/api/sessions/{old_session}")
    assert deleted.status_code == 200, deleted.text
    new_session = _manual_session(client, "reused-new")
    assert new_session == old_session

    new_claim = resource_full_auto.acquire_runner_claim(new_session)
    assert new_claim is not None
    assert new_claim.fencing_token > old_claim.fencing_token
    with pytest.raises(resource_full_auto.FullAutoRunError) as stale_release:
        resource_full_auto.release_runner_claim(old_claim)
    assert stale_release.value.code == "runner_owner_stale"
    current = db.one("SELECT * FROM global_runner_claim WHERE singleton = 1")
    assert (current["owner_nonce"], current["fencing_token"], current["session_id"]) == (
        new_claim.owner_nonce, new_claim.fencing_token, new_session,
    )
    assert resource_full_auto.release_runner_claim(new_claim) is True


def test_replaced_manual_owner_cannot_write_or_dispatch_a_shot(client, make_runner):
    owner_session = _manual_session(client, "stale-write-owner")
    replacement_session = _manual_session(client, "replacement-write-owner")
    old_claim = resource_full_auto.acquire_runner_claim(owner_session)
    assert old_claim is not None
    assert resource_full_auto.release_runner_claim(old_claim) is True
    replacement_claim = resource_full_auto.acquire_runner_claim(replacement_session)
    assert replacement_claim is not None

    shot = db.one("SELECT * FROM shot WHERE session_id = ?", owner_session)
    runner, fake = make_runner()
    heartbeat = resource_full_auto.RunnerClaimHeartbeat(old_claim)

    with pytest.raises(resource_full_auto.FullAutoRunError) as stale_owner:
        asyncio.run(runner._run_shot(owner_session, shot, heartbeat))

    assert stale_owner.value.code == "runner_owner_stale"
    assert db.one("SELECT status FROM shot WHERE id = ?", shot["id"])["status"] == "pending"
    assert db.one(
        "SELECT owner_nonce, fencing_token, session_id FROM global_runner_claim WHERE singleton = 1",
    ) == {
        "owner_nonce": replacement_claim.owner_nonce,
        "fencing_token": replacement_claim.fencing_token,
        "session_id": replacement_session,
    }
    assert fake.attempts == 0
    assert resource_full_auto.release_runner_claim(replacement_claim) is True


def test_expired_runner_lease_is_not_renewed_after_database_lock_wait(
    client, monkeypatch,
):
    session_id = _manual_session(client, "lease-lock")
    clock = [datetime(2026, 10, 12, tzinfo=timezone.utc)]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    claim = resource_full_auto.acquire_runner_claim(session_id)
    assert claim is not None
    before = db.one("SELECT * FROM global_runner_claim WHERE singleton = 1")

    lock_entered = threading.Event()
    unlock = threading.Event()
    renewal_entered_transaction = threading.Event()
    original_transaction = db.transaction

    def witnessed_transaction():
        # The holder sets lock_entered after its BEGIN IMMEDIATE succeeded. Any
        # later transaction entry must wait on the same RLock until it releases.
        if lock_entered.is_set():
            renewal_entered_transaction.set()
        return original_transaction()

    monkeypatch.setattr(db, "transaction", witnessed_transaction)

    def hold_transaction():
        with db.transaction():
            lock_entered.set()
            if not unlock.wait(5):
                raise TimeoutError("database lock holder was not released")

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold_transaction)
        try:
            assert lock_entered.wait(5)
            renewal = pool.submit(resource_full_auto.renew_runner_claim, claim)
            assert renewal_entered_transaction.wait(5)
            assert not renewal.done()
            clock[0] += timedelta(seconds=61)
        finally:
            unlock.set()
        holder.result(timeout=5)
        with pytest.raises(resource_full_auto.FullAutoRunError) as expired:
            renewal.result(timeout=5)

    assert expired.value.code == "runner_lease_expired"
    assert db.one("SELECT * FROM global_runner_claim WHERE singleton = 1") == before
    with pytest.raises(resource_full_auto.FullAutoRunError) as stale_release:
        resource_full_auto.release_runner_claim(claim)
    assert stale_release.value.code == "runner_lease_expired"


def test_restart_preserves_owned_running_shot_as_expired_claim_fence(
    client, make_runner, monkeypatch,
):
    owner_session = _manual_session(client, "restart-unresolved-owner")
    contender_session = _manual_session(client, "restart-contender")
    clock = [datetime(2026, 10, 12, tzinfo=timezone.utc)]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    claim = resource_full_auto.acquire_runner_claim(owner_session)
    assert claim is not None
    shot = db.one("SELECT id FROM shot WHERE session_id = ?", owner_session)
    db.run(
        "UPDATE shot SET status='running', prompt_id=? WHERE id=?",
        "prior-remote-prompt", shot["id"],
    )
    assert db.one(
        "SELECT owner_kind, session_id FROM global_runner_claim WHERE singleton=1",
    ) == {"owner_kind": "manual", "session_id": owner_session}
    clock[0] += timedelta(seconds=61)

    async def simulate_application_restart():
        async with main.lifespan(main.app):
            return db.one("SELECT status FROM shot WHERE id = ?", shot["id"])

    restarted_shot = asyncio.run(simulate_application_restart())
    assert restarted_shot["status"] == "running"

    runner, fake = make_runner()
    with pytest.raises(runner_mod.RunnerBusyError) as busy:
        runner.start(contender_session)
    assert busy.value.code == "runner_busy"
    assert fake.attempts == 0
    assert db.one("SELECT status FROM shot WHERE id = ?", shot["id"])["status"] == "running"
    assert db.one(
        "SELECT owner_nonce, fencing_token, session_id FROM global_runner_claim WHERE singleton=1",
    ) == {
        "owner_nonce": claim.owner_nonce,
        "fencing_token": claim.fencing_token,
        "session_id": owner_session,
    }


def test_full_auto_runner_claim_rechecks_owner_after_slow_admission_validation(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _targets, execute_owner = _approved_execute_owner(
        client, seeded, monkeypatch, photo_count=4,
    )
    clock = [datetime.fromisoformat(execute_owner.lease_started_at)]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    original_review = session_plan._full_auto_duplicate_review_evidence

    def slow_review(*args, **kwargs):
        evidence = original_review(*args, **kwargs)
        clock[0] += timedelta(seconds=61)
        return evidence

    monkeypatch.setattr(session_plan, "_full_auto_duplicate_review_evidence", slow_review)
    runner, fake = make_runner()

    async def start():
        runner.start(session_id, _full_auto_claim=execute_owner)
        if runner._task is not None:
            await runner._task

    with pytest.raises(resource_full_auto.FullAutoRunError) as expired:
        asyncio.run(start())

    assert expired.value.code == "run_lease_expired"
    assert runner._task is None
    assert fake.attempts == 0
    assert db.q("SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id) == []
    assert db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"] != "running"
    assert db.one(
        "SELECT owner_kind FROM global_runner_claim WHERE singleton = 1",
    )["owner_kind"] is None


def test_definitive_comfy_validation_refusal_releases_shared_claim(
    client, make_runner,
):
    session_id = _manual_session(client, "known-refusal")
    runner, fake = make_runner()

    async def rejected_prompt(_graph, _client_id):
        raise ComfyError("prompt validation failed")

    runner.comfy.queue_prompt = rejected_prompt

    async def execute():
        assert runner.start(session_id) is True
        await runner._task

    asyncio.run(execute())

    assert db.one(
        "SELECT status FROM shot WHERE session_id = ?", session_id,
    )["status"] == "failed"
    assert db.one(
        "SELECT owner_kind FROM global_runner_claim WHERE singleton = 1",
    )["owner_kind"] is None
    assert fake.interrupted == 0


def test_lost_queue_ack_keeps_running_marker_and_shared_claim(
    client, make_runner,
):
    session_id = _manual_session(client, "lost-ack")
    other_session = _manual_session(client, "lost-ack-contender")
    runner, fake = make_runner()
    attempted = []

    async def lost_ack(_graph, _client_id):
        fake.attempts += 1
        attempted.append("accepted remotely; response lost")
        raise httpx.ReadError("connection closed after request body was sent")

    runner.comfy.queue_prompt = lost_ack

    async def execute():
        assert runner.start(session_id) is True
        await runner._task

    asyncio.run(execute())

    shot = db.one("SELECT * FROM shot WHERE session_id = ?", session_id)
    assert attempted == ["accepted remotely; response lost"]
    assert shot["status"] == "running"
    assert db.one("SELECT status FROM session WHERE id = ?", session_id)["status"] == "running"
    owner = db.one("SELECT owner_kind, session_id FROM global_runner_claim WHERE singleton = 1")
    assert owner == {"owner_kind": "manual", "session_id": session_id}
    assert fake.interrupted == 0

    contender, contender_fake = make_runner()
    with pytest.raises(runner_mod.RunnerBusyError) as busy:
        contender.start(other_session)
    assert busy.value.code == "runner_busy"
    assert contender_fake.attempts == 0


def test_full_auto_lost_queue_ack_keeps_claim_and_stops_later_dispatch(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, targets, execute_owner = _approved_execute_owner(
        client, seeded, monkeypatch, photo_count=4,
    )
    other_session = _manual_session(client, "full-auto-lost-ack-contender")
    runner, fake = make_runner()
    attempted = []

    async def lost_ack(_graph, _client_id):
        fake.attempts += 1
        attempted.append(fake.attempts)
        raise httpx.ReadError("connection closed after request body was sent")

    runner.comfy.queue_prompt = lost_ack

    async def execute():
        assert runner.start(session_id, _full_auto_claim=execute_owner) is True
        await runner._task

    asyncio.run(execute())

    first = db.one(
        "SELECT status FROM shot WHERE session_id = ? ORDER BY id LIMIT 1", session_id,
    )
    assert attempted == [1]
    assert len(targets) == 4
    assert first["status"] == "running"
    assert db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"] == "running"
    assert db.one(
        "SELECT run_id FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) is not None
    owner = db.one(
        "SELECT owner_kind, session_id, run_id FROM global_runner_claim WHERE singleton = 1",
    )
    assert owner == {
        "owner_kind": "full_auto", "session_id": session_id, "run_id": run_id,
    }
    assert fake.interrupted == 0

    contender, contender_fake = make_runner()
    with pytest.raises(runner_mod.RunnerBusyError) as busy:
        contender.start(other_session)
    assert busy.value.code == "runner_busy"
    assert contender_fake.attempts == 0
