// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Workflows from './Workflows.jsx'

const graph = {
  '1': { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: 'studio.safetensors' } },
  '3': { class_type: 'CLIPTextEncode', _meta: { title: 'Studio positive prompt' }, inputs: { text: 'A quiet studio.', empty: '', enabled: false, 'custom.value': 'Dotted widget value' } },
  '4': { class_type: 'CLIPTextEncode', inputs: { text: 'blur' } },
  '6': { class_type: 'KSampler', inputs: { seed: 9, steps: 12, cfg: 1.5, positive: ['3', 0], negative: ['4', 0] } },
}
const slots = ['positive', 'negative', 'seed', 'steps', 'cfg', 'width', 'custom_slot']
const detected = { positive: '3.inputs.text', negative: '4.inputs.text', seed: '6.inputs.seed', steps: '6.inputs.steps', cfg: '6.inputs.cfg' }
const detection = () => ({ node_map: { ...detected }, slots, nodes: Object.entries(graph).map(([id, node]) => ({
  id, class_type: node.class_type, title: node._meta?.title || '', widgets: Object.keys(node.inputs).filter(k => !Array.isArray(node.inputs[k])),
})) })
const initial = () => ({ id: 12, name: 'Studio workflow', kind: 't2i', graph: structuredClone(graph), node_map: {
  positive: '3.inputs.text', seed: '6.inputs.seed', steps: '6.inputs.positive', cfg: '99.inputs.value', width: '', future_saved: '3.inputs.empty',
} })
const response = (value, status = 200) => ({ ok: status < 400, status, statusText: 'Fixture response', json: async () => structuredClone(value) })
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
let container, root, saved, calls, failures, pending
const button = text => [...container.querySelectorAll('button')].find(el => (el.getAttribute('aria-label') || el.textContent).trim() === text)
const mapping = slot => container.querySelector(`[name="mapping-${slot}"]`)
const row = slot => mapping(slot).closest('li')
async function drain() { await act(async () => { for (let i = 0; i < 24; i++) await Promise.resolve() }) }
async function click(el) { expect(el).toBeTruthy(); await act(async () => el.click()); await drain() }
async function mount() { await act(async () => root.render(<Workflows />)); await drain() }
async function open() { await click(container.querySelector('.workflow-open')) }
async function edit(el, value) {
  await act(async () => {
    if (el.tagName === 'INPUT') Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(el, value)
    else el.value = value
    el.dispatchEvent(new Event(el.tagName === 'INPUT' ? 'input' : 'change', { bubbles: true }))
  })
}
async function upload(value, name = 'fixture.JSON') {
  const input = container.querySelector('input[type="file"]')
  Object.defineProperty(input, 'files', { configurable: true, value: [{ name, text: async () => typeof value === 'string' ? value : JSON.stringify(value) }] })
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })))
  await drain()
}

beforeEach(() => {
  saved = [initial()]; calls = []; failures = {}; pending = {}
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.stubGlobal('fetch', vi.fn(async (path, init = {}) => {
    const method = init.method || 'GET', key = `${method} ${path}`
    const body = init.body ? JSON.parse(init.body) : null
    calls.push({ method, path, body })
    if (pending[key]) return pending[key]
    if (failures[key]) return response({ detail: failures[key] }, 409)
    if (key === 'GET /api/workflows') return response(saved.map(({ graph: _, ...item }) => item))
    if (key === 'GET /api/workflows/12') return response(saved[0])
    if (key === 'POST /api/workflows/detect') return response(detection())
    if (key === 'PATCH /api/workflows/12') { saved[0] = { ...saved[0], ...body }; return response({ ok: true }) }
    if (key === 'POST /api/workflows') { saved.push({ id: 13, ...body }); return response({ id: 13, node_map: body.node_map }) }
    if (key === 'DELETE /api/workflows/12') { saved = []; return response({ ok: true }) }
    throw new Error(`Unexpected fixture request: ${key}`)
  }))
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('distinguishes initial loading, empty inventory and a focused recoverable load error', async () => {
  const task = deferred(); pending['GET /api/workflows'] = task.promise
  await mount()
  expect(container.textContent).toContain('Loading workflows…')
  expect(container.textContent).not.toContain('No workflows yet')
  await act(async () => task.resolve(response({ detail: 'Fixture: list unavailable.' }, 503))); await drain()
  expect(container.querySelector('[role="alert"]').textContent).toContain('list unavailable')
  expect(document.activeElement).toBe(container.querySelector('[role="alert"]'))
  expect(container.textContent).not.toContain('No workflows yet')
  delete pending['GET /api/workflows']; saved = []
  await click(button('Retry loading workflows'))
  expect(container.textContent).toContain('No workflows yet')
  expect(container.querySelector('[role="alert"]')).toBeNull()
  expect(document.activeElement).toBe(container.querySelector('#workflow-list-title'))
})

it('opens through the existing detail/detect contract and associates all controls with labels and help', async () => {
  await mount(); await open()
  expect(calls.find(c => c.path === '/api/workflows/detect').body).toEqual({ graph })
  expect(document.activeElement).toBe(container.querySelector('#workflow-editor-title'))
  for (const control of container.querySelectorAll('form input, form select')) {
    expect(container.querySelector(`label[for="${control.id}"]`)?.textContent).toBeTruthy()
    expect(control.name).toBeTruthy()
    for (const id of control.getAttribute('aria-describedby').split(' ')) expect(container.querySelector(`#${id}`)).toBeTruthy()
  }
  expect(row('positive').textContent).toContain('Studio positive prompt')
  expect(row('positive').textContent).toContain('A quiet studio.')
  expect(row('seed').textContent).toContain('Graph value · number')
  expect(row('custom_slot').textContent).toContain('custom_slot')
})

it('shows connected and missing saved paths without offering connected inputs as destinations', async () => {
  await mount(); await open()
  expect(row('steps').textContent).toContain('Connected')
  expect(row('steps').textContent).toContain('retained but ignored')
  expect(row('cfg').textContent).toContain('Missing widget')
  expect(mapping('steps').value).toBe('6.inputs.positive')
  expect([...mapping('steps').options].find(option => option.value === '6.inputs.positive').disabled).toBe(true)
  for (const select of container.querySelectorAll('form select[name^="mapping-"]')) {
    expect([...select.options].some(o => o.value === '6.inputs.negative')).toBe(false)
    expect([...select.options].filter(o => o.value === '6.inputs.positive').every(o => o.disabled)).toBe(true)
  }
  expect(container.querySelector('.workflow-connections').textContent).toContain('6.inputs.positive')
  expect(container.querySelector('.workflow-connections input')).toBeNull()
})

it('keeps full graph and unknown mappings when saving, and persists an explicit cleared mapping on reopen', async () => {
  await mount(); await open()
  await edit(mapping('positive'), '')
  await edit(container.querySelector('#workflow-name'), 'Renamed studio')
  await edit(container.querySelector('#workflow-kind'), 'guide')
  await click(button('Save workflow'))
  expect(calls.find(c => c.method === 'PATCH').body).toEqual({
    name: 'Renamed studio', kind: 'guide', graph,
    node_map: { ...initial().node_map, positive: '' },
  })
  expect(container.querySelector('form')).toBeNull()
  expect(document.activeElement).toBe(container.querySelector('#workflow-list-title'))
  await open()
  expect(mapping('positive').value).toBe('')
  expect(mapping('steps').value).toBe('6.inputs.positive')
  expect(mapping('cfg').value).toBe('99.inputs.value')
  expect(container.querySelector('#workflow-kind').value).toBe('guide')
})

it('describes a missing widget from graph data without reading inherited object properties', async () => {
  saved[0].node_map.cfg = '3.inputs.toString'
  await mount(); await open()
  expect(row('cfg').textContent).toContain('Missing widget')
  expect(row('cfg').querySelector('pre').textContent).toBe('Unavailable')
  await click(button('Save workflow'))
  expect(calls.find(c => c.method === 'PATCH').body.node_map.cfg).toBe('3.inputs.toString')
})

it('fills only empty mappings and keeps manual, connected, missing and future saved entries', async () => {
  await mount(); await open()
  await edit(mapping('positive'), '4.inputs.text')
  await click(button('Detect missing slots'))
  expect(mapping('positive').value).toBe('4.inputs.text')
  expect(mapping('negative').value).toBe('4.inputs.text')
  expect(mapping('steps').value).toBe('6.inputs.positive')
  expect(mapping('cfg').value).toBe('99.inputs.value')
  expect(calls.filter(c => c.path === '/api/workflows/detect')).toHaveLength(1)
  await click(button('Save workflow'))
  expect(calls.find(c => c.method === 'PATCH').body.node_map.future_saved).toBe('3.inputs.empty')
})

it('shows empty strings, booleans and decimal graph values without changing them', async () => {
  await mount(); await open()
  for (const [path, text] of [['3.inputs.empty','""'],['3.inputs.enabled','false'],['6.inputs.cfg','1.5'],['3.inputs.custom.value','"Dotted widget value"']]) {
    await edit(mapping('positive'), path)
    expect(row('positive').querySelector('pre').textContent).toBe(text)
  }
  await click(button('Save workflow'))
  expect(calls.find(c => c.method === 'PATCH').body.graph).toEqual(graph)
})

it('keeps drafts and focusable errors for failed detail, detection and save requests', async () => {
  await mount()
  failures['GET /api/workflows/12'] = 'Fixture: detail unavailable.'
  await open(); expect(container.querySelector('[role="alert"]').textContent).toContain('detail unavailable')
  delete failures['GET /api/workflows/12']; failures['POST /api/workflows/detect'] = 'Fixture: detection unavailable.'
  await open(); expect(container.querySelector('form')).toBeNull()
  delete failures['POST /api/workflows/detect']; await open()
  await edit(mapping('seed'), '6.inputs.steps')
  failures['PATCH /api/workflows/12'] = 'Fixture: save refused.'
  await click(button('Save workflow'))
  expect(mapping('seed').value).toBe('6.inputs.steps')
  expect(document.activeElement).toBe(container.querySelector('[role="alert"]'))
  expect(button('Save workflow').getAttribute('aria-disabled')).toBe('false')
})

it('blocks repeated saves and other actions while a save is pending, without implicit form saves', async () => {
  await mount(); await open()
  const submit = new Event('submit', { bubbles: true, cancelable: true })
  await act(async () => container.querySelector('form').dispatchEvent(submit))
  expect(submit.defaultPrevented).toBe(true)
  expect(calls.some(c => c.method === 'PATCH')).toBe(false)
  const task = deferred(); pending['PATCH /api/workflows/12'] = task.promise
  button('Save workflow').focus(); await click(button('Save workflow'))
  expect(document.activeElement).toBe(button('Save workflow'))
  expect(button('Save workflow').getAttribute('aria-disabled')).toBe('true')
  await click(button('Save workflow')); await click(button('Cancel')); await click(button('Delete Studio workflow'))
  expect(calls.filter(c => c.method === 'PATCH')).toHaveLength(1)
  expect(window.confirm).not.toHaveBeenCalled()
  await act(async () => task.resolve(response({ ok: true }))); await drain()
})

it('supports the keyboard import trigger and preserves API import/create semantics', async () => {
  saved = []; await mount()
  const input = container.querySelector('input[type="file"]')
  const picker = vi.spyOn(input, 'click')
  expect(button('Import workflow…').type).toBe('button')
  await click(button('Import workflow…')); expect(picker).toHaveBeenCalledOnce()
  await upload(graph)
  expect(input.value).toBe('')
  expect(container.querySelector('#workflow-name').value).toBe('fixture')
  expect(container.querySelector('#workflow-kind').value).toBe('')
  expect(container.textContent).toContain('Unsaved changes')
  expect(calls.some(c => c.path === '/api/workflows' && c.method === 'POST')).toBe(false)
  await click(button('Save workflow'))
  expect(calls.find(c => c.path === '/api/workflows' && c.method === 'POST').body).toEqual({ name: 'fixture', kind: '', graph, node_map: detected })
})

it('rejects malformed and editor-format imports before detection or persistence', async () => {
  await mount(); await upload('{')
  expect(container.querySelector('[role="alert"]')).toBeTruthy()
  await upload({ nodes: [], links: [] })
  expect(container.querySelector('[role="alert"]').textContent).toContain('Export (API)')
  expect(calls.some(c => c.method === 'POST')).toBe(false)
})

it('confirms unsaved cancellation/replacement and restores focus to the opening control', async () => {
  await mount(); await open()
  await edit(mapping('positive'), '')
  window.confirm.mockReturnValue(false)
  await click(button('Cancel'))
  expect(container.querySelector('form')).toBeTruthy()
  const before = calls.length
  await open(); expect(calls).toHaveLength(before)
  window.confirm.mockReturnValue(true)
  await click(button('Cancel'))
  expect(container.querySelector('form')).toBeNull()
  expect(document.activeElement).toBe(container.querySelector('.workflow-open'))
})

it('requires deletion confirmation, preserves blocked deletes and clears the editor only on success', async () => {
  await mount(); await open()
  window.confirm.mockReturnValue(false)
  await click(button('Delete Studio workflow'))
  expect(calls.some(c => c.method === 'DELETE')).toBe(false)
  window.confirm.mockReturnValue(true); failures['DELETE /api/workflows/12'] = 'Fixture: workflow is in use.'
  await click(button('Delete Studio workflow'))
  expect(container.querySelector('form')).toBeTruthy()
  expect(container.querySelector('[role="alert"]').textContent).toContain('in use')
  delete failures['DELETE /api/workflows/12']
  await click(button('Delete Studio workflow'))
  expect(container.querySelector('form')).toBeNull()
  expect(container.textContent).toContain('No workflows yet')
  expect(document.activeElement).toBe(container.querySelector('#workflow-list-title'))
})
