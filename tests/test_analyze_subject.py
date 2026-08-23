"""The one thing `analyze_subject.mjs` can get quietly wrong.

Its whole answer is a regex judgement about what the `her` field is written from,
and this wardrobe has a sweater with an OPEN BACK. `the open back of the knit` is
a garment, not a camera being answered, and counting it as one turns the number
it exists to report into noise in the direction that hides the problem.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

LINE = """Angle & Framing:
%(camera)s, a full-length photograph, head to feet.

Subject:
%(her)s

Expression:
Her mouth is closed."""

BEHIND = "Taken from directly behind her"

# name: (camera, `her` field, offFront, frontBody)
CASES = [
    # A back-view camera with a field that names only her front: the contradiction.
    ("front-only", BEHIND,
     "The sweater sits across her chest with the bra visible at the bust.", 1, 1),
    # The same camera, answered: the field says what a camera behind her sees.
    ("answered", BEHIND,
     "The open knit runs down her spine, her back bare to the waistband.", 1, 0),
    # The trap: `open back` is the garment, and her front is all that is described.
    ("garment-back", BEHIND,
     "The open back of the knit sits over her chest and bust.", 1, 1),
    # Not an off-front camera at all, so it is not counted either way.
    ("frontal-camera", "Taken from directly in front of her",
     "The sweater sits across her chest with the bra at the bust.", 0, 0),
]


@pytest.mark.parametrize("name,camera,her,off_front,front_body", CASES)
def test_the_open_back_of_a_sweater_is_not_a_camera_being_answered(
        tmp_path, name, camera, her, off_front, front_body):
    node = shutil.which("node")
    if not node:
        pytest.skip("needs node")
    run = tmp_path / f"{name}.json"
    # `measure_writer.mjs` logs its check tally ahead of the array and the
    # analyzer skips to it, so the fixture carries one too.
    # newline="": the analyzer finds the array by `\n[\n`, and Python would
    # otherwise write CRLF here and hand it a file it reads as empty.
    run.write_text("checks\n" + json.dumps([LINE % {"camera": camera, "her": her}], indent=1),
                   encoding="utf8", newline="")
    out = subprocess.run([node, str(ROOT / "scripts/analyze_subject.mjs"), str(run)],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    total = next(l for l in out.stdout.splitlines() if l.startswith("total")).split()
    assert (int(total[2]), int(total[3])) == (off_front, front_body), out.stdout
