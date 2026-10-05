from __future__ import annotations

import copy

import pytest

import db
import main
import session_plan
from test_session_plan import (
    _task34_resource_session,
    _task41_make_plan,
    _task41_resource_revision,
    _task41_seed_authoring_plan,
    _task41_valid_authoring,
    _task42_takes,
    _plant_prepared_take,
    _plant_shot,
)


GARMENTS = [
    {"key": "coat", "wording": "a wool coat", "aside": ""},
    {"key": "shirt", "wording": "a cotton shirt", "aside": ""},
]
LOOK = "Soft curls and natural makeup."
FULL_WARDROBE = "She wears a wool coat, and a cotton shirt."
STAGES = [FULL_WARDROBE, "She wears a cotton shirt.", "She wears nothing at all."]


def _seed_plan(client, seeded, *, changes=None, take_count=6, name="progression"):
    resource = _task41_resource_revision()
    session_id = _task34_resource_session(client, seeded, name)
    authoring = _task41_valid_authoring(
        look_text=LOOK,
        wardrobe_text=FULL_WARDROBE,
        garments=copy.deepcopy(GARMENTS),
        progression=False,
        scene_anchor_triple=resource,
        origin_look="saved_look",
        origin_wardrobe="saved_look",
    )
    plan = _task41_make_plan(
        resource,
        auth=authoring,
        look=LOOK,
        wardrobe=FULL_WARDROBE,
        takes=_task42_takes(take_count),
    )
    plan["wardrobe_changes"] = copy.deepcopy(changes or [])
    _task41_seed_authoring_plan(session_id, plan)
    return session_id, plan


def _stored_plan_row(session_id):
    return db.one(
        "SELECT plan_revision, plan_json, updated_at FROM session_plan WHERE session_id = ?",
        session_id,
    )


def _preview(client, session_id, *, policy="replace", start="take-001", end="take-006", stages=None):
    return client.post(
        f"/api/sessions/{session_id}/plan/wardrobe-progression/preview",
        json={
            "expected_revision": 1,
            "start_take_id": start,
            "end_take_id": end,
            "stage_indices": stages or [0, 1, 2],
            "event_policy": policy,
        },
    )


def _apply(client, session_id, preview):
    return client.post(
        f"/api/sessions/{session_id}/plan/wardrobe-progression/apply",
        json={
            "expected_revision": preview["expected_revision"],
            "preview_token": preview["preview_token"],
            "review_digest": preview["review_digest"],
            "reviewed_wardrobes": preview["reviewed_wardrobes"],
        },
    )


def _plan(client, session_id):
    response = client.get(f"/api/sessions/{session_id}/plan")
    assert response.status_code == 200, response.text
    return response.json()["plan"]


def test_preview_binds_each_effective_state_and_apply_persists_stable_events(client, seeded):
    session_id, _ = _seed_plan(client, seeded)
    before = _stored_plan_row(session_id)
    preview_response = _preview(
        client, session_id, start="take-002", end="take-005",
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert _stored_plan_row(session_id) == before
    assert preview["initial_wardrobe"] == FULL_WARDROBE
    assert preview["wardrobe_progression"] == {
        "source_look_digest": _plan(client, session_id)["authoring"]["look_snapshot"]["content_digest"],
        "start_take_id": "take-002",
        "end_take_id": "take-005",
        "stage_indices": [0, 1, 2],
        "applied_revision": 2,
    }
    assert preview["reviewed_wardrobes"] == [
        {"take_id": "take-001", "wardrobe": FULL_WARDROBE},
        {"take_id": "take-002", "wardrobe": FULL_WARDROBE},
        {"take_id": "take-003", "wardrobe": FULL_WARDROBE},
        {"take_id": "take-004", "wardrobe": STAGES[1]},
        {"take_id": "take-005", "wardrobe": STAGES[2]},
        {"take_id": "take-006", "wardrobe": STAGES[2]},
    ]
    applied = _apply(client, session_id, preview)
    assert applied.status_code == 200, applied.text
    assert applied.json()["plan_revision"] == 2
    saved = _plan(client, session_id)
    assert saved["initial_wardrobe"] == FULL_WARDROBE
    assert saved["authoring"]["wardrobe_progression"] == preview["wardrobe_progression"]
    assert saved["wardrobe_changes"] == [
        {"take_id": "take-004", "scope": "from_here", "wardrobe": STAGES[1]},
        {"take_id": "take-005", "scope": "from_here", "wardrobe": STAGES[2]},
    ]
    assert session_plan.resolve_effective_wardrobes(saved) == {
        entry["take_id"]: entry["wardrobe"]
        for entry in preview["reviewed_wardrobes"]
    }


def test_apply_materializes_first_selected_stage_and_marks_changed_origin_as_user(client, seeded):
    session_id, _ = _seed_plan(client, seeded, name="partial starting stage")
    request = _preview(client, session_id, stages=[1, 2])
    assert request.status_code == 200, request.text
    preview = request.json()
    assert preview["initial_wardrobe"] == STAGES[1]
    assert _apply(client, session_id, preview).status_code == 200
    saved = _plan(client, session_id)
    assert saved["initial_wardrobe"] == STAGES[1]
    assert saved["authoring"]["shared_state"]["initial_wardrobe"] == {
        "origin": "user", "evidence_id": None,
    }
    assert saved["wardrobe_changes"] == [
        {"take_id": "take-006", "scope": "from_here", "wardrobe": STAGES[2]},
    ]


def test_replace_keeps_this_take_and_moves_a_transition_to_the_next_free_take(client, seeded):
    changes = [
        {"take_id": "take-004", "scope": "this_take", "wardrobe": "A checked scarf."},
        {"take_id": "take-005", "scope": "from_here", "wardrobe": "An old saved state."},
    ]
    session_id, _ = _seed_plan(client, seeded, changes=changes, name="override progression")
    preview_response = _preview(client, session_id, policy="replace")
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["reviewed_wardrobes"] == [
        {"take_id": "take-001", "wardrobe": FULL_WARDROBE},
        {"take_id": "take-002", "wardrobe": FULL_WARDROBE},
        {"take_id": "take-003", "wardrobe": FULL_WARDROBE},
        {"take_id": "take-004", "wardrobe": "A checked scarf."},
        {"take_id": "take-005", "wardrobe": STAGES[1]},
        {"take_id": "take-006", "wardrobe": STAGES[2]},
    ]
    applied = _apply(client, session_id, preview)
    assert applied.status_code == 200, applied.text
    saved = _plan(client, session_id)
    assert saved["wardrobe_changes"] == [
        {"take_id": "take-004", "scope": "this_take", "wardrobe": "A checked scarf."},
        {"take_id": "take-005", "scope": "from_here", "wardrobe": STAGES[1]},
        {"take_id": "take-006", "scope": "from_here", "wardrobe": STAGES[2]},
    ]


def test_merge_keeps_existing_events_and_previews_their_effective_states(client, seeded):
    changes = [{
        "take_id": "take-002",
        "scope": "from_here",
        "wardrobe": "A reviewed merge state.",
    }]
    session_id, _ = _seed_plan(client, seeded, changes=changes, name="merge progression")
    preview_response = _preview(client, session_id, policy="merge")
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["reviewed_wardrobes"] == [
        {"take_id": "take-001", "wardrobe": FULL_WARDROBE},
        {"take_id": "take-002", "wardrobe": "A reviewed merge state."},
        {"take_id": "take-003", "wardrobe": "A reviewed merge state."},
        {"take_id": "take-004", "wardrobe": STAGES[1]},
        {"take_id": "take-005", "wardrobe": STAGES[1]},
        {"take_id": "take-006", "wardrobe": STAGES[2]},
    ]
    assert _apply(client, session_id, preview).status_code == 200
    saved = _plan(client, session_id)
    assert saved["wardrobe_changes"][0] == changes[0]
    assert len(saved["wardrobe_changes"]) == 3


def test_merge_collision_refuses_with_replace_remedy_and_no_write(client, seeded):
    changes = [{
        "take_id": "take-004",
        "scope": "from_here",
        "wardrobe": "A conflicting saved state.",
    }]
    session_id, _ = _seed_plan(client, seeded, changes=changes, name="collision progression")
    before = _stored_plan_row(session_id)
    merge = _preview(client, session_id, policy="merge")
    assert merge.status_code == 409
    assert "choose replace" in merge.text
    assert _stored_plan_row(session_id) == before
    replace = _preview(client, session_id, policy="replace")
    assert replace.status_code == 200, replace.text
    assert replace.json()["wardrobe_changes"] == [
        {"take_id": "take-004", "scope": "from_here", "wardrobe": STAGES[1]},
        {"take_id": "take-006", "scope": "from_here", "wardrobe": STAGES[2]},
    ]


def test_consecutive_this_take_overrides_refuse_an_unrepresentable_stage(client, seeded):
    changes = [
        {"take_id": "take-004", "scope": "this_take", "wardrobe": "Override four."},
        {"take_id": "take-005", "scope": "this_take", "wardrobe": "Override five."},
    ]
    session_id, _ = _seed_plan(client, seeded, changes=changes, name="blocked progression")
    before = _stored_plan_row(session_id)
    response = _preview(client, session_id, policy="replace")
    assert response.status_code == 409
    assert "no unoverridden take" in response.text
    assert _stored_plan_row(session_id) == before


def test_review_tampering_stale_cas_and_token_tampering_make_zero_writes(client, seeded):
    session_id, _ = _seed_plan(client, seeded, name="tamper progression")
    preview = _preview(client, session_id).json()
    before = _stored_plan_row(session_id)
    altered = copy.deepcopy(preview["reviewed_wardrobes"])
    altered[1]["wardrobe"] = "Forged reviewed value."
    tampered = client.post(
        f"/api/sessions/{session_id}/plan/wardrobe-progression/apply",
        json={
            "expected_revision": 1,
            "preview_token": preview["preview_token"],
            "review_digest": preview["review_digest"],
            "reviewed_wardrobes": altered,
        },
    )
    assert tampered.status_code == 409
    assert _stored_plan_row(session_id) == before

    bad_token = client.post(
        f"/api/sessions/{session_id}/plan/wardrobe-progression/apply",
        json={
            "expected_revision": 1,
            "preview_token": preview["preview_token"] + "x",
            "review_digest": preview["review_digest"],
            "reviewed_wardrobes": preview["reviewed_wardrobes"],
        },
    )
    assert bad_token.status_code == 409
    assert _stored_plan_row(session_id) == before

    draft = _plan(client, session_id)
    draft["authoring"]["brief"] = "Another tab saved first."
    saved = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": draft, "expected_revision": 1},
    )
    assert saved.status_code == 200, saved.text
    after_draft = _stored_plan_row(session_id)
    stale = _apply(client, session_id, preview)
    assert stale.status_code == 409
    assert _stored_plan_row(session_id) == after_draft


def test_reordering_keeps_materialized_events_bound_to_stable_take_ids(client, seeded):
    session_id, _ = _seed_plan(client, seeded, name="reorder progression")
    preview = _preview(client, session_id).json()
    assert _apply(client, session_id, preview).status_code == 200
    applied = _plan(client, session_id)
    events_by_id = {event["take_id"]: event for event in applied["wardrobe_changes"]}
    applied["takes"] = list(reversed(applied["takes"]))
    saved = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": applied, "expected_revision": 2},
    )
    assert saved.status_code == 200, saved.text
    reordered = _plan(client, session_id)
    assert {event["take_id"]: event for event in reordered["wardrobe_changes"]} == events_by_id
    assert reordered["authoring"]["wardrobe_progression"] == applied["authoring"]["wardrobe_progression"]
    assert session_plan.resolve_effective_wardrobes(reordered)["take-006"] == STAGES[2]
    assert session_plan.resolve_effective_wardrobes(reordered)["take-001"] == STAGES[1]


def test_short_interval_and_generated_initial_change_refuse_without_writes(client, seeded):
    short_session, _ = _seed_plan(client, seeded, name="short progression interval")
    before_short = _stored_plan_row(short_session)
    short = _preview(
        client, short_session, start="take-004", end="take-005", stages=[0, 1, 2],
    )
    assert short.status_code == 422
    assert "takes for 3 selected stages" in short.text
    assert _stored_plan_row(short_session) == before_short

    frozen_session, _ = _seed_plan(client, seeded, name="frozen progression initial")
    shot_id = _plant_shot(frozen_session, prompt="generated before progression")
    _plant_prepared_take(
        frozen_session, 1, "take-001", status="generated",
        linked_shot_id=shot_id, final_prompt="immutable generated prompt",
    )
    before_frozen = _stored_plan_row(frozen_session)
    partial = _preview(client, frozen_session, stages=[1, 2])
    assert partial.status_code == 200, partial.text
    refused = _apply(client, frozen_session, partial.json())
    assert refused.status_code == 409
    assert "continuity fields" in refused.text
    assert _stored_plan_row(frozen_session) == before_frozen
    assert db.one(
        "SELECT status, final_prompt, linked_shot_id FROM prepared_take "
        "WHERE session_id = ? AND take_id = ?",
        frozen_session, "take-001",
    ) == {
        "status": "generated",
        "final_prompt": "immutable generated prompt",
        "linked_shot_id": shot_id,
    }


def test_scoped_progression_after_generation_preserves_history_and_revokes_approval(client, seeded):
    session_id, _ = _seed_plan(client, seeded, name="generated scoped progression")
    shot_id = _plant_shot(session_id, prompt="kept generated shot")
    _plant_prepared_take(
        session_id, 1, "take-001", status="generated",
        linked_shot_id=shot_id, final_prompt="kept generated prompt",
    )
    db.run(
        "INSERT INTO session_plan_approval (session_id, plan_revision, approved_at) "
        "VALUES (?, 1, ?)",
        session_id, db.now(),
    )
    preview = _preview(client, session_id).json()
    applied = _apply(client, session_id, preview)
    assert applied.status_code == 200, applied.text
    assert db.one(
        "SELECT status, final_prompt, linked_shot_id FROM prepared_take "
        "WHERE session_id = ? AND take_id = ?",
        session_id, "take-001",
    ) == {
        "status": "generated",
        "final_prompt": "kept generated prompt",
        "linked_shot_id": shot_id,
    }
    assert db.one(
        "SELECT session_id FROM session_plan_approval WHERE session_id = ?",
        session_id,
    ) is None


def test_feature_flag_blocks_preview_and_apply_api_and_domain_calls(client, seeded, monkeypatch):
    session_id, _ = _seed_plan(client, seeded, name="disabled progression")
    preview_body = {
        "expected_revision": 1,
        "start_take_id": "take-001",
        "end_take_id": "take-006",
        "stage_indices": [0, 1, 2],
        "event_policy": "replace",
    }
    monkeypatch.setattr(main, "is_resource_planning_enabled", lambda: False)
    before = _stored_plan_row(session_id)
    preview = client.post(
        f"/api/sessions/{session_id}/plan/wardrobe-progression/preview",
        json=preview_body,
    )
    assert preview.status_code == 503
    apply = client.post(
        f"/api/sessions/{session_id}/plan/wardrobe-progression/apply",
        json={
            "expected_revision": 1,
            "preview_token": "invalid",
            "review_digest": "0" * 64,
            "reviewed_wardrobes": [{"take_id": "take-001", "wardrobe": FULL_WARDROBE}],
        },
    )
    assert apply.status_code == 503
    with pytest.raises(session_plan.ResourcePlanningDisabled):
        session_plan.preview_wardrobe_progression(
            session_id, 1, "take-001", "take-006", [0, 1, 2], "replace",
            planning_enabled=False,
        )
    with pytest.raises(session_plan.ResourcePlanningDisabled):
        session_plan.apply_wardrobe_progression_preview(
            session_id, 1, "invalid", "0" * 64,
            [{"take_id": "take-001", "wardrobe": FULL_WARDROBE}],
            planning_enabled=False,
        )
    assert _stored_plan_row(session_id) == before
