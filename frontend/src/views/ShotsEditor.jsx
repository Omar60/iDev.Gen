import React from 'react'
import { KINDS } from '../kinds.js'

/** A take of the given kind: an edit kind starts its rows ticked as `ref`,
 *  because a session whose whole point is editing a photo asking you to tick
 *  every row is the app making you repeat yourself. */
export const blankShot = (kind) => ({
  label: '', prompt: '', negative: '', count: KINDS[kind]?.refDefault ? 1 : 4, seed: 0,
  reference: !!KINDS[kind]?.refDefault, reference_strength: null,
})

/** The takes of a session: pose, angle, framing — never wardrobe, which belongs
 *  to the session's look and stays identical in every frame. Shared by session
 *  creation and the "add shots" panel. The kind only chooses the guidance and
 *  the defaults: every row keeps its own `ref` box, so nothing is locked. */
export default function ShotsEditor({ shots, onChange, kind }) {
  const set = (i, k, v) => onChange(shots.map((s, j) => (j === i ? { ...s, [k]: v } : s)))
  const total = shots.reduce((n, s) => n + (s.prompt.trim() ? Math.max(1, s.count) : 0), 0)
  const spec = KINDS[kind] || KINDS.shoot

  // A row that opted out of the kind's default is the other kind of take, and
  // the two want opposite prompts: a description or an instruction.
  const placeholder = (shot, i) => {
    const list = shot.reference === !!spec.refDefault ? spec.examples
      : (shot.reference ? KINDS.edit.examples : KINDS.shoot.examples)
    return list[i % list.length]
  }

  return (
    <>
      <table className="looks-table">
        <tbody>
          {shots.map((shot, i) => (
            <tr key={i}>
              <td className="lbl">
                <input value={shot.label} placeholder={`shot ${i + 1}`}
                       onChange={(e) => set(i, 'label', e.target.value)} />
              </td>
              <td>
                <textarea value={shot.prompt} rows={2}
                          placeholder={placeholder(shot, i)}
                          onChange={(e) => set(i, 'prompt', e.target.value)} />
              </td>
              <td className="n">
                <label className="chk" title="Edit the session's reference photo instead of shooting from scratch. The prompt is sent as an instruction, on its own — no trigger, no base prompt, no look.">
                  <input type="checkbox" checked={!!shot.reference}
                         onChange={(e) => set(i, 'reference', e.target.checked)} />
                  ref
                </label>
              </td>
              <td className="n">
                {/* Only a reference take has anything to be pulled towards. */}
                {shot.reference && (
                  <input type="number" step="0.1" min="0" placeholder="str"
                         value={shot.reference_strength ?? ''}
                         title="Reference strength. Empty follows the session. High holds the frame still so a garment edit lands cleanly; low lets the pose move. Shoot the same prompt and seed at a few values to find yours."
                         onChange={(e) => set(i, 'reference_strength',
                                              e.target.value === '' ? null : parseFloat(e.target.value))} />
                )}
              </td>
              <td className="n">
                <input type="number" min="1" value={shot.count} title="Variations"
                       onChange={(e) => set(i, 'count', Number(e.target.value))} />
              </td>
              <td className="n">
                <input type="number" min="0" value={shot.seed || ''} placeholder="seed"
                       title="Seed. Empty follows the session; set it to compare a prompt change on the same noise."
                       onChange={(e) => set(i, 'seed', Number(e.target.value))} />
              </td>
              <td style={{ width: 34 }}>
                <button className="icon danger" title="Remove"
                        onClick={() => onChange(shots.filter((_, j) => j !== i))}>×</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row" style={{ marginTop: 8 }}>
        <button onClick={() => onChange([...shots, blankShot(kind)])}>+ Shot</button>
        <span className="muted">
          {total} photos · {spec.footer}
          {!spec.refDefault && (
            <> Tick <b>ref</b> on a take to edit the session's reference photo instead: the prompt
            goes out on its own, as an instruction, which is the only way to take something off
            the look.</>
          )}
        </span>
      </div>
    </>
  )
}
