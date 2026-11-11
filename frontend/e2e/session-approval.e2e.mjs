import { expect } from '@playwright/test'
import { state } from './fixtures.mjs'
import { advanceReviewRevision, snapshot, test } from './session-fixture.mjs'

test.use({ approvalMode: true })

const reviewPath = (session) => `/api/sessions/${session.id}/plan/review`
const approvalPath = (session) => `/api/sessions/${session.id}/plan/review/approve`
const approval = (page, session) => page.getByRole('button', {
  name: `Approve Review (Rev ${session.plan_revision})`, exact: true,
})
const approved = (page, revision) => page.getByText(`Review Approved (Rev ${revision})`, { exact: false })

async function open(page, session) {
  if (new URL(page.url()).hash.startsWith('#/session/')) {
    const navigation = page.getByRole('navigation', { name: 'Session stages' })
    await navigation.getByRole('button', { name: '3. Takes (3)', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Current stage: Takes', exact: true })).toBeVisible()
  }
  await page.goto(`${state.baseUrl}/#/session/${session.id}?step=review`)
  await expect(page.getByRole('heading', { level: 1, name: session.session.name, exact: true })).toBeVisible()
  await expect(approval(page, session).or(approved(page, session.plan_revision))).toBeVisible()
  if (await approval(page, session).count()) await expect(approval(page, session)).toBeEnabled()
}

async function deliver(page, gate, session, status) {
  const response = page.waitForResponse((item) => (
    item.url() === `${state.baseUrl}${approvalPath(session)}`
  ))
  gate.release()
  const result = await response
  expect(result.status()).toBe(status)
  expect(await result.finished()).toBeNull()
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  return result
}

async function assertState(request, before, rows, posts, reviews) {
  const after = await snapshot(request)
  expect(after.approval_rows.map(({ session_id, plan_revision }) => [session_id, plan_revision]))
    .toEqual(rows.map(({ session_id, plan_revision }) => [session_id, plan_revision]))
  for (const row of after.approval_rows) expect(row.approved_at).toBeTruthy()
  const approvedBySession = new Map(rows.map(({ session_id, plan_revision }) => [session_id, plan_revision]))
  expect(after.sessions.map(({ id, plan_revision, reviewed_revision }) => [
    id, plan_revision, reviewed_revision,
  ])).toEqual(before.sessions.map(({ id, plan_revision }) => [
    id, plan_revision, approvedBySession.get(id) ?? null,
  ]))
  expect(after.counts).toEqual({
    ...before.counts,
    approvals: before.counts.approvals + rows.length - before.approval_rows.length,
  })
  for (const key of ['plans', 'prepared_takes', 'shots', 'authoring_operations', 'revision_controls']) {
    expect(after[key], key).toEqual(before[key])
  }
  expect(after.requests.filter(({ method }) => method === 'POST').map(({ path, status, payload }) => ({
    path, status, payload,
  }))).toEqual(posts.map(([path, status, payload]) => ({ path, status, payload })))
  expect(after.requests.filter(({ method }) => !['GET', 'POST'].includes(method))).toEqual([])
  expect(after.requests.filter(({ method, path }) => method === 'GET' && path.endsWith('/plan/review'))
    .map(({ path, query, status, payload }) => ({ path, query, status, payload })))
    .toEqual(reviews.map(([session, revision, status = 200]) => ({
      path: reviewPath(session), query: `plan_revision=${revision}`, status, payload: null,
    })))
  const identity = await request.get(`${state.baseUrl}/__e2e/identity`, { maxRedirects: 0 })
  expect(identity.status()).toBe(200)
  expect((await identity.json()).request_denials).toEqual([])
  return after
}

test('approves only after an explicit action and persists its exact revision', async ({ page, request, sessionPair }) => {
  const [a] = sessionPair
  const before = await snapshot(request)
  await open(page, a)
  const entered = await snapshot(request)
  expect(entered.approval_rows).toEqual([])
  expect(entered.requests.filter(({ method }) => method === 'POST')).toEqual([])
  expect(entered.sessions.map(({ reviewed_revision }) => reviewed_revision)).toEqual([null, null])

  const response = page.waitForResponse((item) => item.url() === `${state.baseUrl}${approvalPath(a)}`)
  await approval(page, a).click()
  const result = await response
  expect(result.status()).toBe(200)
  expect(await result.json()).toMatchObject({ session_id: a.id, plan_revision: 1, approved: true })
  await expect(approved(page, 1)).toBeVisible()
  await assertState(request, before, [{ session_id: a.id, plan_revision: 1 }], [
    [approvalPath(a), 200, { plan_revision: 1 }],
  ], [[a, 1]])
})

test('keeps a pending A approval on A through A-to-B-to-A navigation', async ({ page, request, sessionPair, egressGuard }) => {
  const [a, b] = sessionPair
  const before = await snapshot(request)
  const gate = egressGuard.hold({ method: 'POST', path: approvalPath(a), phase: 'after-response' })
  await open(page, a)
  await approval(page, a).click()
  const held = await gate.received
  expect(held.request.postDataJSON()).toEqual({ plan_revision: 1 })
  expect(held.response.status()).toBe(200)

  await open(page, b)
  await expect(approved(page, 1)).toHaveCount(0)
  await expect(approval(page, b)).toBeEnabled()
  await open(page, a)
  await expect(approved(page, 1)).toBeVisible()
  await deliver(page, gate, a, 200)
  await expect(approved(page, 1)).toBeVisible()
  const after = await assertState(request, before, [{ session_id: a.id, plan_revision: 1 }], [
    [approvalPath(a), 200, { plan_revision: 1 }],
  ], [[a, 1], [b, 1], [a, 1]])
  expect(after.sessions.find(({ id }) => id === b.id).reviewed_revision).toBeNull()
})

test('does not apply A approval to B when its response arrives after navigation', async ({ page, request, sessionPair, egressGuard }) => {
  const [a, b] = sessionPair
  const before = await snapshot(request)
  const gate = egressGuard.hold({ method: 'POST', path: approvalPath(a), phase: 'after-response' })
  await open(page, a)
  await approval(page, a).click()
  expect((await gate.received).response.status()).toBe(200)

  await open(page, b)
  await expect(approved(page, 1)).toHaveCount(0)
  await deliver(page, gate, a, 200)
  await expect(approved(page, 1)).toHaveCount(0)
  await open(page, a)
  await expect(approved(page, 1)).toBeVisible()
  const after = await assertState(request, before, [{ session_id: a.id, plan_revision: 1 }], [
    [approvalPath(a), 200, { plan_revision: 1 }],
  ], [[a, 1], [b, 1], [a, 1]])
  expect(after.sessions.find(({ id }) => id === b.id).reviewed_revision).toBeNull()
})

test('ignores a successful approval response after its plan revision changes', async ({ page, request, sessionPair, egressGuard }) => {
  const [a, b] = sessionPair
  const gate = egressGuard.hold({ method: 'POST', path: approvalPath(a), phase: 'after-response' })
  await open(page, a)
  await approval(page, a).click()
  const held = await gate.received
  expect(held.response.status()).toBe(200)

  const current = await advanceReviewRevision(request, a)
  await open(page, b)
  await open(page, current)
  await expect(approval(page, current)).toBeEnabled()
  const before = await snapshot(request)
  await deliver(page, gate, a, 200)
  await expect(approval(page, current)).toBeEnabled()
  await expect(approved(page, 1)).toHaveCount(0)
  await assertState(request, before, [], [
    [approvalPath(a), 200, { plan_revision: 1 }],
  ], [[a, 1], [b, 1], [current, 2]])
})

test('keeps the newer approval result when an older stale result arrives later', async ({ page, request, sessionPair, egressGuard }) => {
  const [a, b] = sessionPair
  const gate = egressGuard.hold({ method: 'POST', path: approvalPath(a), phase: 'before-send' })
  await open(page, a)
  await approval(page, a).click()
  const held = await gate.received
  expect(held.request.postDataJSON()).toEqual({ plan_revision: 1 })
  expect((await snapshot(request)).requests.filter(({ method }) => method === 'POST')).toEqual([])

  const current = await advanceReviewRevision(request, a)
  await open(page, b)
  await open(page, current)
  const before = await snapshot(request)
  const latest = page.waitForResponse((item) => item.url() === `${state.baseUrl}${approvalPath(current)}`)
  await approval(page, current).click()
  expect((await latest).status()).toBe(200)
  await expect(approved(page, 2)).toBeVisible()

  const older = await deliver(page, gate, a, 409)
  expect(await older.text()).toContain('requested 1')
  await expect(approved(page, 2)).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await assertState(request, before, [{ session_id: a.id, plan_revision: 2 }], [
    [approvalPath(a), 200, { plan_revision: 2 }],
    [approvalPath(a), 409, { plan_revision: 1 }],
  ], [[a, 1], [b, 1], [current, 2]])
})

test('does not update Review after an approval response arrives after unmount', async ({ page, request, sessionPair, egressGuard }) => {
  const [a] = sessionPair
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  const before = await snapshot(request)
  const gate = egressGuard.hold({ method: 'POST', path: approvalPath(a), phase: 'after-response' })
  await open(page, a)
  await approval(page, a).click()
  const held = await gate.received
  expect(held.response.status()).toBe(200)

  await page.goto(`${state.baseUrl}/#/sessions`)
  await expect(page.getByRole('heading', { level: 1, name: 'Sessions', exact: true })).toBeVisible()
  await deliver(page, gate, a, 200)
  await expect(page.getByRole('heading', { level: 1, name: 'Sessions', exact: true })).toBeVisible()
  expect(pageErrors).toEqual([])

  await open(page, a)
  await expect(approved(page, 1)).toBeVisible()
  await assertState(request, before, [{ session_id: a.id, plan_revision: 1 }], [
    [approvalPath(a), 200, { plan_revision: 1 }],
  ], [[a, 1], [a, 1]])
})

test('recovers from a stale approval only after loading the current revision', async ({ page, request, sessionPair }) => {
  const [a] = sessionPair
  await open(page, a)
  await advanceReviewRevision(request, a)
  const before = await snapshot(request)

  const stale = page.waitForResponse((item) => item.url() === `${state.baseUrl}${approvalPath(a)}`)
  await approval(page, a).click()
  expect((await stale).status()).toBe(409)
  await expect(page.getByRole('alert')).toBeVisible()
  expect((await snapshot(request)).approval_rows).toEqual([])

  await page.reload()
  const current = { ...a, plan_revision: 2 }
  await expect(approval(page, current)).toBeEnabled()
  const recovered = page.waitForResponse((item) => item.url() === `${state.baseUrl}${approvalPath(current)}`)
  await approval(page, current).click()
  expect((await recovered).status()).toBe(200)
  await expect(approved(page, 2)).toBeVisible()
  await assertState(request, before, [{ session_id: a.id, plan_revision: 2 }], [
    [approvalPath(a), 409, { plan_revision: 1 }],
    [approvalPath(current), 200, { plan_revision: 2 }],
  ], [[a, 1], [a, 2]])
})

test('recovers from approval transport failure only after an explicit retry', async ({ page, request, sessionPair, egressGuard }) => {
  const [a] = sessionPair
  const before = await snapshot(request)
  const gate = egressGuard.hold({ method: 'POST', path: approvalPath(a), phase: 'before-send' })
  await open(page, a)
  await approval(page, a).click()
  const held = await gate.received
  expect(held.request.postDataJSON()).toEqual({ plan_revision: 1 })
  gate.release({ abort: true })
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(approved(page, 1)).toHaveCount(0)
  expect((await snapshot(request)).requests.filter(({ method }) => method === 'POST')).toEqual([])
  expect(egressGuard.faults).toContainEqual({
    method: 'POST', path: approvalPath(a), action: 'abort',
  })

  const recovered = page.waitForResponse((item) => item.url() === `${state.baseUrl}${approvalPath(a)}`)
  await approval(page, a).click()
  expect((await recovered).status()).toBe(200)
  await expect(approved(page, 1)).toBeVisible()
  await assertState(request, before, [{ session_id: a.id, plan_revision: 1 }], [
    [approvalPath(a), 200, { plan_revision: 1 }],
  ], [[a, 1]])
})
