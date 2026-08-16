import React, { useState } from 'react'
import { EXPRESSIONS, expressionTake } from '../kinds.js'

/** The expressions of an edit session, clicked rather than written.
 *
 *  Same shape as the angle picker one level over, and for a related reason: the
 *  wording is not a preference, it is the measured part. An expression asked for
 *  in a take comes back trading one feature for another — the mouth opens and the
 *  wink goes — where the same words as an *edit* on a finished photograph move
 *  the mouth and leave the rest of the frame alone. Typing them again per session
 *  is retyping the measurement.
 *
 *  Every take is a reference take: it edits the anchor, carries no trigger, no
 *  base prompt and no look, and goes out exactly as written.
 */
export default function ExpressionPicker({ onAdd }) {
  const [picked, setPicked] = useState([])

  const toggle = (s) => setPicked(
    picked.includes(s) ? picked.filter((x) => x !== s) : [...picked, s])

  const add = () => onAdd(EXPRESSIONS.filter((e) => picked.includes(e.s)).map((e) => ({
    label: e.s, prompt: expressionTake(e),
    negative: '', count: 1, seed: 0,
    reference: true, reference_strength: null,
  })))

  return (
    <div className="panel" style={{ background: 'var(--panel-2)', marginBottom: 10 }}>
      {/* The rule that decides whether any of this works, printed where the
          decision is made — the same place the angles kind prints its own. */}
      <p className="rule" style={{ marginTop: 0 }}>
        <b>Anchor on a photo where her face fills the frame.</b> An expression is a few
        hundred pixels of mouth: on a full-length photograph there is nothing there for the
        edit to move, and it returns the frame unchanged. Measured — ten presets on a
        full-length anchor came back identical to it, all but the wink, and the same ten on
        a head-and-shoulders anchor moved the mouth on the first try.
      </p>
      <div className="row" style={{ marginBottom: 6 }}>
        <label style={{ width: 70, margin: 0 }}>Expression</label>
        {EXPRESSIONS.map((e) => (
          <button key={e.s} className={'chip' + (picked.includes(e.s) ? ' on' : '')}
                  title={expressionTake(e) + (e.note ? ` — ${e.note}` : '')
                         + (e.measured ? '' : ' — written to the same shape as the four that '
                                              + 'were measured, but not measured itself')}
                  onClick={() => toggle(e.s)}>{e.s}{e.measured ? '' : ' ·'}</button>
        ))}
      </div>
      <div className="row">
        <button className="primary" disabled={!picked.length} onClick={add}>
          Add {picked.length} take{picked.length === 1 ? '' : 's'}
        </button>
        <span className="muted">
          one edit of the reference photo each — the strength box is not the dial between
          them, the wording is, so shoot the ones you are choosing between side by side.
          A chip marked <b>·</b> has not been measured on this model yet.
        </span>
      </div>
    </div>
  )
}
