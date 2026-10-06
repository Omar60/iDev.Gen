"""Integrated regression for the Task 10.3 automatic resource journey."""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import subprocess
import time
import uuid

import db
import main
from scripts.task10_3_browser_demo import SYNTHETIC_ASSISTANT_URL, _synthetic_async_client


_FRONTEND_PLAN_SAVE = r"""
import {
  computePlanChangeImpact,
  executeSavePlan,
  loadSessionPlan,
  updateTake,
} from './src/sessionPlan.js'

let input = ''
for await (const chunk of process.stdin) input += chunk
const { sessionId, plan, expectedRevision, takeId, changes } = JSON.parse(input)
const loaded = await loadSessionPlan(sessionId, {
  get: async (path) => {
    if (path !== `/api/sessions/${sessionId}/plan`) throw new Error(`Unexpected GET ${path}`)
    return { plan_revision: expectedRevision, plan }
  },
})
if (!loaded.ok) throw new Error(loaded.error)
const draft = {
  ...loaded.plan,
  takes: updateTake(loaded.plan.takes, takeId, changes),
}
const impact = computePlanChangeImpact(loaded.plan, draft)
let sent = null
const result = await executeSavePlan(sessionId, draft, loaded.planRevision, {
  post: async (path, body) => {
    if (path !== `/api/sessions/${sessionId}/plan`) throw new Error(`Unexpected POST ${path}`)
    sent = body
    return { plan_revision: expectedRevision + 1, conflicts: [] }
  },
})
process.stdout.write(JSON.stringify({ result, impact, payload: sent }))
"""


def _frontend_plan_save(session_id, plan, expected_revision, take_id, changes):
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", _FRONTEND_PLAN_SAVE],
        cwd=frontend,
        input=json.dumps({
            "sessionId": session_id,
            "plan": plan,
            "expectedRevision": expected_revision,
            "takeId": take_id,
            "changes": changes,
        }, ensure_ascii=False),
        capture_output=True,
        check=True,
        encoding="utf-8",
        timeout=30,
    )
    return json.loads(completed.stdout)


def _take_ids():
    return [f"take-{index:03d}" for index in range(1, 13)]


def _prepared_snapshots(session_id, revision):
    return {
        row["take_id"]: row
        for row in db.q(
            "SELECT id, take_id, final_prompt, effective_state, mapping_version, "
            "compiler_version, provenance, status FROM prepared_take "
            "WHERE session_id = ? AND plan_revision = ? ORDER BY take_id",
            session_id, revision,
        )
    }


def _operation_body(kind, revision, *, take_ids=None):
    body = {
        "request_id": str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": kind,
    }
    if take_ids is not None:
        body["take_ids"] = take_ids
    return body


def _install_synthetic_assistant(monkeypatch):
    calls, synthetic_client = _synthetic_async_client(main.enhance.httpx)
    monkeypatch.setitem(main.CONFIG, "llm_url", SYNTHETIC_ASSISTANT_URL)
    monkeypatch.setitem(main.CONFIG, "llm_model", "task10-3-synthetic-assistant")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setattr(main.enhance.httpx, "AsyncClient", synthetic_client)
    return calls


def _import_invented_scene(client, library_key):
    assert all(row["library_key"] != library_key for row in client.get("/api/resources/libraries").json())
    payload = {
        "library": library_key,
        "items": [{
            "id": "scene-001",
            "label": "\u67b6\u7a7a\u306e\u80d6\u50cf\u30b9\u30bf\u30b8\u30aa",
            "scene_theme": "\u67d4\u3089\u304b\u306a\u5149\u304c\u9ad8\u3044\u7a93\u304b\u3089\u9759\u304b\u306a\u30b9\u30bf\u30b8\u30aa\u306b\u5dee\u3057\u8fbc\u3080\u3002",
        }],
    }
    selection = client.post("/api/resources/import-selections", json={"request_id": str(uuid.uuid4())})
    assert selection.status_code == 201, selection.text
    selection_id = selection.json()["selection_id"]
    upload = client.post(
        f"/api/resources/import-selections/{selection_id}/files",
        files={"file": (
            "invented_rooms.json",
            io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            "application/json",
        )},
        data={"upload_id": str(uuid.uuid4())},
    )
    assert upload.status_code == 201, upload.text
    staged = upload.json()
    file = staged["files"][0]
    assert file["declared_library"] == library_key
    assert file["effective_library_key"] == library_key
    assert db.one("SELECT id FROM resource_library WHERE library_key = ?", library_key) is None

    preview = client.post(
        f"/api/resources/import-selections/{selection_id}/preview",
        json={"expected_revision": staged["selection_revision"]},
    )
    assert preview.status_code == 200, preview.text
    preview_view = preview.json()
    assert preview_view["preview"]["committable"] is True
    assert db.one("SELECT id FROM resource_library WHERE library_key = ?", library_key) is None

    commit = client.post(
        f"/api/resources/import-selections/{selection_id}/commit",
        json={
            "expected_revision": preview_view["selection_revision"],
            "preview_token": preview_view["preview"]["preview_token"],
        },
    )
    assert commit.status_code == 200, commit.text
    imported = client.get(f"/api/resources/libraries/{library_key}")
    assert imported.status_code == 200, imported.text
    revision = imported.json()["revisions"][0]
    return {
        "library_key": library_key,
        "source_id": revision["source_id"],
        "content_digest": revision["content_digest"],
    }


def _review_translation_proposals(client, library_key):
    rows_response = client.get(f"/api/resources/libraries/{library_key}/translations/rows")
    assert rows_response.status_code == 200, rows_response.text
    rows = rows_response.json()["rows"]
    assert [row["field"] for row in rows] == ["label", "scene_theme"]
    assert all(not row["identity_required"] for row in rows)
    proposal_response = client.post(
        f"/api/resources/libraries/{library_key}/translations/proposals",
        json={"entries": [{
            "source_id": row["revision"]["source_id"],
            "content_digest": row["revision"]["content_digest"],
            "field": row["field"],
            "source_shape": row["source_shape"],
            "list_index": row["list_index"],
        } for row in rows]},
    )
    assert proposal_response.status_code == 200, proposal_response.text
    proposals = proposal_response.json()["proposals"]
    assert len(proposals) == 2
    assert all(item["translation"] != item["source_value"] for item in proposals)

    translation_map = {
        item["source_value"]: {
            "source": item["source_value"],
            "translation": item["translation"],
            "fields": [item["field"]],
        }
        for item in proposals
    }
    preview = client.post(
        f"/api/resources/libraries/{library_key}/translations/preview",
        json={"translation_map": translation_map},
    )
    assert preview.status_code == 200, preview.text
    preview_view = preview.json()
    assert preview_view["would_be_ready"] == 1
    applied = client.post(
        f"/api/resources/libraries/{library_key}/translations/apply",
        json={
            "translation_map": translation_map,
            "attestation_token": preview_view["attestation_token"],
        },
    )
    assert applied.status_code == 200, applied.text
    return proposals


def test_automatic_journey_resumes_invalidates_reviews_and_runs_only_selection(
    client, seeded, make_runner, comfy_output, tmp_path, monkeypatch,
):
    calls = _install_synthetic_assistant(monkeypatch)
    library_key = f"task10_3_{uuid.uuid4().hex[:12]}"

    # Start with an unimported invented source, preview its safe target, then commit it.
    anchor = _import_invented_scene(client, library_key)
    proposals = _review_translation_proposals(client, library_key)
    scene_theme_translation = next(
        item["translation"] for item in proposals if item["field"] == "scene_theme"
    )
    ready_library = client.get(f"/api/resources/libraries/{library_key}").json()
    assert ready_library["revisions"][0]["readiness"]["status"] == "ready"
    assert calls["translation"] == 1

    # Guided retry replays the same session and does not create another session or plan.
    takes = _take_ids()
    sessions_before = db.one("SELECT COUNT(*) AS n FROM session")["n"]
    plans_before = db.one("SELECT COUNT(*) AS n FROM session_plan")["n"]
    requests_before = db.one("SELECT COUNT(*) AS n FROM guided_session_request")["n"]
    guided_body = {
        "request_id": str(uuid.uuid4()),
        "character_id": seeded["model_id"],
        "scene_anchor": anchor,
        "photo_count": 12,
        "mode": "automatic",
        "brief": "",
        "look": "",
        "initial_wardrobe": "",
    }
    created = client.post("/api/sessions/guided", json=guided_body)
    assert created.status_code == 201, created.text
    replay = client.post("/api/sessions/guided", json=guided_body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == created.json()
    assert db.one("SELECT COUNT(*) AS n FROM session")["n"] == sessions_before + 1
    assert db.one("SELECT COUNT(*) AS n FROM session_plan")["n"] == plans_before + 1
    assert db.one("SELECT COUNT(*) AS n FROM guided_session_request")["n"] == requests_before + 1
    session_id = created.json()["session_id"]
    revision = created.json()["plan_revision"]
    initial_plan = client.get(f"/api/sessions/{session_id}/plan").json()
    assert [take["take_id"] for take in initial_plan["plan"]["takes"]] == takes
    assert client.get(f"/api/sessions/{session_id}").json()["shots"] == []

    # Keep workers deterministic while still using the public start/status/resume/accept routes.
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", lambda _view: None)
    shared = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_operation_body("shared_suggestions", revision),
    )
    assert shared.status_code == 202, shared.text
    shared_id = shared.json()["operation_id"]
    asyncio.run(main._run_shared_suggestion_operation(session_id, shared_id))
    shared_status = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{shared_id}"
    )
    assert shared_status.status_code == 200, shared_status.text
    assert shared_status.json()["state"] == "succeeded"
    assert calls["shared"] == 1
    accepted = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations/{shared_id}/accept",
        json={
            "expected_revision": revision,
            "accepted": {
                "look": "",
                "initial_wardrobe": "",
            },
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["conflicts"] == []
    revision = accepted.json()["plan_revision"]
    assert revision == 2
    accepted_plan = client.get(f"/api/sessions/{session_id}/plan").json()["plan"]
    assert accepted_plan["look"] == accepted_plan["initial_wardrobe"] == ""
    assert accepted_plan["authoring"]["shared_state"] == {
        "look": {"origin": "assistant_edited", "evidence_id": shared_id},
        "initial_wardrobe": {"origin": "assistant_edited", "evidence_id": shared_id},
    }
    accepted_evidence = accepted_plan["authoring"]["evidence"][-1]
    assert accepted_evidence["output"] == {
        "look": "A calm portrait with soft side light.",
        "initial_wardrobe": "A cream cotton shirt and dark trousers.",
    }
    assert accepted_evidence["accepted"] == {
        "look": "", "initial_wardrobe": "",
    }

    # The first run persists exactly three snapshots before its deterministic fourth-call failure.
    prepare = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_operation_body("prepare_takes", revision, take_ids=takes),
    )
    assert prepare.status_code == 202, prepare.text
    operation_id = prepare.json()["operation_id"]
    asyncio.run(main._run_prepare_takes_operation(session_id, operation_id))
    failed = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    ).json()
    assert failed["state"] == "failed"
    assert failed["progress"]["completed"] == takes[:3]
    assert failed["progress"]["failed"]["take_id"] == "take-004"
    assert client.get(f"/api/sessions/{session_id}").json()["shots"] == []
    writer_calls_before_resume = calls["writer"]
    assert writer_calls_before_resume == 4
    first_three_before_resume = _prepared_snapshots(session_id, revision)
    assert set(first_three_before_resume) == set(takes[:3])
    writer_synthesis = main.resource_preparation.load_writer_synthesis(
        session_id, revision, "take-001",
    )
    assert writer_synthesis["writer_output"]["camera"] == "Synthetic camera, variation 1"
    assistant_request = writer_synthesis["writer_input"]["assistant_request"]
    assert assistant_request["model"] == "task10-3-synthetic-assistant"
    assert assistant_request["parameters"] == {
        "temperature": 0.8,
        "stream": False,
        "response_format": {"type": "json_object"},
        "reasoning_effort": "none",
    }
    user_message = next(message["content"] for message in assistant_request["messages"] if message["role"] == "user")
    assert json.loads(user_message.partition("Context: ")[2])["take_id"] == "take-001"

    resumed = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}/resume",
        json={"expected_revision": revision},
    )
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["progress"]["completed"] == takes[:3]
    asyncio.run(main._run_prepare_takes_operation(session_id, operation_id))
    completed = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    ).json()
    assert completed["state"] == "succeeded"
    assert completed["progress"]["completed"] == takes
    first_three_after_resume = _prepared_snapshots(session_id, revision)
    assert {
        take_id: {
            key: first_three_after_resume[take_id][key]
            for key in ("id", "final_prompt", "effective_state", "provenance")
        }
        for take_id in takes[:3]
    } == {
        take_id: {
            key: first_three_before_resume[take_id][key]
            for key in ("id", "final_prompt", "effective_state", "provenance")
        }
        for take_id in takes[:3]
    }
    assert all(
        scene_theme_translation in first_three_after_resume[take_id]["final_prompt"]
        for take_id in takes
    )
    assert calls["writer"] == writer_calls_before_resume + 9
    historical_before_edit = _prepared_snapshots(session_id, revision)
    assert set(historical_before_edit) == set(takes)

    # Exercise the same frontend load, edit, impact and save helpers used by the UI.
    draft_response = client.get(f"/api/sessions/{session_id}/plan")
    assert draft_response.status_code == 200, draft_response.text
    plan = draft_response.json()["plan"]
    source_revision = revision
    frontend_save = _frontend_plan_save(
        session_id,
        plan,
        source_revision,
        "take-004",
        {"expression": "a quiet, thoughtful expression"},
    )
    assert frontend_save["result"]["ok"] is True
    assert frontend_save["impact"]["reason"] == "automatic-downstream"
    assert frontend_save["impact"]["affectedTakeIds"] == takes[3:]
    save_payload = frontend_save["payload"]
    assert save_payload["expected_revision"] == source_revision
    assert save_payload["plan"]["takes"][:3] == plan["takes"][:3]
    edited_take = dict(plan["takes"][3])
    edited_take["expression"] = "a quiet, thoughtful expression"
    assert save_payload["plan"]["takes"][3] == edited_take
    assert save_payload["plan"]["takes"][4:] == plan["takes"][4:]
    edited = client.post(
        f"/api/sessions/{session_id}/plan",
        json=save_payload,
    )
    assert edited.status_code == 200, edited.text
    revision = edited.json()["plan_revision"]
    assert revision == source_revision + 1
    assert calls["writer"] == 13
    historical_after_edit = _prepared_snapshots(session_id, source_revision)
    snapshot_fields = (
        "id", "final_prompt", "effective_state", "mapping_version",
        "compiler_version", "provenance",
    )
    assert {
        take_id: tuple(row[field] for field in snapshot_fields)
        for take_id, row in historical_after_edit.items()
    } == {
        take_id: tuple(row[field] for field in snapshot_fields)
        for take_id, row in historical_before_edit.items()
    }
    copied_forward = _prepared_snapshots(session_id, revision)
    assert set(copied_forward) == set(takes[:3])
    for take_id in takes[:3]:
        source = historical_after_edit[take_id]
        copied = copied_forward[take_id]
        assert copied["status"] == "ready"
        assert all(copied[field] == source[field] for field in (
            "final_prompt", "effective_state", "mapping_version", "compiler_version",
        ))
        assert scene_theme_translation in copied["final_prompt"]
        lineage = json.loads(copied["provenance"])["copy_forward"]
        copied_provenance = json.loads(copied["provenance"])
        source_provenance = json.loads(source["provenance"])
        assert {
            key: value for key, value in copied_provenance.items()
            if key != "copy_forward"
        } == source_provenance
        assert lineage["source_prepared_id"] == source["id"]
        assert lineage["source_plan_revision"] == source_revision
        assert lineage["destination_plan_revision"] == revision
        assert lineage["origin_prepared_id"] == source["id"]
        assert lineage["origin_plan_revision"] == source_revision
    recovery = client.get(f"/api/sessions/{session_id}/plan").json()["preparation"]
    assert [item["take_id"] for item in recovery["completed"]] == takes[:3]
    assert [item["take_id"] for item in recovery["incomplete"]] == takes[3:]
    assert calls["writer"] == 13

    affected = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_operation_body("prepare_takes", revision, take_ids=takes[3:]),
    )
    assert affected.status_code == 202, affected.text
    affected_id = affected.json()["operation_id"]
    asyncio.run(main._run_prepare_takes_operation(session_id, affected_id))
    affected_status = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{affected_id}"
    ).json()
    assert affected_status["state"] == "succeeded"
    assert affected_status["progress"]["requested"] == takes[3:]
    review = client.get(f"/api/sessions/{session_id}/plan/review?plan_revision={revision}")
    assert review.status_code == 200, review.text
    assert client.get(f"/api/sessions/{session_id}/plan").json()["reviewed_revision"] is None
    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approved.status_code == 200, approved.text
    assert client.get(f"/api/sessions/{session_id}/plan").json()["reviewed_revision"] == revision

    # Preparation and approval create no shots. Submission creates only the chosen two, and Run executes only them.
    assert client.get(f"/api/sessions/{session_id}").json()["shots"] == []
    fake_runner, fake_comfy = make_runner()
    monkeypatch.setattr(main, "runner", fake_runner)
    monkeypatch.setattr(main, "COMFY_OUTPUT", comfy_output)
    monkeypatch.setitem(main.CONFIG, "comfy_output_dir", str(comfy_output))
    selected = ["take-001", "take-004"]
    submitted = client.post(
        f"/api/sessions/{session_id}/plan/preparations/submit-selected",
        json={"plan_revision": revision, "take_ids": selected},
    )
    assert submitted.status_code == 200, submitted.text
    shots = client.get(f"/api/sessions/{session_id}").json()["shots"]
    assert len(shots) == len(selected)
    assert all(shot["status"] == "pending" for shot in shots)
    assert fake_comfy.attempts == 0

    run = client.post(f"/api/sessions/{session_id}/run")
    assert run.status_code == 200, run.text
    deadline = time.monotonic() + 5
    latest = None
    while time.monotonic() < deadline:
        latest = client.get(f"/api/sessions/{session_id}").json()
        if all(shot["status"] == "done" for shot in latest["shots"]):
            break
        time.sleep(0.01)
    assert latest is not None
    assert fake_comfy.attempts == len(selected)
    assert len(latest["shots"]) == len(selected)
    assert all(shot["status"] == "done" for shot in latest["shots"])
    assert calls["writer"] == 22
    assert len(proposals) == 2
