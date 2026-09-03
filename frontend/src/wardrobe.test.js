import { describe, it, expect, beforeEach } from 'vitest'
import { setWardrobe, arcFor, statesFor, wearing, garmentKeys } from './wardrobe.js'

const STORE = {
  garments: [
    { key: 'grey-sweatshirt', wording: 'an oversized grey sweatshirt with the sleeves pushed up her forearms' },
    { key: 'black-leggings', wording: 'black leggings', covers: true },
    { key: 'black-knickers', wording: 'black cotton knickers', covers: true, aside: 'black cotton knickers, pulled aside' },
  ],
  outfits: [
    { key: 'sweatshirt-and-leggings', label: 'Sweatshirt and leggings',
      garments: 'black-leggings,grey-sweatshirt,black-knickers' },
    { key: 'one-piece', label: 'One piece', garments: ['grey-sweatshirt'] },
  ],
}

beforeEach(() => setWardrobe(STORE))

describe('the arc derived from an outfit', () => {
  it('takes one garment off per state and ends bare', () => {
    expect(statesFor('sweatshirt-and-leggings')).toEqual([
      'She wears black leggings, an oversized grey sweatshirt with the sleeves pushed up her forearms, and black cotton knickers.',
      'She wears an oversized grey sweatshirt with the sleeves pushed up her forearms, and black cotton knickers.',
      'She wears black cotton knickers.',
      'She wears black cotton knickers, pulled aside.',
      'She wears nothing at all.',
    ])
  })

  it('gives N garments N+1 states, plus one where the last is moved aside', () => {
    expect(statesFor('one-piece')).toHaveLength(2)          // no aside on the sweatshirt
    expect(statesFor('sweatshirt-and-leggings')).toHaveLength(5)
  })

  // The correction that produced the column: a toy or a hand does not need her
  // undressed, it needs access, and a garment that moves gives access while she
  // still has it on. Without the stage the arc jumps from `knickers` to nothing
  // and every such photograph is of a naked woman.
  it('reaches the aside stage while she is still wearing it', () => {
    const states = statesFor('sweatshirt-and-leggings')
    const aside = states[states.length - 2]
    expect(aside).toContain('pulled aside')
    expect(aside).toContain('knickers')
    expect(states[states.length - 1]).toBe('She wears nothing at all.')
  })

  it('adds no stage for a garment that cannot be moved', () => {
    setWardrobe({
      garments: [{ key: 'jeans', wording: 'blue jeans' }],
      outfits: [{ key: 'only-jeans', garments: 'jeans' }],
    })
    expect(statesFor('only-jeans')).toEqual(['She wears blue jeans.', 'She wears nothing at all.'])
  })

  // The whole point of the catalogue. A state written as what came OFF puts the
  // removed garment's word in the composed line, the crop law reads it as the
  // part of her that garment covers, and every framing above it leaves the pool
  // for the run. Derived states cannot say it.
  it('never names a garment that has come off', () => {
    const states = statesFor('sweatshirt-and-leggings')
    expect(states[1]).not.toContain('leggings')
    expect(states[2]).not.toContain('leggings')
    expect(states[2]).not.toContain('sweatshirt')
    // The last state, not a fixed index: the aside stage sits between the
    // knickers and nothing, and it names them because she is still wearing them.
    expect(states[states.length - 1]).not.toContain('knickers')
  })

  it('is empty for an outfit nobody has', () => {
    expect(statesFor('no-such-outfit')).toEqual([])
  })

  // An identifier in a prompt is two words the sampler paints.
  it('drops a garment key the catalogue does not hold rather than writing it', () => {
    setWardrobe({ ...STORE, outfits: [{ key: 'x', garments: 'ghost-garment,black-leggings' }] })
    // Dropped before the arc is built, so it costs no stage either: an unknown
    // key used to leave a state identical to the one after it, which reads as
    // the shoot standing still for a photograph.
    expect(statesFor('x')).toEqual([
      'She wears black leggings.',
      'She wears nothing at all.',
    ])
  })
})

describe('wearing', () => {
  it('says nothing at all rather than nothing', () => {
    expect(wearing([])).toBe('She wears nothing at all.')
  })

  it('joins two with an and, and three with commas', () => {
    expect(wearing(['a', 'b'])).toBe('She wears a, and b.')
    expect(wearing(['a', 'b', 'c'])).toBe('She wears a, b, and c.')
  })
})

describe('garmentKeys', () => {
  it('reads the column and a raw list the same way', () => {
    expect(garmentKeys({ garments: 'a, b ,c' })).toEqual(['a', 'b', 'c'])
    expect(garmentKeys({ garments: ['a', 'b', 'c'] })).toEqual(['a', 'b', 'c'])
    expect(garmentKeys({})).toEqual([])
  })
})

describe('access, per stage', () => {
  // The correction the whole column came from: a toy or a hand needs ACCESS, not
  // an undressed woman. Two stages of five give it — the one where the knickers
  // are pulled aside, and the bare one — and the three where something covers
  // her below the waist do not.
  it('is true only where nothing covers her below the waist', () => {
    expect(arcFor('sweatshirt-and-leggings').map((x) => x.access))
      .toEqual([false, false, false, true, true])
  })

  it('ignores a garment that covers nothing below the waist', () => {
    setWardrobe({
      garments: [{ key: 'tee', wording: 'a navy t-shirt', covers: false }],
      outfits: [{ key: 'just-a-tee', garments: 'tee' }],
    })
    // She has a t-shirt on and it is in her way for nothing.
    expect(arcFor('just-a-tee').map((x) => x.access)).toEqual([true, true])
  })
})
