// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api.js'
import Library from './Library.jsx'

let root
let container

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  container = null
  vi.restoreAllMocks()
})

async function renderLibrary(sessions) {
  vi.spyOn(api, 'get').mockResolvedValue(sessions)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(React.createElement(Library))
    await Promise.resolve()
  })
}

describe('Task 8.5 resource Library cards', () => {
  it('shows plan-owned look and wardrobe from the resource search projection', async () => {
    await renderLibrary([{
      id: 851,
      name: 'Invented resource card',
      model_name: 'Model A',
      composition_mode: 'resource-v1',
      look: 'Plan-owned studio light',
      wardrobe: 'Plan-owned blue coat',
      shot_count: 0,
      done_count: 0,
      tags: [],
    }])

    expect(container.textContent).toContain('Plan-owned studio light')
    expect(container.textContent).toContain('Plan-owned blue coat')
    expect(container.querySelector('input')?.placeholder).toContain('look or wardrobe')
  })

  it('shows a resource-plan diagnostic instead of inventing constants', async () => {
    await renderLibrary([{
      id: 852,
      name: 'Invented invalid resource card',
      model_name: 'Model A',
      composition_mode: 'resource-v1',
      look: null,
      wardrobe: null,
      diagnostic: { code: 'resource_plan_invalid', message: 'The saved resource plan is invalid.' },
      shot_count: 0,
      done_count: 0,
      tags: [],
    }])

    expect(container.textContent).toContain('Resource plan needs attention')
    expect(container.textContent).toContain('The saved resource plan is invalid.')
    expect(container.textContent).not.toContain('No additional look constraint')
    expect(container.textContent).not.toContain('No wardrobe description')
  })
})
