import { positionsFor, arrangements, framings } from './kinds.js'

/** Resolve the forced-choice list for a given slot and session manner.
 *
 *  The catalogue lives in the component store. Labels are viewer descriptions
 *  (judge_label), NEVER prompt wordings.
 *
 *  Every non-empty choice list ends with the explicit "None or cannot tell"
 *  answer (key: '').
 */
export function slotChoices(slot, manner = 'directed') {
  let list = []
  if (slot === 'camera') {
    list = positionsFor(manner)
  } else if (slot === 'act') {
    list = arrangements(manner)
  } else if (slot === 'framing') {
    list = framings(manner)
  }

  if (list.length === 0) return []

  return [
    ...list.map((c) => ({
      key: c.key,
      label: c.judge_label || c.key,
      text: c.judge_label || c.key,
    })),
    { key: '', label: 'None or cannot tell', text: 'None of the above or cannot tell' },
  ]
}

/** Build the judging pass deck from unjudged shots and control shots.
 *
 *  The deck carries { shot_id, isControl: boolean } items. Shuffled with
 *  `rand` so controls are interspersed unpredictably without on-screen markers.
 */
export function buildJudgeDeck(shots = [], controls = [], rand = Math.random) {
  const regular = shots.map((id) => ({ shot_id: id, isControl: false }))
  if (!controls || controls.length === 0) return regular

  const ctrlItems = controls.map((id) => ({ shot_id: id, isControl: true }))
  const combined = [...regular, ...ctrlItems]

  for (let i = combined.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1))
    const tmp = combined[i]
    combined[i] = combined[j]
    combined[j] = tmp
  }
  return combined
}

/** Compute the agreement rate and list disagreements from judging results.
 *
 *  `results` contains entries from each answered photograph. Control items
 *  carry `{ control: true, agreed: boolean, stored: string, answered: string }`.
 */
export function computeAgreement(results = []) {
  const controls = results.filter((r) => r.control)
  const totalControls = controls.length
  const agreedCount = controls.filter((r) => r.agreed).length
  const disagreedCount = totalControls - agreedCount
  const rate = totalControls > 0 ? (agreedCount / totalControls) : null
  const disagreements = controls
    .filter((r) => !r.agreed)
    .map((r) => ({
      shot_id: r.shot_id,
      stored: r.stored,
      answered: r.answered,
    }))

  return {
    totalControls,
    agreedCount,
    disagreedCount,
    rate,
    disagreements,
  }
}
