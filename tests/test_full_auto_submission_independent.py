"""Independent acceptance checks for Full Auto submission recovery boundaries."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import db
import main
from backend import resource_full_auto, resource_store, session_plan
import runner as runner_mod
from runner import Runner
import test_full_auto_preparation_coordinator as preparation


@pytest.fixture(autouse=True)
def _configured_assistant(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "submission-independent-test")
    monkeypatch.setitem(main.CONFIG, "llm_key", "submission-independent-key")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)


def _reopen_database() -> None:
    path = db.conn().execute("PRAGMA database_list").fetchone()["file"]
    db.conn().commit()
    db.conn().close()
    db.connect(Path(path))


def _recover_owner(run_id: str, phase: str) -> resource_full_auto.RunClaim:
    db.run(
        "UPDATE full_auto_run SET lease_expires_at = ?, lease_started_at = ? WHERE run_id = ?",
        "2000-01-01T00:00:00+00:00", "1999-12-31T23:59:00+00:00", run_id,
    )
    reconciliation = resource_full_auto.claim_run(run_id)
    view, owner = resource_full_auto.reconcile_expired_lease(
        reconciliation,
        resource_full_auto.LeaseReconciliationEvidence("safe_to_continue", 0),
    )
    assert view["phase"] == phase
    assert owner is not None
    return owner


def _approved_run(
    client, seeded, monkeypatch, *, photo_count: int = 4,
    canvas: tuple[int, int] | None = None,
):
    if canvas is None:
        session_id, run_id, revision, targets = preparation._create_run(
            client, seeded, photo_count=photo_count,
        )
    else:
        preparation._install_t2i_workflow(client, seeded)
        _saved, _published, base = preparation._full_auto_fixture(
            seeded, photo_count=photo_count,
        )
        base["width"], base["height"] = canvas
        consent = preparation._preflight(client, base)
        created, _payload = preparation._create_from_preflight(client, base, consent)
        assert created.status_code == 201, created.text
        result = created.json()
        session_id, run_id = result["session_id"], result["full_auto_run_id"]
        row = db.one(
            "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
            session_id,
        )
        revision = int(row["plan_revision"])
        targets = [take["take_id"] for take in json.loads(row["plan_json"])["takes"]]
        assert len(targets) == photo_count
    preparation._install_provider(monkeypatch, session_id, revision, targets)
    prepared = asyncio.run(
        main._run_full_auto_preparation(resource_full_auto.claim_run(run_id)),
    )
    assert (prepared["state"], prepared["phase"]) == ("active", "validate")
    validation_owner = _recover_owner(run_id, "validate")
    reviewed, owner = main._run_full_auto_review_and_approve(validation_owner)
    assert (reviewed["state"], reviewed["phase"]) == ("active", "submit")
    assert owner is not None
    return session_id, run_id, revision, targets, owner


def _linked_rows(session_id: int, revision: int) -> list[dict]:
    return db.q(
        "SELECT p.take_id, p.final_prompt, p.linked_shot_id, s.prompt, s.id AS shot_id "
        "FROM prepared_take p LEFT JOIN shot s ON s.id = p.linked_shot_id "
        "WHERE p.session_id = ? AND p.plan_revision = ? ORDER BY p.rowid",
        session_id, revision,
    )


async def _start_and_wait(runner: Runner, session_id: int, claim=None) -> None:
    if claim is None:
        runner.start(session_id)
    else:
        runner.start(session_id, _full_auto_claim=claim)
    if runner._task is not None:
        await runner._task


def test_full_auto_runner_heartbeat_keeps_long_comfy_io_owned(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner = _approved_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    _view, execute_owner = main._run_full_auto_submission(owner)
    runner, fake = make_runner()
    entered = asyncio.Event()
    release = asyncio.Event()
    original_history = fake.history
    original_now = db.now
    clock = [datetime.fromisoformat(original_now().replace("Z", "+00:00"))]
    # Each renewal validates both leases; keep synthetic clock steps below expiry.
    step = [timedelta(seconds=10)]

    async def slow_history(prompt_id):
        entered.set()
        await release.wait()
        return await original_history(prompt_id)

    def fake_now():
        value = clock[0]
        clock[0] += step[0]
        return value.isoformat(timespec="seconds")

    monkeypatch.setattr(fake, "history", slow_history)
    monkeypatch.setattr(
        resource_full_auto, "RUNNER_HEARTBEAT_INTERVAL", timedelta(milliseconds=5),
    )

    async def run_with_long_io():
        runner.start(session_id, _full_auto_claim=execute_owner)
        await entered.wait()
        before = db.one(
            "SELECT lease_started_at FROM full_auto_run WHERE run_id = ?", run_id,
        )["lease_started_at"]
        before_runner = db.one(
            "SELECT lease_started_at FROM global_runner_claim WHERE singleton = 1",
        )["lease_started_at"]
        monkeypatch.setattr(db, "now", fake_now)
        await asyncio.sleep(0.1)
        after = db.one(
            "SELECT lease_started_at FROM full_auto_run WHERE run_id = ?", run_id,
        )["lease_started_at"]
        after_runner = db.one(
            "SELECT lease_started_at FROM global_runner_claim WHERE singleton = 1",
        )["lease_started_at"]
        assert datetime.fromisoformat(after) - datetime.fromisoformat(before) >= timedelta(seconds=60)
        assert (
            datetime.fromisoformat(after_runner) - datetime.fromisoformat(before_runner)
            >= timedelta(seconds=60)
        )
        step[0] = timedelta(0)
        release.set()
        await runner._task

    asyncio.run(run_with_long_io())
    assert fake.attempts == len(_targets)
    assert {row["status"] for row in db.q(
        "SELECT status FROM shot WHERE session_id = ?", session_id,
    )} == {"done"}
    assert db.one("SELECT status FROM session WHERE id = ?", session_id)["status"] == "done"
    assert db.one("SELECT run_id FROM full_auto_runner_admission WHERE run_id = ?", run_id)


def test_full_auto_runner_owner_loss_preserves_unknown_admission_without_redispatch(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner = _approved_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    _view, execute_owner = main._run_full_auto_submission(owner)
    runner, fake = make_runner()
    entered = asyncio.Event()
    release = asyncio.Event()
    original_queue = fake.queue_prompt

    async def delayed_queue(graph, client_id, **kwargs):
        prompt_id = await original_queue(graph, client_id, **kwargs)
        entered.set()
        await release.wait()
        return prompt_id

    monkeypatch.setattr(fake, "queue_prompt", delayed_queue)

    async def lose_owner_during_comfy_io():
        runner.start(session_id, _full_auto_claim=execute_owner)
        await entered.wait()
        db.run(
            "UPDATE full_auto_run SET lease_started_at = ?, lease_expires_at = ? WHERE run_id = ?",
            "1999-12-31T23:58:00+00:00", "2000-01-01T00:00:00+00:00", run_id,
        )
        reconciliation = resource_full_auto.claim_run(run_id)
        unresolved = resource_full_auto.unresolved_work_count(session_id, run_id)
        assert unresolved > 0
        view, _next_owner = resource_full_auto.reconcile_expired_lease(
            reconciliation,
            resource_full_auto.LeaseReconciliationEvidence(
                "unknown", unresolved, "run_work_unsettled",
            ),
        )
        assert view["state"] == "blocked"
        release.set()
        await runner._task

    asyncio.run(lose_owner_during_comfy_io())
    shots = db.q("SELECT status, prompt_id FROM shot WHERE session_id = ? ORDER BY id", session_id)
    assert fake.attempts == 1
    assert shots[0] == {"status": "running", "prompt_id": "pid-1"}
    assert all(row == {"status": "pending", "prompt_id": ""} for row in shots[1:])
    attempt = db.one(
        "SELECT dispatch_state, prompt_id FROM full_auto_execution_attempt WHERE run_id = ?",
        run_id,
    )
    assert attempt == {"dispatch_state": "acknowledged", "prompt_id": "pid-1"}
    assert db.one("SELECT status FROM session WHERE id = ?", session_id)["status"] == "running"
    assert db.one("SELECT run_id FROM full_auto_runner_admission WHERE run_id = ?", run_id)


def test_full_auto_attempt_journal_orders_queue_ack_output_and_locked_move(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner = _approved_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    _view, execute_owner = main._run_full_auto_submission(owner)
    runner, fake = make_runner()
    events = []
    original_queue = fake.queue_prompt
    original_history = fake.history
    real_move = runner_mod.shutil.move
    move_calls = 0

    async def observe_queue(graph, client_id, **kwargs):
        shot = db.one(
            "SELECT id, status, seed, prompt_id FROM shot WHERE session_id = ? "
            "AND status = 'running'", session_id,
        )
        attempt = db.one(
            "SELECT * FROM full_auto_execution_attempt WHERE shot_id = ?", shot["id"],
        )
        runner_claim = db.one("SELECT * FROM global_runner_claim WHERE singleton = 1")
        assert shot is not None and attempt is not None
        assert (shot["status"], shot["prompt_id"]) == ("running", "")
        assert attempt["dispatch_state"] == "intent"
        assert attempt["completion_state"] == "pending"
        assert attempt["seed"] == shot["seed"] > 0
        assert attempt["run_id"] == run_id
        assert attempt["run_epoch"] == execute_owner.epoch
        assert attempt["run_fencing_token"] == execute_owner.fencing_token
        assert attempt["runner_fencing_token"] == runner_claim["fencing_token"]
        assert attempt["filename_prefix"] == graph["8"]["inputs"]["filename_prefix"]
        assert attempt["filename_prefix"].endswith(attempt["attempt_id"])
        assert attempt["graph_digest"] == resource_store.canonical_digest(graph)
        events.append("intent")
        return await original_queue(graph, client_id, **kwargs)

    async def observe_history(prompt_id):
        attempt = db.one(
            "SELECT dispatch_state, prompt_id FROM full_auto_execution_attempt "
            "WHERE prompt_id = ?", prompt_id,
        )
        assert attempt == {"dispatch_state": "acknowledged", "prompt_id": prompt_id}
        events.append("acknowledged")
        return await original_history(prompt_id)

    def lock_then_move(src, dest):
        nonlocal move_calls
        move_calls += 1
        attempt = db.one(
            "SELECT completion_state, output_evidence_json, expected_filename "
            "FROM full_auto_execution_attempt WHERE completion_state = 'output_available' "
            "ORDER BY attempt_number LIMIT 1",
        )
        assert attempt is not None
        assert json.loads(attempt["output_evidence_json"])["filename"]
        assert Path(dest).name == attempt["expected_filename"]
        events.append("output_available")
        if move_calls == 1:
            raise PermissionError(32, "The process cannot access the file")
        return real_move(src, dest)

    fake.queue_prompt = observe_queue
    fake.history = observe_history
    monkeypatch.setattr(runner_mod.shutil, "move", lock_then_move)

    async def run():
        runner.start(session_id, _full_auto_claim=execute_owner)
        await runner._task

    asyncio.run(run())
    attempts = db.q(
        "SELECT * FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY shot_id",
        run_id,
    )
    shots = db.q("SELECT * FROM shot WHERE session_id = ? ORDER BY id", session_id)
    assert len(attempts) == len(shots) == 4
    assert len({attempt["filename_prefix"] for attempt in attempts}) == 4
    assert all(attempt["dispatch_state"] == "acknowledged" for attempt in attempts)
    assert all(attempt["completion_state"] == "completed" for attempt in attempts)
    assert all(attempt["prompt_id"] == shot["prompt_id"] for attempt, shot in zip(attempts, shots))
    assert all(attempt["filename"] == shot["filename"] for attempt, shot in zip(attempts, shots))
    assert attempts[0]["expected_filename"] == attempts[0]["filename"]
    assert "intent" in events and "acknowledged" in events and "output_available" in events
    assert events.index("intent") < events.index("acknowledged") < events.index("output_available")
    assert move_calls == 5  # the first output hit the lock and succeeded on retry
    with pytest.raises(sqlite3.IntegrityError, match="transition is invalid"):
        db.run(
            "UPDATE full_auto_execution_attempt SET dispatch_state='intent' WHERE attempt_id = ?",
            attempts[0]["attempt_id"],
        )


@pytest.mark.parametrize("changed", ["settings", "workflow", "base_prompt"])
def test_full_auto_refuses_frozen_input_drift_after_comfy_availability(
    client, seeded, monkeypatch, make_runner, changed,
):
    session_id, run_id, _revision, _targets, owner = _approved_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    _view, execute_owner = main._run_full_auto_submission(owner)
    runner, fake = make_runner()
    changed_once = False

    async def drift_during_availability():
        nonlocal changed_once
        if not changed_once:
            changed_once = True
            if changed == "settings":
                session = db.one("SELECT settings FROM session WHERE id = ?", session_id)
                settings = json.loads(session["settings"])
                settings["steps"] += 1
                db.run(
                    "UPDATE session SET settings = ? WHERE id = ?",
                    json.dumps(settings), session_id,
                )
            elif changed == "workflow":
                workflow_id = db.one(
                    "SELECT workflow_id FROM session WHERE id = ?", session_id,
                )["workflow_id"]
                workflow = json.loads(db.one(
                    "SELECT graph FROM workflow WHERE id = ?", workflow_id,
                )["graph"])
                workflow["99"] = {"class_type": "UnusedNode", "inputs": {}}
                db.run(
                    "UPDATE workflow SET graph = ? WHERE id = ?",
                    json.dumps(workflow), workflow_id,
                )
            else:
                model_id = db.one(
                    "SELECT model_id FROM session WHERE id = ?", session_id,
                )["model_id"]
                db.run(
                    "UPDATE model SET base_positive = ? WHERE id = ?",
                    "Changed effective base prompt", model_id,
                )
        return {}

    fake.stats = drift_during_availability

    async def run():
        runner.start(session_id, _full_auto_claim=execute_owner)
        await runner._task

    asyncio.run(run())
    run_row = db.one("SELECT state, blocker_code FROM full_auto_run WHERE run_id = ?", run_id)
    shot = db.one("SELECT status FROM shot WHERE session_id = ?", session_id)
    assert fake.attempts == 0
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )["n"] == 0
    assert shot["status"] == "failed"
    assert run_row["state"] == "blocked"
    assert run_row["blocker_code"] == (
        "workflow_changed" if changed == "workflow" else "dependency_changed"
    )


def test_full_auto_preserves_frozen_workflow_when_model_default_changes(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner = _approved_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    _view, execute_owner = main._run_full_auto_submission(owner)
    runner, fake = make_runner()
    changed_once = False

    async def change_irrelevant_model_defaults_during_availability():
        nonlocal changed_once
        if not changed_once:
            changed_once = True
            model_id = db.one(
                "SELECT model_id FROM session WHERE id = ?", session_id,
            )["model_id"]
            db.run(
                "UPDATE model SET workflow_id = NULL, name = ? WHERE id = ?",
                "Renamed model", model_id,
            )
        return {}

    fake.stats = change_irrelevant_model_defaults_during_availability

    async def run():
        runner.start(session_id, _full_auto_claim=execute_owner)
        await runner._task

    asyncio.run(run())
    run_row = db.one("SELECT state, phase, blocker_code FROM full_auto_run WHERE run_id = ?", run_id)
    assert fake.attempts == 4
    assert run_row == {"state": "succeeded", "phase": "execute", "blocker_code": None}
    assert db.one("SELECT status FROM session WHERE id = ?", session_id)["status"] == "done"
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id = ? "
        "AND completion_state = 'completed'", run_id,
    )["n"] == 4


def test_full_auto_keeps_explicit_canvas_when_model_dimension_defaults_change(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner = _approved_run(
        client, seeded, monkeypatch, photo_count=4, canvas=(768, 1360),
    )
    _view, execute_owner = main._run_full_auto_submission(owner)
    runner, fake = make_runner()
    changed_once = False

    async def change_model_dimensions_during_availability():
        nonlocal changed_once
        if not changed_once:
            changed_once = True
            model_id = db.one(
                "SELECT model_id FROM session WHERE id = ?", session_id,
            )["model_id"]
            model_settings = json.loads(db.one(
                "SELECT settings FROM model WHERE id = ?", model_id,
            )["settings"])
            model_settings.update(width=1024, height=1024)
            db.run(
                "UPDATE model SET settings = ? WHERE id = ?",
                json.dumps(model_settings), model_id,
            )
        return {}

    fake.stats = change_model_dimensions_during_availability

    async def run():
        runner.start(session_id, _full_auto_claim=execute_owner)
        await runner._task

    asyncio.run(run())
    run_row = db.one("SELECT state, blocker_code FROM full_auto_run WHERE run_id = ?", run_id)
    assert fake.attempts == 4
    assert run_row["state"] == "succeeded"
    assert run_row["blocker_code"] is None
    assert all(
        graph["5"]["inputs"]["width"] == 768
        and graph["5"]["inputs"]["height"] == 1360
        for graph in fake.graphs
    )


def test_full_auto_lost_queue_response_recovers_unique_attempt_without_reposting(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner = _approved_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    _view, execute_owner = main._run_full_auto_submission(owner)
    runner, fake = make_runner()
    original_queue = fake.queue_prompt

    async def accepted_without_response(graph, client_id, **kwargs):
        await original_queue(graph, client_id, **kwargs)
        raise RuntimeError("queue response was lost")

    fake.queue_prompt = accepted_without_response

    async def run():
        runner.start(session_id, _full_auto_claim=execute_owner)
        await runner._task

    asyncio.run(run())
    attempts = db.q(
        "SELECT dispatch_state, completion_state, prompt_id, client_prompt_id "
        "FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY shot_id", run_id,
    )
    assert len(attempts) == len(_targets)
    assert all(row["dispatch_state"] == "acknowledged" for row in attempts)
    assert all(row["completion_state"] == "completed" for row in attempts)
    assert all(row["prompt_id"] and row["client_prompt_id"] is None for row in attempts)
    assert db.one(
        "SELECT COUNT(*) AS n FROM shot WHERE session_id = ? AND status = 'done'",
        session_id,
    )["n"] == len(_targets)
    # Every accepted lost response was reconciled read-only; no POST was replayed.
    assert fake.attempts == len(_targets)


@pytest.mark.parametrize(
    "tamper", ["duplicate", "graph_mismatch", "malformed_metadata", "malformed_queue", "malformed_history"],
)
def test_full_auto_queue_reconciliation_does_not_repost_unresolved_work(
    client, seeded, monkeypatch, make_runner, tamper,
):
    session_id, run_id, _revision, _targets, owner = _approved_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    _view, execute_owner = main._run_full_auto_submission(owner)
    runner, fake = make_runner()
    fake.advertise_client_prompt_id = True
    original_queue = fake.queue_prompt
    original_history = fake.history
    candidate_output_path = None
    collect_calls = []
    collect = runner._collect

    async def record_collect(check_session_id, shot, image, heartbeat=None):
        collect_calls.append(image)
        return await collect(check_session_id, shot, image, heartbeat)

    runner._collect = record_collect

    async def ambiguous_acceptance(graph, client_id, **kwargs):
        nonlocal candidate_output_path
        prompt_id = await original_queue(graph, client_id, **kwargs)
        if tamper == "duplicate":
            fake.queue_records.append(fake.queue_records[-1])
        else:
            if tamper == "graph_mismatch":
                fake.queue_records[-1][2]["8"]["inputs"]["filename_prefix"] = "ComfyUI"
            elif tamper == "malformed_metadata":
                fake.queue_records[-1][3]["idevgen_attempt"] = ["invalid"]
            elif tamper == "malformed_queue":
                fake.queue_records.append(["invalid"])
            else:
                prompt_id = fake.queue_records[-1][1]
                queue_record = fake.queue_records[-1]
                completed_history = await original_history(prompt_id)
                image = completed_history["outputs"]["8"]["images"][0]
                candidate_output_path = (
                    fake.output_dir / image["subfolder"] / image["filename"]
                )
                malformed_history = {**completed_history, "prompt": ["invalid"]}
                fake.queue_records.append(queue_record)
                fake.prompt_records[prompt_id]["prompt"] = ["invalid"]
                # Keep history enumeration empty so the one well-formed queue
                # occurrence can establish remote identity; the later direct
                # history read must still fail closed for output adoption.
                fake.prompt_records[prompt_id]["status"] = None

                async def read_malformed_history(_prompt_id):
                    return malformed_history

                fake.history = read_malformed_history
        raise RuntimeError("queue response was lost")

    fake.queue_prompt = ambiguous_acceptance

    async def run():
        runner.start(session_id, _full_auto_claim=execute_owner)
        await runner._task

    asyncio.run(run())
    attempt = db.one(
        "SELECT dispatch_state, completion_state, prompt_id, client_prompt_id, "
        "output_evidence_json, filename "
        "FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )
    assert attempt["completion_state"] == "pending"
    assert attempt["client_prompt_id"] is not None
    assert fake.attempts == 1
    shot = db.one(
        "SELECT status, filename FROM shot WHERE session_id = ?", session_id,
    )
    assert shot["status"] == "running"
    assert collect_calls == []
    assert attempt["output_evidence_json"] is None
    assert attempt["filename"] == ""
    assert shot["filename"] == ""
    if tamper == "malformed_history":
        assert attempt["dispatch_state"] == "acknowledged"
        assert attempt["prompt_id"] is not None
        assert candidate_output_path is not None and candidate_output_path.is_file()
    else:
        assert attempt["dispatch_state"] == "outcome_unknown"
        assert attempt["prompt_id"] is None


def test_full_auto_output_metadata_recovers_when_remote_history_is_empty(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _revision, _targets, owner = _approved_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    _view, execute_owner = main._run_full_auto_submission(owner)
    runner, fake = make_runner()
    original_queue = fake.queue_prompt
    original_history = fake.history

    async def accepted_then_lost(graph, client_id, **kwargs):
        prompt_id = await original_queue(graph, client_id, **kwargs)
        await original_history(prompt_id)
        fake.queue_records.clear()
        fake.prompt_records.clear()
        raise RuntimeError("queue response was lost after output")

    fake.queue_prompt = accepted_then_lost

    async def run():
        runner.start(session_id, _full_auto_claim=execute_owner)
        await runner._task

    asyncio.run(run())
    attempt = db.one(
        "SELECT dispatch_state, completion_state, prompt_id, output_evidence_json "
        "FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )
    assert attempt["dispatch_state"] == "outcome_unknown"
    assert attempt["completion_state"] == "completed"
    assert attempt["prompt_id"] is None
    assert json.loads(attempt["output_evidence_json"])["correlation"]["source"] == "output_png"
    shots = db.q(
        "SELECT status, prompt_id FROM shot WHERE session_id = ? ORDER BY id", session_id,
    )
    assert len(shots) == 4
    assert all(shot["status"] == "done" for shot in shots)
    assert all(shot["prompt_id"].startswith("idevgen-recovered-output:") for shot in shots)
    assert fake.attempts == 4


@pytest.mark.parametrize("drift", ["identity", "filename_only", "malformed_png"])
def test_full_auto_lost_ack_refuses_drift_or_filename_only_output(
    client, seeded, monkeypatch, make_runner, drift,
):
    session_id, run_id, _revision, _targets, owner = _approved_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    _view, execute_owner = main._run_full_auto_submission(owner)
    runner, fake = make_runner(write_file=False)
    original_queue = fake.queue_prompt

    async def accepted_without_recoverable_receipt(graph, client_id, **kwargs):
        prompt_id = await original_queue(graph, client_id, **kwargs)
        if drift == "identity":
            fake.server_stats["system"]["comfyui_version"] = "changed-test-fake"
        elif drift in {"filename_only", "malformed_png"}:
            prefix = graph["8"]["inputs"]["filename_prefix"]
            subfolder, _, stem = prefix.rpartition("/")
            folder = fake.output_dir / subfolder
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{stem}_00001_.png"
            if drift == "filename_only":
                Image.new("RGB", (2, 2), (10, 20, 30)).save(path)
            else:
                png_info = PngInfo()
                png_info.add_text("prompt", json.dumps(graph))
                png_info.add_text("idevgen_attempt", "{malformed")
                Image.new("RGB", (2, 2), (10, 20, 30)).save(path, pnginfo=png_info)
            fake.queue_records.clear()
            fake.prompt_records.clear()
        raise RuntimeError("queue response was lost")

    fake.queue_prompt = accepted_without_recoverable_receipt

    async def run():
        runner.start(session_id, _full_auto_claim=execute_owner)
        await runner._task

    asyncio.run(run())
    attempt = db.one(
        "SELECT dispatch_state, completion_state, prompt_id "
        "FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )
    assert attempt == {
        "dispatch_state": "outcome_unknown", "completion_state": "pending", "prompt_id": None,
    }
    assert fake.attempts == 1
    assert db.one(
        "SELECT status FROM shot WHERE session_id = ?", session_id,
    )["status"] == "running"


@pytest.mark.parametrize("interleaving", ["feature_flag", "lease_expiry"])
def test_receipt_rechecks_authority_after_review_and_resumes_the_committed_link(
    client, seeded, monkeypatch, make_runner, interleaving,
):
    session_id, run_id, revision, targets, owner = _approved_run(
        client, seeded, monkeypatch,
    )
    original_review = session_plan._full_auto_duplicate_review_evidence
    original_receipt = resource_full_auto.record_submission_receipt
    original_now = db.now
    in_receipt = False
    injected = False

    def record_receipt(*args, **kwargs):
        nonlocal in_receipt
        in_receipt = True
        try:
            return original_receipt(*args, **kwargs)
        finally:
            in_receipt = False

    with monkeypatch.context() as patcher:
        def review(*args, **kwargs):
            nonlocal injected
            evidence = original_review(*args, **kwargs)
            if in_receipt and not injected:
                injected = True
                if interleaving == "feature_flag":
                    patcher.setitem(main.CONFIG, "resource_planning_enabled", False)
                else:
                    future = datetime.fromisoformat(
                        original_now().replace("Z", "+00:00"),
                    ) + timedelta(seconds=120)
                    patcher.setattr(
                        db, "now", lambda: future.isoformat(timespec="seconds"),
                    )
            return evidence

        patcher.setattr(session_plan, "_full_auto_duplicate_review_evidence", review)
        patcher.setattr(resource_full_auto, "record_submission_receipt", record_receipt)
        with pytest.raises(
            session_plan.ResourcePlanningDisabled
            if interleaving == "feature_flag"
            else resource_full_auto.FullAutoRunError,
        ) as failure:
            main._run_full_auto_submission(owner)

    if interleaving == "lease_expiry":
        assert failure.value.code == "run_lease_expired"
    assert injected is True
    linked_before_restart = _linked_rows(session_id, revision)
    assert [row["take_id"] for row in linked_before_restart] == targets
    assert linked_before_restart[0]["linked_shot_id"] is not None
    assert linked_before_restart[0]["prompt"] == linked_before_restart[0]["final_prompt"]
    assert all(row["linked_shot_id"] is None for row in linked_before_restart[1:])
    assert db.q(
        "SELECT * FROM full_auto_submission_receipt WHERE run_id = ?", run_id,
    ) == []
    assert db.q(
        "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == []

    runner, fake = make_runner()
    status_before = db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"]
    with pytest.raises(resource_full_auto.FullAutoRunError):
        asyncio.run(_start_and_wait(runner, session_id, owner))
    assert runner._task is None
    assert fake.attempts == 0
    assert db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"] == status_before

    _reopen_database()
    if interleaving == "feature_flag":
        main.CONFIG["resource_planning_enabled"] = True
    recovered_owner = _recover_owner(run_id, "submit")
    view, execute_owner = main._run_full_auto_submission(recovered_owner)
    assert (view["state"], view["phase"]) == ("active", "execute")
    assert execute_owner is not None

    linked_after_restart = _linked_rows(session_id, revision)
    assert [row["take_id"] for row in linked_after_restart] == targets
    assert all(row["linked_shot_id"] is not None for row in linked_after_restart)
    assert all(row["prompt"] == row["final_prompt"] for row in linked_after_restart)
    assert linked_after_restart[0]["shot_id"] == linked_before_restart[0]["shot_id"]
    assert len({row["shot_id"] for row in linked_after_restart}) == len(targets)
    assert [row["take_id"] for row in db.q(
        "SELECT take_id FROM full_auto_submission_receipt WHERE run_id = ? "
        "ORDER BY target_index", run_id,
    )] == targets

    asyncio.run(_start_and_wait(runner, session_id, execute_owner))
    assert fake.attempts == len(targets)
    assert db.q(
        "SELECT run_id FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == [{"run_id": run_id}]
    shot_prompts = [row["prompt"] for row in db.q(
        "SELECT prompt FROM shot WHERE session_id = ? ORDER BY id", session_id,
    )]
    observed_prompts = [
        value
        for graph in fake.graphs
        for node in graph.values()
        for value in node.get("inputs", {}).values()
        if value in shot_prompts
    ]
    assert observed_prompts == shot_prompts
    assert all(row["status"] == "done" for row in db.q(
        "SELECT status FROM shot WHERE session_id = ? ORDER BY id", session_id,
    ))


def test_runner_refuses_a_valid_existing_link_that_precedes_target_order(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, revision, targets, owner = _approved_run(
        client, seeded, monkeypatch,
    )
    preexisting = session_plan.submit_prepared_take(
        session_id, revision, targets[1], _full_auto_claim=owner,
    )
    view, execute_owner = main._run_full_auto_submission(owner)
    assert (view["state"], view["phase"]) == ("active", "execute")
    assert execute_owner is not None
    rows = _linked_rows(session_id, revision)
    assert [row["take_id"] for row in rows] == targets
    assert rows[1]["shot_id"] == preexisting["shot_id"]
    assert rows[1]["shot_id"] < rows[0]["shot_id"]

    runner, fake = make_runner()
    status_before = db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"]
    with pytest.raises(resource_full_auto.FullAutoRunError) as refusal:
        asyncio.run(_start_and_wait(runner, session_id, execute_owner))
    assert refusal.value.code == "full_auto_execution_state_stale"
    assert runner._task is None
    assert fake.attempts == 0
    assert db.q(
        "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == []
    assert db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"] == status_before


@pytest.mark.parametrize("interleaving", ["feature_flag", "lease_expiry"])
def test_runner_admission_rechecks_flag_and_lease_after_review(
    client, seeded, monkeypatch, make_runner, interleaving,
):
    session_id, run_id, _revision, _targets, submit_owner = _approved_run(
        client, seeded, monkeypatch,
    )
    view, execute_owner = main._run_full_auto_submission(submit_owner)
    assert view["phase"] == "execute"
    assert execute_owner is not None
    run_before = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    original_review = session_plan._full_auto_duplicate_review_evidence
    original_now = db.now
    injected = False

    with monkeypatch.context() as patcher:
        def review(*args, **kwargs):
            nonlocal injected
            evidence = original_review(*args, **kwargs)
            if not injected:
                injected = True
                if interleaving == "feature_flag":
                    patcher.setitem(main.CONFIG, "resource_planning_enabled", False)
                else:
                    future = datetime.fromisoformat(
                        original_now().replace("Z", "+00:00"),
                    ) + timedelta(seconds=120)
                    patcher.setattr(
                        db, "now", lambda: future.isoformat(timespec="seconds"),
                    )
            return evidence

        patcher.setattr(session_plan, "_full_auto_duplicate_review_evidence", review)
        runner, fake = make_runner()
        with pytest.raises(
            session_plan.ResourcePlanningDisabled
            if interleaving == "feature_flag"
            else resource_full_auto.FullAutoRunError,
        ) as refusal:
            asyncio.run(_start_and_wait(runner, session_id, execute_owner))

    if interleaving == "lease_expiry":
        assert refusal.value.code == "run_lease_expired"
    assert injected is True
    assert runner._task is None
    assert fake.attempts == 0
    assert db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id) == run_before
    assert db.q(
        "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == []
    assert db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"] != "running"


@pytest.mark.parametrize("interruption", ["cancel", "fence_takeover"])
def test_obsolete_execution_claim_cannot_admit_runner_after_cancel_or_takeover(
    client, seeded, monkeypatch, make_runner, interruption,
):
    session_id, run_id, _revision, _targets, submit_owner = _approved_run(
        client, seeded, monkeypatch,
    )
    view, old_owner = main._run_full_auto_submission(submit_owner)
    assert view["phase"] == "execute"
    if interruption == "cancel":
        run = db.one(
            "SELECT revision, plan_revision FROM full_auto_run WHERE run_id = ?", run_id,
        )
        cancelled = resource_full_auto.cancel_run(
            run_id, "independent-execution-cancel", run["revision"],
            run["plan_revision"], work_pending=True,
        )
        assert cancelled.view["state"] == "cancel_requested"
    else:
        _reopen_database()
        current_owner = _recover_owner(run_id, "execute")
        assert current_owner.fencing_token > old_owner.fencing_token

    run_before_refusal = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    runner, fake = make_runner()
    with pytest.raises(resource_full_auto.FullAutoRunError) as refusal:
        asyncio.run(_start_and_wait(runner, session_id, old_owner))
    assert refusal.value.code == "run_owner_stale"
    assert runner._task is None
    assert fake.attempts == 0
    assert db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id) == run_before_refusal
    assert db.q(
        "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == []
    assert db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"] != "running"

    if interruption == "fence_takeover":
        asyncio.run(_start_and_wait(runner, session_id, current_owner))
        assert fake.attempts == len(_targets)


def test_runner_rejects_a_missing_prepared_shot_link_without_admission(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, revision, targets, owner = _approved_run(
        client, seeded, monkeypatch,
    )
    view, execute_owner = main._run_full_auto_submission(owner)
    assert view["phase"] == "execute"
    target = db.one(
        "SELECT id FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, revision, targets[0],
    )
    db.run("UPDATE prepared_take SET linked_shot_id = NULL WHERE id = ?", target["id"])

    runner, fake = make_runner()
    with pytest.raises(resource_full_auto.FullAutoRunError) as refusal:
        asyncio.run(_start_and_wait(runner, session_id, execute_owner))
    assert refusal.value.code == "linked_snapshot_invalid"
    assert runner._task is None
    assert fake.attempts == 0
    assert db.q(
        "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == []


def test_manual_runner_still_works_after_full_auto_is_cancelled(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, revision, targets, _owner = _approved_run(
        client, seeded, monkeypatch,
    )
    run = db.one(
        "SELECT revision, plan_revision FROM full_auto_run WHERE run_id = ?", run_id,
    )
    cancelled = resource_full_auto.cancel_run(
        run_id, "independent-manual-transfer", run["revision"], run["plan_revision"],
        work_pending=False,
    )
    assert cancelled.view["state"] == "cancelled"
    session_plan.approve_plan_review(session_id, revision)
    manual = session_plan.submit_prepared_take(session_id, revision, targets[0])
    shot_id = manual["shot_id"]

    runner, fake = make_runner()
    asyncio.run(_start_and_wait(runner, session_id))
    assert fake.attempts == 1
    assert db.one("SELECT status FROM shot WHERE id = ?", shot_id)["status"] == "done"
    assert db.q(
        "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == []
