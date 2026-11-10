import { expect } from '@playwright/test'
import { test as isolated, state } from './fixtures.mjs'

const fixtureUrl = `${state.baseUrl}/__e2e/model-detail`

const test = isolated.extend({
  modelPair: async ({ request }, use) => {
    const created = await request.post(fixtureUrl, { maxRedirects: 0 })
    expect(created.status()).toBe(200)
    const { models } = await created.json()
    expect(models).toHaveLength(2)
    expect(models.map((model) => model.name)).toEqual(['E2E Model A', 'E2E Model B'])
    expect(models[0].id).not.toBe(models[1].id)
    try {
      await use(models)
    } finally {
      const removed = await request.delete(fixtureUrl, { maxRedirects: 0 })
      expect(removed.status()).toBe(200)
      expect((await removed.json()).models).toEqual([])
      const identity = await request.get(`${state.baseUrl}/__e2e/identity`, { maxRedirects: 0 })
      expect(identity.status()).toBe(200)
      expect(await identity.json()).toMatchObject({
        server_pid: state.pid, server_nonce: state.nonce, root_id: state.rootId,
        database_path_owned: true, seeded_models: 1, seeded_sessions: 1,
        backend_socket_denials: [], request_denials: [],
      })
    }
  },
})

const modelForm = (page) => page.getByRole('form', { name: 'Edit model', exact: true })
const modelName = (page) => modelForm(page).getByRole('textbox', { name: 'Name', exact: true })

async function openModel(page, model) {
  await page.goto(`${state.baseUrl}/#/model/${model.id}`)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(model.name)
  await expect(page.getByText(`Model #${model.id}`, { exact: true })).toBeVisible()
}

async function snapshot(request) {
  const response = await request.get(fixtureUrl, { maxRedirects: 0 })
  expect(response.status()).toBe(200)
  return response.json()
}

test('keeps independent model and Legacy drafts through A-to-B-to-A and browser history', async ({ page, request, modelPair }) => {
  const [a, b] = modelPair
  for (const model of [a, b]) {
    await openModel(page, model)
    await page.getByRole('button', { name: 'Edit model', exact: true }).click()
    await modelName(page).fill(`${model.name} draft`)
    await page.getByRole('button', { name: 'Legacy session', exact: true }).click()
    await page.locator('#legacy-session-form').getByRole('textbox', { name: 'Name', exact: true }).fill(`Draft for ${model.id}`)
  }
  await openModel(page, a)
  const verifyDraft = async (model) => {
    await expect(modelName(page)).toHaveValue(`${model.name} draft`)
    await expect(page.locator('#legacy-session-form').getByRole('textbox', { name: 'Name', exact: true })).toHaveValue(`Draft for ${model.id}`)
  }
  await verifyDraft(a)
  await page.goBack()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(b.name)
  await verifyDraft(b)
  await page.goBack()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(a.name)
  await verifyDraft(a)
  await page.goForward()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(b.name)
  await verifyDraft(b)
  await page.goForward()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(a.name)
  await verifyDraft(a)
  expect(await snapshot(request)).toEqual({ models: [a, b], operations: [] })
})

for (const releaseOn of ['B', 'A after a route cycle']) {
  test(`keeps newer A edits and B isolated when a committed PATCH arrives on ${releaseOn}`, async ({ page, request, modelPair, egressGuard }) => {
    const [a, b] = modelPair
    await openModel(page, b)
    await page.getByRole('button', { name: 'Edit model', exact: true }).click()
    await modelName(page).fill('Independent B draft')
    await openModel(page, a)
    await page.getByRole('button', { name: 'Edit model', exact: true }).click()
    await modelName(page).fill('Submitted A name')

    const path = `/api/models/${a.id}`
    const barrier = egressGuard.holdPatch(path)
    await modelForm(page).getByRole('button', { name: 'Save model', exact: true }).click()
    const held = await barrier.received
    expect(held.response.status()).toBe(200)
    expect(await held.response.json()).toEqual({ ok: true })
    expect(held.request.postDataJSON()).toEqual({ ...a, name: 'Submitted A name' })
    await modelName(page).fill('Newer unsaved A name')
    await openModel(page, b)
    await expect(modelName(page)).toHaveValue('Independent B draft')
    await modelName(page).fill('Newer independent B draft')

    // The real handler has committed; only delivery of its real response is held.
    const committed = await snapshot(request)
    expect(committed.models).toEqual([{ ...a, name: 'Submitted A name' }, b])
    expect(committed.operations).toEqual([
      { method: 'PATCH', path, payload: { ...a, name: 'Submitted A name' }, status: 200 },
    ])
    if (releaseOn !== 'B') {
      await page.goBack()
      await expect(page.getByRole('heading', { level: 1 })).toHaveText('Submitted A name')
      await expect(modelName(page)).toHaveValue('Newer unsaved A name')
    }
    barrier.release()
    await expect(modelForm(page).getByRole('button', { name: 'Save model', exact: true })).toBeEnabled()
    if (releaseOn === 'B') {
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(b.name)
      await expect(modelName(page)).toHaveValue('Newer independent B draft')
      await expect(page.getByText('Submitted A name', { exact: true })).toHaveCount(0)
      await page.goBack()
      await expect(page.getByRole('heading', { level: 1 })).toHaveText('Submitted A name')
    }
    await expect(modelName(page)).toHaveValue('Newer unsaved A name')
    expect(await snapshot(request)).toEqual(committed)

    const saved = page.waitForResponse((response) => response.url() === `${state.baseUrl}${path}` && response.request().method() === 'PATCH')
    await modelForm(page).getByRole('button', { name: 'Save model', exact: true }).click()
    expect((await saved).status()).toBe(200)
    await expect(modelForm(page)).toHaveCount(0)
    const final = await snapshot(request)
    expect(final.models).toEqual([{ ...a, name: 'Newer unsaved A name' }, b])
    expect(final.operations).toEqual([
      ...committed.operations,
      { method: 'PATCH', path, payload: { ...a, name: 'Newer unsaved A name' }, status: 200 },
    ])
    await page.goForward()
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(b.name)
    await expect(modelName(page)).toHaveValue('Newer independent B draft')
  })
}
