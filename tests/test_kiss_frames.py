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
import { kissPlan, KISS_FRAMES, KISS_CAMERA, kissCameraFor, POSITIONS, MANNERS,
         shootChunkNote } from '%(kinds)s'

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

// The note for a chunk that contains one, and for a chunk that does not. The
// text comes from `kissCameraFor` now: KISS_CAMERA.candid is a KEY into the
// candid camera catalogue, and the wording is read from the concept there.
const candidKissText = kissCameraFor('candid').wordings[0].text
const note = shootChunkNote({
  from: 1, want: 4, total: 8, cameras: ['Taken from directly behind her'],
  kisses: [{ at: 2, frame: KISS_FRAMES[3], camera: candidKissText }],
})
const quiet = shootChunkNote({ from: 5, want: 4, total: 8, cameras: [], kisses: [] })

console.log(JSON.stringify({
  inRange, always, capped, adjacent,
  flavours: [...flavours].sort(),
  keys: KISS_FRAMES.map((f) => f.key),
  eyesShut: KISS_FRAMES[0].wordings[0].text.includes('HER EYES ARE COMPLETELY CLOSED'),
  hands: KISS_FRAMES.filter((f) => f.hand).length,
  noteNames: note.includes('PHOTOGRAPH 2 IS A KISS FRAME'),
  noteCamera: note.includes(candidKissText),
  noteHand: note.includes('middle finger up'),
  quietIsQuiet: !quiet.includes('KISS FRAME'),
  // --- 1.2: KISS_CAMERA is a map of manner to a key in THAT MANNER'S OWN
  // camera catalogue. The text that used to be here lives there and nowhere
  // else, which is the 1.3 prerequisite.
  //
  // Every key resolves inside its own manner's catalogue. This is the
  // assertion a rename has to trip over: the wording lives in one place, so
  // the only way left to break it is to point at nothing.
  kissKeysResolve: Object.entries(KISS_CAMERA).every(
    ([manner, key]) => POSITIONS[manner].some((p) => p.key === key)),
  // Every manner covered - a manner missing from the map silently falls back
  // to the directed frontal, which is the wrong camera for a phone shoot.
  kissCoversManners: MANNERS.every((m) => m.key in KISS_CAMERA),
  // What makes it a kiss camera and not another pick: it replaces the camera
  // the spread dealt, and the resolved concept says so.
  kissOverride: MANNERS.every((m) => kissCameraFor(m.key).override === 'dealt-camera'),
  // It is a camera concept, in the ordinary 1.1 shape - not a second kind of
  // entry with a pointer where its wording should be.
  kissIsAConcept: MANNERS.every((m) => {
    const c = kissCameraFor(m.key)
    return typeof c.key === 'string' && c.slot === 'camera'
           && typeof c.wordings[0].text === 'string'
  }),
  // The text is the same as before: candid reaches the candid form, directed
  // the directed one. These are the strings that USED to be hard-coded in the
  // per-manner map.
  candidText: kissCameraFor('candid').wordings[0].text,
  selfieText: kissCameraFor('selfie').wordings[0].text,
  directedText: kissCameraFor('directed').wordings[0].text,
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


def test_the_kiss_camera_is_a_camera_component_with_an_override(tmp_path_factory):
    """Task 1.2: KISS_CAMERA used to be a 3-entry map of plain strings, and each
    of its three values was a third copy of text that already lived in
    CAMERA_POSITIONS or CANDID_POSITIONS - the defect 1.3 is written to catch.

    The fix is that a kiss camera IS a camera component: the map holds a key
    into that manner's own catalogue, and what makes it a kiss camera rather
    than another pick is the `override` tag saying it replaces the camera the
    spread dealt. One concept shape in the catalogue, not a second kind of
    entry with a pointer where its wording should be."""
    out = _node_json(PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                     tmp_path_factory.mktemp("kisscatalogue"))

    # The assertion a rename trips over. With the text in one place, pointing
    # at nothing is the only failure this design still has.
    assert out["kissKeysResolve"] is True, out
    assert out["kissCoversManners"] is True, out
    # It replaces the dealt camera, and the resolved concept records that.
    assert out["kissOverride"] is True, out
    # And it is an ordinary camera concept in the 1.1 shape.
    assert out["kissIsAConcept"] is True, out
    # The text is the same three strings the old map carried, read from the
    # catalogue. This is what "no prompt text changed" means for 1.2.
    assert out["candidText"] == "Phone held out at arm's length in front of her face", out
    assert out["selfieText"] == "Phone held out at arm's length in front of her face", out
    assert out["directedText"] == "Taken from directly in front of her", out


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
