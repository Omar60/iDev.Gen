"""Independent HTTP acceptance for immutable saved-look application."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import threading
from uuid import uuid4

import db
import pytest
import session_plan
from test_session_plan import (
    _plant_prepared_take,
    _plant_shot,
    _task34_resource_session,
    _task41_make_plan,
    _task41_resource_revision,
    _task41_seed_authoring_plan,
    _task41_valid_authoring,
)


def _session_with_authoring_plan(
    client,
    seeded,
    name: str,
    *,
    look: str = "Existing studio appearance.",
    wardrobe: str = "She wears an existing wool coat.",
    look_origin: str = "user",
    wardrobe_origin: str = "user",
) -> tuple[int, dict]:
    resource = _task41_resource_revision()
    session_id = _task34_resource_session(client, seeded, name)
    plan = _task41_make_plan(
        resource,
        auth=_task41_valid_authoring(
            look_text=look,
            wardrobe_text=wardrobe,
            scene_anchor_triple=resource,
            snapshot=False,
            progression=False,
            origin_look=look_origin,
            origin_wardrobe=wardrobe_origin,
        ),
        look=look,
        wardrobe=wardrobe,
    )
    _task41_seed_authoring_plan(session_id, plan)
    return session_id, plan


def _create_look(client, name: str, appearance: str, garments: list[dict]) -> dict:
    response = client.post("/api/looks", json={
        "name": name,
        "appearance": appearance,
        "garments": garments,
    })
    assert response.status_code == 200, response.text
    return response.json()


def _apply(
    client,
    session_id: int,
    look: dict,
    revision: int,
    *,
    decisions: dict | None = None,
):
    return client.post(
        f"/api/sessions/{session_id}/plan/apply-look",
        json={
            "expected_revision": revision,
            "look_key": look["key"],
            "version": look["version"],
            "decisions": decisions or {
                "look": "replace", "initial_wardrobe": "replace",
            },
        },
    )


def _stored_plan_row(session_id: int) -> dict:
    return db.one(
        "SELECT plan_revision, plan_json, updated_at FROM session_plan WHERE session_id = ?",
        session_id,
    )


def _insert_active_operation(session_id: int) -> str:
    operation_id = str(uuid4())
    request_id = str(uuid4())
    db.run(
        """INSERT INTO authoring_operation (
               operation_id, request_id, session_id, plan_revision, kind,
               request_digest, state, fencing_token, lease_expires_at,
               requested_json, completed_json, remaining_json, created_at, updated_at
           ) VALUES (?, ?, ?, 1, 'shared_suggestions', ?, 'active', 7, ?, '[]', '[]', '[]', ?, ?)""",
        operation_id,
        request_id,
        session_id,
        "a" * 64,
        db.authoring_operation_lease_deadline(),
        db.now(),
        db.now(),
    )
    return operation_id


def _expected_snapshot(look: dict) -> dict:
    return {
        "look_id": look["key"],
        "version": look["version"],
        "content_digest": look["content_digest"],
        "appearance": look["appearance"],
        "outfit": look["outfit"],
    }


def test_applied_session_keeps_its_full_snapshot_after_a_later_look_edit(
    client, seeded,
):
    session_id, _ = _session_with_authoring_plan(
        client, seeded, "saved-look acceptance copy",
    )
    look_v1 = _create_look(
        client,
        "Layered acceptance look",
        "Soft light and short curls.",
        [
            {"wording": "a linen dress", "aside": ""},
            {"wording": "a blue overshirt", "aside": "unbuttoned"},
        ],
    )

    applied = _apply(client, session_id, look_v1, revision=1)
    assert applied.status_code == 200, applied.text
    assert applied.json()["plan_revision"] == 2
    accepted = client.get(f"/api/sessions/{session_id}/plan").json()
    accepted_plan = accepted["plan"]
    assert accepted_plan["authoring"]["look_snapshot"] == _expected_snapshot(look_v1)
    assert accepted_plan["look"] == "Soft light and short curls."
    assert accepted_plan["initial_wardrobe"] == (
        "She wears a linen dress, and a blue overshirt."
    )

    edited = client.post(
        f"/api/looks/{look_v1['key']}/versions",
        json={
            "expected_version": 1,
            "name": "Changed later",
            "appearance": "A later appearance revision.",
            "garments": [
                {"wording": "a dark jacket", "aside": "open"},
                {"wording": "a cotton shirt", "aside": ""},
            ],
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 2

    reopened = client.get(f"/api/sessions/{session_id}/plan").json()
    assert reopened["plan_revision"] == accepted["plan_revision"]
    assert reopened["plan"] == accepted_plan
    assert db.one(
        "SELECT plan_revision FROM session_plan WHERE session_id = ?", session_id,
    )["plan_revision"] == 2


def test_simultaneous_http_applications_have_one_cas_winner_and_no_mixed_snapshot(
    client, seeded,
):
    session_id, _ = _session_with_authoring_plan(
        client, seeded, "saved-look acceptance CAS",
    )
    looks = [
        _create_look(
            client,
            "First concurrent look",
            "First appearance.",
            [{"wording": "a cream cardigan", "aside": "buttoned"}],
        ),
        _create_look(
            client,
            "Second concurrent look",
            "Second appearance.",
            [
                {"wording": "a navy jacket", "aside": "open"},
                {"wording": "a striped shirt", "aside": ""},
            ],
        ),
    ]
    start = threading.Barrier(2)

    def submit(look: dict):
        start.wait(timeout=10)
        return _apply(client, session_id, look, revision=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, look) for look in looks]
        responses = [future.result(timeout=20) for future in futures]

    assert sorted(response.status_code for response in responses) == [200, 409]
    winner_index = next(
        index for index, response in enumerate(responses)
        if response.status_code == 200
    )
    assert responses[winner_index].json()["plan_revision"] == 2
    stale = responses[1 - winner_index]
    assert "expected 1" in stale.text

    stored = client.get(f"/api/sessions/{session_id}/plan").json()
    plan = stored["plan"]
    winner = looks[winner_index]
    assert stored["plan_revision"] == 2
    assert plan["authoring"]["look_snapshot"] == _expected_snapshot(winner)
    assert plan["look"] == winner["appearance"]
    expected_wardrobe = (
        "She wears a cream cardigan."
        if winner_index == 0
        else "She wears a navy jacket, and a striped shirt."
    )
    assert plan["initial_wardrobe"] == expected_wardrobe
    assert plan["authoring"]["shared_state"] == {
        "look": {"origin": "saved_look", "evidence_id": None},
        "initial_wardrobe": {"origin": "saved_look", "evidence_id": None},
    }
    assert db.one(
        "SELECT COUNT(*) AS count FROM session_plan WHERE session_id = ?",
        session_id,
    )["count"] == 1


def test_keep_decisions_preserve_empty_and_assistant_values_as_user_overrides(
    client, seeded,
):
    assistant_session, assistant_plan = _session_with_authoring_plan(
        client,
        seeded,
        "saved-look keep assistant values",
        look="Assistant appearance.",
        wardrobe="She wears an assistant-selected coat.",
        look_origin="assistant",
        wardrobe_origin="assistant",
    )
    empty_session, empty_plan = _session_with_authoring_plan(
        client,
        seeded,
        "saved-look keep empty values",
        look="",
        wardrobe="",
        look_origin="user",
        wardrobe_origin="user",
    )
    look = _create_look(
        client,
        "Kept-value look",
        "Preset appearance.",
        [{"wording": "a soft cardigan", "aside": "buttoned"}],
    )
    keep_both = {"look": "keep", "initial_wardrobe": "keep"}

    for session_id in (assistant_session, empty_session):
        applied = _apply(
            client, session_id, look, revision=1, decisions=keep_both,
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["plan_revision"] == 2

    assistant_result = client.get(
        f"/api/sessions/{assistant_session}/plan",
    ).json()["plan"]
    assert assistant_result["look"] == assistant_plan["look"]
    assert assistant_result["initial_wardrobe"] == assistant_plan["initial_wardrobe"]
    assert assistant_result["authoring"]["shared_state"] == {
        "look": {"origin": "user", "evidence_id": None},
        "initial_wardrobe": {"origin": "user", "evidence_id": None},
    }
    assert assistant_result["authoring"]["evidence"] == assistant_plan["authoring"]["evidence"]

    empty_result = client.get(
        f"/api/sessions/{empty_session}/plan",
    ).json()["plan"]
    assert empty_result["look"] == ""
    assert empty_result["initial_wardrobe"] == ""
    assert empty_result["authoring"]["shared_state"] == {
        "look": {"origin": "user", "evidence_id": None},
        "initial_wardrobe": {"origin": "user", "evidence_id": None},
    }


def test_missing_and_corrupt_look_versions_have_stable_zero_write_failures(
    client, seeded,
):
    session_id, _ = _session_with_authoring_plan(
        client, seeded, "saved-look invalid version preservation",
    )
    look = _create_look(
        client,
        "Look with one valid version",
        "Verified appearance.",
        [{"wording": "a wool jacket", "aside": "open"}],
    )
    now = db.now()
    db.run(
        """INSERT INTO saved_look_version
           (look_key, version, name, snapshot_json, created_at)
           VALUES (?, 2, ?, ?, ?)""",
        look["key"],
        "Corrupt test version",
        "{malformed-json",
        now,
    )
    db.run(
        "INSERT INTO session_plan_approval (session_id, plan_revision, approved_at) VALUES (?, 1, ?)",
        session_id,
        now,
    )
    operation_id = _insert_active_operation(session_id)
    before_plan = _stored_plan_row(session_id)
    before_operation = db.one(
        "SELECT state, fencing_token, lease_expires_at, plan_revision "
        "FROM authoring_operation WHERE operation_id = ?",
        operation_id,
    )
    before_approval = db.one(
        "SELECT session_id, plan_revision, approved_at "
        "FROM session_plan_approval WHERE session_id = ?",
        session_id,
    )

    for version, status, code in (
        (99, 404, "look_not_found"),
        (2, 500, "look_data_invalid"),
    ):
        response = client.post(
            f"/api/sessions/{session_id}/plan/apply-look",
            json={
                "expected_revision": 1,
                "look_key": look["key"],
                "version": version,
                "decisions": {"look": "replace", "initial_wardrobe": "replace"},
            },
        )
        assert response.status_code == status, response.text
        assert response.json()["detail"]["code"] == code
        assert _stored_plan_row(session_id) == before_plan
        assert db.one(
            "SELECT state, fencing_token, lease_expires_at, plan_revision "
            "FROM authoring_operation WHERE operation_id = ?",
            operation_id,
        ) == before_operation
        assert db.one(
            "SELECT session_id, plan_revision, approved_at "
            "FROM session_plan_approval WHERE session_id = ?",
            session_id,
        ) == before_approval

    applied = _apply(client, session_id, look, revision=1)
    assert applied.status_code == 200, applied.text
    operation_after = db.one(
        "SELECT state, fencing_token, lease_expires_at, plan_revision "
        "FROM authoring_operation WHERE operation_id = ?",
        operation_id,
    )
    assert operation_after["state"] == "cancelled"
    assert operation_after["fencing_token"] == 8
    assert operation_after["lease_expires_at"] is None
    assert operation_after["plan_revision"] == 1
    assert db.one(
        "SELECT session_id FROM session_plan_approval WHERE session_id = ?",
        session_id,
    ) is None


def test_same_content_identity_and_version_changes_freeze_after_generation(
    client, seeded,
):
    look = _create_look(
        client,
        "Original continuity look",
        "Continuity appearance.",
        [{"wording": "a charcoal coat", "aside": "open"}],
    )
    wardrobe = "She wears a charcoal coat."
    session_id, _ = _session_with_authoring_plan(
        client,
        seeded,
        "saved-look generated continuity",
        look=look["appearance"],
        wardrobe=wardrobe,
    )
    initial = _apply(
        client,
        session_id,
        look,
        revision=1,
        decisions={"look": "keep", "initial_wardrobe": "keep"},
    )
    assert initial.status_code == 200, initial.text
    assert initial.json()["plan_revision"] == 2

    shot_id = _plant_shot(session_id, prompt="generated continuity take")
    _plant_prepared_take(
        session_id,
        2,
        "take-001",
        status="generated",
        linked_shot_id=shot_id,
        final_prompt="Generated prompt retained across rejected applications.",
    )
    db.run(
        "INSERT INTO session_plan_approval (session_id, plan_revision, approved_at) VALUES (?, 2, ?)",
        session_id,
        db.now(),
    )
    before_plan = _stored_plan_row(session_id)
    before_prepared = db.one(
        "SELECT status, final_prompt, linked_shot_id FROM prepared_take "
        "WHERE session_id = ? AND plan_revision = 2 AND take_id = ?",
        session_id,
        "take-001",
    )
    before_approval = db.one(
        "SELECT session_id, plan_revision, approved_at "
        "FROM session_plan_approval WHERE session_id = ?",
        session_id,
    )

    same_content_other_identity = client.post("/api/looks", json={
        "name": "Same outfit under another look identity",
        "appearance": look["appearance"],
        "outfit_key": look["outfit"]["outfit_key"],
    })
    assert same_content_other_identity.status_code == 200, same_content_other_identity.text
    other_identity = same_content_other_identity.json()
    assert other_identity["key"] != look["key"]
    assert other_identity["content_digest"] == look["content_digest"]

    renamed_version = client.post(
        f"/api/looks/{look['key']}/versions",
        json={"expected_version": 1, "name": "Same content in version two"},
    )
    assert renamed_version.status_code == 200, renamed_version.text
    version_two = renamed_version.json()
    assert version_two["version"] == 2
    assert version_two["content_digest"] == look["content_digest"]

    keep_both = {"look": "keep", "initial_wardrobe": "keep"}
    for candidate in (other_identity, version_two):
        refused = _apply(
            client,
            session_id,
            candidate,
            revision=2,
            decisions=keep_both,
        )
        assert refused.status_code == 409, refused.text
        assert "continuity fields" in refused.text
        assert _stored_plan_row(session_id) == before_plan
        assert db.one(
            "SELECT status, final_prompt, linked_shot_id FROM prepared_take "
            "WHERE session_id = ? AND plan_revision = 2 AND take_id = ?",
            session_id,
            "take-001",
        ) == before_prepared
        assert db.one(
            "SELECT session_id, plan_revision, approved_at "
            "FROM session_plan_approval WHERE session_id = ?",
            session_id,
        ) == before_approval


def test_generic_plan_save_cannot_forge_applied_look_snapshot(client, seeded):
    session_id, _ = _session_with_authoring_plan(
        client, seeded, "saved-look generic-save ownership",
    )
    look = _create_look(
        client,
        "Server-owned snapshot",
        "Original snapshot appearance.",
        [{"wording": "a linen jacket", "aside": "open"}],
    )
    applied = _apply(
        client,
        session_id,
        look,
        revision=1,
        decisions={"look": "keep", "initial_wardrobe": "keep"},
    )
    assert applied.status_code == 200, applied.text
    before = _stored_plan_row(session_id)
    candidate = copy.deepcopy(
        client.get(f"/api/sessions/{session_id}/plan").json()["plan"],
    )
    candidate["authoring"]["look_snapshot"]["look_id"] = "forged-look-identity"
    refused = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": candidate, "expected_revision": 2},
    )
    assert refused.status_code == 409, refused.text
    assert "look_snapshot is server-owned" in refused.text
    assert _stored_plan_row(session_id) == before


def test_sqlite_unrepresentable_look_version_is_rejected_without_server_error(
    client, seeded,
):
    session_id, _ = _session_with_authoring_plan(
        client, seeded, "saved-look sqlite version bound",
    )
    look = _create_look(
        client, "Bounded look version", "A bounded appearance.", [],
    )
    before = _stored_plan_row(session_id)
    try:
        response = client.post(
            f"/api/sessions/{session_id}/plan/apply-look",
            json={
                "expected_revision": 1,
                "look_key": look["key"],
                "version": 1 << 63,
                "decisions": {"look": "replace"},
            },
        )
    except OverflowError as exc:
        pytest.fail(
            "unrepresentable version escaped the API as an SQLite binding error: "
            f"{exc}"
        )
    assert response.status_code == 422, response.text
    assert _stored_plan_row(session_id) == before
    with pytest.raises(session_plan.PlanValidationError, match="version"):
        session_plan.apply_saved_look(
            session_id,
            1,
            look["key"],
            1 << 63,
            {"look": "replace"},
            planning_enabled=True,
        )
    assert _stored_plan_row(session_id) == before
