// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, afterEach, it, expect, vi } from 'vitest'
import { Judge } from './Judge.jsx'
import AppShell from '../components/AppShell.jsx'
import { api } from '../api.js'
let root, host, readings, pass
const drain = () => new Promise((resolve) => setTimeout(resolve, 0))
const button = (text) => [...host.querySelectorAll('button')].find((b) => b.textContent.includes(text))
const click = async (el) => { expect(el).toBeTruthy(); await act(async () => { if (!el.disabled) el.focus(); el.click(); await drain() }) }
const edit = async (id, value) => act(async () => {
  const el = host.querySelector(`#${id}`)
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value').set.call(el, value)
  el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); await drain()
})
const mount = async (element = <Judge />) => { host = document.createElement('div'); document.body.append(host); root = createRoot(host); await act(async () => { root.render(element); await drain(); await drain() }) }
const loaded = async () => act(async () => { host.querySelector('img').dispatchEvent(new Event('load')); await drain() })
const key = async (value, extra = {}) => act(async () => { document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: value, bubbles: true, cancelable: true, ...extra })); await drain() })
const begin = async () => { await mount(); await click(button('Start Judging Pass')); await loaded() }
beforeEach(() => {
  window.location.hash = '#/judge?session_id=1'
  readings = [{ id: 1, slot: 'camera', key: 'front', label: 'Front-facing view', session_id: null }, { id: 2, slot: 'framing', key: 'wide', label: 'Whole figure', session_id: null }]
  pass = { shots: [11, 12], controls: [], readings: [readings[0]] }
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    if (path === '/api/sessions') return [1, 2].map((id) => ({ id, name: `Studio ${id}`, manner: 'directed', shot_count: 2 }))
    if (/^\/api\/sessions\/\d+$/.test(path)) return { id: Number(path.split('/').pop()), name: 'Studio', manner: 'directed', checkpoint: 'test-model' }
    if (path.startsWith('/api/readings?')) return structuredClone(readings)
    if (path.includes('/judge-pass?')) return structuredClone(pass)
    throw Error(path)
  })
  vi.spyOn(api, 'post').mockResolvedValue({ answered: 'front', agreed: true })
  vi.spyOn(api, 'del').mockResolvedValue({})
  vi.stubGlobal('confirm', vi.fn(() => false))
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(async () => { if (root) await act(async () => root.unmount()); host?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
it('requires an explicit axis and preserves the request contract', async () => {
  readings[0].axis = 'position'; readings.push({ ...readings[0], id: 3, key: 'low', axis: 'height', label: 'Low view' })
  await mount(); expect(button('Start Judging Pass').disabled).toBe(true)
  await click([...host.querySelectorAll('button')].find((b) => b.textContent === 'position')); await click(button('Start Judging Pass'))
  expect(api.get).toHaveBeenCalledWith('/api/sessions/1/judge-pass?slot=camera&axis=position')
  expect(api.post).not.toHaveBeenCalled()
})
it('keeps the blind photograph bare and labels None with the actual 0 shortcut', async () => {
  pass.readings[0].key = 'SECRET_FAMILY'
  await begin(); expect(host.textContent).not.toContain('SECRET_FAMILY')
  expect(button('None or cannot tell').querySelector('kbd').textContent).toBe('0')
  await key('0'); expect(api.post).toHaveBeenCalledWith('/api/shots/11/judge', { camera: '', control: false })
  expect(host.querySelector('h2').textContent).toContain('Photo 2')
})
it('preserves framing deep links and the matching question and answer body', async () => {
  window.location.hash = '#/judge?session_id=1&slot=framing'; pass.readings = [readings[1]]
  await begin(); expect(host.textContent).toContain('Which framing')
  await key('1'); expect(api.post).toHaveBeenCalledWith('/api/shots/11/judge', { framing: 'wide', control: false })
})
it('does not submit until a photograph loads and supports an explicit image retry', async () => {
  await mount(); await click(button('Start Judging Pass')); await key('1'); expect(api.post).not.toHaveBeenCalled()
  await act(async () => host.querySelector('img').dispatchEvent(new Event('error')))
  expect(button('Front-facing').disabled).toBe(true)
  await click(button('Reload photograph')); await loaded(); await click(button('Front-facing'))
  expect(api.post).toHaveBeenCalledTimes(1)
})
it('preserves contradiction semantics and ignores modified or repeated shortcuts', async () => {
  await begin(); await key('1', { ctrlKey: true }); await key('1', { repeat: true }); expect(api.post).not.toHaveBeenCalled()
  await key('C'); expect(api.post).toHaveBeenCalledWith('/api/shots/11/judge', { defect: 'contradiction', slot: 'camera', control: false })
})
it('keeps the current photo on a failed answer without automatic retry', async () => {
  api.post.mockRejectedValueOnce(Error('Response uncertain'))
  await begin(); await key('1'); expect(host.textContent).toContain('Response uncertain')
  expect(host.querySelector('h2').textContent).toContain('Photo 1'); expect(api.post).toHaveBeenCalledTimes(1)
})
it('blocks duplicate submissions and does not resurrect a pass after exit', async () => {
  let resolve; api.post.mockImplementationOnce(() => new Promise((yes) => { resolve = yes }))
  await begin(); await key('1'); await key('1'); expect(api.post).toHaveBeenCalledTimes(1)
  await key('Escape'); expect(host.textContent).toContain('Configure the pass')
  expect(document.activeElement).toBe(host.querySelector('h2'))
  await act(async () => { resolve({ answered: 'front' }); await drain() })
  expect(host.textContent).not.toContain('Photo 2'); expect(host.textContent).not.toContain('Judging Pass Complete')
})
it('reports control agreement without altering the deck or scoring helper', async () => {
  pass = { shots: [], controls: [21, 22], readings: [readings[0]] }
  vi.spyOn(Math, 'random').mockReturnValue(.99)
  api.post.mockResolvedValueOnce({ agreed: true, stored: 'front', answered: 'front' }).mockResolvedValueOnce({ agreed: false, stored: 'side', answered: 'front' })
  await begin(); await key('1'); await loaded(); await key('1')
  expect(api.post).toHaveBeenNthCalledWith(1, '/api/shots/21/judge', { camera: 'front', control: true })
  expect(host.textContent).toContain('50%'); expect(host.textContent).toContain('1 / 2')
  expect(host.querySelector('tbody').textContent).toContain('side')
  expect(host.querySelector('a').getAttribute('href')).toBe('#/session/1')
  expect(document.activeElement).toBe(host.querySelector('h2'))
})
it('reports no available agreement rather than a zero score', async () => {
  pass.shots = [11]; await begin(); await key('1')
  expect(host.textContent).toContain('No agreement score is available'); expect(host.textContent).not.toContain('0%')
})
it('keeps reading drafts on failure and sends explicit scoped additions', async () => {
  await mount(); await edit('judge-reading-key', 'overhead'); await edit('judge-reading-label', 'View from above')
  api.post.mockRejectedValueOnce(Error('Family unavailable'))
  await act(async () => { host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await drain() })
  expect(api.post).toHaveBeenCalledWith('/api/readings', { slot: 'camera', manner: 'directed', key: 'overhead', label: 'View from above', session_id: 1 })
  expect(host.querySelector('#judge-reading-key').value).toBe('overhead'); expect(host.textContent).toContain('Family unavailable')
})
it('removes only scoped readings after confirmation through the existing del helper', async () => {
  readings.push({ id: 3, slot: 'camera', key: 'above', label: 'Above', session_id: 1 })
  await mount(); expect(host.querySelectorAll('.judge-remove')).toHaveLength(1)
  await click(button('Remove')); expect(api.del).not.toHaveBeenCalled()
  confirm.mockReturnValue(true); await click(button('Remove')); expect(api.del).toHaveBeenCalledWith('/api/readings/3')
})
it('rejects late session details after changing the selection', async () => {
  let resolve; const original = api.get.getMockImplementation()
  api.get.mockImplementation((path) => path === '/api/sessions/1' ? new Promise((yes) => { resolve = yes }) : original(path))
  await mount(); await edit('judge-session', '2')
  await act(async () => { resolve({ id: 1, name: 'Stale session', manner: 'pov' }); await drain() })
  expect(host.textContent).not.toContain('Stale session'); expect(api.get).not.toHaveBeenCalledWith('/api/readings?manner=pov&session_id=2')
})
it('shows empty and blocked passes without decisions', async () => {
  pass.shots = []; await mount(); await click(button('Start Judging Pass'))
  expect(host.textContent).toContain('No photographs in session #1'); expect(api.post).not.toHaveBeenCalled()
})

it('distinguishes a confirmed rejection from an uncertain network result', async () => {
  api.post.mockRejectedValueOnce(Object.assign(Error('Already judged'), { status: 409 }))
  await begin(); await key('1'); expect(host.textContent).toContain('The answer was rejected')
  expect(host.textContent).not.toContain('The response was not confirmed')
})
it('sends the correct control flag for each identity in a mixed deck', async () => {
  pass.shots = [11]; pass.controls = [21]; vi.spyOn(Math, 'random').mockReturnValue(.99)
  await begin(); await key('1'); await loaded(); await key('0')
  expect(api.post).toHaveBeenNthCalledWith(1, '/api/shots/11/judge', { camera: 'front', control: false })
  expect(api.post).toHaveBeenNthCalledWith(2, '/api/shots/21/judge', { camera: '', control: true })
})

it('ignores a stale pass when its question changes while loading', async () => {
  readings[0].axis = 'position'; readings.push({ ...readings[0], id: 4, key: 'high', axis: 'height' })
  let resolve; const original = api.get.getMockImplementation()
  api.get.mockImplementation((path) => path.includes('/judge-pass?') ? new Promise((yes) => { resolve = yes }) : original(path))
  await mount(); await click([...host.querySelectorAll('button')].find((b) => b.textContent === 'position'))
  await click(button('Start Judging Pass')); await click([...host.querySelectorAll('button')].find((b) => b.textContent === 'height'))
  await act(async () => { resolve(pass); await drain() })
  expect(host.querySelector('img')).toBeNull(); expect(button('height').getAttribute('aria-pressed')).toBe('true')
  expect(api.post).not.toHaveBeenCalled()
})
it('does not adopt stale vocabulary after another session is selected', async () => {
  let resolve; const original = api.get.getMockImplementation()
  api.get.mockImplementation((path) => path === '/api/readings?manner=directed&session_id=1' ? new Promise((yes) => { resolve = yes }) : original(path))
  await mount(); await edit('judge-session', '2')
  await act(async () => { resolve([{ id: 9, slot: 'camera', key: 'stale', label: 'Stale vocabulary', session_id: 1 }]); await drain() })
  expect(host.textContent).not.toContain('Stale vocabulary'); expect(host.querySelector('#judge-session').value).toBe('2')
})

it('treats a server failure as an uncertain outcome without advancing or retrying', async () => {
  api.post.mockRejectedValueOnce(Object.assign(Error('Server failure'), { status: 500 }))
  await begin(); await key('1')
  expect(host.textContent).toContain('The response was not confirmed')
  expect(host.textContent).not.toContain('The answer was rejected')
  expect(host.querySelector('h2').textContent).toContain('Photo 1'); expect(api.post).toHaveBeenCalledTimes(1)
})

const answerKeys = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'c', 'C']
const shell = (view = 'judge') => <AppShell view={view} routeKey={view}>{view === 'judge' ? <Judge /> : <p>Library route</p>}</AppShell>
it('blocks answer shortcuts in the real mobile navigation and restores safe evaluation after closing', async () => {
  const get = api.get.getMockImplementation()
  api.get.mockImplementation((path) => path === '/api/comfy/status' ? Promise.resolve({ online: true }) : get(path))
  vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })
  await mount(shell()); await click(button('Start Judging Pass')); await loaded()
  const trigger = host.querySelector('[aria-label="Open navigation"]')
  trigger.focus(); await click(trigger)
  const drawer = host.querySelector('[role="dialog"][aria-modal="true"]')
  expect(drawer).toBeTruthy(); expect(host.querySelector('.judge-pass').closest('[inert]')).toBeTruthy()
  for (const control of drawer.querySelectorAll('a, button')) {
    control.focus()
    for (const value of answerKeys) await key(value)
  }
  expect(api.post).not.toHaveBeenCalled(); expect(host.querySelector('.judge-pass h2').textContent).toContain('Photo 1')
  await key('Escape')
  expect(host.querySelector('[aria-modal="true"]')).toBeNull(); expect(document.activeElement).toBe(trigger)
  for (const value of answerKeys) await key(value)
  expect(api.post).not.toHaveBeenCalled()
  host.querySelector('.judge-pass h2').focus(); await key('0')
  expect(api.post).toHaveBeenCalledWith('/api/shots/11/judge', { camera: '', control: false })
  await loaded()
  trigger.focus(); await click(trigger)
  const library = host.querySelector('nav a[href="#/library"]')
  library.focus(); await click(library)
  await act(async () => { root.render(shell('library')); await drain() })
  expect(host.querySelector('.judge-pass')).toBeNull()
  for (const value of answerKeys) await key(value)
  expect(api.post).toHaveBeenCalledTimes(1)
  await act(async () => root.unmount()); root = null
  for (const value of answerKeys) await key(value)
  expect(api.post).toHaveBeenCalledTimes(1)
})
it('ignores events outside Judge and inside inert regions even during a ready pass', async () => {
  await begin()
  const outside = document.createElement('button'); outside.textContent = 'Outside Judge'; host.append(outside)
  outside.focus(); for (const value of answerKeys) await key(value)
  expect(api.post).not.toHaveBeenCalled()
  const heading = host.querySelector('.judge-pass h2'); heading.focus()
  const region = document.createElement('div'); region.setAttribute('inert', '')
  host.querySelector('.judge-pass').append(region)
  const control = document.createElement('button'); region.append(control)
  for (const value of answerKeys) await act(async () => control.dispatchEvent(new KeyboardEvent('keydown', { key: value, bubbles: true })))
  host.querySelector('.judge-pass').setAttribute('inert', '')
  for (const value of answerKeys) await key(value)
  expect(api.post).not.toHaveBeenCalled()
  host.querySelector('.judge-pass').removeAttribute('inert'); await key('1')
  expect(api.post).toHaveBeenCalledWith('/api/shots/11/judge', { camera: 'front', control: false })
})
it('blocks shortcuts when a foreign modal is open even if focus leaks into Judge', async () => {
  await begin()
  for (const markup of ['<dialog open></dialog>', '<div role="dialog" aria-modal="true"></div>', '<div role="alertdialog" aria-modal="true"></div>']) {
    const outside = document.createElement('div'); outside.innerHTML = markup; host.append(outside)
    for (const value of answerKeys) await key(value)
    expect(api.post).not.toHaveBeenCalled(); outside.remove()
  }
  await key('C')
  expect(api.post).toHaveBeenCalledWith('/api/shots/11/judge', { defect: 'contradiction', slot: 'camera', control: false })
})
it('keeps text-entry, editable, modified and handled events out of the answer shortcuts', async () => {
  await begin()
  const pass = host.querySelector('.judge-pass')
  for (const tag of ['input', 'textarea', 'select', 'div']) {
    const field = document.createElement(tag)
    if (tag === 'div') { field.contentEditable = 'true'; field.tabIndex = 0 }
    pass.append(field); field.focus()
    for (const value of answerKeys) await key(value)
    field.remove()
  }
  const heading = pass.querySelector('h2'); heading.focus()
  for (const modifier of ['ctrlKey', 'metaKey', 'altKey', 'repeat']) for (const value of answerKeys) await key(value, { [modifier]: true })
  await act(async () => {
    const event = new KeyboardEvent('keydown', { key: '0', bubbles: true, cancelable: true }); event.preventDefault(); heading.dispatchEvent(event)
  })
  expect(api.post).not.toHaveBeenCalled()
  await key('1'); expect(api.post).toHaveBeenCalledTimes(1)
})

const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const settleAnswer = async (request, status = 200) => act(async () => {
  if (status === 200) request.resolve({ answered: 'front' })
  else request.reject(Object.assign(Error('Response not confirmed'), { status }))
  await drain()
})

it.each(['Enter', ' '])('keeps stable focus and Escape during a pending answer activated with %s', async (activation) => {
  const request = deferred(); api.post.mockReturnValueOnce(request.promise)
  await begin()
  const answer = button('Front-facing'); answer.focus()
  await key(activation)
  // happy-dom does not implement the browser's default keyboard activation.
  await click(answer)
  expect(answer.disabled).toBe(true)
  expect(document.activeElement).toBe(host.querySelector('.judge-pass h2'))
  await key('0'); await key('C'); await click(answer)
  expect(api.post).toHaveBeenCalledExactlyOnceWith('/api/shots/11/judge', { camera: 'front', control: false })
  await key('Escape')
  expect(host.querySelector('.judge-pass')).toBeNull()
  expect(document.activeElement).toBe(host.querySelector('h2'))
  await settleAnswer(request)
  expect(host.textContent).not.toContain('Photo 2')
  expect(api.post).toHaveBeenCalledTimes(1)
})

for (const modal of ['dialog', 'alertdialog', 'native']) {
  it.each([200, 503])(`keeps an external ${modal} focused when an answer returns %s`, async (status) => {
    const request = deferred(); api.post.mockReturnValueOnce(request.promise)
    await begin(); await key('1')
    const external = document.createElement(modal === 'native' ? 'dialog' : 'div')
    if (modal === 'native') external.setAttribute('open', '')
    else { external.setAttribute('role', modal); external.setAttribute('aria-modal', 'true') }
    const action = document.createElement('button'); action.textContent = 'External action'
    external.append(action); host.append(external); action.focus()
    await settleAnswer(request, status)
    expect(document.activeElement).toBe(action)
    expect(host.textContent).toContain(status === 200 ? 'Photo 2' : 'The response was not confirmed')
    await key('0'); expect(api.post).toHaveBeenCalledTimes(1)
    external.remove()
    const navigation = document.createElement('button'); host.append(navigation); navigation.focus()
    await act(async () => { await drain() })
    expect(document.activeElement).toBe(navigation)
  })
}

it.each([200, 503])('does not steal voluntary external focus after an answer returns %s', async (status) => {
  const request = deferred(); api.post.mockReturnValueOnce(request.promise)
  await begin(); await key('1')
  const outside = document.createElement('button'); outside.textContent = 'Navigation'; host.append(outside); outside.focus()
  await settleAnswer(request, status)
  expect(document.activeElement).toBe(outside)
  expect(api.post).toHaveBeenCalledTimes(1)
})

it.each([200, 503])('leaves the mobile drawer focused while an answer returns %s behind inert content', async (status) => {
  const get = api.get.getMockImplementation()
  api.get.mockImplementation((path) => path === '/api/comfy/status' ? Promise.resolve({ online: true }) : get(path))
  vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })
  const request = deferred(); api.post.mockReturnValueOnce(request.promise)
  await mount(shell()); await click(button('Start Judging Pass')); await loaded(); await key('1')
  const trigger = host.querySelector('[aria-label="Open navigation"]'); await click(trigger)
  const focused = document.activeElement
  expect(host.querySelector('.judge-pass').closest('[inert]')).toBeTruthy()
  await settleAnswer(request, status)
  expect(document.activeElement).toBe(focused)
  await key('0'); expect(api.post).toHaveBeenCalledTimes(1)
  await key('Escape'); expect(document.activeElement).toBe(trigger)
})

it('moves focus before removing Reload and keeps answers disabled until the current image loads', async () => {
  await begin()
  await act(async () => host.querySelector('img').dispatchEvent(new Event('error')))
  const reload = button('Reload photograph'); await click(reload)
  expect(document.activeElement).toBe(host.querySelector('.judge-pass h2'))
  expect(button('Front-facing').disabled).toBe(true)
  expect(host.querySelector('img').getAttribute('src')).toBe('/api/shots/11/image')
  await key('1'); expect(api.post).not.toHaveBeenCalled()
  await loaded(); expect(document.activeElement).toBe(host.querySelector('.judge-pass h2'))
  await key('1'); expect(api.post).toHaveBeenCalledExactlyOnceWith('/api/shots/11/judge', { camera: 'front', control: false })
})

it('does not restore image retry focus after voluntary navigation or a later modal', async () => {
  await begin()
  await act(async () => host.querySelector('img').dispatchEvent(new Event('error')))
  await click(button('Reload photograph'))
  const outside = document.createElement('div'); outside.setAttribute('role', 'dialog'); outside.setAttribute('aria-modal', 'true')
  const action = document.createElement('button'); outside.append(action); host.append(outside); action.focus()
  await loaded()
  expect(document.activeElement).toBe(action)
  await key('1'); expect(api.post).not.toHaveBeenCalled()
})

it.each([200, 503])('does not restore focus or a pass after unmount and a late %s response', async (status) => {
  const request = deferred(); api.post.mockReturnValueOnce(request.promise)
  await begin(); await key('1')
  await act(async () => root.unmount()); root = null
  const outside = document.createElement('button'); host.append(outside); outside.focus()
  await settleAnswer(request, status)
  expect(document.activeElement).toBe(outside)
  expect(host.querySelector('.judge-pass')).toBeNull()
  expect(api.post).toHaveBeenCalledTimes(1)
})
