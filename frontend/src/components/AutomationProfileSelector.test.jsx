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
})
