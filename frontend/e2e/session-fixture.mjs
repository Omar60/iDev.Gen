import { expect } from '@playwright/test'
import { test as isolated, state } from './fixtures.mjs'

const fixtureUrl = `${state.baseUrl}/__e2e/session-flow`

export const test = isolated.extend({
  reviewMode: [false, { option: true }],
  approvalMode: [false, { option: true }],
  sessionPair: async ({ request, reviewMode, approvalMode }, use) => {
    const unapproved = reviewMode || approvalMode
    const created = await request.post(fixtureUrl, {
      maxRedirects: 0,
      ...(unapproved ? { data: { [approvalMode ? 'approval' : 'review']: true } } : {}),
    })
    expect(created.status()).toBe(200)
    const { sessions } = await created.json()
    expect(sessions).toHaveLength(2)
    expect(sessions.map(({ plan_revision: revision, reviewed_revision: reviewed }) => [revision, reviewed]))
      .toEqual(unapproved ? [[1, null], [1, null]] : [[1, 1], [1, 1]])
    expect(sessions.map(({ ready_take_ids: ready, incomplete_take_ids: incomplete }) => [ready, incomplete]))
      .toEqual([
        [['take-001', 'take-002'], ['take-003']],
        [['take-001', 'take-002'], ['take-003']],
      ])
    try {
      await use(sessions)
    } finally {
      const removed = await request.delete(fixtureUrl, { maxRedirects: 0 })
      expect(removed.status()).toBe(200)
      const result = await removed.json()
      expect(result.cleaned).toBe(true)
      expect(result.counts).toEqual(result.baseline_counts)
      const idle = await snapshot(request)
      expect(idle.sessions).toEqual([])
      expect(idle.counts).toEqual(result.baseline_counts)
    }
  },
})

export async function snapshot(request) {
  const response = await request.get(fixtureUrl, { maxRedirects: 0 })
  expect(response.status()).toBe(200)
  return response.json()
}

export async function advanceReviewRevision(request, session) {
  const before = await snapshot(request)
  const response = await request.patch(fixtureUrl, {
    maxRedirects: 0, data: { session_id: session.id, expected_revision: session.plan_revision },
  })
  expect(response.status()).toBe(200)
  const current = await response.json()
  expect(current.id).toBe(session.id)
  expect(current.plan_revision).toBe(session.plan_revision + 1)
  expect(current.reviewed_revision).toBeNull()
  const after = await snapshot(request)
  expect(after.revision_controls).toEqual([{
    session_id: session.id, expected_revision: session.plan_revision, plan_revision: current.plan_revision,
  }])
  expect(after.sessions.filter(({ id }) => id !== session.id))
    .toEqual(before.sessions.filter(({ id }) => id !== session.id))
  return current
}
