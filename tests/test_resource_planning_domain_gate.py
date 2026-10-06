"""Direct-caller checks for the live resource-planning write boundary."""
from __future__ import annotations

import asyncio
import sys

import pytest

import db
from backend import (
    authoring_operations,
    guided_sessions,
    photo_extraction,
    photo_staging,
    resource_planning,
    resource_preparation,
    resource_selection,
    resource_translation,
    saved_looks,
    session_plan,
)


def test_standalone_feature_flag_reads_config_and_environment_override(
    tmp_path, monkeypatch,
):
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setenv("IDEVGEN_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delitem(sys.modules, "main", raising=False)
    monkeypatch.delitem(sys.modules, "backend.main", raising=False)
    config_path = tmp_path / "config.json"
    config_path.write_text('{"resource_planning_enabled": false}', encoding="utf-8")

    assert resource_planning.is_enabled() is False

    monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "true")
    assert resource_planning.is_enabled() is True
    monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "off")
    assert resource_planning.is_enabled() is False


def test_resource_planning_domain_writes_are_gated_before_validation(
    client, monkeypatch,
):
    monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "false")
    before_changes = db.conn().total_changes

    session_writes = (
        lambda: session_plan.save_draft(0, None, 0),
        lambda: session_plan.approve_plan_review(0, 0),
        lambda: session_plan.begin_preparation(0, 0, ""),
        lambda: session_plan.complete_preparation(
            0, 0, "", final_prompt="", effective_state={},
            mapping_version="", compiler_version="", provenance={},
        ),
        lambda: session_plan.complete_authoring_preparation(None),
        lambda: session_plan.record_writer_synthesis(
            0, 0, "", effective_state={}, mapping_version="",
            compiler_version="", provenance={},
        ),
        lambda: session_plan.apply_shared_suggestion_acceptance(
            0, 0, expected_fields=[], evidence={}, accepted={},
        ),
        lambda: session_plan.apply_saved_look(
            0, 0, "", 0, {}, planning_enabled=True,
        ),
        lambda: session_plan.preview_wardrobe_progression(
            0, 0, "", "", [], "", planning_enabled=True,
        ),
        lambda: session_plan.apply_wardrobe_progression_preview(
            0, 0, None, None, None, planning_enabled=True,
        ),
        lambda: session_plan.refresh_resources(0, 0, planning_enabled=True),
    )
    for call in session_writes:
        with pytest.raises(session_plan.ResourcePlanningDisabled):
            call()

    selection_writes = (
        lambda: resource_selection.create_or_replay_selection(""),
        lambda: resource_selection.reserve_file_slot("", "", ""),
        lambda: resource_selection.reserve_file_bytes("", "", 1),
        lambda: resource_selection.stage_file_chunk("", "", b"x"),
        lambda: resource_selection.finalize_staged_file("", ""),
        lambda: resource_selection.remove_staged_file("", ""),
        lambda: resource_selection.update_file_targets("", "", 0, effective_library_key="x"),
        lambda: resource_selection.preview_selection("", 0),
        lambda: resource_selection.commit_selection("", 0, ""),
        lambda: resource_selection.save_preview("", 0, "", "0" * 64, False, {}),
        lambda: resource_selection.acquire_commit_claim("", 0, "", ""),
        lambda: resource_selection.record_commit_result("", "", {}),
    )
    for call in selection_writes:
        with pytest.raises(resource_selection.ResourcePlanningDisabledError):
            call()

    translation_writes = (
        lambda: resource_translation.preview_translation_map("", {}),
        lambda: resource_translation.apply_translation_map("", {}, ""),
        lambda: resource_translation.apply_revision_translation("", "", "", {}),
    )
    for call in translation_writes:
        with pytest.raises(resource_translation.ResourcePlanningDisabledError):
            call()

    with pytest.raises(guided_sessions.GuidedSessionError) as guided:
        guided_sessions.create_or_replay(None)
    assert guided.value.status_code == 503
    assert guided.value.code == "resource_planning_disabled"

    with pytest.raises(authoring_operations.AuthoringOperationError) as operation:
        authoring_operations.start_operation(
            1, {}, planning_enabled=True, assistant_available=False,
        )
    assert operation.value.status_code == 503
    assert operation.value.code == "resource_planning_disabled"

    look_writes = (
        lambda: saved_looks.create({}),
        lambda: saved_looks.create_version("", 0, {}),
        lambda: saved_looks.preview_portable_import(None),
        lambda: saved_looks.commit_portable_import(None, "", "", ""),
        lambda: saved_looks.preview_legacy_import(None),
        lambda: saved_looks.commit_legacy_import(None, "", "", ""),
        lambda: saved_looks.save_photo_evidence(
            {}, metadata={}, request_projection={}, output={}, corrections={},
        ),
    )
    for call in look_writes:
        with pytest.raises(saved_looks.SavedLookError) as look_error:
            call()
        assert look_error.value.status_code == 503
        assert look_error.value.code == "resource_planning_disabled"

    photo_writes = (
        lambda: photo_staging.create_stage(b"x"),
        lambda: photo_staging.save_look("", {}, lambda _: None),
        lambda: photo_extraction.save_review("", {}, {}, lambda _: None, lambda *_: None),
    )
    for call in photo_writes:
        with pytest.raises(photo_staging.PhotoStageError) as photo_error:
            call()
        assert photo_error.value.status_code == 503
        assert photo_error.value.code == "resource_planning_disabled"

    with pytest.raises(resource_preparation.ResourcePlanningDisabled):
        resource_preparation.record_take_adaptation(0, 0, "", {})
    with pytest.raises(resource_preparation.ResourcePlanningDisabled):
        resource_preparation.finalize_take_preparation(0, 0, "")
    with pytest.raises(resource_preparation.ResourcePlanningDisabled):
        resource_preparation.synthesize_unlocked_fields(0, 0, "")

    provider_calls = []

    async def provider(*_args, **_kwargs):
        provider_calls.append(True)
        return {}

    with pytest.raises(photo_extraction.PhotoExtractionError) as extraction:
        asyncio.run(photo_extraction.extract("", {}, provider, lambda: True))
    assert extraction.value.status_code == 503
    assert extraction.value.code == "resource_planning_disabled"
    assert provider_calls == []
    assert db.conn().total_changes == before_changes


def test_prepared_write_error_mapper_preserves_live_gate_status():
    import main

    error = main._prepared_take_http_error(
        session_plan.ResourcePlanningDisabled("Resource planning is disabled."),
    )
    assert error.status_code == 503


def test_save_plan_draft_maps_disable_race_to_503(
    client, seeded, monkeypatch,
):
    import main

    session_id = db.run(
        "INSERT INTO session (model_id, workflow_id, name, settings, created_at) "
        "VALUES (?, ?, 'Flag race', '{\"composition_mode\":\"resource-v1\"}', 'now')",
        seeded["model_id"], seeded["workflow_id"],
    )
    enabled_then_disabled = iter((True, False))
    monkeypatch.setattr(
        main, "is_resource_planning_enabled", lambda: next(enabled_then_disabled),
    )

    response = client.post(f"/api/sessions/{session_id}/plan", json={
        "expected_revision": 0,
        "plan": {
            "version": "resource-v1",
            "look": "studio light",
            "initial_wardrobe": "t-shirt",
            "takes": [{"take_id": "take_1", "wardrobe": None}],
        },
    })

    assert response.status_code == 503
    assert "Resource planning is disabled" in response.json()["detail"]
    assert db.one(
        "SELECT id FROM session_plan WHERE session_id=?", session_id,
    ) is None
