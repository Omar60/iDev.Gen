"""The camera plan, which decides where the camera stands before the writer is
asked anything.

Why it exists at all: measured 2026-08-22, three arms of n=25 x 5 runs of the
real writer. The writer takes 19-20 of every 25 camera fields verbatim from the
five examples in the instruction, and deleting the examples does break that
habit — but the shoot does not change. Classified by which side of her the
camera actually stands on, the free-writing arms had the same 5.8 position
families and the same biggest family as the control; what they lost was the
field order and every verified form, inventing `at her hip height` and `at
mattress level`, which sessions 227 and 228 measured as coming back at eye
level. So the choice moved into code.

The three properties below are the ones the plan exists to guarantee, and each
is a failure that was actually seen in a shoot.
"""
from __future__ import annotations

import json
from pathlib import Path

from test_shoot_checks import _node_json

ROOT = Path(__file__).resolve().parents[1]

PROBE = """
import { cameraPlan, CAMERA_POSITIONS } from '%(kinds)s'

const family = (line) => CAMERA_POSITIONS.find((p) => p.line === line)?.family ?? 'UNKNOWN'

// Every draw of a forty-five photograph shoot, so a run of them has to hold the
// properties and not merely one lucky one.
const runs = Array.from({ length: 200 }, () => cameraPlan(45))

const consecutive = runs.filter((plan) =>
  plan.some((line, i) => i > 0 && family(line) === family(plan[i - 1]))).length

const unknown = runs.flat().filter((line) => family(line) === 'UNKNOWN').length

// The share of the shoot the most-used single position takes, worst over all runs.
const biggest = Math.max(...runs.map((plan) => {
  const tally = {}
  for (const line of plan) tally[line] = (tally[line] || 0) + 1
  return Math.max(...Object.values(tally)) / plan.length
}))

// Families reached in the worst run, out of the six the catalogue has.
const families = Math.min(...runs.map((plan) => new Set(plan.map(family)).size))

console.log(JSON.stringify({
  consecutive, unknown, biggest, families,
  short: cameraPlan(1).length,
  none: cameraPlan(0).length,
  offEye: CAMERA_POSITIONS.filter((p) => ['overhead', 'floor'].includes(p.family)).length,
}))
"""


def test_the_plan_holds_its_three_properties(tmp_path_factory):
    plan = _node_json(PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                      tmp_path_factory.mktemp("cameraplan"))

    # Session 82's shoot read in order was one photograph taken seventy times.
    # Two photographs running from the same side of her is that failure starting.
    assert plan["consecutive"] == 0, plan

    # Every line handed to the writer has to be one of the measured forms. A
    # position that is not in the catalogue is a position nobody shot.
    assert plan["unknown"] == 0, plan

    # No single position owns the shoot: the whole complaint against the canned
    # five was 19-20 of 25, and a third is the ceiling the instruction asked for
    # and never enforced.
    assert plan["biggest"] <= 1 / 3, plan

    # And it reaches the whole catalogue, not the comfortable half of it.
    assert plan["families"] == 6, plan
    assert plan["offEye"] == 3, plan

    # A one-photograph shoot and a zero-photograph one are both real calls.
    assert plan["short"] == 1 and plan["none"] == 0, plan


FORMS_PROBE = """
import { CAMERA_FORMS, CAMERA_POSITIONS, SHOOT_LINE_INSTRUCTION } from '%(kinds)s'

console.log(JSON.stringify({
  inInstruction: SHOOT_LINE_INSTRUCTION.includes('WHERE THE CAMERA IS, WRITTEN AS A CAMERA'),
  missing: CAMERA_POSITIONS.filter((p) => !CAMERA_FORMS.includes(p.line)).map((p) => p.line),
}))
"""


def test_the_example_positions_are_only_for_a_writer_without_a_plan(tmp_path_factory):
    """The list of camera forms and `cameraPlan` are two voices naming the same
    thing, and a shoot must hear exactly one of them.

    Measured 2026-08-22, five runs a side of n=25 `directed`: taking the list out
    of the instruction changed nothing the analyzer counts — 6.0 position
    families both sides, 9.6 against 9.4 obeyed off-eye forms, 25 of 25 lines
    writing the camera before the framing either way — for 270 words less prompt
    per chunk. So `directed` no longer sees it. `candid` has no plan and still
    free-writes its camera, so it still does, and it has to keep every form the
    plan draws from.
    """
    forms = _node_json(FORMS_PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                       tmp_path_factory.mktemp("cameraforms"))

    # Re-inlined into the instruction, it reaches a planned shoot again as a
    # second voice offering positions that are already decided.
    assert forms["inInstruction"] is False, forms

    # And a form the plan can draw is a form the free-writing manner must have
    # seen, or candid invents `at her hip height` and gets eye level back.
    assert forms["missing"] == [], forms

    # The gate itself: appended only when there is no plan.
    enhance = (ROOT / "frontend/src/enhance.js").read_text(encoding="utf-8")
    assert r"cameras ? '' : `\n\n${CAMERA_FORMS}`" in enhance
