// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../App.jsx'
import { api } from '../api.js'
import SessionView from './SessionView.jsx'

let container, root
const drain = () => new Promise((resolve) => setTimeout(resolve, 0))
const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}
const session = (id, tags) => ({
  id, name: `Session ${id}`, status: 'draft', model_id: 4, workflow_id: 12,
  model: { id: 4, name: 'Invented character', workflow_id: 12 },
  settings: { composition_mode: 'legacy', anchor_shot_ids: [] }, shots: [], tags,
})
const edit = async (field, value) => act(async () => {
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(field, value)
  field.dispatchEvent(new Event('input', { bubbles: true }))
})
const editTextarea = async (field, value) => act(async () => {
  Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set.call(field, value)
  field.dispatchEvent(new Event('input', { bubbles: true }))
})
const changeHash = async (hash) => {
  await act(async () => {
    window.location.hash = hash
    window.dispatchEvent(new Event('hashchange'))
    await drain()
  })
}
const traverse = async (direction, expectedHash) => {
  await act(async () => {
    window.history[direction]()
    window.dispatchEvent(new Event('hashchange'))
    for (let attempt = 0; attempt < 30 && window.location.hash !== expectedHash; attempt += 1) await drain()
  })
  expect(window.location.hash).toBe(expectedHash)
}
const mount = async (getSession) => {
  window.history.replaceState(null, '', '#/session/41')
  vi.spyOn(window, 'matchMedia').mockReturnValue({ addEventListener() {}, removeEventListener() {} })
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    if (path === '/api/sessions/41' || path === '/api/sessions/42') return getSession(path)
    if (path === '/api/comfy/status') return { online: false }
    if (path === '/api/config' || path === '/api/comfy/models') return {}
    if (path === '/api/wardrobe') return { garments: [], outfits: [] }
    return []
  })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => { root.render(<App />); await drain() })
}

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  vi.restoreAllMocks()
  window.history.replaceState(null, '', '#/')
})

it('hides A while B loads, keeps tag drafts per session, and patches only the visible route', async () => {
  const a = session(41, ['from-A'])
  const b = session(42, ['from-B'])
  const pendingB = deferred()
  let bReads = 0
  await mount((path) => {
    if (path.endsWith('/41')) return a
    bReads += 1
    return bReads === 1 ? pendingB.promise : b
  })
  expect(container.querySelector('.session-header h1').textContent).toBe('Session 41')
  await edit(container.querySelector('[aria-label="New session tag"]'), 'draft-A')

  await changeHash('#/session/42')
  expect(container.querySelector('.session-header h1')).toBeNull()
  expect(container.querySelector('[aria-label="New session tag"]')).toBeNull()
  expect(container.textContent).toContain('Loading…')

  await act(async () => { pendingB.resolve(b); await drain() })
  expect(container.querySelector('.session-header h1').textContent).toBe('Session 42')
  const tagInput = container.querySelector('[aria-label="New session tag"]')
  expect(tagInput.value).toBe('')
  await edit(tagInput, 'new-from-B')
  const patch = vi.spyOn(api, 'patch').mockResolvedValue({})
  await act(async () => {
    tagInput.closest('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await drain()
  })
  expect(patch).toHaveBeenCalledTimes(1)
  expect(patch).toHaveBeenCalledWith('/api/sessions/42', { tags: ['from-B', 'new-from-B'] })

  await traverse('back', '#/session/41')
  expect(container.querySelector('.session-header h1').textContent).toBe('Session 41')
  expect(container.querySelector('[aria-label="New session tag"]').value).toBe('draft-A')
  await traverse('forward', '#/session/42')
  expect(container.querySelector('.session-header h1').textContent).toBe('Session 42')
  expect(container.querySelector('[aria-label="New session tag"]').value).toBe('')
})

it('ignores a late B response after browser Back returns to A', async () => {
  const a = session(41, ['from-A'])
  const b = session(42, ['from-B'])
  const pendingB = deferred()
  let bReads = 0
  await mount((path) => {
    if (path.endsWith('/41')) return a
    bReads += 1
    return bReads === 1 ? pendingB.promise : b
  })

  await changeHash('#/session/42')
  expect(container.querySelector('.session-header h1')).toBeNull()
  await traverse('back', '#/session/41')
  expect(container.querySelector('.session-header h1').textContent).toBe('Session 41')
  await act(async () => { pendingB.resolve(b); await drain() })
  expect(container.querySelector('.session-header h1').textContent).toBe('Session 41')
  expect(container.textContent).toContain('from-A')
  expect(container.textContent).not.toContain('from-B')
})

it('does not display a session payload whose ID differs from the requested route', async () => {
  await mount(() => session(41, ['from-A']))
  await changeHash('#/session/42')
  expect(container.querySelector('.session-header h1')).toBeNull()
  expect(container.querySelector('[aria-label="New session tag"]')).toBeNull()
  expect(container.textContent).toContain('The session response did not match the requested session.')
})

it('keeps a dirty resource-v1 plan with A and blocks displaying it under B', async () => {
  const resourceSession = (id) => ({ ...session(id, []), settings: { composition_mode: 'resource-v1' } })
  const plan = { version: 'resource-v1', look: 'Saved A look', initial_wardrobe: '', selected_resources: [],
    wardrobe_changes: [], takes: [{ take_id: 'take-41', label: 'Portrait', camera: '', framing: '', pose: '', expression: '' }] }
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    if (path === '/api/sessions/41') return resourceSession(41)
    if (path === '/api/sessions/42') return resourceSession(42)
    if (path === '/api/config' || path === '/api/comfy/models') return {}
    return []
  })
  vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => (
    path.endsWith('/full-auto') ? { current_run: null } : { plan_revision: 3, plan }
  ))
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => {
    root.render(<SessionView id={41} initialSession={resourceSession(41)} initialPlan={plan}
      initialRevision={3} initialActiveStep="constants" />)
    await drain()
  })
  await editTextarea(container.querySelector('#session-41-look'), 'Unsaved A look')

  await act(async () => { root.render(<SessionView id={42} />); await drain() })
  expect(container.textContent).toContain('Unsaved changes belong to Session 41.')
  expect(container.querySelector('h1')).toBeNull()
  expect(container.querySelector('textarea')).toBeNull()

  await act(async () => {
    root.render(<SessionView id={41} />)
    await drain()
  })
  expect(container.querySelector('#session-41-look').value).toBe('Unsaved A look')
})
