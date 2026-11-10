// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../App.jsx'
import { api } from '../api.js'
import SessionView from './SessionView.jsx'

let container, root
const drain = () => new Promise((resolve) => setTimeout(resolve, 0))
const deferred = () => {
  let resolve, reject
  const promise = new Promise((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
const session = (id, tags) => ({
  id, name: `Session ${id}`, status: 'draft', model_id: 4, workflow_id: 12,
  model: { id: 4, name: 'Invented character', workflow_id: 12 },
  settings: { composition_mode: 'legacy', anchor_shot_ids: [] }, shots: [], tags,
})
const edit = async (field, value) => act(async () => {
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(field, value)
  field.dispatchEvent(new Event('input', { bubbles: true }))
})
const editTextarea = async (field, value) => act(async () => {
  Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set.call(field, value)
  field.dispatchEvent(new Event('input', { bubbles: true }))
})
const changeHash = async (hash) => {
  await act(async () => {
    window.location.hash = hash
    window.dispatchEvent(new Event('hashchange'))
    await drain()
  })
}
const traverse = async (direction, expectedHash) => {
  await act(async () => {
    window.history[direction]()
    window.dispatchEvent(new Event('hashchange'))
    for (let attempt = 0; attempt < 30 && window.location.hash !== expectedHash; attempt += 1) await drain()
  })
  expect(window.location.hash).toBe(expectedHash)
}
const mount = async (getSession) => {
  window.history.replaceState(null, '', '#/session/41')
  vi.spyOn(window, 'matchMedia').mockReturnValue({ addEventListener() {}, removeEventListener() {} })
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    if (path === '/api/sessions/41' || path === '/api/sessions/42') return getSession(path)
    if (path === '/api/comfy/status') return { online: false }
    if (path === '/api/config' || path === '/api/comfy/models') return {}
    if (path === '/api/wardrobe') return { garments: [], outfits: [] }
    return []
  })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => { root.render(<App />); await drain() })
}
const submitTakeIds = { 41: ['take-41', 'take-44'], 42: ['take-42', 'take-43'] }
const submitRevision = (id) => id === 41 ? 3 : 7
const submittedFixtureTakes = new Set()
const submitSession = (id) => ({
  ...session(id, []),
  name: `Submit session ${id}`,
  settings: { composition_mode: 'resource-v1' },
  shots: submitTakeIds[id].some((takeId) => submittedFixtureTakes.has(`${id}:${takeId}`))
    ? [{ id: id + 900, status: 'pending' }]
    : [],
})
const submitPlan = (id) => ({
  version: 'resource-v1', look: '', initial_wardrobe: '', selected_resources: [], wardrobe_changes: [],
  takes: submitTakeIds[id].map((takeId) => ({
    take_id: takeId, label: `Take ${takeId}`, camera: '50mm', framing: 'portrait',
    pose: 'standing', expression: 'neutral',
  })),
})
const submitPreparation = (id) => ({
  completed: submitTakeIds[id].map((take_id) => {
    const submitted = submittedFixtureTakes.has(`${id}:${take_id}`)
    return { take_id, status: submitted ? 'generated' : 'ready', linked_shot_id: submitted ? id + 900 : null }
  }),
  incomplete: [], history: [],
})
const submitPlanResponse = (id) => ({
  plan_revision: submitRevision(id),
  reviewed_revision: submitRevision(id),
  plan: submitPlan(id),
  preparation: submitPreparation(id),
  conflicts: [],
})
const mountSubmitFixtures = async () => {
  submittedFixtureTakes.clear()
  window.history.replaceState(null, '', '#/session/41?step=review')
  vi.spyOn(window, 'matchMedia').mockReturnValue({ addEventListener() {}, removeEventListener() {} })
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    const sessionMatch = path.match(/^\/api\/sessions\/(\d+)$/)
    if (sessionMatch) return submitSession(Number(sessionMatch[1]))
    const reviewMatch = path.match(/^\/api\/sessions\/(\d+)\/plan\/review\?plan_revision=(\d+)$/)
    if (reviewMatch) {
      const id = Number(reviewMatch[1])
      const revision = Number(reviewMatch[2])
      return {
        session_id: id,
        plan_revision: revision,
        reviewed_revision: submitRevision(id),
        takes: submitTakeIds[id].map((take_id) => ({ take_id, session_id: id, plan_revision: revision })),
      }
    }
    if (path === '/api/config') return { llm_ok: false }
    if (path === '/api/comfy/status') return { online: false }
    if (path === '/api/comfy/models') return {}
    if (path === '/api/wardrobe') return { garments: [], outfits: [] }
    if (path === '/api/rooms') return { rooms: [], libraries: [] }
    return []
  })
  vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => {
    const fullAutoMatch = path.match(/^\/api\/sessions\/(\d+)\/full-auto$/)
    if (fullAutoMatch) return { current_run: null }
    const planMatch = path.match(/^\/api\/sessions\/(\d+)\/plan$/)
    if (planMatch) return submitPlanResponse(Number(planMatch[1]))
    return {}
  })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => { root.render(<App />); await drain() })
}
const buttonNamed = (label) => [...container.querySelectorAll('button')]
  .find((button) => button.textContent.trim() === label)
const clickButton = async (label) => {
  const button = buttonNamed(label)
  expect(button).toBeTruthy()
  await act(async () => { button.click(); await drain() })
  return button
}
const takeCheckbox = (takeId) => [...container.querySelectorAll('tbody tr')]
  .find((row) => row.textContent.includes(takeId))?.querySelector('input[type="checkbox"]')
const selectTake = async (takeId) => {
  const checkbox = takeCheckbox(takeId)
  expect(checkbox).toBeTruthy()
  await act(async () => { checkbox.click(); await drain() })
  return checkbox
}
const deferNextPlanReload = (sessionId) => {
  const started = deferred()
  const pending = deferred()
  let held = false
  vi.mocked(api.getVersioned).mockImplementation(async (path) => {
    const fullAutoMatch = path.match(/^\/api\/sessions\/(\d+)\/full-auto$/)
    if (fullAutoMatch) return { current_run: null }
    const planMatch = path.match(/^\/api\/sessions\/(\d+)\/plan$/)
    if (planMatch) {
      const id = Number(planMatch[1])
      if (id === sessionId && !held) {
        held = true
        started.resolve()
        return pending.promise
      }
      return submitPlanResponse(id)
    }
    return {}
  })
  return { started, pending }
}
const startAtoBtoAWithPendingSubmitReload = async () => {
  await mountSubmitFixtures()
  const pendingSubmit = deferred()
  const post = vi.spyOn(api, 'post')
    .mockReturnValueOnce(pendingSubmit.promise)
    .mockResolvedValue({ submitted_count: 1 })
  await selectTake('take-41')
  await clickButton('Submit Test Selection (1)')
  await changeHash('#/session/42?step=review')
  await changeHash('#/session/41?step=review')
  expect(container.querySelector('.session-header h1').textContent).toBe('Submit session 41')

  submittedFixtureTakes.add('41:take-41')
  const planReload = deferNextPlanReload(41)
  await act(async () => {
    pendingSubmit.resolve({ submitted_count: 1 })
    await planReload.started.promise
    await drain()
  })
  return { post, planReload }
}

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  vi.restoreAllMocks()
  window.history.replaceState(null, '', '#/')
})

it('hides A while B loads, keeps tag drafts per session, and patches only the visible route', async () => {
  const a = session(41, ['from-A'])
  const b = session(42, ['from-B'])
  const pendingB = deferred()
  let bReads = 0
  await mount((path) => {
    if (path.endsWith('/41')) return a
    bReads += 1
    return bReads === 1 ? pendingB.promise : b
  })
  expect(container.querySelector('.session-header h1').textContent).toBe('Session 41')
  await edit(container.querySelector('[aria-label="New session tag"]'), 'draft-A')

  await changeHash('#/session/42')
  expect(container.querySelector('.session-header h1')).toBeNull()
  expect(container.querySelector('[aria-label="New session tag"]')).toBeNull()
  expect(container.textContent).toContain('Loading…')

  await act(async () => { pendingB.resolve(b); await drain() })
  expect(container.querySelector('.session-header h1').textContent).toBe('Session 42')
  const tagInput = container.querySelector('[aria-label="New session tag"]')
  expect(tagInput.value).toBe('')
  await edit(tagInput, 'new-from-B')
  const patch = vi.spyOn(api, 'patch').mockResolvedValue({})
  await act(async () => {
    tagInput.closest('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await drain()
  })
  expect(patch).toHaveBeenCalledTimes(1)
  expect(patch).toHaveBeenCalledWith('/api/sessions/42', { tags: ['from-B', 'new-from-B'] })

  await traverse('back', '#/session/41')
  expect(container.querySelector('.session-header h1').textContent).toBe('Session 41')
  expect(container.querySelector('[aria-label="New session tag"]').value).toBe('draft-A')
  await traverse('forward', '#/session/42')
  expect(container.querySelector('.session-header h1').textContent).toBe('Session 42')
  expect(container.querySelector('[aria-label="New session tag"]').value).toBe('')
})

it('ignores a late B response after browser Back returns to A', async () => {
  const a = session(41, ['from-A'])
  const b = session(42, ['from-B'])
  const pendingB = deferred()
  let bReads = 0
  await mount((path) => {
    if (path.endsWith('/41')) return a
    bReads += 1
    return bReads === 1 ? pendingB.promise : b
  })

  await changeHash('#/session/42')
  expect(container.querySelector('.session-header h1')).toBeNull()
  await traverse('back', '#/session/41')
  expect(container.querySelector('.session-header h1').textContent).toBe('Session 41')
  await act(async () => { pendingB.resolve(b); await drain() })
  expect(container.querySelector('.session-header h1').textContent).toBe('Session 41')
  expect(container.textContent).toContain('from-A')
  expect(container.textContent).not.toContain('from-B')
})

it('does not display a session payload whose ID differs from the requested route', async () => {
  await mount(() => session(41, ['from-A']))
  await changeHash('#/session/42')
  expect(container.querySelector('.session-header h1')).toBeNull()
  expect(container.querySelector('[aria-label="New session tag"]')).toBeNull()
  expect(container.textContent).toContain('The session response did not match the requested session.')
})

it('keeps a dirty resource-v1 plan with A and blocks displaying it under B', async () => {
  const resourceSession = (id) => ({ ...session(id, []), settings: { composition_mode: 'resource-v1' } })
  const plan = { version: 'resource-v1', look: 'Saved A look', initial_wardrobe: '', selected_resources: [],
    wardrobe_changes: [], takes: [{ take_id: 'take-41', label: 'Portrait', camera: '', framing: '', pose: '', expression: '' }] }
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    if (path === '/api/sessions/41') return resourceSession(41)
    if (path === '/api/sessions/42') return resourceSession(42)
    if (path === '/api/config' || path === '/api/comfy/models') return {}
    return []
  })
  vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => (
    path.endsWith('/full-auto') ? { current_run: null } : { plan_revision: 3, plan }
  ))
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => {
    root.render(<SessionView id={41} initialSession={resourceSession(41)} initialPlan={plan}
      initialRevision={3} initialActiveStep="constants" />)
    await drain()
  })
  await editTextarea(container.querySelector('#session-41-look'), 'Unsaved A look')

  await act(async () => { root.render(<SessionView id={42} />); await drain() })
  expect(container.textContent).toContain('Unsaved changes belong to Session 41.')
  expect(container.querySelector('h1')).toBeNull()
  expect(container.querySelector('textarea')).toBeNull()

  await act(async () => {
    root.render(<SessionView id={41} />)
    await drain()
  })
  expect(container.querySelector('#session-41-look').value).toBe('Unsaved A look')
})

it('keeps B selection and Review stage when A submission succeeds after navigation', async () => {
  await mountSubmitFixtures()
  const pendingA = deferred()
  const post = vi.spyOn(api, 'post').mockReturnValue(pendingA.promise)
  await selectTake('take-41')
  await clickButton('Submit Test Selection (1)')
  expect(post).toHaveBeenCalledWith('/api/sessions/41/plan/preparations/submit-selected', {
    plan_revision: 3, take_ids: ['take-41'],
  })

  await changeHash('#/session/42?step=review')
  expect(container.querySelector('.session-header h1').textContent).toBe('Submit session 42')
  await selectTake('take-42')

  submittedFixtureTakes.add('41:take-41')
  await act(async () => { pendingA.resolve({ submitted_count: 1 }); await drain() })
  expect(container.querySelector('.session-stage-heading h2').textContent).toBe('Current stage: Review')
  expect(takeCheckbox('take-42').checked).toBe(true)
  expect(container.textContent).not.toContain('Submitted 1 take(s) to queue')

  await changeHash('#/session/41?step=review')
  expect(container.querySelector('tbody').textContent).toContain('Generated [Shot #941]')
})

it('keeps each session POST bound to its selection and handles responses out of order without duplicates', async () => {
  await mountSubmitFixtures()
  const pendingA = deferred()
  const pendingB = deferred()
  const post = vi.spyOn(api, 'post').mockImplementation((path) => (
    path.startsWith('/api/sessions/41/') ? pendingA.promise : pendingB.promise
  ))
  await selectTake('take-41')
  const submitA = await clickButton('Submit Test Selection (1)')
  await act(async () => { submitA.click(); await drain() })
  expect(post).toHaveBeenCalledTimes(1)

  await changeHash('#/session/42?step=review')
  await selectTake('take-42')
  await selectTake('take-43')
  const submitB = await clickButton('Submit Test Selection (2)')
  await act(async () => { submitB.click(); await drain() })
  expect(post).toHaveBeenCalledTimes(2)
  expect(post).toHaveBeenNthCalledWith(1, '/api/sessions/41/plan/preparations/submit-selected', {
    plan_revision: 3, take_ids: ['take-41'],
  })
  expect(post).toHaveBeenNthCalledWith(2, '/api/sessions/42/plan/preparations/submit-selected', {
    plan_revision: 7, take_ids: ['take-42', 'take-43'],
  })

  submittedFixtureTakes.add('42:take-42')
  submittedFixtureTakes.add('42:take-43')
  await act(async () => { pendingB.resolve({ submitted_count: 2 }); await drain() })
  expect(container.querySelector('.session-stage-heading h2').textContent).toBe('Current stage: Generation')
  expect(container.textContent).toContain('Submitted 2 take(s) to queue')
  submittedFixtureTakes.add('41:take-41')
  await act(async () => { pendingA.resolve({ submitted_count: 1 }); await drain() })
  expect(container.querySelector('.session-stage-heading h2').textContent).toBe('Current stage: Generation')
  expect(container.textContent).toContain('Submitted 2 take(s) to queue')
  expect(container.textContent).not.toContain('Submitted 1 take(s) to queue')
  expect(post).toHaveBeenCalledTimes(2)
})

it('does not display a late A submission failure in B', async () => {
  await mountSubmitFixtures()
  const pendingA = deferred()
  const post = vi.spyOn(api, 'post').mockReturnValue(pendingA.promise)
  await selectTake('take-41')
  await clickButton('Submit Test Selection (1)')

  await changeHash('#/session/42?step=review')
  await act(async () => { pendingA.reject(new Error('A submission failed')); await drain() })
  expect(container.querySelector('.session-header h1').textContent).toBe('Submit session 42')
  expect(container.textContent).not.toContain('A submission failed')
  expect(container.querySelector('.session-stage-heading h2').textContent).toBe('Current stage: Review')
  expect(post).toHaveBeenCalledTimes(1)
})

it('ignores an A submission response after A→B→A creates a new session context', async () => {
  await mountSubmitFixtures()
  const pendingA = deferred()
  const post = vi.spyOn(api, 'post').mockReturnValue(pendingA.promise)
  await selectTake('take-41')
  await clickButton('Submit Test Selection (1)')

  await changeHash('#/session/42?step=review')
  await changeHash('#/session/41?step=review')
  expect(container.querySelector('.session-header h1').textContent).toBe('Submit session 41')

  submittedFixtureTakes.add('41:take-41')
  await act(async () => { pendingA.resolve({ submitted_count: 1 }); await drain() })
  expect(container.querySelector('.session-stage-heading h2').textContent).toBe('Current stage: Review')
  expect(container.textContent).not.toContain('Submitted 1 take(s) to queue')
  expect(container.querySelector('tbody').textContent).toContain('Generated [Shot #941]')
  expect(post).toHaveBeenCalledTimes(1)
})

it('preserves a new take selection made while A→B→A Submit is reconciling the plan', async () => {
  const { post, planReload } = await startAtoBtoAWithPendingSubmitReload()

  expect((await selectTake('take-44')).checked).toBe(true)
  await act(async () => { planReload.pending.resolve(submitPlanResponse(41)); await drain() })

  expect(takeCheckbox('take-44').checked).toBe(true)
  expect(post).toHaveBeenCalledTimes(1)
})

it('blocks another Submit for the same take until A→B→A plan reconciliation finishes', async () => {
  const { post, planReload } = await startAtoBtoAWithPendingSubmitReload()

  await selectTake('take-41')
  const submit = buttonNamed('Submitting…') || buttonNamed('Submit Test Selection (1)')
  expect(submit).toBeTruthy()
  await act(async () => { submit.click(); await drain() })

  expect(post).toHaveBeenCalledTimes(1)
  await act(async () => { planReload.pending.resolve(submitPlanResponse(41)); await drain() })
})

it('releases the Submit lock after an A→B→A plan reload fails', async () => {
  const { post, planReload } = await startAtoBtoAWithPendingSubmitReload()

  expect(buttonNamed('Submitting…')).toBeTruthy()
  await act(async () => { planReload.pending.reject(new Error('A plan refresh failed')); await drain() })

  expect(container.textContent).not.toContain('Submitting…')
  expect(container.textContent).toContain('A plan refresh failed')
  expect(post).toHaveBeenCalledTimes(1)
})

it('releases the Submit lock after a current-session submission error', async () => {
  await mountSubmitFixtures()
  const pendingSubmit = deferred()
  const post = vi.spyOn(api, 'post').mockReturnValue(pendingSubmit.promise)
  await selectTake('take-41')
  await clickButton('Submit Test Selection (1)')

  await act(async () => { pendingSubmit.reject(new Error('A submission failed')); await drain() })

  expect(container.querySelector('[role=alert]').textContent).toContain('A submission failed')
  expect(buttonNamed('Submit Test Selection (1)').disabled).toBe(false)
  expect(post).toHaveBeenCalledTimes(1)
})

it('keeps Submit locked through replacement Prepare session and plan reloads', async () => {
  const { post, planReload } = await startAtoBtoAWithPendingSubmitReload()
  const take44Row = [...container.querySelectorAll('tbody tr')]
    .find((row) => row.textContent.includes('take-44'))
  await act(async () => { take44Row.querySelector('button').click(); await drain() })
  expect(buttonNamed('Re-prepare Take')).toBeTruthy()

  const get = vi.mocked(api.get)
  const originalGet = get.getMockImplementation()
  const replacementSession = deferred()
  const replacementSessionStarted = deferred()
  let holdSession = true
  get.mockImplementation(async (path) => {
    if (path === '/api/sessions/41' && holdSession) {
      holdSession = false
      replacementSessionStarted.resolve()
      return replacementSession.promise
    }
    return originalGet(path)
  })
  const getVersioned = vi.mocked(api.getVersioned)
  const originalGetVersioned = getVersioned.getMockImplementation()
  const replacementPlan = deferred()
  const replacementPlanStarted = deferred()
  let holdPlan = true
  getVersioned.mockImplementation(async (path) => {
    if (path === '/api/sessions/41/plan' && holdPlan) {
      holdPlan = false
      replacementPlanStarted.resolve()
      return replacementPlan.promise
    }
    return originalGetVersioned(path)
  })
  const repeatedSubmit = deferred()
  post.mockImplementation((path) => (
    path.endsWith('/submit-selected') ? repeatedSubmit.promise : Promise.resolve({})
  ))

  await clickButton('Re-prepare Take')
  await act(async () => { await replacementSessionStarted.promise; await drain() })
  await selectTake('take-44')
  await act(async () => { planReload.pending.resolve(submitPlanResponse(41)); await drain() })

  expect.soft(buttonNamed('Submitting…')).toBeTruthy()
  const submit = buttonNamed('Submitting…') || [...container.querySelectorAll('button')]
    .find((button) => button.textContent.trim().startsWith('Submit Test Selection ('))
  expect(submit).toBeTruthy()
  await act(async () => { submit.click(); await drain() })
  expect.soft(post).toHaveBeenCalledTimes(2)
  expect(post).toHaveBeenNthCalledWith(1, '/api/sessions/41/plan/preparations/submit-selected', {
    plan_revision: 3, take_ids: ['take-41'],
  })
  expect(post).toHaveBeenNthCalledWith(2, '/api/sessions/41/plan/takes/take-44/prepare', {
    plan_revision: 3, manual_completion: null, adaptations: null,
  })

  await act(async () => {
    replacementSession.resolve(submitSession(41))
    await replacementPlanStarted.promise
    await drain()
  })
  expect(buttonNamed('Submitting…')).toBeTruthy()
  await act(async () => { replacementPlan.resolve(submitPlanResponse(41)); await drain() })

  expect(buttonNamed('Submitting…')).toBeFalsy()
  expect(takeCheckbox('take-44').checked).toBe(true)
  expect(takeCheckbox('take-41').disabled).toBe(true)
  expect(container.querySelector('tbody').textContent).toContain('Generated [Shot #941]')
  expect.soft(post).toHaveBeenCalledTimes(2)
  if (post.mock.calls.filter(([path]) => path.endsWith('/submit-selected')).length > 1) {
    repeatedSubmit.resolve({ submitted_count: 1 })
    await act(async () => { await drain(); await drain() })
  }
})

it('invalidates an old plan and recovers after the session GET returns another ID', async () => {
  await mountSubmitFixtures()
  const get = vi.mocked(api.get)
  const originalGet = get.getMockImplementation()
  let mismatchNextRead = false
  get.mockImplementation(async (path) => {
    if (path === '/api/sessions/41' && mismatchNextRead) {
      mismatchNextRead = false
      return submitSession(42)
    }
    return originalGet(path)
  })
  const post = vi.spyOn(api, 'post').mockResolvedValue({ submitted_count: 1 })
  await selectTake('take-41')
  submittedFixtureTakes.add('41:take-41')
  mismatchNextRead = true
  await clickButton('Submit Test Selection (1)')
  await act(async () => { await drain(); await drain() })

  expect.soft(container.querySelector('tbody')).toBeNull()
  expect.soft(container.textContent).toContain('did not match the requested session')
  const retry = buttonNamed('Retry loading session')
  expect.soft(retry).toBeTruthy()
  if (retry) {
    await act(async () => { retry.click(); await drain(); await drain() })
  }

  expect(container.querySelector('tbody').textContent).toContain('Generated [Shot #941]')
  expect(post).toHaveBeenCalledTimes(1)
})

it('preserves a failed session reload outcome and releases the Submit lock', async () => {
  await mountSubmitFixtures()
  const get = vi.mocked(api.get)
  const getImplementation = get.getMockImplementation()
  let failReload = true
  get.mockImplementation(async (path) => {
    if (path === '/api/sessions/41' && failReload) {
      failReload = false
      throw new Error('A session reload failed')
    }
    return getImplementation(path)
  })
  const pendingSubmit = deferred()
  const post = vi.spyOn(api, 'post').mockReturnValue(pendingSubmit.promise)
  await selectTake('take-41')
  await clickButton('Submit Test Selection (1)')

  submittedFixtureTakes.add('41:take-41')
  await act(async () => { pendingSubmit.resolve({ submitted_count: 1 }); await drain() })

  expect(container.textContent).toContain('A session reload failed')
  expect(container.textContent).not.toContain('Submitting…')
  expect(post).toHaveBeenCalledTimes(1)
  await clickButton('Retry loading session')
  await act(async () => { await drain(); await drain() })
  expect(container.querySelector('tbody').textContent).toContain('Generated [Shot #941]')
})
