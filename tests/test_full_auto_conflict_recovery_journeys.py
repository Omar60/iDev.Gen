"""Connected synthetic-provider journeys for Full Auto conflicts and recovery."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

import db
import main
import resource_store
import runner as runner_module
from backend import saved_looks
from backend import resource_full_auto
from comfy import Comfy
from runner import Runner
from scripts.task7_1_full_auto_browser_demo import (
    ASSISTANT_URL,
    COMFY_URL,
    SyntheticTransport,
    _profile as _accessory_profile,
    _write_config,
    isolated_async_client_factory,
)
from test_full_auto_run_independent import _all_rows
import test_task31_acceptance as task31
from test_automation_profile_readiness_independent import _profile as _ready_profile


_REAL_SCHEDULE_FULL_AUTO_RUN = main._schedule_full_auto_run
_REAL_SCHEDULE_FULL_AUTO_CANCELLATION = main._schedule_full_auto_cancellation
_TERMINAL_STATES = {"cancelled", "failed", "succeeded", "completed_with_errors"}


class ControlledTransport(SyntheticTransport):
    """Synthetic providers with optional async response barriers and duplicates."""

    def __init__(self, output_dir, *, pause_call: int | None = None, duplicate=False):
        super().__init__(output_dir)
        self.pause_call = pause_call
        self.duplicate = duplicate
        self.assistant_reached = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    async def __call__(self, request: httpx.Request):
        if request.url.host == "synthetic-assistant.invalid" and request.method == "POST":
            self.assistant_reached += 1
            if self.assistant_reached == self.pause_call:
                self.entered.set()
                if not await asyncio.to_thread(self.release.wait, 10):
                    raise TimeoutError("The controlled assistant response was not released.")
            response = super().__call__(request)
            if self.duplicate and request.url.path.endswith("/chat/completions"):
                payload = response.json()
                generated = json.loads(payload["choices"][0]["message"]["content"])
                payload["choices"][0]["message"]["content"] = json.dumps({
                    field: f"Shared synthetic {field} choice" for field in generated
                })
                return self._response(request, 200, payload)
            return response
        return super().__call__(request)


def _connect(client, seeded, monkeypatch, tmp_path, providers):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.setitem(main.CONFIG, "llm_url", ASSISTANT_URL)
    monkeypatch.setitem(main.CONFIG, "llm_model", "task7-2-synthetic-assistant")
    monkeypatch.setitem(main.CONFIG, "llm_key", "")
    monkeypatch.setitem(main.CONFIG, "comfy_url", COMFY_URL)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setattr(main, "_schedule_full_auto_run", _REAL_SCHEDULE_FULL_AUTO_RUN)
    monkeypatch.setattr(
        main, "_schedule_full_auto_cancellation", _REAL_SCHEDULE_FULL_AUTO_CANCELLATION,
    )
    monkeypatch.setattr(runner_module, "POLL_SECONDS", 0.001)

    output_dir = tmp_path / "comfy-output"
    sessions_dir = tmp_path / "sessions"
    output_dir.mkdir(exist_ok=True)
    providers.output_dir = output_dir
    monkeypatch.setitem(main.CONFIG, "comfy_output_dir", str(output_dir))
    monkeypatch.setattr(main, "COMFY_OUTPUT", output_dir)
    monkeypatch.setattr(main, "SESSIONS_DIR", sessions_dir)
    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        isolated_async_client_factory(providers, original_async_client),
    )
    comfy = Comfy(COMFY_URL)
    monkeypatch.setattr(main, "comfy", comfy)
    monkeypatch.setattr(main, "runner", Runner(comfy, sessions_dir, output_dir))
    workflow_id = task31._install_t2i_workflow(client, seeded)
    assert db.one("SELECT id FROM workflow WHERE id = ?", workflow_id) is not None
    return output_dir, sessions_dir


def _create_look(client, *, appearance: str, wording: str | None, aside: str = ""):
    body = {"name": f"Task 7.2 look {uuid.uuid4().hex}", "appearance": appearance}
    if wording is not None:
        body["garments"] = [{"wording": wording, "aside": aside}]
    response = client.post("/api/looks", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _publish(client, saved: dict, profile: dict, expected_revision: int):
    response = client.post("/api/looks/automation-profiles", json={
        "look_key": saved["key"],
        "version": saved["version"],
        "content_digest": saved["content_digest"],
        "expected_profile_revision": expected_revision,
        "profile": profile,
    })
    assert response.status_code == 200, response.text
    return response.json()


def _set_profile_revision(profile: dict, revision: int) -> dict:
    profile = copy.deepcopy(profile)
    provenances = [
        profile["authored_provenance"],
        profile["inventory_complete"]["provenance"],
        profile["baseline_coverage"]["provenance"],
        *(item["provenance"] for item in profile["appearance_segments"]),
        *(
            fact["provenance"]
            for states in profile["garment_coverage"].values()
            for fact in states.values()
        ),
    ]
    for provenance in provenances:
        provenance["accepted_revision"] = revision
    return profile


def _room_source():
    library_key = f"task72-room-{uuid.uuid4().hex}"
    source_id = "scene-001"
    library_id = resource_store.ensure_library(library_key, kind="rooms")
    revision_id = resource_store.record_revision(
        library_id,
        source_id,
        {"id": source_id, "label": "invented studio", "scene_theme": "A quiet studio."},
        translation={"label": "invented studio", "scene_theme": "A quiet studio."},
    )
    revision = resource_store.get_revision(revision_id=revision_id)
    assert revision is not None
    anchor = {
        "library_key": library_key,
        "source_id": source_id,
        "content_digest": revision["content_digest"],
    }
    return anchor, revision_id


def _payload(seeded, anchor, saved, profile, *, photo_count=4):
    return task31._full_auto_base(
        seeded, anchor, saved, profile, photo_count=photo_count,
    )


def _start(client, base):
    consent = task31._preflight(client, base)
    response, payload = task31._create_from_preflight(client, base, consent)
    assert response.status_code == 201, response.text
    return response.json(), payload


def _wait_for_state(client, session_id, run_id, states, timeout=15):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest["state"] in states:
            return latest
        time.sleep(0.01)
    pytest.fail(f"Full Auto run did not reach {sorted(states)}: {latest}")


def _control_body(status):
    return {
        "request_id": str(uuid.uuid4()),
        "expected_run_revision": status["revision"],
        "expected_plan_revision": status["plan_revision"],
    }


def _full_body_payload(seeded, anchor, saved, profile, *, photo_count=3):
    payload = _payload(seeded, anchor, saved, profile, photo_count=photo_count)
    payload["variation_policy"] = copy.deepcopy(task31.ALL_VARY)
    payload["full_auto"]["framing_intent"] = "full_body"
    return payload


def _cancel_full_body_run_after_first_image(
    client, seeded, monkeypatch, tmp_path,
):
    providers = ControlledTransport(tmp_path / "provider-output")
    _connect(client, seeded, monkeypatch, tmp_path, providers)
    anchor, _revision_id = _room_source()
    saved = saved_looks.create({
        "name": "Task 7.4 full-body resume",
        "appearance": "A quiet portrait.",
    })
    published = _publish(client, saved, _ready_profile(saved), 0)
    base = _full_body_payload(seeded, anchor, saved, published)
    consent = task31._preflight(client, base)

    second_shot_entered = threading.Event()
    release_second_shot = threading.Event()
    original_run_shot = Runner._run_shot
    run_shot_calls = 0

    async def hold_second_shot(self, session_id, shot, heartbeat=None):
        nonlocal run_shot_calls
        run_shot_calls += 1
        if run_shot_calls == 2:
            second_shot_entered.set()
            if not await asyncio.to_thread(release_second_shot.wait, 10):
                raise TimeoutError("The controlled second shot was not released.")
        return await original_run_shot(self, session_id, shot, heartbeat)

    monkeypatch.setattr(Runner, "_run_shot", hold_second_shot)
    created_response, request = task31._create_from_preflight(client, base, consent)
    assert created_response.status_code == 201, created_response.text
    session_id = created_response.json()["session_id"]
    run_id = created_response.json()["full_auto_run_id"]
    try:
        assert second_shot_entered.wait(timeout=10), "the second render boundary was not reached"
        shots_before_cancel = [dict(row) for row in db.q(
            "SELECT id, status FROM shot WHERE session_id = ? ORDER BY id",
            session_id,
        )]
        assert [row["status"] for row in shots_before_cancel] == ["done", "pending", "pending"]
        assert len(providers.comfy_prompts) == 1

        current = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
        cancelled = client.post(
            f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
            json=_control_body(current),
        )
        assert cancelled.status_code in {200, 202}, cancelled.text
    finally:
        release_second_shot.set()

    stopped = _wait_for_state(client, session_id, run_id, {"cancelled"})
    assert stopped["progress"]["completed_count"] == 1
    assert stopped["progress"]["unresolved_work_count"] == 0
    assert len(providers.comfy_prompts) == 1
    prepared_before = [dict(row) for row in db.q(
        "SELECT id, take_id, final_prompt, effective_state, mapping_version, "
        "compiler_version, provenance FROM prepared_take "
        "WHERE session_id = ? ORDER BY take_id",
        session_id,
    )]
    assert len(prepared_before) == 3
    assert [row["id"] for row in db.q(
        "SELECT id FROM shot WHERE session_id = ? ORDER BY id", session_id,
    )] == [row["id"] for row in shots_before_cancel]
    return {
        "providers": providers,
        "session_id": session_id,
        "run_id": run_id,
        "stopped": stopped,
        "prepared_before": prepared_before,
        "shots_before_cancel": shots_before_cancel,
        "request": request,
        "saved": saved,
    }


def _renew_expired_full_body_authorization(client, monkeypatch, run):
    session_id, run_id = run["session_id"], run["run_id"]
    root = db.one(
        "SELECT authorization_expires_at FROM full_auto_run_receipt WHERE run_id = ?",
        run_id,
    )
    future = (
        datetime.fromisoformat(root["authorization_expires_at"])
        + timedelta(seconds=1)
    ).isoformat(timespec="seconds")
    monkeypatch.setattr(db, "now", lambda: future)

    renewal_quote_response = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert renewal_quote_response.status_code == 200, renewal_quote_response.text
    renewal_quote = renewal_quote_response.json()
    assert renewal_quote["changed"] is True
    assert "request_digest" not in renewal_quote["changed_fields"]

    current = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
    body = _control_body(current)
    body.update({
        "scope_digest": renewal_quote["scope_digest"],
        "comparison_issued_at": renewal_quote["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    monkeypatch.setattr(main, "_schedule_full_auto_run", lambda *_args, **_kwargs: None)
    resumed = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=body,
    )
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["run_id"] == run_id
    assert db.q(
        "SELECT authorization_sequence FROM full_auto_run_authorization "
        "WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    ) == [{"authorization_sequence": 1}]

    next_quote_response = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert next_quote_response.status_code == 200, next_quote_response.text
    next_quote = next_quote_response.json()
    assert next_quote["changed"] is False
    assert next_quote["changed_fields"] == []
    assert next_quote["old_scope"]["summary"] == next_quote["new_scope"]["summary"]
    assert len(run["providers"].comfy_prompts) == 1
    return next_quote


def _assert_full_row_transition(before: dict, after: dict, allowed_fields, *, expected=None):
    """Compare every stored column and permit only named lifecycle changes."""
    assert set(before) == set(after)
    changed = {field for field in before if before[field] != after[field]}
    assert changed <= set(allowed_fields), f"Unexpected row changes: {sorted(changed)}"
    for field, value in (expected or {}).items():
        assert after[field] == value, f"Unexpected {field}: {after[field]!r}"


def _process_restart_history(connection, session_id: int, run_id: str) -> dict:
    queries = {
        "creation": ("SELECT * FROM guided_session_request WHERE session_id = ?", (session_id,)),
        "run_receipt": ("SELECT * FROM full_auto_run_receipt WHERE run_id = ?", (run_id,)),
        "scope_input": ("SELECT * FROM full_auto_run_scope_input WHERE run_id = ?", (run_id,)),
        "authorization": (
            "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
            (run_id,),
        ),
        "actions": (
            "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ? ORDER BY request_id",
            (run_id,),
        ),
        "plan": ("SELECT * FROM session_plan WHERE session_id = ?", (session_id,)),
        "approval": ("SELECT * FROM full_auto_plan_approval WHERE run_id = ?", (run_id,)),
        "duplicate_acceptance": (
            "SELECT * FROM full_auto_duplicate_review_acceptance WHERE run_id = ?",
            (run_id,),
        ),
        "prepare_progress": (
            "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", (run_id,),
        ),
        "prepare_children": (
            "SELECT * FROM full_auto_prepare_child WHERE run_id = ? ORDER BY batch_index",
            (run_id,),
        ),
        "authoring_operations": (
            "SELECT * FROM authoring_operation WHERE full_auto_run_id = ? ORDER BY operation_id",
            (run_id,),
        ),
        "assistant_reservations": (
            "SELECT * FROM full_auto_assistant_reservation WHERE run_id = ? ORDER BY reservation_id",
            (run_id,),
        ),
        "prepared_takes": (
            "SELECT * FROM prepared_take WHERE session_id = ? ORDER BY id", (session_id,),
        ),
        "submission_receipts": (
            "SELECT * FROM full_auto_submission_receipt WHERE run_id = ? ORDER BY target_index",
            (run_id,),
        ),
    }
    return {
        name: [dict(row) for row in connection.execute(query, parameters)]
        for name, (query, parameters) in queries.items()
    }


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _spawn_recovery_worker(tmp_path: Path, port: int, *, hold_first_prompt: bool):
    script = Path(__file__).resolve().parents[1] / "scripts" / "task7_2_full_auto_recovery_worker.py"
    output_dir = tmp_path / "process-comfy-output"
    output_dir.mkdir(exist_ok=True)
    ledger = tmp_path / "synthetic-remote" / "accepted-prompts.jsonl"
    accepted_marker = tmp_path / "synthetic-remote" / "accepted.marker"
    release_marker = tmp_path / "synthetic-remote" / "release.marker"
    command = [
        sys.executable, str(script),
        "--port", str(port),
        "--output-dir", str(output_dir),
        "--ledger", str(ledger),
        "--accepted-marker", str(accepted_marker),
        "--release-marker", str(release_marker),
    ]
    if hold_first_prompt:
        command.append("--hold-first-prompt")
    log_path = tmp_path / f"backend-worker-{port}.log"
    log = log_path.open("ab")
    process = subprocess.Popen(
        command,
        cwd=script.parents[1],
        env=os.environ.copy(),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    return process, log, ledger, accepted_marker, log_path


def _wait_for_worker(base_url: str, process: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + 15
    with httpx.Client(timeout=0.25) as remote:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(
                    f"Backend worker exited with {process.returncode}: "
                    f"{log_path.read_text(encoding='utf-8', errors='replace')}"
                )
            try:
                response = remote.get(f"{base_url}/api/config")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
    raise AssertionError(
        f"Backend worker did not become ready: "
        f"{log_path.read_text(encoding='utf-8', errors='replace')}"
    )


def test_accessory_ownership_refusal_then_corrected_profile_reaches_final_prompt(
    client, seeded, monkeypatch, tmp_path,
):
    providers = ControlledTransport(tmp_path / "provider-output")
    output_dir, sessions_dir = _connect(client, seeded, monkeypatch, tmp_path, providers)
    anchor, _revision_id = _room_source()
    appearance = "A portrait in a quiet studio. She wears a brass bracelet."
    saved = _create_look(
        client, appearance=appearance, wording="a brass bracelet",
        aside="a brass bracelet resting beside the wrist",
    )

    bad_profile = _accessory_profile(saved)
    bad_segment = next(
        segment for segment in bad_profile["appearance_segments"]
        if segment["segment_id"] == "appearance-bracelet"
    )
    bad_segment["owner"] = "permanent"
    rejected_profile = _publish(client, saved, bad_profile, 0)
    bad_base = _payload(seeded, anchor, saved, rejected_profile)
    before_refusal = _all_rows()

    refused = client.post("/api/sessions/guided/full-auto/preflight", json=bad_base)

    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "appearance_progression_conflict"
    assert _all_rows() == before_refusal
    assert providers.assistant_calls == providers.comfy_prompts == []

    corrected = _publish(
        client, saved, _set_profile_revision(_accessory_profile(saved), 2), 1,
    )
    run_result, _request = _start(
        client, _payload(seeded, anchor, saved, corrected),
    )
    session_id, run_id = run_result["session_id"], run_result["full_auto_run_id"]
    result = _wait_for_state(client, session_id, run_id, _TERMINAL_STATES)

    assert result["state"] == "succeeded", json.dumps(result, indent=2)
    shots = db.q(
        "SELECT shot.prompt, shot.status, receipt.target_index "
        "FROM full_auto_submission_receipt receipt JOIN shot ON shot.id = receipt.shot_id "
        "WHERE receipt.run_id = ? ORDER BY receipt.target_index", run_id,
    )
    assert len(shots) == 4
    assert all(row["status"] == "done" for row in shots)
    assert "brass bracelet" not in shots[-1]["prompt"].lower()
    assert "brass bracelet" in shots[0]["prompt"].lower()
    assert len(providers.assistant_calls) == 4
    assert len(providers.comfy_prompts) == 4
    assert all((sessions_dir / str(session_id) / row["filename"]).is_file() for row in db.q(
        "SELECT filename FROM shot WHERE session_id = ?", session_id,
    ))
    assert output_dir.is_dir()


def test_source_drift_refuses_stale_consent_without_allocation_then_refreshes(
    client, seeded, monkeypatch, tmp_path,
):
    providers = ControlledTransport(tmp_path / "provider-output")
    _connect(client, seeded, monkeypatch, tmp_path, providers)
    anchor, revision_id = _room_source()
    saved = saved_looks.create({"name": "Task 7.2 source drift", "appearance": "A quiet portrait."})
    published = _publish(client, saved, _ready_profile(saved), 0)
    base = _payload(seeded, anchor, saved, published, photo_count=2)
    consent = task31._preflight(client, base)

    resource_store.update_translation(revision_id, {
        "label": "invented studio", "scene_theme": "A changed quiet studio.",
    })
    before_stale_create = _all_rows()
    stale_payload = copy.deepcopy(base)
    stale_payload["request_id"] = str(uuid.uuid4())
    stale_payload["full_auto"].update({
        "scope_digest": consent["scope_digest"],
        "scope_issued_at": consent["scope_issued_at"],
    })

    stale = client.post("/api/sessions/guided", json=stale_payload)

    detail = task31._assert_error(stale, 409, "authorization_scope_changed")
    assert detail["current"]["scope_digest"] != consent["scope_digest"]
    assert _all_rows() == before_stale_create
    assert providers.assistant_calls == providers.comfy_prompts == []

    refreshed = task31._preflight(client, base)
    accepted, _request = task31._create_from_preflight(client, base, refreshed)
    assert accepted.status_code == 201, accepted.text
    session_id, run_id = accepted.json()["session_id"], accepted.json()["full_auto_run_id"]
    result = _wait_for_state(client, session_id, run_id, _TERMINAL_STATES)
    assert result["state"] == "succeeded", json.dumps(result, indent=2)
    assert len(providers.assistant_calls) == 2
    assert len(providers.comfy_prompts) == 2


def test_successful_run_with_consumed_image_budget_has_no_budget_blocker(
    client, seeded, monkeypatch, tmp_path,
):
    providers = ControlledTransport(tmp_path / "provider-output")
    _connect(client, seeded, monkeypatch, tmp_path, providers)
    anchor, _revision_id = _room_source()
    saved = saved_looks.create({
        "name": "Task 7.2 completed budget",
        "appearance": "A quiet portrait.",
    })
    published = _publish(client, saved, _ready_profile(saved), 0)
    created, _request = _start(
        client, _payload(seeded, anchor, saved, published, photo_count=2),
    )

    result = _wait_for_state(
        client, created["session_id"], created["full_auto_run_id"], _TERMINAL_STATES,
    )

    assert result["state"] == "succeeded", json.dumps(result, indent=2)
    assert result["progress"]["completed_count"] == 2
    assert result["progress"]["unresolved_work_count"] == 0
    assert result["allowances"]["image_attempts_consumed"] == 2
    assert result["allowances"]["image_attempts_remaining_lower_bound"] == 0
    assert result["blocker_code"] is None
    assert result["blocker"] is None
    assert result["can_resume"] is False
    assert result["retry_control"]["can_retry_failed_shots"] is False
    assert len(providers.comfy_prompts) == 2


def test_full_body_vary_request_resumes_generated_prefix_without_duplicate_dispatch(
    client, seeded, monkeypatch, tmp_path,
):
    run = _cancel_full_body_run_after_first_image(
        client, seeded, monkeypatch, tmp_path,
    )
    providers = run["providers"]
    session_id, run_id = run["session_id"], run["run_id"]
    assert run["request"]["variation_policy"]["framing"] == {"mode": "vary"}

    before_comparison = _all_rows()
    response = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )

    assert response.status_code == 200, response.text
    comparison = response.json()
    assert comparison["changed"] is False
    assert comparison["changed_fields"] == []
    assert comparison["old_scope"]["summary"] == comparison["new_scope"]["summary"]
    assert _all_rows() == before_comparison

    assistant_calls_before_resume = list(providers.assistant_calls)
    current = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
    resume_body = _control_body(current)
    resume_body.update({
        "scope_digest": comparison["scope_digest"],
        "comparison_issued_at": comparison["comparison_issued_at"],
        "accept_changed_scope": False,
    })
    resumed = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=resume_body,
    )
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["run_id"] == run_id
    assert resumed.json()["session_id"] == session_id

    completed = _wait_for_state(client, session_id, run_id, {"succeeded"})

    assert completed["progress"]["completed_count"] == 3
    assert completed["progress"]["unresolved_work_count"] == 0
    assert providers.assistant_calls == assistant_calls_before_resume
    assert len(providers.comfy_prompts) == 3
    prompt_ids = [item["prompt_id"] for item in providers.comfy_prompts]
    assert len(prompt_ids) == len(set(prompt_ids))
    shots_after_resume = [dict(row) for row in db.q(
        "SELECT id, status FROM shot WHERE session_id = ? ORDER BY id",
        session_id,
    )]
    assert [row["id"] for row in shots_after_resume] == [
        row["id"] for row in run["shots_before_cancel"]
    ]
    assert all(row["status"] == "done" for row in shots_after_resume)
    prepared_after_resume = [dict(row) for row in db.q(
        "SELECT id, take_id, final_prompt, effective_state, mapping_version, "
        "compiler_version, provenance FROM prepared_take "
        "WHERE session_id = ? ORDER BY take_id",
        session_id,
    )]
    assert prepared_after_resume == run["prepared_before"]


def test_full_body_vary_request_remains_unchanged_after_expiry_renewal(
    client, seeded, monkeypatch, tmp_path,
):
    run = _cancel_full_body_run_after_first_image(
        client, seeded, monkeypatch, tmp_path,
    )

    quote = _renew_expired_full_body_authorization(client, monkeypatch, run)

    assert quote["old_scope"]["summary"]["request_digest"] == quote["new_scope"]["summary"]["request_digest"]
    assert quote["new_scope"]["summary"]["effective_framing"] == {
        "mode": "fixed",
        "value": "Full body, head to toe, both feet visible, with space above the head and below the feet.",
        "value_origin": "user",
    }
    assert len(run["providers"].assistant_calls) == 3
    assert len(run["providers"].comfy_prompts) == 1


@pytest.mark.parametrize(
    ("drift", "expected_code"),
    [
        ("framing", "framing_conflict"),
        ("pose", "full_auto_continuity_requires_new_session"),
        ("model", "full_auto_continuity_requires_new_session"),
        ("profile", "profile_frozen"),
    ],
)
def test_full_body_generated_history_refuses_current_input_drift_after_renewal_without_writes(
    client, seeded, monkeypatch, tmp_path, drift, expected_code,
):
    run = _cancel_full_body_run_after_first_image(
        client, seeded, monkeypatch, tmp_path,
    )
    session_id, run_id = run["session_id"], run["run_id"]
    _renew_expired_full_body_authorization(client, monkeypatch, run)
    if drift in {"framing", "pose"}:
        row = db.one("SELECT plan_json FROM session_plan WHERE session_id = ?", session_id)
        plan = json.loads(row["plan_json"])
        plan["authoring"]["variation_policy"][drift] = {
            "mode": "fixed",
            "value": "Waist-up portrait." if drift == "framing" else "A crouched pose.",
            "value_origin": "user",
        }
        db.run(
            "UPDATE session_plan SET plan_json = ? WHERE session_id = ?",
            json.dumps(plan, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            session_id,
        )
    elif drift == "model":
        model_id = db.one(
            "SELECT model_id FROM session WHERE id = ?", session_id,
        )["model_id"]
        db.run(
            "UPDATE model SET name = ? WHERE id = ?",
            "Changed model binding", model_id,
        )
    else:
        saved = run["saved"]
        profile2 = _publish(
            client, saved, _set_profile_revision(_ready_profile(saved), 2), 1,
        )
        before_profile_change = _all_rows()
        profile_change = client.post(
            f"/api/sessions/{session_id}/plan/apply-automation-profile",
            json={
                "expected_revision": 1,
                "look_key": saved["key"],
                "version": saved["version"],
                "content_digest": saved["content_digest"],
                "profile_revision": profile2["profile_revision"],
                "profile_digest": profile2["profile_digest"],
                "decisions": {"look": "replace", "initial_wardrobe": "replace"},
            },
        )
        task31._assert_error(profile_change, 409, expected_code)
        assert _all_rows() == before_profile_change
        before_comparison = _all_rows()
        unchanged = client.get(
            f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
        )
        assert unchanged.status_code == 200, unchanged.text
        assert unchanged.json()["changed"] is False
        assert unchanged.json()["old_scope"]["summary"] == unchanged.json()["new_scope"]["summary"]
        assert _all_rows() == before_comparison
        assert len(run["providers"].comfy_prompts) == 1
        return
    before_comparison = _all_rows()

    response = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )

    task31._assert_error(response, 409, expected_code)
    assert _all_rows() == before_comparison
    assert len(run["providers"].comfy_prompts) == 1


def test_room_translation_drift_fences_held_response_and_preserves_prepared_prefix(
    client, seeded, monkeypatch, tmp_path,
):
    providers = ControlledTransport(tmp_path / "provider-output", pause_call=2)
    _connect(client, seeded, monkeypatch, tmp_path, providers)
    anchor, revision_id = _room_source()
    saved = saved_looks.create({
        "name": "Task 7.2 runtime room drift",
        "appearance": "A quiet portrait.",
    })
    published = _publish(client, saved, _ready_profile(saved), 0)
    created, _request = _start(
        client, _payload(seeded, anchor, saved, published, photo_count=3),
    )
    session_id, run_id = created["session_id"], created["full_auto_run_id"]
    assert providers.entered.wait(timeout=10), "the second assistant HTTP boundary was not reached"
    prefix_before = [dict(row) for row in db.q(
        "SELECT * FROM prepared_take WHERE session_id = ? ORDER BY id", session_id,
    )]
    assert len(prefix_before) == 1
    assert len(providers.assistant_calls) == 1

    resource_store.update_translation(revision_id, {
        "label": "invented studio", "scene_theme": "A changed quiet studio.",
    })
    providers.release.set()
    stopped = _wait_for_state(client, session_id, run_id, {"blocked"})

    assert stopped["blocker"]["code"] == "dependency_changed"
    assert [dict(row) for row in db.q(
        "SELECT * FROM prepared_take WHERE session_id = ? ORDER BY id", session_id,
    )] == prefix_before
    assert db.q("SELECT * FROM shot WHERE session_id = ?", session_id) == []
    assert db.q(
        "SELECT * FROM full_auto_submission_receipt WHERE run_id = ?", run_id,
    ) == []
    assert len(providers.assistant_calls) == 2
    assert providers.comfy_prompts == []

    comparison_response = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert comparison_response.status_code == 200, comparison_response.text
    comparison = comparison_response.json()
    assert comparison["changed"] is True

    # A new canonical source write invalidates the displayed comparison. The
    # stale Resume must fail before changing any durable row.
    resource_store.update_translation(revision_id, {
        "label": "invented studio", "scene_theme": "A second changed quiet studio.",
    })
    before_stale_resume = _all_rows()
    stale_body = _control_body(stopped)
    stale_body.update({
        "scope_digest": comparison["scope_digest"],
        "comparison_issued_at": comparison["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    stale_resume = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=stale_body,
    )
    task31._assert_error(stale_resume, 409, "resume_comparison_stale")
    assert _all_rows() == before_stale_resume
    assert providers.comfy_prompts == []

    current = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
    cancelled = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
        json=_control_body(current),
    )
    assert cancelled.status_code in {200, 202}, cancelled.text
    cancelled_status = _wait_for_state(client, session_id, run_id, {"cancelled"})
    refreshed = client.post(
        f"/api/sessions/{session_id}/plan/refresh-resources",
        json={"expected_revision": 1},
    )
    assert refreshed.status_code == 200, refreshed.text
    refreshed_body = refreshed.json()
    assert refreshed_body["refreshed"] is True
    assert refreshed_body["plan_revision"] == 2
    invalidated = db.q(
        "SELECT * FROM prepared_take WHERE session_id = ? ORDER BY id", session_id,
    )
    assert len(invalidated) == 1
    assert invalidated[0]["status"] == "invalidated"

    corrected_comparison = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    ).json()
    assert corrected_comparison["changed"] is True

    preserved_tables = {
        "creation": [dict(row) for row in db.q(
            "SELECT * FROM guided_session_request WHERE session_id = ?", session_id,
        )],
        "run_receipt": [dict(row) for row in db.q(
            "SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id,
        )],
        "scope_input": [dict(row) for row in db.q(
            "SELECT * FROM full_auto_run_scope_input WHERE run_id = ?", run_id,
        )],
        "authorization": [dict(row) for row in db.q(
            "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
            run_id,
        )],
        "actions": [dict(row) for row in db.q(
            "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ? ORDER BY request_id",
            run_id,
        )],
        "prepared": [dict(row) for row in invalidated],
        "children": [dict(row) for row in db.q(
            "SELECT * FROM full_auto_prepare_child WHERE run_id = ? ORDER BY batch_index", run_id,
        )],
        "operations": [dict(row) for row in db.q(
            "SELECT * FROM authoring_operation WHERE full_auto_run_id = ? ORDER BY operation_id",
            run_id,
        )],
        "reservations": [dict(row) for row in db.q(
            "SELECT * FROM full_auto_assistant_reservation WHERE run_id = ? ORDER BY reservation_id",
            run_id,
        )],
    }
    assert len(preserved_tables["children"]) == 1
    assert len(preserved_tables["operations"]) == 1
    assert len(preserved_tables["reservations"]) == 2

    corrected_body = _control_body(cancelled_status)
    corrected_body.update({
        "expected_plan_revision": refreshed_body["plan_revision"],
        "scope_digest": corrected_comparison["scope_digest"],
        "comparison_issued_at": corrected_comparison["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    corrected_resume = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=corrected_body,
    )
    assert corrected_resume.status_code == 202, corrected_resume.text
    corrected_status = _wait_for_state(
        client, session_id, run_id,
        {"succeeded"},
    )
    assert corrected_status["progress"]["prepared_count"] == 3
    assert corrected_status["progress"]["submitted_count"] == 3
    assert corrected_status["progress"]["completed_count"] == 3
    assert len(providers.comfy_prompts) == 3
    assert len(providers.assistant_calls) == 5

    preserved_queries = {
        "creation": ("SELECT * FROM guided_session_request WHERE session_id = ?", session_id, "request_id"),
        "run_receipt": ("SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id, "run_id"),
        "scope_input": ("SELECT * FROM full_auto_run_scope_input WHERE run_id = ?", run_id, "run_id"),
        "authorization": (
            "SELECT * FROM full_auto_run_authorization WHERE run_id = ?", run_id,
            "authorization_sequence",
        ),
        "actions": (
            "SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
            "request_id",
        ),
        "prepared": ("SELECT * FROM prepared_take WHERE session_id = ?", session_id, "id"),
        "children": ("SELECT * FROM full_auto_prepare_child WHERE run_id = ?", run_id, "batch_index"),
        "operations": ("SELECT * FROM authoring_operation WHERE full_auto_run_id = ?", run_id, "operation_id"),
        "reservations": (
            "SELECT * FROM full_auto_assistant_reservation WHERE run_id = ?", run_id,
            "reservation_id",
        ),
    }
    for table, (query, identity, key) in preserved_queries.items():
        after = {row[key]: dict(row) for row in db.q(query, identity)}
        for row in preserved_tables[table]:
            assert after.get(row[key]) == row, f"Historical {table} row changed: {row[key]}"

    progress = resource_full_auto._read_prepare_progress(run_id)
    assert progress["plan_revision"] == refreshed_body["plan_revision"]
    assert progress["completed_take_ids"] == progress["target_ids"]
    history = progress["historical_children"]
    assert [child["batch_index"] for child in history] == [0, 1]
    assert [child["plan_revision"] for child in history] == [1, 2]
    current_children = [
        child for child in progress["children"]
        if child["plan_revision"] == progress["plan_revision"]
    ]
    assert len(current_children) == 1
    assert current_children[0]["target_ids"] == progress["target_ids"]
    progress_row = db.one("SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id)
    assert progress_row["plan_revision"] == refreshed_body["plan_revision"]
    assert json.loads(progress_row["completed_ids_json"]) == progress["target_ids"]


def test_resume_quote_expiry_during_prefix_validation_is_no_write(
    client, seeded, monkeypatch, tmp_path,
):
    from backend import guided_sessions

    providers = ControlledTransport(tmp_path / "provider-output", pause_call=2)
    _connect(client, seeded, monkeypatch, tmp_path, providers)
    anchor, revision_id = _room_source()
    saved = saved_looks.create({
        "name": "Task 7.2 expired resume quote",
        "appearance": "A quiet portrait.",
    })
    published = _publish(client, saved, _ready_profile(saved), 0)
    created, _request = _start(
        client, _payload(seeded, anchor, saved, published, photo_count=3),
    )
    session_id, run_id = created["session_id"], created["full_auto_run_id"]
    assert providers.entered.wait(timeout=10), "the second assistant response was not held"

    resource_store.update_translation(revision_id, {
        "label": "invented studio", "scene_theme": "A changed quiet studio.",
    })
    providers.release.set()
    stopped = _wait_for_state(client, session_id, run_id, {"blocked"})
    assert stopped["blocker"]["code"] == "dependency_changed"

    current = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
    cancelled = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
        json=_control_body(current),
    )
    assert cancelled.status_code in {200, 202}, cancelled.text
    cancelled_status = _wait_for_state(client, session_id, run_id, {"cancelled"})
    refreshed = client.post(
        f"/api/sessions/{session_id}/plan/refresh-resources",
        json={"expected_revision": 1},
    )
    assert refreshed.status_code == 200, refreshed.text
    comparison = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    ).json()
    assert comparison["changed"] is True

    expired_at = (
        datetime.fromisoformat(comparison["comparison_issued_at"])
        + guided_sessions._FULL_AUTO_SCOPE_MAX_AGE
        + timedelta(seconds=1)
    ).isoformat(timespec="seconds")
    original_verify = resource_full_auto._verified_prepared_prefix

    def expire_quote_after_validation(*args, **kwargs):
        result = original_verify(*args, **kwargs)
        monkeypatch.setattr(
            resource_full_auto, "_now_text", lambda _value=None: expired_at,
        )
        return result

    monkeypatch.setattr(
        resource_full_auto, "_verified_prepared_prefix", expire_quote_after_validation,
    )
    monkeypatch.setattr(main, "_schedule_full_auto_run", lambda *_args, **_kwargs: None)
    before = _all_rows()
    body = _control_body(cancelled_status)
    body.update({
        "expected_plan_revision": refreshed.json()["plan_revision"],
        "scope_digest": comparison["scope_digest"],
        "comparison_issued_at": comparison["comparison_issued_at"],
        "accept_changed_scope": True,
    })

    response = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )

    task31._assert_error(response, 409, "resume_comparison_stale")
    assert _all_rows() == before
    assert providers.comfy_prompts == []


def test_linked_generated_history_refuses_prepare_rebase_without_any_write(
    client, seeded, monkeypatch, tmp_path,
):
    providers = ControlledTransport(tmp_path / "provider-output")
    _connect(client, seeded, monkeypatch, tmp_path, providers)
    anchor, _revision_id = _room_source()
    saved = _create_look(
        client, appearance="A quiet portrait in an invented studio.", wording=None,
    )
    published = _publish(client, saved, _ready_profile(saved), 0)
    created, _request = _start(
        client, _payload(seeded, anchor, saved, published, photo_count=1),
    )
    session_id, run_id = created["session_id"], created["full_auto_run_id"]
    result = _wait_for_state(client, session_id, run_id, {"succeeded"})
    assert result["state"] == "succeeded"
    run_row = dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id))
    progress = resource_full_auto._read_prepare_progress(run_id)
    prepared = [dict(row) for row in db.q(
        "SELECT * FROM prepared_take WHERE session_id = ? ORDER BY id", session_id,
    )]
    assert len(prepared) == 1
    assert prepared[0]["status"] == "generated"
    assert prepared[0]["linked_shot_id"] is not None
    assert db.q("SELECT * FROM full_auto_submission_receipt WHERE run_id = ?", run_id)

    # A real linked run has advanced past prepare. Present its unchanged
    # persisted evidence to the rebase guard as a pre-link candidate so this
    # specific linked/generated-history refusal is exercised without altering
    # the durable run row.
    rebase_candidate = {**run_row, "phase": "prepare", "resume_phase": None}
    before_refusal = _all_rows()
    with pytest.raises(resource_full_auto.FullAutoRunError) as refusal:
        with db.transaction():
            resource_full_auto._rebase_prepare_progress_for_resume(
                rebase_candidate,
                int(run_row["plan_revision"]) + 1,
                progress["target_ids"],
                final_validation=lambda: pytest.fail(
                    "linked/generated history must be refused before final validation",
                ),
            )
    assert refusal.value.code == "full_auto_continuity_requires_new_session"
    assert _all_rows() == before_refusal


def test_provider_drift_discards_inflight_response_and_blocks_stale_output(
    client, seeded, monkeypatch, tmp_path,
):
    providers = ControlledTransport(tmp_path / "provider-output", pause_call=1)
    _connect(client, seeded, monkeypatch, tmp_path, providers)
    anchor, _revision_id = _room_source()
    saved = saved_looks.create({"name": "Task 7.2 provider drift", "appearance": "A quiet portrait."})
    published = _publish(client, saved, _ready_profile(saved), 0)
    created, _request = _start(
        client, _payload(seeded, anchor, saved, published, photo_count=2),
    )
    session_id, run_id = created["session_id"], created["full_auto_run_id"]
    assert providers.entered.wait(timeout=10), "the real assistant HTTP boundary was not reached"
    assert providers.assistant_reached == 1
    before_response = _all_rows()

    monkeypatch.setitem(main.CONFIG, "llm_model", "changed-after-authorization")
    providers.release.set()
    result = _wait_for_state(client, session_id, run_id, {"blocked"})

    assert result["blocker"]["code"] == "provider_changed"
    assert db.q("SELECT * FROM prepared_take WHERE session_id = ?", session_id) == []
    assert len(providers.assistant_calls) == 1
    assert providers.comfy_prompts == []
    assert _all_rows() != before_response


def test_duplicate_review_accepts_current_digest_and_resumes_same_snapshots(
    client, seeded, monkeypatch, tmp_path,
):
    providers = ControlledTransport(tmp_path / "provider-output", duplicate=True)
    _connect(client, seeded, monkeypatch, tmp_path, providers)
    anchor, _revision_id = _room_source()
    saved = saved_looks.create({"name": "Task 7.2 deliberate repeat", "appearance": "A quiet portrait."})
    published = _publish(client, saved, _ready_profile(saved), 0)
    created, _request = _start(
        client, _payload(seeded, anchor, saved, published, photo_count=3),
    )
    session_id, run_id = created["session_id"], created["full_auto_run_id"]
    stopped = _wait_for_state(client, session_id, run_id, {"blocked"})
    assert stopped["blocker"]["code"] == "duplicate_review_required"
    assert stopped["progress"]["prepared_count"] == 3
    assert stopped["progress"]["submitted_count"] == 0
    snapshots_before = _all_rows()["prepared_take"]
    snapshot_identity_before = [dict(row) for row in db.q(
        "SELECT id, take_id, final_prompt, effective_state, mapping_version, "
        "compiler_version, provenance FROM prepared_take "
        "WHERE session_id = ? ORDER BY take_id", session_id,
    )]

    review_response = client.get(f"/api/sessions/{session_id}/plan/review")
    assert review_response.status_code == 200, review_response.text
    review = review_response.json()["full_auto_duplicate_review"]
    assert review["state"] == "duplicate_review_required"
    assert len(review["duplicates"]) == 3
    before_bad_acceptance = _all_rows()
    rejected = client.post(
        f"/api/sessions/{session_id}/plan/review/duplicates/accept",
        json={"plan_revision": stopped["plan_revision"], "review_digest": "0" * 64},
    )
    assert rejected.status_code == 409
    assert _all_rows() == before_bad_acceptance

    accepted = client.post(
        f"/api/sessions/{session_id}/plan/review/duplicates/accept",
        json={
            "plan_revision": stopped["plan_revision"],
            "review_digest": review["review_digest"],
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert _all_rows()["prepared_take"] == snapshots_before
    digest_row = db.one(
        "SELECT plan_revision, target_digest, snapshot_digest, duplicate_set_digest, review_digest "
        "FROM full_auto_duplicate_review_acceptance WHERE run_id = ?", run_id,
    )
    assert digest_row["snapshot_digest"] == review["snapshot_digest"]
    assert digest_row["duplicate_set_digest"] == review["duplicate_set_digest"]
    assert digest_row["review_digest"] == review["review_digest"]

    resumed_status = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
    resumed = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json=_control_body(resumed_status),
    )
    assert resumed.status_code == 202, resumed.text
    result = _wait_for_state(client, session_id, run_id, _TERMINAL_STATES)

    assert result["state"] == "succeeded", json.dumps(result, indent=2)
    assert result["progress"]["prepared_count"] == 3
    assert result["progress"]["completed_count"] == 3
    assert len(providers.assistant_calls) == 3
    assert len(providers.comfy_prompts) == 3
    after = db.q(
        "SELECT final_prompt, effective_state, mapping_version, compiler_version, provenance "
        "FROM prepared_take WHERE session_id = ? ORDER BY take_id", session_id,
    )
    snapshot_identity_after = [dict(row) for row in db.q(
        "SELECT id, take_id, final_prompt, effective_state, mapping_version, "
        "compiler_version, provenance FROM prepared_take "
        "WHERE session_id = ? ORDER BY take_id", session_id,
    )]
    assert snapshot_identity_after == snapshot_identity_before
    assert len(after) == 3
    assert {row["final_prompt"] for row in after} == {
        row["prompt"] for row in db.q(
            "SELECT prompt FROM shot WHERE session_id = ?", session_id,
        )
    }


def test_cancellation_fences_a_late_assistant_response_and_preserves_prefix(
    client, seeded, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        resource_full_auto, "RUNNER_HEARTBEAT_INTERVAL", timedelta(milliseconds=20),
    )
    providers = ControlledTransport(tmp_path / "provider-output", pause_call=2)
    _connect(client, seeded, monkeypatch, tmp_path, providers)
    anchor, _revision_id = _room_source()
    saved = saved_looks.create({"name": "Task 7.2 cancellation", "appearance": "A quiet portrait."})
    published = _publish(client, saved, _ready_profile(saved), 0)
    created, _request = _start(
        client, _payload(seeded, anchor, saved, published, photo_count=3),
    )
    session_id, run_id = created["session_id"], created["full_auto_run_id"]
    assert providers.entered.wait(timeout=10), "the second assistant HTTP boundary was not reached"
    prefix_before = [dict(row) for row in db.q(
        "SELECT * FROM prepared_take WHERE session_id = ? ORDER BY id", session_id,
    )]
    assert len(prefix_before) == 1
    before_cancel = _all_rows()
    status = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
    cancelled = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
        json=_control_body(status),
    )
    assert cancelled.status_code in {200, 202}, cancelled.text
    providers.release.set()
    result = _wait_for_state(client, session_id, run_id, {"cancelled"})

    assert len(providers.assistant_calls) == 2
    assert db.q("SELECT * FROM prepared_take WHERE session_id = ? ORDER BY id", session_id) == prefix_before
    assert db.q("SELECT * FROM shot WHERE session_id = ?", session_id) == []
    assert _all_rows() != before_cancel


def test_expired_run_lease_discards_late_writer_result(client, seeded, monkeypatch, tmp_path):
    providers = ControlledTransport(tmp_path / "provider-output", pause_call=1)
    _connect(client, seeded, monkeypatch, tmp_path, providers)
    anchor, _revision_id = _room_source()
    saved = saved_looks.create({"name": "Task 7.2 expired lease", "appearance": "A quiet portrait."})
    published = _publish(client, saved, _ready_profile(saved), 0)
    created, _request = _start(
        client, _payload(seeded, anchor, saved, published, photo_count=2),
    )
    session_id, run_id = created["session_id"], created["full_auto_run_id"]
    assert providers.entered.wait(timeout=10), "the assistant HTTP boundary was not reached"
    before_expiry = _all_rows()
    prior_owner = db.one(
        "SELECT epoch, fencing_token FROM full_auto_run WHERE run_id = ?", run_id,
    )
    db.run(
        "UPDATE full_auto_run SET lease_expires_at = ? WHERE run_id = ?",
        "2000-01-01T00:00:00+00:00", run_id,
    )
    replacement = resource_full_auto.claim_run(run_id)
    assert replacement.requires_reconciliation is True
    assert replacement.epoch == prior_owner["epoch"]
    assert replacement.fencing_token == prior_owner["fencing_token"] + 1
    assert _all_rows() != before_expiry
    providers.release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        reservation = db.one(
            "SELECT settled_at FROM full_auto_assistant_reservation WHERE run_id = ? "
            "ORDER BY reservation_id DESC LIMIT 1", run_id,
        )
        if reservation and reservation["settled_at"] is not None:
            break
        time.sleep(0.01)
    else:
        pytest.fail("The expired owner's in-flight assistant request did not settle.")

    current = db.one("SELECT epoch, fencing_token, phase FROM full_auto_run WHERE run_id = ?", run_id)
    assert current["epoch"] == replacement.epoch
    assert current["fencing_token"] == replacement.fencing_token
    assert current["phase"] == "reconcile"
    assert db.q("SELECT * FROM prepared_take WHERE session_id = ?", session_id) == []
    assert providers.assistant_reached == 1
    assert len(providers.assistant_calls) == 1
    assert providers.comfy_prompts == []


def test_backend_process_restart_correlates_accepted_unacknowledged_comfy_prompt(
    client, seeded, monkeypatch, tmp_path,
):
    """A real process restart recovers one accepted ComfyUI POST by its remote identity."""
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.setitem(main.CONFIG, "llm_url", ASSISTANT_URL)
    monkeypatch.setitem(main.CONFIG, "llm_model", "task7-2-synthetic-assistant")
    monkeypatch.setitem(main.CONFIG, "llm_key", "")
    monkeypatch.setitem(main.CONFIG, "comfy_url", COMFY_URL)
    output_dir = tmp_path / "process-comfy-output"
    output_dir.mkdir()
    sessions_dir = tmp_path / "process-sessions"
    monkeypatch.setitem(main.CONFIG, "comfy_output_dir", str(output_dir))
    monkeypatch.setattr(main, "COMFY_OUTPUT", output_dir)
    monkeypatch.setattr(main, "SESSIONS_DIR", sessions_dir)
    workflow_id = task31._install_t2i_workflow(client, seeded)
    assert db.one("SELECT id FROM workflow WHERE id = ?", workflow_id) is not None

    anchor, _revision_id = _room_source()
    saved = saved_looks.create({
        "name": "Task 7.2 accepted then lost acknowledgment",
        "appearance": "A quiet portrait.",
    })
    published = _publish(client, saved, _ready_profile(saved), 0)
    created, _request = _start(
        client, _payload(seeded, anchor, saved, published, photo_count=2),
    )
    session_id, run_id = created["session_id"], created["full_auto_run_id"]

    config_path = tmp_path / "backend-config.json"
    _write_config(config_path, main.DATA_DIR, output_dir)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["llm_model"] = "task7-2-synthetic-assistant"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("IDEVGEN_CONFIG", str(config_path))
    monkeypatch.setenv("IDEVGEN_DATA_DIR", str(main.DATA_DIR))
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)

    connection = db.conn()
    database_path = Path(connection.execute("PRAGMA database_list").fetchone()["file"])
    connection.close()
    db._conn = None

    port = _unused_port()
    base_url = f"http://127.0.0.1:{port}"
    worker, worker_log, ledger, accepted, log_path = _spawn_recovery_worker(
        tmp_path, port, hold_first_prompt=True,
    )
    workers = [(worker, worker_log)]
    try:
        _wait_for_worker(base_url, worker, log_path)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not accepted.is_file():
            if worker.poll() is not None:
                pytest.fail(
                    f"The first backend exited before remote acceptance: "
                    f"{log_path.read_text(encoding='utf-8', errors='replace')}"
                )
            time.sleep(0.05)
        assert accepted.is_file(), "The synthetic ComfyUI did not durably accept /prompt."
        first_remote_record = json.loads(accepted.read_text(encoding="utf-8"))
        first_ledger = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        assert first_ledger == [first_remote_record]
        assert first_remote_record["kind"] == "comfy_prompt"
        assert first_remote_record["output_sha256"]
        attempt_marker = first_remote_record["request"]["extra_data"]["idevgen_attempt"]
        assert first_remote_record["prompt_id"] == first_remote_record["request"]["prompt_id"]

        with sqlite3.connect(database_path) as snapshot:
            snapshot.row_factory = sqlite3.Row
            before_restart = dict(snapshot.execute(
                "SELECT * FROM full_auto_run WHERE run_id = ?",
                (run_id,),
            ).fetchone())
            session_before = dict(snapshot.execute(
                "SELECT * FROM session WHERE id = ?", (session_id,),
            ).fetchone())
            attempts_before = [dict(row) for row in snapshot.execute(
                "SELECT * FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY shot_id, attempt_number",
                (run_id,),
            )]
            receipts_before = [dict(row) for row in snapshot.execute(
                "SELECT * FROM full_auto_submission_receipt WHERE run_id = ? ORDER BY target_index",
                (run_id,),
            )]
            shots_before = [dict(row) for row in snapshot.execute(
                "SELECT * FROM shot WHERE session_id = ? ORDER BY id", (session_id,),
            )]
            history_before = _process_restart_history(snapshot, session_id, run_id)
        assert len(attempts_before) == 1
        assert attempts_before[0]["dispatch_state"] == "intent"
        assert attempts_before[0]["completion_state"] == "pending"
        assert len(receipts_before) == 2
        assert len(shots_before) == 2
        assert attempts_before[0]["client_prompt_id"] == first_remote_record["prompt_id"]
        assert attempts_before[0]["attempt_id"] == attempt_marker["attempt_id"]
        assert attempts_before[0]["filename_prefix"] == attempt_marker["filename_prefix"]
        assert attempts_before[0]["graph_digest"] == attempt_marker["graph_digest"]
        worker.kill()
        worker.wait(timeout=10)
        worker_log.close()

        # Advance persisted leases past the app's bounded recovery interval.
        # The remote prompt, history and output remain in the fake Comfy ledger.
        expired = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")
        with sqlite3.connect(database_path) as snapshot:
            snapshot.execute(
                "UPDATE full_auto_run SET lease_started_at = ?, lease_expires_at = ? WHERE run_id = ?",
                (expired, expired, run_id),
            )
            snapshot.execute(
                "UPDATE global_runner_claim SET lease_started_at = ?, lease_expires_at = ? "
                "WHERE singleton = 1 AND run_id = ?",
                (expired, expired, run_id),
            )
            snapshot.commit()

        port = _unused_port()
        base_url = f"http://127.0.0.1:{port}"
        worker, worker_log, ledger, accepted, log_path = _spawn_recovery_worker(
            tmp_path, port, hold_first_prompt=False,
        )
        workers.append((worker, worker_log))
        _wait_for_worker(base_url, worker, log_path)

        deadline = time.monotonic() + 15
        latest = None
        with httpx.Client(timeout=2) as remote:
            while time.monotonic() < deadline:
                if worker.poll() is not None:
                    pytest.fail(
                        f"The restarted backend exited: "
                        f"{log_path.read_text(encoding='utf-8', errors='replace')}"
                    )
                response = remote.get(
                    f"{base_url}/api/sessions/{session_id}/full-auto/{run_id}",
                )
                assert response.status_code == 200, response.text
                latest = response.json()
                if latest["state"] in {"blocked", "failed", "cancelled", "succeeded"}:
                    break
                time.sleep(0.05)

        final_ledger = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        assert final_ledger[0] == first_remote_record
        assert latest is not None
        assert latest["revision"] > before_restart["revision"], "Restart did not recover the persisted run."
        with sqlite3.connect(database_path) as snapshot:
            snapshot.row_factory = sqlite3.Row
            run_after = dict(snapshot.execute(
                "SELECT * FROM full_auto_run WHERE run_id = ?", (run_id,),
            ).fetchone())
            attempts_after = [dict(row) for row in snapshot.execute(
                "SELECT * FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY shot_id, attempt_number",
                (run_id,),
            )]
            shots_after = [dict(row) for row in snapshot.execute(
                "SELECT * FROM shot WHERE session_id = ? ORDER BY id",
                (session_id,),
            )]
            receipts_after = [dict(row) for row in snapshot.execute(
                "SELECT * FROM full_auto_submission_receipt WHERE run_id = ? ORDER BY target_index",
                (run_id,),
            )]
            session_after = dict(snapshot.execute(
                "SELECT * FROM session WHERE id = ?", (session_id,),
            ).fetchone())
            history_after = _process_restart_history(snapshot, session_id, run_id)
        assert len(attempts_after) == 2
        assert history_after == history_before, (
            "Restart changed a complete historical row or its presence."
        )
        _assert_full_row_transition(
            before_restart, run_after,
            {
                "state", "phase", "revision", "epoch", "fencing_token",
                "lease_expires_at", "lease_started_at", "cancel_intent",
                "cancel_intent_at", "blocker_code", "runner_ready_at",
                "assistant_retry_after_at", "comfy_probe_scope", "comfy_probe_count",
                "comfy_probe_due_at", "resume_phase", "evidence_json", "updated_at",
            },
            expected={
                "run_id": run_id,
                "session_id": session_id,
                "state": "succeeded",
                "phase": "execute",
                "plan_revision": before_restart["plan_revision"],
                "lease_expires_at": None,
                "blocker_code": None,
                "created_at": before_restart["created_at"],
            },
        )
        _assert_full_row_transition(
            session_before, session_after, {"status"}, expected={"status": "done"},
        )
        assert [row["id"] for row in shots_after] == [row["id"] for row in shots_before]
        for before, after in zip(shots_before, shots_after, strict=True):
            _assert_full_row_transition(
                before, after,
                {"status", "seed", "prompt_id", "filename", "error", "finished_at"},
                expected={"status": "done"},
            )
        before_attempt = next(
            row for row in attempts_before if row["attempt_id"] == attempt_marker["attempt_id"]
        )
        first_recovered_attempt = next(
            row for row in attempts_after if row["attempt_id"] == attempt_marker["attempt_id"]
        )
        _assert_full_row_transition(
            before_attempt, first_recovered_attempt,
            {
                "dispatch_state", "prompt_id", "client_prompt_id", "client_id",
                "capability_evidence_json", "completion_state", "output_evidence_json",
                "expected_filename", "filename", "error", "updated_at",
            },
            expected={
                "attempt_id": attempt_marker["attempt_id"],
                "run_id": run_id,
                "session_id": session_id,
                "dispatch_state": "acknowledged",
                "prompt_id": first_remote_record["prompt_id"],
                "completion_state": "completed",
            },
        )
        remote_attempt_ids = [
            entry["request"]["extra_data"]["idevgen_attempt"]["attempt_id"]
            for entry in final_ledger
        ]
        assert remote_attempt_ids.count(attempt_marker["attempt_id"]) == 1, (
            "Restart replayed the same execution attempt: "
            + json.dumps({
                "remote_posts": [
                    {
                        "sequence": entry["sequence"],
                        "prompt_id": entry["prompt_id"],
                        "attempt_id": entry["request"]["extra_data"]["idevgen_attempt"]["attempt_id"],
                        "filename_prefix": entry["request"]["extra_data"]["idevgen_attempt"]["filename_prefix"],
                        "request_sha256": entry["request_sha256"],
                    }
                    for entry in final_ledger
                ],
                "attempts_before": attempts_before,
                "attempts_after": attempts_after,
                "run": latest,
            }, indent=2)
        )
        assert len(remote_attempt_ids) == len(set(remote_attempt_ids))
        assert set(remote_attempt_ids) == {row["attempt_id"] for row in attempts_after}
        assert len(final_ledger) == 2
        assert latest["state"] == "succeeded", json.dumps(latest, indent=2)
        assert all(row["dispatch_state"] == "acknowledged" for row in attempts_after)
        assert all(row["completion_state"] == "completed" for row in attempts_after)
        assert [row["status"] for row in shots_after] == ["done", "done"]
        assert all(row["filename"] for row in shots_after)
        attempts_by_shot = {row["shot_id"]: row for row in attempts_after}
        assert all(row["seed"] == attempts_by_shot[row["id"]]["seed"] for row in shots_after)
        session_dir = Path(main.DATA_DIR) / "sessions" / str(session_id)
        assert all((session_dir / row["filename"]).is_file() for row in shots_after)
    finally:
        for process, log in workers:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            if not log.closed:
                log.close()
        db.connect(database_path)
