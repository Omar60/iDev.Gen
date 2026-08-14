import React, { useState } from 'react'
import { KINDS } from '../kinds.js'
import { guideFor, rewriteTake, takesFromBrief, lookFromBrief } from '../enhance.js'

/** A take of the given kind: an edit kind starts its rows ticked as `ref`,
 *  because a session whose whole point is editing a photo asking you to tick
 *  every row is the app making you repeat yourself. */
export const blankShot = (kind) => ({
  label: '', prompt: '', negative: '', count: KINDS[kind]?.refDefault ? 1 : 4, seed: 0,
  reference: !!KINDS[kind]?.refDefault, reference_strength: null,
})

const BRIEF_TAKES = 4

/** The takes of a session: pose, angle, framing — never wardrobe, which belongs
 *  to the session's look and stays identical in every frame. Shared by session
 *  creation and the "add shots" panel. The kind only chooses the guidance and
 *  the defaults: every row keeps its own `ref` box, so nothing is locked.
 *
 *  With a prompt assistant configured it also writes: a brief fills the panel,
 *  and ✨ rewrites one row. Both are suggestions — text in a box, editable, and
 *  nothing is queued until Run. `context` and `look` are what the server already
 *  prepends, sent so the assistant knows what *not* to repeat.
 */
export default function ShotsEditor({ shots, onChange, kind, llm = false,
                                      context = '', look = '', onLook = null }) {
  const set = (i, k, v) => onChange(shots.map((s, j) => (j === i ? { ...s, [k]: v } : s)))
  const total = shots.reduce((n, s) => n + (s.prompt.trim() ? Math.max(1, s.count) : 0), 0)
  const spec = KINDS[kind] || KINDS.shoot

  const [brief, setBrief] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  // What each row said before ✨ touched it. A rewrite that cannot be undone is a
  // rewrite you have to think about before clicking.
  const [undo, setUndo] = useState({})

  // A row that opted out of the kind's default is the other kind of take, and
  // the two want opposite prompts: a description or an instruction.
  const placeholder = (shot, i) => {
    const list = shot.reference === !!spec.refDefault ? spec.examples
      : (shot.reference ? KINDS.edit.examples : KINDS.shoot.examples)
    return list[i % list.length]
  }

  const run = async (what, fn) => {
    setBusy(what); setError('')
    try { await fn() } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  // Trigger, base prompt and look: what the server prepends, so what the take
  // itself must not say again.
  const already = (theLook) => [context, theLook].map((x) => (x || '').trim()).filter(Boolean).join(', ')

  const rewrite = (i) => run(`row${i}`, async () => {
    const shot = shots[i]
    const text = await rewriteTake(kind, shot.reference, shot.prompt, already(look))
    if (!text) throw new Error('the assistant answered nothing usable')
    setUndo({ ...undo, [i]: shot.prompt })
    set(i, 'prompt', text)
  })

  // The look first, then the takes with that look as context: the second call is
  // the one that must not repeat the wardrobe, so it has to know it.
  //
  // Only when the box is empty. A look already there was decided — typed, or read
  // off a photo — and overwriting it is the worst thing this button could do:
  // the session then shoots a wardrobe nobody chose, and the photo you picked it
  // from is nowhere in it. In the add-shots panel there is no look to write at
  // all; it belongs to the session.
  const fromBrief = () => run('brief', async () => {
    let ctx = already(look)
    if (onLook && !look.trim()) {
      const written = await lookFromBrief(brief)
      if (written) { onLook(written); ctx = already(written) }
    }
    const lines = await takesFromBrief(kind, !!spec.refDefault, brief, ctx, BRIEF_TAKES)
    if (!lines.length) throw new Error('the assistant answered nothing usable')
    onChange([
      ...shots.filter((s) => s.prompt.trim()),
      ...lines.map((l) => ({ ...blankShot(kind), label: l.label, prompt: l.prompt })),
    ])
  })

  return (
    <>
      {llm && guideFor(kind, !!spec.refDefault) && (
        <div className="row" style={{ marginBottom: 8 }}>
          <textarea rows={2} value={brief} disabled={!!busy}
                    placeholder={onLook
                      ? 'Describe the session: “a rooftop at sunset, streetwear, standing, sitting and walking”'
                      : 'Describe what to shoot next and it writes the takes'}
                    onChange={(e) => setBrief(e.target.value)} />
          <button className="primary" style={{ whiteSpace: 'nowrap' }}
                  disabled={!brief.trim() || !!busy} onClick={fromBrief}>
            {busy === 'brief' ? '…' : `✨ Write ${BRIEF_TAKES} takes`}
          </button>
          {/* Which of the two it is about to do, before it does it. */}
          <span className="muted">
            {onLook && !look.trim()
              ? 'writes the look too, since it is still empty'
              : 'takes only — the look above is kept as it is'}
          </span>
        </div>
      )}
      {error && <div className="error" onClick={() => setError('')}>{error}</div>}
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
              {llm && (
                <td className="n">
                  {guideFor(kind, shot.reference) && (
                    undo[i] !== undefined && undo[i] !== shot.prompt
                      ? <button className="icon" title="Put back what this take said before"
                                onClick={() => { set(i, 'prompt', undo[i]); setUndo({ ...undo, [i]: undefined }) }}>↩</button>
                      : <button className="icon" disabled={!shot.prompt.trim() || !!busy}
                                title={shot.reference
                                  ? 'Rewrite as an instruction on the reference photo'
                                  : 'Rewrite this take — the look is not repeated in it'}
                                onClick={() => rewrite(i)}>{busy === `row${i}` ? '…' : '✨'}</button>
                  )}
                </td>
              )}
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
