import { describe, it, expect } from 'vitest'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import SessionView from './views/SessionView.jsx'
import {
  MODE_RESOURCE_V1,
  isResourceSession,
  normalizePlan,
  nextTakeId,
  createTake,
  updateTake,
  removeTake,
  buildPlanSavePayload,
  loadSessionPlan,
  executeSavePlan,
  canPreparePlan,
  canProceedToGeneration,
  canGenerateSession,
  isLegacyControlVisible,
  requiresCatalogueOrJudge,
  getAdvancedSettings,
  createSessionViewController,
} from './sessionPlan.js'

describe('sessionPlan pure helpers (Task 5.2)', () => {
  // 1. Correct detection of resource-v1 vs legacy
  describe('isResourceSession mode detection', () => {
    it('detects resource-v1 from top-level composition_mode', () => {
      expect(isResourceSession({ composition_mode: 'resource-v1' })).toBe(true)
    })

    it('detects resource-v1 from session.settings.composition_mode', () => {
      expect(isResourceSession({ settings: { composition_mode: 'resource-v1' } })).toBe(true)
    })

    it('treats missing, null, empty or unknown composition_mode as legacy', () => {
      expect(isResourceSession(null)).toBe(false)
      expect(isResourceSession({})).toBe(false)
      expect(isResourceSession({ composition_mode: '' })).toBe(false)
      expect(isResourceSession({ settings: {} })).toBe(false)
      expect(isResourceSession({ settings: { composition_mode: '' } })).toBe(false)
      expect(isResourceSession({ composition_mode: 'legacy' })).toBe(false)
      expect(isResourceSession({ settings: { composition_mode: 'other' } })).toBe(false)
    })
  })

  // 2. Loading and normalization without losing revision triples
  describe('normalizePlan preservation of triples', () => {
    it('preserves exact resource revision triples verbatim', () => {
      const triples = [
        {
          library_key: 'custom_room_library',
          source_id: 'studio_room_01',
          content_digest: 'sha256:abcd1234efgh5678',
        },
        {
          library_key: 'perspectives_pack',
          source_id: 'high_angle_02',
          content_digest: 'sha256:9876fedcba4321',
        },
      ]

      const normalized = normalizePlan({
        version: 'resource-v1',
        look: 'Soft natural daylight from tall windows.',
        initial_wardrobe: 'Linen shirt and dark trousers.',
        selected_resources: triples,
        takes: [{ take_id: 'take-001' }],
      })

      expect(normalized.selected_resources).toEqual(triples)
      expect(normalized.selected_resources[0].library_key).toBe('custom_room_library')
      expect(normalized.selected_resources[0].source_id).toBe('studio_room_01')
      expect(normalized.selected_resources[0].content_digest).toBe('sha256:abcd1234efgh5678')
      expect(normalized.selected_resources[1].content_digest).toBe('sha256:9876fedcba4321')
    })
  })

  // 3. Preservation of take_id
  describe('take_id preservation and stability', () => {
    it('preserves take_id during updates and editing', () => {
      const takes = [
        { take_id: 'take-001', camera: '35mm', framing: 'waist up', pose: 'standing', expression: 'neutral' },
        { take_id: 'take-002', camera: '50mm', framing: 'close up', pose: 'seated', expression: 'smile' },
      ]

      const updated = updateTake(takes, 'take-001', {
        camera: '85mm portrait lens',
        pose: 'leaning against chair',
        take_id: 'malicious-override', // Attempt to override take_id
      })

      expect(updated[0].take_id).toBe('take-001') // locked!
      expect(updated[0].camera).toBe('85mm portrait lens')
      expect(updated[0].pose).toBe('leaning against chair')
      expect(updated[1].take_id).toBe('take-002')
    })

    it('generates next stable take_id when creating new takes without altering existing ones', () => {
      const initialTakes = [
        { take_id: 'take-001', camera: 'wide' },
        { take_id: 'take-002', camera: 'close' },
      ]

      const nextId = nextTakeId(initialTakes)
      expect(nextId).toBe('take-003')

      const withNew = createTake(initialTakes, { camera: '50mm' })
      expect(withNew).toHaveLength(3)
      expect(withNew[0].take_id).toBe('take-001')
      expect(withNew[1].take_id).toBe('take-002')
      expect(withNew[2].take_id).toBe('take-003')
      expect(withNew[2].camera).toBe('50mm')
    })

    it('handles gap or custom numbered take_ids gracefully', () => {
      const takes = [{ take_id: 'take-007' }]
      expect(nextTakeId(takes)).toBe('take-008')
    })

    it('never removes the final take', () => {
      const single = [{ take_id: 'take-001', camera: 'wide' }]
      expect(removeTake(single, 'take-001')).toEqual(single)
    })
  })

  // 4. Editing constants and takes produces a schema accepted by backend
  describe('backend schema compatibility', () => {
    it('produces an exact schema conforming to backend PlanDraftIn / validate_draft', () => {
      const rawPlan = {
        version: MODE_RESOURCE_V1,
        look: 'Warm sunset lighting through sheer curtains.',
        initial_wardrobe: 'Dark navy suit and leather shoes.',
        takes: [
          {
            take_id: 'take-001',
            label: 'portrait',
            camera: '85mm f/1.4 prime',
            framing: 'bust shot',
            pose: 'three-quarter profile',
            expression: 'subtle smile',
          },
        ],
        selected_resources: [
          {
            library_key: 'scenes_v1',
            source_id: 'scene_001',
            content_digest: 'sha256:112233445566',
          },
        ],
        wardrobe_changes: [],
      }

      const payload = buildPlanSavePayload(rawPlan, 2)
      expect(payload).toHaveProperty('plan')
      expect(payload).toHaveProperty('expected_revision', 2)

      const p = payload.plan
      expect(p.version).toBe('resource-v1')
      expect(typeof p.look).toBe('string')
      expect(typeof p.initial_wardrobe).toBe('string')
      expect(Array.isArray(p.takes)).toBe(true)
      expect(Array.isArray(p.selected_resources)).toBe(true)
      expect(Array.isArray(p.wardrobe_changes)).toBe(true)

      // Verify each take has non-empty take_id
      for (const t of p.takes) {
        expect(typeof t.take_id).toBe('string')
        expect(t.take_id.length).toBeGreaterThan(0)
      }

      // Verify selected resources structure
      for (const r of p.selected_resources) {
        expect(r).toHaveProperty('library_key')
        expect(r).toHaveProperty('source_id')
        expect(r).toHaveProperty('content_digest')
      }
    })
  })

  // 5. Save includes the current expected_revision
  describe('CAS expected_revision inclusion', () => {
    it('includes the provided expected_revision in the payload', () => {
      const plan = {
        version: MODE_RESOURCE_V1,
        look: 'Look',
        initial_wardrobe: 'Wardrobe',
        takes: [{ take_id: 'take-001' }],
        selected_resources: [],
      }

      const payload0 = buildPlanSavePayload(plan, 0)
      expect(payload0.expected_revision).toBe(0)

      const payload5 = buildPlanSavePayload(plan, 5)
      expect(payload5.expected_revision).toBe(5)
    })

    it('rejects invalid or negative expected_revision', () => {
      const plan = { version: MODE_RESOURCE_V1, takes: [{ take_id: 'take-001' }] }
      expect(() => buildPlanSavePayload(plan, -1)).toThrow()
      expect(() => buildPlanSavePayload(plan, 'bad')).toThrow()
    })
  })

  // 6. Response with new revision updates the revision used for the next save
  describe('CAS revision state transition', () => {
    it('chains consecutive CAS saves correctly', () => {
      let currentRevision = 0
      const plan = {
        version: MODE_RESOURCE_V1,
        look: 'Look 1',
        initial_wardrobe: 'Wardrobe 1',
        takes: [{ take_id: 'take-001' }],
        selected_resources: [],
      }

      // First save
      const firstSavePayload = buildPlanSavePayload(plan, currentRevision)
      expect(firstSavePayload.expected_revision).toBe(0)

      // Simulate backend response bumping revision to 1
      const firstBackendResponse = { plan_revision: 1, conflicts: [] }
      currentRevision = firstBackendResponse.plan_revision
      expect(currentRevision).toBe(1)

      // Second save with updated constants
      plan.look = 'Updated Look 2'
      const secondSavePayload = buildPlanSavePayload(plan, currentRevision)
      expect(secondSavePayload.expected_revision).toBe(1)
      expect(secondSavePayload.plan.look).toBe('Updated Look 2')

      // Simulate backend response bumping revision to 2
      const secondBackendResponse = { plan_revision: 2, conflicts: [] }
      currentRevision = secondBackendResponse.plan_revision
      expect(currentRevision).toBe(2)
    })
  })

  // 7. Repeated creative choices are valid
  describe('repeated creative choices validity', () => {
    it('allows identical camera, framing, pose, and expression across multiple takes', () => {
      const plan = {
        version: MODE_RESOURCE_V1,
        look: 'Studio light',
        initial_wardrobe: 'Casual clothes',
        takes: [
          {
            take_id: 'take-001',
            camera: '50mm prime at eye level',
            framing: 'close-up portrait',
            pose: 'facing camera directly',
            expression: 'neutral gaze',
          },
          {
            take_id: 'take-002',
            camera: '50mm prime at eye level', // intentional repeat
            framing: 'close-up portrait',      // intentional repeat
            pose: 'facing camera directly',    // intentional repeat
            expression: 'neutral gaze',        // intentional repeat
          },
          {
            take_id: 'take-003',
            camera: '50mm prime at eye level', // intentional repeat
            framing: 'close-up portrait',      // intentional repeat
            pose: 'slight head tilt',
            expression: 'neutral gaze',        // intentional repeat
          },
        ],
        selected_resources: [],
      }

      const normalized = normalizePlan(plan)
      expect(normalized.takes).toHaveLength(3)
      expect(normalized.takes[0].camera).toBe(normalized.takes[1].camera)
      expect(normalized.takes[0].framing).toBe(normalized.takes[1].framing)
      expect(normalized.takes[0].pose).toBe(normalized.takes[1].pose)
      expect(normalized.takes[0].expression).toBe(normalized.takes[1].expression)

      // Payload is generated without error and preserves repeated choices
      const payload = buildPlanSavePayload(normalized, 1)
      expect(payload.plan.takes[0].camera).toBe(payload.plan.takes[1].camera)
    })
  })

  // 8. Resource-v1 does not depend on Catalogue or Judge data
  describe('catalogue independence', () => {
    it('prepares and normalizes plans without any measured components or catalogue structures', () => {
      // Empty catalogue / zero measured components simulated
      const emptyCatalogueSession = {
        id: 42,
        model_id: 1,
        name: 'Independent Resource Session',
        composition_mode: 'resource-v1',
        manner: '',
        checkpoint: '',
        settings: { composition_mode: 'resource-v1' },
      }

      expect(isResourceSession(emptyCatalogueSession)).toBe(true)

      const plan = normalizePlan({
        version: 'resource-v1',
        look: 'Plain daylight',
        initial_wardrobe: 'Simple dress',
        takes: [
          { take_id: 'take-001', camera: 'custom camera', framing: 'custom framing' },
        ],
        selected_resources: [{ library_key: 'lib1', source_id: 'src1', content_digest: 'dig1' }],
      })

      // No calls or properties from catalogue
      expect(plan.takes[0].camera).toBe('custom camera')
      expect(plan.takes[0].framing).toBe('custom framing')
      expect(plan).not.toHaveProperty('cells')
      expect(plan).not.toHaveProperty('manner')
      expect(plan).not.toHaveProperty('judge')
    })
  })

  // 9. Legacy preserves previous behavior
  describe('legacy session preservation', () => {
    it('leaves legacy sessions unflagged for resource planning', () => {
      const legacySession1 = {
        id: 10,
        model_id: 2,
        name: 'Legacy Directed Shoot',
        manner: 'directed',
        checkpoint: 'sd_xl_base.safetensors',
        settings: { width: 1024, height: 1024 },
      }

      const legacySession2 = {
        id: 11,
        model_id: 3,
        name: 'Legacy Unspecified',
        composition_mode: '',
        settings: { composition_mode: '' },
      }

      expect(isResourceSession(legacySession1)).toBe(false)
      expect(isResourceSession(legacySession2)).toBe(false)
    })
  })

  // 10. canProceedToGeneration guard requirements
  describe('canProceedToGeneration guard contract', () => {
    const validResourceSession = {
      id: 1,
      composition_mode: 'resource-v1',
      workflow_id: 10,
    }

    const validPlan = {
      version: 'resource-v1',
      look: 'Soft daylight',
      initial_wardrobe: 'Grey knit sweater',
      takes: [{ take_id: 'take-001', camera: '50mm' }],
      selected_resources: [],
    }

    it('unconditionally permits proceeding to generation for legacy sessions', () => {
      const legacySession = { id: 2, composition_mode: '' }
      expect(canProceedToGeneration(legacySession, null)).toBe(true)
      expect(canProceedToGeneration(legacySession, {})).toBe(true)
    })

    it('denies proceeding to generation for resource-v1 when plan is missing or invalid', () => {
      expect(canProceedToGeneration(validResourceSession, null)).toBe(false)
      expect(canProceedToGeneration(validResourceSession, {})).toBe(false)
      expect(canProceedToGeneration(validResourceSession, { plan: null, planRevision: 1 })).toBe(false)
      expect(canProceedToGeneration(validResourceSession, { plan: validPlan, planRevision: null })).toBe(false)
      expect(canProceedToGeneration(validResourceSession, { plan: validPlan, planRevision: -1 })).toBe(false)
    })

    it('denies proceeding to generation when plan has unsaved local edits (planDirty)', () => {
      expect(canProceedToGeneration(validResourceSession, {
        plan: validPlan,
        planRevision: 2,
        planDirty: true,
        conflicts: [],
      })).toBe(false)
    })

    it('denies proceeding to generation when unresolved conflicts are present', () => {
      expect(canProceedToGeneration(validResourceSession, {
        plan: validPlan,
        planRevision: 2,
        planDirty: false,
        conflicts: [{ type: 'mismatch', source_id: 'src1' }],
      })).toBe(false)
    })

    it('denies proceeding to generation when plan has no planned takes', () => {
      const emptyTakesPlan = { ...validPlan, takes: [] }
      expect(canProceedToGeneration(validResourceSession, {
        plan: emptyTakesPlan,
        planRevision: 2,
        planDirty: false,
        conflicts: [],
      })).toBe(false)
    })

    it('denies proceeding to generation when session lacks workflow assignment in all scopes', () => {
      const sessionWithoutWorkflow = {
        id: 3,
        composition_mode: 'resource-v1',
        workflow_id: null,
        model: { workflow_id: null },
        settings: { workflow_id: null },
      }
      expect(canProceedToGeneration(sessionWithoutWorkflow, {
        plan: validPlan,
        planRevision: 2,
        planDirty: false,
        conflicts: [],
      })).toBe(false)
    })

    it('permits proceeding to generation when all resource-v1 review preconditions are satisfied', () => {
      expect(canProceedToGeneration(validResourceSession, {
        plan: validPlan,
        planRevision: 2,
        planDirty: false,
        conflicts: [],
      })).toBe(true)
    })
  })

  // 11. canGenerateSession execution readiness contract
  describe('canGenerateSession execution readiness contract', () => {
    const validResourceSession = {
      id: 1,
      composition_mode: 'resource-v1',
      workflow_id: 10,
      shots: [{ id: 101, status: 'pending' }],
    }

    const validPlan = {
      version: 'resource-v1',
      look: 'Soft daylight',
      initial_wardrobe: 'Grey knit sweater',
      takes: [{ take_id: 'take-001', camera: '50mm' }],
      selected_resources: [],
    }

    it('denies execution when session is running', () => {
      expect(canGenerateSession({ ...validResourceSession, status: 'running' }, {
        plan: validPlan,
        planRevision: 2,
        planDirty: false,
        conflicts: [],
        activeStep: 'generation',
        reviewedRevision: 2,
        pending: 1,
      })).toBe(false)
    })

    it('denies execution when pending === 0, proving reaching Generation does not authorize Run', () => {
      // Session with reviewed plan and active in generation, but zero pending shots
      const sessionWithNoPending = {
        ...validResourceSession,
        shots: [],
      }
      expect(canGenerateSession(sessionWithNoPending, {
        plan: validPlan,
        planRevision: 2,
        planDirty: false,
        conflicts: [],
        activeStep: 'generation',
        reviewedRevision: 2,
        pending: 0,
      })).toBe(false)
    })

    it('permits execution for legacy sessions only when pending > 0 and not running', () => {
      const legacyWithPending = { id: 2, composition_mode: '', shots: [{ id: 1, status: 'pending' }] }
      const legacyWithoutPending = { id: 2, composition_mode: '', shots: [] }

      expect(canGenerateSession(legacyWithPending)).toBe(true)
      expect(canGenerateSession(legacyWithoutPending)).toBe(false)
      expect(canGenerateSession({ ...legacyWithPending, status: 'running' })).toBe(false)
    })

    it('denies execution for resource-v1 when plan review is missing, dirty, or stale', () => {
      // unreviewed
      expect(canGenerateSession(validResourceSession, {
        plan: validPlan,
        planRevision: 2,
        planDirty: false,
        conflicts: [],
        activeStep: 'generation',
        reviewedRevision: null,
        pending: 1,
      })).toBe(false)

      // dirty local changes
      expect(canGenerateSession(validResourceSession, {
        plan: validPlan,
        planRevision: 2,
        planDirty: true,
        conflicts: [],
        activeStep: 'generation',
        reviewedRevision: 2,
        pending: 1,
      })).toBe(false)

      // stale review (revision bumped to 3 after save)
      expect(canGenerateSession(validResourceSession, {
        plan: validPlan,
        planRevision: 3,
        planDirty: false,
        conflicts: [],
        activeStep: 'generation',
        reviewedRevision: 2,
        pending: 1,
      })).toBe(false)

      // not in generation step
      expect(canGenerateSession(validResourceSession, {
        plan: validPlan,
        planRevision: 2,
        planDirty: false,
        conflicts: [],
        activeStep: 'review',
        reviewedRevision: 2,
        pending: 1,
      })).toBe(false)
    })

    it('permits execution when real pending shots exist AND review/plan conditions are fully met', () => {
      expect(canGenerateSession(validResourceSession, {
        plan: validPlan,
        planRevision: 2,
        planDirty: false,
        conflicts: [],
        activeStep: 'generation',
        reviewedRevision: 2,
        pending: 1,
      })).toBe(true)
    })
  })
})

describe('API persistence and CAS operations (loadSessionPlan & executeSavePlan)', () => {
  it('loadSessionPlan loads valid plan and revision from backend', async () => {
    const mockApi = {
      get: async (p) => {
        expect(p).toBe('/api/sessions/10/plan')
        return {
          plan_revision: 3,
          plan: {
            version: 'resource-v1',
            look: 'Morning light',
            initial_wardrobe: 'Coat',
            takes: [{ take_id: 'take-001' }],
            selected_resources: [],
          },
        }
      },
    }
    const res = await loadSessionPlan(10, mockApi)
    expect(res.ok).toBe(true)
    expect(res.planRevision).toBe(3)
    expect(res.plan.look).toBe('Morning light')
    expect(res.error).toBeNull()
  })

  it('loadSessionPlan fails visibly and never creates fallback plan when backend gives 404', async () => {
    const mockApi = {
      get: async () => {
        throw new Error('no plan draft for this session')
      },
    }
    const res = await loadSessionPlan(10, mockApi)
    expect(res.ok).toBe(false)
    expect(res.plan).toBeNull()
    expect(res.planRevision).toBeNull()
    expect(res.error).toBe('no plan draft for this session')
  })

  it('loadSessionPlan fails visibly when backend returns response without valid plan_revision', async () => {
    const mockApi = {
      get: async () => ({
        plan: { version: 'resource-v1', takes: [{ take_id: 'take-001' }] },
      }),
    }
    const res = await loadSessionPlan(10, mockApi)
    expect(res.ok).toBe(false)
    expect(res.plan).toBeNull()
    expect(res.planRevision).toBeNull()
    expect(res.error).toContain('missing valid plan_revision')
  })

  it('executeSavePlan requires a valid integer plan_revision from backend and never invents expectedRevision + 1', async () => {
    const mockApiWithoutRevision = {
      post: async () => ({
        conflicts: [],
      }),
    }

    const plan = { version: 'resource-v1', takes: [{ take_id: 'take-001' }] }
    const res = await executeSavePlan(10, plan, 4, mockApiWithoutRevision)

    expect(res.ok).toBe(false)
    expect(res.error).toContain('backend did not provide a valid plan_revision')
    expect(res.planRevision).toBe(4)
  })

  it('executeSavePlan surfaces CAS 409 conflict and preserves prior revision', async () => {
    const mockApiConflict = {
      post: async () => {
        throw new Error('session 10 plan revision is 5, expected 4; refusing to overwrite newer draft')
      },
    }

    const plan = { version: 'resource-v1', takes: [{ take_id: 'take-001' }] }
    const res = await executeSavePlan(10, plan, 4, mockApiConflict)

    expect(res.ok).toBe(false)
    expect(res.error).toContain('refusing to overwrite newer draft')
    expect(res.planRevision).toBe(4)
  })
})

describe('SessionView screen controller contract (createSessionViewController)', () => {
  it('surfaces GET /plan failures visibly and blocks preparation/generation', async () => {
    const mockApi = {
      get: async (path) => {
        if (path === '/api/sessions/100') {
          return { id: 100, composition_mode: 'resource-v1', settings: { composition_mode: 'resource-v1' }, shots: [] }
        }
        if (path === '/api/sessions/100/plan') {
          throw new Error('no plan draft for this session')
        }
      },
    }

    const controller = createSessionViewController(100, { api: mockApi })
    const state = await controller.reload()

    expect(state.error).toBe('no plan draft for this session')
    expect(state.plan).toBeNull()
    expect(state.planRevision).toBeNull()
    expect(state.canPrepare).toBe(false)
    expect(state.canGenerate).toBe(false)
    expect(controller.navigateStep('constants')).toBe(false)
    expect(controller.navigateStep('generation')).toBe(false)
    expect(controller.getState().error).toContain('plan draft is incomplete or missing')
    expect(controller.isControlVisible('incomplete_plan_notice')).toBe(true)
    expect(controller.isControlVisible('character_step')).toBe(false)
  })

  it('loads valid draft with authentic backend plan_revision and allows preparation but keeps generation guarded until review', async () => {
    const mockApi = {
      get: async (path) => {
        if (path === '/api/sessions/101') {
          return { id: 101, composition_mode: 'resource-v1', workflow_id: 1, settings: { composition_mode: 'resource-v1' }, shots: [] }
        }
        if (path === '/api/sessions/101/plan') {
          return {
            plan_revision: 7,
            plan: {
              version: 'resource-v1',
              look: 'Diffused window light in gallery',
              initial_wardrobe: 'Tailored wool coat',
              takes: [{ take_id: 'take-001', camera: '50mm', framing: 'portrait' }],
              selected_resources: [
                { library_key: 'gallery_rooms', source_id: 'room_01', content_digest: 'sha256:aaa111' },
              ],
            },
          }
        }
      },
    }

    const controller = createSessionViewController(101, { api: mockApi })
    const state = await controller.reload()

    expect(state.error).toBe('')
    expect(state.planRevision).toBe(7)
    expect(state.plan.look).toBe('Diffused window light in gallery')
    expect(state.canPrepare).toBe(true)
    expect(state.canGenerate).toBe(false)
    expect(controller.isControlVisible('run_button')).toBe(false)
    expect(controller.isControlVisible('character_step')).toBe(true)
    expect(controller.isControlVisible('incomplete_plan_notice')).toBe(false)
  })

  it('sends current plan_revision as expected_revision and updates to backend-confirmed revision on save', async () => {
    let sentBody = null
    let currentBackendRevision = 3

    const mockApi = {
      get: async (path) => {
        if (path === '/api/sessions/102') {
          return { id: 102, composition_mode: 'resource-v1', settings: { composition_mode: 'resource-v1' }, shots: [] }
        }
        if (path === '/api/sessions/102/plan') {
          return {
            plan_revision: currentBackendRevision,
            plan: {
              version: 'resource-v1',
              look: 'Initial Look',
              initial_wardrobe: 'Initial Wardrobe',
              takes: [{ take_id: 'take-001' }],
              selected_resources: [],
            },
          }
        }
      },
      post: async (path, body) => {
        sentBody = body
        currentBackendRevision = 4
        return { plan_revision: 4, conflicts: [] }
      },
    }

    const controller = createSessionViewController(102, { api: mockApi })
    await controller.reload()
    expect(controller.getState().planRevision).toBe(3)

    controller.editConstants('Updated Look', 'Updated Wardrobe')
    expect(controller.getState().planDirty).toBe(true)

    const saveRes = await controller.savePlan()
    expect(saveRes.ok).toBe(true)
    expect(sentBody.expected_revision).toBe(3)
    expect(controller.getState().planRevision).toBe(4)
    expect(controller.getState().planDirty).toBe(false)
  })

  it('fails visibly and does not advance revision if save response lacks valid plan_revision', async () => {
    const mockApi = {
      get: async (path) => {
        if (path === '/api/sessions/103') {
          return { id: 103, composition_mode: 'resource-v1', settings: { composition_mode: 'resource-v1' }, shots: [] }
        }
        if (path === '/api/sessions/103/plan') {
          return {
            plan_revision: 5,
            plan: { version: 'resource-v1', takes: [{ take_id: 'take-001' }], selected_resources: [] },
          }
        }
      },
      post: async () => ({
        conflicts: [],
      }),
    }

    const controller = createSessionViewController(103, { api: mockApi })
    await controller.reload()
    expect(controller.getState().planRevision).toBe(5)

    const saveRes = await controller.savePlan()
    expect(saveRes.ok).toBe(false)
    expect(controller.getState().error).toContain('backend did not provide a valid plan_revision')
    expect(controller.getState().planRevision).toBe(5)
  })

  it('surfaces CAS conflict visibly on 409 and does not advance revision', async () => {
    const mockApi = {
      get: async (path) => {
        if (path === '/api/sessions/104') {
          return { id: 104, composition_mode: 'resource-v1', settings: { composition_mode: 'resource-v1' }, shots: [] }
        }
        if (path === '/api/sessions/104/plan') {
          return {
            plan_revision: 2,
            plan: { version: 'resource-v1', takes: [{ take_id: 'take-001' }], selected_resources: [] },
          }
        }
      },
      post: async () => {
        throw new Error('session 104 plan revision is 3, expected 2; refusing to overwrite newer draft')
      },
    }

    const controller = createSessionViewController(104, { api: mockApi })
    await controller.reload()
    expect(controller.getState().planRevision).toBe(2)

    const saveRes = await controller.savePlan()
    expect(saveRes.ok).toBe(false)
    expect(controller.getState().error).toContain('refusing to overwrite newer draft')
    expect(controller.getState().planRevision).toBe(2)
  })

  it('preserves take_ids and immutable resource triples across load -> edit -> save', async () => {
    let savedPayload = null
    const originalTriples = [
      { library_key: 'rooms_v1', source_id: 'loft_01', content_digest: 'sha256:abc12345' },
    ]

    const mockApi = {
      get: async (path) => {
        if (path === '/api/sessions/105') {
          return { id: 105, composition_mode: 'resource-v1', settings: { composition_mode: 'resource-v1' }, shots: [] }
        }
        if (path === '/api/sessions/105/plan') {
          return {
            plan_revision: 1,
            plan: {
              version: 'resource-v1',
              look: 'Initial Look',
              initial_wardrobe: 'Initial Wardrobe',
              takes: [
                { take_id: 'take-001', camera: '35mm' },
                { take_id: 'take-002', camera: '50mm' },
              ],
              selected_resources: originalTriples,
            },
          }
        }
      },
      post: async (path, body) => {
        savedPayload = body
        return { plan_revision: 2, conflicts: [] }
      },
    }

    const controller = createSessionViewController(105, { api: mockApi })
    await controller.reload()

    controller.addTake({ camera: '85mm' })
    controller.editTake('take-002', { camera: '50mm f/1.2' })

    await controller.savePlan()

    expect(savedPayload).not.toBeNull()
    const savedTakes = savedPayload.plan.takes
    expect(savedTakes).toHaveLength(3)
    expect(savedTakes[0].take_id).toBe('take-001')
    expect(savedTakes[1].take_id).toBe('take-002')
    expect(savedTakes[1].camera).toBe('50mm f/1.2')
    expect(savedTakes[2].take_id).toBe('take-003')
    expect(savedTakes[2].camera).toBe('85mm')

    expect(savedPayload.plan.selected_resources).toEqual(originalTriples)
  })

  it('excludes legacy controls in resource-v1 and preserves them in legacy mode', () => {
    const resourceSession = { composition_mode: 'resource-v1' }
    const legacySession = { composition_mode: '' }

    expect(isLegacyControlVisible(resourceSession, 'compose')).toBe(false)
    expect(isLegacyControlVisible(resourceSession, 'fill_cell')).toBe(false)
    expect(isLegacyControlVisible(resourceSession, 'add_shots')).toBe(false)
    expect(isLegacyControlVisible(resourceSession, 'catalogue_outfit')).toBe(false)

    expect(isLegacyControlVisible(legacySession, 'compose')).toBe(true)
    expect(isLegacyControlVisible(legacySession, 'fill_cell')).toBe(true)
    expect(isLegacyControlVisible(legacySession, 'add_shots')).toBe(true)
    expect(isLegacyControlVisible(legacySession, 'catalogue_outfit')).toBe(true)
  })

  it('keeps advanced settings and profiles accessible for both resource-v1 and legacy sessions', () => {
    const resourceSession = {
      id: 301,
      composition_mode: 'resource-v1',
      workflow_id: 10,
      reference_workflow_id: 12,
      checkpoint: 'sd_xl_base.safetensors',
      settings: {
        checkpoint: 'sd_xl_base.safetensors',
        lora_strength: 0.85,
        steps: 25,
        cfg: 6.5,
        sampler: 'euler_ancestral',
        scheduler: 'karras',
        denoise: 0.35,
      },
    }

    const legacySession = {
      id: 302,
      composition_mode: '',
      workflow_id: 10,
      reference_workflow_id: 12,
      checkpoint: 'sd_xl_base.safetensors',
      settings: {
        checkpoint: 'sd_xl_base.safetensors',
        lora_strength: 0.85,
        steps: 25,
        cfg: 6.5,
        sampler: 'euler_ancestral',
        scheduler: 'karras',
        denoise: 0.35,
      },
    }

    const resourceAdv = getAdvancedSettings(resourceSession)
    const legacyAdv = getAdvancedSettings(legacySession)

    expect(resourceAdv.workflow_id).toBe(10)
    expect(resourceAdv.reference_workflow_id).toBe(12)
    expect(resourceAdv.checkpoint).toBe('sd_xl_base.safetensors')
    expect(resourceAdv.lora_strength).toBe(0.85)
    expect(resourceAdv.steps).toBe(25)
    expect(resourceAdv.cfg).toBe(6.5)
    expect(resourceAdv.sampler).toBe('euler_ancestral')
    expect(resourceAdv.scheduler).toBe('karras')
    expect(resourceAdv.denoise).toBe(0.35)

    expect(legacyAdv).toEqual(resourceAdv)
  })

  it('rejects direct jump to generation from character, constants, or takes steps', async () => {
    const mockApi = {
      get: async (path) => {
        if (path === '/api/sessions/201') {
          return { id: 201, composition_mode: 'resource-v1', workflow_id: 1, settings: { composition_mode: 'resource-v1' }, shots: [] }
        }
        if (path === '/api/sessions/201/plan') {
          return {
            plan_revision: 3,
            plan: {
              version: 'resource-v1',
              look: 'Diffused window light',
              initial_wardrobe: 'Coat',
              takes: [{ take_id: 'take-001', camera: '50mm' }],
              selected_resources: [],
            },
          }
        }
      },
    }

    const controller = createSessionViewController(201, { api: mockApi })
    await controller.reload()

    // Step 1: Character -> attempt jump to generation
    expect(controller.getState().activeStep).toBe('character')
    expect(controller.navigateStep('generation')).toBe(false)
    expect(controller.getState().error).toContain('review step must be completed first')
    expect(controller.getState().canGenerate).toBe(false)

    // Step 2: Scene / Constants -> attempt jump to generation
    expect(controller.navigateStep('constants')).toBe(true)
    expect(controller.getState().activeStep).toBe('constants')
    expect(controller.navigateStep('generation')).toBe(false)
    expect(controller.getState().error).toContain('review step must be completed first')
    expect(controller.getState().canGenerate).toBe(false)

    // Step 3: Takes -> attempt jump to generation
    expect(controller.navigateStep('takes')).toBe(true)
    expect(controller.getState().activeStep).toBe('takes')
    expect(controller.navigateStep('generation')).toBe(false)
    expect(controller.getState().error).toContain('review step must be completed first')
    expect(controller.getState().canGenerate).toBe(false)
  })

  it('allows entering generation after review, but keeps Run blocked when pending === 0', async () => {
    const mockApi = {
      get: async (path) => {
        if (path === '/api/sessions/202') {
          return { id: 202, composition_mode: 'resource-v1', workflow_id: 5, settings: { composition_mode: 'resource-v1' }, shots: [] }
        }
        if (path === '/api/sessions/202/plan') {
          return {
            plan_revision: 2,
            plan: {
              version: 'resource-v1',
              look: 'Studio daylight',
              initial_wardrobe: 'Blazer',
              takes: [{ take_id: 'take-001', camera: '85mm' }],
              selected_resources: [],
            },
          }
        }
      },
    }

    const controller = createSessionViewController(202, { api: mockApi })
    await controller.reload()

    // Walk through Character -> Constants -> Takes -> Review
    expect(controller.navigateStep('constants')).toBe(true)
    expect(controller.navigateStep('takes')).toBe(true)
    expect(controller.navigateStep('review')).toBe(true)

    // In Review step, proceeding to generation is permitted
    expect(controller.getState().activeStep).toBe('review')
    expect(controller.getState().canProceedToGeneration).toBe(true)
    expect(controller.isControlVisible('proceed_to_generation_button')).toBe(true)
    expect(controller.getState().canGenerate).toBe(false)
    expect(controller.isControlVisible('run_button')).toBe(false)

    // Proceed to Generation from Review: transition succeeds
    expect(controller.navigateStep('generation')).toBe(true)
    const state = controller.getState()
    expect(state.activeStep).toBe('generation')
    expect(state.reviewedRevision).toBe(2)

    // Entering Generation by itself DOES NOT authorize Run when pending === 0
    expect(state.canGenerate).toBe(false)
    expect(controller.isControlVisible('run_button')).toBe(false)
  })

  it('authorizes Run in generation step when real pending shots exist', async () => {
    const mockApi = {
      get: async (path) => {
        if (path === '/api/sessions/202b') {
          return {
            id: '202b',
            composition_mode: 'resource-v1',
            workflow_id: 5,
            settings: { composition_mode: 'resource-v1' },
            shots: [{ id: 1, status: 'pending' }],
          }
        }
        if (path === '/api/sessions/202b/plan') {
          return {
            plan_revision: 2,
            plan: {
              version: 'resource-v1',
              look: 'Studio daylight',
              initial_wardrobe: 'Blazer',
              takes: [{ take_id: 'take-001', camera: '85mm' }],
              selected_resources: [],
            },
          }
        }
      },
    }

    const controller = createSessionViewController('202b', { api: mockApi })
    await controller.reload()

    controller.navigateStep('review')
    expect(controller.navigateStep('generation')).toBe(true)
    const state = controller.getState()
    expect(state.activeStep).toBe('generation')
    expect(state.reviewedRevision).toBe(2)

    // With real pending shots, Run is authorized
    expect(state.canGenerate).toBe(true)
    expect(controller.isControlVisible('run_button')).toBe(true)
  })

  it('invalidates review and blocks Run when plan is edited (dirty)', async () => {
    let currentRev = 1
    const mockApi = {
      get: async (path) => {
        if (path === '/api/sessions/203') {
          return {
            id: 203,
            composition_mode: 'resource-v1',
            workflow_id: 5,
            settings: { composition_mode: 'resource-v1' },
            shots: [{ id: 10, status: 'pending' }],
          }
        }
        if (path === '/api/sessions/203/plan') {
          return {
            plan_revision: currentRev,
            plan: {
              version: 'resource-v1',
              look: 'Morning light',
              initial_wardrobe: 'Shirt',
              takes: [{ take_id: 'take-001', camera: '35mm' }],
              selected_resources: [],
            },
          }
        }
      },
      post: async () => {
        currentRev = 2
        return { plan_revision: 2, conflicts: [] }
      },
    }

    const controller = createSessionViewController(203, { api: mockApi })
    await controller.reload()

    // Walk to Generation successfully
    controller.navigateStep('review')
    controller.navigateStep('generation')
    expect(controller.getState().canGenerate).toBe(true)
    expect(controller.isControlVisible('run_button')).toBe(true)

    // Edit a take -> plan becomes dirty
    controller.editTake('take-001', { camera: '50mm' })
    let state = controller.getState()
    expect(state.planDirty).toBe(true)
    expect(state.reviewedRevision).toBeNull()
    expect(state.canGenerate).toBe(false)
    expect(controller.isControlVisible('run_button')).toBe(false)

    // Attempting to advance to generation while dirty is blocked
    controller.navigateStep('review')
    expect(controller.navigateStep('generation')).toBe(false)
    expect(controller.getState().error).toContain('plan has unsaved changes')

    // Saving clears dirty, updates revision, but requires re-review
    const saveRes = await controller.savePlan()
    expect(saveRes.ok).toBe(true)
    state = controller.getState()
    expect(state.planRevision).toBe(2)
    expect(state.planDirty).toBe(false)
    expect(state.reviewedRevision).toBeNull()
    expect(state.canGenerate).toBe(false)
    expect(controller.isControlVisible('run_button')).toBe(false)

    // Re-navigating to generation from review stamps new revision and re-authorizes Run
    expect(controller.navigateStep('generation')).toBe(true)
    state = controller.getState()
    expect(state.reviewedRevision).toBe(2)
    expect(state.canGenerate).toBe(true)
    expect(controller.isControlVisible('run_button')).toBe(true)
  })

  it('blocks generation when unresolved resource conflicts are present', async () => {
    const mockApi = {
      get: async (path) => {
        if (path === '/api/sessions/204') {
          return { id: 204, composition_mode: 'resource-v1', workflow_id: 2, settings: { composition_mode: 'resource-v1' }, shots: [] }
        }
        if (path === '/api/sessions/204/plan') {
          return {
            plan_revision: 1,
            plan: {
              version: 'resource-v1',
              look: 'Studio light',
              initial_wardrobe: 'Tee',
              takes: [{ take_id: 'take-001' }],
              selected_resources: [],
            },
          }
        }
      },
      post: async () => ({
        plan_revision: 2,
        conflicts: [{ type: 'digest_mismatch', source_id: 'room_01' }],
      }),
    }

    const controller = createSessionViewController(204, { api: mockApi })
    await controller.reload()

    // Trigger save that returns conflicts
    controller.editConstants('Updated Look', 'Updated Wardrobe')
    await controller.savePlan()

    expect(controller.getState().planConflicts).toHaveLength(1)

    // Try navigating to generation from review
    controller.navigateStep('review')
    expect(controller.navigateStep('generation')).toBe(false)
    expect(controller.getState().error).toContain('unresolved resource conflicts')
    expect(controller.getState().canGenerate).toBe(false)
    expect(controller.isControlVisible('run_button')).toBe(false)
  })

  it('blocks generation when session lacks workflow assignment in all scopes', async () => {
    const mockApi = {
      get: async (path) => {
        if (path === '/api/sessions/205') {
          return {
            id: 205,
            composition_mode: 'resource-v1',
            workflow_id: null,
            model: { id: 1, workflow_id: null },
            settings: { composition_mode: 'resource-v1', workflow_id: null },
            shots: [],
          }
        }
        if (path === '/api/sessions/205/plan') {
          return {
            plan_revision: 1,
            plan: {
              version: 'resource-v1',
              look: 'Studio light',
              initial_wardrobe: 'Tee',
              takes: [{ take_id: 'take-001' }],
              selected_resources: [],
            },
          }
        }
      },
    }

    const controller = createSessionViewController(205, { api: mockApi })
    await controller.reload()

    controller.navigateStep('review')
    expect(controller.navigateStep('generation')).toBe(false)
    expect(controller.getState().error).toContain('session has no workflow assigned')
    expect(controller.getState().canGenerate).toBe(false)
    expect(controller.isControlVisible('run_button')).toBe(false)
  })

  it('preserves legacy generation permission and controls without plan state', async () => {
    const mockApi = {
      get: async (path) => {
        if (path === '/api/sessions/206') {
          return {
            id: 206,
            composition_mode: '',
            workflow_id: 3,
            manner: 'directed',
            checkpoint: 'sd_xl.safetensors',
            shots: [{ id: 1, prompt: 'legacy shot', status: 'pending' }],
            settings: { composition_mode: '' },
          }
        }
      },
    }

    const controller = createSessionViewController(206, { api: mockApi })
    const state = await controller.reload()

    expect(state.isResource).toBe(false)
    expect(state.plan).toBeNull()
    expect(state.planRevision).toBeNull()
    expect(state.canPrepare).toBe(true)
    // With pending shots present, legacy generation is permitted
    expect(state.canGenerate).toBe(true)
    expect(controller.isControlVisible('run_button')).toBe(true)
    expect(controller.isControlVisible('compose')).toBe(true)
    expect(controller.isControlVisible('fill_cell')).toBe(true)
  })
})

describe('SessionView React component rendering integration (renderToStaticMarkup)', () => {
  const baseSession = {
    id: 501,
    name: 'Ada Resource Portrait Shoot',
    model_id: 1,
    model: { id: 1, name: 'Ada', trigger: 'ohwx woman' },
    checkpoint: 'sd_xl_base.safetensors',
    shots: [],
    tags: [],
    settings: {
      composition_mode: 'resource-v1',
      width: 1024,
      height: 1024,
      steps: 20,
      cfg: 7.0,
      lora_strength: 1.0,
      checkpoint: 'sd_xl_base.safetensors',
    },
  }

  const basePlan = {
    version: 'resource-v1',
    look: 'Clean studio natural daylight',
    initial_wardrobe: 'Tailored grey blazer',
    takes: [
      { take_id: 'take-001', label: 'Close up', camera: '85mm', framing: 'tight', pose: 'facing front', expression: 'neutral' },
      { take_id: 'take-002', label: 'Medium shot', camera: '50mm', framing: 'medium', pose: 'three-quarter', expression: 'slight smile' },
    ],
    selected_resources: [
      { library_key: 'rooms', source_id: 'studio_white', content_digest: 'sha256:112233' },
    ],
    wardrobe_changes: [],
  }

  it('renders the ordered 5-step preparation flow for resource-v1 with valid draft', () => {
    const html = renderToStaticMarkup(
      React.createElement(SessionView, {
        id: 501,
        initialSession: baseSession,
        initialPlan: basePlan,
        initialRevision: 2,
      })
    )

    expect(html).toContain('1. Character')
    expect(html).toContain('2. Scene / Constants')
    expect(html).toContain('3. Takes')
    expect(html).toContain('4. Review')
    expect(html).toContain('5. Generation')
    expect(html).toContain('Rev 2')

    expect(html).toContain('Character identity is fixed for this session')
    expect(html).toContain('Next: Scene / Constants →')

    expect(html).not.toContain('Compose</button>')
    expect(html).not.toContain('Fill ')
    expect(html).not.toContain('+ Shots')
  })

  it('renders incomplete draft panel and suppresses steps/generation when draft failed to load', () => {
    const html = renderToStaticMarkup(
      React.createElement(SessionView, {
        id: 501,
        initialSession: baseSession,
        initialPlan: null,
        initialRevision: null,
        initialError: 'no plan draft for this session',
      })
    )

    expect(html).toContain('Resource Session Plan Incomplete')
    expect(html).toContain('no plan draft for this session')
    expect(html).toContain('Preparation and generation are blocked until a valid draft is available.')

    expect(html).not.toContain('Next: Scene / Constants →')
    expect(html).not.toContain('Run (')
  })

  it('renders legacy controls for a legacy session and suppresses resource-v1 stepper', () => {
    const legacySession = {
      id: 502,
      name: 'Legacy Shoot',
      model_id: 1,
      model: { id: 1, name: 'Ada' },
      composition_mode: '',
      manner: 'directed',
      checkpoint: 'sd_xl_base.safetensors',
      shots: [],
      tags: [],
      settings: {
        composition_mode: '',
        width: 1024,
        height: 1024,
        steps: 20,
        cfg: 7.0,
      },
    }

    const html = renderToStaticMarkup(
      React.createElement(SessionView, {
        id: 502,
        initialSession: legacySession,
        initialPlan: null,
        initialRevision: null,
      })
    )

    expect(html).toContain('Compose')
    expect(html).toContain('Fill ')
    expect(html).toContain('+ Shots')

    expect(html).not.toContain('1. Character')
    expect(html).not.toContain('2. Scene / Constants')
    expect(html).not.toContain('Resource Session Plan Incomplete')
  })

  it('renders Settings button allowing access to advanced options in both session modes', () => {
    const resourceHtml = renderToStaticMarkup(
      React.createElement(SessionView, {
        id: 501,
        initialSession: baseSession,
        initialPlan: basePlan,
        initialRevision: 2,
      })
    )

    expect(resourceHtml).toContain('⚙ Settings')
  })

  it('hides Run button at Character step even when valid draft is loaded', () => {
    const html = renderToStaticMarkup(
      React.createElement(SessionView, {
        id: 501,
        initialSession: { ...baseSession, workflow_id: 10 },
        initialPlan: basePlan,
        initialRevision: 2,
        initialActiveStep: 'character',
      })
    )

    expect(html).not.toContain('Run (')
  })

  it('hides Run button at Generation step when no pending shots exist (pending === 0)', () => {
    const html = renderToStaticMarkup(
      React.createElement(SessionView, {
        id: 501,
        initialSession: { ...baseSession, workflow_id: 10, shots: [] },
        initialPlan: basePlan,
        initialRevision: 2,
        initialActiveStep: 'generation',
        initialReviewedRevision: 2,
      })
    )

    expect(html).not.toContain('Run (')
    expect(html).toContain('5. Generation')
    expect(html).toContain('Session plan reviewed.')
  })

  it('renders Run button when at Generation step with reviewed revision and real pending shots', () => {
    const html = renderToStaticMarkup(
      React.createElement(SessionView, {
        id: 501,
        initialSession: { ...baseSession, workflow_id: 10, shots: [{ id: 1, status: 'pending' }] },
        initialPlan: basePlan,
        initialRevision: 2,
        initialActiveStep: 'generation',
        initialReviewedRevision: 2,
      })
    )

    expect(html).toContain('Run (1)')
  })

  it('renders legacy Run button when legacy session has real pending shots', () => {
    const legacySessionWithShots = {
      id: 502,
      name: 'Legacy Shoot',
      model_id: 1,
      model: { id: 1, name: 'Ada' },
      composition_mode: '',
      checkpoint: 'sd_xl_base.safetensors',
      shots: [{ id: 1, status: 'pending' }],
      settings: { composition_mode: '' },
    }

    const html = renderToStaticMarkup(
      React.createElement(SessionView, {
        id: 502,
        initialSession: legacySessionWithShots,
        initialPlan: null,
        initialRevision: null,
      })
    )

    expect(html).toContain('Run (1)')
  })

  it('disables Proceed to Generation button and shows warning in Review step when unresolved conflicts exist', () => {
    const html = renderToStaticMarkup(
      React.createElement(SessionView, {
        id: 501,
        initialSession: { ...baseSession, workflow_id: 10 },
        initialPlan: basePlan,
        initialRevision: 2,
        initialActiveStep: 'review',
        initialConflicts: [{ type: 'mismatch', source_id: 'studio_white' }],
      })
    )

    expect(html).toContain('Unresolved conflicts block proceeding to generation')
    expect(html).toContain('disabled=""')
  })
})
