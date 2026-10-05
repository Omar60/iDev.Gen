from __future__ import annotations

import copy

import pytest

import db
import main
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


def _seed_plan(client, seeded, *, look_origin="user", wardrobe_origin="user"):
    resource = _task41_resource_revision()
    session_id = _task34_resource_session(client, seeded, "saved look application")
    look = "Existing session appearance."
    wardrobe = "She wears the existing jacket."
    authoring = _task41_valid_authoring(
        look_text=look,
        wardrobe_text=wardrobe,
        scene_anchor_triple=resource,
        origin_look=look_origin,
        origin_wardrobe=wardrobe_origin,
        snapshot=False,
        progression=False,
    )
    plan = _task41_make_plan(
        resource, auth=authoring, look=look, wardrobe=wardrobe,
    )
    _task41_seed_authoring_plan(session_id, plan)
    return session_id, plan


def _create_look(client, *, name, appearance, garments=None):
    payload = {"name": name, "appearance": appearance}
    if garments is not None:
        payload["garments"] = garments
    response = client.post("/api/looks", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _apply(client, session_id, *, revision, look, decisions):
    return client.post(
        f"/api/sessions/{session_id}/plan/apply-look",
        json={
            "expected_revision": revision,
            "look_key": look["key"],
            "version": look["version"],
            "decisions": decisions,
        },
    )


def _stored_plan_row(session_id):
    return db.one(
        "SELECT plan_revision, plan_json, updated_at FROM session_plan WHERE session_id = ?",
        session_id,
    )


def test_apply_look_uses_requested_verified_version_and_separates_appearance_from_clothes(
    client, seeded,
):
    session_id, _ = _seed_plan(client, seeded)
    v1 = _create_look(
        client,
        name="Ordered outfit",
        appearance="Soft makeup and short curls.",
        garments=[
            {"wording": "a linen dress"},
            {"wording": "a blue overshirt", "aside": "unbuttoned"},
        ],
    )
    v2_response = client.post(
        f"/api/looks/{v1['key']}/versions",
        json={
            "expected_version": 1,
            "name": "Newer catalog wording",
            "appearance": "Different version appearance.",
            "garments": [{"wording": "a new outfit"}],
        },
    )
    assert v2_response.status_code == 200, v2_response.text
    v2 = v2_response.json()
    assert v2["version"] == 2

    response = _apply(
        client,
        session_id,
        revision=1,
        look=v1,
        decisions={"look": "replace", "initial_wardrobe": "replace"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["plan_revision"] == 2
    assert response.json()["conflicts"]

    plan = client.get(f"/api/sessions/{session_id}/plan").json()["plan"]
    expected_snapshot = {
        key: v1[key]
        for key in ("key", "version", "content_digest", "appearance", "outfit")
    }
    expected_snapshot["look_id"] = expected_snapshot.pop("key")
    assert plan["authoring"]["look_snapshot"] == expected_snapshot
    assert plan["authoring"]["look_snapshot"]["version"] == 1
    assert plan["authoring"]["look_snapshot"] != {
        **{
            key: v2[key]
            for key in ("content_digest", "appearance", "outfit", "version")
        },
        "look_id": v2["key"],
    }
    assert plan["look"] == v1["appearance"]
    assert plan["look"] not in plan["initial_wardrobe"]
    assert plan["initial_wardrobe"] == session_plan.compose_saved_look_wardrobe(v1["outfit"])
    assert plan["authoring"]["shared_state"] == {
        "look": {"origin": "saved_look", "evidence_id": None},
        "initial_wardrobe": {"origin": "saved_look", "evidence_id": None},
    }


def test_keep_uses_user_origin_and_appearance_only_leaves_independent_wardrobe_unchanged(
    client, seeded,
):
    session_id, original = _seed_plan(
        client, seeded, look_origin="assistant", wardrobe_origin="assistant",
    )
    appearance_only = _create_look(
        client, name="Appearance only", appearance="Braided hair.",
    )

    response = _apply(
        client,
        session_id,
        revision=1,
        look=appearance_only,
        decisions={"look": "replace"},
    )
    assert response.status_code == 200, response.text
    plan = client.get(f"/api/sessions/{session_id}/plan").json()["plan"]
    assert plan["look"] == "Braided hair."
    assert plan["initial_wardrobe"] == original["initial_wardrobe"]
    assert plan["authoring"]["look_snapshot"]["outfit"] is None
    assert plan["authoring"]["shared_state"] == {
        "look": {"origin": "saved_look", "evidence_id": None},
        "initial_wardrobe": {"origin": "assistant", "evidence_id": "ev-01"},
    }
    assert plan["authoring"]["evidence"] == original["authoring"]["evidence"]


def test_appearance_only_requires_keep_to_detach_saved_look_wardrobe_origin(client, seeded):
    session_id, _ = _seed_plan(client, seeded)
    outfit_look = _create_look(
        client,
        name="Saved outfit",
        appearance="Short hair.",
        garments=[{"wording": "a green jacket"}],
    )
    applied = _apply(
        client,
        session_id,
        revision=1,
        look=outfit_look,
        decisions={"look": "replace", "initial_wardrobe": "replace"},
    )
    assert applied.status_code == 200, applied.text
    before = _stored_plan_row(session_id)
    wardrobe = client.get(f"/api/sessions/{session_id}/plan").json()["plan"]["initial_wardrobe"]

    appearance_only = _create_look(
        client, name="New appearance only", appearance="Long curls.",
    )
    missing_keep = _apply(
        client,
        session_id,
        revision=2,
        look=appearance_only,
        decisions={"look": "replace"},
    )
    assert missing_keep.status_code == 422
    assert "initial_wardrobe decision must be 'keep'" in missing_keep.text
    assert _stored_plan_row(session_id) == before

    forbidden_replace = _apply(
        client,
        session_id,
        revision=2,
        look=appearance_only,
        decisions={"look": "replace", "initial_wardrobe": "replace"},
    )
    assert forbidden_replace.status_code == 422
    assert "cannot be replaced by an appearance-only look" in forbidden_replace.text
    assert _stored_plan_row(session_id) == before

    kept = _apply(
        client,
        session_id,
        revision=2,
        look=appearance_only,
        decisions={"look": "replace", "initial_wardrobe": "keep"},
    )
    assert kept.status_code == 200, kept.text
    plan = client.get(f"/api/sessions/{session_id}/plan").json()["plan"]
    assert plan["initial_wardrobe"] == wardrobe
    assert plan["authoring"]["look_snapshot"]["outfit"] is None
    assert plan["authoring"]["shared_state"]["initial_wardrobe"] == {
        "origin": "user", "evidence_id": None,
    }


def test_apply_look_rejects_stale_forged_or_generated_mutations_without_writes(
    client, seeded,
):
    session_id, original = _seed_plan(client, seeded)
    look = _create_look(
        client,
        name="Immutable preset",
        appearance="A verified look.",
        garments=[{"wording": "a woven coat"}],
    )

    forged = client.post(
        f"/api/sessions/{session_id}/plan/apply-look",
        json={
            "expected_revision": 1,
            "look_key": look["key"],
            "version": 1,
            "decisions": {"look": "replace", "initial_wardrobe": "replace"},
            "look_snapshot": {"appearance": "client-forged"},
        },
    )
    assert forged.status_code == 422
    assert _stored_plan_row(session_id)["plan_revision"] == 1

    changed = copy.deepcopy(original)
    changed["authoring"]["brief"] = "A later brief."
    saved = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": changed, "expected_revision": 1},
    )
    assert saved.status_code == 200, saved.text
    before_stale = _stored_plan_row(session_id)
    stale = _apply(
        client,
        session_id,
        revision=1,
        look=look,
        decisions={"look": "replace", "initial_wardrobe": "replace"},
    )
    assert stale.status_code == 409
    assert _stored_plan_row(session_id) == before_stale

    frozen_session, _ = _seed_plan(client, seeded)
    shot_id = _plant_shot(frozen_session)
    _plant_prepared_take(
        frozen_session, 1, "take-001", status="generated", linked_shot_id=shot_id,
        final_prompt="immutable generated prompt",
    )
    before_frozen = _stored_plan_row(frozen_session)
    frozen = _apply(
        client,
        frozen_session,
        revision=1,
        look=look,
        decisions={"look": "replace", "initial_wardrobe": "replace"},
    )
    assert frozen.status_code == 409
    assert "continuity fields" in frozen.text
    assert _stored_plan_row(frozen_session) == before_frozen
    row = db.one(
        "SELECT status, final_prompt, linked_shot_id FROM prepared_take WHERE session_id = ?",
        frozen_session,
    )
    assert row == {
        "status": "generated",
        "final_prompt": "immutable generated prompt",
        "linked_shot_id": shot_id,
    }


def test_apply_look_feature_gate_rejects_http_and_direct_calls_without_writes(
    client, seeded, monkeypatch,
):
    session_id, _ = _seed_plan(client, seeded)
    look = _create_look(client, name="Disabled preset", appearance="Soft waves.")
    before = _stored_plan_row(session_id)
    monkeypatch.setattr(main, "is_resource_planning_enabled", lambda: False)

    response = _apply(
        client,
        session_id,
        revision=1,
        look=look,
        decisions={"look": "replace"},
    )
    assert response.status_code == 503
    assert _stored_plan_row(session_id) == before

    with pytest.raises(session_plan.ResourcePlanningDisabled):
        session_plan.apply_saved_look(
            session_id,
            1,
            look["key"],
            1,
            {"look": "replace"},
            planning_enabled=False,
        )
    assert _stored_plan_row(session_id) == before


def test_apply_look_invalid_decisions_fail_before_revision_write(client, seeded):
    session_id, _ = _seed_plan(client, seeded)
    look = _create_look(
        client,
        name="Decision preset",
        appearance="Neat hair.",
        garments=[{"wording": "a cotton jacket"}],
    )
    before = _stored_plan_row(session_id)
    response = _apply(
        client,
        session_id,
        revision=1,
        look=look,
        decisions={"look": "replace"},
    )
    assert response.status_code == 422
    assert "decisions must contain exactly" in response.text
    assert _stored_plan_row(session_id) == before


@pytest.mark.parametrize("version", [True, 1.5, 1 << 63])
def test_apply_look_rejects_non_sqlite_versions_without_writes(
    client, seeded, version,
):
    session_id, _ = _seed_plan(client, seeded)
    look = _create_look(client, name="Bounded version", appearance="Short curls.")
    before = _stored_plan_row(session_id)
    payload = {
        "expected_revision": 1,
        "look_key": look["key"],
        "version": version,
        "decisions": {"look": "replace"},
    }

    response = client.post(
        f"/api/sessions/{session_id}/plan/apply-look", json=payload,
    )
    assert response.status_code == 422, response.text
    assert _stored_plan_row(session_id) == before

    with pytest.raises(session_plan.PlanValidationError):
        session_plan.apply_saved_look(
            session_id,
            1,
            look["key"],
            version,
            {"look": "replace"},
            planning_enabled=True,
        )
    assert _stored_plan_row(session_id) == before


def test_apply_look_invalidates_old_ready_takes_and_revokes_approval(client, seeded):
    session_id, _ = _seed_plan(client, seeded)
    _plant_prepared_take(session_id, 1, "take-001", status="ready")
    db.run(
        "INSERT INTO session_plan_approval (session_id, plan_revision, approved_at) VALUES (?, 1, ?)",
        session_id,
        db.now(),
    )
    look = _create_look(
        client,
        name="Invalidating preset",
        appearance="A new shared look.",
        garments=[{"wording": "a new shared coat"}],
    )

    response = _apply(
        client,
        session_id,
        revision=1,
        look=look,
        decisions={"look": "replace", "initial_wardrobe": "replace"},
    )
    assert response.status_code == 200, response.text
    assert db.one(
        "SELECT status FROM prepared_take WHERE session_id = ? AND plan_revision = 1",
        session_id,
    )["status"] == "invalidated"
    assert db.one(
        "SELECT session_id FROM session_plan_approval WHERE session_id = ?",
        session_id,
    ) is None
