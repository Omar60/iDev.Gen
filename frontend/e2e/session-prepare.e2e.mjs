import { expect } from '@playwright/test'
import { state } from './fixtures.mjs'
import { snapshot, test } from './session-fixture.mjs'

const sessionPath = (id) => `/api/sessions/${id}`
const planPath = (id) => `${sessionPath(id)}/plan`
const submitPath = (id) => `${planPath(id)}/preparations/submit-selected`
const preparePath = (id, takeId) => `${planPath(id)}/takes/${takeId}/prepare`
const writes = (audit) => audit.requests.filter(({ method }) => method !== 'GET')
const target = ({ path, query }) => `${path}${query ? `?${query}` : ''}`
const takeCheckbox = (page, takeId) => page.getByRole('checkbox', {
  name: `Select take ${takeId} for submission`, exact: true,
})
const takeRow = (page, takeId) => page.locator('tbody tr').filter({ has: takeCheckbox(page, takeId) })

async function openReview(page, session) {
  await page.goto(`${state.baseUrl}/#/session/${session.id}?step=review`)
  await expect(page.getByRole('heading', { level: 1, name: session.session.name, exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '4. Review', exact: true })).toBeVisible()
  await expect(takeRow(page, 'take-003').getByRole('button', { name: 'Prepare', exact: true })).toBeEnabled()
}

function awaitDeliveredGet(page, path) {
  return page.waitForResponse((response) => (
    response.url() === `${state.baseUrl}${path}` && response.request().method() === 'GET'
  ))
}

async function expectNonoperable(page) {
  await expect(page.getByRole('group', { name: 'Session actions' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Prepare/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Submit Test Selection/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Approve Review/ })).toHaveCount(0)
}

async function expectNoWrites(request) {
  const audit = await snapshot(request)
  expect(writes(audit)).toEqual([])
  await expectNoDeniedRequests(request)
  return audit
}

async function expectNoDeniedRequests(request) {
  const identity = await request.get(`${state.baseUrl}/__e2e/identity`, { maxRedirects: 0 })
  expect(identity.status()).toBe(200)
  expect((await identity.json()).request_denials).toEqual([])
}

const submitWrite = (id) => ({
  method: 'POST', path: submitPath(id), status: 200,
  payload: { plan_revision: 1, take_ids: ['take-001'] },
})

async function submitFirstTake(page, request, session, egressGuard, blockedGetPath) {
  await openReview(page, session)
  const baseline = await expectNoWrites(request)
  const blockedGet = egressGuard.hold({ method: 'GET', path: blockedGetPath, phase: 'before-send' })
  await takeCheckbox(page, 'take-001').check()
  const submitPending = page.waitForResponse((response) => (
    response.url() === `${state.baseUrl}${submitPath(session.id)}`
    && response.request().method() === 'POST'
  ))
  await page.getByRole('button', { name: 'Submit Test Selection (1)', exact: true }).click()
  const submitResponse = await submitPending
  expect(submitResponse.status()).toBe(200)
  expect(await submitResponse.finished()).toBeNull()
  const heldGet = await blockedGet.received
  expect(heldGet.request.method()).toBe('GET')
  return { baseline, blockedGet, heldGet }
}

async function expectOneSubmittedShot(audit, baseline, session, request) {
  expect(writes(audit)).toMatchObject([submitWrite(session.id)])
  expect(writes(audit)).toHaveLength(1)
  const prepared = audit.prepared_takes.filter(({ session_id }) => session_id === session.id)
  expect(prepared).toHaveLength(2)
  const generated = prepared.find(({ take_id }) => take_id === 'take-001')
  expect(generated).toMatchObject({ plan_revision: 1, status: 'generated' })
  expect(generated.linked_shot_id).not.toBeNull()
  expect(prepared.find(({ take_id }) => take_id === 'take-002')).toMatchObject({
    plan_revision: 1, status: 'ready', linked_shot_id: null,
  })
  const linkedShot = audit.shots.find(({ id }) => String(id) === String(generated.linked_shot_id))
  expect(linkedShot).toMatchObject({ session_id: session.id, status: 'pending' })
  const plan = audit.plans.find(({ session_id }) => session_id === session.id)
  expect(plan.plan_revision).toBe(1)
  expect(JSON.parse(plan.plan_json).takes.map(({ take_id }) => take_id))
    .toEqual(session.plan.takes.map(({ take_id }) => take_id))
  for (const key of ['models', 'sessions', 'workflows', 'plans', 'approvals', 'prepared_takes', 'authoring_operations']) {
    expect(audit.counts[key]).toBe(baseline.counts[key])
  }
  expect(audit.counts.shots).toBe(baseline.counts.shots + 1)
  await expectNoDeniedRequests(request)
  return generated
}

async function expectDisabledOrAbsent(locator) {
  if (await locator.count()) await expect(locator).toBeDisabled()
}

test('reconciles submit against the latest reload while preserving only the current ready selection', async ({ page, request, sessionPair, egressGuard }) => {
  const [session] = sessionPair
  await openReview(page, session)
  await expect(page.getByText('Each operation is explicit. Changing stages does not prepare, approve, submit, or run anything.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Approve Review (Rev 1)', exact: true })).toHaveCount(0)
  const baseline = await expectNoWrites(request)

  await takeCheckbox(page, 'take-001').check()
  await expect(page.getByRole('button', { name: 'Submit Test Selection (1)', exact: true })).toBeEnabled()
  expect((await expectNoWrites(request)).counts).toEqual(baseline.counts)

  const oldReload = egressGuard.hold({ method: 'GET', path: planPath(session.id), phase: 'after-response' })
  const latestReload = egressGuard.hold({ method: 'GET', path: planPath(session.id), phase: 'after-response' })
  await page.getByRole('button', { name: 'Submit Test Selection (1)', exact: true }).click()

  const oldHeld = await oldReload.received
  expect(oldHeld.request.method()).toBe('GET')
  expect(new URL(oldHeld.request.url()).pathname).toBe(planPath(session.id))
  expect(oldHeld.response.status()).toBe(200)

  await takeCheckbox(page, 'take-002').check()
  await takeCheckbox(page, 'take-001').check()
  await expect(page.getByRole('button', { name: 'Submitting…', exact: true })).toBeDisabled()

  const prepareResponse = page.waitForResponse((response) => (
    response.url() === `${state.baseUrl}${preparePath(session.id, 'take-003')}`
    && response.request().method() === 'POST'
  ))
  await takeRow(page, 'take-003').getByRole('button', { name: 'Prepare', exact: true }).click()
  expect((await prepareResponse).status()).toBe(200)

  const latestHeld = await latestReload.received
  expect(latestHeld.request.method()).toBe('GET')
  expect(new URL(latestHeld.request.url()).pathname).toBe(planPath(session.id))
  expect(latestHeld.response.status()).toBe(200)

  const committed = await snapshot(request)
  const reads = committed.requests.filter(({ method }) => method === 'GET').map(target)
  expect(reads).toContain(`${planPath(session.id)}/review?plan_revision=1`)
  expect(reads).toContain(`${planPath(session.id)}/takes/take-003/review?plan_revision=1`)
  const sessionPlans = committed.plans.filter(({ session_id }) => session_id === session.id)
  expect(sessionPlans).toHaveLength(1)
  expect(sessionPlans[0].plan_revision).toBe(session.plan_revision)
  const storedPlan = JSON.parse(sessionPlans[0].plan_json)
  expect(storedPlan.takes.map(({ take_id }) => take_id)).toEqual(session.plan.takes.map(({ take_id }) => take_id))

  const prepared = committed.prepared_takes.filter(({ session_id }) => session_id === session.id)
  expect(prepared).toHaveLength(3)
  const generated = prepared.find(({ take_id }) => take_id === 'take-001')
  expect(generated).toMatchObject({ plan_revision: 1, status: 'generated' })
  expect(generated.linked_shot_id).not.toBeNull()
  expect(prepared.find(({ take_id }) => take_id === 'take-002')).toMatchObject({
    plan_revision: 1, status: 'ready', linked_shot_id: null,
  })
  expect(prepared.find(({ take_id }) => take_id === 'take-003')).toMatchObject({
    plan_revision: 1, status: 'ready', linked_shot_id: null,
  })
  const linkedShot = committed.shots.find(({ id }) => String(id) === String(generated.linked_shot_id))
  expect(linkedShot).toMatchObject({ session_id: session.id, status: 'pending' })

  const expectedWrites = [
    {
      method: 'POST', path: submitPath(session.id), status: 200,
      payload: { plan_revision: 1, take_ids: ['take-001'] },
    },
    {
      method: 'POST', path: preparePath(session.id, 'take-003'), status: 200,
      payload: { plan_revision: 1, manual_completion: null, adaptations: null },
    },
  ]
  expect(writes(committed)).toHaveLength(expectedWrites.length)
  expect(writes(committed)).toMatchObject(expectedWrites)

  for (const key of ['models', 'sessions', 'workflows', 'plans', 'approvals', 'authoring_operations']) {
    expect(committed.counts[key]).toBe(baseline.counts[key])
  }
  expect(committed.counts.prepared_takes).toBe(baseline.counts.prepared_takes + 1)
  expect(committed.counts.shots).toBe(baseline.counts.shots + 1)

  const oldDeliveredPending = awaitDeliveredGet(page, planPath(session.id))
  oldReload.release()
  const oldDelivered = await oldDeliveredPending
  expect(oldDelivered.status()).toBe(200)
  expect(await oldDelivered.finished()).toBeNull()
  await expect(page.getByRole('button', { name: 'Submitting…', exact: true })).toBeDisabled()

  const latestDeliveredPending = awaitDeliveredGet(page, planPath(session.id))
  latestReload.release()
  const latestDelivered = await latestDeliveredPending
  expect(latestDelivered.status()).toBe(200)
  expect(await latestDelivered.finished()).toBeNull()

  await expect(page.getByRole('heading', { name: 'Current stage: Generation', exact: true })).toBeVisible()
  await page.getByRole('navigation', { name: 'Session stages' })
    .getByRole('button', { name: '4. Review', exact: true }).click()
  await expect(page.getByRole('heading', { name: '4. Review', exact: true })).toBeVisible()
  await expect(takeRow(page, 'take-001')).toContainText(`Generated [Shot #${generated.linked_shot_id}]`)
  await expect(takeRow(page, 'take-002')).toContainText('Ready')
  await expect(takeRow(page, 'take-003')).toContainText('Ready')
  await expect(takeCheckbox(page, 'take-001')).not.toBeChecked()
  await expect(takeCheckbox(page, 'take-002')).toBeChecked()
  await expect(page.getByRole('button', { name: 'Submit Test Selection (1)', exact: true })).toBeEnabled()

  const final = await snapshot(request)
  expect(writes(final)).toMatchObject(expectedWrites)
  expect(final.counts).toEqual(committed.counts)
  expect(final.prepared_takes.filter(({ session_id }) => session_id === session.id)).toEqual(prepared)
  expect(final.shots.filter(({ session_id }) => session_id === session.id)).toEqual(
    committed.shots.filter(({ session_id }) => session_id === session.id),
  )
  await expectNoDeniedRequests(request)
})

test('keeps a failed session GET nonoperable until the user retries loading it', async ({ page, request, sessionPair, egressGuard }) => {
  const [session] = sessionPair
  const { baseline, blockedGet, heldGet } = await submitFirstTake(
    page, request, session, egressGuard, sessionPath(session.id),
  )
  expect(new URL(heldGet.request.url()).pathname).toBe(sessionPath(session.id))
  const failedRequest = page.waitForEvent('requestfailed', (failed) => failed.url() === `${state.baseUrl}${sessionPath(session.id)}`)
  blockedGet.release({ abort: true })
  await failedRequest
  await expect(page.getByRole('button', { name: 'Retry loading session', exact: true })).toBeVisible()
  await expectNonoperable(page)
  expect(egressGuard.faults).toMatchObject([{ method: 'GET', path: sessionPath(session.id), action: 'abort' }])
  const committed = await snapshot(request)
  const generated = await expectOneSubmittedShot(committed, baseline, session, request)

  await page.getByRole('button', { name: 'Retry loading session', exact: true }).click()
  await expect(page.getByRole('heading', { level: 1, name: session.session.name, exact: true })).toBeVisible()
  await expect(takeRow(page, 'take-003').getByRole('button', { name: 'Prepare', exact: true })).toBeEnabled()
  const recovered = await snapshot(request)
  const stillGenerated = await expectOneSubmittedShot(recovered, baseline, session, request)
  expect(stillGenerated.linked_shot_id).toBe(generated.linked_shot_id)
})

test('rejects a real other-session response and recovers the requested session only after retry', async ({ page, request, sessionPair, egressGuard }) => {
  const [session, otherSession] = sessionPair
  const { baseline, blockedGet, heldGet } = await submitFirstTake(
    page, request, session, egressGuard, sessionPath(session.id),
  )
  expect(new URL(heldGet.request.url()).pathname).toBe(sessionPath(session.id))
  const delivered = awaitDeliveredGet(page, sessionPath(session.id))
  blockedGet.release({ responsePath: sessionPath(otherSession.id) })
  const mismatchedResponse = await delivered
  expect(mismatchedResponse.status()).toBe(200)
  expect(await mismatchedResponse.finished()).toBeNull()

  await expect(page.getByRole('alert')).toContainText('The session response did not match the requested session.')
  await expect(page.getByRole('button', { name: 'Retry loading session', exact: true })).toBeVisible()
  await expectNonoperable(page)
  expect(egressGuard.faults).toMatchObject([{
    method: 'GET', path: sessionPath(session.id), action: 'response',
    responsePath: sessionPath(otherSession.id), status: 200,
  }])
  const committed = await snapshot(request)
  const generated = await expectOneSubmittedShot(committed, baseline, session, request)

  await page.getByRole('button', { name: 'Retry loading session', exact: true }).click()
  await expect(page.getByRole('heading', { level: 1, name: session.session.name, exact: true })).toBeVisible()
  await expect(takeRow(page, 'take-003').getByRole('button', { name: 'Prepare', exact: true })).toBeEnabled()
  const recovered = await snapshot(request)
  const stillGenerated = await expectOneSubmittedShot(recovered, baseline, session, request)
  expect(stillGenerated.linked_shot_id).toBe(generated.linked_shot_id)
})

test('keeps a failed plan GET nonoperable and recovers after explicit browser reload', async ({ page, request, sessionPair, egressGuard }) => {
  const [session] = sessionPair
  const { baseline, blockedGet, heldGet } = await submitFirstTake(
    page, request, session, egressGuard, planPath(session.id),
  )
  expect(new URL(heldGet.request.url()).pathname).toBe(planPath(session.id))
  const failedRequest = page.waitForEvent('requestfailed', (failed) => failed.url() === `${state.baseUrl}${planPath(session.id)}`)
  blockedGet.release({ abort: true })
  await failedRequest
  await expect(page.getByRole('heading', { name: 'Session stages unavailable', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { level: 1, name: session.session.name, exact: true })).toBeVisible()
  await expectDisabledOrAbsent(page.getByRole('button', { name: /Prepare/ }))
  await expectDisabledOrAbsent(page.getByRole('button', { name: /Submit Test Selection/ }))
  await expectDisabledOrAbsent(page.getByRole('button', { name: /Approve Review/ }))
  await expectDisabledOrAbsent(page.getByRole('button', { name: /Run/ }))
  expect(egressGuard.faults).toMatchObject([{ method: 'GET', path: planPath(session.id), action: 'abort' }])
  const committed = await snapshot(request)
  const generated = await expectOneSubmittedShot(committed, baseline, session, request)

  await page.reload()
  await expect(page.getByRole('heading', { level: 1, name: session.session.name, exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '4. Review', exact: true })).toBeVisible()
  await expect(takeRow(page, 'take-003').getByRole('button', { name: 'Prepare', exact: true })).toBeEnabled()
  const recovered = await snapshot(request)
  const stillGenerated = await expectOneSubmittedShot(recovered, baseline, session, request)
  expect(stillGenerated.linked_shot_id).toBe(generated.linked_shot_id)
})
