"""The candid camera batch and the judge that reads it, checked against each other.

The failure this exists for is silent. `judge_camera.asked_of` answers `?` for a
clause it does not recognise and the run still prints a table, so an arm whose
wording drifted by one word is scored against `?` and can only ever be a miss -
a whole batch of renders read as a form that failed. The two files are edited
apart, so they are held together here.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import judge_camera  # noqa: E402
import shoot_candid_cameras as batch  # noqa: E402

# What each arm asks for. Written out here rather than derived, so a changed
# clause has to be re-decided by a person and not merely re-parsed.
EXPECTED = {
    "C1-front": ("front", "no"),
    "C2-overhead": ("overhead", "no"),
    "C3-floor": ("floor", "no"),
    "N1-shelf-high": ("overhead", "no"),
    "N2-hand-overhead": ("overhead", "no"),
    "N3-carpet-floor": ("floor", "no"),
    "N4-armslength-phone": ("front", "yes"),
    "N5-mirror": ("front", "yes"),
    "N6-armslength-bare": ("front", "no"),
}


def test_the_judge_recognises_every_arm_in_the_batch():
    for label, camera, _ in batch.ARMS:
        prompt = batch.prompt_for(camera)
        want, _device = EXPECTED[label]
        assert judge_camera.asked_of(prompt) == want, label


def test_the_device_is_expected_only_where_the_phone_is_in_her_hand():
    for label, camera, _ in batch.ARMS:
        low = " ".join(batch.prompt_for(camera).split()).lower()
        saw = "yes" if any(c in low for c in judge_camera.DEVICE_YES) else "no"
        assert saw == EXPECTED[label][1], label


def test_the_arms_differ_by_the_camera_clause_and_nothing_else():
    # The whole protocol is one line with one field swapped. A stray difference
    # anywhere else makes every arm a comparison of two things.
    rests = {batch.prompt_for(c).split("Pose:")[1] for _, c, _ in batch.ARMS}
    assert len(rests) == 1
    assert len({label for label, _, _ in batch.ARMS}) == len(batch.ARMS)
