"""The camera plan, which decides where the camera stands before the writer is
asked anything.
"""
from __future__ import annotations

import json
from pathlib import Path

from test_shoot_checks import _node_json

ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = (ROOT / "data" / "catalogue-seed.json").as_posix()

PROBE = """
import fs from 'fs'
import { cameraPlan, setCatalogue, positionsFor } from '%(kinds)s'

const seed = JSON.parse(fs.readFileSync('%(seed)s', 'utf-8'))
setCatalogue(seed)

const directed = positionsFor('directed')
const family = (line) => directed.find((p) => p.wordings[0].text === line)?.wordings[0].family ?? 'UNKNOWN'

// Every draw of a forty-five photograph shoot, so a run of them has to hold the
// properties and not merely one lucky one.
const runs = Array.from({ length: 200 }, () => cameraPlan(45, Math.random, directed))

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
  short: cameraPlan(1, Math.random, directed).length,
  none: cameraPlan(0, Math.random, directed).length,
  offEye: directed.filter((p) => ['overhead', 'floor'].includes(p.wordings[0].family)).length,
}))
"""


def test_the_plan_holds_its_three_properties(tmp_path_factory):
    plan = _node_json(PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix(), "seed": SEED_PATH},
                      tmp_path_factory.mktemp("cameraplan"))

    assert plan["consecutive"] == 0, plan
    assert plan["unknown"] == 0, plan
    assert plan["biggest"] <= 1 / 3, plan
    assert plan["families"] == 6, plan
    assert plan["offEye"] == 3, plan
    assert plan["short"] == 1 and plan["none"] == 0, plan


CATALOGUE_PROBE = """
import fs from 'fs'
import { cameraPlan, setCatalogue, positionsFor, MANNERS } from '%(kinds)s'

const seed = JSON.parse(fs.readFileSync('%(seed)s', 'utf-8'))
setCatalogue(seed)

const candid = positionsFor('candid')
const family = (line) => candid.find((p) => p.wordings[0].text === line)?.wordings[0].family ?? 'UNKNOWN'
const runs = Array.from({ length: 200 }, () => cameraPlan(45, Math.random, candid))

console.log(JSON.stringify({
  unplanned: MANNERS.map((m) => m.key).filter((k) => positionsFor(k).length === 0),
  lines: candid.map((p) => p.wordings[0].text),
  families: [...new Set(candid.map((p) => p.wordings[0].family))].sort(),
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
    out = _node_json(CATALOGUE_PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix(), "seed": SEED_PATH},
                     tmp_path_factory.mktemp("candidplan"))

    assert out["unplanned"] == [], out
    assert out["lines"] == CANDID_LINES, out
    # Five families, not four: the arm's-length phone left `front` on 2026-09-02
    # (a family is only as fine as the constraint it carries, and an act with
    # both hands on the floor has no hand for a phone). The store and
    # `candid-cameras-seed.json` moved that day; `catalogue-seed.json` did not,
    # and this line went on pinning the shape the older file still had — green
    # against data the app had already stopped using.
    assert out["families"] == ["arm", "front", "mirror", "overhead", "shoulder"], out
    assert out["consecutive"] == 0, out
    assert out["unknown"] == 0, out
    assert out["biggest"] <= 1 / 3, out


SELFIE_PROBE = """
import fs from 'fs'
import { cameraPlan, setCatalogue, positionsFor, MANNER } from '%(kinds)s'

const seed = JSON.parse(fs.readFileSync('%(seed)s', 'utf-8'))
setCatalogue(seed)

const candid = positionsFor('candid')
const selfie = positionsFor('selfie')
const candidKeys = new Set(candid.map((p) => p.key))
const family = (line) => selfie.find((p) => p.wordings[0].text === line)?.wordings[0].family ?? 'UNKNOWN'
const runs = Array.from({ length: 200 }, () => cameraPlan(45, Math.random, selfie))

console.log(JSON.stringify({
  keepsCandid: candid.every((p) => selfie.some((s) => s.key === p.key && s.wordings[0].text === p.wordings[0].text)),
  added: selfie.filter((p) => !candidKeys.has(p.key)).map((p) => p.wordings[0].family),
  sameLook: MANNER.selfie.look === MANNER.candid.look,
  arm: MANNER.selfie.line.includes('HER OWN ARM IS IN THE FRAME'),
  eyes: MANNER.selfie.line.includes('HER EYES ARE ON THE LENS'),
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
    out = _node_json(SELFIE_PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix(), "seed": SEED_PATH},
                     tmp_path_factory.mktemp("selfieplan"))

    assert out["keepsCandid"] is True, out
    assert out["added"] == ["pov", "pov"], out
    assert out["sameLook"] is True, out
    assert out["arm"] is True and out["eyes"] is True, out
    assert out["afterCandid"] is True, out
    assert out["consecutive"] == 0, out
    assert out["unknown"] == 0, out
    assert out["biggest"] <= 1 / 3, out


TECHNIQUE_PROBE = r"""
import { MANNER } from '%(kinds)s'

const line = MANNER.candid.line
const start = line.indexOf('line to line:')
const menu = line.slice(start, line.indexOf('`. ', start) + 1)
const examples = [...menu.matchAll(/`([^`]+)`/g)].map((m) => m[1])

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
    out = _node_json(TECHNIQUE_PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                     tmp_path_factory.mktemp("techniquemenu"))

    assert out["count"] == 8, out
    assert out["loose"] == [], out
    assert out["room"] == [], out
    assert out["device"] == [], out
