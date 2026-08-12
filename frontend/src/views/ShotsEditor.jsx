import React from 'react'

export const blankShot = () => ({
  label: '', prompt: '', negative: '', count: 4, seed: 0,
  reference: false, reference_strength: null,
})

const EXAMPLES = [
  'three-quarter view, hands in pockets, looking away',
  'close-up, chin slightly down, eyes to camera',
  'full body, walking, mid-stride',
  'sitting by the window, leaning on one arm',
]

// A reference take is an instruction on the photo, not a description of it.
const REF_EXAMPLES = [
  'remove the jacket, same pose',
  'let the hair down',
  'turn to a three-quarter view',
  'change the background to a plain grey studio wall',
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
                <textarea value={shot.prompt} rows={2}
                          placeholder={(shot.reference ? REF_EXAMPLES : EXAMPLES)[i % EXAMPLES.length]}
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
        <button onClick={() => onChange([...shots, blankShot()])}>+ Shot</button>
        <span className="muted">
          {total} photos · pose, angle, place — the trigger, base prompt and the session's look
          are prepended automatically. Leave the seed empty unless you are comparing a change.
          {' '}Tick <b>ref</b> to edit the session's reference photo instead: the prompt goes out
          on its own, as an instruction, which is the only way to take something off the look.
          A ref take also gets a strength box — one dial, pulling between “hold the frame” and
          “let the pose move”. Same prompt and seed at a few values is how you find it.
        </span>
      </div>
    </>
  )
}
