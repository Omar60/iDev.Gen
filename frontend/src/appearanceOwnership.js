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

export function assignScalarSpanOwner(text, segments, start, end, owner, idFactory) {
  const values = scalarValues(text)
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end)
      || start < 0 || end <= start || end > values.length) {
    throw new RangeError('ownership selection is outside the appearance')
  }
  if (typeof owner !== 'string' || !owner) throw new TypeError('ownership must be explicit')
  const makeId = typeof idFactory === 'function'
    ? idFactory
    : (() => `appearance-${Math.random().toString(36).slice(2)}`)
  const source = segments.length
    ? segments
    : [{ start: 0, end: values.length, owner: null }]
  const result = []
  for (const segment of source) {
    const boundaries = [segment.start]
    if (start > segment.start && start < segment.end) boundaries.push(start)
    if (end > segment.start && end < segment.end) boundaries.push(end)
    boundaries.push(segment.end)
    boundaries.sort((left, right) => left - right)
    for (let index = 0; index < boundaries.length - 1; index += 1) {
      const partStart = boundaries[index]
      const partEnd = boundaries[index + 1]
      if (partEnd <= partStart) continue
      const partOwner = partStart >= start && partEnd <= end ? owner : segment.owner
      result.push({
        segment_id: makeId(),
        start: partStart,
        end: partEnd,
        text: values.slice(partStart, partEnd).join(''),
        owner: partOwner,
      })
    }
  }
  return result
}
