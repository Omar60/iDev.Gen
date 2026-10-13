"""Independent restart acceptance for Full Auto's durable checkpoints."""
from __future__ import annotations
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid
import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
import db
import main
from backend import (
    authoring_operations, resource_full_auto, resource_preparation, resource_store,
    workflow_binding,
)
from conftest import FakeComfy, GRAPH, ROOT
from runner import Runner
import test_full_auto_preparation_coordinator as preparation
import test_full_auto_submission_independent as submission
import test_runner


class _ProcessExit(BaseException):
    """Stop at a committed boundary without running application recovery code."""


class _RestartableApp:
    def __init__(self) -> None:
        self.client: TestClient | None = None
    def start(self) -> TestClient:
        self.client = TestClient(main.app)
        return self.client.__enter__()
    def stop(self) -> None:
        if self.client is not None:
            self.client.__exit__(None, None, None)
            self.client = None
    def restart(self) -> TestClient:
        self.stop()
        return self.start()


def _seed(api: TestClient) -> dict:
    items = json.loads((ROOT / "data" / "catalogue-seed.json").read_text(encoding="utf-8"))
    seen_readings = set()
    for item in items:
        db.run(
            """INSERT INTO component
               (concept_key, slot, manner, family, faces, wording, judge_label, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            item["concept_key"], item["slot"], item["manner"], item.get("family", ""),
            item.get("faces", ""), item["wording"], item["judge_label"], db.now(),
        )
        family = item.get("family", "")
        key = (item["slot"], item["manner"], family)
        if family and key not in seen_readings:
            seen_readings.add(key)
            db.run(
                """INSERT INTO reading (slot, manner, session_id, key, label, created_at)
                   VALUES (?, ?, NULL, ?, ?, ?)""",
                item["slot"], item["manner"], family,
                item.get("judge_label") or family, db.now(),
            )
    workflow = api.post("/api/workflows", json={"name": "startup-wf", "graph": GRAPH})
    assert workflow.status_code == 200, workflow.text
    model = api.post("/api/models", json={
        "name": "startup-ada",
        "lora_name": "characters/ada.safetensors",
        "trigger": "4da woman",
        "base_positive": "photo, 35mm",
        "base_negative": "blurry",
        "workflow_id": workflow.json()["id"],
        "settings": {"width": 832, "height": 1216, "steps": 8, "cfg": 1.0},
    })
    assert model.status_code == 200, model.text
    return {"workflow_id": workflow.json()["id"], "model_id": model.json()["id"]}


@pytest.fixture
def restartable_world(monkeypatch, tmp_path):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "startup-independent-provider")
    monkeypatch.setitem(main.CONFIG, "llm_key", "startup-independent-test-key")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    comfy_output = tmp_path / "comfy-output"
    comfy_output.mkdir()
    sessions_dir = tmp_path / "sessions"
    fake = FakeComfy(comfy_output)
    runner = Runner(fake, sessions_dir, comfy_output)
    monkeypatch.setattr(main, "COMFY_OUTPUT", comfy_output)
    monkeypatch.setattr(main, "SESSIONS_DIR", sessions_dir)
    monkeypatch.setitem(main.CONFIG, "comfy_output_dir", str(comfy_output))
    monkeypatch.setattr(main, "comfy", fake)
    monkeypatch.setattr(main, "runner", runner)
    app = _RestartableApp()
    api = app.start()
    seeded = _seed(api)
    try:
        yield {
            "app": app, "api": api, "seeded": seeded, "fake": fake,
            "runner": runner,
        }
    finally:
        app.stop()
        db.run(
            "UPDATE global_runner_claim SET owner_kind=NULL, session_id=NULL, run_id=NULL, "
            "owner_nonce=NULL, lease_started_at=NULL, lease_expires_at=NULL WHERE singleton=1",
        )
        db.run("DELETE FROM full_auto_run")
        for table in (
            "shot", "session", "model", "workflow", "cell", "component", "reading",
            "garment", "outfit", "saved_look_photo_evidence",
            "saved_look_automation_profile", "saved_look_version",
        ):
            db.run(f"DELETE FROM {table}")


def _expire_run(run_id: str) -> None:
    db.run(
        "UPDATE full_auto_run SET lease_started_at=?, lease_expires_at=? WHERE run_id=?",
        "1999-12-31T23:59:00+00:00", "2000-01-01T00:00:00+00:00", run_id,
    )


def _expire_runner() -> None:
    db.run(
        "UPDATE global_runner_claim SET lease_started_at=?, lease_expires_at=? "
        "WHERE singleton=1 AND owner_kind IS NOT NULL",
        "1999-12-31T23:59:00+00:00", "2000-01-01T00:00:00+00:00",
    )


def _status(api: TestClient, session_id: int, run_id: str) -> dict:
    response = api.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert response.status_code == 200, {
        "response": response.text,
        "requested_session_id": session_id,
        "requested_run_id": run_id,
    }
    return response.json()


def _wait_for_settlement(world: dict, session_id: int, run_id: str) -> dict:
    deadline = time.monotonic() + 15
    latest = _status(world["api"], session_id, run_id)
    while latest["state"] in {"active", "waiting_authoring", "waiting_runner", "cancel_requested"}:
        if time.monotonic() >= deadline:
            break
        time.sleep(0.02)
        latest = _status(world["api"], session_id, run_id)
    if latest["state"] in {"active", "waiting_authoring", "waiting_runner", "cancel_requested"}:
        raise AssertionError({
            "view": latest,
            "run": db.one("SELECT * FROM full_auto_run WHERE run_id=?", run_id),
            "attempts": db.q(
                "SELECT * FROM full_auto_execution_attempt WHERE run_id=? ORDER BY shot_id, attempt_number",
                run_id,
            ),
            "shots": db.q(
                "SELECT id, status, seed, filename, error FROM shot WHERE session_id=? ORDER BY id",
                session_id,
            ),
        })
    return latest


def _strict_writer(
    monkeypatch, session_id: int, revision: int, targets: list[str],
):
    provider = preparation._install_provider(monkeypatch, session_id, revision, targets)
    expected = list(provider["dispatch_order"])
    calls: list[str] = []
    unexpected: list[str] = []
    original_client = preparation._ORIGINAL_ASYNC_CLIENT
    def respond(request: httpx.Request) -> httpx.Response:
        user = next(message["content"] for message in json.loads(request.content)["messages"]
                    if message["role"] == "user")
        prefix, separator, serialized = user.partition("Context: ")
        assert separator and prefix
        context = json.loads(serialized)
        take_id = context["take_id"]
        calls.append(take_id)
        if not expected or expected[0] != take_id:
            unexpected.append(take_id)
        else:
            expected.pop(0)
        fields = context["preparation"]["requested_fields"]
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps({
                field: f"{field} for {take_id}" for field in fields
            })}}],
        }, request=request)
    def local_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(respond)
        return original_client(*args, **kwargs)
    monkeypatch.setattr(main.enhance.httpx, "AsyncClient", local_client)
    return {
        "calls": calls, "unexpected": unexpected, "expected": provider["dispatch_order"],
        "remaining": expected,
    }


def _independent_sqlite_snapshot(run_id: str) -> dict:
    database_path = (main.DATA_DIR / "idevgen.db").resolve()
    connection = sqlite3.connect(
        f"{database_path.as_uri()}?mode=ro", uri=True, timeout=0.25,
    )
    connection.row_factory = sqlite3.Row
    try:
        run = connection.execute(
            "SELECT * FROM full_auto_run WHERE run_id=?", (run_id,),
        ).fetchone()
        operation_rows = connection.execute(
            "SELECT operation_id, state, completed_json, remaining_json "
            "FROM authoring_operation WHERE full_auto_run_id=? ORDER BY created_at, operation_id",
            (run_id,),
        ).fetchall()
        receipt = connection.execute(
            "SELECT run_id, session_id, scope_digest, dependencies_digest, plan_digest "
            "FROM full_auto_run_receipt WHERE run_id=?", (run_id,),
        ).fetchone()
        return {
            "run": dict(run) if run is not None else None,
            "operations": [dict(row) for row in operation_rows],
            "receipt": dict(receipt) if receipt is not None else None,
        }
    finally:
        connection.close()


def _begin_execution(world: dict, monkeypatch, *, photo_count: int = 4):
    session_id, run_id, revision, targets, submit_owner = _without_post_commit_dispatch(
        monkeypatch, submission._approved_run,
        world["api"], world["seeded"], monkeypatch, photo_count=photo_count,
    )
    _view, execute_owner = main._run_full_auto_submission(submit_owner)
    queue_calls = [0]
    original_queue = world["fake"].queue_prompt
    async def count_queue(*args, **kwargs):
        queue_calls[0] += 1
        return await original_queue(*args, **kwargs)
    monkeypatch.setattr(world["fake"], "queue_prompt", count_queue)
    return session_id, run_id, revision, targets, execute_owner, queue_calls, original_queue


def _without_post_commit_dispatch(monkeypatch, create, *args, **kwargs):
    scheduler = main._schedule_full_auto_run
    monkeypatch.setattr(main, "_schedule_full_auto_run", lambda _run_id: None)
    try:
        return create(*args, **kwargs)
    finally:
        monkeypatch.setattr(main, "_schedule_full_auto_run", scheduler)


def _create_run_before_dispatch(world, monkeypatch, *, photo_count: int):
    return _without_post_commit_dispatch(
        monkeypatch, preparation._create_run,
        world["api"], world["seeded"], photo_count=photo_count,
    )


def _approved_run_before_dispatch(world, monkeypatch, *, photo_count: int):
    return _without_post_commit_dispatch(
        monkeypatch, submission._approved_run,
        world["api"], world["seeded"], monkeypatch, photo_count=photo_count,
    )


def _crash_after_persisted_preparation_prefix(
    world: dict,
    monkeypatch,
    session_id: int,
    run_id: str,
    revision: int,
    targets: list[str],
    prefix_count: int,
) -> dict:
    """Crash after real per-take response transactions have committed a prefix."""
    owner = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(owner)
    preparation._install_provider(monkeypatch, session_id, revision, targets)
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
    original_persist = authoring_operations.persist_operation_response
    committed_prefix = []

    def persist_then_crash(*args, **kwargs):
        updated = original_persist(*args, **kwargs)
        committed_prefix[:] = db.q(
            "SELECT take_id FROM prepared_take "
            "WHERE session_id=? AND plan_revision=? AND status='ready' ORDER BY id",
            session_id, revision,
        )
        if len(committed_prefix) == prefix_count:
            raise _ProcessExit()
        return updated

    with monkeypatch.context() as patch:
        patch.setattr(authoring_operations, "persist_operation_response", persist_then_crash)
        with pytest.raises(_ProcessExit):
            asyncio.run(main._run_prepare_takes_operation(
                session_id, child["operation_id"], _full_auto_claim=owner,
            ))
    assert [row["take_id"] for row in committed_prefix] == targets[:prefix_count]
    child_after = db.one(
        "SELECT state, completed_json, remaining_json FROM authoring_operation WHERE operation_id=?",
        child["operation_id"],
    )
    assert child_after["state"] == "active"
    assert json.loads(child_after["completed_json"]) == targets[:prefix_count]
    assert json.loads(child_after["remaining_json"]) == targets[prefix_count:]
    return {"owner": owner, "operation_id": child["operation_id"]}


def _crash_runner(runner: Runner, session_id: int, owner) -> None:
    async def run() -> None:
        assert runner.start(session_id, _full_auto_claim=owner)
        await runner._task
    asyncio.run(run())


def test_startup_recovers_committed_creation_through_existing_run(
    restartable_world, monkeypatch,):
    world = restartable_world
    session_id, run_id, revision, targets = _create_run_before_dispatch(
        world, monkeypatch, photo_count=4,
    )
    plan_before = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id=?", session_id,
    )
    writer = _strict_writer(monkeypatch, session_id, revision, targets)
    api = world["app"].restart()
    world["api"] = api
    result = _wait_for_settlement(world, session_id, run_id)
    assert result["state"] == "succeeded", result
    assert writer["calls"] == writer["expected"]
    assert writer["unexpected"] == []
    assert writer["remaining"] == []
    assert db.one("SELECT COUNT(*) AS n FROM session") == {"n": 1}
    assert db.one("SELECT COUNT(*) AS n FROM full_auto_run") == {"n": 1}
    assert db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id=?", session_id,
    ) == plan_before
    shots = db.q("SELECT id, seed, prompt, status FROM shot WHERE session_id=? ORDER BY id", session_id)
    assert len(shots) == len(targets)
    assert all(row["status"] == "done" and row["seed"] > 0 for row in shots)
    assert world["fake"].attempts == len(targets)


def test_creation_dispatches_after_its_durable_commit(restartable_world, monkeypatch):
    world = restartable_world
    scheduled = []
    writers = {}
    commit_snapshots = []
    schedule = main._schedule_full_auto_run

    def inspect_committed_run(run_id):
        committed = db.one(
            "SELECT * FROM full_auto_run WHERE run_id=?", run_id,
        )
        assert committed is not None
        durable = _independent_sqlite_snapshot(run_id)
        assert durable["run"] == committed
        assert durable["receipt"] is not None
        assert durable["receipt"]["run_id"] == run_id
        assert durable["receipt"]["session_id"] == committed["session_id"]
        commit_snapshots.append(durable)
        plan = db.one(
            "SELECT plan_json FROM session_plan WHERE session_id=?", committed["session_id"],
        )
        targets = [take["take_id"] for take in json.loads(plan["plan_json"])["takes"]]
        writers[run_id] = _strict_writer(
            monkeypatch, committed["session_id"], committed["plan_revision"], targets,
        )
        scheduled.append(run_id)
        schedule(run_id)

    monkeypatch.setattr(main, "_schedule_full_auto_run", inspect_committed_run)
    session_id, run_id, _revision, targets = preparation._create_run(
        world["api"], world["seeded"], photo_count=4,
    )
    assert len(commit_snapshots) == 1
    assert scheduled == [run_id]
    poll_errors = []
    polls = []
    stop_polling = threading.Event()
    poll_barrier = threading.Barrier(3)

    def poll_status() -> None:
        poll_barrier.wait(timeout=5)
        while not stop_polling.is_set():
            try:
                response = world["api"].get(
                    f"/api/sessions/{session_id}/full-auto/{run_id}",
                )
                polls.append(response.status_code)
                if response.status_code != 200:
                    poll_errors.append(response.text)
                    return
                view = response.json()
                if view["state"] in {"succeeded", "failed", "blocked", "cancelled"}:
                    return
            except Exception as exc:  # noqa: BLE001 - preserve concurrent API failure
                poll_errors.append(f"{type(exc).__name__}: {exc}")
                return

    with ThreadPoolExecutor(max_workers=2) as pool:
        readers = [pool.submit(poll_status) for _ in range(2)]
        poll_barrier.wait(timeout=5)
        result = _wait_for_settlement(world, session_id, run_id)
        stop_polling.set()
        for reader in readers:
            reader.result(timeout=10)
    assert result["state"] == "succeeded", result
    assert poll_errors == []
    assert polls and all(code == 200 for code in polls)
    assert writers[run_id]["calls"] == writers[run_id]["expected"]
    assert len(writers[run_id]["calls"]) == 4
    assert len(set(writers[run_id]["calls"])) == 4
    assert writers[run_id]["remaining"] == []
    assert world["fake"].attempts == len(targets)
    assert world["fake"].attempts == 4
    snapshots = db.q(
        "SELECT id, take_id, status, linked_shot_id FROM prepared_take "
        "WHERE session_id=? ORDER BY id", session_id,
    )
    receipts = db.q(
        "SELECT target_index, take_id, prepared_take_id, shot_id, snapshot_digest "
        "FROM full_auto_submission_receipt WHERE run_id=? ORDER BY target_index", run_id,
    )
    assert [row["take_id"] for row in snapshots] == targets
    assert all(row["status"] == "generated" and row["linked_shot_id"] is not None
               for row in snapshots)
    assert len({row["id"] for row in snapshots}) == 4
    assert len({row["linked_shot_id"] for row in snapshots}) == 4
    assert len(receipts) == 4
    assert [row["take_id"] for row in receipts] == targets
    assert len({row["prepared_take_id"] for row in receipts}) == 4
    assert len({row["shot_id"] for row in receipts}) == 4
    assert all(len(row["snapshot_digest"]) == 64 for row in receipts)
    shots = db.q(
        "SELECT id, status FROM shot WHERE session_id=? ORDER BY id", session_id,
    )
    assert len(shots) == 4
    assert {row["status"] for row in shots} == {"done"}
    assert {row["id"] for row in shots} == {row["shot_id"] for row in receipts}


def test_startup_continues_after_persisted_preparation_prefix(
    restartable_world, monkeypatch,):
    world = restartable_world
    session_id, run_id, revision, targets = _create_run_before_dispatch(
        world, monkeypatch, photo_count=4,
    )
    crash = _crash_after_persisted_preparation_prefix(
        world, monkeypatch, session_id, run_id, revision, targets, prefix_count=2,
    )
    prefix_before = db.q(
        "SELECT id, take_id, final_prompt, effective_state, mapping_version, compiler_version, provenance "
        "FROM prepared_take WHERE session_id=? AND plan_revision=? ORDER BY id",
        session_id, revision,
    )
    assert [row["take_id"] for row in prefix_before] == targets[:2]
    assert db.one(
        "SELECT state FROM authoring_operation WHERE operation_id=?", crash["operation_id"],
    ) == {"state": "active"}
    _expire_run(run_id)
    writer = _strict_writer(monkeypatch, session_id, revision, targets[2:])
    api = world["app"].restart()
    world["api"] = api
    result = _wait_for_settlement(world, session_id, run_id)
    assert result["state"] == "succeeded", result
    assert writer["calls"] == writer["expected"]
    assert writer["unexpected"] == []
    assert writer["remaining"] == []
    prefix_after = db.q(
        "SELECT id, take_id, final_prompt, effective_state, mapping_version, compiler_version, provenance "
        "FROM prepared_take WHERE session_id=? AND plan_revision=? ORDER BY id LIMIT 2",
        session_id, revision,
    )
    assert prefix_after == prefix_before
    assert db.q(
        "SELECT status, linked_shot_id FROM prepared_take "
        "WHERE session_id=? AND plan_revision=? ORDER BY id LIMIT 2",
        session_id, revision,
    ) == [{"status": "generated", "linked_shot_id": row["id"]} for row in db.q(
        "SELECT id FROM shot WHERE session_id=? ORDER BY id LIMIT 2", session_id,
    )]
    assert world["fake"].attempts == len(targets)


def test_startup_continues_after_persisted_policy_approval(restartable_world, monkeypatch):
    world = restartable_world
    session_id, run_id, revision, targets, owner = _approved_run_before_dispatch(
        world, monkeypatch, photo_count=4,
    )
    approval_before = db.one(
        "SELECT * FROM full_auto_plan_approval WHERE run_id=?", run_id,
    )
    prepared_before = db.q(
        "SELECT take_id, final_prompt, effective_state, mapping_version, compiler_version, provenance "
        "FROM prepared_take "
        "WHERE session_id=? AND plan_revision=? ORDER BY id", session_id, revision,
    )
    assert approval_before is not None
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", session_id) == {"n": 0}
    _expire_run(run_id)
    api = world["app"].restart()
    world["api"] = api
    result = _wait_for_settlement(world, session_id, run_id)
    assert result["state"] == "succeeded", result
    assert db.one("SELECT * FROM full_auto_plan_approval WHERE run_id=?", run_id) == approval_before
    prepared_after = db.q(
        "SELECT take_id, final_prompt, effective_state, mapping_version, compiler_version, provenance "
        "FROM prepared_take WHERE session_id=? AND plan_revision=? ORDER BY id", session_id, revision,
    )
    assert prepared_after == prepared_before
    shots = db.q(
        "SELECT id, prompt, status FROM shot WHERE session_id=? ORDER BY id", session_id,
    )
    linked_ids = db.q(
        "SELECT linked_shot_id FROM prepared_take WHERE session_id=? AND plan_revision=? ORDER BY id",
        session_id, revision,
    )
    assert [shot["id"] for shot in shots] == [row["linked_shot_id"] for row in linked_ids]
    assert [shot["prompt"] for shot in shots] == [row["final_prompt"] for row in prepared_before]
    assert all(shot["status"] == "done" for shot in shots)
    assert db.one("SELECT COUNT(*) AS n FROM full_auto_submission_receipt WHERE run_id=?", run_id) == {"n": len(targets)}
    assert world["fake"].attempts == len(targets)


@pytest.mark.parametrize("crash_point", ["before_receipt", "after_receipt"])
def test_startup_reuses_a_committed_partial_shot_link(
    restartable_world, monkeypatch, crash_point,):
    world = restartable_world
    session_id, run_id, revision, targets, owner = _approved_run_before_dispatch(
        world, monkeypatch, photo_count=4,
    )
    original_receipt = resource_full_auto.record_submission_receipt
    crashed = False
    def interrupt_after_link(*args, **kwargs):
        nonlocal crashed
        target_index = args[1] if len(args) > 1 else kwargs["target_index"]
        if target_index != 1:
            return original_receipt(*args, **kwargs)
        if not crashed and crash_point == "before_receipt":
            crashed = True
            raise _ProcessExit()
        result = original_receipt(*args, **kwargs)
        if not crashed and crash_point == "after_receipt":
            crashed = True
            raise _ProcessExit()
        return result
    with monkeypatch.context() as patch:
        patch.setattr(resource_full_auto, "record_submission_receipt", interrupt_after_link)
        with pytest.raises(_ProcessExit):
            main._run_full_auto_submission(owner)
    linked_before = db.q(
        "SELECT * FROM prepared_take "
        "WHERE session_id=? AND plan_revision=? AND linked_shot_id IS NOT NULL ORDER BY id",
        session_id, revision,
    )
    assert [row["take_id"] for row in linked_before] == targets[:2]
    receipt_before = db.q(
        "SELECT target_index, take_id, shot_id FROM full_auto_submission_receipt "
        "WHERE run_id=? ORDER BY target_index", run_id,
    )
    if crash_point == "before_receipt":
        assert len(receipt_before) == 1
    else:
        assert len(receipt_before) == 2
    shots_before = db.q(
        "SELECT id, prompt FROM shot WHERE session_id=? ORDER BY id", session_id,
    )
    assert len(shots_before) == 2
    _expire_run(run_id)
    api = world["app"].restart()
    world["api"] = api
    result = _wait_for_settlement(world, session_id, run_id)
    assert result["state"] == "succeeded", result
    linked_after = db.q(
        "SELECT * FROM prepared_take "
        "WHERE session_id=? AND plan_revision=? AND linked_shot_id IS NOT NULL ORDER BY id LIMIT 2",
        session_id, revision,
    )
    assert linked_after == linked_before
    assert db.q(
        "SELECT id, prompt FROM shot WHERE session_id=? ORDER BY id LIMIT 2", session_id,
    ) == shots_before
    assert db.q(
        "SELECT status FROM shot WHERE session_id=? ORDER BY id LIMIT 2", session_id,
    ) == [{"status": "done"}, {"status": "done"}]
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", session_id) == {"n": len(targets)}
    assert db.one("SELECT COUNT(*) AS n FROM full_auto_submission_receipt WHERE run_id=?", run_id) == {"n": len(targets)}
    attempts_after = db.q(
        "SELECT shot_id, seed, attempt_number, completion_state FROM full_auto_execution_attempt "
        "WHERE run_id=? ORDER BY shot_id, attempt_number", run_id,
    )
    assert len(attempts_after) == len(targets)
    assert all(attempt["attempt_number"] == 1 and attempt["completion_state"] == "completed"
               for attempt in attempts_after)
    shot_seeds = {row["id"]: row["seed"] for row in db.q(
        "SELECT id, seed FROM shot WHERE session_id=?", session_id,
    )}
    assert all(shot_seeds[attempt["shot_id"]] == attempt["seed"] for attempt in attempts_after)
    assert world["fake"].attempts == len(targets)


@pytest.mark.parametrize("crash_point", ["before_ack", "after_ack"])
def test_startup_reconciles_a_known_remote_attempt_without_reposting(
    restartable_world, monkeypatch, crash_point,):
    world = restartable_world
    session_id, run_id, _revision, targets, owner, queue_calls, original_queue = _begin_execution(
        world, monkeypatch,
    )
    if crash_point == "before_ack":
        async def accepted_then_lost(*args, **kwargs):
            queue_calls[0] += 1
            await original_queue(*args, **kwargs)
            raise _ProcessExit()
        monkeypatch.setattr(world["fake"], "queue_prompt", accepted_then_lost)
        # Keep counting subsequent physical submissions while allowing them to complete.
        async def count_after_restart(*args, **kwargs):
            queue_calls[0] += 1
            return await original_queue(*args, **kwargs)
    else:
        original_record = resource_full_auto.record_execution_dispatch
        def acknowledge_then_exit(*args, **kwargs):
            result = original_record(*args, **kwargs)
            if kwargs.get("state") == "acknowledged":
                raise _ProcessExit()
            return result
        monkeypatch.setattr(resource_full_auto, "record_execution_dispatch", acknowledge_then_exit)
        async def count_after_restart(*args, **kwargs):
            queue_calls[0] += 1
            return await original_queue(*args, **kwargs)
    with pytest.raises(_ProcessExit):
        _crash_runner(world["runner"], session_id, owner)
    attempt_before = db.one(
        "SELECT attempt_id, shot_id, seed, filename_prefix, dispatch_state, prompt_id, completion_state "
        "FROM full_auto_execution_attempt WHERE run_id=?", run_id,
    )
    assert attempt_before["dispatch_state"] == ("intent" if crash_point == "before_ack" else "acknowledged")
    shot_before = db.one("SELECT id, seed, status FROM shot WHERE session_id=?", session_id)
    assert attempt_before["shot_id"] == shot_before["id"]
    assert attempt_before["seed"] == shot_before["seed"] > 0
    assert attempt_before["filename_prefix"].startswith(f"idevgen/{session_id}/{shot_before['id']}_")
    assert queue_calls[0] == 1
    assert len(targets) == 4
    _expire_run(run_id)
    _expire_runner()
    monkeypatch.setattr(world["fake"], "queue_prompt", count_after_restart)
    if crash_point == "after_ack":
        monkeypatch.setattr(resource_full_auto, "record_execution_dispatch", original_record)
    api = world["app"].restart()
    world["api"] = api
    result = _wait_for_settlement(world, session_id, run_id)
    assert result["state"] == "succeeded", result
    attempt_after = db.one(
        "SELECT attempt_id, shot_id, seed, filename_prefix, dispatch_state, prompt_id, completion_state "
        "FROM full_auto_execution_attempt WHERE run_id=?", run_id,
    )
    assert attempt_after["attempt_id"] == attempt_before["attempt_id"]
    assert attempt_after["shot_id"] == attempt_before["shot_id"]
    assert attempt_after["seed"] == attempt_before["seed"]
    assert attempt_after["filename_prefix"] == attempt_before["filename_prefix"]
    assert attempt_after["completion_state"] == "completed"
    assert queue_calls[0] == len(targets)
    assert world["fake"].attempts == len(targets)
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", session_id) == {"n": len(targets)}
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id=? AND shot_id=?",
        run_id, attempt_before["shot_id"],
    ) == {"n": 1}
    assert db.q(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id=? GROUP BY shot_id ORDER BY shot_id",
        run_id,
    ) == [{"n": 1}] * len(targets)
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id=?", run_id,
    ) == {"n": len(targets)}


def test_startup_reattaches_running_comfy_work_without_a_second_post(
    restartable_world, monkeypatch,):
    world = restartable_world
    session_id, run_id, _revision, targets, owner, queue_calls, original_queue = _begin_execution(
        world, monkeypatch,
    )
    async def accepted_then_exit(*args, **kwargs):
        queue_calls[0] += 1
        await original_queue(*args, **kwargs)
        raise _ProcessExit()
    monkeypatch.setattr(world["fake"], "queue_prompt", accepted_then_exit)
    with pytest.raises(_ProcessExit):
        _crash_runner(world["runner"], session_id, owner)
    attempt_before = db.one(
        "SELECT attempt_id, run_epoch, run_fencing_token, runner_fencing_token, shot_id, seed, "
        "filename_prefix, dispatch_state, prompt_id FROM full_auto_execution_attempt WHERE run_id=?",
        run_id,
    )
    shot_before = db.one("SELECT id, seed, status FROM shot WHERE session_id=?", session_id)
    assert attempt_before["dispatch_state"] == "intent"
    assert shot_before == {
        "id": attempt_before["shot_id"], "seed": attempt_before["seed"], "status": "running",
    }
    assert world["fake"].queue_records
    _expire_run(run_id)
    _expire_runner()
    entered_history = threading.Event()
    release_history = threading.Event()
    original_history = world["fake"].history
    async def count_after_restart(*args, **kwargs):
        queue_calls[0] += 1
        return await original_queue(*args, **kwargs)
    async def hold_recovery_history(prompt_id):
        entered_history.set()
        await asyncio.to_thread(release_history.wait, 10)
        return await original_history(prompt_id)
    monkeypatch.setattr(world["fake"], "history", hold_recovery_history)
    recovery_errors = []
    recover_running_shot = world["runner"]._recover_running_shot
    async def capture_recovery_error(*args, **kwargs):
        try:
            return await recover_running_shot(*args, **kwargs)
        except BaseException as exc:
            recovery_errors.append(f"{type(exc).__name__}: {exc}")
            raise
    monkeypatch.setattr(world["runner"], "_recover_running_shot", capture_recovery_error)
    restart_done = threading.Event()
    restart_errors = []
    def restart_app():
        try:
            world["api"] = world["app"].restart()
        except BaseException as exc:  # preserve startup failures from the test thread
            restart_errors.append(exc)
        finally:
            restart_done.set()
    restart_thread = threading.Thread(target=restart_app, daemon=True)
    restart_thread.start()
    assert entered_history.wait(10), "recovery never reattached to the matching running prompt"
    reattached = db.one(
        "SELECT attempt_id, shot_id, seed, filename_prefix, dispatch_state, prompt_id, completion_state "
        "FROM full_auto_execution_attempt WHERE run_id=?", run_id,
    )
    assert reattached == {
        "attempt_id": attempt_before["attempt_id"],
        "shot_id": attempt_before["shot_id"],
        "seed": attempt_before["seed"],
        "filename_prefix": attempt_before["filename_prefix"],
        "dispatch_state": "acknowledged",
        "prompt_id": "pid-1",
        "completion_state": "pending",
    }
    assert db.one("SELECT id, seed, status FROM shot WHERE session_id=?", session_id) == {
        "id": shot_before["id"], "seed": shot_before["seed"], "status": "running",
    }
    assert queue_calls[0] == 1
    assert world["fake"].attempts == 1
    monkeypatch.setattr(world["fake"], "queue_prompt", count_after_restart)
    release_history.set()
    assert restart_done.wait(15), "startup did not finish after Comfy history became available"
    restart_thread.join(timeout=1)
    assert not restart_errors
    result = _wait_for_settlement(world, session_id, run_id)
    settlement_evidence = {
        "status": result,
        "run": db.one("SELECT * FROM full_auto_run WHERE run_id=?", run_id),
        "attempts": db.q(
            "SELECT * FROM full_auto_execution_attempt WHERE run_id=? ORDER BY attempt_number",
            run_id,
        ),
        "shots": db.q("SELECT * FROM shot WHERE session_id=? ORDER BY id", session_id),
        "receipts": db.q(
            "SELECT * FROM full_auto_submission_receipt WHERE run_id=? ORDER BY target_index",
            run_id,
        ),
        "runner_claim": db.one("SELECT * FROM global_runner_claim WHERE singleton=1"),
        "fake_queue_records": world["fake"].queue_records,
        "fake_prompt_records": world["fake"].prompt_records,
        "recovery_errors": recovery_errors,
    }
    assert result["state"] == "succeeded", json.dumps(settlement_evidence, default=str, indent=2)
    assert queue_calls[0] == len(targets)
    assert world["fake"].attempts == len(targets)
    assert db.one(
        "SELECT attempt_id, shot_id, seed, filename_prefix, dispatch_state, prompt_id, completion_state "
        "FROM full_auto_execution_attempt WHERE run_id=?", run_id,
    ) == {
        "attempt_id": attempt_before["attempt_id"],
        "shot_id": attempt_before["shot_id"],
        "seed": attempt_before["seed"],
        "filename_prefix": attempt_before["filename_prefix"],
        "dispatch_state": "acknowledged",
        "prompt_id": "pid-1",
        "completion_state": "completed",
    }
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id=? AND shot_id=?",
        run_id, attempt_before["shot_id"],
    ) == {"n": 1}
    assert db.q(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id=? GROUP BY shot_id ORDER BY shot_id",
        run_id,
    ) == [{"n": 1}] * len(targets)


@pytest.mark.parametrize(
    "evidence", ["absent", "duplicate_queue", "filename_only", "server_identity_drift", "prior_epoch"],)


def test_startup_keeps_unresolved_dispatch_blocked_without_repost(
    restartable_world, monkeypatch, evidence,):
    world = restartable_world
    session_id, run_id, _revision, targets, owner, queue_calls, original_queue = _begin_execution(
        world, monkeypatch,
    )
    accepted_queue_entries = []
    async def accepted_then_exit(*args, **kwargs):
        queue_calls[0] += 1
        prompt_id = await original_queue(*args, **kwargs)
        accepted_queue_entries.append(copy.deepcopy(world["fake"].queue_records[-1]))
        await world["fake"].history(prompt_id)
        raise _ProcessExit()
    monkeypatch.setattr(world["fake"], "queue_prompt", accepted_then_exit)
    with pytest.raises(_ProcessExit):
        _crash_runner(world["runner"], session_id, owner)
    attempt = db.one("SELECT * FROM full_auto_execution_attempt WHERE run_id=?", run_id)
    if evidence == "absent":
        world["fake"].queue_records.clear()
        world["fake"].prompt_records.clear()
        for path in (world["fake"].output_dir / attempt["filename_prefix"]).parent.glob("*.png"):
            path.unlink()
    elif evidence == "duplicate_queue":
        assert len(accepted_queue_entries) == 1
        world["fake"].queue_records[:] = [
            copy.deepcopy(accepted_queue_entries[0]),
            copy.deepcopy(accepted_queue_entries[0]),
        ]
        world["fake"].prompt_records.clear()
    elif evidence == "filename_only":
        world["fake"].queue_records.clear()
        world["fake"].prompt_records.clear()
        folder = world["fake"].output_dir / Path(attempt["filename_prefix"]).parent
        folder.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2, 2), (32, 64, 96)).save(
            folder / f"{Path(attempt['filename_prefix']).name}_00001_.png",
        )
    elif evidence == "server_identity_drift":
        world["fake"].server_stats["system"]["comfyui_version"] = "different-fake-server"
    else:
        # Model a prior-epoch attempt surviving beside a newer execution epoch.
        # The attempt identity is immutable; recovery must reject this evidence.
        db.run("UPDATE full_auto_run SET epoch=epoch+1 WHERE run_id=?", run_id)
    _expire_run(run_id)
    _expire_runner()
    async def count_after_restart(*args, **kwargs):
        queue_calls[0] += 1
        return await original_queue(*args, **kwargs)
    monkeypatch.setattr(world["fake"], "queue_prompt", count_after_restart)
    api = world["app"].restart()
    world["api"] = api
    result = _wait_for_settlement(world, session_id, run_id)
    assert result["state"] == "blocked", result
    assert result["blocker_code"] == "execution_outcome_unknown"
    assert queue_calls[0] == 1
    assert world["fake"].attempts == 1
    attempt_after = db.one(
        "SELECT attempt_id, run_epoch, seed, filename_prefix, dispatch_state, completion_state, filename "
        "FROM full_auto_execution_attempt WHERE run_id=?", run_id,
    )
    assert attempt_after["attempt_id"] == attempt["attempt_id"]
    assert attempt_after["seed"] == attempt["seed"]
    assert attempt_after["filename_prefix"] == attempt["filename_prefix"]
    assert attempt_after["completion_state"] == "pending"
    assert attempt_after["filename"] == ""
    if evidence == "prior_epoch":
        assert attempt_after["run_epoch"] < db.one(
            "SELECT epoch FROM full_auto_run WHERE run_id=?", run_id,
        )["epoch"]
        assert attempt_after["dispatch_state"] == "intent"
    elif evidence == "server_identity_drift":
        assert attempt_after["dispatch_state"] in {"intent", "outcome_unknown"}
    else:
        assert attempt_after["dispatch_state"] == "outcome_unknown"
    assert db.one("SELECT status FROM shot WHERE session_id=?", session_id) == {"status": "running"}
    assert list((main.SESSIONS_DIR / str(session_id)).glob("*.png")) == []


@pytest.mark.parametrize("failed_read", ["queue_state", "history_entries"])
def test_startup_retries_unavailable_attempt_evidence_across_restart_without_posting(
    restartable_world, monkeypatch, failed_read,
):
    world = restartable_world
    session_id, run_id, _revision, _targets, owner, queue_calls, original_queue = _begin_execution(
        world, monkeypatch,
    )

    async def accepted_then_exit(*args, **kwargs):
        queue_calls[0] += 1
        prompt_id = await original_queue(*args, **kwargs)
        await world["fake"].history(prompt_id)
        raise _ProcessExit()

    monkeypatch.setattr(world["fake"], "queue_prompt", accepted_then_exit)
    with pytest.raises(_ProcessExit):
        _crash_runner(world["runner"], session_id, owner)

    failed_read_calls = []
    async def unavailable_read(*args, **kwargs):
        failed_read_calls.append(failed_read)
        raise OSError(f"synthetic {failed_read} outage")

    monkeypatch.setattr(world["fake"], failed_read, unavailable_read)
    monkeypatch.setattr(resource_full_auto, "COMFY_PROBE_DELAYS", (2, 15, 30))
    _expire_run(run_id)
    _expire_runner()
    world["api"] = world["app"].restart()

    first_retry_deadline = time.monotonic() + 10
    cursor_after_first_retry = None
    while time.monotonic() < first_retry_deadline:
        cursor_after_first_retry = db.one(
            "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
            "FROM full_auto_run WHERE run_id=?", run_id,
        )
        if cursor_after_first_retry["comfy_probe_count"] >= 1 and len(failed_read_calls) >= 2:
            break
        run = db.one("SELECT state, blocker_code FROM full_auto_run WHERE run_id=?", run_id)
        if run["state"] == "blocked":
            break
        time.sleep(0.01)
    assert cursor_after_first_retry is not None
    assert cursor_after_first_retry["comfy_probe_count"] == 1, {
        "failed_read": failed_read,
        "read_calls": len(failed_read_calls),
        "cursor": cursor_after_first_retry,
        "run": db.one("SELECT * FROM full_auto_run WHERE run_id=?", run_id),
    }
    assert cursor_after_first_retry["comfy_probe_scope"] is not None
    assert cursor_after_first_retry["comfy_probe_due_at"] is not None
    assert len(failed_read_calls) == 2

    world["app"].stop()
    _expire_run(run_id)
    _expire_runner()
    resumed_time = datetime.fromisoformat(db.now().replace("Z", "+00:00")) + timedelta(seconds=20)
    monkeypatch.setattr(db, "now", lambda: resumed_time.isoformat(timespec="seconds"))
    monkeypatch.setattr(resource_full_auto, "COMFY_PROBE_DELAYS", (0, 0, 0))
    world["api"] = world["app"].start()
    result = _wait_for_settlement(world, session_id, run_id)

    assert result["state"] == "blocked"
    assert result["blocker_code"] == "comfy_unavailable"
    assert len(failed_read_calls) == 4
    assert queue_calls[0] == 1
    assert world["fake"].attempts == 1
    assert db.one(
        "SELECT comfy_probe_scope, comfy_probe_count, comfy_probe_due_at "
        "FROM full_auto_run WHERE run_id=?", run_id,
    ) == {
        "comfy_probe_scope": cursor_after_first_retry["comfy_probe_scope"],
        "comfy_probe_count": 3,
        "comfy_probe_due_at": None,
    }
    assert db.one(
        "SELECT dispatch_state, completion_state FROM full_auto_execution_attempt WHERE run_id=?",
        run_id,
    ) == {"dispatch_state": "intent", "completion_state": "pending"}
    assert db.one("SELECT status FROM shot WHERE session_id=?", session_id) == {"status": "running"}
    assert not (main.SESSIONS_DIR / str(session_id)).exists()


def test_startup_recovers_a_moved_output_before_attempt_completion(
    restartable_world, monkeypatch,):
    world = restartable_world
    session_id, run_id, _revision, targets, owner, queue_calls, original_queue = _begin_execution(
        world, monkeypatch,
    )
    acquired_runner_claims = []
    original_acquire = resource_full_auto.acquire_runner_claim
    def capture_runner_claim(*args, **kwargs):
        claim = original_acquire(*args, **kwargs)
        if claim is not None:
            acquired_runner_claims.append(claim)
        return claim
    monkeypatch.setattr(resource_full_auto, "acquire_runner_claim", capture_runner_claim)
    original_finish = resource_full_auto.finish_execution_attempt
    def finish_then_exit(*args, **kwargs):
        if kwargs.get("outcome") == "completed":
            raise _ProcessExit()
        return original_finish(*args, **kwargs)
    monkeypatch.setattr(resource_full_auto, "finish_execution_attempt", finish_then_exit)
    with pytest.raises(_ProcessExit):
        _crash_runner(world["runner"], session_id, owner)
    attempt = db.one("SELECT * FROM full_auto_execution_attempt WHERE run_id=?", run_id)
    shot = db.one("SELECT * FROM shot WHERE session_id=?", session_id)
    moved_file = main.SESSIONS_DIR / str(session_id) / attempt["expected_filename"]
    assert attempt["completion_state"] == "output_available"
    assert shot["status"] == "running"
    assert moved_file.is_file()
    digest_before = hashlib.sha256(moved_file.read_bytes()).hexdigest()
    assert queue_calls[0] == 1
    _expire_run(run_id)
    _expire_runner()
    monkeypatch.setattr(resource_full_auto, "finish_execution_attempt", original_finish)
    async def count_after_restart(*args, **kwargs):
        queue_calls[0] += 1
        return await original_queue(*args, **kwargs)
    monkeypatch.setattr(world["fake"], "queue_prompt", count_after_restart)
    api = world["app"].restart()
    world["api"] = api
    result = _wait_for_settlement(world, session_id, run_id)
    assert result["state"] == "succeeded", result
    final_attempt = db.one(
        "SELECT attempt_id, shot_id, seed, filename_prefix, completion_state, filename "
        "FROM full_auto_execution_attempt WHERE run_id=?", run_id,
    )
    assert final_attempt["attempt_id"] == attempt["attempt_id"]
    assert final_attempt["shot_id"] == shot["id"]
    assert final_attempt["seed"] == attempt["seed"]
    assert final_attempt["filename_prefix"] == attempt["filename_prefix"]
    assert final_attempt["completion_state"] == "completed"
    assert final_attempt["filename"] == attempt["expected_filename"]
    assert hashlib.sha256(moved_file.read_bytes()).hexdigest() == digest_before
    assert len(list((main.SESSIONS_DIR / str(session_id)).glob("*.png"))) == len(targets)
    assert queue_calls[0] == len(targets)
    assert world["fake"].attempts == len(targets)
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id=? AND shot_id=?",
        run_id, shot["id"],
    ) == {"n": 1}
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id=?", run_id,
    ) == {"n": len(targets)}
    assert db.q(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id=? GROUP BY shot_id ORDER BY shot_id",
        run_id,
    ) == [{"n": 1}] * len(targets)
    assert acquired_runner_claims
    settled_before_stale_write = db.one(
        "SELECT attempt_id, dispatch_state, completion_state, filename "
        "FROM full_auto_execution_attempt WHERE run_id=?", run_id,
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as stale_write:
        resource_full_auto.finish_execution_attempt(
            attempt["attempt_id"], owner, acquired_runner_claims[0],
            outcome="failed", error="stale prior owner write",
        )
    assert stale_write.value.code in {"runner_owner_stale", "run_owner_stale"}
    assert db.one(
        "SELECT attempt_id, dispatch_state, completion_state, filename "
        "FROM full_auto_execution_attempt WHERE run_id=?", run_id,
    ) == settled_before_stale_write
    assert db.one("SELECT status FROM shot WHERE id=?", shot["id"]) == {"status": "done"}


@pytest.mark.parametrize("gate", ["disabled", "expired"])
def test_startup_settles_dispatched_output_before_gating_remaining_work(
    restartable_world, monkeypatch, gate,):
    world = restartable_world
    clock = [datetime.now(timezone.utc).replace(microsecond=0)]
    if gate == "expired":
        monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    session_id, run_id, revision, targets, owner, queue_calls, original_queue = _begin_execution(
        world, monkeypatch, photo_count=4,
    )
    original_finish = resource_full_auto.finish_execution_attempt
    def finish_then_exit(*args, **kwargs):
        if kwargs.get("outcome") == "completed":
            raise _ProcessExit()
        return original_finish(*args, **kwargs)
    monkeypatch.setattr(resource_full_auto, "finish_execution_attempt", finish_then_exit)
    with pytest.raises(_ProcessExit):
        _crash_runner(world["runner"], session_id, owner)
    attempt_before = db.one(
        "SELECT attempt_id, run_epoch, run_fencing_token, runner_fencing_token, shot_id, seed, "
        "filename_prefix, graph_digest, completion_state, expected_filename "
        "FROM full_auto_execution_attempt WHERE run_id=?", run_id,
    )
    shots_before = db.q(
        "SELECT id, seed, prompt, status FROM shot WHERE session_id=? ORDER BY id", session_id,
    )
    assert [row["status"] for row in shots_before] == ["running", "pending", "pending", "pending"]
    assert attempt_before["completion_state"] == "output_available"
    plan_before = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id=?", session_id,
    )
    prepared_before = db.q(
        "SELECT id, take_id, final_prompt, effective_state, mapping_version, compiler_version, "
        "provenance, linked_shot_id FROM prepared_take WHERE session_id=? AND plan_revision=? ORDER BY id",
        session_id, revision,
    )
    if gate == "disabled":
        monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    else:
        clock[0] += timedelta(days=8)
    _expire_run(run_id)
    _expire_runner()
    monkeypatch.setattr(resource_full_auto, "finish_execution_attempt", original_finish)
    async def count_after_restart(*args, **kwargs):
        queue_calls[0] += 1
        return await original_queue(*args, **kwargs)
    monkeypatch.setattr(world["fake"], "queue_prompt", count_after_restart)
    world["api"] = world["app"].restart()
    result = _wait_for_settlement(world, session_id, run_id)
    assert result["state"] == "blocked", result
    assert result["blocker_code"] in {
        "resource_planning_disabled", "authorization_expired", "provider_changed",
        "workflow_changed", "dependency_changed", "authorization_scope_changed",
    }
    shots_after = db.q(
        "SELECT id, seed, prompt, status FROM shot WHERE session_id=? ORDER BY id", session_id,
    )
    assert [row["status"] for row in shots_after] == ["done", "pending", "pending", "pending"]
    assert shots_after[0]["id"] == attempt_before["shot_id"]
    assert shots_after[0]["seed"] == attempt_before["seed"]
    assert shots_after[0]["prompt"] == shots_before[0]["prompt"]
    assert shots_after[1:] == shots_before[1:]
    attempt_after = db.one(
        "SELECT attempt_id, run_epoch, run_fencing_token, runner_fencing_token, shot_id, seed, "
        "filename_prefix, graph_digest, completion_state, expected_filename, filename "
        "FROM full_auto_execution_attempt WHERE run_id=?", run_id,
    )
    assert attempt_after == {
        **attempt_before,
        "completion_state": "completed",
        "filename": attempt_before["expected_filename"],
    }
    assert db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id=?", session_id,
    ) == plan_before
    assert db.q(
        "SELECT id, take_id, final_prompt, effective_state, mapping_version, compiler_version, "
        "provenance, linked_shot_id FROM prepared_take WHERE session_id=? AND plan_revision=? ORDER BY id",
        session_id, revision,
    ) == prepared_before
    assert db.one("SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id=?", run_id) == {"n": 1}
    assert queue_calls[0] == 1
    assert world["fake"].attempts == 1


@pytest.mark.parametrize("gate", ["disabled", "expired", "provider_drift", "workflow_drift"])
def test_startup_gates_new_work_on_current_authority(restartable_world, monkeypatch, gate):
    world = restartable_world
    clock = [datetime.now(timezone.utc).replace(microsecond=0)]
    if gate == "expired":
        monkeypatch.setattr(db, "now", lambda: clock[0].isoformat(timespec="seconds"))
    session_id, run_id, revision, targets = _create_run_before_dispatch(
        world, monkeypatch, photo_count=4,
    )
    writer = _strict_writer(monkeypatch, session_id, revision, targets)
    plan_before = db.one("SELECT plan_json FROM session_plan WHERE session_id=?", session_id)
    plan_value = json.loads(plan_before["plan_json"])
    bound_workflow = plan_value["authoring"]["workflow_binding"]
    session_workflow = db.one("SELECT workflow_id FROM session WHERE id=?", session_id)
    assert session_workflow["workflow_id"] == bound_workflow["workflow_id"]
    workflow_digests = None
    operation_start_errors = []
    if gate == "disabled":
        monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    elif gate == "expired":
        clock[0] += timedelta(days=8)
    elif gate == "provider_drift":
        monkeypatch.setitem(main.CONFIG, "llm_model", "changed-after-authorization")
    else:
        workflow_id = session_workflow["workflow_id"]
        before_binding = workflow_binding.build_workflow_binding(workflow_id)
        assert before_binding == bound_workflow
        row = db.one("SELECT graph FROM workflow WHERE id=?", workflow_id)
        graph = json.loads(row["graph"])
        before_graph_digest = resource_store.canonical_digest(graph)
        assert before_graph_digest == bound_workflow["graph_digest"]
        graph["6"]["inputs"]["steps"] += 1
        after_graph_digest = resource_store.canonical_digest(graph)
        assert after_graph_digest != before_graph_digest
        db.run(
            "UPDATE workflow SET graph=? WHERE id=?",
            json.dumps(graph), workflow_id,
        )
        current_binding = workflow_binding.build_workflow_binding(workflow_id)
        assert current_binding["workflow_id"] == bound_workflow["workflow_id"]
        assert current_binding["graph_digest"] == after_graph_digest
        workflow_digests = {
            "workflow_id": workflow_id,
            "bound_graph_digest": bound_workflow["graph_digest"],
            "before_graph_digest": before_graph_digest,
            "current_graph_digest": current_binding["graph_digest"],
        }
        original_start_operation = authoring_operations.start_operation
        def capture_operation_start_error(*args, **kwargs):
            try:
                return original_start_operation(*args, **kwargs)
            except BaseException as exc:
                operation_start_errors.append(f"{type(exc).__name__}: {exc}")
                raise
        monkeypatch.setattr(
            authoring_operations, "start_operation", capture_operation_start_error,
        )
    api = world["app"].restart()
    world["api"] = api
    result = _wait_for_settlement(world, session_id, run_id)
    authority_evidence = {
        "status": result,
        "run": db.one("SELECT * FROM full_auto_run WHERE run_id=?", run_id),
        "workflow_digests": workflow_digests,
        "operation_start_errors": operation_start_errors,
        "session_workflow_id": session_workflow["workflow_id"],
        "plan": db.one("SELECT plan_json FROM session_plan WHERE session_id=?", session_id),
        "writer_calls": writer["calls"],
        "writer_unexpected": writer["unexpected"],
        "shots": db.q("SELECT id, seed, status, filename FROM shot WHERE session_id=? ORDER BY id", session_id),
        "physical_attempts": world["fake"].attempts,
    }
    assert result["state"] == "blocked", json.dumps(authority_evidence, default=str, indent=2)
    assert result["blocker_code"] in {
        "resource_planning_disabled", "authorization_expired", "provider_changed",
        "workflow_changed", "dependency_changed", "authorization_scope_changed",
    }, json.dumps({
        "blocker_code": result["blocker_code"],
        "blocker": result["blocker"],
        "state": result["state"],
        "operation_start_errors": operation_start_errors,
        "run_evidence": db.one(
            "SELECT evidence_json FROM full_auto_run WHERE run_id=?", run_id,
        ),
        "authoring_children": db.q(
            "SELECT operation_id, state, failed_item, error, completed_json, remaining_json "
            "FROM authoring_operation WHERE session_id=? ORDER BY created_at",
            session_id,
        ),
        "prepare_child_checkpoints": db.q(
            "SELECT * FROM full_auto_prepare_child WHERE run_id=? ORDER BY created_at",
            run_id,
        ),
        "workflow_digests": workflow_digests,
        "writer_calls": writer["calls"],
        "physical_attempts": world["fake"].attempts,
    }, indent=2)
    assert writer["calls"] == []
    assert writer["unexpected"] == []
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", session_id) == {"n": 0}
    assert db.one("SELECT plan_json FROM session_plan WHERE session_id=?", session_id) == plan_before
    assert world["fake"].attempts == 0


def test_startup_does_not_steal_an_unexpired_coordinator_owner(
    restartable_world, monkeypatch,):
    world = restartable_world
    session_id, run_id, _revision, targets = _create_run_before_dispatch(
        world, monkeypatch, photo_count=4,
    )
    owner = resource_full_auto.claim_run(run_id)
    old_run = db.one(
        "SELECT state, phase, fencing_token, lease_expires_at FROM full_auto_run WHERE run_id=?",
        run_id,
    )
    writer = _strict_writer(monkeypatch, session_id, _revision, targets)
    api = world["app"].restart()
    world["api"] = api
    time.sleep(0.2)
    after = db.one(
        "SELECT state, phase, fencing_token, lease_expires_at FROM full_auto_run WHERE run_id=?",
        run_id,
    )
    assert after == old_run
    assert owner.fencing_token == after["fencing_token"]
    assert writer["calls"] == []
    assert world["fake"].attempts == 0


def test_startup_does_not_steal_an_unexpired_admitted_runner_owner(
    restartable_world, monkeypatch,):
    world = restartable_world
    session_id, run_id, _revision, _targets, submit_owner = _approved_run_before_dispatch(
        world, monkeypatch, photo_count=4,
    )
    _view, execute_owner = main._run_full_auto_submission(submit_owner)
    runner_claim = resource_full_auto.acquire_runner_claim(
        session_id, run_claim=execute_owner,
    )
    assert runner_claim is not None
    admission_before = resource_full_auto.admit_runner_start(session_id, execute_owner)
    runner_before = db.one("SELECT * FROM global_runner_claim WHERE singleton=1")
    run_before = db.one(
        "SELECT state, phase, epoch, revision, fencing_token, lease_expires_at "
        "FROM full_auto_run WHERE run_id=?", run_id,
    )
    admission_row_before = db.one(
        "SELECT * FROM full_auto_runner_admission WHERE run_id=?", run_id,
    )
    assert admission_before["run_id"] == run_id
    assert runner_before["owner_kind"] == "full_auto"
    assert runner_before["run_id"] == run_id
    world["api"] = world["app"].restart()
    time.sleep(0.2)
    assert db.one("SELECT * FROM global_runner_claim WHERE singleton=1") == runner_before
    assert db.one(
        "SELECT state, phase, epoch, revision, fencing_token, lease_expires_at "
        "FROM full_auto_run WHERE run_id=?", run_id,
    ) == run_before
    assert db.one(
        "SELECT * FROM full_auto_runner_admission WHERE run_id=?", run_id,
    ) == admission_row_before
    assert db.one("SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id=?", run_id) == {"n": 0}
    assert db.q("SELECT status FROM shot WHERE session_id=? ORDER BY id", session_id) == [
        {"status": "pending"} for _ in range(4)
    ]
    assert world["fake"].attempts == 0


def test_startup_keeps_waiting_runner_run_and_resumes_after_manual_owner_releases(
    restartable_world, monkeypatch,):
    world = restartable_world
    session_id, run_id, revision, targets = _create_run_before_dispatch(
        world, monkeypatch, photo_count=4,
    )
    writer = _strict_writer(monkeypatch, session_id, revision, targets)
    owner = resource_full_auto.claim_run(run_id)
    view, owner = asyncio.run(
        main._run_full_auto_preparation(owner, _return_claim=True),
    )
    assert (view["state"], view["phase"]) == ("active", "validate")
    view, owner = main._run_full_auto_review_and_approve(owner)
    assert (view["state"], view["phase"]) == ("active", "submit")
    view, execute_owner = main._run_full_auto_submission(owner)
    assert (view["state"], view["phase"]) == ("active", "execute")
    assert execute_owner is not None

    authorization_before = db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id=? ORDER BY authorization_sequence",
        run_id,
    )
    receipt_before = db.one(
        "SELECT * FROM full_auto_run_receipt WHERE run_id=?", run_id,
    )
    submissions_before = db.q(
        "SELECT * FROM full_auto_submission_receipt WHERE run_id=? ORDER BY target_index",
        run_id,
    )
    plan_before = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id=?", session_id,
    )

    manual_session = test_runner._session(
        world["api"], count=1, tag="waiting-runner-manual-owner",
    )
    manual_claim = resource_full_auto.acquire_runner_claim(manual_session)
    assert manual_claim is not None and manual_claim.owner_kind == "manual"
    manual_slot_before = db.one(
        "SELECT * FROM global_runner_claim WHERE singleton=1",
    )
    assert manual_slot_before["owner_kind"] == "manual"
    assert manual_slot_before["session_id"] == manual_session

    assert world["runner"].start(session_id, _full_auto_claim=execute_owner) is False
    waiting = db.one(
        "SELECT state, phase, runner_ready_at, lease_expires_at FROM full_auto_run WHERE run_id=?",
        run_id,
    )
    assert waiting["state"] == "waiting_runner"
    assert waiting["phase"] == "execute"
    assert waiting["runner_ready_at"] is not None
    shots_before_restart = db.q(
        "SELECT id, seed, prompt, status FROM shot WHERE session_id=? ORDER BY id", session_id,
    )
    assert [row["status"] for row in shots_before_restart] == ["pending"] * 4
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_runner_admission WHERE run_id=?", run_id,
    ) == {"n": 0}
    assert world["fake"].attempts == 0

    world["api"] = world["app"].restart()
    time.sleep(main._FULL_AUTO_COORDINATOR_RETRY_SECONDS + 0.1)
    assert db.one("SELECT * FROM global_runner_claim WHERE singleton=1") == manual_slot_before
    still_waiting = db.one(
        "SELECT state, phase, runner_ready_at FROM full_auto_run WHERE run_id=?", run_id,
    )
    assert still_waiting["state"] == "waiting_runner"
    assert still_waiting["phase"] == "execute"
    assert still_waiting["runner_ready_at"] == waiting["runner_ready_at"]
    assert db.q(
        "SELECT id, seed, prompt, status FROM shot WHERE session_id=? ORDER BY id", session_id,
    ) == shots_before_restart
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id=?", run_id,
    ) == {"n": 0}
    assert world["fake"].attempts == 0
    assert writer["calls"] == targets
    assert resource_full_auto.release_runner_claim(manual_claim) is True

    result = _wait_for_settlement(world, session_id, run_id)
    assert result["run_id"] == run_id
    assert result["state"] == "succeeded", result
    assert db.one("SELECT COUNT(*) AS n FROM full_auto_run WHERE session_id=?", session_id) == {"n": 1}
    assert db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id=? ORDER BY authorization_sequence",
        run_id,
    ) == authorization_before
    assert db.one("SELECT * FROM full_auto_run_receipt WHERE run_id=?", run_id) == receipt_before
    assert db.q(
        "SELECT * FROM full_auto_submission_receipt WHERE run_id=? ORDER BY target_index",
        run_id,
    ) == submissions_before
    assert db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id=?", session_id,
    ) == plan_before
    assert writer["calls"] == targets
    shots_after = db.q(
        "SELECT id, seed, prompt, status FROM shot WHERE session_id=? ORDER BY id", session_id,
    )
    assert len(shots_after) == 4
    assert [row["id"] for row in shots_after] == [row["id"] for row in shots_before_restart]
    assert [row["prompt"] for row in shots_after] == [row["prompt"] for row in shots_before_restart]
    assert all(row["status"] == "done" and row["seed"] > 0 for row in shots_after)
    attempts = db.q(
        "SELECT shot_id, seed, filename_prefix FROM full_auto_execution_attempt "
        "WHERE run_id=? ORDER BY shot_id", run_id,
    )
    assert len(attempts) == 4
    assert len({row["shot_id"] for row in attempts}) == 4
    assert len({row["filename_prefix"] for row in attempts}) == 4
    assert {row["id"]: row["seed"] for row in shots_after} == {
        row["shot_id"]: row["seed"] for row in attempts
    }
    assert world["fake"].attempts == 4
    assert db.one("SELECT owner_kind FROM global_runner_claim WHERE singleton=1") == {
        "owner_kind": None,
    }


@pytest.mark.parametrize("planning_enabled", [True, False])
def test_startup_resumes_only_preparation_wait_with_current_authority(
    restartable_world, monkeypatch, planning_enabled,
):
    world = restartable_world
    session_id, run_id, revision, targets = _create_run_before_dispatch(
        world, monkeypatch, photo_count=4,
    )
    writer = _strict_writer(monkeypatch, session_id, revision, targets)
    owner = resource_full_auto.claim_run(run_id)
    waiting = resource_full_auto.advance_run(owner, "waiting_authoring")
    assert (waiting["state"], waiting["phase"]) == ("waiting_authoring", "prepare")
    authorization_before = db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id=? ORDER BY authorization_sequence",
        run_id,
    )
    receipt_before = db.one(
        "SELECT * FROM full_auto_run_receipt WHERE run_id=?", run_id,
    )
    plan_before = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id=?", session_id,
    )
    runner_slot_before = db.one(
        "SELECT * FROM global_runner_claim WHERE singleton=1",
    )
    assert runner_slot_before["owner_kind"] is None
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", planning_enabled)

    world["api"] = world["app"].restart()
    result = _wait_for_settlement(world, session_id, run_id)

    assert result["run_id"] == run_id
    assert db.one("SELECT COUNT(*) AS n FROM full_auto_run WHERE session_id=?", session_id) == {"n": 1}
    assert db.one("SELECT * FROM full_auto_run_receipt WHERE run_id=?", run_id) == receipt_before
    assert db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id=? ORDER BY authorization_sequence",
        run_id,
    ) == authorization_before
    assert db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id=?", session_id,
    ) == plan_before
    runner_slot_after = db.one(
        "SELECT * FROM global_runner_claim WHERE singleton=1",
    )
    for column in (
        "owner_kind", "session_id", "run_id", "owner_nonce",
        "lease_expires_at", "lease_started_at",
    ):
        assert runner_slot_after[column] is None
    if planning_enabled:
        # A successful startup must acquire and release the runner exactly once.
        assert runner_slot_after["fencing_token"] == runner_slot_before["fencing_token"] + 1
    else:
        assert runner_slot_after == runner_slot_before
    if planning_enabled:
        assert result["state"] == "succeeded", result
        assert writer["calls"] == targets
        assert writer["unexpected"] == []
        assert writer["remaining"] == []
        prepared = db.q(
            "SELECT take_id, status FROM prepared_take WHERE session_id=? ORDER BY id", session_id,
        )
        assert [row["take_id"] for row in prepared] == targets
        assert {row["status"] for row in prepared} == {"generated"}
        assert db.q(
            "SELECT status FROM shot WHERE session_id=? ORDER BY id", session_id,
        ) == [{"status": "done"}] * 4
        assert world["fake"].attempts == 4
    else:
        assert result["state"] == "blocked", result
        assert result["blocker_code"] == "resource_planning_disabled"
        assert writer["calls"] == []
        assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", session_id) == {"n": 0}
        assert db.one("SELECT COUNT(*) AS n FROM prepared_take WHERE session_id=?", session_id) == {"n": 0}
        assert world["fake"].attempts == 0


def test_startup_fails_closed_for_waiting_authoring_in_non_prepare_phase(
    restartable_world, monkeypatch,):
    world = restartable_world
    session_id, run_id, revision, targets = _create_run_before_dispatch(
        world, monkeypatch, photo_count=4,
    )
    writer = _strict_writer(monkeypatch, session_id, revision, targets)
    owner = resource_full_auto.claim_run(run_id)
    view, owner = asyncio.run(
        main._run_full_auto_preparation(owner, _return_claim=True),
    )
    assert (view["state"], view["phase"]) == ("active", "validate")
    view, owner = main._run_full_auto_review_and_approve(owner)
    assert (view["state"], view["phase"]) == ("active", "submit")
    view, execute_owner = main._run_full_auto_submission(owner)
    assert (view["state"], view["phase"]) == ("active", "execute")
    waiting = resource_full_auto.advance_run(execute_owner, "waiting_authoring")
    assert (waiting["state"], waiting["phase"]) == ("waiting_authoring", "execute")
    authorization_before = db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id=? ORDER BY authorization_sequence",
        run_id,
    )
    receipt_before = db.one(
        "SELECT * FROM full_auto_run_receipt WHERE run_id=?", run_id,
    )
    submissions_before = db.q(
        "SELECT * FROM full_auto_submission_receipt WHERE run_id=? ORDER BY target_index",
        run_id,
    )
    plan_before = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id=?", session_id,
    )
    shots_before = db.q(
        "SELECT id, seed, prompt, status FROM shot WHERE session_id=? ORDER BY id", session_id,
    )
    assert [row["status"] for row in shots_before] == ["pending"] * 4
    runner_slot_before = db.one(
        "SELECT * FROM global_runner_claim WHERE singleton=1",
    )
    assert runner_slot_before["owner_kind"] is None

    world["api"] = world["app"].restart()
    time.sleep(0.2)

    run_after = db.one(
        "SELECT state, phase FROM full_auto_run WHERE run_id=?", run_id,
    )
    assert run_after == {"state": "waiting_authoring", "phase": "execute"}
    assert writer["calls"] == targets
    assert db.one("SELECT * FROM full_auto_run_receipt WHERE run_id=?", run_id) == receipt_before
    assert db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id=? ORDER BY authorization_sequence",
        run_id,
    ) == authorization_before
    assert db.q(
        "SELECT * FROM full_auto_submission_receipt WHERE run_id=? ORDER BY target_index",
        run_id,
    ) == submissions_before
    assert db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id=?", session_id,
    ) == plan_before
    assert db.q(
        "SELECT id, seed, prompt, status FROM shot WHERE session_id=? ORDER BY id", session_id,
    ) == shots_before
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_runner_admission WHERE run_id=?", run_id,
    ) == {"n": 0}
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id=?", run_id,
    ) == {"n": 0}
    assert db.one("SELECT * FROM global_runner_claim WHERE singleton=1") == runner_slot_before
    assert world["fake"].attempts == 0


@pytest.mark.parametrize("initial_state", ["blocked", "cancelled"])
def test_startup_does_not_restart_blocked_or_cancelled_runs(
    restartable_world, monkeypatch, initial_state,):
    world = restartable_world
    session_id, run_id, revision, targets = _create_run_before_dispatch(
        world, monkeypatch, photo_count=4,
    )
    if initial_state == "blocked":
        owner = resource_full_auto.claim_run(run_id)
        resource_full_auto.advance_run(owner, "blocked", blocker_code="policy_changed")
    else:
        row = db.one("SELECT revision, plan_revision FROM full_auto_run WHERE run_id=?", run_id)
        resource_full_auto.cancel_run(
            run_id, str(uuid.uuid4()), row["revision"], row["plan_revision"],
            work_pending=False,
        )
    before = db.one(
        "SELECT state, phase, revision, epoch, fencing_token, blocker_code, cancel_intent "
        "FROM full_auto_run WHERE run_id=?", run_id,
    )
    assert before["state"] == initial_state
    writer = _strict_writer(monkeypatch, session_id, revision, targets)
    world["api"] = world["app"].restart()
    time.sleep(0.2)
    assert db.one(
        "SELECT state, phase, revision, epoch, fencing_token, blocker_code, cancel_intent "
        "FROM full_auto_run WHERE run_id=?", run_id,
    ) == before
    assert writer["calls"] == []
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", session_id) == {"n": 0}
    assert db.one("SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id=?", run_id) == {"n": 0}
    assert world["fake"].attempts == 0


def test_startup_retains_legacy_running_rows_without_journal_recovery(
    restartable_world,):
    world = restartable_world
    created_at = db.now()
    session_id = db.run(
        "INSERT INTO session (model_id, name, status, workflow_id, created_at) "
        "VALUES (?, 'legacy-running', 'running', ?, ?)",
        world["seeded"]["model_id"], world["seeded"]["workflow_id"], created_at,
    )
    shot_id = db.run(
        "INSERT INTO shot (session_id, prompt, seed, status, created_at) "
        "VALUES (?, 'legacy prompt', 1234, 'running', ?)", session_id, created_at,
    )
    assert db.one("SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE session_id=?", session_id) == {"n": 0}
    world["api"] = world["app"].restart()
    assert db.one("SELECT status FROM session WHERE id=?", session_id) == {"status": "failed"}
    assert db.one("SELECT status, error FROM shot WHERE id=?", shot_id) == {
        "status": "failed", "error": "interrupted when the app closed",
    }
    assert db.one("SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE session_id=?", session_id) == {"n": 0}
