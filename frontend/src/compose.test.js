import { beforeEach, describe, expect, it } from 'vitest'
import { setCatalogue, positionsFor, arrangements, framings } from './kinds.js'
import { candidatePool, defaultCount, fillCellDefaultCount } from './compose.js'
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
