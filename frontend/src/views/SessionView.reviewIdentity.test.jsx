// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api.js'
import SessionView from './SessionView.jsx'

const takeId = 'take-001'
const revision = 4
const deferred = () => {
  let resolve, reject
  const promise = new Promise((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 0))
const settle = async () => { for (let i = 0; i < 8; i += 1) await tick() }
const session = (id) => ({
  id,
  name: `Session ${id}`,
  model_id: 4,
  model_name: 'Invented model',
  model: { id: 4, name: 'Invented model', workflow_id: 12 },
  status: 'draft',
  shot_count: 0,
  done_count: 0,
  tags: [],
  settings: { composition_mode: 'resource-v1', width: 1024, height: 1024, steps: 20, cfg: 7 },
  shots: [],
})
const plan = (id) => ({
  version: 'resource-v1',
  look: `Session ${id} look`,
  initial_wardrobe: 'Invented wardrobe',
  selected_resources: [],
  wardrobe_changes: [],
  takes: [{
    take_id: takeId,
    label: `Session ${id} take`,
    camera: '50mm portrait',
    framing: 'medium portrait',
    pose: 'standing',
    expression: 'calm',
  }],
  authoring: {
    schema_version: 1,
    mode: 'manual',
    brief: 'Invented review fixture.',
    scene_anchor: null,
    workflow_binding: null,
    variation_policy: {
      camera: { mode: 'fixed' },
      framing: { mode: 'fixed' },
      pose: { mode: 'fixed' },
      expression: { mode: 'fixed' },
    },
    shared_state: {
      look: { origin: 'user', evidence_id: null },
      initial_wardrobe: { origin: 'user', evidence_id: null },
    },
    evidence: [],
    look_snapshot: null,
    wardrobe_progression: null,
  },
})
const takeReview = (id, finalPrompt) => ({
  session_id: id,
  plan_revision: revision,
  take_id: takeId,
  composition_mode: 'resource-v1',
  ready_for_finalization: true,
  conflicts: [],
  stale_adaptations: [],
  unresolved_placeholders: [],
  final_prompt: finalPrompt,
  snapshot: {
    session_id: id,
    plan_revision: revision,
    take_id: takeId,
    status: 'ready',
    final_prompt: finalPrompt,
    effective_state: {},
    provenance: {},
  },
})
const preparation = { completed: [], incomplete: [{ take_id: takeId, status: 'missing' }], history: [] }

let container, root
const renderSession = async (id) => {
  await act(async () => {
    root.render(<SessionView
      id={id}
      initialSession={id === 41 ? session(41) : null}
      initialPlan={id === 41 ? plan(41) : null}
      initialRevision={id === 41 ? revision : null}
      initialPreparation={id === 41 ? preparation : null}
      initialActiveStep="review"
      initialExpandedTakeId={takeId}
    />)
    await settle()
  })
}
const click = async (button) => act(async () => { button.click(); await settle() })

const setup = ({ reviewed = {}, directReview = () => null, aggregateReview = () => null, approve = () => null } = {}) => {
  const gets = []
  const posts = []
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    gets.push(path)
    const sessionMatch = path.match(/^\/api\/sessions\/(\d+)$/)
    if (sessionMatch) return session(Number(sessionMatch[1]))
    const planMatch = path.match(/^\/api\/sessions\/(\d+)\/plan$/)
    if (planMatch) {
      const id = Number(planMatch[1])
      return {
        plan_revision: revision,
        reviewed_revision: reviewed[id] ?? null,
        plan: plan(id),
        preparation,
        conflicts: [],
      }
    }
    const takeMatch = path.match(/^\/api\/sessions\/(\d+)\/plan\/takes\/([^/]+)\/review(?:\?plan_revision=\d+)?$/)
    if (takeMatch) return directReview(Number(takeMatch[1]), takeMatch[2])
    const aggregateMatch = path.match(/^\/api\/sessions\/(\d+)\/plan\/review\?plan_revision=(\d+)$/)
    if (aggregateMatch) {
      const id = Number(aggregateMatch[1])
      const aggregate = await aggregateReview(id)
      return {
        session_id: id,
        plan_revision: Number(aggregateMatch[2]),
        reviewed_revision: reviewed[id] ?? null,
        takes: [aggregate || takeReview(id, `SESSION_${id}_REVIEW_PROMPT`)],
      }
    }
    if (path.endsWith('/full-auto')) return { current_run: null }
    return []
  })
  vi.spyOn(api, 'post').mockImplementation(async (path, body) => {
    posts.push([path, body])
    if (path.endsWith('/plan/review/approve')) return approve(Number(path.split('/')[3]), body)
    throw new Error(`Unexpected POST ${path}`)
  })
  return { gets, posts }
}

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  container = null
  vi.restoreAllMocks()
})

const mount = async () => {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await renderSession(41)
}

it('does not apply a late take review from A to B and reloads A review when returning', async () => {
  const lateA = deferred()
  const { gets } = setup({
    directReview: (id) => id === 41 ? lateA.promise : takeReview(id, `SESSION_${id}_DIRECT_PROMPT`),
  })
  await mount()
  expect(container.textContent).toContain('SESSION_41_REVIEW_PROMPT')
  await click(container.querySelector('button[title="Reload review data from backend"]'))
  expect(gets).toContain(`/api/sessions/41/plan/takes/${takeId}/review?plan_revision=${revision}`)

  await renderSession(42)
  expect(container.textContent).toContain('SESSION_42_REVIEW_PROMPT')
  await act(async () => { lateA.resolve(takeReview(41, 'LATE_A_ONLY_PROMPT')); await settle() })
  expect(container.textContent).toContain('SESSION_42_REVIEW_PROMPT')
  expect(container.textContent).not.toContain('LATE_A_ONLY_PROMPT')

  await renderSession(41)
  expect(container.textContent).toContain('SESSION_41_REVIEW_PROMPT')
  expect(container.textContent).not.toContain('SESSION_42_REVIEW_PROMPT')
})

it('does not apply a late plan-wide review from A after B becomes active', async () => {
  const lateA = deferred()
  let aReads = 0
  setup({ aggregateReview: (id) => {
    if (id === 41 && aReads++ === 0) return lateA.promise
    return takeReview(id, `SESSION_${id}_RELOADED_PROMPT`)
  } })
  await mount()
  await renderSession(42)
  expect(container.textContent).toContain('SESSION_42_RELOADED_PROMPT')
  await act(async () => { lateA.resolve(takeReview(41, 'LATE_A_PLAN_REVIEW')); await settle() })
  expect(container.textContent).toContain('SESSION_42_RELOADED_PROMPT')
  expect(container.textContent).not.toContain('LATE_A_PLAN_REVIEW')

  await renderSession(41)
  expect(container.textContent).toContain('SESSION_41_RELOADED_PROMPT')
})

it('does not let a late plan-wide review error block B', async () => {
  const lateA = deferred()
  setup({ aggregateReview: (id) => id === 41 ? lateA.promise : takeReview(id, `SESSION_${id}_REVIEW_PROMPT`) })
  await mount()
  await renderSession(42)
  const approveButton = [...container.querySelectorAll('button')].find((button) => button.textContent.includes('Approve Review'))
  expect(approveButton?.disabled).toBe(false)
  await act(async () => { lateA.reject(new Error('A plan review failed late')); await settle() })
  expect(container.textContent).not.toContain('A plan review failed late')
  expect(container.textContent).toContain('SESSION_42_REVIEW_PROMPT')
  expect([...container.querySelectorAll('button')].find((button) => button.textContent.includes('Approve Review'))?.disabled).toBe(false)
})

it('does not surface a late take review error on another session', async () => {
  const lateA = deferred()
  setup({ directReview: (id) => id === 41 ? lateA.promise : takeReview(id, `SESSION_${id}_DIRECT_PROMPT`) })
  await mount()
  await click(container.querySelector('button[title="Reload review data from backend"]'))
  await renderSession(42)
  await act(async () => { lateA.reject(new Error('A review failed late')); await settle() })
  expect(container.textContent).not.toContain('A review failed late')
  expect(container.textContent).toContain('SESSION_42_REVIEW_PROMPT')

  await renderSession(41)
  expect(container.textContent).toContain('SESSION_41_REVIEW_PROMPT')
})

it('keeps the newest take review when requests for the same session resolve out of order', async () => {
  const older = deferred()
  const newer = deferred()
  let reads = 0
  setup({ directReview: (id) => {
    if (id !== 41) return takeReview(id, `SESSION_${id}_DIRECT_PROMPT`)
    reads += 1
    return reads === 1 ? older.promise : newer.promise
  } })
  await mount()
  const refresh = container.querySelector('button[title="Reload review data from backend"]')
  await click(refresh)
  await click(refresh)
  await act(async () => { newer.resolve(takeReview(41, 'NEWER_REVIEW_PROMPT')); await settle() })
  expect(container.textContent).toContain('NEWER_REVIEW_PROMPT')
  await act(async () => { older.resolve(takeReview(41, 'OLDER_REVIEW_PROMPT')); await settle() })
  expect(container.textContent).toContain('NEWER_REVIEW_PROMPT')
  expect(container.textContent).not.toContain('OLDER_REVIEW_PROMPT')
})

it('does not show A approval in B and restores A approval from A authoritative state', async () => {
  const pendingApproval = deferred()
  const reviewed = {}
  const { posts } = setup({ reviewed, approve: (id, body) => pendingApproval.promise.then((response) => {
    reviewed[id] = body.plan_revision
    return response
  }) })
  await mount()
  const approveButton = [...container.querySelectorAll('button')].find((button) => button.textContent.includes('Approve Review'))
  expect(approveButton?.disabled).toBe(false)
  await click(approveButton)
  expect(posts).toEqual([[`/api/sessions/41/plan/review/approve`, { plan_revision: revision }]])

  await renderSession(42)
  expect(container.textContent).toContain('SESSION_42_REVIEW_PROMPT')
  expect(container.textContent).not.toContain('Review Approved')
  await act(async () => { pendingApproval.resolve({ plan_revision: revision, reviewed: true }); await settle() })
  expect(container.textContent).not.toContain('Review Approved')
  expect([...container.querySelectorAll('button')].some((button) => button.textContent.includes('Approve Review'))).toBe(true)
  expect(posts).toHaveLength(1)

  await renderSession(41)
  expect(container.textContent).toContain(`Review Approved (Rev ${revision})`)
  expect(posts).toHaveLength(1)
})

it('keeps a pending A approval associated with A through A-to-B-to-A navigation', async () => {
  const pendingApproval = deferred()
  const reviewed = {}
  const { posts } = setup({ reviewed, approve: (id, body) => pendingApproval.promise.then((response) => {
    reviewed[id] = body.plan_revision
    return response
  }) })
  await mount()
  const approveButton = [...container.querySelectorAll('button')].find((button) => button.textContent.includes('Approve Review'))
  await click(approveButton)
  await renderSession(42)
  expect(container.textContent).not.toContain('Review Approved')
  await renderSession(41)
  await act(async () => { pendingApproval.resolve({ plan_revision: revision, approved: true }); await settle() })
  expect(container.textContent).toContain(`Review Approved (Rev ${revision})`)
  expect(posts).toEqual([[`/api/sessions/41/plan/review/approve`, { plan_revision: revision }]])
})

it('ignores an older approval response after a newer explicit approval completes', async () => {
  const older = deferred()
  const newer = deferred()
  let approvals = 0
  const { posts } = setup({ approve: () => (++approvals === 1 ? older.promise : newer.promise) })
  await mount()
  const approve = () => [...container.querySelectorAll('button')]
    .find((button) => button.textContent.includes('Approve Review'))
  await click(approve())
  await click(approve())
  await act(async () => { newer.resolve({ plan_revision: revision, approved: true }); await settle() })
  expect(container.textContent).toContain(`Review Approved (Rev ${revision})`)
  await act(async () => { older.reject(new Error('Older approval failed')); await settle() })
  expect(container.textContent).toContain(`Review Approved (Rev ${revision})`)
  expect(container.textContent).not.toContain('Older approval failed')
  expect(posts).toEqual([
    [`/api/sessions/41/plan/review/approve`, { plan_revision: revision }],
    [`/api/sessions/41/plan/review/approve`, { plan_revision: revision }],
  ])
})

it('does not surface a late approval error on another session', async () => {
  const pendingApproval = deferred()
  const { posts } = setup({ approve: () => pendingApproval.promise })
  await mount()
  const approveButton = [...container.querySelectorAll('button')].find((button) => button.textContent.includes('Approve Review'))
  await click(approveButton)
  await renderSession(42)
  await act(async () => { pendingApproval.reject(new Error('A approval failed late')); await settle() })
  expect(container.textContent).not.toContain('A approval failed late')
  expect(container.textContent).toContain('SESSION_42_REVIEW_PROMPT')
  expect(posts).toHaveLength(1)
  await renderSession(41)
  expect(container.textContent).toContain('A approval failed late')
})
