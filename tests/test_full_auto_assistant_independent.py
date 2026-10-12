"""Independent adversarial checks for Full Auto assistant ownership and budgets."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import threading
import uuid

import httpx
import pytest

import db
import main
from backend import authoring_operations, guided_sessions, resource_full_auto
from test_full_auto_control_api import _control_body
from test_full_auto_preparation_coordinator import _create_run


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _configure_assistant(monkeypatch) -> None:
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "fa48-independent-model")
    monkeypatch.setitem(main.CONFIG, "llm_key", "fa48-independent-key")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)


def _start_owned_child(session_id: int, run_id: str, take_ids: list[str]):
    owner = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(owner)
    revision = int(db.one(
        "SELECT plan_revision FROM session_plan WHERE session_id = ?", session_id,
    )["plan_revision"])
    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": "prepare_takes",
        "take_ids": take_ids,
    })
    view, created = authoring_operations.start_operation(
        session_id, request, planning_enabled=True, assistant_available=True,
        _full_auto_claim=owner,
    )
    assert created is True
    claim = authoring_operations.load_worker_claim(
        session_id, view["operation_id"], planning_enabled=True,
        _full_auto_claim=owner,
    )
    return owner, claim


def _install_async_responder(monkeypatch, responder) -> None:
    class MockAsyncClient(_REAL_ASYNC_CLIENT):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(responder)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(main.enhance.httpx, "AsyncClient", MockAsyncClient)


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


def test_cancelled_post_stays_quarantined_after_database_reopen(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    first_session, first_run, revision, first_targets = _create_run(
        client, seeded, photo_count=4,
    )
    second_session, second_run, _revision, second_targets = _create_run(
        client, seeded, photo_count=4,
    )
    first_owner, first_child = _start_owned_child(
        first_session, first_run, first_targets,
    )
    second_owner, second_child = _start_owned_child(
        second_session, second_run, second_targets,
    )

    calls = []
    remote_request_live = {"value": False}
    request_entered = asyncio.Event()

    async def respond(request):
        calls.append(request)
        if len(calls) == 1:
            # The provider accepted the request before the local coroutine is
            # cancelled; its server-side work remains live until the timeout.
            remote_request_live["value"] = True
            request_entered.set()
            await asyncio.Event().wait()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not structured JSON"}}]},
            request=request,
        )

    _install_async_responder(monkeypatch, respond)

    async def exercise():
        first_task = asyncio.create_task(main._run_prepare_takes_operation(
            first_session, first_child.operation_id, _full_auto_claim=first_owner,
        ))
        await asyncio.wait_for(request_entered.wait(), timeout=3)
        first_reservation = db.one(
            "SELECT * FROM full_auto_assistant_reservation WHERE run_id = ?",
            first_run,
        )
        assert first_reservation is not None
        assert first_reservation["settled_at"] is None

        first_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_task

        assert remote_request_live["value"] is True
        slot = db.one(
            "SELECT state, lease_expires_at, inflight_reservation_id "
            "FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
            first_run, first_targets[0],
        )
        assert slot["state"] == "quarantined"
        assert slot["inflight_reservation_id"] == first_reservation["reservation_id"]
        deadline = datetime.fromisoformat(slot["lease_expires_at"])
        dispatch = datetime.fromisoformat(first_reservation["dispatched_at"])
        assert deadline >= dispatch + timedelta(seconds=300)

        # Reopening the isolated DB must preserve the consumed physical call
        # and the quarantine that prevents a second Full Auto POST.
        database_path = main.DATA_DIR / "idevgen.db"
        db.conn().close()
        db.connect(database_path)
        persisted = db.one(
            "SELECT settled_at FROM full_auto_assistant_reservation "
            "WHERE reservation_id = ?", first_reservation["reservation_id"],
        )
        assert persisted == {"settled_at": None}
        assert resource_full_auto.assistant_call_accounting(first_run) == {
            "authorized": 8, "reserved": 1, "remaining": 7,
        }

        second_task = asyncio.create_task(main._run_prepare_takes_operation(
            second_session, second_child.operation_id, _full_auto_claim=second_owner,
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
                raise AssertionError("the second Full Auto run did not wait behind quarantine")
            assert remote_request_live["value"] is True
            assert len(calls) == 1

            # Simulate the provider timeout boundary without sleeping five
            # minutes. The next run may dispatch only after this durable fence
            # becomes eligible for recovery.
            old_dispatch = (datetime.now(timezone.utc) - timedelta(seconds=301)).isoformat(
                timespec="seconds",
            )
            db.run(
                "UPDATE full_auto_assistant_reservation SET dispatched_at = ? "
                "WHERE reservation_id = ?",
                old_dispatch, first_reservation["reservation_id"],
            )
            db.run(
                "UPDATE full_auto_assistant_slot SET lease_expires_at = ? "
                "WHERE run_id = ? AND take_id = ?",
                old_dispatch, first_run, first_targets[0],
            )
            remote_request_live["value"] = False
            await asyncio.wait_for(second_task, timeout=5)
        finally:
            if not second_task.done():
                second_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await second_task

        assert len(calls) == 2
        assert db.one(
            "SELECT COUNT(*) AS count FROM full_auto_assistant_reservation WHERE run_id = ?",
            first_run,
        )["count"] == 1
        assert db.one(
            "SELECT COUNT(*) AS count FROM full_auto_assistant_reservation WHERE run_id = ?",
            second_run,
        )["count"] == 1

    asyncio.run(exercise())


def test_slot_admission_rechecks_run_lease_after_waiting_for_sqlite_lock(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, _revision, targets = _create_run(client, seeded, photo_count=4)
    owner, operation = _start_owned_child(session_id, run_id, targets)
    run = db.one("SELECT lease_expires_at FROM full_auto_run WHERE run_id = ?", run_id)
    deadline = datetime.fromisoformat(run["lease_expires_at"])
    before_expiry = deadline - timedelta(seconds=1)
    after_expiry = deadline + timedelta(seconds=1)
    clock = [before_expiry]
    monkeypatch.setattr(
        db, "now", lambda: clock[0].isoformat(timespec="seconds"),
    )

    entered_transaction = threading.Event()
    original_transaction = db.transaction

    @contextmanager
    def observed_transaction():
        entered_transaction.set()
        with original_transaction():
            yield

    monkeypatch.setattr(db, "transaction", observed_transaction)
    results = []
    failures = []

    def acquire():
        try:
            provider = guided_sessions._provider_identity(main.CONFIG)[0]
            results.append(resource_full_auto.try_acquire_assistant_slot(
                owner, operation, targets[0], provider,
            ))
        except BaseException as exc:  # preserve the worker-thread assertion result
            failures.append(exc)

    with original_transaction():
        worker = threading.Thread(target=acquire)
        worker.start()
        assert entered_transaction.wait(timeout=3)
        # For an implementation that samples before BEGIN IMMEDIATE, the
        # timestamp is already stale by the time this waiter enters the lock.
        clock[0] = after_expiry
    worker.join(timeout=5)

    assert not worker.is_alive(), "the blocked admission thread did not finish"
    assert not results, "stale run ownership acquired the assistant slot after the lock wait"
    assert len(failures) == 1 and isinstance(failures[0], resource_full_auto.FullAutoRunError)
    assert failures[0].code == "run_owner_stale"
    assert db.one(
        "SELECT state FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
        run_id, targets[0],
    ) is None


@pytest.mark.parametrize("action", ["reserve", "renew"])
def test_assistant_slot_rechecks_expiry_after_owner_validation_before_write(
    client, seeded, monkeypatch, action,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, _revision, targets = _create_run(client, seeded, photo_count=4)
    owner, operation = _start_owned_child(session_id, run_id, targets)
    provider = guided_sessions._provider_identity(main.CONFIG)[0]
    slot = resource_full_auto.try_acquire_assistant_slot(
        owner, operation, targets[0], provider,
    )
    assert slot is not None

    expiry = datetime.fromisoformat(db.now()) + timedelta(seconds=20)
    db.run(
        "UPDATE full_auto_assistant_slot SET lease_expires_at = ? "
        "WHERE run_id = ? AND take_id = ?",
        expiry.isoformat(timespec="seconds"), run_id, targets[0],
    )
    slot = resource_full_auto._make_assistant_slot_claim(db.one(
        "SELECT * FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
        run_id, targets[0],
    ))
    before = db.one(
        "SELECT * FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
        run_id, targets[0],
    )
    clock = [expiry - timedelta(seconds=1)]
    monkeypatch.setattr(
        db, "now", lambda: clock[0].isoformat(timespec="seconds"),
    )
    original_owner_rows = resource_full_auto._assistant_owner_rows

    def advance_after_owner_validation(*args, **kwargs):
        rows = original_owner_rows(*args, **kwargs)
        clock[0] = expiry + timedelta(seconds=1)
        return rows

    monkeypatch.setattr(
        resource_full_auto, "_assistant_owner_rows", advance_after_owner_validation,
    )
    reservation_count_before = db.one(
        "SELECT COUNT(*) AS count FROM full_auto_assistant_reservation WHERE run_id = ?",
        run_id,
    )["count"]

    with pytest.raises(resource_full_auto.FullAutoRunError) as exc:
        if action == "renew":
            resource_full_auto.renew_assistant_slot(
                slot, owner, operation, provider,
            )
        else:
            resource_full_auto.reserve_assistant_dispatch(
                slot, owner, operation, provider,
            )

    assert exc.value.code == "run_owner_stale"
    after = db.one(
        "SELECT * FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
        run_id, targets[0],
    )
    assert after == before
    assert db.one(
        "SELECT COUNT(*) AS count FROM full_auto_assistant_reservation WHERE run_id = ?",
        run_id,
    )["count"] == reservation_count_before


def test_expired_scope_does_not_add_allowance_without_explicit_acceptance(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, revision, _targets = _create_run(client, seeded, photo_count=4)
    original = db.one(
        "SELECT authorization_expires_at FROM full_auto_run_receipt WHERE run_id = ?",
        run_id,
    )
    run = db.one("SELECT revision FROM full_auto_run WHERE run_id = ?", run_id)
    cancelled = resource_full_auto.cancel_run(
        run_id, str(uuid.uuid4()), int(run["revision"]), revision,
        work_pending=None, derive_work_pending=True,
    )
    assert cancelled.view["state"] == "cancelled"
    before_grants = db.q(
        "SELECT authorization_sequence, scope_digest "
        "FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    )
    expiry = datetime.fromisoformat(original["authorization_expires_at"])
    monkeypatch.setattr(
        db, "now", lambda: (expiry + timedelta(seconds=1)).isoformat(timespec="seconds"),
    )

    comparison = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert comparison.status_code == 200, comparison.text
    body = _control_body(run_id, revision)
    body.update({
        "scope_digest": comparison.json()["scope_digest"],
        "comparison_issued_at": comparison.json()["comparison_issued_at"],
        "accept_changed_scope": False,
    })
    resumed = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )

    assert resumed.status_code == 409, resumed.text
    assert resumed.json()["detail"]["code"] == "authorization_scope_changed"
    assert db.one(
        "SELECT state FROM full_auto_run WHERE run_id = ?", run_id,
    )["state"] == "cancelled"
    assert db.q(
        "SELECT authorization_sequence, scope_digest "
        "FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    ) == before_grants
    assert resource_full_auto.assistant_call_accounting(run_id) == {
        "authorized": 8, "reserved": 0, "remaining": 8,
    }


def test_failed_initial_and_fallback_consume_two_calls_without_a_third_post(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, _revision, targets = _create_run(client, seeded, photo_count=4)
    owner, operation = _start_owned_child(session_id, run_id, targets)
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(400, text="Unsupported reasoning_effort parameter", request=request)

    _install_async_responder(monkeypatch, respond)
    asyncio.run(main._run_prepare_takes_operation(
        session_id, operation.operation_id, _full_auto_claim=owner,
    ))

    assert len(requests) == 2
    assert "reasoning_effort" in requests[0]
    assert "reasoning_effort" not in requests[1]
    reservations = db.q(
        "SELECT authorization_sequence, take_id, request_number, settled_at, response_metadata_json "
        "FROM full_auto_assistant_reservation WHERE run_id = ? ORDER BY reservation_id",
        run_id,
    )
    assert len(reservations) == 2
    assert [(row["authorization_sequence"], row["take_id"], row["request_number"]) for row in reservations] == [
        (0, targets[0], 1), (0, targets[0], 2),
    ]
    assert all(row["settled_at"] is not None for row in reservations)
    assert [json.loads(row["response_metadata_json"])["status"] for row in reservations] == [400, 400]
    assert db.one(
        "SELECT 1 FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, targets[0],
    ) is None


def test_full_authorization_exhaustion_is_two_posts_per_take(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, _revision, targets = _create_run(client, seeded, photo_count=4)
    owner, operation = _start_owned_child(session_id, run_id, targets)
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, text="Unsupported reasoning_effort parameter", request=request)
        return _valid_writer_response(request)

    _install_async_responder(monkeypatch, respond)
    asyncio.run(main._run_prepare_takes_operation(
        session_id, operation.operation_id, _full_auto_claim=owner,
    ))

    assert len(requests) == 2 * len(targets)
    rows = db.q(
        "SELECT authorization_sequence, take_id, request_number, settled_at "
        "FROM full_auto_assistant_reservation WHERE run_id = ? ORDER BY reservation_id",
        run_id,
    )
    assert [(row["authorization_sequence"], row["take_id"], row["request_number"]) for row in rows] == [
        (0, take_id, request_number)
        for take_id in targets for request_number in (1, 2)
    ]
    assert all(row["settled_at"] is not None for row in rows)
    assert resource_full_auto.assistant_call_accounting(run_id) == {
        "authorized": 2 * len(targets),
        "reserved": 2 * len(targets),
        "remaining": 0,
    }


@pytest.mark.parametrize("ineligible_state", ["blocked", "cancel_requested", "authorization_expired"])
def test_ineligible_fifo_waiter_does_not_hold_slot_priority(
    client, seeded, monkeypatch, ineligible_state,
):
    _configure_assistant(monkeypatch)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    clock = [now]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    real_datetime_type = guided_sessions.datetime

    class FrozenDateTime(real_datetime_type):
        @classmethod
        def now(cls, tz=None):
            value = clock[0]
            return value if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(guided_sessions, "datetime", FrozenDateTime)
    occupying_session, occupying_run, _revision, occupying_targets = _create_run(
        client, seeded, photo_count=4,
    )
    stale_session = stale_run = stale_revision = stale_targets = None
    if ineligible_state == "authorization_expired":
        # Make the middle waiter expire just after the test's current time,
        # while the occupying and later waiter remain valid for seven days.
        clock[0] = now - timedelta(days=7) + timedelta(hours=1)
    stale_session, stale_run, stale_revision, stale_targets = _create_run(
        client, seeded, photo_count=4,
    )
    clock[0] = now
    later_session = later_run = later_targets = None
    if ineligible_state != "authorization_expired":
        later_session, later_run, _later_revision, later_targets = _create_run(
            client, seeded, photo_count=4,
        )
    occupying_owner, occupying_operation = _start_owned_child(
        occupying_session, occupying_run, occupying_targets,
    )
    stale_owner, stale_operation = _start_owned_child(
        stale_session, stale_run, stale_targets,
    )
    if ineligible_state != "authorization_expired":
        later_owner, later_operation = _start_owned_child(
            later_session, later_run, later_targets,
        )
    provider = guided_sessions._provider_identity(main.CONFIG)[0]

    occupied = resource_full_auto.try_acquire_assistant_slot(
        occupying_owner, occupying_operation, occupying_targets[0], provider,
    )
    assert occupied is not None
    assert resource_full_auto.try_acquire_assistant_slot(
        stale_owner, stale_operation, stale_targets[0], provider,
    ) is None
    if ineligible_state != "authorization_expired":
        assert resource_full_auto.try_acquire_assistant_slot(
            later_owner, later_operation, later_targets[0], provider,
        ) is None

    # Make the intended FIFO order deterministic even on a one-second clock.
    db.run(
        "UPDATE full_auto_assistant_slot SET ready_at = ? WHERE run_id = ? AND take_id = ?",
        (now - timedelta(seconds=10)).isoformat(timespec="seconds"), stale_run, stale_targets[0],
    )
    if ineligible_state != "authorization_expired":
        db.run(
            "UPDATE full_auto_assistant_slot SET ready_at = ? WHERE run_id = ? AND take_id = ?",
            (now - timedelta(seconds=5)).isoformat(timespec="seconds"), later_run, later_targets[0],
        )

    if ineligible_state == "blocked":
        resource_full_auto.advance_run(
            stale_owner, "blocked", blocker_code="budget_exhausted",
        )
    elif ineligible_state == "cancel_requested":
        revision = int(db.one(
            "SELECT revision FROM full_auto_run WHERE run_id = ?", stale_run,
        )["revision"])
        action = resource_full_auto.cancel_run(
            stale_run, str(uuid.uuid4()), revision, stale_revision,
            work_pending=True, derive_work_pending=False,
        )
        assert action.view["state"] == "cancel_requested"
    else:
        expiry = datetime.fromisoformat(db.one(
            "SELECT authorization_expires_at FROM full_auto_run_receipt WHERE run_id = ?",
            stale_run,
        )["authorization_expires_at"])
        assert expiry > now
        assert expiry - now == timedelta(hours=1)
        # Create the later waiter under a fresh authorization/lease after the
        # middle run's actual persisted seven-day authorization has expired.
        clock[0] = expiry + timedelta(seconds=1)
        later_session, later_run, _later_revision, later_targets = _create_run(
            client, seeded, photo_count=4,
        )
        real_operation_deadline = db.authoring_operation_lease_deadline
        monkeypatch.setattr(
            db, "authoring_operation_lease_deadline",
            lambda started_at=None: (clock[0] + timedelta(minutes=10)).isoformat(timespec="seconds"),
        )
        later_owner, later_operation = _start_owned_child(
            later_session, later_run, later_targets,
        )
        monkeypatch.setattr(
            db, "authoring_operation_lease_deadline", real_operation_deadline,
        )
        db.run(
            "UPDATE full_auto_assistant_slot SET ready_at = ? WHERE run_id = ? AND take_id = ?",
            clock[0].isoformat(timespec="seconds"), later_run, later_targets[0],
        )

    if ineligible_state != "authorization_expired":
        resource_full_auto.release_assistant_slot(occupied, completed=False)
    admitted = resource_full_auto.try_acquire_assistant_slot(
        later_owner, later_operation, later_targets[0], provider,
    )
    assert admitted is not None, "an ineligible earlier waiter must not retain FIFO priority"
    assert db.one(
        "SELECT state FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
        stale_run, stale_targets[0],
    )["state"] == "released"


def test_fifo_breaks_equal_ready_timestamps_by_run_id(client, seeded, monkeypatch):
    _configure_assistant(monkeypatch)
    runs = []
    targets = []
    owners = []
    operations = []
    for _ in range(3):
        session_id, run_id, revision, take_ids = _create_run(
            client, seeded, photo_count=4,
        )
        owner, operation = _start_owned_child(session_id, run_id, take_ids)
        runs.append(run_id)
        targets.append(take_ids)
        owners.append(owner)
        operations.append(operation)

    provider = guided_sessions._provider_identity(main.CONFIG)[0]
    occupied = resource_full_auto.try_acquire_assistant_slot(
        owners[0], operations[0], targets[0][0], provider,
    )
    assert occupied is not None
    for index in (1, 2):
        assert resource_full_auto.try_acquire_assistant_slot(
            owners[index], operations[index], targets[index][0], provider,
        ) is None

    tied_timestamp = db.now()
    for index in (1, 2):
        db.run(
            "UPDATE full_auto_assistant_slot SET ready_at = ? WHERE run_id = ? AND take_id = ?",
            tied_timestamp, runs[index], targets[index][0],
        )
    winner = min((1, 2), key=lambda index: runs[index])
    loser = 3 - winner
    resource_full_auto.release_assistant_slot(occupied, completed=False)

    assert resource_full_auto.try_acquire_assistant_slot(
        owners[loser], operations[loser], targets[loser][0], provider,
    ) is None
    admitted = resource_full_auto.try_acquire_assistant_slot(
        owners[winner], operations[winner], targets[winner][0], provider,
    )
    assert admitted is not None
    assert admitted.run_id == runs[winner]


def test_failure_settlement_rechecks_child_lease_after_sqlite_lock_wait(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, _revision, targets = _create_run(client, seeded, photo_count=4)
    owner, operation = _start_owned_child(session_id, run_id, targets)
    ticket = authoring_operations.renew_operation_lease(
        operation, planning_enabled=True, _full_auto_claim=owner,
    )
    expiry = datetime.fromisoformat(ticket.lease_expires_at)
    parent_deadline = (expiry + timedelta(minutes=1)).isoformat(timespec="seconds")
    db.run(
        "UPDATE full_auto_run SET lease_expires_at = ? WHERE run_id = ?",
        parent_deadline, run_id,
    )
    owner = resource_full_auto._make_claim(
        db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id), False,
    )
    clock = [expiry - timedelta(seconds=1)]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    before = db.one(
        "SELECT state, lease_expires_at, failed_item, error, remaining_json, updated_at "
        "FROM authoring_operation WHERE operation_id = ?", operation.operation_id,
    )
    entered = threading.Event()
    original_transaction = db.transaction

    @contextmanager
    def observed_transaction():
        entered.set()
        with original_transaction():
            yield

    monkeypatch.setattr(db, "transaction", observed_transaction)
    results, failures = [], []

    def settle():
        try:
            results.append(authoring_operations.fail_full_auto_item_after_authority_fence(
                operation, ticket, targets[0], _full_auto_claim=owner,
            ))
        except BaseException as exc:
            failures.append(exc)

    with original_transaction():
        worker = threading.Thread(target=settle)
        worker.start()
        assert entered.wait(timeout=3)
        clock[0] = expiry + timedelta(seconds=1)
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert not results, "an expired child lease must not settle a Full Auto item after lock contention"
    assert len(failures) == 1
    assert isinstance(failures[0], authoring_operations.AuthoringOperationError)
    assert db.one(
        "SELECT state, lease_expires_at, failed_item, error, remaining_json, updated_at "
        "FROM authoring_operation WHERE operation_id = ?", operation.operation_id,
    ) == before


def test_cancel_settlement_rechecks_child_lease_after_sqlite_lock_wait(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, revision, targets = _create_run(client, seeded, photo_count=4)
    owner, operation = _start_owned_child(session_id, run_id, targets)
    ticket = authoring_operations.renew_operation_lease(
        operation, planning_enabled=True, _full_auto_claim=owner,
    )
    run_revision = int(db.one(
        "SELECT revision FROM full_auto_run WHERE run_id = ?", run_id,
    )["revision"])
    cancelled = resource_full_auto.cancel_run(
        run_id, str(uuid.uuid4()), run_revision, revision,
        work_pending=True, derive_work_pending=False,
    )
    assert cancelled.view["state"] == "cancel_requested"
    expiry = datetime.fromisoformat(ticket.lease_expires_at)
    clock = [expiry - timedelta(seconds=1)]
    monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    before = db.one(
        "SELECT state, lease_expires_at, failed_item, error, remaining_json, updated_at "
        "FROM authoring_operation WHERE operation_id = ?", operation.operation_id,
    )
    entered = threading.Event()
    original_transaction = db.transaction

    @contextmanager
    def observed_transaction():
        entered.set()
        with original_transaction():
            yield

    monkeypatch.setattr(db, "transaction", observed_transaction)
    results, failures = [], []

    def settle():
        try:
            results.append(authoring_operations.cancel_full_auto_item_after_run_cancel(
                operation, ticket, targets[0],
            ))
        except BaseException as exc:
            failures.append(exc)

    with original_transaction():
        worker = threading.Thread(target=settle)
        worker.start()
        assert entered.wait(timeout=3)
        clock[0] = expiry + timedelta(seconds=1)
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert not results, "an expired child lease must not settle cancellation after lock contention"
    assert len(failures) == 1
    assert isinstance(failures[0], authoring_operations.AuthoringOperationError)
    assert db.one(
        "SELECT state, lease_expires_at, failed_item, error, remaining_json, updated_at "
        "FROM authoring_operation WHERE operation_id = ?", operation.operation_id,
    ) == before


def test_latest_exhausted_grant_hides_old_unused_calls_and_resume_requires_new_acceptance(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, revision, targets = _create_run(client, seeded, photo_count=4)
    initial = db.one("SELECT revision FROM full_auto_run WHERE run_id = ?", run_id)
    cancelled = resource_full_auto.cancel_run(
        run_id, str(uuid.uuid4()), int(initial["revision"]), revision,
        work_pending=False, derive_work_pending=False,
    )
    assert cancelled.view["state"] == "cancelled"

    # Accept one provider-scope change to create a linked grant. The first
    # grant keeps four unused calls, while the linked grant will be exhausted.
    monkeypatch.setitem(main.CONFIG, "llm_model", "fa48-independent-renewed-model")
    comparison = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert comparison.status_code == 200, comparison.text
    body = _control_body(run_id, revision)
    body.update({
        "scope_digest": comparison.json()["scope_digest"],
        "comparison_issued_at": comparison.json()["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    resumed = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert resumed.status_code == 202, resumed.text
    assert db.q(
        "SELECT authorization_sequence FROM full_auto_run_authorization "
        "WHERE run_id = ? ORDER BY authorization_sequence", run_id,
    ) == [{"authorization_sequence": 1}]

    stamp = db.now()
    for sequence, take_id, request_number in [
        *((0, take_id, 1) for take_id in targets),
        *((1, take_id, number) for take_id in targets for number in (1, 2)),
    ]:
        db.run(
            """INSERT INTO full_auto_assistant_reservation
               (run_id, authorization_sequence, session_id, take_id, operation_id,
                slot_generation, request_number, dispatched_at, settled_at, response_metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            run_id, sequence, session_id, take_id,
            f"fa48-independent-operation-{sequence}", 1, request_number,
            stamp, stamp, json.dumps({"status": 200}, separators=(",", ":")),
        )

    accounting = resource_full_auto.assistant_call_accounting(run_id)
    assert accounting == {"authorized": 16, "reserved": 12, "remaining": 4}
    owner = resource_full_auto.claim_run(run_id)
    resource_full_auto.advance_run(owner, "blocked", blocker_code="budget_exhausted")

    status = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert status.status_code == 200, status.text
    assert status.json()["allowances"]["assistant_calls_remaining_lower_bound"] == 0
    assert status.json()["can_resume"] is False

    no_change_body = _control_body(run_id, revision)
    no_change_resume = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=no_change_body,
    )
    assert no_change_resume.status_code == 409, no_change_resume.text
    assert no_change_resume.json()["detail"]["code"] == "budget_exhausted"
    assert db.q(
        "SELECT authorization_sequence FROM full_auto_run_authorization "
        "WHERE run_id = ? ORDER BY authorization_sequence", run_id,
    ) == [{"authorization_sequence": 1}]

    # A genuinely changed scope plus a fresh explicit acceptance is the only
    # path that can extend the authorization chain beyond the exhausted grant.
    monkeypatch.setitem(main.CONFIG, "llm_model", "fa48-independent-third-model")
    next_comparison = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert next_comparison.status_code == 200, next_comparison.text
    changed_body = _control_body(run_id, revision)
    changed_body.update({
        "scope_digest": next_comparison.json()["scope_digest"],
        "comparison_issued_at": next_comparison.json()["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    changed_resume = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=changed_body,
    )
    assert changed_resume.status_code == 202, changed_resume.text
    assert db.q(
        "SELECT authorization_sequence FROM full_auto_run_authorization "
        "WHERE run_id = ? ORDER BY authorization_sequence", run_id,
    ) == [
        {"authorization_sequence": 1},
        {"authorization_sequence": 2},
    ]
    updated = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert updated.status_code == 200, updated.text
    assert updated.json()["allowances"]["assistant_calls_remaining_lower_bound"] == 8


@pytest.mark.parametrize("hang_on", [1, 2], ids=["initial", "compatibility-fallback"])
def test_owner_loss_quarantine_covers_live_transport_after_timeout(
    client, seeded, monkeypatch, hang_on,
):
    """An owner pause must not let a second Full Auto POST overlap a live one."""
    _configure_assistant(monkeypatch)
    monkeypatch.setattr(main.enhance, "TIMEOUT", 0.25)
    first_session, first_run, _revision, first_targets = _create_run(
        client, seeded, photo_count=4,
    )
    first_owner, first_child = _start_owned_child(
        first_session, first_run, first_targets,
    )
    second_session, second_run, _revision, second_targets = _create_run(
        client, seeded, photo_count=4,
    )
    second_owner, second_child = _start_owned_child(
        second_session, second_run, second_targets,
    )
    calls = []
    active = 0
    maximum_active = 0
    first_entered = asyncio.Event()
    first_exited = asyncio.Event()
    second_entered = asyncio.Event()
    release_first = asyncio.Event()
    events = []

    async def respond(request):
        nonlocal active, maximum_active
        calls.append(request)
        active += 1
        maximum_active = max(maximum_active, active)
        try:
            if hang_on == 2 and len(calls) == 1:
                return httpx.Response(
                    400, text="Unrecognized reasoning_effort parameter", request=request,
                )
            if len(calls) == hang_on:
                events.append("first_enter")
                first_entered.set()
                await release_first.wait()
            else:
                events.append("next_enter")
                second_entered.set()
            return _valid_writer_response(request)
        except asyncio.CancelledError:
            events.append("first_cancelled")
            raise
        finally:
            active -= 1
            if len(calls) == hang_on:
                events.append("first_exit")
                first_exited.set()

    _install_async_responder(monkeypatch, respond)

    async def exercise():
        first_task = asyncio.create_task(main._run_prepare_takes_operation(
            first_session, first_child.operation_id, _full_auto_claim=first_owner,
        ))
        await asyncio.wait_for(first_entered.wait(), timeout=3)

        # Model a paused coordinator with an expired 10-minute owner lease. Keep
        # the dispatch time current so the scaled total request deadline and its
        # persisted reservation/quarantine boundary remain aligned.
        old = (datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat(
            timespec="seconds",
        )
        db.run(
            "UPDATE full_auto_run SET lease_expires_at = ? WHERE run_id = ?",
            old, first_run,
        )
        db.run(
            "UPDATE authoring_operation SET lease_expires_at = ? WHERE operation_id = ?",
            old, first_child.operation_id,
        )
        db.run(
            "UPDATE full_auto_assistant_slot SET lease_expires_at = ? "
            "WHERE run_id = ? AND take_id = ?",
            old, first_run, first_targets[0],
        )
        second_task = asyncio.create_task(main._run_prepare_takes_operation(
            second_session, second_child.operation_id, _full_auto_claim=second_owner,
        ))
        try:
            try:
                await asyncio.wait_for(first_exited.wait(), timeout=2)
            except asyncio.TimeoutError:
                raise AssertionError(
                    "the held Full Auto c.post outlived the scaled total request timeout"
                )
            await asyncio.wait_for(second_entered.wait(), timeout=5)
            assert events.index("first_exit") < events.index("next_enter"), (
                "a second Full Auto POST started before the prior c.post ended"
            )
            assert maximum_active == 1
            first_reservations = db.q(
                "SELECT request_number, settled_at FROM full_auto_assistant_reservation "
                "WHERE run_id = ? ORDER BY request_number",
                first_run,
            )
            assert [(row["request_number"], row["settled_at"] is not None)
                    for row in first_reservations] == [(number, True) for number in range(1, hang_on + 1)]
        finally:
            release_first.set()
            await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=10)

        assert maximum_active == 1

    asyncio.run(exercise())

