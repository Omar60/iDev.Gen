from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import uuid

import pytest

import db
import main
from backend import resource_full_auto, resource_store
import runner as runner_mod
import test_full_auto_submission_independent as submission


class _SimulatedProcessExit(BaseException):
    pass


@pytest.fixture(autouse=True)
def _configured_assistant(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "attempt-journal-independent-test")
    monkeypatch.setitem(main.CONFIG, "llm_key", "attempt-journal-independent-test-key")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)


def _disk_one(sql: str, *params) -> dict | None:
    """Read committed rows on another connection, like the queue server would."""
    path = db.conn().execute("PRAGMA database_list").fetchone()["file"]
    with sqlite3.connect(path, timeout=2) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(sql, params).fetchone()
        return None if row is None else dict(row)


def _execution(
    client, seeded, monkeypatch, make_runner, *, photo_count=4,
    provider_options: dict | None = None,
):
    session_id, run_id, revision, targets, owner = submission._approved_run(
        client, seeded, monkeypatch, photo_count=photo_count,
    )
    _view, execute_owner = main._run_full_auto_submission(owner)
    assert execute_owner is not None
    runner, fake = make_runner(**(provider_options or {}))
    return session_id, run_id, revision, targets, execute_owner, runner, fake


def _resume_retry_body(session_id, run_id, quote, *, retry_failed=False, accept_unknown=False):
    run = resource_full_auto.get_run(run_id)
    plan = db.one(
        "SELECT plan_revision FROM session_plan WHERE session_id = ?", session_id,
    )
    return {
        "request_id": str(uuid.uuid4()),
        "expected_run_revision": run["revision"],
        "expected_plan_revision": plan["plan_revision"],
        "scope_digest": quote["scope_digest"],
        "comparison_issued_at": quote["comparison_issued_at"],
        "retry_failed_shots": retry_failed,
        "accept_unknown_outcome_risk": accept_unknown,
    }


def _resume_quote(client, session_id, run_id):
    response = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _run_retry_attempt(runner, session_id, run_id):
    claim = resource_full_auto.claim_run(run_id)
    assert runner.start(
        session_id, _full_auto_claim=claim, _full_auto_recovery=True,
    )
    await runner._task


def test_attempt_intent_is_committed_before_post_and_ack_before_history(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    original_queue = fake.queue_prompt
    original_history = fake.history
    observed = []

    async def observe_queue(graph, client_id, **kwargs):
        shot = _disk_one(
            "SELECT id, status, seed, prompt_id FROM shot "
            "WHERE session_id = ? AND status = 'running'",
            session_id,
        )
        assert shot is not None
        attempt = _disk_one(
            "SELECT * FROM full_auto_execution_attempt WHERE shot_id = ?",
            shot["id"],
        )
        assert attempt is not None
        assert (shot["status"], shot["prompt_id"]) == ("running", "")
        assert attempt["dispatch_state"] == "intent"
        assert attempt["completion_state"] == "pending"
        assert attempt["seed"] == shot["seed"] == graph["6"]["inputs"]["seed"]
        assert attempt["run_id"] == run_id
        assert attempt["filename_prefix"] == graph["8"]["inputs"]["filename_prefix"]
        assert attempt["filename_prefix"].startswith(
            f"idevgen/{session_id}/{shot['id']}_",
        )
        assert attempt["graph_digest"] == resource_store.canonical_digest(graph)
        observed.append((attempt["shot_id"], attempt["attempt_id"]))
        return await original_queue(graph, client_id, **kwargs)

    async def observe_history(prompt_id):
        attempt = _disk_one(
            "SELECT dispatch_state, prompt_id FROM full_auto_execution_attempt "
            "WHERE prompt_id = ?",
            prompt_id,
        )
        assert attempt == {"dispatch_state": "acknowledged", "prompt_id": prompt_id}
        return await original_history(prompt_id)

    fake.queue_prompt = observe_queue
    fake.history = observe_history

    async def run():
        assert runner.start(session_id, _full_auto_claim=owner)
        await runner._task

    asyncio.run(run())
    attempts = db.q(
        "SELECT * FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY shot_id",
        run_id,
    )
    shots = db.q("SELECT * FROM shot WHERE session_id = ? ORDER BY id", session_id)
    assert [shot["take_id"] for shot in db.q(
        "SELECT take_id FROM full_auto_submission_receipt WHERE run_id = ? ORDER BY target_index",
        run_id,
    )] == targets
    assert len(observed) == len(attempts) == len(shots) == len(targets)
    assert len({attempt["filename_prefix"] for attempt in attempts}) == len(attempts)
    for attempt, shot in zip(attempts, shots, strict=True):
        assert attempt["seed"] == shot["seed"] > 0
        assert attempt["dispatch_state"] == "acknowledged"
        assert attempt["completion_state"] == "completed"
        assert attempt["filename"] == shot["filename"]
        assert (runner.sessions_dir / str(session_id) / shot["filename"]).is_file()


def test_cancelled_inflight_attempt_drains_and_retains_its_late_output(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, revision, targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner, photo_count=4,
    )
    entered_history = asyncio.Event()
    release_history = asyncio.Event()
    original_history = fake.history

    async def hold_history(prompt_id):
        entered_history.set()
        await release_history.wait()
        return await original_history(prompt_id)

    fake.history = hold_history
    monkeypatch.setattr(runner_mod, "CANCELLATION_QUEUE_POLL_SECONDS", 0.01)

    async def cancel_observe_and_drain():
        assert runner.start(session_id, _full_auto_claim=owner)
        await asyncio.wait_for(entered_history.wait(), timeout=5)
        current = resource_full_auto.get_run(run_id)
        cancelled = resource_full_auto.cancel_run(
            run_id, str(uuid.uuid4()), current["revision"], current["plan_revision"],
            work_pending=None, derive_work_pending=True,
            expected_session_id=session_id,
        )
        assert cancelled.view["state"] == "cancel_requested"
        cancellation_claim = resource_full_auto.claim_run(run_id)
        with pytest.raises(resource_full_auto.FullAutoRunError) as transfer:
            resource_full_auto.require_session_mutation_allowed(session_id)
        assert transfer.value.code == "full_auto_session_owned"

        attempt = db.one(
            "SELECT * FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
        )
        shot = db.one("SELECT * FROM shot WHERE id = ?", attempt["shot_id"])
        pending_shot = db.one(
            "SELECT * FROM shot WHERE session_id = ? AND status = 'cancelled' ORDER BY id LIMIT 1",
            session_id,
        )
        assert attempt["dispatch_state"] == "acknowledged"
        assert attempt["completion_state"] == "pending"
        assert shot["status"] == "running"
        assert pending_shot is not None
        assert db.one(
            "SELECT linked_shot_id FROM prepared_take WHERE session_id = ? AND take_id = ?",
            session_id, targets[1],
        )["linked_shot_id"] == pending_shot["id"]

        # Model an owner lost while its exact prompt is still on ComfyUI's
        # running queue. Reconciliation must report running promptly.
        db.run(
            "UPDATE global_runner_claim SET lease_expires_at = ? WHERE singleton = 1",
            "2000-01-01T00:00:00+00:00",
        )
        outcome, running_claim, blocker, attempt_ids, shot_ids = (
            await runner.reconcile_full_auto_cancellation(session_id, cancellation_claim)
        )
        assert (outcome, blocker) == ("running", None)
        assert attempt_ids == (attempt["attempt_id"],)
        assert shot_ids == (str(shot["id"]),)
        run_view = resource_full_auto.reconcile_cancellation(
            running_claim,
            resource_full_auto.CancellationEvidence(
                "running", db.now(), resource_full_auto.unresolved_work_count(session_id, run_id),
                attempt_ids=attempt_ids, shot_ids=shot_ids,
            ),
        )
        assert run_view["state"] == "cancel_requested"
        assert run_view["cancel_intent"] is True
        assert fake.interrupted == 0

        # The prior owner cannot persist its response. The independent
        # cancellation owner later adopts the matching completed output.
        release_history.set()
        await runner._task
        outcome, settled_claim, blocker, attempt_ids, shot_ids = (
            await runner.reconcile_full_auto_cancellation(session_id, running_claim)
        )
        assert (outcome, blocker) == ("settled", None)
        assert attempt_ids == (attempt["attempt_id"],)
        assert shot_ids == (str(shot["id"]),)
        settled = resource_full_auto.reconcile_cancellation(
            settled_claim,
            resource_full_auto.CancellationEvidence(
                "settled", db.now(), 0,
                attempt_ids=attempt_ids, shot_ids=shot_ids,
            ),
        )
        return attempt, shot, pending_shot, settled

    attempt, shot, pending_shot, settled = asyncio.run(cancel_observe_and_drain())
    assert fake.attempts == 1
    assert fake.interrupted == 0
    final_attempt = db.one(
        "SELECT completion_state, filename FROM full_auto_execution_attempt WHERE attempt_id = ?",
        attempt["attempt_id"],
    )
    final_shot = db.one("SELECT status, filename FROM shot WHERE id = ?", shot["id"])
    assert settled["state"] == "cancelled"
    assert final_attempt["completion_state"] == "completed"
    assert final_shot["status"] == "done"
    assert final_shot["filename"] == final_attempt["filename"]
    assert (runner.sessions_dir / str(session_id) / final_shot["filename"]).is_file()
    assert fake.attempts == 1
    assert fake.interrupted == 0
    assert resource_full_auto.require_session_mutation_allowed(session_id) is None

    links_before_resume = db.q(
        "SELECT take_id, shot_id FROM full_auto_submission_receipt WHERE run_id = ? "
        "ORDER BY target_index", run_id,
    )
    current = resource_full_auto.get_run(run_id)
    resumed = resource_full_auto.resume_run(
        run_id, str(uuid.uuid4()), current["revision"], revision,
        eligibility=None, config=lambda: main.CONFIG,
        expected_session_id=session_id,
    )
    assert resumed.view["state"] == "waiting_runner"
    assert db.q(
        "SELECT take_id, shot_id FROM full_auto_submission_receipt WHERE run_id = ? "
        "ORDER BY target_index", run_id,
    ) == links_before_resume
    restored = db.one("SELECT status FROM shot WHERE id = ?", pending_shot["id"])
    assert restored["status"] == "pending"

    async def continue_same_targets():
        assert runner.start(
            session_id, _full_auto_claim=resumed.claim, _full_auto_recovery=True,
        )
        await runner._task

    asyncio.run(continue_same_targets())
    assert fake.attempts == 4
    assert db.one("SELECT status FROM shot WHERE id = ?", pending_shot["id"])["status"] == "done"
    assert db.one("SELECT state FROM full_auto_run WHERE run_id = ?", run_id)["state"] == "succeeded"


@pytest.mark.parametrize("drift", ["model_settings", "lora"])
def test_actual_model_settings_and_lora_drift_during_wait_block_before_post(
    client, seeded, monkeypatch, make_runner, drift,
):
    session_id, run_id, _revision, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def delayed_stats():
        waiting.set()
        await release.wait()
        return {}

    fake.stats = delayed_stats

    async def run_with_drift():
        assert runner.start(session_id, _full_auto_claim=owner)
        try:
            await asyncio.wait_for(waiting.wait(), timeout=5)
            model_id = db.one(
                "SELECT model_id FROM session WHERE id = ?", session_id,
            )["model_id"]
            if drift == "model_settings":
                row = db.one("SELECT settings FROM model WHERE id = ?", model_id)
                settings = json.loads(row["settings"] or "{}")
                settings["steps"] = settings.get("steps", 8) + 1
                db.run(
                    "UPDATE model SET settings = ? WHERE id = ?",
                    json.dumps(settings), model_id,
                )
            else:
                db.run(
                    "UPDATE model SET lora_name = ? WHERE id = ?",
                    "characters/changed.safetensors", model_id,
                )
        finally:
            release.set()
        await runner._task

    asyncio.run(run_with_drift())
    run_row = db.one(
        "SELECT state, blocker_code FROM full_auto_run WHERE run_id = ?", run_id,
    )
    assert fake.attempts == 0
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )["n"] == 0
    assert run_row["state"] == "blocked"
    assert run_row["blocker_code"] == "dependency_changed"


def test_restored_workflow_source_cannot_authorize_a_stale_mapping_read(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    session = db.one("SELECT workflow_id FROM session WHERE id = ?", session_id)
    workflow = db.one(
        "SELECT graph, node_map FROM workflow WHERE id = ?", session["workflow_id"],
    )
    original_graph, original_map = workflow["graph"], workflow["node_map"]
    stale_map = json.loads(original_map)
    stale_map["positive"] = "4.inputs.text"
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def restore_during_wait():
        waiting.set()
        await release.wait()
        db.run(
            "UPDATE workflow SET graph = ?, node_map = ? WHERE id = ?",
            original_graph, original_map, session["workflow_id"],
        )
        return {}

    fake.stats = restore_during_wait

    async def run_with_restoration():
        assert runner.start(session_id, _full_auto_claim=owner)
        # Admission validated the authorized mapping. Change the source before
        # the runner task reads it, then restore it while stats is suspended.
        db.run(
            "UPDATE workflow SET node_map = ? WHERE id = ?",
            json.dumps(stale_map), session["workflow_id"],
        )
        try:
            await asyncio.wait_for(waiting.wait(), timeout=5)
        finally:
            release.set()
        await runner._task

    asyncio.run(run_with_restoration())
    shots = db.q("SELECT id, prompt, negative FROM shot WHERE session_id = ? ORDER BY id", session_id)
    assert len(fake.graphs) in {0, len(shots)}
    assert fake.attempts == len(fake.graphs)
    for shot, graph in zip(shots, fake.graphs, strict=False):
        # The authorized map routes positive to node 3 and negative to node 4.
        # The transient stale map routes positive to node 4, where apply_map's
        # later negative assignment overwrites it. Inspect the graph that really
        # crossed the queue boundary instead of inferring safety from DB state.
        assert graph["3"]["inputs"]["text"] == shot["prompt"]
        assert graph["4"]["inputs"]["text"] == shot["negative"]
    attempts = db.q(
        "SELECT graph_digest FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY shot_id",
        run_id,
    )
    assert len(attempts) in {0, len(fake.graphs)}
    for attempt, graph in zip(attempts, fake.graphs, strict=True):
        assert attempt["graph_digest"] == resource_store.canonical_digest(graph)
    if not fake.graphs:
        assert attempts == []
        assert db.one(
            "SELECT state, blocker_code FROM full_auto_run WHERE run_id = ?", run_id,
        ) == {"state": "blocked", "blocker_code": "workflow_changed"}
        assert shots[0]["id"] and db.one(
            "SELECT status FROM shot WHERE id = ?", shots[0]["id"],
        )["status"] == "failed"
    else:
        assert db.one(
            "SELECT status FROM session WHERE id = ?", session_id,
        )["status"] == "done"


@pytest.mark.parametrize("changed_input", ["session_steps", "model_lora"])
def test_restored_effective_input_cannot_authorize_a_stale_graph_read(
    client, seeded, monkeypatch, make_runner, changed_input,
):
    session_id, run_id, _revision, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    session = db.one(
        "SELECT model_id, settings FROM session WHERE id = ?", session_id,
    )
    model = db.one("SELECT lora_name FROM model WHERE id = ?", session["model_id"])
    workflow_id = db.one(
        "SELECT workflow_id FROM session WHERE id = ?", session_id,
    )["workflow_id"]
    node_map = json.loads(db.one(
        "SELECT node_map FROM workflow WHERE id = ?", workflow_id,
    )["node_map"])
    original_settings = json.loads(session["settings"])
    original_lora = model["lora_name"]
    if changed_input == "session_steps":
        graph_target = node_map["steps"]
        expected_value = original_settings["steps"]
    else:
        graph_target = node_map["lora_name"]
        expected_value = original_lora
    node_id, _group, input_name = graph_target.split(".", 2)

    waiting = asyncio.Event()
    release = asyncio.Event()

    async def restore_during_wait():
        waiting.set()
        await release.wait()
        if changed_input == "session_steps":
            db.run(
                "UPDATE session SET settings = ? WHERE id = ?",
                json.dumps(original_settings), session_id,
            )
        else:
            db.run(
                "UPDATE model SET lora_name = ? WHERE id = ?",
                original_lora, session["model_id"],
            )
        return {}

    fake.stats = restore_during_wait

    async def run_with_transient_input():
        assert runner.start(session_id, _full_auto_claim=owner)
        if changed_input == "session_steps":
            altered = dict(original_settings)
            altered["steps"] = expected_value + 7
            db.run(
                "UPDATE session SET settings = ? WHERE id = ?",
                json.dumps(altered), session_id,
            )
        else:
            db.run(
                "UPDATE model SET lora_name = ? WHERE id = ?",
                "characters/transient.safetensors", session["model_id"],
            )
        await asyncio.wait_for(waiting.wait(), timeout=5)
        release.set()
        await runner._task

    asyncio.run(run_with_transient_input())
    assert len(fake.graphs) in {0, len(_targets)}
    assert fake.attempts == len(fake.graphs)
    for graph in fake.graphs:
        assert graph[node_id]["inputs"][input_name] == expected_value
    attempts = db.q(
        "SELECT graph_digest FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY shot_id",
        run_id,
    )
    assert len(attempts) in {0, len(fake.graphs)}
    for attempt, graph in zip(attempts, fake.graphs, strict=True):
        assert attempt["graph_digest"] == resource_store.canonical_digest(graph)
    if not fake.graphs:
        assert attempts == []
        assert db.one(
            "SELECT state, blocker_code FROM full_auto_run WHERE run_id = ?", run_id,
        ) == {"state": "blocked", "blocker_code": "dependency_changed"}
        assert db.one(
            "SELECT status FROM shot WHERE session_id = ? ORDER BY id LIMIT 1",
            session_id,
        )["status"] == "failed"
    else:
        assert db.one(
            "SELECT status FROM session WHERE id = ?", session_id,
        )["status"] == "done"


def test_runner_claim_is_rechecked_after_dependency_validation(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    original_validate = resource_full_auto._validate_execution_dependencies
    original_now = db.now
    future = (
        datetime.fromisoformat(original_now().replace("Z", "+00:00")) + timedelta(days=1)
    ).astimezone(timezone.utc).isoformat(timespec="seconds")

    def expire_after_validation(run):
        original_validate(run)
        monkeypatch.setattr(db, "now", lambda: future)

    monkeypatch.setattr(
        resource_full_auto, "_validate_execution_dependencies", expire_after_validation,
    )

    async def run():
        assert runner.start(session_id, _full_auto_claim=owner)
        await runner._task

    asyncio.run(run())
    assert fake.attempts == 0
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )["n"] == 0


def test_expired_owner_cannot_record_output_evidence_but_ack_receipt_is_retained(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    entered_history = asyncio.Event()
    release_history = asyncio.Event()
    captured = []
    original_dispatch = resource_full_auto.record_execution_dispatch
    original_history = fake.history

    def capture_dispatch(attempt_id, claim, runner_claim, **kwargs):
        result = original_dispatch(attempt_id, claim, runner_claim, **kwargs)
        if kwargs.get("state") == "acknowledged":
            captured.append((attempt_id, claim, runner_claim))
        return result

    async def delayed_history(prompt_id):
        entered_history.set()
        await release_history.wait()
        return await original_history(prompt_id)

    monkeypatch.setattr(resource_full_auto, "record_execution_dispatch", capture_dispatch)
    fake.history = delayed_history

    async def race_stale_completion():
        assert runner.start(session_id, _full_auto_claim=owner)
        await asyncio.wait_for(entered_history.wait(), timeout=5)
        assert captured
        attempt_id, claim, runner_claim = captured[0]
        stale = "2000-01-01T00:00:00+00:00"
        db.run(
            "UPDATE full_auto_run SET lease_started_at = ?, lease_expires_at = ? WHERE run_id = ?",
            stale, stale, run_id,
        )
        db.run(
            "UPDATE global_runner_claim SET lease_started_at = ?, lease_expires_at = ? "
            "WHERE singleton = 1",
            stale, stale,
        )
        attempt = db.one(
            "SELECT shot_id FROM full_auto_execution_attempt WHERE attempt_id = ?", attempt_id,
        )
        shot = db.one("SELECT * FROM shot WHERE id = ?", attempt["shot_id"])
        expected = runner_mod._output_filename(
            shot, {"filename": "result.png"},
        )
        error = None
        try:
            resource_full_auto.record_execution_output(
                attempt_id, claim, runner_claim,
                image={"filename": "result.png", "subfolder": "remote", "type": "output"},
                expected_filename=expected,
            )
        except resource_full_auto.FullAutoRunError as exc:
            error = exc
        finally:
            release_history.set()
        await runner._task
        assert error is not None
        assert error.code in {"run_owner_stale", "runner_owner_stale"}

    asyncio.run(race_stale_completion())
    attempt = db.one(
        "SELECT dispatch_state, completion_state, output_evidence_json "
        "FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )
    assert attempt == {
        "dispatch_state": "acknowledged",
        "completion_state": "pending",
        "output_evidence_json": None,
    }


def test_moved_file_survives_completion_write_crash_without_marking_or_queueing_next(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    original_finish = resource_full_auto.finish_execution_attempt
    original_db_run = db.run
    captured = []

    def fail_shot_completion(sql, *params):
        if sql.lstrip().startswith("UPDATE shot SET status='done'"):
            raise OSError("injected shot completion write failure")
        return original_db_run(sql, *params)

    def process_exit_before_finish(attempt_id, claim, runner_claim, **kwargs):
        assert kwargs["outcome"] == "completed"
        attempt = db.one(
            "SELECT * FROM full_auto_execution_attempt WHERE attempt_id = ?", attempt_id,
        )
        shot = db.one("SELECT * FROM shot WHERE id = ?", attempt["shot_id"])
        filename = attempt["expected_filename"]
        assert attempt["dispatch_state"] == "acknowledged"
        assert attempt["completion_state"] == "output_available"
        assert attempt["prompt_id"] == shot["prompt_id"] != ""
        assert filename == kwargs["filename"]
        assert (runner.sessions_dir / str(session_id) / filename).is_file()

        # Fail the second half of the normal atomic write. The durable output
        # receipt must remain available while the shot stays unresolved.
        monkeypatch.setattr(db, "run", fail_shot_completion)
        try:
            with pytest.raises(OSError, match="injected shot completion write failure"):
                original_finish(
                    attempt_id, claim, runner_claim, **kwargs,
                )
        finally:
            monkeypatch.setattr(db, "run", original_db_run)

        after = db.one(
            "SELECT completion_state, filename, output_evidence_json, expected_filename "
            "FROM full_auto_execution_attempt WHERE attempt_id = ?", attempt_id,
        )
        assert after == {
            "completion_state": "output_available",
            "filename": "",
            "output_evidence_json": attempt["output_evidence_json"],
            "expected_filename": filename,
        }
        assert db.one(
            "SELECT status, filename FROM shot WHERE id = ?", shot["id"],
        ) == {"status": "running", "filename": ""}
        captured.append(attempt_id)
        raise _SimulatedProcessExit()

    monkeypatch.setattr(
        resource_full_auto, "finish_execution_attempt", process_exit_before_finish,
    )

    async def run_until_process_exit():
        assert runner.start(session_id, _full_auto_claim=owner)
        with pytest.raises(_SimulatedProcessExit):
            await runner._task

    asyncio.run(run_until_process_exit())

    shots = db.q(
        "SELECT status, prompt_id, filename FROM shot WHERE session_id = ? ORDER BY id",
        session_id,
    )
    attempts = db.q(
        "SELECT attempt_id, dispatch_state, completion_state FROM "
        "full_auto_execution_attempt WHERE run_id = ? ORDER BY shot_id",
        run_id,
    )
    assert len(captured) == fake.attempts == len(fake.graphs) == 1
    assert len(targets) == len(shots) > 1
    assert shots[0]["status"] == "running" and shots[0]["prompt_id"] == "pid-1"
    assert shots[0]["filename"] == ""
    assert all(row["status"] == "pending" and row["prompt_id"] == "" for row in shots[1:])
    assert attempts == [{
        "attempt_id": captured[0],
        "dispatch_state": "acknowledged",
        "completion_state": "output_available",
    }]


def test_execution_capability_migration_is_additive_and_rebuilds_identity_trigger(tmp_path):
    connection = sqlite3.connect(tmp_path / "legacy.sqlite")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(db.SCHEMA)
        connection.execute(
            "DROP TRIGGER full_auto_execution_attempt_identity_immutable",
        )
        for column in (
            "client_prompt_id", "client_id", "capability_evidence_json",
        ):
            connection.execute(
                f"ALTER TABLE full_auto_execution_attempt DROP COLUMN {column}",
            )
        connection.execute(
            """CREATE TRIGGER full_auto_execution_attempt_identity_immutable
               BEFORE UPDATE OF attempt_id ON full_auto_execution_attempt
               BEGIN
                   SELECT RAISE(ABORT, 'legacy identity trigger');
               END""",
        )

        db._migrate(connection, tmp_path)

        columns = {
            row["name"]: row for row in connection.execute(
                "PRAGMA table_info(full_auto_execution_attempt)",
            )
        }
        assert {"client_prompt_id", "client_id", "capability_evidence_json"} <= set(columns)
        assert columns["client_id"]["dflt_value"] == "''"
        assert columns["capability_evidence_json"]["dflt_value"] == "'{}'"
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='full_auto_execution_attempt_identity_immutable'",
        ).fetchone()["sql"]
        assert "client_prompt_id" in trigger
        assert "client_id" in trigger
        assert "capability_evidence_json" in trigger
    finally:
        connection.close()


def test_known_failed_retry_grants_one_cumulative_attempt_and_preserves_mixed_results(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, revision, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
        provider_options={"fail_first": 1},
    )

    async def initial_run():
        assert runner.start(session_id, _full_auto_claim=owner)
        await runner._task

    asyncio.run(initial_run())
    assert db.one(
        "SELECT state FROM full_auto_run WHERE run_id = ?", run_id,
    )["state"] == "completed_with_errors"
    attempts_before = db.q(
        "SELECT * FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY shot_id",
        run_id,
    )
    failed = [item for item in attempts_before if item["completion_state"] == "failed"]
    assert len(attempts_before) == 4
    assert len(failed) == 1
    failed_shot_id = failed[0]["shot_id"]

    status = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
    assert status["retry_control"]["failed_shot_ids"] == [failed_shot_id]
    legacy_retry = client.post(f"/api/sessions/{session_id}/retry")
    assert legacy_retry.status_code == 409
    assert legacy_retry.json()["detail"]["code"] == "full_auto_retry_requires_resume"
    assert db.one("SELECT status FROM shot WHERE id = ?", failed_shot_id)["status"] == "failed"
    quote = _resume_quote(client, session_id, run_id)
    image_quote = quote["image_retry_allowance"]
    assert image_quote["initial_image_attempts_total"] == 4
    assert image_quote["previous_retry_grants"] == 0
    assert image_quote["image_attempts_consumed"] == 4
    assert image_quote["authorized_image_attempts_total"] == 4
    assert image_quote["authorized_image_attempts_remaining"] == 0
    assert image_quote["retry_failed_shot_ids"] == [failed_shot_id]
    assert image_quote["additional_if_retry_failed"] == 1
    assert image_quote["authorized_total_after_retry_failed"] == 5

    missing_quote = _resume_retry_body(
        session_id, run_id, {"scope_digest": None, "comparison_issued_at": None},
        retry_failed=True,
    )
    missing_quote.pop("scope_digest")
    missing_quote.pop("comparison_issued_at")
    refused = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=missing_quote,
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "retry_comparison_required"
    assert db.q(
        "SELECT * FROM full_auto_attempt_retry_authorization WHERE run_id = ?", run_id,
    ) == []

    body = _resume_retry_body(session_id, run_id, quote, retry_failed=True)
    retry = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert retry.status_code == 202, retry.text
    assert retry.json()["state"] == "waiting_runner"
    grant = db.one(
        "SELECT * FROM full_auto_attempt_retry_authorization WHERE run_id = ?", run_id,
    )
    assert grant["attempt_id"] == failed[0]["attempt_id"]
    assert grant["request_id"] == body["request_id"]
    assert grant["run_epoch"] == retry.json()["epoch"]
    assert db.one(
        "SELECT status FROM shot WHERE id = ?", failed_shot_id,
    )["status"] == "pending"
    after_grant = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
    assert after_grant["allowances"]["image_attempts_initial_total"] == 4
    assert after_grant["allowances"]["image_retry_grants"] == 1
    assert after_grant["allowances"]["image_attempts_consumed"] == 4
    assert after_grant["allowances"]["image_attempts_total"] == 5
    assert after_grant["allowances"]["image_attempts_remaining_lower_bound"] == 1

    fake.fail_queue = True
    replay = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == retry.json()
    assert len(db.q(
        "SELECT * FROM full_auto_attempt_retry_authorization WHERE run_id = ?", run_id,
    )) == 1

    asyncio.run(_run_retry_attempt(runner, session_id, run_id))
    attempts_after = db.q(
        "SELECT * FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY shot_id, attempt_number",
        run_id,
    )
    retry_attempts = [item for item in attempts_after if item["shot_id"] == failed_shot_id]
    assert len(retry_attempts) == 2
    assert retry_attempts[1]["attempt_number"] == retry_attempts[0]["attempt_number"] + 1
    assert retry_attempts[1]["attempt_id"] != retry_attempts[0]["attempt_id"]
    assert retry_attempts[1]["filename_prefix"] != retry_attempts[0]["filename_prefix"]
    assert retry_attempts[1]["completion_state"] == "failed"
    final = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
    assert final["state"] == "completed_with_errors"
    assert final["progress"]["completed_count"] == 3
    assert final["progress"]["failed_count"] == 1
    assert final["allowances"]["image_attempts_consumed"] == 5
    assert final["allowances"]["image_attempts_total"] == 5


def test_failed_and_cancelled_shots_retry_only_with_one_grant_each(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
        photo_count=4, provider_options={"fail_queue": True},
    )

    async def initial_run():
        assert runner.start(session_id, _full_auto_claim=owner)
        await runner._task

    asyncio.run(initial_run())
    assert db.one(
        "SELECT state FROM full_auto_run WHERE run_id = ?", run_id,
    )["state"] == "failed"
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt "
        "WHERE run_id = ? AND completion_state = 'failed'", run_id,
    )["n"] == 4
    cancelled_shot_id = db.one(
        "SELECT shot_id FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY shot_id LIMIT 1",
        run_id,
    )["shot_id"]
    db.run("UPDATE shot SET status = 'cancelled' WHERE id = ?", cancelled_shot_id)

    quote = _resume_quote(client, session_id, run_id)
    image_quote = quote["image_retry_allowance"]
    assert db.one("SELECT status FROM shot WHERE id = ?", cancelled_shot_id)["status"] == "cancelled"
    assert image_quote["retry_failed_shot_ids"] == [
        item["shot_id"] for item in db.q(
            "SELECT shot_id FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY shot_id",
            run_id,
        )
    ]
    assert image_quote["additional_if_retry_failed"] == 4
    assert image_quote["authorized_total_after_retry_failed"] == 8
    body = _resume_retry_body(session_id, run_id, quote, retry_failed=True)
    response = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert response.status_code == 202, response.text
    assert len(db.q(
        "SELECT * FROM full_auto_attempt_retry_authorization WHERE run_id = ?", run_id,
    )) == 4
    assert client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()[
        "allowances"]["image_attempts_total"] == 8

    asyncio.run(_run_retry_attempt(runner, session_id, run_id))
    final = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
    assert final["state"] == "failed"
    assert final["progress"]["completed_count"] == 0
    assert final["progress"]["failed_count"] == 4
    assert final["allowances"]["image_attempts_consumed"] == 8
    assert final["allowances"]["image_attempts_total"] == 8


def test_unknown_outcome_retry_requires_current_queue_absence_and_keeps_risk_guard(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    monkeypatch.setattr(main, "runner", runner)
    original_queue_prompt = fake.queue_prompt
    original_capabilities = fake.execution_capabilities
    accepted = {}

    async def accepted_but_lost(graph, client_id, **kwargs):
        prompt_id = await original_queue_prompt(graph, client_id, **kwargs)
        accepted["entry"] = fake.queue_records[-1]
        fake.queue_records.clear()
        raise TimeoutError("simulated lost queue response")

    fake.queue_prompt = accepted_but_lost

    async def initial_run():
        assert runner.start(session_id, _full_auto_claim=owner)
        await runner._task

    asyncio.run(initial_run())
    fake.queue_prompt = original_queue_prompt
    run = resource_full_auto.get_run(run_id)
    assert (run["state"], run["blocker_code"]) == ("blocked", "execution_outcome_unknown")
    old_attempt = db.one(
        "SELECT * FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY attempt_number LIMIT 1",
        run_id,
    )
    assert old_attempt["dispatch_state"] == "outcome_unknown"
    assert old_attempt["completion_state"] == "pending"
    old_attempt_id = old_attempt["attempt_id"]
    old_shot_id = old_attempt["shot_id"]

    status = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
    assert status["retry_control"]["unknown_outcome_shot_ids"] == [old_shot_id]
    assert status["retry_control"]["unknown_attempt_ids"] == [old_attempt_id]
    assert "duplicate work" in status["retry_control"]["unknown_outcome_warning"].lower()
    quote = _resume_quote(client, session_id, run_id)
    allowance = quote["image_retry_allowance"]
    assert allowance["unknown_outcome_shot_ids"] == [old_shot_id]
    assert allowance["unknown_attempt_ids"] == [old_attempt_id]
    assert allowance["additional_if_accept_unknown_outcome_risk"] == 1
    assert allowance["authorized_total_after_unknown_risk"] == 5

    body = _resume_retry_body(session_id, run_id, quote, accept_unknown=True)
    original_queue_state = fake.queue_state
    fake.queue_records.append(accepted["entry"])
    still_active = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert still_active.status_code == 409, still_active.text
    assert still_active.json()["detail"]["code"] == "unknown_outcome_still_active"
    assert db.q(
        "SELECT * FROM full_auto_attempt_risk_acceptance WHERE run_id = ?", run_id,
    ) == []

    db.run(
        "UPDATE full_auto_run SET cancel_intent = 1, cancel_intent_at = ? WHERE run_id = ?",
        db.now(), run_id,
    )

    async def forbidden_queue_check():
        raise AssertionError("pending cancellation must not probe or retry remote work")

    fake.queue_state = forbidden_queue_check
    cancelled_retry = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert cancelled_retry.status_code == 409, cancelled_retry.text
    assert cancelled_retry.json()["detail"]["code"] == "cancel_reconciliation_required"
    assert db.q(
        "SELECT * FROM full_auto_attempt_risk_acceptance WHERE run_id = ?", run_id,
    ) == []
    db.run(
        "UPDATE full_auto_run SET cancel_intent = 0, cancel_intent_at = NULL WHERE run_id = ?",
        run_id,
    )
    db.run(
        "UPDATE global_runner_claim SET lease_expires_at = ? WHERE singleton = 1",
        "2000-01-01T00:00:00+00:00",
    )
    fake.queue_state = original_queue_state
    fake.queue_records.clear()

    quote = _resume_quote(client, session_id, run_id)
    body = _resume_retry_body(session_id, run_id, quote, accept_unknown=True)
    accepted_retry = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert accepted_retry.status_code == 202, accepted_retry.text
    receipt = db.one(
        "SELECT * FROM full_auto_attempt_risk_acceptance WHERE attempt_id = ?",
        old_attempt_id,
    )
    assert receipt is not None
    assert receipt["request_id"] == body["request_id"]
    assert receipt["queue_snapshot_digest"] == resource_store.canonical_digest({
        "queue_running": [], "queue_pending": [],
    })
    assert db.one(
        "SELECT dispatch_state, completion_state FROM full_auto_execution_attempt "
        "WHERE attempt_id = ?", old_attempt_id,
    ) == {"dispatch_state": "outcome_unknown", "completion_state": "pending"}
    assert db.one("SELECT status FROM shot WHERE id = ?", old_shot_id)["status"] == "pending"
    before_replay_grants = db.one(
        "SELECT COUNT(*) AS n FROM full_auto_attempt_risk_acceptance WHERE run_id = ?",
        run_id,
    )["n"]
    after_grant = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
    assert after_grant["allowances"]["image_attempts_initial_total"] == 4
    assert after_grant["allowances"]["image_retry_grants"] == 1
    assert after_grant["allowances"]["image_attempts_consumed"] == 1
    assert after_grant["allowances"]["image_attempts_total"] == 5

    async def no_replay_probe():
        raise AssertionError("historical retry replay must not probe ComfyUI")

    fake.execution_capabilities = no_replay_probe
    replay = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == accepted_retry.json()
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_attempt_risk_acceptance WHERE run_id = ?",
        run_id,
    )["n"] == before_replay_grants == 1
    fake.execution_capabilities = original_capabilities

    asyncio.run(_run_retry_attempt(runner, session_id, run_id))
    attempts = db.q(
        "SELECT * FROM full_auto_execution_attempt WHERE run_id = ? AND shot_id = ? "
        "ORDER BY attempt_number",
        run_id, old_shot_id,
    )
    assert len(attempts) == 2
    assert attempts[0]["attempt_id"] == old_attempt_id
    assert attempts[1]["attempt_id"] != old_attempt_id
    assert attempts[1]["filename_prefix"] != attempts[0]["filename_prefix"]
    assert attempts[0]["completion_state"] == "pending"
    final = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
    assert final["state"] == "succeeded", final
    assert final["progress"]["completed_count"] == 4
    assert resource_full_auto._run_deletion_protected(
        resource_full_auto._run_row(run_id),
    ) is True
    with pytest.raises(sqlite3.IntegrityError, match="full_auto_in_use"):
        db.run("DELETE FROM full_auto_run WHERE run_id = ?", run_id)
    with pytest.raises(sqlite3.IntegrityError, match="full_auto_in_use"):
        db.run("DELETE FROM session WHERE id = ?", session_id)

    # Test-only cleanup removes the synthetic receipt after checking its guard.
    delete_guard = db.one(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
        "AND name = 'full_auto_attempt_risk_acceptance_no_delete'",
    )
    db.run("DROP TRIGGER full_auto_attempt_risk_acceptance_no_delete")
    db.run("DELETE FROM full_auto_attempt_risk_acceptance WHERE run_id = ?", run_id)
    if delete_guard is not None:
        db.run(delete_guard["sql"])
