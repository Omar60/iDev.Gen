// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, parseVersionJson, versionText } from '../api.js'
import { executeSavePlan, loadSessionPlan } from '../sessionPlan.js'
import Looks from './Looks.jsx'
import SessionView from './SessionView.jsx'
import SessionLookProgression from './SessionLookProgression.jsx'

const SQLITE_MAX = '9223372036854775807'
const MAX_SAFE = '9007199254740991'
const wardrobe = { garments: [], outfits: [] }

let root
let container

const response = (text, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status === 200 ? 'OK' : 'Error',
  text: vi.fn(async () => text),
  json: vi.fn(async () => JSON.parse(text)),
})

const flush = async () => act(async () => {
  for (let index = 0; index < 16; index += 1) await Promise.resolve()
})

const mount = async (element) => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(element))
  await flush()
  return container
}

const click = async (element) => {
  expect(element).toBeTruthy()
  await act(async () => element.click())
  await flush()
}

const setValue = async (element, value) => {
  expect(element).toBeTruthy()
  await act(async () => {
    const descriptor = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), 'value')
    if (descriptor?.set) descriptor.set.call(element, String(value))
    else element.value = String(value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
    element.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await flush()
}

const button = (label) => Array.from(container.querySelectorAll('button'))
  .find((item) => item.textContent.trim() === label || item.getAttribute('aria-label') === label)

const lookDetail = (version) => ({
  key: 'look-boundary',
  version,
  name: 'Boundary look',
  content_digest: 'a'.repeat(64),
  appearance: 'short dark hair',
  outfit: null,
})

const planBeforeLook = () => ({
  version: 'resource-v1',
  look: 'Existing appearance',
  initial_wardrobe: 'Existing clothing',
  selected_resources: [],
  takes: [{ take_id: 'take-001', label: 'Take 1', camera: '', framing: '', pose: 'Original pose', expression: '' }],
  wardrobe_changes: [],
  authoring: {
    schema_version: 1,
    mode: 'manual',
    shared_state: {
      look: { origin: 'user', evidence_id: null },
      initial_wardrobe: { origin: 'user', evidence_id: null },
    },
    look_snapshot: null,
    wardrobe_progression: null,
  },
})

const authoritativePlanWire = (pose) => `{"plan_revision":5,"plan":{"version":"resource-v1","look":"short dark hair","initial_wardrobe":"Existing clothing","selected_resources":[],"takes":[{"take_id":"take-001","label":"Take 1","camera":"","framing":"","pose":${JSON.stringify(pose)},"expression":""}],"wardrobe_changes":[],"authoring":{"schema_version":1,"mode":"manual","shared_state":{"look":{"origin":"saved_look","evidence_id":null},"initial_wardrobe":{"origin":"user","evidence_id":null}},"look_snapshot":{"look_id":"look-boundary","version":${SQLITE_MAX},"content_digest":"${'a'.repeat(64)}","appearance":"short dark hair","outfit":null},"wardrobe_progression":null}}}`

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  container = null
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('Task 9.11 independent exact-version boundaries', () => {
  it('keeps the version lexer scoped to version fields and preserves ordinary API transport', async () => {
    const parsed = parseVersionJson(
      `{"\\u0076ersion":${SQLITE_MAX},"schema_version":1,"expected_version":9007199254740993,"quoted":"\\\"version\\\": ${SQLITE_MAX}","label":"resource-v1"}`,
    )
    expect(parsed.version).toBe(SQLITE_MAX)
    expect(parsed.expected_version).toBe('9007199254740993')
    expect(parsed.schema_version).toBe(1)
    expect(parsed.quoted).toBe(`"version": ${SQLITE_MAX}`)
    expect(parsed.label).toBe('resource-v1')
    expect(versionText(parsed.version)).toBe(SQLITE_MAX)
    expect(versionText(parseVersionJson('{"version":9.007199254740992e15}').version)).toBeNull()
    let roundedFraction = null
    try { roundedFraction = versionText(parseVersionJson('{"version":9007199254740991.1}').version) } catch { /* malformed version response may be rejected */ }
    expect(roundedFraction).toBeNull()

    const getResponse = response('{"schema_version":1,"version":"resource-v1"}')
    const postResponse = response('{"version":2}')
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(getResponse)
      .mockResolvedValueOnce(postResponse)
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.get('/ordinary')).resolves.toEqual({ schema_version: 1, version: 'resource-v1' })
    await expect(api.post('/ordinary', { schema_version: 1, version: 'resource-v1' })).resolves.toEqual({ version: 2 })
    expect(getResponse.json).toHaveBeenCalledOnce()
    expect(getResponse.text).not.toHaveBeenCalled()
    expect(fetchMock.mock.calls[1][1].body).toBe('{"schema_version":1,"version":"resource-v1"}')
  })

  it('bounds the visible selector and saves the successor of Number.MAX_SAFE_INTEGER exactly', async () => {
    const detailPath = `/api/looks/look-boundary/versions/${MAX_SAFE}`
    const nextVersion = '9007199254740992'
    const calls = []
    const fetchMock = vi.fn(async (path, init) => {
      calls.push({ path, init })
      if (path === '/api/looks') return response(`[{"key":"look-boundary","version":${MAX_SAFE},"name":"Boundary look","content_digest":"${'a'.repeat(64)}"}]`)
      if (path === '/api/wardrobe') return response(JSON.stringify(wardrobe))
      if (path === '/api/config') return response('{"llm_ok":false,"llm_vision_model":""}')
      if (path === detailPath) return response(JSON.stringify(lookDetail(MAX_SAFE)))
      if (path === `/api/looks/look-boundary/versions`) {
        return response(`{"key":"look-boundary","version":${nextVersion},"name":"Boundary look","content_digest":"${'b'.repeat(64)}","appearance":"updated appearance","outfit":null}`)
      }
      throw new Error(`Unexpected fetch ${init?.method || 'GET'} ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    await mount(<Looks />)
    await click(button(`Boundary look · version ${MAX_SAFE}`))

    const options = Array.from(container.querySelector('[aria-label="Version history"]').options)
    expect(options).toHaveLength(21)
    expect(options.map((item) => item.value)).toContain(MAX_SAFE)
    expect(calls.some((call) => call.path === detailPath)).toBe(true)

    await setValue(container.querySelector('[aria-label="Constant appearance"]'), 'updated appearance')
    await click(button('Save look'))

    expect(container.textContent).toContain(`Saved as version ${nextVersion}.`)
    expect(calls.some((call) => call.path === `/api/looks/look-boundary/versions/${nextVersion}`)).toBe(false)
    const write = calls.find((call) => call.path === '/api/looks/look-boundary/versions')
    expect(write.init.body).toContain(`"expected_version":${MAX_SAFE}`)
    expect(write.init.body).not.toContain(`"expected_version":"${MAX_SAFE}"`)
  })

  it('applies a SQLite-maximum look, reads its snapshot exactly, and echoes it through an unrelated CAS edit', async () => {
    const currentPlan = { wire: authoritativePlanWire('Original pose') }
    const calls = []
    const fetchMock = vi.fn(async (path, init = {}) => {
      calls.push({ path, init })
      if (path === '/api/looks') return response(`[{"key":"look-boundary","version":${SQLITE_MAX},"name":"Boundary look"}]`)
      if (path === `/api/looks/look-boundary/versions/${SQLITE_MAX}`) {
        return response(`{"key":"look-boundary","version":${SQLITE_MAX},"name":"Boundary look","content_digest":"${'a'.repeat(64)}","appearance":"short dark hair","outfit":null}`)
      }
      if (path === '/api/sessions/911/plan/apply-look') {
        currentPlan.wire = authoritativePlanWire('Original pose')
        return response('{"plan_revision":5,"conflicts":[]}')
      }
      if (path === '/api/sessions/911/plan' && init.method === 'GET') return response(currentPlan.wire)
      if (path === '/api/sessions/911/plan' && init.method === 'POST') {
        const body = JSON.parse(init.body)
        currentPlan.wire = authoritativePlanWire(body.plan.takes[0].pose)
        return response('{"plan_revision":6,"conflicts":[]}')
      }
      throw new Error(`Unexpected fetch ${init.method || 'GET'} ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    let loadedPlan
    const onReload = async () => {
      loadedPlan = await loadSessionPlan(911, api)
      return loadedPlan
    }
    const before = planBeforeLook()
    await mount(
      <SessionLookProgression
        sessionId={911}
        plan={before}
        revision={4}
        disabled={false}
        onBusyChange={vi.fn()}
        onReload={onReload}
      />,
    )
    await click(container.querySelector('summary'))
    await setValue(container.querySelector('[aria-label="Saved look"]'), 'look-boundary')
    expect(container.textContent).toContain(`version ${SQLITE_MAX}`)
    await setValue(container.querySelector('[aria-label="Look decision"]'), 'replace')
    await click(button('Apply saved look with these decisions'))

    const apply = calls.find((call) => call.path === '/api/sessions/911/plan/apply-look')
    expect(apply.init.body).toBe(
      `{"expected_revision":4,"look_key":"look-boundary","version":${SQLITE_MAX},"decisions":{"look":"replace"}}`,
    )
    expect(loadedPlan.ok).toBe(true)
    expect(loadedPlan.plan.authoring.look_snapshot.version).toBe(SQLITE_MAX)
    expect(loadedPlan.plan.authoring.schema_version).toBe(1)

    loadedPlan.plan.takes[0].pose = 'Edited independently after reopening'
    const saved = await executeSavePlan(911, loadedPlan.plan, loadedPlan.planRevision, api)
    expect(saved).toMatchObject({ ok: true, planRevision: 6 })
    const planWrite = calls.find((call) => call.path === '/api/sessions/911/plan' && call.init.method === 'POST')
    expect(planWrite.init.body).toContain(`"look_snapshot":{"look_id":"look-boundary","version":${SQLITE_MAX}`)
    expect(planWrite.init.body).not.toContain(`"version":"${SQLITE_MAX}"`)
    expect(planWrite.init.body).toContain('"pose":"Edited independently after reopening"')

    const reopened = await loadSessionPlan(911, api)
    expect(reopened.ok).toBe(true)
    expect(reopened.plan.authoring.look_snapshot.version).toBe(SQLITE_MAX)
    expect(reopened.plan.authoring.schema_version).toBe(1)
    expect(reopened.plan.takes[0].pose).toBe('Edited independently after reopening')
  })

  it('treats an explicit 503 refusal as a no-write outcome and leaves a recovery path', async () => {
    const calls = []
    const fetchMock = vi.fn(async (path, init = {}) => {
      calls.push({ path, init })
      if (path === '/api/looks') return response('[{"key":"look-boundary","version":3,"name":"Boundary look"}]')
      if (path === '/api/looks/look-boundary/versions/3') return response(JSON.stringify(lookDetail(3)))
      if (path === '/api/sessions/911/plan/apply-look') {
        return response('{"detail":"Resource planning is disabled by configuration"}', 503)
      }
      throw new Error(`Unexpected fetch ${init.method || 'GET'} ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const onBusyChange = vi.fn()
    const onReload = vi.fn(async () => ({ ok: true, planRevision: 4, plan: planBeforeLook() }))

    await mount(
      <SessionLookProgression
        sessionId={911}
        plan={planBeforeLook()}
        revision={4}
        disabled={false}
        onBusyChange={onBusyChange}
        onReload={onReload}
      />,
    )
    await click(container.querySelector('summary'))
    await setValue(container.querySelector('[aria-label="Saved look"]'), 'look-boundary')
    await setValue(container.querySelector('[aria-label="Look decision"]'), 'replace')
    await click(button('Apply saved look with these decisions'))

    expect(calls.filter((call) => call.path === '/api/sessions/911/plan/apply-look')).toHaveLength(1)
    expect(container.textContent).toContain('Resource planning is disabled by configuration')
    expect(onReload).not.toHaveBeenCalled()
    expect(onBusyChange).toHaveBeenLastCalledWith(false)
    expect(button('Apply saved look with these decisions').disabled).toBe(false)
    expect(button('Retry saved-plan readback')).toBeFalsy()
  })

  it('preserves a dirty session draft and blocks optional saved-look selection', async () => {
    const sessionId = 911
    const session = {
      id: sessionId,
      name: 'Invented resource session',
      status: 'draft',
      model_id: 4,
      model: { id: 4, name: 'Model A', trigger: 'invented trigger' },
      workflow_id: 12,
      shots: [],
      tags: [],
      look: 'Legacy look',
      wardrobe: 'Legacy wardrobe',
      settings: { composition_mode: 'resource-v1', checkpoint: 'invented-model.safetensors' },
    }
    const plan = planBeforeLook()
    const get = vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === `/api/sessions/${sessionId}`) return session
      if (path === `/api/sessions/${sessionId}/plan`) return { plan_revision: 4, plan }
      if (path === '/api/config' || path === '/api/comfy/models') return {}
      return []
    })
    const post = vi.spyOn(api, 'post').mockRejectedValue(new Error('Unexpected write'))

    await mount(
      <SessionView
        id={sessionId}
        initialSession={session}
        initialPlan={plan}
        initialRevision={4}
        initialActiveStep="constants"
      />,
    )
    const lookDraft = container.querySelector('textarea')
    await setValue(lookDraft, 'Local session draft stays intact')
    await click(Array.from(container.querySelectorAll('summary'))
      .find((item) => item.textContent.includes('Choose a saved look (optional)')))

    expect(lookDraft.value).toBe('Local session draft stays intact')
    expect(container.querySelector('[aria-label="Saved look"]').disabled).toBe(true)
    expect(get.mock.calls.some(([path]) => path === '/api/looks')).toBe(false)
    expect(post).not.toHaveBeenCalled()
  })
})
