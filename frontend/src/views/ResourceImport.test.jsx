// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Resources from './Resources.jsx'
import { api } from '../api.js'
import { normalizeSafeReport } from '../resources.js'

const digest = 'a'.repeat(64)
const source = 'source-with-a-long-identity-'.repeat(8)
const report = () => ({ version: 1, phase: 'preview',
  summary: { files: 1, inputs: 4, accepted: 2, auxiliary: 1, duplicates: 1, unresolved: 1, new: 1, updated: 1, unchanged: 1, missing: 1 },
  files: [{ library_key: 'studio', total_inputs: 4,
    accepted: [{ source_id: source, library_key: 'studio', kind: 'rooms', classification: 'new', new_content_digest: digest, previous_content_digest: null },
      { source_id: 'room-two', library_key: 'studio', kind: 'rooms', classification: 'updated', new_content_digest: 'b'.repeat(64), previous_content_digest: 'c'.repeat(64) }],
    auxiliary: [{ library_key: 'studio', kind: 'lighting_map', classification: 'unchanged', new_content_digest: 'd'.repeat(64), previous_content_digest: 'd'.repeat(64) }],
    duplicates: [{ source_id: 'duplicate-room', occurrences: 2 }],
    unresolved: [{ bucket: 'rooms', index: 3, reason: 'Expected a scalar label', received_type: 'list', expected_kind: 'rooms', identifier_fields: ['id', 'key'] }] }],
  missing_source_entries: [{ library_key: 'studio', source_id: 'missing-room', latest_content_digest: 'e'.repeat(64) }] })
const staged = (revision = 1) => ({ selection_id: 'selection-example', selection_revision: revision, state: 'open', expires_at: '2099-01-01T00:00:00Z',
  files: [{ file_id: 'file-one', file_name: 'StudioSource'.repeat(14) + '.json', byte_count: 128, declared_library: 'studio', effective_library_key: 'studio', matched_auxiliary_kinds: ['lighting_map'], effective_auxiliary_kind: null, status: 'staged' }], preview: null, commit_result: null, cleanup_warning: null })
const preview = (committable = true) => ({ ...staged(), preview: { preview_token: 'opaque-preview-token', manifest_digest: digest, committable, report: report() } })
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
let root, box, post, upload
const drain = () => act(async () => { for (let index = 0; index < 24; index++) await Promise.resolve() })
const button = label => [...box.querySelectorAll('button')].find(element => element.textContent.trim() === label)
async function click(element) { expect(element).toBeTruthy(); await act(async () => element.click()); await drain() }
async function choose() {
  const input = box.querySelector('input[type=file]')
  Object.defineProperty(input, 'files', { configurable: true, value: [new File(['{}'], 'studio.json', { type: 'application/json' })] })
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })))
}
async function mount() { await act(async () => root.render(<Resources />)); await drain(); await click(button('Import Preview')) }
async function stage() { await choose(); await click(button('Upload 1 file(s)')) }
beforeEach(() => {
  box = document.createElement('div'); document.body.append(box); root = createRoot(box)
  vi.spyOn(api, 'get').mockResolvedValue([])
  vi.spyOn(api, 'getVersioned').mockResolvedValue([])
  post = vi.spyOn(api, 'post').mockImplementation(async url => url.endsWith('/preview') ? preview() : staged())
  upload = vi.spyOn(api, 'uploadMultipart').mockResolvedValue(staged())
  expect(normalizeSafeReport(report(), 'preview')).not.toBe(null)
})
afterEach(() => { act(() => root.unmount()); box.remove(); vi.restoreAllMocks() })

it('labels file selection, explains limits and never uploads or imports just by choosing files', async () => {
  await mount(); await choose()
  expect(box.querySelector('input[type=file]').labels[0].textContent).toBe('Source JSON files')
  expect(box.textContent).toContain('10 MiB per file'); expect(box.textContent).toContain('24 hours')
  expect(box.querySelectorAll('.import-steps [aria-current=step]')).toHaveLength(1)
  expect(post).not.toHaveBeenCalled(); expect(upload).not.toHaveBeenCalled()
})
it('shows all outcomes, complete identities and one explicit Commit action after Preview', async () => {
  await mount(); await stage()
  expect(button('Commit Import').disabled).toBe(true)
  await click(button('Preview Import'))
  const commits = [...box.querySelectorAll('button')].filter(element => element.textContent === 'Commit Import')
  expect(commits).toHaveLength(1); expect(commits[0].closest('section').getAttribute('aria-label')).toBe('Confirm resource import')
  expect(document.activeElement.textContent).toBe('Import Preview Results')
  for (const text of [source, digest, 'b'.repeat(64), 'c'.repeat(64), 'd'.repeat(64), 'Expected a scalar label', 'duplicate-room', 'missing-room']) expect(box.textContent).toContain(text)
  expect(box.querySelector('table[aria-label="Accepted entries"] td').getAttribute('role')).toBe('cell')
  expect(box.querySelector('.import-cell-label').getAttribute('aria-hidden')).toBe('true')
  expect(box.textContent).not.toContain('opaque-preview-token')
  expect(post.mock.calls.some(([url]) => url.endsWith('/commit'))).toBe(false)
  await click(commits[0])
  expect(post).toHaveBeenCalledWith('/api/resources/import-selections/selection-example/commit', { expected_revision: 1, preview_token: 'opaque-preview-token' })
})
it('keeps a noncommittable Preview inspectable without enabling Import', async () => {
  post.mockImplementation(async url => url.endsWith('/preview') ? preview(false) : staged())
  await mount(); await stage(); await click(button('Preview Import'))
  expect(box.textContent).toContain('Import Preview Results'); expect(button('Commit Import').disabled).toBe(true)
  expect(box.textContent).toContain('Generate a current committable Preview')
})
it('focuses upload errors, keeps the selection and allows the same local files to be retried', async () => {
  upload.mockRejectedValueOnce(new Error('Invalid JSON source. Select a valid JSON file.'))
  await mount(); await choose(); await click(button('Upload 1 file(s)'))
  expect(document.activeElement.getAttribute('role')).toBe('alert')
  expect(box.textContent).toContain('Invalid JSON source')
  expect(box.textContent).toContain('Selected: studio.json')
  await click(button('Upload 1 file(s)')); expect(upload).toHaveBeenCalledTimes(2)
  expect(box.querySelector('[role=alert]')).toBe(null)
})
it('retains a current Preview through a failed retry without silently committing', async () => {
  await mount(); await stage(); await click(button('Preview Import'))
  post.mockRejectedValueOnce(new Error('Preview fixture unavailable'))
  await click(button('Preview Import'))
  expect(box.textContent).toContain(source); expect(document.activeElement.getAttribute('role')).toBe('alert')
  expect(post.mock.calls.some(([url]) => url.endsWith('/commit'))).toBe(false)
  await click(button('Preview Import')); expect(box.querySelector('[role=alert]')).toBe(null)
})
it('ignores late Preview responses after a newer upload invalidates the old result', async () => {
  const oldPreview = deferred()
  await mount(); await stage()
  post.mockReturnValueOnce(oldPreview.promise)
  await click(button('Preview Import'))
  upload.mockResolvedValueOnce(staged(2)); await choose(); await click(button('Upload 1 file(s)'))
  oldPreview.resolve(preview()); await drain()
  expect(box.textContent).toContain('Revision: 2')
  expect(box.textContent).not.toContain('Import Preview Results')
  expect(button('Commit Import').disabled).toBe(true)
})
it('resets presentation and focus without accepting an old selection response', async () => {
  const oldPreview = deferred()
  await mount(); await stage(); post.mockReturnValueOnce(oldPreview.promise)
  await click(button('Preview Import')); await click(button('New Selection'))
  expect(document.activeElement).toBe(box.querySelector('input[type=file]'))
  oldPreview.resolve(preview()); await drain()
  expect(box.textContent).not.toContain('Import Preview Results')
  expect(box.querySelector('[aria-label="Confirm resource import"]')).toBe(null)
})
it('labels explicit compatibility choices and identifies each remove action', async () => {
  await mount(); await stage(); await click([...box.querySelectorAll('button')].find(element => element.textContent.includes('Show Advanced Compatibility Options')))
  expect(box.querySelector('#import-target-file-one').labels[0].textContent).toBe('Target Library Key')
  expect(box.querySelector('#import-aux-file-one').labels[0].textContent).toBe('Auxiliary Kind')
  expect(box.querySelector('button[title="Remove file from selection"]').getAttribute('aria-label')).toContain(staged().files[0].file_name)
})
it('distinguishes new local files from the currently staged Preview', async () => {
  await mount(); await stage(); await click(button('Preview Import')); await choose()
  expect(box.textContent).toContain('These local files are not staged yet')
  expect(post.mock.calls.some(([url]) => url.endsWith('/commit'))).toBe(false)
})
it('returns focus to file selection after removing the focused file without changing its request', async () => {
  const remove = vi.spyOn(api, 'del').mockResolvedValue({ ...staged(2), files: [] })
  await mount(); await stage()
  await click(box.querySelector('button[title="Remove file from selection"]'))
  expect(remove).toHaveBeenCalledWith('/api/resources/import-selections/selection-example/files/file-one?expected_revision=1')
  expect(document.activeElement).toBe(box.querySelector('input[type=file]'))
})
it('closes the visual steps after cancellation with retained Preview and focuses recovery', async () => {
  await mount(); await stage(); await click(button('Preview Import'))
  post.mockResolvedValueOnce({ ...preview(), state: 'cancelled', selection_revision: 2 })
  button('Cancel Selection').focus()
  await click(button('Cancel Selection'))
  expect(box.querySelectorAll('.import-steps [aria-current=step]')).toHaveLength(0)
  expect(button('Commit Import').disabled).toBe(true)
  expect(document.activeElement).toBe(button('New Selection'))
  expect(box.textContent).toContain('Import Preview Results')
  await click(button('New Selection'))
  expect(box.querySelector('input[type=file]').disabled).toBe(false)
  expect(document.activeElement).toBe(box.querySelector('input[type=file]'))
})
it('does not mark an actionable step for an expired selection', async () => {
  upload.mockResolvedValueOnce({ ...staged(), state: 'expired' })
  await mount(); await stage()
  expect(box.querySelectorAll('.import-steps [aria-current=step]')).toHaveLength(0)
  expect(box.querySelector('input[type=file]').disabled).toBe(true)
  expect(button('Commit Import').disabled).toBe(true)
})
