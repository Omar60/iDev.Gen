import { expect } from '@playwright/test'
import { test, snapshot } from './session-fixture.mjs'
import { state } from './fixtures.mjs'

const sessionPath = (id) => `/api/sessions/${id}`
const planPath = (id) => `${sessionPath(id)}/plan`
const submitPath = (id) => `${planPath(id)}/preparations/submit-selected`
const reviewStage = (page) => page.getByRole('heading', { name: 'Current stage: Review', exact: true })
const generatedTakeCheckbox = (page, takeId) => (
  page.getByRole('checkbox', { name: `Select take ${takeId} for submission`, exact: true })
)

async function openReview(page, session) {
  await page.goto(`${state.baseUrl}/#/session/${session.id}?step=review`)
  await expect(reviewStage(page)).toBeVisible()
  await expect(page.getByText(`✓ Review Approved (Rev ${session.plan_revision})`, { exact: true })).toBeVisible()
}

async function selectTakes(page, takeIds) {
  for (const takeId of takeIds) await generatedTakeCheckbox(page, takeId).check()
  await expect(page.getByRole('button', {
    name: `Submit Test Selection (${takeIds.length})`, exact: true,
  })).toBeEnabled()
}

async function submitUntilHeldRead(page, egressGuard, session, takeId, readPath) {
  const postGate = egressGuard.hold({
    method: 'POST', path: submitPath(session.id), phase: 'before-send',
  })
  await page.getByRole('button', { name: 'Submit Test Selection (1)', exact: true }).click()
  const post = await postGate.received
  expect(post.request.postDataJSON()).toEqual({
    plan_revision: session.plan_revision,
    take_ids: [takeId],
  })

  const readBarrier = egressGuard.hold({ method: 'GET', path: readPath, phase: 'after-response' })
  postGate.release()
  const read = await readBarrier.received
  expect(read.response.status()).toBe(200)
  return readBarrier
}

async function returnToReview(page) {
  await page.getByRole('button', { name: '4. Review', exact: true }).click()
  await expect(reviewStage(page)).toBeVisible()
}

async function releaseAndWaitForSubmit(page, barrier, session) {
  const delivered = page.waitForResponse((response) => (
    response.url() === `${state.baseUrl}${submitPath(session.id)}`
    && response.request().method() === 'POST'
  ))
  barrier.release()
  const response = await delivered
  expect(response.status()).toBe(200)
  await response.finished()
  await page.evaluate(() => new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve))
  }))
}

function submittedRequests(stateSnapshot, session) {
  return stateSnapshot.requests.filter((entry) => (
    entry.method === 'POST' && entry.path === submitPath(session.id)
  ))
}

function expectSubmittedRequest(stateSnapshot, session, takeIds) {
  expect(submittedRequests(stateSnapshot, session)).toHaveLength(1)
  expect(submittedRequests(stateSnapshot, session)[0]).toMatchObject({
    method: 'POST',
    path: submitPath(session.id),
    status: 200,
    payload: { plan_revision: session.plan_revision, take_ids: takeIds },
  })
}

function expectOnlyShotCountsChanged(before, after, shotDelta) {
  expect(after.baseline_counts).toEqual(before.baseline_counts)
  for (const key of ['models', 'sessions', 'workflows', 'plans', 'approvals', 'prepared_takes', 'authoring_operations']) {
    expect(after.counts[key], `${key} count`).toBe(before.counts[key])
  }
  expect(after.counts.shots).toBe(before.counts.shots + shotDelta)
}

function expectGeneratedLinks(stateSnapshot, session, takeIds) {
  const prepared = stateSnapshot.prepared_takes.filter((row) => (
    row.session_id === session.id
    && row.plan_revision === session.plan_revision
    && takeIds.includes(row.take_id)
  ))
  expect(prepared).toHaveLength(takeIds.length)
  for (const row of prepared) {
    expect(row.status).toBe('generated')
    expect(row.linked_shot_id).not.toBeNull()
    const shot = stateSnapshot.shots.find((candidate) => candidate.id === row.linked_shot_id)
    expect(shot).toMatchObject({ session_id: session.id, status: 'pending' })
    expect(shot.prompt).toBeTruthy()
  }
}

async function expectGeneratedTakeUI(page, stateSnapshot, session, takeId) {
  const prepared = stateSnapshot.prepared_takes.find((row) => (
    row.session_id === session.id
    && row.plan_revision === session.plan_revision
    && row.take_id === takeId
  ))
  const checkbox = generatedTakeCheckbox(page, takeId)
  await expect(checkbox).toBeDisabled()
  await expect(page.getByRole('row').filter({ has: checkbox }))
    .toContainText(`Generated [Shot #${prepared.linked_shot_id}]`)
}

function expectReadySelectionAfterSubmit(page, submittedTakeId, remainingTakeId) {
  return Promise.all([
    expect(generatedTakeCheckbox(page, submittedTakeId)).toBeDisabled(),
    expect(generatedTakeCheckbox(page, remainingTakeId)).toBeChecked(),
    expect(page.getByRole('button', { name: 'Submit Test Selection (1)', exact: true })).toBeEnabled(),
  ])
}

test('submits a double-clicked ready selection once and persists its pending shot link', async ({ page, request, sessionPair, egressGuard }) => {
  const [a] = sessionPair
  const takeId = a.ready_take_ids[0]
  await openReview(page, a)
  const before = await snapshot(request)
  await selectTakes(page, [takeId])

  const barrier = egressGuard.hold({ method: 'POST', path: submitPath(a.id), phase: 'after-response' })
  await page.getByRole('button', { name: 'Submit Test Selection (1)', exact: true }).dblclick()
  const received = await barrier.received
  expect(received.request.postDataJSON()).toEqual({ plan_revision: a.plan_revision, take_ids: [takeId] })
  expect(received.response.status()).toBe(200)
  await expect(page.getByRole('button', { name: 'Submitting…', exact: true })).toBeDisabled()
  await expect(reviewStage(page)).toBeVisible()

  const committed = await snapshot(request)
  expectSubmittedRequest(committed, a, [takeId])
  expectOnlyShotCountsChanged(before, committed, 1)
  expectGeneratedLinks(committed, a, [takeId])
  barrier.release()

  await expect(page.getByRole('heading', { name: 'Current stage: Generation', exact: true })).toBeVisible()
  await returnToReview(page)
  const final = await snapshot(request)
  await expectGeneratedTakeUI(page, final, a, takeId)
  expect(final.counts.shots).toBe(before.counts.shots + 1)
})

test('keeps B review state unchanged when A submit commits and its response arrives late', async ({ page, request, sessionPair, egressGuard }) => {
  const [a, b] = sessionPair
  const aTake = a.ready_take_ids[0]
  await openReview(page, a)
  const before = await snapshot(request)
  await selectTakes(page, [aTake])

  const aBarrier = egressGuard.hold({ method: 'POST', path: submitPath(a.id), phase: 'after-response' })
  await page.getByRole('button', { name: 'Submit Test Selection (1)', exact: true }).click()
  const aReceived = await aBarrier.received
  expect(aReceived.response.status()).toBe(200)
  await expect(page.getByRole('button', { name: 'Submitting…', exact: true })).toBeDisabled()
  const committedA = await snapshot(request)
  expectSubmittedRequest(committedA, a, [aTake])
  expectGeneratedLinks(committedA, a, [aTake])

  await openReview(page, b)
  await selectTakes(page, b.ready_take_ids)
  await page.getByRole('button', { name: 'Submit Test Selection (2)', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Current stage: Generation', exact: true })).toBeVisible()
  const bNavigation = page.getByRole('region', { name: 'Resource session navigation' })
  await expect(bNavigation.getByRole('status')).toContainText('Submitted 2 take(s) to queue')
  await returnToReview(page)
  for (const takeId of b.ready_take_ids) {
    await expect(generatedTakeCheckbox(page, takeId)).toBeDisabled()
    await expect(generatedTakeCheckbox(page, takeId)).not.toBeChecked()
  }
  const bNotice = await bNavigation.getByRole('status').textContent()

  await releaseAndWaitForSubmit(page, aBarrier, a)
  await expect(reviewStage(page)).toBeVisible()
  for (const takeId of b.ready_take_ids) {
    await expect(generatedTakeCheckbox(page, takeId)).toBeDisabled()
    await expect(generatedTakeCheckbox(page, takeId)).not.toBeChecked()
  }
  await expect(bNavigation.getByRole('status')).toHaveText(bNotice)
  await expect(bNavigation.getByRole('status')).toContainText('Submitted 2 take(s) to queue')

  const after = await snapshot(request)
  expectSubmittedRequest(after, a, [aTake])
  expectSubmittedRequest(after, b, b.ready_take_ids)
  expectOnlyShotCountsChanged(before, after, 3)
  expectGeneratedLinks(after, a, [aTake])
  expectGeneratedLinks(after, b, b.ready_take_ids)

  await openReview(page, a)
  await expectGeneratedTakeUI(page, after, a, aTake)
  await expect(generatedTakeCheckbox(page, a.ready_take_ids[1])).not.toBeChecked()
})

test('reconciles A after returning A-to-B-to-A before the committed Submit response arrives', async ({ page, request, sessionPair, egressGuard }) => {
  const [a, b] = sessionPair
  const aTake = a.ready_take_ids[0]
  await openReview(page, a)
  const before = await snapshot(request)
  await selectTakes(page, [aTake])

  const aBarrier = egressGuard.hold({ method: 'POST', path: submitPath(a.id), phase: 'after-response' })
  await page.getByRole('button', { name: 'Submit Test Selection (1)', exact: true }).click()
  const aReceived = await aBarrier.received
  expect(aReceived.response.status()).toBe(200)

  await openReview(page, b)
  await selectTakes(page, b.ready_take_ids)
  await page.getByRole('button', { name: 'Submit Test Selection (2)', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Current stage: Generation', exact: true })).toBeVisible()
  await returnToReview(page)

  await openReview(page, a)
  await expect(page.getByRole('button', { name: 'Submitting…', exact: true })).toBeDisabled()
  await releaseAndWaitForSubmit(page, aBarrier, a)
  await expect(reviewStage(page)).toBeVisible()

  const after = await snapshot(request)
  expectSubmittedRequest(after, a, [aTake])
  expectSubmittedRequest(after, b, b.ready_take_ids)
  expectOnlyShotCountsChanged(before, after, 3)
  expectGeneratedLinks(after, a, [aTake])
  expectGeneratedLinks(after, b, b.ready_take_ids)
  await expectGeneratedTakeUI(page, after, a, aTake)
  await expect(page.getByRole('button', { name: 'Submitting…', exact: true })).toHaveCount(0)
})

for (const heldRead of ['session', 'plan']) {
  test(`preserves a new ready selection while Submit waits for ${heldRead} reconciliation`, async ({ page, request, sessionPair, egressGuard }) => {
    const [a] = sessionPair
    const [submittedTakeId, remainingTakeId] = a.ready_take_ids
    await openReview(page, a)
    const before = await snapshot(request)
    await selectTakes(page, [submittedTakeId])

    const path = heldRead === 'session' ? sessionPath(a.id) : planPath(a.id)
    const barrier = await submitUntilHeldRead(page, egressGuard, a, submittedTakeId, path)
    await expect(page.getByRole('button', { name: 'Submitting…', exact: true })).toBeDisabled()
    await generatedTakeCheckbox(page, remainingTakeId).check()
    await expect(page.getByRole('checkbox', { name: `Select take ${remainingTakeId} for submission`, exact: true })).toBeChecked()

    if (heldRead === 'session') {
      const waiting = await snapshot(request)
      expect(waiting.requests.filter((entry) => entry.method === 'GET' && entry.path === planPath(a.id)))
        .toHaveLength(before.requests.filter((entry) => entry.method === 'GET' && entry.path === planPath(a.id)).length)
    }

    barrier.release()
    await expect(page.getByRole('heading', { name: 'Current stage: Generation', exact: true })).toBeVisible()
    await returnToReview(page)

    const reconciled = await snapshot(request)
    expectSubmittedRequest(reconciled, a, [submittedTakeId])
    expectOnlyShotCountsChanged(before, reconciled, 1)
    expectGeneratedLinks(reconciled, a, [submittedTakeId])
    await expectReadySelectionAfterSubmit(page, submittedTakeId, remainingTakeId)
    await expectGeneratedTakeUI(page, reconciled, a, submittedTakeId)
    const remaining = reconciled.prepared_takes.find((row) => (
      row.session_id === a.id && row.plan_revision === a.plan_revision && row.take_id === remainingTakeId
    ))
    expect(remaining).toMatchObject({ status: 'ready', linked_shot_id: null })
  })
}
