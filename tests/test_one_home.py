"""One home: a component's wording text has exactly one place it lives.
"""
from __future__ import annotations

from pathlib import Path

from test_shoot_checks import _node_json

ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = (ROOT / "data" / "catalogue-seed.json").as_posix()

PROBE = """
import fs from 'fs'
import {
  setCatalogue, positionsFor, arrangements, framings,
  BODY_OPENINGS, TECHNIQUE_DEFECTS,
  KISS_FRAMES, EXPRESSIONS,
  LOOK_INSTRUCTION, LOOK_FROM_PHOTO_INSTRUCTION, LOOK_ONLY_INSTRUCTION,
  WARDROBE_INSTRUCTION, WARDROBE_PROGRESSION_INSTRUCTION,
  REPAIR_INSTRUCTION, BRIEF_INSTRUCTION,
  STAGE_PLAN_INSTRUCTION, SHOOT_LINE_INSTRUCTION,
  EXPLICIT_STRETCH, EXPLICIT_REGISTER,
  ANGLE_FROM_TEXT_INSTRUCTION, EXPRESSION_KEEP, EXPRESSION_KEEP_NO_EYES,
  MANNERS,
} from '%(kinds)s'

const seed = JSON.parse(fs.readFileSync('%(seed)s', 'utf-8'))
setCatalogue(seed)

const homes = []
for (const [catalogue, list] of [
  ['camera.directed',  positionsFor('directed')],
  ['camera.candid',    positionsFor('candid')],
  ['camera.selfie',    positionsFor('selfie')],
  ['act.directed',     arrangements('directed')],
  ['framing.directed', framings('directed')],
  ['BODY_OPENINGS',    BODY_OPENINGS],
  ['TECHNIQUE_DEFECTS',TECHNIQUE_DEFECTS],
  ['KISS_FRAMES',      KISS_FRAMES],
  ['EXPRESSIONS',      EXPRESSIONS],
]) {
  for (const entry of list) {
    for (const w of entry.wordings) {
      homes.push({ text: w.text, catalogue, concept: entry.key })
    }
  }
}

const byText = new Map()
for (const h of homes) {
  if (!byText.has(h.text)) byText.set(h.text, { text: h.text, homes: [] })
  byText.get(h.text).homes.push(`${h.catalogue}.${h.concept}`)
}

const instructionStrings = [
  LOOK_INSTRUCTION,
  LOOK_FROM_PHOTO_INSTRUCTION,
  LOOK_ONLY_INSTRUCTION,
  WARDROBE_INSTRUCTION,
  WARDROBE_PROGRESSION_INSTRUCTION,
  REPAIR_INSTRUCTION,
  BRIEF_INSTRUCTION,
  STAGE_PLAN_INSTRUCTION,
  SHOOT_LINE_INSTRUCTION,
  EXPLICIT_STRETCH,
  EXPLICIT_REGISTER,
  ANGLE_FROM_TEXT_INSTRUCTION,
  EXPRESSION_KEEP,
  EXPRESSION_KEEP_NO_EYES,
  ...MANNERS.flatMap((m) => [m.line, m.brief, m.look, m.lookNote].filter(Boolean)),
]
const promptSystem = instructionStrings.join(String.fromCharCode(10))

const offenders = []
for (const stats of byText.values()) {
  let count = 0
  let pos = 0
  while ((pos = promptSystem.indexOf(stats.text, pos)) !== -1) {
    count += 1
    pos += stats.text.length
  }
  if (count > 0) {
    offenders.push({ text: stats.text, count, homes: stats.homes })
  }
}

const wink = KISS_FRAMES.find((f) => f.key === 'wink')
const finger = KISS_FRAMES.find((f) => f.key === 'finger')
const winkFingerShareText = !!(wink && finger
                               && wink.wordings[0].text === finger.wordings[0].text)
const winkFingerDifferInHand = !!(wink && finger && wink.hand !== finger.hand)

console.log(JSON.stringify({
  totalHomes: homes.length,
  offenders,
  winkFingerShareText,
  winkFingerDifferInHand,
}))
"""

# The duplicates already in the tree, in catalogue order. Shrinking this list
# is the cleanup; growing it is a regression and needs a reason written here.
#
# Still open, task 7.1 of the prompt-component-matrix change (the two inline
# camera examples in `SHOOT_LINE_INSTRUCTION` that proposal names):
#   'Taken from her right side, her body in full profile', 'Taken from
#   directly behind her'
# Real duplicates 7.1 does not touch — the camera and `mirror-selfie` examples
# in the candid manner's `line` (inherited by selfie), the body openings
# enumerated in the `her` field description, and the two technique defects used
# as examples in candid's `line`.
# Not a duplicate at all: 'her feet' is two ordinary words, and the probe
# matches on substring, so it also hits prose that merely says her feet.
# Removing it from this list means rewording English that has nothing to do
# with the rule.
#
# Added by the catalogue-store change, and NOT a new duplicate — a pre-existing
# one this probe could not see before:
#   'a three-quarter photograph from the knees up'
# The framing concept was never in the probe's catalogue list while framing was
# a lone constant, so its text was never compared against the prompt system.
# Now that framing is a component like any other, the copy that has always sat
# in `SHOOT_LINE_INSTRUCTION` (kinds.js:1442, the two-person framing rule)
# shows up. It is a real second home and belongs to the same cleanup as the two
# camera examples above; nothing about it was introduced by that change.
KNOWN_DUPLICATES = [
    'Taken from directly in front of her',
    'Taken from her right side, her body in full profile',
    'Taken from directly behind her',
    'Overhead camera directly above her',
    'Mirror selfie, the phone up in her right hand and visible in the mirror',
    'a three-quarter photograph from the knees up',
    'her chest and torso',
    'her hips and legs',
    'her feet',
    'the colour washed out of her skin',
    'her shoulders running a few degrees off level in the frame',
]


def _run(tmp_path_factory):
    return _node_json(PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix(), "seed": SEED_PATH},
                      tmp_path_factory.mktemp("onehome"))


def test_a_components_wording_text_has_exactly_one_home(tmp_path_factory):
    """A catalogue wording's text lives once, in the store, and nowhere else in
    the prompt system. A second copy in an instruction is what makes a
    catalogue entry have no effect: whichever copy the reader meets first is
    the one that decides the photograph, and the two drift apart without
    anything failing.

    The rule is not met today, so the assertion is against the baseline above
    rather than against the empty list. A brand new duplicate fails the moment
    it is written; the ones already in the tree are listed, each with its
    reason, and the list is only allowed to shrink.

    `data/catalogue-seed.json` is read here as the catalogue, and it is not a
    second home while nothing loads it automatically: it is the offer the
    operator either imports into the store or does not.
    """
    out = _run(tmp_path_factory)
    assert sorted([o["text"] for o in out["offenders"]]) == sorted(KNOWN_DUPLICATES), out


def test_wink_and_finger_are_an_allowed_pair_with_shared_text(tmp_path_factory):
    out = _run(tmp_path_factory)
    assert out["winkFingerShareText"] is True, out
    assert out["winkFingerDifferInHand"] is True, out
