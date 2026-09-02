import { beforeEach, describe, expect, it } from 'vitest'
import { setCatalogue, positionsFor, arrangements, framings, MANNER } from './kinds.js'
import { candidatePool, defaultCount, extrasFor, fillCellDefaultCount } from './compose.js'
import seedCatalogue from '../../data/catalogue-seed.json'

describe('candidatePool', () => {
  beforeEach(() => {
    setCatalogue(seedCatalogue)
  })

  it('offers every camera for directed', () => {
    const { camera, act, framing } = candidatePool('directed')
    const keys = camera.map((c) => c.key)
    expect(keys).toEqual(positionsFor('directed').map((c) => c.key))
    for (const c of camera) {
      expect(c.slot).toBe('camera')
      expect(c.wordings[0].key).toBe(c.key)
      expect(c.wordings[0].text).toBeTruthy()
    }
  })

  it('offers every camera for candid', () => {
    const { camera } = candidatePool('candid')
    expect(camera.map((c) => c.key)).toEqual(positionsFor('candid').map((c) => c.key))
  })

  it('offers every camera for selfie', () => {
    const { camera } = candidatePool('selfie')
    expect(camera.map((c) => c.key)).toEqual(positionsFor('selfie').map((c) => c.key))
  })

  it('offers every act for every manner', () => {
    for (const manner of ['directed', 'candid', 'selfie']) {
      const { act } = candidatePool(manner)
      expect(act.map((a) => a.key)).toEqual(arrangements(manner).map((a) => a.key))
      for (const a of act) {
        expect(a.slot).toBe('act')
        expect(a.wordings[0].key).toBe(a.key)
        expect(a.wordings[0].text).toBeTruthy()
      }
    }
  })

  it('ships framings from catalogue', () => {
    const { framing } = candidatePool('directed')
    expect(framing).toHaveLength(1)
    expect(framing[0].wordings).toHaveLength(1)
    expect(framing[0].wordings[0].text).toBe('a three-quarter photograph from the knees up')
  })

  it('offers nothing for a manner the catalogue has no components for', () => {
    // Not a fallback to `directed`. A manner with an empty catalogue draws
    // from nothing and the caller refuses: falling back would shoot the
    // session from another manner's cameras and record every cell under this
    // manner, which is a measurement of a catalogue nobody drew from.
    const { camera, act, framing } = candidatePool('something-new')
    expect(camera).toEqual([])
    expect(act).toEqual([])
    expect(framing).toEqual([])
    expect(positionsFor('directed').length).toBeGreaterThan(0)
  })

  it('returns the shape /compose-run reads (candidates dict per slot)', () => {
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
  beforeEach(() => {
    setCatalogue(seedCatalogue)
  })

  it('opens on the smallest slot that has a choice', () => {
    expect(defaultCount('directed')).toBe(Math.min(positionsFor('directed').length, arrangements('directed').length))
    expect(defaultCount('directed')).toBeGreaterThan(1)
  })

  it('is the same for every manner while the act list is the binding slot', () => {
    for (const manner of ['directed', 'candid', 'selfie']) {
      expect(defaultCount(manner)).toBe(defaultCount('directed'))
    }
  })
})

describe('fillCellDefaultCount', () => {
  it('is the threshold a cell needs to reach verified or dead', () => {
    expect(fillCellDefaultCount()).toBe(10)
  })

  it('takes no arguments and is a constant', () => {
    expect(fillCellDefaultCount()).toBe(fillCellDefaultCount())
  })
})

describe('extrasFor', () => {
  it('deals candid one slip and one defect per photograph, never two running', () => {
    const extras = extrasFor('candid', 12)
    expect(extras).toHaveLength(12)
    const slips = MANNER.candid.slips.map((r) => r.wordings[0].text)
    const defects = MANNER.candid.defects.map((r) => r.wordings[0].text)
    const partsOf = (line) => {
      const slip = slips.find((t) => line.includes(t))
      const defect = defects.find((t) => line.includes(t))
      expect(slip, line).toBeTruthy()
      expect(defect, line).toBeTruthy()
      // Nothing but the two clauses and the full stop between them.
      expect(line).toBe(`${slip}. ${defect}`)
      return { slip, defect }
    }
    const seen = extras.map(partsOf)
    // The whole reason both are dealt through `cameraPlan`: the spreader never
    // opens two consecutive photographs on the same family.
    const family = (rows, text) => rows.find((r) => r.wordings[0].text === text).wordings[0].family
    for (let i = 1; i < seen.length; i += 1) {
      expect(family(MANNER.candid.slips, seen[i].slip))
        .not.toBe(family(MANNER.candid.slips, seen[i - 1].slip))
      expect(family(MANNER.candid.defects, seen[i].defect))
        .not.toBe(family(MANNER.candid.defects, seen[i - 1].defect))
    }
  })

  it('deals nothing for a manner that defines neither', () => {
    // `directed` has no `technique` field at all, so a composed directed line
    // is what it was before extras existed.
    expect(extrasFor('directed', 5)).toEqual([])
  })
})
