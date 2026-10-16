"""Independent adversarial checks for Full Auto's Comfy reconciliation path."""
from __future__ import annotations

import asyncio
import copy
import json

from PIL import Image
import pytest

import db
import main
from backend.comfy import execution_capability_evidence
from backend import resource_full_auto
from runner import Runner
import test_full_auto_submission_independent as submission


@pytest.fixture(autouse=True)
def _configured_assistant(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "correlation-independent-test")
    monkeypatch.setitem(main.CONFIG, "llm_key", "correlation-independent-test-key")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)


def _execution(client, seeded, monkeypatch, make_runner, *, photo_count=4):
    session_id, run_id, _revision, targets, owner = submission._approved_run(
        client, seeded, monkeypatch, photo_count=photo_count,
    )
    _view, execute_owner = main._run_full_auto_submission(owner)
    assert execute_owner is not None
    runner, fake = make_runner()
    return session_id, run_id, targets, execute_owner, runner, fake


def _start(runner, session_id, owner):
    async def run():
        runner.start(session_id, _full_auto_claim=owner)
        if runner._task is not None:
            await runner._task

    asyncio.run(run())


def test_unknown_server_version_does_not_enable_client_prompt_uuid():
    evidence = execution_capability_evidence(
        "http://fake-comfy.invalid",
        {"system": {"comfyui_version": "unknown"}},
        {"client_prompt_id": True},
    )

    assert evidence["advertised_client_prompt_id"] is True
    assert evidence["client_prompt_id"] is False


@pytest.mark.parametrize("subfolder", ["idevgen/429", r"idevgen\429"])
def test_attempt_output_prefix_accepts_relative_posix_and_windows_subfolders(subfolder):
    image = {
        "filename": "take_00001_00001_.png",
        "subfolder": subfolder,
        "type": "output",
    }

    assert Runner._image_matches_attempt_prefix(image, "idevgen/429/take_00001")


@pytest.mark.parametrize(
    ("prefix", "subfolder"),
    [
        ("idevgen/429/take_00001", r"idevgen\..\idevgen\429"),
        ("/idevgen/429/take_00001", "/idevgen/429"),
        ("C:/idevgen/429/take_00001", "C:/idevgen/429"),
        ("idevgen/C:/429/take_00001", "idevgen/C:/429"),
        (r"\\server\share\idevgen\429\take_00001", r"\\server\share\idevgen\429"),
        (r"\\?\C:\idevgen\429\take_00001", r"\\?\C:\idevgen\429"),
        ("idevgen/../429/take_00001", "idevgen/../429"),
    ],
)
def test_attempt_output_prefix_rejects_traversal_and_absolute_subfolders(prefix, subfolder):
    image = {
        "filename": "take_00001_00001_.png",
        "subfolder": subfolder,
        "type": "output",
    }

    assert not Runner._image_matches_attempt_prefix(image, prefix)


@pytest.mark.parametrize(
    ("filename", "prefix"),
    [
        ("../take_00001_00001_.png", "idevgen/429/take_00001"),
        (r"..\take_00001_00001_.png", "idevgen/429/take_00001"),
        ("C:take_00001_00001_.png", "C:take_00001"),
    ],
)
def test_attempt_output_prefix_rejects_path_like_filenames(filename, prefix):
    image = {"filename": filename, "subfolder": "idevgen/429", "type": "output"}

    assert not Runner._image_matches_attempt_prefix(image, prefix)


def test_uuid_png_recovery_collects_the_same_shot_after_lost_ack(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    fake.advertise_client_prompt_id = True
    original_queue = fake.queue_prompt
    original_history = fake.history

    async def accepted_then_lost(graph, client_id, **kwargs):
        prompt_id = await original_queue(graph, client_id, **kwargs)
        await original_history(prompt_id)
        fake.queue_records.clear()
        fake.prompt_records.clear()
        raise RuntimeError("accepted request; lost acknowledgement")

    fake.queue_prompt = accepted_then_lost
    _start(runner, session_id, owner)

    attempts = db.q(
        "SELECT attempt_id, shot_id, dispatch_state, completion_state, prompt_id, "
        "client_prompt_id, output_evidence_json, filename FROM full_auto_execution_attempt "
        "WHERE run_id = ? ORDER BY shot_id", run_id,
    )
    shots = db.q(
        "SELECT id, status, prompt_id, filename FROM shot WHERE session_id = ? ORDER BY id",
        session_id,
    )
    assert len(attempts) == len(shots) == len(targets)
    assert fake.attempts == len(targets)
    assert [row["shot_id"] for row in attempts] == [row["id"] for row in shots]
    assert all(row["dispatch_state"] == "outcome_unknown" for row in attempts)
    assert all(row["completion_state"] == "completed" for row in attempts)
    assert all(row["prompt_id"] is None for row in attempts)
    assert all(row["client_prompt_id"] for row in attempts)
    assert all(row["status"] == "done" for row in shots)
    for attempt, shot in zip(attempts, shots, strict=True):
        assert shot["prompt_id"] == f"idevgen-recovered-output:{attempt['attempt_id']}"
        evidence = json.loads(attempt["output_evidence_json"])
        assert evidence["correlation"]["source"] == "output_png"
        assert evidence["correlation"]["client_prompt_id"] == attempt["client_prompt_id"]
        assert (runner.sessions_dir / str(session_id) / attempt["filename"]).is_file()


def test_same_uuid_in_queue_and_history_is_ambiguous_on_nondeduplicating_server(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    fake.advertise_client_prompt_id = True
    original_queue = fake.queue_prompt
    original_history = fake.history

    async def duplicated_across_states(graph, client_id, **kwargs):
        prompt_id = await original_queue(graph, client_id, **kwargs)
        await original_history(prompt_id)
        fake.queue_records.append(copy.deepcopy(fake.prompt_records[prompt_id]["prompt"]))
        raise RuntimeError("accepted request; lost acknowledgement")

    fake.queue_prompt = duplicated_across_states
    _start(runner, session_id, owner)

    attempt = db.one(
        "SELECT dispatch_state, completion_state, prompt_id "
        "FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )
    shot = db.one("SELECT status, filename FROM shot WHERE session_id = ?", session_id)
    assert attempt == {
        "dispatch_state": "outcome_unknown", "completion_state": "pending", "prompt_id": None,
    }
    assert shot["status"] == "running" and shot["filename"] == ""
    assert fake.attempts == 1
    assert any(fake.output_dir.rglob("*.png"))
    assert not (runner.sessions_dir / str(session_id)).exists()


def test_acknowledged_history_must_match_attempt_graph_metadata_and_output(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    fake.advertise_client_prompt_id = True
    original_history = fake.history

    async def mismatched_history(prompt_id):
        history = await original_history(prompt_id)
        record = copy.deepcopy(fake.prompt_records[prompt_id])
        record["prompt"][2]["8"]["inputs"]["filename_prefix"] = "foreign/prefix"
        record["prompt"][3]["idevgen_attempt"]["graph_digest"] = "0" * 64
        history["prompt"] = record["prompt"]
        # A real unrelated PNG exists at the name returned by the corrupted response.
        foreign_dir = fake.output_dir / "foreign"
        foreign_dir.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2, 2), (1, 2, 3)).save(foreign_dir / "foreign.png")
        history["outputs"] = {
            "8": {"images": [{"filename": "foreign.png", "subfolder": "foreign", "type": "output"}]},
        }
        return history

    fake.history = mismatched_history
    _start(runner, session_id, owner)

    attempt = db.one(
        "SELECT dispatch_state, completion_state, prompt_id, filename "
        "FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )
    shot = db.one("SELECT status, filename FROM shot WHERE session_id = ?", session_id)
    observed = {
        "dispatch_state": attempt["dispatch_state"],
        "completion_state": attempt["completion_state"],
        "prompt_id_present": bool(attempt["prompt_id"]),
        "attempt_filename": attempt["filename"],
        "shot_status": shot["status"],
        "shot_filename": shot["filename"],
        "foreign_source_exists": (fake.output_dir / "foreign" / "foreign.png").exists(),
        "collected_file_exists": bool(shot["filename"])
        and (runner.sessions_dir / str(session_id) / shot["filename"]).is_file(),
        "physical_posts": fake.attempts,
    }
    assert observed == {
        "dispatch_state": "acknowledged",
        "completion_state": "pending",
        "prompt_id_present": True,
        "attempt_filename": "",
        "shot_status": "running",
        "shot_filename": "",
        "foreign_source_exists": True,
        "collected_file_exists": False,
        "physical_posts": 1,
    }


def test_mismatched_queue_ack_stays_unknown_even_when_remote_metadata_matches(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    fake.advertise_client_prompt_id = True
    original_queue = fake.queue_prompt

    async def wrong_ack(graph, client_id, **kwargs):
        await original_queue(graph, client_id, **kwargs)
        return "different-prompt-id"

    fake.queue_prompt = wrong_ack
    _start(runner, session_id, owner)

    attempt = db.one(
        "SELECT dispatch_state, completion_state, prompt_id, client_prompt_id "
        "FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )
    shot = db.one("SELECT status, filename FROM shot WHERE session_id = ?", session_id)
    assert attempt["dispatch_state"] == "outcome_unknown"
    assert attempt["completion_state"] == "pending"
    assert attempt["prompt_id"] is None and attempt["client_prompt_id"]
    assert shot == {"status": "running", "filename": ""}
    assert fake.attempts == 1
    assert len(fake.queue_records) == 1
    assert fake.queue_records[0][1] == attempt["client_prompt_id"]
    assert fake.queue_records[0][3]["idevgen_attempt"]["attempt_id"] == db.one(
        "SELECT attempt_id FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )["attempt_id"]
    assert not (runner.sessions_dir / str(session_id)).exists()


def test_server_identity_drift_after_ack_blocks_history_output_adoption(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    fake.advertise_client_prompt_id = True
    original_queue = fake.queue_prompt

    async def acknowledged_then_server_replaced(graph, client_id, **kwargs):
        prompt_id = await original_queue(graph, client_id, **kwargs)
        fake.server_stats["system"]["comfyui_version"] = "replacement-server"
        return prompt_id

    fake.queue_prompt = acknowledged_then_server_replaced
    _start(runner, session_id, owner)

    attempt = db.one(
        "SELECT dispatch_state, completion_state, filename "
        "FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )
    shot = db.one("SELECT status, filename FROM shot WHERE session_id = ?", session_id)
    assert attempt["completion_state"] == "pending" and attempt["filename"] == ""
    assert shot["status"] == "running" and shot["filename"] == ""
    assert attempt["dispatch_state"] in {"acknowledged", "outcome_unknown"}
    assert fake.attempts == 1
    assert any(fake.output_dir.rglob("*.png"))
    assert not (runner.sessions_dir / str(session_id)).exists()


def test_capability_identity_change_after_intent_prevents_physical_post(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    fake.advertise_client_prompt_id = True
    original_begin = resource_full_auto.begin_execution_attempt
    identity_changes = 0

    def change_identity_after_intent(*args, **kwargs):
        nonlocal identity_changes
        attempt = original_begin(*args, **kwargs)
        identity_changes += 1
        fake.server_stats["system"]["comfyui_version"] = "replacement-server"
        return attempt

    monkeypatch.setattr(resource_full_auto, "begin_execution_attempt", change_identity_after_intent)
    _start(runner, session_id, owner)

    attempts = db.q(
        "SELECT dispatch_state, completion_state FROM full_auto_execution_attempt WHERE run_id = ?",
        run_id,
    )
    assert identity_changes == 1
    assert len(attempts) == 1
    assert attempts[0]["dispatch_state"] != "acknowledged"
    assert attempts[0]["completion_state"] != "completed"
    assert fake.attempts == 0
    assert db.one("SELECT status FROM shot WHERE session_id = ?", session_id)["status"] != "done"


@pytest.mark.parametrize("interleaving", ["feature_flag", "lease_expiry", "settings"])
def test_post_intent_identity_wait_rechecks_authority_before_physical_post(
    client, seeded, monkeypatch, make_runner, interleaving,
):
    session_id, run_id, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    original_capabilities = fake.execution_capabilities
    identity_waiting = asyncio.Event()
    release_identity = asyncio.Event()
    capability_reads = 0

    async def wait_during_post_intent_revalidation():
        nonlocal capability_reads
        capability_reads += 1
        evidence = await original_capabilities()
        if capability_reads == 2:
            assert db.one(
                "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id = ?",
                run_id,
            )["n"] == 1
            identity_waiting.set()
            await release_identity.wait()
        return evidence

    fake.execution_capabilities = wait_during_post_intent_revalidation

    async def run_with_authority_change():
        runner.start(session_id, _full_auto_claim=owner)
        try:
            await asyncio.wait_for(identity_waiting.wait(), timeout=5)
            if interleaving == "feature_flag":
                monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
            elif interleaving == "lease_expiry":
                expired = "2000-01-01T00:00:00+00:00"
                db.run(
                    "UPDATE full_auto_run SET lease_expires_at = ? WHERE run_id = ?",
                    expired, run_id,
                )
                db.run(
                    "UPDATE global_runner_claim SET lease_expires_at = ? WHERE singleton = 1",
                    expired,
                )
            else:
                row = db.one("SELECT settings FROM session WHERE id = ?", session_id)
                settings = json.loads(row["settings"])
                settings["steps"] += 1
                db.run(
                    "UPDATE session SET settings = ? WHERE id = ?",
                    json.dumps(settings), session_id,
                )
        finally:
            release_identity.set()
        await runner._task

    asyncio.run(run_with_authority_change())

    attempts = db.q(
        "SELECT dispatch_state, completion_state, prompt_id "
        "FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )
    assert capability_reads == 2
    assert len(attempts) == 1
    assert attempts[0]["dispatch_state"] != "acknowledged"
    assert attempts[0]["completion_state"] == "pending"
    assert attempts[0]["prompt_id"] is None
    assert fake.attempts == 0


def test_stale_owner_cannot_collect_a_png_recovered_after_lost_ack(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _targets, owner, runner, fake = _execution(
        client, seeded, monkeypatch, make_runner,
    )
    original_queue = fake.queue_prompt
    original_history = fake.history
    original_recovery = runner._recover_execution_attempt
    recovery_results = []

    async def accepted_then_lost(graph, client_id, **kwargs):
        prompt_id = await original_queue(graph, client_id, **kwargs)
        await original_history(prompt_id)
        fake.queue_records.clear()
        fake.prompt_records.clear()
        raise RuntimeError("accepted request; lost acknowledgement")

    async def expire_owner_after_recovery(attempt, heartbeat):
        recovered = await original_recovery(attempt, heartbeat)
        recovery_results.append(recovered)
        db.run(
            "UPDATE full_auto_run SET lease_expires_at = ? WHERE run_id = ?",
            "2000-01-01T00:00:00+00:00", run_id,
        )
        return recovered

    fake.queue_prompt = accepted_then_lost
    runner._recover_execution_attempt = expire_owner_after_recovery
    _start(runner, session_id, owner)

    attempt = db.one(
        "SELECT dispatch_state, completion_state, output_evidence_json "
        "FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )
    assert recovery_results and recovery_results[0] is not None
    assert attempt == {
        "dispatch_state": "outcome_unknown", "completion_state": "pending", "output_evidence_json": None,
    }
    assert db.one("SELECT status, filename FROM shot WHERE session_id = ?", session_id) == {
        "status": "running", "filename": "",
    }
    assert fake.attempts == 1
    assert any(fake.output_dir.rglob("*.png"))
    assert not (runner.sessions_dir / str(session_id)).exists()
