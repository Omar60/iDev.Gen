// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api.js'
import Library from './Library.jsx'
import SessionView from './SessionView.jsx'

const sessionId = 8505
const revision = 4
const digest = 'a'.repeat(64)
const takeId = (index) => `take-${String(index).padStart(3, '0')}`

const resourceSession = {
  id: sessionId,
  name: 'Invented plan projection session',
  model_id: 4,
  model_name: 'Invented character model',
  model: { id: 4, name: 'Invented character model', trigger: 'invented trigger', workflow_id: 12 },
  status: 'draft',
  shot_count: 0,
  done_count: 0,
  cover_shot_id: null,
  tags: [],
  settings: {
    composition_mode: 'resource-v1',
    width: 1024,
    height: 1024,
    steps: 20,
    cfg: 7,
    lora_strength: 1,
    checkpoint: 'invented-base.safetensors',
  },
  look: 'Plan-owned look after CAS.',
  wardrobe: 'Plan-owned initial wardrobe after CAS.',
  diagnostic: null,
  shots: [],
}

const legacySession = {
  id: sessionId + 1,
  name: 'Invented legacy session',
  model_name: 'Invented character model',
  look: 'Legacy look stays searchable.',
  wardrobe: 'Legacy wardrobe stays searchable.',
  shot_count: 2,
  done_count: 1,
  tags: [],
}

const brokenResourceSession = {
  ...resourceSession,
  id: sessionId + 2,
  name: 'Invented inconsistent resource session',
  look: null,
  wardrobe: null,
  diagnostic: {
    code: 'resource_plan_missing',
    message: 'Resource-v1 plan is missing; look and wardrobe are unavailable.',
  },
}

function makePlan({ mode = 'automatic', emptyShared = false } = {}) {
  const look = emptyShared ? '' : 'Plan-owned look after CAS.'
  const initialWardrobe = emptyShared ? '' : 'Plan-owned initial wardrobe after CAS.'
  return {
    version: 'resource-v1',
    look,
    initial_wardrobe: initialWardrobe,
    selected_resources: [{ library_key: 'rooms_task85', source_id: 'scene-001', content_digest: digest }],
    wardrobe_changes: [{ take_id: takeId(2), scope: 'from_here', wardrobe: 'A changed wardrobe from take two.' }],
    takes: [
      {
        take_id: takeId(1),
        label: 'First invented take',
        camera: '85mm at eye level',
        framing: 'medium portrait',
        pose: 'standing beside a window',
        expression: 'calm expression',
      },
      {
        take_id: takeId(2),
        label: 'Second invented take',
        camera: emptyShared ? '' : '35mm from below',
        framing: 'wide portrait',
        pose: emptyShared ? '' : 'seated by a table',
        expression: 'direct gaze',
      },
      {
        take_id: takeId(3),
        label: 'Third invented take',
        camera: '50mm front view',
        framing: 'full portrait',
        pose: 'leaning against a wall',
        expression: 'small smile',
      },
    ],
    authoring: {
      schema_version: 1,
      mode,
      brief: 'An invented concise brief.',
      scene_anchor: { library_key: 'rooms_task85', source_id: 'scene-001', content_digest: digest },
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
        look: { origin: emptyShared ? 'user' : 'user', evidence_id: null },
        initial_wardrobe: { origin: 'user', evidence_id: null },
      },
      evidence: [],
      look_snapshot: null,
      wardrobe_progression: null,
    },
  }
}

const writerSynthesis = {
  version: 1,
  kind: 'assistant',
  requested_fields: ['camera', 'pose'],
  writer_input: {
    request: { take_id: takeId(1), plan_revision: revision },
    assistant_request: {
      messages: [
        { role: 'system', content: 'Invented writer system evidence.' },
        { role: 'user', content: 'WRITER_REQUEST_EVIDENCE_SENTINEL' },
      ],
      model: 'invented-writer-model',
      parameters: { temperature: 0 },
    },
  },
  writer_output: { camera: 'WRITER_OUTPUT_CAMERA_SENTINEL', pose: 'WRITER_OUTPUT_POSE_SENTINEL' },
}

function reviewFor(plan, id, { generated = false, empty = false } = {}) {
  const take = plan.takes.find((item) => item.take_id === id)
  const effectiveState = {
    look: empty ? '' : plan.look,
    initial_wardrobe: empty ? '' : plan.initial_wardrobe,
    wardrobe: empty ? '' : (id === takeId(1) ? plan.initial_wardrobe : 'A changed wardrobe from take two.'),
    take_choices: {
      camera: empty ? '' : take.camera,
      framing: take.framing,
      pose: empty ? '' : take.pose,
      expression: take.expression,
    },
  }
  const provenance = {
    selected_resource_revisions: [
      { library_key: 'rooms_task85', source_id: 'scene-001', content_digest: digest, kind: 'rooms' },
    ],
    writer_synthesis: writerSynthesis,
    authoring_evidence: {
      writer_synthesis: writerSynthesis,
      duplicate_flags: { flags: [{ take_id: takeId(2), plan_revision: revision }] },
    },
  }
  const snapshot = {
    session_id: sessionId,
    plan_revision: revision,
    take_id: id,
    status: generated ? 'generated' : 'ready',
    linked_shot_id: generated ? 884 : null,
    final_prompt: generated ? 'HISTORICAL_PROMPT_SENTINEL' : 'AUTHORITATIVE_FINAL_PROMPT_SENTINEL',
    effective_state: effectiveState,
    provenance,
    compiler_version: 'resource-v1-test-compiler',
    mapping_version: 'resource-v1-test-mapping',
    created_at: '2026-10-02T12:00:00Z',
  }
  return {
    take_id: id,
    session_id: sessionId,
    plan_revision: revision,
    composition_mode: 'resource-v1',
    effective_state: effectiveState,
    selected_resource_revisions: provenance.selected_resource_revisions,
    fused_descriptions: [],
    conflicts: [],
    resolved_conflicts: [],
    adaptations: [],
    stale_adaptations: [],
    unresolved_placeholders: [],
    ready_for_finalization: true,
    snapshot,
    final_prompt: snapshot.final_prompt,
    compiler_version: snapshot.compiler_version,
    mapping_version: snapshot.mapping_version,
  }
}

function makePreparation(plan, { generated = [takeId(3)], ready = [takeId(1), takeId(2)] } = {}) {
  const completed = [
    ...ready.map((id) => ({ take_id: id, status: 'ready', linked_shot_id: null })),
    ...generated.map((id) => ({ take_id: id, status: 'generated', linked_shot_id: 884 })),
  ]
  const done = new Set(completed.map((item) => item.take_id))
  return {
    completed,
    incomplete: plan.takes.filter((take) => !done.has(take.take_id)).map((take) => ({ take_id: take.take_id, status: 'missing' })),
    history: [],
  }
}

function makeSharedSummary(plan) {
  return {
    available: true,
    look: { value: plan.look, origin: plan.authoring.shared_state.look.origin },
    initial_wardrobe: { value: plan.initial_wardrobe, origin: plan.authoring.shared_state.initial_wardrobe.origin },
  }
}

function createHarness({
  plan = makePlan(),
  preparation = makePreparation(plan),
  session = resourceSession,
  planError = null,
  reviewError = null,
  refreshResult = null,
  refreshGate = null,
  llmOkay = false,
} = {}) {
  const state = { revision, planGets: 0, reviewGets: 0, requests: [] }
  const historicalIds = new Set((preparation.history || []).map((item) => item.take_id))
  const completedById = new Map((preparation.completed || []).map((item) => [item.take_id, item]))
  const reviewTakes = plan.takes.map((take) => {
    const current = completedById.get(take.take_id)
    const review = reviewFor(plan, take.take_id, { generated: current?.status === 'generated' })
    if (historicalIds.has(take.take_id) && !current) {
      return { ...review, snapshot: null, final_prompt: 'CURRENT_REVISION_REVIEW_PROMPT' }
    }
    return review
  })

  vi.spyOn(api, 'get').mockImplementation(async (url) => {
    if (url === `/api/sessions/${sessionId}`) return structuredClone(session)
    if (url === `/api/sessions/${sessionId}/plan`) {
      state.planGets += 1
      if (planError) throw planError
      return {
        plan_revision: state.revision,
        plan: structuredClone(plan),
        conflicts: [],
        preparation: structuredClone(preparation),
        shared_summary: makeSharedSummary(plan),
        reviewed_revision: null,
      }
    }
    if (url === `/api/sessions/${sessionId}/plan/review?plan_revision=${state.revision}`) {
      state.reviewGets += 1
      if (reviewError) throw reviewError
      return { session_id: sessionId, plan_revision: state.revision, reviewed_revision: null, takes: structuredClone(reviewTakes) }
    }
    if (url.startsWith(`/api/sessions/${sessionId}/plan/takes/`) && url.includes('/review')) {
      const id = url.split(`/api/sessions/${sessionId}/plan/takes/`)[1].split('/review')[0]
      return structuredClone(reviewTakes.find((item) => item.take_id === id))
    }
    if (url === '/api/workflows' || url === '/api/sessions' || url === '/api/comfy/models') return []
    if (url === '/api/config') return { llm_ok: llmOkay }
    return []
  })

  vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
    state.requests.push([url, structuredClone(body)])
    if (url.endsWith('/plan/refresh-resources')) {
      if (refreshGate) {
        const result = await refreshGate
        if (result) state.revision = result.plan_revision
        return structuredClone(result)
      }
      if (refreshResult) {
        state.revision = refreshResult.plan_revision
        return structuredClone(refreshResult)
      }
      return { plan_revision: state.revision, refreshed: false, affected_takes: [], required_preparation: [], copied_forward_takes: [], diagnostics: [] }
    }
    if (url.endsWith('/plan/review/approve')) return { plan_revision: body.plan_revision, reviewed: true }
    if (url.endsWith('/plan/preparations/submit-selected')) return { submitted: body.take_ids }
    if (url.endsWith('/plan/authoring/operations')) {
      return {
        can_cancel: true,
        can_resume: false,
        created_at: '2026-10-02T12:00:00Z',
        updated_at: '2026-10-02T12:00:00Z',
        error: null,
        kind: body.kind,
        lease_expires_at: null,
        operation_id: 'invented-active-operation',
        plan_revision: body.expected_revision,
        progress: { completed: [], failed: null, remaining: [...body.take_ids], requested: [...body.take_ids] },
        result: null,
        session_id: sessionId,
        state: 'active',
      }
    }
    if (url === `/api/sessions/${sessionId}/plan`) {
      state.revision += 1
      return { plan_revision: state.revision, conflicts: [] }
    }
    throw new Error(`Unexpected POST ${url}`)
  })

  return state
}

let container = null
let root = null

beforeEach(() => {
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
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  window.location.hash = ''
  vi.restoreAllMocks()
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

async function drain() {
  await act(async () => {
    for (let index = 0; index < 24; index += 1) await Promise.resolve()
  })
}

async function renderSession(step = 'review') {
  await act(async () => {
    root.render(<SessionView id={sessionId} initialActiveStep={step} />)
  })
  await drain()
}

async function click(button) {
  expect(button).toBeTruthy()
  await act(async () => { button.click() })
  await drain()
}

function buttonMatching(pattern, scope = container) {
  return Array.from(scope.querySelectorAll('button'))
    .find((button) => pattern.test(button.textContent.trim()))
}

function changeValue(element, value) {
  const prototype = element instanceof HTMLTextAreaElement
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value)
  element.dispatchEvent(new Event('input', { bubbles: true }))
  element.dispatchEvent(new Event('change', { bubbles: true }))
}

describe('Task 8.5 independent acceptance probes', () => {
  it('shows plan-projected look and wardrobe on session cards, keeps legacy values, and surfaces missing-plan diagnostics', async () => {
    const calls = []
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      calls.push(url)
      if (url.includes('?q=')) {
        if (url.includes('Plan-owned')) return [resourceSession]
        if (url.includes('Legacy')) return [legacySession]
        return []
      }
      return [resourceSession, legacySession, brokenResourceSession]
    })

    await act(async () => { root.render(<Library />) })
    await drain()
    expect(container.textContent).toContain('Plan-owned look after CAS.')
    expect(container.textContent).toContain('Plan-owned initial wardrobe after CAS.')
    expect(container.textContent).toContain('Invented legacy session')
    expect(container.textContent).toContain('Resource-v1 plan is missing')
    expect(container.textContent).not.toContain('Resource-v1 plan is missing; look and wardrobe are unavailable. Plan-owned')

    const search = container.querySelector('input[placeholder="Search by name, look or wardrobe"]')
    expect(search).toBeTruthy()
    await act(async () => {
      changeValue(search, 'Plan-owned')
      await new Promise((resolve) => setTimeout(resolve, 300))
    })
    await drain()
    expect(calls.some((url) => url.includes('/api/sessions?q=Plan-owned'))).toBe(true)
    expect(container.textContent).toContain('Plan-owned look after CAS.')

    await act(async () => {
      changeValue(search, 'Legacy')
      await new Promise((resolve) => setTimeout(resolve, 300))
    })
    await drain()
    expect(calls.some((url) => url.includes('/api/sessions?q=Legacy'))).toBe(true)
    expect(container.textContent).toContain('Invented legacy session')
    expect(container.textContent).not.toContain('Plan-owned look after CAS.')
  })

  it('renders effective values and saved writer evidence while keeping generated history out of test submission', async () => {
    const plan = makePlan({ emptyShared: true })
    const state = createHarness({
      plan,
      session: { ...resourceSession, look: '', wardrobe: '' },
    })
    await renderSession('review')

    expect(container.textContent).toContain('Review')
    const firstRow = Array.from(container.querySelectorAll('tbody tr'))
      .find((row) => row.textContent.includes(takeId(1)))
    expect(firstRow?.textContent).toContain('85mm at eye level')
    expect(firstRow?.textContent).toContain('medium portrait')
    expect(firstRow?.textContent).toContain('standing beside a window')
    expect(firstRow?.textContent).toContain('calm expression')
    const secondRow = Array.from(container.querySelectorAll('tbody tr'))
      .find((row) => row.textContent.includes(takeId(2)))
    expect(secondRow?.querySelectorAll('td')[3]?.textContent).toContain('(empty in prepared snapshot)')
    expect(secondRow?.querySelectorAll('td')[5]?.textContent).toContain('(empty in prepared snapshot)')
    expect(secondRow?.querySelectorAll('td')[4]?.textContent).toContain('wide portrait')
    expect(secondRow?.querySelectorAll('td')[6]?.textContent).toContain('direct gaze')
    expect(container.textContent).toContain('Generated [Shot #884]')
    expect(container.textContent).not.toContain('Plan-owned look after CAS.')
    expect(container.textContent).not.toContain('Plan-owned initial wardrobe after CAS.')
    expect(state.requests).toEqual([])

    await click(buttonMatching(/▼ Review/))
    expect(container.textContent).toContain('Effective Wardrobe')
    expect(container.textContent).toContain('(empty in prepared snapshot: no wardrobe description)')
    expect(container.textContent).toContain('Effective Look')
    expect(container.textContent).toContain('(empty in prepared snapshot: no additional look constraint)')
    expect(container.textContent).toContain('AUTHORITATIVE_FINAL_PROMPT_SENTINEL')
    await click(Array.from(container.querySelectorAll('summary'))
      .find((summary) => summary.textContent.includes('Inspect authoring evidence and writer request/output')))
    expect(container.textContent).toContain('WRITER_REQUEST_EVIDENCE_SENTINEL')
    expect(container.textContent).toContain('WRITER_OUTPUT_CAMERA_SENTINEL')
    expect(container.textContent).toContain('rooms_task85:scene-001')
    expect(container.textContent).toContain('Exact Duplicate Choices (1)')
    expect(state.requests).toEqual([])

    const rows = Array.from(container.querySelectorAll('tbody tr')).filter((row) => row.textContent.includes(takeId(3)))
    expect(rows[0]?.textContent).toContain('Generated [Shot #884]')
    const generatedCheckbox = rows[0]?.querySelector('input[type="checkbox"]')
    expect(generatedCheckbox?.disabled).toBe(true)
  })

  it('requires the user to approve review and explicitly submit a selected ready take', async () => {
    const state = createHarness()
    await renderSession('review')

    expect(state.requests).toEqual([])
    const submitButton = buttonMatching(/Submit Test Selection \(0\)/)
    expect(submitButton?.disabled).toBe(true)

    await click(buttonMatching(/Approve Review/))
    expect(state.requests.map(([url]) => url)).toEqual([
      `/api/sessions/${sessionId}/plan/review/approve`,
    ])

    const readyRow = Array.from(container.querySelectorAll('tbody tr'))
      .find((row) => row.textContent.includes(takeId(1)))
    const readyCheckbox = readyRow?.querySelector('input[type="checkbox"]')
    expect(readyCheckbox?.disabled).toBe(false)
    await click(readyCheckbox)
    await click(buttonMatching(/Submit Test Selection \(1\)/))

    expect(state.requests.map(([url]) => url)).toEqual([
      `/api/sessions/${sessionId}/plan/review/approve`,
      `/api/sessions/${sessionId}/plan/preparations/submit-selected`,
    ])
    expect(state.requests[1][1]).toEqual({ plan_revision: revision, take_ids: [takeId(1)] })
    expect(state.requests.some(([url]) => url === `/api/sessions/${sessionId}/run`)).toBe(false)
  })

  it('does not claim drift from a failed review read; explicit CAS refresh reports affected work without starting downstream actions', async () => {
    const plan = makePlan()
    const reviewError = Object.assign(new Error('Prepared take evidence could not be verified.'), {
      status: 409,
      detail: 'authoring_evidence_invalid: Prepared authoring evidence is invalid.',
    })
    const state = createHarness({
      plan,
      reviewError,
      refreshResult: {
        plan_revision: revision + 1,
        refreshed: true,
        affected_takes: [takeId(1), takeId(2)],
        required_preparation: [takeId(1), takeId(2)],
        copied_forward_takes: [],
        diagnostics: [
          { take_id: takeId(1), code: 'resource_dependency_changed' },
          { take_id: takeId(2), code: 'automatic_downstream' },
        ],
      },
    })
    await renderSession('review')

    expect(state.reviewGets).toBe(1)
    expect(container.textContent).toContain('Take Review Unavailable')
    expect(container.textContent).toContain('Resource drift is not confirmed')
    expect(container.textContent).not.toMatch(/resource drift (confirmed|detected)/i)
    expect(state.requests).toEqual([])

    await click(buttonMatching(/refresh.*resource|resource.*refresh/i))
    expect(state.requests).toEqual([[
      `/api/sessions/${sessionId}/plan/refresh-resources`,
      { expected_revision: revision },
    ]])
    expect(container.textContent).toContain(takeId(1))
    expect(container.textContent).toContain(takeId(2))
    expect(state.requests.some(([url]) => /authoring\/operations|review\/approve|submit-selected|\/run$/.test(url))).toBe(false)
  })

  it('keeps an unsaved draft when an explicit resource refresh resolves during editing', async () => {
    let resolveRefresh
    const refreshGate = new Promise((resolve) => { resolveRefresh = resolve })
    const plan = makePlan()
    const reviewError = Object.assign(new Error('Prepared take evidence could not be verified.'), {
      status: 409,
      detail: 'authoring_evidence_invalid: Prepared authoring evidence is invalid.',
    })
    const state = createHarness({ plan, reviewError, refreshGate })
    await renderSession('review')

    await click(buttonMatching(/refresh.*resource|resource.*refresh/i))
    await click(buttonMatching(/^2\. Scene \/ Constants$/))
    const look = Array.from(container.querySelectorAll('textarea'))
      .find((textarea) => textarea.value === 'Plan-owned look after CAS.')
    expect(look).toBeTruthy()
    await act(async () => {
      changeValue(look, 'Unsaved look retained across refresh.')
      await Promise.resolve()
    })
    await drain()

    await act(async () => {
      resolveRefresh({
        plan_revision: revision + 1,
        refreshed: true,
        affected_takes: [takeId(1)],
        required_preparation: [takeId(1)],
        copied_forward_takes: [],
        diagnostics: [{ take_id: takeId(1), code: 'resource_dependency_changed' }],
      })
      await new Promise((resolve) => setTimeout(resolve, 0))
      await drain()
    })

    expect(Array.from(container.querySelectorAll('textarea'))
      .some((textarea) => textarea.value === 'Unsaved look retained across refresh.')).toBe(true)
    expect(state.requests.map(([url]) => url)).toEqual([
      `/api/sessions/${sessionId}/plan/refresh-resources`,
    ])
    expect(container.textContent).toContain('unsaved edits')
  })

  it('shows the backend missing-plan diagnostic on session detail without substituting legacy constants', async () => {
    const session = {
      ...brokenResourceSession,
      look: null,
      wardrobe: null,
    }
    const error = Object.assign(new Error('no plan draft for this session'), { status: 404 })
    const state = createHarness({ session, planError: error })
    await renderSession('constants')

    expect(container.textContent).toContain('Resource-v1 plan is missing')
    expect(container.textContent).toContain('look and wardrobe are unavailable')
    expect(container.textContent).not.toContain('Plan-owned look after CAS.')
    expect(container.textContent).not.toContain('Plan-owned initial wardrobe after CAS.')
    expect(state.requests).toEqual([])
  })

  it('shows exact downstream impact before saving an automatic edit and writes only after explicit confirmation', async () => {
    const plan = makePlan()
    const preparation = makePreparation(plan, { generated: [takeId(3)], ready: [takeId(1), takeId(2)] })
    const state = createHarness({ plan, preparation })
    await renderSession('takes')
    await click(buttonMatching(/^3\. Takes/))
    await click(container.querySelector('summary'))

    const camera = Array.from(container.querySelectorAll('input')).find((input) => input.value === '85mm at eye level')
    expect(camera).toBeTruthy()
    await act(async () => {
      changeValue(camera, '90mm at eye level')
      await Promise.resolve()
    })
    await drain()

    await click(buttonMatching(/^Save Draft$/))
    const dialog = container.querySelector('[role="dialog"]')
    expect(dialog).toBeTruthy()
    expect(dialog.textContent).toMatch(/downstream|re-?prepar|affected/i)
    expect(dialog.textContent).toContain(takeId(2))
    expect(dialog.textContent).toContain('Generated or shot-linked history retained')
    expect(dialog.textContent).toContain(takeId(3))
    expect(state.requests).toEqual([])

    await click(buttonMatching(/^Save Plan Changes$/))
    expect(state.requests).toHaveLength(1)
    expect(state.requests[0][0]).toBe(`/api/sessions/${sessionId}/plan`)
    expect(state.requests[0][1].expected_revision).toBe(revision)
    expect(state.requests[0][1].plan.takes[0].camera).toBe('90mm at eye level')
    expect(state.requests.some(([url]) => /authoring\/operations|review\/approve|submit-selected|\/run$/.test(url))).toBe(false)
  })

  it.each(['edit', 'remove', 'reorder', 'global'])(
    'warns about ungenerated downstream work before save for a %s change',
    async (change) => {
      const plan = makePlan()
      const preparation = makePreparation(plan)
      const state = createHarness({ plan, preparation })
      await renderSession(change === 'global' ? 'constants' : 'takes')

      if (change === 'edit') {
        await click(container.querySelector('summary'))
        const camera = Array.from(container.querySelectorAll('input')).find((input) => input.value === '85mm at eye level')
        expect(camera).toBeTruthy()
        await act(async () => {
          changeValue(camera, '90mm at eye level')
          await Promise.resolve()
        })
      } else if (change === 'remove') {
        await click(container.querySelector('button[title="Remove take"]'))
      } else if (change === 'reorder') {
        await click(container.querySelector('button[title="Move take down"]'))
      } else {
        const look = Array.from(container.querySelectorAll('textarea'))
          .find((textarea) => textarea.value === 'Plan-owned look after CAS.')
        expect(look).toBeTruthy()
        await act(async () => {
          changeValue(look, 'A new plan-owned look.')
          await Promise.resolve()
        })
      }
      await drain()

      await click(buttonMatching(/^Save Draft$/))
      const dialog = container.querySelector('[role="dialog"]')
      expect(dialog).toBeTruthy()
      const warningText = dialog.textContent
      expect(warningText).toMatch(/downstream|re-?prepar|affected/i)
      expect(warningText).toContain(takeId(2))
      expect(warningText).toContain('Generated or shot-linked history retained')
      expect(warningText).toContain(takeId(3))
      expect(state.requests).toEqual([])
    },
  )

  it('does not tell the user a previously linked take needs preparation after a later-revision edit', async () => {
    const plan = makePlan()
    const preparation = {
      completed: [],
      incomplete: plan.takes.map((take) => ({ take_id: take.take_id, status: 'missing' })),
      history: [{
        take_id: takeId(1),
        plan_revision: revision - 1,
        status: 'generated',
        linked_shot_id: 884,
        final_prompt: 'Historical prompt remains immutable.',
      }],
    }
    const state = createHarness({ plan, preparation })
    await renderSession('takes')
    await click(buttonMatching(/^3\. Takes/))
    await click(container.querySelector('summary'))
    const camera = Array.from(container.querySelectorAll('input'))
      .find((input) => input.value === '85mm at eye level')
    expect(camera).toBeTruthy()
    await act(async () => {
      changeValue(camera, '90mm at eye level')
      await Promise.resolve()
    })
    await drain()

    await click(buttonMatching(/^Save Draft$/))
    const dialog = container.querySelector('[role="dialog"]')
    expect(dialog.textContent).toContain('Generated or shot-linked history retained')
    expect(dialog.textContent).toContain(takeId(1))
    const pendingLine = Array.from(dialog.querySelectorAll('div'))
      .map((element) => element.textContent)
      .find((text) => text.startsWith('Pending or incomplete affected takes:'))
    expect(pendingLine).not.toContain(takeId(1))
    expect(state.requests).toEqual([])
  })

  it('keeps prior linked history visibly submitted from its snapshot and omits it from automatic targets', async () => {
    const oldPlan = makePlan()
    const plan = structuredClone(oldPlan)
    plan.takes[0].camera = '90mm changed in current plan.'
    const oldReview = reviewFor(oldPlan, takeId(1), { generated: true })
    const preparation = {
      completed: [{ take_id: takeId(3), status: 'ready' }],
      incomplete: [
        { take_id: takeId(1), status: 'missing' },
        { take_id: takeId(2), status: 'missing' },
      ],
      history: [{
        ...oldReview.snapshot,
        plan_revision: revision - 1,
        status: 'generated',
        linked_shot_id: 884,
      }],
    }
    const state = createHarness({ plan, preparation, llmOkay: true })
    await renderSession('review')

    const historicalRow = Array.from(container.querySelectorAll('tbody tr'))
      .find((row) => row.textContent.includes(takeId(1)))
    expect(historicalRow?.querySelectorAll('td')[3]?.textContent).toContain('85mm at eye level')
    expect(historicalRow?.querySelectorAll('td')[3]?.textContent).not.toContain('90mm changed in current plan.')
    expect(historicalRow?.textContent).toMatch(/generated|submitted|historical/i)
    expect(historicalRow?.textContent).toContain('884')

    const prepareButton = buttonMatching(/(?:Prepare \d+ take\(s\)|Continue preparation \(\d+\))/)
    expect(prepareButton).toBeTruthy()
    await click(prepareButton)
    expect(state.requests).toContainEqual([
      `/api/sessions/${sessionId}/plan/authoring/operations`,
      expect.objectContaining({
        expected_revision: revision,
        kind: 'prepare_takes',
        take_ids: [takeId(2)],
      }),
    ])
    expect(state.requests.some(([, body]) => body?.take_ids?.includes(takeId(1)))).toBe(false)
  })
})
