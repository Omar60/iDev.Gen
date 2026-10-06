"""Integrated regression for the Task 10.4 offline/manual journey."""
from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import uuid

from PIL import Image
import pytest

import db
import main


_FRONTEND_TRANSLATIONS = r"""
import { buildTranslationMapFromRows } from './src/resources.js'
let input = ''
for await (const chunk of process.stdin) input += chunk
const { rows } = JSON.parse(input)
process.stdout.write(JSON.stringify(buildTranslationMapFromRows(rows)))
"""

_FRONTEND_PLAN_SAVE = r"""
import {
  canPreparePlan,
  executeSavePlan,
  loadSessionPlan,
  updateTake,
} from './src/sessionPlan.js'
let input = ''
for await (const chunk of process.stdin) input += chunk
const { sessionId, plan, expectedRevision, takeId, changes, sharedDecisions } = JSON.parse(input)
const loaded = await loadSessionPlan(sessionId, {
  get: async (path) => {
    if (path !== `/api/sessions/${sessionId}/plan`) throw new Error(`Unexpected GET ${path}`)
    return { plan_revision: expectedRevision, plan }
  },
})
if (!loaded.ok) throw new Error(loaded.error)
const draft = { ...loaded.plan }
if (takeId) draft.takes = updateTake(draft.takes, takeId, changes)
let sent = null
const result = await executeSavePlan(sessionId, draft, loaded.planRevision, {
  post: async (path, body) => {
    if (path !== `/api/sessions/${sessionId}/plan`) throw new Error(`Unexpected POST ${path}`)
    sent = body
    return { plan_revision: expectedRevision + 1, conflicts: [] }
  },
}, sharedDecisions)
process.stdout.write(JSON.stringify({ result, payload: sent, canPrepare: canPreparePlan(loaded) }))
"""

_FRONTEND_PREPARE_TAKE = r"""
import { prepareTake } from './src/sessionPlan.js'
let input = ''
for await (const chunk of process.stdin) input += chunk
const { sessionId, takeId, planRevision, options } = JSON.parse(input)
let sent = null
const result = await prepareTake(sessionId, takeId, planRevision, options || {}, {
  post: async (path, body) => {
    const expected = `/api/sessions/${sessionId}/plan/takes/${takeId}/prepare`
    if (path !== expected) throw new Error(`Unexpected POST ${path}`)
    sent = body
    return { take_id: takeId, status: 'ready' }
  },
})
process.stdout.write(JSON.stringify({ result, path: `/api/sessions/${sessionId}/plan/takes/${takeId}/prepare`, body: sent }))
"""

_FRONTEND_PLAN_READ = r"""
import { canPreparePlan, loadSessionPlan } from './src/sessionPlan.js'
let input = ''
for await (const chunk of process.stdin) input += chunk
const { sessionId, plan, planRevision } = JSON.parse(input)
const loaded = await loadSessionPlan(sessionId, {
  get: async (path) => {
    if (path !== `/api/sessions/${sessionId}/plan`) throw new Error(`Unexpected GET ${path}`)
    return { plan_revision: planRevision, plan }
  },
})
process.stdout.write(JSON.stringify({ ok: loaded.ok, mode: loaded.plan?.authoring?.mode, canPrepare: canPreparePlan(loaded) }))
"""


def _frontend(script: str, payload: dict) -> dict:
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=frontend,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        check=True,
        encoding="utf-8",
        timeout=30,
    )
    return json.loads(result.stdout)


def test_empty_choice_is_unset_only_for_manual_authoring():
    manual_plan = {"authoring": {"schema_version": 1, "mode": "manual"}}
    automatic_plan = {"authoring": {"schema_version": 1, "mode": "automatic"}}

    assert main.resource_preparation._take_choices_for_authoring_plan(
        manual_plan, {"camera": ""},
    ) == {}
    assert main.resource_preparation._take_choices_for_authoring_plan(
        manual_plan, {"camera": " "},
    ) == {"camera": " "}
    with pytest.raises(main.resource_preparation.PreparationArgumentError):
        main.resource_preparation._take_choices_for_authoring_plan(
            manual_plan, {"camera": None},
        )
    for plan in (automatic_plan, {}):
        with pytest.raises(main.resource_preparation.PreparationArgumentError):
            main.resource_preparation._take_choices_for_authoring_plan(
                plan, {"camera": ""},
            )


def _configure_no_assistant(monkeypatch) -> list:
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.setitem(main.CONFIG, "llm_url", "")
    monkeypatch.setitem(main.CONFIG, "llm_model", "")
    monkeypatch.setitem(main.CONFIG, "llm_vision_model", "")
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    calls = []

    async def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("The offline manual journey attempted an assistant call.")

    monkeypatch.setattr(main.enhance, "run_structured", forbidden)
    return calls


def _import_untranslated_scene(client, library_key: str) -> dict:
    source = {
        "library": library_key,
        "items": [{
            "id": "scene-001",
            "label": "Invented portrait studio",
            "scene_theme": "Soft daylight enters a quiet portrait studio.",
        }],
    }
    created = client.post(
        "/api/resources/import-selections",
        json={"request_id": str(uuid.uuid4())},
    )
    assert created.status_code == 201, created.text
    selection_id = created.json()["selection_id"]
    uploaded = client.post(
        f"/api/resources/import-selections/{selection_id}/files",
        files={"file": (
            "invented_rooms.json",
            io.BytesIO(json.dumps(source).encode("utf-8")),
            "application/json",
        )},
        data={"upload_id": str(uuid.uuid4())},
    )
    assert uploaded.status_code == 201, uploaded.text
    staged = uploaded.json()
    assert staged["files"][0]["matched_auxiliary_kinds"] == []

    preview = client.post(
        f"/api/resources/import-selections/{selection_id}/preview",
        json={"expected_revision": staged["selection_revision"]},
    )
    assert preview.status_code == 200, preview.text
    preview_view = preview.json()
    assert preview_view["preview"]["committable"] is True
    committed = client.post(
        f"/api/resources/import-selections/{selection_id}/commit",
        json={
            "expected_revision": preview_view["selection_revision"],
            "preview_token": preview_view["preview"]["preview_token"],
        },
    )
    assert committed.status_code == 200, committed.text

    library = client.get(f"/api/resources/libraries/{library_key}")
    assert library.status_code == 200, library.text
    revision = library.json()["revisions"][0]
    assert revision["readiness"]["status"] == "pending"
    return {
        "library_key": library_key,
        "source_id": revision["source_id"],
        "content_digest": revision["content_digest"],
    }


def _create_guided_session(client, seeded: dict, anchor: dict, mode: str) -> tuple[int, int]:
    response = client.post("/api/sessions/guided", json={
        "request_id": str(uuid.uuid4()),
        "character_id": seeded["model_id"],
        "scene_anchor": anchor,
        "photo_count": 2,
        "mode": mode,
        "brief": "",
        "look": "",
        "initial_wardrobe": "",
    })
    assert response.status_code == 201, response.text
    body = response.json()
    return body["session_id"], body["plan_revision"]


def test_offline_manual_journey_and_automatic_draft_stop_at_review(
    client, seeded, monkeypatch,
):
    assistant_calls = _configure_no_assistant(monkeypatch)
    library_key = f"task104_manual_{uuid.uuid4().hex[:12]}"
    anchor = _import_untranslated_scene(client, library_key)

    rows_response = client.get(f"/api/resources/libraries/{library_key}/translations/rows")
    assert rows_response.status_code == 200, rows_response.text
    rows = rows_response.json()["rows"]
    required_rows = [row for row in rows if row["required"]]
    assert len(required_rows) == 2
    assert all(row["identity_required"] for row in required_rows)
    built = _frontend(_FRONTEND_TRANSLATIONS, {
        "rows": [
            {**row, "translation": row["source_value"], "emit": True}
            for row in required_rows
        ],
    })
    assert built["errors"] == []
    translation_map = built["translationMap"]
    assert all(item["source"] == item["translation"] for item in translation_map.values())

    pending_before = client.get(f"/api/resources/libraries/{library_key}").json()
    preview = client.post(
        f"/api/resources/libraries/{library_key}/translations/preview",
        json={"translation_map": translation_map},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["would_be_ready"] == 1
    pending_after_preview = client.get(f"/api/resources/libraries/{library_key}").json()
    assert pending_after_preview["revisions"][0]["readiness"]["status"] == "pending"
    assert pending_after_preview["revisions"][0]["translation"] == pending_before["revisions"][0]["translation"]
    applied = client.post(
        f"/api/resources/libraries/{library_key}/translations/apply",
        json={
            "translation_map": translation_map,
            "attestation_token": preview.json()["attestation_token"],
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["ready"] == 1
    ready_library = client.get(f"/api/resources/libraries/{library_key}").json()
    assert ready_library["revisions"][0]["readiness"]["status"] == "ready"

    # Empty shared constraints are an explicit manual decision. Both take
    # entries are completed through the same frontend edit/save helpers as the
    # rendered session UI.
    session_id, revision = _create_guided_session(client, seeded, anchor, "manual")
    initial = client.get(f"/api/sessions/{session_id}/plan").json()
    assert initial["plan"]["authoring"]["mode"] == "manual"
    choices_by_take = {
        "take-001": {
            "camera": "50mm close camera",
            "framing": "head-and-shoulders portrait",
            "pose": "",
            "expression": "",
        },
        "take-002": {
            "camera": "35mm eye-level camera",
            "framing": "",
            "pose": "",
            "expression": "",
        },
    }
    manual_completions = {
        "take-001": {
            "pose": "seated beside a window",
            "expression": "thoughtful expression",
        },
        "take-002": {
            "framing": "waist-up portrait",
            "pose": "standing with relaxed shoulders",
            "expression": "calm, attentive expression",
        },
    }
    edited = _frontend(_FRONTEND_PLAN_SAVE, {
        "sessionId": session_id,
        "plan": initial["plan"],
        "expectedRevision": revision,
        "takeId": "take-001",
        "changes": choices_by_take["take-001"],
        "sharedDecisions": ["look", "initial_wardrobe"],
    })
    assert edited["result"]["ok"] is True
    assert edited["canPrepare"] is True
    saved = client.post(f"/api/sessions/{session_id}/plan", json=edited["payload"])
    assert saved.status_code == 200, saved.text
    revision = saved.json()["plan_revision"]
    after_first_edit = client.get(f"/api/sessions/{session_id}/plan").json()
    edited_second = _frontend(_FRONTEND_PLAN_SAVE, {
        "sessionId": session_id,
        "plan": after_first_edit["plan"],
        "expectedRevision": revision,
        "takeId": "take-002",
        "changes": choices_by_take["take-002"],
        "sharedDecisions": None,
    })
    assert edited_second["result"]["ok"] is True
    saved_second = client.post(
        f"/api/sessions/{session_id}/plan", json=edited_second["payload"],
    )
    assert saved_second.status_code == 200, saved_second.text
    revision = saved_second.json()["plan_revision"]
    manual_plan = client.get(f"/api/sessions/{session_id}/plan").json()["plan"]
    assert manual_plan["look"] == manual_plan["initial_wardrobe"] == ""
    assert manual_plan["authoring"]["shared_state"] == {
        "look": {"origin": "user", "evidence_id": None},
        "initial_wardrobe": {"origin": "user", "evidence_id": None},
    }
    assert (
        [
            take[field]
            for take in manual_plan["takes"]
            for field in choices_by_take[take["take_id"]]
        ]
        == [value for choices in choices_by_take.values() for value in choices.values()]
    )

    # A still-unset manual plan cannot become ready until the user provides
    # those values. The real frontend prepare helper sends null when its manual
    # completion form is empty, and the refusal must leave no prepared row.
    missing = _frontend(_FRONTEND_PREPARE_TAKE, {
        "sessionId": session_id,
        "takeId": "take-001",
        "planRevision": revision,
        "options": {},
    })
    assert missing["result"]["ok"] is True
    assert missing["body"]["manual_completion"] is None
    refused = client.post(missing["path"], json=missing["body"])
    assert refused.status_code == 422, refused.text
    assert "unlocked descriptive choices" in refused.text
    assert db.one(
        "SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ?", session_id,
    )["n"] == 0

    # Single-take preparation accepts values only for the exact empty fields
    # saved by the frontend normalizer.
    single_request = _frontend(_FRONTEND_PREPARE_TAKE, {
        "sessionId": session_id,
        "takeId": "take-001",
        "planRevision": revision,
        "options": {"manualCompletion": manual_completions["take-001"]},
    })
    assert single_request["result"]["ok"] is True
    assert single_request["body"]["manual_completion"] == manual_completions["take-001"]
    single_prepared = client.post(single_request["path"], json=single_request["body"])
    assert single_prepared.status_code == 200, single_prepared.text
    assert single_prepared.json()["status"] == "ready"

    # Bulk preparation uses the same empty-is-unset rule in its prevalidation
    # before it finalizes through the canonical per-take preparation path.
    batch_prepared = client.post(
        f"/api/sessions/{session_id}/plan/preparations/prepare",
        json={
            "plan_revision": revision,
            "take_ids": ["take-002"],
            "manual_completions": {"take-002": manual_completions["take-002"]},
        },
    )
    assert batch_prepared.status_code == 200, batch_prepared.text
    assert [item["take_id"] for item in batch_prepared.json()["prepared"]] == ["take-002"]
    assert batch_prepared.json()["prepared"][0]["status"] == "ready"

    manual_snapshots = db.q(
        "SELECT take_id, final_prompt, effective_state, provenance FROM prepared_take "
        "WHERE session_id = ? AND plan_revision = ? ORDER BY take_id",
        session_id, revision,
    )
    assert len(manual_snapshots) == 2
    for snapshot in manual_snapshots:
        assert "Soft daylight enters a quiet portrait studio." in snapshot["final_prompt"]
        effective_take_choices = json.loads(snapshot["effective_state"])["take_choices"]
        assert effective_take_choices == {
            **choices_by_take[snapshot["take_id"]],
            **manual_completions[snapshot["take_id"]],
        }
        evidence = json.loads(snapshot["provenance"])["authoring_evidence"]
        assert evidence["source"] == "manual"
        assert evidence["manual_completion"]["descriptive_inputs"] == manual_completions[
            snapshot["take_id"]
        ]

    for take_id in ("take-001", "take-002"):
        review = client.get(
            f"/api/sessions/{session_id}/plan/takes/{take_id}/review?plan_revision={revision}"
        )
        assert review.status_code == 200, review.text
        assert review.json()["snapshot"]["effective_state"]["take_choices"] == {
            **choices_by_take[take_id], **manual_completions[take_id],
        }
    plan_review = client.get(
        f"/api/sessions/{session_id}/plan/review?plan_revision={revision}"
    )
    assert plan_review.status_code == 200, plan_review.text
    assert client.get(f"/api/sessions/{session_id}/plan").json()["reviewed_revision"] is None
    assert client.get(f"/api/sessions/{session_id}").json()["shots"] == []
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id)["n"] == 0
    assert assistant_calls == []

    # Automatic mode remains a valid editable draft without a text assistant,
    # while the authoring operation is refused before any operation/evidence write.
    automatic_id, automatic_revision = _create_guided_session(
        client, seeded, anchor, "automatic",
    )
    automatic = client.get(f"/api/sessions/{automatic_id}/plan").json()
    assert automatic["plan"]["authoring"]["mode"] == "automatic"
    assert _frontend(_FRONTEND_PLAN_READ, {
        "sessionId": automatic_id,
        "plan": automatic["plan"],
        "planRevision": automatic["plan_revision"],
    }) == {"ok": True, "mode": "automatic", "canPrepare": True}
    before_edit = automatic["plan_revision"]
    automatic_edit = _frontend(_FRONTEND_PLAN_SAVE, {
        "sessionId": automatic_id,
        "plan": automatic["plan"],
        "expectedRevision": before_edit,
        "takeId": "take-001",
        "changes": {"camera": "user-selected eye-level camera"},
        "sharedDecisions": None,
    })
    assert automatic_edit["result"]["ok"] is True
    saved_automatic = client.post(
        f"/api/sessions/{automatic_id}/plan", json=automatic_edit["payload"],
    )
    assert saved_automatic.status_code == 200, saved_automatic.text
    automatic_revision = saved_automatic.json()["plan_revision"]
    reloaded = client.get(f"/api/sessions/{automatic_id}/plan").json()
    assert reloaded["plan"]["takes"][0]["camera"] == "user-selected eye-level camera"
    assert _frontend(_FRONTEND_PLAN_READ, {
        "sessionId": automatic_id,
        "plan": reloaded["plan"],
        "planRevision": automatic_revision,
    }) == {"ok": True, "mode": "automatic", "canPrepare": True}

    operations_before = db.one(
        "SELECT COUNT(*) AS n FROM authoring_operation WHERE session_id = ?", automatic_id,
    )["n"]
    prepared_before = db.one(
        "SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ?", automatic_id,
    )["n"]
    unavailable = client.post(
        f"/api/sessions/{automatic_id}/plan/authoring/operations",
        json={
            "request_id": str(uuid.uuid4()),
            "expected_revision": automatic_revision,
            "kind": "prepare_takes",
            "take_ids": ["take-001"],
        },
    )
    assert unavailable.status_code == 409, unavailable.text
    assert unavailable.json()["detail"]["code"] == "assistant_unavailable"
    assert db.one(
        "SELECT COUNT(*) AS n FROM authoring_operation WHERE session_id = ?", automatic_id,
    )["n"] == operations_before == 0
    assert db.one(
        "SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ?", automatic_id,
    )["n"] == prepared_before == 0
    assert assistant_calls == []


def test_text_only_assistant_cannot_receive_staged_look_image(client, monkeypatch):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.setitem(main.CONFIG, "llm_url", "http://text-only-assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "invented-text-only-model")
    monkeypatch.setitem(main.CONFIG, "llm_vision_model", "")
    monkeypatch.setitem(main.CONFIG, "llm_key", "")
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    assert main.enhance.configured(main.CONFIG) is True
    assert main.enhance.vision_configured(main.CONFIG) is False

    image = io.BytesIO()
    Image.new("RGB", (4, 3), (120, 150, 180)).save(image, format="PNG")
    staged = client.post(
        "/api/looks/photo-stages",
        files={"file": ("invented-look.png", image.getvalue(), "image/png")},
    )
    assert staged.status_code == 201, staged.text
    photo_id = staged.json()["photo_id"]

    assistant_calls = []

    async def forbidden(*_args, **_kwargs):
        assistant_calls.append(True)
        raise AssertionError("A staged look image reached the text-only assistant.")

    monkeypatch.setattr(main.enhance, "run_structured", forbidden)
    saved_before = db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"]
    evidence_before = db.one("SELECT COUNT(*) AS n FROM saved_look_photo_evidence")["n"]
    proposals_before = db.one("SELECT COUNT(*) AS n FROM photo_look_proposal")["n"]
    response = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "vision_unavailable"
    assert assistant_calls == []
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == saved_before
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_photo_evidence")["n"] == evidence_before
    assert db.one("SELECT COUNT(*) AS n FROM photo_look_proposal")["n"] == proposals_before
    assert client.get(f"/api/looks/photo-stages/{photo_id}/preview").status_code == 200
    cancelled = client.post(f"/api/looks/photo-stages/{photo_id}/cancel", json={})
    assert cancelled.status_code == 200, cancelled.text
