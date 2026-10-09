const HIGH_SURROGATE_MIN = 0xd800
const HIGH_SURROGATE_MAX = 0xdbff
const LOW_SURROGATE_MIN = 0xdc00
const LOW_SURROGATE_MAX = 0xdfff

function scalarValues(text) {
  if (typeof text !== 'string') throw new TypeError('appearance must be a string')
  const values = Array.from(text)
  if (values.some((value) => {
    const point = value.codePointAt(0)
    return point >= HIGH_SURROGATE_MIN && point <= LOW_SURROGATE_MAX
  })) throw new RangeError('appearance must not contain unpaired surrogates')
  return values
}

export function scalarOffsetFromUtf16(text, offset) {
  scalarValues(text)
  if (!Number.isSafeInteger(offset) || offset < 0 || offset > text.length) {
    throw new RangeError('selection offset is outside the appearance')
  }
  if (
    offset > 0 && offset < text.length
    && text.charCodeAt(offset - 1) >= HIGH_SURROGATE_MIN
    && text.charCodeAt(offset - 1) <= HIGH_SURROGATE_MAX
    && text.charCodeAt(offset) >= LOW_SURROGATE_MIN
    && text.charCodeAt(offset) <= LOW_SURROGATE_MAX
  ) throw new RangeError('selection offset splits a Unicode scalar')
  return Array.from(text.slice(0, offset)).length
}

export function scalarSpanFromUtf16Selection(text, startOffset, endOffset) {
  const start = scalarOffsetFromUtf16(text, startOffset)
  const end = scalarOffsetFromUtf16(text, endOffset)
  if (end < start) throw new RangeError('selection end precedes its start')
  return {
    start,
    end,
    text: scalarValues(text).slice(start, end).join(''),
  }
}
