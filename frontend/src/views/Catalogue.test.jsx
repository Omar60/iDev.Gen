// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Catalogue from './Catalogue.jsx'
import SessionView from './SessionView.jsx'
import { api } from '../api.js'
import * as catalogueStore from '../kinds.js'
import * as wardrobeStore from '../wardrobe.js'

let root, container, rows, readings
const component = (id, extra = {}) => ({ id, concept_key: `camera-${id}`, slot: 'camera', manner: 'directed',
  family: 'front', faces: 'front', cameras: [], wording: `Invented prompt ${id}`, judge_label: `Visible view ${id}`,
  judged: 10, arrived: 7, contradicted: 2, state: 'tentative', retired_at: null, ...extra })
const wardrobe = { garments: [{ key: 'blue-jacket', wording: 'A blue jacket.' }], outfits: [{ key: 'studio', garments: 'blue-jacket' }] }
const drain = () => new Promise((resolve) => setTimeout(resolve, 0))
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const named = (name) => container.querySelector(`[aria-label="${name}"]`)
const button = (text) => [...container.querySelectorAll('button')].find((item) => item.textContent === text)
const field = (id) => container.querySelector(`#${id}`)
const click = async (element) => { expect(element).toBeTruthy(); await act(async () => { element.click(); await drain() }) }
const edit = async (element, value) => act(async () => {
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), 'value').set.call(element, value)
  element.dispatchEvent(new Event(element.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }))
  await drain()
})
const submit = async (form) => act(async () => { form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await drain() })
const mount = async () => {
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  await act(async () => { root.render(<Catalogue />); await drain() })
}
beforeEach(() => {
  rows = [component(101), component(102, { concept_key: 'LongKeyWithoutSpaces'.repeat(5), judged: 0 }),
    component(103, { retired_at: '2030-01-01' }), component(104, { slot: 'act', cameras: ['side', 'mirror'] }), component(105, { manner: 'pov' })]
  readings = [{ id: 201, key: 'front', label: 'Front-facing view' }]
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    if (path === '/api/components?all=1') return structuredClone(rows)
    if (path === '/api/wardrobe') return structuredClone(wardrobe)
    if (path.startsWith('/api/readings?')) return structuredClone(readings)
    throw new Error(`Unexpected read: ${path}`)
  })
  vi.spyOn(api, 'post').mockImplementation(async (path) => {
    const match = path.match(/^\/api\/components\/(\d+)\/(retire|restore)$/)
    if (match) rows.find((row) => row.id === Number(match[1])).retired_at = match[2] === 'retire' ? '2030-01-02' : null
    return { added: 2, skipped: 3 }
  })
  vi.spyOn(api, 'patch').mockResolvedValue({})
  vi.spyOn(api, 'del').mockResolvedValue({})
  vi.spyOn(catalogueStore, 'setCatalogue')
  vi.spyOn(wardrobeStore, 'setWardrobe')
  vi.stubGlobal('confirm', vi.fn(() => false))
})
afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null; container?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals()
})

it('keeps API order, evidence and shared-store refreshes without implicit writes', async () => {
  await mount()
  expect([...container.querySelectorAll('.catalogue-card h3')].map((item) => item.textContent)).toEqual([rows[0].concept_key, rows[1].concept_key])
  expect(container.textContent).toContain('Arrived 7 of 10')
  expect(container.textContent).toContain('2 contradicted')
  expect(container.textContent).toContain('Not measured')
  expect(catalogueStore.setCatalogue).toHaveBeenCalledWith(rows)
  expect(wardrobeStore.setWardrobe).toHaveBeenCalledWith(wardrobe)
  expect(wardrobeStore.outfits()).toEqual(wardrobe.outfits)
  expect(catalogueStore.positionsFor('directed').map((item) => item.key)).toEqual([rows[0].concept_key, rows[1].concept_key])
  expect(api.post).not.toHaveBeenCalled(); expect(api.patch).not.toHaveBeenCalled(); expect(api.del).not.toHaveBeenCalled()
})

it('combines local search, original slot/manner filters and retired visibility without changing order', async () => {
  await mount()
  await edit(field('catalogue-search'), 'visible view 101')
  expect(container.querySelectorAll('.catalogue-card')).toHaveLength(1)
  await edit(field('catalogue-search'), 'no-match')
  expect(container.textContent).toContain('No matching components')
  await click(button('Clear search'))
  await click(container.querySelector('.catalogue-check input'))
  expect(container.querySelectorAll('.catalogue-card')).toHaveLength(3)
  await edit(field('catalogue-manner'), 'pov')
  expect(named('Edit component 105')).toBeTruthy()
  expect(container.querySelectorAll('.catalogue-card')).toHaveLength(1)
  expect(api.post).not.toHaveBeenCalled(); expect(api.del).not.toHaveBeenCalled()
})

it('preserves edit identity and ordered camera payloads, including hidden act fields', async () => {
  await mount(); await edit(field('catalogue-slot'), 'act')
  const trigger = named('Edit component 104'); trigger.focus(); await click(trigger)
  expect(container.querySelector('dialog').open).toBe(true)
  expect(field('component-cameras').value).toBe('side, mirror')
  await edit(field('component-cameras'), ' mirror, side, mirror, ')
  await edit(field('component-slot'), 'camera')
  expect(field('component-cameras')).toBeNull()
  await submit(container.querySelector('dialog form'))
  expect(api.patch).toHaveBeenCalledExactlyOnceWith('/api/components/104', {
    concept_key: 'camera-104', slot: 'camera', manner: 'directed', family: 'front', faces: 'front',
    cameras: ['mirror', 'side', 'mirror'], wording: 'Invented prompt 104', judge_label: 'Visible view 104',
  })
  expect(container.querySelector('dialog')).toBeNull()
  expect(document.activeElement).toBe(trigger)
  expect(api.post).not.toHaveBeenCalled()
})

it('keeps add defaults, failed drafts, explicit submission and pending duplicate protection', async () => {
  await mount(); await click(button('Add component'))
  expect(field('component-slot').value).toBe('camera'); expect(field('component-manner').value).toBe('directed')
  await edit(field('component-concept-key'), 'new-camera')
  await edit(field('component-wording'), 'Invented prompt')
  await edit(field('component-judge-label'), 'Visible scene')
  const request = deferred(); vi.mocked(api.post).mockReturnValueOnce(request.promise)
  const form = container.querySelector('dialog form')
  await submit(form); await submit(form)
  expect(api.post).toHaveBeenCalledOnce()
  expect(button('Cancel').disabled).toBe(true)
  await act(async () => container.querySelector('dialog').dispatchEvent(new Event('cancel', { cancelable: true })))
  expect(container.querySelector('dialog')).toBeTruthy()
  await act(async () => { request.reject(new Error('Outcome unknown; verify before retrying')); await drain() })
  expect(field('component-concept-key').value).toBe('new-camera')
  expect(field('component-wording').value).toBe('Invented prompt')
  expect(document.activeElement).toBe(container.querySelector('dialog [role="alert"]'))
  expect(api.post).toHaveBeenCalledOnce()
})

it('keeps retire/restore separate and requires confirmation for the existing DELETE contracts', async () => {
  await mount(); await click(named('Retire component 101'))
  expect(api.post).toHaveBeenLastCalledWith('/api/components/101/retire')
  await click(container.querySelector('.catalogue-check input'))
  await click(named('Restore component 101'))
  expect(api.post).toHaveBeenLastCalledWith('/api/components/101/restore')
  await click(named('Delete component 102')); await click(named('Delete reading 201'))
  expect(api.del).not.toHaveBeenCalled()
  vi.mocked(globalThis.confirm).mockReturnValue(true)
  await click(named('Delete component 102'))
  expect(api.del).toHaveBeenLastCalledWith('/api/components/102')
  await click(named('Delete reading 201'))
  expect(api.del).toHaveBeenLastCalledWith('/api/readings/201')
})

it('preserves base-reading payloads and input drafts on write errors', async () => {
  await mount(); await edit(field('catalogue-slot'), 'act')
  await edit(field('catalogue-reading-key'), ' side '); await edit(field('catalogue-reading-label'), ' Side view ')
  vi.mocked(api.post).mockRejectedValueOnce(new Error('Duplicate reading'))
  await submit(container.querySelector('.catalogue-reading-form'))
  expect(api.post).toHaveBeenCalledExactlyOnceWith('/api/readings', { slot: 'act', manner: 'directed', key: 'side', label: 'Side view' })
  expect(field('catalogue-reading-key').value).toBe(' side ')
  expect(field('catalogue-reading-label').value).toBe(' Side view ')
  expect(container.textContent).toContain('Duplicate reading')
})

it('imports each seed explicitly and reports the original counts without altering existing labels', async () => {
  await mount()
  const request = deferred(); vi.mocked(api.post).mockReturnValueOnce(request.promise)
  await click(button('Import measured seed')); await click(button('Importing…'))
  expect(api.post).toHaveBeenCalledExactlyOnceWith('/api/components/import')
  await act(async () => { request.resolve({ added: 2, skipped: 3 }); await drain() })
  expect(container.textContent).toContain('Imported catalogue: 2 added, 3 already present.')
  await click(button('Import readings seed'))
  expect(api.post).toHaveBeenLastCalledWith('/api/readings/import')
  expect(container.textContent).toContain('Imported readings: 2 added, 3 already present.')
  await click(button('Import wardrobe seed'))
  expect(api.post).toHaveBeenLastCalledWith('/api/wardrobe/import')
  expect(wardrobeStore.setWardrobe).toHaveBeenCalledTimes(2)
})

it('ignores old scope responses and keeps the latest readings attached to their selected slot', async () => {
  const old = deferred(), read = vi.mocked(api.get).getMockImplementation()
  vi.mocked(api.get).mockImplementation((path) => path === '/api/readings?slot=camera&manner=directed' ? old.promise
    : path === '/api/readings?slot=act&manner=directed' ? Promise.resolve([{ id: 222, key: 'standing', label: 'Standing pose' }]) : read(path))
  await mount(); await edit(field('catalogue-slot'), 'act')
  expect(container.querySelector('.catalogue-readings-list').textContent).toContain('Standing pose')
  await act(async () => { old.resolve([{ id: 211, key: 'old', label: 'Wrong old scope' }]); await drain() })
  expect(container.querySelector('.catalogue-readings-list').textContent).toContain('Standing pose')
  expect(container.textContent).not.toContain('Wrong old scope')
})

it('keeps fresh components and their shared store when an earlier load arrives after import', async () => {
  const old = deferred(), read = vi.mocked(api.get).getMockImplementation(); let first = true
  vi.mocked(api.get).mockImplementation((path) => {
    if (path === '/api/components?all=1' && first) { first = false; return old.promise }
    return read(path)
  })
  await mount(); await click(button('Import measured seed'))
  expect(named('Edit component 101')).toBeTruthy()
  await act(async () => { old.resolve([component(999)]); await drain() })
  expect(named('Edit component 999')).toBeNull()
  expect(catalogueStore.setCatalogue).toHaveBeenCalledOnce()
  expect(catalogueStore.setCatalogue).toHaveBeenCalledWith(rows)
})

it('recovers failed reads without importing and closes the editor without saving on Escape', async () => {
  vi.mocked(api.get).mockRejectedValueOnce(new Error('Catalogue offline'))
  await mount()
  expect(container.textContent).toContain('Catalogue offline')
  await click(button('Refresh components'))
  expect(named('Edit component 101')).toBeTruthy()
  const trigger = button('Add component'); trigger.focus(); await click(trigger)
  await edit(field('component-concept-key'), 'Unsaved key')
  await act(async () => container.querySelector('dialog').dispatchEvent(new Event('cancel', { cancelable: true })))
  expect(container.querySelector('dialog')).toBeNull()
  expect(document.activeElement).toBe(trigger)
  expect(api.post).not.toHaveBeenCalled(); expect(api.patch).not.toHaveBeenCalled()
})

it('refreshes the existing shared stores when explicit imports settle after leaving Catalogue', async () => {
  await mount()
  const components = deferred(), wardrobeImport = deferred()
  vi.mocked(api.post).mockImplementation((path) => path === '/api/components/import' ? components.promise : wardrobeImport.promise)
  await click(button('Import measured seed')); await click(button('Import wardrobe seed'))
  await act(async () => root.unmount()); root = null
  rows = [...rows, component(106)]
  await act(async () => { components.resolve({ added: 1, skipped: 0 }); wardrobeImport.resolve({}); await drain() })
  expect(catalogueStore.setCatalogue).toHaveBeenCalledTimes(2)
  expect(catalogueStore.setCatalogue).toHaveBeenLastCalledWith(rows)
  expect(wardrobeStore.setWardrobe).toHaveBeenCalledTimes(2)
  expect(api.post).toHaveBeenCalledTimes(2)
})

it('keeps imported wardrobe options available in the unchanged session picker', async () => {
  await mount(); await click(button('Import wardrobe seed'))
  await act(async () => root.unmount())
  const session = { id: 884, name: 'Invented consumer session', status: 'draft', model_id: 4,
    model: { id: 4, name: 'Invented model', workflow_id: 12 }, workflow_id: 12,
    settings: { composition_mode: 'legacy' }, shots: [], tags: [] }
  vi.mocked(api.get).mockImplementation(async (path) => {
    if (path === '/api/sessions/884') return session
    if (path === '/api/config' || path === '/api/comfy/models') return {}
    return []
  })
  vi.spyOn(api, 'getVersioned').mockResolvedValue({ current_run: null })
  root = createRoot(container)
  await act(async () => { root.render(<SessionView id={884} initialSession={session} />); await drain() })
  await click(button('⚙ Settings'))
  const option = container.querySelector('select option[value="studio"]')
  expect(option).toBeTruthy()
  expect(option.textContent).toBe('studio')
  expect(option.parentElement.value).toBe('')
  expect(api.post).toHaveBeenCalledExactlyOnceWith('/api/wardrobe/import')
  expect(api.patch).not.toHaveBeenCalled(); expect(api.del).not.toHaveBeenCalled()
})

it('keeps a failed reading write bound to its submitted scope after filters change', async () => {
  await mount(); await edit(field('catalogue-reading-key'), 'side'); await edit(field('catalogue-reading-label'), 'Side view')
  const request = deferred(); vi.mocked(api.post).mockReturnValueOnce(request.promise)
  await submit(container.querySelector('.catalogue-reading-form'))
  await edit(field('catalogue-slot'), 'act')
  await act(async () => { request.reject(new Error('Write outcome unknown')); await drain() })
  expect(api.post).toHaveBeenCalledExactlyOnceWith('/api/readings', { slot: 'camera', manner: 'directed', key: 'side', label: 'Side view' })
  expect(container.textContent).toContain('Reading side (camera · directed): Write outcome unknown')
  expect(field('catalogue-slot').value).toBe('act')
  expect(field('catalogue-reading-key').value).toBe('side')
})
