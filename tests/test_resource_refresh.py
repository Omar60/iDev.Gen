"""Explicit resource refresh preserves CAS, history, and verified carry-forward."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import db
import main
import pytest

from test_authoring_operation_api import (
    _configure_assistant,
    _create_guided_session,
    _start_body,
    _task76_adapted_ready_session,
)


@pytest.fixture(autouse=True)
def _disable_background_suggestion_dispatch(monkeypatch):
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", lambda view: None)


def _change_scene_theme(session_id, value):
    selected = main.session_plan.get_draft(session_id)["plan"]["selected_resources"][0]
    db.run(
        "UPDATE asset_revision SET translation = ? WHERE library_id = "
        "(SELECT id FROM resource_library WHERE library_key = ?) "
        "AND source_id = ? AND content_digest = ?",
        json.dumps({"label": "invented studio", "scene_theme": value}),
        selected["library_key"], selected["source_id"], selected["content_digest"],
    )


def _refresh(client, session_id, revision=1):
    return client.post(
        f"/api/sessions/{session_id}/plan/refresh-resources",
        json={"expected_revision": revision},
    )


def test_refresh_no_drift_is_write_free_and_stale_retry_conflicts(client, seeded, monkeypatch):
    session_id, source, _ = _task76_adapted_ready_session(client, seeded, monkeypatch)
    before = {
        name: db.q(f"SELECT * FROM {name} WHERE session_id = ?", session_id)
        for name in ("session_plan", "prepared_take", "take_resource_adaptation", "authoring_operation")
    }
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    assert _refresh(client, session_id).status_code == 503
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    unchanged = _refresh(client, session_id)
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["plan_revision"] == 1
    assert unchanged.json()["refreshed"] is False
    assert before == {
        name: db.q(f"SELECT * FROM {name} WHERE session_id = ?", session_id)
        for name in before
    }

    _change_scene_theme(session_id, "A changed authorized scene description.")
    refreshed = _refresh(client, session_id)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["plan_revision"] == 2
    assert refreshed.json()["refreshed"] is True
    assert "take-001" in refreshed.json()["affected_takes"]
    assert _refresh(client, session_id).status_code == 409
    assert _refresh(client, session_id, 2).json()["refreshed"] is False
    assert db.one("SELECT source_value FROM take_resource_adaptation WHERE id = ?", source["id"])["source_value"] == source["source_value"]


def test_refresh_adaptation_before_finalization_and_fresh_approval(client, seeded, monkeypatch):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, mode="manual")
    anchor = main.session_plan.get_draft(session_id)["plan"]["authoring"]["scene_anchor"]
    old = main.resource_preparation.record_take_adaptation(
        session_id, revision, "take-001",
        {**anchor, "resource_field": "scene_theme", "adapted_value": "An invented adaptation."},
    )
    _change_scene_theme(session_id, "A changed authorized scene description.")
    refreshed = _refresh(client, session_id)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["plan_revision"] == 2
    assert "take-001" in refreshed.json()["required_preparation"]
    assert db.one(
        "SELECT source_value FROM take_resource_adaptation WHERE session_id = ? "
        "AND plan_revision = 1 AND take_id = 'take-001'",
        session_id,
    )["source_value"] == old["source_value"]
    fresh = main.resource_preparation.record_take_adaptation(
        session_id, 2, "take-001",
        {**anchor, "resource_field": "scene_theme", "adapted_value": "A new invented adaptation."},
    )
    assert fresh["source_value"] == "A changed authorized scene description."
    _change_scene_theme(session_id, "A second changed authorized description.")
    assert _refresh(client, session_id, 2).json()["plan_revision"] == 3


def test_refresh_missing_translation_creates_blocked_revision(client, seeded, monkeypatch):
    session_id, _, _ = _task76_adapted_ready_session(client, seeded, monkeypatch)
    _change_scene_theme(session_id, "")
    refreshed = _refresh(client, session_id)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["plan_revision"] == 2
    assert any(item["code"] == "resource_dependency_unverifiable" for item in refreshed.json()["diagnostics"])
    with pytest.raises(main.resource_preparation.PreparationError):
        main.resource_preparation.prepare_take_inputs(session_id, 2, "take-001")


def test_refresh_ignores_new_unused_approval_in_ready_digest(client, seeded, monkeypatch):
    session_id, source, _ = _task76_adapted_ready_session(client, seeded, monkeypatch)
    now = db.now()
    db.run(
        "INSERT INTO take_resource_adaptation "
        "(session_id, plan_revision, take_id, library_key, source_id, "
        "content_digest, resource_field, source_value, adapted_value, "
        "created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        session_id, "take-001", source["library_key"], source["source_id"],
        source["content_digest"], "label", "invented studio",
        "A later unused label adaptation.", now, now,
    )
    refreshed = _refresh(client, session_id)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["refreshed"] is False


def test_refresh_copies_unaffected_manual_snapshot_and_fences_affected_take(client, seeded, monkeypatch):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, mode="manual", photo_count=2)
    preparation = main.resource_preparation.prepare_take_inputs(session_id, revision, "take-001")
    fields = main.resource_preparation.compute_unlocked_fields(preparation)
    source = main.resource_preparation.finalize_take_preparation(
        session_id, revision, "take-001",
        manual_completion={field: f"invented {field}" for field in fields},
    )
    anchor = main.session_plan.get_draft(session_id)["plan"]["authoring"]["scene_anchor"]
    main.resource_preparation.record_take_adaptation(
        session_id, revision, "take-002",
        {**anchor, "resource_field": "scene_theme", "adapted_value": "An invented adaptation."},
    )
    db.run(
        "UPDATE take_resource_adaptation SET source_value = ? WHERE session_id = ? "
        "AND plan_revision = ? AND take_id = ?",
        "An obsolete source description.", session_id, revision, "take-002",
    )
    refreshed = _refresh(client, session_id)
    assert refreshed.status_code == 200, refreshed.text
    result = refreshed.json()
    assert result["affected_takes"] == ["take-002"]
    assert result["copied_forward_takes"] == ["take-001"]
    copy = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = 2 AND take_id = ?",
        session_id, "take-001",
    )
    assert copy["final_prompt"] == source["final_prompt"]
    assert db.one(
        "SELECT id FROM prepared_take WHERE session_id = ? AND plan_revision = 2 AND take_id = ?",
        session_id, "take-002",
    ) is None


def test_refresh_invalidates_uncopyable_ready_and_preserves_linked_history(client, seeded, monkeypatch):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, mode="manual", photo_count=2)
    preparation = main.resource_preparation.prepare_take_inputs(session_id, revision, "take-001")
    fields = main.resource_preparation.compute_unlocked_fields(preparation)
    source = main.resource_preparation.finalize_take_preparation(
        session_id, revision, "take-001",
        manual_completion={field: f"invented {field}" for field in fields},
    )
    anchor = main.session_plan.get_draft(session_id)["plan"]["authoring"]["scene_anchor"]
    main.resource_preparation.record_take_adaptation(
        session_id, revision, "take-002",
        {**anchor, "resource_field": "scene_theme", "adapted_value": "An invented adaptation."},
    )
    db.run(
        "UPDATE take_resource_adaptation SET source_value = ? WHERE session_id = ? "
        "AND plan_revision = ? AND take_id = ?",
        "An obsolete source description.", session_id, revision, "take-002",
    )
    db.run("UPDATE prepared_take SET final_prompt = ? WHERE id = ?", "Forged prompt.", source["id"])
    result = _refresh(client, session_id).json()
    assert result["required_preparation"] == ["take-001", "take-002"]
    assert db.one("SELECT status FROM prepared_take WHERE id = ?", source["id"])["status"] == "invalidated"

    # A separate completed take ID remains historical across later revisions.
    linked_id, linked_revision = _create_guided_session(client, seeded, mode="manual", photo_count=2)
    linked_prep = main.resource_preparation.prepare_take_inputs(linked_id, linked_revision, "take-001")
    linked_fields = main.resource_preparation.compute_unlocked_fields(linked_prep)
    main.resource_preparation.finalize_take_preparation(
        linked_id, linked_revision, "take-001",
        manual_completion={field: f"invented {field}" for field in linked_fields},
    )
    main.session_plan.approve_plan_review(linked_id, linked_revision)
    linked = main.session_plan.submit_prepared_take(linked_id, linked_revision, "take-001")
    main.session_plan.save_draft(linked_id, main.session_plan.get_draft(linked_id)["plan"], 1)
    linked_anchor = main.session_plan.get_draft(linked_id)["plan"]["authoring"]["scene_anchor"]
    main.resource_preparation.record_take_adaptation(
        linked_id, 2, "take-002",
        {**linked_anchor, "resource_field": "scene_theme", "adapted_value": "An invented adaptation."},
    )
    db.run(
        "UPDATE take_resource_adaptation SET source_value = ? WHERE session_id = ? "
        "AND plan_revision = 2 AND take_id = ?",
        "An obsolete source description.", linked_id, "take-002",
    )
    result = _refresh(client, linked_id, 2).json()
    assert result["affected_takes"] == ["take-002"]
    assert result["required_preparation"] == ["take-002"]
    assert db.one("SELECT linked_shot_id FROM prepared_take WHERE id = ?", linked["id"])["linked_shot_id"] == linked["linked_shot_id"]


def test_concurrent_refresh_creates_one_revision(client, seeded, monkeypatch):
    session_id, _, _ = _task76_adapted_ready_session(client, seeded, monkeypatch)
    _change_scene_theme(session_id, "A changed authorized scene description.")
    start = threading.Barrier(2)

    def refresh_once():
        start.wait()
        try:
            return main.session_plan.refresh_resources(session_id, 1, planning_enabled=True)
        except main.session_plan.PlanRevisionStale:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: refresh_once(), range(2)))
    assert sum(isinstance(item, dict) and item["plan_revision"] == 2 for item in results) == 1
    assert results.count("stale") == 1
    assert main.session_plan.current_revision(session_id) == 2


def test_refresh_fences_active_authoring_operation(client, seeded, monkeypatch):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    anchor = main.session_plan.get_draft(session_id)["plan"]["authoring"]["scene_anchor"]
    main.resource_preparation.record_take_adaptation(
        session_id, revision, "take-001",
        {**anchor, "resource_field": "scene_theme", "adapted_value": "An invented adaptation."},
    )
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=revision, take_ids=["take-001"]),
    )
    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]
    _change_scene_theme(session_id, "A changed authorized scene description.")
    assert _refresh(client, session_id).json()["plan_revision"] == 2
    operation = db.one("SELECT state FROM authoring_operation WHERE operation_id = ?", operation_id)
    assert operation["state"] == "cancelled"
