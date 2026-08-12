import React, { useState } from 'react'
import { ANGLE_AXES } from '../kinds.js'

const DEFAULT_SELECTION = {
  direction: ['front view'], height: ['eye-level shot'], size: ['medium shot'],
}

/** Builds the takes of a camera-angle session.
 *
 *  The LoRA reads a short camera line in a closed vocabulary — direction,
 *  height, framing — and ignores every word outside it. Typed by hand that is
 *  fourteen near-identical lines and one typo away from a take that silently
 *  did nothing, so the vocabulary is chips and the app writes the lines.
 */
export default function AnglePicker({ onAdd }) {
  // Empty by default and never prefilled with the character's trigger: this is
  // the angle LoRA's own token, and a reference take carries no trigger — the
  // photo it edits already shows the character.
  const [token, setToken] = useState('')
  const [sel, setSel] = useState(DEFAULT_SELECTION)

  const toggle = (key, v) => setSel({
    ...sel,
    [key]: sel[key].includes(v) ? sel[key].filter((x) => x !== v) : [...sel[key], v],
  })

  const short = (v) => ANGLE_AXES.flatMap((a) => a.chips).find((c) => c.v === v)?.s || v

  // Direction, height, framing — the order the vocabulary is written in.
  const takes = sel.direction.flatMap((d) => sel.height.flatMap((h) => sel.size.map((z) => [d, h, z])))

  const add = () => onAdd(takes.map(([d, h, z]) => ({
    label: [short(d), short(h), short(z)].join(' · '),
    prompt: [token.trim(), d, h, z].filter(Boolean).join(' '),
    negative: '', count: 1, seed: 0,
    // An angle take is a reference take like any other: it edits the anchor and
    // carries no look, so it must never be composed.
    reference: true, reference_strength: null,
  })))

  return (
    <div className="panel" style={{ background: 'var(--panel-2)', marginBottom: 10 }}>
      {ANGLE_AXES.map((axis) => (
        <div className="row" key={axis.key} style={{ marginBottom: 6 }}>
          <label style={{ width: 70, margin: 0 }}>{axis.label}</label>
          {axis.chips.map((c) => (
            <button key={c.v} className={'chip' + (sel[axis.key].includes(c.v) ? ' on' : '')}
                    title={c.v} onClick={() => toggle(axis.key, c.v)}>{c.s}</button>
          ))}
        </div>
      ))}
      <div className="row" style={{ marginTop: 10 }}>
        <label style={{ width: 70, margin: 0 }} title="The angle LoRA's own trigger token, if it has one. Not the character's: the photo being turned already shows the character, which is why a reference take carries no trigger.">
          Token
        </label>
        <input style={{ width: 140 }} value={token} placeholder="none"
               onChange={(e) => setToken(e.target.value)} />
        <button className="primary" disabled={!takes.length} onClick={add}>
          Add {takes.length} take{takes.length === 1 ? '' : 's'}
        </button>
        <span className="muted">
          one take per combination, one photo each — the same angle twice is the same photo twice
        </span>
      </div>
    </div>
  )
}
