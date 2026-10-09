import { describe, expect, it } from 'vitest'
import {
  scalarOffsetFromUtf16,
  scalarSpanFromUtf16Selection,
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
})
