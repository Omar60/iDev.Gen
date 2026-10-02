// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { api } from '../api.js'
import Resources from './Resources.jsx'

describe('Task 8.3 independent guided creation probes', () => {
  let container
  let root
  let libraries
  let models

  const response = (sessionId, request) => ({
    session_id: sessionId,
    plan_revision: 1,
    plan: {
      version: 'resource-v1',
      takes: Array.from({ length: request.photo_count }, (_, index) => ({
        take_id: `take-${String(index + 1).padStart(3, '0')}`,
      })),
      selected_resources: [request.scene_anchor],
      authoring: {
        schema_version: 1,
        mode: request.mode,
        scene_anchor: request.scene_anchor,
      },
    },
  })
  const room = {
    id: 1,
    library_key: 'guided_rooms',
    display_name: 'Guided Rooms',
    kind: 'rooms',
    revisions: [{
      revision_id: 3,
      source_id: 'room-alpha',
      content_digest: 'a'.repeat(64),
      readiness: { status: 'ready', pending_fields: {} },
    }],
    auxiliary: [],
  }

  const button = (label, scope = container) => Array.from(scope.querySelectorAll('button'))
    .find((item) => typeof label === 'string' ? item.textContent.trim() === label : label.test(item.textContent))

  const setValue = (element, value) => {
    const prototype = element instanceof HTMLTextAreaElement
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
    element.dispatchEvent(new Event('change', { bubbles: true }))
  }

  const render = async (props = {}) => act(async () => { root.render(<Resources {...props} />) })
  const click = async (element) => act(async () => { element.click() })

  const openForm = async () => {
    await render()
    await click(button('Create session'))
    expect(container.querySelector('[aria-label="Guided session setup"]')).toBeTruthy()
  }

  const submit = () => button(/Create guided session|Creating…/)

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    window.location.hash = ''
    libraries = [room]
    models = [{ id: 4, name: 'Character A', workflow_id: 12 }]
    vi.restoreAllMocks()
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return libraries
      if (url === '/api/models') return models
      if (url === '/api/config') return { llm_ok: false }
      if (url === '/api/workflows') return []
      return []
    })
    vi.spyOn(api, 'post').mockImplementation((url) => { throw new Error(`Unexpected POST ${url}`) })
  })

  afterEach(() => {
    act(() => root?.unmount())
    container?.remove()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it.each([201, 200])('navigates from a valid guided response with HTTP %i', async (status) => {
    const post = vi.spyOn(api, 'postWithStatus').mockImplementation(async (_path, request) => ({
      status,
      data: response(status === 201 ? 31 : 32, request),
    }))
    await openForm()
    await click(submit())

    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0][0]).toBe('/api/sessions/guided')
    expect(window.location.hash).toBe(`#/session/${status === 201 ? 31 : 32}`)
    expect(api.post).not.toHaveBeenCalled()
    expect(post.mock.calls.every(([path]) => path === '/api/sessions/guided')).toBe(true)
  })

  it('retries a lost response with the frozen UUID and body after model and room inventory change', async () => {
    const bodies = []
    const post = vi.spyOn(api, 'postWithStatus').mockImplementation((_path, body) => {
      bodies.push(structuredClone(body))
      return bodies.length === 1
        ? Promise.reject(new TypeError('Network disconnected'))
        : Promise.resolve({ status: 200, data: response(41, body) })
    })
    await openForm()
    await click(submit())
    expect(container.textContent).toContain('creation result is unknown')
    expect(window.location.hash).toBe('')

    libraries = []
    models = [{ id: 5, name: 'Character B', workflow_id: 99 }]
    await render({ requestedModelId: '5' })
    const retry = button(/retry|try again/i)
    expect(retry).toBeTruthy()
    await click(retry)

    expect(post).toHaveBeenCalledTimes(2)
    expect(bodies[1]).toEqual(bodies[0])
    expect(bodies[0].request_id).toMatch(/^[0-9a-f-]{36}$/i)
    expect(bodies[0].scene_anchor).toEqual({
      library_key: 'guided_rooms', source_id: 'room-alpha', content_digest: 'a'.repeat(64),
    })
    expect(window.location.hash).toBe('#/session/41')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('treats a server 5xx as unknown and retries the same request', async () => {
    const bodies = []
    const post = vi.spyOn(api, 'postWithStatus').mockImplementation((_path, body) => {
      bodies.push(structuredClone(body))
      return bodies.length === 1
        ? Promise.reject(Object.assign(new Error('Temporary server failure.'), { status: 503 }))
        : Promise.resolve({ status: 200, data: response(42, body) })
    })
    await openForm()
    await click(submit())
    await click(button(/retry|try again/i))

    expect(post).toHaveBeenCalledTimes(2)
    expect(bodies[1]).toEqual(bodies[0])
    expect(window.location.hash).toBe('#/session/42')
  })

  it('does not navigate or unlock edits for a malformed success response', async () => {
    vi.spyOn(api, 'postWithStatus').mockResolvedValue({ status: 201, data: { session_id: 0, plan_revision: 1, plan: {} } })
    await openForm()
    await click(submit())

    expect(window.location.hash).toBe('')
    expect(container.textContent).toContain('unknown')
    expect(container.querySelector('#guided-brief').disabled).toBe(true)
  })

  it('keeps creation outcome unknown when a 2xx response body cannot be decoded', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      statusText: 'Created',
      json: vi.fn().mockRejectedValue(new SyntaxError('invalid JSON')),
    }))
    await openForm()
    await click(submit())

    expect(container.textContent).toContain('creation result is unknown')
    expect(container.querySelector('#guided-brief').disabled).toBe(true)
    expect(button(/retry|try again/i)).toBeTruthy()
    expect(window.location.hash).toBe('')
  })

  it('keeps an unknown request frozen when retry receives an unreadable 422 body', async () => {
    const bodies = []
    const fetchMock = vi.fn((_path, init) => {
      bodies.push(JSON.parse(init.body))
      if (bodies.length === 1) return Promise.reject(new TypeError('Network disconnected'))
      return Promise.resolve({
        ok: false,
        status: 422,
        statusText: 'Unprocessable Entity',
        json: vi.fn().mockRejectedValue(new SyntaxError('invalid JSON')),
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    await openForm()
    await click(submit())
    await click(button(/retry|try again/i))

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls.every(([path]) => path === '/api/sessions/guided')).toBe(true)
    expect(bodies[1]).toEqual(bodies[0])
    expect(container.querySelector('#guided-brief').disabled).toBe(true)
    expect(button(/retry|try again/i)).toBeTruthy()
    expect(window.location.hash).toBe('')
  })

  it('lets the user correct a parsed definitive 422 and submit again', async () => {
    const bodies = []
    const post = vi.spyOn(api, 'postWithStatus').mockImplementation((_path, body) => {
      bodies.push(structuredClone(body))
      if (bodies.length === 1) return Promise.reject(Object.assign(new Error('Choose a ready workflow.'), {
        status: 422,
        detail: { code: 'workflow_required', message: 'Choose a ready workflow.' },
        hasStableErrorBody: true,
      }))
      return Promise.resolve({ status: 201, data: response(52, body) })
    })
    await openForm()
    await click(submit())

    const brief = container.querySelector('#guided-brief')
    expect(brief.disabled).toBe(false)
    await act(async () => setValue(brief, 'A corrected brief.'))
    await click(submit())

    expect(post).toHaveBeenCalledTimes(2)
    expect(bodies[1].brief).toBe('A corrected brief.')
    expect(window.location.hash).toBe('#/session/52')
  })

  it('does not create a new request or navigate on a decoded idempotency conflict', async () => {
    const bodies = []
    const post = vi.spyOn(api, 'postWithStatus').mockImplementation((_path, body) => {
      bodies.push(structuredClone(body))
      if (bodies.length === 1) {
        return Promise.reject(Object.assign(new Error('Conflict detail from server.'), {
          status: 409,
          detail: { code: 'idempotency_conflict', message: 'Conflict detail from server.' },
          hasStableErrorBody: true,
        }))
      }
      return Promise.resolve({ status: 201, data: response(71, body) })
    })
    await openForm()
    await click(submit())

    expect(post).toHaveBeenCalledTimes(1)
    const conflict = container.querySelector('[role="alert"]')
    expect(conflict?.textContent).toMatch(/idempotency|conflict|already bound/i)
    const startNewAttempt = button(/new creation attempt/i, conflict)
    expect(startNewAttempt).toBeTruthy()
    expect(container.querySelector('#guided-brief').disabled).toBe(true)
    expect(window.location.hash).toBe('')

    await click(startNewAttempt)
    expect(post).toHaveBeenCalledTimes(1)
    expect(container.querySelector('#guided-brief').disabled).toBe(false)
    await click(submit())

    expect(post).toHaveBeenCalledTimes(2)
    expect(bodies[1].request_id).not.toBe(bodies[0].request_id)
    const { request_id: _firstId, ...firstBody } = bodies[0]
    const { request_id: _secondId, ...secondBody } = bodies[1]
    expect(secondBody).toEqual(firstBody)
    expect(window.location.hash).toBe('#/session/71')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('coalesces synchronous double clicks into one guided request', async () => {
    let resolveRequest
    const post = vi.spyOn(api, 'postWithStatus').mockImplementation((_path, request) => new Promise((resolve) => {
      resolveRequest = () => resolve({ status: 201, data: response(61, request) })
    }))
    await openForm()
    await act(async () => {
      submit().click()
      submit().click()
      await Promise.resolve()
    })

    expect(post).toHaveBeenCalledTimes(1)
    await act(async () => resolveRequest())
    expect(window.location.hash).toBe('#/session/61')
  })

  it('ignores a valid response that arrives after Resources unmounts', async () => {
    let resolveRequest
    vi.spyOn(api, 'postWithStatus').mockImplementation((_path, request) => new Promise((resolve) => {
      resolveRequest = () => resolve({ status: 201, data: response(62, request) })
    }))
    await openForm()
    await click(submit())
    await act(async () => { root.unmount(); root = null })
    window.location.hash = '#/session/63'
    await act(async () => resolveRequest())

    expect(window.location.hash).toBe('#/session/63')
  })

  it('keeps fused scenes on the advanced editor path', async () => {
    libraries = [{ ...room, library_key: 'guided_fused', kind: 'fused_scenes' }]
    await render()
    const row = Array.from(container.querySelectorAll('tr')).find((item) => item.textContent.includes('room-alpha'))
    expect(button('Use advanced editor', row)).toBeTruthy()
    expect(button('Create session', row)).toBeFalsy()
  })
})
