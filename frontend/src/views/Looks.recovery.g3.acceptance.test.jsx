// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRoot } from 'react-dom/client'
import { api } from '../api.js'
import Looks from './Looks.jsx'

const lookSummary = {
  key: 'look-g3-target',
  version: 2,
  name: 'Target look',
  content_digest: 'a'.repeat(64),
}

const lookVersion = {
  ...lookSummary,
  appearance: 'Short dark hair ✨.',
  outfit: null,
}

function combinedResult(body, overrides = {}) {
  const digest = 'c'.repeat(64)
  return {
    key: 'look-g3-target',
    version: '3',
    name: body.name,
    content_digest: digest,
    appearance: body.appearance,
    outfit: null,
    automation_profile: {
      look_key: 'look-g3-target',
      version: '3',
      content_digest: digest,
      profile_revision: '1',
      profile_digest: 'd'.repeat(64),
      readiness: { profile_ready: true, manual_available: true, diagnostics: [] },
    },
    ...overrides,
  }
}

describe('Looks combined-save acceptance fences', () => {
  let container
  let root

  const flush = () => new Promise((resolve) => setTimeout(resolve, 0))
  const render = async () => act(async () => { root.render(<Looks />); await flush() })
  const click = async (element) => {
    expect(element).toBeTruthy()
    await act(async () => { element.click(); await flush() })
  }
  const button = (label) => [...container.querySelectorAll('button')]
    .find((item) => item.textContent.trim() === label || item.getAttribute('aria-label') === label)
  const field = (label) => container.querySelector(`[aria-label="${label}"]`)
  const setValue = async (element, value) => {
    expect(element).toBeTruthy()
    const prototype = element instanceof HTMLSelectElement
      ? window.HTMLSelectElement.prototype
      : element instanceof HTMLTextAreaElement
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype
    await act(async () => {
      Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value)
      element.dispatchEvent(new Event('input', { bubbles: true }))
      element.dispatchEvent(new Event('change', { bubbles: true }))
      await flush()
    })
  }
  const checkLabel = (text) => [...container.querySelectorAll('label')]
    .find((label) => label.textContent.includes(text))?.querySelector('input[type="checkbox"]')
  const accept = async (checkbox) => {
    expect(checkbox).toBeTruthy()
    await act(async () => { checkbox.click(); await flush() })
  }
  const reviewTarget = async () => {
    await render()
    await click(button('Target look · version 2'))
    expect(field('Look name')?.value).toBe('Target look')
    await click(button('All appearance is permanent'))
    await click(button('Confirm this user assertion'))
    for (const region of ['chest', 'hips', 'legs', 'feet']) {
      await setValue(field(`Baseline · every listed garment absent ${region}`), 'none')
    }
    await setValue(field('Inventory confirmation'), 'true')
    await accept(checkLabel('I confirm the complete garment list and removal order shown above.'))
    await accept(checkLabel('I reviewed ownership, every coverage state, the baseline and inventory for this exact look draft.'))
    expect(button('Save look + automation profile').disabled).toBe(false)
  }
  const makeDeferred = () => {
    let resolve
    let reject
    const promise = new Promise((yes, no) => { resolve = yes; reject = no })
    return { promise, resolve, reject }
  }

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    window.location.hash = ''
    vi.restoreAllMocks()
    vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => {
      if (path === '/api/looks') return [lookSummary]
      if (path === '/api/looks/look-g3-target/versions/2') return lookVersion
      return []
    })
    vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === '/api/wardrobe') return { garments: [], outfits: [] }
      if (path === '/api/config') return { llm_ok: false, llm_vision_model: '' }
      if (path === '/api/comfy/status') return { online: false, output_dir_ok: true }
      if (path === '/api/components?all=1') return []
      throw new Error(`Unexpected GET ${path}`)
    })
    vi.spyOn(api, 'postVersioned').mockRejectedValue(new Error('Unexpected POST'))
  })

  afterEach(() => {
    act(() => root?.unmount())
    container?.remove()
    container = null
    vi.restoreAllMocks()
  })

  it.each([
    ['different look key', (result) => {
      const wrongKey = { ...result, key: 'look-another-target' }
      wrongKey.automation_profile = { ...result.automation_profile, look_key: wrongKey.key }
      return wrongKey
    }],
    ['non-successor version', (result) => {
      const wrongVersion = { ...result, version: '4' }
      wrongVersion.automation_profile = { ...result.automation_profile, version: '4' }
      return wrongVersion
    }],
  ])('keeps an internally consistent but wrong %s response recoverable', async (_label, wrongResponse) => {
    let resolveRetry
    const retry = makeDeferred()
    resolveRetry = retry.resolve
    const post = api.postVersioned
    post.mockImplementationOnce(async (_path, body) => wrongResponse(combinedResult(body)))
      .mockReturnValueOnce(retry.promise)
    await reviewTarget()

    await click(button('Save look + automation profile'))

    expect(post).toHaveBeenCalledTimes(1)
    const [path, body, integerFields] = post.mock.calls[0]
    expect(path).toBe('/api/looks/look-g3-target/versions')
    expect(integerFields).toEqual(['expected_version'])
    expect(body.expected_version).toBe('2')
    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeTruthy()
    expect(field('Look name').value).toBe('Target look')
    expect(container.textContent).not.toContain('automation profile revision 1')

    await click(button('Retry the identical combined save'))
    expect(post).toHaveBeenCalledTimes(2)
    expect(post.mock.calls[1][1]).toBe(body)
    expect(post.mock.calls[1][1].request_id).toBe(body.request_id)
    resolveRetry(combinedResult(body))
    await act(async () => { await flush() })
    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeNull()
    expect(container.textContent).toContain('automation profile revision 1')
  })

  it('retains the exact pending request across unmount, response loss, and remount', async () => {
    const lost = makeDeferred()
    const retry = makeDeferred()
    const post = api.postVersioned
    post.mockReturnValueOnce(lost.promise).mockReturnValueOnce(retry.promise)
    await reviewTarget()

    await click(button('Save look + automation profile'))
    expect(post).toHaveBeenCalledTimes(1)
    const [path, originalBody, integerFields] = post.mock.calls[0]
    const originalRequestId = originalBody.request_id
    expect(path).toBe('/api/looks/look-g3-target/versions')
    expect(integerFields).toEqual(['expected_version'])

    await act(async () => { root.unmount() })
    root = createRoot(container)
    lost.reject(new TypeError('connection lost after leaving the editor'))
    await act(async () => { await flush() })
    await render()

    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeTruthy()
    await click(button('Retry the identical combined save'))
    expect(post).toHaveBeenCalledTimes(2)
    expect(post.mock.calls[1][0]).toBe(path)
    expect(post.mock.calls[1][1]).toBe(originalBody)
    expect(post.mock.calls[1][1].request_id).toBe(originalRequestId)
    expect(post.mock.calls[1][2]).toEqual(integerFields)

    retry.resolve(combinedResult(originalBody))
    await act(async () => { await flush() })
    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeNull()
    expect(container.textContent).toContain('Saved look version 3 and automation profile revision 1')
  })

  it('does not duplicate a still-pending write after remount and handles its exact late success offscreen', async () => {
    const original = makeDeferred()
    const post = api.postVersioned
    post.mockReturnValueOnce(original.promise)
    await reviewTarget()

    const save = button('Save look + automation profile')
    await act(async () => { save.click(); save.click(); await flush() })
    expect(post).toHaveBeenCalledTimes(1)
    const [path, body, integerFields] = post.mock.calls[0]

    await act(async () => { root.unmount() })
    root = createRoot(container)
    await render()
    expect(container.textContent).toContain('still awaiting a response')
    expect(button('Retry the identical combined save')).toBeFalsy()
    expect(button('Create look').disabled).toBe(true)
    expect(post).toHaveBeenCalledTimes(1)

    original.resolve(combinedResult(body))
    await act(async () => { await flush() })
    expect(post).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('completed after its editor closed')
    expect(container.querySelector('[aria-label="Create look form"]')).toBeNull()
    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeNull()
    expect(path).toBe('/api/looks/look-g3-target/versions')
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/i)
    expect(integerFields).toEqual(['expected_version'])
  })
})
