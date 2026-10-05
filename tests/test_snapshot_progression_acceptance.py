from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import session_plan
from test_saved_look_application_acceptance import (
    _apply,
    _create_look,
    _session_with_authoring_plan,
)


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = {
    "outfit_key": "acceptance-outfit",
    "garments": [
        {"key": "coat", "wording": "a synthetic coat", "aside": ""},
        {"key": "trousers", "wording": "black trousers", "aside": ""},
    ],
}


def _node_wardrobe_results(payload: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("snapshot/backend parity requires Node.js")

    script = """
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const { deriveSavedLookWardrobeProgression, setWardrobe, wearing } = await import(
  pathToFileURL(resolve('frontend/src/wardrobe.js'))
);
const input = JSON.parse(readFileSync(0, 'utf8'));
setWardrobe({ garments: [], outfits: [] });
const boundary = input.boundary.map((wording) => {
  const outfit = { outfit_key: 'boundary', garments: [{ key: 'g', wording: wording.wording, aside: wording.aside }] };
  try {
    return { accepted: true, state: deriveSavedLookWardrobeProgression(
      outfit, '', 1, { stageIndices: [0] },
    )[0] };
  } catch {
    return { accepted: false };
  }
});
const internal = input.internal.map(({ wording, aside }) => {
  const outfit = { outfit_key: 'internal', garments: [{ key: 'g', wording, aside }] };
  return deriveSavedLookWardrobeProgression(outfit, '', 3, { stageIndices: [0, 1, 2] });
});
const snapshotBefore = JSON.stringify(input.snapshot);
const progressed = deriveSavedLookWardrobeProgression(input.snapshot, 'Current clothing.', 7, {
  stageIndices: [0, 1, 2], intervalStart: 1, intervalEnd: 4,
});
const defaulted = deriveSavedLookWardrobeProgression(null, 'Current clothing.', 2);
const singleTake = deriveSavedLookWardrobeProgression(input.snapshot, 'Current clothing.', 1, {
  stageIndices: [1], intervalStart: 0, intervalEnd: 0,
});
const canonical = [
  wearing([]),
  wearing(['a synthetic coat']),
  wearing(['a synthetic coat', 'black trousers']),
  wearing(['a synthetic coat', 'black trousers', 'a blue scarf']),
  deriveSavedLookWardrobeProgression(
    { outfit_key: 'one', garments: [{ key: 'only', wording: 'a synthetic coat', aside: '' }] },
    '', 1, { stageIndices: [1],
  })[0],
];
process.stdout.write(JSON.stringify({
  boundary, internal, canonical, progressed, defaulted, singleTake,
  snapshotUnchanged: JSON.stringify(input.snapshot) === snapshotBefore,
}));
"""
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        input=json.dumps(payload, ensure_ascii=True),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        cwd=ROOT,
    )
    return json.loads(proc.stdout)


def _node_trim_whitespace_codepoints() -> set[int]:
    node = shutil.which("node")
    if not node:
        pytest.skip("snapshot/backend parity requires Node.js")
    script = """
const codepoints = [];
for (let codepoint = 0; codepoint <= 0x10ffff; codepoint += 1) {
  if (String.fromCodePoint(codepoint).trim() === '') codepoints.push(codepoint);
}
process.stdout.write(JSON.stringify(codepoints));
"""
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        cwd=ROOT,
    )
    return set(json.loads(proc.stdout))


def test_backend_and_real_javascript_agree_on_unicode_wording_boundaries():
    # Python str.strip() and JavaScript String.trim() classify some Unicode
    # whitespace code points differently. The snapshot contract requires both
    # runtimes to accept or reject the same complete wording.
    python_whitespace = {
        codepoint for codepoint in range(0x110000)
        if chr(codepoint).isspace()
    }
    boundary_codepoints = sorted(
        python_whitespace | _node_trim_whitespace_codepoints()
    )
    boundary = []
    for codepoint in boundary_codepoints:
        character = chr(codepoint)
        for field, wording, aside in (
            ("wording-prefix", character + "coat", ""),
            ("wording-suffix", "coat" + character, ""),
            ("aside-prefix", "coat", character + "moved aside"),
            ("aside-suffix", "coat", "moved aside" + character),
        ):
            boundary.append({
                "label": f"U+{codepoint:04X} {field}",
                "wording": wording,
                "aside": aside,
            })
    internal = [
        {"wording": f"coat{character}lining", "aside": f"coat{character}moved aside"}
        for character in map(chr, (0xFEFF, 0x0085, *range(0x1C, 0x20)))
    ]
    payload = {"boundary": boundary, "internal": internal, "snapshot": SNAPSHOT}
    javascript_results = _node_wardrobe_results(payload)
    javascript = javascript_results["boundary"]
    javascript_internal = javascript_results["internal"]

    python = []
    for boundary_case in boundary:
        outfit = {
            "outfit_key": "boundary",
            "garments": [{
                "key": "g",
                "wording": boundary_case["wording"],
                "aside": boundary_case["aside"],
            }],
        }
        try:
            state = session_plan.derive_saved_look_wardrobe_progression(
                outfit, "", 1, stage_indices=[0],
            )[0]
            python.append({"accepted": True, "state": state})
        except session_plan.PlanValidationError:
            python.append({"accepted": False})

    javascript_accepted = [item["accepted"] for item in javascript]
    python_accepted = [item["accepted"] for item in python]
    assert javascript_accepted == python_accepted, (
        "backend/frontend snapshot wording acceptance diverged for "
        + ", ".join(
            case["label"]
            for case, js_result, py_result in zip(boundary, javascript, python)
            if js_result["accepted"] != py_result["accepted"]
        )
    )
    assert not any(python_accepted), (
        "Python accepted leading or trailing snapshot whitespace for "
        + ", ".join(
            case["label"]
            for case, result in zip(boundary, python)
            if result["accepted"]
        )
    )
    assert not any(javascript_accepted), (
        "JavaScript accepted leading or trailing snapshot whitespace for "
        + ", ".join(
            case["label"]
            for case, result in zip(boundary, javascript)
            if result["accepted"]
        )
    )

    for boundary_case, js_result, py_result in zip(boundary, javascript, python):
        if js_result["accepted"] and py_result["accepted"]:
            assert js_result["state"] == py_result["state"], boundary_case["label"]

    expected_internal = [
        [
            f"She wears {case['wording']}.",
            f"She wears {case['aside']}.",
            "She wears nothing at all.",
        ]
        for case in internal
    ]
    internal_outfits = [
        {
            "outfit_key": "internal",
            "garments": [{"key": f"g{index}", **case}],
        }
        for index, case in enumerate(internal)
    ]
    backend_internal = [
        session_plan.derive_saved_look_wardrobe_progression(
            outfit, "", 3, stage_indices=[0, 1, 2],
        )
        for outfit in internal_outfits
    ]
    assert javascript_internal == backend_internal == expected_internal


def test_backend_and_real_javascript_match_canonical_sentences_and_pure_preview():
    payload = {"boundary": [], "internal": [], "snapshot": SNAPSHOT}
    javascript = _node_wardrobe_results(payload)

    expected_canonical = [
        "She wears nothing at all.",
        "She wears a synthetic coat.",
        "She wears a synthetic coat, and black trousers.",
        "She wears a synthetic coat, black trousers, and a blue scarf.",
        "She wears nothing at all.",
    ]
    assert javascript["canonical"] == expected_canonical

    one_garment = {
        "outfit_key": "one",
        "garments": [{"key": "only", "wording": "a synthetic coat", "aside": ""}],
    }
    backend_canonical = [
        session_plan.derive_saved_look_wardrobe_progression(
            one_garment, "", 1, stage_indices=[1],
        )[0],
        session_plan.compose_saved_look_wardrobe(one_garment),
        session_plan.compose_saved_look_wardrobe(SNAPSHOT),
        session_plan.compose_saved_look_wardrobe({
            "outfit_key": "three",
            "garments": [
                *SNAPSHOT["garments"],
                {"key": "scarf", "wording": "a blue scarf", "aside": ""},
            ],
        }),
        session_plan.derive_saved_look_wardrobe_progression(
            one_garment, "", 1, stage_indices=[1],
        )[0],
    ]
    assert backend_canonical == expected_canonical

    expected_progression = [
        "Current clothing.",
        "She wears a synthetic coat, and black trousers.",
        "She wears a synthetic coat, and black trousers.",
        "She wears black trousers.",
        "She wears nothing at all.",
        "She wears nothing at all.",
        "She wears nothing at all.",
    ]
    snapshot_before = copy.deepcopy(SNAPSHOT)
    backend_progression = session_plan.derive_saved_look_wardrobe_progression(
        SNAPSHOT,
        "Current clothing.",
        7,
        stage_indices=[0, 1, 2],
        interval_start=1,
        interval_end=4,
    )
    assert SNAPSHOT == snapshot_before
    assert backend_progression == expected_progression
    assert javascript["progressed"] == expected_progression
    assert javascript["defaulted"] == ["Current clothing.", "Current clothing."]
    assert javascript["singleTake"] == ["She wears black trousers."]
    assert javascript["snapshotUnchanged"] is True

    assert session_plan.derive_saved_look_wardrobe_progression(
        SNAPSHOT,
        "Current clothing.",
        1,
        stage_indices=[1],
        interval_start=0,
        interval_end=0,
    ) == ["She wears black trousers."]


def test_live_saved_look_application_keeps_wardrobe_constant_without_progression(
    client, seeded,
):
    session_id, original = _session_with_authoring_plan(
        client, seeded, "snapshot progression default",
    )
    assert original["wardrobe_changes"] == []
    assert original["authoring"]["wardrobe_progression"] is None

    look = _create_look(
        client,
        "Default clothing look",
        "Soft makeup and short curls.",
        [
            {"wording": "a linen dress", "aside": ""},
            {"wording": "a blue overshirt", "aside": "unbuttoned"},
        ],
    )
    applied = _apply(client, session_id, look, revision=1)
    assert applied.status_code == 200, applied.text

    stored = client.get(f"/api/sessions/{session_id}/plan").json()
    plan = stored["plan"]
    assert stored["plan_revision"] == 2
    assert plan["initial_wardrobe"] == (
        "She wears a linen dress, and a blue overshirt."
    )
    assert plan["wardrobe_changes"] == []
    assert plan["authoring"]["wardrobe_progression"] is None
    effective = session_plan.resolve_effective_wardrobes(plan)
    assert effective
    assert set(effective.values()) == {plan["initial_wardrobe"]}
