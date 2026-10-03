// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api.js'
import SessionView from './views/SessionView.jsx'

const sessionId = 804
const revision = 3
const operationUrl = `/api/sessions/${sessionId}/plan/authoring/operations`
const operationUrlFor = (operationId) => `${operationUrl}/${operationId}`
const takeId = (index) => `take-${String(index).padStart(3, '0')}`

const session = {
  id: sessionId,
  name: 'Invented operation recovery session',
  status: 'draft',
  model_id: 4,
  model: { id: 4, name: 'Model A', trigger: 'model trigger', workflow_id: 12 },
  checkpoint: 'base.safetensors',
  shots: [],
  tags: [],
  settings: { composition_mode: 'resource-v1', width: 1024, height: 1024, steps: 20, cfg: 7 },
}

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
      scene_anchor: { library_key: 'rooms_task84', source_id: 'scene-001', content_digest: 'a'.repeat(64) },
      workflow_binding: { workflow_id: 12, kind: 'guide', graph_digest: 'b'.repeat(64), node_map_digest: 'c'.repeat(64) },
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

function operationView(kind, operationId = 'recovered-operation') {
  const requested = kind === 'prepare_takes' ? [takeId(1)] : ['look']
  return {
    operation_id: operationId,
    session_id: sessionId,
    plan_revision: revision,
    kind,
    state: 'active',
    created_at: '2026-10-02T12:00:00Z',
    updated_at: '2026-10-02T12:00:01Z',
    lease_expires_at: '2026-10-02T12:10:00Z',
    progress: { requested, completed: [], failed: null, remaining: requested },
    result: null,
    error: null,
    can_cancel: true,
    can_resume: false,
  }
}

function installReadApi(plan) {
  const read = async (url) => {
    if (url === `/api/sessions/${sessionId}`) return structuredClone(session)
    if (url === `/api/sessions/${sessionId}/plan`) {
      return {
        plan_revision: revision,
        plan: structuredClone(plan),
        shared_summary: null,
        conflicts: [],
        preparation: { completed: [], incomplete: plan.takes.map(({ take_id }) => ({ take_id, status: 'missing' })), history: [] },
        reviewed_revision: null,
      }
    }
    if (url === `/api/sessions/${sessionId}/plan/review?plan_revision=${revision}`) {
      return { plan_revision: revision, takes: [] }
    }
    if (url === '/api/workflows' || url === '/api/sessions' || url === '/api/comfy/models') return []
    if (url === '/api/config') return { llm_ok: true }
    return []
  }
  vi.spyOn(api, 'get').mockImplementation(read)
  return read
}

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status >= 500 ? 'Internal Server Error' : 'Unprocessable Entity',
    json: async () => structuredClone(body),
  }
}

let container
let root
let storage

async function drain() {
  await act(async () => {
    for (let index = 0; index < 25; index += 1) await Promise.resolve()
  })
}

async function renderSession(step) {
  await act(async () => root.render(<SessionView id={sessionId} initialActiveStep={step} />))
  await drain()
}

function findButton(pattern) {
  return Array.from(container.querySelectorAll('button'))
    .find((button) => pattern.test(button.textContent.trim()))
}

async function click(button) {
  expect(button, 'expected action button to be present').toBeTruthy()
  await act(async () => { button.click() })
  await drain()
}

beforeEach(() => {
  storage = new Map()
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key) => storage.get(String(key)) ?? null,
      setItem: (key, value) => storage.set(String(key), String(value)),
      removeItem: (key) => storage.delete(String(key)),
      clear: () => storage.clear(),
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
  delete window.localStorage
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Task 8.4 independent transport and identity probes', () => {
  it.each([
    ['preparation', 'prepare_takes', 'review', /^Prepare 1 take\(s\)$/, /^Retry preparation$/],
    ['shared suggestions', 'shared_suggestions', 'constants', /^Generate shared suggestions$/, /^Retry suggestion request$/],
  ])('%s keeps the frozen request after a 500 with a valid error body', async (_label, kind, step, initialButton, retryButton) => {
    const plan = makePlan()
    installReadApi(plan)
    const posts = []
    const originalFetch = vi.fn(async (url, init) => {
      expect(url).toBe(operationUrl)
      posts.push(JSON.parse(init.body))
      if (posts.length === 1) {
        return response(500, { detail: { code: 'operation_start_uncertain', message: 'The operation may have been saved before the service failed.' } })
      }
      return response(202, operationView(kind))
    })
    vi.stubGlobal('fetch', originalFetch)

    await renderSession(step)
    await click(findButton(initialButton))

    expect(findButton(retryButton)).toBeTruthy()
    expect(posts).toHaveLength(1)

    await click(findButton(retryButton))
    expect(posts).toHaveLength(2)
    expect(posts[1]).toEqual(posts[0])
    expect(posts[0]).toMatchObject({ expected_revision: revision, kind })
    expect(posts[0].request_id).toMatch(/^[0-9a-f-]{36}$/i)
    expect(container.querySelector('[aria-label="Authoring operation"]')?.textContent)
      .toContain('recovered-operation')
  })

  it('keeps a malformed 422 start response retryable with the same preparation identity', async () => {
    installReadApi(makePlan(2))
    const posts = []
    const originalFetch = vi.fn(async (_url, init) => {
      posts.push(JSON.parse(init.body))
      if (posts.length === 1) return response(422, { detail: { code: 'invalid_operation_request' } })
      return response(202, operationView('prepare_takes'))
    })
    vi.stubGlobal('fetch', originalFetch)

    await renderSession('review')
    await click(findButton(/^Prepare 2 take\(s\)$/))
    expect(findButton(/^Retry preparation$/)).toBeTruthy()
    await click(findButton(/^Retry preparation$/))

    expect(posts).toHaveLength(2)
    expect(posts[1]).toEqual(posts[0])
  })

  it('uses GET only for operation polling and ignores a response arriving after unmount', async () => {
    const plan = makePlan()
    const read = installReadApi(plan)
    let resolvePoll
    let pollGets = 0
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === operationUrlFor('poll-only-operation')) {
        pollGets += 1
        return new Promise((resolve) => { resolvePoll = resolve })
      }
      return read(url)
    })
    const posts = []
    vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
      posts.push([url, structuredClone(body)])
      if (url === operationUrl) return operationView('prepare_takes', 'poll-only-operation')
      throw new Error(`Unexpected operation mutation: ${url}`)
    })

    await renderSession('review')
    vi.useFakeTimers()
    await click(findButton(/^Prepare 1 take\(s\)$/))
    expect(posts).toHaveLength(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(900) })
    expect(pollGets).toBe(1)
    expect(posts).toHaveLength(1)

    await act(async () => root.unmount())
    root = null
    await act(async () => {
      resolvePoll(operationView('prepare_takes', 'poll-only-operation'))
      await Promise.resolve()
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })

    expect(pollGets).toBe(1)
    expect(posts).toHaveLength(1)
  })
})
