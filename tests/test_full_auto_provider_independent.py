"""Independent provider-failure and recovery checks for Full Auto Task 4.9."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import uuid

import httpx
import pytest
from fastapi import HTTPException

import db
import main
from backend import resource_full_auto
import runner as runner_module
import test_full_auto_preparation_coordinator as preparation
import test_full_auto_submission_independent as submission
from test_full_auto_assistant_independent import (
    _install_async_responder,
    _start_owned_child,
)
from test_full_auto_control_api import _control_body


def _configure_assistant(monkeypatch) -> None:
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "fa49-independent-model")
    monkeypatch.setitem(main.CONFIG, "llm_key", "fa49-independent-key")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)


def _valid_writer_response(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    user = next(message["content"] for message in body["messages"] if message["role"] == "user")
    prefix, separator, serialized = user.partition("Context: ")
    assert prefix and separator
    context = json.loads(serialized)
    fields = context["preparation"]["requested_fields"]
    content = json.dumps({field: f"Independent value for {field}" for field in fields})
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=request,
    )


def _run_status(run_id: str) -> dict:
    return dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id))


def _reopen_database() -> None:
    path = Path(db.conn().execute("PRAGMA database_list").fetchone()["file"])
    db.conn().commit()
    db.conn().close()
    db.connect(path)


def _resume_eligibility(run_id: str, session_id: int) -> resource_full_auto.ResumeEligibility:
    run = db.one("SELECT plan_revision FROM full_auto_run WHERE run_id = ?", run_id)
    return resource_full_auto.ResumeEligibility(
        current_plan_revision=int(run["plan_revision"]),
        unresolved_work=resource_full_auto.unresolved_work_count(session_id, run_id),
        authorization_valid=True,
        policy_supported=True,
        budget_available=True,
        session_exclusive=True,
        scope_matches=True,
        planning_enabled=True,
        next_state="waiting_authoring",
    )


def _approved_execute_owner(client, seeded, monkeypatch):
    session_id, run_id, revision, targets, submit_owner = submission._approved_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    _view, execute_owner = main._run_full_auto_submission(submit_owner)
    return session_id, run_id, revision, targets, execute_owner


def _install_virtual_probe_clock(monkeypatch, start: datetime) -> list[datetime]:
    """Advance only runner sleeps; leave the owned heartbeat suspended."""
    clock = [start]
    real_sleep = asyncio.sleep
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    monkeypatch.setattr(
        resource_full_auto, "RUN_HEARTBEAT_INTERVAL", timedelta(seconds=1000),
    )

    async def sleep(seconds=0, result=None):
        seconds = float(seconds)
        if seconds >= 1000:
            await real_sleep(3600)
            return result
        clock[0] += timedelta(seconds=seconds)
        await real_sleep(0)
        return result

    monkeypatch.setattr(runner_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(runner_module, "POLL_SECONDS", 0)
    return clock


@pytest.mark.parametrize("retry_after_kind", ["delta", "http_date"])
@pytest.mark.parametrize("resume_path", ["http", "domain"])
def test_rate_limit_retry_after_survives_restart_and_blocks_early_resume(
    client, seeded, monkeypatch, retry_after_kind, resume_path,
):
    _configure_assistant(monkeypatch)
    start = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
    clock = [start]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    session_id, run_id, revision, targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    owner, child = _start_owned_child(session_id, run_id, targets)
    retry_time = start + timedelta(seconds=30)
    retry_header = (
        "30" if retry_after_kind == "delta"
        else format_datetime(retry_time, usegmt=True)
    )
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return _valid_writer_response(request)
        return httpx.Response(
            429, headers={"Retry-After": retry_header},
            json={"error": {"message": "slow down"}}, request=request,
        )

    _install_async_responder(monkeypatch, respond)
    asyncio.run(main._run_prepare_takes_operation(
        session_id, child.operation_id, _full_auto_claim=owner,
    ))

    run = _run_status(run_id)
    assert (run["state"], run["blocker_code"]) == ("blocked", "assistant_rate_limited")
    public = resource_full_auto.public_run_status(session_id, run_id)
    assert public["blocker"]["code"] == "assistant_rate_limited"
    assert "wait" in public["blocker"]["remedies"]
    assert public["can_resume"] is False
    assert len(requests) == 2
    reservations = db.q(
        "SELECT take_id, request_number, settled_at, response_metadata_json "
        "FROM full_auto_assistant_reservation WHERE run_id = ? ORDER BY reservation_id",
        run_id,
    )
    assert len(reservations) == 2
    assert [(row["take_id"], row["request_number"]) for row in reservations] == [
        (targets[0], 1), (targets[1], 1),
    ]
    metadata = json.loads(reservations[-1]["response_metadata_json"])
    assert metadata["status"] == 429
    assert metadata["retry_after"] == retry_header
    assert all(row["settled_at"] is not None for row in reservations)
    prepared_prefix = db.q(
        "SELECT take_id, status FROM prepared_take WHERE session_id = ? "
        "AND plan_revision = ? ORDER BY rowid",
        session_id, revision,
    )
    assert [row["take_id"] for row in prepared_prefix] == [targets[0]]

    _reopen_database()
    assert db.q(
        "SELECT take_id, status FROM prepared_take WHERE session_id = ? "
        "AND plan_revision = ? ORDER BY rowid",
        session_id, revision,
    ) == prepared_prefix
    before = _run_status(run_id)

    clock[0] = retry_time - timedelta(seconds=1)
    if resume_path == "http":
        early = client.post(
            f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
            json=_control_body(run_id, revision),
        )
        assert not early.is_success, early.text
        assert _run_status(run_id) == before
    else:
        with pytest.raises(resource_full_auto.FullAutoRunError):
            resource_full_auto.resume_run(
                run_id, f"early-{uuid.uuid4()}", int(before["revision"]), revision,
                eligibility=_resume_eligibility(run_id, session_id),
                now_text=clock[0].isoformat(timespec="seconds"),
            )
        assert _run_status(run_id) == before

    clock[0] = retry_time
    assert resource_full_auto.public_run_status(session_id, run_id)["can_resume"] is True
    if resume_path == "http":
        resumed = client.post(
            f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
            json=_control_body(run_id, revision),
        )
        assert resumed.status_code == 202, resumed.text
        assert resumed.json()["state"] in {"active", "waiting_authoring"}
    else:
        resumed = resource_full_auto.resume_run(
            run_id, f"due-{uuid.uuid4()}", int(before["revision"]), revision,
            eligibility=_resume_eligibility(run_id, session_id),
            now_text=clock[0].isoformat(timespec="seconds"),
        )
        assert resumed.view["state"] in {"active", "waiting_authoring"}
    assert len(requests) == 2, "Resume must never hide another provider POST."
    assert db.q(
        "SELECT take_id, status FROM prepared_take WHERE session_id = ? "
        "AND plan_revision = ? ORDER BY rowid",
        session_id, revision,
    ) == prepared_prefix


@pytest.mark.parametrize(
    ("response_json", "expected_blocker"),
    [
        pytest.param(
            {"choices": [{"message": {"refusal": "I cannot help with this request."}}]},
            "assistant_refused", id="structured-refusal",
        ),
        pytest.param(
            {"choices": [{"finish_reason": "content_filter", "message": {"content": ""}}]},
            "assistant_refused", id="content-filter",
        ),
        pytest.param(
            {"choices": [{"message": {"content": "This is ordinary malformed prose."}}]},
            "writer_output_invalid", id="unclassified-prose",
        ),
    ],
)
def test_structured_refusal_is_distinct_from_malformed_prose_and_never_retries(
    client, seeded, monkeypatch, response_json, expected_blocker,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, _revision, targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    owner, child = _start_owned_child(session_id, run_id, targets)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=response_json, request=request)

    _install_async_responder(monkeypatch, respond)
    asyncio.run(main._run_prepare_takes_operation(
        session_id, child.operation_id, _full_auto_claim=owner,
    ))

    run = _run_status(run_id)
    assert (run["state"], run["blocker_code"]) == ("blocked", expected_blocker)
    public = resource_full_auto.public_run_status(session_id, run_id)
    assert public["blocker"]["code"] == expected_blocker
    assert expected_blocker in {"assistant_refused", "writer_output_invalid"}
    assert not any(
        secret in json.dumps(public)
        for secret in ("I cannot help with this request.", "This is ordinary malformed prose.")
    )
    if expected_blocker == "assistant_refused":
        assert "revise_brief" in public["blocker"]["remedies"]
        assert "manual_authoring" in public["blocker"]["remedies"]
    assert len(requests) == 1
    reservations = db.q(
        "SELECT settled_at, response_metadata_json FROM full_auto_assistant_reservation "
        "WHERE run_id = ? ORDER BY reservation_id",
        run_id,
    )
    assert len(reservations) == 1
    assert reservations[0]["settled_at"] is not None
    assert db.one(
        "SELECT 1 FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, targets[0],
    ) is None
    metadata = json.loads(reservations[0]["response_metadata_json"])
    if expected_blocker == "assistant_refused":
        assert metadata.get("structured_refusal") is True or metadata.get("content_filter") is True
    else:
        assert metadata.get("structured_refusal") is not True
        assert metadata.get("content_filter") is not True


def test_missing_provider_configuration_blocks_before_any_post(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, _revision, targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    owner, child = _start_owned_child(session_id, run_id, targets)
    monkeypatch.setitem(main.CONFIG, "llm_url", "")
    requests = []
    _install_async_responder(monkeypatch, lambda request: requests.append(request))

    asyncio.run(main._run_prepare_takes_operation(
        session_id, child.operation_id, _full_auto_claim=owner,
    ))

    run = _run_status(run_id)
    assert (run["state"], run["blocker_code"]) == ("blocked", "assistant_unavailable")
    assert requests == []
    assert db.q(
        "SELECT * FROM full_auto_assistant_reservation WHERE run_id = ?", run_id,
    ) == []


def test_transport_error_consumes_one_dispatch_without_hidden_retry(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, _revision, targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    owner, child = _start_owned_child(session_id, run_id, targets)
    requests = []

    def fail_transport(request):
        requests.append(request)
        raise httpx.ConnectError("synthetic transport failure", request=request)

    _install_async_responder(monkeypatch, fail_transport)
    asyncio.run(main._run_prepare_takes_operation(
        session_id, child.operation_id, _full_auto_claim=owner,
    ))

    run = _run_status(run_id)
    assert (run["state"], run["blocker_code"]) == ("blocked", "assistant_transport_error")
    assert len(requests) == 1
    reservations = db.q(
        "SELECT settled_at, response_metadata_json FROM full_auto_assistant_reservation "
        "WHERE run_id = ? ORDER BY reservation_id",
        run_id,
    )
    assert len(reservations) == 1
    assert reservations[0]["settled_at"] is not None
    assert json.loads(reservations[0]["response_metadata_json"])["status"] is None


@pytest.mark.parametrize("drift_phase", ["before_dispatch", "response_in_flight"])
def test_provider_drift_fences_dispatch_or_discards_late_response(
    client, seeded, monkeypatch, drift_phase,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, revision, targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    owner, child = _start_owned_child(session_id, run_id, targets)
    requests = []
    response_entered = asyncio.Event()
    release_response = asyncio.Event()

    async def respond(request):
        requests.append(request)
        if drift_phase == "response_in_flight":
            response_entered.set()
            await release_response.wait()
        return _valid_writer_response(request)

    _install_async_responder(monkeypatch, respond)
    if drift_phase == "before_dispatch":
        monkeypatch.setitem(main.CONFIG, "llm_model", "fa49-changed-model")
        asyncio.run(main._run_prepare_takes_operation(
            session_id, child.operation_id, _full_auto_claim=owner,
        ))
    else:
        async def run_with_drift():
            task = asyncio.create_task(main._run_prepare_takes_operation(
                session_id, child.operation_id, _full_auto_claim=owner,
            ))
            await asyncio.wait_for(response_entered.wait(), timeout=3)
            monkeypatch.setitem(main.CONFIG, "llm_model", "fa49-changed-model")
            release_response.set()
            await task

        asyncio.run(run_with_drift())

    run = _run_status(run_id)
    assert (run["state"], run["blocker_code"]) == ("blocked", "provider_changed")
    assert len(requests) == (0 if drift_phase == "before_dispatch" else 1)
    reservations = db.q(
        "SELECT request_number, settled_at, response_metadata_json "
        "FROM full_auto_assistant_reservation WHERE run_id = ? ORDER BY reservation_id",
        run_id,
    )
    assert len(reservations) == len(requests)
    if drift_phase == "before_dispatch":
        assert reservations == []
    else:
        assert reservations[0]["request_number"] == 1
        assert reservations[0]["settled_at"] is not None
        assert json.loads(reservations[0]["response_metadata_json"])["status"] == 200
    assert db.one(
        "SELECT 1 FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, targets[0],
    ) is None


def test_owned_runner_reserves_each_read_probe_before_get_and_survives_reopen(
    client, seeded, monkeypatch, make_runner,
):
    _configure_assistant(monkeypatch)
    session_id, _run_id, _revision, targets, owner = _approved_execute_owner(
        client, seeded, monkeypatch,
    )
    start = datetime.fromisoformat(db.now().replace("Z", "+00:00"))
    clock = _install_virtual_probe_clock(monkeypatch, start)
    runner, fake = make_runner()
    first_shot = db.one(
        "SELECT id FROM shot WHERE session_id = ? ORDER BY id LIMIT 1", session_id,
    )
    scope = f"dispatch:{first_shot['id']}"
    observations = []

    async def stats():
        call_number = len(observations) + 1
        row = db.one(
            "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
            "FROM full_auto_run WHERE session_id = ?", session_id,
        )
        observations.append((clock[0], dict(row)))
        if call_number == 1:
            assert row["comfy_probe_scope"] != scope
            raise OSError("initial availability read failed")
        if call_number > 4:
            return {}
        assert row["comfy_probe_scope"] == scope
        assert row["comfy_probe_count"] == call_number - 1, (
            "the next GET must be durably reserved before its transport starts"
        )
        if call_number == 2:
            _reopen_database()
            assert db.one(
                "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
                "FROM full_auto_run WHERE session_id = ?", session_id,
            ) == row
            raise OSError("probe response was lost after reservation and database reopen")
        if call_number == 3:
            raise OSError("second bounded probe failed")
        if call_number == 4:
            assert row["comfy_probe_due_at"] is None
            return {}
        return {}

    fake.stats = stats

    async def run_owned():
        runner.start(session_id, _full_auto_claim=owner)
        await runner._task

    asyncio.run(run_owned())

    assert [item[1]["comfy_probe_count"] for item in observations[:4]] == [
        0, 1, 2, 3,
    ]
    assert observations[1][0] - start == timedelta(seconds=5)
    assert observations[2][0] - start == timedelta(seconds=20)
    assert observations[3][0] - start == timedelta(seconds=50)
    assert fake.attempts == len(targets)
    assert {row["status"] for row in db.q(
        "SELECT status FROM shot WHERE session_id = ?", session_id,
    )} == {"done"}
    assert db.one(
        "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
        "FROM full_auto_run WHERE session_id = ?", session_id,
    ) == {
        "comfy_probe_scope": None,
        "comfy_probe_count": 0,
        "comfy_probe_due_at": None,
    }


def test_owned_history_probe_reservation_survives_lost_response_and_database_reopen(
    client, seeded, monkeypatch, make_runner,
):
    _configure_assistant(monkeypatch)
    session_id, _run_id, _revision, targets, owner = _approved_execute_owner(
        client, seeded, monkeypatch,
    )
    start = datetime.fromisoformat(db.now().replace("Z", "+00:00"))
    clock = _install_virtual_probe_clock(monkeypatch, start)
    runner, fake = make_runner()
    original_history = fake.history
    history_calls = []

    async def history(prompt_id):
        history_calls.append((prompt_id, clock[0]))
        scope = "history:" + hashlib.sha256(prompt_id.encode("utf-8")).hexdigest()
        row = db.one(
            "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
            "FROM full_auto_run WHERE session_id = ?", session_id,
        )
        if len(history_calls) == 1:
            raise OSError("initial history read failed")
        if len(history_calls) == 2:
            assert row["comfy_probe_scope"] == scope
            assert row["comfy_probe_count"] == 1
            assert row["comfy_probe_due_at"] == (
                start + timedelta(seconds=20)
            ).isoformat(timespec="seconds")
            _reopen_database()
            assert db.one(
                "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
                "FROM full_auto_run WHERE session_id = ?", session_id,
            ) == row
            raise OSError("history response was lost after durable reservation")
        if len(history_calls) == 3:
            assert row["comfy_probe_scope"] == scope
            assert row["comfy_probe_count"] == 2
            assert row["comfy_probe_due_at"] == (
                start + timedelta(seconds=50)
            ).isoformat(timespec="seconds")
        return await original_history(prompt_id)

    fake.history = history

    async def run_owned():
        runner.start(session_id, _full_auto_claim=owner)
        await runner._task

    asyncio.run(run_owned())

    first_prompt_id = history_calls[0][0]
    assert [item[0] for item in history_calls[:3]] == [first_prompt_id] * 3
    assert history_calls[1][1] - start == timedelta(seconds=5)
    assert history_calls[2][1] - start == timedelta(seconds=20)
    assert fake.attempts == len(targets), "history recovery must never resend /prompt"
    assert {row["status"] for row in db.q(
        "SELECT status FROM shot WHERE session_id = ?", session_id,
    )} == {"done"}
    assert db.one(
        "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
        "FROM full_auto_run WHERE session_id = ?", session_id,
    ) == {
        "comfy_probe_scope": None,
        "comfy_probe_count": 0,
        "comfy_probe_due_at": None,
    }


def test_owned_runner_reentry_uses_persisted_due_before_any_new_initial_read(
    client, seeded, monkeypatch, make_runner,
):
    _configure_assistant(monkeypatch)
    session_id, _run_id, _revision, _targets, owner = _approved_execute_owner(
        client, seeded, monkeypatch,
    )
    start = datetime.fromisoformat(db.now().replace("Z", "+00:00"))
    clock = [start]
    real_sleep = asyncio.sleep
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    monkeypatch.setattr(
        resource_full_auto, "RUN_HEARTBEAT_INTERVAL", timedelta(seconds=1000),
    )
    runner, fake = make_runner()
    shot = db.one(
        "SELECT * FROM shot WHERE session_id = ? ORDER BY id LIMIT 1", session_id,
    )
    scope = f"dispatch:{shot['id']}"
    resource_full_auto.admit_runner_start(session_id, owner)
    stats_calls = []
    restarted = [False]

    async def stats():
        stats_calls.append(clock[0])
        if len(stats_calls) == 1:
            raise OSError("initial availability read failed before process restart")
        state = db.one(
            "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
            "FROM full_auto_run WHERE session_id = ?", session_id,
        )
        assert (state["comfy_probe_scope"], state["comfy_probe_count"]) == (scope, 1)
        assert clock[0] == start + timedelta(seconds=5)
        assert state["comfy_probe_due_at"] == (
            start + timedelta(seconds=20)
        ).isoformat(timespec="seconds")
        return {}

    fake.stats = stats

    class SimulatedProcessRestart(Exception):
        pass

    async def stop_before_due(seconds=0, result=None):
        seconds = float(seconds)
        if seconds >= 1000:
            await real_sleep(3600)
            return result
        if seconds == 5 and not restarted[0]:
            raise SimulatedProcessRestart("stop after schedule commit, before first probe")
        clock[0] += timedelta(seconds=seconds)
        await real_sleep(0)
        return result

    monkeypatch.setattr(runner_module.asyncio, "sleep", stop_before_due)

    async def first_process():
        async with resource_full_auto.RunClaimHeartbeat(owner) as heartbeat:
            await runner._run_shot(session_id, shot, heartbeat)

    with pytest.raises(SimulatedProcessRestart):
        asyncio.run(first_process())

    persisted = db.one(
        "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
        "FROM full_auto_run WHERE session_id = ?", session_id,
    )
    assert persisted == {
        "comfy_probe_scope": scope,
        "comfy_probe_count": 0,
        "comfy_probe_due_at": (start + timedelta(seconds=5)).isoformat(timespec="seconds"),
    }
    assert stats_calls == [start]
    assert db.one("SELECT status FROM shot WHERE id = ?", shot["id"])["status"] == "pending"
    _reopen_database()
    restarted[0] = True

    async def resume_after_reopen():
        async with resource_full_auto.RunClaimHeartbeat(owner) as heartbeat:
            await runner._run_shot(session_id, shot, heartbeat)

    asyncio.run(resume_after_reopen())
    assert len(stats_calls) == 2
    assert stats_calls[1] == start + timedelta(seconds=5)
    assert fake.attempts == 1
    assert db.one("SELECT status FROM shot WHERE id = ?", shot["id"])["status"] == "done"
    assert db.one(
        "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
        "FROM full_auto_run WHERE session_id = ?", session_id,
    ) == {
        "comfy_probe_scope": None,
        "comfy_probe_count": 0,
        "comfy_probe_due_at": None,
    }


def test_exhausted_owned_dispatch_stays_bounded_on_same_scope_reentry(
    client, seeded, monkeypatch, make_runner,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, _revision, _targets, owner = _approved_execute_owner(
        client, seeded, monkeypatch,
    )
    start = datetime.fromisoformat(db.now().replace("Z", "+00:00"))
    _install_virtual_probe_clock(monkeypatch, start)
    runner, fake = make_runner()
    stats_calls = []
    history_calls = []

    async def unavailable_stats():
        stats_calls.append("GET /system_stats")
        raise OSError("synthetic ComfyUI outage")

    async def unexpected_history(prompt_id):
        history_calls.append(prompt_id)
        raise AssertionError("history cannot be read before a prompt dispatch")

    fake.stats = unavailable_stats
    fake.history = unexpected_history
    original_ensure = runner._ensure_comfy_available
    repeated = []

    async def reenter_after_exhaustion(session, heartbeat, scope):
        try:
            await original_ensure(session, heartbeat, scope)
        except resource_full_auto.FullAutoRunError as error:
            if error.code == "comfy_unavailable" and not repeated:
                repeated.append(scope)
                with pytest.raises(resource_full_auto.FullAutoRunError) as reentry:
                    await original_ensure(session, heartbeat, scope)
                assert reentry.value.code == "comfy_unavailable"
                assert len(stats_calls) == 4, "an exhausted cursor must not trigger a fresh GET"
            raise

    monkeypatch.setattr(runner, "_ensure_comfy_available", reenter_after_exhaustion)

    async def run_owned():
        runner.start(session_id, _full_auto_claim=owner)
        await runner._task

    asyncio.run(run_owned())

    run = _run_status(run_id)
    assert (run["state"], run["blocker_code"]) == ("blocked", "comfy_unavailable")
    first_shot_id = db.one(
        "SELECT id FROM shot WHERE session_id = ? ORDER BY id LIMIT 1", session_id,
    )["id"]
    public = resource_full_auto.public_run_status(session_id, run_id)
    assert public["blocker"]["code"] == "comfy_unavailable"
    assert "review_status" in public["blocker"]["remedies"]
    assert public["can_resume"] is False
    assert "resume" not in public["blocker"]["remedies"]
    assert stats_calls == ["GET /system_stats"] * 4
    assert repeated == [f"dispatch:{first_shot_id}"]
    assert fake.attempts == 0
    assert history_calls == []
    assert fake.interrupted == 0
    assert db.q(
        "SELECT status, prompt_id FROM shot WHERE session_id = ? ORDER BY id", session_id,
    )[0] == {"status": "pending", "prompt_id": ""}
    assert db.one(
        "SELECT comfy_probe_count, comfy_probe_due_at FROM full_auto_run WHERE run_id = ?",
        run_id,
    ) == {"comfy_probe_count": 3, "comfy_probe_due_at": None}


@pytest.mark.parametrize("interleaving", ["cancel", "disable", "expire", "fence"])
def test_owned_runner_rechecks_authority_before_next_probe_get(
    client, seeded, monkeypatch, make_runner, interleaving,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, revision, _targets, owner = _approved_execute_owner(
        client, seeded, monkeypatch,
    )
    start = datetime.fromisoformat(db.now().replace("Z", "+00:00"))
    clock = [start]
    real_sleep = asyncio.sleep
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    monkeypatch.setattr(
        resource_full_auto, "RUN_HEARTBEAT_INTERVAL", timedelta(seconds=1000),
    )
    runner, fake = make_runner()
    stats_calls = []

    async def stats():
        stats_calls.append("GET /system_stats")
        raise OSError("initial ComfyUI read failed")

    fake.stats = stats

    async def invalidate_during_wait(seconds=0, result=None):
        seconds = float(seconds)
        if seconds >= 1000:
            await real_sleep(3600)
            return result
        clock[0] += timedelta(seconds=seconds)
        if seconds == 5:
            if interleaving == "cancel":
                row = _run_status(run_id)
                resource_full_auto.cancel_run(
                    run_id, f"cancel-{uuid.uuid4()}", int(row["revision"]), revision,
                    work_pending=True, now_text=clock[0].isoformat(timespec="seconds"),
                )
            elif interleaving == "disable":
                monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
            elif interleaving == "expire":
                clock[0] = datetime.fromisoformat(owner.lease_expires_at) + timedelta(seconds=1)
            else:
                db.run(
                    "UPDATE full_auto_run SET fencing_token = fencing_token + 1 WHERE run_id = ?",
                    run_id,
                )
        await real_sleep(0)
        return result

    monkeypatch.setattr(runner_module.asyncio, "sleep", invalidate_during_wait)

    async def run_owned():
        runner.start(session_id, _full_auto_claim=owner)
        await runner._task

    asyncio.run(run_owned())

    assert stats_calls == ["GET /system_stats"]
    assert fake.attempts == 0
    first_shot_id = db.one(
        "SELECT id FROM shot WHERE session_id = ? ORDER BY id LIMIT 1", session_id,
    )["id"]
    assert db.one(
        "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
        "FROM full_auto_run WHERE run_id = ?", run_id,
    ) == {
        "comfy_probe_scope": f"dispatch:{first_shot_id}",
        "comfy_probe_count": 0,
        "comfy_probe_due_at": (start + timedelta(seconds=5)).isoformat(timespec="seconds"),
    }
    if interleaving == "cancel":
        assert _run_status(run_id)["state"] == "cancel_requested"


def test_successful_connectivity_does_not_settle_an_unknown_owned_dispatch(
    client, seeded, monkeypatch, make_runner,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, _revision, _targets, owner = _approved_execute_owner(
        client, seeded, monkeypatch,
    )
    start = datetime.fromisoformat(db.now().replace("Z", "+00:00"))
    _install_virtual_probe_clock(monkeypatch, start)
    monkeypatch.setattr(runner_module, "POLL_SECONDS", 0.1)
    monkeypatch.setattr(runner_module, "SHOT_TIMEOUT", 0.1)
    runner, fake = make_runner()
    stats_calls = []
    history_calls = []

    async def healthy_stats():
        stats_calls.append("GET /system_stats")
        return {}

    async def incomplete_history(prompt_id):
        history_calls.append(prompt_id)
        if len(history_calls) == 1:
            raise OSError("history transport failed")
        return {"status": {}, "outputs": {}}

    fake.stats = healthy_stats
    fake.history = incomplete_history

    async def run_owned():
        runner.start(session_id, _full_auto_claim=owner)
        await runner._task

    asyncio.run(run_owned())

    shot = db.one(
        "SELECT status, prompt_id, filename FROM shot WHERE session_id = ? ORDER BY id LIMIT 1",
        session_id,
    )
    assert stats_calls == ["GET /system_stats"]
    assert len(history_calls) == 2
    assert history_calls[0] == history_calls[1]
    assert fake.attempts == 1
    assert fake.interrupted == 0
    assert shot["status"] == "running"
    assert shot["prompt_id"]
    assert not shot["filename"]
    assert db.one("SELECT status FROM session WHERE id = ?", session_id)["status"] == "running"
    assert db.one("SELECT state FROM full_auto_run WHERE run_id = ?", run_id)["state"] == "active"


def test_probe_reservation_checks_authority_time_after_transaction_lock(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _session_id, run_id, _revision, _targets, owner = _approved_execute_owner(
        client, seeded, monkeypatch,
    )
    start = datetime.fromisoformat(db.now().replace("Z", "+00:00"))
    clock = [start]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    scope = "dispatch:1"
    scheduled = resource_full_auto.schedule_comfy_probe(
        owner, scope, now_text=clock[0].isoformat(timespec="seconds"),
    )
    expected_expiry = datetime.fromisoformat(owner.lease_expires_at)
    transaction = db.transaction

    @contextmanager
    def advance_clock_after_lock():
        with transaction():
            clock[0] = expected_expiry + timedelta(seconds=1)
            yield

    monkeypatch.setattr(db, "transaction", advance_clock_after_lock)
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.reserve_comfy_probe(owner, scope)
    assert error.value.code == "run_lease_expired"
    monkeypatch.setattr(db, "transaction", transaction)
    assert db.one(
        "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
        "FROM full_auto_run WHERE run_id = ?", run_id,
    ) == {
        "comfy_probe_scope": scope,
        "comfy_probe_count": 0,
        "comfy_probe_due_at": scheduled["due_at"],
    }


def test_probe_reservation_rechecks_lease_after_authority_validation(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _session_id, run_id, _revision, _targets, owner = _approved_execute_owner(
        client, seeded, monkeypatch,
    )
    start = datetime.fromisoformat(db.now().replace("Z", "+00:00"))
    clock = [start]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    scope = "dispatch:1"
    scheduled = resource_full_auto.schedule_comfy_probe(
        owner, scope, now_text=clock[0].isoformat(timespec="seconds"),
    )
    clock[0] = datetime.fromisoformat(scheduled["due_at"])
    original_check = resource_full_auto._current_authority_issue
    expected_expiry = datetime.fromisoformat(owner.lease_expires_at)
    validated_at = []

    def delayed_authority_check(row, timestamp):
        issue = original_check(row, timestamp)
        validated_at.append(timestamp)
        if len(validated_at) == 1:
            clock[0] = expected_expiry + timedelta(seconds=1)
        return issue

    monkeypatch.setattr(
        resource_full_auto, "_current_authority_issue", delayed_authority_check,
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.reserve_comfy_probe(owner, scope)

    assert error.value.code == "run_lease_expired"
    assert len(validated_at) == 1
    assert db.one(
        "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
        "FROM full_auto_run WHERE run_id = ?", run_id,
    ) == {
        "comfy_probe_scope": scope,
        "comfy_probe_count": 0,
        "comfy_probe_due_at": scheduled["due_at"],
    }


def test_probe_reservation_rechecks_authorization_after_authority_validation(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _session_id, run_id, _revision, _targets, owner = _approved_execute_owner(
        client, seeded, monkeypatch,
    )
    start = datetime.fromisoformat(db.now().replace("Z", "+00:00"))
    expiry = (start + timedelta(seconds=30)).isoformat(timespec="seconds")
    trigger = db.one(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
        "AND name = 'full_auto_run_receipt_immutable'",
    )
    receipt = db.one(
        "SELECT authorization_json FROM full_auto_run_receipt WHERE run_id = ?",
        run_id,
    )
    authorization = json.loads(receipt["authorization_json"])
    authorization["authorization_expires_at"] = expiry
    authorization["summary"]["authorization_expires_at"] = expiry
    db.run("DROP TRIGGER full_auto_run_receipt_immutable")
    try:
        db.run(
            "UPDATE full_auto_run_receipt "
            "SET authorization_json = ?, authorization_expires_at = ? WHERE run_id = ?",
            json.dumps(authorization, ensure_ascii=False, sort_keys=True), expiry, run_id,
        )
    finally:
        db.run(trigger["sql"])

    clock = [start]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    scope = "dispatch:1"
    scheduled = resource_full_auto.schedule_comfy_probe(
        owner, scope, now_text=clock[0].isoformat(timespec="seconds"),
    )
    clock[0] = datetime.fromisoformat(scheduled["due_at"])
    original_check = resource_full_auto._current_authority_issue
    validated_at = []

    def expire_authority_after_prewrite_validation(row, timestamp):
        issue = original_check(row, timestamp)
        validated_at.append(timestamp)
        if len(validated_at) == 2:
            clock[0] = datetime.fromisoformat(expiry) + timedelta(seconds=1)
        return issue

    monkeypatch.setattr(
        resource_full_auto, "_current_authority_issue",
        expire_authority_after_prewrite_validation,
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.reserve_comfy_probe(owner, scope)

    assert error.value.code == "authorization_expired"
    assert datetime.fromisoformat(owner.lease_expires_at) > clock[0]
    assert len(validated_at) == 2
    assert db.one(
        "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
        "FROM full_auto_run WHERE run_id = ?", run_id,
    ) == {
        "comfy_probe_scope": scope,
        "comfy_probe_count": 0,
        "comfy_probe_due_at": scheduled["due_at"],
    }


def test_manual_transport_keeps_existing_single_attempt_failure_behavior(monkeypatch):
    _configure_assistant(monkeypatch)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            429, headers={"Retry-After": "60"},
            json={"error": {"message": "rate limited"}}, request=request,
        )

    _install_async_responder(monkeypatch, respond)
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.enhance.run_structured(
            main.CONFIG, main.enhance.EnhanceIn(instruction="manual compatibility"),
        ))
    assert error.value.status_code == 502
    assert len(requests) == 1
