import { expect } from '@playwright/test'
import { state } from './fixtures.mjs'
import { advanceReviewRevision, snapshot, test } from './session-fixture.mjs'

test.use({ reviewMode: true })

const planReview = (session) => `/api/sessions/${session.id}/plan/review?plan_revision=${session.plan_revision}`
const takeReview = (session) => `/api/sessions/${session.id}/plan/takes/take-001/review?plan_revision=${session.plan_revision}`
const takeRow = (page) => page.locator('tbody tr').filter({ has: page.getByRole('checkbox', {
  name: 'Select take take-001 for submission', exact: true,
}) })
const approval = (page, session) => page.getByRole('button', {
  name: `Approve Review (Rev ${session.plan_revision})`, exact: true,
})

async function open(page, session, ready = true) {
  if (new URL(page.url()).hash.startsWith('#/session/')) {
    // Leave Review observably before changing IDs, so only the new saved plan starts its review load.
    await page.getByRole('navigation', { name: 'Session stages' })
      .getByRole('button', { name: '3. Takes (3)', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Current stage: Takes', exact: true })).toBeVisible()
  }
  await page.goto(`${state.baseUrl}/#/session/${session.id}?step=review`)
  await expect(page.getByRole('heading', { level: 1, name: session.session.name, exact: true })).toBeVisible()
  if (ready) await expect(approval(page, session)).toBeEnabled()
}

async function inspect(page, session, audit) {
  const expand = takeRow(page).getByRole('button', { name: '▼ Review', exact: true })
  if (await expand.count()) await expand.click()
  else await expect(takeRow(page).getByRole('button', { name: '▲ Close', exact: true })).toBeVisible()
  const prepared = audit.prepared_takes.find((row) => (
    row.session_id === session.id && row.plan_revision === session.plan_revision && row.take_id === 'take-001'
  ))
  expect(prepared).toMatchObject({ status: 'ready', linked_shot_id: null })
  expect(prepared.final_prompt).toContain(session.plan.look)
  await expect(page.getByText('Authoritative Final Prompt', { exact: true })).toBeVisible()
  await expect(page.getByText(prepared.final_prompt, { exact: true })).toBeVisible()
}

async function assertReadOnly(request, before, expectedReviews) {
  const after = await snapshot(request)
  for (const key of ['sessions', 'counts', 'plans', 'prepared_takes', 'shots', 'authoring_operations', 'revision_controls']) {
    expect(after[key], key).toEqual(before[key])
  }
  expect(after.shots).toEqual([])
  expect(after.authoring_operations).toEqual([])
  expect(after.counts.approvals).toBe(after.baseline_counts.approvals)
  for (const session of after.sessions) expect(session.reviewed_revision).toBeNull()
  expect(after.requests.filter(({ method }) => method !== 'GET')).toEqual([])
  const reviews = after.requests.filter(({ path }) => path.endsWith('/review'))
  expect(reviews.map(({ method, path, query, status, payload }) => ({
    method, target: `${path}?${query}`, status, payload,
  }))).toEqual(expectedReviews.map(([target, status]) => ({ method: 'GET', target, status, payload: null })))
  const identity = await request.get(`${state.baseUrl}/__e2e/identity`, { maxRedirects: 0 })
  expect(identity.status()).toBe(200)
  expect((await identity.json()).request_denials).toEqual([])
  return after
}

async function deliver(page, barrier, path) {
  const pending = page.waitForResponse((response) => response.url() === `${state.baseUrl}${path}`)
  barrier.release()
  const response = await pending
  expect(response.status()).toBe(200)
  expect(await response.finished()).toBeNull()
  // Flush response handlers through observable browser frames, without a timer.
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))))
}

test('reviews persisted preparation and explicitly refreshes a take without writing or approving', async ({ page, request, sessionPair, egressGuard }) => {
  const [a] = sessionPair
  const before = await snapshot(request)
  const gate = egressGuard.hold({ method: 'GET', path: planReview(a), phase: 'after-response' })
  await open(page, a, false)
  const held = await gate.received
  expect(held.request.method()).toBe('GET')
  expect(held.request.postData()).toBeNull()
  const body = await held.response.json()
  expect(body).toMatchObject({ session_id: a.id, plan_revision: 1, reviewed_revision: null })
  expect(body.takes.map(({ take_id }) => take_id)).toEqual(['take-001', 'take-002', 'take-003'])
  const stored = before.prepared_takes.find(({ session_id, take_id }) => session_id === a.id && take_id === 'take-001')
  expect(body.takes[0].snapshot).toMatchObject({ id: stored.id, final_prompt: stored.final_prompt })
  await expect(approval(page, a)).toBeDisabled()
  gate.release()
  await expect(approval(page, a)).toBeEnabled()
  await inspect(page, a, before)
  const refreshed = page.waitForResponse((response) => response.url() === `${state.baseUrl}${takeReview(a)}`)
  await page.getByRole('button', { name: '↻ Refresh', exact: true }).click()
  const response = await refreshed
  expect(response.status()).toBe(200)
  expect(await response.json()).toMatchObject({ take_id: 'take-001', plan_revision: 1, reviewed_revision: null })
  await assertReadOnly(request, before, [[planReview(a), 200], [takeReview(a), 200]])
})

for (const returnBeforeDelivery of [false, true]) {
  test(`keeps independent A/B reviews when late A arrives ${returnBeforeDelivery ? 'after A-to-B-to-A' : 'on B'}`, async ({ page, request, sessionPair, egressGuard }) => {
    const [a, b] = sessionPair
    const before = await snapshot(request)
    const gate = egressGuard.hold({ method: 'GET', path: planReview(a), phase: 'after-response' })
    await open(page, a, false)
    await gate.received
    await open(page, b)
    if (returnBeforeDelivery) await open(page, a)
    const current = returnBeforeDelivery ? a : b
    await inspect(page, current, before)
    await deliver(page, gate, planReview(a))
    await expect(approval(page, current)).toBeEnabled()
    await expect(page.getByText(current.plan.look, { exact: true })).toBeVisible()
    await expect(page.getByText((returnBeforeDelivery ? b : a).plan.look, { exact: true })).toHaveCount(0)
    if (!returnBeforeDelivery) {
      await open(page, a)
      await inspect(page, a, before)
    }
    await assertReadOnly(request, before, [[planReview(a), 200], [planReview(b), 200], [planReview(a), 200]])
  })
}

for (const scope of ['plan', 'take']) {
  test(`discards a late ${scope} review after authoritative revision reload through A-to-B-to-A`, async ({ page, request, sessionPair, egressGuard }) => {
    const [a, b] = sessionPair
    const oldPath = scope === 'plan' ? planReview(a) : takeReview(a)
    if (scope === 'take') await open(page, a)
    const gate = egressGuard.hold({ method: 'GET', path: oldPath, phase: 'after-response' })
    if (scope === 'plan') await open(page, a, false)
    else {
      await takeRow(page).getByRole('button', { name: '▼ Review', exact: true }).click()
      await page.getByRole('button', { name: '↻ Refresh', exact: true }).click()
    }
    const held = await gate.received
    const old = await held.response.json()
    expect(old.plan_revision).toBe(1)
    const current = await advanceReviewRevision(request, a)
    const persisted = await snapshot(request)
    await open(page, b)
    await open(page, current)
    await inspect(page, current, persisted)
    await deliver(page, gate, oldPath)
    await expect(approval(page, current)).toBeEnabled()
    await expect(page.getByText(current.plan.look, { exact: true })).toBeVisible()
    await expect(page.getByText(a.plan.look, { exact: true })).toHaveCount(0)
    const sequence = [[planReview(a), 200]]
    if (scope === 'take') sequence.push([takeReview(a), 200])
    sequence.push([planReview(b), 200], [planReview(current), 200])
    await assertReadOnly(request, persisted, sequence)
  })
}

test('reloads Review on stage reentry while its earlier complete review is still pending', async ({ page, request, sessionPair, egressGuard }) => {
  const [a] = sessionPair
  const before = await snapshot(request)
  const gate = egressGuard.hold({ method: 'GET', path: planReview(a), phase: 'after-response' })
  await open(page, a, false)
  await gate.received
  const navigation = page.getByRole('navigation', { name: 'Session stages' })
  await navigation.getByRole('button', { name: '3. Takes (3)', exact: true }).click()
  await navigation.getByRole('button', { name: '4. Review', exact: true }).click()
  await expect(approval(page, a)).toBeEnabled()
  await deliver(page, gate, planReview(a))
  await expect(approval(page, a)).toBeEnabled()
  await inspect(page, a, before)
  await assertReadOnly(request, before, [[planReview(a), 200], [planReview(a), 200]])
})

for (const failure of ['stale revision', 'transport']) {
  test(`keeps ${failure} Review nonoperable until explicit reload, without implicit operations`, async ({ page, request, sessionPair, egressGuard }) => {
    const [a] = sessionPair
    const gate = egressGuard.hold({ method: 'GET', path: planReview(a), phase: 'before-send' })
    await open(page, a, false)
    await gate.received
    const current = failure === 'stale revision' ? await advanceReviewRevision(request, a) : a
    const before = await snapshot(request)
    gate.release(failure === 'transport' ? { abort: true } : {})
    await expect(page.getByRole('alert').filter({ hasText: 'Take Review Unavailable' })).toBeVisible()
    await expect(approval(page, a)).toBeDisabled()
    for (const button of await page.getByRole('button', { name: /^(Prepare|Submit Test Selection|Run)/ }).all()) {
      await expect(button).toBeDisabled()
    }
    const rejected = await snapshot(request)
    expect(rejected.prepared_takes).toEqual(before.prepared_takes)
    expect(rejected.counts).toEqual(before.counts)
    if (failure === 'stale revision') {
      await expect(page.getByText('Backend status: 409', { exact: true })).toBeVisible()
    }
    await page.reload()
    await expect(approval(page, current)).toBeEnabled()
    await inspect(page, current, before)
    await assertReadOnly(request, before, failure === 'stale revision'
      ? [[planReview(a), 409], [planReview(current), 200]] : [[planReview(a), 200]])
  })
}
