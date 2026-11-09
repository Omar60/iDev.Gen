// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../api.js'
import ModelDetail from './ModelDetail.jsx'

let container, root
const drain = () => new Promise((resolve) => setTimeout(resolve, 0))
const deferred = () => {
  let resolve, reject
  const promise = new Promise((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
const model = (id, name) => ({
  id, name, lora_name: `characters/model-${id}.safetensors`, trigger: `trigger-${id}`,
  lora_strength: 0.9, workflow_id: 12, base_positive: `Base prompt ${name}`,
  base_negative: 'blur', notes: `Notes ${name}`,
  settings: { width: 832, height: 1216, steps: 8, cfg: 1, checkpoint: 'base.safetensors', sampler: 'euler', scheduler: 'simple' },
  sessions: [], session_count: 0,
})
const models = { 7: model(7, 'Model A'), 8: model(8, 'Model B') }
const change = async (field, value) => act(async () => {
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(field, value)
  field.dispatchEvent(new Event('input', { bubbles: true }))
})
const click = async (name) => act(async () => {
  const button = [...container.querySelectorAll('button')].find((item) => item.textContent.trim() === name)
  expect(button).toBeTruthy()
  button.click()
  await drain()
})
const render = async (id) => act(async () => {
  root.render(<ModelDetail id={id} />)
  await drain()
})
const nameField = () => [...container.querySelectorAll('input')]
  .find((input) => [...input.labels].some((label) => label.textContent.trim() === 'Name'))
const legacyNameField = () => container.querySelector('input[name="session_name"]')
const editModel = async (name) => {
  await click('Edit model')
  await change(nameField(), name)
}
const submit = async () => act(async () => {
  container.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await drain()
})

beforeEach(() => {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    if (path === '/api/models/7') return models[7]
    if (path === '/api/models/8') return models[8]
    if (path === '/api/workflows') return [{ id: 12, name: 'Invented workflow', kind: 't2i' }]
    if (path === '/api/comfy/loras') return { loras: ['characters/model-7.safetensors', 'characters/model-8.safetensors'] }
    if (path === '/api/comfy/models') return { checkpoints: ['base.safetensors'], samplers: ['euler'], schedulers: ['simple'] }
    if (path === '/api/config') return { llm_ok: false }
    if (path === '/api/rooms') return { rooms: [], libraries: [] }
    throw new Error(`Unexpected fixture request: ${path}`)
  })
})

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  vi.restoreAllMocks()
})

it('hides A while B loads and keeps each edit draft and save on its own model', async () => {
  const pendingB = deferred()
  const get = vi.mocked(api.get)
  get.mockImplementation((path) => {
    if (path === '/api/models/8') return pendingB.promise
    return Promise.resolve(path === '/api/models/7' ? models[7] : path === '/api/workflows'
      ? [{ id: 12, name: 'Invented workflow', kind: 't2i' }]
      : path === '/api/comfy/loras'
        ? { loras: ['characters/model-7.safetensors', 'characters/model-8.safetensors'] }
        : path === '/api/comfy/models'
          ? { checkpoints: ['base.safetensors'], samplers: ['euler'], schedulers: ['simple'] }
          : path === '/api/config' ? { llm_ok: false } : { rooms: [], libraries: [] })
  })
  await render(7)
  await editModel('A draft')

  await render(8)
  expect(container.querySelector('h1')).toBeNull()
  expect(container.querySelector('form')).toBeNull()
  expect(container.textContent).toContain('Loading model…')

  await act(async () => { pendingB.resolve(models[8]); await drain() })
  expect(container.querySelector('h1').textContent).toBe('Model B')
  expect(container.querySelector('form')).toBeNull()

  await editModel('B draft')
  await render(7)
  expect(nameField().value).toBe('A draft')
  await render(8)
  expect(nameField().value).toBe('B draft')

  const patch = vi.spyOn(api, 'patch').mockResolvedValue({})
  await submit()
  expect(patch).toHaveBeenCalledTimes(1)
  expect(patch).toHaveBeenCalledWith('/api/models/8', { ...models[8], name: 'B draft' })
  await render(7)
  expect(nameField().value).toBe('A draft')
})

it('ignores an older A response that arrives after B is loaded', async () => {
  const pendingA = deferred()
  const get = vi.mocked(api.get)
  get.mockImplementation((path) => {
    if (path === '/api/models/7') return pendingA.promise
    if (path === '/api/models/8') return Promise.resolve(models[8])
    if (path === '/api/workflows') return Promise.resolve([])
    if (path === '/api/comfy/loras') return Promise.resolve({ loras: [] })
    if (path === '/api/comfy/models') return Promise.resolve({})
    if (path === '/api/config') return Promise.resolve({})
    if (path === '/api/rooms') return Promise.resolve({ rooms: [], libraries: [] })
    return Promise.resolve({})
  })

  await render(7)
  await render(8)
  expect(container.querySelector('h1').textContent).toBe('Model B')
  await act(async () => { pendingA.resolve(models[7]); await drain() })
  expect(container.querySelector('h1').textContent).toBe('Model B')
  expect(container.textContent).not.toContain('Model A')
})

it('rejects a model payload whose ID does not match the current route', async () => {
  vi.mocked(api.get).mockImplementation((path) => {
    if (path === '/api/models/7' || path === '/api/models/8') return Promise.resolve(models[7])
    if (path === '/api/workflows') return Promise.resolve([])
    if (path === '/api/comfy/loras') return Promise.resolve({ loras: [] })
    if (path === '/api/comfy/models') return Promise.resolve({})
    if (path === '/api/config') return Promise.resolve({})
    if (path === '/api/rooms') return Promise.resolve({ rooms: [], libraries: [] })
    return Promise.resolve({})
  })

  await render(7)
  await render(8)
  expect(container.querySelector('h1')).toBeNull()
  expect(container.querySelector('form')).toBeNull()
  expect(container.textContent).toContain('The model response did not match the requested model.')
})

it('keeps a failed B load isolated and allows retry without displaying A', async () => {
  const get = vi.mocked(api.get)
  let bReads = 0
  get.mockImplementation((path) => {
    if (path === '/api/models/7') return Promise.resolve(models[7])
    if (path === '/api/models/8') {
      bReads += 1
      return bReads === 1 ? Promise.reject(new Error('B load failed')) : Promise.resolve(models[8])
    }
    if (path === '/api/workflows') return Promise.resolve([])
    if (path === '/api/comfy/loras') return Promise.resolve({ loras: [] })
    if (path === '/api/comfy/models') return Promise.resolve({})
    if (path === '/api/config') return Promise.resolve({})
    if (path === '/api/rooms') return Promise.resolve({ rooms: [], libraries: [] })
    return Promise.resolve({})
  })

  await render(7)
  await render(8)
  expect(container.querySelector('h1')).toBeNull()
  expect(container.textContent).toContain('B load failed')
  expect(container.textContent).not.toContain('Model A')
  await click('Retry loading model')
  expect(container.querySelector('h1').textContent).toBe('Model B')
})

it('keeps a save started on A bound to A while the route changes to B', async () => {
  await render(7)
  await editModel('A saved draft')
  const pendingSave = deferred()
  const patch = vi.spyOn(api, 'patch').mockReturnValue(pendingSave.promise)
  await submit()
  expect(patch).toHaveBeenCalledWith('/api/models/7', { ...models[7], name: 'A saved draft' })

  await render(8)
  expect(container.querySelector('h1').textContent).toBe('Model B')
  expect(container.querySelector('form')).toBeNull()
  await act(async () => { pendingSave.resolve({}); await drain() })
  expect(container.querySelector('h1').textContent).toBe('Model B')
  expect(container.textContent).not.toContain('A saved draft')
})

it('keeps model edits made after an explicit save was sent', async () => {
  await render(7)
  await editModel('Submitted A name')
  const pendingSave = deferred()
  const patch = vi.spyOn(api, 'patch').mockReturnValueOnce(pendingSave.promise).mockResolvedValueOnce({})

  await submit()
  expect(patch).toHaveBeenCalledWith('/api/models/7', { ...models[7], name: 'Submitted A name' })
  await change(nameField(), 'Newer A name')

  await act(async () => { pendingSave.resolve({}); await drain() })
  expect(nameField()?.value).toBe('Newer A name')

  await submit()
  expect(patch).toHaveBeenCalledTimes(2)
  expect(patch).toHaveBeenLastCalledWith('/api/models/7', { ...models[7], name: 'Newer A name' })
})

it('keeps a newer A draft when a save response arrives after A→B→A navigation', async () => {
  await render(7)
  await editModel('Submitted A name')
  const pendingSave = deferred()
  const patch = vi.spyOn(api, 'patch').mockReturnValue(pendingSave.promise)

  await submit()
  await render(8)
  await render(7)
  expect(nameField().value).toBe('Submitted A name')
  await change(nameField(), 'Newer A name')

  await act(async () => { pendingSave.resolve({}); await drain() })
  expect(nameField()?.value).toBe('Newer A name')
  expect(patch).toHaveBeenCalledWith('/api/models/7', { ...models[7], name: 'Submitted A name' })
})

it('keeps a late A save error and its draft out of B', async () => {
  await render(7)
  await editModel('A pending draft')
  const pendingSave = deferred()
  vi.spyOn(api, 'patch').mockReturnValue(pendingSave.promise)
  await submit()
  await change(nameField(), 'A newer pending draft')
  await render(8)
  expect(container.querySelector('h1').textContent).toBe('Model B')

  await act(async () => { pendingSave.reject(new Error('A save failed')); await drain() })
  expect(container.querySelector('h1').textContent).toBe('Model B')
  expect(container.textContent).not.toContain('A save failed')
  expect(container.textContent).not.toContain('A pending draft')

  await render(7)
  expect(nameField().value).toBe('A newer pending draft')
})

it('keeps newer model edits and shows the error when their pending save fails', async () => {
  await render(7)
  await editModel('Submitted A name')
  const pendingSave = deferred()
  vi.spyOn(api, 'patch').mockReturnValue(pendingSave.promise)

  await submit()
  await change(nameField(), 'Newer A name')
  await act(async () => { pendingSave.reject(new Error('A save failed')); await drain() })

  expect(container.querySelector('[role=alert]').textContent).toContain('A save failed')
  expect(nameField().value).toBe('Newer A name')
})

it('keeps Legacy drafts and creation payloads bound to each model across A→B→A', async () => {
  const pendingCreate = deferred()
  const post = vi.spyOn(api, 'post').mockReturnValue(pendingCreate.promise)
  await render(7)
  await click('Legacy session')
  await change(legacyNameField(), 'A draft')

  await render(8)
  expect(legacyNameField()).toBeNull()
  expect(container.textContent).not.toContain('A draft')
  await click('Legacy session')
  expect(legacyNameField().value).toBe('Session 1')
  await change(legacyNameField(), 'B draft')

  await render(7)
  expect(legacyNameField().value).toBe('A draft')
  await click('Create session (0 photos)')
  expect(post).toHaveBeenCalledTimes(1)
  expect(post).toHaveBeenCalledWith('/api/sessions', expect.objectContaining({ model_id: 7, name: 'A draft' }))

  await act(async () => { pendingCreate.reject(Object.assign(new Error('Create rejected'), { status: 422 })); await drain() })
})

it('keeps a late successful A creation and newer edits out of B', async () => {
  const pendingCreate = deferred()
  const post = vi.spyOn(api, 'post').mockReturnValue(pendingCreate.promise)
  await render(7)
  await click('Legacy session')
  await change(legacyNameField(), 'Submitted A draft')
  await click('Create session (0 photos)')
  expect(post).toHaveBeenCalledWith('/api/sessions', expect.objectContaining({ model_id: 7, name: 'Submitted A draft' }))
  await change(legacyNameField(), 'Newer A draft')

  await render(8)
  expect(container.querySelector('h1').textContent).toBe('Model B')
  expect(legacyNameField()).toBeNull()
  expect(container.textContent).not.toContain('Submitted A draft')
  expect(container.textContent).not.toContain('Newer A draft')

  await act(async () => { pendingCreate.resolve({ id: 701 }); await drain() })
  expect(container.querySelector('h1').textContent).toBe('Model B')
  expect(container.querySelector('a[href="#/session/701"]')).toBeNull()
  expect(container.textContent).not.toContain('701')

  await render(7)
  expect(container.querySelector('h1').textContent).toBe('Model A')
  expect(legacyNameField().value).toBe('Newer A draft')
  expect(container.querySelector('a[href="#/session/701"]')).toBeTruthy()
  expect(container.textContent).toContain('Session #701 was created for this model.')
})

it('does not navigate after an A→B→A route cycle during a pending A creation', async () => {
  const pendingCreate = deferred()
  const post = vi.spyOn(api, 'post').mockReturnValue(pendingCreate.promise)
  await render(7)
  await click('Legacy session')
  await click('Create session (0 photos)')
  expect(post).toHaveBeenCalledWith('/api/sessions', expect.objectContaining({ model_id: 7 }))

  await render(8)
  await render(7)
  const initialHash = window.location.hash
  await act(async () => { pendingCreate.resolve({ id: 702 }); await drain() })

  expect(container.querySelector('h1').textContent).toBe('Model A')
  expect(container.querySelector('a[href="#/session/702"]')).toBeTruthy()
  expect(window.location.hash).toBe(initialHash)
})

it('keeps an uncertain A creation on A and requires an explicit retry', async () => {
  const pendingCreate = deferred()
  const retry = deferred()
  const post = vi.spyOn(api, 'post')
    .mockReturnValueOnce(pendingCreate.promise)
    .mockReturnValueOnce(retry.promise)
  await render(7)
  await click('Legacy session')
  await change(legacyNameField(), 'A uncertain draft')
  await click('Create session (0 photos)')

  await render(8)
  expect(legacyNameField()).toBeNull()
  expect(container.textContent).not.toContain('A uncertain draft')
  expect(container.textContent).not.toContain('The server may already have created this session.')
  expect([...container.querySelectorAll('button')].find((button) => button.textContent.trim() === 'Legacy session').disabled).toBe(true)

  await act(async () => { pendingCreate.reject(Object.assign(new Error('A response was lost'), { status: 500 })); await drain() })
  expect(legacyNameField()).toBeNull()
  expect(container.textContent).not.toContain('The server may already have created this session.')
  expect(post).toHaveBeenCalledTimes(1)

  await render(7)
  expect(legacyNameField().value).toBe('A uncertain draft')
  expect(container.textContent).toContain('The server may already have created this session.')
  await click('Retry session creation')
  expect(post).toHaveBeenCalledTimes(2)
  expect(post).toHaveBeenLastCalledWith('/api/sessions', expect.objectContaining({ model_id: 7, name: 'A uncertain draft' }))

  await act(async () => { retry.reject(Object.assign(new Error('Retry rejected'), { status: 422 })); await drain() })
})
