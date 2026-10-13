from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import sqlite3

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


def _execution(client, seeded, monkeypatch, make_runner, *, photo_count=4):
    session_id, run_id, revision, targets, owner = submission._approved_run(
        client, seeded, monkeypatch, photo_count=photo_count,
    )
    _view, execute_owner = main._run_full_auto_submission(owner)
    assert execute_owner is not None
    runner, fake = make_runner()
    return session_id, run_id, revision, targets, execute_owner, runner, fake


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
