import { describe, expect, it } from 'vitest'
import { POSITIONS, ARRANGEMENTS, CAMERA_POSITIONS, CANDID_POSITIONS, SELFIE_POSITIONS } from './kinds.js'
import { candidatePool, defaultCount, FRAMING_WORDING } from './compose.js'

/** The candidate pool the compose control posts. Each test pins a different
 *  fact: the catalogue slice (every camera of the manner, every act), the
 *  fixed framing wording (the one the scripts use), the shape
 *  `/compose-run` reads, and the fallback for an unknown manner. The
 *  shape the endpoint consumes is the catalogue's own concept entry, so
 *  these tests can check the keys and the wording text directly. */

describe('candidatePool', () => {
  it('offers every camera of POSITIONS[manner] for directed', () => {
    const { camera, act, framing } = candidatePool('directed')
    const keys = camera.map((c) => c.key)
    expect(keys).toEqual(CAMERA_POSITIONS.map((c) => c.key))
    // Concept shape: each camera carries a wordings[0] with key + text.
    for (const c of camera) {
      expect(c.slot).toBe('camera')
      expect(c.wordings[0].key).toBe(c.key)
      expect(c.wordings[0].text).toBeTruthy()
    }
  })

  it('offers every camera of POSITIONS[manner] for candid', () => {
    const { camera } = candidatePool('candid')
    expect(camera.map((c) => c.key)).toEqual(CANDID_POSITIONS.map((c) => c.key))
  })

  it('offers every camera of POSITIONS[manner] for selfie', () => {
    const { camera } = candidatePool('selfie')
    expect(camera.map((c) => c.key)).toEqual(SELFIE_POSITIONS.map((c) => c.key))
  })

  it('offers every act of ARRANGEMENTS for every manner', () => {
    // Same acts across manners — the catalogue is shared, and the spec
    // names "every act of ARRANGEMENTS" without a per-manner slice.
    for (const manner of ['directed', 'candid', 'selfie']) {
      const { act } = candidatePool(manner)
      expect(act.map((a) => a.key)).toEqual(ARRANGEMENTS.map((a) => a.key))
      for (const a of act) {
        expect(a.slot).toBe('act')
        expect(a.wordings[0].key).toBe(a.key)
        expect(a.wordings[0].text).toBeTruthy()
      }
    }
  })

  it('ships exactly one framing, fixed to the wording the scripts use', () => {
    // FRAMING IS FIXED: no catalogue, no choice. The wording the screen
    // offers the operator is the wording the composer will use, and that
    // is the one in scripts/shoot_arrangements.py:_FRAMING_CONCEPT.
    const { framing } = candidatePool('directed')
    expect(framing).toHaveLength(1)
    expect(framing[0].wordings).toHaveLength(1)
    expect(framing[0].wordings[0].text).toBe(FRAMING_WORDING)
    expect(FRAMING_WORDING).toBe('a three-quarter photograph from the knees up')
  })

  it('falls back to the directed camera catalogue for an unknown manner', () => {
    // A session created before the catalogue slice for its manner
    // existed still gets a non-empty pool, the same way kissCameraFor
    // already does (`frontend/src/kinds.js:2132-2134`). The refusal the
    // operator sees is the lack of a verified cell, not the lack of a
    // candidate.
    const { camera } = candidatePool('something-new')
    expect(camera.map((c) => c.key)).toEqual(CAMERA_POSITIONS.map((c) => c.key))
  })

  it('does not mutate POSITIONS or ARRANGEMENTS across calls', () => {
    // The function slices the catalogue out of habit, but a future
    // refactor that uses `POSITIONS[manner]` directly should not leave
    // the catalogue's array shape changed between two button presses.
    const beforeCam = POSITIONS.directed.map((c) => c.key)
    const beforeAct = ARRANGEMENTS.map((a) => a.key)
    candidatePool('directed')
    candidatePool('candid')
    expect(POSITIONS.directed.map((c) => c.key)).toEqual(beforeCam)
    expect(ARRANGEMENTS.map((a) => a.key)).toEqual(beforeAct)
  })

  it('returns the shape /compose-run reads (candidates dict per slot)', () => {
    // The endpoint reads `c["key"]` for the pool builder and
    // `c["wordings"][0]["text"]` for the join (`backend/main.py:1036-1037`
    // and `compose_shot`). A pool that returns the right keys but
    // without wordings would fail at the join, not at the pool build.
    const { camera, act, framing } = candidatePool('directed')
    expect(Array.isArray(camera)).toBe(true)
    expect(Array.isArray(act)).toBe(true)
    expect(Array.isArray(framing)).toBe(true)
    for (const c of [...camera, ...act, ...framing]) {
      expect(typeof c.key).toBe('string')
      expect(c.key).not.toBe('')
      expect(Array.isArray(c.wordings)).toBe(true)
      expect(c.wordings.length).toBeGreaterThan(0)
      expect(typeof c.wordings[0].text).toBe('string')
      expect(c.wordings[0].text).not.toBe('')
    }
  })
})


describe('defaultCount', () => {
  it('opens on the smallest slot that has a choice, not on the fixed framing', () => {
    // The framing is one wording and the no-repeat rule exempts it, so it
    // must not be what the control opens on — a default of 1 would make the
    // button useless for the batch it exists to produce.
    expect(defaultCount('directed')).toBe(Math.min(POSITIONS.directed.length, ARRANGEMENTS.length))
    expect(defaultCount('directed')).toBeGreaterThan(1)
  })

  it('is the same for every manner while the act list is the binding slot', () => {
    // The initialiser in SessionView runs before the session loads and reads
    // the `directed` fallback. That is only safe while the smallest slot with
    // a choice is the shared act list; this test fails the day a manner gets
    // fewer cameras than there are acts, which is when the value has to move
    // out of the initialiser.
    for (const manner of ['directed', 'candid', 'selfie']) {
      expect(defaultCount(manner)).toBe(defaultCount('directed'))
    }
  })
})
