"""One home: a component's wording text has exactly one place it lives.

Task 1.3 of the prompt-component-matrix change. The rule itself is in the
prompt-components spec: "The text of a component SHALL appear in exactly one
place. No instruction, no example and no per-manner text may carry a second
copy of a wording that the catalogue already holds."

A second copy is what makes a catalogue entry have no effect. The reader meets
the copy in the instruction and uses that, while the catalogue entry sits
unchanged. The two drift apart without anything failing, and a measured change
to the catalogue entry does not move the photograph.

The test walks every catalogue, takes every wording's text, and asserts none
of those texts appear anywhere in the prompt system. The prompt system is the
exported instruction strings the model actually reads: the global
instructions (SHOOT_LINE_INSTRUCTION, LOOK_INSTRUCTION, the explicit register,
the JSON_SYSTEM and so on) and the per-manner prose (each MANNER's `line`,
`brief`, `look`, `lookNote`). Comments, docstrings and function bodies are
NOT in this set - the model does not read them.

The rule is not met yet, so the check is against a written-down baseline
(`KNOWN_DUPLICATES`) and not against the empty list: the suite stays green
while a new duplicate still fails immediately. Ten texts are in that
baseline. Two of them are the inline camera examples in
`SHOOT_LINE_INSTRUCTION` that task 7.1 removes; the rest are duplicates 7.1
does not touch, and one ('her feet') is a substring false positive. The list
carries the reasons.

The wink/finger kiss-frame pair is the documented exception. The two
concepts share the same wording text by design - the face is one
photograph and rewriting either copy would rewrite the same thing. The
distinction between the two flavours lives in the `hand` attribute
(`wink` has none; `finger` adds the middle-finger-up description), not in
the face text. The test says so explicitly: a test that lets the pair
pass by accident is a test that lets a future "deduplication" pass by
accident too, and that is the kind of regression the rule exists to catch.
"""
from __future__ import annotations

from pathlib import Path

from test_shoot_checks import _node_json

ROOT = Path(__file__).resolve().parents[1]

# The catalogues that carry wording text. `KISS_CAMERA` is now a key map (no
# wordings); the kiss-face text lives in `KISS_FRAMES`. The probe imports the
# full module so the test sees the same shape the running app does.
PROBE = """
import {
  CAMERA_POSITIONS, CANDID_POSITIONS, SELFIE_POSITIONS,
  ARRANGEMENTS, BODY_OPENINGS, TECHNIQUE_DEFECTS,
  KISS_FRAMES, EXPRESSIONS,
  LOOK_INSTRUCTION, LOOK_FROM_PHOTO_INSTRUCTION, LOOK_ONLY_INSTRUCTION,
  WARDROBE_INSTRUCTION, WARDROBE_PROGRESSION_INSTRUCTION,
  REPAIR_INSTRUCTION, BRIEF_INSTRUCTION,
  STAGE_PLAN_INSTRUCTION, SHOOT_LINE_INSTRUCTION,
  EXPLICIT_STRETCH, EXPLICIT_REGISTER,
  ANGLE_FROM_TEXT_INSTRUCTION, EXPRESSION_KEEP, EXPRESSION_KEEP_NO_EYES,
  MANNERS,
} from '%(kinds)s'

// Every (text, concept, catalogue) home. Two homes for the same text in
// different catalogues (e.g. `front-direct` in CAMERA_POSITIONS and
// CANDID_POSITIONS, same key) is the per-manner design and is not a
// violation; what would be a violation is the same text appearing in any of
// the instruction strings below.
const homes = []
for (const [catalogue, list] of [
  ['CAMERA_POSITIONS', CAMERA_POSITIONS],
  ['CANDID_POSITIONS', CANDID_POSITIONS],
  ['SELFIE_POSITIONS', SELFIE_POSITIONS],
  ['ARRANGEMENTS',     ARRANGEMENTS],
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

// One offender per text, regardless of how many catalogues share the text
// (SELFIE_POSITIONS spreads CANDID_POSITIONS, so the same entry shows up
// under both keys). What matters is the text's homes and how often the text
// appears in the prompt system, not the raw catalogue count.
const byText = new Map()
for (const h of homes) {
  if (!byText.has(h.text)) byText.set(h.text, { text: h.text, homes: [] })
  byText.get(h.text).homes.push(`${h.catalogue}.${h.concept}`)
}

// The "prompt system": every instruction string the model actually reads.
// Comments and docstrings are deliberately NOT in this set; the model does
// not read them, so a catalogue text mentioned in a comment is not a second
// home, it is a reference.
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
const promptSystem = instructionStrings.join('\\n')

// A catalogue text appearing anywhere in the prompt system is a second
// home: the reader (the model) sees the copy in the instruction and uses
// that, while the catalogue entry is silently shadowed. One offender per
// text, with the count of prompt-system occurrences and the list of
// catalogue homes for context.
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

// The wink/finger design decision: two concepts, one face text, different
// `hand` values. The text is in the catalogue, not in the prompt system -
// the kiss frame is a concept of its own, and the writer gets the face
// text whole (it is never reworded inline). The two checks below are
// what stops a future edit from silently collapsing the pair.
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
# Removed by task 7.1 (the two inline camera examples in
# `SHOOT_LINE_INSTRUCTION` the proposal names):
#   'Taken from her right side, her body in full profile', 'Taken from
#   directly behind her'
# Real duplicates 7.1 does not touch - the camera and `mirror-selfie`
# examples in the candid manner's `line` (inherited by selfie), the body
# openings enumerated in the `her` field description, and the two technique
# defects used as examples in candid's `line`.
# Not a duplicate at all: 'her feet' is two ordinary words, and the probe
# matches on substring, so it also hits prose that merely says her feet.
# Removing it from this list means rewording English that has nothing to do
# with the rule.
KNOWN_DUPLICATES = [
    'Taken from directly in front of her',
    'Taken from her right side, her body in full profile',
    'Taken from directly behind her',
    'Overhead camera directly above her',
    'Mirror selfie, the phone up in her right hand and visible in the mirror',
    'her chest and torso',
    'her hips and legs',
    'her feet',
    'the colour washed out of her skin',
    'her shoulders running a few degrees off level in the frame',
]


def _run(tmp_path_factory):
    return _node_json(PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                      tmp_path_factory.mktemp("onehome"))


def test_a_components_wording_text_has_exactly_one_home(tmp_path_factory):
    """A catalogue wording's text lives once, in the catalogue, and nowhere
    else in the prompt system. A second copy in an instruction is what makes
    a catalogue entry have no effect: whichever copy the reader meets first
    is the one that decides the photograph, and the two drift apart without
    anything failing.

    The rule is not met today, so the assertion is against the baseline
    below rather than against the empty list. A brand new duplicate fails
    the moment it is written; the ones already in the tree are listed, each
    with what removes it. Asserting the empty list instead would leave the
    suite red for the whole change and catch nothing new while it was.
    """
    out = _run(tmp_path_factory)
    assert [o["text"] for o in out["offenders"]] == KNOWN_DUPLICATES, out


def test_wink_and_finger_are_an_allowed_pair_with_shared_text(tmp_path_factory):
    """`wink` and `finger` share their wording text by design.

    The two concepts differ in the concept-level `hand` attribute (`wink`
    has none; `finger` adds the middle-finger-up description) but the kiss
    face is one photograph and rewriting either copy of the text would be
    rewriting the same face. The shared text is in the catalogue, not in
    the prompt system, so the single-home check above does not flag it.

    Saying so explicitly here is what stops a future edit from
    "deduplicating" the two into one concept (silently dropping the hand
    distinction) or splitting them into two wordings (doubling the cell
    count for the same face). Either of those would pass a test that
    allowed it to pass by accident.
    """
    out = _run(tmp_path_factory)
    assert out["winkFingerShareText"] is True, out
    assert out["winkFingerDifferInHand"] is True, out
