import { describe, expect, it } from 'vitest'
import { computePlanChangeImpact, getTakePreparationState, loadPlanReviews } from './sessionPlan.js'

const take = (take_id, pose = 'standing') => ({
  take_id,
  camera: '50mm eye level',
  framing: 'medium portrait',
  pose,
  expression: 'calm',
})

const plan = (takes, overrides = {}) => ({
  version: 'resource-v1',
  look: '',
  initial_wardrobe: '',
  selected_resources: [],
  wardrobe_changes: [],
  takes,
  ...overrides,
})

const automaticAuthoring = {
  mode: 'automatic',
  brief: 'Invented brief',
  scene_anchor: { library_key: 'invented_scenes', source_id: 'scene-01', content_digest: 'a'.repeat(64) },
  workflow_binding: { workflow_id: 4, kind: 'guide', graph_digest: 'b'.repeat(64), node_map_digest: 'c'.repeat(64) },
  variation_policy: { camera: { mode: 'vary' } },
}

describe('Task 8.5 plan-save impact preview', () => {
  it('reports plan-wide constant changes and preserves generated take history', () => {
    const before = plan([take('take-001'), take('take-002')])
    const after = { ...before, look: 'A quiet studio light' }
    const impact = computePlanChangeImpact(before, after, {
      completed: [
        { take_id: 'take-001', status: 'ready' },
        { take_id: 'take-002', status: 'generated', linked_shot_id: 81 },
      ],
    })

    expect(impact.reason).toBe('plan-wide')
    expect(impact.affectedTakeIds).toEqual(['take-001', 'take-002'])
    expect(impact.readyTakeIds).toEqual(['take-001'])
    expect(impact.generatedTakeIds).toEqual(['take-002'])
    expect(impact.requiredPreparationTakeIds).toEqual(['take-001'])
  })

  it('reports edits and reorders across automatic downstream context, including new work', () => {
    const before = plan(
      [take('take-001'), take('take-002'), take('take-003')],
      { authoring: automaticAuthoring },
    )
    const after = plan(
      [take('take-001'), take('take-002', 'seated'), take('take-003'), take('take-004')],
      { authoring: automaticAuthoring },
    )
    const editImpact = computePlanChangeImpact(before, after)
    expect(editImpact.affectedTakeIds).toEqual(['take-002', 'take-003', 'take-004'])
    expect(editImpact.addedTakeIds).toEqual(['take-004'])
    expect(editImpact.reason).toBe('automatic-downstream')

    const reordered = plan(
      [take('take-002'), take('take-001'), take('take-003')],
      { authoring: automaticAuthoring },
    )
    expect(computePlanChangeImpact(before, reordered).affectedTakeIds)
      .toEqual(['take-002', 'take-001', 'take-003'])
  })

  it('limits manual edits to direct takes while identifying added work separately', () => {
    const before = plan([take('take-001'), take('take-002')])
    const after = plan([take('take-001'), take('take-002', 'seated'), take('take-003')])
    const impact = computePlanChangeImpact(before, after)

    expect(impact.affectedTakeIds).toEqual(['take-002'])
    expect(impact.addedTakeIds).toEqual(['take-003'])
    expect(impact.reason).toBe('direct-input')
  })

  it('recognizes generated history after its take row is removed', () => {
    const before = plan([take('take-001')])
    const after = plan([])
    const preparation = {
      completed: [],
      history: [{ take_id: 'take-001', status: 'generated', linked_shot_id: 82 }],
    }
    const impact = computePlanChangeImpact(before, after, preparation)

    expect(impact.removedTakeIds).toEqual(['take-001'])
    expect(impact.generatedTakeIds).toEqual(['take-001'])
    expect(impact.requiredPreparationTakeIds).toEqual([])

    const restoredRow = computePlanChangeImpact(plan([]), before, preparation)
    expect(restoredRow.addedTakeIds).toEqual([])
    expect(restoredRow.generatedTakeIds).toEqual(['take-001'])
    expect(restoredRow.requiredPreparationTakeIds).toEqual([])
  })

  it('protects a stable take ID from re-preparation when generated history survives but the current revision is missing', () => {
    const before = plan([take('take-001')])
    const after = { ...before, look: 'Changed plan look' }
    const preparation = {
      completed: [],
      incomplete: [{ take_id: 'take-001', status: 'missing' }],
      history: [{ take_id: 'take-001', status: 'generated', linked_shot_id: 83 }],
    }
    const impact = computePlanChangeImpact(before, after, preparation)

    expect(impact.generatedTakeIds).toEqual(['take-001'])
    expect(impact.readyTakeIds).toEqual([])
    expect(impact.requiredPreparationTakeIds).toEqual([])
    expect(impact.pendingTakeIds).toEqual([])
    expect(getTakePreparationState('take-001', preparation)).toBe('generated')
    expect(getTakePreparationState('take-001', preparation, true)).toBe('generated')
  })

  it('preserves intentionally empty plan values and treats an unknown baseline conservatively', () => {
    const emptyPlan = plan([take('take-001')])
    const same = computePlanChangeImpact(emptyPlan, { ...emptyPlan, look: '' })
    expect(same.affectedTakeIds).toEqual([])

    const unknown = computePlanChangeImpact(null, emptyPlan)
    expect(unknown.baselineAvailable).toBe(false)
    expect(unknown.affectedTakeIds).toEqual(['take-001'])
    expect(unknown.addedTakeIds).toEqual(['take-001'])
  })

  it('preserves review error status and detail for the session diagnostic', async () => {
    const detail = 'authoring_evidence_invalid: prepared evidence is invalid'
    const result = await loadPlanReviews(85, 4, {
      get: async () => {
        throw Object.assign(new Error(detail), { status: 422, detail })
      },
    })

    expect(result.ok).toBe(false)
    expect(result.status).toBe(422)
    expect(result.detail).toBe(detail)
    expect(result.error).toBe(detail)
  })
})
