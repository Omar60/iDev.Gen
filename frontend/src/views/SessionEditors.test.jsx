// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SessionView from './SessionView.jsx'
import { api } from '../api.js'

let container, root
const session = { id: 860, name: 'Invented editor session', status: 'draft', model_id: 4,
  model: { id: 4, name: 'Invented character', trigger: 'invented trigger' }, workflow_id: 12,
  shots: [], tags: [], settings: { composition_mode: 'resource-v1', checkpoint: 'invented-model.safetensors', lora_strength: 0 } }
const original = { version: 'resource-v1', look: 'Saved look', initial_wardrobe: 'A blue coat.', selected_resources: [],
  wardrobe_changes: [{ take_id: 'take-001', scope: 'from_here', wardrobe: '' }],
  takes: [{ take_id: 'take-001', label: 'First', camera: '35mm', framing: '', pose: 'standing', expression: '' },
    { take_id: 'take-002', label: 'Second', camera: '', framing: 'full body', pose: '', expression: '', wardrobe_coverage: '' }] }
const drain = () => new Promise((resolve) => setTimeout(resolve, 0))
const click = async (target) => { expect(target).toBeTruthy(); await act(async () => { target.click(); await drain() }) }
const button = (text) => [...container.querySelectorAll('button')].find((item) => item.textContent.trim() === text)
const field = (id) => document.getElementById(id)
const edit = async (input, value) => act(async () => {
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value').set.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
})
const mount = async (step = 'takes', { latePlan, failSave = false } = {}) => {
  let saved = structuredClone(original), revision = 3
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    if (path === '/api/sessions/860') return session
    if (path === '/api/config' || path === '/api/comfy/models') return {}
    return []
  })
  vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => {
    if (path.endsWith('/full-auto')) return { current_run: null }
    if (path.endsWith('/plan')) return latePlan || { plan_revision: revision, plan: saved }
    return {}
  })
  const post = vi.spyOn(api, 'post').mockImplementation(async (path, body) => {
    expect(path).toBe('/api/sessions/860/plan')
    if (failSave) throw new Error('Fixture save unavailable')
    saved = structuredClone(body.plan)
    revision += 1
    return { plan_revision: revision, conflicts: [] }
  })
  const versioned = vi.spyOn(api, 'postVersioned')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  await act(async () => { root.render(<SessionView id={860} initialSession={session} initialPlan={original}
    initialRevision={3} initialActiveStep={step} />); await drain() })
  return { post, versioned }
}
afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null; container?.remove(); vi.restoreAllMocks()
})

describe('studio Character, Scene and Takes editors', () => {
  it('labels fixed character identity and preserves a zero LoRA value without writes', async () => {
    const { post, versioned } = await mount('character')
    expect(field('session-860-model').value).toBe(session.model.name)
    expect(field('session-860-model').disabled).toBe(true)
    expect(field('session-860-lora').value).toBe('0')
    for (const label of container.querySelectorAll('.session-stage-editor label[for]')) {
      expect(field(label.htmlFor)).toBeTruthy()
    }
    await click(button('Next: Scene / Constants →'))
    expect(field('session-860-look').value).toBe('Saved look')
    expect(post).not.toHaveBeenCalled(); expect(versioned).not.toHaveBeenCalled()
  })

  it('keeps IDs, values, explicit empty coverage and focus through add, reorder, removal and explicit CAS save', async () => {
    const { post, versioned } = await mount()
    await click(button('+ Add Take'))
    expect(document.activeElement).toBe(field('take-take-003-label'))
    for (const key of ['camera', 'framing', 'pose', 'expression']) expect(field(`take-take-003-${key}`).value).toBe('')
    await edit(field('take-take-003-label'), 'Third edited')
    await edit(field('take-take-003-camera'), '85mm')
    const third = container.querySelector('[data-take-id="take-003"]')
    await click(third.querySelector('[data-take-action="up"]'))
    expect(document.activeElement).toBe(field('take-take-003-label'))
    expect([...container.querySelectorAll('[data-take-id]')].map((item) => item.dataset.takeId)).toEqual(['take-001', 'take-003', 'take-002'])
    await click(container.querySelector('[data-take-id="take-001"] [data-take-action="remove"]'))
    expect(document.activeElement).toBe(field('take-take-003-label'))
    expect(post).not.toHaveBeenCalled()
    await click(button('Save Draft'))
    if (button('Save Plan Changes')) await click(button('Save Plan Changes'))
    expect(post).toHaveBeenCalledOnce()
    const body = post.mock.calls[0][1]
    expect(body.expected_revision).toBe(3)
    expect(body.plan.takes.map((take) => take.take_id)).toEqual(['take-003', 'take-002'])
    expect(body.plan.takes[0]).toEqual({ take_id: 'take-003', camera: '85mm', framing: '', pose: '', expression: '', label: 'Third edited' })
    expect(body.plan.takes[1]).toEqual(original.takes[1])
    expect(body.plan.wardrobe_changes).toEqual([])
    expect(body.plan.initial_wardrobe).toBe(original.initial_wardrobe)
    expect(field('take-take-003-camera').value).toBe('85mm')
    expect(versioned).toHaveBeenCalledOnce()
    expect(versioned.mock.calls[0][0]).toBe('/api/sessions/860/plan')
  })

  it('retains an editable draft and focuses a save error without approving or preparing', async () => {
    const { post, versioned } = await mount('constants', { failSave: true })
    await edit(field('session-860-look'), 'Unsaved corrected scene')
    await click(button('Save Draft'))
    if (button('Save Plan Changes')) await click(button('Save Plan Changes'))
    expect(field('session-860-look').value).toBe('Unsaved corrected scene')
    expect(document.activeElement.getAttribute('role')).toBe('alert')
    expect(document.activeElement.textContent).toContain('Fixture save unavailable')
    expect(container.textContent.split('Fixture save unavailable')).toHaveLength(2)
    await click(button('Dismiss error'))
    expect(container.textContent).not.toContain('Fixture save unavailable')
    expect(document.activeElement).toBe(field('session-860-look'))
    expect(post).toHaveBeenCalledOnce(); expect(versioned).toHaveBeenCalledOnce()
    await click(button('Next: Takes →'))
    await click(button('← Back: Scene / Constants'))
    expect(field('session-860-look').value).toBe('Unsaved corrected scene')
  })

  it('does not replace dirty take values when an earlier plan response arrives late', async () => {
    let resolve
    const latePlan = new Promise((done) => { resolve = done })
    const { post, versioned } = await mount('takes', { latePlan })
    await edit(field('take-take-002-label'), 'Newer local label')
    await act(async () => { resolve({ plan_revision: 3, plan: original }); await drain() })
    expect(field('take-take-002-label').value).toBe('Newer local label')
    expect([...container.querySelectorAll('[data-take-id]')].map((item) => item.dataset.takeId)).toEqual(['take-001', 'take-002'])
    expect(post).not.toHaveBeenCalled(); expect(versioned).not.toHaveBeenCalled()
  })
})
