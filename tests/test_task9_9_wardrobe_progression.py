from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import session_plan


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = {
    "outfit_key": "snapshot-outfit",
    "garments": [
        {"key": "jacket", "wording": "A wool jacket (buttoned!)", "aside": "A wool jacket, unbuttoned"},
        {"key": "skirt", "wording": "a Pleated skirt; navy", "aside": "Folded aside"},
        {"key": "shoes", "wording": "shoes", "aside": "Shoes, moved aside"},
    ],
}
SNAPSHOT_STAGES = [
    "She wears A wool jacket (buttoned!), a Pleated skirt; navy, and shoes.",
    "She wears a Pleated skirt; navy, and shoes.",
    "She wears shoes.",
    "She wears Shoes, moved aside.",
    "She wears nothing at all.",
]
NODE_PROGRESSION_SCRIPT = """
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const { deriveSavedLookWardrobeProgression } = await import(
  pathToFileURL(resolve('frontend/src/wardrobe.js'))
);
const cases = JSON.parse(readFileSync(0, 'utf8'));
const result = cases.map(({ outfit, initialWardrobe, takeCount, options }) => {
  try {
    return {
      accepted: true,
      output: deriveSavedLookWardrobeProgression(outfit, initialWardrobe, takeCount, options),
    };
  } catch (error) {
    return { accepted: false, error: error.name };
  }
});
process.stdout.write(JSON.stringify(result));
"""


def _node_progression(node, cases):
    proc = subprocess.run(
        [node, "--input-type=module", "-e", NODE_PROGRESSION_SCRIPT],
        input=json.dumps(cases, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        check=True,
    )
    return json.loads(proc.stdout)


def test_snapshot_arc_preserves_wording_and_only_adds_final_garment_aside():
    stages = session_plan.derive_saved_look_wardrobe_progression(
        SNAPSHOT,
        "Current clothing.",
        len(SNAPSHOT_STAGES),
        stage_indices=list(range(len(SNAPSHOT_STAGES))),
    )

    assert stages == SNAPSHOT_STAGES
    assert stages[0] == session_plan.compose_saved_look_wardrobe(SNAPSHOT)


def test_selected_stages_use_endpoint_preserving_distribution_and_carry():
    assert session_plan.derive_saved_look_wardrobe_progression(
        SNAPSHOT,
        "Current clothing.",
        7,
        stage_indices=[0, 2, 4],
        interval_start=1,
        interval_end=5,
    ) == [
        "Current clothing.",
        SNAPSHOT_STAGES[0],
        SNAPSHOT_STAGES[0],
        SNAPSHOT_STAGES[2],
        SNAPSHOT_STAGES[2],
        SNAPSHOT_STAGES[4],
        SNAPSHOT_STAGES[4],
    ]


def test_one_stage_is_constant_and_unrequested_or_absent_outfit_keeps_current_wardrobe():
    assert session_plan.derive_saved_look_wardrobe_progression(
        SNAPSHOT,
        "Current clothing.",
        4,
        stage_indices=[3],
        interval_start=1,
        interval_end=2,
    ) == ["Current clothing.", SNAPSHOT_STAGES[3], SNAPSHOT_STAGES[3], SNAPSHOT_STAGES[3]]
    assert session_plan.derive_saved_look_wardrobe_progression(None, "", 3) == ["", "", ""]


def test_internal_boundary_codepoints_remain_unchanged():
    wording = "A\ufeff\u0085\u001c shirt"
    aside = "A\u001f jacket, moved aside"
    outfit = {
        "outfit_key": "internal-bytes",
        "garments": [{"key": "garment", "wording": wording, "aside": aside}],
    }

    assert session_plan.derive_saved_look_wardrobe_progression(
        outfit, "", 3, stage_indices=[0, 1, 2],
    ) == [f"She wears {wording}.", f"She wears {aside}.", "She wears nothing at all."]


@pytest.mark.parametrize(
    "outfit",
    [
        {"garments": [{"key": "jacket", "wording": "a jacket", "aside": ""}]},
        {"outfit_key": "", "garments": [{"key": "jacket", "wording": "a jacket", "aside": ""}]},
        {"outfit_key": "bad", "garments": []},
        {"outfit_key": "bad", "garments": [{"key": "jacket", "wording": "a jacket"}]},
        {"outfit_key": "bad", "garments": [{"key": "jacket", "wording": " a jacket", "aside": ""}]},
        {"outfit_key": "bad", "garments": [{"key": "jacket", "wording": "a jacket", "aside": " open"}]},
        {
            "outfit_key": "bad",
            "garments": [
                {"key": "jacket", "wording": "a jacket", "aside": ""},
                {"key": "jacket", "wording": "a shirt", "aside": ""},
            ],
        },
        {
            "outfit_key": "bad",
            "garments": [{"key": "jacket", "wording": "a jacket", "aside": "", "covers": True}],
        },
    ],
)
def test_explicit_progression_rejects_incomplete_or_inconsistent_snapshot(outfit):
    with pytest.raises(session_plan.PlanValidationError):
        session_plan.derive_saved_look_wardrobe_progression(
            outfit,
            "",
            1,
            stage_indices=[0],
        )


@pytest.mark.parametrize(
    "stage_indices, take_count, interval_start, interval_end",
    [
        ([], 1, 0, 0),
        ([0, 0], 2, 0, 1),
        ([2, 1], 2, 0, 1),
        ([99], 1, 0, 0),
        ([True], 1, 0, 0),
        ([0, 1, 2], 2, 0, 1),
        ([0], 2, 1, 2),
    ],
)
def test_progression_rejects_bad_stages_or_interval(stage_indices, take_count, interval_start, interval_end):
    with pytest.raises(session_plan.PlanValidationError):
        session_plan.derive_saved_look_wardrobe_progression(
            SNAPSHOT,
            "",
            take_count,
            stage_indices=stage_indices,
            interval_start=interval_start,
            interval_end=interval_end,
        )


def test_node_and_backend_progression_outputs_match_byte_for_byte():
    node = shutil.which("node")
    if not node:
        pytest.skip("needs node")

    cases = [
        {
            "outfit": SNAPSHOT,
            "initialWardrobe": "Current clothing.",
            "takeCount": 5,
            "options": {"stageIndices": [0, 1, 2, 3, 4]},
        },
        {
            "outfit": SNAPSHOT,
            "initialWardrobe": "Current clothing.",
            "takeCount": 7,
            "options": {"stageIndices": [0, 2, 4], "intervalStart": 1, "intervalEnd": 5},
        },
        {
            "outfit": None,
            "initialWardrobe": "",
            "takeCount": 3,
            "options": {},
        },
        {
            "outfit": {
                "outfit_key": "internal-bytes",
                "garments": [{
                    "key": "garment",
                    "wording": "A\ufeff\u0085\u001c shirt",
                    "aside": "A\u001f jacket, moved aside",
                }],
            },
            "initialWardrobe": "",
            "takeCount": 3,
            "options": {"stageIndices": [0, 1, 2]},
        },
    ]
    node_results = _node_progression(node, cases)
    expected = []
    for case in cases:
        output = session_plan.derive_saved_look_wardrobe_progression(
            case["outfit"],
            case["initialWardrobe"],
            case["takeCount"],
            stage_indices=case["options"].get("stageIndices"),
            interval_start=case["options"].get("intervalStart", 0),
            interval_end=case["options"].get("intervalEnd"),
        )
        expected.append({"accepted": True, "output": output})
    assert node_results == expected


def test_node_and_backend_reject_the_union_of_boundary_whitespace():
    node = shutil.which("node")
    if not node:
        pytest.skip("needs node")

    boundary_characters = ("\ufeff", "\u0085", "\u001c", "\u001d", "\u001e", "\u001f")
    cases = []
    for boundary in boundary_characters:
        for side in ("leading", "trailing"):
            for field in ("wording", "aside"):
                bad_value = f"{boundary}garment wording" if side == "leading" else f"garment wording{boundary}"
                garment = {"key": "garment", "wording": "a coat", "aside": ""}
                garment[field] = bad_value
                outfit = {"outfit_key": "boundary-check", "garments": [garment]}
                cases.append({
                    "outfit": outfit,
                    "initialWardrobe": "",
                    "takeCount": 1,
                    "options": {"stageIndices": [0]},
                })
                with pytest.raises(session_plan.PlanValidationError):
                    session_plan.derive_saved_look_wardrobe_progression(
                        outfit, "", 1, stage_indices=[0],
                    )

    assert _node_progression(node, cases) == [
        {"accepted": False, "error": "TypeError"} for _ in cases
    ]


def test_legacy_fully_worn_composer_keeps_its_existing_boundary_validation():
    assert session_plan.compose_saved_look_wardrobe({
        "garments": [{"wording": "\ufeffa coat"}],
    }) == "She wears \ufeffa coat."
