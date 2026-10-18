// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api.js'
import AutomationProfileSelector from './AutomationProfileSelector.jsx'

const digest = 'a'.repeat(64)
const profileDigest = 'b'.repeat(64)
const look = { key: 'g4-look', version: '17', content_digest: digest }
const lookB = { key: 'g4-look', version: '18', content_digest: 'c'.repeat(64) }

const row = (revision = '3', readiness = true, rowDigest = profileDigest) => ({
  profile_revision: revision,
  profile_digest: rowDigest,
  readiness: {
    profile_ready: readiness,
    diagnostics: readiness ? [] : [{ code: 'coverage_unknown', region: 'feet' }],
  },
})

const catalogue = (exactLook = look, profiles = [row()]) => ({
  look: { key: exactLook.key, version: exactLook.version, content_digest: exactLook.content_digest },
  latest_profile_revision: profiles.at(-1)?.profile_revision || '0',
  profiles,
})

const exact = (item = row(), exactLook = look, appearance = 'Current appearance.') => ({
  look: { key: exactLook.key, version: exactLook.version, content_digest: exactLook.content_digest },
  profile_revision: item.profile_revision,
  profile_digest: item.profile_digest,
  appearance_segments: [{ segment_id: 'permanent-1', owner: 'permanent', text: appearance }],
})

const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

let root
let container

async function drain() {
  await act(async () => {
    for (let index = 0; index < 20; index += 1) await Promise.resolve()
  })
}

function mount(props) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  const render = (nextProps) => act(() => root.render(<AutomationProfileSelector {...nextProps} />))
  render(props)
  return { render }
}

async function chooseRevision(revision) {
  const select = container.querySelector('select[aria-label="Exact automation profile revision"]')
  expect(select).toBeTruthy()
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(select), 'value')?.set
    if (setter) setter.call(select, String(revision))
    else select.value = String(revision)
    select.dispatchEvent(new Event('change', { bubbles: true }))
    for (let index = 0; index < 20; index += 1) await Promise.resolve()
  })
}

beforeEach(() => vi.restoreAllMocks())
afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  container = null
  vi.restoreAllMocks()
})

describe('Group 4 independent exact-profile acceptance', () => {
  it('does not auto-select a sole ready profile for callers that did not opt in', async () => {
    const get = vi.spyOn(api, 'getVersioned').mockResolvedValue(catalogue(look, [row('41')]))
    const onProfileChange = vi.fn()
    mount({ look, onProfileChange })
    await drain()

    expect(container.querySelector('select').value).toBe('')
    expect(get).toHaveBeenCalledTimes(1)
    expect(get.mock.calls[0][0]).toContain('/versions/17/automation-profiles?')
    expect(get.mock.calls[0][0]).toContain(`content_digest=${digest}`)
    expect(onProfileChange).toHaveBeenCalledWith(null)
    expect(onProfileChange.mock.calls.some(([value]) => value?.profile_revision)).toBe(false)
  })

  it('preserves the legacy close/reopen reset and refresh for non-opt-in consumers', async () => {
    let discoveryCalls = 0
    const get = vi.spyOn(api, 'getVersioned').mockImplementation((path) => {
      if (path.includes('profile_revision=')) return Promise.resolve(exact(row('41')))
      discoveryCalls += 1
      return Promise.resolve(catalogue(look, [row(discoveryCalls === 1 ? '41' : '42')]))
    })
    const onProfileChange = vi.fn()
    const { render } = mount({ look, active: true, onProfileChange })
    await drain()
    await chooseRevision('41')
    expect(onProfileChange).toHaveBeenLastCalledWith(expect.objectContaining({ profile_revision: '41' }))

    render({ look, active: false, onProfileChange })
    expect(container.querySelector('select').value).toBe('')
    expect(onProfileChange).toHaveBeenLastCalledWith(null)

    render({ look, active: true, onProfileChange })
    await drain()
    expect(container.querySelector('select').value).toBe('')
    expect([...container.querySelectorAll('option')].map((option) => option.value)).toEqual(['', '42'])
    expect(discoveryCalls).toBe(2)
    expect(get).toHaveBeenCalledTimes(3)
  })

  it.each([
    ['different look key', (value) => { value.look.key = 'other-look' }],
    ['different look version', (value) => { value.look.version = '16' }],
    ['different look digest', (value) => { value.look.content_digest = 'c'.repeat(64) }],
    ['duplicate profile revision', (value) => { value.profiles.push(row('3', false, 'd'.repeat(64))) }],
    ['malformed readiness diagnostics', (value) => { value.profiles[0].readiness.diagnostics = null }],
  ])('fails closed for a malformed discovery envelope or row: %s', async (_caseName, corrupt) => {
    const response = catalogue(look, [row()])
    corrupt(response)
    const get = vi.spyOn(api, 'getVersioned').mockResolvedValue(response)
    const onDiscovery = vi.fn()
    const onProfileChange = vi.fn()
    mount({ look, autoSelectSoleReady: true, onDiscovery, onProfileChange })
    await drain()

    expect(container.querySelector('select').value).toBe('')
    expect(container.querySelector('[role="alert"]')?.textContent)
      .toContain('different or incomplete identity')
    expect(get).toHaveBeenCalledTimes(1)
    expect(onDiscovery).not.toHaveBeenCalled()
    expect(onProfileChange.mock.calls.some(([value]) => value?.profile_revision)).toBe(false)
  })

  it('uses discovery readiness as the only readiness authority and leaves an incomplete sole row explicit', async () => {
    const get = vi.spyOn(api, 'getVersioned').mockResolvedValue(catalogue(look, [row('44', false)]))
    const onProfileChange = vi.fn()
    mount({ look, autoSelectSoleReady: true, onProfileChange })
    await drain()

    expect(container.querySelector('select').value).toBe('')
    expect(container.textContent).toContain('Coverage is unknown for feet.')
    expect(container.querySelector('[aria-label="No ready automation profile"]')).toBeTruthy()
    expect(get).toHaveBeenCalledTimes(1)
    expect(get.mock.calls[0][0]).not.toContain('profile_revision=')
    expect(onProfileChange.mock.calls.some(([value]) => value?.profile_revision)).toBe(false)
  })

  it.each([
    ['wrong look key', (value) => { value.look.key = 'other-look' }],
    ['wrong look version', (value) => { value.look.version = '16' }],
    ['wrong content digest', (value) => { value.look.content_digest = 'c'.repeat(64) }],
    ['wrong profile revision', (value) => { value.profile_revision = '4' }],
    ['wrong profile digest', (value) => { value.profile_digest = 'd'.repeat(64) }],
    ['missing appearance segments', (value) => { delete value.appearance_segments }],
  ])('rejects the loaded exact response when %s', async (_caseName, corrupt) => {
    const loaded = exact(row())
    corrupt(loaded)
    const get = vi.spyOn(api, 'getVersioned').mockImplementation((path) => (
      path.includes('profile_revision=') ? Promise.resolve(loaded) : Promise.resolve(catalogue(look, [row()]))
    ))
    const onProfileChange = vi.fn()
    mount({ look, autoSelectSoleReady: true, onProfileChange })
    await drain()

    expect(container.querySelector('select').value).toBe('3')
    expect(container.querySelector('[role="alert"]')?.textContent)
      .toContain('different or incomplete profile revision')
    expect(onProfileChange.mock.calls.some(([value]) => value?.profile_revision)).toBe(false)
    const exactUrl = get.mock.calls.map(([path]) => path).find((path) => path.includes('profile_revision='))
    expect(exactUrl).toContain(`look_key=${look.key}`)
    expect(exactUrl).toContain(`version=${look.version}`)
    expect(exactUrl).toContain(`content_digest=${digest}`)
    expect(exactUrl).toContain('profile_revision=3')
    expect(exactUrl).toContain(`profile_digest=${profileDigest}`)
  })

  it('fences a pending exact A load across A-to-B-to-A even when the request identity repeats', async () => {
    const staleA = deferred()
    const currentA = deferred()
    let exactACalls = 0
    const get = vi.spyOn(api, 'getVersioned').mockImplementation((path) => {
      if (!path.includes('profile_revision=')) {
        return Promise.resolve(path.includes(`content_digest=${lookB.content_digest}`)
          ? catalogue(lookB, [row('7', false, 'd'.repeat(64))])
          : catalogue(look, [row('3')]))
      }
      exactACalls += 1
      return exactACalls === 1 ? staleA.promise : currentA.promise
    })
    const onProfileChange = vi.fn()
    const { render } = mount({ look, autoSelectSoleReady: true, onProfileChange })
    await drain()
    expect(exactACalls).toBe(1)

    render({ look: lookB, autoSelectSoleReady: true, onProfileChange })
    render({ look, autoSelectSoleReady: true, onProfileChange })
    await drain()
    expect(exactACalls).toBe(2)

    currentA.resolve(exact(row('3'), look, 'Current A appearance.'))
    await drain()
    expect(onProfileChange).toHaveBeenLastCalledWith(expect.objectContaining({
      look_key: look.key,
      version: look.version,
      content_digest: look.content_digest,
      profile_revision: '3',
      profile_digest: profileDigest,
      permanent_appearance: 'Current A appearance.',
    }))

    staleA.resolve(exact(row('3'), look, 'Stale A appearance.'))
    await drain()
    const accepted = onProfileChange.mock.calls
      .map(([value]) => value)
      .filter((value) => value?.profile_revision === '3')
    expect(accepted.map((value) => value.permanent_appearance)).toEqual(['Current A appearance.'])
    expect(get).toHaveBeenCalledTimes(5)
  })

  it('does not publish an exact-profile result after the selector unmounts', async () => {
    const pending = deferred()
    const incomplete = row('9', false)
    vi.spyOn(api, 'getVersioned').mockImplementation((path) => (
      path.includes('profile_revision=') ? pending.promise : Promise.resolve(catalogue(look, [incomplete]))
    ))
    const onProfileChange = vi.fn()
    mount({ look, onProfileChange })
    await drain()
    await chooseRevision('9')
    expect(container.querySelector('[role="status"]')?.textContent).toContain('Loading exact profile identity')

    await act(async () => root.unmount())
    root = null
    pending.resolve(exact(incomplete))
    await drain()

    expect(onProfileChange.mock.calls.some(([value]) => value?.profile_revision === '9')).toBe(false)
  })
})
