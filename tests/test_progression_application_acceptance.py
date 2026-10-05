from __future__ import annotations

import copy
import json
import uuid

import db
import main
import session_plan
import pytest
from backend import authoring_operations
from test_session_plan import (
    _plant_prepared_take,
    _plant_shot,
    _task34_resource_session,
    _task41_make_plan,
    _task41_resource_revision,
    _task41_seed_authoring_plan,
    _task41_valid_authoring,
    _task42_takes,
)
from test_task9_8_saved_look_application import _apply, _create_look, _stored_plan_row
from test_authoring_operation_api import _create_guided_session
from test_authoring_operation_api import _persist_response_for_state_test, _start_body


GARMENTS = [
    {"wording": "a wool jacket", "aside": "the wool jacket, moved aside"},
    {"wording": "a pleated skirt", "aside": "the pleated skirt, moved aside"},
    {"wording": "black shoes", "aside": "the black shoes, moved aside"},
]
STAGE_INDICES = [0, 2, 4]
START_TAKE_ID = "take-002"
END_TAKE_ID = "take-005"


def _seed_progression_session(client, seeded, *, wardrobe_changes=None, look_decisions=None):
    resource = _task41_resource_revision()
    session_id = _task34_resource_session(client, seeded, "progression application acceptance")
    look_text = "Existing session appearance."
    wardrobe_text = "She wears an existing coat."
    authoring = _task41_valid_authoring(
        look_text=look_text,
        wardrobe_text=wardrobe_text,
        scene_anchor_triple=resource,
        origin_look="user",
        origin_wardrobe="user",
        snapshot=False,
        progression=False,
    )
    plan = _task41_make_plan(
        resource,
        auth=authoring,
        look=look_text,
        wardrobe=wardrobe_text,
        takes=_task42_takes(6),
    )
    if wardrobe_changes is not None:
        plan["wardrobe_changes"] = copy.deepcopy(wardrobe_changes)
    _task41_seed_authoring_plan(session_id, plan)

    look = _create_look(
        client,
        name="Ordered studio outfit",
        appearance="Soft studio makeup and short curls.",
        garments=GARMENTS,
    )
    applied = _apply(
        client,
        session_id,
        revision=1,
        look=look,
        decisions=look_decisions or {"look": "replace", "initial_wardrobe": "replace"},
    )
    assert applied.status_code == 200, applied.text
    return session_id, look


def _preview(
    client,
    session_id,
    revision,
    *,
    policy="replace",
    start_take_id=START_TAKE_ID,
    end_take_id=END_TAKE_ID,
    stage_indices=STAGE_INDICES,
):
    return client.post(
        f"/api/sessions/{session_id}/plan/wardrobe-progression/preview",
        json={
            "expected_revision": revision,
            "start_take_id": start_take_id,
            "end_take_id": end_take_id,
            "stage_indices": stage_indices,
            "event_policy": policy,
        },
    )


def _apply_preview(client, session_id, preview, *, revision=None, changes=None):
    body = {
        "expected_revision": preview["expected_revision"] if revision is None else revision,
        "preview_token": preview["preview_token"],
        "review_digest": preview["review_digest"],
        "reviewed_wardrobes": copy.deepcopy(preview["reviewed_wardrobes"]),
    }
    if changes is not None:
        body.update(changes)
    return client.post(
        f"/api/sessions/{session_id}/plan/wardrobe-progression/apply",
        json=body,
    )


def _plan_view(client, session_id):
    response = client.get(f"/api/sessions/{session_id}/plan")
    assert response.status_code == 200, response.text
    return response.json()


def _preview_request_fields(preview):
    assert preview["expected_revision"] == 2
    assert preview["event_policy"] in {"merge", "replace"}
    assert isinstance(preview["preview_token"], str) and preview["preview_token"]
    assert isinstance(preview["review_digest"], str) and len(preview["review_digest"]) == 64
    assert all(
        set(row) == {"take_id", "wardrobe"}
        for row in preview["reviewed_wardrobes"]
    )


def _full_outfit_wardrobe(look):
    assert [item["wording"] for item in look["outfit"]["garments"]] == [
        "a wool jacket", "a pleated skirt", "black shoes",
    ]
    return "She wears a wool jacket, a pleated skirt, and black shoes."


def _reviewed_rows(wardrobes):
    return [
        {"take_id": f"take-{index:03d}", "wardrobe": wardrobe}
        for index, wardrobe in enumerate(wardrobes, 1)
    ]


def _save_plan(client, session_id, plan_view):
    return client.post(
        f"/api/sessions/{session_id}/plan",
        json={
            "plan": plan_view["plan"],
            "expected_revision": plan_view["plan_revision"],
        },
    )


def _independent_effective_wardrobes(plan):
    inherited = plan["initial_wardrobe"]
    changes = {item["take_id"]: item for item in plan["wardrobe_changes"]}
    result = {}
    for take in plan["takes"]:
        event = changes.get(take["take_id"])
        if event is None:
            result[take["take_id"]] = inherited
        elif event["scope"] == "this_take":
            result[take["take_id"]] = event["wardrobe"]
        else:
            inherited = event["wardrobe"]
            result[take["take_id"]] = inherited
    return result


def test_reviewed_preview_applies_minimal_stable_events_in_one_cas_without_authoring_or_generation(
    client, seeded, monkeypatch,
):
    session_id, look = _seed_progression_session(client, seeded)
    before = _stored_plan_row(session_id)
    monkeypatch.setattr(
        main,
        "_schedule_shared_suggestion_operation",
        lambda *_args, **_kwargs: pytest.fail("progression must not schedule assistant work"),
    )
    full = _full_outfit_wardrobe(look)
    expected = [
        full,
        full,
        full,
        "She wears black shoes.",
        "She wears nothing at all.",
        "She wears nothing at all.",
    ]

    response = _preview(client, session_id, before["plan_revision"])
    assert response.status_code == 200, response.text
    preview = response.json()
    _preview_request_fields(preview)
    assert preview["initial_wardrobe"] == full
    assert preview["wardrobe_changes"] == [
        {"take_id": "take-004", "scope": "from_here", "wardrobe": expected[3]},
        {"take_id": "take-005", "scope": "from_here", "wardrobe": expected[4]},
    ]
    assert preview["reviewed_wardrobes"] == _reviewed_rows(expected)
    assert _stored_plan_row(session_id) == before

    applied = _apply_preview(client, session_id, preview)
    assert applied.status_code == 200, applied.text
    current = _plan_view(client, session_id)
    plan = current["plan"]
    revision = current["plan_revision"]
    assert revision == before["plan_revision"] + 1
    assert plan["initial_wardrobe"] == full
    assert plan["wardrobe_changes"] == preview["wardrobe_changes"]
    assert plan["authoring"]["wardrobe_progression"] == {
        "source_look_digest": look["content_digest"],
        "start_take_id": START_TAKE_ID,
        "end_take_id": END_TAKE_ID,
        "stage_indices": STAGE_INDICES,
        "applied_revision": revision,
    }
    assert len({item["take_id"] for item in plan["wardrobe_changes"]}) == len(
        plan["wardrobe_changes"]
    )
    assert db.one("SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ?", session_id)["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id)["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM authoring_operation WHERE session_id = ?", session_id)["n"] == 0


def test_merge_deduplicates_equal_transition_and_keeps_this_take_in_effective_review(
    client, seeded,
):
    session_id, look = _seed_progression_session(
        client,
        seeded,
        wardrobe_changes=[
            {
                "take_id": "take-003",
                "scope": "this_take",
                "wardrobe": "She wears a checked sweater.",
            },
            {
                "take_id": "take-004",
                "scope": "from_here",
                "wardrobe": "She wears black shoes.",
            },
        ],
    )
    full = _full_outfit_wardrobe(look)
    response = _preview(client, session_id, 2, policy="merge")
    assert response.status_code == 200, response.text
    preview = response.json()
    _preview_request_fields(preview)
    assert preview["event_policy"] == "merge"
    assert preview["reviewed_wardrobes"] == [
        {"take_id": "take-001", "wardrobe": full},
        {"take_id": "take-002", "wardrobe": full},
        {"take_id": "take-003", "wardrobe": "She wears a checked sweater."},
        {"take_id": "take-004", "wardrobe": "She wears black shoes."},
        {"take_id": "take-005", "wardrobe": "She wears nothing at all."},
        {"take_id": "take-006", "wardrobe": "She wears nothing at all."},
    ]

    applied = _apply_preview(client, session_id, preview)
    assert applied.status_code == 200, applied.text
    plan = _plan_view(client, session_id)["plan"]
    assert plan["wardrobe_changes"] == [
        {
            "take_id": "take-003",
            "scope": "this_take",
            "wardrobe": "She wears a checked sweater.",
        },
        {
            "take_id": "take-004",
            "scope": "from_here",
            "wardrobe": "She wears black shoes.",
        },
        {
            "take_id": "take-005",
            "scope": "from_here",
            "wardrobe": "She wears nothing at all.",
        },
    ]
    assert len({item["take_id"] for item in plan["wardrobe_changes"]}) == 3


def test_replace_removes_old_persistent_events_but_preserves_local_override(
    client, seeded,
):
    session_id, look = _seed_progression_session(
        client,
        seeded,
        wardrobe_changes=[
            {
                "take_id": "take-001",
                "scope": "from_here",
                "wardrobe": "She wears an older black dress.",
            },
            {
                "take_id": "take-003",
                "scope": "this_take",
                "wardrobe": "She wears a checked sweater.",
            },
        ],
    )
    full = _full_outfit_wardrobe(look)
    preview_response = _preview(client, session_id, 2, policy="replace")
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["reviewed_wardrobes"] == [
        {"take_id": "take-001", "wardrobe": full},
        {"take_id": "take-002", "wardrobe": full},
        {"take_id": "take-003", "wardrobe": "She wears a checked sweater."},
        {"take_id": "take-004", "wardrobe": "She wears black shoes."},
        {"take_id": "take-005", "wardrobe": "She wears nothing at all."},
        {"take_id": "take-006", "wardrobe": "She wears nothing at all."},
    ]

    applied = _apply_preview(client, session_id, preview)
    assert applied.status_code == 200, applied.text
    plan = _plan_view(client, session_id)["plan"]
    assert plan["wardrobe_changes"] == [
        {
            "take_id": "take-003",
            "scope": "this_take",
            "wardrobe": "She wears a checked sweater.",
        },
        {
            "take_id": "take-004",
            "scope": "from_here",
            "wardrobe": "She wears black shoes.",
        },
        {
            "take_id": "take-005",
            "scope": "from_here",
            "wardrobe": "She wears nothing at all.",
        },
    ]


def test_unrepresentable_final_this_take_collision_is_write_free_and_fewer_stages_resolve_it(
    client, seeded,
):
    session_id, look = _seed_progression_session(
        client,
        seeded,
        wardrobe_changes=[{
            "take_id": "take-006",
            "scope": "this_take",
            "wardrobe": "She wears a locally reviewed scarf.",
        }],
    )
    full = _full_outfit_wardrobe(look)
    before = _stored_plan_row(session_id)
    preview_response = _preview(
        client,
        session_id,
        2,
        start_take_id="take-002",
        end_take_id="take-006",
    )
    assert preview_response.status_code == 409, preview_response.text
    assert "final progression stage is covered" in preview_response.text
    assert _stored_plan_row(session_id) == before

    fewer_stages_response = _preview(
        client,
        session_id,
        2,
        start_take_id="take-002",
        end_take_id="take-006",
        stage_indices=[0],
    )
    assert fewer_stages_response.status_code == 200, fewer_stages_response.text
    preview = fewer_stages_response.json()
    assert preview["initial_wardrobe"] == full
    assert preview["wardrobe_changes"] == [{
        "take_id": "take-006",
        "scope": "this_take",
        "wardrobe": "She wears a locally reviewed scarf.",
    }]
    assert preview["reviewed_wardrobes"] == _reviewed_rows([
        full,
        full,
        full,
        full,
        full,
        "She wears a locally reviewed scarf.",
    ])

    applied = _apply_preview(client, session_id, preview)
    assert applied.status_code == 200, applied.text
    plan = _plan_view(client, session_id)["plan"]
    assert plan["initial_wardrobe"] == full
    assert plan["wardrobe_changes"] == preview["wardrobe_changes"]
    assert plan["authoring"]["wardrobe_progression"]["stage_indices"] == [0]


def test_tampered_or_stale_reviewed_preview_is_refused_without_extra_writes(client, seeded):
    session_id, _ = _seed_progression_session(client, seeded)
    preview_response = _preview(client, session_id, 2)
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    before_tamper = _stored_plan_row(session_id)

    tampered = copy.deepcopy(preview["reviewed_wardrobes"])
    tampered[0]["wardrobe"] = "Client-invented wardrobe."
    rejected_tamper = _apply_preview(
        client,
        session_id,
        preview,
        changes={"reviewed_wardrobes": tampered},
    )
    assert rejected_tamper.status_code == 409, rejected_tamper.text
    assert _stored_plan_row(session_id) == before_tamper

    tampered_token = _apply_preview(
        client,
        session_id,
        preview,
        changes={"preview_token": "forged-preview-token"},
    )
    assert tampered_token.status_code == 409, tampered_token.text
    assert _stored_plan_row(session_id) == before_tamper

    current = _plan_view(client, session_id)
    changed = copy.deepcopy(current["plan"])
    changed["authoring"]["brief"] = "A later session brief."
    saved = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": changed, "expected_revision": current["plan_revision"]},
    )
    assert saved.status_code == 200, saved.text
    after_user_edit = _stored_plan_row(session_id)
    rejected_stale = _apply_preview(client, session_id, preview)
    assert rejected_stale.status_code == 409, rejected_stale.text
    assert _stored_plan_row(session_id) == after_user_edit


def test_merge_collision_and_too_short_interval_are_write_free(client, seeded):
    session_id, _ = _seed_progression_session(
        client,
        seeded,
        wardrobe_changes=[
            {
                "take_id": "take-004",
                "scope": "from_here",
                "wardrobe": "She wears a different red coat.",
            },
        ],
    )
    before = _stored_plan_row(session_id)
    collision = _preview(client, session_id, 2, policy="merge")
    assert collision.status_code == 409, collision.text
    assert _stored_plan_row(session_id) == before

    too_short = client.post(
        f"/api/sessions/{session_id}/plan/wardrobe-progression/preview",
        json={
            "expected_revision": 2,
            "start_take_id": "take-002",
            "end_take_id": "take-003",
            "stage_indices": STAGE_INDICES,
            "event_policy": "replace",
        },
    )
    assert too_short.status_code == 422, too_short.text
    assert _stored_plan_row(session_id) == before


def test_closed_request_contract_and_cross_session_preview_authority_are_write_free(
    client, seeded,
):
    session_a, _ = _seed_progression_session(client, seeded)
    session_b, _ = _seed_progression_session(client, seeded)
    before_a = _stored_plan_row(session_a)
    before_b = _stored_plan_row(session_b)

    extra_preview_field = client.post(
        f"/api/sessions/{session_a}/plan/wardrobe-progression/preview",
        json={
            "expected_revision": 2,
            "start_take_id": START_TAKE_ID,
            "end_take_id": END_TAKE_ID,
            "stage_indices": STAGE_INDICES,
            "event_policy": "replace",
            "client_authority": True,
        },
    )
    assert extra_preview_field.status_code == 422, extra_preview_field.text

    bool_stage = client.post(
        f"/api/sessions/{session_a}/plan/wardrobe-progression/preview",
        json={
            "expected_revision": 2,
            "start_take_id": START_TAKE_ID,
            "end_take_id": END_TAKE_ID,
            "stage_indices": [False, 2, 4],
            "event_policy": "replace",
        },
    )
    assert bool_stage.status_code == 422, bool_stage.text

    preview_response = _preview(client, session_a, 2)
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    extra_apply_field = _apply_preview(
        client,
        session_a,
        preview,
        changes={"replacement_policy": "merge"},
    )
    assert extra_apply_field.status_code == 422, extra_apply_field.text

    wrong_session = _apply_preview(client, session_b, preview)
    assert wrong_session.status_code == 409, wrong_session.text
    assert _stored_plan_row(session_a) == before_a
    assert _stored_plan_row(session_b) == before_b


def test_saved_events_remain_stable_id_authority_after_reorder_add_and_remove(client, seeded):
    session_id, _ = _seed_progression_session(client, seeded)
    preview_response = _preview(client, session_id, 2)
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    applied = _apply_preview(client, session_id, preview)
    assert applied.status_code == 200, applied.text

    current = _plan_view(client, session_id)
    applied_record = copy.deepcopy(current["plan"]["authoring"]["wardrobe_progression"])
    original_events = copy.deepcopy(current["plan"]["wardrobe_changes"])
    assert original_events == [
        {"take_id": "take-004", "scope": "from_here", "wardrobe": "She wears black shoes."},
        {"take_id": "take-005", "scope": "from_here", "wardrobe": "She wears nothing at all."},
    ]

    reordered = copy.deepcopy(current)
    reordered["plan"]["takes"] = [
        reordered["plan"]["takes"][index]
        for index in (0, 4, 1, 3, 2, 5)
    ]
    saved_reorder = _save_plan(client, session_id, reordered)
    assert saved_reorder.status_code == 200, saved_reorder.text
    after_reorder = _plan_view(client, session_id)
    plan = after_reorder["plan"]
    assert plan["wardrobe_changes"] == original_events
    assert plan["authoring"]["wardrobe_progression"] == applied_record
    full = plan["initial_wardrobe"]
    assert _independent_effective_wardrobes(plan) == {
        "take-001": full,
        "take-005": "She wears nothing at all.",
        "take-002": "She wears nothing at all.",
        "take-004": "She wears black shoes.",
        "take-003": "She wears black shoes.",
        "take-006": "She wears black shoes.",
    }

    added = copy.deepcopy(after_reorder)
    added["plan"]["takes"].insert(
        2, {"take_id": "take-007", "camera": "50mm", "pose": "standing"},
    )
    saved_add = _save_plan(client, session_id, added)
    assert saved_add.status_code == 200, saved_add.text
    after_add = _plan_view(client, session_id)
    assert after_add["plan"]["authoring"]["wardrobe_progression"] == applied_record
    assert after_add["plan"]["wardrobe_changes"] == original_events
    assert _independent_effective_wardrobes(after_add["plan"])["take-007"] == "She wears nothing at all."

    removed = copy.deepcopy(after_add)
    removed["plan"]["takes"] = [
        take for take in removed["plan"]["takes"] if take["take_id"] != "take-005"
    ]
    removed["plan"]["wardrobe_changes"] = [
        event for event in removed["plan"]["wardrobe_changes"]
        if event["take_id"] != "take-005"
    ]
    saved_remove = _save_plan(client, session_id, removed)
    assert saved_remove.status_code == 200, saved_remove.text
    after_remove = _plan_view(client, session_id)["plan"]
    assert after_remove["authoring"]["wardrobe_progression"] == applied_record
    assert after_remove["wardrobe_changes"] == [original_events[0]]
    assert "take-005" not in {take["take_id"] for take in after_remove["takes"]}
    assert _independent_effective_wardrobes(after_remove) == {
        "take-001": full,
        "take-007": full,
        "take-002": full,
        "take-004": "She wears black shoes.",
        "take-003": "She wears black shoes.",
        "take-006": "She wears black shoes.",
    }


def test_progression_cas_invalidates_reviewed_work_and_cancels_old_authoring_owner(
    client, seeded, monkeypatch,
):
    session_id, revision = _create_guided_session(
        client,
        seeded,
        photo_count=6,
        look="Existing session appearance.",
        initial_wardrobe="She wears an existing coat.",
    )
    look = _create_look(
        client,
        name="Operation-bound outfit",
        appearance="Soft studio makeup and short curls.",
        garments=GARMENTS,
    )
    applied_look = _apply(
        client,
        session_id,
        revision=revision,
        look=look,
        decisions={"look": "replace", "initial_wardrobe": "replace"},
    )
    assert applied_look.status_code == 200, applied_look.text
    revision += 1
    monkeypatch.setattr(main.enhance, "configured", lambda _config: True)
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", lambda _view: None)
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(
            revision=revision,
            take_ids=["take-001", "take-002", "take-003"],
        ),
    )
    assert started.status_code == 202, started.text
    preparation_claim = authoring_operations.load_worker_claim(
        session_id, started.json()["operation_id"],
    )
    prepared = None
    for index in range(1, 4):
        preparation_ticket = authoring_operations.renew_operation_lease(preparation_claim)
        prepared = _persist_response_for_state_test(
            preparation_claim,
            preparation_ticket,
            [{
                "target": f"take-{index:03d}",
                "result": {
                    "camera": f"invented camera {index}",
                    "framing": f"invented framing {index}",
                    "pose": f"invented pose {index}",
                    "expression": f"invented expression {index}",
                },
            }],
        )
    assert prepared["state"] == "succeeded"
    verified_sources = {
        f"take-{index:03d}": db.one(
            "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
            session_id, revision, f"take-{index:03d}",
        )
        for index in range(1, 4)
    }
    for take_id, row in verified_sources.items():
        assert row["status"] == "ready"
        main.resource_preparation.validate_authoring_prepared_evidence(
            session_id, revision, take_id,
        )

    # Keep an active owner for a different take so this CAS also proves that
    # stale in-flight authoring is fenced and cancelled.
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=revision, take_ids=["take-006"]),
    )
    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]
    operation_before = db.one(
        "SELECT state, fencing_token FROM authoring_operation WHERE operation_id = ?",
        operation_id,
    )
    assert operation_before["state"] == "active"

    prepared_ids = {
        **{take_id: row["id"] for take_id, row in verified_sources.items()},
        **{
            f"take-{index:03d}": _plant_prepared_take(
                session_id, revision, f"take-{index:03d}", status="ready",
            )
            for index in range(4, 7)
        },
    }
    rows_before = {
        take_id: db.one("SELECT * FROM prepared_take WHERE id = ?", row_id)
        for take_id, row_id in prepared_ids.items()
    }
    db.run(
        "INSERT INTO session_plan_approval (session_id, plan_revision, approved_at) VALUES (?, ?, ?)",
        session_id,
        revision,
        db.now(),
    )
    preview_response = _preview(client, session_id, revision)
    assert preview_response.status_code == 200, preview_response.text
    applied = _apply_preview(client, session_id, preview_response.json())
    assert applied.status_code == 200, applied.text

    current = _plan_view(client, session_id)
    assert current["plan_revision"] == revision + 1
    rows_after = {
        take_id: db.one("SELECT * FROM prepared_take WHERE id = ?", row_id)
        for take_id, row_id in prepared_ids.items()
    }
    assert {take_id for take_id, row in rows_after.items() if row["status"] == "invalidated"} == {
        "take-004", "take-005", "take-006",
    }
    for take_id in ("take-001", "take-002", "take-003"):
        assert rows_after[take_id] == rows_before[take_id]
        copied = db.one(
            "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
            session_id, revision + 1, take_id,
        )
        assert copied is not None
        assert copied["status"] == "ready"
        assert copied["final_prompt"] == rows_before[take_id]["final_prompt"]
        assert json.loads(copied["provenance"])["copy_forward"]["source_prepared_id"] == rows_before[take_id]["id"]
        assert json.loads(copied["provenance"])["authoring_evidence"] == json.loads(
            rows_before[take_id]["provenance"]
        )["authoring_evidence"]
    for take_id in ("take-004", "take-005", "take-006"):
        assert rows_after[take_id] == {
            **rows_before[take_id],
            "status": "invalidated",
            "updated_at": rows_after[take_id]["updated_at"],
        }
    assert session_plan.get_approved_plan_revision(session_id) is None
    operation_after = db.one(
        "SELECT state, fencing_token, error FROM authoring_operation WHERE operation_id = ?",
        operation_id,
    )
    assert operation_after["state"] == "cancelled"
    assert operation_after["fencing_token"] > operation_before["fencing_token"]
    assert "plan changed" in operation_after["error"]


def test_generated_continuity_freeze_and_feature_flag_gate_leave_plan_history_unchanged(
    client, seeded, monkeypatch,
):
    session_id, look = _seed_progression_session(
        client,
        seeded,
        look_decisions={"look": "replace", "initial_wardrobe": "keep"},
    )
    current = _plan_view(client, session_id)
    changed = copy.deepcopy(current["plan"])
    changed["initial_wardrobe"] = "She wears a user-kept jacket."
    saved = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": changed, "expected_revision": current["plan_revision"]},
    )
    assert saved.status_code == 200, saved.text
    revision = saved.json()["plan_revision"]
    shot_id = _plant_shot(session_id, "A linked generated prompt.")
    prepared_id = _plant_prepared_take(
        session_id,
        revision,
        "take-001",
        status="generated",
        linked_shot_id=shot_id,
        final_prompt="A linked generated prompt.",
    )
    before = _stored_plan_row(session_id)
    prepared_before = db.one("SELECT * FROM prepared_take WHERE id = ?", prepared_id)
    shot_before = db.one("SELECT * FROM shot WHERE id = ?", shot_id)

    preview_response = _preview(client, session_id, revision)
    assert preview_response.status_code == 200, preview_response.text
    event_only = _apply_preview(client, session_id, preview_response.json())
    assert event_only.status_code == 200, event_only.text
    after_event_only = _stored_plan_row(session_id)
    assert after_event_only["plan_revision"] == revision + 1
    event_only_plan = _plan_view(client, session_id)["plan"]
    assert event_only_plan["initial_wardrobe"] == "She wears a user-kept jacket."
    assert event_only_plan["wardrobe_changes"] == [
        {"take_id": "take-002", "scope": "from_here", "wardrobe": _full_outfit_wardrobe(look)},
        {"take_id": "take-004", "scope": "from_here", "wardrobe": "She wears black shoes."},
        {"take_id": "take-005", "scope": "from_here", "wardrobe": "She wears nothing at all."},
    ]
    assert db.one("SELECT * FROM prepared_take WHERE id = ?", prepared_id) == prepared_before
    assert db.one("SELECT * FROM shot WHERE id = ?", shot_id) == shot_before
    assert event_only_plan["authoring"]["look_snapshot"]["content_digest"] == look["content_digest"]

    frozen_preview = _preview(
        client,
        session_id,
        revision + 1,
        start_take_id="take-001",
        end_take_id="take-005",
    )
    assert frozen_preview.status_code == 200, frozen_preview.text
    before_frozen_attempt = _stored_plan_row(session_id)
    frozen = _apply_preview(client, session_id, frozen_preview.json())
    assert frozen.status_code == 409, frozen.text
    assert _stored_plan_row(session_id) == before_frozen_attempt
    assert db.one("SELECT * FROM prepared_take WHERE id = ?", prepared_id) == prepared_before
    assert db.one("SELECT * FROM shot WHERE id = ?", shot_id) == shot_before
    assert _plan_view(client, session_id)["plan"] == event_only_plan

    monkeypatch.setattr(main, "is_resource_planning_enabled", lambda: False)
    disabled_preview = _preview(client, session_id, revision + 1)
    assert disabled_preview.status_code == 503, disabled_preview.text
    disabled_apply = _apply_preview(client, session_id, frozen_preview.json())
    assert disabled_apply.status_code == 503, disabled_apply.text
    with pytest.raises(session_plan.ResourcePlanningDisabled):
        session_plan.preview_wardrobe_progression(
            session_id,
            revision + 1,
            "take-001",
            "take-005",
            STAGE_INDICES,
            "replace",
            planning_enabled=False,
        )
    with pytest.raises(session_plan.ResourcePlanningDisabled):
        session_plan.apply_wardrobe_progression_preview(
            session_id,
            revision + 1,
            "invalid-preview-token",
            "0" * 64,
            [{"take_id": "take-001", "wardrobe": "invented"}],
            planning_enabled=False,
        )
    assert _stored_plan_row(session_id) == before_frozen_attempt
    assert db.one("SELECT * FROM prepared_take WHERE id = ?", prepared_id) == prepared_before
    assert db.one("SELECT * FROM shot WHERE id = ?", shot_id) == shot_before
