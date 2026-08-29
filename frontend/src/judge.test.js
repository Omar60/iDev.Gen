import { describe, it, expect } from 'vitest'
import { slotChoices, buildJudgeDeck, computeAgreement } from './judge.js'

describe('slotChoices', () => {
  it('returns choices from readings in order with "None or cannot tell" as the final choice', () => {
    const readings = [
      { key: 'side-level', label: 'Side level view' },
      { key: 'front', label: 'Front view' },
    ]
    const choices = slotChoices(readings)
    expect(choices.length).toBe(3)
    expect(choices[0]).toEqual({ key: 'side-level', label: 'Side level view', text: 'Side level view' })
    expect(choices[1]).toEqual({ key: 'front', label: 'Front view', text: 'Front view' })
    expect(choices[2]).toEqual({
      key: '',
      label: 'None or cannot tell',
      text: 'None of the above or cannot tell',
    })
  })

  it('returns an empty array when readings is empty, null, or undefined', () => {
    expect(slotChoices([])).toEqual([])
    expect(slotChoices(null)).toEqual([])
    expect(slotChoices(undefined)).toEqual([])
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
