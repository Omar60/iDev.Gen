// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../api.js'
import Library from './Library.jsx'
let root, box, get
const row = (id, extra = {}) => ({ id, name: `Session ${id}`, model_name: 'Ada', shot_count: 4, done_count: 2,
  cover_shot_id: null, tags: ['night'], ...extra })
const drain = () => act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve() })
const button = name => [...box.querySelectorAll('button')].find(el => el.textContent === name)
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
async function render() { await act(async () => root.render(<Library />)); await drain() }
async function click(el) { expect(el).toBeTruthy(); await act(async () => el.click()); await drain() }
async function type(value) {
  await act(async () => {
    const input = box.querySelector('input')
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function debounce() { await act(async () => vi.advanceTimersByTime(250)); await drain() }
beforeEach(() => {
  vi.useFakeTimers()
  box = document.createElement('div'); document.body.append(box); root = createRoot(box)
  get = vi.spyOn(api, 'get').mockResolvedValue([row(8), row(3, { tags: ['Night', 'nightclub'] })])
})
afterEach(() => { act(() => root.unmount()); box.remove(); vi.useRealTimers(); vi.restoreAllMocks() })

it('shares one initial request, keeps server order, whole tags and real deep links', async () => {
  await render()
  expect(get.mock.calls).toEqual([['/api/sessions']])
  expect([...box.querySelectorAll('.library-card')].map(el => el.getAttribute('href'))).toEqual(['#/session/8', '#/session/3'])
  expect(button('night')).toBeTruthy(); expect(button('Night')).toBeUndefined(); expect(button('nightclub')).toBeTruthy()
  expect(button('All tags').getAttribute('aria-pressed')).toBe('true')
  const event = new MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true })
  box.querySelector('.library-card').dispatchEvent(event)
  expect(event.defaultPrevented).toBe(false)
})
it('debounces trimmed text and combines it with an exact tag while refreshing the full inventory', async () => {
  await render(); await click(button('night'))
  await type('  balcony ')
  expect(get.mock.calls.filter(([url]) => url.includes('q='))).toHaveLength(0)
  await debounce()
  expect(get).toHaveBeenCalledWith('/api/sessions?q=balcony&tag=night')
  expect(get.mock.calls.filter(([url]) => url === '/api/sessions')).toHaveLength(3)
  expect(button('nightclub')).toBeTruthy()
  expect(button('night').getAttribute('aria-pressed')).toBe('true')
  expect(box.querySelector('input').labels[0].textContent).toBe('Search sessions')
})
it('ignores stale rows, errors and tag inventories that finish after a newer filter', async () => {
  const oldRows = deferred(), oldTags = deferred()
  await render()
  get.mockImplementation(async url => url.includes('q=old') ? oldRows.promise : [row(9, { tags: ['fresh'] })])
  get.mockImplementationOnce(() => oldRows.promise).mockImplementationOnce(() => oldTags.promise)
  await type('old'); await debounce()
  await type('new'); await debounce()
  oldRows.reject(new Error('Stale failure')); oldTags.resolve([row(4, { tags: ['stale'] })]); await drain()
  expect(box.querySelector('.library-card').getAttribute('href')).toBe('#/session/9')
  expect(box.textContent).not.toContain('Stale failure'); expect(button('stale')).toBeUndefined()
  expect(button('fresh')).toBeTruthy()
})
it('distinguishes loading, empty inventory and empty matches with focused reset', async () => {
  const pending = deferred(); get.mockReturnValueOnce(pending.promise)
  await render(); expect(box.querySelector('[role=status]').textContent).toBe('Loading sessions…')
  expect(box.textContent).not.toContain('Nothing matched'); expect(box.textContent).not.toContain('No sessions yet')
  pending.resolve([]); await drain()
  expect(box.textContent).toContain('No sessions yet')
  expect(box.querySelector('a').getAttribute('href')).toBe('#/models')
  get.mockResolvedValue([]); await type('missing'); await debounce()
  expect(box.textContent).toContain('Nothing matched'); await click(button('Clear filters'))
  expect(document.activeElement).toBe(box.querySelector('input')); expect(box.querySelector('input').value).toBe('')
})
it('retains search and selected tag after failure and supports retry without a focus loss', async () => {
  await render(); await click(button('night')); await type('balcony')
  get.mockImplementation(async url => url.includes('q=') ? Promise.reject(new Error('Fixture unavailable')) : [row(8)])
  await debounce(); expect(box.querySelector('[role=alert]').textContent).toContain('Fixture unavailable')
  expect(box.querySelector('input').value).toBe('balcony'); expect(button('night').getAttribute('aria-pressed')).toBe('true')
  get.mockResolvedValue([row(8)]); await click(button('Retry loading sessions'))
  expect(box.querySelector('[role=alert]')).toBe(null); expect(document.activeElement).toBe(box.querySelector('input'))
  expect(get).toHaveBeenCalledWith('/api/sessions?q=balcony&tag=night')
})
it('surfaces inventory refresh failures independently from successful results', async () => {
  await render()
  get.mockImplementation(async url => url === '/api/sessions' ? Promise.reject(new Error('Tag fixture unavailable')) : [row(6)])
  await click(button('night'))
  expect(box.querySelector('.library-card').getAttribute('href')).toBe('#/session/6')
  expect(box.querySelector('[role=alert]').textContent).toContain('Could not refresh tags')
  expect(button('nightclub')).toBeTruthy()
  get.mockResolvedValue([row(6)]); await click(button('Retry library'))
  expect(box.querySelector('[role=alert]')).toBe(null)
})
it('keeps full unbroken names, model names, tags and blank-name identifiers without changing data', async () => {
  const records = [row(5, { name: 'A'.repeat(240), model_name: 'B'.repeat(180), tags: ['C'.repeat(160)] }), row(2, { name: '   ' })]
  get.mockResolvedValue(records); await render()
  expect(box.textContent).toContain('A'.repeat(240)); expect(box.textContent).toContain('B'.repeat(180))
  expect(box.textContent).toContain('C'.repeat(160)); expect(box.textContent).toContain('Session #2')
  expect(records[1].name).toBe('   ')
})
it('keeps cover dimensions, lazy loads, missing/failed states and retries when the cover changes', async () => {
  get.mockResolvedValue([row(8, { cover_shot_id: 10 })]); await render()
  const image = box.querySelector('img')
  expect([image.alt, image.width, image.height, image.loading, image.getAttribute('src')]).toEqual(['', 480, 640, 'lazy', '/api/shots/10/image'])
  await act(async () => image.dispatchEvent(new Event('error')))
  expect(box.textContent).toContain('Cover unavailable')
  get.mockResolvedValue([row(8, { cover_shot_id: 11 })]); await click(button('night'))
  expect(box.querySelector('img').getAttribute('src')).toBe('/api/shots/11/image')
  get.mockResolvedValue([row(8)]); await click(button('night'))
  expect(box.querySelector('img')).toBe(null); expect(box.textContent).toContain('No cover yet')
})
it('preserves plan constants and diagnostics without exposing legacy fallback values', async () => {
  get.mockResolvedValue([row(1, { composition_mode: 'resource-v1', look: 'Plan light', wardrobe: 'Plan coat' }),
    row(2, { composition_mode: 'resource-v1', look: 'Legacy hidden', wardrobe: 'Legacy hidden', diagnostic: { code: 'plan_missing', message: 'Plan unavailable' } })])
  await render(); expect(box.textContent).toContain('Plan light'); expect(box.textContent).toContain('Plan coat')
  expect(box.textContent).toContain('plan_missing'); expect(box.textContent).not.toContain('Legacy hidden')
})
it('does not rebuild gallery entries on typing before a response and cancels debounce on unmount', async () => {
  const records = [row(8)], mapping = vi.spyOn(records, 'map'); get.mockResolvedValue(records)
  await render(); const count = mapping.mock.calls.length, card = box.querySelector('.library-card')
  await type('b'); await type('ba'); await type('bal')
  expect(mapping.mock.calls.length).toBe(count); expect(box.querySelector('.library-card')).toBe(card)
  act(() => root.unmount()); root = { unmount() {} }
  await debounce(); expect(get).toHaveBeenCalledTimes(1)
})
