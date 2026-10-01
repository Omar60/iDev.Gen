import asyncio
import json

import db
import main
import resource_store
import pytest
from test_authoring_operation_api import (
    _configure_assistant,
    _create_guided_session,
    _enable_background_suggestion_dispatch,
    _install_fake_structured_assistant,
    _start_body,
    _task76_adapted_ready_session,
    _wait_for_operation_state,
)


@pytest.fixture(autouse=True)
def _disable_background_suggestion_dispatch(monkeypatch):
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", lambda view: None)


def test_task_710_linked_first_and_stale_second_selected_batch_is_write_free(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    session_id, revision = _create_guided_session(
        client,
        seeded,
        photo_count=2,
        look="short dark hair and warm makeup",
        initial_wardrobe="a blue denim jacket",
    )
    outputs = [
        {
            "camera": f"camera choice {index}",
            "framing": f"medium framing {index}",
            "pose": f"standing by a window {index}",
            "expression": f"calm expression {index}",
        }
        for index in (1, 2)
    ]
    assistant_calls = _install_fake_structured_assistant(monkeypatch, outputs)
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=revision, take_ids=["take-001", "take-002"]),
    )
    assert started.status_code == 202, started.text
    operation = _wait_for_operation_state(
        client, session_id, started.json()["operation_id"], "succeeded",
    )
    assert operation["progress"]["completed"] == ["take-001", "take-002"]
    assert len(assistant_calls) == 2


    snapshots_before = db.q(
        "SELECT * FROM prepared_take WHERE session_id = ? ORDER BY id", session_id,
    )
    assert [row["status"] for row in snapshots_before] == ["ready", "ready"]
    for row, output in zip(snapshots_before, outputs, strict=True):
        evidence = json.loads(row["provenance"])["authoring_evidence"]
        assert evidence["writer_synthesis"]["writer_output"] == output
        assert evidence["effective_resource_input_digest"]["digest"]

    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approved.status_code == 200, approved.text
    assert main.session_plan.get_approved_plan_revision(session_id) == revision

    linked = main.session_plan.submit_prepared_take(
        session_id, revision, "take-001",
    )
    assert linked["status"] == "generated"
    assert linked["linked_shot_id"] is not None

    plan = main.session_plan.get_draft(session_id)["plan"]
    selected = plan["selected_resources"][0]
    library = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        selected["library_key"],
    )
    resource = resource_store.get_revision(
        library_id=library["id"],
        source_id=selected["source_id"],
        content_digest=selected["content_digest"],
    )
    source_theme = resource["payload"]["scene_theme"]
    translation_map = {
        source_theme: {
            "source": source_theme,
            "translation": "Bright daylight fills the fictional studio.",
            "fields": ["scene_theme"],
        },
    }
    preview = client.post(
        f"/api/resources/libraries/{selected['library_key']}/translations/preview",
        json={"translation_map": translation_map},
    )
    assert preview.status_code == 200, preview.text
    applied = client.post(
        f"/api/resources/libraries/{selected['library_key']}/translations/apply",
        json={
            "translation_map": translation_map,
            "attestation_token": preview.json()["attestation_token"],
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["updated"] == 1
    assert resource_store.get_revision(
        library_id=library["id"],
        source_id=selected["source_id"],
        content_digest=selected["content_digest"],
    )["translation"]["scene_theme"] == "Bright daylight fills the fictional studio."
    assert main.session_plan.get_approved_plan_revision(session_id) is None

    persisted_before_batch = {
        table: db.q(
            f"SELECT * FROM {table} WHERE session_id = ? ORDER BY rowid",
            session_id,
        )
        for table in (
            "session_plan",
            "session_plan_approval",
            "prepared_take",
            "take_resource_adaptation",
            "authoring_operation",
            "shot",
        )
    }
    assert len(persisted_before_batch["shot"]) == 1
    assert persisted_before_batch["prepared_take"][0]["linked_shot_id"] == linked["linked_shot_id"]

    refused = client.post(
        f"/api/sessions/{session_id}/plan/preparations/submit-selected",
        json={"plan_revision": revision, "take_ids": ["take-001", "take-002"]},
    )
    assert refused.status_code == 409, refused.text
    assert "Prepared authoring evidence is invalid" in refused.text
    assert {
        table: db.q(
            f"SELECT * FROM {table} WHERE session_id = ? ORDER BY rowid",
            session_id,
        )
        for table in persisted_before_batch
    } == persisted_before_batch
    assert len(assistant_calls) == 2


@pytest.mark.parametrize("collision", ["snapshot", "adaptation"])
def test_task_710_http_copy_forward_conflict_is_409_and_rolls_back(
    client, seeded, monkeypatch, collision,
):
    session_id, source_adaptation, _ = _task76_adapted_ready_session(
        client, seeded, monkeypatch,
    )
    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": 1},
    )
    assert approved.status_code == 200, approved.text

    now = db.now()
    if collision == "snapshot":
        db.run(
            "INSERT INTO prepared_take (session_id, plan_revision, take_id, status, "
            "created_at, updated_at) VALUES (?, 2, 'take-001', 'pending', ?, ?)",
            session_id, now, now,
        )
    else:
        db.run(
            "INSERT INTO take_resource_adaptation "
            "(session_id, plan_revision, take_id, library_key, source_id, "
            "content_digest, resource_field, source_value, adapted_value, "
            "created_at, updated_at) VALUES (?, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            session_id, "take-001", source_adaptation["library_key"],
            source_adaptation["source_id"], source_adaptation["content_digest"],
            source_adaptation["resource_field"], source_adaptation["source_value"],
            "A conflicting destination adaptation.", now, now,
        )

    before = {
        table: db.q(
            f"SELECT * FROM {table} WHERE session_id = ? ORDER BY rowid",
            session_id,
        )
        for table in (
            "session_plan",
            "session_plan_approval",
            "prepared_take",
            "take_resource_adaptation",
            "authoring_operation",
            "shot",
        )
    }
    plan = main.session_plan.get_draft(session_id)["plan"]
    refused = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": plan, "expected_revision": 1},
    )

    assert refused.status_code == 409, refused.text
    assert {
        table: db.q(
            f"SELECT * FROM {table} WHERE session_id = ? ORDER BY rowid",
            session_id,
        )
        for table in before
    } == before


def test_task_710_explicit_operation_does_not_reauthor_linked_history_after_cas(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(
        client,
        seeded,
        photo_count=3,
        look="short dark hair and warm makeup",
        initial_wardrobe="a blue denim jacket",
    )
    original_output = {
        "camera": "wide camera",
        "framing": "full body framing",
        "pose": "standing beside a window",
        "expression": "calm expression",
    }
    first_calls = _install_fake_structured_assistant(monkeypatch, [original_output])
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=revision, take_ids=["take-001"]),
    )
    assert started.status_code == 202, started.text
    asyncio.run(main._run_prepare_takes_operation(session_id, started.json()["operation_id"]))
    assert client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{started.json()['operation_id']}"
    ).json()["state"] == "succeeded"
    assert len(first_calls) == 1

    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approved.status_code == 200, approved.text
    linked = main.session_plan.submit_prepared_take(session_id, revision, "take-001")
    source_snapshot = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? "
        "AND take_id = 'take-001'",
        session_id, revision,
    )
    source_shot = db.one(
        "SELECT * FROM shot WHERE id = ?", linked["linked_shot_id"],
    )

    plan = main.session_plan.get_draft(session_id)["plan"]
    plan["takes"][1]["pose"] = "A changed later take pose."
    saved = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": plan, "expected_revision": revision},
    )
    assert saved.status_code == 200, saved.text
    next_revision = saved.json()["plan_revision"]
    assert db.one(
        "SELECT id FROM prepared_take WHERE session_id = ? AND plan_revision = ? "
        "AND take_id = 'take-001'",
        session_id, next_revision,
    ) is None
    operations_before = db.q(
        "SELECT * FROM authoring_operation WHERE session_id = ? ORDER BY rowid",
        session_id,
    )

    followup_calls = _install_fake_structured_assistant(monkeypatch, [original_output])
    followup = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=next_revision, take_ids=["take-001"]),
    )
    assert followup.status_code == 409, followup.text
    assert followup.json()["detail"]["code"] == "authoring_inputs_stale"

    current_snapshot = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? "
        "AND take_id = 'take-001'",
        session_id, next_revision,
    )
    state = {
        "assistant_calls": len(followup_calls),
        "current_snapshot": None if current_snapshot is None else {
            "status": current_snapshot["status"],
            "linked_shot_id": current_snapshot["linked_shot_id"],
        },
        "shots": db.q("SELECT * FROM shot WHERE session_id = ?", session_id),
        "operations": db.q(
            "SELECT * FROM authoring_operation WHERE session_id = ? ORDER BY rowid",
            session_id,
        ),
        "source_snapshot": db.one(
            "SELECT * FROM prepared_take WHERE id = ?", source_snapshot["id"],
        ),
        "source_shot": db.one("SELECT * FROM shot WHERE id = ?", source_shot["id"]),
    }
    assert state == {
        "assistant_calls": 0,
        "current_snapshot": None,
        "shots": [source_shot],
        "operations": operations_before,
        "source_snapshot": source_snapshot,
        "source_shot": source_shot,
    }
