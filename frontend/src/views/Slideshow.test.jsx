// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Slideshow from './Slideshow.jsx'
import { api } from '../api.js'
let root, host, rows, preloads, fullElement, decodeFailure
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const flush = async () => act(async () => { await Promise.resolve(); await Promise.resolve() })
const tick = async (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const mount = async () => {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  await act(async () => { root.render(<Slideshow />); await Promise.resolve() }); await tick(0)
}
const image = () => host.querySelector('.slideshow-photo')
const click = async (name) => act(async () => { const el = [...host.querySelectorAll('button')].find((b) => b.textContent === name); expect(el).toBeTruthy(); el.click(); await Promise.resolve() })
const keyboardActivate = async (name, key) => act(async () => {
  const el = [...host.querySelectorAll('button')].find((button) => button.textContent === name)
  expect(el).toBeTruthy(); el.focus()
  el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
  el.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 0 }))
  el.dispatchEvent(new KeyboardEvent('keyup', { key, bubbles: true }))
  await Promise.resolve()
})
const hash = async (value) => { await act(async () => { window.location.hash = value; window.dispatchEvent(new HashChangeEvent('hashchange')); await Promise.resolve() }); await tick(0) }
const edit = async (id, value) => {
  await act(async () => {
    const el = host.querySelector(`#${id}`)
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(el, value)
    el.dispatchEvent(new Event('change', { bubbles: true })); window.dispatchEvent(new HashChangeEvent('hashchange')); await Promise.resolve()
  }); await tick(0)
}
const fail = async () => act(async () => { image().dispatchEvent(new Event('error')); await Promise.resolve() })
beforeEach(() => {
  vi.useFakeTimers(); preloads = []; fullElement = null; decodeFailure = false
  window.location.hash = '#/slideshow'; document.body.style.overflow = 'auto'
  rows = [{ id: 11, session_id: 1, session_name: 'Morning study' }, { id: 12, session_id: 2, session_name: 'Evening study' }, { id: 13, session_id: 3, session_name: '' }]
  vi.spyOn(Math, 'random').mockReturnValue(.999)
  vi.spyOn(api, 'get').mockImplementation(async () => structuredClone(rows))
  vi.spyOn(api, 'post').mockResolvedValue({})
  vi.stubGlobal('Image', class {
    constructor() { preloads.push(this) }
    decode = vi.fn(() => decodeFailure ? Promise.reject(Error('Missing preload')) : Promise.resolve())
  })
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => fullElement })
  Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: vi.fn(async () => { fullElement = null; document.dispatchEvent(new Event('fullscreenchange')) }) })
  Object.defineProperty(document.documentElement, 'requestFullscreen', { configurable: true, value: vi.fn(async () => { fullElement = document.documentElement; document.dispatchEvent(new Event('fullscreenchange')) }) })
})
afterEach(async () => {
  if (root) await act(async () => root.unmount()); root = null; host?.remove()
  vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals()
  delete document.fullscreenElement; delete document.exitFullscreen; delete document.documentElement.requestFullscreen
  document.body.style.overflow = ''
})
it('opens a focused read-only modal and requests the unchanged all-session listing', async () => {
  await mount()
  expect(host.querySelector('dialog').open).toBe(true); expect(document.activeElement).toBe(host.querySelector('h1'))
  expect(document.body.style.overflow).toBe('hidden')
  expect(api.get).toHaveBeenCalledExactlyOnceWith('/api/photos?min_rating=0')
  expect(image().getAttribute('src')).toBe('/api/shots/11/image')
  expect(image().getAttribute('alt')).toContain('Morning study')
  expect(host.querySelector('a').getAttribute('href')).toBe('#/library')
  expect(api.post).not.toHaveBeenCalled(); expect(document.documentElement.requestFullscreen).not.toHaveBeenCalled()
})
it('distinguishes loading from an empty set and keeps labelled recovery controls', async () => {
  const wait = deferred(); api.get.mockReturnValueOnce(wait.promise)
  await mount(); expect(host.textContent).toContain('Loading photographs…'); expect(host.textContent).not.toContain('No photographs meet this threshold')
  await act(async () => { wait.resolve([]); await Promise.resolve() })
  expect(host.textContent).toContain('No photographs meet this threshold')
  expect(host.querySelector('label[for="ss-rating"]')).toBeTruthy(); expect(host.querySelector('label[for="ss-interval"]')).toBeTruthy()
  expect(host.querySelector('#ss-rating').value).toBe('0'); expect(api.post).not.toHaveBeenCalled()
})
it.each([
  ['interval=7&min_rating=4&lookahead=2', '7', '4', 2],
  ['interval=nope&min_rating=nope&lookahead=nope', '3', '0', 3],
  ['interval=999&min_rating=99&lookahead=99', '60', '5', 3],
  ['interval=0&min_rating=-1&lookahead=0', '1', '0', 1],
])('preserves settings parsing and URL-only lookahead for %s', async (query, interval, rating, count) => {
  window.location.hash = `#/slideshow?${query}`; await mount()
  expect(host.querySelector('#ss-interval').value).toBe(interval); expect(host.querySelector('#ss-rating').value).toBe(rating)
  expect(api.get).toHaveBeenCalledWith(`/api/photos?min_rating=${rating}`); expect(preloads).toHaveLength(count)
  expect(host.querySelectorAll('select')).toHaveLength(2)
})
it('automatically walks the original identities without a repeat at the pass seam', async () => {
  await mount(); const seen = [image().getAttribute('src')]
  await tick(3000); seen.push(image().getAttribute('src')); await tick(3000); seen.push(image().getAttribute('src'))
  expect(seen).toEqual(['/api/shots/11/image', '/api/shots/12/image', '/api/shots/13/image'])
  expect(host.textContent).toContain('Session #3'); await tick(3000)
  expect(image().getAttribute('src')).not.toBe(seen[2]); expect(api.get).toHaveBeenCalledTimes(1)
})
it('retunes the interval without fetching or restarting the set', async () => {
  await mount(); await tick(3000); const current = image().getAttribute('src')
  await edit('ss-interval', '5'); expect(window.location.hash).toContain('interval=5')
  expect(image().getAttribute('src')).toBe(current); expect(api.get).toHaveBeenCalledTimes(1)
  await tick(4999); expect(image().getAttribute('src')).toBe(current)
  await tick(1); expect(image().getAttribute('src')).toBe('/api/shots/13/image')
})
it('keeps one timer through a reshuffle and clears it on unmount', async () => {
  const schedule = vi.spyOn(globalThis, 'setInterval'); const clear = vi.spyOn(globalThis, 'clearInterval')
  await mount(); await tick(9000)
  expect(schedule.mock.calls.filter((call) => call[1] === 3000)).toHaveLength(1); expect(clear).not.toHaveBeenCalled()
  await act(async () => root.unmount()); root = null
  expect(clear).toHaveBeenCalledTimes(1); expect(document.body.style.overflow).toBe('auto')
  const count = api.get.mock.calls.length; await tick(6000); expect(api.get).toHaveBeenCalledTimes(count)
})
it('does not run an advance timer for a single photograph and deduplicates a large lookahead', async () => {
  rows = [rows[0]]; window.location.hash = '#/slideshow?lookahead=10'
  const schedule = vi.spyOn(globalThis, 'setInterval')
  await mount(); expect(preloads).toHaveLength(1); expect(preloads[0].decode).toHaveBeenCalledTimes(1)
  await tick(60000); expect(image().getAttribute('src')).toBe('/api/shots/11/image')
  expect(schedule).not.toHaveBeenCalled(); expect(host.textContent).toContain('Single photograph')
})
it('reuses decoded photographs and decodes only the next new identity', async () => {
  rows.push({ id: 14, session_id: 4, session_name: 'Fourth study' }); window.location.hash = '#/slideshow?lookahead=2'
  await mount(); expect(preloads.map((p) => p.src)).toEqual(['/api/shots/12/image', '/api/shots/13/image'])
  await tick(3000); expect(preloads.map((p) => p.src)).toEqual(['/api/shots/12/image', '/api/shots/13/image', '/api/shots/14/image'])
})
it('ignores stale threshold responses and preserves the remaining query parameters', async () => {
  const first = deferred(); api.get.mockReturnValueOnce(first.promise)
  window.location.hash = '#/slideshow?interval=7&lookahead=2&extra=keep'; await mount()
  api.get.mockResolvedValueOnce([{ id: 22, session_id: 2, session_name: 'New threshold' }])
  await edit('ss-rating', '4')
  expect(window.location.hash).toContain('interval=7'); expect(window.location.hash).toContain('lookahead=2'); expect(window.location.hash).toContain('extra=keep')
  expect(api.get).toHaveBeenLastCalledWith('/api/photos?min_rating=4')
  await act(async () => { first.resolve(rows); await Promise.resolve() })
  expect(image().getAttribute('src')).toBe('/api/shots/22/image'); expect(host.textContent).toContain('New threshold')
})
it('keeps the timer cadence while a new threshold request is pending', async () => {
  await mount(); const second = deferred(); api.get.mockReturnValueOnce(second.promise)
  await edit('ss-rating', '4'); await tick(3000)
  expect(image().getAttribute('src')).toBe('/api/shots/12/image'); expect(host.textContent).toContain('Updating the photograph set')
  await act(async () => { second.resolve([]); await Promise.resolve() })
  expect(image()).toBeNull(); expect(host.textContent).toContain('No photographs meet this threshold')
})
it('retries listing errors only on an explicit action and keeps URL settings', async () => {
  api.get.mockRejectedValueOnce(Error('Listing unavailable')); window.location.hash = '#/slideshow?interval=7&min_rating=4'
  await mount(); expect(host.querySelector('[role="alert"]').textContent).toBe('Listing unavailable')
  await tick(30000); expect(api.get).toHaveBeenCalledTimes(1)
  await click('Retry photographs'); expect(api.get).toHaveBeenCalledTimes(2)
  expect(host.querySelector('#ss-interval').value).toBe('7'); expect(host.querySelector('#ss-rating').value).toBe('4')
})
it('keeps focus on the viewer while a keyboard retry is pending and after success', async () => {
  const response = deferred()
  api.get.mockRejectedValueOnce(Error('Listing unavailable')).mockReturnValueOnce(response.promise)
  window.location.hash = '#/slideshow?interval=7&min_rating=4&lookahead=2'
  await mount()
  await keyboardActivate('Retry photographs', 'Enter')
  expect(api.get).toHaveBeenCalledTimes(2)
  expect(document.activeElement).toBe(host.querySelector('h1'))
  expect(host.querySelector('.slideshow-stage').getAttribute('aria-busy')).toBe('true')
  await act(async () => { response.resolve(rows); await Promise.resolve(); await Promise.resolve() })
  expect(image()).toBeTruthy(); expect(document.activeElement).toBe(host.querySelector('h1'))
  expect(window.location.hash).toBe('#/slideshow?interval=7&min_rating=4&lookahead=2')
})
it('keeps focus on the viewer when a keyboard retry returns another error', async () => {
  const response = deferred()
  api.get.mockRejectedValueOnce(Error('Listing unavailable')).mockReturnValueOnce(response.promise)
  await mount(); await keyboardActivate('Retry photographs', ' ')
  expect(document.activeElement).toBe(host.querySelector('h1'))
  await act(async () => { response.reject(Error('Retry unavailable')); await Promise.resolve(); await Promise.resolve() })
  expect(host.querySelector('[role="alert"]').textContent).toBe('Retry unavailable')
  expect(document.activeElement).toBe(host.querySelector('h1'))
  expect(api.get).toHaveBeenCalledTimes(2)
})
it('keeps focus on the viewer when reloading an empty set by keyboard', async () => {
  const response = deferred()
  api.get.mockResolvedValueOnce([]).mockReturnValueOnce(response.promise)
  rows = []
  await mount(); await keyboardActivate('Reload photographs', 'Enter')
  expect(document.activeElement).toBe(host.querySelector('h1'))
  expect(host.querySelector('.slideshow-stage').getAttribute('aria-busy')).toBe('true')
  await act(async () => { response.resolve([]); await Promise.resolve(); await Promise.resolve() })
  expect(host.textContent).toContain('No photographs meet this threshold')
  expect(document.activeElement).toBe(host.querySelector('h1'))
})
it('does not move focus to the viewer when a pointer retry starts without focus', async () => {
  const response = deferred()
  api.get.mockRejectedValueOnce(Error('Listing unavailable')).mockReturnValueOnce(response.promise)
  await mount()
  const interval = host.querySelector('#ss-interval'), button = [...host.querySelectorAll('button')].find((item) => item.textContent === 'Retry photographs')
  interval.focus()
  await act(async () => { button.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 })); await Promise.resolve() })
  expect(document.activeElement).toBe(interval)
  await act(async () => { response.resolve(rows); await Promise.resolve(); await Promise.resolve() })
})
it('does not steal focus moved voluntarily while a keyboard retry is pending', async () => {
  const response = deferred()
  api.get.mockRejectedValueOnce(Error('Listing unavailable')).mockReturnValueOnce(response.promise)
  await mount(); await keyboardActivate('Retry photographs', 'Enter')
  const interval = host.querySelector('#ss-interval')
  interval.focus()
  await act(async () => { response.resolve(rows); await Promise.resolve(); await Promise.resolve() })
  expect(document.activeElement).toBe(interval)
})
it('skips a missing image while decode failures leave playback working', async () => {
  decodeFailure = true; await mount(); await fail()
  expect(image().getAttribute('src')).toBe('/api/shots/12/image'); expect(host.textContent).toContain('Unavailable photographs encountered: 1')
  await tick(3000); expect(image().getAttribute('src')).toBe('/api/shots/13/image')
})
it('stops repeated failed-image requests when every image is unavailable without altering the timer', async () => {
  rows = rows.slice(0, 2); await mount(); await fail(); await fail()
  expect(image()).toBeNull(); expect(host.textContent).toContain('No photograph could be displayed')
  const prepared = preloads.length; await tick(30000); expect(preloads).toHaveLength(prepared); expect(api.get).toHaveBeenCalledTimes(1)
  await click('Retry photographs'); expect(api.get).toHaveBeenCalledTimes(2); expect(image()).toBeTruthy(); expect(api.post).not.toHaveBeenCalled()
})
it('ignores a stale image error after the timer has moved to another photograph', async () => {
  await mount(); const old = image(); await tick(3000)
  await act(async () => old.dispatchEvent(new Event('error')))
  expect(image().getAttribute('src')).toBe('/api/shots/12/image')
})
it('tracks external fullscreen changes and exits owned fullscreen on unmount', async () => {
  await mount(); await click('Fullscreen'); expect(document.documentElement.requestFullscreen).toHaveBeenCalledTimes(1)
  expect([...host.querySelectorAll('button')].some((b) => b.textContent === 'Exit fullscreen')).toBe(true)
  await act(async () => { fullElement = null; document.dispatchEvent(new Event('fullscreenchange')) })
  await click('Fullscreen'); await act(async () => root.unmount()); root = null
  expect(document.exitFullscreen).toHaveBeenCalledTimes(1); expect(fullElement).toBeNull()
})
it('reports a fullscreen refusal without changing the photograph or writing data', async () => {
  document.documentElement.requestFullscreen.mockRejectedValueOnce(Error('Fullscreen blocked'))
  await mount(); await click('Fullscreen')
  expect(host.textContent).toContain('Fullscreen blocked'); expect(image().getAttribute('src')).toBe('/api/shots/11/image'); expect(api.post).not.toHaveBeenCalled()
})
it('leaves on native Escape cancellation when fullscreen is not active', async () => {
  await mount()
  const cancel = new Event('cancel', { cancelable: true })
  await act(async () => host.querySelector('dialog').dispatchEvent(cancel))
  expect(cancel.defaultPrevented).toBe(true); expect(window.location.hash).toBe('#/library')
  expect(document.exitFullscreen).not.toHaveBeenCalled()
})
it('uses the first fullscreen Escape only to return to window mode', async () => {
  await mount(); await click('Fullscreen')
  const dialog = host.querySelector('dialog')
  const cancel = new Event('cancel', { cancelable: true })
  await act(async () => {
    dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    dialog.dispatchEvent(cancel)
    dialog.dispatchEvent(new KeyboardEvent('keyup', { key: 'Escape', bubbles: true }))
    fullElement = null
    document.dispatchEvent(new Event('fullscreenchange'))
  })
  expect(cancel.defaultPrevented).toBe(true)
  expect(document.exitFullscreen).toHaveBeenCalledTimes(1)
  expect(window.location.hash).toBe('#/slideshow')
  expect(dialog.open).toBe(true); expect(fullElement).toBeNull()

  const secondCancel = new Event('cancel', { cancelable: true })
  await act(async () => dialog.dispatchEvent(secondCancel))
  expect(window.location.hash).toBe('#/library')
  expect(host.querySelector('a').getAttribute('href')).toBe('#/library')
})
it('allows the next Escape to leave when fullscreen exit fails', async () => {
  await mount(); await click('Fullscreen')
  document.exitFullscreen.mockRejectedValueOnce(Error('Fullscreen exit blocked'))
  const dialog = host.querySelector('dialog')
  await act(async () => {
    dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    dialog.dispatchEvent(new Event('cancel', { cancelable: true }))
    dialog.dispatchEvent(new KeyboardEvent('keyup', { key: 'Escape', bubbles: true }))
    await Promise.resolve(); await Promise.resolve()
  })
  expect(dialog.open).toBe(true); expect(window.location.hash).toBe('#/slideshow')
  expect(host.querySelector('[role="alert"]').textContent).toContain('Fullscreen exit blocked')

  await act(async () => {
    dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    dialog.dispatchEvent(new Event('cancel', { cancelable: true }))
    dialog.dispatchEvent(new KeyboardEvent('keyup', { key: 'Escape', bubbles: true }))
  })
  expect(window.location.hash).toBe('#/library')
})
it('lets the fullscreen button exit without closing the viewer', async () => {
  await mount(); await click('Fullscreen'); await click('Exit fullscreen')
  expect(fullElement).toBeNull(); expect(host.querySelector('dialog').open).toBe(true)
  expect(window.location.hash).toBe('#/slideshow')
})
it('does not adopt a listing that resolves after unmount', async () => {
  const wait = deferred(); api.get.mockReturnValueOnce(wait.promise); await mount()
  await act(async () => root.unmount()); root = null
  await act(async () => { wait.resolve(rows); await Promise.resolve() })
  expect(host.textContent).toBe(''); expect(preloads).toHaveLength(0)
})

it('disables unsupported fullscreen without changing playback', async () => {
  delete document.documentElement.requestFullscreen
  await mount(); expect([...host.querySelectorAll('button')].find((b) => b.textContent === 'Fullscreen').disabled).toBe(true)
  expect(host.textContent).toContain('Fullscreen is unavailable'); await tick(3000)
  expect(image().getAttribute('src')).toBe('/api/shots/12/image')
})
it('cleans up a fullscreen request that resolves after unmount', async () => {
  const wait = deferred()
  document.documentElement.requestFullscreen.mockImplementationOnce(() => wait.promise.then(() => { fullElement = document.documentElement }))
  await mount(); await click('Fullscreen'); await act(async () => root.unmount()); root = null
  await act(async () => { wait.resolve(); await Promise.resolve(); await Promise.resolve() })
  expect(document.exitFullscreen).toHaveBeenCalledTimes(1); expect(fullElement).toBeNull()
})

it('wraps Tab and Shift+Tab through available viewer controls', async () => {
  await mount()
  const first = host.querySelector('a'); const last = [...host.querySelectorAll('button')].at(-1)
  first.focus()
  const back = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })
  await act(async () => first.dispatchEvent(back))
  expect(back.defaultPrevented).toBe(true); expect(document.activeElement).toBe(last)
  const forward = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
  await act(async () => last.dispatchEvent(forward))
  expect(forward.defaultPrevented).toBe(true); expect(document.activeElement).toBe(first)
  host.querySelector('h1').focus()
  await act(async () => host.querySelector('h1').dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })))
  expect(document.activeElement).toBe(last)
})

it('does not claim playback continues after a later listing failure clears the deck', async () => {
  await mount(); await fail(); expect(host.textContent).toContain('Playback continues')
  api.get.mockRejectedValueOnce(Error('New threshold unavailable')); await edit('ss-rating', '4')
  expect(image()).toBeNull(); expect(host.textContent).toContain('New threshold unavailable')
  expect(host.textContent).not.toContain('Playback continues')
})
