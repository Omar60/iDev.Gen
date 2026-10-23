// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true
import React, { act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ResourceInventory from './ResourceInventory.jsx'
import { api } from '../api.js'
import { filterLibraries } from '../resources.js'

const revision = (source, extra = {}) => ({ source_id: source, revision_id: 1, content_digest: 'a'.repeat(64), readiness: { status: 'ready' }, ...extra })
const library = (key, revisions, extra = {}) => ({ library_key: key, display_name: key, kind: 'rooms', revisions, auxiliary: [], ...extra })
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
let root, box, props, get
const drain = () => act(async () => { for (let index = 0; index < 20; index++) await Promise.resolve() })
const button = text => [...box.querySelectorAll('button')].find(element => element.textContent === text)
async function click(element) { expect(element).toBeTruthy(); await act(async () => element.click()); await drain() }
async function input(value) {
  await act(async () => {
    const element = box.querySelector('input')
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(element, value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
function Harness() {
  const [selection, onSelectionChange] = useState(''), [query, onQueryChange] = useState(''), [category, onCategoryChange] = useState('all')
  return <ResourceInventory {...props} selection={selection} onSelectionChange={onSelectionChange} query={query} onQueryChange={onQueryChange}
    category={category} onCategoryChange={onCategoryChange} filteredLibraries={filterLibraries(props.libraries, { query, category })} />
}
async function render() { await act(async () => root.render(<Harness />)); await drain() }
beforeEach(() => {
  box = document.createElement('div'); document.body.append(box); root = createRoot(box)
  props = { libraries: [library('studio', [revision('room-one'), revision('room-two')])], categories: ['rooms'], models: [{ id: 7, name: 'Ada' }],
    selectedModelId: '7', onModelChange: vi.fn(), loading: false, error: '', importedIdentities: new Set(), onRefresh: vi.fn(), onImport: vi.fn(),
    creationLocked: false, onCreate: vi.fn(), onTranslate: vi.fn(), onStructuredScene: vi.fn() }
  get = vi.spyOn(api, 'get').mockResolvedValue({ payload: { label: 'Room' }, readiness: { field_readiness: [{ name: 'label', role: 'descriptive_input', translated: true }] } })
})
afterEach(() => { act(() => root.unmount()); box.remove(); vi.restoreAllMocks() })

it('selects by the full identity, keeps all digests and moves focus to detail and back', async () => {
  props.libraries.push(library('other', [revision('room-one')]))
  await render()
  const choices = box.querySelectorAll('.resource-choice')
  expect(choices).toHaveLength(3)
  await click(choices[2])
  expect(box.querySelector('.resource-detail code').textContent).toBe('other')
  expect(box.querySelector('.resource-detail').textContent).toContain('a'.repeat(64))
  expect(document.activeElement).toBe(box.querySelector('.resource-detail h2'))
  await click(button('Back to inventory'))
  expect(document.activeElement).toBe(choices[2])
  await click(button('Create session'))
  expect(props.onCreate).toHaveBeenCalledWith(props.libraries[1], props.libraries[1].revisions[0])
})
it('keeps an explicit selection outside search results and restores it after clearing', async () => {
  await render(); await click(box.querySelectorAll('.resource-choice')[1]); await input('room-one')
  expect(box.querySelectorAll('.resource-choice')).toHaveLength(1)
  expect(box.querySelector('.resource-detail h2').textContent).toBe('room-two')
  expect(box.textContent).toContain('This selection is outside the current filter')
  await input(''); expect(box.querySelector('[aria-pressed="true"]').textContent).toContain('room-two')
  await input('no matches'); await click(button('Clear filters'))
  expect(document.activeElement).toBe(box.querySelector('input'))
  expect(box.querySelector('.resource-detail h2').textContent).toBe('room-two')
})
it('does not substitute a different revision when the selected identity disappears on refresh', async () => {
  await render(); await click(box.querySelectorAll('.resource-choice')[1])
  props.libraries = [library('studio', [revision('room-one')])]; await render()
  expect(box.querySelector('.resource-detail').textContent).toContain('no longer in the inventory')
  expect(button('Create session')).toBeUndefined()
})
it('ignores late inspection successes and errors after another revision is selected', async () => {
  const old = deferred(), current = deferred()
  get.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
  await render(); await click(button('Inspect'))
  await click(box.querySelectorAll('.resource-choice')[1]); await click(button('Inspect'))
  current.resolve({ payload: { label: 'Latest exact revision' }, readiness: {} }); await drain()
  old.reject(new Error('Stale detail error')); await drain()
  expect(box.textContent).not.toContain('Stale detail error')
  expect(box.querySelector('.resource-inspection pre').textContent).toContain('Latest exact revision')
  expect(get.mock.calls[1][0]).toBe(`/api/resources/revisions/studio/room-two/${'a'.repeat(64)}`)
})
it('cannot replace a new inspection with a late successful response', async () => {
  const old = deferred(); get.mockReturnValueOnce(old.promise)
  await render(); await click(button('Inspect')); await click(box.querySelectorAll('.resource-choice')[1]); await click(button('Inspect'))
  old.resolve({ payload: { label: 'Stale content' }, readiness: {} }); await drain()
  expect(box.querySelector('.resource-inspection pre').textContent).not.toContain('Stale content')
})
it('retries inspection and retains the exact revision identity', async () => {
  get.mockRejectedValueOnce(new Error('Fixture unavailable'))
  await render(); await click(button('Inspect'))
  expect(box.querySelector('[role=alert]').textContent).toContain('Fixture unavailable')
  await click(button('Retry inspection')); expect(get).toHaveBeenCalledTimes(2)
  expect(get.mock.calls[0][0]).toBe(get.mock.calls[1][0]); expect(box.querySelector('[role=alert]')).toBe(null)
})
it('preserves readiness semantics, source diagnostics and distinct translation/reimport actions', async () => {
  props.libraries = [library('studio', [revision('pending', { readiness: { status: 'pending', pending_fields: { label: 'Missing English label' } } }),
    revision('broken', { readiness: { status: 'ready', diagnostics: [{ code: 'invalid_source_shape', field: 'label', message: 'Expected a scalar' }] } })])]
  await render(); expect(button('Create session')).toBeUndefined(); await click(button('Translate'))
  expect(props.onTranslate).toHaveBeenCalledWith('studio')
  await click(box.querySelectorAll('.resource-choice')[1]); expect(button('Translate')).toBeUndefined()
  expect(box.querySelector('.resource-detail').textContent).toContain('Needs source correction')
  await click(button('Re-import')); expect(props.onImport).toHaveBeenCalledTimes(1)
  get.mockResolvedValue({ readiness: { coverage: { sidecar_error: 'Invalid sidecar', unmapped_fields: ['unsafe'] }, diagnostics: [{ field: 'label', code: 'invalid_source_shape', message: 'Expected a scalar' }] } })
  await click(button('Inspect source')); expect(box.textContent).toContain('Invalid sidecar'); expect(box.textContent).toContain('unsafe')
})
it('keeps fused editor semantics and disables creation while an outcome is unknown', async () => {
  props.libraries[0].kind = 'fused_scenes'; props.creationLocked = true
  await render(); expect(button('Use advanced editor').disabled).toBe(true)
  await click(button('Choose a structured scene')); expect(props.onStructuredScene).toHaveBeenCalledTimes(1)
  expect(props.onCreate).not.toHaveBeenCalled()
})
it('uses a translated label or source identity as the name, never another descriptive field', async () => {
  props.libraries = [library('studio', [revision('source-without-label', { translation: { scene_theme: 'Description is not a name', label: '  ' } })])]
  await render()
  expect(box.querySelector('.resource-detail h2').textContent).toBe('source-without-label')
  expect(box.querySelector('.resource-choice strong').textContent).toBe('source-without-label')
  expect(box.querySelector('.resource-detail').textContent).toContain('Description is not a name')
  props.libraries = [library('studio', [revision('source', { translation: { display_label: 'Display label' } })])]
  await render(); expect(box.querySelector('.resource-detail h2').textContent).toBe('Display label')
})
it('labels filters, separates loading from empty, and retains content through refresh errors', async () => {
  props.loading = true; props.libraries = []; await render()
  expect(box.textContent).toContain('Loading inventory…'); expect(box.textContent).not.toContain('No resource libraries imported yet')
  expect(box.querySelector('input').labels[0].textContent).toBe('Search resources')
  props.loading = false; await render(); await click(button('Open Import Preview')); expect(props.onImport).toHaveBeenCalled()
  props.libraries = [library('studio', [revision('room-one')])]; props.error = 'Unavailable'; await render()
  expect(box.querySelector('.resource-detail').textContent).toContain('room-one')
  await click(button('Retry inventory')); expect(props.onRefresh).toHaveBeenCalled()
  props.libraries = []; await render()
  expect(box.textContent).not.toContain('No resource libraries imported yet')
  expect(box.querySelector('[role=alert]').textContent).toContain('Unavailable')
})
it('handles an extensive inventory, complete long names, duplicate digests and auxiliary-only matches', async () => {
  props.libraries = [library('studio', Array.from({ length: 500 }, (_, index) => revision(`room-${index}`, { translation: { label: `Room ${index}` } })),
    { auxiliary: [{ auxiliary_id: 2, kind: 'lighting_map', content_digest: 'b'.repeat(64) }] })]
  await render(); expect(box.querySelectorAll('.resource-choice')).toHaveLength(500)
  await input('lighting_map'); expect(box.textContent).toContain('Auxiliary data is listed below')
  expect(box.textContent).toContain('b'.repeat(64))
  props.libraries = [library('L'.repeat(150), [revision('S'.repeat(220), { translation: { label: 'N'.repeat(240) } })])]
  await input(''); await render(); expect(box.textContent).toContain('N'.repeat(240)); expect(box.textContent).toContain('S'.repeat(220))
})
