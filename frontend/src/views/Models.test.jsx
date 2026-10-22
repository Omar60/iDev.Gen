// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Models, { ModelForm } from './Models.jsx'
import ModelDetail from './ModelDetail.jsx'
import ModelPreview from '../components/ModelPreview.jsx'
import CanvasSize from './CanvasSize.jsx'
import { go } from '../App.jsx'

vi.mock('../App.jsx', () => ({ go: vi.fn() }))

const model = () => ({ id: 7, name: 'Ada', lora_name: 'characters/ada.safetensors', trigger: '4da woman',
  lora_strength: 0.9, workflow_id: 12, base_positive: 'photo', base_negative: 'blur', notes: 'Keep this note',
  settings: { width: 832, height: 1216, steps: 8, cfg: 1, checkpoint: 'studio.safetensors', sampler: 'euler', scheduler: 'simple', custom: false },
  sessions: [{ id: 20, name: '   ', status: 'draft', cover_shot_id: null, done_count: 0, shot_count: 0 }], session_count: 1 })
const workflows = [{ id: 12, name: 'Studio', kind: 't2i' }, { id: 13, name: 'Edit', kind: 'edit' }]
const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => structuredClone(data) })
let container, root, fetchMock, records, detail, failure, calls, config
const drain = () => act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve() })
async function render(element = <Models />) { await act(async () => root.render(element)); await drain() }
const button = (name) => [...container.querySelectorAll('button')].find(b => b.textContent.trim() === name)
const field = (name, scope = container) => [...scope.querySelectorAll('input,select,textarea')].find(el =>
  [...(el.labels || [])].some(label => label.textContent.trim() === name) || el.getAttribute('aria-label') === name)
async function click(el) { expect(el).toBeTruthy(); await act(async () => el.click()); await drain() }
async function change(el, value) {
  expect(el).toBeTruthy()
  await act(async () => {
    const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype
    if (el.tagName === 'SELECT') el.value = value
    else Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value)
    el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }))
  })
}
async function submit() {
  await act(async () => container.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  await drain()
}
beforeEach(() => {
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  detail = model(); records = [detail]; failure = ''; calls = []
  config = { llm_ok: false, checkpoints: { 'other.safetensors': { steps: 16, cfg: 2 } } }
  fetchMock = vi.fn(async (url, options = {}) => {
    const method = options.method || 'GET'
    if (method !== 'GET') {
      const body = options.body ? JSON.parse(options.body) : undefined
      calls.push({ url, method, body })
      if (failure === 'save') return response({ detail: 'Fixture save failed' }, 503)
      if (method === 'PATCH') detail = { ...detail, ...body }
      return response(method === 'POST' ? { id: 41 } : { ok: true })
    }
    if (failure === url) return response({ detail: 'Fixture load failed' }, 503)
    const data = { '/api/models': records, '/api/models/7': detail, '/api/workflows': workflows,
      '/api/comfy/loras': { loras: ['characters/ada.safetensors'] },
      '/api/comfy/models': { checkpoints: ['studio.safetensors', 'other.safetensors'], samplers: ['euler'], schedulers: ['simple'] },
      '/api/config': config,
      '/api/rooms': { rooms: [], libraries: [] } }
    if (!(url in data)) throw new Error(`Unexpected fixture request: ${url}`)
    return response(data[url])
  })
  vi.stubGlobal('fetch', fetchMock); vi.mocked(go).mockClear()
})
afterEach(() => { act(() => root.unmount()); container.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('keeps inventory order and real links, full names and distinct missing/broken previews', async () => {
  records = [model(), { ...model(), id: 3, name: 'A'.repeat(180), lora_name: '', session_count: 0 }, { ...model(), id: 9, name: '   ' }]
  await render()
  const links = [...container.querySelectorAll('.model-card')]
  expect(links.map(a => a.getAttribute('href'))).toEqual(['#/model/7', '#/model/3', '#/model/9'])
  expect(links[1].textContent).toContain('A'.repeat(180)); expect(links[2].textContent).toContain('Model #9')
  expect(links[1].textContent).toContain('No LoRA selected'); expect(links[1].textContent).toContain('No preview')
  const image = links[0].querySelector('img')
  expect([image.alt, image.width, image.height, image.loading]).toEqual(['', 96, 128, 'lazy'])
  await act(async () => image.dispatchEvent(new Event('error')))
  expect(links[0].textContent).toContain('Preview unavailable')
  const modifiedClick = new MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true })
  links[0].dispatchEvent(modifiedClick); expect(modifiedClick.defaultPrevented).toBe(false)
  expect(records[2].name).toBe('   ')
})

it('retries focused load errors and distinguishes an empty inventory from loading', async () => {
  failure = '/api/models'; await render()
  expect(document.activeElement).toBe(container.querySelector('[role=alert]'))
  expect(container.textContent).not.toContain('No models yet')
  failure = ''; records = []; await click(button('Retry loading models'))
  expect(container.textContent).toContain('No models yet')
  await click(button('Create your first model')); expect(document.activeElement).toBe(field('Name'))
})

it('creates with the original defaults and supports keyboard form submission', async () => {
  await render(); await click(button('+ New model'))
  expect(document.activeElement).toBe(field('Name'))
  await change(field('Name'), 'New character'); await submit()
  expect(calls).toEqual([{ url: '/api/models', method: 'POST', body: {
    name: 'New character', lora_name: '', trigger: '', lora_strength: 1,
    base_positive: '', base_negative: '', workflow_id: null, notes: '',
    settings: { width: 832, height: 1216, steps: 8, cfg: 1 },
  } }])
  expect(go).toHaveBeenCalledWith('/model/41')
})

it('retains failed creation entries, focuses the error, then returns focus on cancel', async () => {
  await render(); await click(button('+ New model')); await change(field('Name'), 'Keep draft')
  failure = 'save'; await submit()
  expect(field('Name').value).toBe('Keep draft'); expect(document.activeElement).toBe(container.querySelector('[role=alert]'))
  expect(button('Create model').disabled).toBe(false)
  await click(button('Cancel creation')); expect(document.activeElement).toBe(button('+ New model'))
})

it('associates every model field and help, preserves unavailable choices, numbers, canvas and extra settings', async () => {
  let draft = { ...model(), lora_name: 'missing.safetensors', workflow_id: 99,
    settings: { ...model().settings, checkpoint: 'missing.safetensors', sampler: 'missing', scheduler: 'missing' } }
  function Harness() { const [value, setValue] = React.useState(draft); draft = value
    return <><ModelForm form={value} setForm={setValue} loras={[]} workflows={[]} />
      <ModelForm form={model()} setForm={() => {}} loras={[]} workflows={[]} /></> }
  await render(<Harness />)
  const controls = [...container.querySelectorAll('input,select,textarea')]
  expect(controls.every(el => el.labels.length === 1)).toBe(true)
  expect(new Set(controls.map(el => el.id)).size).toBe(controls.length)
  for (const el of controls.filter(el => el.hasAttribute('aria-describedby'))) {
    expect(document.getElementById(el.getAttribute('aria-describedby'))).toBeTruthy()
  }
  expect(field('LoRA (from ComfyUI)').selectedOptions[0].textContent).toContain('not in ComfyUI')
  expect(field('Workflow').selectedOptions[0].textContent).toContain('Workflow #99')
  expect(field('Base model').value).toBe('missing.safetensors'); expect(field('Sampler').value).toBe('missing')
  await change(field('Workflow'), ''); await change(field('Steps'), '12'); await change(field('CFG'), '1.5')
  await change(field('LoRA strength'), '0.75'); await change(field('Canvas'), '1:1')
  expect(draft.workflow_id).toBe(null); expect(draft.settings.steps).toBe(12); expect(draft.settings.cfg).toBe(1.5)
  expect(draft.lora_strength).toBe(0.75); expect(draft.settings.custom).toBe(false)
  expect([draft.settings.width, draft.settings.height]).toEqual([1024, 1024])
})

it('resets a failed preview when its source changes and leaves unopted canvas callers unchanged', async () => {
  await render(<ModelPreview src="/first" />)
  await act(async () => container.querySelector('img').dispatchEvent(new Event('error')))
  await render(<ModelPreview src="/second" />); expect(container.querySelector('img').getAttribute('src')).toBe('/second')
  const changed = vi.fn(); await render(<CanvasSize onChange={changed} />)
  expect(container.querySelector('.ui-control')).toBe(null)
  expect([...container.querySelectorAll('input')].map(el => el.value)).toEqual(['832', '1216'])
  await change(container.querySelector('select'), '1:1'); expect(changed).toHaveBeenCalledWith(1024, 1024)
})

it('reuses gallery rendering while a model draft changes and refreshes after a new response', async () => {
  fetchMock.mockImplementationOnce(async () => ({ ok: true, json: async () => records }))
  const mapping = vi.spyOn(records, 'map')
  await render()
  expect(mapping).toHaveBeenCalledTimes(1)
  const card = container.querySelector('.model-card')
  await click(button('+ New model')); await change(field('Name'), 'New character')
  await change(field('Trigger word'), 'new character')
  expect(mapping).toHaveBeenCalledTimes(1)
  expect(container.querySelector('.model-card')).toBe(card)
  records = [{ ...model(), id: 42, name: 'Fresh response' }]
  await submit()
  expect(container.querySelector('.model-card').getAttribute('href')).toBe('#/model/42')
  expect(container.querySelector('.model-card').textContent).toContain('Fresh response')
})

it('reuses model-session cards during editing and invalidates them after saving', async () => {
  fetchMock.mockImplementationOnce(async () => ({ ok: true, json: async () => detail }))
  const mapping = vi.spyOn(detail.sessions, 'map')
  await render(<ModelDetail id={7} />)
  expect(mapping).toHaveBeenCalledTimes(1)
  const card = container.querySelector('.model-card')
  await click(button('Edit model')); await change(field('Name'), 'Updated character')
  await change(field('Trigger word'), 'updated')
  expect(mapping).toHaveBeenCalledTimes(1)
  expect(container.querySelector('.model-card')).toBe(card)
  const original = fetchMock.getMockImplementation()
  fetchMock.mockImplementation((url, options) => url === '/api/models/7' && options.method === 'GET'
    ? Promise.resolve(response({ ...detail, sessions: [{ ...detail.sessions[0], id: 21, name: 'Fresh session response' }] }))
    : original(url, options))
  await submit()
  expect(container.querySelector('h1').textContent).toBe('Updated character')
  expect(container.querySelector('.model-card').getAttribute('href')).toBe('#/session/21')
  expect(container.querySelector('.model-card').textContent).toContain('Fresh session response')
})

it('keeps preview source changes retryable and handles missing sources without extra DOM', async () => {
  await render(<ModelPreview src="/valid" />)
  const image = container.querySelector('img')
  await act(async () => image.dispatchEvent(new Event('load')))
  expect(container.querySelector('img')).toBe(image)
  await render(<ModelPreview src={null} />)
  expect(container.querySelector('img')).toBe(null); expect(container.textContent).toBe('No preview')
  await render(<ModelPreview src="/broken" />)
  await act(async () => container.querySelector('img').dispatchEvent(new Event('error')))
  expect(container.textContent).toBe('Preview unavailable')
  await render(<ModelPreview src="/valid" />)
  expect(container.querySelector('img').getAttribute('src')).toBe('/valid')
  await render(<ModelPreview src="/broken" />)
  expect(container.querySelector('img').getAttribute('src')).toBe('/broken')
})

it('preserves the Resources canvas override and custom dimensions outside studio forms', async () => {
  const changed = vi.fn()
  await render(<CanvasSize width={768} height={1360} onChange={changed} />)
  expect(container.querySelector('select').value).toBe('')
  expect(container.querySelector('.ui-control')).toBe(null)
  const inputs = container.querySelectorAll('input')
  expect([...inputs].map(el => el.value)).toEqual(['768', '1360'])
  await change(inputs[0], '776'); expect(changed).toHaveBeenLastCalledWith(776, 1360)
  await change(container.querySelector('select'), 'portrait'); expect(changed).toHaveBeenLastCalledWith(832, 1216)
})

it('shows model defaults, session status, blank-name IDs and resource setup as a separate real link', async () => {
  await render(<ModelDetail id={7} />)
  expect(container.querySelector('h1').textContent).toBe('Ada')
  expect(container.querySelector('a.ui-button').getAttribute('href')).toBe('#/resources/7')
  expect(container.querySelector('.model-card').getAttribute('href')).toBe('#/session/20')
  expect(container.querySelector('.model-card').textContent).toContain('Session #20')
  expect(container.querySelector('.model-card').textContent).toContain('draft')
  expect(container.textContent).toContain('Studio'); expect(calls).toHaveLength(0)
})

it('edits only selected model values, preserves opaque data and restores focus after saving', async () => {
  const initial = structuredClone(detail)
  await render(<ModelDetail id={7} />); await click(button('Edit model'))
  expect(document.activeElement).toBe(field('Name'))
  await change(field('Name'), 'Renamed'); await change(field('Workflow'), '')
  failure = 'save'; await submit()
  expect(document.activeElement).toBe(container.querySelector('[role=alert]')); expect(field('Name').value).toBe('Renamed')
  failure = ''; await submit()
  expect(calls.at(-1)).toEqual({ url: '/api/models/7', method: 'PATCH', body: { ...initial, name: 'Renamed', workflow_id: null } })
  expect(container.querySelector('form')).toBe(null); expect(document.activeElement).toBe(button('Edit model'))
  expect(container.querySelector('h1').textContent).toBe('Renamed')
})

it('keeps edit and legacy IDs unique and preserves the original empty legacy session contract', async () => {
  await render(<ModelDetail id={7} />); await click(button('Edit model')); await click(button('Legacy session'))
  const legacy = container.querySelector('#legacy-session-form')
  expect(document.activeElement).toBe(legacy); expect(legacy.tagName).toBe('SECTION')
  const ids = [...container.querySelectorAll('[id]')].map(el => el.id)
  expect(new Set(ids).size).toBe(ids.length)
  await click(button('Create session (0 photos)'))
  const request = calls.at(-1)
  expect(request.url).toBe('/api/sessions'); expect(request.method).toBe('POST')
  expect(request.body).toEqual({ model_id: 7, name: 'Session 2', look: expect.any(String), wardrobe: '',
    shots: [], workflow_id: 12, reference_workflow_id: null,
    settings: { ...detail.settings, lora_strength: 0.9, kind: 'shoot' },
    seed_mode: 'random', seed: 0, manner: 'directed', room_key: expect.any(String) })
  expect(go).toHaveBeenCalledWith('/session/41')
  await click(button('Cancel')); expect(document.activeElement).toBe(button('Legacy session'))
})

it('keeps legacy kind switching, edited prompts, fixed seed and reference workflow semantics', async () => {
  await render(<ModelDetail id={7} />); await click(button('Legacy session'))
  const legacy = container.querySelector('#legacy-session-form')
  await change(legacy.querySelector('table textarea'), 'A quiet portrait')
  await click(button('Photo edit'))
  expect(field('Reference workflow (edits)').value).toBe('13')
  await change(field('Seeds'), 'fixed'); await change(field('Starting seed'), '42')
  await change(field('Base model'), 'other.safetensors')
  await click(button('Create session (4 photos)'))
  expect(calls.at(-1).body).toMatchObject({ reference_workflow_id: 13, seed_mode: 'fixed', seed: 42,
    settings: { kind: 'edit', checkpoint: 'other.safetensors', steps: 16, cfg: 2 },
    shots: [{ prompt: 'A quiet portrait', reference: false, count: 4, wardrobe: null }] })
})

it('keeps the optional photo picker keyboard reachable without invoking the assistant', async () => {
  config.llm_ok = true
  await render(<ModelDetail id={7} />); await click(button('Legacy session'))
  const input = container.querySelector('input[type=file]')
  expect(input.hidden).toBe(false); expect(input.disabled).toBe(false)
  expect(input.getAttribute('aria-label')).toBe('Wardrobe from a photo')
  expect(input.getAttribute('accept')).toBe('image/png,image/jpeg,image/webp')
  input.focus(); expect(document.activeElement).toBe(input)
  expect(calls).toHaveLength(0)
})

it('retries detail load failures and keeps deletion behind its existing explicit confirmation', async () => {
  failure = '/api/models/7'; await render(<ModelDetail id={7} />)
  expect(document.activeElement).toBe(container.querySelector('[role=alert]'))
  failure = ''; await click(button('Retry loading model'))
  const confirm = vi.fn().mockReturnValue(false); vi.stubGlobal('confirm', confirm)
  await click(button('Delete')); expect(calls).toHaveLength(0)
  expect(confirm).toHaveBeenCalledWith('Delete the model "Ada" and all its sessions?')
  confirm.mockReturnValue(true); failure = 'save'; await click(button('Delete'))
  expect(container.querySelector('[role=alert]').textContent).toContain('Fixture save failed')
  expect(go).not.toHaveBeenCalled()
})
