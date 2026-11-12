// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRoot } from 'react-dom/client'

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
  let api
  let Looks
  let reviewModule
  let digestCalls
  let digestGates
  let postScenarios
  let postReplies

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
  const prepareTarget = async () => {
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
  const reviewTarget = async () => {
    await render()
    await prepareTarget()
  }
  const makeDeferred = () => {
    let resolve
    let reject
    let settled = false
    const promise = new Promise((yes, no) => {
      resolve = (value) => { settled = true; yes(value) }
      reject = (error) => { settled = true; no(error) }
    })
    return { promise, resolve, reject, get settled() { return settled } }
  }
  const makePostReply = () => {
    const response = makeDeferred()
    let requestBody = null
    const handler = (_path, body) => {
      requestBody = body
      return response.promise
    }
    postReplies.push(() => {
      if (!response.settled) response.resolve(requestBody ? combinedResult(requestBody) : undefined)
    })
    return { handler, resolve: response.resolve, reject: response.reject }
  }
  const mockPosts = (...handlers) => {
    const scenario = {
      calls: [],
      started: handlers.map(() => makeDeferred()),
      finished: handlers.map(() => makeDeferred()),
    }
    let next = 0
    api.postVersioned.mockImplementation((...args) => {
      const index = next++
      scenario.calls.push(args)
      if (!handlers[index]) throw new Error('Unexpected additional combined-save POST.')
      scenario.started[index].resolve(args)
      let response
      try {
        response = handlers[index](...args)
      } catch (error) {
        scenario.finished[index].resolve()
        throw error
      }
      return Promise.resolve(response).then((value) => {
        scenario.finished[index].resolve()
        return value
      }, (error) => {
        scenario.finished[index].resolve()
        throw error
      })
    })
    postScenarios.push(scenario)
    return scenario
  }
  const clickUntilPostStarts = async (label, scenario, index = 0) => {
    const digestIndex = digestCalls.length
    await act(async () => {
      button(label).click()
      await scenario.started[index].promise
    })
    const digest = digestCalls[digestIndex]
    expect(digest).toBeTruthy()
    await digest.started.promise
    await digest.resolved.promise
  }
  const clickRetryUntilPostStarts = async (scenario, index = 0) => {
    await act(async () => {
      button('Retry the identical combined save').click()
      await scenario.started[index].promise
    })
  }
  const holdNextDigest = () => {
    const started = makeDeferred()
    const gate = makeDeferred()
    digestGates.push(gate)
    reviewModule.draftContentDigest.mockImplementationOnce((source) => {
      started.resolve(source)
      return gate.promise
    })
    const resolved = gate
    digestCalls.push({ started, resolved })
    return { started: started.promise, gate }
  }

  beforeEach(async () => {
    vi.restoreAllMocks()
    vi.resetModules()
    api = (await import('../api.js')).api
    reviewModule = await import('../looksAutomationReview.js')
    Looks = (await import('./Looks.jsx')).default
    digestCalls = []
    digestGates = []
    postScenarios = []
    postReplies = []
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    window.location.hash = ''
    const actualDigest = reviewModule.draftContentDigest
    vi.spyOn(reviewModule, 'draftContentDigest').mockImplementation(async (source) => {
      const started = makeDeferred()
      const resolved = makeDeferred()
      const call = { started, resolved }
      digestCalls.push(call)
      started.resolve(source)
      try {
        const value = await actualDigest(source)
        resolved.resolve(value)
        return value
      } catch (error) {
        resolved.reject(error)
        throw error
      }
    })
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

  afterEach(async () => {
    await act(async () => { root?.unmount() })
    root = null
    for (const gate of digestGates) gate.resolve('e'.repeat(64))
    await act(async () => { await Promise.all(digestGates.map((gate) => gate.promise)) })
    for (const release of postReplies) release()
    const startedCalls = postScenarios.flatMap((scenario) => scenario.calls
      .slice(0, scenario.finished.length).map((_, index) => scenario.finished[index].promise))
    await act(async () => { await Promise.all(startedCalls) })
    container?.remove()
    container = null
    vi.restoreAllMocks()
  })

  it('keeps recovery focus after a pending combined save loses its response', async () => {
    const lost = makePostReply()
    const retry = makePostReply()
    const posts = mockPosts(lost.handler, retry.handler)
    await reviewTarget()
    await clickUntilPostStarts('Save look + automation profile', posts)
    expect(posts.calls).toHaveLength(1)
    await act(async () => {
      lost.reject(new TypeError('connection lost'))
      await posts.finished[0].promise
    })
    expect(document.activeElement.getAttribute('aria-label')).toBe('Combined save recovery')
    expect(field('Look name').disabled).toBe(true)
    const originalBody = posts.calls[0][1]
    await clickRetryUntilPostStarts(posts, 1)
    expect(posts.calls).toHaveLength(2)
    expect(posts.calls[1][1]).toBe(originalBody)
    expect(posts.calls[1][1].request_id).toBe(originalBody.request_id)
    await act(async () => {
      retry.resolve(combinedResult(originalBody))
      await posts.finished[1].promise
    })
    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeNull()
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
    const retry = makePostReply()
    const posts = mockPosts(
      async (_path, body) => wrongResponse(combinedResult(body)),
      retry.handler,
    )
    await reviewTarget()

    await clickUntilPostStarts('Save look + automation profile', posts)
    await act(async () => { await posts.finished[0].promise })

    expect(posts.calls).toHaveLength(1)
    const [path, body, integerFields] = posts.calls[0]
    expect(path).toBe('/api/looks/look-g3-target/versions')
    expect(integerFields).toEqual(['expected_version'])
    expect(body.expected_version).toBe('2')
    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeTruthy()
    expect(document.activeElement.getAttribute('aria-label')).toBe('Combined save recovery')
    expect(field('Look name').value).toBe('Target look')
    expect(container.textContent).not.toContain('automation profile revision 1')

    await clickRetryUntilPostStarts(posts, 1)
    expect(posts.calls).toHaveLength(2)
    expect(posts.calls[1][1]).toBe(body)
    expect(posts.calls[1][1].request_id).toBe(body.request_id)
    await act(async () => {
      retry.resolve(combinedResult(body))
      await posts.finished[1].promise
    })
    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeNull()
    expect(container.textContent).toContain('automation profile revision 1')
  })

  it('aborts digest preparation after unmount and permits a later explicit save', async () => {
    const digest = holdNextDigest()
    const post = api.postVersioned
    await reviewTarget()

    await act(async () => {
      button('Save look + automation profile').click()
      await digest.started
    })
    expect(post).not.toHaveBeenCalled()

    await act(async () => { root.unmount() })
    root = createRoot(container)
    await render()
    expect(button('Create look').disabled).toBe(false)
    expect(container.textContent).not.toContain('still awaiting a response')

    await act(async () => {
      digest.gate.resolve('f'.repeat(64))
      await digest.gate.promise
    })
    expect(post).not.toHaveBeenCalled()
    expect(button('Create look').disabled).toBe(false)
    expect(container.textContent).not.toContain('still awaiting a response')

    await prepareTarget()
    const success = makePostReply()
    const posts = mockPosts(success.handler)
    await clickUntilPostStarts('Save look + automation profile', posts)
    expect(posts.calls).toHaveLength(1)
    success.resolve(combinedResult(posts.calls[0][1]))
    await act(async () => { await posts.finished[0].promise })
    expect(container.textContent).toContain('Saved look version 3 and automation profile revision 1')
  })

  it('retains the exact pending request across unmount, response loss, and remount', async () => {
    const lost = makePostReply()
    const retry = makePostReply()
    const posts = mockPosts(lost.handler, retry.handler)
    await reviewTarget()

    await clickUntilPostStarts('Save look + automation profile', posts)
    expect(posts.calls).toHaveLength(1)
    const [path, originalBody, integerFields] = posts.calls[0]
    const originalRequestId = originalBody.request_id
    expect(path).toBe('/api/looks/look-g3-target/versions')
    expect(integerFields).toEqual(['expected_version'])

    await act(async () => { root.unmount() })
    root = createRoot(container)
    await act(async () => {
      lost.reject(new TypeError('connection lost after leaving the editor'))
      await posts.finished[0].promise
    })
    await render()

    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeTruthy()
    await clickRetryUntilPostStarts(posts, 1)
    expect(posts.calls).toHaveLength(2)
    expect(posts.calls[1][0]).toBe(path)
    expect(posts.calls[1][1]).toBe(originalBody)
    expect(posts.calls[1][1].request_id).toBe(originalRequestId)
    expect(posts.calls[1][2]).toEqual(integerFields)

    retry.resolve(combinedResult(originalBody))
    await act(async () => { await posts.finished[1].promise })
    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeNull()
    expect(container.textContent).toContain('Saved look version 3 and automation profile revision 1')
  })

  it('does not duplicate a still-pending write after remount and handles its exact late success offscreen', async () => {
    const original = makePostReply()
    const posts = mockPosts(original.handler)
    await reviewTarget()

    const save = button('Save look + automation profile')
    const digestIndex = digestCalls.length
    await act(async () => {
      save.click()
      save.click()
      await posts.started[0].promise
    })
    await digestCalls[digestIndex].started.promise
    await digestCalls[digestIndex].resolved.promise
    expect(posts.calls).toHaveLength(1)
    const [path, body, integerFields] = posts.calls[0]

    await act(async () => { root.unmount() })
    root = createRoot(container)
    await render()
    expect(container.textContent).toContain('still awaiting a response')
    expect(button('Retry the identical combined save')).toBeFalsy()
    expect(button('Create look').disabled).toBe(true)
    expect(posts.calls).toHaveLength(1)

    original.resolve(combinedResult(body))
    await act(async () => { await posts.finished[0].promise })
    expect(posts.calls).toHaveLength(1)
    expect(container.textContent).toContain('completed after its editor closed')
    expect(container.querySelector('[aria-label="Create look form"]')).toBeNull()
    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeNull()
    expect(path).toBe('/api/looks/look-g3-target/versions')
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/i)
    expect(integerFields).toEqual(['expected_version'])
  })
})
