import { describe, expect, it } from 'vitest'
import {
  draftContentDigest,
  emptyAutomationDraft,
  hasCompleteAutomationFacts,
  newLookRowId,
  prospectiveProfile,
  prospectiveSource,
} from './looksAutomationReview.js'

describe('prospective look automation review', () => {
  it('matches the backend canonical source digest for Unicode text and maps stable row owners to current order', async () => {
    const first = { id: newLookRowId(), key: '', wording: 'blue jacket', aside: '' }
    const second = { id: newLookRowId(), key: '', wording: 'silver earrings', aside: '' }
    const source = prospectiveSource('braided 🧵', [first])

    await expect(draftContentDigest(source)).resolves.toBe(
      '0fc6a7fd1556e453147e905952bad8bdad2d127cf651c1f7f8cb3a4069db88dd',
    )

    const draft = emptyAutomationDraft('braided 🧵', [first, second])
    draft.appearance_segments = [{
      segment_id: 'segment-braid', start: 0, end: 9, text: 'braided 🧵', owner: `garment:${second.id}`,
    }]
    const reordered = [second, first]
    const profile = prospectiveProfile(draft, 'braided 🧵', reordered, 'd'.repeat(64))
    expect(profile.appearance_segments[0].owner).toEqual({ garment_index: 0 })
    expect(profile.garment_coverage.map((entry) => entry.garment_index)).toEqual([0, 1])
  })

  it('starts with every assertion unaccepted and requires complete known facts', () => {
    const row = { id: newLookRowId(), wording: 'a blue jacket', aside: 'the jacket moved aside' }
    const draft = emptyAutomationDraft('dark hair', [row])

    expect(draft.appearance_segments[0].owner).toBe(null)
    expect(draft.inventory_complete.value).toBe(null)
    expect(draft.baseline_coverage).toBe(null)
    expect(Object.values(draft.garment_coverage[row.id].worn.regions)).toEqual(['unknown', 'unknown', 'unknown', 'unknown'])
    expect(hasCompleteAutomationFacts(draft, 'dark hair', [row])).toBe(false)

    draft.appearance_segments[0].owner = 'permanent'
    draft.inventory_complete.value = true
    draft.baseline_coverage = { regions: { chest: 'none', hips: 'none', legs: 'none', feet: 'none' } }
    draft.garment_coverage[row.id] = {
      worn: { regions: { chest: 'full', hips: 'full', legs: 'full', feet: 'full' } },
      aside: { regions: { chest: 'none', hips: 'none', legs: 'none', feet: 'none' } },
    }
    expect(hasCompleteAutomationFacts(draft, 'dark hair', [row])).toBe(true)
    expect(hasCompleteAutomationFacts(draft, 'dark hair', [{ ...row, id: newLookRowId() }])).toBe(false)
  })
})
