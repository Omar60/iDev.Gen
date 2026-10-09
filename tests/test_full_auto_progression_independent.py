from __future__ import annotations

import copy

import pytest

import resource_store
import session_plan
from test_session_plan import _task41_make_plan, _task41_valid_authoring, _task42_takes


ROOM = {
    "library_key": "invented-rooms",
    "source_id": "invented-studio",
    "content_digest": "a" * 64,
}
APPEARANCE = "Soft studio makeup and short curls."
GARMENTS = [
    {"key": "jacket", "wording": "a wool jacket", "aside": "the wool jacket, moved aside"},
    {"key": "skirt", "wording": "a pleated skirt", "aside": "the pleated skirt, moved aside"},
    {"key": "shoes", "wording": "black shoes", "aside": "the black shoes, moved aside"},
]
ALL_STAGES = [
    "She wears a wool jacket, a pleated skirt, and black shoes.",
    "She wears a pleated skirt, and black shoes.",
    "She wears black shoes.",
    "She wears the black shoes, moved aside.",
    "She wears nothing at all.",
]


def _plan(take_count: int) -> dict:
    outfit = {"outfit_key": "outfit-01", "garments": copy.deepcopy(GARMENTS)}
    wardrobe = session_plan.compose_saved_look_wardrobe(outfit)
    authoring = _task41_valid_authoring(
        look_text=APPEARANCE,
        wardrobe_text=wardrobe,
        garments=copy.deepcopy(GARMENTS),
        snapshot=True,
        progression=False,
        scene_anchor_triple=ROOM,
        origin_look="saved_look",
        origin_wardrobe="saved_look",
    )
    return _task41_make_plan(
        ROOM,
        auth=authoring,
        look=APPEARANCE,
        wardrobe=wardrobe,
        takes=_task42_takes(take_count),
    )


@pytest.mark.parametrize(
    "take_count,expected_states,expected_event_ids",
    [
        (5, ALL_STAGES, ["take-002", "take-003", "take-004", "take-005"]),
        (
            8,
            [ALL_STAGES[index] for index in (0, 0, 1, 1, 2, 2, 3, 4)],
            ["take-003", "take-005", "take-007", "take-008"],
        ),
    ],
)
def test_all_stage_candidate_uses_full_interval_and_minimal_stable_id_events(
    take_count, expected_states, expected_event_ids,
):
    plan = _plan(take_count)
    before = copy.deepcopy(plan)

    candidate = session_plan.build_full_auto_wardrobe_progression_candidate(plan)

    assert plan == before
    assert list(session_plan.resolve_effective_wardrobes(candidate).values()) == expected_states
    assert [event["take_id"] for event in candidate["wardrobe_changes"]] == expected_event_ids
    progression = candidate["authoring"]["wardrobe_progression"]
    assert progression["start_take_id"] == "take-001"
    assert progression["end_take_id"] == f"take-{take_count:03d}"
    assert progression["stage_indices"] == list(range(len(ALL_STAGES)))
    assert progression["applied_revision"] == 1
    assert candidate["wardrobe_changes"][-1]["wardrobe"] == ALL_STAGES[-1]


def test_single_stage_distribution_and_appearance_only_full_auto_are_empty():
    one_garment = {
        "outfit_key": "single-stage",
        "garments": [{"key": "coat", "wording": "a synthetic coat", "aside": ""}],
    }
    assert session_plan.derive_saved_look_wardrobe_progression(
        one_garment, "", 1, stage_indices=[0],
    ) == ["She wears a synthetic coat."]

    authoring = _task41_valid_authoring(
        look_text=APPEARANCE,
        wardrobe_text="",
        snapshot=False,
        progression=False,
        scene_anchor_triple=ROOM,
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
    candidate = session_plan.build_full_auto_wardrobe_progression_candidate(
        _task41_make_plan(
            ROOM, auth=authoring, look=APPEARANCE, wardrobe="", takes=_task42_takes(1),
        )
    )

    assert candidate["initial_wardrobe"] == ""
    assert candidate["wardrobe_changes"] == []
    assert candidate["authoring"]["wardrobe_progression"] is None
    assert list(session_plan.resolve_effective_wardrobes(candidate).values()) == [""]


def test_full_auto_rejects_n_less_than_k_without_changing_the_plan():
    plan = _plan(4)
    before = copy.deepcopy(plan)

    with pytest.raises(session_plan.PlanValidationError, match="at least 5 takes"):
        session_plan.build_full_auto_wardrobe_progression_candidate(plan)

    assert plan == before
