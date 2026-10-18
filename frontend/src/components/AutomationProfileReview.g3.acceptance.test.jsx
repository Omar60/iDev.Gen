// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import AutomationProfileReview, { unknownRegions } from './AutomationProfileReview.jsx'

const appearance = 'A👗e\u0301Z'
const rows = [
  { id: 'row-coat', wording: 'a blue coat', aside: '' },
  { id: 'row-bracelet', wording: 'a brass bracelet', aside: 'bracelet moved aside' },
]
const knownRegions = { chest: 'partial', hips: 'full', legs: 'none', feet: 'none' }

function completeDraft() {
  return {
    appearance_segments: [{
      segment_id: 'all-permanent', start: 0, end: 5, text: appearance, owner: 'permanent',
    }],
    garment_coverage: Object.fromEntries(rows.map((garment) => [garment.id, {
      worn: { regions: { ...knownRegions } },
      ...(garment.aside ? { aside: { regions: { ...knownRegions } } } : {}),
    }])),
    baseline_coverage: { regions: { chest: 'none', hips: 'none', legs: 'none', feet: 'none' } },
    inventory_complete: { value: true },
  }
}

function Harness({ complete = false }) {
  const [draft, setDraft] = useState(() => complete ? completeDraft() : {
    appearance_segments: [{
      segment_id: 'unassigned', start: 0, end: 5, text: appearance, owner: null,
    }],
    garment_coverage: Object.fromEntries(rows.map((garment) => [garment.id, {
      worn: { regions: unknownRegions() },
      ...(garment.aside ? { aside: { regions: unknownRegions() } } : {}),
    }])),
    baseline_coverage: null,
    inventory_complete: { value: null },
  })
  const [inventoryConfirmed, setInventoryConfirmed] = useState(complete)
  const [baselineConfirmed, setBaselineConfirmed] = useState(complete)
  const [reviewConfirmed, setReviewConfirmed] = useState(complete)
  return <>
    <AutomationProfileReview
      appearance={appearance}
      garments={rows}
      draft={draft}
      onDraftChange={setDraft}
      inventoryConfirmed={inventoryConfirmed}
      onInventoryConfirmed={setInventoryConfirmed}
      baselineConfirmed={baselineConfirmed}
      onBaselineConfirmed={setBaselineConfirmed}
      reviewConfirmed={reviewConfirmed}
      onReviewConfirmed={setReviewConfirmed}
    />
    <output aria-label="G3 review state">{JSON.stringify({ draft, inventoryConfirmed, baselineConfirmed, reviewConfirmed })}</output>
  </>
}

let root
let container

async function drain() {
  await act(async () => {
    for (let index = 0; index < 12; index += 1) await Promise.resolve()
  })
}

async function selectValue(element, value) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), 'value')?.set
    if (setter) setter.call(element, String(value))
    else element.value = String(value)
    element.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await drain()
}

function render(complete = false) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<Harness complete={complete} />))
}

function state() {
  return JSON.parse(container.querySelector('[aria-label="G3 review state"]').textContent)
}

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  container = null
})

describe('Group 3 shared review behavior', () => {
  it('leaves new facts unknown and selects an exact Unicode scalar span for its stable row owner', async () => {
    render()
    expect(container.querySelector('[aria-label="Inventory confirmation"]').value).toBe('')
    expect(container.querySelector('[aria-label="Baseline · every listed garment absent chest"]').value).toBe('unknown')
    expect(container.querySelector('[aria-label="a brass bracelet · worn chest"]').value).toBe('unknown')
    expect(container.querySelector('[aria-label="Moved aside · bracelet moved aside feet"]').value).toBe('unknown')
    expect(state().reviewConfirmed).toBe(false)

    const textarea = container.querySelector('[aria-label="Appearance for ownership selection"]')
    await act(async () => {
      textarea.setSelectionRange(1, 3)
      textarea.dispatchEvent(new Event('select', { bubbles: true }))
      textarea.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    })
    await drain()
    expect(container.textContent).toContain('Selected phrase: “👗”')

    await selectValue(container.querySelector('[aria-label="Selected phrase owner"]'), 'garment:row-bracelet')
    await act(async () => {
      [...container.querySelectorAll('button')]
        .find((button) => button.textContent === 'Assign or correct selected phrase').click()
    })
    await drain()

    expect(state().draft.appearance_segments).toEqual([
      expect.objectContaining({ start: 0, end: 1, text: 'A', owner: null }),
      expect.objectContaining({ start: 1, end: 2, text: '👗', owner: 'garment:row-bracelet' }),
      expect.objectContaining({ start: 2, end: 5, text: 'e\u0301Z', owner: null }),
    ])
    expect(state().reviewConfirmed).toBe(false)
  })

  it('clears accepted review when shared coverage changes while preserving other unknown states', async () => {
    render(true)
    expect(state().reviewConfirmed).toBe(true)

    await selectValue(container.querySelector('[aria-label="a brass bracelet · worn chest"]'), 'none')

    const current = state()
    expect(current.reviewConfirmed).toBe(false)
    expect(current.draft.garment_coverage['row-bracelet'].worn.regions).toEqual({
      chest: 'none', hips: 'full', legs: 'none', feet: 'none',
    })
    expect(current.draft.garment_coverage['row-bracelet'].aside.regions).toEqual(knownRegions)
    expect(current.draft.baseline_coverage.regions).toEqual({
      chest: 'none', hips: 'none', legs: 'none', feet: 'none',
    })
  })
})
