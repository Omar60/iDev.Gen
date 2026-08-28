"""The arrangements: what the two of them are doing, picked per session.
"""
from __future__ import annotations

from pathlib import Path

from test_shoot_checks import _node_json

ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = (ROOT / "data" / "catalogue-seed.json").as_posix()

PROBE = """
import fs from 'fs'
import { arrangementPlan, arrangements, setCatalogue, kissPlan, shootChunkNote } from '%(kinds)s'

const seed = JSON.parse(fs.readFileSync('%(seed)s', 'utf-8'))
setCatalogue(seed)

const allArrangements = arrangements('directed')
const lengths = [1, 4, 8, 12, 16, 24, 32, 45]
const picked = ['astride', 'reverse', 'wall']
const runs = lengths.flatMap((n) =>
  Array.from({ length: 50 }, () => ({ n, plan: arrangementPlan(n, picked, Math.random, 'directed') })))

const noneIsNone = lengths.every((n) =>
  Object.keys(arrangementPlan(n, [], Math.random, 'directed')).length === 0
  && Object.keys(arrangementPlan(n, undefined, Math.random, 'directed')).length === 0)

const inRange = runs.every(({ n, plan }) =>
  Object.keys(plan).every((at) => Number(at) >= 1 && Number(at) <= n))

const onlyPicked = runs.every(({ plan }) =>
  Object.values(plan).every((a) => picked.includes(a.key)))

const capped = Math.max(...runs.map(({ n, plan }) => Object.keys(plan).length - Math.ceil(n / 5)))
const cycles = Object.keys(arrangementPlan(45, picked, Math.random, 'directed')).length >= 6
const adjacent = runs.filter(({ plan }) => {
  const at = Object.keys(plan).map(Number).sort((a, b) => a - b)
  return at.some((k, i) => i > 0 && k - at[i - 1] < 2)
}).length

const share = Math.max(...runs.filter(({ n }) => n >= 24)
  .map(({ n, plan }) => Object.keys(plan).length / n))

const note = shootChunkNote({
  from: 1, want: 4, total: 8,
  cameras: ['Taken from directly behind her', 'Overhead camera directly above her',
            'Taken from her right side, her body in full profile'],
  poses: [{ at: 3, arrangement: allArrangements[0] }],
})
const quiet = shootChunkNote({ from: 5, want: 4, total: 8, cameras: [], poses: [] })

const act = (a) => a.wordings[0].text

console.log(JSON.stringify({
  noneIsNone, inRange, onlyPicked, capped, adjacent, share, cycles,
  keys: allArrangements.map((a) => a.key),
  twoPeople: allArrangements.every((a) => act(a).includes('two people in frame')),
  noCamera: allArrangements.every((a) =>
    !/\\b(camera|photograph|taken from|framing|close-up|overhead)\\b/i.test(act(a))),
  noteNames: note.includes(`3 | act: ${act(allArrangements[0])}`) && note.includes('ALREADY DECIDED'),
  noteRowIsRight: note.indexOf('3 | act:') > note.indexOf('3 | Taken from her right side')
                  && note.indexOf('3 | act:') < note.indexOf('Open each line'),
  noteIsFirm: note.includes('it is not yours') && note.includes('OPENS with those words'),
  noteOwnsOnePhotograph: note.includes('THAT PHOTOGRAPH AND NO OTHER'),
  noneDead: ['behind', 'back', 'side'].every((k) => !allArrangements.some((a) => a.key === k)),
  quietIsQuiet: !quiet.includes('ALREADY DECIDED'),
  kissStillPlans: Object.keys(kissPlan(24)).length >= 1,
}))
"""


def test_a_picked_pool_lands_in_a_minority_of_the_photographs(tmp_path_factory):
    out = _node_json(PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix(), "seed": SEED_PATH},
                     tmp_path_factory.mktemp("poses"))

    assert out["noneIsNone"], out
    assert out["inRange"], out
    assert out["onlyPicked"], out
    assert out["capped"] <= 0, out
    assert out["cycles"], out
    assert out["adjacent"] == 0, out
    assert out["share"] <= 1 / 5, out
    assert out["kissStillPlans"], out


def test_an_arrangement_says_the_bodies_and_nothing_else(tmp_path_factory):
    out = _node_json(PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix(), "seed": SEED_PATH},
                     tmp_path_factory.mktemp("poses2"))

    assert out["twoPeople"], out
    assert out["noCamera"], out
    assert out["noteNames"], out
    assert out["noteRowIsRight"], out
    assert out["noteIsFirm"], out
    assert out["noteOwnsOnePhotograph"], out
    assert out["noneDead"], out
    assert out["quietIsQuiet"], out


CLASH_PROBE = """
import fs from 'fs'
import { withoutClashing } from '%(enhance)s'
import { arrangements, setCatalogue, KISS_FRAMES } from '%(kinds)s'

const seed = JSON.parse(fs.readFileSync('%(seed)s', 'utf-8'))
setCatalogue(seed)

const allArrangements = arrangements('directed')
const kisses = { 22: KISS_FRAMES[0], 28: KISS_FRAMES[1], 30: KISS_FRAMES[2] }
const moved = withoutClashing(
  { 5: allArrangements[0], 22: allArrangements[2], 29: allArrangements[1] }, kisses, 30)

const dropped = withoutClashing(
  { 27: allArrangements[0], 28: allArrangements[1], 29: allArrangements[2] },
  { 28: KISS_FRAMES[0] }, 30)

console.log(JSON.stringify({
  keeps: moved[5]?.key === 'astride' && moved[29]?.key === 'reverse',
  movedOff: !moved[22] && moved[23]?.key === 'wall',
  noneOnAKiss: Object.keys(moved).every((at) => !kisses[at]),
  droppedIt: Object.keys(dropped).length === 2 && !dropped[28],
}))
"""


def test_an_arrangement_never_lands_on_a_kiss_frame(tmp_path_factory):
    out = _node_json(CLASH_PROBE % {"enhance": (ROOT / "frontend/src/enhance.js").as_posix(),
                                    "kinds": (ROOT / "frontend/src/kinds.js").as_posix(),
                                    "seed": SEED_PATH},
                     tmp_path_factory.mktemp("clash"))

    assert out["keeps"], out
    assert out["movedOff"], out
    assert out["noneOnAKiss"], out
    assert out["droppedIt"], out


FIT_PROBE = """
import fs from 'fs'
import { fitCameras, cameraPlan, arrangementPlan, arrangements, setCatalogue, positionsFor }
  from '%(kinds)s'

const seed = JSON.parse(fs.readFileSync('%(seed)s', 'utf-8'))
setCatalogue(seed)

const manners = ['directed', 'candid', 'selfie']
const allArrangements = arrangements('directed')

const familyOf = (positions, line) => positions.find((p) => p.wordings[0].text === line)?.wordings[0].family
const familyOfEntry = (p) => p.wordings[0].family

const reachable = manners.every((manner) => {
  const positions = positionsFor(manner)
  return allArrangements.every((a) => positions.some((p) => a.cameras.includes(familyOfEntry(p))))
})

const runs = manners.flatMap((manner) => {
  const positions = positionsFor(manner)
  return Array.from({ length: 100 }, () => {
    const n = 30
    const poses = arrangementPlan(n, allArrangements.map((a) => a.key), Math.random, manner)
    const before = cameraPlan(n, Math.random, positions)
    const after = fitCameras(before, poses, positions)
    return { manner, positions, poses, before, after }
  })
})

const fitted = runs.every(({ positions, poses, after }) =>
  Object.entries(poses).every(([at, a]) => {
    const best = a.cameras.find((f) => positions.some((p) => familyOfEntry(p) === f))
    return familyOf(positions, after[Number(at) - 1]) === best
  }))

const untouched = runs.every(({ poses, before, after }) =>
  before.every((line, i) => poses[i + 1] || line === after[i]))

const moved = runs.reduce((sum, { before, after }) =>
  sum + before.filter((line, i) => line !== after[i]).length, 0)

const onlyFront = [{ key: 'front-direct', slot: 'camera',
                     wordings: [{ text: 'Taken from directly in front of her', family: 'front' }] }]
const stuck = fitCameras(['Taken from directly in front of her'],
                         { 1: allArrangements.find((a) => a.key === 'wall') }, onlyFront)

console.log(JSON.stringify({
  reachable, fitted, untouched, moved,
  stuck: stuck[0] === 'Taken from directly in front of her',
  known: allArrangements.every((a) => a.cameras.every((f) =>
    manners.some((manner) => positionsFor(manner).some((p) => familyOfEntry(p) === f)))),
}))
"""


def test_a_planted_arrangement_gets_a_camera_that_can_see_it(tmp_path_factory):
    out = _node_json(FIT_PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix(), "seed": SEED_PATH},
                     tmp_path_factory.mktemp("fit"))

    assert out["reachable"], out
    assert out["known"], out
    assert out["fitted"], out
    assert out["untouched"], out
    assert out["moved"] > 0, out
    assert out["stuck"], out
