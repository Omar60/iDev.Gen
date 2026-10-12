"""Task 4.8 integration checks for shared Full Auto assistant dispatches."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import uuid

import httpx
import pytest

import db
import enhance
import main
from backend import authoring_operations, resource_full_auto, resource_preparation
from test_full_auto_preparation_coordinator import _create_run, _install_provider


_ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _assistant_config(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "task48-test-provider")
    monkeypatch.setitem(main.CONFIG, "llm_key", "task48-test-secret")


def _start_child(session_id, run_id, revision, targets):
    owner = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(owner)
    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": "prepare_takes",
        "take_ids": targets,
    })
    child, created = authoring_operations.start_operation(
        session_id, request, planning_enabled=True, assistant_available=True,
        _full_auto_claim=owner,
    )
    assert created is True
    return owner, child["operation_id"]


def _fields_by_take(session_id, revision, targets):
    fields = {}
    for start in range(0, len(targets), 20):
        batch = targets[start:start + 20]
        prepared = resource_preparation.prepare_take_inputs_batch(session_id, revision, batch)
        fields.update({
            take_id: resource_preparation.compute_unlocked_fields(prepared[index])
            for index, take_id in enumerate(batch)
        })
    return fields


def _install_mock_transport(monkeypatch, responder):
    transport = httpx.MockTransport(responder)

    class MockAsyncClient(_ORIGINAL_ASYNC_CLIENT):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(main.enhance.httpx, "AsyncClient", MockAsyncClient)
    return transport


def _assistant_response(fields):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps({
                field: f"test value for {field}" for field in fields
            })}}],
            "usage": {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20},
        },
    )


def _insert_settled_reservations(run_id, session_id, take_counts, *, sequence):
    stamp = db.now()
    for take_id, count in take_counts.items():
        for request_number in range(1, count + 1):
            db.run(
                """INSERT INTO full_auto_assistant_reservation
                   (run_id, authorization_sequence, session_id, take_id, operation_id,
                    slot_generation, request_number, dispatched_at, settled_at,
                    response_metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                run_id, sequence, session_id, take_id,
                f"task48-reservation-{sequence}-{take_id}", 1, request_number,
                stamp, stamp, json.dumps({"status": 200}, separators=(",", ":")),
            )


def test_reasoning_400_fallback_reserves_both_real_http_posts(client, seeded, monkeypatch):
    session_id, run_id, revision, targets = _create_run(client, seeded, photo_count=4)
    owner, operation_id = _start_child(session_id, run_id, revision, targets)
    field_map = _fields_by_take(session_id, revision, targets)
    bodies = []
    successful_takes = [0]

    def respond(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(400, text="Unrecognized reasoning_effort parameter")
        take_id = targets[successful_takes[0]]
        successful_takes[0] += 1
        return _assistant_response(field_map[take_id])

    _install_mock_transport(monkeypatch, respond)
    asyncio.run(main._run_prepare_takes_operation(
        session_id, operation_id, _full_auto_claim=owner,
    ))

    rows = db.q(
        "SELECT authorization_sequence, request_number, settled_at, response_metadata_json "
        "FROM full_auto_assistant_reservation WHERE run_id = ? ORDER BY reservation_id",
        run_id,
    )
    assert len(bodies) == len(targets) + 1
    assert "reasoning_effort" in bodies[0]
    assert "reasoning_effort" not in bodies[1]
    assert [(row["authorization_sequence"], row["request_number"]) for row in rows] == [
        (0, 1), (0, 2), (0, 1), (0, 1), (0, 1),
    ]
    assert all(row["settled_at"] is not None for row in rows)
    assert json.loads(rows[0]["response_metadata_json"]) == {"status": 400}
    assert json.loads(rows[1]["response_metadata_json"]) == {
        "status": 200,
        "structured_refusal": False,
        "content_filter": False,
        "usage": {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20},
    }
    assert resource_full_auto.assistant_call_accounting(run_id) == {
        "authorized": 8, "reserved": 5, "remaining": 3,
    }


def test_full_auto_total_timeout_cancels_fallback_before_next_post(
    client, seeded, monkeypatch,
):
    first_session, first_run, first_revision, first_targets = _create_run(
        client, seeded, photo_count=4,
    )
    second_session, second_run, second_revision, second_targets = _create_run(
        client, seeded, photo_count=4,
    )
    first_owner, first_operation = _start_child(
        first_session, first_run, first_revision, first_targets,
    )
    second_owner, second_operation = _start_child(
        second_session, second_run, second_revision, second_targets,
    )
    second_fields = _fields_by_take(second_session, second_revision, second_targets)

    assert enhance.TIMEOUT == resource_full_auto.ASSISTANT_DISPATCH_TIMEOUT.total_seconds()
    timeout_seconds = 0.08
    monkeypatch.setattr(enhance, "TIMEOUT", timeout_seconds)
    monkeypatch.setattr(
        resource_full_auto, "ASSISTANT_DISPATCH_TIMEOUT",
        timedelta(seconds=timeout_seconds),
    )

    requests = []
    active = [0]
    maximum_active = [0]
    fallback_entered = asyncio.Event()
    fallback_finished = asyncio.Event()
    second_started_after_fallback = []
    second_request_number = [0]

    async def respond(request):
        active[0] += 1
        maximum_active[0] = max(maximum_active[0], active[0])
        requests.append(request)
        try:
            if len(requests) == 1:
                return httpx.Response(400, text="Unsupported reasoning_effort")
            if len(requests) == 2:
                fallback_entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    fallback_finished.set()
            second_started_after_fallback.append(fallback_finished.is_set())
            take_id = second_targets[second_request_number[0]]
            second_request_number[0] += 1
            return _assistant_response(second_fields[take_id])
        finally:
            active[0] -= 1

    _install_mock_transport(monkeypatch, respond)

    async def exercise():
        first_task = asyncio.create_task(main._run_prepare_takes_operation(
            first_session, first_operation, _full_auto_claim=first_owner,
        ))
        await asyncio.wait_for(fallback_entered.wait(), timeout=3)
        first_reservations = db.q(
            "SELECT * FROM full_auto_assistant_reservation WHERE run_id = ? "
            "ORDER BY reservation_id", first_run,
        )
        assert len(first_reservations) == 2
        assert json.loads(first_reservations[0]["response_metadata_json"]) == {
            "status": 400,
        }
        assert first_reservations[1]["settled_at"] is None

        stale = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(
            timespec="seconds",
        )
        db.run(
            "UPDATE full_auto_run SET lease_expires_at = ? WHERE run_id = ?",
            stale, first_run,
        )
        db.run(
            "UPDATE full_auto_assistant_slot SET lease_expires_at = ? "
            "WHERE run_id = ? AND take_id = ?",
            stale, first_run, first_targets[0],
        )
        replacement_owner = resource_full_auto.claim_run(first_run)
        assert replacement_owner.requires_reconciliation is True
        slot = db.one(
            "SELECT state, inflight_reservation_id, lease_expires_at "
            "FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
            first_run, first_targets[0],
        )
        assert slot["state"] == "quarantined"
        assert slot["inflight_reservation_id"] == first_reservations[1]["reservation_id"]

        second_task = asyncio.create_task(main._run_prepare_takes_operation(
            second_session, second_operation, _full_auto_claim=second_owner,
        ))
        try:
            for _ in range(200):
                waiting = db.one(
                    "SELECT state FROM full_auto_assistant_slot "
                    "WHERE run_id = ? AND take_id = ?",
                    second_run, second_targets[0],
                )
                if waiting is not None and waiting["state"] == "waiting":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("the second run did not wait behind the live fallback POST")

            await asyncio.wait_for(
                second_task, timeout=5,
            )
            await asyncio.wait_for(first_task, timeout=5)
        finally:
            for task in (first_task, second_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(first_task, second_task, return_exceptions=True)

    asyncio.run(exercise())

    assert fallback_finished.is_set()
    assert second_started_after_fallback == [True] * len(second_targets)
    assert maximum_active == [1]
    assert len(requests) == 2 + len(second_targets)
    settled = db.q(
        "SELECT settled_at, response_metadata_json FROM full_auto_assistant_reservation "
        "WHERE run_id = ? ORDER BY reservation_id", first_run,
    )
    assert len(settled) == 2
    assert settled[1]["settled_at"] is not None
    assert json.loads(settled[1]["response_metadata_json"]) == {"status": None}


def test_manual_structured_transport_has_no_full_auto_total_deadline(monkeypatch):
    monkeypatch.setattr(enhance, "TIMEOUT", 0.01)
    calls = []

    async def respond(request):
        calls.append(request)
        await asyncio.sleep(0.04)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}}]},
        )

    _install_mock_transport(monkeypatch, respond)
    result = asyncio.run(enhance.run_structured(
        main.CONFIG, enhance.EnhanceIn(instruction="manual structured request"),
    ))
    assert result == {}
    assert len(calls) == 1


def test_fifo_yields_after_each_take_and_serializes_across_runs(client, seeded, monkeypatch):
    first_session, first_run, first_revision, first_targets = _create_run(
        client, seeded, photo_count=4,
    )
    second_session, second_run, second_revision, second_targets = _create_run(
        client, seeded, photo_count=4,
    )
    first_owner, first_operation = _start_child(
        first_session, first_run, first_revision, first_targets,
    )
    second_owner, second_operation = _start_child(
        second_session, second_run, second_revision, second_targets,
    )
    field_map = {
        **_fields_by_take(first_session, first_revision, first_targets),
        **_fields_by_take(second_session, second_revision, second_targets),
    }
    expected = [
        take_id
        for index in range(max(len(first_targets), len(second_targets)))
        for take_id in (
            ([first_targets[index]] if index < len(first_targets) else [])
            + ([second_targets[index]] if index < len(second_targets) else [])
        )
    ]
    calls = []
    active = [0]
    max_active = [0]
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def respond(request):
        index = len(calls)
        take_id = expected[index]
        calls.append(take_id)
        active[0] += 1
        max_active[0] = max(max_active[0], active[0])
        try:
            if index == 0:
                first_entered.set()
                await release_first.wait()
            return _assistant_response(field_map[take_id])
        finally:
            active[0] -= 1

    _install_mock_transport(monkeypatch, respond)

    async def exercise():
        first_task = asyncio.create_task(main._run_prepare_takes_operation(
            first_session, first_operation, _full_auto_claim=first_owner,
        ))
        await asyncio.wait_for(first_entered.wait(), timeout=3)
        second_task = asyncio.create_task(main._run_prepare_takes_operation(
            second_session, second_operation, _full_auto_claim=second_owner,
        ))
        for _ in range(200):
            waiting = db.one(
                "SELECT state, ready_at FROM full_auto_assistant_slot "
                "WHERE run_id = ? AND take_id = ?",
                second_run, second_targets[0],
            )
            if waiting is not None and waiting["state"] == "waiting":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("the second run did not enter the persisted FIFO")
        assert calls == [first_targets[0]]
        # Ensure the later take has a strictly newer persisted readiness time.
        await asyncio.sleep(1.05)
        release_first.set()
        await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=10)

    asyncio.run(exercise())
    assert calls == expected
    assert max_active == [1]
    assert db.one(
        "SELECT state FROM authoring_operation WHERE operation_id = ?", first_operation,
    )["state"] == "succeeded"
    assert db.one(
        "SELECT state FROM authoring_operation WHERE operation_id = ?", second_operation,
    )["state"] == "succeeded"


def test_lost_response_consumes_budget_and_quarantines_until_timeout(client, seeded, monkeypatch):
    first_session, first_run, first_revision, first_targets = _create_run(
        client, seeded, photo_count=4,
    )
    second_session, second_run, second_revision, second_targets = _create_run(
        client, seeded, photo_count=4,
    )
    first_owner, first_operation = _start_child(
        first_session, first_run, first_revision, first_targets,
    )
    second_owner, second_operation = _start_child(
        second_session, second_run, second_revision, second_targets,
    )
    fields = _fields_by_take(second_session, second_revision, second_targets)
    entered = asyncio.Event()
    calls = []
    completed_requests = [0]

    async def respond(request):
        calls.append(request)
        if len(calls) == 1:
            entered.set()
            await asyncio.Event().wait()
        take_id = second_targets[completed_requests[0]]
        completed_requests[0] += 1
        return _assistant_response(fields[take_id])

    _install_mock_transport(monkeypatch, respond)

    async def exercise():
        first_task = asyncio.create_task(main._run_prepare_takes_operation(
            first_session, first_operation, _full_auto_claim=first_owner,
        ))
        await asyncio.wait_for(entered.wait(), timeout=3)
        first_reservation = db.one(
            "SELECT * FROM full_auto_assistant_reservation WHERE run_id = ?", first_run,
        )
        assert first_reservation is not None and first_reservation["settled_at"] is None
        expired_run_lease = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).isoformat(timespec="seconds")
        db.run(
            "UPDATE full_auto_run SET lease_expires_at = ? WHERE run_id = ?",
            expired_run_lease, first_run,
        )
        replacement_owner = resource_full_auto.claim_run(first_run)
        assert replacement_owner.requires_reconciliation is True
        first_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_task
        slot = db.one(
            "SELECT state, lease_expires_at, inflight_reservation_id "
            "FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
            first_run, first_targets[0],
        )
        assert slot["state"] == "quarantined"
        assert slot["inflight_reservation_id"] == first_reservation["reservation_id"]

        second_task = asyncio.create_task(main._run_prepare_takes_operation(
            second_session, second_operation, _full_auto_claim=second_owner,
        ))
        for _ in range(200):
            waiter = db.one(
                "SELECT state FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
                second_run, second_targets[0],
            )
            if waiter is not None and waiter["state"] == "waiting":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("the second run did not wait behind the quarantine")
        assert len(calls) == 1
        assert resource_full_auto.assistant_call_accounting(first_run)["remaining"] == 7

        old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(timespec="seconds")
        db.run(
            "UPDATE full_auto_assistant_reservation SET dispatched_at = ? WHERE reservation_id = ?",
            old, first_reservation["reservation_id"],
        )
        db.run(
            "UPDATE full_auto_assistant_slot SET lease_expires_at = ? WHERE run_id = ? AND take_id = ?",
            old, first_run, first_targets[0],
        )
        await asyncio.wait_for(second_task, timeout=5)

    asyncio.run(exercise())
    assert len(calls) == 1 + len(second_targets)
    lost = db.one(
        "SELECT settled_at FROM full_auto_assistant_reservation WHERE run_id = ?", first_run,
    )
    assert lost["settled_at"] is None
    assert resource_full_auto.assistant_call_accounting(first_run) == {
        "authorized": 8, "reserved": 1, "remaining": 7,
    }
    assert resource_full_auto.assistant_call_accounting(second_run)["reserved"] == 4


def test_assistant_slot_heartbeat_renews_its_ten_minute_lease(client, seeded, monkeypatch):
    session_id, run_id, revision, targets = _create_run(client, seeded, photo_count=4)
    owner, operation_id = _start_child(session_id, run_id, revision, targets)
    fields = _fields_by_take(session_id, revision, targets)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def respond(request):
        index = len(calls)
        calls.append(request)
        if index == 0:
            entered.set()
            await release.wait()
        return _assistant_response(fields[targets[index]])

    _install_mock_transport(monkeypatch, respond)
    assert resource_full_auto.ASSISTANT_SLOT_LEASE == timedelta(minutes=10)
    assert resource_full_auto.ASSISTANT_SLOT_HEARTBEAT_INTERVAL == timedelta(seconds=15)
    monkeypatch.setattr(
        resource_full_auto, "ASSISTANT_SLOT_LEASE", timedelta(seconds=30),
    )
    monkeypatch.setattr(
        resource_full_auto, "ASSISTANT_SLOT_HEARTBEAT_INTERVAL", timedelta(milliseconds=2),
    )
    monkeypatch.setattr(
        resource_full_auto, "RUN_HEARTBEAT_INTERVAL", timedelta(milliseconds=2),
    )

    async def exercise():
        task = asyncio.create_task(main._run_prepare_takes_operation(
            session_id, operation_id, _full_auto_claim=owner,
        ))
        await asyncio.wait_for(entered.wait(), timeout=3)
        before = db.one(
            "SELECT lease_expires_at, updated_at FROM full_auto_assistant_slot WHERE run_id = ?",
            run_id,
        )
        clock = [datetime.now(timezone.utc)]

        def fake_now():
            value = clock[0]
            clock[0] += timedelta(seconds=1)
            return value.isoformat(timespec="seconds")

        monkeypatch.setattr(db, "now", fake_now)
        await asyncio.sleep(0.05)
        after = db.one(
            "SELECT lease_expires_at, updated_at FROM full_auto_assistant_slot WHERE run_id = ?",
            run_id,
        )
        assert after["updated_at"] != before["updated_at"]
        assert datetime.fromisoformat(after["lease_expires_at"]) > datetime.fromisoformat(
            before["lease_expires_at"],
        )
        release.set()
        await asyncio.wait_for(task, timeout=8)

    asyncio.run(exercise())
    assert len(calls) == len(targets)
    assert db.one(
        "SELECT state FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
        run_id, targets[-1],
    )["state"] == "completed"


def test_cancel_after_first_response_blocks_reasoning_fallback_post(client, seeded, monkeypatch):
    session_id, run_id, revision, targets = _create_run(client, seeded, photo_count=4)
    owner, operation_id = _start_child(session_id, run_id, revision, targets)
    fields = _fields_by_take(session_id, revision, targets)[targets[0]]
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(400, text="Unsupported reasoning argument")

    _install_mock_transport(monkeypatch, respond)
    original_settle = resource_full_auto.settle_assistant_dispatch
    cancelled = []

    def cancel_after_settlement(slot_claim, reservation_id, metadata, **kwargs):
        original_settle(slot_claim, reservation_id, metadata, **kwargs)
        if not cancelled:
            cancelled.append(resource_full_auto.cancel_run(
                run_id, str(uuid.uuid4()),
                int(db.one("SELECT revision FROM full_auto_run WHERE run_id = ?", run_id)["revision"]),
                revision, work_pending=None, derive_work_pending=True,
            ))

    monkeypatch.setattr(resource_full_auto, "settle_assistant_dispatch", cancel_after_settlement)
    asyncio.run(main._run_prepare_takes_operation(
        session_id, operation_id, _full_auto_claim=owner,
    ))
    assert len(calls) == 1, (
        db.one("SELECT state, blocker_code FROM full_auto_run WHERE run_id = ?", run_id),
        db.one("SELECT state, error, failed_item FROM authoring_operation WHERE operation_id = ?", operation_id),
        db.q("SELECT state, ready_at FROM full_auto_assistant_slot WHERE run_id = ?", run_id),
    )
    reservations = db.q(
        "SELECT request_number, settled_at, response_metadata_json "
        "FROM full_auto_assistant_reservation WHERE run_id = ? ORDER BY reservation_id",
        run_id,
    )
    assert len(reservations) == 1
    assert reservations[0]["request_number"] == 1
    assert reservations[0]["settled_at"] is not None
    assert json.loads(reservations[0]["response_metadata_json"])["status"] == 400
    assert cancelled[0].view["state"] == "cancel_requested"
    assert db.one(
        "SELECT state FROM authoring_operation WHERE operation_id = ?", operation_id,
    )["state"] == "cancelled"
    assert db.one(
        "SELECT 1 FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, targets[0],
    ) is None


def test_expiry_after_post_discards_output_and_leaves_exact_reservation(client, seeded, monkeypatch):
    session_id, run_id, revision, targets = _create_run(client, seeded, photo_count=4)
    expiry_text = db.one(
        "SELECT authorization_expires_at FROM full_auto_run_receipt WHERE run_id = ?", run_id,
    )["authorization_expires_at"]
    expiry = datetime.fromisoformat(expiry_text)
    near_expiry = (expiry - timedelta(seconds=5)).isoformat(timespec="seconds")
    monkeypatch.setattr(db, "now", lambda: near_expiry)
    owner, operation_id = _start_child(session_id, run_id, revision, targets)
    db.run(
        "UPDATE authoring_operation SET lease_expires_at = ? WHERE operation_id = ?",
        (expiry + timedelta(minutes=10)).isoformat(timespec="seconds"), operation_id,
    )
    fields = _fields_by_take(session_id, revision, targets)[targets[0]]
    calls = []

    def respond(request):
        calls.append(request)
        return _assistant_response(fields)

    _install_mock_transport(monkeypatch, respond)
    original_settle = resource_full_auto.settle_assistant_dispatch

    def expire_after_settlement(slot_claim, reservation_id, metadata, **kwargs):
        original_settle(slot_claim, reservation_id, metadata, **kwargs)
        monkeypatch.setattr(
            db, "now", lambda: (expiry + timedelta(seconds=1)).isoformat(timespec="seconds"),
        )

    monkeypatch.setattr(resource_full_auto, "settle_assistant_dispatch", expire_after_settlement)
    asyncio.run(main._run_prepare_takes_operation(
        session_id, operation_id, _full_auto_claim=owner,
    ))

    assert len(calls) == 1, (
        db.one("SELECT state, blocker_code FROM full_auto_run WHERE run_id = ?", run_id),
        db.one("SELECT state, error, failed_item FROM authoring_operation WHERE operation_id = ?", operation_id),
        db.q("SELECT state, ready_at FROM full_auto_assistant_slot WHERE run_id = ?", run_id),
    )
    assert db.one(
        "SELECT state, blocker_code FROM full_auto_run WHERE run_id = ?", run_id,
    ) == {"state": "blocked", "blocker_code": "authorization_expired"}
    assert db.one(
        "SELECT state FROM authoring_operation WHERE operation_id = ?", operation_id,
    )["state"] == "failed"
    assert db.one(
        "SELECT 1 FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, targets[0],
    ) is None
    reservation = db.one(
        "SELECT settled_at, response_metadata_json FROM full_auto_assistant_reservation WHERE run_id = ?",
        run_id,
    )
    assert reservation["settled_at"] is not None
    assert json.loads(reservation["response_metadata_json"])["status"] == 200
    assert resource_full_auto.assistant_call_accounting(run_id)["reserved"] == 1


@pytest.mark.parametrize("drift_before_dispatch", [True, False])
def test_provider_drift_fences_full_auto_post_and_response(
    client, seeded, monkeypatch, drift_before_dispatch,
):
    session_id, run_id, revision, targets = _create_run(client, seeded, photo_count=4)
    owner, operation_id = _start_child(session_id, run_id, revision, targets)
    fields = _fields_by_take(session_id, revision, targets)[targets[0]]
    calls = []

    def respond(request):
        calls.append(request)
        return _assistant_response(fields)

    _install_mock_transport(monkeypatch, respond)
    if drift_before_dispatch:
        monkeypatch.setitem(main.CONFIG, "llm_model", "task48-drifted-provider")
    else:
        original_settle = resource_full_auto.settle_assistant_dispatch

        def drift_after_settlement(slot_claim, reservation_id, metadata, **kwargs):
            original_settle(slot_claim, reservation_id, metadata, **kwargs)
            monkeypatch.setitem(main.CONFIG, "llm_model", "task48-drifted-provider")

        monkeypatch.setattr(resource_full_auto, "settle_assistant_dispatch", drift_after_settlement)

    asyncio.run(main._run_prepare_takes_operation(
        session_id, operation_id, _full_auto_claim=owner,
    ))
    assert len(calls) == (0 if drift_before_dispatch else 1)
    assert db.one(
        "SELECT state, blocker_code FROM full_auto_run WHERE run_id = ?", run_id,
    ) == {"state": "blocked", "blocker_code": "authorization_scope_changed"}
    assert db.one(
        "SELECT state FROM authoring_operation WHERE operation_id = ?", operation_id,
    )["state"] == "failed"
    assert db.one(
        "SELECT 1 FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, targets[0],
    ) is None
    reservation = db.one(
        "SELECT settled_at FROM full_auto_assistant_reservation WHERE run_id = ?",
        run_id,
    )
    assert (reservation is not None) is (not drift_before_dispatch)
    if reservation is not None:
        assert reservation["settled_at"] is not None


@pytest.mark.parametrize("disable_after_dispatch", [True, False])
def test_resource_planning_flag_fences_full_auto_post_and_response(
    client, seeded, monkeypatch, disable_after_dispatch,
):
    session_id, run_id, revision, targets = _create_run(client, seeded, photo_count=4)
    owner, operation_id = _start_child(session_id, run_id, revision, targets)
    fields = _fields_by_take(session_id, revision, targets)[targets[0]]
    calls = []

    def respond(request):
        calls.append(request)
        return _assistant_response(fields)

    _install_mock_transport(monkeypatch, respond)
    if disable_after_dispatch:
        monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    else:
        original_settle = resource_full_auto.settle_assistant_dispatch

        def disable_after_settlement(slot_claim, reservation_id, metadata, **kwargs):
            original_settle(slot_claim, reservation_id, metadata, **kwargs)
            monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)

        monkeypatch.setattr(resource_full_auto, "settle_assistant_dispatch", disable_after_settlement)

    asyncio.run(main._run_prepare_takes_operation(
        session_id, operation_id, _full_auto_claim=owner,
    ))
    assert len(calls) == (0 if disable_after_dispatch else 1)
    assert db.one(
        "SELECT state, blocker_code FROM full_auto_run WHERE run_id = ?", run_id,
    ) == {"state": "blocked", "blocker_code": "resource_planning_disabled"}
    assert db.one(
        "SELECT state FROM authoring_operation WHERE operation_id = ?", operation_id,
    )["state"] == "cancelled"
    assert db.one(
        "SELECT 1 FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, targets[0],
    ) is None
    reservation = db.one(
        "SELECT settled_at FROM full_auto_assistant_reservation WHERE run_id = ?",
        run_id,
    )
    assert (reservation is not None) is (not disable_after_dispatch)
    if reservation is not None:
        assert reservation["settled_at"] is not None


def test_linked_resume_grant_adds_allowance_without_resetting_reservations(
    client, seeded, monkeypatch,
):
    from test_full_auto_control_api import _control_body

    session_id, run_id, revision, targets = _create_run(client, seeded, photo_count=4)
    owner, operation_id = _start_child(session_id, run_id, revision, targets)
    _install_provider(monkeypatch, session_id, revision, targets)
    asyncio.run(main._run_prepare_takes_operation(
        session_id, operation_id, _full_auto_claim=owner,
    ))
    assert resource_full_auto.assistant_call_accounting(run_id) == {
        "authorized": 8, "reserved": 4, "remaining": 4,
    }

    current = db.one("SELECT revision FROM full_auto_run WHERE run_id = ?", run_id)
    cancelled = resource_full_auto.cancel_run(
        run_id, str(uuid.uuid4()), int(current["revision"]), revision,
        work_pending=None, derive_work_pending=True,
    )
    assert cancelled.view["state"] == "cancelled"
    monkeypatch.setitem(main.CONFIG, "llm_model", "task48-renewed-provider")
    compared = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert compared.status_code == 200, compared.text
    body = _control_body(run_id, revision)
    body.update({
        "scope_digest": compared.json()["scope_digest"],
        "comparison_issued_at": compared.json()["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    resumed = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert resumed.status_code == 202, resumed.text
    grants = db.q(
        "SELECT authorization_sequence FROM full_auto_run_authorization "
        "WHERE run_id = ? ORDER BY authorization_sequence", run_id,
    )
    assert [row["authorization_sequence"] for row in grants] == [1]
    reservations = db.q(
        "SELECT authorization_sequence, request_number FROM full_auto_assistant_reservation "
        "WHERE run_id = ? ORDER BY reservation_id", run_id,
    )
    assert [(row["authorization_sequence"], row["request_number"]) for row in reservations] == [
        (0, 1), (0, 1), (0, 1), (0, 1),
    ]
    assert resource_full_auto.assistant_call_accounting(run_id) == {
        "authorized": 16, "reserved": 4, "remaining": 12,
    }
    status = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert status.status_code == 200, status.text
    assert status.json()["allowances"]["assistant_calls_total"] == 16
    assert status.json()["allowances"]["assistant_calls_remaining_lower_bound"] == 8


def test_per_take_exhaustion_requires_budget_only_resume_acceptance_and_rebinds_quote(
    client, seeded, monkeypatch,
):
    from test_full_auto_control_api import _control_body

    session_id, run_id, revision, targets = _create_run(client, seeded, photo_count=4)
    run = db.one("SELECT revision FROM full_auto_run WHERE run_id = ?", run_id)
    cancelled = resource_full_auto.cancel_run(
        run_id, str(uuid.uuid4()), int(run["revision"]), revision,
        work_pending=False, derive_work_pending=False,
    )
    assert cancelled.view["state"] == "cancelled"

    initial_quote = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert initial_quote.status_code == 200, initial_quote.text
    assert initial_quote.json()["changed"] is False
    _insert_settled_reservations(
        run_id, session_id, {targets[0]: 2}, sequence=0,
    )

    stale_body = _control_body(run_id, revision)
    stale_body.update({
        "scope_digest": initial_quote.json()["scope_digest"],
        "comparison_issued_at": initial_quote.json()["comparison_issued_at"],
    })
    stale_resume = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=stale_body,
    )
    assert stale_resume.status_code == 409, stale_resume.text
    assert stale_resume.json()["detail"]["code"] == "resume_comparison_stale"
    assert db.q(
        "SELECT authorization_sequence FROM full_auto_run_authorization "
        "WHERE run_id = ? ORDER BY authorization_sequence", run_id,
    ) == []

    status = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert status.status_code == 200, status.text
    assert status.json()["allowances"]["assistant_calls_remaining_lower_bound"] == 6
    assert status.json()["can_resume"] is False
    accounting = resource_full_auto.assistant_call_accounting(run_id)
    assert accounting == {"authorized": 8, "reserved": 2, "remaining": 6}

    quote = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert quote.status_code == 200, quote.text
    projection = quote.json()
    assert projection["changed"] is True
    assert "assistant_allowance" in projection["changed_fields"]
    assert set(projection["changed_fields"]) <= {"deadline", "assistant_allowance"}
    assert projection["assistant_allowance"] == {
        "pending_take_id": targets[0],
        "old_executable_remaining": 0,
        "old_pending_take_remaining": 0,
        "additional_calls_total": 8,
        "additional_calls_per_take": 2,
        "new_executable_remaining": 2,
        "new_pending_take_remaining": 2,
    }
    refused_body = _control_body(run_id, revision)
    refused_body.update({
        "scope_digest": projection["scope_digest"],
        "comparison_issued_at": projection["comparison_issued_at"],
    })
    refused = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=refused_body,
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "authorization_scope_changed"
    assert db.q(
        "SELECT authorization_sequence FROM full_auto_run_authorization "
        "WHERE run_id = ? ORDER BY authorization_sequence", run_id,
    ) == []

    accepted_body = _control_body(run_id, revision)
    accepted_body.update({
        "scope_digest": projection["scope_digest"],
        "comparison_issued_at": projection["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    accepted = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=accepted_body,
    )
    assert accepted.status_code == 202, accepted.text
    assert resource_full_auto.assistant_call_accounting(run_id) == {
        "authorized": 16, "reserved": 2, "remaining": 14,
    }
    resumed = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["allowances"]["assistant_calls_remaining_lower_bound"] == 8


def test_latest_grant_exhaustion_hides_unused_prior_grant_balance(client, seeded, monkeypatch):
    from test_full_auto_control_api import _control_body

    session_id, run_id, revision, targets = _create_run(client, seeded, photo_count=4)
    run = db.one("SELECT revision FROM full_auto_run WHERE run_id = ?", run_id)
    cancelled = resource_full_auto.cancel_run(
        run_id, str(uuid.uuid4()), int(run["revision"]), revision,
        work_pending=False, derive_work_pending=False,
    )
    assert cancelled.view["state"] == "cancelled"

    monkeypatch.setitem(main.CONFIG, "llm_model", "task48-prior-grant-renewal")
    quote = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert quote.status_code == 200, quote.text
    body = _control_body(run_id, revision)
    body.update({
        "scope_digest": quote.json()["scope_digest"],
        "comparison_issued_at": quote.json()["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    renewed = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert renewed.status_code == 202, renewed.text
    run = db.one("SELECT revision FROM full_auto_run WHERE run_id = ?", run_id)
    cancelled_again = resource_full_auto.cancel_run(
        run_id, str(uuid.uuid4()), int(run["revision"]), revision,
        work_pending=False, derive_work_pending=False,
    )
    assert cancelled_again.view["state"] == "cancelled"

    _insert_settled_reservations(
        run_id, session_id, {take_id: 2 for take_id in targets}, sequence=1,
    )
    assert resource_full_auto.assistant_call_accounting(run_id) == {
        "authorized": 16, "reserved": 8, "remaining": 8,
    }
    allowance = resource_full_auto.assistant_executable_allowance(run_id, targets[0])
    assert allowance["authorization_sequence"] == 1
    assert allowance["grant_remaining"] == 0
    assert allowance["executable_remaining"] == 0

    status = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert status.status_code == 200, status.text
    assert status.json()["allowances"]["assistant_calls_total"] == 16
    assert status.json()["allowances"]["assistant_calls_remaining_lower_bound"] == 0
    assert status.json()["can_resume"] is False
    renewal_quote = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert renewal_quote.status_code == 200, renewal_quote.text
    assert renewal_quote.json()["changed"] is True
    assert renewal_quote.json()["assistant_allowance"]["old_executable_remaining"] == 0
    assert renewal_quote.json()["assistant_allowance"]["additional_calls_total"] == 8

    renewed_body = _control_body(run_id, revision)
    renewed_body.update({
        "scope_digest": renewal_quote.json()["scope_digest"],
        "comparison_issued_at": renewal_quote.json()["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    renewed_again = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=renewed_body,
    )
    assert renewed_again.status_code == 202, renewed_again.text
    assert db.q(
        "SELECT authorization_sequence FROM full_auto_run_authorization "
        "WHERE run_id = ? ORDER BY authorization_sequence", run_id,
    ) == [
        {"authorization_sequence": 1},
        {"authorization_sequence": 2},
    ]
    assert resource_full_auto.assistant_call_accounting(run_id) == {
        "authorized": 24, "reserved": 8, "remaining": 16,
    }
    resumed = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["allowances"]["assistant_calls_remaining_lower_bound"] == 8


def test_manual_structured_transport_keeps_reasoning_fallback_unreserved(monkeypatch):
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(400, text="Unsupported reasoning_effort")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    _install_mock_transport(monkeypatch, respond)
    result = asyncio.run(enhance.run_structured(
        main.CONFIG, enhance.EnhanceIn(instruction="manual structured request"),
    ))
    assert result == {}
    assert len(requests) == 2
    assert "reasoning_effort" in requests[0]
    assert "reasoning_effort" not in requests[1]
    assert db.one("SELECT COUNT(*) AS count FROM full_auto_assistant_reservation")["count"] == 0
    assert db.one("SELECT COUNT(*) AS count FROM full_auto_assistant_slot")["count"] == 0
