import React, { useState } from 'react'
import { ANGLE_AXES } from '../kinds.js'
import { anglesFromText } from '../enhance.js'

const VOCABULARY = ANGLE_AXES.flatMap((a) => a.chips.map((c) => c.v))

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
export default function AnglePicker({ onAdd, llm = false }) {
  // Empty by default and never prefilled with the character's trigger: this is
  // the angle LoRA's own token, and a reference take carries no trigger — the
  // photo it edits already shows the character.
  const [token, setToken] = useState('')
  const [sel, setSel] = useState(DEFAULT_SELECTION)
  const [ask, setAsk] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  /** Free text picks chips, it never becomes a take: the LoRA reads this
   *  vocabulary and drops everything else, and prose it dropped looks exactly
   *  like prose it read. The server clamps the answer to the list below, and
   *  what survives is ticked here. */
  const fromText = async () => {
    setBusy(true); setError('')
    try {
      const line = await anglesFromText(ask, VOCABULARY)
      const picked = {}
      for (const axis of ANGLE_AXES) {
        const hits = axis.chips.filter((c) => line.includes(c.v)).map((c) => c.v)
        if (hits.length) picked[axis.key] = hits
      }
      if (!Object.keys(picked).length) throw new Error('nothing in the camera vocabulary matched that')
      setSel({ ...sel, ...picked })
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

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
      {llm && (
        <div className="row" style={{ marginBottom: 8 }}>
          <input value={ask} disabled={busy} placeholder="ask for an angle: “from behind, a bit lower”"
                 onChange={(e) => setAsk(e.target.value)} />
          <button disabled={!ask.trim() || busy} onClick={fromText}
                  title="Pick the chips that match — the vocabulary is closed, so this ticks boxes rather than writing a take">
            {busy ? '…' : '✨ Pick'}
          </button>
        </div>
      )}
      {error && <div className="error" onClick={() => setError('')}>{error}</div>}
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
