// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../api.js'
import SessionView from './SessionView.jsx'

let container, root
const drain = () => new Promise((resolve) => setTimeout(resolve, 0))
const deferred = () => {
  let resolve, reject
  const promise = new Promise((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
const resourceSession = {
  id: 850, name: 'Invented session', status: 'draft', model_id: 4, workflow_id: 12,
  model: { id: 4, name: 'Invented model', workflow_id: 12 }, shots: [], tags: ['fixture'],
  settings: { composition_mode: 'resource-v1', anchor_shot_ids: [] },
}
const plan = {
  version: 'resource-v1', look: '', initial_wardrobe: '', selected_resources: [], wardrobe_changes: [],
  takes: [
    { take_id: 'take-001', label: 'First take', camera: '', framing: '', pose: '', expression: '' },
    { take_id: 'take-002', label: 'Second take', camera: '', framing: '', pose: '', expression: '' },
  ],
}
const edit = async (field, value) => act(async () => {
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(field, value)
  field.dispatchEvent(new Event('input', { bubbles: true }))
})
const render = async () => act(async () => {
  root.render(<SessionView id={850} initialSession={resourceSession} initialPlan={plan}
    initialRevision={3} initialActiveStep="takes" />)
  await drain()
  await drain()
})
const addTag = async (value) => {
  await edit(container.querySelector('[aria-label="New session tag"]'), value)
  await act(async () => {
    container.querySelector('[aria-label="New session tag"]').closest('form')
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    for (let attempt = 0; attempt < 8; attempt += 1) await drain()
  })
}

beforeEach(() => {
  resourceSession.tags = ['fixture']
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    if (path === '/api/sessions/850') return resourceSession
    if (path === '/api/config' || path === '/api/comfy/models') return {}
    if (path === '/api/wardrobe') return { garments: [], outfits: [] }
    return []
  })
  vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => (
    path === '/api/sessions/850/full-auto' ? { current_run: null }
      : path === '/api/sessions/850/plan' ? { plan_revision: 3, plan }
        : {}
  ))
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  vi.restoreAllMocks()
})

it('keeps dirty take IDs, order, and edits when a saved tag is followed by a failed plan refresh', async () => {
  let planReads = 0
  vi.mocked(api.getVersioned).mockImplementation(async (path) => {
    if (path === '/api/sessions/850/full-auto') return { current_run: null }
    if (path === '/api/sessions/850/plan') {
      planReads += 1
      if (planReads === 1 || planReads > 2) return { plan_revision: 3, plan }
      throw new Error('Plan refresh temporarily unavailable')
    }
    return {}
  })
  await render()
  const patch = vi.spyOn(api, 'patch').mockImplementation(async (_path, body) => {
    if (body?.tags) resourceSession.tags = body.tags
    return {}
  })
  await edit(container.querySelector('#take-take-001-label'), 'Unsaved edited take')

  await addTag('saved-tag')

  expect(patch).toHaveBeenCalledTimes(1)
  expect(patch).toHaveBeenCalledWith('/api/sessions/850', { tags: ['fixture', 'saved-tag'] })
  expect(planReads).toBe(2)
  expect(container.querySelector('#take-take-001-label')).toBeTruthy()
  expect(container.querySelector('#take-take-001-label').value).toBe('Unsaved edited take')
  expect(container.querySelectorAll('[data-take-id]')).toHaveLength(2)
  expect([...container.querySelectorAll('[data-take-id]')].map((take) => take.dataset.takeId))
    .toEqual(['take-001', 'take-002'])
  expect(container.textContent).toContain('Plan has unsaved modifications')
  expect(container.textContent).toContain('Tag saved.')
  expect(container.textContent).toContain('The plan refresh failed.')

  await act(async () => {
    [...container.querySelectorAll('button')].find((button) => button.textContent === 'Retry plan refresh').click()
    for (let attempt = 0; attempt < 8; attempt += 1) await drain()
  })
  expect(container.querySelector('[role="alert"]')).toBeNull()
  expect(container.querySelector('#take-take-001-label').value).toBe('Unsaved edited take')
  expect(container.textContent).toContain('Plan has unsaved modifications')
  expect(patch).toHaveBeenCalledTimes(1)
})

it('ignores a late failed plan read after a newer refresh succeeds', async () => {
  const olderRead = deferred()
  let planReads = 0
  vi.mocked(api.getVersioned).mockImplementation(async (path) => {
    if (path === '/api/sessions/850/full-auto') return { current_run: null }
    if (path === '/api/sessions/850/plan') {
      planReads += 1
      if (planReads === 1 || planReads === 3) return { plan_revision: 3, plan }
      return olderRead.promise
    }
    return {}
  })
  const patch = vi.spyOn(api, 'patch').mockImplementation(async (_path, body) => {
    if (body?.tags) resourceSession.tags = body.tags
    return {}
  })
  const post = vi.spyOn(api, 'post')
  await render()
  await edit(container.querySelector('#take-take-001-label'), 'Latest local draft')

  await addTag('first-tag')
  expect(planReads).toBe(2)
  await addTag('second-tag')
  expect(planReads).toBe(3)
  expect(container.querySelector('#take-take-001-label').value).toBe('Latest local draft')

  await act(async () => {
    olderRead.reject(new Error('Older plan response failed'))
    for (let attempt = 0; attempt < 8; attempt += 1) await drain()
  })
  expect(container.querySelector('[role="alert"]')).toBeNull()
  expect(container.querySelector('#take-take-001-label').value).toBe('Latest local draft')
  expect([...container.querySelectorAll('[data-take-id]')].map((take) => take.dataset.takeId))
    .toEqual(['take-001', 'take-002'])
  expect(patch).toHaveBeenCalledTimes(2)
  expect(patch).toHaveBeenNthCalledWith(1, '/api/sessions/850', { tags: ['fixture', 'first-tag'] })
  expect(patch).toHaveBeenNthCalledWith(2, '/api/sessions/850', { tags: ['fixture', 'first-tag', 'second-tag'] })
  expect(post).not.toHaveBeenCalled()
})
