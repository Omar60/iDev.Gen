// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, expect, it, vi } from 'vitest'
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { api } from '../api.js'
import { buildPlanSavePayload, computePlanChangeImpact, normalizePlan, reorderTakes, updateTake } from '../sessionPlan.js'
import SessionView from './SessionView.jsx'

const wardrobe = 'She wears a pleated skirt.'
const coverage = 'Her chest is bare; her hips are covered by the skirt; her feet are bare. No jacket is worn.'
const session = {
  id: 851, name: 'Invented coverage session', status: 'draft', shots: [], tags: [],
  model_id: 4, model: { id: 4, name: 'Invented model' }, workflow_id: 12,
  settings: { composition_mode: 'resource-v1' },
}
const basePlan = {
  version: 'resource-v1', look: '', initial_wardrobe: wardrobe,
  selected_resources: [], wardrobe_changes: [],
  takes: [
    { take_id: 'take-001', camera: '50mm', framing: 'full body', pose: 'standing', expression: 'calm' },
    { take_id: 'take-002', camera: '50mm', framing: 'full body', pose: 'sitting', expression: 'calm' },
  ],
}
let root
let container

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  container = null
  vi.restoreAllMocks()
})

it.each(['manual', 'automatic'])('edits and saves explicit coverage through CAS in %s mode', async (mode) => {
  let plan = { ...basePlan, authoring: { mode } }
  let revision = 3
  vi.spyOn(api, 'get').mockImplementation(async (path) => {
    if (path === `/api/sessions/${session.id}`) return session
    if (path === `/api/sessions/${session.id}/plan`) return { plan_revision: revision, plan }
    if (path === '/api/config' || path === '/api/comfy/models') return {}
    return []
  })
  const post = vi.spyOn(api, 'post').mockImplementation(async (path, body) => {
    expect(path).toBe(`/api/sessions/${session.id}/plan`)
    plan = body.plan
    revision = 4
    return { plan_revision: revision, conflicts: [] }
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(React.createElement(SessionView, {
    id: session.id, initialSession: session, initialPlan: plan,
    initialRevision: revision, initialActiveStep: 'takes',
  })))
  const input = container.querySelector('#coverage-take-001')
  expect(input).not.toBeNull()
  expect(input.maxLength).toBe(2000)
  expect(input.value).toBe('')
  expect(container.querySelector('#coverage-take-002').value).toBe('')
  expect(container.textContent).toContain('No detail is inferred')
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set.call(input, coverage)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  const save = [...container.querySelectorAll('button')].find((button) => button.textContent.includes('Save Draft'))
  await act(async () => save.click())
  expect(post).not.toHaveBeenCalled()
  expect(container.textContent).toContain('Affected takes: take-001')
  const confirm = [...container.querySelectorAll('button')].find((button) => button.textContent.includes('Save Plan Changes'))
  await act(async () => confirm.click())
  expect(post).toHaveBeenCalledOnce()
  const body = post.mock.calls[0][1]
  expect(body.expected_revision).toBe(3)
  expect(body.plan.takes[0].wardrobe_coverage).toBe(coverage)
  expect(body.plan.takes[1]).not.toHaveProperty('wardrobe_coverage')
  expect(body.plan.initial_wardrobe).toBe(wardrobe)
  expect(body.plan.wardrobe_changes).toEqual([])
})

it('reviews frozen coverage instead of later draft detail', () => {
  const html = renderToStaticMarkup(React.createElement(SessionView, {
    id: session.id, initialSession: session,
    initialPlan: { ...basePlan, takes: updateTake(basePlan.takes, 'take-001', { wardrobe_coverage: 'Later draft detail' }) },
    initialRevision: 4, initialActiveStep: 'review', initialExpandedTakeId: 'take-001',
    initialPreparation: {
      completed: [], incomplete: [{ take_id: 'take-001', status: 'missing' }],
      history: [{
        take_id: 'take-001', plan_revision: 3, status: 'generated', linked_shot_id: 884,
        final_prompt: `${wardrobe} ${coverage}`,
        effective_state: { wardrobe, wardrobe_coverage: coverage, take_choices: basePlan.takes[0] },
        provenance: { selected_resource_revisions: [] },
      }],
    },
  }))
  expect(html).toContain('Wardrobe coverage / detail')
  expect(html).toContain(coverage)
  expect(html).not.toContain('Later draft detail')
  expect(html).toContain('Generated [Shot #884]')
})

it('preserves per-take detail after reorder and flags changes using stable IDs', () => {
  const initial = { ...basePlan, takes: updateTake(basePlan.takes, 'take-001', { wardrobe_coverage: coverage }) }
  const reordered = { ...initial, takes: reorderTakes(initial.takes, 0, 1) }
  expect(buildPlanSavePayload(reordered, 4).plan.takes[1].wardrobe_coverage).toBe(coverage)
  const changed = { ...initial, takes: updateTake(initial.takes, 'take-001', { wardrobe_coverage: 'Bare feet.' }) }
  const impact = computePlanChangeImpact(initial, changed)
  expect(impact.affectedTakeIds).toContain('take-001')
  expect(normalizePlan(basePlan).takes[0]).not.toHaveProperty('wardrobe_coverage')
})

it.each([null, false, [], {}, 8, 'x'.repeat(2001), 'bad\u0000text', 'bad\u007ftext'])('refuses invalid coverage before saving: %j', (invalid) => {
  const invalidPlan = { ...basePlan, takes: updateTake(basePlan.takes, 'take-001', { wardrobe_coverage: invalid }) }
  expect(() => buildPlanSavePayload(invalidPlan, 3)).toThrow('Wardrobe coverage')
})
