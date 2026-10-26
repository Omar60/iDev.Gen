// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import SessionView from './SessionView.jsx'
import { api } from '../api.js'

let root, container
const photos = [
  { id: 801, shot_label: 'Portrait', status: 'done', seed: 17, rating: 4, prompt: 'Invented portrait', filename: 'portrait.png' },
  { id: 802, shot_label: 'WideStudyWithoutSpaces'.repeat(8), status: 'done', seed: 18, rating: 1, prompt: 'Invented landscape', filename: 'landscape.png', rejected: true, use_reference: true, reference_shot_ids: [801], reference_strength: 1.5 },
  { id: 803, shot_label: '', status: 'pending', seed: null, rating: 0 },
]
const drain = () => new Promise((resolve) => setTimeout(resolve, 0))
const click = async (element) => { expect(element).toBeTruthy(); await act(async () => { element.click(); await drain() }) }
const named = (name) => container.querySelector(`[aria-label="${name}"]`)
const mount = async ({ mode = 'legacy', shots = photos } = {}) => {
  let session = { id: 864, name: 'Invented gallery session', status: 'done', model_id: 4,
    model: { id: 4, name: 'Invented model', workflow_id: 12 }, workflow_id: 12,
    settings: { composition_mode: mode, anchor_shot_ids: [] }, shots: structuredClone(shots), tags: [] }
  const plan = { version: 'resource-v1', look: '', initial_wardrobe: '', selected_resources: [], wardrobe_changes: [], takes: [{ take_id: 'take-001' }] }
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    if (path === '/api/sessions/864') return structuredClone(session)
    if (path === '/api/sessions/865') return { ...session, id: 865, name: 'Second invented session', shots: [{ ...photos[0], id: 901, shot_label: 'Second session photo' }] }
    if (path === '/api/config' || path === '/api/comfy/models') return {}
    return []
  })
  vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => path.endsWith('/full-auto') ? { current_run: null } : path.endsWith('/plan') ? { plan_revision: 3, reviewed_revision: 3, plan } : {})
  const patch = vi.spyOn(api, 'patch').mockImplementation(async (path, body) => {
    const photo = session.shots.find((item) => path === `/api/shots/${item.id}`)
    if (photo) Object.assign(photo, body)
    else if (path === '/api/sessions/864') Object.assign(session.settings, body)
    return {}
  })
  const post = vi.spyOn(api, 'post').mockResolvedValue({})
  const del = vi.spyOn(api, 'del').mockImplementation(async (path) => { session.shots = session.shots.filter((shot) => path !== `/api/shots/${shot.id}`); return {} })
  vi.stubGlobal('confirm', vi.fn(() => false))
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  await act(async () => { root.render(<SessionView id={864} initialSession={session} initialPlan={plan}
    initialRevision={3} initialReviewedRevision={3} initialActiveStep="generation" />); await drain() })
  const changeSession = async () => act(async () => {
    root.render(<SessionView id={865} initialSession={{ ...session, id: 865 }} initialPlan={plan}
      initialRevision={3} initialReviewedRevision={3} initialActiveStep="generation" />)
    await drain()
  })
  return { patch, post, del, changeSession }
}
afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null; container?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals()
})

it('preserves photo identity/order, filter thresholds and explicit rating persistence', async () => {
  const { patch, post, del } = await mount()
  expect([...container.querySelectorAll('.photo-label')].map((item) => item.textContent)).toEqual(['Portrait', photos[1].shot_label, 'Shot #803'])
  await click(named('Rate shot 801 4 of 5; click current rating to clear'))
  expect(patch).toHaveBeenLastCalledWith('/api/shots/801', { rating: 0 })
  await click(named('Rate shot 801 5 of 5; click current rating to clear'))
  expect(patch).toHaveBeenLastCalledWith('/api/shots/801', { rating: 5 })
  const filter = named('Photo filter')
  await act(async () => { filter.value = 'picks'; filter.dispatchEvent(new Event('change', { bubbles: true })); await drain() })
  expect([...container.querySelectorAll('.photo-label')].map((item) => item.textContent)).toEqual(['Portrait'])
  expect(post).not.toHaveBeenCalled(); expect(del).not.toHaveBeenCalled()
})

it('keeps reject reversible and confirms deletion and reshoot independently', async () => {
  const { patch, post, del } = await mount()
  await click(named('Restore photo 802'))
  expect(patch).toHaveBeenLastCalledWith('/api/shots/802', { rejected: false })
  await click(named('Delete photo 801')); await click(named('Reshoot photo 801'))
  expect(del).not.toHaveBeenCalled(); expect(post).not.toHaveBeenCalled()
  vi.mocked(globalThis.confirm).mockReturnValue(true)
  await click(named('Reshoot photo 801'))
  expect(post).toHaveBeenLastCalledWith('/api/shots/801/reshoot')
  expect(del).not.toHaveBeenCalled()
  await click(named('Delete photo 801'))
  expect(del).toHaveBeenLastCalledWith('/api/shots/801')
  expect(container.querySelector('[aria-label="Open photo: Portrait"]')).toBeNull()
})

it('keeps variation and same-seed drafts explicit without dispatching generation', async () => {
  const { post } = await mount()
  await click(named('More like photo 801'))
  expect(container.textContent).toContain('Add')
  expect(post).not.toHaveBeenCalled()
  await click(named('Tweak photo 802 on the same seed'))
  expect(container.textContent).toContain('Strength')
  expect(post).not.toHaveBeenCalled()
})

it('shows pending actions, blocks duplicate writes and retains the photo after an uncertain error', async () => {
  const { patch } = await mount()
  let reject
  patch.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail }))
  await click(named('Reject photo 801'))
  expect(named('Reject photo 801').disabled).toBe(true)
  expect(container.querySelector('.shot[aria-busy="true"]')).toBeTruthy()
  await click(named('Reject photo 801'))
  expect(patch).toHaveBeenCalledOnce()
  await act(async () => { reject(new Error('Outcome could not be verified')); await drain() })
  expect([...container.querySelectorAll('.error')].filter((item) => item.textContent.includes('Outcome could not be verified'))).toHaveLength(1)
  expect(named('Open photo: Portrait')).toBeTruthy()
  expect(patch).toHaveBeenCalledOnce()
})

it('opens the same viewer in resource-v1 Generation and performs no writes on open/close', async () => {
  const { patch, post, del } = await mount({ mode: 'resource-v1' })
  const trigger = named('Open photo: Portrait'); trigger.focus()
  await click(trigger)
  expect(container.querySelector('dialog').open).toBe(true)
  expect(container.querySelector('#session-photo-title').textContent).toBe('Portrait')
  await click(named('Close photo viewer'))
  expect(container.querySelector('dialog')).toBeNull()
  expect(document.activeElement).toBe(trigger)
  expect(patch).not.toHaveBeenCalled(); expect(post).not.toHaveBeenCalled(); expect(del).not.toHaveBeenCalled()
})

it('ignores an old photo refresh after changing sessions and closes the old viewer', async () => {
  const { patch, changeSession } = await mount()
  let finish
  patch.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  await click(named('Reject photo 801'))
  await click(named('Open photo: Portrait'))
  expect(container.querySelector('dialog')).toBeTruthy()
  await changeSession()
  expect(container.querySelector('dialog')).toBeNull()
  expect(named('Open photo: Second session photo')).toBeTruthy()
  await act(async () => { finish({}); await drain() })
  expect(named('Open photo: Second session photo')).toBeTruthy()
  expect(named('Open photo: Portrait')).toBeNull()
  expect(patch).toHaveBeenCalledOnce()
})

it('keeps new-session continuation pending until its existing request settles', async () => {
  const { post } = await mount()
  let reject
  post.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail }))
  const select = named('Continue with photo 801')
  const choice = [...select.options].find((option) => option.value.startsWith('new:')).value
  await act(async () => { select.value = choice; select.dispatchEvent(new Event('change', { bubbles: true })); await drain() })
  expect(select.disabled).toBe(true)
  expect(container.querySelector('.shot[aria-busy="true"]')).toBeTruthy()
  expect(post).toHaveBeenCalledOnce()
  expect(post.mock.calls[0][0]).toBe('/api/sessions')
  await act(async () => { reject(new Error('Creation outcome unknown')); await drain() })
  expect(select.disabled).toBe(false)
  expect(post).toHaveBeenCalledOnce()
})

it('distinguishes an empty session from an empty filtered selection', async () => {
  const { post } = await mount({ shots: [] })
  expect(container.querySelector('.session-gallery').textContent).toContain('No photos have been generated')
  expect(container.querySelector('.session-gallery').textContent).toContain('Add takes or import a photo')
  expect(post).not.toHaveBeenCalled()
})
