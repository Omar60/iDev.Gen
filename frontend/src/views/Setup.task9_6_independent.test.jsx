// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Setup from './Setup.jsx'

const jsonResponse = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status >= 400 ? 'Error' : 'OK',
  json: async () => structuredClone(body),
})

const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

const config = (overrides = {}) => ({
  comfy_url: 'http://127.0.0.1:8188',
  comfy_output_dir: '',
  lora_dir: '',
  data_dir: 'data',
  llm_url: 'http://assistant.local/v1',
  llm_model: '',
  llm_vision_model: '',
  llm_key: '',
  checkpoints: {},
  room_libraries: [],
  ...overrides,
})

let container
let root

function mount() {
  act(() => root.render(React.createElement(Setup)))
}

async function drain() {
  await act(async () => {
    for (let index = 0; index < 32; index += 1) await Promise.resolve()
  })
}

async function click(button) {
  expect(button).toBeTruthy()
  await act(async () => { button.click() })
  await drain()
}

function field(labelText) {
  const label = Array.from(container.querySelectorAll('label'))
    .find((candidate) => candidate.textContent.trim() === labelText)
  expect(label).toBeTruthy()
  return label.parentElement.querySelector('input, select')
}

function setValue(element, value) {
  act(() => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(element, value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
    element.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

function saveButton() {
  return Array.from(container.querySelectorAll('button'))
    .find((button) => button.textContent.trim() === 'Save')
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(async () => {
  await act(async () => root?.unmount())
  container?.remove()
  container = null
  root = null
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Task 9.6 Setup vision selection contract', () => {
  it('stores a detected visual text model explicitly even when both IDs are the same', async () => {
    let stored = config({ llm_model: 'shared-visual-model' })
    const calls = []
    vi.stubGlobal('fetch', vi.fn(async (path, init = {}) => {
      const method = (init.method || 'GET').toUpperCase()
      calls.push({ path: String(path), method, body: init.body ? JSON.parse(init.body) : null })
      if (method === 'GET' && path === '/api/config') return jsonResponse(stored)
      if (method === 'POST' && path === '/api/llm/models') {
        return jsonResponse({
          url: 'http://assistant.local/v1',
          models: [
            { id: 'text-only-model', vision: false },
            { id: 'shared-visual-model', vision: true },
          ],
        })
      }
      if (method === 'PATCH' && path === '/api/config') {
        stored = { ...stored, ...JSON.parse(init.body) }
        return jsonResponse({ restart_required: false })
      }
      throw new Error(`Unexpected request: ${method} ${path}`)
    }))

    mount()
    await drain()

    expect(field('Vision model (optional)').value).toBe('shared-visual-model')
    expect(Array.from(field('Vision model (optional)').options).map((option) => option.value))
      .toContain('shared-visual-model')
    await click(saveButton())

    const saved = calls.find((call) => call.method === 'PATCH' && call.path === '/api/config')
    expect(saved.body.llm_model).toBe('shared-visual-model')
    expect(saved.body.llm_vision_model).toBe('shared-visual-model')
  })

  it('does not auto-select an unverified model and keeps a typed local declaration', async () => {
    let stored = config({ llm_model: 'text-only-model' })
    const calls = []
    vi.stubGlobal('fetch', vi.fn(async (path, init = {}) => {
      const method = (init.method || 'GET').toUpperCase()
      calls.push({ path: String(path), method, body: init.body ? JSON.parse(init.body) : null })
      if (method === 'GET' && path === '/api/config') return jsonResponse(stored)
      if (method === 'POST' && path === '/api/llm/models') {
        return jsonResponse({
          url: 'http://assistant.local/v1',
          models: [{ id: 'text-only-model', vision: false }],
        })
      }
      if (method === 'PATCH' && path === '/api/config') {
        stored = { ...stored, ...JSON.parse(init.body) }
        return jsonResponse({ restart_required: false })
      }
      throw new Error(`Unexpected request: ${method} ${path}`)
    }))

    mount()
    await drain()

    const vision = field('Vision model (optional)')
    expect(vision.tagName).toBe('INPUT')
    expect(vision.value).toBe('')
    setValue(vision, 'operator-declared-vision-model')
    await click(saveButton())

    const saved = calls.find((call) => call.method === 'PATCH' && call.path === '/api/config')
    expect(saved.body.llm_vision_model).toBe('operator-declared-vision-model')
  })

  it('discards an old endpoint discovery that resolves after the endpoint changes', async () => {
    const oldDiscovery = deferred()
    const stored = config({
      llm_url: 'http://old-assistant.local/v1',
      llm_model: 'old-text-model',
      llm_vision_model: 'old-vision-model',
    })
    const calls = []
    vi.stubGlobal('fetch', vi.fn(async (path, init = {}) => {
      const method = (init.method || 'GET').toUpperCase()
      calls.push({ path: String(path), method, body: init.body ? JSON.parse(init.body) : null })
      if (method === 'GET' && path === '/api/config') return jsonResponse(stored)
      if (method === 'POST' && path === '/api/llm/models') return oldDiscovery.promise
      throw new Error(`Unexpected request: ${method} ${path}`)
    }))

    mount()
    await drain()
    expect(calls.some((call) => call.method === 'POST' && call.body.url === 'http://old-assistant.local/v1'))
      .toBe(true)

    setValue(field('Prompt assistant endpoint'), 'http://new-assistant.local/v1')
    oldDiscovery.resolve(jsonResponse({
      url: 'http://old-assistant.local/v1',
      models: [{ id: 'old-vision-model', vision: true }],
    }))
    await drain()

    expect(field('Prompt assistant endpoint').value).toBe('http://new-assistant.local/v1')
    expect(field('Model').value).toBe('')
    expect(field('Vision model (optional)').value).toBe('')
    expect(container.textContent).not.toContain('old-vision-model — not on this endpoint')
  })
})
