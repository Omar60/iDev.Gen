from __future__ import annotations

import copy

import pytest

import resource_store
import session_plan
from test_session_plan import _task41_make_plan, _task41_valid_authoring, _task42_takes


APPEARANCE = "Soft studio makeup and short curls."
SCENE_ANCHOR = {
    "library_key": "invented-rooms",
    "source_id": "invented-studio",
    "content_digest": "a" * 64,
}
GARMENTS = [
    {"key": "jacket", "wording": "a wool jacket", "aside": "the wool jacket, moved aside"},
    {"key": "skirt", "wording": "a pleated skirt", "aside": "the pleated skirt, moved aside"},
    {"key": "shoes", "wording": "black shoes", "aside": "the black shoes, moved aside"},
]


def _plan(take_count: int, *, outfit: bool = True) -> dict:
    wardrobe = ""
    if outfit:
        snapshot_outfit = {"outfit_key": "outfit-01", "garments": copy.deepcopy(GARMENTS)}
        wardrobe = session_plan.compose_saved_look_wardrobe(snapshot_outfit)
        authoring = _task41_valid_authoring(
            look_text=APPEARANCE,
            wardrobe_text=wardrobe,
            garments=copy.deepcopy(GARMENTS),
            snapshot=True,
            progression=False,
            scene_anchor_triple=SCENE_ANCHOR,
            origin_look="saved_look",
            origin_wardrobe="saved_look",
        )
    else:
        authoring = _task41_valid_authoring(
            look_text=APPEARANCE,
            wardrobe_text="",
            snapshot=False,
            progression=False,
            scene_anchor_triple=SCENE_ANCHOR,
            origin_look="saved_look",
            origin_wardrobe="user",
        )
        authoring["look_snapshot"] = {
            "look_id": "look-01",
            "version": 1,
            "content_digest": resource_store.canonical_digest({
                "appearance": APPEARANCE,
                "outfit": None,
            }),
            "appearance": APPEARANCE,
            "outfit": None,
        }

    return _task41_make_plan(
        SCENE_ANCHOR,
        auth=authoring,
        look=APPEARANCE,
        wardrobe=wardrobe,
        takes=_task42_takes(take_count),
    )


def test_full_auto_materializes_all_five_stages_across_twelve_takes_with_minimal_events():
    plan = _plan(12)
    before = copy.deepcopy(plan)

    candidate = session_plan.build_full_auto_wardrobe_progression_candidate(plan)

    assert plan == before
    assert candidate["authoring"]["wardrobe_progression"] == {
        "source_look_digest": plan["authoring"]["look_snapshot"]["content_digest"],
        "start_take_id": "take-001",
        "end_take_id": "take-012",
        "stage_indices": [0, 1, 2, 3, 4],
        "applied_revision": 1,
    }
    assert candidate["initial_wardrobe"] == (
        "She wears a wool jacket, a pleated skirt, and black shoes."
    )
    assert candidate["wardrobe_changes"] == [
        {"take_id": "take-004", "scope": "from_here", "wardrobe": "She wears a pleated skirt, and black shoes."},
        {"take_id": "take-007", "scope": "from_here", "wardrobe": "She wears black shoes."},
        {"take_id": "take-010", "scope": "from_here", "wardrobe": "She wears the black shoes, moved aside."},
        {"take_id": "take-012", "scope": "from_here", "wardrobe": "She wears nothing at all."},
    ]
    assert list(session_plan.resolve_effective_wardrobes(candidate).values()) == [
        "She wears a wool jacket, a pleated skirt, and black shoes.",
        "She wears a wool jacket, a pleated skirt, and black shoes.",
        "She wears a wool jacket, a pleated skirt, and black shoes.",
        "She wears a pleated skirt, and black shoes.",
        "She wears a pleated skirt, and black shoes.",
        "She wears a pleated skirt, and black shoes.",
        "She wears black shoes.",
        "She wears black shoes.",
        "She wears black shoes.",
        "She wears the black shoes, moved aside.",
        "She wears the black shoes, moved aside.",
        "She wears nothing at all.",
    ]


def test_full_auto_includes_each_stage_once_when_take_count_matches_stage_count():
    candidate = session_plan.build_full_auto_wardrobe_progression_candidate(_plan(5))

    assert list(session_plan.resolve_effective_wardrobes(candidate).values()) == [
        "She wears a wool jacket, a pleated skirt, and black shoes.",
        "She wears a pleated skirt, and black shoes.",
        "She wears black shoes.",
        "She wears the black shoes, moved aside.",
        "She wears nothing at all.",
    ]
    assert [change["take_id"] for change in candidate["wardrobe_changes"]] == [
        "take-002", "take-003", "take-004", "take-005",
    ]


def test_single_selected_stage_supports_one_take_and_appearance_only_has_no_wardrobe_stage():
    one_garment = {
        "outfit_key": "one",
        "garments": [{"key": "coat", "wording": "a synthetic coat", "aside": ""}],
    }
    assert session_plan.derive_saved_look_wardrobe_progression(
        one_garment, "", 1, stage_indices=[0],
    ) == ["She wears a synthetic coat."]

    candidate = session_plan.build_full_auto_wardrobe_progression_candidate(
        _plan(1, outfit=False),
    )

    assert candidate["initial_wardrobe"] == ""
    assert candidate["wardrobe_changes"] == []
    assert candidate["authoring"]["wardrobe_progression"] is None
    assert list(session_plan.resolve_effective_wardrobes(candidate).values()) == [""]


def test_full_auto_rejects_too_few_takes_and_existing_events_without_mutating_input():
    short_plan = _plan(4)
    before_short = copy.deepcopy(short_plan)
    with pytest.raises(session_plan.PlanValidationError, match="at least 5 takes"):
        session_plan.build_full_auto_wardrobe_progression_candidate(short_plan)
    assert short_plan == before_short

    competing_plan = _plan(5)
    competing_plan["wardrobe_changes"] = [{
        "take_id": "take-002",
        "scope": "from_here",
        "wardrobe": "She wears a reviewed alternate coat.",
    }]
    before_competing = copy.deepcopy(competing_plan)
    with pytest.raises(session_plan.PlanOwnershipConflict, match="cannot replace existing wardrobe changes"):
        session_plan.build_full_auto_wardrobe_progression_candidate(competing_plan)
    assert competing_plan == before_competing


def test_materialized_progression_is_reused_after_take_reorder_add_and_remove():
    materialized = session_plan.build_full_auto_wardrobe_progression_candidate(_plan(12))
    assert session_plan.build_full_auto_wardrobe_progression_candidate(materialized) == materialized

    continued = copy.deepcopy(materialized)
    continued["takes"] = list(reversed(continued["takes"]))
    continued["takes"].insert(3, {"take_id": "take-013", "camera": "35mm", "pose": "seated"})
    continued["takes"] = [take for take in continued["takes"] if take["take_id"] != "take-012"]
    continued["wardrobe_changes"] = [
        change for change in continued["wardrobe_changes"] if change["take_id"] != "take-012"
    ]
    before_replay = copy.deepcopy(continued)

    replayed = session_plan.build_full_auto_wardrobe_progression_candidate(continued)

    assert continued == before_replay
    assert replayed["takes"] == before_replay["takes"]
    assert replayed["initial_wardrobe"] == materialized["initial_wardrobe"]
    assert replayed["wardrobe_changes"] == before_replay["wardrobe_changes"]
    assert replayed["authoring"]["wardrobe_progression"] == materialized["authoring"]["wardrobe_progression"]
