"""Independent acceptance probes for Task 7.9 duplicate review evidence."""
from __future__ import annotations

import asyncio
import json
import uuid

import db
import main
import resource_store
import pytest
from backend import authoring_operations

from test_authoring_operation_api import (
    _configure_assistant,
    _create_guided_session,
    _persist_response_for_state_test,
    _start_body,
    _wait_for_operation_state,
)


@pytest.fixture(autouse=True)
def _keep_authoring_operations_deterministic(monkeypatch):
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", lambda view: None)


def _start_operation(client, session_id: int, revision: int, take_ids: list[str], *, request_id=None):
    body = _start_body(
        revision=revision,
        take_ids=take_ids,
        request_id=request_id or str(uuid.uuid4()),
    )
    response = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=body,
    )
    assert response.status_code == 202, response.text
    operation_id = response.json()["operation_id"]
    claim = authoring_operations.load_worker_claim(session_id, operation_id)
    return body, operation_id, claim


def _finish_take(claim, take_id: str, output: dict) -> None:
    ticket = authoring_operations.renew_operation_lease(claim)
    _persist_response_for_state_test(
        claim,
        ticket,
        [{"target": take_id, "result": output}],
    )


def _row(session_id: int, revision: int, take_id: str) -> dict:
    row = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? "
        "AND take_id = ?",
        session_id,
        revision,
        take_id,
    )
    assert row is not None
    return row


def _evidence(row: dict) -> dict:
    return json.loads(row["provenance"])["authoring_evidence"]


def _write_provenance(row: dict, provenance: dict) -> None:
    db.run(
        "UPDATE prepared_take SET provenance = ? WHERE id = ?",
        json.dumps(provenance, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        row["id"],
    )


def _duplicate_outputs() -> tuple[dict, dict]:
    first = {
        "camera": "wide camera",
        "framing": "full body framing",
        "pose": "standing beside a window",
        "expression": "calm expression",
    }
    repeated = {
        "camera": "  WIDE   CAMERA ",
        "framing": "FULL BODY   FRAMING",
        "pose": "Standing  beside a window",
        "expression": " CALM EXPRESSION ",
    }
    return first, repeated


def _prepare_pair(client, seeded, monkeypatch, *, approve_before: bool = False):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, photo_count=4)
    if approve_before:
        approved = client.post(
            f"/api/sessions/{session_id}/plan/review/approve",
            json={"plan_revision": revision},
        )
        assert approved.status_code == 200, approved.text
    body, operation_id, claim = _start_operation(
        client, session_id, revision, ["take-001", "take-002"],
    )
    first, repeated = _duplicate_outputs()
    _finish_take(claim, "take-001", first)
    _finish_take(claim, "take-002", repeated)
    status = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    )
    assert status.status_code == 200, status.text
    assert status.json()["state"] == "succeeded"
    return session_id, revision, body, operation_id, first, repeated


def test_preexisting_plan_approval_does_not_authorize_a_later_flagged_repeat(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, photo_count=3)
    approved_before = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approved_before.status_code == 200, approved_before.text
    first, repeated = _duplicate_outputs()
    _, first_operation, first_claim = _start_operation(
        client, session_id, revision, ["take-001"],
    )
    _finish_take(first_claim, "take-001", first)
    assert client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{first_operation}"
    ).json()["state"] == "succeeded"
    first_submit = client.post(
        f"/api/sessions/{session_id}/plan/preparations/submit",
        json={"plan_revision": revision, "take_id": "take-001"},
    )
    assert first_submit.status_code == 200, first_submit.text
    first_shot_id = first_submit.json()["linked_shot_id"]

    _, second_operation, second_claim = _start_operation(
        client, session_id, revision, ["take-002"],
    )
    _finish_take(second_claim, "take-002", repeated)
    assert client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{second_operation}"
    ).json()["state"] == "succeeded"
    candidate = _row(session_id, revision, "take-002")
    flags = _evidence(candidate)["duplicate_flags"]["flags"]
    assert [(flag["take_id"], flag["status"]) for flag in flags] == [
        ("take-001", "generated"),
    ]
    assert main.session_plan.get_approved_plan_revision(session_id) is None

    submitted = client.post(
        f"/api/sessions/{session_id}/plan/preparations/submit",
        json={"plan_revision": revision, "take_id": "take-002"},
    )
    assert submitted.status_code == 409, (
        "a plan approval recorded before the duplicate snapshot must not authorize "
        f"its submission; got {submitted.status_code}: {submitted.text}"
    )
    still_ready = _row(session_id, revision, "take-002")
    assert still_ready["status"] == "ready"
    assert still_ready["linked_shot_id"] is None
    assert db.one(
        "SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id,
    )["n"] == 1
    linked_retry = client.post(
        f"/api/sessions/{session_id}/plan/preparations/submit",
        json={"plan_revision": revision, "take_id": "take-001"},
    )
    assert linked_retry.status_code == 200, linked_retry.text
    assert linked_retry.json()["linked_shot_id"] == first_shot_id
    assert db.one(
        "SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id,
    )["n"] == 1

    approved_after_flag = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approved_after_flag.status_code == 200, approved_after_flag.text
    submitted_after_review = client.post(
        f"/api/sessions/{session_id}/plan/preparations/submit",
        json={"plan_revision": revision, "take_id": "take-002"},
    )
    assert submitted_after_review.status_code == 200, submitted_after_review.text
    assert submitted_after_review.json()["status"] == "generated"
    assert submitted_after_review.json()["linked_shot_id"] is not None
    assert db.one(
        "SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id,
    )["n"] == 2


def test_flagged_repeat_is_reviewable_and_submitable_after_post_snapshot_approval(
    client, seeded, monkeypatch,
):
    session_id, revision, body, operation_id, first, repeated = _prepare_pair(
        client, seeded, monkeypatch,
    )
    candidate = _row(session_id, revision, "take-002")
    evidence = _evidence(candidate)
    duplicate = evidence["duplicate_flags"]
    assert evidence["schema_version"] == 3
    assert duplicate["status"] == "recorded"
    assert [
        (item["take_id"], item["plan_revision"], item["status"])
        for item in duplicate["flags"]
    ] == [("take-001", revision, "ready")]
    assert evidence["writer_synthesis"]["writer_output"] == repeated
    effective_state = json.loads(candidate["effective_state"])
    assert effective_state["take_choices"] == repeated

    # Replaying the same operation request returns its original completed result.
    replay = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=body,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["operation_id"] == operation_id
    assert _row(session_id, revision, "take-002") == candidate

    review = client.get(
        f"/api/sessions/{session_id}/plan/takes/take-002/review",
        params={"plan_revision": revision},
    )
    assert review.status_code == 200, review.text
    assert review.json()["snapshot"]["provenance"]["authoring_evidence"][
        "duplicate_flags"]["flags"] == duplicate["flags"]

    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approved.status_code == 200, approved.text
    submitted = client.post(
        f"/api/sessions/{session_id}/plan/preparations/submit",
        json={"plan_revision": revision, "take_id": "take-002"},
    )
    assert submitted.status_code == 200, submitted.text
    generated = submitted.json()
    assert generated["status"] == "generated"
    assert generated["final_prompt"] == candidate["final_prompt"]
    assert generated["linked_shot_id"] is not None

    repeated_submit = client.post(
        f"/api/sessions/{session_id}/plan/preparations/submit",
        json={"plan_revision": revision, "take_id": "take-002"},
    )
    assert repeated_submit.status_code == 200, repeated_submit.text
    assert repeated_submit.json()["linked_shot_id"] == generated["linked_shot_id"]
    assert db.one(
        "SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id,
    )["n"] == 1
    assert first != repeated

    # A distinct operation must reuse a genuine schema-3 snapshot without
    # rewriting it into the legacy schema-2 shape.
    _, reuse_operation_id, _ = _start_operation(
        client, session_id, revision, ["take-002"],
    )
    asyncio.run(main._run_prepare_takes_operation(session_id, reuse_operation_id))
    reused = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{reuse_operation_id}"
    )
    assert reused.status_code == 200, reused.text
    assert reused.json()["state"] == "succeeded"
    reused_row = _row(session_id, revision, "take-002")
    assert reused_row["id"] == candidate["id"]
    assert _evidence(reused_row) == evidence


def test_generated_equal_text_lineages_survive_dependency_refresh_as_distinct_references(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, photo_count=4)
    repeated = _duplicate_outputs()[0]
    distinct = {
        "camera": "close camera",
        "framing": "medium framing",
        "pose": "sitting beside a window",
        "expression": "curious expression",
    }
    _, operation_id, claim = _start_operation(
        client, session_id, revision, ["take-001", "take-002", "take-003"],
    )
    _finish_take(claim, "take-001", repeated)
    _finish_take(claim, "take-002", repeated)
    _finish_take(claim, "take-003", distinct)
    finished = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["state"] == "succeeded"

    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approved.status_code == 200, approved.text
    generated_ids = []
    for take_id in ("take-001", "take-002"):
        submitted = client.post(
            f"/api/sessions/{session_id}/plan/preparations/submit",
            json={"plan_revision": revision, "take_id": take_id},
        )
        assert submitted.status_code == 200, submitted.text
        generated_ids.append(submitted.json()["linked_shot_id"])
    assert generated_ids[0] != generated_ids[1]

    plan = main.session_plan.get_draft(session_id)["plan"]
    selected = plan["selected_resources"][0]
    db.run(
        "UPDATE asset_revision SET translation = ? WHERE library_id = "
        "(SELECT id FROM resource_library WHERE library_key = ?) "
        "AND source_id = ? AND content_digest = ?",
        json.dumps({"label": "invented refreshed room", "scene_theme": "A changed but authorized description."}),
        selected["library_key"],
        selected["source_id"],
        selected["content_digest"],
    )
    refreshed = client.post(
        f"/api/sessions/{session_id}/plan/refresh-resources",
        json={"expected_revision": revision},
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["refreshed"] is True
    next_revision = refreshed.json()["plan_revision"]
    assert next_revision == revision + 1

    _, next_operation_id, next_claim = _start_operation(
        client, session_id, next_revision, ["take-004"],
    )
    _finish_take(next_claim, "take-004", repeated)
    next_state = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{next_operation_id}"
    )
    assert next_state.status_code == 200, next_state.text
    assert next_state.json()["state"] == "succeeded"

    candidate = _row(session_id, next_revision, "take-004")
    comparison = _evidence(candidate)["duplicate_flags"]["comparison"]
    representatives = comparison["representatives"]
    invalidated_ready = _row(session_id, revision, "take-003")
    assert invalidated_ready["status"] == "invalidated"
    assert len(representatives) == 2
    assert {item["prepared_take_id"] for item in representatives} == {
        _row(session_id, revision, "take-001")["id"],
        _row(session_id, revision, "take-002")["id"],
    }
    assert {item["lineage_root_id"] for item in representatives} == {
        _row(session_id, revision, "take-001")["id"],
        _row(session_id, revision, "take-002")["id"],
    }
    assert {item["status"] for item in representatives} == {"generated"}
    assert invalidated_ready["id"] not in {
        item["prepared_take_id"] for item in representatives
    }
    assert len(_evidence(_row(session_id, revision, "take-001"))["duplicate_flags"]["flags"]) == 0
    main.resource_preparation.validate_authoring_prepared_evidence(
        session_id, next_revision, "take-004",
    )


def test_copy_lineage_reference_rejects_forged_origin_and_boolean_identity(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, photo_count=3)
    first, _ = _duplicate_outputs()
    second = {
        "camera": "telephoto camera",
        "framing": "medium framing",
        "pose": "sitting beside a window",
        "expression": "curious expression",
    }
    _, initial_operation, initial_claim = _start_operation(
        client, session_id, revision, ["take-001", "take-002"],
    )
    _finish_take(initial_claim, "take-001", first)
    _finish_take(initial_claim, "take-002", second)
    assert client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{initial_operation}"
    ).json()["state"] == "succeeded"

    plan = main.session_plan.get_draft(session_id)["plan"]
    plan["takes"][2]["label"] = "Updated third take label."
    saved = main.session_plan.save_draft(session_id, plan, revision)
    next_revision = saved["plan_revision"]
    source_second = _row(session_id, revision, "take-002")
    copied_second = _row(session_id, next_revision, "take-002")

    _, operation_id, claim = _start_operation(
        client, session_id, next_revision, ["take-003"],
    )
    _finish_take(claim, "take-003", second)
    state = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    )
    assert state.status_code == 200, state.text
    assert state.json()["state"] == "succeeded"

    candidate = _row(session_id, next_revision, "take-003")
    evidence = _evidence(candidate)
    comparison = evidence["duplicate_flags"]["comparison"]
    assert evidence["schema_version"] == 3
    assert [
        (item["prepared_take_id"], item["lineage_root_id"], item["take_id"])
        for item in evidence["duplicate_flags"]["flags"]
    ] == [(copied_second["id"], source_second["id"], "take-002")]
    assert len(comparison["representatives"]) == 2
    assert all(item["take_id"] != "take-003" for item in comparison["representatives"])

    # The frozen representative is the current verified copy, while its root
    # points to the original prepared row. Forging that root breaks validation.
    original_copy_provenance = json.loads(copied_second["provenance"])
    copied_provenance = json.loads(copied_second["provenance"])
    copied_provenance["copy_forward"]["origin_prepared_id"] = source_second["id"] + 1000
    _write_provenance(copied_second, copied_provenance)
    with pytest.raises(main.session_plan.AuthoringEvidenceInvalid):
        main.resource_preparation.validate_authoring_prepared_evidence(
            session_id, next_revision, "take-003",
        )
    _write_provenance(copied_second, original_copy_provenance)

    main.resource_preparation.validate_authoring_prepared_evidence(
        session_id, next_revision, "take-003",
    )

    # Exact integer checks must reject bool IDs despite bool/int equality.
    original_candidate_provenance = json.loads(candidate["provenance"])
    tampered = json.loads(candidate["provenance"])
    tampered["authoring_evidence"]["duplicate_flags"]["comparison"][
        "candidate_prepared_take_id"
    ] = True
    _write_provenance(candidate, tampered)
    with pytest.raises(
        main.session_plan.AuthoringEvidenceInvalid,
        match="automatic duplicate candidate binding is invalid",
    ):
        main.resource_preparation.validate_authoring_prepared_evidence(
            session_id, next_revision, "take-003",
        )
    _write_provenance(candidate, original_candidate_provenance)

    tampered = json.loads(candidate["provenance"])
    tampered["authoring_evidence"]["duplicate_flags"]["comparison"][
        "representatives_digest"
    ] = "0" * 64
    _write_provenance(candidate, tampered)
    with pytest.raises(
        main.session_plan.AuthoringEvidenceInvalid,
        match="automatic duplicate representative digest is invalid",
    ):
        main.resource_preparation.validate_authoring_prepared_evidence(
            session_id, next_revision, "take-003",
        )


def test_frozen_duplicate_projection_survives_representative_invalidation_and_future_rows(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, photo_count=3)
    repeated = _duplicate_outputs()[0]

    # Prepare a later take first, so it is a comparison representative but is
    # outside the candidate's predecessor context.
    _, future_operation, future_claim = _start_operation(
        client, session_id, revision, ["take-002"],
    )
    _finish_take(future_claim, "take-002", repeated)
    assert client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{future_operation}"
    ).json()["state"] == "succeeded"

    _, candidate_operation, candidate_claim = _start_operation(
        client, session_id, revision, ["take-001"],
    )
    _finish_take(candidate_claim, "take-001", repeated)
    assert client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{candidate_operation}"
    ).json()["state"] == "succeeded"
    candidate = _row(session_id, revision, "take-001")
    original_evidence = _evidence(candidate)
    assert [
        (item["take_id"], item["status"])
        for item in original_evidence["duplicate_flags"]["flags"]
    ] == [("take-002", "ready")]

    representative = _row(session_id, revision, "take-002")
    db.run(
        "UPDATE prepared_take SET status = 'invalidated' WHERE id = ?",
        representative["id"],
    )
    main.resource_preparation.validate_authoring_prepared_evidence(
        session_id, revision, "take-001",
    )

    _, later_operation, later_claim = _start_operation(
        client, session_id, revision, ["take-003"],
    )
    _finish_take(later_claim, "take-003", {
        "camera": "distant camera",
        "framing": "wide framing",
        "pose": "walking away from the window",
        "expression": "thoughtful expression",
    })
    assert client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{later_operation}"
    ).json()["state"] == "succeeded"
    later = _row(session_id, revision, "take-003")
    later_representatives = _evidence(later)["duplicate_flags"]["comparison"][
        "representatives"
    ]
    assert representative["id"] not in {
        item["prepared_take_id"] for item in later_representatives
    }
    assert _evidence(_row(session_id, revision, "take-001")) == original_evidence
    main.resource_preparation.validate_authoring_prepared_evidence(
        session_id, revision, "take-001",
    )
