"""Exercise the real Full Auto coordinator and runner with local HTTP fakes."""
from __future__ import annotations

import json
import time

import httpx
import pytest

import db
import main
import runner as runner_module
from backend import saved_looks
from comfy import Comfy
from runner import Runner
from scripts.task7_1_full_auto_browser_demo import (
    ASSISTANT_URL,
    COMFY_URL,
    SyntheticTransport,
    _profile,
    isolated_async_client_factory,
)
import test_task31_acceptance as task31


_REAL_SCHEDULE_FULL_AUTO_RUN = main._schedule_full_auto_run
_TERMINAL_STATES = {"cancelled", "failed", "succeeded", "completed_with_errors"}
_COVERAGE = (
    "The chest is uncovered. The hips are uncovered. "
    "The legs are uncovered. The feet are uncovered."
)


def _versioned_accessory_look():
    saved = saved_looks.create({
        "name": "Task 7.1 progressive bracelet look",
        "appearance": "A portrait in a quiet room. She wears a brass bracelet.",
        "garments": [{
            "wording": "a brass bracelet",
            "aside": "a brass bracelet resting beside the wrist",
        }],
    })
    profile = saved_looks.publish_automation_profile(
        saved["key"], saved["version"], saved["content_digest"], 0, _profile(saved),
    )
    latest = saved_looks.create_version(saved["key"], saved["version"], {
        "name": saved["name"],
        "appearance": "A different portrait in a quiet room.",
        "garments": [{"wording": "a silver necklace", "aside": ""}],
    })
    return saved, profile, latest


def _wait_for_completion(client, session_id: int, run_id: str) -> dict:
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        response = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
        assert response.status_code == 200, response.text
        status = response.json()
        if status["state"] in _TERMINAL_STATES:
            return status
        time.sleep(0.01)
    pytest.fail("The isolated Full Auto run did not reach a terminal state within 600 seconds.")


def test_browser_contract_supports_one_authorized_40_take_full_auto_run(
    client, seeded, monkeypatch, tmp_path,
):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.setitem(main.CONFIG, "llm_url", ASSISTANT_URL)
    monkeypatch.setitem(main.CONFIG, "llm_model", "task7-1-synthetic-assistant")
    monkeypatch.setitem(main.CONFIG, "llm_key", "")
    monkeypatch.setitem(main.CONFIG, "comfy_url", COMFY_URL)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setattr(main, "_schedule_full_auto_run", _REAL_SCHEDULE_FULL_AUTO_RUN)
    monkeypatch.setattr(runner_module, "POLL_SECONDS", 0.001)

    output_dir = tmp_path / "comfy-output"
    sessions_dir = tmp_path / "sessions"
    output_dir.mkdir()
    monkeypatch.setitem(main.CONFIG, "comfy_output_dir", str(output_dir))
    monkeypatch.setattr(main, "COMFY_OUTPUT", output_dir)
    monkeypatch.setattr(main, "SESSIONS_DIR", sessions_dir)
    fake_comfy = SyntheticTransport(output_dir)
    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        isolated_async_client_factory(fake_comfy, original_async_client),
    )
    comfy_client = Comfy(COMFY_URL)
    monkeypatch.setattr(main, "comfy", comfy_client)
    monkeypatch.setattr(main, "runner", Runner(comfy_client, sessions_dir, output_dir))

    workflow_id = task31._install_t2i_workflow(client, seeded)
    anchor = task31._room_anchor()
    saved, profile, latest = _versioned_accessory_look()
    assert latest["version"] > saved["version"]
    base = task31._full_auto_base(
        seeded, anchor, saved, profile, photo_count=40,
    )
    before_preflight = task31._allocation_counts()
    consent = task31._preflight(client, base)
    assert task31._allocation_counts() == before_preflight

    created, request = task31._create_from_preflight(client, base, consent)
    assert created.status_code == 201, created.text
    session_id = created.json()["session_id"]
    run_id = created.json()["full_auto_run_id"]
    plan = created.json()["plan"]
    take_ids = [take["take_id"] for take in plan["takes"]]
    assert len(take_ids) == 40
    assert "take_ids" not in request and "takes" not in request
    assert created.json()["plan"]["authoring"]["look_snapshot"] == task31._expected_snapshot(saved)

    initial_authorization = db.one(
        "SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id,
    )
    assert initial_authorization is not None
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_run_receipt WHERE session_id = ?", session_id,
    )["n"] == 1
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_run_authorization WHERE run_id = ?", run_id,
    )["n"] == 0
    assert db.one(
        "SELECT COUNT(*) AS n FROM guided_session_request WHERE session_id = ?", session_id,
    )["n"] == 1

    profile_evidence = db.one(
        "SELECT * FROM session_profile_evidence WHERE session_id = ? AND plan_revision = 1",
        session_id,
    )
    assert profile_evidence is not None
    assert profile_evidence["look_key"] == saved["key"]
    assert profile_evidence["look_version"] == saved["version"]
    assert profile_evidence["look_content_digest"] == saved["content_digest"]
    assert profile_evidence["profile_revision"] == profile["profile_revision"]
    assert profile_evidence["profile_digest"] == profile["profile_digest"]

    final_status = _wait_for_completion(client, session_id, run_id)
    assert final_status["state"] == "succeeded", json.dumps(final_status, indent=2)
    assert final_status["phase"] == "execute"
    progress = final_status["progress"]
    assert progress["target_count"] == 40
    assert progress["prepared_count"] == 40
    assert progress["submitted_count"] == 40
    assert progress["completed_count"] == 40
    assert progress["failed_count"] == progress["cancelled_count"] == 0
    assert progress["unresolved_work_count"] == 0
    assert len(progress["prepared_take_ids"]) == 40
    assert progress["target_take_ids"] == take_ids
    assert len(progress["submitted_shot_ids"]) == 40
    assert len(progress["completed_shot_ids"]) == 40

    run_count = db.one(
        "SELECT COUNT(*) AS n FROM full_auto_run WHERE session_id = ?", session_id,
    )["n"]
    assert run_count == 1
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    )["n"] == 1
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    )["n"] == 1

    children = db.q(
        "SELECT batch_index, target_ids_json FROM full_auto_prepare_child "
        "WHERE run_id = ? ORDER BY batch_index", run_id,
    )
    batches = [json.loads(row["target_ids_json"]) for row in children]
    assert [row["batch_index"] for row in children] == [0, 1]
    assert [len(batch) for batch in batches] == [20, 20]
    assert [take_id for batch in batches for take_id in batch] == take_ids

    evidence_rows = db.q(
        "SELECT take_id, stage_index FROM session_take_coverage_evidence "
        "WHERE session_id = ? AND plan_revision = 1", session_id,
    )
    stage_by_take = {row["take_id"]: row["stage_index"] for row in evidence_rows}
    stage_count = 3
    expected_stages = [index * (stage_count - 1) // (len(take_ids) - 1) for index in range(len(take_ids))]
    actual_stages = [stage_by_take[take_id] for take_id in take_ids]
    assert actual_stages == expected_stages
    assert actual_stages[0] == 0 and actual_stages[-1] == stage_count - 1
    assert set(actual_stages) == set(range(stage_count))

    expected_wardrobes = [
        "She wears a brass bracelet.",
        "She wears a brass bracelet resting beside the wrist.",
        "She wears nothing at all.",
    ]
    shots = db.q(
        "SELECT receipt.target_index, receipt.take_id, receipt.prepared_take_id, receipt.shot_id, "
        "prepared.final_prompt, prepared.effective_state, shot.prompt, shot.status, shot.filename "
        "FROM full_auto_submission_receipt receipt "
        "JOIN prepared_take prepared ON prepared.id = receipt.prepared_take_id "
        "JOIN shot ON shot.id = receipt.shot_id "
        "WHERE receipt.run_id = ? ORDER BY receipt.target_index", run_id,
    )
    attempts = db.q(
        "SELECT * FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY shot_id", run_id,
    )
    assert len(shots) == len(attempts) == 40
    assert [row["take_id"] for row in shots] == take_ids
    assert len({row["prepared_take_id"] for row in shots}) == 40
    assert len({row["shot_id"] for row in shots}) == 40
    assert len({row["attempt_id"] for row in attempts}) == 40
    assert len({row["filename_prefix"] for row in attempts}) == 40
    assert len({row["prompt_id"] for row in attempts}) == 40
    assert len({row["filename"] for row in shots}) == 40

    for take_index, row in enumerate(shots):
        stage_index = expected_stages[take_index]
        effective = json.loads(row["effective_state"])
        assert effective["wardrobe"] == expected_wardrobes[stage_index]
        assert effective["wardrobe_coverage"] == _COVERAGE
        assert row["final_prompt"] == row["prompt"]
        assert row["final_prompt"].count(_COVERAGE) == 1
        if stage_index == 2:
            assert "brass bracelet" not in row["final_prompt"].lower()
        else:
            assert "brass bracelet" in row["final_prompt"].lower()
        assert row["status"] == "done"
        assert (sessions_dir / str(session_id) / row["filename"]).is_file()

    assert all(
        attempt["dispatch_state"] == "acknowledged"
        and attempt["completion_state"] == "completed"
        and attempt["client_prompt_id"] == attempt["prompt_id"]
        for attempt in attempts
    )
    assert [call["take_id"] for call in fake_comfy.assistant_calls] == take_ids
    assert len(fake_comfy.assistant_calls) == 40
    assert [prompt["filename_prefix"] for prompt in fake_comfy.comfy_prompts] == [
        attempt["filename_prefix"] for attempt in attempts
    ]
    assert len(fake_comfy.comfy_prompts) == 40
    workflow = db.one("SELECT node_map FROM workflow WHERE id = ?", workflow_id)
    assert workflow is not None
    positive_path = json.loads(workflow["node_map"]).get("positive")
    assert isinstance(positive_path, str) and positive_path
    node_id, _, input_name = positive_path.partition(".")
    input_name = input_name.removeprefix("inputs.")
    queued_positive_prompts = [
        item["graph"][node_id]["inputs"][input_name]
        for item in fake_comfy.comfy_prompts
    ]
    assert queued_positive_prompts == [row["final_prompt"] for row in shots]
