// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
let RoomImport, api, root, host, report, holdEffects, heldEffects
const drain = () => new Promise((done) => setTimeout(done, 0))
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const mount = async () => {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  await act(async () => { root.render(<RoomImport />); await drain() })
}
const edit = async (id, value) => act(async () => {
  const el = host.querySelector(`#${id}`)
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true })); await drain()
})
const paths = async () => { await edit('ri-source', '  fixture-source  '); await edit('ri-map', '  fixture-map.json  ') }
const submit = async () => act(async () => { host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await drain() })
beforeEach(async () => {
  holdEffects = false; heldEffects = []
  vi.doMock('react', () => ({ ...React, default: React, useEffect: (effect, dependencies) => React.useEffect(() => {
    if (!holdEffects) return effect()
    let cleanup, cancelled = false
    heldEffects.push(() => { if (!cancelled) cleanup = effect() })
    return () => { cancelled = true; cleanup?.() }
  }, dependencies) }))
  vi.resetModules()
  ;({ default: RoomImport } = await import('./RoomImport.jsx'))
  ;({ api } = await import('../api.js'))
  report = { accepted: 4, refused: 2, written: 5, created: 3, updated: 1, unchanged: 1, orphaned: 1, skipped_empty: 0,
    refused_identifiers: ['room_042', 'room_043'], refused_libraries: [{ library: 'unhandled_library', reason: 'not_adopted' }],
    by_signal: { identifier: 1, tags: 1, theme_text: 0 }, by_library: { general_scenes: 2 },
    destinations: { 'fixture-rooms-seed.json': { created_keys: ['room-01'], updated_keys: ['room-02'], orphaned_keys: ['room-03'] } },
    outlying_weights: [{ identifier: 'room_004', weight: 24, median: 1 }], orphaned_verdicts: ['room_099'], skipped_placeholder: 1 }
  vi.spyOn(api, 'post').mockImplementation(async () => structuredClone(report))
  vi.spyOn(api, 'get').mockResolvedValue([])
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(async () => { if (root) await act(async () => root.unmount()); host?.remove(); vi.restoreAllMocks(); vi.doUnmock('react') })
it('opens without reads or writes and associates paths with labels and help', async () => {
  await mount(); expect(api.get).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled()
  expect(host.textContent).toContain('No import report yet'); expect(host.querySelector('button').disabled).toBe(true)
  for (const id of ['ri-source', 'ri-map']) {
    expect(host.querySelector(`label[for="${id}"]`)).toBeTruthy()
    expect(host.querySelector(`#${id}`).required).toBe(true)
    expect(document.getElementById(host.querySelector(`#${id}`).getAttribute('aria-describedby'))).toBeTruthy()
  }
  expect(host.querySelector('a').getAttribute('href')).toBe('#/resources')
})
it('requires both nonblank paths before any explicit import', async () => {
  await mount(); await edit('ri-source', '   '); await edit('ri-map', 'fixture-map.json'); await submit()
  expect(api.post).not.toHaveBeenCalled()
})
it('preserves the exact import payload, count order, identities, diagnostics and report details', async () => {
  await mount(); await paths(); await submit()
  expect(api.post).toHaveBeenCalledExactlyOnceWith('/api/rooms/import', { source_dir: 'fixture-source', map_path: 'fixture-map.json' })
  expect([...host.querySelectorAll('.room-import-counts dt')].map((n) => n.textContent)).toEqual(['Accepted', 'Refused', 'Written', 'Created', 'Updated', 'Unchanged', 'Orphaned', 'Skipped (empty theme)'])
  expect([...host.querySelectorAll('.room-import-counts dd')].map((n) => n.textContent)).toEqual(['4', '2', '5', '3', '1', '1', '1', '0'])
  expect([...host.querySelectorAll('.room-import-identities li')].map((n) => n.textContent)).toEqual(['room_042', 'room_043'])
  expect(host.textContent).toContain('room_004 (24)'); expect(host.textContent).toContain('room_099')
  expect(JSON.parse(host.querySelector('pre').textContent)).toEqual(report)
  expect(document.activeElement).toBe(host.querySelector('.room-import-result'))
  expect(host.querySelector('#ri-source').value).toBe('  fixture-source  ')
})
it('shows every count as zero when omitted without inventing rows or reports', async () => {
  report = { written: 0 }; await mount(); await paths(); await submit()
  expect([...host.querySelectorAll('.room-import-counts dd')].map((n) => n.textContent)).toEqual(Array(8).fill('0'))
  expect(host.textContent).toContain('no written rows'); expect(host.querySelector('[aria-label="Refusal details"]')).toBeNull()
})
it('keeps every uncovered source value and the draft on a translation refusal', async () => {
  const uncovered = [{ identifier: 'room_01', field: 'label', string: 'ORIGINAL-LABEL' }, { identifier: 'room_02', field: 'theme', string: 'SourceWithoutSpaces'.repeat(12) }]
  api.post.mockRejectedValueOnce(Object.assign(Error('translation_missing'), { status: 422, detail: { error: 'translation_missing', uncovered } }))
  await mount(); await paths(); await submit()
  expect([...host.querySelectorAll('tbody tr')].map((r) => [...r.querySelectorAll('td')].map((c) => c.textContent))).toEqual(uncovered.map((u) => [u.identifier, u.field, u.string]))
  expect(host.textContent).toContain('Nothing was written'); expect(host.querySelector('#ri-map').value).toBe('  fixture-map.json  ')
  expect(document.activeElement).toBe(host.querySelector('[role="alert"]')); expect(api.post).toHaveBeenCalledTimes(1)
})
it.each([422, 500, undefined])('keeps paths and warns about uncertain or partial writes on error %s', async (status) => {
  api.post.mockRejectedValueOnce(Object.assign(Error('Registry result unavailable'), { status }))
  await mount(); await paths(); await submit(); await act(async () => { await drain() })
  expect(host.textContent).toContain('Files or registry entries may already have changed')
  expect(host.textContent).not.toContain('Nothing was written')
  expect(host.querySelector('#ri-source').value).toBe('  fixture-source  '); expect(api.post).toHaveBeenCalledTimes(1)
})
it('blocks synchronous duplicate submits and locks the submitted context while pending', async () => {
  const wait = deferred(); api.post.mockReturnValueOnce(wait.promise)
  await mount(); await paths()
  await act(async () => { for (let n = 0; n < 2; n++) host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await drain() })
  expect(api.post).toHaveBeenCalledTimes(1); expect(host.querySelector('#ri-source').disabled).toBe(true)
  expect(host.querySelector('#ri-map').disabled).toBe(true); expect(host.querySelector('form').getAttribute('aria-busy')).toBe('true')
  expect(host.querySelector('[role="status"]').textContent).toContain('Waiting for the backend report')
  await act(async () => { wait.resolve(report); await drain() })
  expect(host.querySelector('#ri-source').disabled).toBe(false); expect(api.post).toHaveBeenCalledTimes(1)
})
it('retries only after another explicit submit with the retained paths', async () => {
  api.post.mockRejectedValueOnce(Error('Response unavailable'))
  await mount(); await paths(); await submit(); expect(api.post).toHaveBeenCalledTimes(1)
  await submit(); expect(api.post).toHaveBeenCalledTimes(2)
  expect(api.post.mock.calls[1]).toEqual(api.post.mock.calls[0]); expect(host.querySelector('pre')).toBeTruthy()
})
it.each(['resolve', 'reject'])('reuses a pending import across route remounts and handles its late %s without a duplicate POST', async (settle) => {
  const old = deferred(); api.post.mockReturnValueOnce(old.promise)
  await mount(); await paths(); await submit()
  await act(async () => root.unmount()); host.remove(); root = null
  await mount(); expect(host.querySelector('#ri-source').value).toBe('  fixture-source  ')
  expect(host.querySelector('#ri-source').disabled).toBe(true); await submit()
  expect(api.post).toHaveBeenCalledTimes(1)
  await act(async () => { old[settle](settle === 'resolve' ? report : Error('Original request failed')); await drain() })
  expect(host.querySelector('#ri-source').disabled).toBe(false)
  if (settle === 'resolve') expect(host.querySelector('.room-import-counts dd').textContent).toBe('4')
  else expect(host.textContent).toContain('Original request failed')
  expect(api.post).toHaveBeenCalledTimes(1)
  await edit('ri-source', 'next-source'); await submit()
  expect(api.post).toHaveBeenCalledTimes(2)
  expect(host.querySelector('.room-import-context dd').textContent).toBe('next-source')
})
it('does not update an unmounted screen when its request settles', async () => {
  const wait = deferred(); api.post.mockReturnValueOnce(wait.promise)
  await mount(); await paths(); await submit()
  await act(async () => root.unmount()); root = null
  await act(async () => { wait.resolve(report); await drain() })
  expect(host.textContent).toBe(''); expect(api.post).toHaveBeenCalledTimes(1)
})
it('keeps report provenance when the form is edited for the next import', async () => {
  await mount(); await paths(); await submit(); await edit('ri-source', 'next-source')
  expect(host.querySelector('.room-import-context dd').textContent).toBe('fixture-source')
  expect(host.querySelector('#ri-source').value).toBe('next-source'); expect(api.post).toHaveBeenCalledTimes(1)
})
it('does not steal focus from a foreign modal when the result arrives', async () => {
  const wait = deferred(); api.post.mockReturnValueOnce(wait.promise)
  await mount(); await paths(); await submit()
  const modal = document.createElement('div'); modal.setAttribute('role', 'dialog'); modal.setAttribute('aria-modal', 'true')
  const control = document.createElement('button'); modal.append(control); host.append(modal); control.focus()
  await act(async () => { wait.resolve(report); await drain() })
  expect(document.activeElement).toBe(control); expect(host.querySelector('pre')).toBeTruthy()
})

const uncovered = [
  { identifier: 'fixture-room-1', field: 'label', string: 'Invented untranslated label' },
  { identifier: 'fixture-room-2', field: 'theme', string: 'Invented source text'.repeat(12) },
]
const finish = async (wait, kind) => act(async () => {
  if (kind === '200') wait.resolve(structuredClone(report))
  else wait.reject(Object.assign(Error(kind === '422' ? 'translation_missing' : 'Registry result unavailable'), {
    status: Number(kind), detail: kind === '422' ? { error: 'translation_missing', uncovered } : 'Registry result unavailable',
  }))
  await drain()
})
const expectRecovered = (kind) => {
  expect(host.querySelector('#ri-source').value).toBe('  fixture-source  ')
  expect(host.querySelector('#ri-map').value).toBe('  fixture-map.json  ')
  expect(host.querySelector('#ri-source').disabled).toBe(false)
  expect(host.querySelector('form').getAttribute('aria-busy')).toBe('false')
  expect([...host.querySelectorAll('.room-import-context dd')].map((node) => node.textContent)).toEqual(['fixture-source', 'fixture-map.json'])
  expect(host.textContent).not.toContain('No import report yet')
  expect(host.querySelector('.room-import-result').getAttribute('role')).toBe(kind === '200' ? 'status' : 'alert')
  if (kind === '200') expect(JSON.parse(host.querySelector('pre').textContent)).toEqual(report)
  else {
    expect(host.querySelector('pre')).toBeNull()
    if (kind === '422') {
      expect([...host.querySelectorAll('tbody tr')].map((row) => [...row.querySelectorAll('td')].map((cell) => cell.textContent))).toEqual(uncovered.map((row) => [row.identifier, row.field, row.string]))
      expect(host.textContent).toContain('Nothing was written')
      expect(host.querySelector('[aria-label="Strings still to translate"]').tabIndex).toBe(0)
    } else {
      expect(host.textContent).toContain('Registry result unavailable')
      expect(host.textContent).toContain('Files or registry entries may already have changed')
      expect(host.textContent).not.toContain('Nothing was written')
    }
  }
  expect(api.post).toHaveBeenCalledExactlyOnceWith('/api/rooms/import', { source_dir: 'fixture-source', map_path: 'fixture-map.json' })
}
it.each(['200', '422', '503'])('recovers the exact %s outcome settled while the screen was unmounted', async (kind) => {
  const wait = deferred(); api.post.mockReturnValueOnce(wait.promise)
  await mount(); await paths(); await submit()
  await act(async () => root.unmount()); root = null; host.remove()
  await finish(wait, kind)
  await mount(); expectRecovered(kind)
})
it.each(['200', '422', '503'])('recovers %s after remounting the same pending request without resending', async (kind) => {
  const wait = deferred(); api.post.mockReturnValueOnce(wait.promise)
  await mount(); await paths(); await submit()
  await act(async () => root.unmount()); root = null; host.remove()
  await mount()
  expect(host.querySelector('#ri-source').value).toBe('  fixture-source  ')
  expect(host.querySelector('#ri-map').value).toBe('  fixture-map.json  ')
  expect(host.querySelectorAll('input:disabled')).toHaveLength(2)
  expect(host.querySelector('button').disabled).toBe(true)
  expect(host.querySelector('form').getAttribute('aria-busy')).toBe('true')
  await submit(); await submit(); expect(api.post).toHaveBeenCalledTimes(1)
  await finish(wait, kind); expectRecovered(kind)
})
it('retains a displayed report and its submitted provenance independently of subsequent draft edits', async () => {
  await mount(); await paths(); await submit(); await edit('ri-source', 'next-source')
  await act(async () => root.unmount()); root = null; host.remove()
  await mount(); expectRecovered('200')
  await edit('ri-source', 'next-source'); await edit('ri-map', 'next-map.json')
  expect([...host.querySelectorAll('.room-import-context dd')].map((node) => node.textContent)).toEqual(['fixture-source', 'fixture-map.json'])
  await edit('ri-source', ' '); await submit(); expect(api.post).toHaveBeenCalledTimes(1)
  expect(JSON.parse(host.querySelector('pre').textContent)).toEqual(report)
  await edit('ri-source', 'next-source'); await submit()
  expect(api.post).toHaveBeenCalledTimes(2)
  expect(api.post.mock.calls[1]).toEqual(['/api/rooms/import', { source_dir: 'next-source', map_path: 'next-map.json' }])
  await act(async () => root.unmount()); root = null; host.remove()
  await mount()
  expect(host.querySelector('#ri-source').value).toBe('next-source')
  expect(host.querySelector('.room-import-context dd').textContent).toBe('next-source')
  expect(api.post).toHaveBeenCalledTimes(2)
})
it.each(['200', '422', '503'])('handles %s completion between the initial render and installation of its effects', async (kind) => {
  const wait = deferred(); api.post.mockReturnValueOnce(wait.promise)
  await mount(); await paths(); await submit()
  await act(async () => root.unmount()); root = null; host.remove()
  holdEffects = true; await mount()
  expect(heldEffects).toHaveLength(2)
  await finish(wait, kind)
  expect(host.querySelector('form').getAttribute('aria-busy')).toBe('true')
  holdEffects = false
  await act(async () => { heldEffects.splice(0).forEach((effect) => effect()); await drain() })
  expectRecovered(kind)
})
it('ignores an obsolete completion callback after an explicit newer operation starts', async () => {
  const first = deferred(), next = deferred()
  api.post.mockReturnValueOnce(first.promise).mockReturnValueOnce(next.promise)
  await mount(); await paths(); await submit()
  const firstRoot = root, firstHost = host
  try {
    holdEffects = true; await mount()
    // This subscription captures the old operation, but its native promise callback is queued later.
    holdEffects = false
    await act(async () => {
      first.resolve(report)
      await first.promise
      await Promise.resolve()
      heldEffects.splice(0).forEach((effect) => effect())
      firstHost.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await drain()
    })
    expect(api.post).toHaveBeenCalledTimes(2)
    expect(firstHost.querySelector('form').getAttribute('aria-busy')).toBe('true')
    expect(firstHost.querySelector('.room-import-result')).toBeNull()
    expect(host.querySelector('form').getAttribute('aria-busy')).toBe('true')
    expect(host.querySelector('.room-import-result')).toBeNull()
    await act(async () => root.unmount()); host.remove(); root = firstRoot; host = firstHost
    await act(async () => { next.resolve({ written: 9 }); await drain() })
    expect(JSON.parse(host.querySelector('pre').textContent)).toEqual({ written: 9 })
    expect(api.post).toHaveBeenCalledTimes(2)
  } finally {
    if (root !== firstRoot) { await act(async () => root.unmount()); host.remove(); root = firstRoot; host = firstHost }
    next.resolve({ written: 9 })
  }
})
it.each(['200', '422', '503'])('recovers accessible %s content without moving focus from a foreign dialog or inert region', async (kind) => {
  const wait = deferred(); api.post.mockReturnValueOnce(wait.promise)
  await mount(); await paths(); await submit()
  await act(async () => root.unmount()); root = null; host.remove()
  const dialog = document.createElement('div'); dialog.setAttribute('aria-modal', 'true'); dialog.setAttribute('role', 'dialog')
  const control = document.createElement('button'); dialog.append(control); document.body.append(dialog); control.focus()
  try {
    await finish(wait, kind); await mount(); expectRecovered(kind)
    expect(document.activeElement).toBe(control)
    await act(async () => root.unmount()); root = null; host.remove(); dialog.remove()
    holdEffects = true; await mount(); host.setAttribute('inert', '')
    document.body.append(control); control.focus(); holdEffects = false
    await act(async () => { heldEffects.splice(0).forEach((effect) => effect()); await drain() })
    expectRecovered(kind); expect(document.activeElement).toBe(control)
  } finally { dialog.remove(); control.remove() }
})
