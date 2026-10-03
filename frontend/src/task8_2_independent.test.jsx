// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { api } from './api.js'
import Resources from './views/Resources.jsx'
import SessionView from './views/SessionView.jsx'

const sessionId = 501
const anchor = {
  library_key: 'rooms_task82',
  source_id: 'scene-001',
  content_digest: 'a'.repeat(64),
}

function sharedSummary(plan) {
  return {
    available: true,
    look: {
      value: plan.look,
      origin: plan.authoring.shared_state.look.origin,
    },
    initial_wardrobe: {
      value: plan.initial_wardrobe,
      origin: plan.authoring.shared_state.initial_wardrobe.origin,
    },
    scene_descriptions: [{
      ...anchor,
      kind: 'rooms',
      descriptive_inputs: { scene_theme: 'An invented quiet studio.' },
    }],
  }
}

function planFor(mode = 'manual') {
  return {
    version: 'resource-v1',
    look: '',
    initial_wardrobe: '',
    takes: [{
      take_id: 'take-001',
      label: 'Invented studio portrait',
      camera: '50mm eye-level',
      framing: 'medium portrait',
      pose: 'standing by a window',
      expression: 'calm',
    }],
    selected_resources: [anchor],
    wardrobe_changes: [],
    authoring: {
      mode,
      brief: '',
      scene_anchor: anchor,
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
        look: { origin: 'none', evidence_id: null },
        initial_wardrobe: { origin: 'none', evidence_id: null },
      },
      look_snapshot: null,
      wardrobe_progression: null,
      evidence: [],
    },
  }
}

function suggestionOperationView(operationId, requested, items) {
  return {
    operation_id: operationId,
    session_id: sessionId,
    plan_revision: 1,
    kind: 'shared_suggestions',
    state: 'succeeded',
    created_at: '2026-10-02T12:00:00Z',
    updated_at: '2026-10-02T12:00:01Z',
    lease_expires_at: null,
    progress: { requested, completed: requested, failed: null, remaining: [] },
    result: { items },
    error: null,
    can_cancel: false,
    can_resume: false,
  }
}

const session = {
  id: sessionId,
  name: 'Invented Task 8.2 session',
  status: 'draft',
  model_id: 4,
  model: { id: 4, name: 'Model A', trigger: 'model trigger' },
  checkpoint: 'base.safetensors',
  shots: [],
  tags: [],
  settings: {
    composition_mode: 'resource-v1',
    width: 1024,
    height: 1024,
    steps: 20,
    cfg: 7,
    lora_strength: 1,
    checkpoint: 'base.safetensors',
  },
}

function installSessionApi(initialPlan) {
  const state = {
    plan: structuredClone(initialPlan),
    revision: 1,
    posts: [],
    get handlePost() { return this._handlePost },
    set handlePost(value) { this._handlePost = value },
  }
  vi.spyOn(api, 'get').mockImplementation(async (url) => {
    if (url === `/api/sessions/${sessionId}`) return structuredClone(session)
    if (url === `/api/sessions/${sessionId}/plan`) {
      return {
        plan_revision: state.revision,
        plan: structuredClone(state.plan),
        shared_summary: sharedSummary(state.plan),
        conflicts: [],
        preparation: null,
        reviewed_revision: null,
      }
    }
    if (url === '/api/workflows' || url === '/api/sessions' || url === '/api/comfy/models') return []
    if (url === '/api/config') return { llm_ok: true }
    return []
  })
  vi.spyOn(api, 'post').mockImplementation(async (url, body) => {
    state.posts.push([url, structuredClone(body)])
    if (state.handlePost) return state.handlePost(url, body, state)
    if (url === `/api/sessions/${sessionId}/plan`) {
      state.plan = structuredClone(body.plan)
      for (const field of body.shared_decisions || []) {
        state.plan.authoring.shared_state[field] = { origin: 'user', evidence_id: null }
      }
      state.revision = body.expected_revision + 1
      return { plan_revision: state.revision, conflicts: [] }
    }
    return {}
  })
  return state
}

function setValue(element, value) {
  const prototype = element instanceof HTMLTextAreaElement
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value)
  element.dispatchEvent(new Event('input', { bubbles: true }))
  element.dispatchEvent(new Event('change', { bubbles: true }))
}

function setSelectValue(element, value) {
  element.value = value
  element.dispatchEvent(new Event('change', { bubbles: true }))
}

function labeledInput(labelText) {
  const label = Array.from(container.querySelectorAll('label'))
    .find((candidate) => candidate.textContent.trim() === labelText)
  expect(label, `label ${labelText}`).toBeTruthy()
  return label.parentElement.querySelector('input, textarea, select')
}

async function clickButton(name, scope = container) {
  const button = Array.from(scope.querySelectorAll('button'))
    .find((candidate) => candidate.textContent.trim() === name)
  expect(button, `button ${name}`).toBeTruthy()
  await act(async () => { button.click() })
  return button
}

async function flush() {
  await act(async () => { await Promise.resolve() })
}

let container = null
let root = null

beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  window.location.hash = ''
})

afterEach(() => {
  if (root) act(() => root.unmount())
  root = null
  container?.remove()
  container = null
  vi.restoreAllMocks()
})

describe('Task 8.2 independent UI acceptance', () => {
  it('keeps guided initial inputs and sends shared overrides plus fixed/vary policy from Advanced', async () => {
    const library = {
      id: 1,
      library_key: anchor.library_key,
      display_name: 'Ready Rooms',
      kind: 'rooms',
      revisions: [{
        revision_id: 3,
        ...anchor,
        readiness: { status: 'ready', pending_fields: {} },
      }],
      auxiliary: [],
    }
    const posts = []
    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/resources/libraries') return [library]
      if (url === '/api/models') return [{ id: 4, name: 'Model A', workflow_id: 12 }]
      if (url === '/api/config') return { llm_ok: false }
      return []
    })
    vi.spyOn(api, 'postWithStatus').mockImplementation(async (url, body) => {
      posts.push([url, structuredClone(body)])
      return {
        status: 201,
        data: {
          session_id: sessionId,
          plan_revision: 1,
          plan: {
            version: 'resource-v1',
            takes: Array.from({ length: body.photo_count }, (_, index) => ({
              take_id: `take-${String(index + 1).padStart(3, '0')}`,
            })),
            selected_resources: [body.scene_anchor],
            authoring: {
              schema_version: 1,
              mode: body.mode,
              scene_anchor: body.scene_anchor,
            },
          },
        },
      }
    })

    await act(async () => { root.render(<Resources requestedModelId="4" />) })
    await clickButton('Create session')
    const form = container.querySelector('[aria-label="Guided session setup"]')
    expect(form).toBeTruthy()
    expect(form.querySelector('#guided-character')).toBeTruthy()
    expect(form.querySelector('#guided-scene')).toBeTruthy()
    expect(form.querySelector('#guided-photo-count')).toBeTruthy()
    expect(form.querySelector('#guided-brief')).toBeTruthy()
    expect(form.querySelector('#guided-takes')).toBeFalsy()

    await act(async () => { form.querySelector('summary').click() })
    for (const field of ['camera', 'framing', 'pose', 'expression']) {
      expect(form.querySelector(`#guided-${field}-mode`).value).toBe('vary')
    }
    setSelectValue(form.querySelector('#guided-camera-mode'), 'fixed')
    setValue(form.querySelector('[aria-label="Fixed camera"]'), 'eye-level 50mm')
    setValue(form.querySelector('#guided-look'), 'A quiet daylight studio.')
    setValue(form.querySelector('#guided-initial-wardrobe'), 'A navy jacket.')
    setValue(form.querySelector('#guided-photo-count'), '3')
    setValue(form.querySelector('#guided-brief'), 'Invented short brief.')

    await clickButton('Create guided session', form)
    expect(posts).toHaveLength(1)
    expect(posts[0][0]).toBe('/api/sessions/guided')
    expect(posts[0][1]).toEqual({
      request_id: expect.stringMatching(/^[0-9a-f-]{36}$/i),
      character_id: 4,
      workflow_id: null,
      scene_anchor: anchor,
      photo_count: 3,
      brief: 'Invented short brief.',
      mode: 'manual',
      variation_policy: {
        camera: { mode: 'fixed', value: 'eye-level 50mm', value_origin: 'user' },
        framing: { mode: 'vary' },
        pose: { mode: 'vary' },
        expression: { mode: 'vary' },
      },
      look: 'A quiet daylight studio.',
      initial_wardrobe: 'A navy jacket.',
    })
    expect(posts[0][1]).not.toHaveProperty('takes')
    expect(posts[0][1]).not.toHaveProperty('plan')
  })

  it('shows editable take fields by default in manual mode and keeps them collapsed in automatic mode', async () => {
    const manualState = installSessionApi(planFor('manual'))
    await act(async () => {
      root.render(
        <SessionView
          id={sessionId}
          initialSession={session}
          initialPlan={manualState.plan}
          initialRevision={1}
          initialActiveStep="takes"
          initialSharedSummary={sharedSummary(manualState.plan)}
        />,
      )
    })
    await flush()

    const fields = Array.from(container.querySelectorAll('details'))
      .find((detail) => detail.querySelector('summary')?.textContent.trim()
        === 'Advanced take fields (camera, framing, pose, expression)')
    expect(fields?.open).toBe(true)
    for (const name of ['Camera', 'Framing', 'Pose', 'Expression']) {
      expect(labeledInput(name)).toBeTruthy()
    }
    setValue(labeledInput('Camera'), '35mm from above')
    setValue(labeledInput('Framing'), 'wide portrait')
    setValue(labeledInput('Pose'), 'seated by the window')
    setValue(labeledInput('Expression'), 'small smile')
    await clickButton('Save Draft')
    await flush()
    const save = manualState.posts.find(([url]) => url === `/api/sessions/${sessionId}/plan`)
    expect(save?.[1].plan.takes[0]).toMatchObject({
      camera: '35mm from above',
      framing: 'wide portrait',
      pose: 'seated by the window',
      expression: 'small smile',
    })

    act(() => root.unmount())
    root = createRoot(container)
    const automaticState = installSessionApi(planFor('automatic'))
    await act(async () => {
      root.render(
        <SessionView
          id={sessionId}
          initialSession={session}
          initialPlan={automaticState.plan}
          initialRevision={1}
          initialActiveStep="takes"
          initialSharedSummary={sharedSummary(automaticState.plan)}
        />,
      )
    })
    await flush()
    const automaticFields = Array.from(container.querySelectorAll('details'))
      .find((detail) => detail.querySelector('summary')?.textContent.trim()
        === 'Advanced take fields (camera, framing, pose, expression)')
    expect(automaticFields?.open).toBe(false)
  })

  it('records an explicit empty choice through plan CAS without preparing or approving', async () => {
    const state = installSessionApi(planFor('manual'))
    await act(async () => {
      root.render(
        <SessionView
          id={sessionId}
          initialSession={session}
          initialPlan={state.plan}
          initialRevision={1}
          initialActiveStep="constants"
          initialSharedSummary={sharedSummary(state.plan)}
        />,
      )
    })
    await flush()

    const saved = container.querySelector('[aria-label="Saved shared session summary"]')
    expect(saved.textContent).toContain('Origin: none')
    await clickButton('Choose no additional look constraint', container.querySelector('[aria-label="Shared choices"]'))

    const writes = state.posts.filter(([url]) => url !== '/api/sessions/501/plan')
    expect(writes).toEqual([])
    const planWrite = state.posts.find(([url]) => url === `/api/sessions/${sessionId}/plan`)
    expect(planWrite?.[1]).toMatchObject({ expected_revision: 1, shared_decisions: ['look'] })
    expect(planWrite?.[1].plan.look).toBe('')
    expect(container.querySelector('[aria-label="Saved shared session summary"]').textContent)
      .toContain('Origin: user')
    expect(container.querySelector('[aria-label="Saved shared session summary"]').textContent)
      .toContain('No additional constraint')
    expect(state.revision).toBe(2)
    expect(state.posts.some(([url]) => /preparations|review\/approve|\/run$/.test(url))).toBe(false)
  })

  it('prevents an explicit empty save from discarding a plan edit made while its CAS is pending', async () => {
    const state = installSessionApi(planFor('manual'))
    let resolveSave
    state.handlePost = async (url, body, store) => {
      if (url !== `/api/sessions/${sessionId}/plan`) throw new Error(`Unexpected write: ${url}`)
      return new Promise((resolve) => {
        resolveSave = () => {
          store.plan = structuredClone(body.plan)
          for (const field of body.shared_decisions || []) {
            store.plan.authoring.shared_state[field] = { origin: 'user', evidence_id: null }
          }
          store.revision = body.expected_revision + 1
          resolve({ plan_revision: store.revision, conflicts: [] })
        }
      })
    }
    await act(async () => {
      root.render(
        <SessionView
          id={sessionId}
          initialSession={session}
          initialPlan={state.plan}
          initialRevision={1}
          initialActiveStep="constants"
          initialSharedSummary={sharedSummary(state.plan)}
        />,
      )
    })
    await flush()

    await act(async () => {
      const button = Array.from(container.querySelectorAll('button'))
        .find((candidate) => candidate.textContent.trim() === 'Choose no additional look constraint')
      button.click()
      await Promise.resolve()
    })
    await clickButton('Next: Takes →')
    const camera = labeledInput('Camera')
    const editAllowedDuringSave = !camera.disabled
    if (editAllowedDuringSave) setValue(camera, '24mm low-angle detail')
    expect(resolveSave).toBeTypeOf('function')
    await act(async () => { resolveSave() })
    await flush()

    const currentCamera = labeledInput('Camera')
    if (editAllowedDuringSave) {
      expect(currentCamera.value).toBe('24mm low-angle detail')
      expect(container.textContent).toContain('Plan has unsaved modifications.')
    } else {
      expect(currentCamera.value).toBe('50mm eye-level')
      expect(state.revision).toBe(2)
    }
    expect(state.posts.filter(([url]) => url === `/api/sessions/${sessionId}/plan`)).toHaveLength(1)
    expect(state.posts.some(([url]) => /preparations|review\/approve|\/run$/.test(url))).toBe(false)
  })

  it('does not report an empty choice saved after a later authoritative value replaced it', async () => {
    const state = installSessionApi(planFor('manual'))
    state.handlePost = async (url, body, store) => {
      if (url !== `/api/sessions/${sessionId}/plan`) throw new Error(`Unexpected write: ${url}`)
      store.plan = structuredClone(body.plan)
      store.plan.authoring.shared_state.look = { origin: 'user', evidence_id: null }
      store.revision = body.expected_revision + 1
      // The explicit empty CAS commits, then another tab updates the same field
      // before the first client can read back its result.
      store.plan.look = 'A newer look saved from another tab.'
      store.revision += 1
      throw new Error('The original save response was lost after a later edit.')
    }
    await act(async () => {
      root.render(
        <SessionView
          id={sessionId}
          initialSession={session}
          initialPlan={state.plan}
          initialRevision={1}
          initialActiveStep="constants"
          initialSharedSummary={sharedSummary(state.plan)}
        />,
      )
    })
    await flush()

    await clickButton('Choose no additional look constraint', container.querySelector('[aria-label="Shared choices"]'))
    await flush()

    const saved = container.querySelector('[aria-label="Saved shared session summary"]')
    expect(saved.textContent).toContain('A newer look saved from another tab.')
    expect(saved.textContent).toContain('Origin: user')
    expect(container.querySelector('[aria-label="Shared choices"] [role="status"]')?.textContent || '')
      .not.toContain('The empty shared choice was saved')
    expect(state.revision).toBe(3)
  })

  it('keeps proposals pending and retries an unknown acceptance with the identical payload', async () => {
    const state = installSessionApi(planFor('automatic'))
    const proposed = {
      look: 'A proposed soft studio look.',
      initial_wardrobe: 'A proposed blue jacket.',
    }
    let acceptCount = 0
    state.handlePost = async (url, body, store) => {
      if (url === `/api/sessions/${sessionId}/plan/authoring/operations`) {
        return suggestionOperationView('operation-task82', ['look', 'initial_wardrobe'], [
          { target: 'look', result: proposed.look },
          { target: 'initial_wardrobe', result: proposed.initial_wardrobe },
        ])
      }
      if (url.endsWith('/operation-task82/accept')) {
        acceptCount += 1
        if (acceptCount === 1) {
          store.plan.look = body.accepted.look
          store.plan.initial_wardrobe = body.accepted.initial_wardrobe
          store.plan.authoring.shared_state = {
            look: { origin: 'assistant_edited', evidence_id: 'operation-task82' },
            initial_wardrobe: { origin: 'assistant', evidence_id: 'operation-task82' },
          }
          store.plan.authoring.evidence = [{ id: 'operation-task82', kind: 'shared_choices' }]
          store.revision = 2
          throw new Error('Acceptance response was lost after the server saved it.')
        }
        return { plan_revision: 2, accepted: body.accepted }
      }
      throw new Error(`Unexpected write: ${url}`)
    }

    await act(async () => {
      root.render(
        <SessionView
          id={sessionId}
          initialSession={session}
          initialPlan={state.plan}
          initialRevision={1}
          initialActiveStep="constants"
          initialSharedSummary={sharedSummary(state.plan)}
        />,
      )
    })
    await flush()

    await clickButton('Generate shared suggestions', container.querySelector('[aria-label="Shared choices"]'))
    const savedSummary = container.querySelector('[aria-label="Saved shared session summary"]')
    expect(savedSummary.textContent).toContain('Origin: none')
    expect(savedSummary.textContent).not.toContain(proposed.look)
    expect(container.querySelector('#shared-proposal-look').value).toBe(proposed.look)
    expect(container.querySelector('#shared-proposal-initial_wardrobe').value)
      .toBe(proposed.initial_wardrobe)

    setValue(container.querySelector('#shared-proposal-look'), 'An operator-edited look.')
    await clickButton('Accept edited suggestions')
    expect(container.querySelector('[role="alert"]').textContent)
      .toContain('acceptance result is unknown')
    await clickButton('Retry acceptance')

    const operationCalls = state.posts.filter(([url]) => url.endsWith('/authoring/operations'))
    const acceptanceCalls = state.posts.filter(([url]) => url.endsWith('/operation-task82/accept'))
    expect(operationCalls).toHaveLength(1)
    expect(acceptanceCalls).toHaveLength(2)
    expect(acceptanceCalls[0][1]).toEqual(acceptanceCalls[1][1])
    expect(acceptanceCalls[1][1]).toEqual({
      expected_revision: 1,
      accepted: {
        look: 'An operator-edited look.',
        initial_wardrobe: proposed.initial_wardrobe,
      },
    })
    expect(state.revision).toBe(2)
    expect(container.querySelector('[aria-label="Saved shared session summary"]').textContent)
      .toContain('Origin: assistant_edited')
    expect(container.querySelector('[aria-label="Saved shared session summary"]').textContent)
      .toContain('An operator-edited look.')
    expect(state.posts.some(([url]) => /preparations|review\/approve|\/run$/.test(url))).toBe(false)
  })

  it('ignores a late suggestion start response after the saved plan revision advances', async () => {
    const state = installSessionApi(planFor('automatic'))
    let resolveStart
    state.handlePost = async (url, body, store) => {
      if (url === `/api/sessions/${sessionId}/plan/authoring/operations`) {
        return new Promise((resolve) => { resolveStart = resolve })
      }
      if (url === `/api/sessions/${sessionId}/plan`) {
        store.plan = structuredClone(body.plan)
        for (const field of body.shared_decisions || []) {
          store.plan.authoring.shared_state[field] = { origin: 'user', evidence_id: null }
        }
        store.revision = 2
        return { plan_revision: 2, conflicts: [] }
      }
      throw new Error(`Unexpected write: ${url}`)
    }
    await act(async () => {
      root.render(
        <SessionView
          id={sessionId}
          initialSession={session}
          initialPlan={state.plan}
          initialRevision={1}
          initialActiveStep="constants"
          initialSharedSummary={sharedSummary(state.plan)}
        />,
      )
    })
    await flush()

    await act(async () => {
      const button = Array.from(container.querySelectorAll('button'))
        .find((candidate) => candidate.textContent.trim() === 'Generate shared suggestions')
      button.click()
      await Promise.resolve()
    })
    await clickButton('Choose no additional look constraint', container.querySelector('[aria-label="Shared choices"]'))
    await flush()
    await act(async () => {
      resolveStart({
        operation_id: 'stale-operation',
        session_id: sessionId,
        plan_revision: 1,
        kind: 'shared_suggestions',
        state: 'succeeded',
        progress: { requested: ['look'] },
        result: { items: [{ target: 'look', result: 'Stale proposal.' }] },
      })
      await Promise.resolve()
    })
    await flush()

    expect(state.revision).toBe(2)
    expect(container.querySelector('[aria-label="Saved shared session summary"]').textContent)
      .toContain('Origin: user')
    expect(container.querySelector('#shared-proposal-look')).toBeFalsy()
    expect(state.posts.some(([url]) => url.endsWith('/accept'))).toBe(false)
  })

  it('does not adopt a late acceptance response after SessionView unmounts', async () => {
    const state = installSessionApi(planFor('automatic'))
    let resolveAcceptance
    state.handlePost = async (url) => {
      if (url === `/api/sessions/${sessionId}/plan/authoring/operations`) {
        return suggestionOperationView('unmount-operation', ['look'], [
          { target: 'look', result: 'A proposed look.' },
        ])
      }
      if (url.endsWith('/unmount-operation/accept')) {
        return new Promise((resolve) => { resolveAcceptance = resolve })
      }
      throw new Error(`Unexpected write: ${url}`)
    }
    await act(async () => {
      root.render(
        <SessionView
          id={sessionId}
          initialSession={session}
          initialPlan={state.plan}
          initialRevision={1}
          initialActiveStep="constants"
          initialSharedSummary={sharedSummary(state.plan)}
        />,
      )
    })
    await flush()
    await clickButton('Generate shared suggestions', container.querySelector('[aria-label="Shared choices"]'))
    await clickButton('Accept edited suggestions')
    const readsBeforeUnmount = api.get.mock.calls.length
    act(() => root.unmount())
    root = null

    await act(async () => {
      resolveAcceptance({ plan_revision: 2, accepted: { look: 'A proposed look.' } })
      await Promise.resolve()
    })
    await flush()

    expect(api.get).toHaveBeenCalledTimes(readsBeforeUnmount)
    expect(state.posts.filter(([url]) => url.endsWith('/accept'))).toHaveLength(1)
  })

  it('does not surface an old acceptance reload error after the view changes sessions', async () => {
    const state = installSessionApi(planFor('automatic'))
    const otherSessionId = sessionId + 1
    const otherSession = { ...session, id: otherSessionId, name: 'Another invented session' }
    const otherPlan = planFor('manual')
    otherPlan.look = 'A different saved look.'
    otherPlan.takes[0].camera = '85mm from the side'
    let holdOldReload = false
    let resolveOldSession
    let resolveOldPlan
    const originalGet = api.get.getMockImplementation()
    api.get.mockImplementation((url) => {
      if (url === `/api/sessions/${otherSessionId}`) return Promise.resolve(structuredClone(otherSession))
      if (url === `/api/sessions/${otherSessionId}/plan`) {
        return Promise.resolve({
          plan_revision: 7,
          plan: structuredClone(otherPlan),
          shared_summary: sharedSummary(otherPlan),
          conflicts: [],
          preparation: null,
          reviewed_revision: null,
        })
      }
      if (holdOldReload && url === `/api/sessions/${sessionId}`) {
        return new Promise((resolve) => { resolveOldSession = () => resolve(structuredClone(session)) })
      }
      if (holdOldReload && url === `/api/sessions/${sessionId}/plan`) {
        const acceptedPlan = planFor('automatic')
        acceptedPlan.look = 'An accepted look.'
        acceptedPlan.authoring.shared_state.look = { origin: 'assistant_edited', evidence_id: 'switch-operation' }
        return new Promise((resolve) => {
          resolveOldPlan = () => resolve({
            plan_revision: 2,
            plan: acceptedPlan,
            shared_summary: sharedSummary(acceptedPlan),
            conflicts: [],
            preparation: null,
            reviewed_revision: null,
          })
        })
      }
      return originalGet(url)
    })
    state.handlePost = async (url) => {
      if (url === `/api/sessions/${sessionId}/plan/authoring/operations`) {
        return suggestionOperationView('switch-operation', ['look'], [
          { target: 'look', result: 'A proposal to accept.' },
        ])
      }
      if (url.endsWith('/switch-operation/accept')) {
        holdOldReload = true
        return { plan_revision: 2, accepted: { look: 'An accepted look.' } }
      }
      throw new Error(`Unexpected write: ${url}`)
    }

    await act(async () => {
      root.render(
        <SessionView
          id={sessionId}
          initialSession={session}
          initialPlan={state.plan}
          initialRevision={1}
          initialActiveStep="constants"
          initialSharedSummary={sharedSummary(state.plan)}
        />,
      )
    })
    await flush()
    await clickButton('Generate shared suggestions', container.querySelector('[aria-label="Shared choices"]'))
    await clickButton('Accept edited suggestions')
    expect(resolveOldSession).toBeTypeOf('function')
    expect(resolveOldPlan).toBeTypeOf('function')

    await act(async () => {
      root.render(
        <SessionView
          id={otherSessionId}
          initialSession={otherSession}
          initialPlan={otherPlan}
          initialRevision={7}
          initialActiveStep="constants"
          initialSharedSummary={sharedSummary(otherPlan)}
        />,
      )
    })
    await flush()
    expect(container.querySelector('[role="alert"]')).toBeFalsy()
    expect(container.querySelector('[aria-label="Saved shared session summary"]').textContent)
      .toContain('A different saved look.')

    await act(async () => {
      resolveOldSession()
      resolveOldPlan()
      await Promise.resolve()
    })
    await flush()

    expect(container.querySelector('[role="alert"]')).toBeFalsy()
    expect(container.querySelector('[aria-label="Saved shared session summary"]').textContent)
      .toContain('A different saved look.')
    expect(container.querySelector('[aria-label="Saved shared session summary"]').textContent)
      .not.toContain('An accepted look.')
  })
})
