"""Independent acceptance for local Full Auto POST completion fencing."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import threading

import httpx
import pytest

import db
import enhance
import main
from backend import authoring_operations, guided_sessions, resource_full_auto
from test_full_auto_assistant_slots import _create_run, _start_child


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _configure_assistant(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "task48-test-provider")
    monkeypatch.setitem(main.CONFIG, "llm_key", "task48-test-secret")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)


def _install_transport(monkeypatch, responder):
    class MockAsyncClient(_REAL_ASYNC_CLIENT):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(responder)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(enhance.httpx, "AsyncClient", MockAsyncClient)


def test_cancel_waits_for_assistant_reservation_to_unwind_before_settlement(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, revision, targets = _create_run(client, seeded, photo_count=4)
    owner, operation_id = _start_child(session_id, run_id, revision, targets)
    child = authoring_operations.load_worker_claim(
        session_id, operation_id, planning_enabled=True,
        _full_auto_claim=owner,
    )
    provider_identity = guided_sessions._provider_identity(main.CONFIG)[0]
    slot = resource_full_auto.try_acquire_assistant_slot(
        owner, child, targets[0], provider_identity,
    )
    assert slot is not None
    dispatch = resource_full_auto.reserve_assistant_dispatch(
        slot, owner, child, provider_identity,
    )

    current = resource_full_auto.get_run(run_id)
    cancelled = resource_full_auto.cancel_run(
        run_id, "assistant-cancel-inflight", current["revision"], current["plan_revision"],
        work_pending=None, derive_work_pending=True,
    )

    assert cancelled.view["state"] == "cancel_requested"
    cancellation_claim = resource_full_auto.claim_run(run_id)
    assert resource_full_auto.unresolved_work_count(session_id, run_id) > 0
    row = db.one(
        "SELECT settled_at FROM full_auto_assistant_reservation WHERE run_id = ?",
        run_id,
    )
    assert row["settled_at"] is None

    resource_full_auto.settle_assistant_dispatch(slot, dispatch, {"status": None})
    resource_full_auto.finish_local_assistant_dispatch(dispatch)
    assert resource_full_auto.unresolved_work_count(session_id, run_id) == 0

    settled = resource_full_auto.reconcile_cancellation(
        cancellation_claim,
        resource_full_auto.CancellationEvidence("settled", db.now(), 0),
    )
    assert settled["state"] == "cancelled"


def test_live_post_fence_survives_owner_loss_quarantine_expiry_and_loop_delay(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    timeout_seconds = 0.2
    monkeypatch.setattr(enhance, "TIMEOUT", timeout_seconds)
    monkeypatch.setattr(
        resource_full_auto, "ASSISTANT_DISPATCH_TIMEOUT",
        timedelta(seconds=timeout_seconds),
    )

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
    second_child = authoring_operations.load_worker_claim(
        second_session, second_operation, planning_enabled=True,
        _full_auto_claim=second_owner,
    )
    provider_identity = guided_sessions._provider_identity(main.CONFIG)[0]

    calls = []
    active = [0]
    maximum_active = [0]
    lock = threading.Lock()
    fallback_entered = threading.Event()
    cancellation_cleanup_entered = threading.Event()
    fallback_finished = threading.Event()
    allow_cleanup = asyncio.Event()
    second_post_after_fallback = []

    async def respond(request):
        with lock:
            calls.append(request)
            request_number = len(calls)
            active[0] += 1
            maximum_active[0] = max(maximum_active[0], active[0])
        try:
            if request_number == 1:
                return httpx.Response(
                    400, text="Unsupported reasoning_effort", request=request,
                )
            if request_number == 2:
                fallback_entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancellation_cleanup_entered.set()
                    await allow_cleanup.wait()
                    fallback_finished.set()
            second_post_after_fallback.append(fallback_finished.is_set())
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "{}"}}]},
                request=request,
            )
        finally:
            with lock:
                active[0] -= 1

    _install_transport(monkeypatch, respond)

    first_loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=first_loop.run_forever, daemon=True)
    loop_thread.start()
    first_task = asyncio.run_coroutine_threadsafe(
        main._run_prepare_takes_operation(
            first_session, first_operation, _full_auto_claim=first_owner,
        ),
        first_loop,
    )
    loop_release = threading.Event()
    loop_blocked = threading.Event()

    def block_first_loop():
        loop_blocked.set()
        loop_release.wait()

    try:
        assert fallback_entered.wait(timeout=3), "the compatibility fallback POST did not start"
        first_reservations = db.q(
            "SELECT reservation_id, settled_at, response_metadata_json "
            "FROM full_auto_assistant_reservation WHERE run_id = ? ORDER BY reservation_id",
            first_run,
        )
        assert len(first_reservations) == 2
        assert first_reservations[0]["settled_at"] is not None
        assert json.loads(first_reservations[0]["response_metadata_json"]) == {"status": 400}
        assert first_reservations[1]["settled_at"] is None

        expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(
            timespec="seconds",
        )
        db.run(
            "UPDATE full_auto_run SET lease_expires_at = ? WHERE run_id = ?",
            expired, first_run,
        )
        db.run(
            "UPDATE full_auto_assistant_slot SET lease_expires_at = ? "
            "WHERE run_id = ? AND take_id = ?",
            expired, first_run, first_targets[0],
        )
        replacement = resource_full_auto.claim_run(first_run)
        assert replacement.requires_reconciliation is True
        quarantined = db.one(
            "SELECT state, inflight_reservation_id, lease_expires_at "
            "FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
            first_run, first_targets[0],
        )
        assert quarantined["state"] == "quarantined"
        assert quarantined["inflight_reservation_id"] == first_reservations[1]["reservation_id"]

        # Keep a live local POST tied to the same durable database after its
        # connection is reopened, then expire only its persisted quarantine.
        database_path = main.DATA_DIR / "idevgen.db"
        db.conn().close()
        db.connect(database_path)
        assert db.one(
            "SELECT settled_at FROM full_auto_assistant_reservation WHERE reservation_id = ?",
            first_reservations[1]["reservation_id"],
        ) == {"settled_at": None}
        db.run(
            "UPDATE full_auto_assistant_slot SET lease_expires_at = ? "
            "WHERE run_id = ? AND take_id = ?",
            expired, first_run, first_targets[0],
        )

        async def second_dispatch_after_release():
            slot = None
            while slot is None:
                slot = resource_full_auto.try_acquire_assistant_slot(
                    second_owner, second_child, second_targets[0], provider_identity,
                )
                if slot is None:
                    await asyncio.sleep(0.01)
            try:
                return await enhance.run_structured(
                    main.CONFIG,
                    enhance.EnhanceIn(instruction="Independent authorized request"),
                    full_auto_before_dispatch=lambda: resource_full_auto.reserve_assistant_dispatch(
                        slot, second_owner, second_child, provider_identity,
                    ),
                    full_auto_settle_dispatch=lambda token, metadata: resource_full_auto.settle_assistant_dispatch(
                        slot, token, metadata,
                    ),
                    full_auto_check_response=lambda token, metadata: resource_full_auto.check_assistant_response(
                        slot, second_owner, second_child, provider_identity,
                    ),
                    full_auto_finish_dispatch=resource_full_auto.finish_local_assistant_dispatch,
                )
            finally:
                resource_full_auto.release_assistant_slot(slot, completed=True)

        async def observe_waiter_during_timeout_and_unwind():
            second_attempts = [0]
            attempt = asyncio.Event()
            waiting = asyncio.Event()
            keep_waiting = True

            async def second_waiter():
                while keep_waiting:
                    await attempt.wait()
                    attempt.clear()
                    second_attempts[0] += 1
                    slot = resource_full_auto.try_acquire_assistant_slot(
                        second_owner, second_child, second_targets[0], provider_identity,
                    )
                    if slot is not None:
                        resource_full_auto.release_assistant_slot(slot, completed=False)
                        raise AssertionError("the second run acquired a live local POST fence")
                    persisted = db.one(
                        "SELECT state FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
                        second_run, second_targets[0],
                    )
                    if persisted is not None and persisted["state"] == "waiting":
                        waiting.set()

            waiter = asyncio.create_task(second_waiter())
            attempt.set()
            await asyncio.wait_for(waiting.wait(), timeout=3)
            assert len(calls) == 2

            first_loop.call_soon_threadsafe(block_first_loop)
            assert await asyncio.to_thread(loop_blocked.wait, 3)
            await asyncio.sleep(timeout_seconds * 2)
            assert not fallback_finished.is_set(), "the blocked loop unexpectedly finished transport cleanup"

            attempt.set()
            while second_attempts[0] < 2:
                await asyncio.sleep(0.005)
            assert len(calls) == 2, "a second physical POST started while the first loop was stalled"
            assert db.one(
                "SELECT settled_at FROM full_auto_assistant_reservation WHERE reservation_id = ?",
                first_reservations[1]["reservation_id"],
            ) == {"settled_at": None}
            assert resource_full_auto.assistant_call_accounting(first_run)["reserved"] == 2
            assert resource_full_auto.assistant_call_accounting(second_run)["reserved"] == 0

            loop_release.set()
            assert await asyncio.to_thread(cancellation_cleanup_entered.wait, 3)
            attempt.set()
            while second_attempts[0] < 3:
                await asyncio.sleep(0.005)
            assert len(calls) == 2, "a second physical POST started during cancellation cleanup"
            assert active[0] == 1
            assert resource_full_auto.assistant_call_accounting(second_run)["reserved"] == 0

            first_loop.call_soon_threadsafe(allow_cleanup.set)
            return waiter

        waiter = asyncio.run(observe_waiter_during_timeout_and_unwind())
        # The observer loop has stopped the waiter in a persisted FIFO wait.
        # Let the original HTTPX call finish before allowing another dispatch.
        try:
            first_task.result(timeout=5)
        finally:
            loop_release.set()
            first_loop.call_soon_threadsafe(allow_cleanup.set)
        assert fallback_finished.is_set()
        settled = db.one(
            "SELECT settled_at, response_metadata_json FROM full_auto_assistant_reservation "
            "WHERE reservation_id = ?", first_reservations[1]["reservation_id"],
        )
        assert settled["settled_at"] is not None
        assert json.loads(settled["response_metadata_json"]) == {"status": None}

        # The second run only gets a reservation after the actual transport
        # unwind has completed, and the request is still FIFO-admitted.
        assert asyncio.run(second_dispatch_after_release()) == {}
        assert fallback_finished.is_set()
        assert second_post_after_fallback == [True]
        assert len(calls) == 3
        assert maximum_active == [1]
        assert ["reasoning_effort" in json.loads(request.content) for request in calls[:2]] == [
            True, False,
        ]
        assert resource_full_auto.assistant_call_accounting(first_run) == {
            "authorized": 8, "reserved": 2, "remaining": 6,
        }
        assert resource_full_auto.assistant_call_accounting(second_run) == {
            "authorized": 8, "reserved": 1, "remaining": 7,
        }
        first_slot = db.one(
            "SELECT state, inflight_reservation_id FROM full_auto_assistant_slot "
            "WHERE run_id = ? AND take_id = ?",
            first_run, first_targets[0],
        )
        assert first_slot["state"] == "released"
        assert first_slot["inflight_reservation_id"] is None
        assert db.one(
            "SELECT state FROM full_auto_assistant_slot WHERE run_id = ? AND take_id = ?",
            second_run, second_targets[0],
        )["state"] == "completed"
    finally:
        loop_release.set()
        first_loop.call_soon_threadsafe(allow_cleanup.set)
        if not first_task.done():
            first_task.cancel()
            try:
                first_task.result(timeout=5)
            except (asyncio.CancelledError, Exception):
                pass
        first_loop.call_soon_threadsafe(first_loop.stop)
        loop_thread.join(timeout=5)
        first_loop.close()


def test_transport_exception_finishes_the_local_dispatch_fence(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, run_id, revision, targets = _create_run(client, seeded, photo_count=4)
    owner, operation_id = _start_child(session_id, run_id, revision, targets)
    child = authoring_operations.load_worker_claim(
        session_id, operation_id, planning_enabled=True,
        _full_auto_claim=owner,
    )
    provider_identity = guided_sessions._provider_identity(main.CONFIG)[0]
    slot = resource_full_auto.try_acquire_assistant_slot(
        owner, child, targets[0], provider_identity,
    )
    assert slot is not None
    order = []

    def fail_transport(request):
        order.append("post")
        raise httpx.ConnectError("synthetic transport failure", request=request)

    _install_transport(monkeypatch, fail_transport)

    def reserve():
        order.append("reserve")
        return resource_full_auto.reserve_assistant_dispatch(
            slot, owner, child, provider_identity,
        )

    def finish(fence):
        order.append("finish")
        resource_full_auto.finish_local_assistant_dispatch(fence)

    def settle(fence, metadata):
        order.append("settle")
        resource_full_auto.settle_assistant_dispatch(slot, fence, metadata)

    def check(_fence, _metadata):
        order.append("check")
        resource_full_auto.check_assistant_response(
            slot, owner, child, provider_identity,
        )

    with pytest.raises(enhance.AssistantProviderError) as error:
        asyncio.run(enhance.run_structured(
            main.CONFIG,
            enhance.EnhanceIn(instruction="Independent authorized request"),
            full_auto_before_dispatch=reserve,
            full_auto_settle_dispatch=settle,
            full_auto_check_response=check,
            full_auto_finish_dispatch=finish,
        ))
    assert error.value.code == "assistant_transport_error"
    assert order == ["reserve", "post", "settle", "check", "finish"]
    reservation = db.one(
        "SELECT settled_at, response_metadata_json FROM full_auto_assistant_reservation "
        "WHERE run_id = ?", run_id,
    )
    assert reservation["settled_at"] is not None
    assert json.loads(reservation["response_metadata_json"]) == {"status": None}

    resource_full_auto.release_assistant_slot(slot, completed=False)
    retry_slot = resource_full_auto.try_acquire_assistant_slot(
        owner, child, targets[0], provider_identity,
    )
    assert retry_slot is not None, "the completed transport left the local dispatch fence registered"
    resource_full_auto.release_assistant_slot(retry_slot, completed=False)
