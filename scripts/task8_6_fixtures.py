"""Export the task 8.6 HTTP contract from an isolated FastAPI test client.

Run ``python scripts/task8_6_fixtures.py --write`` to refresh the checked-in
snapshot, or ``--check`` to prove it still matches fresh backend responses.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "frontend" / "src" / "views" / "__fixtures__" / "task_8_6_backend_contract.json"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

STAMP = "2026-01-02T03:04:05+00:00"
_ID_MAP: dict[tuple[str, str], str] = {}
_ID_COUNTS: dict[str, int] = {}


def _normalize(value, key=""):
    if isinstance(value, dict):
        return {name: _normalize(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item, key) for item in value]
    if isinstance(value, str):
        if key == "operation_id" and value == "50000000-0000-4000-8000-000000000001":
            return value
        if key in {"selection_id", "file_id", "operation_id"}:
            original_key = (key, value)
            if original_key not in _ID_MAP:
                _ID_COUNTS[key] = _ID_COUNTS.get(key, 0) + 1
                number = _ID_COUNTS[key]
                if key == "operation_id":
                    _ID_MAP[original_key] = f"00000000-0000-4000-8000-{number:012d}"
                elif key == "selection_id":
                    _ID_MAP[original_key] = f"sel_{number:032x}"
                else:
                    _ID_MAP[original_key] = f"file_{number:032x}"
            return _ID_MAP[original_key]
        if key.endswith("_at") or key in {"expires_at", "lease_expires_at"}:
            return STAMP
        if key == "data_dir_resolved":
            return "<isolated-demo-data>"
        return value
    return value


def _response(response):
    return {"status": response.status_code, "body": response.json()}


def _graph():
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "invented-base.safetensors"}},
        "2": {"class_type": "LoraLoader", "inputs": {
            "lora_name": "characters/ada.safetensors", "strength_model": 0.7,
            "strength_clip": 1.0, "model": ["1", 0], "clip": ["1", 1],
        }},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 1]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 1]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {
            "seed": 1, "steps": 8, "cfg": 1.0, "sampler_name": "euler",
            "scheduler": "normal", "denoise": 1.0, "model": ["2", 0],
            "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0],
        }},
        "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "demo", "images": ["6", 0]}},
    }


def _room_anchor(resource_store):
    library_id = resource_store.ensure_library("task86_synthetic_rooms", kind="rooms")
    revision_id = resource_store.record_revision(
        library_id,
        "studio-001",
        {"id": "studio-001", "label": "Invented studio", "scene_theme": "Soft daylight in a quiet studio."},
        translation={"label": "Invented studio", "scene_theme": "Soft daylight in a quiet studio."},
    )
    revision = resource_store.get_revision(revision_id=revision_id)
    return {
        "library_key": "task86_synthetic_rooms",
        "source_id": "studio-001",
        "content_digest": revision["content_digest"],
    }


def _guide(client, *, model_id, anchor, mode, request_id, look="", wardrobe="", photo_count=2):
    request = {
        "request_id": request_id,
        "character_id": model_id,
        "scene_anchor": anchor,
        "photo_count": photo_count,
        "brief": "Two calm portraits in an invented studio.",
        "mode": mode,
        "look": look,
        "initial_wardrobe": wardrobe,
    }
    response = client.post("/api/sessions/guided", json=request)
    assert response.status_code == 201, response.text
    return request, response


def _build_snapshot():
    from fastapi.testclient import TestClient

    _ID_MAP.clear()
    _ID_COUNTS.clear()

    with tempfile.TemporaryDirectory(prefix="task86-fixture-") as temporary:
        temporary_path = Path(temporary)
        config_path = temporary_path / "config.json"
        config_path.write_text(json.dumps({
            "comfy_url": "http://comfy.invalid",
            "comfy_output_dir": "",
            "lora_dir": "",
            "data_dir": "isolated-demo-data",
            "resource_planning_enabled": True,
            "llm_url": "http://assistant.invalid/v1",
            "llm_model": "synthetic-assistant",
            "llm_vision_model": "",
            "llm_key": "",
            "checkpoints": {},
            "room_libraries": [],
        }, indent=2), encoding="utf-8")
        os.environ["IDEVGEN_CONFIG"] = str(config_path)
        os.environ["IDEVGEN_DATA_DIR"] = str(temporary_path / "data")

        import db
        import main
        import resource_store
        from backend import authoring_operations

        main._schedule_shared_suggestion_operation = lambda _view: None
        fixtures = {
            "_contract": {
                "source": "FastAPI TestClient responses from isolated temporary config and data directories",
                "normalizations": {
                    "canonical_selection_id": "sel_00000000000000000000000000000001",
                    "canonical_file_ids": ["file_00000000000000000000000000000001", "file_00000000000000000000000000000002"],
                    "canonical_operation_id": "00000000-0000-4000-8000-000000000001",
                    "partial_operation_id": "50000000-0000-4000-8000-000000000001 (fixed by the isolated worker harness and preserved with provenance digests)",
                    "timestamps": STAMP,
                    "config.data_dir_resolved": "<isolated-demo-data>",
                },
                "responses": {
                    "libraries": "GET /api/resources/libraries",
                    "models": "GET /api/models",
                    "workflows": "GET /api/workflows",
                    "config": "GET /api/config",
                    "selectionCreated": "POST /api/resources/import-selections",
                    "selectionUploaded": "POST /api/resources/import-selections/{selection_id}/files",
                    "selectionPatched": "PATCH /api/resources/import-selections/{selection_id}/files/{file_id}",
                    "selectionStale": "PATCH /api/resources/import-selections/{selection_id}/files/{file_id} with old expected_revision",
                    "guidedCreated": "POST /api/sessions/guided",
                    "guidedReplay": "POST /api/sessions/guided using the identical request_id and body",
                "workflowError": "POST /api/sessions/guided with a model whose default workflow disappeared after inventory load",
                    "session": "GET /api/sessions/{session_id}",
                    "automaticReview": "GET /api/sessions/{session_id}/plan/review?plan_revision={plan_revision}",
                    "plan": "GET /api/sessions/{session_id}/plan",
                    "prepared": "POST /api/sessions/{session_id}/plan/preparations/prepare",
                    "review": "GET /api/sessions/{session_id}/plan/review?plan_revision={plan_revision}",
                    "operationStarted": "POST /api/sessions/{session_id}/plan/authoring/operations",
                    "operationConflict": "POST /api/sessions/{session_id}/plan/authoring/operations while another operation is active",
                    "operationProgress": "GET /api/sessions/{session_id}/plan/authoring/operations/{operation_id}",
                },
            },
        }

        try:
            with TestClient(main.app) as client:
                anchor = _room_anchor(resource_store)
                workflow_response = client.post("/api/workflows", json={"name": "Synthetic task 8.6 workflow", "graph": _graph()})
                assert workflow_response.status_code == 200, workflow_response.text
                workflow = workflow_response.json()
                model_response = client.post("/api/models", json={
                    "name": "Invented Ada model",
                    "lora_name": "characters/ada.safetensors",
                    "trigger": "4da woman",
                    "base_positive": "portrait photograph",
                    "base_negative": "blur",
                    "workflow_id": workflow["id"],
                    "settings": {"width": 832, "height": 1216, "steps": 8, "cfg": 1.0},
                })
                assert model_response.status_code == 200, model_response.text
                model = model_response.json()
                missing_workflow_model_response = client.post("/api/models", json={
                    "name": "Invented model without a workflow",
                    "lora_name": "characters/ada.safetensors",
                    "trigger": "4da woman",
                    "base_positive": "portrait photograph",
                    "base_negative": "blur",
                    "workflow_id": None,
                    "settings": {"width": 832, "height": 1216, "steps": 8, "cfg": 1.0},
                })
                assert missing_workflow_model_response.status_code == 200, missing_workflow_model_response.text
                missing_workflow_model = missing_workflow_model_response.json()

                selection_request = {"request_id": "10000000-0000-4000-8000-000000000001"}
                selection_created_response = client.post("/api/resources/import-selections", json=selection_request)
                assert selection_created_response.status_code == 201, selection_created_response.text
                selection_id = selection_created_response.json()["selection_id"]
                upload_response = client.post(
                    f"/api/resources/import-selections/{selection_id}/files",
                    files={"file": ("invented-room.json", b'{"rooms":[{"id":"studio-002","label":"Invented annex"}]}', "application/json")},
                    data={"upload_id": "20000000-0000-4000-8000-000000000001"},
                )
                assert upload_response.status_code == 201, upload_response.text
                upload_second_response = client.post(
                    f"/api/resources/import-selections/{selection_id}/files",
                    files={"file": ("invented-annex.json", b'{"rooms":[{"id":"studio-003","label":"Invented gallery"}]}', "application/json")},
                    data={"upload_id": "20000000-0000-4000-8000-000000000002"},
                )
                assert upload_second_response.status_code == 201, upload_second_response.text
                file_id, second_file_id = [item["file_id"] for item in upload_second_response.json()["files"]]
                expected_revision = upload_second_response.json()["selection_revision"]
                patch_response = client.patch(
                    f"/api/resources/import-selections/{selection_id}/files/{file_id}",
                    json={"expected_revision": expected_revision, "effective_library_key": "task86_synthetic_rooms"},
                )
                assert patch_response.status_code == 200, patch_response.text
                stale_after_first_response = client.patch(
                    f"/api/resources/import-selections/{selection_id}/files/{second_file_id}",
                    json={"expected_revision": expected_revision, "effective_library_key": "task86_synthetic_rooms"},
                )
                assert stale_after_first_response.status_code == 409, stale_after_first_response.text
                patch_second_response = client.patch(
                    f"/api/resources/import-selections/{selection_id}/files/{second_file_id}",
                    json={"expected_revision": patch_response.json()["selection_revision"], "effective_library_key": "task86_synthetic_rooms"},
                )
                assert patch_second_response.status_code == 200, patch_second_response.text
                stale_response = client.patch(
                    f"/api/resources/import-selections/{selection_id}/files/{file_id}",
                    json={"expected_revision": patch_response.json()["selection_revision"], "effective_library_key": "task86_synthetic_rooms"},
                )
                assert stale_response.status_code == 409, stale_response.text
                fixtures["selection"] = {
                    "request": selection_request,
                    "created": _response(selection_created_response),
                    "uploadedFirst": _response(upload_response),
                    "uploaded": _response(upload_second_response),
                    "patched": _response(patch_response),
                    "staleAfterFirst": _response(stale_after_first_response),
                    "patchedSecond": _response(patch_second_response),
                    "staleLatest": _response(stale_response),
                }

                automatic_request, automatic_response = _guide(
                    client, model_id=model["id"], anchor=anchor, mode="automatic",
                    request_id="30000000-0000-4000-8000-000000000001",
                    look="Soft daylight in an invented studio.",
                    wardrobe="A white cotton shirt and dark denim trousers.",
                )
                automatic = automatic_response.json()
                automatic_id = automatic["session_id"]
                auto_plan_response = client.get(f"/api/sessions/{automatic_id}/plan")
                assert auto_plan_response.status_code == 200, auto_plan_response.text
                auto_review_response = client.get(
                    f"/api/sessions/{automatic_id}/plan/review?plan_revision=1"
                )
                assert auto_review_response.status_code == 200, auto_review_response.text

                manual_request, manual_response = _guide(
                    client, model_id=model["id"], anchor=anchor, mode="manual",
                    request_id="30000000-0000-4000-8000-000000000002",
                    look="A clean invented studio with diffuse daylight.",
                    wardrobe="A linen shirt and dark trousers.",
                )
                manual = manual_response.json()
                manual_id = manual["session_id"]
                manual_plan = manual["plan"]
                manual_plan["takes"] = [
                    {
                        "take_id": "take-001", "label": "First invented portrait",
                        "camera": "50mm eye-level", "framing": "medium portrait",
                        "pose": "standing beside a high window", "expression": "calm gaze",
                    },
                    {
                        "take_id": "take-002", "label": "Second invented portrait",
                        "camera": "85mm at eye-level", "framing": "close portrait",
                        "pose": "seated beside a studio table", "expression": "small smile",
                    },
                ]
                saved_response = client.post(
                    f"/api/sessions/{manual_id}/plan",
                    json={"expected_revision": 1, "plan": manual_plan},
                )
                assert saved_response.status_code == 200, saved_response.text
                current_revision = saved_response.json()["plan_revision"]
                manual_plan_before_preparation_response = client.get(
                    f"/api/sessions/{manual_id}/plan"
                )
                assert manual_plan_before_preparation_response.status_code == 200, manual_plan_before_preparation_response.text
                prepared_response = client.post(
                    f"/api/sessions/{manual_id}/plan/preparations/prepare",
                    json={"plan_revision": current_revision, "take_ids": ["take-001", "take-002"]},
                )
                assert prepared_response.status_code == 200, prepared_response.text
                manual_plan_response = client.get(f"/api/sessions/{manual_id}/plan")
                assert manual_plan_response.status_code == 200, manual_plan_response.text
                review_response = client.get(
                    f"/api/sessions/{manual_id}/plan/review?plan_revision={current_revision}"
                )
                assert review_response.status_code == 200, review_response.text

                replay_response = client.post("/api/sessions/guided", json=automatic_request)
                assert replay_response.status_code == 200, replay_response.text
                workflow_error_sessions_before = client.get("/api/sessions")
                assert workflow_error_sessions_before.status_code == 200, workflow_error_sessions_before.text
                missing_request = {
                    **automatic_request,
                    "request_id": "30000000-0000-4000-8000-000000000003",
                }
                db.run("UPDATE model SET workflow_id = NULL WHERE id = ?", model["id"])
                workflow_error_response = client.post("/api/sessions/guided", json=missing_request)
                assert workflow_error_response.status_code == 422, workflow_error_response.text
                workflow_error_sessions_after = client.get("/api/sessions")
                assert workflow_error_sessions_after.status_code == 200, workflow_error_sessions_after.text
                assert [item["id"] for item in workflow_error_sessions_after.json()] == [
                    item["id"] for item in workflow_error_sessions_before.json()
                ]
                db.run("UPDATE model SET workflow_id = ? WHERE id = ?", workflow["id"], model["id"])

                operation_request = {
                    "request_id": "40000000-0000-4000-8000-000000000001",
                    "expected_revision": 1,
                    "kind": "prepare_takes",
                    "take_ids": ["take-001", "take-002"],
                }
                operation_response = client.post(
                    f"/api/sessions/{automatic_id}/plan/authoring/operations", json=operation_request,
                )
                assert operation_response.status_code == 202, operation_response.text
                operation = operation_response.json()
                operation_path = f"/api/sessions/{automatic_id}/plan/authoring/operations"
                conflict_response = client.post(operation_path, json={
                    **operation_request,
                    "request_id": "40000000-0000-4000-8000-000000000002",
                })
                assert conflict_response.status_code == 409, conflict_response.text
                progress_response = client.get(f"{operation_path}/{operation['operation_id']}")
                assert progress_response.status_code == 200, progress_response.text

                partial_request, partial_session_response = _guide(
                    client, model_id=model["id"], anchor=anchor, mode="automatic",
                    request_id="30000000-0000-4000-8000-000000000004",
                    look="Soft daylight in an invented studio.",
                    wardrobe="A white cotton shirt and dark denim trousers.",
                    photo_count=3,
                )
                partial_session_id = partial_session_response.json()["session_id"]
                partial_plan_response = client.get(f"/api/sessions/{partial_session_id}/plan")
                assert partial_plan_response.status_code == 200, partial_plan_response.text
                partial_review_response = client.get(
                    f"/api/sessions/{partial_session_id}/plan/review?plan_revision=1"
                )
                assert partial_review_response.status_code == 200, partial_review_response.text
                partial_operation_request = {
                    "request_id": "40000000-0000-4000-8000-000000000003",
                    "expected_revision": 1,
                    "kind": "prepare_takes",
                    "take_ids": ["take-001", "take-002", "take-003"],
                }
                partial_operation_path = (
                    f"/api/sessions/{partial_session_id}/plan/authoring/operations"
                )
                assistant_calls = 0
                original_run_structured = main.enhance.run_structured
                original_uuid4 = authoring_operations.uuid.uuid4

                async def synthetic_run_structured(config, prompt, image="", *, request_evidence=None):
                    nonlocal assistant_calls
                    assistant_calls += 1
                    await asyncio.sleep(0.25)
                    if assistant_calls == 2:
                        raise RuntimeError("Synthetic assistant failure for fixture capture")
                    if request_evidence is not None:
                        request_evidence.update({
                            "messages": main.enhance._messages(prompt, image, structured=True),
                            "model": config["llm_model"],
                            "parameters": {
                                "temperature": 0.8,
                                "stream": False,
                                "response_format": {"type": "json_object"},
                                "reasoning_effort": "none",
                            },
                        })
                    return {
                        "camera": "50mm eye-level",
                        "framing": "medium portrait",
                        "pose": "standing beside a studio window",
                        "expression": "calm gaze",
                    }

                try:
                    main.enhance.run_structured = synthetic_run_structured
                    authoring_operations.uuid.uuid4 = lambda: authoring_operations.uuid.UUID(
                        "50000000-0000-4000-8000-000000000001"
                    )
                    try:
                        partial_start_response = client.post(
                            partial_operation_path, json=partial_operation_request,
                        )
                    finally:
                        authoring_operations.uuid.uuid4 = original_uuid4
                    assert partial_start_response.status_code == 202, partial_start_response.text
                    partial_operation = partial_start_response.json()
                    partial_conflict_response = client.post(partial_operation_path, json={
                        **partial_operation_request,
                        "request_id": "40000000-0000-4000-8000-000000000004",
                    })
                    assert partial_conflict_response.status_code == 409, partial_conflict_response.text
                    asyncio.run(main._run_prepare_takes_operation(
                        partial_session_id, partial_operation["operation_id"],
                    ))
                    assert assistant_calls == 2
                    partial_progress_response = client.get(
                        f"{partial_operation_path}/{partial_operation['operation_id']}"
                    )
                    assert partial_progress_response.status_code == 200, partial_progress_response.text
                    partial_progress = partial_progress_response.json()
                    assert partial_progress["state"] == "failed", partial_progress
                    assert partial_progress["progress"]["completed"] == ["take-001"]
                    assert partial_progress["progress"]["failed"]["take_id"] == "take-002"
                    assert partial_progress["progress"]["remaining"] == ["take-003"]
                    partial_plan_after_failure_response = client.get(
                        f"/api/sessions/{partial_session_id}/plan"
                    )
                    assert partial_plan_after_failure_response.status_code == 200, partial_plan_after_failure_response.text
                    partial_review_after_failure_response = client.get(
                        f"/api/sessions/{partial_session_id}/plan/review?plan_revision=1"
                    )
                    assert partial_review_after_failure_response.status_code == 200, partial_review_after_failure_response.text
                    partial_session_view_response = client.get(
                        f"/api/sessions/{partial_session_id}"
                    )
                    assert partial_session_view_response.status_code == 200, partial_session_view_response.text
                finally:
                    authoring_operations.uuid.uuid4 = original_uuid4
                    main.enhance.run_structured = original_run_structured

                fixtures["resources"] = {
                    "libraries": _response(client.get("/api/resources/libraries")),
                    "models": _response(client.get("/api/models")),
                    "workflows": _response(client.get("/api/workflows")),
                    "config": _response(client.get("/api/config")),
                }
                fixtures["guided"] = {
                    "request": automatic_request,
                    "created": _response(automatic_response),
                    "replay": _response(replay_response),
                    "workflowErrorRequest": missing_request,
                    "workflowError": _response(workflow_error_response),
                    "workflowErrorNoOrphan": {
                        "before_session_ids": [item["id"] for item in workflow_error_sessions_before.json()],
                        "after_session_ids": [item["id"] for item in workflow_error_sessions_after.json()],
                    },
                }
                fixtures["automaticSession"] = _response(client.get(f"/api/sessions/{automatic_id}"))
                fixtures["automaticPlan"] = _response(auto_plan_response)
                fixtures["automaticReview"] = _response(auto_review_response)
                fixtures["manualSession"] = _response(client.get(f"/api/sessions/{manual_id}"))
                fixtures["manualPlanBeforePreparation"] = _response(manual_plan_before_preparation_response)
                fixtures["manualPlan"] = _response(manual_plan_response)
                fixtures["manualPreparation"] = {
                    "request": {"plan_revision": current_revision, "take_ids": ["take-001", "take-002"]},
                    "response": _response(prepared_response),
                }
                fixtures["manualReview"] = _response(review_response)
                fixtures["authoringOperation"] = {
                    "request": operation_request,
                    "started": _response(operation_response),
                    "conflict": _response(conflict_response),
                    "progress": _response(progress_response),
                }
                fixtures["partialAuthoringOperation"] = {
                    "session": _response(partial_session_view_response),
                    "planBeforeFailure": _response(partial_plan_response),
                    "reviewBeforeFailure": _response(partial_review_response),
                    "request": partial_operation_request,
                    "started": _response(partial_start_response),
                    "conflict": _response(partial_conflict_response),
                    "progress": _response(partial_progress_response),
                    "planAfterFailure": _response(partial_plan_after_failure_response),
                    "reviewAfterFailure": _response(partial_review_after_failure_response),
                    "assistant_calls": assistant_calls,
                }
                session_list_response = client.get("/api/sessions")
                assert session_list_response.status_code == 200, session_list_response.text
                fixtures["sessions"] = _response(session_list_response)
        finally:
            try:
                db.conn().close()
                db._conn = None
            except Exception:
                pass
        return _normalize(fixtures)


def main_cli():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true", help="write the regenerated fixture")
    action.add_argument("--check", action="store_true", help="compare the fixture with a fresh capture")
    args = parser.parse_args()
    snapshot = json.dumps(_build_snapshot(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.write:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(snapshot, encoding="utf-8")
        print(f"Wrote {OUTPUT.relative_to(ROOT)}")
        return 0
    if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != snapshot:
        print(f"Fixture differs from a fresh backend capture: {OUTPUT.relative_to(ROOT)}", file=sys.stderr)
        return 1
    print(f"Verified {OUTPUT.relative_to(ROOT)} against fresh backend responses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
