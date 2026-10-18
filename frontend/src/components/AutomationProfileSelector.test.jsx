// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api.js'
import AutomationProfileSelector from './AutomationProfileSelector.jsx'

const digest = 'a'.repeat(64)
const profileDigest = 'b'.repeat(64)
const look = { key: 'profile-selector', version: '9223372036854775807', content_digest: digest }
const readyRow = (revision, profileDigestValue = profileDigest) => ({
  profile_revision: String(revision),
  profile_digest: profileDigestValue,
  readiness: { profile_ready: true, diagnostics: [] },
})
const incompleteRow = (revision, profileDigestValue = 'c'.repeat(64)) => ({
  profile_revision: String(revision),
  profile_digest: profileDigestValue,
  readiness: {
    profile_ready: false,
    diagnostics: [{ code: 'coverage_unknown', region: 'feet' }],
  },
})
const discoveryFor = (profiles) => ({
  look: { key: look.key, version: look.version, content_digest: digest },
  latest_profile_revision: String(Math.max(0, ...profiles.map((item) => Number(item.profile_revision)))),
  profiles,
})
const exactProfileFor = (row, profileLook = look) => ({
  ...row,
  look: { key: profileLook.key, version: profileLook.version, content_digest: profileLook.content_digest },
  appearance_segments: [{ segment_id: 'p', owner: 'permanent', text: 'Fixed hair.' }],
})

let root
let container

const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

async function drain() {
  await act(async () => {
    for (let index = 0; index < 16; index += 1) await Promise.resolve()
  })
}

async function selectValue(element, value) {
  expect(element).toBeTruthy()
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), 'value')?.set
    if (setter) setter.call(element, String(value))
    else element.value = String(value)
    element.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await drain()
}

function mount(props) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  const render = (nextProps) => act(() => root.render(<AutomationProfileSelector {...nextProps} />))
  render(props)
  return { render }
}

beforeEach(() => vi.restoreAllMocks())
afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  container = null
  vi.restoreAllMocks()
})

describe('exact automation profile selector', () => {
  it('fences stale discovery responses and never selects the newest revision implicitly', async () => {
    const oldResponse = deferred()
    const currentDiscovery = {
      look: { key: 'profile-selector', version: '9223372036854775807', content_digest: digest },
      latest_profile_revision: '9007199254740994',
      profiles: [{
        profile_revision: '9007199254740993', profile_digest: profileDigest,
        readiness: { profile_ready: false, manual_available: true, diagnostics: [{ code: 'coverage_unknown', region: 'feet' }] },
      }],
    }
    const get = vi.spyOn(api, 'getVersioned').mockImplementation((path) => (
      path.includes('content_digest')
        ? (path.includes(firstLook.content_digest) ? oldResponse.promise : Promise.resolve(currentDiscovery))
        : Promise.resolve(null)
    ))
    const onChange = vi.fn()
    const firstLook = { key: 'profile-selector', version: '1', content_digest: 'c'.repeat(64) }
    const { render } = mount({ look: firstLook, active: true, onProfileChange: onChange })
    render({ look, active: true, onProfileChange: onChange })
    await drain()
    oldResponse.resolve({
      look: { key: firstLook.key, version: firstLook.version, content_digest: firstLook.content_digest },
      latest_profile_revision: 1,
      profiles: [{ profile_revision: 1, profile_digest: 'c'.repeat(64), readiness: { profile_ready: true, manual_available: true, diagnostics: [] } }],
    })
    await drain()

    const select = container.querySelector('select')
    expect(select.value).toBe('')
    expect([...select.options].map((option) => option.textContent).join(' ')).toContain('9007199254740993')
    expect([...select.options].map((option) => option.textContent).join(' ')).not.toContain('Revision 1 · ccccc')
    expect(container.textContent).not.toContain('feet')
    expect(onChange).toHaveBeenCalledWith(null)
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('loads only the selected exact revision and exposes server diagnostics', async () => {
    const discovery = {
      look: { key: look.key, version: look.version, content_digest: digest },
      latest_profile_revision: '9007199254740994',
      profiles: [{
        profile_revision: '9007199254740993', profile_digest: profileDigest,
        readiness: { profile_ready: false, manual_available: true, diagnostics: [{ code: 'coverage_unknown', region: 'feet' }] },
      }],
    }
    const exact = {
      ...discovery.profiles[0],
      look: discovery.look,
      appearance_segments: [{ segment_id: 'p', owner: 'permanent', text: 'Fixed hair.' }],
    }
    const get = vi.spyOn(api, 'getVersioned').mockImplementation((path) => (
      path.includes('profile_revision') ? Promise.resolve(exact) : Promise.resolve(discovery)
    ))
    const onChange = vi.fn()
    mount({ look, active: true, onProfileChange: onChange })
    await drain()
    const select = container.querySelector('select')
    expect(select.value).toBe('')
    await selectValue(select, '9007199254740993')

    const identityUrl = get.mock.calls.find(([path]) => path.includes('profile_revision'))[0]
    expect(identityUrl).toContain('version=9223372036854775807')
    expect(identityUrl).toContain('profile_revision=9007199254740993')
    expect(identityUrl).toContain(`profile_digest=${profileDigest}`)
    expect(container.textContent).toContain('Coverage is unknown for feet.')
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      look_key: look.key,
      version: look.version,
      content_digest: digest,
      profile_revision: '9007199254740993',
      profile_digest: profileDigest,
      permanent_appearance: 'Fixed hair.',
    }))
  })

  it('selects and verifies the sole ready revision when other revisions need review', async () => {
    const incomplete = incompleteRow('9007199254740993')
    const ready = readyRow('9007199254740994', 'd'.repeat(64))
    const discovery = discoveryFor([incomplete, ready])
    const get = vi.spyOn(api, 'getVersioned').mockImplementation((path) => (
      path.includes('profile_revision')
        ? Promise.resolve(exactProfileFor(ready))
        : Promise.resolve(discovery)
    ))
    const onChange = vi.fn()
    mount({ look, active: true, autoSelectSoleReady: true, onProfileChange: onChange })
    await drain()

    const select = container.querySelector('select')
    expect(select.value).toBe('9007199254740994')
    expect(container.textContent).toContain('Server readiness: ready for Full Auto.')
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      profile_revision: '9007199254740994',
      profile_digest: 'd'.repeat(64),
      readiness: ready.readiness,
    }))
    const exactPath = get.mock.calls.find(([path]) => path.includes('profile_revision'))[0]
    expect(exactPath).toContain('version=9223372036854775807')
    expect(exactPath).toContain('profile_revision=9007199254740994')
    expect(exactPath).toContain(`profile_digest=${'d'.repeat(64)}`)
  })

  it('keeps zero-ready and multiple-ready revisions explicit with diagnostics', async () => {
    const lookB = { key: look.key, version: '2', content_digest: 'c'.repeat(64) }
    const get = vi.spyOn(api, 'getVersioned').mockImplementation((path) => (
      path.includes(`content_digest=${lookB.content_digest}`)
        ? Promise.resolve({
          look: { key: lookB.key, version: lookB.version, content_digest: lookB.content_digest },
          profiles: [readyRow('1'), readyRow('2', 'd'.repeat(64))],
        })
        : Promise.resolve(discoveryFor([incompleteRow('1')]))
    ))
    const { render } = mount({ look, active: true, autoSelectSoleReady: true })
    await drain()

    expect(container.querySelector('select').value).toBe('')
    expect(container.querySelector('[aria-label="No ready automation profile"]')).toBeTruthy()
    expect(container.textContent).toContain('Coverage is unknown for feet.')
    expect(container.querySelector('a[href="#/looks"]')?.textContent).toContain('Open Looks')
    expect(get).toHaveBeenCalledTimes(1)

    render({ look: lookB, active: true, autoSelectSoleReady: true })
    await drain()

    expect(container.querySelector('select').value).toBe('')
    expect(container.textContent).toContain('More than one profile revision is ready.')
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('does not accept a loaded profile when its digest differs from the exact catalogue row', async () => {
    const ready = readyRow('2')
    const get = vi.spyOn(api, 'getVersioned').mockImplementation((path) => (
      path.includes('profile_revision')
        ? Promise.resolve(exactProfileFor({ ...ready, profile_digest: 'd'.repeat(64) }))
        : Promise.resolve(discoveryFor([ready]))
    ))
    const onChange = vi.fn()
    mount({ look, active: true, autoSelectSoleReady: true, onProfileChange: onChange })
    await drain()

    expect(container.textContent).toContain('The server returned a different or incomplete profile revision.')
    expect(onChange).not.toHaveBeenCalledWith(expect.objectContaining({ profile_revision: '2' }))
    expect(get.mock.calls.find(([path]) => path.includes('profile_revision'))[0]).toContain(`profile_digest=${profileDigest}`)
  })

  it('fences A-to-B-to-A discovery responses by the newest request identity', async () => {
    const firstA = deferred()
    const lookB = { key: look.key, version: '2', content_digest: 'c'.repeat(64) }
    const onDiscovery = vi.fn()
    const get = vi.spyOn(api, 'getVersioned').mockImplementation((path) => {
      if (path.includes(`content_digest=${digest}`)) {
        const matchingCalls = get.mock.calls.filter(([url]) => url.includes(`content_digest=${digest}`)).length
        return matchingCalls === 1 ? firstA.promise : Promise.resolve(discoveryFor([incompleteRow('3')]))
      }
      return Promise.resolve({
        look: { key: lookB.key, version: lookB.version, content_digest: lookB.content_digest },
        profiles: [incompleteRow('8')],
      })
    })
    const { render } = mount({ look, active: true, autoSelectSoleReady: true, onDiscovery })
    render({ look: lookB, active: true, autoSelectSoleReady: true, onDiscovery })
    render({ look, active: true, autoSelectSoleReady: true, onDiscovery })
    await drain()
    firstA.resolve(discoveryFor([readyRow('1')]))
    await drain()

    expect(container.querySelector('select').value).toBe('')
    expect([...container.querySelectorAll('option')].map((option) => option.value)).toEqual(['', '3'])
    expect(onDiscovery).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledTimes(3)
  })

  it('retains a manual choice when Full Auto is toggled off and back on', async () => {
    const first = readyRow('1')
    const second = readyRow('2', 'd'.repeat(64))
    const get = vi.spyOn(api, 'getVersioned').mockImplementation((path) => (
      path.includes('profile_revision')
        ? Promise.resolve(exactProfileFor(second))
        : Promise.resolve(discoveryFor([first, second]))
    ))
    const onChange = vi.fn()
    const { render } = mount({ look, active: true, autoSelectSoleReady: true, onProfileChange: onChange })
    await drain()
    await selectValue(container.querySelector('select'), '2')
    expect(container.querySelector('select').value).toBe('2')

    render({ look, active: false, autoSelectSoleReady: true, onProfileChange: onChange })
    render({ look, active: true, autoSelectSoleReady: true, onProfileChange: onChange })
    await drain()

    expect(container.querySelector('select').value).toBe('2')
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ profile_revision: '2' }))
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('resets and rediscovers an explicit-mode choice when its panel closes and reopens', async () => {
    const row = readyRow('1')
    const get = vi.spyOn(api, 'getVersioned').mockImplementation((path) => (
      path.includes('profile_revision')
        ? Promise.resolve(exactProfileFor(row))
        : Promise.resolve(discoveryFor([row]))
    ))
    const onChange = vi.fn()
    const { render } = mount({ look, active: true, onProfileChange: onChange })
    await drain()
    await selectValue(container.querySelector('select'), '1')
    expect(container.querySelector('select').value).toBe('1')
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ profile_revision: '1' }))

    render({ look, active: false, onProfileChange: onChange })
    expect(container.querySelector('select').value).toBe('')
    expect(onChange).toHaveBeenLastCalledWith(null)

    render({ look, active: true, onProfileChange: onChange })
    await drain()

    expect(container.querySelector('select').value).toBe('')
    expect(get).toHaveBeenCalledTimes(3)
    expect(onChange).toHaveBeenLastCalledWith(null)
  })

  it('does not publish an exact-load response after unmount', async () => {
    const exactResponse = deferred()
    const selectedRow = incompleteRow('1')
    vi.spyOn(api, 'getVersioned').mockImplementation((path) => (
      path.includes('profile_revision') ? exactResponse.promise : Promise.resolve(discoveryFor([selectedRow]))
    ))
    const onChange = vi.fn()
    mount({ look, active: true, onProfileChange: onChange })
    await drain()
    await act(async () => {
      const select = container.querySelector('select')
      select.value = '1'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await act(async () => root.unmount())
    root = null
    exactResponse.resolve(exactProfileFor(selectedRow))
    await drain()

    expect(onChange).not.toHaveBeenCalledWith(expect.objectContaining({ profile_revision: '1' }))
  })
})
