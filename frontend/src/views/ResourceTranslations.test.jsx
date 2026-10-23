// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Resources from './Resources.jsx'
import { api } from '../api.js'

const digest = 'a'.repeat(64)
const library = { library_key: 'studio_rooms', display_name: 'Studio Rooms', kind: 'rooms', auxiliary: [], revisions: [
  { source_id: 'room-one', revision_id: 1, content_digest: digest, readiness: { status: 'pending', pending_fields: { label: 'Missing approved English translation' } } },
  { source_id: 'invalid-room', revision_id: 2, content_digest: 'b'.repeat(64), readiness: { status: 'pending', pending_fields: {}, diagnostics: [{ code: 'invalid_source_shape', field: 'prompt', message: 'Expected a scalar string' }] } },
] }
const row = { revision: { source_id: 'room-one', content_digest: digest }, field: 'label', source_field: 'name', source_shape: 'scalar', list_index: null,
  source_value: 'OriginalSource'.repeat(10), translation: 'New studio room', current_translation: 'Previously saved room', required: true, role: 'descriptive_input', emit_by_default: true, identity_required: false }
const preview = { library_key: 'studio_rooms', total_revisions: 2, matched_revisions: 1, would_update: 1, would_be_ready: 1, would_remain_pending: 1, unmatched_map_entries: 0, attestation_token: 'reviewed-token' }
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
let box, root, get, post
const flush = () => act(async () => { for (let n = 0; n < 24; n++) await Promise.resolve() })
const button = text => [...box.querySelectorAll('button')].find(b => b.textContent.trim() === text)
const click = async element => { expect(element).toBeTruthy(); await act(async () => element.click()); await flush() }
const edit = async (input, value) => { await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })) }); await flush() }
const load = async () => click(button('Load editable rows'))
beforeEach(async () => {
  box = document.createElement('div'); document.body.append(box); root = createRoot(box)
  get = vi.spyOn(api, 'get').mockImplementation(async url => url === '/api/resources/libraries' ? [library] : url.endsWith('/translations/rows') ? { rows: [row], diagnostics: [{ code: 'invalid_source_shape', field: 'prompt', message: 'Expected a scalar string' }] } : [])
  vi.spyOn(api, 'getVersioned').mockResolvedValue([])
  post = vi.spyOn(api, 'post').mockResolvedValue(preview)
  await act(async () => root.render(<Resources />)); await flush()
})
afterEach(() => { act(() => root.unmount()); box.remove(); vi.restoreAllMocks() })
it('shows exact revision blockers and original/existing values without approving readiness', async () => {
  await load()
  const region = box.querySelector('.resource-translations')
  expect(region.textContent).toContain('Affected fields: label')
  expect(region.textContent).toContain('Correct the source and re-import it')
  expect(region.textContent).toContain(row.source_value)
  expect(region.textContent).toContain(row.current_translation)
  expect(region.textContent).toContain(digest)
  expect(region.textContent).toContain('Expected a scalar string')
  expect(post).not.toHaveBeenCalled()
  expect(button('Confirm & Apply manual translations').disabled).toBe(true)
})
it('labels map and editable rows and keeps responsive labels out of accessible cell names', async () => {
  await load()
  const input = box.querySelector('input[name=translation]')
  expect(document.getElementById(input.getAttribute('aria-describedby').split(' ')[0]).textContent).toBe(row.source_value)
  expect(document.getElementById(input.getAttribute('aria-describedby').split(' ')[1]).textContent).toContain('room-one')
  expect(box.querySelector('input[name=translation_map_path]').labels).toHaveLength(1)
  expect(box.querySelector('.translation-cell-label').getAttribute('aria-hidden')).toBe('true')
  expect(box.querySelector('.translation-rows thead th').getAttribute('scope')).toBe('col')
})
it('keeps Preview projected and sends only reviewed map plus token on explicit Apply', async () => {
  await load(); await click(button('Preview manual translations'))
  expect(box.textContent).toContain('Projected result only')
  expect(box.querySelector('.resource-translations').textContent).toContain('2 Pending')
  expect(post.mock.calls.some(([url]) => url.endsWith('/apply'))).toBe(false)
  expect(box.textContent).not.toContain('reviewed-token')
  post.mockResolvedValueOnce({ updated: 1, ready: 1, pending: 1 })
  await click(button('Confirm & Apply manual translations'))
  expect(post).toHaveBeenCalledWith('/api/resources/libraries/studio_rooms/translations/apply', { translation_map: { [row.source_value]: { source: row.source_value, translation: row.translation, fields: ['label'] } }, attestation_token: 'reviewed-token' })
})
it('focuses errors and preserves draft and selection for retry', async () => {
  await load(); post.mockRejectedValueOnce(new Error('Preview unavailable'))
  await click(button('Preview manual translations'))
  expect(document.activeElement.getAttribute('role')).toBe('alert')
  expect(box.querySelector('input[name=translation]').value).toBe(row.translation)
  expect(box.textContent).toContain('Your draft is kept')
  expect(box.textContent).toContain('room-one')
  await click(button('Preview manual translations'))
  expect(button('Confirm & Apply manual translations').disabled).toBe(false)
})
it('fences a late Preview when its draft changes', async () => {
  await load(); const pending = deferred(); post.mockReturnValueOnce(pending.promise)
  await click(button('Preview manual translations'))
  await edit(box.querySelector('input[name=translation]'), 'Updated draft')
  pending.resolve(preview); await flush()
  expect(button('Confirm & Apply manual translations').disabled).toBe(true)
  expect(box.querySelector('input[name=translation]').value).toBe('Updated draft')
  expect(box.querySelector('.translation-preview')).toBe(null)
})
it('announces empty rows and gives a correction path without enabling Apply', async () => {
  get.mockResolvedValueOnce({ rows: [], diagnostics: [] }); await load()
  expect(box.textContent).toContain('No editable source-backed rows returned')
  expect(button('Confirm & Apply manual translations')).toBeUndefined()
  expect(post).not.toHaveBeenCalled()
})
it('invalidates map Preview immediately and ignores its late response after a path edit', async () => {
  const input = box.querySelector('input[name=translation_map_path]')
  await edit(input, 'first-map.json'); const pending = deferred(); post.mockReturnValueOnce(pending.promise)
  await click(button('Preview Translations')); await edit(input, 'second-map.json')
  pending.resolve(preview); await flush()
  expect(input.value).toBe('second-map.json')
  expect(button('Confirm & Apply Translations')).toBeUndefined()
})
