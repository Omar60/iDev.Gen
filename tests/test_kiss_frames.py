"""The kiss frame: one photograph a shoot that is written differently from the rest.
"""
from __future__ import annotations

from pathlib import Path

from test_shoot_checks import _node_json

ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = (ROOT / "data" / "catalogue-seed.json").as_posix()

PROBE = """
import fs from 'fs'
import { kissPlan, KISS_FRAMES, KISS_CAMERA, kissCameraFor, setCatalogue, positionsFor, MANNERS,
         shootChunkNote } from '%(kinds)s'

const seed = JSON.parse(fs.readFileSync('%(seed)s', 'utf-8'))
setCatalogue(seed)

const lengths = [1, 4, 8, 12, 16, 24, 32, 45]
const runs = lengths.flatMap((n) =>
  Array.from({ length: 50 }, () => ({ n, plan: kissPlan(n) })))

const inRange = runs.every(({ n, plan }) =>
  Object.keys(plan).every((at) => Number(at) >= 1 && Number(at) <= n))
const always = runs.every(({ plan }) => Object.keys(plan).length >= 1)
const capped = Math.max(...runs.map(({ plan }) => Object.keys(plan).length))

const adjacent = runs.filter(({ plan }) => {
  const at = Object.keys(plan).map(Number).sort((a, b) => a - b)
  return at.some((k, i) => i > 0 && k - at[i - 1] < 2)
}).length
const flavours = new Set(runs.flatMap(({ plan }) => Object.values(plan).map((f) => f.key)))

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
  kissKeysResolve: Object.entries(KISS_CAMERA).every(
    ([manner, key]) => positionsFor(manner).some((p) => p.key === key)),
  kissCoversManners: MANNERS.every((m) => m.key in KISS_CAMERA),
  kissOverride: MANNERS.every((m) => kissCameraFor(m.key).override === 'dealt-camera'),
  kissIsAConcept: MANNERS.every((m) => {
    const c = kissCameraFor(m.key)
    return typeof c.key === 'string' && c.slot === 'camera'
           && typeof c.wordings[0].text === 'string'
  }),
  candidText: kissCameraFor('candid').wordings[0].text,
  selfieText: kissCameraFor('selfie').wordings[0].text,
  directedText: kissCameraFor('directed').wordings[0].text,
}))
"""


def test_every_shoot_gets_a_kiss_frame(tmp_path_factory):
    out = _node_json(PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix(), "seed": SEED_PATH},
                     tmp_path_factory.mktemp("kiss"))

    assert out["always"] is True, out
    assert out["inRange"] is True, out
    assert out["capped"] == 4, out
    assert out["adjacent"] == 0, out
    assert out["keys"] == ["closed", "wink", "open", "finger"], out
    assert out["flavours"] == ["closed", "finger", "open", "wink"], out
    assert out["eyesShut"] is True, out
    assert out["hands"] == 1, out


def test_the_chunk_note_hands_the_frame_over_whole(tmp_path_factory):
    out = _node_json(PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix(), "seed": SEED_PATH},
                     tmp_path_factory.mktemp("kissnote"))

    assert out["noteNames"] is True, out
    assert out["noteCamera"] is True, out
    assert out["noteHand"] is True, out
    assert out["quietIsQuiet"] is True, out


def test_the_kiss_camera_is_a_camera_component_with_an_override(tmp_path_factory):
    out = _node_json(PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix(), "seed": SEED_PATH},
                     tmp_path_factory.mktemp("kisscatalogue"))

    assert out["kissKeysResolve"] is True, out
    assert out["kissCoversManners"] is True, out
    assert out["kissOverride"] is True, out
    assert out["kissIsAConcept"] is True, out
    assert out["candidText"] == "Phone held out at arm's length in front of her face", out
    assert out["selfieText"] == "Phone held out at arm's length in front of her face", out
    assert out["directedText"] == "Taken from directly in front of her", out


def test_the_manner_that_forbids_eye_contact_makes_room_for_it():
    kinds = (ROOT / "frontend/src/kinds.js").read_text(encoding="utf-8")
    assert "named below as a kiss frame" in kinds
    enhance = (ROOT / "frontend/src/enhance.js").read_text(encoding="utf-8")
    assert "kissPlan(n)" in enhance
    assert "kisses: Object.entries(kisses)" in enhance
