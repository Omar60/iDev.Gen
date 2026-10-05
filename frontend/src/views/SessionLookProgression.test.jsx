// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api.js'
import SessionLookProgression from './SessionLookProgression.jsx'
import { deriveSavedLookWardrobeProgression } from '../wardrobe.js'

let root
let container

const outfit = {
  outfit_key: 'outfit-session-look',
  garments: [
    { key: 'coat', wording: 'a wool coat', aside: '' },
    { key: 'skirt', wording: 'a pleated skirt', aside: '' },
    { key: 'shoes', wording: 'leather shoes', aside: 'shoes moved aside' },
  ],
}
const savedLook = {
  key: 'look-evening',
  version: 3,
  name: 'Evening light',
  content_digest: 'a'.repeat(64),
  appearance: 'Soft fixed makeup and swept-back hair.',
  outfit,
}
const takes = ['take-001', 'take-002', 'take-003', 'take-004'].map((take_id, index) => ({
  take_id,
  label: `Take ${index + 1}`,
}))

function makePlan({
  look = 'Current appearance',
  initialWardrobe = 'Current clothing',
  wardrobeOrigin = 'user',
  lookSnapshot = null,
  wardrobeChanges = [],
  wardrobeProgression = null,
  takeList = takes,
} = {}) {
  return {
    version: 'resource-v1',
    look,
    initial_wardrobe: initialWardrobe,
    selected_resources: [],
    takes: takeList,
    wardrobe_changes: wardrobeChanges,
    authoring: {
      schema_version: 1,
      mode: 'manual',
      shared_state: {
        look: { origin: 'user', evidence_id: null },
        initial_wardrobe: { origin: wardrobeOrigin, evidence_id: null },
      },
      look_snapshot: lookSnapshot,
      wardrobe_progression: wardrobeProgression,
    },
  }
}

function snapshotFromLook(look = savedLook) {
  return {
    look_id: look.key,
    version: look.version,
    content_digest: look.content_digest,
    appearance: look.appearance,
    outfit: look.outfit,
  }
}

const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

async function drain() {
  await act(async () => {
    for (let index = 0; index < 16; index += 1) await Promise.resolve()
  })
}

async function click(element) {
  expect(element).toBeTruthy()
  await act(async () => { element.click() })
  await drain()
}

async function selectValue(element, value) {
  expect(element).toBeTruthy()
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), 'value')?.set
    if (setter) setter.call(element, String(value))
    else element.value = String(value)
    element.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await drain()
}

async function setChecked(element, checked) {
  expect(element).toBeTruthy()
  if (element.checked !== Boolean(checked)) await click(element)
}

function mountPanel({
  sessionId = 911,
  plan = makePlan(),
  revision = 4,
  disabled = false,
  onBusyChange = vi.fn(),
  onReload = vi.fn(),
} = {}) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  const render = (props = {}) => act(() => root.render(
    <SessionLookProgression
      sessionId={sessionId}
      plan={plan}
      revision={revision}
      disabled={disabled}
      onBusyChange={onBusyChange}
      onReload={onReload}
      {...props}
    />,
  ))
  render()
  return { render }
}

const savedPlanAfterLook = (before, decisions, version = savedLook) => {
  const next = structuredClone(before)
  next.authoring.look_snapshot = snapshotFromLook(version)
  next.authoring.shared_state.look.origin = decisions.look === 'replace' ? 'saved_look' : 'user'
  next.look = decisions.look === 'replace' ? version.appearance : before.look
  if (decisions.initial_wardrobe) {
    next.authoring.shared_state.initial_wardrobe.origin = 'user'
    if (decisions.initial_wardrobe === 'replace') {
      next.initial_wardrobe = deriveSavedLookWardrobeProgression(version.outfit, before.initial_wardrobe, 1, {
        stageIndices: [0],
      })[0]
      next.authoring.shared_state.initial_wardrobe.origin = 'saved_look'
    }
  }
  return next
}

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  container = null
  vi.restoreAllMocks()
})

describe('Task 9.11 session saved looks and wardrobe progression', () => {
  it('keeps saved-look selection optional and performs no write by default', async () => {
    const get = vi.spyOn(api, 'getVersioned').mockResolvedValue([{ key: savedLook.key, version: 3, name: savedLook.name }])
    const post = vi.spyOn(api, 'post').mockResolvedValue({})
    mountPanel()
    await drain()

    expect(container.querySelector('summary').textContent).toContain('Choose a saved look (optional)')
    expect(get).not.toHaveBeenCalled()
    expect(post).not.toHaveBeenCalled()

    await click(container.querySelector('summary'))
    expect(get).toHaveBeenCalledWith('/api/looks')
    expect(post).not.toHaveBeenCalled()
    expect(container.querySelector('button.primary')).toBeNull()
  })

  it('loads an immutable version and applies only after explicit Replace or Keep decisions', async () => {
    const before = makePlan()
    const onReload = vi.fn(async (minimumRevision) => ({
      ok: true,
      planRevision: 5,
      plan: savedPlanAfterLook(before, { look: 'replace', initial_wardrobe: 'keep' }),
      minimumRevision,
    }))
    const get = vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => (
      path === '/api/looks'
        ? [{ key: savedLook.key, version: savedLook.version, name: savedLook.name }]
        : savedLook
    ))
    const post = vi.spyOn(api, 'postVersioned').mockResolvedValue({ plan_revision: 5, conflicts: [] })
    mountPanel({ plan: before, revision: 4, onReload })
    await click(container.querySelector('summary'))
    await selectValue(container.querySelector('[aria-label="Saved look"]'), savedLook.key)

    expect(get).toHaveBeenCalledWith(`/api/looks/${encodeURIComponent(savedLook.key)}/versions/3`)
    expect(container.textContent).toContain('Removable garments in confirmed removal order')
    expect(container.querySelector('button.primary').disabled).toBe(true)
    await selectValue(container.querySelector('[aria-label="Look decision"]'), 'replace')
    await selectValue(container.querySelector('[aria-label="Wardrobe decision"]'), 'keep')
    expect(container.querySelector('button.primary').disabled).toBe(false)
    await click(container.querySelector('button.primary'))

    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith(`/api/sessions/911/plan/apply-look`, {
      expected_revision: 4,
      look_key: savedLook.key,
      version: '3',
      decisions: { look: 'replace', initial_wardrobe: 'keep' },
    }, ['version'])
    expect(onReload).toHaveBeenCalledWith(5)
    expect(container.textContent).toContain('authoritative session plan')
  })

  it('requires Keep for an old saved-look wardrobe over an appearance-only version', async () => {
    const appearanceOnly = { ...savedLook, version: 2, content_digest: 'b'.repeat(64), outfit: null }
    const before = makePlan({ wardrobeOrigin: 'saved_look', initialWardrobe: 'Previously saved coat.' })
    const onReload = vi.fn(async () => ({
      ok: true,
      planRevision: 5,
      plan: savedPlanAfterLook(before, { look: 'replace', initial_wardrobe: 'keep' }, appearanceOnly),
    }))
    const get = vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => (
      path === '/api/looks'
        ? [{ key: appearanceOnly.key, version: appearanceOnly.version, name: appearanceOnly.name }]
        : appearanceOnly
    ))
    const post = vi.spyOn(api, 'postVersioned').mockResolvedValue({ plan_revision: 5 })
    mountPanel({ plan: before, onReload })
    await click(container.querySelector('summary'))
    await selectValue(container.querySelector('[aria-label="Saved look"]'), appearanceOnly.key)
    expect(get).toHaveBeenCalledWith(`/api/looks/${encodeURIComponent(appearanceOnly.key)}/versions/2`)
    expect(container.textContent).toContain('explicitly choose Keep')
    expect(container.querySelector('[aria-label="Wardrobe decision"]').querySelectorAll('option')).toHaveLength(2)
    await selectValue(container.querySelector('[aria-label="Look decision"]'), 'replace')
    expect(container.querySelector('button.primary').disabled).toBe(true)
    await selectValue(container.querySelector('[aria-label="Wardrobe decision"]'), 'keep')
    await click(container.querySelector('button.primary'))

    expect(post.mock.calls[0][1].decisions).toEqual({ look: 'replace', initial_wardrobe: 'keep' })
    expect(onReload).toHaveBeenCalledWith(5)
  })

  it('keeps a SQLite-max version exact through selection, lookup, apply, and readback', async () => {
    const version = '9223372036854775807'
    const largeLook = { ...savedLook, key: 'look-large-version', version }
    const before = makePlan()
    const onReload = vi.fn(async () => ({
      ok: true,
      planRevision: 5,
      plan: savedPlanAfterLook(before, { look: 'replace', initial_wardrobe: 'keep' }, largeLook),
    }))
    const get = vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => (
      path === '/api/looks'
        ? [{ key: largeLook.key, version, name: largeLook.name }]
        : largeLook
    ))
    const post = vi.spyOn(api, 'postVersioned').mockResolvedValue({ plan_revision: 5, conflicts: [] })
    mountPanel({ plan: before, revision: 4, onReload })
    await click(container.querySelector('summary'))
    await selectValue(container.querySelector('[aria-label="Saved look"]'), largeLook.key)

    expect(container.querySelector('[aria-label="Immutable version"]').value).toBe(version)
    expect(get).toHaveBeenCalledWith(
      `/api/looks/${encodeURIComponent(largeLook.key)}/versions/${version}`,
    )
    await selectValue(container.querySelector('[aria-label="Look decision"]'), 'replace')
    await selectValue(container.querySelector('[aria-label="Wardrobe decision"]'), 'keep')
    await click(container.querySelector('button.primary'))

    expect(post).toHaveBeenCalledWith(`/api/sessions/911/plan/apply-look`, {
      expected_revision: 4,
      look_key: largeLook.key,
      version,
      decisions: { look: 'replace', initial_wardrobe: 'keep' },
    }, ['version'])
    expect(container.textContent).toContain('authoritative session plan')
  })

  it('sends only one look mutation for rapid duplicate clicks', async () => {
    const before = makePlan()
    const updated = savedPlanAfterLook(before, { look: 'replace', initial_wardrobe: 'keep' })
    const writeGate = deferred()
    const onReload = vi.fn(async () => ({ ok: true, planRevision: 5, plan: updated }))
    vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => (
      path === '/api/looks'
        ? [{ key: savedLook.key, version: 3, name: savedLook.name }]
        : savedLook
    ))
    const post = vi.spyOn(api, 'postVersioned').mockReturnValue(writeGate.promise)
    mountPanel({ plan: before, onReload })
    await click(container.querySelector('summary'))
    await selectValue(container.querySelector('[aria-label="Saved look"]'), savedLook.key)
    await selectValue(container.querySelector('[aria-label="Look decision"]'), 'replace')
    await selectValue(container.querySelector('[aria-label="Wardrobe decision"]'), 'keep')
    const apply = container.querySelector('button.primary')

    await act(async () => {
      apply.click()
      apply.click()
      await Promise.resolve()
    })
    expect(post).toHaveBeenCalledTimes(1)

    await act(async () => {
      writeGate.resolve({ plan_revision: 5 })
      await Promise.resolve()
    })
    await drain()
    expect(onReload).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('authoritative session plan')
  })

  it('previews every effective take state, names retained overrides, and applies the exact signed review', async () => {
    const full = deriveSavedLookWardrobeProgression(outfit, '', 1, { stageIndices: [0] })[0]
    const partial = deriveSavedLookWardrobeProgression(outfit, '', 1, { stageIndices: [2] })[0]
    const oldEvent = { take_id: 'take-004', scope: 'from_here', wardrobe: 'Old saved event.' }
    const override = { take_id: 'take-003', scope: 'this_take', wardrobe: 'A retained one-take exception.' }
    const before = makePlan({
      initialWardrobe: full,
      lookSnapshot: snapshotFromLook(),
      wardrobeChanges: [override, oldEvent],
    })
    const result = {
      expected_revision: 4,
      event_policy: 'replace',
      initial_wardrobe: full,
      wardrobe_changes: [
        { take_id: 'take-002', scope: 'from_here', wardrobe: partial },
        override,
      ],
      wardrobe_progression: {
        source_look_digest: savedLook.content_digest,
        start_take_id: 'take-001',
        end_take_id: 'take-004',
        stage_indices: [0, 2, 4],
        applied_revision: 5,
      },
      reviewed_wardrobes: [
        { take_id: 'take-001', wardrobe: full },
        { take_id: 'take-002', wardrobe: partial },
        { take_id: 'take-003', wardrobe: override.wardrobe },
        { take_id: 'take-004', wardrobe: partial },
      ],
      review_digest: 'c'.repeat(64),
      preview_token: 'signed-preview-token',
    }
    const appliedPlan = structuredClone(before)
    appliedPlan.initial_wardrobe = full
    appliedPlan.wardrobe_changes = result.wardrobe_changes
    appliedPlan.authoring.wardrobe_progression = result.wardrobe_progression
    const onReload = vi.fn(async () => ({ ok: true, planRevision: 5, plan: appliedPlan }))
    const post = vi.spyOn(api, 'post').mockImplementation(async (path) => (
      path.endsWith('/preview') ? result : { plan_revision: 5, conflicts: [], wardrobe_progression: result.wardrobe_progression }
    ))
    mountPanel({ plan: before, onReload })

    const summaries = [...container.querySelectorAll('summary')]
    await click(summaries.find((item) => item.textContent.includes('Plan clothing changes')))
    await setChecked(container.querySelector('[aria-label="Confirm garment removal order"]'), true)
    await selectValue(container.querySelector('[aria-label="Final clothing stage"]'), '4')
    await setChecked(container.querySelector('[aria-label="Include stage 1"]'), true)
    await setChecked(container.querySelector('[aria-label="Include stage 3"]'), true)
    await selectValue(container.querySelector('[aria-label="Existing wardrobe event policy"]'), 'replace')
    const previewButton = [...container.querySelectorAll('button')]
      .find((button) => button.textContent.includes('Preview clothing for every take'))
    expect(previewButton.disabled).toBe(false)
    await click(previewButton)

    expect(post).toHaveBeenCalledWith('/api/sessions/911/plan/wardrobe-progression/preview', {
      expected_revision: 4,
      start_take_id: 'take-001',
      end_take_id: 'take-004',
      stage_indices: [0, 2, 4],
      event_policy: 'replace',
    })
    expect(container.textContent).toContain('Reviewed effective wardrobe for every take')
    expect(container.textContent).toContain('take-001')
    expect(container.textContent).toContain('take-002')
    expect(container.textContent).toContain('take-003')
    expect(container.textContent).toContain('take-004')
    expect(container.textContent).toContain('retained saved event')
    expect(container.textContent).toContain('Replace will remove saved from-here events on: take-004')

    const applyButton = [...container.querySelectorAll('button')]
      .find((button) => button.textContent.includes('Apply this reviewed timeline'))
    await click(applyButton)
    expect(post).toHaveBeenCalledTimes(2)
    expect(post.mock.calls[1]).toEqual([
      '/api/sessions/911/plan/wardrobe-progression/apply',
      {
        expected_revision: 4,
        preview_token: 'signed-preview-token',
        review_digest: 'c'.repeat(64),
        reviewed_wardrobes: result.reviewed_wardrobes,
      },
    ])
    expect(onReload).toHaveBeenCalledWith(5)
    expect(post.mock.calls.some(([path]) => /approve|submit|run/.test(path))).toBe(false)
  })

  it('clears a completed preview immediately when a planner input changes', async () => {
    const plan = makePlan({ lookSnapshot: snapshotFromLook() })
    const preview = {
      expected_revision: 4,
      event_policy: 'merge',
      initial_wardrobe: 'Current clothing',
      wardrobe_changes: [],
      wardrobe_progression: {
        source_look_digest: savedLook.content_digest,
        start_take_id: 'take-001',
        end_take_id: 'take-004',
        stage_indices: [0],
        applied_revision: 5,
      },
      reviewed_wardrobes: takes.map((take) => ({ take_id: take.take_id, wardrobe: 'Current clothing' })),
      review_digest: 'e'.repeat(64),
      preview_token: 'signed-preview-token',
    }
    const post = vi.spyOn(api, 'post').mockResolvedValue(preview)
    mountPanel({ plan })
    await click([...container.querySelectorAll('summary')].find((item) => item.textContent.includes('Plan clothing changes')))
    await setChecked(container.querySelector('[aria-label="Confirm garment removal order"]'), true)
    await selectValue(container.querySelector('[aria-label="Final clothing stage"]'), '0')
    await selectValue(container.querySelector('[aria-label="Existing wardrobe event policy"]'), 'merge')
    await click([...container.querySelectorAll('button')].find((button) => button.textContent.includes('Preview clothing for every take')))
    expect(container.textContent).toContain('Reviewed effective wardrobe for every take')
    expect([...container.querySelectorAll('button')].some((button) => button.textContent.includes('Apply this reviewed timeline'))).toBe(true)

    await selectValue(container.querySelector('[aria-label="Existing wardrobe event policy"]'), 'replace')
    expect(container.textContent).not.toContain('Reviewed effective wardrobe for every take')
    expect([...container.querySelectorAll('button')].some((button) => button.textContent.includes('Apply this reviewed timeline'))).toBe(false)
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('ignores an in-flight preview after an input edit and allows a fresh review', async () => {
    const plan = makePlan({ lookSnapshot: snapshotFromLook() })
    const previewGate = deferred()
    const post = vi.spyOn(api, 'post').mockReturnValue(previewGate.promise)
    mountPanel({ plan })
    await click([...container.querySelectorAll('summary')].find((item) => item.textContent.includes('Plan clothing changes')))
    await setChecked(container.querySelector('[aria-label="Confirm garment removal order"]'), true)
    await selectValue(container.querySelector('[aria-label="Final clothing stage"]'), '0')
    await selectValue(container.querySelector('[aria-label="Existing wardrobe event policy"]'), 'merge')
    await click([...container.querySelectorAll('button')].find((button) => button.textContent.includes('Preview clothing for every take')))
    expect(container.textContent).toContain('Requesting reviewed timeline…')

    await selectValue(container.querySelector('[aria-label="Existing wardrobe event policy"]'), 'replace')
    expect(container.textContent).not.toContain('Requesting reviewed timeline…')
    expect(container.textContent).not.toContain('Reviewed effective wardrobe for every take')
    expect([...container.querySelectorAll('button')].find((button) => button.textContent.includes('Preview clothing for every take')).disabled).toBe(false)

    await act(async () => {
      previewGate.resolve({
        expected_revision: 4,
        event_policy: 'merge',
        initial_wardrobe: 'Current clothing',
        wardrobe_changes: [],
        wardrobe_progression: {
          source_look_digest: savedLook.content_digest,
          start_take_id: 'take-001',
          end_take_id: 'take-004',
          stage_indices: [0],
          applied_revision: 5,
        },
        reviewed_wardrobes: takes.map((take) => ({ take_id: take.take_id, wardrobe: 'Current clothing' })),
        review_digest: 'f'.repeat(64),
        preview_token: 'stale-preview-token',
      })
      await Promise.resolve()
    })
    await drain()

    expect(container.textContent).not.toContain('Reviewed effective wardrobe for every take')
    expect(container.textContent).not.toContain('stale-preview-token')
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('gives the fewer-stages remedy when the interval cannot fit the selection', async () => {
    const plan = makePlan({ lookSnapshot: snapshotFromLook(), takeList: takes.slice(0, 2) })
    const post = vi.spyOn(api, 'post').mockResolvedValue({
      expected_revision: 4,
      event_policy: 'merge',
      initial_wardrobe: 'Current clothing',
      wardrobe_changes: [],
      wardrobe_progression: {},
      reviewed_wardrobes: takes.slice(0, 2).map((take) => ({ take_id: take.take_id, wardrobe: 'Current clothing' })),
      review_digest: 'd'.repeat(64),
      preview_token: 'signed-preview-token',
    })
    mountPanel({ plan })
    await click([...container.querySelectorAll('summary')].find((item) => item.textContent.includes('Plan clothing changes')))
    await setChecked(container.querySelector('[aria-label="Confirm garment removal order"]'), true)
    await selectValue(container.querySelector('[aria-label="Final clothing stage"]'), '3')
    await setChecked(container.querySelector('[aria-label="Include stage 1"]'), true)
    await setChecked(container.querySelector('[aria-label="Include stage 2"]'), true)
    await selectValue(container.querySelector('[aria-label="Existing wardrobe event policy"]'), 'merge')

    expect(container.textContent).toContain('Widen the take interval or choose fewer stages.')
    expect([...container.querySelectorAll('button')].find((button) => button.textContent.includes('Preview clothing for every take')).disabled).toBe(true)
    expect(post).not.toHaveBeenCalled()

    await selectValue(container.querySelector('[aria-label="Progression end take"]'), 'take-002')
    expect(container.textContent).toContain('Widen the take interval or choose fewer stages.')
  })

  it('does not resend a write after an unknown response and retries only authoritative readback', async () => {
    const before = makePlan()
    const updated = savedPlanAfterLook(before, { look: 'replace', initial_wardrobe: 'keep' })
    const readbacks = [false, { ok: true, planRevision: 5, plan: updated }]
    const onReload = vi.fn(async () => readbacks.shift())
    vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => (
      path === '/api/looks'
        ? [{ key: savedLook.key, version: 3, name: savedLook.name }]
        : savedLook
    ))
    const post = vi.spyOn(api, 'postVersioned').mockRejectedValue(new Error('connection lost'))
    mountPanel({ plan: before, onReload })
    await click(container.querySelector('summary'))
    await selectValue(container.querySelector('[aria-label="Saved look"]'), savedLook.key)
    await selectValue(container.querySelector('[aria-label="Look decision"]'), 'replace')
    await selectValue(container.querySelector('[aria-label="Wardrobe decision"]'), 'keep')
    await click(container.querySelector('button.primary'))

    expect(post).toHaveBeenCalledTimes(1)
    expect(onReload).toHaveBeenCalledWith(5)
    expect(container.textContent).toContain('Retry saved-plan readback')
    await click([...container.querySelectorAll('button')].find((button) => button.textContent.includes('Retry saved-plan readback')))
    expect(post).toHaveBeenCalledTimes(1)
    expect(onReload).toHaveBeenCalledTimes(2)
    expect(container.textContent).toContain('authoritative session plan')
  })

  it('ignores a late immutable-version response after the session changes', async () => {
    const detailGate = deferred()
    const get = vi.spyOn(api, 'getVersioned').mockImplementation((path) => (
      path === '/api/looks'
        ? Promise.resolve([{ key: savedLook.key, version: 3, name: savedLook.name }])
        : detailGate.promise
    ))
    const { render } = mountPanel()
    await click(container.querySelector('summary'))
    await selectValue(container.querySelector('[aria-label="Saved look"]'), savedLook.key)
    render({ sessionId: 912 })
    await act(async () => { detailGate.resolve(savedLook); await Promise.resolve() })
    await drain()

    expect(get).toHaveBeenCalled()
    expect(container.textContent).not.toContain(savedLook.appearance)
    expect(container.querySelector('[aria-label="Look decision"]')).toBeNull()
    expect(container.textContent).not.toContain('Applying saved look')
  })

  it('treats the exact planning-disabled 503 as a known no-write result', async () => {
    const message = 'Resource planning is disabled by configuration'
    const refusal = Object.assign(new Error(message), { status: 503, detail: message })
    const onBusyChange = vi.fn()
    const onReload = vi.fn(async () => ({ ok: true, planRevision: 4, plan: makePlan() }))
    vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => (
      path === '/api/looks'
        ? [{ key: savedLook.key, version: 3, name: savedLook.name }]
        : savedLook
    ))
    const post = vi.spyOn(api, 'postVersioned').mockRejectedValue(refusal)
    mountPanel({ onBusyChange, onReload })
    await click(container.querySelector('summary'))
    await selectValue(container.querySelector('[aria-label="Saved look"]'), savedLook.key)
    await selectValue(container.querySelector('[aria-label="Look decision"]'), 'replace')
    await selectValue(container.querySelector('[aria-label="Wardrobe decision"]'), 'keep')
    await click(container.querySelector('button.primary'))

    expect(post).toHaveBeenCalledTimes(1)
    expect(onReload).not.toHaveBeenCalled()
    expect(onBusyChange).toHaveBeenLastCalledWith(false)
    expect(container.textContent).toContain(message)
    expect(container.textContent).toContain('No changes were applied. Resource planning is disabled')
    expect(container.querySelector('button.primary').disabled).toBe(false)
    expect(container.textContent).not.toContain('Retry saved-plan readback')
  })

  it('keeps an unclassified 503 in readback-only recovery', async () => {
    const transient = Object.assign(new Error('Gateway unavailable'), {
      status: 503,
      detail: 'Gateway unavailable',
    })
    const onReload = vi.fn(async () => false)
    vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => (
      path === '/api/looks'
        ? [{ key: savedLook.key, version: 3, name: savedLook.name }]
        : savedLook
    ))
    const post = vi.spyOn(api, 'postVersioned').mockRejectedValue(transient)
    mountPanel({ onReload })
    await click(container.querySelector('summary'))
    await selectValue(container.querySelector('[aria-label="Saved look"]'), savedLook.key)
    await selectValue(container.querySelector('[aria-label="Look decision"]'), 'replace')
    await selectValue(container.querySelector('[aria-label="Wardrobe decision"]'), 'keep')
    await click(container.querySelector('button.primary'))

    expect(post).toHaveBeenCalledTimes(1)
    expect(onReload).toHaveBeenCalledWith(5)
    expect(container.textContent).toContain('Retry saved-plan readback')
    expect(container.querySelector('button.primary').disabled).toBe(true)
  })

  it('treats preview verification unavailability during apply as no-write and asks for a fresh review', async () => {
    const unavailableMessage = 'Wardrobe progression preview is temporarily unavailable.'
    const unavailable = Object.assign(new Error(unavailableMessage), {
      status: 503,
      detail: unavailableMessage,
    })
    const preview = {
      expected_revision: 4,
      event_policy: 'merge',
      initial_wardrobe: 'Current clothing',
      wardrobe_changes: [],
      wardrobe_progression: {
        source_look_digest: savedLook.content_digest,
        start_take_id: 'take-001',
        end_take_id: 'take-004',
        stage_indices: [0],
        applied_revision: 5,
      },
      reviewed_wardrobes: takes.map((take) => ({ take_id: take.take_id, wardrobe: 'Current clothing' })),
      review_digest: 'd'.repeat(64),
      preview_token: 'review-token',
    }
    const onBusyChange = vi.fn()
    const onReload = vi.fn()
    const post = vi.spyOn(api, 'post').mockImplementation(async (path) => (
      path.endsWith('/preview') ? preview : Promise.reject(unavailable)
    ))
    mountPanel({ plan: makePlan({ lookSnapshot: snapshotFromLook() }), onBusyChange, onReload })
    await click([...container.querySelectorAll('summary')].find((item) => item.textContent.includes('Plan clothing changes')))
    await setChecked(container.querySelector('[aria-label="Confirm garment removal order"]'), true)
    await selectValue(container.querySelector('[aria-label="Final clothing stage"]'), '0')
    await selectValue(container.querySelector('[aria-label="Existing wardrobe event policy"]'), 'merge')
    await click([...container.querySelectorAll('button')].find((button) => button.textContent.includes('Preview clothing for every take')))
    await click([...container.querySelectorAll('button')].find((button) => button.textContent.includes('Apply this reviewed timeline')))

    expect(post).toHaveBeenCalledTimes(2)
    expect(onReload).not.toHaveBeenCalled()
    expect(onBusyChange).toHaveBeenLastCalledWith(false)
    expect(container.textContent).toContain(unavailableMessage)
    expect(container.textContent).toContain('request and review a fresh preview')
    expect(container.textContent).not.toContain('Retry saved-plan readback')
    expect(container.textContent).not.toContain('Apply this reviewed timeline')
    expect([...container.querySelectorAll('button')].find((button) => button.textContent.includes('Preview clothing for every take')).disabled).toBe(false)
  })

  it('keeps freeze and stale-plan errors visible with an actionable remedy', async () => {
    const get = vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => (
      path === '/api/looks'
        ? [{ key: savedLook.key, version: 3, name: savedLook.name }]
        : savedLook
    ))
    const frozen = Object.assign(new Error('plan constants are frozen after generated history'), { status: 409 })
    const post = vi.spyOn(api, 'postVersioned').mockRejectedValue(frozen)
    const onReload = vi.fn(async () => ({ ok: true, planRevision: 4, plan: makePlan() }))
    mountPanel({ onReload })
    await click(container.querySelector('summary'))
    await selectValue(container.querySelector('[aria-label="Saved look"]'), savedLook.key)
    await selectValue(container.querySelector('[aria-label="Look decision"]'), 'replace')
    await selectValue(container.querySelector('[aria-label="Wardrobe decision"]'), 'keep')
    await click(container.querySelector('button.primary'))

    expect(post).toHaveBeenCalledTimes(1)
    expect(onReload).toHaveBeenCalledWith(0)
    expect(container.textContent).toContain('plan constants are frozen after generated history')
    expect(container.textContent).toContain('No changes were applied. Reload the current plan')
    expect(get).toHaveBeenCalled()
  })
})
