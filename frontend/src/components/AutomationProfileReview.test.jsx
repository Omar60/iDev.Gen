// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import AutomationProfileReview, { unknownRegions } from './AutomationProfileReview.jsx'

const garments = [
  { id: 'row-first', wording: 'a blue coat', aside: '' },
  { id: 'row-second', wording: 'a brass bracelet', aside: 'bracelet moved aside' },
]
const appearance = 'A👗e\u0301'
const provenance = { origin: 'user', source_identity: 'looks-integrated-review', source_digest: 'a'.repeat(64) }

function Harness() {
  const [draft, setDraft] = useState(() => ({
    appearance_segments: [{
      segment_id: 'unassigned', start: 0, end: 4, text: appearance, owner: null,
    }],
    garment_coverage: Object.fromEntries(garments.map((garment) => [garment.id, {
      worn: { regions: unknownRegions(), provenance },
      ...(garment.aside ? { aside: { regions: unknownRegions(), provenance } } : {}),
    }])),
    baseline_coverage: null,
    inventory_complete: { value: null, provenance },
  }))
  const [inventoryConfirmed, setInventoryConfirmed] = useState(false)
  const [baselineConfirmed, setBaselineConfirmed] = useState(false)
  const [reviewConfirmed, setReviewConfirmed] = useState(false)
  return <>
    <AutomationProfileReview
      appearance={appearance}
      garments={garments}
      draft={draft}
      onDraftChange={setDraft}
      inventoryConfirmed={inventoryConfirmed}
      onInventoryConfirmed={setInventoryConfirmed}
      baselineConfirmed={baselineConfirmed}
      onBaselineConfirmed={setBaselineConfirmed}
      reviewConfirmed={reviewConfirmed}
      onReviewConfirmed={setReviewConfirmed}
      provenance={provenance}
    />
    <output aria-label="Controlled prospective draft">{JSON.stringify(draft)}</output>
  </>
}

let root
let container

async function drain() {
  await act(async () => {
    for (let index = 0; index < 8; index += 1) await Promise.resolve()
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

function render() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<Harness />))
}

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  container = null
})

describe('shared automation profile review controls', () => {
  it('keeps initial facts unknown and maps selected Unicode spans to stable garment row identities', async () => {
    render()
    const coverage = container.querySelector('[aria-label="a brass bracelet · worn coverage"]')
    expect(coverage).toBeTruthy()
    expect(coverage.querySelector('[aria-label="a brass bracelet · worn chest"]').value).toBe('unknown')
    expect(coverage.querySelector('[aria-label="a brass bracelet · worn feet"]').value).toBe('unknown')
    const baseline = container.querySelector('[aria-label="Baseline · every listed garment absent coverage"]')
    expect([...baseline.querySelectorAll('select')].map((select) => select.value)).toEqual(Array(4).fill('unknown'))
    expect(container.querySelector('[aria-label="Inventory confirmation"]').value).toBe('')
    expect([...container.querySelectorAll('p')].some((paragraph) => paragraph.textContent.includes('Assign every part'))).toBe(true)

    const textarea = container.querySelector('[aria-label="Appearance for ownership selection"]')
    await act(async () => {
      textarea.setSelectionRange(1, 3)
      textarea.dispatchEvent(new Event('select', { bubbles: true }))
      textarea.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    })
    await drain()
    expect(container.textContent).toContain('Selected phrase: “👗”')
    await selectValue(container.querySelector('[aria-label="Selected phrase owner"]'), 'garment:row-second')
    await act(async () => {
      [...container.querySelectorAll('button')]
        .find((button) => button.textContent === 'Assign or correct selected phrase').click()
    })
    await drain()

    const draft = JSON.parse(container.querySelector('[aria-label="Controlled prospective draft"]').textContent)
    expect(draft.appearance_segments).toEqual([
      expect.objectContaining({ start: 0, end: 1, text: 'A', owner: null }),
      expect.objectContaining({ start: 1, end: 2, text: '👗', owner: 'garment:row-second' }),
      expect.objectContaining({ start: 2, end: 4, text: 'e\u0301', owner: null }),
    ])
    expect(draft.garment_coverage['row-first'].worn.regions).toEqual(unknownRegions())
    expect(draft.garment_coverage['row-second'].aside.regions).toEqual(unknownRegions())
  })
})
