import { describe, expect, it } from 'vitest'
import {
  scalarOffsetFromUtf16,
  scalarSpanFromUtf16Selection,
  assignScalarSpanOwner,
} from './appearanceOwnership.js'

describe('appearance ownership Unicode coordinates', () => {
  it('maps visual UTF-16 selections to exact scalar spans', () => {
    const appearance = 'a😀e\u0301z'
    expect(scalarSpanFromUtf16Selection(appearance, 0, 3)).toEqual({
      start: 0, end: 2, text: 'a😀',
    })
    expect(scalarSpanFromUtf16Selection(appearance, 3, 5)).toEqual({
      start: 2, end: 4, text: 'e\u0301',
    })
    expect(scalarOffsetFromUtf16(appearance, 6)).toBe(5)
  })

  it('rejects offsets inside surrogate pairs and unpaired source surrogates', () => {
    expect(() => scalarOffsetFromUtf16('a😀b', 2)).toThrow(RangeError)
    expect(() => scalarOffsetFromUtf16('\ud800', 0)).toThrow(RangeError)
    expect(() => scalarOffsetFromUtf16('ok', 3)).toThrow(RangeError)
  })

  it('corrects ownership by Unicode scalar selection and preserves a full partition', () => {
    const appearance = 'A 😀 bracelet.'
    let nextId = 0
    const ids = () => `segment-${++nextId}`
    const initial = [{ start: 0, end: Array.from(appearance).length, owner: 'permanent' }]
    const first = assignScalarSpanOwner(appearance, initial, 3, 12, 'garment:bracelet', ids)
    const corrected = assignScalarSpanOwner(appearance, first, 3, 4, 'permanent', ids)
    expect(corrected.map(({ text, owner }) => ({ text, owner }))).toEqual([
      { text: 'A 😀', owner: 'permanent' },
      { text: ' ', owner: 'permanent' },
      { text: 'bracelet', owner: 'garment:bracelet' },
      { text: '.', owner: 'permanent' },
    ])
    expect(corrected[0].start).toBe(0)
    expect(corrected.at(-1).end).toBe(Array.from(appearance).length)
    expect(corrected.slice(1).every((segment, index) => (
      corrected[index].end === segment.start
    ))).toBe(true)
  })
})
