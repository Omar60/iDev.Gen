"""The canvas menu: five shooting canvases, and what keeps them shootable.

A preset list is data, and the way data goes wrong is that somebody adds the
size a platform asked for. Instagram's 1080x1350 and Facebook's 820x312 are
delivery sizes — a crop of a finished photograph — and putting either on the
canvas costs a re-shoot for the first and a broken frame for the second.
These are the properties that would have caught that.
"""
from __future__ import annotations

import json
from pathlib import Path

from test_shoot_checks import _node_json

ROOT = Path(__file__).resolve().parents[1]

PROBE = """
import { CANVAS_PRESETS, presetKey } from '%(canvas)s'

const ratio = (p) => Math.max(p.width, p.height) / Math.min(p.width, p.height)

console.log(JSON.stringify({
  keys: CANVAS_PRESETS.map((p) => p.key),
  // ComfyUI's latent is built in multiples of 8; the samplers here want 16.
  stepped: CANVAS_PRESETS.every((p) => p.width %% 16 === 0 && p.height %% 16 === 0),
  // Every canvas is about the megapixel the model paints at. A delivery size
  // slipped in here would show up as one that is not.
  pixels: CANVAS_PRESETS.map((p) => p.width * p.height),
  // Nothing wider than 16:9 either way. Past that the whole body is painted
  // twice, and that is the entry this list exists to keep out.
  widest: Math.max(...CANVAS_PRESETS.map(ratio)),
  // The canvas every session so far was shot on is still reachable by name.
  roundTrip: CANVAS_PRESETS.every((p) => presetKey(p.width, p.height) === p.key),
  default: presetKey(832, 1216),
  // The width and height boxes stay, so an off-list canvas is not an error.
  custom: presetKey(1080, 1350),
}))
"""


def test_the_canvas_presets_are_all_shootable(tmp_path_factory):
    out = _node_json(PROBE % {"canvas": (ROOT / "frontend/src/canvas.js").as_posix()},
                     tmp_path_factory.mktemp("canvas"))

    assert out["stepped"], out
    assert all(0.85e6 <= n <= 1.15e6 for n in out["pixels"]), out["pixels"]
    assert out["widest"] <= 16 / 9 + 1e-9, out["widest"]
    assert len(set(out["keys"])) == len(out["keys"]), out["keys"]


def test_the_measured_canvas_survives_the_menu(tmp_path_factory):
    """832x1216 is not a true 2:3, and rounding it to one would strand every
    session in the notebook on a canvas the app no longer offers."""
    out = _node_json(PROBE % {"canvas": (ROOT / "frontend/src/canvas.js").as_posix()},
                     tmp_path_factory.mktemp("canvas"))

    assert out["default"] != "", out
    assert out["roundTrip"], out
    assert out["custom"] == "", out
