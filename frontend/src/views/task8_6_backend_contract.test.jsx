// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api.js'
import { normalizeSelectionView, reduceSelectionView } from '../resources.js'
import SessionView from './SessionView.jsx'
import Resources from './Resources.jsx'
import contract from './__fixtures__/task_8_6_backend_contract.json'

const jsonResponse = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status === 409 ? 'Conflict' : status >= 400 ? 'Error' : 'OK',
  json: async () => structuredClone(body),
  text: async () => JSON.stringify(body),
})

const body = (response) => response.body
const response = (value) => jsonResponse(value.status, value.body)
const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

let container
let root
let secondContainer
let secondRoot
let storage

function mount(Component, props = {}, targetRoot = root) {
  act(() => targetRoot.render(React.createElement(Component, props)))
}

async function drain() {
  await act(async () => {
    for (let index = 0; index < 32; index += 1) await Promise.resolve()
  })
}

async function click(button) {
  expect(button).toBeTruthy()
  await act(async () => { button.click() })
  await drain()
}

function clickMatching(pattern, scope = container) {
  return click(Array.from(scope.querySelectorAll('button'))
    .find((button) => pattern.test(button.textContent.trim())))
}

function setValue(element, value) {
  const prototype = element instanceof HTMLTextAreaElement
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype
  act(() => {
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
    element.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

function setSelect(select, value) {
  act(() => {
    select.value = value
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

function sessionFetch(path, init = {}) {
  const method = (init.method || 'GET').toUpperCase()
  if (path === '/api/workflows') return response(contract.resources.workflows)
  if (path === '/api/sessions') return response(contract.sessions)
  if (path === '/api/config') return response(contract.resources.config)
  if (path === '/api/comfy/models') return jsonResponse(200, {})
  if (method === 'GET' && path === `/api/sessions/${body(contract.automaticSession).id}`) {
    return response(contract.automaticSession)
  }
  if (method === 'GET' && path === `/api/sessions/${body(contract.manualSession).id}`) {
    return response(contract.manualSession)
  }
  if (method === 'GET' && path === `/api/sessions/${body(contract.automaticSession).id}/plan`) {
    return response(contract.automaticPlan)
  }
  if (method === 'GET' && path === `/api/sessions/${body(contract.automaticSession).id}/plan/review?plan_revision=1`) {
    return response(contract.automaticReview)
  }
  if (method === 'GET' && path === `/api/sessions/${body(contract.manualSession).id}/plan/review?plan_revision=2`) {
    return response(contract.manualReview)
  }
  return jsonResponse(200, {})
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  secondContainer = document.createElement('div')
  document.body.appendChild(secondContainer)
  secondRoot = createRoot(secondContainer)
  window.location.hash = ''
  const values = new Map()
  storage = {
    getItem: (key) => values.get(String(key)) ?? null,
    setItem: (key, value) => values.set(String(key), String(value)),
    removeItem: (key) => values.delete(String(key)),
    clear: () => values.clear(),
  }
  Object.defineProperty(window, 'localStorage', { configurable: true, value: storage })
  window.localStorage.clear()
})

afterEach(async () => {
  await act(async () => {
    root?.unmount()
    secondRoot?.unmount()
  })
  container?.remove()
  secondContainer?.remove()
  container = null
  secondContainer = null
  root = null
  secondRoot = null
  window.localStorage.clear()
  delete window.localStorage
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Task 8.6 backend response contract integration', () => {
  it('keeps the newest real selection revision when a delayed earlier upload arrives last', async () => {
    const firstUpload = deferred()
    const calls = []
    vi.stubGlobal('fetch', vi.fn(async (url, init = {}) => {
      const path = String(url)
      const method = (init.method || 'GET').toUpperCase()
      calls.push({ path, method })
      if (path === '/api/resources/libraries') return response(contract.resources.libraries)
      if (path === '/api/models') return response(contract.resources.models)
      if (path === '/api/config') return response(contract.resources.config)
      if (path === '/api/workflows') return response(contract.resources.workflows)
      if (method === 'POST' && path === '/api/resources/import-selections') {
        return response(contract.selection.created)
      }
      if (method === 'POST' && path.endsWith('/files')) {
        const fileName = init.body.get('file').name
        if (fileName === 'invented-room.json') return firstUpload.promise
        if (fileName === 'invented-annex.json') return response(contract.selection.uploaded)
      }
      throw new Error(`Unexpected request: ${method} ${path}`)
    }))

    mount(Resources)
    await drain()
    await clickMatching(/^Import Preview$/)
    const input = container.querySelector('#resource-file-input')
    Object.defineProperty(input, 'files', {
      configurable: true,
      value: [
        new File(['{"rooms":[]}'], 'invented-room.json', { type: 'application/json' }),
        new File(['{"rooms":[]}'], 'invented-annex.json', { type: 'application/json' }),
      ],
    })
    await act(async () => { input.dispatchEvent(new Event('change', { bubbles: true })) })
    await drain()
    await clickMatching(/^Upload 2 file\(s\)$/)

    expect(container.textContent).toContain('invented-room.json')
    expect(container.textContent).toContain('invented-annex.json')
    expect(container.textContent).toContain('Revision: 2')

    await act(async () => {
      firstUpload.resolve(response(contract.selection.uploadedFirst))
      await Promise.resolve()
    })
    await drain()

    expect(container.textContent).toContain('Revision: 2')
    expect(container.textContent).toContain('invented-room.json')
    expect(container.textContent).toContain('invented-annex.json')
    expect(calls.filter((call) => call.method === 'POST' && call.path.endsWith('/files'))).toHaveLength(2)
  })

  it('preserves the backend stale-revision error detail and refuses to roll back the newer safe view', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(contract.selection.staleAfterFirst)))
    let failure
    try {
      await api.patch('/api/resources/import-selections/sel_fixture/files/file_fixture', {
        expected_revision: 2,
        effective_library_key: 'task86_synthetic_rooms',
      })
    } catch (error) {
      failure = error
    }

    expect(failure?.status).toBe(409)
    expect(failure?.detail.code).toBe('selection_revision_stale')
    const current = normalizeSelectionView(failure.detail.current)
    const earlier = normalizeSelectionView(body(contract.selection.uploaded))
    expect(current.selection_revision).toBeGreaterThan(earlier.selection_revision)
    const result = reduceSelectionView(current, earlier, {
      activeSelectionId: current.selection_id,
      currentEpoch: 4,
      candidateEpoch: 4,
      currentGen: 10,
      candidateGen: 11,
    })
    expect(result.accepted).toBe(false)
    expect(result.view.selection_revision).toBe(current.selection_revision)
  })

  it('retries a lost guided response with the identical request through api.js', async () => {
    const guidedRequests = []
    vi.stubGlobal('fetch', vi.fn(async (url, init = {}) => {
      const path = String(url)
      const method = (init.method || 'GET').toUpperCase()
      if (path === '/api/resources/libraries') return response(contract.resources.libraries)
      if (path === '/api/models') return response(contract.resources.models)
      if (path === '/api/config') return response(contract.resources.config)
      if (path === '/api/workflows') return response(contract.resources.workflows)
      if (method === 'POST' && path === '/api/sessions/guided') {
        guidedRequests.push(JSON.parse(init.body))
        if (guidedRequests.length === 1) throw new TypeError('Response connection was lost after commit')
        return response(contract.guided.replay)
      }
      throw new Error(`Unexpected request: ${method} ${path}`)
    }))

    mount(Resources)
    await drain()
    await click(container.querySelector('[title="Create a resource-v1 session draft with this exact revision"]'))
    setValue(container.querySelector('#guided-brief'), contract.guided.request.brief)
    setValue(container.querySelector('#guided-look'), contract.guided.request.look)
    setValue(container.querySelector('#guided-initial-wardrobe'), contract.guided.request.initial_wardrobe)
    setValue(container.querySelector('#guided-photo-count'), '2')
    await clickMatching(/^Create guided session$/)

    expect(container.textContent).toContain('Retry creation')
    await clickMatching(/^Retry creation$/)

    expect(guidedRequests).toHaveLength(2)
    expect(guidedRequests[1]).toEqual(guidedRequests[0])
    const { request_id: _requestId, ...capturedRequest } = contract.guided.request
    expect(guidedRequests[0]).toMatchObject({
      ...capturedRequest,
      character_id: body(contract.resources.models)[0].id,
    })
    expect(window.location.hash).toBe(`#/session/${body(contract.guided.replay).session_id}`)
  })

  it('renders the captured 422 workflow error, shows both remedies, and creates no orphan session', async () => {
    const requests = []
    vi.stubGlobal('fetch', vi.fn(async (url, init = {}) => {
      const path = String(url)
      const method = (init.method || 'GET').toUpperCase()
      if (path === '/api/resources/libraries') return response(contract.resources.libraries)
      if (path === '/api/models') return response(contract.resources.models)
      if (path === '/api/config') return response(contract.resources.config)
      if (path === '/api/workflows') return response(contract.resources.workflows)
      requests.push({ path, method, body: init.body ? JSON.parse(init.body) : null })
      if (method === 'POST' && path === '/api/sessions/guided') {
        return response(contract.guided.workflowError)
      }
      throw new Error(`Unexpected request: ${method} ${path}`)
    }))
    mount(Resources)
    await drain()
    await click(container.querySelector('[title="Create a resource-v1 session draft with this exact revision"]'))
    setValue(container.querySelector('#guided-brief'), contract.guided.workflowErrorRequest.brief)
    setValue(container.querySelector('#guided-look'), contract.guided.workflowErrorRequest.look)
    setValue(container.querySelector('#guided-initial-wardrobe'), contract.guided.workflowErrorRequest.initial_wardrobe)
    setValue(container.querySelector('#guided-photo-count'), '2')
    await clickMatching(/^Create guided session$/)

    const guidedPosts = requests.filter(({ method, path }) => method === 'POST' && path === '/api/sessions/guided')
    expect(guidedPosts).toHaveLength(1)
    expect(guidedPosts[0].body).toMatchObject({
      ...contract.guided.workflowErrorRequest,
      request_id: expect.any(String),
      character_id: body(contract.resources.models)[0].id,
    })
    expect(container.querySelector('.error')?.textContent).toContain('workflow_required')
    expect(container.querySelector('.error')?.textContent).toContain('assign a workflow')
    expect(container.querySelector('.error')?.textContent).toContain('Advanced workflow override')
    expect(window.location.hash).toBe('')
    expect(contract.guided.workflowErrorNoOrphan.before_session_ids)
      .toEqual(contract.guided.workflowErrorNoOrphan.after_session_ids)

    setSelect(container.querySelector('#guided-character'), String(body(contract.resources.models)[1].id))
    await drain()
    expect(container.textContent).toContain('This character has no default workflow.')
    const characterDefault = container.querySelector('a[href="#/model/2"]')
    expect(characterDefault).toBeTruthy()
    const overrideRemedy = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('choose an Advanced workflow override'))
    await click(overrideRemedy)
    expect(container.querySelector('details')?.open).toBe(true)
    const override = container.querySelector('#guided-workflow-override')
    expect(override.querySelector('option[value="1"]')?.textContent).toBe('Synthetic task 8.6 workflow')
    expect(window.location.hash).toBe('')
    expect(requests.filter(({ method, path }) => method === 'POST' && path === '/api/sessions/guided'))
      .toHaveLength(1)
    expect(requests.some(({ path }) => path.includes('/review/approve')
      || path.includes('/preparations/submit-selected') || path.endsWith('/run'))).toBe(false)
  })

  it('renders the authoritative projection and exposes the real manual prepared shape without implicit finalization', async () => {
    const sessionId = body(contract.manualSession).id
    const calls = []
    let planReads = 0
    vi.stubGlobal('fetch', vi.fn(async (url, init = {}) => {
      const path = String(url)
      const method = (init.method || 'GET').toUpperCase()
      calls.push({ path, method, body: init.body ? JSON.parse(init.body) : null })
      if (path === '/api/workflows') return response(contract.resources.workflows)
      if (path === '/api/sessions') return response(contract.sessions)
      if (path === '/api/config') return response(contract.resources.config)
      if (path === '/api/comfy/models') return jsonResponse(200, {})
      if (path === `/api/sessions/${sessionId}`) return response(contract.manualSession)
      if (path === `/api/sessions/${sessionId}/plan`) {
        planReads += 1
        return response(planReads === 1 ? contract.manualPlanBeforePreparation : contract.manualPlan)
      }
      if (path === `/api/sessions/${sessionId}/plan/review?plan_revision=2`) return response(contract.manualReview)
      if (method === 'POST' && path === `/api/sessions/${sessionId}/plan/preparations/prepare`) {
        return response(contract.manualPreparation.response)
      }
      throw new Error(`Unexpected request: ${method} ${path}`)
    }))

    mount(SessionView, { id: sessionId, initialActiveStep: 'review' })
    await drain()
    expect(container.textContent).toContain('A clean invented studio with diffuse daylight.')
    expect(container.textContent).toContain('A linen shirt and dark trousers.')
    expect(container.textContent).toContain('Shared Session Summary')
    const prepareButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Prepare Incomplete Takes'))
    expect(prepareButton?.disabled).toBe(false)
    await click(prepareButton)

    expect(calls.find((call) => call.path.endsWith('/preparations/prepare'))?.body).toEqual(
      contract.manualPreparation.request,
    )
    expect(body(contract.manualPreparation.response)).toHaveProperty('prepared')
    expect(body(contract.manualPreparation.response)).not.toHaveProperty('completed_count')
    expect(body(contract.manualPreparation.response).prepared).toHaveLength(2)
    expect(container.textContent).toContain('Batch preparation complete: 2 prepared')
    expect(calls.some(({ method, path }) => method === 'POST' && (
      path.includes('/review/approve')
      || path.includes('/preparations/submit-selected')
      || path.endsWith('/run')
    ))).toBe(false)
  })

  it('restores a captured partial worker result with completed, failed, and remaining takes', async () => {
    const partial = contract.partialAuthoringOperation
    const operation = body(partial.progress)
    const sessionId = body(partial.session).id
    storage.setItem(
      `idevgen:authoring-operation:${sessionId}:1`,
      JSON.stringify({ operation_id: operation.operation_id }),
    )
    storage.setItem(`idevgen:authoring-operation:${sessionId}:latest`, '1')
    const calls = []
    vi.stubGlobal('fetch', vi.fn(async (url, init = {}) => {
      const path = String(url)
      const method = (init.method || 'GET').toUpperCase()
      calls.push({ path, method })
      if (path === '/api/workflows') return response(contract.resources.workflows)
      if (path === '/api/sessions') return response(contract.sessions)
      if (path === '/api/config') return response(contract.resources.config)
      if (path === '/api/comfy/models') return jsonResponse(200, {})
      if (path === `/api/sessions/${sessionId}`) return response(partial.session)
      if (path === `/api/sessions/${sessionId}/plan`) return response(partial.planAfterFailure)
      if (path === `/api/sessions/${sessionId}/plan/review?plan_revision=1`) {
        return response(partial.reviewAfterFailure)
      }
      if (path.endsWith(`/plan/authoring/operations/${operation.operation_id}`)) {
        return response(partial.progress)
      }
      throw new Error(`Unexpected request: ${method} ${path}`)
    }))

    mount(SessionView, { id: sessionId, initialActiveStep: 'review' })
    await drain()

    expect(container.textContent).toContain('Authoring operation')
    expect(container.textContent).toContain('failed')
    expect(container.textContent).toContain('Requested: take-001, take-002, take-003')
    expect(container.textContent).toContain('Completed: take-001')
    expect(container.textContent).toContain('Failed: take-002: The authoring assistant could not complete this item.')
    expect(container.textContent).toContain('Remaining: take-003')
    expect(Array.from(container.querySelectorAll('button')).some(
      (button) => button.textContent.trim() === 'Resume operation',
    )).toBe(true)
    expect(calls.some(({ method, path }) => method === 'GET'
      && path.endsWith(`/plan/authoring/operations/${operation.operation_id}`))).toBe(true)
    expect(calls.some(({ method, path }) => method === 'POST' && (
      path.includes('/review/approve')
      || path.includes('/preparations/submit-selected')
      || path.endsWith('/run')
    ))).toBe(false)
  })

  it('shows the backend-owned active progress after the second tab receives the real 409 envelope', async () => {
    const sessionId = body(contract.automaticSession).id
    let startCount = 0
    const starts = []
    const fetcher = vi.fn(async (url, init = {}) => {
      const path = String(url)
      const method = (init.method || 'GET').toUpperCase()
      if (path === '/api/workflows') return response(contract.resources.workflows)
      if (path === '/api/sessions') return response(contract.sessions)
      if (path === '/api/config') return response(contract.resources.config)
      if (path === '/api/comfy/models') return jsonResponse(200, {})
      if (path === `/api/sessions/${sessionId}`) return response(contract.automaticSession)
      if (path === `/api/sessions/${sessionId}/plan`) return response(contract.automaticPlan)
      if (path === `/api/sessions/${sessionId}/plan/review?plan_revision=1`) return response(contract.automaticReview)
      if (method === 'POST' && path === `/api/sessions/${sessionId}/plan/authoring/operations`) {
        starts.push(JSON.parse(init.body))
        startCount += 1
        return startCount === 1
          ? response(contract.authoringOperation.started)
          : response(contract.authoringOperation.conflict)
      }
      if (method === 'GET' && path.endsWith(`/${body(contract.authoringOperation.progress).operation_id}`)) {
        return response(contract.authoringOperation.progress)
      }
      throw new Error(`Unexpected request: ${method} ${path}`)
    })
    vi.stubGlobal('fetch', fetcher)

    mount(SessionView, { id: sessionId, initialActiveStep: 'review' }, root)
    mount(SessionView, { id: sessionId, initialActiveStep: 'review' }, secondRoot)
    await drain()
    const findPrepare = (scope) => Array.from(scope.querySelectorAll('button'))
      .find((button) => /^Prepare \d+ take\(s\)$/.test(button.textContent.trim()))
    expect(findPrepare(container)?.disabled).toBe(false)
    expect(findPrepare(secondContainer)?.disabled).toBe(false)

    await click(findPrepare(container))
    await click(findPrepare(secondContainer), secondContainer)

    expect(starts).toHaveLength(2)
    expect(starts[0]).toMatchObject({ expected_revision: 1, kind: 'prepare_takes', take_ids: ['take-001', 'take-002'] })
    expect(starts[1]).toMatchObject({ expected_revision: 1, kind: 'prepare_takes', take_ids: ['take-001', 'take-002'] })
    expect(container.textContent).toContain('take-001')
    expect(secondContainer.textContent).toContain('Another authoring operation is active')
    expect(secondContainer.textContent).toContain('take-002')
    expect(fetcher.mock.calls.some(([url, init = {}]) => String(url).includes('/review/approve')
      || String(url).includes('/preparations/submit-selected')
      || String(url).endsWith('/run'))).toBe(false)
  })

  it('ignores a late response for a previously viewed session', async () => {
    const firstSessionId = body(contract.automaticSession).id
    const secondSessionId = body(contract.manualSession).id
    const lateSession = deferred()
    vi.stubGlobal('fetch', vi.fn(async (url, init = {}) => {
      const path = String(url)
      const method = (init.method || 'GET').toUpperCase()
      if (path === '/api/workflows') return response(contract.resources.workflows)
      if (path === '/api/sessions') return response(contract.sessions)
      if (path === '/api/config') return response(contract.resources.config)
      if (path === '/api/comfy/models') return jsonResponse(200, {})
      if (method === 'GET' && path === `/api/sessions/${firstSessionId}`) return lateSession.promise
      if (method === 'GET' && path === `/api/sessions/${secondSessionId}`) return response(contract.manualSession)
      if (path === `/api/sessions/${secondSessionId}/plan`) return response(contract.manualPlanBeforePreparation)
      if (path === `/api/sessions/${secondSessionId}/plan/review?plan_revision=2`) return response(contract.manualReview)
      return jsonResponse(200, {})
    }))

    mount(SessionView, { id: firstSessionId, initialActiveStep: 'review' })
    await drain()
    mount(SessionView, { id: secondSessionId, initialActiveStep: 'review' })
    await drain()
    await act(async () => {
      lateSession.resolve(response(contract.automaticSession))
      await Promise.resolve()
    })
    await drain()

    expect(container.textContent).toContain('A clean invented studio with diffuse daylight.')
    expect(container.textContent).not.toContain('Soft daylight in an invented studio.')
  })
})
