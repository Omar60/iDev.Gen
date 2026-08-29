import { positionsFor, arrangements, framings } from './kinds.js'

/** Resolve the forced-choice list for a given slot and session manner.
 *
 *  The catalogue lives in the component store. Labels are viewer descriptions
 *  (judge_label), NEVER prompt wordings.
 *
 *  Every non-empty choice list ends with the explicit "None or cannot tell"
 *  answer (key: '').
 */
export function slotChoices(slot, manner = 'directed', families = null) {
  let list = []
  if (slot === 'camera') {
    list = positionsFor(manner)
  } else if (slot === 'act') {
    list = arrangements(manner)
  } else if (slot === 'framing') {
    list = framings(manner)
  }

  // `families`, when the pass supplies it, is the families actually
  // photographed in the deck. Without it the choices are the whole catalogue
  // slice for the manner — so a catalogue that grew after a shoot puts
  // families in the question the shoot never photographed, and the judge is
  // asked to tell apart things that are not in front of them.
  if (families && families.length) {
    list = list.filter((c) => families.includes(c.family || c.key))
  }

  if (list.length === 0) return []

  // ONE choice per FAMILY, not per wording. A shoot that varies the WORDING
  // inside one concept produces three labels describing the same photograph
  // ("profile shot from the side" / "side view, camera level with torso"),
  // and nobody looking at the frame can tell which synonym produced it — the
  // forced choice becomes a 1-in-3 guess and `arrived` measures the guess.
  // The family is the part that IS visible. The cell stays keyed on the
  // wording, so the wordings are still compared against each other by the
  // counts they land on; the backend scores a family match (`judge_shot`).
  // A slot whose catalogue collapses to one family is a yes/no question
  // against the "None or cannot tell" answer, which is a real question when
  // the floor is known.
  const byFamily = new Map()
  for (const c of list) {
    const family = c.family || c.key
    if (!byFamily.has(family)) byFamily.set(family, c)
  }

  return [
    ...[...byFamily.values()].map((c) => ({
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
