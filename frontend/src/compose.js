// The candidate pool the compose-run button on a session builds from the
// catalogue.
//
// Same shape as `judge.js`: pure functions, no React, no fetch, the view imports
// the result and posts it. Two reasons. The first is test reach — `vitest` runs
// this without rendering anything, so the catalogue scan is the same test
// surface a manual read of the screen cannot see. The second is the one this
// file exists to keep: the operator-facing pool the spec scenario names
// (every camera of the session's manner, every act of the catalogue) is
// decided here, in code that has no opinion on a button, so a future "let me
// add a checkbox for the high cameras" lands in the view, not in the
// catalogue, and the screen still asks for the whole catalogue slice by
// default.

import { positionsFor, arrangements, framings, cameraPlan, MANNER } from './kinds.js'

/** The candidate pool for a session's manner, in the shape
 *  `ComposeRunIn.candidates` (`backend/main.py:ComposeRunIn`).
 *
 *  Cameras, acts, and framings come from the component catalogue.
 */
export function candidatePool(manner = 'directed') {
  return {
    camera: positionsFor(manner),
    act: arrangements(manner),
    framing: framings(manner),
  }
}

/** The count the control opens on: the smallest slot the no-repeat rule
 *  actually binds. A run never uses a component twice
 *  (`backend/main.py`, task 3.4), and a slot the pool offers one value
 *  for is exempt — so the ceiling is the smallest slot that HAS a
 *  choice, which today is the act list. Derived rather than written as
 *  a number so it follows the catalogue: adding a fourth arrangement
 *  moves the default without anyone remembering to.
 *
 *  It is an opening value, not a promise. Dead cells shrink the real
 *  pool below this, and the composer's own refusal names the largest
 *  fillable count when that happens.
 */
export function defaultCount(manner) {
  const pool = candidatePool(manner)
  const sizes = [pool.camera.length, pool.act.length, pool.framing.length].filter((n) => n > 1)
  return sizes.length ? Math.min(...sizes) : 1
}


/** The count the "fill cell" control opens on: the threshold a cell
 *  needs to reach `verified` or `dead`, which is `db.cell_state`'s
 *  `judged >= 10`. `compose-run` produces VARIETY (3.4's no-repeat
 *  rule), so a 10-photograph run is 10 distinct trios — that fills 10
 *  cells, not one. The fill-cell control is a different request: pick
 *  one trio, queue N photographs of it, take ONE cell to its
 *  threshold. The default is 10 because that is the operator's
 *  intent most of the time, and a number the form can show without
 *  a sentence explaining it.
 *
 *  The function is pure: same input, same number. The view
 *  initialises the count input with this, and the operator can
 *  edit it. A future "let me also fill below threshold" lands
 *  here as a different value, not a code change in the view.
 */
export function fillCellDefaultCount() {
  return 10
}


/** The part of a composed photograph no catalogue row carries: how the frame is
 *  careless and how the photograph was taken, one string per photograph, in the
 *  order the run queues them (`ComposeRunIn.extras`).
 *
 *  Dealt here from the SAME lists and the same spreader the written path deals
 *  them with (`enhance.js:shootLines`) — `FRAMING_SLIPS` and `TECHNIQUE_DEFECTS`
 *  through `cameraPlan`, one family each, so no two consecutive photographs open
 *  on the same slip or the same defect. The composed line is meant to be the
 *  written line with nobody writing it, and both clauses were measured on the
 *  written one: chosen freely the careless framing survived 2.5 lines of 12 once
 *  the camera rows were dealt, because the dealt rows crowd out what nobody
 *  handed the writer. A composer hands everything or the clause is simply gone.
 *
 *  A manner that defines neither (directed, which has no `technique` field at
 *  all) gets an empty array and the run posts no extras — the composed line is
 *  then byte-for-byte what it was before this existed.
 */
export function extrasFor(manner, n, rand = Math.random) {
  const deal = (rows) => (rows && n > 0 ? cameraPlan(n, rand, rows) : null)
  const slips = deal(MANNER[manner]?.slips)
  const defects = deal(MANNER[manner]?.defects)
  if (!slips && !defects) return []
  // Joined with a full stop because that is what `_sentences` puts between every
  // other piece of the line: the two clauses are two sentences, not a list.
  return Array.from({ length: n }, (_, i) =>
    [slips?.[i], defects?.[i]].filter(Boolean).join('. '))
}
