"""Task 9.12 integration coverage for saved-look creation and use.

Coverage map for detailed boundaries already exercised elsewhere:
- Manual creation and immutable versions:
  test_saved_looks_api::test_manual_create_generates_keys_and_keeps_one_piece_layers_and_accessories_ordered.
- Portable JSON conflicts and synchronized commits:
  test_portable_look_import::test_concurrent_duplicate_and_different_content_imports_are_serialized.
- Text-only vision refusal before transport:
  test_photo_extraction::test_unavailable_vision_and_failed_provider_leave_manual_save_available.
- A provider exception, retryable preview, and manual fallback:
  test_provider_exception_keeps_photo_preview_and_manual_entry_available below.
- Redacted reviewed photo evidence and retained corrections:
  test_photo_extraction_acceptance::test_http_wire_projection_is_exact_redacted_and_survives_stage_purge.
- Fixed expiry, terminal cleanup, and retryable filesystem warnings:
  test_look_photo_staging_independent and test_photo_staging.
- Snapshot schema and canonical prompt sentences:
  test_saved_look_application_acceptance and test_task9_9_wardrobe_progression.

This module joins the creation routes to a persisted session snapshot, reviewed
wardrobe progression, and the real preparation endpoint.
"""
from __future__ import annotations

from datetime import timedelta
from io import BytesIO
import json
import uuid

from PIL import Image
import pytest

import db
import main
import session_plan
from backend import photo_staging, resource_store


@pytest.fixture(autouse=True)
def _clear_photo_artifacts(client):
    def clear():
        for row in db.q("SELECT photo_id, staged_path FROM look_photo_stage"):
            photo_staging._safe_unlink(row["photo_id"], row["staged_path"])
        db.run("DELETE FROM photo_look_proposal")
        db.run("DELETE FROM look_photo_stage")
        db.run("DELETE FROM saved_look_photo_evidence")
        db.run("DELETE FROM saved_look_import_receipt")

    clear()
    yield
    clear()


def _portable_envelope() -> dict:
    return {
        "schema_version": 1,
        "look": {
            "key": "task-9-12-imported-look",
            "version": 3,
            "name": "Imported studio layers",
            "appearance": "Warm curls and natural makeup.",
            "outfit": {
                "key": "task-9-12-imported-outfit",
                "garment_keys": ["task-9-12-cardigan", "task-9-12-shirt"],
            },
        },
        "garments": [
            {"key": "task-9-12-cardigan", "wording": "a blue cardigan", "aside": ""},
            {"key": "task-9-12-shirt", "wording": "a linen shirt", "aside": ""},
        ],
        "provenance": {"source": "import", "image_sha256": None},
    }


def _create_photo_look(client, monkeypatch) -> dict:
    config = {
        **main.CONFIG,
        "resource_planning_enabled": True,
        "llm_url": "https://vision.example.invalid/v1",
        "llm_model": "invented-text-model",
        "llm_vision_model": "invented-vision-model",
        "llm_key": "task-9-12-invented-key",
    }
    monkeypatch.setattr(main, "CONFIG", config)

    async def fake_provider(_config, prompt, image, *, request_evidence):
        request_evidence.update({
            "messages": [
                {"role": "system", "content": "Use only visible evidence."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt.instruction},
                    {"type": "image_url", "image_url": {"url": image}},
                ]},
            ],
            "model": config["llm_vision_model"],
            "parameters": {
                "temperature": 0.8,
                "stream": False,
                "response_format": {"type": "json_object"},
            },
        })
        return {
            "appearance": "Provider description before review.",
            "garments": ["a white blouse", "a dark cardigan"],
            "unresolved": [{"id": "u1", "detail": "The clasp is partly obscured."}],
        }

    monkeypatch.setattr(main.enhance, "run_structured", fake_provider)
    image_stream = BytesIO()
    Image.new("RGB", (5, 4), (31, 83, 149)).save(image_stream, format="PNG")
    image_bytes = image_stream.getvalue()
    uploaded = client.post(
        "/api/looks/photo-stages",
        files={"file": ("invented-look.png", image_bytes, "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    photo_id = uploaded.json()["photo_id"]

    proposal = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={})
    assert proposal.status_code == 200, proposal.text
    proposal_body = proposal.json()
    reviewed = {
        "proposal_id": proposal_body["proposal_id"],
        "name": "Corrected photo look",
        "appearance": "Short dark hair with a silver clip. The clasp is silver.",
        "garments": [
            {"wording": "a navy cardigan", "aside": "folded over one arm"},
            {"wording": "a cream blouse", "aside": ""},
        ],
        "unresolved_decisions": [
            {"id": "u1", "action": "correct", "correction": "The clasp is silver."},
        ],
        "review_confirmed": True,
        "removal_order_confirmed": True,
    }
    saved = client.post(
        f"/api/looks/photo-stages/{photo_id}/save-extracted", json=reviewed,
    )
    assert saved.status_code == 200, saved.text
    look = saved.json()
    assert look["appearance"] == reviewed["appearance"]
    assert [item["wording"] for item in look["outfit"]["garments"]] == [
        item["wording"] for item in reviewed["garments"]
    ]

    evidence = client.get(
        f"/api/looks/{look['key']}/versions/{look['version']}/photo-evidence"
    )
    assert evidence.status_code == 200, evidence.text
    evidence_body = evidence.json()
    assert evidence_body["corrections"]["appearance"] == reviewed["appearance"]
    assert evidence_body["corrections"]["garments"] == reviewed["garments"]
    assert evidence_body["corrections"]["unresolved_decisions"] == reviewed["unresolved_decisions"]
    serialized_evidence = json.dumps(evidence_body)
    assert "data:image" not in serialized_evidence
    assert config["llm_url"] not in serialized_evidence
    assert config["llm_key"] not in serialized_evidence
    assert not photo_staging._stage_path(photo_id).exists()

    # Expire and purge the terminal stage as the recovery sweep would. Saved
    # descriptions and the redacted evidence must remain usable afterward.
    expired = photo_staging._iso(photo_staging._now() - timedelta(hours=25))
    db.run("UPDATE look_photo_stage SET expires_at = ? WHERE photo_id = ?", expired, photo_id)
    photo_staging.startup_recovery(now=photo_staging._now())
    assert db.one("SELECT photo_id FROM look_photo_stage WHERE photo_id = ?", photo_id) is None
    assert client.get(
        f"/api/looks/{look['key']}/versions/{look['version']}/photo-evidence"
    ).json() == evidence_body
    return look


def _create_look(client, source: str, monkeypatch) -> dict:
    if source == "manual":
        response = client.post("/api/looks", json={
            "name": "Manually entered layers",
            "appearance": "Soft curls and even makeup.",
            "garments": [
                {"wording": "a wool coat", "aside": ""},
                {"wording": "a cotton shirt", "aside": ""},
            ],
        })
        assert response.status_code == 200, response.text
        return response.json()

    if source == "json":
        envelope = _portable_envelope()
        preview = client.post("/api/looks/import/preview", json=envelope)
        assert preview.status_code == 200, preview.text
        reviewed = preview.json()
        committed = client.post("/api/looks/import/commit", json={
            "envelope": envelope,
            "preview_token": reviewed["preview_token"],
            "review_digest": reviewed["review_digest"],
            "choice": "import",
        })
        assert committed.status_code == 200, committed.text
        assert committed.json()["no_op"] is False
        return committed.json()["look"]

    if source == "photo":
        return _create_photo_look(client, monkeypatch)

    raise AssertionError(f"unexpected look source: {source}")


def _room_anchor() -> dict[str, str]:
    library_key = f"task-9-12-rooms-{uuid.uuid4().hex}"
    source_id = "studio-01"
    label = "invented studio"
    description = "Soft window light falls across a quiet, empty studio."
    library_id = resource_store.ensure_library(library_key, kind="rooms")
    revision_id = resource_store.record_revision(
        library_id,
        source_id,
        {"id": source_id, "label": label, "scene_theme": description},
        translation={"label": label, "scene_theme": description},
    )
    revision = resource_store.get_revision(revision_id=revision_id)
    assert revision is not None
    return {
        "library_key": library_key,
        "source_id": source_id,
        "content_digest": revision["content_digest"],
    }


def _guided_session(client, seeded) -> tuple[int, int]:
    response = client.post("/api/sessions/guided", json={
        "request_id": str(uuid.uuid4()),
        "character_id": seeded["model_id"],
        "scene_anchor": _room_anchor(),
        "photo_count": 3,
        "mode": "manual",
        "brief": "Three invented portrait takes for look integration coverage.",
        "look": "Original session appearance.",
        "initial_wardrobe": "She wears the session's original clothes.",
    })
    assert response.status_code == 201, response.text
    result = response.json()
    return result["session_id"], result["plan_revision"]


def _set_take_choices(client, session_id: int, revision: int) -> int:
    current = client.get(f"/api/sessions/{session_id}/plan")
    assert current.status_code == 200, current.text
    body = current.json()
    assert body["plan_revision"] == revision
    plan = body["plan"]
    choices = [
        {"camera": "35mm", "framing": "three-quarter", "pose": "standing by a window", "expression": "a quiet smile"},
        {"camera": "50mm", "framing": "waist-up", "pose": "turning toward the camera", "expression": "a neutral expression"},
        {"camera": "85mm", "framing": "close portrait", "pose": "looking over one shoulder", "expression": "a thoughtful look"},
    ]
    for take, values in zip(plan["takes"], choices, strict=True):
        take.update(values)
    saved = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"expected_revision": revision, "plan": plan},
    )
    assert saved.status_code == 200, saved.text
    return saved.json()["plan_revision"]


def _apply_look(client, session_id: int, revision: int, look: dict) -> dict:
    applied = client.post(
        f"/api/sessions/{session_id}/plan/apply-look",
        json={
            "expected_revision": revision,
            "look_key": look["key"],
            "version": look["version"],
            "decisions": {"look": "replace", "initial_wardrobe": "replace"},
        },
    )
    assert applied.status_code == 200, applied.text
    return applied.json()


def _edit_live_look(client, look: dict) -> dict:
    changed = client.post(
        f"/api/looks/{look['key']}/versions",
        json={
            "expected_version": look["version"],
            "name": "Later catalogue wording",
            "appearance": "Changed live appearance that must not leak into the session.",
            "garments": [{"wording": "an unrelated silver vest", "aside": ""}],
        },
    )
    assert changed.status_code == 200, changed.text
    return changed.json()


def _preview_and_apply_progression(client, session_id: int, revision: int, look: dict) -> int:
    garments = look["outfit"]["garments"]
    full = session_plan.compose_saved_look_wardrobe(look["outfit"])
    remaining = session_plan.compose_saved_look_wardrobe({
        **look["outfit"], "garments": garments[1:],
    })
    preview_response = client.post(
        f"/api/sessions/{session_id}/plan/wardrobe-progression/preview",
        json={
            "expected_revision": revision,
            "start_take_id": "take-001",
            "end_take_id": "take-003",
            "stage_indices": [0, 1, 2],
            "event_policy": "replace",
        },
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["reviewed_wardrobes"] == [
        {"take_id": "take-001", "wardrobe": full},
        {"take_id": "take-002", "wardrobe": remaining},
        {"take_id": "take-003", "wardrobe": "She wears nothing at all."},
    ]
    applied = client.post(
        f"/api/sessions/{session_id}/plan/wardrobe-progression/apply",
        json={
            "expected_revision": preview["expected_revision"],
            "preview_token": preview["preview_token"],
            "review_digest": preview["review_digest"],
            "reviewed_wardrobes": preview["reviewed_wardrobes"],
        },
    )
    assert applied.status_code == 200, applied.text
    return applied.json()["plan_revision"]


def _prepare_prompts(client, session_id: int, revision: int) -> list[dict]:
    response = client.post(
        f"/api/sessions/{session_id}/plan/preparations/prepare",
        json={
            "plan_revision": revision,
            "take_ids": ["take-001", "take-002", "take-003"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["prepared"]


@pytest.mark.parametrize("source", ["manual", "json", "photo"])
def test_created_look_becomes_frozen_session_snapshot_and_prepared_prompts_use_only_current_garments(
    client, seeded, monkeypatch, source,
):
    look = _create_look(client, source, monkeypatch)
    original_snapshot = {
        "look_id": look["key"],
        "version": look["version"],
        "content_digest": look["content_digest"],
        "appearance": look["appearance"],
        "outfit": look["outfit"],
    }
    session_id, revision = _guided_session(client, seeded)
    revision = _set_take_choices(client, session_id, revision)
    applied = _apply_look(client, session_id, revision, look)
    assert applied["plan_revision"] == revision + 1

    # Later catalogue edits and removal cannot change a look already copied
    # into a session plan.
    _edit_live_look(client, look)
    db.run("DELETE FROM outfit WHERE key = ?", look["outfit"]["outfit_key"])
    for garment in look["outfit"]["garments"]:
        db.run("DELETE FROM garment WHERE key = ?", garment["key"])

    plan_response = client.get(f"/api/sessions/{session_id}/plan")
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()["plan"]
    assert plan["authoring"]["look_snapshot"] == original_snapshot
    assert plan["look"] == look["appearance"]
    assert plan["initial_wardrobe"] == session_plan.compose_saved_look_wardrobe(look["outfit"])

    revision = _preview_and_apply_progression(
        client, session_id, plan_response.json()["plan_revision"], look,
    )
    prepared = _prepare_prompts(client, session_id, revision)
    assert [snapshot["take_id"] for snapshot in prepared] == [
        "take-001", "take-002", "take-003",
    ]
    by_take = {snapshot["take_id"]: snapshot for snapshot in prepared}
    removed, remaining_garment = look["outfit"]["garments"]
    expected_wardrobes = {
        "take-001": session_plan.compose_saved_look_wardrobe(look["outfit"]),
        "take-002": session_plan.compose_saved_look_wardrobe({
            **look["outfit"], "garments": look["outfit"]["garments"][1:],
        }),
        "take-003": "She wears nothing at all.",
    }
    assert remaining_garment["key"] != removed["key"]
    for take_id, snapshot in by_take.items():
        prompt = snapshot["final_prompt"]
        assert look["appearance"] in prompt
        assert "Changed live appearance" not in prompt
        assert "an unrelated silver vest" not in prompt
        assert expected_wardrobes[take_id] in prompt
        persisted = client.get(
            f"/api/sessions/{session_id}/plan/takes/{take_id}/review"
        )
        assert persisted.status_code == 200, persisted.text
        assert persisted.json()["snapshot"]["final_prompt"] == prompt
    assert all(g["wording"] in by_take["take-001"]["final_prompt"] for g in look["outfit"]["garments"])
    assert removed["wording"] not in by_take["take-002"]["final_prompt"]
    assert remaining_garment["wording"] in by_take["take-002"]["final_prompt"]
    assert removed["wording"] not in by_take["take-003"]["final_prompt"]
    assert remaining_garment["wording"] not in by_take["take-003"]["final_prompt"]
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id)["n"] == 0


def test_provider_exception_keeps_photo_preview_and_manual_entry_available(client, monkeypatch):
    monkeypatch.setattr(main, "CONFIG", {
        **main.CONFIG,
        "resource_planning_enabled": True,
        "llm_url": "https://vision.example.invalid/v1",
        "llm_model": "invented-text-model",
        "llm_vision_model": "invented-vision-model",
        "llm_key": "task-9-12-invented-key",
    })

    async def refused_by_provider(*_args, **_kwargs):
        raise RuntimeError("provider refused this invented image")

    monkeypatch.setattr(main.enhance, "run_structured", refused_by_provider)
    image_stream = BytesIO()
    Image.new("RGB", (4, 3), (43, 97, 131)).save(image_stream, format="PNG")
    uploaded = client.post(
        "/api/looks/photo-stages",
        files={"file": ("invented-failure.png", image_stream.getvalue(), "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    photo_id = uploaded.json()["photo_id"]

    failed = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={})
    assert failed.status_code == 502, failed.text
    assert failed.json()["detail"]["code"] == "vision_request_failed"
    assert "provider refused" not in failed.text
    assert client.get(f"/api/looks/photo-stages/{photo_id}/preview").status_code == 200
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0

    manual = client.post(
        f"/api/looks/photo-stages/{photo_id}/save",
        json={"name": "Manual after provider refusal", "appearance": "Hand-entered curls."},
    )
    assert manual.status_code == 200, manual.text
    assert manual.json()["appearance"] == "Hand-entered curls."
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_photo_evidence")["n"] == 0


def test_malformed_applied_snapshot_is_refused_by_progression_route_without_writes(client, seeded):
    look_response = client.post("/api/looks", json={
        "name": "Malformed snapshot source",
        "appearance": "Short curls and even makeup.",
        "garments": [
            {"wording": "a soft coat", "aside": ""},
            {"wording": "a linen blouse", "aside": ""},
        ],
    })
    assert look_response.status_code == 200, look_response.text
    look = look_response.json()
    session_id, revision = _guided_session(client, seeded)
    _apply_look(client, session_id, revision, look)

    stored = db.one(
        "SELECT plan_revision, plan_json, updated_at FROM session_plan WHERE session_id = ?",
        session_id,
    )
    malformed = json.loads(stored["plan_json"])
    snapshot = malformed["authoring"]["look_snapshot"]
    snapshot["outfit"]["garments"][1]["key"] = snapshot["outfit"]["garments"][0]["key"]
    snapshot["content_digest"] = resource_store.canonical_digest({
        "appearance": snapshot["appearance"],
        "outfit": snapshot["outfit"],
    })
    malformed_json = json.dumps(malformed, ensure_ascii=True, separators=(",", ":"))
    db.run("UPDATE session_plan SET plan_json = ? WHERE session_id = ?", malformed_json, session_id)

    before_prepared = db.one(
        "SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ?", session_id,
    )["n"]
    refused = client.post(
        f"/api/sessions/{session_id}/plan/wardrobe-progression/preview",
        json={
            "expected_revision": stored["plan_revision"],
            "start_take_id": "take-001",
            "end_take_id": "take-003",
            "stage_indices": [0, 1, 2],
            "event_policy": "replace",
        },
    )
    assert refused.status_code == 422, refused.text
    assert "duplicate garment key" in refused.json()["detail"]
    after = db.one(
        "SELECT plan_revision, plan_json, updated_at FROM session_plan WHERE session_id = ?",
        session_id,
    )
    assert after == stored | {"plan_json": malformed_json}
    assert db.one(
        "SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ?", session_id,
    )["n"] == before_prepared == 0
