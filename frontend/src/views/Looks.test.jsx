// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRoot } from 'react-dom/client'
import App from '../App.jsx'
import { api } from '../api.js'
import Looks from './Looks.jsx'

const inventory = {
  garments: [
    { key: 'garment-one-piece', wording: 'a single-piece blue dress', aside: '' },
    { key: 'garment-jacket', wording: 'a short denim jacket', aside: 'the denim jacket pushed back from the shoulders' },
    { key: 'garment-earrings', wording: 'small silver earrings', aside: '' },
  ],
  outfits: [{ key: 'outfit-layered', garments: 'garment-one-piece,garment-jacket' }],
}

const summary = (key, version, name) => ({
  key,
  version,
  name,
  content_digest: `${String(version).padStart(64, 'a')}`,
})

const savedLook = ({
  key = 'look-studio', version = 1, name = 'Studio look', appearance = 'dark braided hair', outfit = null,
} = {}) => ({
  ...summary(key, version, name),
  appearance,
  outfit,
})

describe('Looks manual editor', () => {
  let container
  let root
  let summaries
  let versions

  const render = async (component = <Looks />) => act(async () => { root.render(component) })
  const click = async (element) => {
    expect(element).toBeTruthy()
    await act(async () => { element.click() })
  }
  const button = (label, scope = container) => Array.from(scope.querySelectorAll('button'))
    .find((item) => typeof label === 'string'
      ? item.textContent.trim() === label || item.getAttribute('aria-label') === label
      : label.test(item.textContent))
  const setValue = async (element, value) => {
    expect(element).toBeTruthy()
    const prototype = element instanceof HTMLTextAreaElement
      ? window.HTMLTextAreaElement.prototype
      : element instanceof HTMLSelectElement
        ? window.HTMLSelectElement.prototype
        : window.HTMLInputElement.prototype
    await act(async () => {
      Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value)
      element.dispatchEvent(new Event('input', { bubbles: true }))
      element.dispatchEvent(new Event('change', { bubbles: true }))
    })
  }
  const field = (label) => container.querySelector(`[aria-label="${label}"]`)
  const openNewLook = async () => {
    await click(button('Create look'))
    expect(field('Look name')).toBeTruthy()
  }
  const selectOutfitMode = async (value) => setValue(field('Optional outfit'), value)
  const saveNewLook = async ({ name = 'Manual look', appearance = '' } = {}) => {
    await openNewLook()
    await setValue(field('Look name'), name)
    if (appearance) await setValue(field('Constant appearance'), appearance)
    await click(button('Save look'))
  }

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    summaries = []
    versions = {}
    window.location.hash = ''
    vi.restoreAllMocks()
    vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === '/api/looks') return summaries
      if (path === '/api/wardrobe') return inventory
      const match = path.match(/^\/api\/looks\/([^/]+)\/versions\/(\d+)$/)
      if (match) {
        const key = decodeURIComponent(match[1])
        const version = Number(match[2])
        const result = versions[`${key}:${version}`]
        if (!result) throw Object.assign(new Error('Look version not found.'), { status: 404 })
        return result
      }
      if (path === '/api/comfy/status') return { online: false, output_dir_ok: true }
      if (path === '/api/components?all=1') return []
      throw new Error(`Unexpected GET ${path}`)
    })
    vi.spyOn(api, 'post').mockImplementation(async (path, body) => {
      if (path === '/api/looks') return savedLook({ ...body, version: 1 })
      if (/^\/api\/looks\/[^/]+\/versions$/.test(path)) {
        const key = decodeURIComponent(path.split('/')[3])
        return savedLook({ ...body, key, version: (body.expected_version || 0) + 1 })
      }
      throw new Error(`Unexpected POST ${path}`)
    })
  })

  afterEach(() => {
    act(() => root?.unmount())
    container?.remove()
    container = null
    vi.restoreAllMocks()
  })

  it('makes Looks discoverable and saves an appearance-only look manually', async () => {
    const post = api.post
    window.location.hash = '#/looks'
    await render(<App />)
    expect(container.querySelector('nav a[href="#/looks"]')?.textContent).toBe('Looks')
    expect(container.querySelector('h1')?.textContent).toBe('Looks')

    await click(button('Create look'))
    await setValue(field('Look name'), 'Quiet studio')
    await setValue(field('Constant appearance'), 'short dark hair and subtle makeup')
    expect(container.textContent).toContain('Review this description for clothing')
    expect(container.textContent).toContain('not automatic detection')
    await click(button('Save look'))

    expect(post).toHaveBeenCalledWith('/api/looks', {
      name: 'Quiet studio',
      appearance: 'short dark hair and subtle makeup',
      garments: [],
    })
    expect(container.textContent).toContain('Saved as version 1')
    expect(container.textContent).toContain('Previous versions remain available')
    expect(container.textContent).not.toContain('look-studio')
  })

  it('reuses an existing outfit without exposing its key or sending garment definitions', async () => {
    const post = api.post
    await render()
    await openNewLook()
    await setValue(field('Look name'), 'Layered look')
    await selectOutfitMode('outfit-layered')
    expect(container.querySelector('[aria-label="Outfit removal order"]')?.textContent)
      .toContain('a single-piece blue dress')
    expect(container.textContent).not.toContain('outfit-layered')
    await click(button('Save look'))

    const body = post.mock.calls[0][1]
    expect(body).toEqual({ name: 'Layered look', appearance: '', outfit_key: 'outfit-layered' })
    expect(Object.hasOwn(body, 'garments')).toBe(false)
  })

  it('adds one-piece clothing, layers and accessories, reorders rows, and removes an optional row', async () => {
    const post = api.post
    await render()
    await openNewLook()
    await setValue(field('Look name'), 'Layered accessory look')
    await selectOutfitMode('__manual__')

    for (const key of ['garment-one-piece', 'garment-jacket', 'garment-earrings']) {
      await setValue(field('Add existing garment'), key)
      await click(button('Add existing garment'))
    }
    await click(button('Move garment 3 up'))
    await click(button('Add new garment'))
    await setValue(field('Garment 4 worn wording'), 'a small ribbon pinned at the shoulder')
    await setValue(field('Garment 4 moved-aside wording'), 'the ribbon untied and held aside')
    await click(button('Add new garment'))
    await setValue(field('Garment 5 worn wording'), 'a temporary row')
    await click(button('Remove garment 5'))
    expect(container.textContent).toContain('No body-part categories are required.')
    await click(button('Save look'))

    const body = post.mock.calls[0][1]
    expect(body).toEqual({
      name: 'Layered accessory look',
      appearance: '',
      garments: [
        { key: 'garment-one-piece' },
        { key: 'garment-earrings' },
        { key: 'garment-jacket' },
        { wording: 'a small ribbon pinned at the shoulder', aside: 'the ribbon untied and held aside' },
      ],
    })
    expect(body).not.toHaveProperty('outfit_key')
    expect(container.textContent).not.toContain('garment-one-piece')
    expect(container.textContent).not.toContain('garment-earrings')
  })

  it('loads history and saves an edit as a new immutable version using the latest expected version', async () => {
    summaries = [summary('look-studio', 2, 'Studio look')]
    versions['look-studio:1'] = savedLook({ version: 1, appearance: 'first appearance' })
    versions['look-studio:2'] = savedLook({ version: 2, appearance: 'latest appearance' })
    const post = api.post
    await render()
    await click(button('Studio look · version 2'))
    expect(field('Constant appearance').value).toBe('latest appearance')
    expect(Array.from(field('Version history').options).map((option) => option.textContent))
      .toEqual(['Version 1', 'Version 2 · latest'])

    await setValue(field('Version history'), '1')
    expect(field('Constant appearance').value).toBe('first appearance')
    await setValue(field('Constant appearance'), 'corrected appearance')
    await click(button('Save look'))

    expect(post).toHaveBeenCalledWith('/api/looks/look-studio/versions', {
      name: 'Studio look',
      appearance: 'corrected appearance',
      garments: [],
      expected_version: 2,
    })
    expect(container.textContent).toContain('Saved as version 3')
    expect(Array.from(field('Version history').options).map((option) => option.textContent))
      .toEqual(['Version 1', 'Version 2', 'Version 3 · latest'])
  })

  it('keeps the form intact and shows a readable disabled-feature error after a failed save', async () => {
    vi.spyOn(api, 'post').mockRejectedValue(Object.assign(
      new Error('Resource planning feature is disabled.'), { status: 503 },
    ))
    await render()
    await openNewLook()
    await setValue(field('Look name'), 'Still editable')
    await click(button('Save look'))

    expect(container.querySelector('[role="alert"]')?.textContent)
      .toContain('Resource planning is disabled.')
    expect(field('Look name').value).toBe('Still editable')
    expect(field('Look name').disabled).toBe(false)
  })

  it('keeps a just-saved look when a pre-save list refresh resolves late', async () => {
    let resolveRefresh
    const refreshResult = new Promise((resolve) => { resolveRefresh = resolve })
    let listReads = 0
    const get = vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === '/api/looks') {
        listReads += 1
        return listReads === 1 ? [] : refreshResult
      }
      if (path === '/api/wardrobe') return inventory
      throw new Error(`Unexpected GET ${path}`)
    })
    vi.spyOn(api, 'post').mockImplementation(async (_path, body) => savedLook({
      key: 'look-race',
      version: 1,
      name: body.name,
      appearance: body.appearance,
    }))
    await render()
    await click(button('Refresh'))
    await openNewLook()
    await setValue(field('Look name'), 'Race look')
    await click(button('Save look'))
    expect(button('Race look · version 1')).toBeTruthy()
    expect(container.textContent).not.toContain('Loading saved looks…')

    await act(async () => {
      resolveRefresh([])
      await refreshResult
    })
    expect(get.mock.calls.filter(([path]) => path === '/api/looks')).toHaveLength(2)
    expect(button('Race look · version 1')).toBeTruthy()
    expect(container.textContent).not.toContain('No saved looks yet.')
  })

  it('keeps the latest version and history when an older list snapshot resolves late', async () => {
    let resolveRefresh
    const refreshResult = new Promise((resolve) => { resolveRefresh = resolve })
    let listReads = 0
    const get = vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === '/api/looks') {
        listReads += 1
        return listReads === 1 ? [summary('look-race', 1, 'Race look')] : refreshResult
      }
      if (path === '/api/wardrobe') return inventory
      if (path === '/api/looks/look-race/versions/1') {
        return savedLook({ key: 'look-race', version: 1, name: 'Race look' })
      }
      throw new Error(`Unexpected GET ${path}`)
    })
    vi.spyOn(api, 'post').mockImplementation(async (_path, body) => savedLook({
      key: 'look-race',
      version: body.expected_version + 1,
      name: body.name,
      appearance: body.appearance,
    }))
    await render()
    await click(button('Refresh'))
    await click(button('Race look · version 1'))
    await setValue(field('Constant appearance'), 'newer appearance')
    await click(button('Save look'))
    expect(button('Race look · version 2')).toBeTruthy()
    expect(Array.from(field('Version history').options).map((option) => option.textContent))
      .toEqual(['Version 1', 'Version 2 · latest'])

    await act(async () => {
      resolveRefresh([summary('look-race', 1, 'Race look')])
      await refreshResult
    })
    expect(get.mock.calls.filter(([path]) => path === '/api/looks')).toHaveLength(2)
    expect(button('Race look · version 2')).toBeTruthy()
    expect(Array.from(field('Version history').options).map((option) => option.textContent))
      .toEqual(['Version 1', 'Version 2 · latest'])
  })

  it('ignores a stale look-version response after the user opens another look', async () => {
    summaries = [summary('look-alpha', 1, 'Alpha'), summary('look-beta', 1, 'Beta')]
    let resolveAlpha
    const alpha = new Promise((resolve) => { resolveAlpha = resolve })
    versions['look-beta:1'] = savedLook({ key: 'look-beta', name: 'Beta', appearance: 'beta appearance' })
    const get = vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === '/api/looks') return summaries
      if (path === '/api/wardrobe') return inventory
      if (path === '/api/looks/look-alpha/versions/1') return alpha
      if (path === '/api/looks/look-beta/versions/1') return versions['look-beta:1']
      throw new Error(`Unexpected GET ${path}`)
    })
    await render()
    await click(button('Alpha · version 1'))
    await click(button('Beta · version 1'))
    expect(field('Constant appearance').value).toBe('beta appearance')

    await act(async () => {
      resolveAlpha(savedLook({ key: 'look-alpha', name: 'Alpha', appearance: 'stale alpha appearance' }))
      await alpha
    })
    expect(get).toHaveBeenCalledWith('/api/looks/look-alpha/versions/1')
    expect(field('Constant appearance').value).toBe('beta appearance')
    expect(container.textContent).not.toContain('stale alpha appearance')
  })
})
