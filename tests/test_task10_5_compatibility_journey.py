"""Integrated acceptance for the Task 10.5 compatibility journey."""
from __future__ import annotations

import io
import json
from pathlib import Path
import uuid

import db
import main
from backend import resource_selection
from backend import resource_store


def _create_selection(client) -> dict:
    response = client.post(
        "/api/resources/import-selections",
        json={"request_id": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload(client, selection_id: str, name: str, payload: bytes) -> dict:
    response = client.post(
        f"/api/resources/import-selections/{selection_id}/files",
        files={"file": (name, io.BytesIO(payload), "application/json")},
        data={"upload_id": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _choose_target(client, selection_id: str, view: dict, file_id: str, key: str) -> dict:
    response = client.patch(
        f"/api/resources/import-selections/{selection_id}/files/{file_id}",
        json={
            "expected_revision": view["selection_revision"],
            "effective_library_key": key,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _preview(client, selection_id: str, revision: int) -> dict:
    response = client.post(
        f"/api/resources/import-selections/{selection_id}/preview",
        json={"expected_revision": revision},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _commit(client, selection_id: str, preview: dict) -> dict:
    response = client.post(
        f"/api/resources/import-selections/{selection_id}/commit",
        json={
            "expected_revision": preview["selection_revision"],
            "preview_token": preview["preview"]["preview_token"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _revision_rows(keys: tuple[str, ...]) -> list[dict]:
    marks = ",".join("?" for _ in keys)
    return db.q(
        "SELECT rl.library_key, ar.source_id, ar.content_digest, ar.payload, "
        "ar.translation, ar.coverage, ar.created_at "
        "FROM asset_revision ar JOIN resource_library rl ON rl.id = ar.library_id "
        f"WHERE rl.library_key IN ({marks}) "
        "ORDER BY rl.library_key, ar.source_id, ar.content_digest",
        *keys,
    )


def _canonical_resource_rows() -> dict[str, list[dict]]:
    return {
        "libraries": db.q("SELECT * FROM resource_library ORDER BY id"),
        "revisions": db.q("SELECT * FROM asset_revision ORDER BY id"),
        "auxiliary": db.q("SELECT * FROM auxiliary_resource ORDER BY id"),
    }


def _upload_and_import(client, *, filename: str, payload: bytes, target: str | None = None) -> tuple[dict, dict]:
    created = _create_selection(client)
    selection_id = created["selection_id"]
    uploaded = _upload(client, selection_id, filename, payload)
    file_id = uploaded["files"][0]["file_id"]
    view = uploaded
    if target is not None:
        view = _choose_target(client, selection_id, view, file_id, target)
    preview = _preview(client, selection_id, view["selection_revision"])
    return _commit(client, selection_id, preview), preview


def test_compatibility_import_replay_cleanup_and_fused_adaptation(
    client, seeded, monkeypatch,
):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    legacy_key = f"task105_history_{uuid.uuid4().hex[:10]}"
    new_key = f"task105_declared_{uuid.uuid4().hex[:10]}"
    items_key = f"task105_items_{uuid.uuid4().hex[:10]}"
    duplicates_key = f"task105_duplicates_{uuid.uuid4().hex[:10]}"
    source_entry = {
        "id": "portrait-room-001",
        "label": "Invented portrait room",
        "scene_theme": "Soft daylight falls across a quiet portrait studio.",
    }
    historical_bytes = json.dumps(
        {"library": legacy_key, "items": [source_entry]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    # Establish real history through the browser selection API.
    first = _create_selection(client)
    first_id = first["selection_id"]
    first_upload = _upload(client, first_id, "historical_rooms.json", historical_bytes)
    first_preview = _preview(client, first_id, first_upload["selection_revision"])
    assert first_preview["preview"]["report"]["summary"]["new"] == 1
    first_commit = _commit(client, first_id, first_preview)
    assert first_commit["commit_result"]["summary"]["recorded"] == 1
    historical = _revision_rows((legacy_key,))
    assert len(historical) == 1
    assert json.loads(historical[0]["payload"]) == source_entry

    # A new declaration overlaps the historical identity. The safe view asks
    # for a destination; choosing the old library yields an unchanged revision.
    rekeyed_bytes = json.dumps(
        {"library": new_key, "items": [source_entry]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    selection = _create_selection(client)
    selection_id = selection["selection_id"]
    uploaded = _upload(client, selection_id, "declared_new_key.json", rekeyed_bytes)
    file_id = uploaded["files"][0]["file_id"]
    assert uploaded["files"][0]["declared_library"] == new_key
    assert uploaded["files"][0]["effective_library_key"] is None
    staged_path = Path(db.one(
        "SELECT staged_path FROM resource_selection_file WHERE selection_id = ? AND file_id = ?",
        selection_id, file_id,
    )["staged_path"])
    assert staged_path.read_bytes() == rekeyed_bytes

    targeted = _choose_target(client, selection_id, uploaded, file_id, legacy_key)
    assert targeted["files"][0]["effective_library_key"] == legacy_key
    historical_preview = _preview(client, selection_id, targeted["selection_revision"])
    historical_summary = historical_preview["preview"]["report"]["summary"]
    assert historical_summary["accepted"] == 1
    assert historical_summary["unchanged"] == 1
    assert historical_summary["new"] == historical_summary["updated"] == 0
    assert historical_preview["preview"]["committable"] is True
    canonical_before_commit = _canonical_resource_rows()

    cleanup_attempts = 0
    original_delete = resource_selection._safe_delete_file

    def fail_cleanup(path, staging_root=None):
        nonlocal cleanup_attempts
        cleanup_attempts += 1
        return False, "simulated transient cleanup failure"

    monkeypatch.setattr(resource_selection, "_safe_delete_file", fail_cleanup)
    committed = _commit(client, selection_id, historical_preview)
    assert committed["state"] == "committed"
    stored_result = committed["commit_result"]
    assert stored_result["summary"]["unchanged"] == 1
    assert committed["cleanup_warning"] is not None
    assert "simulated transient" not in committed["cleanup_warning"]
    assert staged_path.exists()
    assert _revision_rows((legacy_key,)) == historical
    assert _canonical_resource_rows() == canonical_before_commit

    def forbidden_canonical_commit(**_kwargs):
        raise AssertionError("A committed selection replay entered canonical import.")

    original_canonical_commit = resource_selection.resource_service.commit_selection_import
    monkeypatch.setattr(
        resource_selection.resource_service,
        "commit_selection_import",
        forbidden_canonical_commit,
    )
    replay_response = client.post(
        f"/api/resources/import-selections/{selection_id}/commit",
        json={
            "expected_revision": historical_preview["selection_revision"],
            "preview_token": historical_preview["preview"]["preview_token"],
        },
    )
    assert replay_response.status_code == 200, replay_response.text
    replay = replay_response.json()
    assert replay["state"] == "committed"
    assert replay["commit_result"] == stored_result
    assert _revision_rows((legacy_key,)) == historical
    assert _canonical_resource_rows() == canonical_before_commit
    assert cleanup_attempts >= 2

    monkeypatch.setattr(resource_selection, "_safe_delete_file", original_delete)
    cleaned = client.get(f"/api/resources/import-selections/{selection_id}")
    assert cleaned.status_code == 200, cleaned.text
    assert cleaned.json()["cleanup_warning"] is None
    assert not staged_path.exists()
    assert _revision_rows((legacy_key,)) == historical
    assert _canonical_resource_rows() == canonical_before_commit

    post_cleanup_replay_response = client.post(
        f"/api/resources/import-selections/{selection_id}/commit",
        json={
            "expected_revision": historical_preview["selection_revision"],
            "preview_token": historical_preview["preview"]["preview_token"],
        },
    )
    assert post_cleanup_replay_response.status_code == 200, post_cleanup_replay_response.text
    post_cleanup_replay = post_cleanup_replay_response.json()
    assert post_cleanup_replay["state"] == "committed"
    assert post_cleanup_replay["commit_result"] == stored_result
    assert post_cleanup_replay["cleanup_warning"] is None
    assert not staged_path.exists()
    assert _canonical_resource_rows() == canonical_before_commit
    monkeypatch.setattr(
        resource_selection.resource_service,
        "commit_selection_import",
        original_canonical_commit,
    )

    # A changed payload adds a new immutable revision; the import report's
    # updated count corresponds to the new durable digest while v1 remains.
    updated_entry = {
        **source_entry,
        "label": "Updated portrait room",
        "scene_theme": "Warm afternoon light falls across the portrait studio.",
    }
    updated_bytes = json.dumps(
        {"library": legacy_key, "items": [updated_entry]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    update_commit, update_preview = _upload_and_import(
        client, filename="updated_historical_room.json", payload=updated_bytes,
    )
    update_summary = update_preview["preview"]["report"]["summary"]
    assert update_summary["accepted"] == update_summary["updated"] == 1
    assert update_summary["new"] == update_summary["unchanged"] == 0
    assert update_commit["commit_result"]["summary"]["recorded"] == 1
    updated_history = _revision_rows((legacy_key,))
    assert len(updated_history) == 2
    assert historical[0] in updated_history
    assert any(
        json.loads(row["payload"]) == updated_entry
        and row["content_digest"] != historical[0]["content_digest"]
        for row in updated_history
    )

    # Commit a collection-only items object with no declared library.
    items = [
        {"id": "items-only-001", "label": "Invented window light", "scene_theme": "Cool morning light at a studio window."},
        {"id": "items-only-002", "label": "Invented warm backdrop", "scene_theme": "A warm paper backdrop in a small portrait studio."},
    ]
    items_bytes = json.dumps({"items": items}, ensure_ascii=False, indent=2).encode("utf-8")
    items_created = _create_selection(client)
    items_id = items_created["selection_id"]
    items_upload = _upload(client, items_id, "items_only_rooms.json", items_bytes)
    items_file_id = items_upload["files"][0]["file_id"]
    assert items_upload["files"][0]["declared_library"] is None
    assert items_upload["files"][0]["effective_library_key"] is None
    items_staged = Path(db.one(
        "SELECT staged_path FROM resource_selection_file WHERE selection_id = ? AND file_id = ?",
        items_id, items_file_id,
    )["staged_path"])
    assert items_staged.read_bytes() == items_bytes
    items_target = _choose_target(client, items_id, items_upload, items_file_id, items_key)
    items_preview = _preview(client, items_id, items_target["selection_revision"])
    items_summary = items_preview["preview"]["report"]["summary"]
    assert items_summary["inputs"] == items_summary["accepted"] == items_summary["new"] == 2
    assert items_summary["unresolved"] == 0
    items_commit = _commit(client, items_id, items_preview)
    assert items_commit["commit_result"]["summary"]["recorded"] == 2
    stored_items = _revision_rows((items_key,))
    assert [json.loads(row["payload"]) for row in stored_items] == items
    assert not items_staged.exists()

    # Two files assigned to one explicit library retain canonical duplicate
    # accounting: all four duplicate occurrences are excluded, two unique rows persist.
    duplicates_a = [
        {"id": "unique-a", "label": "Unique A", "scene_theme": "A small studio with pale curtains."},
        {"id": "duplicate-same", "label": "Shared wording", "scene_theme": "A bright studio."},
        {"id": "duplicate-different", "label": "Original wording", "scene_theme": "A blue backdrop."},
    ]
    duplicates_b = [
        {"id": "unique-b", "label": "Unique B", "scene_theme": "A studio with warm paper."},
        {"id": "duplicate-same", "label": "Shared wording", "scene_theme": "A bright studio."},
        {"id": "duplicate-different", "label": "Changed wording", "scene_theme": "A green backdrop."},
    ]
    duplicate_id = _create_selection(client)["selection_id"]
    duplicate_view = _upload(client, duplicate_id, "duplicates_a.json", json.dumps(duplicates_a).encode())
    second_file_view = _upload(client, duplicate_id, "duplicates_b.json", json.dumps(duplicates_b).encode())
    first_file = next(
        file for file in second_file_view["files"]
        if file["file_name"] == "duplicates_a.json"
    )
    stale_choice = client.patch(
        f"/api/resources/import-selections/{duplicate_id}/files/{first_file['file_id']}",
        json={
            "expected_revision": duplicate_view["selection_revision"],
            "effective_library_key": duplicates_key,
        },
    )
    assert stale_choice.status_code == 409
    current_duplicate_view = client.get(
        f"/api/resources/import-selections/{duplicate_id}"
    )
    assert current_duplicate_view.status_code == 200, current_duplicate_view.text
    assert next(
        file for file in current_duplicate_view.json()["files"]
        if file["file_id"] == first_file["file_id"]
    )["effective_library_key"] is None
    duplicate_view = _choose_target(
        client, duplicate_id, current_duplicate_view.json(), first_file["file_id"], duplicates_key,
    )
    second_file = next(
        file for file in second_file_view["files"]
        if file["file_name"] == "duplicates_b.json"
    )
    duplicate_view = _choose_target(
        client, duplicate_id, duplicate_view, second_file["file_id"], duplicates_key,
    )
    duplicate_preview = _preview(client, duplicate_id, duplicate_view["selection_revision"])
    duplicate_summary = duplicate_preview["preview"]["report"]["summary"]
    assert duplicate_summary["files"] == 2
    assert duplicate_summary["inputs"] == 6
    assert duplicate_summary["duplicates"] == 4
    assert duplicate_summary["accepted"] == duplicate_summary["new"] == 2
    duplicate_commit = _commit(client, duplicate_id, duplicate_preview)
    assert duplicate_commit["commit_result"]["summary"]["recorded"] == 2
    assert duplicate_commit["commit_result"]["summary"]["duplicates"] == 4
    durable_duplicates = _revision_rows((duplicates_key,))
    assert {row["source_id"] for row in durable_duplicates} == {"unique-a", "unique-b"}

    # A ready fused revision remains intact while the advanced editor stores
    # and applies one separately reviewed adaptation for the current take.
    fused_key = f"task105_fused_{uuid.uuid4().hex[:10]}"
    fused_id = "fused-scene-001"
    source_prompt = "A woman wearing a red dress stands beside a tall studio window."
    fused_payload = {"id": fused_id, "prompt": source_prompt}
    fused_library_id = resource_store.ensure_library(
        fused_key, display_name="Task 10.5 fused demo", kind="fused_scenes",
    )
    fused_revision_id = resource_store.record_revision(
        fused_library_id,
        fused_id,
        fused_payload,
        translation={"prompt": source_prompt},
    )
    fused_revision = resource_store.get_revision(revision_id=fused_revision_id)
    fused_triple = {
        "library_key": fused_key,
        "source_id": fused_id,
        "content_digest": fused_revision["content_digest"],
    }
    fused_library = client.get(f"/api/resources/libraries/{fused_key}")
    assert fused_library.status_code == 200, fused_library.text
    assert fused_library.json()["revisions"][0]["readiness"]["status"] == "ready"

    session = client.post("/api/sessions", json={
        "model_id": seeded["model_id"],
        "name": "Task 10.5 fused adaptation",
        "composition_mode": "resource-v1",
    })
    assert session.status_code == 200, session.text
    session_id = session.json()["id"]
    plan = {
        "version": "resource-v1",
        "look": "",
        "initial_wardrobe": "A blue denim jacket.",
        "takes": [{
            "take_id": "take-001",
            "camera": "50mm portrait lens",
            "framing": "medium portrait",
            "pose": "standing beside the window",
            "expression": "calm and attentive",
        }],
        "selected_resources": [fused_triple],
        "wardrobe_changes": [],
    }
    saved_plan = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": plan, "expected_revision": 0},
    )
    assert saved_plan.status_code == 200, saved_plan.text
    plan_revision = saved_plan.json()["plan_revision"]

    review = client.get(f"/api/sessions/{session_id}/plan/takes/take-001/review")
    assert review.status_code == 200, review.text
    review_body = review.json()
    assert len(review_body["conflicts"]) == 1
    assert review_body["fused_descriptions"][0]["descriptive_inputs"]["prompt"] == source_prompt
    conflict = review_body["conflicts"][0]
    prepare_before_adaptation = client.post(
        f"/api/sessions/{session_id}/plan/takes/take-001/prepare",
        json={"plan_revision": plan_revision},
    )
    assert prepare_before_adaptation.status_code == 422
    assert db.q(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ?",
        session_id, plan_revision,
    ) == []
    assert db.q(
        "SELECT * FROM session_plan_approval WHERE session_id = ?",
        session_id,
    ) == []
    assert db.q("SELECT * FROM shot WHERE session_id = ?", session_id) == []

    approved_value = "A woman wearing a blue denim jacket stands beside the tall studio window."
    adapted = client.post(
        f"/api/sessions/{session_id}/plan/takes/take-001/adaptations",
        json={
            "plan_revision": plan_revision,
            "adaptation": {
                "library_key": conflict["library_key"],
                "source_id": conflict["source_id"],
                "content_digest": conflict["content_digest"],
                "resource_field": conflict["resource_field"],
                "adapted_value": approved_value,
            },
        },
    )
    assert adapted.status_code == 200, adapted.text

    reviewed = client.get(f"/api/sessions/{session_id}/plan/takes/take-001/review")
    assert reviewed.status_code == 200, reviewed.text
    reviewed_body = reviewed.json()
    assert reviewed_body["conflicts"] == []
    assert reviewed_body["adaptations"][0]["source_value"] == source_prompt
    assert reviewed_body["adaptations"][0]["adapted_value"] == approved_value
    assert reviewed_body["fused_descriptions"][0]["descriptive_inputs"]["prompt"] == source_prompt

    prepared = client.post(
        f"/api/sessions/{session_id}/plan/takes/take-001/prepare",
        json={"plan_revision": plan_revision},
    )
    assert prepared.status_code == 200, prepared.text
    assert approved_value in prepared.json()["final_prompt"]
    evidence = prepared.json()["provenance"]["adaptations"]
    assert evidence[0] == {
        "library_key": fused_key,
        "source_id": fused_id,
        "content_digest": fused_triple["content_digest"],
        "resource_field": "prompt",
        "adapted_value": approved_value,
    }
    persisted_revision = resource_store.get_revision(revision_id=fused_revision_id)
    assert persisted_revision["payload"] == fused_payload
    assert persisted_revision["translation"] == {"prompt": source_prompt}
    prepared_rows = db.q(
        "SELECT status, linked_shot_id FROM prepared_take "
        "WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, plan_revision, "take-001",
    )
    assert prepared_rows == [{"status": "ready", "linked_shot_id": None}]
    assert db.q(
        "SELECT * FROM session_plan_approval WHERE session_id = ?",
        session_id,
    ) == []
    assert db.q("SELECT * FROM shot WHERE session_id = ?", session_id) == []
    assert client.get(f"/api/sessions/{session_id}").json()["shots"] == []
