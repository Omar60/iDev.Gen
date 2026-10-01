"""Task 7.10 integration regressions for copy-forward and dependency fencing."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import db
import main
import pytest
import resource_store
from backend import resource_translation, workflow_binding
from test_authoring_operation_api import (
    _configure_assistant,
    _create_guided_session,
    _install_fake_structured_assistant,
    _start_body,
)


@pytest.fixture(autouse=True)
def _disable_background_operation_dispatch(monkeypatch):
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", lambda view: None)


def _start_and_run(client, session_id, revision, monkeypatch, outputs, take_ids):
    calls = _install_fake_structured_assistant(monkeypatch, outputs)
    response = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=revision, take_ids=take_ids),
    )
    assert response.status_code == 202, response.text
    operation_id = response.json()["operation_id"]
    asyncio.run(main._run_prepare_takes_operation(session_id, operation_id))
    operation = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    )
    assert operation.status_code == 200, operation.text
    assert operation.json()["state"] == "succeeded"
    assert len(calls) == len(outputs)
    return operation_id, calls


def _prepare_auto(client, seeded, monkeypatch, *, photo_count=4, outputs, take_ids, adaptation=None):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(
        client, seeded, photo_count=photo_count,
    )
    source_adaptation = None
    if adaptation is not None:
        anchor = main.session_plan.get_draft(session_id)["plan"]["authoring"]["scene_anchor"]
        source_adaptation = main.resource_preparation.record_take_adaptation(
            session_id, revision, adaptation["take_id"], {
                **anchor,
                "resource_field": "scene_theme",
                "adapted_value": adaptation["adapted_value"],
            },
        )
        source_adaptation = db.one(
            "SELECT * FROM take_resource_adaptation WHERE session_id = ? "
            "AND plan_revision = ? AND take_id = ? AND resource_field = ?",
            session_id, revision, adaptation["take_id"], "scene_theme",
        )
    operation_id, calls = _start_and_run(
        client, session_id, revision, monkeypatch, outputs, take_ids,
    )
    return session_id, revision, operation_id, calls, source_adaptation


def _take_row(session_id, revision, take_id):
    row = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? "
        "AND take_id = ?",
        session_id, revision, take_id,
    )
    assert row is not None
    return row


def _session_rows(session_id):
    return {
        table: db.q(f"SELECT * FROM {table} WHERE session_id = ? ORDER BY 1", session_id)
        for table in (
            "session_plan", "session_plan_approval", "authoring_operation",
            "prepared_take", "take_resource_adaptation", "shot",
        )
    }


def _apply_scene_translation(session_id, translated_value):
    plan = main.session_plan.get_draft(session_id)["plan"]
    anchor = plan["authoring"]["scene_anchor"]
    library = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        anchor["library_key"],
    )
    revision = resource_store.get_revision(
        library_id=library["id"],
        source_id=anchor["source_id"],
        content_digest=anchor["content_digest"],
    )
    source = revision["payload"]["scene_theme"]
    translation_map = {
        source: {
            "source": source,
            "translation": translated_value,
            "fields": ["scene_theme"],
        },
    }
    preview = resource_translation.preview_translation_map(
        anchor["library_key"], translation_map,
    )
    result = resource_translation.apply_translation_map(
        anchor["library_key"], translation_map, preview["attestation_token"],
    )
    assert result["updated"] == 1
    return translation_map, preview["attestation_token"]


def _save_plan(client, session_id, revision, plan):
    return client.post(
        f"/api/sessions/{session_id}/plan",
        json={"expected_revision": revision, "plan": plan},
    )


_FIRST = {
    "camera": "wide camera",
    "framing": "full body framing",
    "pose": "standing beside a window",
    "expression": "calm expression",
}
_SAME = {
    "camera": " WIDE   CAMERA ",
    "framing": "FULL BODY FRAMING",
    "pose": "Standing beside a window",
    "expression": " CALM EXPRESSION ",
}
_OTHER = {
    "camera": "close camera",
    "framing": "medium framing",
    "pose": "sitting beside a window",
    "expression": "curious expression",
}


def test_task_710_fenced_copy_is_reviewed_reused_and_linked_without_regeneration(
    client, seeded, monkeypatch,
):
    session_id, revision, first_operation, first_calls, source_adaptation = _prepare_auto(
        client, seeded, monkeypatch,
        outputs=[_FIRST, _SAME],
        take_ids=["take-001", "take-002"],
        adaptation={"take_id": "take-002", "adapted_value": "A reviewed scene adaptation."},
    )
    assert source_adaptation is not None
    source = _take_row(session_id, revision, "take-001")
    assert json.loads(source["provenance"])["authoring_evidence"]["operation_id"] == first_operation

    source_two = _take_row(session_id, revision, "take-002")
    assert json.loads(source_two["provenance"])["authoring_evidence"]["operation_id"] == first_operation
    main.resource_preparation.validate_authoring_prepared_evidence(
        session_id, revision, "take-002",
    )

    plan = main.session_plan.get_draft(session_id)["plan"]
    plan["takes"][2]["pose"] = "A different pose for the third take."
    saved = _save_plan(client, session_id, revision, plan)
    assert saved.status_code == 200, saved.text
    next_revision = saved.json()["plan_revision"]
    assert next_revision == revision + 1

    copied_two = _take_row(session_id, next_revision, "take-002")
    assert db.one(
        "SELECT id FROM prepared_take WHERE session_id = ? AND plan_revision = ? "
        "AND take_id = 'take-001'",
        session_id, next_revision,
    ) is not None
    assert copied_two["final_prompt"] == source_two["final_prompt"]
    own_lineage = json.loads(copied_two["provenance"])["copy_forward"]["origin_prepared_id"]
    duplicate_evidence = json.loads(copied_two["provenance"])["authoring_evidence"]["duplicate_flags"]
    assert duplicate_evidence["status"] == "recorded"
    assert all(item["take_id"] != "take-002" for item in duplicate_evidence["flags"])
    assert all(item["lineage_root_id"] != own_lineage for item in duplicate_evidence["flags"])
    assert [item["take_id"] for item in duplicate_evidence["flags"]] == ["take-001"]

    review = client.get(
        f"/api/sessions/{session_id}/plan/takes/take-002/review",
        params={"plan_revision": next_revision},
    )
    assert review.status_code == 200, review.text
    assert any(
        item["source_value"] == source_adaptation["source_value"]
        and item["adapted_value"] == source_adaptation["adapted_value"]
        for item in review.json()["adaptations"]
    )
    destination_adaptation = db.one(
        "SELECT * FROM take_resource_adaptation WHERE session_id = ? "
        "AND plan_revision = ? AND take_id = 'take-002'",
        session_id, next_revision,
    )
    assert destination_adaptation["id"] != source_adaptation["id"]

    recovered = main.session_plan.recover_preparation(session_id)
    assert [item["take_id"] for item in recovered["completed"]] == ["take-001", "take-002"]
    operation_count = len(_session_rows(session_id)["authoring_operation"])

    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": next_revision},
    )
    assert approved.status_code == 200, approved.text
    linked_source = main.session_plan.submit_prepared_take(
        session_id, next_revision, "take-001",
    )
    linked_source_id = linked_source["linked_shot_id"]

    reused_start = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=next_revision, take_ids=["take-002"]),
    )
    assert reused_start.status_code == 202, reused_start.text
    reused_id = reused_start.json()["operation_id"]
    asyncio.run(main._run_prepare_takes_operation(session_id, reused_id))
    assert client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{reused_id}"
    ).json()["state"] == "succeeded"
    assert len(first_calls) == 2
    assert len(_session_rows(session_id)["authoring_operation"]) == operation_count + 1

    with pytest.raises(main.session_plan.PlanRevisionStale):
        main.session_plan.submit_prepared_take(session_id, revision, "take-002")
    submitted = main.session_plan.submit_prepared_take(
        session_id, next_revision, "take-002",
    )
    assert submitted["final_prompt"] == source_two["final_prompt"]
    shot_id = submitted["linked_shot_id"]
    assert db.one("SELECT prompt FROM shot WHERE id = ?", shot_id)["prompt"] == source_two["final_prompt"]
    assert main.session_plan.submit_prepared_take(
        session_id, next_revision, "take-002",
    )["linked_shot_id"] == shot_id

    recovered = main.session_plan.recover_preparation(session_id)
    assert any(
        item["take_id"] == "take-001"
        and item["linked_shot_id"] == linked_source_id
        and item["status"] == "generated"
        for item in recovered["completed"]
    )

    plan = main.session_plan.get_draft(session_id)["plan"]
    plan["takes"][2]["pose"] = "A later third-take pose."
    later = _save_plan(client, session_id, next_revision, plan)
    assert later.status_code == 200, later.text
    final_revision = later.json()["plan_revision"]
    for take_id in ("take-001", "take-002"):
        assert db.one(
            "SELECT id FROM prepared_take WHERE session_id = ? AND plan_revision = ? "
            "AND take_id = ?",
            session_id, final_revision, take_id,
        ) is None
    assert _take_row(session_id, next_revision, "take-001")["status"] == "generated"
    assert _take_row(session_id, next_revision, "take-002")["status"] == "generated"
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id)["n"] == 2

    before_refresh = _session_rows(session_id)
    refreshed = client.post(
        f"/api/sessions/{session_id}/plan/refresh-resources",
        json={"expected_revision": final_revision},
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["refreshed"] is False
    assert _session_rows(session_id) == before_refresh

    recovered = main.session_plan.recover_preparation(session_id)
    assert {
        (item["take_id"], item["linked_shot_id"])
        for item in recovered["history"]
        if item["status"] == "generated"
    } == {
        ("take-001", linked_source_id),
        ("take-002", shot_id),
    }
    assert len(first_calls) == 2
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id)["n"] == 2


@pytest.mark.parametrize("destination_conflict", ["snapshot", "adaptation"])
def test_task_710_copy_forward_conflict_preserves_plan_evidence_and_authority(
    client, seeded, monkeypatch, destination_conflict,
):
    session_id, revision, operation_id, _, adaptation = _prepare_auto(
        client, seeded, monkeypatch,
        outputs=[_FIRST], take_ids=["take-001"],
        adaptation={"take_id": "take-001", "adapted_value": "An approved source adaptation."},
    )
    assert adaptation is not None
    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approved.status_code == 200, approved.text

    if destination_conflict == "snapshot":
        now = db.now()
        db.run(
            "INSERT INTO prepared_take (session_id, plan_revision, take_id, status, "
            "created_at, updated_at) VALUES (?, ?, 'take-001', 'pending', ?, ?)",
            session_id, revision + 1, now, now,
        )
    else:
        now = db.now()
        db.run(
            "INSERT INTO take_resource_adaptation "
            "(session_id, plan_revision, take_id, library_key, source_id, "
            "content_digest, resource_field, source_value, adapted_value, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            session_id, revision + 1, "take-001", adaptation["library_key"],
            adaptation["source_id"], adaptation["content_digest"],
            adaptation["resource_field"], adaptation["source_value"],
            "A conflicting destination adaptation.", now, now,
        )

    before = _session_rows(session_id)
    plan = main.session_plan.get_draft(session_id)["plan"]
    plan["takes"][1]["pose"] = "Edit a later take to permit the earlier copy."
    response = _save_plan(client, session_id, revision, plan)
    assert response.status_code == 409, response.text
    assert _session_rows(session_id) == before
    assert main.session_plan.get_draft(session_id)["plan_revision"] == revision
    assert db.one(
        "SELECT plan_revision FROM session_plan_approval WHERE session_id = ?",
        session_id,
    )["plan_revision"] == revision
    assert _take_row(session_id, revision, "take-001")["status"] == "ready"
    operation = db.one(
        "SELECT state FROM authoring_operation WHERE session_id = ? AND operation_id = ?",
        session_id, operation_id,
    )
    assert operation["state"] == "succeeded"
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id)["n"] == 0


def test_task_710_refresh_copy_conflict_returns_409_and_rolls_back_all_rows(
    client, seeded, monkeypatch,
):
    session_id, revision, operation_id, _, adaptation = _prepare_auto(
        client, seeded, monkeypatch,
        outputs=[_FIRST, _OTHER], take_ids=["take-001", "take-002"],
        adaptation={"take_id": "take-002", "adapted_value": "A reviewed later-take adaptation."},
    )
    assert adaptation is not None
    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approved.status_code == 200, approved.text
    db.run(
        "UPDATE take_resource_adaptation SET adapted_value = ? WHERE id = ?",
        "A changed later-take adaptation.", adaptation["id"],
    )
    now = db.now()
    db.run(
        "INSERT INTO prepared_take (session_id, plan_revision, take_id, status, "
        "created_at, updated_at) VALUES (?, ?, 'take-001', 'pending', ?, ?)",
        session_id, revision + 1, now, now,
    )
    before = _session_rows(session_id)

    response = client.post(
        f"/api/sessions/{session_id}/plan/refresh-resources",
        json={"expected_revision": revision},
    )

    assert response.status_code == 409, response.text
    assert _session_rows(session_id) == before
    assert main.session_plan.get_draft(session_id)["plan_revision"] == revision
    assert db.one(
        "SELECT plan_revision FROM session_plan_approval WHERE session_id = ?",
        session_id,
    )["plan_revision"] == revision
    assert db.one(
        "SELECT state FROM authoring_operation WHERE session_id = ? AND operation_id = ?",
        session_id, operation_id,
    )["state"] == "succeeded"
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id)["n"] == 0


@pytest.mark.parametrize("drift", ["translation", "context", "workflow"])
def test_task_710_copy_forward_refuses_translation_context_or_workflow_drift(
    client, seeded, monkeypatch, drift,
):
    session_id, revision, _, _, _ = _prepare_auto(
        client, seeded, monkeypatch,
        outputs=[_FIRST], take_ids=["take-001"],
    )
    source = _take_row(session_id, revision, "take-001")
    plan = main.session_plan.get_draft(session_id)["plan"]

    if drift == "context":
        plan["authoring"]["brief"] = "A changed brief invalidates the saved writer context."
        response = _save_plan(client, session_id, revision, plan)
        assert response.status_code == 200, response.text
        destination_revision = response.json()["plan_revision"]
    elif drift == "translation":
        _apply_scene_translation(session_id, "A changed authorized room description.")
        response = _save_plan(client, session_id, revision, plan)
        assert response.status_code == 200, response.text
        destination_revision = response.json()["plan_revision"]
    else:
        approved = client.post(
            f"/api/sessions/{session_id}/plan/review/approve",
            json={"plan_revision": revision},
        )
        assert approved.status_code == 200, approved.text
        workflow_id = plan["authoring"]["workflow_binding"]["workflow_id"]
        before_workflow = db.one(
            "SELECT * FROM workflow WHERE id = ?", workflow_id,
        )
        graph = json.loads(db.one(
            "SELECT graph FROM workflow WHERE id = ?", workflow_id,
        )["graph"])
        graph["1"]["inputs"]["ckpt_name"] = "changed-after-binding.safetensors"
        db.run(
            "UPDATE workflow SET graph = ? WHERE id = ?",
            json.dumps(graph, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            workflow_id,
        )
        selected = plan["selected_resources"][0]
        library = db.one(
            "SELECT id FROM resource_library WHERE library_key = ?",
            selected["library_key"],
        )
        resource_before = resource_store.get_revision(
            library_id=library["id"],
            source_id=selected["source_id"],
            content_digest=selected["content_digest"],
        )
        before_rows = _session_rows(session_id)
        plan["takes"][2]["pose"] = "Attempt a copy while only the workflow has drifted."
        with pytest.raises(workflow_binding.WorkflowChanged):
            main.session_plan.save_draft(session_id, plan, revision)
        assert _session_rows(session_id) == before_rows
        assert db.one("SELECT * FROM workflow WHERE id = ?", workflow_id) != before_workflow
        assert resource_store.get_revision(
            library_id=library["id"],
            source_id=selected["source_id"],
            content_digest=selected["content_digest"],
        ) == resource_before
        assert db.one(
            "SELECT id FROM prepared_take WHERE session_id = ? AND plan_revision = ? "
            "AND take_id = 'take-001'",
            session_id, revision + 1,
        ) is None
        assert db.one(
            "SELECT status FROM prepared_take WHERE id = ?", source["id"],
        )["status"] == "ready"
        assert db.one(
            "SELECT plan_revision FROM session_plan_approval WHERE session_id = ?",
            session_id,
        )["plan_revision"] == revision
        return

    assert destination_revision == revision + 1
    assert db.one(
        "SELECT id FROM prepared_take WHERE session_id = ? AND plan_revision = ? "
        "AND take_id = 'take-001'",
        session_id, destination_revision,
    ) is None
    source_status = db.one(
        "SELECT status FROM prepared_take WHERE id = ?", source["id"],
    )["status"]
    assert source_status == ("ready" if drift == "translation" else "invalidated")


def test_task_710_approved_translation_drift_blocks_direct_domain_submit_without_writes(
    client, seeded, monkeypatch,
):
    session_id, revision, _, _, _ = _prepare_auto(
        client, seeded, monkeypatch,
        outputs=[_FIRST], take_ids=["take-001"],
    )
    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approved.status_code == 200, approved.text
    _apply_scene_translation(session_id, "A changed authorized room description.")
    before = _session_rows(session_id)

    with pytest.raises(main.session_plan.AuthoringEvidenceInvalid):
        main.session_plan.submit_prepared_take(session_id, revision, "take-001")

    assert _session_rows(session_id) == before
    assert main.session_plan.get_approved_plan_revision(session_id) is None
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id)["n"] == 0


@pytest.mark.parametrize("translation_wins", [True, False])
def test_task_710_canonical_translation_apply_serializes_with_selected_submit(
    client, seeded, monkeypatch, translation_wins,
):
    session_id, revision, _, _, _ = _prepare_auto(
        client, seeded, monkeypatch,
        outputs=[_FIRST, _OTHER], take_ids=["take-001", "take-002"],
    )
    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approved.status_code == 200, approved.text
    plan = main.session_plan.get_draft(session_id)["plan"]
    anchor = plan["authoring"]["scene_anchor"]
    library = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?", anchor["library_key"],
    )
    resource_revision = resource_store.get_revision(
        library_id=library["id"], source_id=anchor["source_id"],
        content_digest=anchor["content_digest"],
    )
    source = resource_revision["payload"]["scene_theme"]
    translation_map = {
        source: {
            "source": source,
            "translation": "A translation committed during selected submission.",
            "fields": ["scene_theme"],
        },
    }
    preview = resource_translation.preview_translation_map(
        anchor["library_key"], translation_map,
    )
    token = preview["attestation_token"]
    before = _session_rows(session_id)

    transaction_attempted = threading.Event()
    translation_attempted = threading.Event()
    translation_written = threading.Event()
    release_translation = threading.Event()
    validation_complete = threading.Event()
    release_submit = threading.Event()
    translation_done = threading.Event()
    translation_thread_id = []
    submit_thread_id = []
    original_transaction = db.transaction
    original_update = resource_store.update_translation
    original_validate = main.session_plan.validate_authoring_prepared_evidence

    def observe_transaction():
        current = threading.get_ident()
        if translation_thread_id and current == translation_thread_id[0]:
            translation_attempted.set()
        if submit_thread_id and current == submit_thread_id[0]:
            transaction_attempted.set()
        return original_transaction()

    def hold_translation_write(*args, **kwargs):
        result = original_update(*args, **kwargs)
        if translation_thread_id and threading.get_ident() == translation_thread_id[0]:
            translation_written.set()
            if translation_wins:
                assert release_translation.wait(timeout=5)
        return result

    def hold_submit_after_validation(*args, **kwargs):
        result = original_validate(*args, **kwargs)
        if (
            not translation_wins
            and submit_thread_id
            and threading.get_ident() == submit_thread_id[0]
            and not validation_complete.is_set()
        ):
            validation_complete.set()
            assert release_submit.wait(timeout=5)
        return result

    monkeypatch.setattr(db, "transaction", observe_transaction)
    monkeypatch.setattr(resource_store, "update_translation", hold_translation_write)
    monkeypatch.setattr(
        main.session_plan, "validate_authoring_prepared_evidence",
        hold_submit_after_validation,
    )

    def apply_translation():
        translation_thread_id.append(threading.get_ident())
        try:
            return resource_translation.apply_translation_map(
                anchor["library_key"], translation_map, token,
            )
        finally:
            translation_done.set()

    def submit_selected():
        submit_thread_id.append(threading.get_ident())
        return main.submit_selected_plan_preparations(
            session_id,
            main.PreparedTakesSubmitSelectedIn(
                plan_revision=revision,
                take_ids=["take-001", "take-002"],
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        if translation_wins:
            translation_future = pool.submit(apply_translation)
            assert translation_written.wait(timeout=5)
            submit_future = pool.submit(submit_selected)
            assert transaction_attempted.wait(timeout=5)
            assert not translation_done.is_set()
            release_translation.set()
            assert translation_future.result(timeout=5)["updated"] == 1
            with pytest.raises(main.HTTPException) as refused:
                submit_future.result(timeout=5)
            assert refused.value.status_code == 409
            assert _session_rows(session_id) == before
            with pytest.raises(main.session_plan.AuthoringEvidenceInvalid):
                main.session_plan.submit_prepared_take(session_id, revision, "take-001")
            assert _session_rows(session_id) == before
            assert main.session_plan.get_approved_plan_revision(session_id) is None
        else:
            submit_future = pool.submit(submit_selected)
            assert validation_complete.wait(timeout=5)
            translation_future = pool.submit(apply_translation)
            assert translation_attempted.wait(timeout=5)
            assert not translation_done.is_set()
            release_submit.set()
            result = submit_future.result(timeout=5)
            assert [item["take_id"] for item in result["submitted"]] == [
                "take-001", "take-002",
            ]
            assert translation_future.result(timeout=5)["updated"] == 1
            assert db.one(
                "SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id,
            )["n"] == 2
            for item in result["submitted"]:
                row = _take_row(session_id, revision, item["take_id"])
                assert row["status"] == "generated"
                assert row["linked_shot_id"] == item["linked_shot_id"]
            retry = main.submit_selected_plan_preparations(
                session_id,
                main.PreparedTakesSubmitSelectedIn(
                    plan_revision=revision,
                    take_ids=["take-001", "take-002"],
                ),
            )
            assert [item["linked_shot_id"] for item in retry["submitted"]] == [
                item["linked_shot_id"] for item in result["submitted"]
            ]
            assert db.one(
                "SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id,
            )["n"] == 2


def test_task_710_authoring_selected_batch_with_one_stale_take_writes_nothing(
    client, seeded, monkeypatch,
):
    session_id, revision, _, _, adaptation = _prepare_auto(
        client, seeded, monkeypatch,
        outputs=[_FIRST, _OTHER], take_ids=["take-001", "take-002"],
        adaptation={"take_id": "take-002", "adapted_value": "A reviewed take-specific scene adaptation."},
    )
    assert adaptation is not None
    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approved.status_code == 200, approved.text
    db.run(
        "UPDATE take_resource_adaptation SET adapted_value = ? WHERE id = ?",
        "A changed take-specific adaptation.", adaptation["id"],
    )
    before = _session_rows(session_id)

    with pytest.raises(main.HTTPException) as refused:
        main.submit_selected_plan_preparations(
            session_id,
            main.PreparedTakesSubmitSelectedIn(
                plan_revision=revision,
                take_ids=["take-001", "take-002"],
            ),
        )
    assert refused.value.status_code == 409
    assert _session_rows(session_id) == before
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id)["n"] == 0


def test_task_710_explicit_operation_does_not_reauthor_linked_history_after_plan_cas(
    client, seeded, monkeypatch,
):
    session_id, revision, operation_id, _, _ = _prepare_auto(
        client, seeded, monkeypatch,
        photo_count=3, outputs=[_FIRST], take_ids=["take-001"],
    )
    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approved.status_code == 200, approved.text
    submitted = main.session_plan.submit_prepared_take(
        session_id, revision, "take-001",
    )
    linked_source = _take_row(session_id, revision, "take-001")
    assert linked_source["status"] == "generated"
    assert linked_source["linked_shot_id"] == submitted["linked_shot_id"]

    original_operation = db.one(
        "SELECT request_id FROM authoring_operation WHERE operation_id = ?",
        operation_id,
    )
    plan = main.session_plan.get_draft(session_id)["plan"]
    plan["takes"][2]["pose"] = "Change another take while preserving submitted history."
    saved = _save_plan(client, session_id, revision, plan)
    assert saved.status_code == 200, saved.text
    next_revision = saved.json()["plan_revision"]
    assert db.one(
        "SELECT id FROM prepared_take WHERE session_id = ? AND plan_revision = ? "
        "AND take_id = 'take-001'",
        session_id, next_revision,
    ) is None

    before_replay = _session_rows(session_id)
    replay = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(
            request_id=original_operation["request_id"],
            revision=revision,
            take_ids=["take-001"],
        ),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["operation_id"] == operation_id
    assert replay.json()["state"] == "succeeded"
    assert _session_rows(session_id) == before_replay

    assistant_calls = _install_fake_structured_assistant(monkeypatch, [_OTHER])
    rejected = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=next_revision, take_ids=["take-001"]),
    )
    assert rejected.status_code == 409, rejected.text
    detail = rejected.json()["detail"]
    assert detail["code"] == "authoring_inputs_stale"
    assert "already submitted" in detail["message"]
    assert "omit" in detail["message"]
    assert assistant_calls == []
    assert _session_rows(session_id) == before_replay
    assert _take_row(session_id, revision, "take-001") == linked_source
    assert db.one(
        "SELECT prompt FROM shot WHERE id = ?", submitted["linked_shot_id"],
    )["prompt"] == linked_source["final_prompt"]
    assert db.one(
        "SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id,
    )["n"] == 1
