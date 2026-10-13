"""Full Auto provider recovery boundaries for OpenSpec Task 4.9."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db
import main
from backend import resource_full_auto
import test_full_auto_assistant_independent as assistant
import test_full_auto_preparation_coordinator as preparation
import test_full_auto_submission_independent as submission


def _configure_assistant(monkeypatch) -> None:
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "fa49-recovery-model")
    monkeypatch.setitem(main.CONFIG, "llm_key", "fa49-recovery-key")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)


def _reopen_database() -> None:
    path = Path(db.conn().execute("PRAGMA database_list").fetchone()["file"])
    db.conn().commit()
    db.conn().close()
    db.connect(path)


def _approved_execute_owner(client, seeded, monkeypatch):
    session_id, run_id, revision, targets, submit_owner = submission._approved_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    _view, execute_owner = main._run_full_auto_submission(submit_owner)
    return session_id, run_id, revision, targets, execute_owner


def test_comfy_probe_deadlines_survive_database_reopen_and_exhaust_at_three(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, _revision, _targets, owner = _approved_execute_owner(
        client, seeded, monkeypatch,
    )
    now = datetime.fromisoformat(db.now().replace("Z", "+00:00"))
    scope = "dispatch:1"

    state = resource_full_auto.schedule_comfy_probe(
        owner, scope, now_text=now.isoformat(timespec="seconds"),
    )
    assert state == {
        "scope": scope, "count": 0,
        "due_at": (now + timedelta(seconds=5)).isoformat(timespec="seconds"),
        "pending": True,
    }
    _reopen_database()
    assert resource_full_auto.comfy_probe_state(
        owner, scope, now_text=now.isoformat(timespec="seconds"),
    ) == state

    state = resource_full_auto.reserve_comfy_probe(
        owner, scope,
        now_text=(now + timedelta(seconds=5)).isoformat(timespec="seconds"),
    )
    assert state["count"] == 1
    assert state["due_at"] == (now + timedelta(seconds=20)).isoformat(timespec="seconds")
    _reopen_database()
    assert resource_full_auto.comfy_probe_state(
        owner, scope, now_text=(now + timedelta(seconds=5)).isoformat(timespec="seconds"),
    ) == state
    state = resource_full_auto.reserve_comfy_probe(
        owner, scope,
        now_text=(now + timedelta(seconds=20)).isoformat(timespec="seconds"),
    )
    assert state["count"] == 2
    assert state["due_at"] == (now + timedelta(seconds=50)).isoformat(timespec="seconds")
    state = resource_full_auto.reserve_comfy_probe(
        owner, scope,
        now_text=(now + timedelta(seconds=50)).isoformat(timespec="seconds"),
    )
    assert state == {"scope": scope, "count": 3, "due_at": None, "pending": True}
    assert db.one(
        "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
        "FROM full_auto_run WHERE run_id = ?", run_id,
    ) == {"comfy_probe_scope": scope, "comfy_probe_count": 3, "comfy_probe_due_at": None}
    assert db.one("SELECT state FROM full_auto_run WHERE run_id = ?", run_id)["state"] == "active"
    assert session_id > 0


@pytest.mark.parametrize("operation", ["schedule", "clear"])
def test_probe_cursor_mutations_recheck_live_lease_before_write(
    client, seeded, monkeypatch, operation,
):
    _configure_assistant(monkeypatch)
    _session_id, run_id, _revision, _targets, owner = _approved_execute_owner(
        client, seeded, monkeypatch,
    )
    start = datetime.fromisoformat(db.now().replace("Z", "+00:00"))
    clock = [start]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    scope = "dispatch:1"
    if operation == "clear":
        scheduled = resource_full_auto.schedule_comfy_probe(
            owner, scope, now_text=clock[0].isoformat(timespec="seconds"),
        )
        original_cursor = {
            "comfy_probe_scope": scope,
            "comfy_probe_count": 0,
            "comfy_probe_due_at": scheduled["due_at"],
        }
    else:
        original_cursor = {
            "comfy_probe_scope": None,
            "comfy_probe_count": 0,
            "comfy_probe_due_at": None,
        }

    expected_expiry = datetime.fromisoformat(owner.lease_expires_at)
    original_check = resource_full_auto._current_authority_issue
    validated_at = []

    def expire_after_authority_check(row, timestamp):
        issue = original_check(row, timestamp)
        validated_at.append(timestamp)
        if len(validated_at) == 1:
            clock[0] = expected_expiry + timedelta(seconds=1)
        return issue

    monkeypatch.setattr(
        resource_full_auto, "_current_authority_issue", expire_after_authority_check,
    )
    mutation = (
        resource_full_auto.schedule_comfy_probe
        if operation == "schedule" else resource_full_auto.clear_comfy_probe_state
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        mutation(owner, scope)

    assert error.value.code == "run_lease_expired"
    assert len(validated_at) == 1
    assert db.one(
        "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
        "FROM full_auto_run WHERE run_id = ?", run_id,
    ) == original_cursor


def test_rate_limit_status_has_durable_safe_resume_deadline(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    clock = [now]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    session_id, run_id, _revision, _targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return assistant._valid_writer_response(request)
        import httpx

        return httpx.Response(429, headers={"Retry-After": "60"}, request=request)

    assistant._install_async_responder(monkeypatch, respond)
    status = asyncio.run(main._run_full_auto_preparation(
        resource_full_auto.claim_run(run_id),
    ))
    deadline = (now + timedelta(seconds=60)).isoformat(timespec="seconds")
    assert (status["state"], status["blocker_code"]) == (
        "blocked", "assistant_rate_limited",
    )
    assert status["can_resume"] is False
    assert deadline in status["blocker"]["message"]
    assert "assistant.invalid" not in status["blocker"]["message"]
    assert "recovery-key" not in status["blocker"]["message"]
    assert db.one(
        "SELECT assistant_retry_after_at FROM full_auto_run WHERE run_id = ?", run_id,
    )["assistant_retry_after_at"] == deadline
    clock[0] = now + timedelta(seconds=59)
    assert resource_full_auto.public_run_status(session_id, run_id)["can_resume"] is False
    clock[0] = now + timedelta(seconds=60)
    assert resource_full_auto.public_run_status(session_id, run_id)["can_resume"] is True
    assert len(requests) == 2


def test_owned_dispatch_recovers_with_read_only_stats_before_queueing(
    client, seeded, monkeypatch, make_runner,
):
    _configure_assistant(monkeypatch)
    session_id, _run_id, _revision, targets, execute_owner = _approved_execute_owner(
        client, seeded, monkeypatch,
    )
    monkeypatch.setattr(resource_full_auto, "COMFY_PROBE_DELAYS", (0, 0, 0))
    runner, fake = make_runner()
    calls = []

    async def recover_stats():
        calls.append("GET /system_stats")
        if len(calls) == 1:
            raise OSError("synthetic unavailable")
        return {}

    fake.stats = recover_stats

    async def run_owned():
        runner.start(session_id, _full_auto_claim=execute_owner)
        await runner._task

    asyncio.run(run_owned())
    assert calls == ["GET /system_stats", "GET /system_stats"] + [
        "GET /system_stats",
    ] * (len(targets) - 1)
    assert fake.attempts == len(targets)
    assert {row["status"] for row in db.q(
        "SELECT status FROM shot WHERE session_id = ?", session_id,
    )} == {"done"}


def test_owned_history_recovery_retries_only_the_read_without_prompt_resend(
    client, seeded, monkeypatch, make_runner,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, _revision, targets, execute_owner = _approved_execute_owner(
        client, seeded, monkeypatch,
    )
    monkeypatch.setattr(resource_full_auto, "COMFY_PROBE_DELAYS", (0, 0, 0))
    runner, fake = make_runner()
    original_history = fake.history
    calls = []

    async def fail_three_reads(prompt_id):
        calls.append(prompt_id)
        if len(calls) <= 3:
            raise OSError("synthetic history outage")
        return await original_history(prompt_id)

    fake.history = fail_three_reads

    async def run_owned():
        runner.start(session_id, _full_auto_claim=execute_owner)
        await runner._task

    asyncio.run(run_owned())
    # One initial history read plus three delayed read-only retries, then one
    # history read for each remaining shot. Prompt dispatches are never repeated.
    assert len(calls) == 4 + len(targets) - 1
    assert calls[0] == calls[1] == calls[2] == calls[3]
    assert fake.attempts == len(targets)
    assert {row["status"] for row in db.q(
        "SELECT status FROM shot WHERE session_id = ?", session_id,
    )} == {"done"}
    assert db.one("SELECT state FROM full_auto_run WHERE run_id = ?", run_id)["state"] == "succeeded"


def test_owned_dispatch_exhaustion_blocks_without_queue_submission(
    client, seeded, monkeypatch, make_runner,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, _revision, _targets, execute_owner = _approved_execute_owner(
        client, seeded, monkeypatch,
    )
    monkeypatch.setattr(resource_full_auto, "COMFY_PROBE_DELAYS", (0, 0, 0))
    runner, fake = make_runner()
    calls = []

    async def unavailable_stats():
        calls.append("GET /system_stats")
        raise OSError("synthetic unavailable")

    fake.stats = unavailable_stats

    async def run_owned():
        runner.start(session_id, _full_auto_claim=execute_owner)
        await runner._task

    asyncio.run(run_owned())
    assert calls == ["GET /system_stats"] * 4
    assert fake.attempts == 0
    assert db.one("SELECT state, blocker_code FROM full_auto_run WHERE run_id = ?", run_id) == {
        "state": "blocked", "blocker_code": "comfy_unavailable",
    }
    public = resource_full_auto.public_run_status(session_id, run_id)
    assert public["can_resume"] is False
    assert public["blocker"]["remedies"] == ["review_status", "cancel"]
    assert db.one("SELECT status FROM session WHERE id = ?", session_id)["status"] == "running"
    assert db.one(
        "SELECT status FROM shot WHERE session_id = ? ORDER BY id LIMIT 1", session_id,
    )["status"] == "pending"
    assert db.one(
        "SELECT comfy_probe_count, comfy_probe_due_at FROM full_auto_run WHERE run_id = ?",
        run_id,
    ) == {"comfy_probe_count": 3, "comfy_probe_due_at": None}
