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
const picked = ['astride', 'reverse', 'wall']
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
  from: 1, want: 4, total: 8,
  cameras: ['Taken from directly behind her', 'Overhead camera directly above her',
            'Taken from her right side, her body in full profile'],
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
  // The arrangement rides on its photograph's own camera row: two lists to line
  // up put it on the photograph before the one it was given to, five rows of
  // twelve.
  noteNames: note.includes(`3 | act: ${ARRANGEMENTS[0].act}`) && note.includes('ALREADY DECIDED'),
  noteRowIsRight: note.indexOf('3 | act:') > note.indexOf('3 | Taken from her right side')
                  && note.indexOf('3 | act:') < note.indexOf('Open each line'),
  // Handed over in the camera note's words, which is what took arrival from 3
  // photographs of 6 to 12 of 12: it is not yours, and the field opens with it.
  noteIsFirm: note.includes('it is not yours') && note.includes('OPENS with those words'),
  // The carry-over is what session 268's remaining misses were made of: the
  // writer repeated a planted arrangement into the photographs after it, and
  // those were dealt cameras that cannot see it.
  noteOwnsOnePhotograph: note.includes('THAT PHOTOGRAPH AND NO OTHER'),
  // The three that never rendered are not on offer. They are kept in the file as
  // prose, so their names appear in comments but never as a key: `behind` was
  // dead in every context, and `back` and `side` were 0 of 41 photographs across
  // two checkpoints and four cameras in sessions 269 and 271.
  noneDead: ['behind', 'back', 'side'].every((k) => !ARRANGEMENTS.some((a) => a.key === k)),
  quietIsQuiet: !quiet.includes('ALREADY DECIDED'),
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
    assert out["noteRowIsRight"], out
    assert out["noteIsFirm"], out
    assert out["noteOwnsOnePhotograph"], out
    assert out["noneDead"], out
    assert out["quietIsQuiet"], out


CLASH_PROBE = """
import { withoutClashing } from '%(enhance)s'
import { ARRANGEMENTS, KISS_FRAMES } from '%(kinds)s'

// Photograph 22 carries both, which is what session 267 shot: the kiss frame
// replaces the camera and dictates the face, the arrangement says they are
// standing against a wall, and the photograph came back as neither.
const kisses = { 22: KISS_FRAMES[0], 28: KISS_FRAMES[1], 30: KISS_FRAMES[2] }
const moved = withoutClashing(
  { 5: ARRANGEMENTS[0], 22: ARRANGEMENTS[2], 29: ARRANGEMENTS[1] }, kisses, 30)

// Both sides of a clash blocked - 27 and 29 are taken, 28 is the kiss - so it
// is dropped rather than stacked.
const dropped = withoutClashing(
  { 27: ARRANGEMENTS[0], 28: ARRANGEMENTS[1], 29: ARRANGEMENTS[2] },
  { 28: KISS_FRAMES[0] }, 30)

console.log(JSON.stringify({
  keeps: moved[5]?.key === 'astride' && moved[29]?.key === 'reverse',
  movedOff: !moved[22] && moved[23]?.key === 'wall',
  noneOnAKiss: Object.keys(moved).every((at) => !kisses[at]),
  droppedIt: Object.keys(dropped).length === 2 && !dropped[28],
}))
"""


def test_an_arrangement_never_lands_on_a_kiss_frame(tmp_path_factory):
    """Session 267 planted both on photograph 22 and got neither: a kiss frame
    replaces the camera and writes the face, an arrangement says what the bodies
    are doing, and one photograph cannot be both. The clash moves one photograph
    either way, and is dropped when neither side is free - a shoot has more
    arrangements coming, and two plans on one line is worse than one."""
    out = _node_json(CLASH_PROBE % {"enhance": (ROOT / "frontend/src/enhance.js").as_posix(),
                                    "kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                     tmp_path_factory.mktemp("clash"))

    assert out["keeps"], out
    assert out["movedOff"], out
    assert out["noneOnAKiss"], out
    assert out["droppedIt"], out


FIT_PROBE = """
import { fitCameras, cameraPlan, arrangementPlan, ARRANGEMENTS, ARRANGEMENT, POSITIONS }
  from '%(kinds)s'

const familyOf = (positions, line) => positions.find((p) => p.line === line)?.family

// Every manner can take every arrangement: a `cameras` list that empties a
// catalogue leaves that photograph with no camera at all.
const reachable = Object.entries(POSITIONS).every(([, positions]) =>
  ARRANGEMENTS.every((a) => positions.some((p) => a.cameras.includes(p.family))))

// The whole thing end to end, on every manner, many draws.
const runs = Object.entries(POSITIONS).flatMap(([manner, positions]) =>
  Array.from({ length: 100 }, () => {
    const n = 30
    const poses = arrangementPlan(n, ARRANGEMENTS.map((a) => a.key))
    const before = cameraPlan(n, Math.random, positions)
    const after = fitCameras(before, poses, positions)
    return { manner, positions, poses, before, after }
  }))

// Every planted photograph ends on a camera its arrangement was measured on -
// and on the FIRST family of its list that this catalogue has, because the lists
// are ordered by what each family scored: `wall` is 3/3 from a mirror and 0/3
// from behind her shoulder, so a random draw among the two spends a third of the
// plantings on a form that was measured to fail.
const fitted = runs.every(({ positions, poses, after }) =>
  Object.entries(poses).every(([at, a]) => {
    const best = a.cameras.find((f) => positions.some((p) => p.family === f))
    return familyOf(positions, after[Number(at) - 1]) === best
  }))

// And nothing else moved: the spread the plan drew is the shoot.
const untouched = runs.every(({ poses, before, after }) =>
  before.every((line, i) => poses[i + 1] || line === after[i]))

// It really had work to do - otherwise the two above pass on an empty change.
const moved = runs.reduce((sum, { before, after }) =>
  sum + before.filter((line, i) => line !== after[i]).length, 0)

// A camera left alone when the catalogue has nothing compatible, rather than
// emptied. `wall` cannot be seen from the front, and a one-position catalogue
// of exactly that is the case with no answer.
const onlyFront = [{ family: 'front', line: 'Taken from directly in front of her' }]
const stuck = fitCameras(['Taken from directly in front of her'],
                         { 1: ARRANGEMENT.wall }, onlyFront)

console.log(JSON.stringify({
  reachable, fitted, untouched, moved,
  stuck: stuck[0] === 'Taken from directly in front of her',
  // No arrangement allows a camera family that no catalogue has, which would be
  // a typo nothing else would catch.
  known: ARRANGEMENTS.every((a) => a.cameras.every((f) =>
    Object.values(POSITIONS).some((positions) => positions.some((p) => p.family === f)))),
}))
"""


def test_a_planted_arrangement_gets_a_camera_that_can_see_it(tmp_path_factory):
    """Session 267: three of five planted arrangements were handed a camera
    behind her shoulder, and all three rendered as a different arrangement -
    asked for her on top facing him with her back to the lens, the sampler
    turned her around on him rather than move the camera. The camera outranks
    the bodies, so the planted photographs take their camera from the families
    their arrangement can be seen from, and only those photographs move."""
    out = _node_json(FIT_PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                     tmp_path_factory.mktemp("fit"))

    assert out["reachable"], out
    assert out["known"], out
    assert out["fitted"], out
    assert out["untouched"], out
    assert out["moved"] > 0, out
    assert out["stuck"], out
