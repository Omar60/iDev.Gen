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
//
// FRAMING IS FIXED. There is no framing catalogue yet (every seeded row in
// `backend/db.py:EVIDENCE_SEED` carries `framing_wording = 'none'`), and
// deciding which framings exist is a measurement decision of the same weight
// as the ones that cost days of renders on this project. The wording this
// script hands the composer is the one `scripts/shoot_arrangements.py`
// already ships as `_FRAMING_CONCEPT` — `a three-quarter photograph from
// the knees up` — and the constant lives in this file so the control on
// the screen can say "framing is fixed" without re-deriving the value.

import { POSITIONS, ARRANGEMENTS } from './kinds.js'

/** The single framing wording the composer carries per shot, mirroring
 *  `scripts/shoot_arrangements.py:_FRAMING_CONCEPT`. A concept with one
 *  wording; the `key` is the slot-stable name the composer reads
 *  (`backend/main.py:compose_shot` reads `wordings[0]["text"]`). */
const FRAMING_CONCEPT = {
  key: 'framing',
  wordings: [{ key: 'framing', text: 'a three-quarter photograph from the knees up' }],
}

/** The candidate pool for a session's manner, in the shape
 *  `ComposeRunIn.candidates` (`backend/main.py:ComposeRunIn`).
 *
 *  Cameras come from `POSITIONS[manner]` — the catalogue slice the spec
 *  scenario names — and the act list is the shared `ARRANGEMENTS`. The
 *  framing is a single fixed concept, not a list, so the screen has nothing
 *  to choose for it.
 *
 *  `manner` is the session's stored value: `directed`, `candid` or
 *  `selfie`. An unknown manner falls back to `POSITIONS.directed` (the
 *  same fallback `kissCameraFor` already uses), so a session that was
 *  created before the catalogue slice for its manner existed still gets a
 *  non-empty pool and the refusal is the lack of a verified cell, not
 *  the lack of a candidate.
 */
export function candidatePool(manner) {
  const cameras = (POSITIONS[manner] || POSITIONS.directed).slice()
  const acts = ARRANGEMENTS.slice()
  return {
    camera: cameras,
    act: acts,
    framing: [FRAMING_CONCEPT],
  }
}

/** The single framing concept the compose control exposes, in case the
 *  view wants to say what it is. Exported as a constant so the control
 *  shows the wording the composer will use, not a paraphrase. */
export const FRAMING_WORDING = FRAMING_CONCEPT.wordings[0].text


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
