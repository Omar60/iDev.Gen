"""The arrangements: what the two of them are doing, picked per session.

Sessions 155 and 161 are the shoot the `selfie` manner was copied from, and
copying the camera out of them left the other half behind - what the bodies are
doing. The stage plan invents its own arrangements, which is right for a shoot
nobody has a picture of in their head and wrong when a particular photograph is
being chased.

So they are a POOL: nothing is planted unless a session picks it, and a picked
one lands in a minority of the photographs. The properties below are what stops
that from quietly becoming "every photograph is one of six".
"""
from __future__ import annotations

from pathlib import Path

from test_shoot_checks import _node_json

ROOT = Path(__file__).resolve().parents[1]

PROBE = """
import { arrangementPlan, ARRANGEMENTS, kissPlan, shootChunkNote } from '%(kinds)s'

const lengths = [1, 4, 8, 12, 16, 24, 32, 45]
const picked = ['astride', 'back', 'behind']
const runs = lengths.flatMap((n) =>
  Array.from({ length: 50 }, () => ({ n, plan: arrangementPlan(n, picked) })))

// A shoot that picks nothing is written exactly the way it was before this
// existed. That is the default and it is the property that matters most.
const noneIsNone = lengths.every((n) =>
  Object.keys(arrangementPlan(n, [])).length === 0
  && Object.keys(arrangementPlan(n, undefined)).length === 0)

const inRange = runs.every(({ n, plan }) =>
  Object.keys(plan).every((at) => Number(at) >= 1 && Number(at) <= n))
// Only what was picked is ever planted.
const onlyPicked = runs.every(({ plan }) =>
  Object.values(plan).every((a) => picked.includes(a.key)))
// Never more than one in five, and never two running. The picks CYCLE - three
// arrangements over forty-five photographs is nine plantings, not three.
const capped = Math.max(...runs.map(({ n, plan }) => Object.keys(plan).length - Math.ceil(n / 5)))
const cycles = Object.keys(arrangementPlan(45, picked)).length >= 6
const adjacent = runs.filter(({ plan }) => {
  const at = Object.keys(plan).map(Number).sort((a, b) => a - b)
  return at.some((k, i) => i > 0 && k - at[i - 1] < 2)
}).length
// The share of a long shoot they take. A pool that fills the shoot is a rule,
// and the stage plan is what writes everything between them.
const share = Math.max(...runs.filter(({ n }) => n >= 24)
  .map(({ n, plan }) => Object.keys(plan).length / n))

const note = shootChunkNote({
  from: 1, want: 4, total: 8, cameras: ['Taken from directly behind her'],
  poses: [{ at: 3, arrangement: ARRANGEMENTS[0] }],
})
const quiet = shootChunkNote({ from: 5, want: 4, total: 8, cameras: [], poses: [] })

console.log(JSON.stringify({
  noneIsNone, inRange, onlyPicked, capped, adjacent, share, cycles,
  keys: ARRANGEMENTS.map((a) => a.key),
  // Every arrangement names two people plainly, which is the only form this
  // project has measured as rendering the act at all.
  twoPeople: ARRANGEMENTS.every((a) => a.act.includes('two people in frame')),
  // And none of them fights the camera plan or the framing, which are planned
  // somewhere else and handed to the same line.
  noCamera: ARRANGEMENTS.every((a) =>
    !/\\b(camera|photograph|taken from|framing|close-up|overhead)\\b/i.test(a.act)),
  noteNames: note.includes('3 | ') && note.includes('ARRANGEMENT IS ALREADY DECIDED'),
  quietIsQuiet: !quiet.includes('ARRANGEMENT'),
  // The kiss frame still plans exactly as it did: it runs on the same spread now.
  kissStillPlans: Object.keys(kissPlan(24)).length >= 1,
}))
"""


def test_a_picked_pool_lands_in_a_minority_of_the_photographs(tmp_path_factory):
    """The arrangements are a pool, not a plan: nothing unless a session picks
    it, only what it picked, never two running, and never most of the shoot."""
    out = _node_json(PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                     tmp_path_factory.mktemp("poses"))

    assert out["noneIsNone"], out
    assert out["inRange"], out
    assert out["onlyPicked"], out
    assert out["capped"] <= 0, out
    assert out["cycles"], out
    assert out["adjacent"] == 0, out
    # A fifth of the shoot at most, however many are picked.
    assert out["share"] <= 1 / 5, out
    assert out["kissStillPlans"], out


def test_an_arrangement_says_the_bodies_and_nothing_else(tmp_path_factory):
    """It goes in `act` beside a camera and a framing that were planned
    elsewhere. An arrangement that names a camera is a second instruction about
    the same photograph, and in this project the object wins - which would make
    the camera plan a suggestion."""
    out = _node_json(PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                     tmp_path_factory.mktemp("poses2"))

    assert out["twoPeople"], out
    assert out["noCamera"], out
    assert out["noteNames"], out
    assert out["quietIsQuiet"], out
