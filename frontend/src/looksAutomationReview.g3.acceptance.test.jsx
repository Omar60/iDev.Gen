import { describe, expect, it } from 'vitest'
import {
  draftFromProspectiveSuggestion,
  emptyAutomationDraft,
  hasCompleteAutomationFacts,
  prospectiveProfile,
} from './looksAutomationReview.js'

const regions = { chest: 'partial', hips: 'full', legs: 'none', feet: 'unknown' }
const appearance = 'A👗e\u0301Z'
const rows = [
  { id: 'row-coat', key: null, wording: 'a blue coat', aside: '' },
  { id: 'row-bracelet', key: null, wording: 'a brass bracelet', aside: 'bracelet moved aside' },
]

describe('Group 3 prospective row identity contracts', () => {
  it('starts with explicit unknowns and requires complete reviewed facts', () => {
    const draft = emptyAutomationDraft(appearance, rows)

    expect(draft.appearance_segments).toEqual([expect.objectContaining({
      start: 0, end: 5, text: appearance, owner: null,
    })])
    expect(draft.baseline_coverage).toBeNull()
    expect(draft.inventory_complete.value).toBeNull()
    expect(draft.garment_coverage['row-coat'].worn.regions).toEqual({
      chest: 'unknown', hips: 'unknown', legs: 'unknown', feet: 'unknown',
    })
    expect(draft.garment_coverage['row-bracelet'].aside.regions).toEqual({
      chest: 'unknown', hips: 'unknown', legs: 'unknown', feet: 'unknown',
    })
    expect(hasCompleteAutomationFacts(draft, appearance, rows)).toBe(false)
  })

  it('maps proposal indices to stable row ids, then rebuilds indices after reordering', () => {
    const draft = draftFromProspectiveSuggestion({
      appearance_segments: [{
        segment_id: 'emoji-owner', start: 1, end: 2, text: '👗', owner: { garment_index: 1 },
      }],
      garment_coverage: [
        { garment_index: 1, states: { worn: { regions }, aside: { regions } } },
        { garment_index: 0, states: { worn: { regions } } },
      ],
      baseline_coverage: { regions },
      inventory_complete: null,
    }, rows)

    expect(draft.appearance_segments[0].owner).toBe('garment:row-bracelet')
    expect(draft.garment_coverage['row-bracelet']).toEqual({
      worn: { regions }, aside: { regions },
    })
    expect(draft.garment_coverage['row-coat']).toEqual({ worn: { regions } })

    const reorderedRows = [rows[1], rows[0]]
    const requestProfile = prospectiveProfile(draft, appearance, reorderedRows, 'a'.repeat(64))

    expect(requestProfile.appearance_segments[0]).toEqual(expect.objectContaining({
      start: 1, end: 2, text: '👗', owner: { garment_index: 0 },
    }))
    expect(requestProfile.garment_coverage).toEqual([
      { garment_index: 0, states: { worn: { regions }, aside: { regions } } },
      { garment_index: 1, states: { worn: { regions } } },
    ])
  })

  it('refuses proposal indices that no longer identify a current row', () => {
    expect(() => draftFromProspectiveSuggestion({
      appearance_segments: [{
        segment_id: 'stale-owner', start: 0, end: 1, text: 'A', owner: { garment_index: 2 },
      }],
      garment_coverage: [],
    }, rows)).toThrow('unknown garment row')

    expect(() => draftFromProspectiveSuggestion({
      appearance_segments: [],
      garment_coverage: [{ garment_index: 1, states: {} }, { garment_index: 1, states: {} }],
    }, rows)).toThrow('invalid garment mapping')
  })
})
