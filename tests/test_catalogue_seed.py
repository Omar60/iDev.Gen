"""Tests for data/catalogue-seed.json contents, judge labels, and faces."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
import pytest
import db


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "data" / "catalogue-seed.json"


def test_seed_file_exists_and_is_valid_json():
    """Task 3.1: data/catalogue-seed.json exists and parses as JSON array."""
    assert SEED_PATH.exists()
    items = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    assert isinstance(items, list)
    assert len(items) > 0


def test_the_seed_file_is_tracked_by_git():
    """The suite reads this file in `conftest.seeded`, and `data/` is otherwise
    ignored wholesale. Left untracked, every gate passes on the machine that
    generated it and six tests fail on a fresh clone — which is exactly how it
    shipped. The `.gitignore` un-ignores this one path; this asserts somebody
    also added it.
    """
    out = subprocess.run(["git", "ls-files", "--", "data/catalogue-seed.json"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git repository")
    assert out.stdout.strip() == "data/catalogue-seed.json", (
        "data/catalogue-seed.json is not tracked; run "
        "`git add data/catalogue-seed.json`"
    )


def test_seed_judge_labels_unique_and_not_equal_to_wording():
    """Task 3.2: no label equals its wording and no two labels in one slot are identical."""
    items = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    slot_labels: dict[str, set[str]] = {}

    for item in items:
        slot = item["slot"]
        wording = item["wording"]
        label = item.get("judge_label", "")

        assert label != "", f"missing judge_label on {item}"
        assert label != wording, f"judge_label equals wording on {item}"

        # Uniqueness within slot across distinct concept_keys
        # (the same concept_key across manners has the same judge_label)
        slot_labels.setdefault(slot, set())
        # Check that within a slot, different concept keys do not share identical judge labels
        # Let's map (slot, judge_label) -> concept_key
        slot_labels[slot].add((item["concept_key"], label))

    # For each slot, verify all distinct concept keys have distinct judge labels
    for slot, pairs in slot_labels.items():
        keys = [k for k, l in pairs]
        labels = [l for k, l in pairs]
        assert len(keys) == len(set(keys))
        assert len(labels) == len(set(labels)), f"duplicate labels in slot {slot}: {labels}"


def test_seed_camera_faces_rules():
    """Task 3.3: assert shoulder and behind families are 'back', mirror and pov families are empty,
    and no camera row is left unset (faces is one of 'front', 'side', 'back', '').
    """
    items = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    allowed_faces = {"front", "side", "back", ""}

    for item in items:
        assert "faces" in item, f"missing 'faces' on {item}"
        faces = item["faces"]
        assert faces in allowed_faces, f"invalid faces {faces!r} on {item}"

        if item["slot"] == "camera":
            family = item.get("family", "")
            if family in ("shoulder", "behind"):
                assert faces == "back", f"expected faces='back' for family {family} on {item}"
            elif family in ("mirror", "pov", "overhead"):
                assert faces == "", f"expected faces='' for family {family} on {item}"
            elif family in ("front", "floor"):
                assert faces == "front", f"expected faces='front' for family {family} on {item}"
            elif family == "side":
                assert faces == "side", f"expected faces='side' for family {family} on {item}"
