import React from 'react'

export const blankShot = () => ({ label: '', prompt: '', negative: '', count: 4, seed: 0 })

const EXAMPLES = [
  'three-quarter view, hands in pockets, looking away',
  'close-up, chin slightly down, eyes to camera',
  'full body, walking, mid-stride',
  'sitting by the window, leaning on one arm',
]

/** The takes of a session: pose, angle, framing — never wardrobe, which belongs
 *  to the session's look and stays identical in every frame. Shared by session
 *  creation and the "add shots" panel. */
export default function ShotsEditor({ shots, onChange }) {
  const set = (i, k, v) => onChange(shots.map((s, j) => (j === i ? { ...s, [k]: v } : s)))
  const total = shots.reduce((n, s) => n + (s.prompt.trim() ? Math.max(1, s.count) : 0), 0)

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
                <textarea value={shot.prompt} rows={2} placeholder={EXAMPLES[i % EXAMPLES.length]}
                          onChange={(e) => set(i, 'prompt', e.target.value)} />
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
        <button onClick={() => onChange([...shots, blankShot()])}>+ Shot</button>
        <span className="muted">
          {total} photos · pose, angle, place — the trigger, base prompt and the session's look
          are prepended automatically. Leave the seed empty unless you are comparing a change.
        </span>
      </div>
    </>
  )
}
