from __future__ import annotations

import db
import main
import pytest
import session_plan
from backend import saved_looks
from test_task9_13_looks_demonstration import (
    _apply_progression,
    _prepare,
    _session,
    _view,
)


@pytest.fixture(autouse=True)
def _manual_only(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)

    def unexpected(*_args, **_kwargs):
        pytest.fail("profile application must not call an assistant or generate images")

    monkeypatch.setattr(main.runner, "start", unexpected)
    monkeypatch.setattr(main.comfy, "queue_prompt", unexpected)
    monkeypatch.setattr(main.enhance, "run_structured", unexpected)
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", unexpected)


def _saved_bracelet_look():
    return saved_looks.create({
        "name": "Invented bracelet ownership look",
        "appearance": "A portrait in a quiet room. She wears a brass bracelet.",
        "garments": [{"wording": "a brass bracelet", "aside": ""}],
    })


def _provenance(saved, revision=1):
    return {
        "origin": "user",
        "source_identity": "task-13-profile-editor",
        "source_digest": saved["content_digest"],
        "accepted_revision": revision,
    }


def _profile(saved, *, bracelet_owner, revision=1):
    appearance = saved["appearance"]
    garment = saved["outfit"]["garments"][0]
    provenance = _provenance(saved, revision)
    segments = []
    if bracelet_owner == "garment":
        split = appearance.index(" She wears")
        segments = [
            {"segment_id": "appearance-1", "start": 0, "end": split,
             "text": appearance[:split], "owner": "permanent",
             "provenance": provenance},
            {"segment_id": "appearance-2", "start": split, "end": len(appearance),
             "text": appearance[split:], "owner": f"garment:{garment['key']}",
             "provenance": provenance},
        ]
    else:
        segments = [{
            "segment_id": "appearance-1", "start": 0, "end": len(appearance),
            "text": appearance, "owner": "permanent", "provenance": provenance,
        }]
    return {
        "schema_version": 1,
        "look": {
            "key": saved["key"], "version": saved["version"],
            "content_digest": saved["content_digest"],
        },
        "appearance_segments": segments,
        "garment_coverage": {garment["key"]: {
            "worn": {
                "regions": {region: "unknown" for region in ("chest", "hips", "legs", "feet")},
                "provenance": provenance,
            },
        }},
        "baseline_coverage": None,
        "inventory_complete": {"value": None, "provenance": provenance},
        "authored_provenance": provenance,
    }


def _publish(saved, profile, *, expected_revision=0):
    return saved_looks.publish_automation_profile(
        saved["key"], saved["version"], saved["content_digest"],
        expected_revision, profile,
    )


def _apply_profile(session_id, expected_revision, saved, published):
    return session_plan.apply_saved_look(
        session_id, expected_revision, saved["key"], saved["version"],
        {"look": "replace", "initial_wardrobe": "replace"},
        planning_enabled=True,
        profile_revision=published["profile_revision"],
        profile_digest=published["profile_digest"],
    )


def _state(session_id):
    return {
        table: db.q(f"SELECT * FROM {table} WHERE session_id = ? ORDER BY rowid", session_id)
        for table in (
            "session_plan", "session_profile_evidence", "prepared_take",
            "take_resource_adaptation", "session_plan_approval", "authoring_operation", "shot",
        )
    }


def test_manual_profile_projection_survives_progression_and_composes_bracelet_only_when_worn(
    client, seeded,
):
    session_id, initial = _session(client, seeded, count=2, scene="A clear studio wall.")
    original_preparation = _prepare(client, session_id, initial["plan_revision"], ["take-001"])[0]
    saved = _saved_bracelet_look()
    published = _publish(saved, _profile(saved, bracelet_owner="garment"))

    applied = _apply_profile(session_id, initial["plan_revision"], saved, published)
    assert applied["plan_revision"] == initial["plan_revision"] + 1
    view = _view(client, session_id)
    plan = view["plan"]
    projection = "A portrait in a quiet room."
    assert plan["look"] == projection
    assert plan["authoring"]["look_snapshot"]["appearance"] == saved["appearance"]
    assert plan["authoring"]["shared_state"]["look"] == {
        "origin": "user", "evidence_id": None,
    }
    invalidated = db.one("SELECT * FROM prepared_take WHERE id = ?", original_preparation["id"])
    assert invalidated["status"] == "invalidated"
    assert invalidated["final_prompt"] == original_preparation["final_prompt"]
    evidence = db.one(
        "SELECT * FROM session_profile_evidence WHERE session_id = ? AND plan_revision = ?",
        session_id, view["plan_revision"],
    )
    assert evidence["look_content_digest"] == saved["content_digest"]
    assert evidence["profile_digest"] == published["profile_digest"]
    assert evidence["appearance_projection"] == projection
    assert saved_looks.get_version(saved["key"], saved["version"])["appearance"] == saved["appearance"]

    preview = client.post(f"/api/sessions/{session_id}/plan/wardrobe-progression/preview", json={
        "expected_revision": view["plan_revision"],
        "start_take_id": "take-001", "end_take_id": "take-002",
        "stage_indices": [0, 1], "event_policy": "replace",
    })
    assert preview.status_code == 200, preview.text
    applied_progression = _apply_progression(client, session_id, preview.json())
    assert applied_progression["plan_revision"] == view["plan_revision"] + 1
    carried_evidence = db.one(
        "SELECT profile_digest, appearance_projection FROM session_profile_evidence "
        "WHERE session_id = ? AND plan_revision = ?",
        session_id, applied_progression["plan_revision"],
    )
    assert carried_evidence == {
        "profile_digest": published["profile_digest"],
        "appearance_projection": projection,
    }

    prepared = _prepare(
        client, session_id, applied_progression["plan_revision"],
        ["take-001", "take-002"],
    )
    worn, removed = prepared
    assert projection in worn["final_prompt"]
    assert "a brass bracelet" in worn["final_prompt"]
    assert "a brass bracelet" not in projection
    assert projection in removed["final_prompt"]
    assert "brass bracelet" not in removed["final_prompt"]
    assert "She wears nothing at all." in removed["final_prompt"]


def test_profile_application_keeps_saved_look_origin_when_projection_is_identical(
    client, seeded,
):
    session_id, view = _session(client, seeded, count=1, scene="An empty studio.")
    saved = saved_looks.create({
        "name": "Invented all-permanent look",
        "appearance": "Soft curls and natural makeup.",
        "garments": [{"wording": "a wool coat", "aside": ""}],
    })
    provenance = _provenance(saved)
    profile = _profile(saved, bracelet_owner="permanent")
    profile["appearance_segments"] = [{
        "segment_id": "appearance-1", "start": 0,
        "end": len(saved["appearance"]), "text": saved["appearance"],
        "owner": "permanent", "provenance": provenance,
    }]
    published = _publish(saved, profile)
    applied = _apply_profile(session_id, view["plan_revision"], saved, published)
    plan = _view(client, session_id)["plan"]
    assert applied["plan_revision"] == view["plan_revision"] + 1
    assert plan["look"] == saved["appearance"]
    assert plan["authoring"]["shared_state"]["look"] == {
        "origin": "saved_look", "evidence_id": None,
    }
    assert db.one("SELECT COUNT(*) AS n FROM session_profile_evidence WHERE session_id = ?", session_id)["n"] == 1


def test_exact_profile_replay_is_a_noop_and_preserves_bound_evidence(
    client, seeded,
):
    session_id, view = _session(client, seeded, count=1, scene="An empty studio.")
    saved = _saved_bracelet_look()
    published = _publish(saved, _profile(saved, bracelet_owner="garment"))
    first = _apply_profile(session_id, view["plan_revision"], saved, published)
    before = _state(session_id)

    replay = _apply_profile(session_id, first["plan_revision"], saved, published)

    assert replay["plan_revision"] == first["plan_revision"]
    assert _state(session_id) == before


def test_generic_look_edit_does_not_carry_active_profile_evidence(
    client, seeded,
):
    session_id, view = _session(client, seeded, count=1, scene="An empty studio.")
    saved = _saved_bracelet_look()
    published = _publish(saved, _profile(saved, bracelet_owner="garment"))
    applied = _apply_profile(session_id, view["plan_revision"], saved, published)
    candidate = _view(client, session_id)["plan"]
    candidate["look"] = "A separately authored manual appearance."

    response = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": candidate, "expected_revision": applied["plan_revision"]},
    )

    assert response.status_code == 200, response.text
    new_revision = response.json()["plan_revision"]
    assert new_revision == applied["plan_revision"] + 1
    assert db.one(
        "SELECT profile_digest FROM session_profile_evidence "
        "WHERE session_id = ? AND plan_revision = ?",
        session_id, new_revision,
    ) is None
    latest = _view(client, session_id)
    assert latest["plan"]["look"] == candidate["look"]
    assert session_plan._load_session_profile_application(
        session_id, new_revision, latest["plan"],
    ) is None


def test_profile_application_refuses_known_permanent_progression_conflict_without_writes(
    client, seeded,
):
    session_id, view = _session(client, seeded, count=2, scene="A clear studio wall.")
    saved = _saved_bracelet_look()
    profile = _profile(saved, bracelet_owner="permanent")
    published = _publish(saved, profile)

    # Establish the exact look and a reviewed stage that removes its bracelet.
    ordinary = session_plan.apply_saved_look(
        session_id, view["plan_revision"], saved["key"], saved["version"],
        {"look": "replace", "initial_wardrobe": "replace"}, planning_enabled=True,
    )
    preview = client.post(f"/api/sessions/{session_id}/plan/wardrobe-progression/preview", json={
        "expected_revision": ordinary["plan_revision"],
        "start_take_id": "take-001", "end_take_id": "take-002",
        "stage_indices": [0, 1], "event_policy": "replace",
    })
    assert preview.status_code == 200, preview.text
    _apply_progression(client, session_id, preview.json())
    before = _state(session_id)

    with pytest.raises(session_plan.PlanOwnershipConflict, match="Permanent appearance ownership"):
        _apply_profile(session_id, ordinary["plan_revision"] + 1, saved, published)
    assert _state(session_id) == before


def test_first_profile_application_after_a_linked_shot_is_frozen_without_writes(
    client, seeded,
):
    session_id, view = _session(client, seeded, count=1, scene="A clear studio wall.")
    prepared = _prepare(client, session_id, view["plan_revision"], ["take-001"])[0]
    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": view["plan_revision"]},
    )
    assert approved.status_code == 200, approved.text
    linked = client.post(f"/api/sessions/{session_id}/plan/preparations/submit", json={
        "plan_revision": view["plan_revision"], "take_id": "take-001",
    })
    assert linked.status_code == 200, linked.text
    saved = _saved_bracelet_look()
    published = _publish(saved, _profile(saved, bracelet_owner="garment"))
    before = _state(session_id)

    with pytest.raises(session_plan.PlanConstantsFrozenAfterGenerated, match="linked shots"):
        _apply_profile(session_id, view["plan_revision"], saved, published)
    assert _state(session_id) == before
    assert db.one("SELECT * FROM prepared_take WHERE id = ?", prepared["id"])["linked_shot_id"] is not None


def test_linked_shot_freezes_a_different_profile_identity_with_the_same_projection(
    client, seeded,
):
    session_id, view = _session(client, seeded, count=1, scene="A clear studio wall.")
    saved = _saved_bracelet_look()
    profile = _profile(saved, bracelet_owner="garment")
    published = _publish(saved, profile)
    applied = _apply_profile(session_id, view["plan_revision"], saved, published)
    prepared = _prepare(client, session_id, applied["plan_revision"], ["take-001"])[0]
    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": applied["plan_revision"]},
    )
    assert approved.status_code == 200, approved.text
    linked = client.post(f"/api/sessions/{session_id}/plan/preparations/submit", json={
        "plan_revision": applied["plan_revision"], "take_id": "take-001",
    })
    assert linked.status_code == 200, linked.text

    revised_profile = _publish(
        saved,
        _profile(saved, bracelet_owner="garment", revision=2),
        expected_revision=published["profile_revision"],
    )
    assert revised_profile["profile_revision"] != published["profile_revision"]
    assert revised_profile["profile_digest"] != published["profile_digest"]
    assert saved_looks.permanent_appearance_projection(revised_profile) == saved_looks.permanent_appearance_projection(published)
    before = _state(session_id)

    with pytest.raises(session_plan.PlanConstantsFrozenAfterGenerated, match="linked shots"):
        _apply_profile(session_id, applied["plan_revision"], saved, revised_profile)

    assert _state(session_id) == before
    assert db.one("SELECT * FROM prepared_take WHERE id = ?", prepared["id"])["linked_shot_id"] is not None
