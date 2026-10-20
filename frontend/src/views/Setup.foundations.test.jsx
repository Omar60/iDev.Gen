// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Setup from './Setup.jsx'

const originalKey = '  invented credential value  '
const initialConfig = () => ({
  comfy_url: 'http://comfy.example', comfy_output_dir: 'studio/output', lora_dir: 'studio/loras',
  data_dir: 'studio/data', data_dir_resolved: 'studio/data',
  llm_url: 'https://assistant.example/v1', llm_model: 'text-model', llm_vision_model: 'vision-model',
  llm_key: originalKey, output_dir_ok: true, lora_dir_ok: true, llm_ok: true,
  checkpoints: { 'studio.safetensors': { steps: 12, cfg: 1.5 } },
  room_libraries: [{ name: 'Studio rooms', seed_file: 'studio/rooms.json', enabled: true, weight: 1 }],
})
const response = (body, status = 200) => ({
  ok: status < 400, status, statusText: status >= 400 ? 'Error' : 'OK', json: async () => structuredClone(body),
})
const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { resolve, promise }
}
let root, container, stored, calls, saveResult, detectResult, loadResult
const button = (name) => [...container.querySelectorAll('button')].find(el => (el.getAttribute('aria-label') || el.textContent).trim() === name)
const field = (name) => container.querySelector(`[name="${name}"]`)
async function drain() {
  await act(async () => { for (let i = 0; i < 24; i++) await Promise.resolve() })
}
async function mount() { await act(async () => root.render(<Setup />)); await drain() }
async function click(element) { expect(element).toBeTruthy(); await act(async () => element.click()); await drain() }
async function edit(name, value) {
  const element = field(name)
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(element, value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

beforeEach(() => {
  stored = initialConfig()
  calls = []
  saveResult = null
  detectResult = null
  loadResult = null
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  vi.stubGlobal('fetch', vi.fn(async (path, init = {}) => {
    const method = init.method || 'GET'
    const body = init.body ? JSON.parse(init.body) : null
    calls.push({ path, method, body })
    if (method === 'GET' && path === '/api/config') return loadResult || response(stored)
    if (method === 'POST' && path === '/api/llm/models') return response({ url: stored.llm_url, models: [] })
    if (method === 'POST' && path === '/api/config/detect') return detectResult || response({
      comfy_root: 'studio/comfy', comfy_output_dir: { exists: true, path: 'detected/output' },
      lora_dir: { exists: false, path: 'detected/loras' },
    })
    if (method === 'PATCH' && path === '/api/config') {
      if (saveResult) return saveResult
      stored = { ...stored, ...body }
      return response({ restart_required: false })
    }
    throw new Error(`Unexpected fixture request: ${method} ${path}`)
  }))
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

it('masks a loaded key by default and changes only visibility when toggled', async () => {
  await mount()
  const key = field('llm_key')
  expect(key.type).toBe('password')
  expect(key.value).toBe(originalKey)
  expect(container.textContent).not.toContain(originalKey)
  const toggle = button('Show API key')
  expect(toggle.type).toBe('button')
  expect(toggle.getAttribute('aria-controls')).toBe(key.id)
  expect(key.autocomplete).toBe('off')
  toggle.focus()
  const before = calls.length
  await click(toggle)
  expect(key.type).toBe('text')
  expect(key.value).toBe(originalKey)
  expect(document.activeElement).toBe(toggle)
  expect(button('Hide API key')).toBe(toggle)
  await click(toggle)
  expect(key.type).toBe('password')
  expect(calls).toHaveLength(before)
})

it('saves the unchanged raw key and complete configuration through the existing transport', async () => {
  const baseline = structuredClone(stored)
  await mount()
  await click(button('Save'))
  expect(calls.find(call => call.method === 'PATCH')).toEqual({
    method: 'PATCH', path: '/api/config', body: {
      comfy_url: baseline.comfy_url, comfy_output_dir: baseline.comfy_output_dir,
      lora_dir: baseline.lora_dir, data_dir: baseline.data_dir,
      llm_url: baseline.llm_url, llm_model: baseline.llm_model, llm_vision_model: baseline.llm_vision_model,
      llm_key: originalKey, checkpoints: baseline.checkpoints, room_libraries: baseline.room_libraries,
    },
  })
  expect(field('llm_key').type).toBe('password')
  expect(container.querySelector('.ui-form-footer [role="status"]').textContent).toBe('Saved.')
})

it('preserves edited key whitespace for discovery and saving, and allows explicit clearing', async () => {
  await mount()
  const edited = '  replacement fixture credential  '
  await edit('llm_key', edited)
  await click(button('Show API key'))
  await click(button('List its models'))
  expect(calls.filter(call => call.path === '/api/llm/models').at(-1).body)
    .toEqual({ url: stored.llm_url, key: edited })
  await click(button('Save'))
  expect(calls.filter(call => call.method === 'PATCH').at(-1).body.llm_key).toBe(edited)
  await edit('llm_key', '')
  await click(button('Save'))
  expect(calls.filter(call => call.method === 'PATCH').at(-1).body.llm_key).toBe('')
})

it('omits an absent room registry and preserves checkpoint settings on save', async () => {
  delete stored.room_libraries
  await mount()
  await click(button('Save'))
  const payload = calls.find(call => call.method === 'PATCH').body
  expect(payload).not.toHaveProperty('room_libraries')
  expect(payload.checkpoints).toEqual(initialConfig().checkpoints)
})

it('retains explicit Save activation instead of saving on implicit form submission', async () => {
  await mount()
  const event = new Event('submit', { bubbles: true, cancelable: true })
  await act(async () => container.querySelector('form').dispatchEvent(event))
  expect(event.defaultPrevented).toBe(true)
  expect(calls.some(call => call.method === 'PATCH')).toBe(false)
  expect(button('Save').type).toBe('button')
})

it('associates every editable field with a visible label and existing help text', async () => {
  await mount()
  const controls = [...container.querySelectorAll('input, select')]
  expect(controls).toHaveLength(8)
  for (const control of controls) {
    expect(container.querySelector(`label[for="${control.id}"]`)?.textContent).toBeTruthy()
    expect(control.name).toBeTruthy()
    expect(container.querySelector(`#${control.getAttribute('aria-describedby')}`)?.textContent).toBeTruthy()
  }
  expect([...container.querySelectorAll('h2')].map(el => el.textContent))
    .toEqual(['ComfyUI and storage', 'Prompt assistant', 'Current state'])
})

it('announces and focuses a save error without losing the entered masked key', async () => {
  await mount()
  await edit('llm_key', 'unsaved fixture credential')
  saveResult = response({ detail: 'Fixture: configuration could not be saved.' }, 500)
  await click(button('Save'))
  const error = container.querySelector('[role="alert"]')
  expect(error.textContent).toContain('configuration could not be saved')
  expect(document.activeElement).toBe(error)
  expect(field('llm_key').value).toBe('unsaved fixture credential')
  expect(field('llm_key').type).toBe('password')
  expect(button('Save').disabled).toBe(false)
})

it('blocks duplicate saves and announces progress while a save is pending', async () => {
  await mount()
  const pending = deferred()
  saveResult = pending.promise
  button('Save').focus()
  await click(button('Save'))
  expect(button('Saving…').getAttribute('aria-disabled')).toBe('true')
  expect(document.activeElement).toBe(button('Saving…'))
  expect(container.querySelector('.ui-form-footer [role="status"]').textContent).toContain('Saving')
  await click(button('Saving…'))
  expect(calls.filter(call => call.method === 'PATCH')).toHaveLength(1)
  await act(async () => pending.resolve(response({ restart_required: true })))
  await drain()
  expect(button('Save').getAttribute('aria-disabled')).toBe('false')
  expect(document.activeElement).toBe(button('Save'))
  expect(container.querySelector('.ui-form-footer [role="status"]').textContent).toContain('Restart the app')
})

it('shows an accessible loading state and a focused load failure', async () => {
  const pending = deferred()
  loadResult = pending.promise
  await mount()
  expect(container.querySelector('[role="status"]').textContent).toBe('Loading…')
  await act(async () => pending.resolve(response({ detail: 'Fixture: configuration unavailable.' }, 503)))
  await drain()
  expect(container.querySelector('[role="alert"]').textContent).toContain('configuration unavailable')
  expect(document.activeElement).toBe(container.querySelector('[role="alert"]'))
})

it('keeps detection as an explicit action and fills only folders that exist', async () => {
  await mount()
  expect(calls.some(call => call.path === '/api/config/detect')).toBe(false)
  const pending = deferred()
  detectResult = pending.promise
  button('Detect from ComfyUI').focus()
  await click(button('Detect from ComfyUI'))
  expect(button('Detecting…').getAttribute('aria-disabled')).toBe('true')
  expect(document.activeElement).toBe(button('Detecting…'))
  await click(button('Detecting…'))
  expect(calls.filter(call => call.path === '/api/config/detect')).toHaveLength(1)
  await act(async () => pending.resolve(response({
    comfy_root: 'studio/comfy', comfy_output_dir: { exists: true, path: 'detected/output' },
    lora_dir: { exists: false, path: 'detected/loras' },
  })))
  await drain()
  expect(field('comfy_output_dir').value).toBe('detected/output')
  expect(field('lora_dir').value).toBe('studio/loras')
  expect(field('llm_key').value).toBe(originalKey)
  expect(document.activeElement).toBe(button('Detect from ComfyUI'))
  expect(calls.some(call => call.method === 'PATCH')).toBe(false)
})
