import React from 'react'
import { CANVAS_PRESETS, presetKey } from '../canvas.js'

/** The canvas a session is painted on: a menu of five, plus the two boxes.
 *
 *  The menu is ergonomics and nothing more. A taller canvas does not lower the
 *  crop — it adds ceiling, measured at 0 of 4 full-length frames — so picking a
 *  ratio here changes the shape of the photograph and not what is in it. That
 *  is measured for the vertical ratios only; square and wide are the same
 *  reasoning and no measurement, which is why the default is untouched.
 *
 *  832x1216 stays the default and is a preset of its own. Every session shot so
 *  far is on it, and quietly rounding it to a true 2:3 would leave the notebook
 *  describing a canvas the app no longer offers.
 */
export default function CanvasSize({ width, height, onChange }) {
  const w = width ?? 832
  const h = height ?? 1216

  const pick = (key) => {
    const p = CANVAS_PRESETS.find((x) => x.key === key)
    if (p) onChange(p.width, p.height)
  }

  return (
    <>
      <div>
        <label title="Delivery sizes are a crop of these, not a canvas of their own: shooting wider than 16:9 with a whole body in frame paints two of her.">Canvas</label>
        <select value={presetKey(w, h)} onChange={(e) => pick(e.target.value)}>
          <option value="">Custom</option>
          {CANVAS_PRESETS.map((p) => (
            <option key={p.key} value={p.key} title={`Delivers ${p.delivers}`}>
              {p.label} — {p.width}x{p.height}
            </option>
          ))}
        </select>
      </div>
      <div><label>Width</label><input type="number" step="8" value={w}
        onChange={(e) => onChange(Number(e.target.value), h)} /></div>
      <div><label>Height</label><input type="number" step="8" value={h}
        onChange={(e) => onChange(w, Number(e.target.value))} /></div>
    </>
  )
}
