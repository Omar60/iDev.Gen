// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api.js'
import SessionView from './SessionView.jsx'

const sessionId = 804
const planRevision = 3
const operationPath = `/api/sessions/${sessionId}/plan/authoring/operations`
const operationKey = `idevgen:authoring-operation:${sessionId}:${planRevision}`
const session = {
  id: sessionId,
  name: 'Invented operation workflow session',
  status: 'draft',
  model_id: 4,
  model: { id: 4, name: 'Model A', trigger: 'model trigger', workflow_id: 12 },
  checkpoint: 'base.safetensors',
  shots: [],
  tags: [],
  settings: {
    composition_mode: 'resource-v1',
    width: 1024,
    height: 1024,
    steps: 20,
    cfg: 7,
    lora_strength: 1,
    checkpoint: 'base.safetensors',
  },
}

const takeId = (index) => `take-${String(index).padStart(3, '0')}`

function makePlan(count = 1) {
  return {
    version: 'resource-v1',
    look: '',
    initial_wardrobe: '',
    takes: Array.from({ length: count }, (_, index) => ({
      take_id: takeId(index + 1),
      label: `Invented take ${index + 1}`,
      camera: '50mm eye-level',
      framing: 'medium portrait',
      pose: 'standing by a window',
      expression: 'calm',
    })),
    selected_resources: [],
    wardrobe_changes: [],
    authoring: {
      mode: 'automatic',
      brief: 'Invented short brief.',
      scene_anchor: {
        library_key: 'rooms_task84',
        source_id: 'scene-001',
        content_digest: 'a'.repeat(64),
      },
      workflow_binding: {
        workflow_id: 12,
        kind: 'guide',
        graph_digest: 'b'.repeat(64),
        node_map_digest: 'c'.repeat(64),
      },
      variation_policy: {
        camera: { mode: 'vary' },
        framing: { mode: 'vary' },
        pose: { mode: 'vary' },
        expression: { mode: 'vary' },
      },
      shared_state: {
        look: { origin: 'none', evidence_id: null },
        initial_wardrobe: { origin: 'none', evidence_id: null },
      },
      look_snapshot: null,
      wardrobe_progression: null,
      evidence: [],
    },
  }
}

function makePreparation(plan, readyIds = [], generatedIds = []) {
  const completed = [
    ...readyIds.map((id) => ({ take_id: id, status: 'ready' })),
    ...generatedIds.map((id) => ({ take_id: id, status: 'generated', linked_shot_id: 901 })),
  ]
  const completedIds = new Set(completed.map((item) => item.take_id))
  return {
    completed,
    incomplete: plan.takes
      .filter((take) => !completedIds.has(take.take_id))
      .map((take) => ({ take_id: take.take_id, status: 'missing' })),
    history: [],
  }
}

function operationView({
  operationId = 'operation-task-8-4',
  kind = 'prepare_takes',
  state = 'active',
  requested = [takeId(1)],
  completed = [],
  failed = null,
  remaining = requested,
  canCancel = state === 'active',
  canResume = false,
  error = null,
} = {}) {
  const active = ['active', 'cancel_requested'].includes(state)
  return {
    operation_id: operationId,
    session_id: sessionId,
    plan_revision: planRevision,
    kind,
    state,
    created_at: '2026-10-02T12:00:00Z',
    updated_at: '2026-10-02T12:00:01Z',
    lease_expires_at: active ? '2026-10-02T12:05:00Z' : null,
    progress: { requested, completed, failed, remaining },
    result: null,
    error,
    can_cancel: canCancel,
    can_resume: canResume,
  }
}

function installApi(plan, preparation = makePreparation(plan)) {
  const state = {
    plan,
    preparation,
    revision: planRevision,
    planGets: 0,
    reviewGets: 0,
  }
  vi.spyOn(api, 'get').mockImplementation(async (url) => {
    if (url === `/api/sessions/${sessionId}`) return structuredClone(session)
    if (url === `/api/sessions/${sessionId}/plan`) {
      state.planGets += 1
      return {
        plan_revision: state.revision,
        plan: structuredClone(state.plan),
        shared_summary: null,
        conflicts: [],
        preparation: structuredClone(state.preparation),
        reviewed_revision: null,
      }
    }
    if (url.startsWith(`/api/sessions/${sessionId}/plan/review?plan_revision=`)) {
      state.reviewGets += 1
      return { plan_revision: state.revision, takes: [] }
    }
    if (url === '/api/workflows' || url === '/api/sessions' || url === '/api/comfy/models') return []
    if (url === '/api/config') return { llm_ok: true }
    return []
  })
  return state
}

function buttonMatching(pattern) {
  return Array.from(container.querySelectorAll('button'))
    .find((button) => pattern.test(button.textContent.trim()))
}

async function drain() {
  await act(async () => {
    for (let index = 0; index < 20; index += 1) await Promise.resolve()
  })
}

async function renderSession(initialActiveStep = 'review') {
  await act(async () => {
    root.render(<SessionView id={sessionId} initialActiveStep={initialActiveStep} />)
  })
  await drain()
}

async function click(button) {
  expect(button).toBeTruthy()
  await act(async () => { button.click() })
  await drain()
}

let container = null
let root = null

beforeEach(() => {
  vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => (
    path.endsWith('/full-auto') ? { current_run: null } : api.get(path, { losslessVersions: true })
  ))
  const values = new Map()
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(String(key), String(value)),
      removeItem: (key) => values.delete(String(key)),
      clear: () => values.clear(),
    },
  })
  window.localStorage.clear()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  window.location.hash = ''
})

afterEach(() => {
  if (root) act(() => root.unmount())
  root = null
  container?.remove()
  container = null
  window.localStorage.clear()
  delete window.localStorage
  vi.restoreAllMocks()
})

describe('Task 8.4 authoring operation UI', () => {
  it('adopts a cross-kind active operation from 409 and cancels only on explicit action', async () => {
    const state = installApi(makePlan())
    const suggestion = operationView({
      operationId: 'shared-suggestions-in-other-tab',
      kind: 'shared_suggestions',
      state: 'active',
      requested: ['look', 'initial_wardrobe'],
      completed: ['look'],
      failed: { take_id: 'initial_wardrobe', error: 'The suggestion could not be produced.' },
      remaining: ['initial_wardrobe'],
    })
    const posts = []
    vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
      posts.push([url, structuredClone(body)])
      if (url === operationPath) {
        const error = new Error('Another authoring operation is active.')
        error.status = 409
        error.detail = { code: 'authoring_active', operation: suggestion }
        throw error
      }
      if (url.endsWith('/shared-suggestions-in-other-tab/cancel')) {
        return operationView({
          operationId: 'shared-suggestions-in-other-tab',
          kind: 'shared_suggestions',
          state: 'cancelled',
          requested: ['look', 'initial_wardrobe'],
          completed: ['look'],
          failed: { take_id: 'initial_wardrobe', error: 'The suggestion could not be produced.' },
          remaining: ['initial_wardrobe'],
          canCancel: false,
        })
      }
      throw new Error(`Unexpected POST ${url}`)
    })

    await renderSession()
    await click(buttonMatching(/^Prepare 1 take\(s\)$/))
    const card = container.querySelector('[aria-label="Authoring operation"]')
    expect(card?.textContent).toContain('shared-suggestions-in-other-tab')
    expect(card?.textContent).toContain('shared_suggestions')
    expect(card?.textContent).toContain('Requested: look, initial_wardrobe')
    expect(card?.textContent).toContain('Completed: look')
    expect(card?.textContent).toContain('Failed: initial_wardrobe: The suggestion could not be produced.')
    expect(card?.textContent).toContain('Remaining: initial_wardrobe')
    expect(posts).toHaveLength(1)

    await click(buttonMatching(/^Cancel operation$/))
    expect(posts).toHaveLength(2)
    expect(posts[1]).toEqual([
      `${operationPath}/shared-suggestions-in-other-tab/cancel`,
      { expected_revision: planRevision },
    ])
    expect(card.textContent).toContain('cancelled')
    expect(buttonMatching(/^Reload saved plan$/)).toBeTruthy()
    const planGetsBeforeReload = state.planGets
    await click(buttonMatching(/^Reload saved plan$/))
    expect(state.planGets).toBeGreaterThan(planGetsBeforeReload)
  })

  it('prepares stable ordered batches of twenty and continues from authoritative remaining state', async () => {
    const plan = makePlan(23)
    const alreadyReady = takeId(1)
    const alreadyGenerated = takeId(2)
    const state = installApi(plan, makePreparation(plan, [alreadyReady], [alreadyGenerated]))
    const batches = []
    vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
      if (url !== operationPath) throw new Error(`Unexpected POST ${url}`)
      batches.push(structuredClone(body))
      const completedBefore = state.preparation.completed
      state.preparation = {
        completed: [
          ...completedBefore,
          ...body.take_ids.map((id) => ({ take_id: id, status: 'ready' })),
        ],
        incomplete: plan.takes
          .filter((take) => !new Set([
            ...completedBefore.map((item) => item.take_id),
            ...body.take_ids,
          ]).has(take.take_id))
          .map((take) => ({ take_id: take.take_id, status: 'missing' })),
        history: [],
      }
      const terminal = batches.length === 1
      return operationView({
        operationId: `prepare-batch-${batches.length}`,
        state: terminal ? 'succeeded' : 'active',
        requested: body.take_ids,
        completed: terminal ? body.take_ids : [],
        remaining: terminal ? [] : body.take_ids,
        canCancel: !terminal,
      })
    })

    await renderSession()
    const prepare = buttonMatching(/^Continue preparation \(20\)$/)
    expect(prepare).toBeTruthy()
    await act(async () => {
      prepare.click()
      prepare.click()
      await Promise.resolve()
    })
    await drain()
    expect(batches).toHaveLength(1)
    expect(batches[0]).toEqual({
      request_id: expect.stringMatching(/^[0-9a-f-]{36}$/i),
      expected_revision: planRevision,
      kind: 'prepare_takes',
      take_ids: Array.from({ length: 20 }, (_, index) => takeId(index + 3)),
    })
    expect(state.preparation.completed.map((item) => item.take_id)).toContain(alreadyReady)
    expect(state.preparation.completed.map((item) => item.take_id)).toContain(alreadyGenerated)
    expect(buttonMatching(/^Continue preparation \(1\)$/)).toBeTruthy()

    await click(buttonMatching(/^Continue preparation \(1\)$/))
    expect(batches).toHaveLength(2)
    expect(batches[1].take_ids).toEqual([takeId(23)])
    expect(batches[1].take_ids).not.toContain(alreadyReady)
    expect(batches[1].take_ids).not.toContain(alreadyGenerated)
  })

  it('recovers an unknown start after remount with the same frozen request ID and body', async () => {
    const state = installApi(makePlan(3))
    const posts = []
    vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
      posts.push([url, structuredClone(body)])
      if (posts.length === 1) {
        const error = new Error('The request could not be verified.')
        error.status = 422
        error.detail = { code: 'invalid_operation_request', message: error.message, fields: ['take_ids'] }
        error.hasStableErrorBody = false
        throw error
      }
      const stale = new Error('The saved plan revision changed.')
      stale.status = 409
      stale.detail = { code: 'plan_revision_stale', message: stale.message }
      stale.hasStableErrorBody = true
      throw stale
    })

    await renderSession()
    await click(buttonMatching(/^Prepare 3 take\(s\)$/))
    expect(posts).toHaveLength(1)
    const remembered = JSON.parse(window.localStorage.getItem(operationKey))
    expect(remembered.request).toEqual(posts[0][1])
    await click(buttonMatching(/^2\. Scene \/ Constants$/))
    expect(buttonMatching(/^Generate shared suggestions$/)?.disabled).toBe(true)
    expect(JSON.parse(window.localStorage.getItem(operationKey)).request).toEqual(posts[0][1])

    await act(async () => root.unmount())
    state.revision = planRevision + 1
    root = createRoot(container)
    await renderSession()
    expect(posts).toHaveLength(1)
    expect(buttonMatching(/^Retry preparation$/)).toBeTruthy()
    await click(buttonMatching(/^Retry preparation$/))

    expect(posts).toHaveLength(2)
    expect(posts[1]).toEqual(posts[0])
    expect(container.textContent).toContain('The saved plan revision changed.')
  })

  it('keeps preparation from replacing a shared-suggestion request with an unknown result', async () => {
    installApi(makePlan())
    const posts = []
    vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
      posts.push([url, structuredClone(body)])
      const error = new Error('The request could not be verified.')
      error.status = 422
      error.detail = { code: 'invalid_operation_request', message: error.message, fields: ['kind'] }
      error.hasStableErrorBody = false
      throw error
    })

    await act(async () => {
      root.render(<SessionView id={sessionId} initialActiveStep="constants" />)
    })
    await drain()
    await click(buttonMatching(/^Generate shared suggestions$/))
    expect(posts).toHaveLength(1)
    const remembered = JSON.parse(window.localStorage.getItem(operationKey))
    expect(remembered.request).toEqual(posts[0][1])

    await click(buttonMatching(/^4\. Review$/))
    const prepare = buttonMatching(/^Prepare 1 take\(s\)$/)
    expect(prepare).toBeTruthy()
    expect(prepare.disabled).toBe(true)
    await act(async () => { prepare.click() })
    expect(posts).toHaveLength(1)
    expect(JSON.parse(window.localStorage.getItem(operationKey)).request).toEqual(posts[0][1])
    await click(buttonMatching(/^2\. Scene \/ Constants$/))
    expect(buttonMatching(/^Retry suggestion request$/)).toBeTruthy()
  })

  it.each(['success', 'conflict'])('rejects a stale suggestion %s without replacing its recovery request', async (responseKind) => {
    const state = installApi(makePlan())
    const requests = []
    let finishStart
    vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
      if (url === operationPath) {
        requests.push(structuredClone(body))
        if (requests.length > 1) {
          const error = new Error('The saved plan revision changed.')
          error.status = 409
          error.detail = { code: 'plan_revision_stale', message: error.message }
          error.hasStableErrorBody = true
          throw error
        }
        return new Promise((resolve, reject) => {
          finishStart = () => {
            const view = operationView({
              kind: 'shared_suggestions',
              state: responseKind === 'success' ? 'succeeded' : 'active',
              requested: ['look'],
              completed: responseKind === 'success' ? ['look'] : [],
              remaining: responseKind === 'success' ? [] : ['look'],
            })
            if (responseKind === 'success') {
              resolve({ ...view, result: { items: [{ target: 'look', result: 'Stale proposal.' }] } })
            } else {
              const error = new Error('Another operation is active.')
              error.status = 409
              error.detail = { code: 'authoring_active', operation: view }
              error.hasStableErrorBody = true
              reject(error)
            }
          }
        })
      }
      if (url === `/api/sessions/${sessionId}/plan`) {
        state.plan = structuredClone(body.plan)
        state.plan.authoring.shared_state.look = { origin: 'user', evidence_id: null }
        state.revision += 1
        return { plan_revision: state.revision, conflicts: [] }
      }
      throw new Error(`Unexpected POST ${url}`)
    })
    await renderSession('constants')
    await click(buttonMatching(/^Generate shared suggestions$/))
    await click(buttonMatching(/^Choose no additional look constraint$/))
    const recovery = window.localStorage.getItem(operationKey)
    const writes = api.post.mock.calls.length
    await act(async () => { finishStart() })
    await drain()

    expect(container.querySelector('#shared-proposal-look')).toBeFalsy()
    expect(container.querySelector('[aria-label="Authoring operation"]')).toBeFalsy()
    expect(window.localStorage.getItem(operationKey)).toBe(recovery)
    expect(api.post).toHaveBeenCalledTimes(writes)
    expect(container.textContent).toContain('older plan revision')
    const reads = state.planGets
    await click(buttonMatching(/^Reload saved plan$/))
    expect(state.planGets).toBeGreaterThan(reads)
    expect(api.post).toHaveBeenCalledTimes(writes)
    await click(buttonMatching(/^Retry suggestion request$/))
    expect(requests).toHaveLength(2)
    expect(requests[1]).toEqual(requests[0])
    expect(window.localStorage.getItem(operationKey)).toBeNull()
  })

  it('keeps a suggestion request recoverable when a successful response has the wrong operation kind', async () => {
    installApi(makePlan())
    vi.spyOn(api, 'post').mockResolvedValue(operationView())
    await renderSession('constants')
    await click(buttonMatching(/^Generate shared suggestions$/))
    expect(container.querySelector('[aria-label="Authoring operation"]')).toBeFalsy()
    expect(container.querySelector('#shared-proposal-look')).toBeFalsy()
    expect(buttonMatching(/^Retry suggestion request$/)).toBeTruthy()
    expect(JSON.parse(window.localStorage.getItem(operationKey)).request).toEqual(api.post.mock.calls[0][1])
    expect(api.post).toHaveBeenCalledTimes(1)
  })

  it.each(['revision', 'operation'])('rejects a restored suggestion with the wrong %s', async (mismatch) => {
    const state = installApi(makePlan())
    const operationId = 'remembered-suggestion'
    const recovery = JSON.stringify({ operation_id: operationId })
    window.localStorage.setItem(operationKey, recovery)
    window.localStorage.setItem(`idevgen:authoring-operation:${sessionId}:latest`, String(planRevision))
    if (mismatch === 'revision') state.revision += 1
    const get = api.get.getMockImplementation()
    api.get.mockImplementation(async (url) => url === `${operationPath}/${operationId}` ? {
      ...operationView({ operationId: mismatch === 'operation' ? 'foreign-suggestion' : operationId,
        kind: 'shared_suggestions', state: 'succeeded', requested: ['look'], completed: ['look'], remaining: [] }),
      result: { items: [{ target: 'look', result: 'Stale proposal.' }] },
    } : get(url))
    vi.spyOn(api, 'post')
    await renderSession('constants')

    expect(container.querySelector('#shared-proposal-look')).toBeFalsy()
    expect(container.querySelector('[aria-label="Authoring operation"]')).toBeFalsy()
    expect(window.localStorage.getItem(operationKey)).toBe(recovery)
    expect(buttonMatching(/^Reload saved plan$/)).toBeTruthy()
    expect(api.post).not.toHaveBeenCalled()
  })

  it.each(['revision', 'operation'])('rejects a polled suggestion with the wrong %s', async (mismatch) => {
    installApi(makePlan())
    const operationId = 'polled-suggestion'
    const recovery = JSON.stringify({ operation_id: operationId })
    window.localStorage.setItem(operationKey, recovery)
    const get = api.get.getMockImplementation()
    let reads = 0
    api.get.mockImplementation(async (url) => {
      if (url !== `${operationPath}/${operationId}`) return get(url)
      reads += 1
      if (reads === 1) return operationView({ operationId, kind: 'shared_suggestions', requested: ['look'] })
      return {
        ...operationView({ operationId: mismatch === 'operation' ? 'foreign-suggestion' : operationId,
          kind: 'shared_suggestions', state: 'succeeded', requested: ['look'], completed: ['look'], remaining: [] }),
        plan_revision: mismatch === 'revision' ? planRevision - 1 : planRevision,
        result: { items: [{ target: 'look', result: 'Stale proposal.' }] },
      }
    })
    vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
      expect(url).toBe(`${operationPath}/${operationId}/cancel`)
      expect(body).toEqual({ expected_revision: planRevision })
      return operationView({ operationId, kind: 'shared_suggestions', state: 'cancelled', requested: ['look'] })
    })
    vi.useFakeTimers()
    try {
      await renderSession('constants')
      await act(async () => { await vi.advanceTimersByTimeAsync(900) })
      await drain()
      expect(reads).toBe(2)
      expect(container.querySelector('#shared-proposal-look')).toBeFalsy()
      expect(container.querySelector('[aria-label="Authoring operation"]').textContent).toContain(operationId)
      expect(container.textContent).not.toContain('foreign-suggestion')
      expect(window.localStorage.getItem(operationKey)).toBe(recovery)
      expect(window.localStorage.getItem(`idevgen:authoring-operation:${sessionId}:${planRevision - 1}`)).toBeNull()
      expect(api.post).not.toHaveBeenCalled()
      await click(buttonMatching(/^Cancel operation$/))
      expect(api.post).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('rejects a suggestion from an earlier A-to-B-to-A session epoch', async () => {
    installApi(makePlan())
    const otherId = sessionId + 1
    const get = api.get.getMockImplementation()
    api.get.mockImplementation(async (url) => {
      if (url === `/api/sessions/${otherId}`) return { ...session, id: otherId }
      if (url === `/api/sessions/${otherId}/plan`) return {
        plan_revision: planRevision, plan: makePlan(), conflicts: [], preparation: null, reviewed_revision: null,
      }
      return get(url)
    })
    let finishStart
    vi.spyOn(api, 'post').mockImplementation(() => new Promise((resolve) => { finishStart = resolve }))
    await renderSession('constants')
    await click(buttonMatching(/^Generate shared suggestions$/))
    await act(async () => { root.render(<SessionView id={otherId} initialActiveStep="constants" />) })
    await drain()
    await renderSession('constants')
    const recovery = window.localStorage.getItem(operationKey)
    await act(async () => {
      finishStart({
        ...operationView({ kind: 'shared_suggestions', state: 'succeeded', requested: ['look'], completed: ['look'], remaining: [] }),
        result: { items: [{ target: 'look', result: 'Stale proposal.' }] },
      })
    })
    await drain()
    expect(container.querySelector('#shared-proposal-look')).toBeFalsy()
    expect(container.querySelector('[aria-label="Authoring operation"]')).toBeFalsy()
    expect(window.localStorage.getItem(operationKey)).toBe(recovery)
    expect(api.post).toHaveBeenCalledTimes(1)
  })

  it('restores status through GET and refreshes preparation again after resuming the same operation ID', async () => {
    const plan = makePlan()
    const state = installApi(plan)
    const expired = operationView({
      operationId: 'resume-the-same-operation',
      state: 'expired',
      requested: [takeId(1)],
      completed: [],
      failed: { take_id: takeId(1), error: 'The assistant stopped before completing this take.' },
      remaining: [takeId(1)],
      canResume: true,
      error: 'The operation lease expired.',
    })
    window.localStorage.setItem(operationKey, JSON.stringify({ operation_id: expired.operation_id }))
    const posts = []
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url.endsWith(`/${expired.operation_id}`)) return expired
      if (url === `/api/sessions/${sessionId}`) return structuredClone(session)
      if (url === `/api/sessions/${sessionId}/plan`) {
        state.planGets += 1
        return {
          plan_revision: planRevision,
          plan: structuredClone(state.plan),
          shared_summary: null,
          conflicts: [],
          preparation: structuredClone(state.preparation),
          reviewed_revision: null,
        }
      }
      if (url === `/api/sessions/${sessionId}/plan/review?plan_revision=${planRevision}`) {
        state.reviewGets += 1
        return { plan_revision: planRevision, takes: [] }
      }
      if (url === '/api/workflows' || url === '/api/sessions' || url === '/api/comfy/models') return []
      if (url === '/api/config') return { llm_ok: true }
      return []
    })
    vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
      posts.push([url, structuredClone(body)])
      if (url === `${operationPath}/${expired.operation_id}/resume`) {
        state.preparation = makePreparation(plan, [takeId(1)])
        return operationView({
          operationId: expired.operation_id,
          state: 'succeeded',
          requested: [takeId(1)],
          completed: [takeId(1)],
          remaining: [],
        })
      }
      throw new Error(`Unexpected POST ${url}`)
    })

    await renderSession()
    expect(container.querySelector('[aria-label="Authoring operation"]')?.textContent)
      .toContain('expired')
    expect(buttonMatching(/^Resume operation$/)).toBeTruthy()
    expect(api.get).toHaveBeenCalledWith(
      `${operationPath}/${expired.operation_id}`,
    )
    expect(posts).toHaveLength(0)
    const planGetsBeforeResume = state.planGets
    const reviewGetsBeforeResume = state.reviewGets

    await click(buttonMatching(/^Resume operation$/))
    expect(posts).toEqual([[
      `${operationPath}/${expired.operation_id}/resume`,
      { expected_revision: planRevision },
    ]])
    expect(state.planGets).toBeGreaterThan(planGetsBeforeResume)
    expect(state.reviewGets).toBeGreaterThan(reviewGetsBeforeResume)
    expect(state.preparation.completed.map((item) => item.take_id)).toContain(takeId(1))
    expect(container.querySelector('[aria-label="Authoring operation"]')?.textContent)
      .toContain('succeeded')
  })
})
