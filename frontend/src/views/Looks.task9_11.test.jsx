// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRoot } from 'react-dom/client'
import { api } from '../api.js'
import Looks from './Looks.jsx'

const stage = {
  photo_id: 'b'.repeat(32),
  state: 'staged',
  created_at: '2026-10-05T10:00:00+00:00',
  expires_at: '2026-10-06T10:00:00+00:00',
  format: 'PNG',
  media_type: 'image/png',
  byte_count: 68,
  width: 1,
  height: 1,
  cleanup_warning: null,
  saved_look: null,
}

const wardrobe = { garments: [], outfits: [] }
const SQLITE_MAX = '9223372036854775807'
const MAX_SAFE_VERSION = '9007199254740991'
const MAX_SAFE_SUCCESSOR = '9007199254740992'
const saved = (name = 'Studio study') => ({
  key: 'look-local',
  version: 1,
  name,
  content_digest: 'a'.repeat(64),
  appearance: 'short dark hair',
  outfit: null,
})

describe('Looks Task 9.11 browser actions', () => {
  let container
  let root
  let createObjectURLDescriptor
  let revokeObjectURLDescriptor

  const flush = () => new Promise((resolve) => setTimeout(resolve, 0))
  const render = async () => act(async () => { root.render(<Looks />); await flush() })
  const click = async (element) => {
    expect(element).toBeTruthy()
    await act(async () => { element.click(); await flush() })
  }
  const button = (label, scope = container) => Array.from(scope.querySelectorAll('button'))
    .find((item) => item.textContent.trim() === label || item.getAttribute('aria-label') === label)
  const field = (label) => container.querySelector(`[aria-label="${label}"]`)
  const setValue = async (element, value) => {
    expect(element).toBeTruthy()
    const prototype = element instanceof HTMLTextAreaElement
      ? window.HTMLTextAreaElement.prototype
      : element instanceof HTMLSelectElement
        ? window.HTMLSelectElement.prototype
        : element instanceof HTMLInputElement
          ? window.HTMLInputElement.prototype
          : null
    if (prototype) Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value)
    else element.value = value
    await act(async () => {
      element.dispatchEvent(new Event('input', { bubbles: true }))
      element.dispatchEvent(new Event('change', { bubbles: true }))
      await flush()
    })
  }
  const chooseFile = async (label, file) => {
    const input = field(label)
    Object.defineProperty(input, 'files', { configurable: true, value: [file] })
    await act(async () => { input.dispatchEvent(new Event('change', { bubbles: true })); await flush() })
  }
  const mockBaseReads = (config = { llm_ok: false, llm_vision_model: '' }, list = []) => {
    vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === '/api/looks') return list
      if (path === '/api/wardrobe') return wardrobe
      if (path === '/api/config') return config
      if (path === `/api/looks/${stage.photo_id}`) return stage
      if (path === `/api/looks/photo-stages/${stage.photo_id}`) return stage
      if (path === `/api/looks/look-local/versions/1`) return saved()
      throw new Error(`Unexpected GET ${path}`)
    })
  }
  const syntheticPhoto = () => new File([new Uint8Array([137, 80, 78, 71])], 'synthetic.png', { type: 'image/png' })

  beforeEach(() => {
    createObjectURLDescriptor = Object.getOwnPropertyDescriptor(URL, 'createObjectURL')
    revokeObjectURLDescriptor = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL')
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    vi.restoreAllMocks()
    mockBaseReads()
    vi.spyOn(api, 'post').mockRejectedValue(new Error('Unexpected POST'))
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(stage)
  })

  afterEach(() => {
    act(() => root?.unmount())
    container?.remove()
    container = null
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    if (createObjectURLDescriptor) Object.defineProperty(URL, 'createObjectURL', createObjectURLDescriptor)
    else delete URL.createObjectURL
    if (revokeObjectURLDescriptor) Object.defineProperty(URL, 'revokeObjectURL', revokeObjectURLDescriptor)
    else delete URL.revokeObjectURL
  })

  it('exports the selected immutable version as a portable JSON download', async () => {
    const get = api.get
    vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === '/api/looks') return [{ key: 'look-local', version: 2, name: 'Studio study', content_digest: 'a'.repeat(64) }]
      if (path === '/api/wardrobe') return wardrobe
      if (path === '/api/config') return { llm_ok: false, llm_vision_model: '' }
      if (path === '/api/looks/look-local/versions/2') return { ...saved(), version: 2 }
      throw new Error(`Unexpected GET ${path}`)
    })
    const exportBytes = '{\n  "schema_version": 1, "look": {"key": "look-local", "version": 2}\n}\n'
    const blobs = []
    const fetchMock = vi.fn(async () => ({ ok: true, blob: async () => {
      const blob = new Blob([exportBytes], { type: 'application/json' })
      blobs.push(blob)
      return blob
    } }))
    vi.stubGlobal('fetch', fetchMock)
    const createObjectURL = vi.fn(() => 'blob:portable-look')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
    let downloadedName = ''
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
      downloadedName = this.download
    })
    await render()
    await click(button('Studio study · version 2'))
    await click(button('Export selected version'))

    expect(get).not.toHaveBeenCalledWith('/api/looks/look-local/versions/2/export')
    expect(fetchMock).toHaveBeenCalledWith('/api/looks/look-local/versions/2/export')
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob))
    await expect(blobs[0].text()).resolves.toBe(exportBytes)
    expect(downloadedName).toBe('Studio-study-v2.json')
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:portable-look')
  })

  it('renders and exports an imported SQLite-maximum version without expanding its history', async () => {
    vi.restoreAllMocks()
    vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === '/api/looks') return [{ key: 'look-local', version: SQLITE_MAX, name: 'Maximum look' }]
      if (path === '/api/wardrobe') return wardrobe
      if (path === '/api/config') return { llm_ok: false, llm_vision_model: '' }
      if (path === `/api/looks/look-local/versions/${SQLITE_MAX}`) {
        return { ...saved('Maximum look'), version: SQLITE_MAX }
      }
      throw new Error(`Unexpected GET ${path}`)
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(stage)
    const exportBytes = `{"schema_version":1,"look":{"key":"look-local","version":${SQLITE_MAX}},"garments":[],"provenance":null}\n`
    const fetchMock = vi.fn(async (path) => {
      if (path === `/api/looks/look-local/versions/${SQLITE_MAX}/export`) {
        return { ok: true, blob: async () => new Blob([exportBytes], { type: 'application/json' }) }
      }
      throw new Error(`Unexpected fetch ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const blobs = []
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn((blob) => { blobs.push(blob); return 'blob:maximum-look' }),
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {})

    await render()
    await click(button(`Maximum look · version ${SQLITE_MAX}`))
    expect(field('Version number').value).toBe(SQLITE_MAX)
    expect(field('Version history').options.length).toBeLessThanOrEqual(22)
    await setValue(field('Version number'), '9223372036854775808')
    expect(button('Open version').disabled).toBe(true)
    await setValue(field('Version number'), SQLITE_MAX)
    expect(button('Open version').disabled).toBe(false)
    await click(button('Export selected version'))

    expect(fetchMock).toHaveBeenCalledWith(`/api/looks/look-local/versions/${SQLITE_MAX}/export`)
    await expect(blobs[0].text()).resolves.toBe(exportBytes)
  })

  it('saves at the safe-integer boundary and keeps the exact non-safe successor', async () => {
    vi.restoreAllMocks()
    vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === '/api/looks') return [{ key: 'look-local', version: MAX_SAFE_VERSION, name: 'Large look' }]
      if (path === '/api/wardrobe') return wardrobe
      if (path === '/api/config') return { llm_ok: false, llm_vision_model: '' }
      if (path === `/api/looks/look-local/versions/${MAX_SAFE_VERSION}`) {
        return { ...saved('Large look'), version: MAX_SAFE_VERSION }
      }
      throw new Error(`Unexpected GET ${path}`)
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(stage)
    const calls = []
    vi.stubGlobal('fetch', vi.fn(async (path, init) => {
      calls.push({ path, init })
      if (path === '/api/looks/look-local/versions' && init.method === 'POST') {
        return {
          ok: true,
          status: 200,
          text: async () => `{"key":"look-local","version":${MAX_SAFE_SUCCESSOR},"name":"Large look","content_digest":"${'a'.repeat(64)}","appearance":"updated appearance","outfit":null}`,
        }
      }
      throw new Error(`Unexpected fetch ${path}`)
    }))

    await render()
    await click(button(`Large look · version ${MAX_SAFE_VERSION}`))
    await setValue(field('Constant appearance'), 'updated appearance')
    await click(button('Save look'))

    expect(calls).toHaveLength(1)
    expect(calls[0].init.body).toContain(`"expected_version":${MAX_SAFE_VERSION}`)
    expect(calls[0].init.body).not.toContain(`"expected_version":"${MAX_SAFE_VERSION}"`)
    expect(container.textContent).toContain(`Saved as version ${MAX_SAFE_SUCCESSOR}`)
    expect(field('Version number').value).toBe(MAX_SAFE_SUCCESSOR)
  })

  it('previews and explicitly resolves a JSON conflict while preserving the raw envelope bytes', async () => {
    const calls = []
    const plan = {
      mode: 'choice_required',
      status: 'choice_required',
      choices: [
        { choice: 'new_version', destination: { key: 'private-look-key', version: 4 } },
        { choice: 'save_copy', destination: { key: 'private-copy-key', version: 1 } },
      ],
      remapped: [{ kind: 'garment', from: 'source', to: 'local', reason: 'key_conflict' }],
      preview_token: 'preview-marker',
      review_digest: 'c'.repeat(64),
    }
    const importedRaw = `{"look":{"key":"private-look-key","version":${SQLITE_MAX},"name":"Portable study"},"no_op":false}`
    vi.stubGlobal('fetch', vi.fn(async (path, init) => {
      calls.push({ path, init })
      return { ok: true, text: async () => calls.length === 1 ? JSON.stringify(plan) : importedRaw }
    }))
    await render()
    const raw = '{"schema_version":1,"look":{"key":"portable","version":9007199254740993,"name":"Portable study"},"garments":[],"provenance":null}'
    await chooseFile('Choose look JSON file', new File([raw], 'portable.json', { type: 'application/json' }))

    expect(calls[0].path).toBe('/api/looks/import/preview')
    expect(calls[0].init.body).toBe(raw)
    expect(button('Commit reviewed import').disabled).toBe(true)
    expect(container.textContent).toContain('Choose how to resolve this conflict')
    expect(container.textContent).toContain('1 catalogue item(s) will use local mappings')
    expect(container.textContent).not.toContain('private-look-key')
    expect(container.textContent).not.toContain('preview-marker')

    const newVersion = container.querySelector('input[value="new_version"]')
    await act(async () => { newVersion.click(); await flush() })
    const commit = button('Commit reviewed import')
    await act(async () => { commit.click(); commit.click(); await flush() })
    expect(calls).toHaveLength(2)
    expect(calls[1].path).toBe('/api/looks/import/commit')
    expect(calls[1].init.body).toContain('"version":9007199254740993')
    expect(calls[1].init.body).toContain('"choice":"new_version"')
    expect(container.textContent).toContain(`Imported Portable study as version ${SQLITE_MAX}`)
  })

  it('converts a committed legacy outfit into a named look only after an explicit editor action', async () => {
    const calls = []
    const plan = {
      mode: 'import',
      status: 'ready',
      choices: ['import'],
      resolved_garments: [{ key: 'local-shirt', wording: 'a cotton shirt', aside: '' }],
      resolved_outfits: [{ key: 'local-outfit', label: 'Summer layers', garments: ['local-shirt'] }],
      preview_token: 'legacy-preview',
      review_digest: 'd'.repeat(64),
    }
    const result = {
      garments: [{ key: 'local-shirt', wording: 'a cotton shirt', aside: '' }],
      outfits: [{ key: 'local-outfit', label: 'Summer layers', garments: ['local-shirt'] }],
      no_op: false,
    }
    vi.stubGlobal('fetch', vi.fn(async (path, init) => {
      calls.push({ path, init })
      return { ok: true, text: async () => JSON.stringify(calls.length === 1 ? plan : result) }
    }))
    const post = api.post
    post.mockImplementation(async (path, body) => {
      if (path === '/api/looks') return { ...saved(body.name), outfit: { outfit_key: 'local-outfit', garments: result.garments } }
      throw new Error(`Unexpected POST ${path}`)
    })
    await render()
    const raw = JSON.stringify({
      garments: [{ key: 'shirt', wording: 'a cotton shirt' }],
      outfits: [{ key: 'summer-layers', label: 'Summer layers', garments: ['shirt'] }],
    })
    await chooseFile('Choose look JSON file', new File([raw], 'wardrobe.json', { type: 'application/json' }))
    expect(container.textContent).toContain('does not create a saved look')
    await click(button('Commit reviewed import'))
    expect(post).not.toHaveBeenCalled()
    await click(button('Start a look from this outfit'))
    expect(field('Look name').value).toBe('Summer layers')
    expect(field('Optional outfit').value).toBe('local-outfit')
    expect(post).not.toHaveBeenCalled()
    await click(button('Save look'))
    expect(post).toHaveBeenCalledWith('/api/looks', {
      name: 'Summer layers', appearance: '', outfit_key: 'local-outfit',
    })
  })

  it('keeps manual photo entry available when only text-only vision is configured and retries cleanup', async () => {
    mockBaseReads({ llm_ok: true, llm_vision_model: '' })
    const post = api.post
    const nextStage = { ...stage, photo_id: 'd'.repeat(32) }
    const cancelled = { ...nextStage, state: 'cancelled', cleanup_warning: 'Temporary-file cleanup will retry.' }
    vi.spyOn(api, 'post').mockImplementation(async (path, body) => {
      if (path.endsWith('/cancel')) return cancelled
      if (path.endsWith('/save')) return saved(body.name)
      throw new Error(`Unexpected POST ${path}`)
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValueOnce(stage).mockResolvedValueOnce(nextStage)
    const get = api.get
    get.mockImplementation(async (path) => {
      if (path === '/api/looks') return []
      if (path === '/api/wardrobe') return wardrobe
      if (path === '/api/config') return { llm_ok: true, llm_vision_model: '' }
      if (path === `/api/looks/photo-stages/${stage.photo_id}`) {
        return { ...stage, state: 'saved', saved_look: { key: 'look-local', version: 1 } }
      }
      if (path === `/api/looks/photo-stages/${nextStage.photo_id}`) return { ...cancelled, cleanup_warning: null }
      throw new Error(`Unexpected GET ${path}`)
    })
    await render()
    await chooseFile('Choose one photo', syntheticPhoto())
    expect(api.uploadMultipart).toHaveBeenCalledWith('/api/looks/photo-stages', expect.any(FormData))
    expect(api.uploadMultipart.mock.calls[0][1].get('file').name).toBe('synthetic.png')
    expect(container.querySelector('img')?.getAttribute('src')).toBe(`/api/looks/photo-stages/${stage.photo_id}/preview`)
    expect(button('Extract look').disabled).toBe(true)
    expect(container.textContent).toContain('without a configured text assistant and explicit vision model')
    expect(container.querySelector('a[href="#/setup"]')).toBeTruthy()

    await click(button('Describe this photo manually'))
    await setValue(field('Look name'), 'Manual photo notes')
    await setValue(field('Constant appearance'), 'a neat dark braid')
    await click(button('Save look'))
    expect(post).toHaveBeenCalledWith(`/api/looks/photo-stages/${stage.photo_id}/save`, {
      name: 'Manual photo notes', appearance: 'a neat dark braid', garments: [],
    })

    await chooseFile('Choose one photo', syntheticPhoto())
    await click(button('Cancel photo stage'))
    expect(container.textContent).toContain('Temporary-file cleanup will retry.')
    await click(button('Retry cleanup'))
    expect(get).toHaveBeenCalledWith(`/api/looks/photo-stages/${nextStage.photo_id}`)
    expect(container.textContent).toContain('Temporary photo-file cleanup completed.')
  })

  it('saves only an explicitly reviewed photo proposal with corrections, omissions, and user-confirmed order', async () => {
    mockBaseReads({ llm_ok: true, llm_vision_model: 'declared-vision-model' })
    const proposal = {
      proposal_id: 'c'.repeat(32),
      appearance: 'short dark hair',
      garments: ['a blue jacket', 'small silver earrings'],
      unresolved: [
        { id: 'u1', detail: 'whether the collar is cotton' },
        { id: 'u2', detail: 'the exact shade of the jacket' },
      ],
    }
    const post = api.post
    vi.spyOn(api, 'post').mockImplementation(async (path, body) => {
      if (path.endsWith('/extract')) return proposal
      if (path.endsWith('/save-extracted')) return {
        ...saved(body.name),
        appearance: body.appearance,
        outfit: { outfit_key: 'photo-outfit', garments: body.garments.map((item, index) => ({ key: `garment-${index}`, ...item })) },
      }
      throw new Error(`Unexpected POST ${path}`)
    })
    const get = api.get
    get.mockImplementation(async (path) => {
      if (path === '/api/looks') return []
      if (path === '/api/wardrobe') return wardrobe
      if (path === '/api/config') return { llm_ok: true, llm_vision_model: 'declared-vision-model' }
      if (path === `/api/looks/photo-stages/${stage.photo_id}`) {
        return { ...stage, state: 'saved', saved_look: { key: 'look-local', version: 1 } }
      }
      throw new Error(`Unexpected GET ${path}`)
    })
    await render()
    await chooseFile('Choose one photo', syntheticPhoto())
    expect(post).not.toHaveBeenCalled()
    await click(button('Extract look'))
    expect(post).toHaveBeenCalledWith(`/api/looks/photo-stages/${stage.photo_id}/extract`, {})
    expect(container.textContent).toContain('Review the appearance')
    expect(container.textContent).not.toContain(proposal.proposal_id)
    await setValue(field('Look name'), 'Reviewed photo look')
    await click(button('Move garment 2 up'))
    expect(field('Garment 1 worn wording').value).toBe('small silver earrings')
    await setValue(field('Resolution for uncertain detail 1'), 'correct')
    await setValue(field('Correction for uncertain detail 1'), 'a cotton collar')
    await click(button('Add new garment'))
    await setValue(field('Garment 3 worn wording'), 'a cotton collar')
    await setValue(field('Resolution for uncertain detail 2'), 'omit')
    expect(button('Save look').disabled).toBe(false)
    const checks = Array.from(container.querySelectorAll('input[type="checkbox"]'))
    await act(async () => {
      checks[0].click()
      checks[1].click()
      await flush()
    })
    await click(button('Save look'))
    const call = post.mock.calls.find(([path]) => path.endsWith('/save-extracted'))
    expect(call[1]).toEqual({
      proposal_id: proposal.proposal_id,
      name: 'Reviewed photo look',
      appearance: 'short dark hair',
      garments: [
        { wording: 'small silver earrings', aside: '' },
        { wording: 'a blue jacket', aside: '' },
        { wording: 'a cotton collar', aside: '' },
      ],
      unresolved_decisions: [
        { id: 'u1', action: 'correct', correction: 'a cotton collar' },
        { id: 'u2', action: 'omit' },
      ],
      review_confirmed: true,
      removal_order_confirmed: true,
    })
  })
})
