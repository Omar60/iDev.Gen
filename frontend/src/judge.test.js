import { beforeEach, describe, it, expect } from 'vitest'
import { setCatalogue } from './kinds.js'
import { slotChoices, buildJudgeDeck, computeAgreement } from './judge.js'
import seedCatalogue from '../../data/catalogue-seed.json'

describe('slotChoices', () => {
  beforeEach(() => {
    setCatalogue(seedCatalogue)
  })

  it('returns camera positions with "None or cannot tell" as the final choice', () => {
    const choices = slotChoices('camera', 'directed')
    expect(choices.length).toBeGreaterThan(1)
    const last = choices[choices.length - 1]
    expect(last).toEqual({
      key: '',
      label: 'None or cannot tell',
      text: 'None of the above or cannot tell',
    })
    for (const c of choices.slice(0, -1)) {
      expect(c.key).toBeTruthy()
      expect(c.label).toBeTruthy()
      expect(c.text).toBeTruthy()
    }
  })

  it('points slotChoices at judge_label and never prompt wording', () => {
    for (const manner of ['directed', 'candid', 'selfie']) {
      for (const slot of ['camera', 'act', 'framing']) {
        const choices = slotChoices(slot, manner)
        for (const c of choices.slice(0, -1)) {
          const comp = seedCatalogue.find((x) => x.concept_key === c.key && x.slot === slot && x.manner === manner)
          if (comp) {
            expect(c.label).toBe(comp.judge_label)
            expect(c.text).toBe(comp.judge_label)
            expect(c.label).not.toBe(comp.wording)
          }
        }
      }
    }
  })

  it('offers ONE choice per family, never two labels for one photograph', () => {
    // The seed holds side-left and side-right (family `side`), shoulder-left
    // and shoulder-right (family `shoulder`). Two labels describing the same
    // geometry make the forced choice a guess: the judge sees the photograph,
    // not the wording that asked for it.
    const choices = slotChoices('camera', 'directed').slice(0, -1)
    const families = choices.map((c) => {
      const comp = seedCatalogue.find(
        (x) => x.concept_key === c.key && x.slot === 'camera' && x.manner === 'directed')
      return comp.family
    })
    expect(new Set(families).size).toBe(families.length)
    // And every family in the catalogue is still offered.
    const all = new Set(seedCatalogue
      .filter((x) => x.slot === 'camera' && x.manner === 'directed' && !x.retired_at)
      .map((x) => x.family))
    expect(new Set(families)).toEqual(all)
  })

  it('narrows the choices to the families the pass actually photographed', () => {
    // The catalogue holds nine directed camera families; a shoot that
    // photographed one must not ask the judge about the other eight.
    const choices = slotChoices('camera', 'directed', ['side'])
    expect(choices.length).toBe(2)
    expect(choices[1].key).toBe('')
    const comp = seedCatalogue.find((x) => x.concept_key === choices[0].key)
    expect(comp.family).toBe('side')
    // No list means the whole slice, the way the screen previews it.
    expect(slotChoices('camera', 'directed').length).toBeGreaterThan(2)
  })

  it('offers a single family as a yes/no question, not an empty list', () => {
    const choices = slotChoices('framing', 'directed')
    expect(choices.length).toBe(2)
    expect(choices[1].key).toBe('')
  })

  it('resolves manner-specific camera positions for selfie and candid', () => {
    const selfieChoices = slotChoices('camera', 'selfie')
    const candidChoices = slotChoices('camera', 'candid')
    expect(selfieChoices.length).toBeGreaterThan(1)
    expect(candidChoices.length).toBeGreaterThan(1)
  })

  it('returns arrangements for the act slot', () => {
    const choices = slotChoices('act')
    expect(choices.length).toBeGreaterThan(1)
    const keys = choices.map((c) => c.key)
    expect(keys).toContain('astride')
    expect(keys).toContain('reverse')
    expect(keys).toContain('wall')
    expect(keys[keys.length - 1]).toBe('')
  })

  it('returns framing choices when catalogue has framings', () => {
    const choices = slotChoices('framing', 'directed')
    expect(choices.length).toBeGreaterThan(1)
  })

  it('returns an empty array for unknown slots', () => {
    expect(slotChoices('unknown')).toEqual([])
  })
})

describe('buildJudgeDeck', () => {
  it('returns only regular shots when controls is empty', () => {
    const deck = buildJudgeDeck([10, 20, 30], [])
    expect(deck).toEqual([
      { shot_id: 10, isControl: false },
      { shot_id: 20, isControl: false },
      { shot_id: 30, isControl: false },
    ])
  })

  it('mixes controls into the deck with isControl: true', () => {
    const rand = () => 0.5
    const deck = buildJudgeDeck([1, 2], [3], rand)
    expect(deck.length).toBe(3)
    expect(deck.filter((x) => x.isControl).length).toBe(1)
    expect(deck.filter((x) => !x.isControl).length).toBe(2)
    expect(deck.map((x) => x.shot_id).sort()).toEqual([1, 2, 3])
  })
})

describe('computeAgreement', () => {
  it('handles empty results and non-control results', () => {
    expect(computeAgreement([])).toEqual({
      totalControls: 0,
      agreedCount: 0,
      disagreedCount: 0,
      rate: null,
      disagreements: [],
    })

    const regularOnly = [
      { shot_id: 1, control: false },
      { shot_id: 2, control: false },
    ]
    expect(computeAgreement(regularOnly)).toEqual({
      totalControls: 0,
      agreedCount: 0,
      disagreedCount: 0,
      rate: null,
      disagreements: [],
    })
  })

  it('computes agreement rate and records disagreements accurately', () => {
    const results = [
      { shot_id: 1, control: false },
      { shot_id: 2, control: true, agreed: true, stored: 'front-direct', answered: 'front-direct' },
      { shot_id: 3, control: false },
      { shot_id: 4, control: true, agreed: false, stored: 'front-direct', answered: 'overhead-direct' },
      { shot_id: 5, control: true, agreed: true, stored: 'astride', answered: 'astride' },
    ]

    const stats = computeAgreement(results)
    expect(stats.totalControls).toBe(3)
    expect(stats.agreedCount).toBe(2)
    expect(stats.disagreedCount).toBe(1)
    expect(stats.rate).toBeCloseTo(2 / 3)
    expect(stats.disagreements).toEqual([
      { shot_id: 4, stored: 'front-direct', answered: 'overhead-direct' },
    ])
  })
})
