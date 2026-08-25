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


CATALOGUE_PROBE = """
import { cameraPlan, CANDID_POSITIONS, POSITIONS, MANNERS } from '%(kinds)s'

const family = (line) => CANDID_POSITIONS.find((p) => p.line === line)?.family ?? 'UNKNOWN'
const runs = Array.from({ length: 200 }, () => cameraPlan(45, Math.random, CANDID_POSITIONS))

console.log(JSON.stringify({
  // Every manner plans, which is why there is no list of example forms left in
  // the instruction for one to free-write from.
  unplanned: MANNERS.map((m) => m.key).filter((k) => !POSITIONS[k]),
  lines: CANDID_POSITIONS.map((p) => p.line),
  families: [...new Set(CANDID_POSITIONS.map((p) => p.family))].sort(),
  consecutive: runs.filter((plan) =>
    plan.some((line, i) => i > 0 && family(line) === family(plan[i - 1]))).length,
  unknown: runs.flat().filter((line) => family(line) === 'UNKNOWN').length,
  biggest: Math.max(...runs.map((plan) => {
    const tally = {}
    for (const line of plan) tally[line] = (tally[line] || 0) + 1
    return Math.max(...Object.values(tally)) / plan.length
  })),
}))
"""

# Word for word what sessions 245-251 judged. A form nobody shot is a form that
# comes back frontal, and the two that are deliberately absent cost four batches
# to establish: `behind` was 0/6 and the floor 0/3 under the candid look, both
# wordings, with the subject block already fixed.
CANDID_LINES = [
    "Taken from directly in front of her",
    "Phone held out at arm's length in front of her face",
    "Overhead camera directly above her",
    "Phone propped on a high shelf across the room, looking down at her",
    "Taken from behind her left shoulder, her back three-quarters to the camera",
    "Taken from behind her right shoulder, her back three-quarters to the camera",
    "Mirror selfie, the phone up in her right hand and visible in the mirror",
]


def test_the_candid_catalogue_is_what_was_measured(tmp_path_factory):
    """`candid` plans from where a phone was put down, not from where a
    photographer stands, and every line in its catalogue was rendered three times
    on three seeds and read back by a blind judge.

    The properties differ from the directed plan in one way that is deliberate:
    four families, not six. `behind` and `floor` do not render under this manner
    at all, so a plan that reached six families would be reaching two that come
    back frontal.
    """
    out = _node_json(CATALOGUE_PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                     tmp_path_factory.mktemp("candidplan"))

    # A manner with no catalogue gets no camera guidance at all now that the
    # example forms are gone: shoot one, or put the list back.
    assert out["unplanned"] == [], out

    # Drift here is silent - a reworded form is a form nobody shot, and it comes
    # back as a front view.
    assert out["lines"] == CANDID_LINES, out
    assert out["families"] == ["front", "mirror", "overhead", "shoulder"], out

    # The same three properties the directed plan holds.
    assert out["consecutive"] == 0, out
    assert out["unknown"] == 0, out
    assert out["biggest"] <= 1 / 3, out


SELFIE_PROBE = """
import { cameraPlan, CANDID_POSITIONS, SELFIE_POSITIONS, MANNER } from '%(kinds)s'

const family = (line) => SELFIE_POSITIONS.find((p) => p.line === line)?.family ?? 'UNKNOWN'
const runs = Array.from({ length: 200 }, () => cameraPlan(45, Math.random, SELFIE_POSITIONS))

console.log(JSON.stringify({
  // The measured seven are carried over untouched, and what is added is added.
  keepsCandid: CANDID_POSITIONS.every((p) => SELFIE_POSITIONS.includes(p)),
  added: SELFIE_POSITIONS.filter((p) => !CANDID_POSITIONS.includes(p)).map((p) => p.family),
  // Inherited: same room, same phone, same capture quality as `candid`.
  sameLook: MANNER.selfie.look === MANNER.candid.look,
  // Overridden: the two rules this manner exists to turn around.
  arm: MANNER.selfie.line.includes('HER OWN ARM IS IN THE FRAME'),
  eyes: MANNER.selfie.line.includes('HER EYES ARE ON THE LENS'),
  // And they are read AFTER the candid rules they contradict, never before.
  afterCandid: MANNER.selfie.line.indexOf('HER EYES ARE ON THE LENS')
               > MANNER.selfie.line.indexOf('HER EYES ARE NOT ON THE LENS'),
  consecutive: runs.filter((plan) =>
    plan.some((line, i) => i > 0 && family(line) === family(plan[i - 1]))).length,
  unknown: runs.flat().filter((line) => family(line) === 'UNKNOWN').length,
  biggest: Math.max(...runs.map((plan) => {
    const tally = {}
    for (const line of plan) tally[line] = (tally[line] || 0) + 1
    return Math.max(...Object.values(tally)) / plan.length
  })),
}))
"""


def test_the_selfie_manner_is_candid_with_two_rules_turned_around(tmp_path_factory):
    """`selfie` is the shoot of sessions 155 and 161: she holds the phone through
    the act and looks into it. That is `candid` with its two strictest rules
    reversed — the phone almost never in frame, her eyes never on the lens — so
    it inherits the manner whole and appends the reversal, which is the only
    order that works: a contradiction read before the rule it contradicts loses.

    Nothing in this manner has been through the judge. What the test pins is that
    the seven measured candid forms survive intact underneath it.
    """
    out = _node_json(SELFIE_PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                     tmp_path_factory.mktemp("selfieplan"))

    assert out["keepsCandid"] is True, out
    assert out["added"] == ["pov", "pov"], out
    assert out["sameLook"] is True, out
    assert out["arm"] is True and out["eyes"] is True, out
    assert out["afterCandid"] is True, out

    # The same three properties every catalogue holds.
    assert out["consecutive"] == 0, out
    assert out["unknown"] == 0, out
    assert out["biggest"] <= 1 / 3, out


# Raw, and it has to stay raw: the word-boundary escapes below are for the JS
# regex, and in a cooked string Python eats `\b` into a backspace byte before
# node ever sees it. That has now cost three separate sittings.
TECHNIQUE_PROBE = r"""
import { MANNER } from '%(kinds)s'

// The eight forms the manner offers for the `technique` field, read off the
// instruction rather than restated here: a list restated in a test drifts from
// the one the writer sees, which is the failure this is guarding against.
const line = MANNER.candid.line
const menu = line.slice(line.indexOf('What goes in it'), line.indexOf('It NAMES NOTHING'))
const examples = [...menu.matchAll(/`([^`]+)`/g)].map((m) => m[1])

// The property sessions 277 and 278 measured: a defect with somewhere to fall
// lands there, and a bare adjective renders as if nothing had been written.
const ATTACHED = /\b(where|across|down|under|along|beside|of her|side of|in the frame)\b/i
const ROOM = /\b(window|sofa|couch|bed|mattress|carpet|lamp|curtain|wall|floor|headboard|bedspread|shelf|door|mirror|room)\b/i
const DEVICE = /\b(phone|camera|lens|flash)\b/i

console.log(JSON.stringify({
  count: examples.length,
  loose: examples.filter((e) => !ATTACHED.test(e)),
  room: examples.filter((e) => ROOM.test(e)),
  device: examples.filter((e) => DEVICE.test(e)),
}))
"""


def test_every_candid_technique_example_has_somewhere_to_fall(tmp_path_factory):
    """A `technique` example that names a defect and not a place teaches a clause
    that does not render.

    Measured 2026-08-24, sessions 277 and 278: four arms of eight seeds on one
    hand-fixed line, judged blind on where the motion blur landed. `slightly
    blurred` put it on her hand in 0 photographs of 8, and a line carrying no
    Technique block at all scored 1 of 8 - the same photograph. `slightly
    blurred where a hand moved` scored 4 of 8 (p=0.038 against the bare form)
    and `blurred down her forearm where her hand moved at her side` scored 6 of
    8, which is not a real gain over the loose one at this size (p=0.30).

    So what is pinned is the ATTACHMENT and not the anatomy: every example has
    to say where its defect falls, and none may reach for the room or a device,
    which two older measurements in the same paragraph already forbid. The
    writer copies the examples rather than the rule, so an example that breaks
    either is the rule being deleted quietly.
    """
    out = _node_json(TECHNIQUE_PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                     tmp_path_factory.mktemp("techniquemenu"))

    # Eight, and the count is pinned so that trimming the list to make room for
    # something else is a decision somebody takes on purpose.
    assert out["count"] == 8, out
    assert out["loose"] == [], out
    assert out["room"] == [], out
    assert out["device"] == [], out
