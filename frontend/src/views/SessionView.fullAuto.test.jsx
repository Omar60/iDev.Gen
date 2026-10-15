// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { api } from '../api.js'
import SessionView from './SessionView.jsx'

const session = {
  id: 963,
  name: 'Invented Full Auto session',
  status: 'failed',
  model_id: 4,
  model: { id: 4, name: 'Model A' },
  workflow_id: 12,
  settings: { composition_mode: 'resource-v1' },
  shots: [
    { id: 700, status: 'done' },
    { id: 701, status: 'failed', error: 'Invented render failure' },
    { id: 702, status: 'cancelled' },
  ],
  tags: [],
}

const plan = {
  version: 'resource-v1',
  look: 'Invented look',
  initial_wardrobe: 'Invented wardrobe',
  selected_resources: [],
  wardrobe_changes: [],
  takes: [{ take_id: 'take-001', label: 'First take' }],
}

const makeRun = (overrides = {}) => ({
  run_id: 'run-invented-963',
  session_id: session.id,
  revision: 8,
  epoch: 1,
  plan_revision: 3,
  current_plan_revision: 3,
  state: 'completed_with_errors',
  phase: 'execute',
  cancel_intent: false,
  waiting_reason: null,
  can_cancel: false,
  can_resume: true,
  resume_action: 'resume',
  authorization_version: 'auth-v1',
  policy_version: 'policy-v1',
  authorization_expires_at: '2030-01-01T00:00:00Z',
  progress: {
    target_take_ids: ['take-001'],
    prepared_take_ids: ['take-001'],
    submitted_shot_ids: [700, 701, 702],
    completed_shot_ids: [700],
    failed_shot_ids: [701],
    cancelled_shot_ids: [702],
    target_count: 1,
    prepared_count: 1,
    submitted_count: 3,
    completed_count: 1,
    failed_count: 1,
    cancelled_count: 1,
    unresolved_work_count: 0,
  },
  allowances: {
    assistant_calls_total: 4,
    assistant_calls_per_take: 4,
    assistant_calls_remaining_lower_bound: 0,
    image_attempts_initial_total: 1,
    image_retry_grants: 2,
    image_attempts_consumed: 2,
    image_attempts_total: 3,
    image_attempts_per_take: 3,
    image_attempts_remaining_lower_bound: 1,
  },
  blocker: null,
  retry_control: {
    can_retry_failed_shots: true,
    can_accept_unknown_outcome_risk: false,
    failed_shot_ids: [701],
    unknown_outcome_shot_ids: [],
    unknown_attempt_ids: [],
    unknown_outcome_warning: null,
    image_retry_allowance: {
      available: 1,
      total: 3,
      consumed: 2,
    },
  },
  ...overrides,
})

const makeComparison = (run, overrides = {}) => ({
  run_id: run.run_id,
  run_revision: run.revision,
  plan_revision: run.current_plan_revision,
  changed: false,
  changed_fields: [],
  scope_digest: 'e'.repeat(64),
  comparison_issued_at: '2030-01-01T00:00:00Z',
  assistant_allowance: {
    pending_take_id: null,
    old_executable_remaining: 4,
    old_pending_take_remaining: null,
    additional_calls_total: 0,
    additional_calls_per_take: 0,
    new_executable_remaining: 4,
    new_pending_take_remaining: null,
  },
  image_retry_allowance: {
    initial_image_attempts_total: 1,
    previous_retry_grants: 2,
    image_attempts_consumed: 2,
    authorized_image_attempts_total: 3,
    authorized_image_attempts_remaining: 1,
    retry_failed_shot_ids: [701],
    unknown_outcome_shot_ids: [],
    unknown_attempt_ids: [],
    additional_if_retry_failed: 1,
    additional_if_accept_unknown_outcome_risk: 0,
    authorized_total_after_retry_failed: 4,
    authorized_total_after_unknown_risk: 3,
    authorized_total_after_both: 4,
  },
  old_scope: {
    summary: { checkpoint: 'invented-model.safetensors' },
    scope_digest: 'a'.repeat(64),
    authorization_expires_at: '2030-02-01T00:00:00Z',
  },
  new_scope: {
    summary: { checkpoint: 'invented-model.safetensors' },
    scope_digest: 'a'.repeat(64),
    authorization_expires_at: '2030-02-01T00:00:00Z',
    renewal_lifetime_seconds: 604800,
  },
  ...overrides,
})

const actionReceipt = (run, action, body, overrides = {}) => ({
  run_id: run.run_id,
  session_id: session.id,
  revision: Number(body.expected_run_revision) + 1,
  epoch: 1,
  plan_revision: Number(body.expected_plan_revision),
  state: action === 'cancel' ? 'cancelled' : 'active',
  phase: 'prepare',
  cancel_intent: action === 'cancel',
  blocker_code: null,
  waiting_reason: null,
  created_at: '2030-01-01T00:00:00Z',
  updated_at: '2030-01-01T00:01:00Z',
  action,
  request_id: body.request_id,
  ...overrides,
})

let root
let container
let originalStorageDescriptor
let storageWasStubbed = false

beforeEach(() => {
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    const sessionMatch = path.match(/^\/api\/sessions\/(\d+)$/)
    if (sessionMatch) return { ...session, id: Number(sessionMatch[1]) }
    if (/^\/api\/sessions\/\d+\/plan$/.test(path)) return { plan_revision: 3, plan }
    if (path === '/api/config' || path === '/api/comfy/models') return {}
    return []
  })
  vi.spyOn(api, 'getVersioned').mockResolvedValue({ current_run: null })
})

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  container = null
  if (storageWasStubbed) {
    if (originalStorageDescriptor) Object.defineProperty(window, 'localStorage', originalStorageDescriptor)
    else delete window.localStorage
    originalStorageDescriptor = undefined
    storageWasStubbed = false
  } else {
    try { window.localStorage?.clear() } catch { /* happy-dom may disable storage. */ }
  }
  vi.restoreAllMocks()
})

const renderSession = async (run, {
  sessionId = session.id,
  activeStep = 'character',
  sessionStatus = session.status,
  statusResolver = null,
  sessionOverride = {},
  planOverride = plan,
  initialReviewedRevision = null,
} = {}) => {
  vi.mocked(api.getVersioned).mockImplementation(async (path) => {
    const statusMatch = path.match(/^\/api\/sessions\/(\d+)\/full-auto$/)
    if (statusMatch) return { current_run: statusResolver ? statusResolver(Number(statusMatch[1])) : Number(statusMatch[1]) === session.id ? run : null }
    if (path === `/api/sessions/${sessionId}/plan`) return { plan_revision: 3, plan: planOverride }
    return {}
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(React.createElement(SessionView, {
      id: sessionId,
      initialSession: { ...session, ...sessionOverride, id: sessionId, status: sessionStatus },
      initialPlan: planOverride,
      initialRevision: 3,
      initialReviewedRevision,
      initialActiveStep: activeStep,
    }))
    await Promise.resolve()
    await Promise.resolve()
  })
}

const rerenderSession = async (sessionId, activeStep = 'character') => {
  await act(async () => {
    root.render(React.createElement(SessionView, {
      id: sessionId,
      initialSession: { ...session, id: sessionId },
      initialPlan: plan,
      initialRevision: 3,
      initialActiveStep: activeStep,
    }))
    await Promise.resolve()
    await Promise.resolve()
  })
}

const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((accept, fail) => { resolve = accept; reject = fail })
  return { promise, resolve, reject }
}

const installLocalStorage = () => {
  const values = new Map()
  originalStorageDescriptor = Object.getOwnPropertyDescriptor(window, 'localStorage')
  storageWasStubbed = true
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, String(value)),
      removeItem: (key) => values.delete(key),
      clear: () => values.clear(),
    },
  })
  return values
}

const disableLocalStorage = () => {
  originalStorageDescriptor = Object.getOwnPropertyDescriptor(window, 'localStorage')
  storageWasStubbed = true
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    get: () => { throw new Error('Storage is unavailable in this browser context.') },
  })
}

const settle = async () => {
  await act(async () => {
    for (let index = 0; index < 24; index += 1) await Promise.resolve()
  })
}

it('adopts terminal server-owned Full Auto results without local storage', async () => {
  const targets = Array.from({ length: 25 }, (_, index) => `take-${String(index + 1).padStart(3, '0')}`)
  const run = makeRun({
    progress: {
      ...makeRun().progress,
      target_take_ids: targets,
      prepared_take_ids: targets,
      target_count: targets.length,
      prepared_count: targets.length,
    },
  })
  await renderSession(run)

  expect(api.getVersioned).toHaveBeenCalledWith(`/api/sessions/${session.id}/full-auto`)
  expect(container.textContent).toContain('Targets25')
  expect(container.textContent).toContain('Full Auto targets and results (25 takes · 3 submitted shots)')
  expect(container.textContent).toContain('take-025')
  expect(container.textContent).toContain('Shot #700: completed')
  expect(container.textContent).toContain('Shot #701: failed')
  expect(container.textContent).toContain('Shot #702: cancelled')
  expect(container.textContent).toContain('Retry failed shots (1)')
})

it('keeps manual Resource Retry available when no Full Auto run owns the session', async () => {
  await renderSession(null)

  const retry = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Retry 2')
  expect(retry).toBeTruthy()

  const post = vi.spyOn(api, 'post').mockResolvedValue({ pending: 2 })
  await act(async () => { retry.click() })
  await settle()

  expect(post).toHaveBeenCalledWith(`/api/sessions/${session.id}/retry`)
})

it('applies the latest same-revision progress read after a newer poll supersedes an older response', async () => {
  const run = makeRun()
  await renderSession(run)
  await settle()
  const older = deferred()
  const olderProgress = makeRun({
    progress: {
      ...makeRun().progress,
      target_take_ids: Array.from({ length: 24 }, (_, index) => `take-${String(index + 1).padStart(3, '0')}`),
      target_count: 24,
    },
  })
  const latest = makeRun({
    progress: {
      ...makeRun().progress,
      target_take_ids: Array.from({ length: 25 }, (_, index) => `take-${String(index + 1).padStart(3, '0')}`),
      target_count: 25,
    },
  })
  let reads = 0
  vi.mocked(api.getVersioned).mockImplementation(async (path) => {
    if (path === `/api/sessions/${session.id}/full-auto`) {
      reads += 1
      return reads === 1 ? older.promise : { current_run: latest }
    }
    if (path === `/api/sessions/${session.id}/plan`) return { plan_revision: 3, plan }
    return {}
  })

  const refresh = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Refresh status')
  await act(async () => { refresh.click() })
  await settle()
  await act(async () => { refresh.click() })
  await settle()
  older.resolve({ current_run: olderProgress })
  await settle()

  expect(reads).toBe(2)
  expect(container.textContent).toContain('Targets25')
  expect(container.textContent).toContain('Full Auto targets and results (25 takes · 3 submitted shots)')
})

it('offers only cancellation reconciliation while cancellation is unsettled', async () => {
  const run = makeRun({
    state: 'blocked',
    cancel_intent: true,
    can_cancel: false,
    can_resume: true,
    resume_action: 'reconcile_cancellation',
    blocker: { code: 'cancel_pending', message: 'Waiting for owned work to settle.', remedies: ['reconcile_cancellation'] },
    retry_control: {
      ...makeRun().retry_control,
      can_retry_failed_shots: true,
    },
  })
  await renderSession(run)
  const post = vi.spyOn(api, 'postWithStatus').mockImplementation(async (_path, body) => ({
    status: 202,
    data: actionReceipt(run, 'resume', body, {
      state: 'cancel_requested', phase: 'reconcile', cancel_intent: true,
    }),
  }))

  expect(container.textContent).toContain('Manual transfer is pending')
  expect(container.textContent).toContain('Reconcile cancellation')
  expect(container.textContent).not.toContain('Retry failed shots')
  expect([...container.querySelectorAll('button')].some((button) => button.textContent === 'Resume')).toBe(false)
  expect([...container.querySelectorAll('button')].some((button) => button.textContent === 'Retry 2')).toBe(false)

  const reconcile = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Reconcile cancellation')
  await act(async () => { reconcile.click() })
  await settle()
  expect(post).toHaveBeenCalledOnce()
  expect(post.mock.calls[0][0]).toBe(`/api/sessions/${session.id}/full-auto/${run.run_id}/resume`)
  expect(post.mock.calls[0][1]).toMatchObject({ expected_run_revision: '8', expected_plan_revision: '3' })
  expect(post.mock.calls[0][1]).not.toHaveProperty('accept_changed_scope')
  expect(vi.mocked(api.getVersioned).mock.calls.some(([path]) => path.includes('/resume-preflight'))).toBe(false)
})

it('requires explicit consent for a changed scope and sends the exact comparison authority', async () => {
  const run = makeRun({ state: 'cancelled', cancel_intent: true })
  const comparison = makeComparison(run, {
    changed: true,
    changed_fields: ['checkpoint'],
    assistant_allowance: {
      pending_take_id: 'take-001',
      old_executable_remaining: 0,
      old_pending_take_remaining: 0,
      additional_calls_total: 8,
      additional_calls_per_take: 4,
      new_executable_remaining: 4,
      new_pending_take_remaining: 4,
    },
    old_scope: {
      summary: { checkpoint: 'old.safetensors' },
      scope_digest: 'b'.repeat(64),
      authorization_expires_at: '2030-02-01T00:00:00Z',
    },
    new_scope: {
      summary: { checkpoint: 'new.safetensors' },
      scope_digest: 'c'.repeat(64),
      authorization_expires_at: '2030-03-01T00:00:00Z',
      renewal_lifetime_seconds: 604800,
    },
  })
  await renderSession(run)
  vi.mocked(api.getVersioned).mockImplementation(async (path) => {
    if (path === `/api/sessions/${session.id}/full-auto`) return { current_run: run }
    if (path === `/api/sessions/${session.id}/plan`) return { plan_revision: 3, plan }
    if (path.endsWith('/resume-preflight')) return comparison
    return {}
  })
  const post = vi.spyOn(api, 'postWithStatus').mockImplementation(async (_path, body) => ({
    status: 202,
    data: actionReceipt(run, 'resume', body),
  }))

  const resume = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Resume')
  await act(async () => { resume.click() })
  await settle()
  expect(container.textContent).toContain('Previously authorized')
  expect(container.textContent).toContain('old.safetensors')
  expect(container.textContent).toContain('new.safetensors')
  expect(container.textContent).toContain('8 calls total · 4 per take')
  expect(container.textContent).toContain('Expires 2030-03-01T00:00:00Z')
  expect(post).not.toHaveBeenCalled()

  const accept = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Accept changes and Resume')
  await act(async () => { accept.click() })
  await settle()
  expect(post).toHaveBeenCalledOnce()
  expect(post.mock.calls[0][0]).toBe(`/api/sessions/${session.id}/full-auto/${run.run_id}/resume`)
  expect(post.mock.calls[0][1]).toMatchObject({
    expected_run_revision: '8',
    expected_plan_revision: '3',
    scope_digest: comparison.scope_digest,
    comparison_issued_at: comparison.comparison_issued_at,
    accept_changed_scope: true,
  })
})

it('replays an uncertain control with the same frozen UUID and body after malformed 409', async () => {
  const run = makeRun({
    state: 'blocked',
    can_cancel: true,
    can_resume: false,
    blocker: { code: 'provider_unavailable', message: 'The provider is unavailable.', remedies: ['cancel'] },
  })
  await renderSession(run)
  const unknown = Object.assign(new Error('Unverified conflict response'), {
    status: 409,
    hasStableErrorBody: false,
    detail: 'not a validated error envelope',
  })
  const post = vi.spyOn(api, 'postWithStatus')
    .mockRejectedValueOnce(unknown)
    .mockImplementationOnce(async (_path, body) => ({
      status: 200,
      data: actionReceipt(run, 'cancel', body),
    }))

  const cancel = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Switch to manual')
  await act(async () => { cancel.click() })
  await settle()
  const retry = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Retry same cancel request')
  expect(retry).toBeTruthy()
  const savedBody = post.mock.calls[0][1]
  expect(savedBody.request_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i)

  await act(async () => { retry.click() })
  await settle()
  expect(post).toHaveBeenCalledTimes(2)
  expect(post.mock.calls[1]).toEqual(post.mock.calls[0])
})

it('resumes unchanged scope after one explicit click without asking for changed-scope consent', async () => {
  const run = makeRun({ state: 'cancelled', cancel_intent: true })
  const comparison = makeComparison(run)
  await renderSession(run)
  vi.mocked(api.getVersioned).mockImplementation(async (path) => {
    if (path === `/api/sessions/${session.id}/full-auto`) return { current_run: run }
    if (path === `/api/sessions/${session.id}/plan`) return { plan_revision: 3, plan }
    if (path.endsWith('/resume-preflight')) return comparison
    return {}
  })
  const post = vi.spyOn(api, 'postWithStatus').mockImplementation(async (_path, body) => ({
    status: 202,
    data: actionReceipt(run, 'resume', body),
  }))

  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Resume').click() })
  await settle()

  expect(vi.mocked(api.getVersioned).mock.calls.filter(([path]) => path.endsWith('/resume-preflight'))).toHaveLength(1)
  expect(post).toHaveBeenCalledOnce()
  expect(post.mock.calls[0][1]).toMatchObject({
    expected_run_revision: '8',
    expected_plan_revision: '3',
    scope_digest: comparison.scope_digest,
    comparison_issued_at: comparison.comparison_issued_at,
    accept_changed_scope: false,
  })
  expect(container.textContent).not.toContain('Accept changes and Resume')
})

it.each(['budget_exhausted', 'authorization_expired'])(
  'offers a server-quoted renewal for %s only after explicit acceptance', async (blockerCode) => {
  const run = makeRun({
    state: 'blocked',
    can_cancel: true,
    can_resume: false,
    allowances: { ...makeRun().allowances, assistant_calls_remaining_lower_bound: 0 },
    blocker: { code: blockerCode, message: 'The current authorization cannot continue.', remedies: ['review_status', 'cancel'] },
  })
  const comparison = makeComparison(run, {
    changed: true,
    changed_fields: ['assistant_allowance'],
    comparison_issued_at: '2030-02-01T00:00:00Z',
    scope_digest: 'd'.repeat(64),
    assistant_allowance: {
      pending_take_id: 'take-002',
      old_executable_remaining: 0,
      old_pending_take_remaining: 0,
      additional_calls_total: 8,
      additional_calls_per_take: 4,
      new_executable_remaining: 4,
      new_pending_take_remaining: 4,
    },
    old_scope: {
      summary: { provider: 'invented-provider', limits: { assistant_calls_total: 8 } },
      scope_digest: 'b'.repeat(64),
      authorization_expires_at: '2030-01-01T00:00:00Z',
    },
    new_scope: {
      summary: { provider: 'invented-provider', limits: { assistant_calls_total: 8 } },
      scope_digest: 'c'.repeat(64),
      authorization_expires_at: '2030-02-08T00:00:00Z',
      renewal_lifetime_seconds: 604800,
    },
  })
  await renderSession(run)
  vi.mocked(api.getVersioned).mockImplementation(async (path) => {
    if (path === `/api/sessions/${session.id}/full-auto`) return { current_run: run }
    if (path === `/api/sessions/${session.id}/plan`) return { plan_revision: 3, plan }
    if (path.endsWith('/resume-preflight')) return comparison
    return {}
  })
  const post = vi.spyOn(api, 'postWithStatus').mockImplementation(async (_path, body) => ({
    status: 202,
    data: actionReceipt(run, 'resume', body, { state: 'active', cancel_intent: false }),
  }))

  const compare = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Compare renewal scope')
  expect(compare).toBeTruthy()
  await act(async () => { compare.click() })
  await settle()

  expect(post).not.toHaveBeenCalled()
  expect(container.textContent).toContain('Before: 0 executable calls remain for take-002.')
  expect(container.textContent).toContain('Additional authorization: 8 calls total · 4 per take.')
  expect(container.textContent).toContain('After acceptance: 4 executable calls for take-002.')
  expect(container.textContent).toContain('Pending-take allowance: before 0 calls · after 4 calls.')
  expect(container.textContent).toContain('Expires 2030-01-01T00:00:00Z')
  expect(container.textContent).toContain('Expires 2030-02-08T00:00:00Z')

  const accept = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Accept changes and Resume')
  await act(async () => { accept.click() })
  await settle()
  expect(post).toHaveBeenCalledOnce()
  expect(post.mock.calls[0][1]).toMatchObject({
    expected_run_revision: '8',
    expected_plan_revision: '3',
    scope_digest: comparison.scope_digest,
    comparison_issued_at: comparison.comparison_issued_at,
    accept_changed_scope: true,
  })
})

it.each([
  ['partial receipt', (receipt) => { delete receipt.updated_at; return receipt }],
  ['wrong run', (receipt) => ({ ...receipt, run_id: 'run-other' })],
  ['wrong action', (receipt) => ({ ...receipt, action: 'resume' })],
  ['wrong request UUID', (receipt) => ({ ...receipt, request_id: '00000000-0000-4000-8000-000000000000' })],
  ['wrong plan revision', (receipt) => ({ ...receipt, plan_revision: 99 })],
  ['unrecognized receipt field', (receipt) => ({ ...receipt, accepted: true })],
])('keeps the exact control UUID after a 2xx %s and accepts a matching receipt on replay', async (_label, mutate) => {
  const run = makeRun({
    state: 'blocked',
    can_cancel: true,
    can_resume: false,
    blocker: { code: 'provider_unavailable', message: 'The provider is unavailable.', remedies: ['cancel'] },
  })
  await renderSession(run)
  const post = vi.spyOn(api, 'postWithStatus').mockImplementation(async (_path, body) => {
    const receipt = actionReceipt(run, 'cancel', body)
    if (post.mock.calls.length === 1) return { status: 202, data: mutate({ ...receipt }) }
    return { status: 202, data: receipt }
  })

  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Switch to manual').click() })
  await settle()
  const retry = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Retry same cancel request')
  expect(retry).toBeTruthy()
  expect(container.textContent).toContain('The control outcome is unknown.')
  const frozen = post.mock.calls[0]

  await act(async () => { retry.click() })
  await settle()
  expect(post).toHaveBeenCalledTimes(2)
  expect(post.mock.calls[1]).toEqual(frozen)
  expect([...container.querySelectorAll('button')].some((button) => button.textContent === 'Retry same cancel request')).toBe(false)
})

it('does not replace newer public status with a matching historical action receipt', async () => {
  const run = makeRun({
    state: 'blocked',
    can_cancel: true,
    can_resume: false,
    blocker: { code: 'provider_unavailable', message: 'The provider is unavailable.', remedies: ['cancel'] },
  })
  let latest = run
  await renderSession(run)
  vi.mocked(api.getVersioned).mockImplementation(async (path) => {
    if (path === `/api/sessions/${session.id}/full-auto`) return { current_run: latest }
    if (path === `/api/sessions/${session.id}/plan`) return { plan_revision: 3, plan }
    return {}
  })
  vi.spyOn(api, 'postWithStatus').mockImplementation(async (_path, body) => {
    latest = makeRun({
      revision: 10,
      state: 'blocked',
      can_cancel: true,
      can_resume: false,
      blocker: { code: 'provider_unavailable', message: 'A newer status remains blocked.', remedies: ['cancel'] },
    })
    return { status: 200, data: actionReceipt(run, 'cancel', body, { revision: 9 }) }
  })

  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Switch to manual').click() })
  await settle()
  expect(container.textContent).toContain('revision 10')
  expect(container.textContent).toContain('A newer status remains blocked.')
  expect(container.textContent).not.toContain('cancelled · prepare')
})

it('requires another user click and a fresh comparison after server rejects stale scope consent', async () => {
  const run = makeRun({ state: 'cancelled', cancel_intent: true })
  const first = makeComparison(run, { changed: true, changed_fields: ['plan'], scope_digest: 'a'.repeat(64) })
  const second = makeComparison(run, {
    changed: true,
    changed_fields: ['plan'],
    scope_digest: 'b'.repeat(64),
    new_scope: {
      summary: { checkpoint: 'fresh.safetensors' },
      scope_digest: 'c'.repeat(64),
      authorization_expires_at: '2030-03-01T00:00:00Z',
      renewal_lifetime_seconds: 604800,
    },
  })
  const stale = Object.assign(new Error('The Resume comparison expired.'), {
    status: 409,
    hasStableErrorBody: true,
    detail: { code: 'resume_comparison_stale' },
  })
  await renderSession(run)
  let comparisons = [first, second]
  vi.mocked(api.getVersioned).mockImplementation(async (path) => {
    if (path === `/api/sessions/${session.id}/full-auto`) return { current_run: run }
    if (path === `/api/sessions/${session.id}/plan`) return { plan_revision: 3, plan }
    if (path.endsWith('/resume-preflight')) return comparisons.shift()
    return {}
  })
  const post = vi.spyOn(api, 'postWithStatus')
    .mockRejectedValueOnce(stale)
    .mockImplementationOnce(async (_path, body) => ({ status: 202, data: actionReceipt(run, 'resume', body) }))

  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Resume').click() })
  await settle()
  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Accept changes and Resume').click() })
  await settle()
  expect(post).toHaveBeenCalledOnce()
  expect(vi.mocked(api.getVersioned).mock.calls.filter(([path]) => path.endsWith('/resume-preflight'))).toHaveLength(1)
  expect(container.textContent).toContain('server rejected this request')

  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Resume').click() })
  await settle()
  expect(post).toHaveBeenCalledOnce()
  expect(vi.mocked(api.getVersioned).mock.calls.filter(([path]) => path.endsWith('/resume-preflight'))).toHaveLength(2)
  expect(container.textContent).toContain('Accept changes and Resume')
  expect(container.textContent).toContain('fresh.safetensors')
})

it('keeps ownership locked through a status outage and unlocks only after verified cancellation settlement', async () => {
  const draining = makeRun({
    state: 'blocked',
    cancel_intent: true,
    can_cancel: false,
    can_resume: false,
    resume_action: 'reconcile_cancellation',
    blocker: { code: 'cancel_pending', message: 'A remote attempt is still draining.', remedies: ['reconcile_cancellation'] },
    progress: { ...makeRun().progress, unresolved_work_count: 1 },
  })
  let latest = draining
  await renderSession(draining, { activeStep: 'takes' })
  vi.mocked(api.getVersioned).mockImplementation(async (path) => {
    if (path === `/api/sessions/${session.id}/full-auto`) {
      if (latest === 'outage') throw new Error('Status service unavailable.')
      return { current_run: latest }
    }
    if (path === `/api/sessions/${session.id}/plan`) return { plan_revision: 3, plan }
    if (path.endsWith('/resume-preflight')) return makeComparison(latest)
    return {}
  })
  const post = vi.spyOn(api, 'postWithStatus').mockImplementation(async (_path, body) => ({
    status: 202,
    data: actionReceipt(latest, 'resume', body, {
      state: 'cancelled', phase: 'reconcile', cancel_intent: true,
    }),
  }))

  expect(container.textContent).toContain('Manual transfer is pending.')
  expect(container.textContent).toContain('A remote attempt is still draining.')
  expect([...container.querySelectorAll('button')].some((button) => button.textContent === 'Resume')).toBe(false)
  expect([...container.querySelectorAll('button')].some((button) => button.textContent.startsWith('Retry failed shots'))).toBe(false)
  expect(container.querySelector('textarea[placeholder="e.g. natural soft daylight in an open loft, detailed skin texture"]')).toBeNull()

  latest = 'outage'
  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Refresh status').click() })
  await settle()
  expect(container.textContent).toContain('Status service unavailable.')
  const unavailableReconcile = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Reconcile cancellation')
  expect(unavailableReconcile).toBeTruthy()
  expect(unavailableReconcile.disabled).toBe(true)
  expect([...container.querySelectorAll('button')].some((button) => button.textContent === 'Run')).toBe(false)
  expect(post).not.toHaveBeenCalled()

  latest = makeRun({
    revision: 10,
    state: 'cancelled',
    cancel_intent: true,
    can_cancel: false,
    can_resume: true,
    resume_action: 'resume',
    progress: { ...makeRun().progress, unresolved_work_count: 0 },
  })
  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Refresh status').click() })
  await settle()
  expect(post).not.toHaveBeenCalled()
  const resume = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Resume')
  expect(resume).toBeTruthy()
  const addTake = [...container.querySelectorAll('button')].find((button) => button.textContent === '+ Add Take')
  expect(addTake).toBeTruthy()
  expect(addTake.disabled).toBe(false)
  expect(post).not.toHaveBeenCalled()
})

it('keeps manual controls fenced by unresolved unknown history after a terminal state', async () => {
  const terminalUnknown = makeRun({
    state: 'completed_with_errors',
    cancel_intent: true,
    can_cancel: false,
    can_resume: false,
    retry_control: {
      ...makeRun().retry_control,
      can_accept_unknown_outcome_risk: false,
      unknown_outcome_shot_ids: [701],
      unknown_attempt_ids: ['attempt-invented-963'],
      unknown_outcome_warning: 'The remote outcome remains unknown.',
    },
    progress: { ...makeRun().progress, unresolved_work_count: 1 },
  })
  await renderSession(terminalUnknown, { activeStep: 'takes' })

  const addTake = [...container.querySelectorAll('button')]
    .find((button) => button.textContent === '+ Add Take')
  expect(addTake).toBeTruthy()
  expect(addTake.disabled).toBe(true)
  expect(container.querySelector('fieldset[disabled]')).toBeTruthy()
})

it('switches to manual through the replay-safe Cancel flow and waits through outage, reopen, and a late response', async () => {
  const values = installLocalStorage()
  const active = makeRun({ state: 'active', can_cancel: true, can_resume: false })
  const draining = makeRun({
    revision: 9,
    state: 'blocked',
    phase: 'reconcile',
    cancel_intent: true,
    can_cancel: false,
    can_resume: true,
    resume_action: 'reconcile_cancellation',
    blocker: { code: 'cancel_pending', message: 'A remote attempt is still draining.', remedies: ['reconcile_cancellation'] },
    progress: { ...makeRun().progress, unresolved_work_count: 1 },
  })
  const settled = makeRun({
    revision: 10,
    state: 'cancelled',
    cancel_intent: true,
    can_cancel: false,
    can_resume: true,
    resume_action: 'resume',
    progress: { ...makeRun().progress, unresolved_work_count: 0 },
  })
  let latest = active
  const readStatus = vi.fn(async (path) => {
    if (path === `/api/sessions/${session.id}/full-auto`) {
      if (latest === 'outage') throw new Error('Status service unavailable.')
      return { current_run: latest }
    }
    if (path === `/api/sessions/${session.id}/plan`) return { plan_revision: 3, plan }
    return {}
  })
  await renderSession(active, { activeStep: 'takes' })
  vi.mocked(api.getVersioned).mockImplementation(readStatus)
  const cancelGate = deferred()
  const post = vi.spyOn(api, 'postWithStatus').mockReturnValue(cancelGate.promise)

  await act(async () => {
    [...container.querySelectorAll('button')].find((button) => button.textContent === 'Switch to manual').click()
    await Promise.resolve()
  })
  await settle()

  expect(post).toHaveBeenCalledOnce()
  const frozen = post.mock.calls[0]
  expect(frozen[0]).toContain(`/full-auto/${active.run_id}/cancel`)
  expect(frozen[1]).toMatchObject({
    expected_run_revision: '8',
    expected_plan_revision: '3',
  })
  const key = `idevgen:full-auto-control:${session.id}:${active.run_id}`
  expect(JSON.parse(values.get(key))).toMatchObject({ action: 'cancel', body: frozen[1] })
  expect(container.textContent).toContain('Manual transfer is pending.')
  expect([...container.querySelectorAll('button')].find((button) => button.textContent === '+ Add Take').disabled).toBe(true)

  await act(async () => root.unmount())
  root = null
  container.remove()
  container = null
  latest = draining
  await renderSession(draining, { activeStep: 'takes' })
  vi.mocked(api.getVersioned).mockImplementation(readStatus)
  await settle()
  expect([...container.querySelectorAll('button')].some((button) => button.textContent === 'Retry same cancel request')).toBe(true)
  expect([...container.querySelectorAll('button')].find((button) => button.textContent === '+ Add Take').disabled).toBe(true)

  latest = 'outage'
  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Refresh status').click() })
  await settle()
  expect(container.textContent).toContain('Status service unavailable.')
  expect([...container.querySelectorAll('button')].find((button) => button.textContent === '+ Add Take').disabled).toBe(true)

  latest = settled
  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Refresh status').click() })
  await settle()
  expect(values.get(key)).toBeUndefined()
  expect([...container.querySelectorAll('button')].find((button) => button.textContent === '+ Add Take').disabled).toBe(false)
  expect([...container.querySelectorAll('button')].some((button) => button.textContent === 'Resume')).toBe(true)

  cancelGate.resolve({
    status: 202,
    data: actionReceipt(active, 'cancel', frozen[1], {
      state: 'cancelled', phase: 'reconcile', cancel_intent: true,
    }),
  })
  await settle()
  expect(post).toHaveBeenCalledOnce()
  expect([...container.querySelectorAll('button')].find((button) => button.textContent === '+ Add Take').disabled).toBe(false)
})

it('requires explicit review approval and navigation does not submit or run', async () => {
  const reviewSession = { ...session, shots: [{ id: 710, status: 'pending' }] }
  vi.mocked(api.get).mockImplementation(async (path) => {
    if (path === `/api/sessions/${session.id}`) return reviewSession
    if (path === `/api/sessions/${session.id}/plan/review?plan_revision=3`) {
      return { plan_revision: 3, takes: [{ take_id: 'take-001', conflicts: [] }] }
    }
    return []
  })
  const post = vi.spyOn(api, 'post').mockResolvedValue({ plan_revision: 3 })
  const control = vi.spyOn(api, 'postWithStatus')
  await renderSession(null, {
    activeStep: 'review',
    sessionStatus: 'draft',
    sessionOverride: { shots: [{ id: 710, status: 'pending' }] },
  })
  await settle()

  const approve = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Approve Review (Rev 3)')
  expect(approve).toBeTruthy()
  expect(approve.disabled).toBe(false)
  const proceed = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Proceed to Generation →')
  expect(proceed.disabled).toBe(true)
  await act(async () => { proceed.click() })
  await settle()

  expect([...container.querySelectorAll('button')].some((button) => button.textContent === 'Run (1)')).toBe(false)
  expect(post).not.toHaveBeenCalled()
  expect(control).not.toHaveBeenCalled()

  await act(async () => { approve.click() })
  await settle()
  expect(post).toHaveBeenCalledOnce()
  expect(post.mock.calls[0]).toEqual([
    `/api/sessions/${session.id}/plan/review/approve`,
    { plan_revision: 3 },
  ])
  const approvedProceed = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Proceed to Generation →')
  expect(approvedProceed.disabled).toBe(false)
  await act(async () => { approvedProceed.click() })
  await settle()
  expect([...container.querySelectorAll('button')].some((button) => button.textContent === 'Run (1)')).toBe(true)
  expect(post).toHaveBeenCalledOnce()
  expect(control).not.toHaveBeenCalled()
})

it('retains the manual prepare, approve, submit, and Run journey after settled transfer', async () => {
  const settled = makeRun({
    state: 'cancelled',
    cancel_intent: true,
    can_cancel: false,
    can_resume: true,
    progress: { ...makeRun().progress, unresolved_work_count: 0 },
  })
  const manualSession = { ...session, status: 'draft', shots: [] }
  let preparation = null
  vi.mocked(api.get).mockImplementation(async (path) => {
    if (path === `/api/sessions/${session.id}`) return manualSession
    if (path === `/api/sessions/${session.id}/plan/review?plan_revision=3`) {
      return { plan_revision: 3, takes: [{ take_id: 'take-001', conflicts: [] }] }
    }
    return []
  })
  vi.mocked(api.getVersioned).mockImplementation(async (path) => {
    if (path === `/api/sessions/${session.id}/full-auto`) return { current_run: settled }
    if (path === `/api/sessions/${session.id}/plan`) {
      return { plan_revision: 3, plan, preparation }
    }
    return {}
  })
  const post = vi.spyOn(api, 'post').mockImplementation(async (path, body) => {
    if (path === `/api/sessions/${session.id}/plan/preparations/prepare`) {
      expect(body).toEqual({ plan_revision: 3, take_ids: ['take-001'] })
      preparation = { completed: [{ take_id: 'take-001', status: 'ready' }], incomplete: [], history: [] }
      return { prepared: [{ take_id: 'take-001', status: 'ready' }] }
    }
    if (path === `/api/sessions/${session.id}/plan/review/approve`) {
      expect(body).toEqual({ plan_revision: 3 })
      return { plan_revision: 3 }
    }
    if (path === `/api/sessions/${session.id}/plan/preparations/submit-selected`) {
      expect(body).toEqual({ plan_revision: 3, take_ids: ['take-001'] })
      manualSession.shots = [{ id: 703, status: 'pending' }]
      return { submitted_count: 1 }
    }
    if (path === `/api/sessions/${session.id}/run`) return {}
    throw new Error(`Unexpected write: ${path}`)
  })
  await renderSession(settled, {
    activeStep: 'takes',
    sessionStatus: 'draft',
    sessionOverride: { shots: [] },
  })
  vi.mocked(api.getVersioned).mockImplementation(async (path) => {
    if (path === `/api/sessions/${session.id}/full-auto`) return { current_run: settled }
    if (path === `/api/sessions/${session.id}/plan`) {
      return { plan_revision: 3, plan, preparation }
    }
    return {}
  })

  await act(async () => {
    [...container.querySelectorAll('button')].find((button) => button.textContent === '4. Review').click()
  })
  await settle()
  const prepare = [...container.querySelectorAll('button')]
    .find((button) => button.textContent === 'Prepare Incomplete Takes (1)')
  expect(prepare, container.textContent).toBeTruthy()
  expect(prepare.disabled).toBe(false)
  await act(async () => { prepare.click() })
  await settle()
  expect(post.mock.calls.map(([path]) => path)).toEqual([
    `/api/sessions/${session.id}/plan/preparations/prepare`,
  ])
  const takeRow = [...container.querySelectorAll('tbody tr')]
    .find((row) => row.textContent.includes('take-001'))
  const select = takeRow.querySelector('input[type="checkbox"]')
  expect(select.disabled).toBe(false)
  await act(async () => { select.click() })
  await settle()

  const approve = [...container.querySelectorAll('button')]
    .find((button) => button.textContent === 'Approve Review (Rev 3)')
  expect(approve).toBeTruthy()
  await act(async () => { approve.click() })
  await settle()
  expect(post.mock.calls.map(([path]) => path)).toEqual([
    `/api/sessions/${session.id}/plan/preparations/prepare`,
    `/api/sessions/${session.id}/plan/review/approve`,
  ])

  const submit = [...container.querySelectorAll('button')]
    .find((button) => button.textContent === 'Submit Test Selection (1)')
  expect(submit).toBeTruthy()
  expect(submit.disabled).toBe(false)
  await act(async () => { submit.click() })
  await settle()
  expect(container.textContent).toContain('Run (1)')
  expect(post.mock.calls.map(([path]) => path)).toEqual([
    `/api/sessions/${session.id}/plan/preparations/prepare`,
    `/api/sessions/${session.id}/plan/review/approve`,
    `/api/sessions/${session.id}/plan/preparations/submit-selected`,
  ])
  expect(post.mock.calls.some(([path]) => path.includes('/full-auto/') || path.includes('/resume'))).toBe(false)

  await act(async () => {
    [...container.querySelectorAll('button')].find((button) => button.textContent === 'Run (1)').click()
  })
  await settle()
  expect(post.mock.calls.map(([path]) => path)).toEqual([
    `/api/sessions/${session.id}/plan/preparations/prepare`,
    `/api/sessions/${session.id}/plan/review/approve`,
    `/api/sessions/${session.id}/plan/preparations/submit-selected`,
    `/api/sessions/${session.id}/run`,
  ])
})

it('shows manual and profile-derived coverage only from prepared snapshot provenance', async () => {
  const takes = [
    { take_id: 'take-001', label: 'Manual take', wardrobe_coverage: 'User-authored coverage.' },
    { take_id: 'take-002', label: 'Derived take', wardrobe_coverage: 'Compiled coverage.' },
    { take_id: 'take-003', label: 'Edited take', wardrobe_coverage: 'Edited coverage.' },
    { take_id: 'take-004', label: 'Unprepared take', wardrobe_coverage: 'Not persisted coverage.' },
  ]
  const snapshots = {
    'take-001': {
      effective_state: { wardrobe_coverage: 'User-authored coverage.' },
      provenance: {},
    },
    'take-002': {
      effective_state: { wardrobe_coverage: 'Compiled coverage.' },
      provenance: { profile_coverage_evidence: {
        look_version: 7,
        profile_revision: 2,
        derived_coverage: { stage_index: 2 },
      } },
    },
    'take-003': {
      effective_state: { wardrobe_coverage: 'Edited coverage.' },
      provenance: { profile_coverage_evidence: {
        look_version: 8,
        profile_revision: 4,
        derived_coverage: null,
      } },
    },
  }
  vi.mocked(api.get).mockImplementation(async (path) => {
    if (path === `/api/sessions/${session.id}`) return session
    if (path === `/api/sessions/${session.id}/plan/review?plan_revision=3`) {
      return {
        plan_revision: 3,
        takes: takes.map((take) => ({
          take_id: take.take_id,
          conflicts: [],
          snapshot: snapshots[take.take_id],
        })),
      }
    }
    const review = path.match(/\/plan\/takes\/(take-\d+)\/review/)
    if (review) return { snapshot: snapshots[review[1]] }
    return []
  })
  await renderSession(null, { activeStep: 'review', planOverride: { ...plan, takes } })
  const openReview = async (takeId) => {
    const row = [...container.querySelectorAll('tbody tr')].find((item) => item.textContent.includes(takeId))
    const button = row?.querySelector('button[title="Inspect effective prompt, provenance, and conflicts"]')
    await act(async () => { button.click() })
    await settle()
  }

  await openReview('take-001')
  expect(container.textContent).toContain('Manual coverage')
  expect(container.textContent).not.toContain('Derived · saved look')
  await openReview('take-002')
  expect(container.textContent).toContain('Derived · saved look v7 · profile revision 2 · stage 3')
  await openReview('take-003')
  expect(container.textContent).toContain('Manual coverage · saved look v8 · profile revision 4')
  expect(container.textContent).not.toContain('stage 1')
  await openReview('take-004')
  expect(container.textContent).not.toContain('Manual coverage')
  expect(container.textContent).not.toContain('Coverage provenance unavailable')
})

it('discovers a server-owned run when browser storage is unavailable', async () => {
  disableLocalStorage()
  const run = makeRun({ state: 'active', can_cancel: true, can_resume: false })
  await renderSession(run)

  expect(container.textContent).toContain(run.run_id)
  expect(container.textContent).toContain('Assistant calls remaining: 0 / 4')
  expect(container.textContent).not.toContain('status could not be refreshed')
  expect([...container.querySelectorAll('button')].some((button) => button.textContent === 'Switch to manual')).toBe(true)
})

it('shows a server-owned preparation wait while the legacy session row remains draft', async () => {
  const waiting = makeRun({
    state: 'waiting_runner',
    phase: 'execute',
    waiting_reason: 'runner_busy',
    can_cancel: true,
    can_resume: false,
  })
  vi.mocked(api.get).mockImplementation(async (path) => {
    if (path === `/api/sessions/${session.id}`) return { ...session, status: 'draft' }
    if (path === `/api/sessions/${session.id}/plan`) return { plan_revision: 3, plan }
    if (path === '/api/config' || path === '/api/comfy/models') return {}
    return []
  })
  await renderSession(waiting, { sessionStatus: 'draft' })
  await settle()

  expect(container.textContent).toContain('waiting runner · execute · revision 8 · waiting for runner busy')
  expect(container.textContent).toContain('Assistant calls remaining: 0 / 4')
  expect(container.textContent).not.toContain('No Full Auto run is recorded')
})

it('restores the same uncertain UUID and body after closing and reopening the session', async () => {
  const values = installLocalStorage()
  const run = makeRun({
    state: 'blocked',
    can_cancel: true,
    can_resume: false,
    blocker: { code: 'provider_unavailable', message: 'The provider is unavailable.', remedies: ['cancel'] },
  })
  await renderSession(run)
  const post = vi.spyOn(api, 'postWithStatus')
    .mockRejectedValueOnce(new Error('Connection lost after dispatch.'))
    .mockImplementationOnce(async (_path, body) => ({ status: 202, data: actionReceipt(run, 'cancel', body) }))

  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Switch to manual').click() })
  await settle()
  const frozen = post.mock.calls[0]
  const key = `idevgen:full-auto-control:${session.id}:${run.run_id}`
  expect(JSON.parse(values.get(key))).toMatchObject({ action: 'cancel', body: frozen[1] })

  await act(async () => root.unmount())
  root = null
  container.remove()
  container = null
  await renderSession(run)
  const retry = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Retry same cancel request')
  expect(retry).toBeTruthy()
  await act(async () => { retry.click() })
  await settle()
  expect(post).toHaveBeenCalledTimes(2)
  expect(post.mock.calls[1]).toEqual(frozen)
})

it('adopts another tab cancellation request through the storage event and replays its exact body', async () => {
  installLocalStorage()
  const run = makeRun({
    state: 'blocked',
    can_cancel: true,
    can_resume: false,
    blocker: { code: 'provider_unavailable', message: 'The provider is unavailable.', remedies: ['cancel'] },
  })
  await renderSession(run)
  const secondContainer = document.createElement('div')
  document.body.appendChild(secondContainer)
  const secondRoot = createRoot(secondContainer)
  await act(async () => {
    secondRoot.render(React.createElement(SessionView, {
      id: session.id,
      initialSession: session,
      initialPlan: plan,
      initialRevision: 3,
      initialActiveStep: 'character',
    }))
    await Promise.resolve()
    await Promise.resolve()
  })
  await settle()
  const post = vi.spyOn(api, 'postWithStatus')
    .mockRejectedValueOnce(new Error('Connection lost after dispatch.'))
    .mockImplementationOnce(async (_path, body) => ({ status: 202, data: actionReceipt(run, 'cancel', body) }))

  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Switch to manual').click() })
  await settle()
  const frozen = post.mock.calls[0]
  const key = `idevgen:full-auto-control:${session.id}:${run.run_id}`
  await act(async () => { window.dispatchEvent(new StorageEvent('storage', { key })) })
  await settle()
  const retry = [...secondContainer.querySelectorAll('button')].find((button) => button.textContent === 'Retry same cancel request')
  expect(retry).toBeTruthy()
  await act(async () => { retry.click() })
  await settle()
  expect(post).toHaveBeenCalledTimes(2)
  expect(post.mock.calls[1]).toEqual(frozen)
  await act(async () => secondRoot.unmount())
  secondContainer.remove()
})

it('discards an A-to-B-to-A preflight response from the earlier session epoch', async () => {
  const run = makeRun({ state: 'cancelled', cancel_intent: true })
  const comparison = makeComparison(run, { changed: true, changed_fields: ['plan'] })
  const delayedComparison = deferred()
  await renderSession(run)
  vi.mocked(api.getVersioned).mockImplementation(async (path) => {
    if (path.endsWith('/full-auto')) {
      const id = Number(path.match(/^\/api\/sessions\/(\d+)/)?.[1])
      return { current_run: id === session.id ? run : null }
    }
    if (path.endsWith('/resume-preflight')) return delayedComparison.promise
    if (path.endsWith('/plan')) return { plan_revision: 3, plan }
    return {}
  })
  const post = vi.spyOn(api, 'postWithStatus')

  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Resume').click() })
  await settle()
  expect(vi.mocked(api.getVersioned).mock.calls.some(([path]) => path.endsWith('/resume-preflight'))).toBe(true)
  await rerenderSession(964)
  await settle()
  await rerenderSession(session.id)
  await settle()
  delayedComparison.resolve(comparison)
  await settle()

  expect(post).not.toHaveBeenCalled()
  expect(container.textContent).not.toContain('Resume scope comparison')
})

it('keeps an in-flight control replay frozen when its response arrives after unmount', async () => {
  installLocalStorage()
  const run = makeRun({
    state: 'blocked',
    can_cancel: true,
    can_resume: false,
    blocker: { code: 'provider_unavailable', message: 'The provider is unavailable.', remedies: ['cancel'] },
  })
  const delayedPost = deferred()
  await renderSession(run)
  const post = vi.spyOn(api, 'postWithStatus').mockImplementation(async (_path, body) => {
    if (post.mock.calls.length === 1) return delayedPost.promise
    return { status: 202, data: actionReceipt(run, 'cancel', body) }
  })
  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Switch to manual').click() })
  await settle()
  const frozen = post.mock.calls[0]
  const key = `idevgen:full-auto-control:${session.id}:${run.run_id}`
  expect(window.localStorage.getItem(key)).toContain(frozen[1].request_id)

  await act(async () => root.unmount())
  root = null
  container.remove()
  container = null
  await renderSession(run)
  expect([...container.querySelectorAll('button')].some((button) => button.textContent === 'Retry same cancel request')).toBe(true)
  delayedPost.resolve({ status: 202, data: actionReceipt(run, 'cancel', frozen[1]) })
  await settle()
  expect([...container.querySelectorAll('button')].some((button) => button.textContent === 'Retry same cancel request')).toBe(true)
  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Retry same cancel request').click() })
  await settle()
  expect(post.mock.calls[1]).toEqual(frozen)
})

it('ignores late duplicate-acceptance responses after the session changes A-to-B-to-A', async () => {
  const run = makeRun({
    state: 'blocked',
    can_cancel: false,
    can_resume: false,
    blocker: { code: 'duplicate_review_required', message: 'Review the current target set.', remedies: ['duplicate_review_required'] },
  })
  const review = {
    state: 'duplicate_review_required',
    plan_revision: 3,
    target_digest: 'a'.repeat(64),
    snapshot_digest: 'b'.repeat(64),
    duplicate_set_digest: 'c'.repeat(64),
    review_digest: 'd'.repeat(64),
    duplicates: [{ take_id: 'take-001', duplicate_take_id: 'take-002' }],
    duplicate_acceptance_required: true,
    duplicate_acceptance_current: false,
    blockers: [],
  }
  const delayedAcceptance = deferred()
  vi.mocked(api.get).mockImplementation(async (path) => {
    const sessionMatch = path.match(/^\/api\/sessions\/(\d+)$/)
    if (sessionMatch) return { ...session, id: Number(sessionMatch[1]) }
    if (/^\/api\/sessions\/\d+\/plan\/review/.test(path)) return {
      plan_revision: 3,
      takes: [],
      full_auto_duplicate_review: path.startsWith(`/api/sessions/${session.id}/`) ? review : null,
    }
    if (path === `/api/sessions/${session.id}/plan`) return { plan_revision: 3, plan }
    if (path === '/api/config' || path === '/api/comfy/models') return {}
    if (/^\/api\/sessions\/\d+\/plan$/.test(path)) return { plan_revision: 3, plan }
    return []
  })
  await renderSession(run, { activeStep: 'review' })
  const post = vi.spyOn(api, 'postWithStatus').mockImplementation(async () => delayedAcceptance.promise)
  await settle()
  const accept = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Accept this exact duplicate set')
  expect(accept).toBeTruthy()
  await act(async () => { accept.click() })
  await settle()
  expect(post).toHaveBeenCalledOnce()

  await rerenderSession(964, 'review')
  await settle()
  await rerenderSession(session.id, 'review')
  await settle()
  delayedAcceptance.resolve({ status: 200, data: { accepted: true, review_digest: review.review_digest } })
  await settle()

  expect(container.textContent).not.toContain('Exact duplicate set accepted')
  expect([...container.querySelectorAll('button')].some((button) => button.textContent === 'Accept this exact duplicate set')).toBe(true)
})

it('waits for cancellation settlement before editing, then compares the corrected plan revision before continuation', async () => {
  const blocked = makeRun({
    state: 'blocked',
    can_cancel: true,
    can_resume: false,
    blocker: { code: 'scope_requires_correction', message: 'Correct the saved brief.', remedies: ['revise_brief', 'cancel'] },
  })
  let latest = blocked
  let currentPlanRevision = 3
  let currentPlan = plan
  const comparison = makeComparison(blocked, {
    run_revision: 10,
    plan_revision: 4,
    changed: true,
    changed_fields: ['plan'],
    scope_digest: 'f'.repeat(64),
    old_scope: {
      summary: { brief: 'Original brief' },
      scope_digest: 'a'.repeat(64),
      authorization_expires_at: '2030-02-01T00:00:00Z',
    },
    new_scope: {
      summary: { brief: 'Corrected brief' },
      scope_digest: 'b'.repeat(64),
      authorization_expires_at: '2030-03-01T00:00:00Z',
      renewal_lifetime_seconds: 604800,
    },
  })
  await renderSession(blocked)
  vi.mocked(api.getVersioned).mockImplementation(async (path) => {
    if (path === `/api/sessions/${session.id}/full-auto`) return { current_run: latest }
    if (path === `/api/sessions/${session.id}/plan`) return { plan_revision: currentPlanRevision, plan: currentPlan }
    if (path.endsWith('/resume-preflight')) return comparison
    return {}
  })
  const post = vi.spyOn(api, 'postWithStatus').mockImplementation(async (path, body) => {
    if (path.endsWith('/cancel')) {
      latest = makeRun({
        revision: 9,
        state: 'cancel_requested',
        cancel_intent: true,
        can_cancel: false,
        can_resume: false,
        resume_action: 'reconcile_cancellation',
        blocker: { code: 'cancel_pending', message: 'Waiting for remote work to settle.', remedies: ['reconcile_cancellation'] },
        progress: { ...makeRun().progress, unresolved_work_count: 1 },
      })
      return { status: 202, data: actionReceipt(blocked, 'cancel', body, { revision: 9, state: 'cancel_requested', phase: 'reconcile', cancel_intent: true }) }
    }
    latest = makeRun({
      revision: 11,
      plan_revision: 4,
      current_plan_revision: 4,
      state: 'active',
      cancel_intent: false,
      can_cancel: true,
      can_resume: false,
      resume_action: 'resume',
    })
    return { status: 202, data: actionReceipt(latest, 'resume', body, { revision: 11, plan_revision: 4, state: 'active', cancel_intent: false }) }
  })

  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Revise brief').click() })
  await settle()
  expect(container.querySelector('textarea[placeholder="e.g. natural soft daylight in an open loft, detailed skin texture"]')).toBeNull()
  expect(container.textContent).toContain('Manual transfer is pending.')
  expect(post).toHaveBeenCalledOnce()
  expect(post.mock.calls[0][0]).toContain('/cancel')

  latest = makeRun({
    revision: 10,
    state: 'cancelled',
    cancel_intent: true,
    can_cancel: false,
    can_resume: true,
    resume_action: 'resume',
    progress: { ...makeRun().progress, unresolved_work_count: 0 },
  })
  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Refresh status').click() })
  await settle()
  const look = [...container.querySelectorAll('textarea')].find((textarea) => textarea.value === plan.look)
  expect(look).toBeTruthy()
  expect(look.disabled).toBe(false)
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
  await act(async () => {
    setter.call(look, 'Corrected session brief and look')
    look.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await settle()
  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Save Draft').click() })
  await settle()
  const saveChanges = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Save Plan Changes')
  expect(saveChanges).toBeTruthy()
  const save = vi.spyOn(api, 'postVersioned').mockImplementation(async (_path, payload) => {
    currentPlanRevision = 4
    currentPlan = payload.plan
    return { plan_revision: 4, conflicts: [] }
  })
  await act(async () => { saveChanges.click() })
  await settle()
  expect(save).toHaveBeenCalledOnce()

  await act(async () => { [...container.querySelectorAll('button')].find((button) => button.textContent === 'Resume').click() })
  await settle()
  expect(vi.mocked(api.getVersioned).mock.calls.filter(([path]) => path.endsWith('/resume-preflight'))).toHaveLength(1)
  expect(post).toHaveBeenCalledOnce()
  expect(container.textContent).toContain('Corrected brief')
  const accept = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Accept changes and Resume')
  expect(accept).toBeTruthy()
  await act(async () => { accept.click() })
  await settle()
  expect(post).toHaveBeenCalledTimes(2)
  expect(post.mock.calls[1][1]).toMatchObject({
    expected_run_revision: '10',
    expected_plan_revision: '4',
    scope_digest: comparison.scope_digest,
    accept_changed_scope: true,
  })
})
