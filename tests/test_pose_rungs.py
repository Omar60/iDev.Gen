"""The posture ladder and the sheet that reads it.

The failure this exists for is silent in the same way `test_candid_camera_arms`
is. The `posestep` set only means anything if its top rung is the Pose block
that beat the reference card WORD FOR WORD - one dropped clause and the arm
that is supposed to reproduce a known result is a new wording nobody measured,
and the run still prints a tidy table. So the rung is compared against the line
the bench actually renders, not against a copy of it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))

import shoot_depth_control as bench  # noqa: E402
import shoot_prompt_weight as probe  # noqa: E402


def pose_block(line: str) -> str:
    start = line.index("Pose:") + len("Pose:")
    return line[start:line.index("Subject:")].strip()


def test_top_rung_is_the_bench_pose_block_verbatim():
    assert probe.POSE_RUNGS[-1] == pose_block(bench.PROMPT)


def test_rungs_only_ever_add():
    for shorter, longer in zip(probe.POSE_RUNGS[1:], probe.POSE_RUNGS[2:]):
        assert longer.startswith(shorter.rstrip("."))


def test_every_arm_holds_the_card_at_one_strength():
    # The Pose block is the only thing allowed to move across the set.
    assert {arm[3] for arm in probe.SETS["posestep"]} == {3.0}
    assert [arm[4] for arm in probe.SETS["posestep"]] == probe.POSE_RUNGS


def test_pose_written_replaces_the_block_and_keeps_the_rest():
    out = probe.pose_written(bench.PROMPT, "She kneels.")
    assert pose_block(out) == "She kneels."
    assert out.index("Angle & Framing:") < out.index("Pose:") < out.index("Subject:")
    assert "Expression:" in out


def test_every_set_can_print_its_arms(monkeypatch):
    """The dry run is the only look at a line before it costs renders, and it
    crashed the moment an arm carried a function instead of a string. Each set
    resets PROMPT first: `main` strikes clauses out of the global in place, so
    one process running two sets strips a block that is already gone."""
    import asyncio

    original = probe.PROMPT
    for name in probe.SETS:
        probe.PROMPT = original
        monkeypatch.setattr(sys, "argv", ["probe", "--set", name, "--dry-run"])
        assert asyncio.run(probe.main()) == 0, name
    probe.PROMPT = original


def test_contact_sheet_gives_each_arm_a_row(tmp_path):
    for label in ("XX-0", "XX-1"):
        for seed in (11, 22, 33):
            Image.new("RGB", (40, 60), "red").save(tmp_path / f"{label}-s{seed}.png")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "contact_sheet.py"),
                        "XX-", "--dir", str(tmp_path), "--cell", "40"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    sheet = Image.open(tmp_path / "sheet-XX.png")
    assert sheet.width // 40 == 3 and sheet.height // 60 == 2
