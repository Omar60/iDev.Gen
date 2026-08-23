"""The kiss frame: one photograph a shoot that is written differently from the rest.

It exists because the user chased this photograph for several sessions and never
got it out of a shoot - a kiss blown at the camera with the eyes shut - and got
it in one try from a prompt written by hand. What that prompt did and a shoot
line does not: it named the kiss and the eyes as one gesture in one clause with
the eyes stated flatly, and it put the camera close and in front. So the wording
is handed to the writer whole, and the three properties below are what stops it
from quietly becoming a suggestion again.
"""
from __future__ import annotations

from pathlib import Path

from test_shoot_checks import _node_json

ROOT = Path(__file__).resolve().parents[1]

PROBE = """
import { kissPlan, KISS_FRAMES, KISS_CAMERA, shootChunkNote } from '%(kinds)s'

// Every draw of the shoot lengths the app actually offers, so a property holds
// for a run of them and not for one lucky one.
const lengths = [1, 4, 8, 12, 16, 24, 32, 45]
const runs = lengths.flatMap((n) =>
  Array.from({ length: 50 }, () => ({ n, plan: kissPlan(n) })))

const inRange = runs.every(({ n, plan }) =>
  Object.keys(plan).every((at) => Number(at) >= 1 && Number(at) <= n))
const always = runs.every(({ plan }) => Object.keys(plan).length >= 1)
const capped = Math.max(...runs.map(({ plan }) => Object.keys(plan).length))
// Two kiss frames running would be the same face twice in a row, which is the
// failure the spread exists to avoid.
const adjacent = runs.filter(({ plan }) => {
  const at = Object.keys(plan).map(Number).sort((a, b) => a - b)
  return at.some((k, i) => i > 0 && k - at[i - 1] < 2)
}).length
const flavours = new Set(runs.flatMap(({ plan }) => Object.values(plan).map((f) => f.key)))

// The note for a chunk that contains one, and for a chunk that does not.
const note = shootChunkNote({
  from: 1, want: 4, total: 8, cameras: ['Taken from directly behind her'],
  kisses: [{ at: 2, frame: KISS_FRAMES[3], camera: KISS_CAMERA.candid }],
})
const quiet = shootChunkNote({ from: 5, want: 4, total: 8, cameras: [], kisses: [] })

console.log(JSON.stringify({
  inRange, always, capped, adjacent,
  flavours: [...flavours].sort(),
  keys: KISS_FRAMES.map((f) => f.key),
  eyesShut: KISS_FRAMES[0].face.includes('HER EYES ARE COMPLETELY CLOSED'),
  hands: KISS_FRAMES.filter((f) => f.hand).length,
  noteNames: note.includes('PHOTOGRAPH 2 IS A KISS FRAME'),
  noteCamera: note.includes(KISS_CAMERA.candid),
  noteHand: note.includes('middle finger up'),
  quietIsQuiet: !quiet.includes('KISS FRAME'),
}))
"""


def test_every_shoot_gets_a_kiss_frame(tmp_path_factory):
    out = _node_json(PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                     tmp_path_factory.mktemp("kiss"))

    # The rule the user asked for: always, in every shoot, however short.
    assert out["always"] is True, out
    assert out["inRange"] is True, out

    # And no more than the four flavours, so a long shoot varies instead of
    # repeating one face.
    assert out["capped"] == 4, out
    assert out["adjacent"] == 0, out
    assert out["keys"] == ["closed", "wink", "open", "finger"], out
    assert out["flavours"] == ["closed", "finger", "open", "wink"], out

    # The eyes are the whole reason this exists and they are stated flatly.
    assert out["eyesShut"] is True, out
    # Exactly one flavour puts a hand in the frame.
    assert out["hands"] == 1, out


def test_the_chunk_note_hands_the_frame_over_whole(tmp_path_factory):
    out = _node_json(PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                     tmp_path_factory.mktemp("kissnote"))

    # Named by photograph number, with its own camera overriding the plan's, and
    # the hand carried into `act` when the flavour has one.
    assert out["noteNames"] is True, out
    assert out["noteCamera"] is True, out
    assert out["noteHand"] is True, out

    # A chunk with no kiss frame in it says nothing about kisses at all.
    assert out["quietIsQuiet"] is True, out


def test_the_manner_that_forbids_eye_contact_makes_room_for_it():
    """`candid` says her eyes are never on the lens, and two of the four flavours
    put them there. Two blocks contradicting each other is how a rule dies in
    this project, so the exemption is written into the rule itself."""
    kinds = (ROOT / "frontend/src/kinds.js").read_text(encoding="utf-8")
    assert "named below as a kiss frame" in kinds

    # And the plan reaches the writer: the chunk carries it beside the cameras.
    enhance = (ROOT / "frontend/src/enhance.js").read_text(encoding="utf-8")
    assert "kissPlan(n)" in enhance
    assert "kisses: Object.entries(kisses)" in enhance
