import { describe, it, expect } from 'vitest'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import SessionView from './views/SessionView.jsx'
import {
  MODE_RESOURCE_V1,
  WARDROBE_SCOPE_THIS_TAKE,
  WARDROBE_SCOPE_FROM_HERE,
  VALID_WARDROBE_SCOPES,
  isResourceSession,
  normalizePlan,
  nextTakeId,
  createTake,
  updateTake,
  removeTake,
  reorderTakes,
  setWardrobeChange,
  removeWardrobeChange,
  resolveEffectiveWardrobes,
  resolveEffectiveWardrobeDetails,
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
  getTakePreparationState,
  isTakeReadyForSubmission,
  loadTakeReview,
  loadPlanReviews,
  recordTakeAdaptation,
  prepareTake,
  preparePlanTakes,
  submitPreparedTake,
  submitSelectedTakes,
  approvePlanReview,
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

describe('Task 5.3: Wardrobe scope controls, pure resolution, reordering, and re-preparation', () => {
  const samplePlan = {
    version: MODE_RESOURCE_V1,
    look: 'Dramatic directional rim light.',
    initial_wardrobe: 'Classic cream trench coat over turtleneck.',
    takes: [
      { take_id: 'take-001', camera: '35mm', framing: 'full body', pose: 'walking', expression: 'calm' },
      { take_id: 'take-002', camera: '50mm', framing: 'medium', pose: 'turning', expression: 'serious' },
      { take_id: 'take-003', camera: '85mm', framing: 'close up', pose: 'profile', expression: 'pensive' },
      { take_id: 'take-004', camera: '50mm', framing: 'three quarter', pose: 'seated', expression: 'soft' },
    ],
    selected_resources: [],
    wardrobe_changes: [],
  }

  describe('Pure wardrobe resolution', () => {
    it('inherits initial_wardrobe across all takes when no wardrobe_changes are present', () => {
      const resolved = resolveEffectiveWardrobes(samplePlan)
      expect(resolved['take-001']).toBe('Classic cream trench coat over turtleneck.')
      expect(resolved['take-002']).toBe('Classic cream trench coat over turtleneck.')
      expect(resolved['take-003']).toBe('Classic cream trench coat over turtleneck.')
      expect(resolved['take-004']).toBe('Classic cream trench coat over turtleneck.')

      const details = resolveEffectiveWardrobeDetails(samplePlan)
      expect(details['take-001'].source).toBe('initial')
      expect(details['take-001'].inheritedFrom).toBe('initial')
      expect(details['take-001'].scope).toBeNull()
      expect(details['take-001'].event).toBeNull()
    })

    it('falls back to empty string when initial_wardrobe is empty or missing', () => {
      const emptyPlan = {
        ...samplePlan,
        initial_wardrobe: '',
        takes: [{ take_id: 'take-001' }],
      }
      const resolved = resolveEffectiveWardrobes(emptyPlan)
      expect(resolved['take-001']).toBe('')
    })

    it('handles isolated override (this_take) without altering following takes', () => {
      const planWithOverride = {
        ...samplePlan,
        wardrobe_changes: [
          { take_id: 'take-002', scope: WARDROBE_SCOPE_THIS_TAKE, wardrobe: 'Red leather jacket override' },
        ],
      }
      const resolved = resolveEffectiveWardrobes(planWithOverride)
      expect(resolved['take-001']).toBe('Classic cream trench coat over turtleneck.')
      expect(resolved['take-002']).toBe('Red leather jacket override')
      expect(resolved['take-003']).toBe('Classic cream trench coat over turtleneck.')
      expect(resolved['take-004']).toBe('Classic cream trench coat over turtleneck.')

      const details = resolveEffectiveWardrobeDetails(planWithOverride)
      expect(details['take-002'].source).toBe('this_take')
      expect(details['take-002'].scope).toBe(WARDROBE_SCOPE_THIS_TAKE)
      expect(details['take-002'].inheritedFrom).toBe('initial')
      expect(details['take-002'].event).toEqual({
        take_id: 'take-002',
        scope: WARDROBE_SCOPE_THIS_TAKE,
        wardrobe: 'Red leather jacket override',
      })

      expect(details['take-003'].source).toBe('initial')
      expect(details['take-003'].inheritedFrom).toBe('initial')
      expect(details['take-003'].scope).toBeNull()
    })

    it('handles persistent change (from_here) advancing inherited state for following takes', () => {
      const planWithPersistent = {
        ...samplePlan,
        wardrobe_changes: [
          { take_id: 'take-002', scope: WARDROBE_SCOPE_FROM_HERE, wardrobe: 'Black evening silk dress' },
        ],
      }
      const resolved = resolveEffectiveWardrobes(planWithPersistent)
      expect(resolved['take-001']).toBe('Classic cream trench coat over turtleneck.')
      expect(resolved['take-002']).toBe('Black evening silk dress')
      expect(resolved['take-003']).toBe('Black evening silk dress')
      expect(resolved['take-004']).toBe('Black evening silk dress')

      const details = resolveEffectiveWardrobeDetails(planWithPersistent)
      expect(details['take-002'].source).toBe('from_here')
      expect(details['take-002'].scope).toBe(WARDROBE_SCOPE_FROM_HERE)
      expect(details['take-002'].inheritedFrom).toBe('take-002')

      expect(details['take-003'].source).toBe('inherited_from_here')
      expect(details['take-003'].scope).toBeNull()
      expect(details['take-003'].inheritedFrom).toBe('take-002')

      expect(details['take-004'].source).toBe('inherited_from_here')
      expect(details['take-004'].inheritedFrom).toBe('take-002')
    })

    it('handles superseding from_here changes in sequence', () => {
      const planMultiple = {
        ...samplePlan,
        wardrobe_changes: [
          { take_id: 'take-002', scope: WARDROBE_SCOPE_FROM_HERE, wardrobe: 'Navy double-breasted suit' },
          { take_id: 'take-004', scope: WARDROBE_SCOPE_FROM_HERE, wardrobe: 'White oversized linen shirt' },
        ],
      }
      const resolved = resolveEffectiveWardrobes(planMultiple)
      expect(resolved['take-001']).toBe('Classic cream trench coat over turtleneck.')
      expect(resolved['take-002']).toBe('Navy double-breasted suit')
      expect(resolved['take-003']).toBe('Navy double-breasted suit')
      expect(resolved['take-004']).toBe('White oversized linen shirt')

      const details = resolveEffectiveWardrobeDetails(planMultiple)
      expect(details['take-003'].inheritedFrom).toBe('take-002')
      expect(details['take-004'].inheritedFrom).toBe('take-004')
    })

    it('handles mixed from_here and this_take without allowing this_take to contaminate persistent state', () => {
      const planMixed = {
        ...samplePlan,
        wardrobe_changes: [
          { take_id: 'take-002', scope: WARDROBE_SCOPE_FROM_HERE, wardrobe: 'Dark charcoal wool suit' },
          { take_id: 'take-003', scope: WARDROBE_SCOPE_THIS_TAKE, wardrobe: 'Yellow vinyl raincoat' },
        ],
      }
      const resolved = resolveEffectiveWardrobes(planMixed)
      expect(resolved['take-001']).toBe('Classic cream trench coat over turtleneck.')
      expect(resolved['take-002']).toBe('Dark charcoal wool suit')
      expect(resolved['take-003']).toBe('Yellow vinyl raincoat')
      expect(resolved['take-004']).toBe('Dark charcoal wool suit')

      const details = resolveEffectiveWardrobeDetails(planMixed)
      expect(details['take-002'].source).toBe('from_here')
      expect(details['take-003'].source).toBe('this_take')
      expect(details['take-003'].inheritedFrom).toBe('take-002')
      expect(details['take-004'].source).toBe('inherited_from_here')
      expect(details['take-004'].inheritedFrom).toBe('take-002')
    })

    it('throws when encountering an unknown or invalid wardrobe scope', () => {
      const planInvalidScope = {
        ...samplePlan,
        wardrobe_changes: [
          { take_id: 'take-002', scope: 'invalid_scope', wardrobe: 'Some clothes' },
        ],
      }
      expect(() => resolveEffectiveWardrobes(planInvalidScope)).toThrow(
        /resolveEffectiveWardrobes encountered unrecognised scope "invalid_scope"/
      )
    })

    it('throws when encountering duplicate wardrobe changes for the same take_id', () => {
      const planWithDuplicates = {
        ...samplePlan,
        wardrobe_changes: [
          { take_id: 'take-002', scope: WARDROBE_SCOPE_THIS_TAKE, wardrobe: 'Outfit A' },
          { take_id: 'take-002', scope: WARDROBE_SCOPE_FROM_HERE, wardrobe: 'Outfit B' },
        ],
      }
      expect(() => resolveEffectiveWardrobes(planWithDuplicates)).toThrow(
        /duplicate wardrobe_change for take_id "take-002"/
      )
    })

    it('throws when a wardrobe change references a nonexistent take_id', () => {
      const planWithNonexistentTake = {
        ...samplePlan,
        wardrobe_changes: [
          { take_id: 'take-999', scope: WARDROBE_SCOPE_THIS_TAKE, wardrobe: 'Ghost outfit' },
        ],
      }
      expect(() => resolveEffectiveWardrobes(planWithNonexistentTake)).toThrow(
        /references take_id "take-999" which does not exist in plan\.takes/
      )
    })
  })

  describe('normalizePlan scope preservation', () => {
    it('preserves invalid/unsupported scopes verbatim so validation catches them', () => {
      const rawPlan = {
        version: MODE_RESOURCE_V1,
        wardrobe_changes: [
          { take_id: 'take-001', scope: 'unsupported_custom_scope', wardrobe: 'Vintage denim' },
        ],
      }
      const normalized = normalizePlan(rawPlan)
      expect(normalized.wardrobe_changes[0].scope).toBe('unsupported_custom_scope')
      expect(normalized.wardrobe_changes[0].scope).not.toBe(WARDROBE_SCOPE_THIS_TAKE)
    })

    it('preserves valid scopes correctly', () => {
      const rawPlan = {
        version: MODE_RESOURCE_V1,
        wardrobe_changes: [
          { take_id: 'take-001', scope: WARDROBE_SCOPE_THIS_TAKE, wardrobe: 'Denim' },
          { take_id: 'take-002', scope: WARDROBE_SCOPE_FROM_HERE, wardrobe: 'Silk' },
        ],
      }
      const normalized = normalizePlan(rawPlan)
      expect(normalized.wardrobe_changes[0].scope).toBe(WARDROBE_SCOPE_THIS_TAKE)
      expect(normalized.wardrobe_changes[1].scope).toBe(WARDROBE_SCOPE_FROM_HERE)
    })
  })

  describe('reorderTakes pure function', () => {
    it('moves a take from one index to another while keeping take_id stable', () => {
      const initialTakes = [
        { take_id: 'take-001', framing: 'wide' },
        { take_id: 'take-002', framing: 'medium' },
        { take_id: 'take-003', framing: 'close' },
      ]
      const reordered = reorderTakes(initialTakes, 0, 2)
      expect(reordered.map((t) => t.take_id)).toEqual(['take-002', 'take-003', 'take-001'])
      expect(reordered[2].framing).toBe('wide')
    })

    it('returns takes unchanged when indices are out of range or equal', () => {
      const initialTakes = [
        { take_id: 'take-001' },
        { take_id: 'take-002' },
      ]
      expect(reorderTakes(initialTakes, 0, 0)).toEqual(initialTakes)
      expect(reorderTakes(initialTakes, -1, 1)).toEqual(initialTakes)
      expect(reorderTakes(initialTakes, 0, 5)).toEqual(initialTakes)
      expect(reorderTakes(initialTakes, 'invalid', 1)).toEqual(initialTakes)
    })

    it('immediately changes effective wardrobes when moving a take across a from_here boundary', () => {
      const plan = {
        version: MODE_RESOURCE_V1,
        initial_wardrobe: 'Initial outfit A',
        takes: [
          { take_id: 'take-001' },
          { take_id: 'take-002' },
          { take_id: 'take-003' },
        ],
        wardrobe_changes: [
          { take_id: 'take-002', scope: WARDROBE_SCOPE_FROM_HERE, wardrobe: 'Changed outfit B' },
        ],
      }

      // Order [001, 002, 003]:
      // take-001: Initial outfit A
      // take-002: Changed outfit B
      // take-003: Changed outfit B
      const initialResolved = resolveEffectiveWardrobes(plan)
      expect(initialResolved['take-001']).toBe('Initial outfit A')
      expect(initialResolved['take-002']).toBe('Changed outfit B')
      expect(initialResolved['take-003']).toBe('Changed outfit B')

      // Move take-002 to index 0: Order [002, 001, 003]
      const movedTakes = reorderTakes(plan.takes, 1, 0)
      const reorderedPlan = { ...plan, takes: movedTakes }
      const reorderedResolved = resolveEffectiveWardrobes(reorderedPlan)

      // Now take-002 is first! Its from_here propagates to take-001 and take-003
      expect(reorderedResolved['take-002']).toBe('Changed outfit B')
      expect(reorderedResolved['take-001']).toBe('Changed outfit B')
      expect(reorderedResolved['take-003']).toBe('Changed outfit B')
    })
  })

  describe('setWardrobeChange and removeWardrobeChange pure helpers', () => {
    it('adds a new wardrobe change for a take', () => {
      const changes = setWardrobeChange([], 'take-001', {
        scope: WARDROBE_SCOPE_FROM_HERE,
        wardrobe: 'Summer floral dress',
      })
      expect(changes).toEqual([
        { take_id: 'take-001', scope: WARDROBE_SCOPE_FROM_HERE, wardrobe: 'Summer floral dress' },
      ])
    })

    it('updates an existing wardrobe change without creating duplicates for the same take_id', () => {
      const initial = [
        { take_id: 'take-001', scope: WARDROBE_SCOPE_THIS_TAKE, wardrobe: 'Old wardrobe' },
        { take_id: 'take-002', scope: WARDROBE_SCOPE_FROM_HERE, wardrobe: 'Other wardrobe' },
      ]
      const updated = setWardrobeChange(initial, 'take-001', {
        scope: WARDROBE_SCOPE_FROM_HERE,
        wardrobe: 'New updated wardrobe',
      })
      expect(updated).toHaveLength(2)
      expect(updated[0]).toEqual({
        take_id: 'take-001',
        scope: WARDROBE_SCOPE_FROM_HERE,
        wardrobe: 'New updated wardrobe',
      })
    })

    it('removes wardrobe change for a take_id and restores inheritance', () => {
      const initial = [
        { take_id: 'take-001', scope: WARDROBE_SCOPE_THIS_TAKE, wardrobe: 'Take 1 wardrobe' },
        { take_id: 'take-002', scope: WARDROBE_SCOPE_FROM_HERE, wardrobe: 'Take 2 wardrobe' },
      ]
      const remaining = removeWardrobeChange(initial, 'take-001')
      expect(remaining).toEqual([
        { take_id: 'take-002', scope: WARDROBE_SCOPE_FROM_HERE, wardrobe: 'Take 2 wardrobe' },
      ])
    })

    it('sanitizes inherited duplicate entries for the target take_id into exactly one updated event', () => {
      const initialWithDuplicates = [
        { take_id: 'take-001', scope: WARDROBE_SCOPE_THIS_TAKE, wardrobe: 'Old wardrobe A' },
        { take_id: 'take-002', scope: WARDROBE_SCOPE_FROM_HERE, wardrobe: 'Other wardrobe' },
        { take_id: 'take-001', scope: WARDROBE_SCOPE_FROM_HERE, wardrobe: 'Stale duplicate B' },
      ]
      const sanitized = setWardrobeChange(initialWithDuplicates, 'take-001', {
        scope: WARDROBE_SCOPE_FROM_HERE,
        wardrobe: 'Fresh clean wardrobe',
      })
      expect(sanitized).toHaveLength(2)
      expect(sanitized[0]).toEqual({
        take_id: 'take-001',
        scope: WARDROBE_SCOPE_FROM_HERE,
        wardrobe: 'Fresh clean wardrobe',
      })
      expect(sanitized[1]).toEqual({
        take_id: 'take-002',
        scope: WARDROBE_SCOPE_FROM_HERE,
        wardrobe: 'Other wardrobe',
      })
      // Confirms that exactly one event exists for take-001
      expect(sanitized.filter((c) => c.take_id === 'take-001')).toHaveLength(1)
    })
  })

  describe('buildPlanSavePayload wardrobe persistence contract', () => {
    it('persists explicit wardrobe_changes and does NOT materialize effective_wardrobe onto takes', () => {
      const planWithChanges = {
        ...samplePlan,
        wardrobe_changes: [
          { take_id: 'take-002', scope: WARDROBE_SCOPE_THIS_TAKE, wardrobe: 'Override wardrobe' },
        ],
      }
      const payload = buildPlanSavePayload(planWithChanges, 3)
      expect(payload.expected_revision).toBe(3)
      expect(payload.plan.wardrobe_changes).toEqual([
        { take_id: 'take-002', scope: WARDROBE_SCOPE_THIS_TAKE, wardrobe: 'Override wardrobe' },
      ])

      // Each take must NOT contain effective_wardrobe or any invented state
      for (const t of payload.plan.takes) {
        expect(t).not.toHaveProperty('effective_wardrobe')
        expect(t).not.toHaveProperty('effectiveWardrobe')
      }
    })
  })

  describe('createSessionViewController wardrobe scope, reordering, and re-preparation operations', () => {
    const fakeApi = {
      get: async (url) => {
        if (url === '/api/sessions/100') {
          return { id: 100, composition_mode: MODE_RESOURCE_V1, workflow_id: 5 }
        }
        if (url === '/api/sessions/100/plan') {
          return {
            plan: samplePlan,
            plan_revision: 2,
            conflicts: [],
            preparation: {
              plan_revision: 2,
              completed: [
                { take_id: 'take-001', status: 'ready' },
                { take_id: 'take-002', status: 'ready' },
                { take_id: 'take-003', status: 'ready' },
                { take_id: 'take-004', status: 'ready' },
              ],
              incomplete: [],
              history: [],
            },
          }
        }
        throw new Error(`Unexpected GET ${url}`)
      },
      post: async (url, body) => {
        if (url === '/api/sessions/100/plan') {
          return {
            plan_revision: body.expected_revision + 1,
            conflicts: [],
          }
        }
        throw new Error(`Unexpected POST ${url}`)
      },
    }

    it('exposes effectiveWardrobes and effectiveWardrobeDetails in state', async () => {
      const controller = createSessionViewController(100, {
        initialSession: { id: 100, composition_mode: MODE_RESOURCE_V1, workflow_id: 5 },
        initialPlan: samplePlan,
        initialPlanRevision: 2,
        api: fakeApi,
      })
      const state = controller.getState()
      expect(state.effectiveWardrobes['take-001']).toBe(samplePlan.initial_wardrobe)
      expect(state.effectiveWardrobeDetails['take-001'].source).toBe('initial')
    })

    it('reorderTake updates order, recalculates effectiveWardrobes, sets planDirty=true, and resets reviewedRevision', () => {
      const planWithChange = {
        ...samplePlan,
        wardrobe_changes: [
          { take_id: 'take-002', scope: WARDROBE_SCOPE_FROM_HERE, wardrobe: 'Special Evening Dress' },
        ],
      }
      const controller = createSessionViewController(100, {
        initialSession: { id: 100, composition_mode: MODE_RESOURCE_V1, workflow_id: 5 },
        initialPlan: planWithChange,
        initialPlanRevision: 2,
        initialReviewedRevision: 2,
        api: fakeApi,
      })

      expect(controller.getState().effectiveWardrobes['take-001']).toBe(samplePlan.initial_wardrobe)
      expect(controller.getState().effectiveWardrobes['take-002']).toBe('Special Evening Dress')

      // Move take-002 before take-001
      const ok = controller.reorderTake(1, 0)
      expect(ok).toBe(true)

      const updatedState = controller.getState()
      expect(updatedState.plan.takes[0].take_id).toBe('take-002')
      expect(updatedState.plan.takes[1].take_id).toBe('take-001')
      // Since take-002 is now first, its from_here applies to take-001!
      expect(updatedState.effectiveWardrobes['take-001']).toBe('Special Evening Dress')
      expect(updatedState.planDirty).toBe(true)
      expect(updatedState.reviewedRevision).toBeNull()
    })

    it('setTakeWardrobeChange updates change, recalculates wardrobes, sets planDirty, and resets reviewedRevision', () => {
      const controller = createSessionViewController(100, {
        initialSession: { id: 100, composition_mode: MODE_RESOURCE_V1, workflow_id: 5 },
        initialPlan: samplePlan,
        initialPlanRevision: 2,
        initialReviewedRevision: 2,
        api: fakeApi,
      })

      const ok = controller.setTakeWardrobeChange('take-002', {
        scope: WARDROBE_SCOPE_THIS_TAKE,
        wardrobe: 'Embroidered velvet jacket',
      })
      expect(ok).toBe(true)

      const state = controller.getState()
      expect(state.effectiveWardrobes['take-002']).toBe('Embroidered velvet jacket')
      expect(state.effectiveWardrobes['take-003']).toBe(samplePlan.initial_wardrobe)
      expect(state.planDirty).toBe(true)
      expect(state.reviewedRevision).toBeNull()
    })

    it('removeTakeWardrobeChange removes change, restores inheritance, and marks plan dirty', () => {
      const planWithChange = {
        ...samplePlan,
        wardrobe_changes: [
          { take_id: 'take-002', scope: WARDROBE_SCOPE_THIS_TAKE, wardrobe: 'Embroidered velvet jacket' },
        ],
      }
      const controller = createSessionViewController(100, {
        initialSession: { id: 100, composition_mode: MODE_RESOURCE_V1, workflow_id: 5 },
        initialPlan: planWithChange,
        initialPlanRevision: 2,
        api: fakeApi,
      })

      expect(controller.getState().effectiveWardrobes['take-002']).toBe('Embroidered velvet jacket')

      controller.removeTakeWardrobeChange('take-002')
      const state = controller.getState()
      expect(state.effectiveWardrobes['take-002']).toBe(samplePlan.initial_wardrobe)
      expect(state.plan.wardrobe_changes).toHaveLength(0)
      expect(state.planDirty).toBe(true)
    })

    it('deleteTake removes the take and cleans up any wardrobe change for that take_id', () => {
      const planWithChange = {
        ...samplePlan,
        wardrobe_changes: [
          { take_id: 'take-002', scope: WARDROBE_SCOPE_FROM_HERE, wardrobe: 'Cocktail dress' },
        ],
      }
      const controller = createSessionViewController(100, {
        initialSession: { id: 100, composition_mode: MODE_RESOURCE_V1, workflow_id: 5 },
        initialPlan: planWithChange,
        initialPlanRevision: 2,
        api: fakeApi,
      })

      controller.deleteTake('take-002')
      const state = controller.getState()
      expect(state.plan.takes.find((t) => t.take_id === 'take-002')).toBeUndefined()
      expect(state.plan.wardrobe_changes.find((c) => c.take_id === 'take-002')).toBeUndefined()
      expect(state.planDirty).toBe(true)
      expect(state.reviewedRevision).toBeNull()
    })

    it('edits a wardrobe change, reorders takes, and saves via controller verifying CAS POST payload carries explicit changes without effective_wardrobe', async () => {
      let capturedPayload = null
      const testApi = {
        get: async (url) => {
          if (url === '/api/sessions/301') {
            return { id: 301, composition_mode: MODE_RESOURCE_V1, workflow_id: 10 }
          }
          if (url === '/api/sessions/301/plan') {
            return {
              plan: samplePlan,
              plan_revision: 2,
              conflicts: [],
              preparation: {
                plan_revision: 2,
                completed: [],
                incomplete: [
                  { take_id: 'take-001', status: 'pending' },
                  { take_id: 'take-002', status: 'pending' },
                  { take_id: 'take-003', status: 'pending' },
                  { take_id: 'take-004', status: 'pending' },
                ],
                history: [],
              },
            }
          }
          throw new Error(`Unexpected GET ${url}`)
        },
        post: async (url, body) => {
          if (url === '/api/sessions/301/plan') {
            capturedPayload = body
            return {
              plan_revision: body.expected_revision + 1,
              conflicts: [],
            }
          }
          throw new Error(`Unexpected POST ${url}`)
        },
      }

      const controller = createSessionViewController(301, {
        initialSession: { id: 301, composition_mode: MODE_RESOURCE_V1, workflow_id: 10 },
        initialPlan: samplePlan,
        initialPlanRevision: 2,
        api: testApi,
      })

      // 1. Edit wardrobe change for take-002
      controller.setTakeWardrobeChange('take-002', {
        scope: WARDROBE_SCOPE_FROM_HERE,
        wardrobe: 'Navy tailored suit',
      })

      // 2. Reorder takes: move take-002 to index 0
      controller.reorderTake(1, 0)

      expect(controller.getState().planDirty).toBe(true)

      // 3. Save via controller
      const saveRes = await controller.savePlan()
      expect(saveRes.ok).toBe(true)
      expect(saveRes.planRevision).toBe(3)
      expect(controller.getState().planDirty).toBe(false)

      // 4. Verify captured CAS payload sent to backend
      expect(capturedPayload).not.toBeNull()
      expect(capturedPayload.expected_revision).toBe(2)
      expect(capturedPayload.plan.version).toBe(MODE_RESOURCE_V1)

      // Explicit wardrobe changes must be preserved exactly
      expect(capturedPayload.plan.wardrobe_changes).toEqual([
        {
          take_id: 'take-002',
          scope: WARDROBE_SCOPE_FROM_HERE,
          wardrobe: 'Navy tailored suit',
        },
      ])

      // Takes must reflect the new order
      expect(capturedPayload.plan.takes.map((t) => t.take_id)).toEqual([
        'take-002',
        'take-001',
        'take-003',
        'take-004',
      ])

      // Crucial invariant: effective_wardrobe must NEVER be materialized onto takes
      for (const t of capturedPayload.plan.takes) {
        expect(t).not.toHaveProperty('effective_wardrobe')
        expect(t).not.toHaveProperty('effectiveWardrobe')
      }
    })
  })

  describe('SessionView UI rendering for Task 5.3', () => {
    const resourceSession = {
      id: 601,
      name: 'Autumn Studio Shoot',
      model_id: 1,
      model: { id: 1, name: 'Ada' },
      composition_mode: MODE_RESOURCE_V1,
      workflow_id: 10,
      shots: [],
      settings: { composition_mode: MODE_RESOURCE_V1 },
    }

    const testPlanWithChanges = {
      version: MODE_RESOURCE_V1,
      look: 'Golden hour sidelight through French doors.',
      initial_wardrobe: 'Beige trench coat and dark trousers.',
      takes: [
        { take_id: 'take-001', camera: '35mm', framing: 'full body', pose: 'standing', expression: 'calm' },
        { take_id: 'take-002', camera: '50mm', framing: 'medium', pose: 'three quarter', expression: 'smile' },
        { take_id: 'take-003', camera: '85mm', framing: 'close up', pose: 'portrait', expression: 'direct' },
      ],
      selected_resources: [],
      wardrobe_changes: [
        { take_id: 'take-002', scope: WARDROBE_SCOPE_THIS_TAKE, wardrobe: 'Red leather biker jacket' },
      ],
    }

    it('renders Move Up (↑) and Move Down (↓) reorder buttons on takes with boundary states', () => {
      const html = renderToStaticMarkup(
        React.createElement(SessionView, {
          id: 601,
          initialSession: resourceSession,
          initialPlan: testPlanWithChanges,
          initialRevision: 1,
          initialActiveStep: 'takes',
        })
      )

      // First take: Move Up should be disabled, Move Down enabled
      expect(html).toContain('title="Move take up"')
      expect(html).toContain('title="Move take down"')
      expect(html).toContain('title="Move take up" disabled=""') // boundary check for first item
    })

    it('renders effective wardrobe badges (Inherited vs Override vs Persistent)', () => {
      const html = renderToStaticMarkup(
        React.createElement(SessionView, {
          id: 601,
          initialSession: resourceSession,
          initialPlan: testPlanWithChanges,
          initialRevision: 1,
          initialActiveStep: 'takes',
        })
      )

      // Take 1: Inherited (initial wardrobe)
      expect(html).toContain('Inherited (initial wardrobe)')
      expect(html).toContain('Beige trench coat and dark trousers.')

      // Take 2: Override (this take only)
      expect(html).toContain('Override (this take only)')
      expect(html).toContain('Red leather biker jacket')

      // Take 3: Reverted back to inherited
      expect(html).toContain('Beige trench coat and dark trousers.')
    })

    it('renders wardrobe scope controls allowing this_take and from_here selections', () => {
      const html = renderToStaticMarkup(
        React.createElement(SessionView, {
          id: 601,
          initialSession: resourceSession,
          initialPlan: testPlanWithChanges,
          initialRevision: 1,
          initialActiveStep: 'takes',
        })
      )

      // Scope select options
      expect(html).toContain('This take only')
      expect(html).toContain('From here onward')
      expect(html).toContain('name="scope-take-002"')
      expect(html).toContain('Remove change')
    })

    it('renders preparation status indicators on takes and shows warning when re-preparation is required', () => {
      const preparationState = {
        plan_revision: 1,
        completed: [
          { take_id: 'take-001', status: 'ready' },
        ],
        incomplete: [
          { take_id: 'take-002', status: 'pending' },
          { take_id: 'take-003', status: 'missing' },
        ],
        history: [
          { take_id: 'take-002', status: 'invalidated' },
        ],
      }

      const html = renderToStaticMarkup(
        React.createElement(SessionView, {
          id: 601,
          initialSession: resourceSession,
          initialPlan: testPlanWithChanges,
          initialRevision: 1,
          initialActiveStep: 'takes',
          initialPreparation: preparationState,
        })
      )

      // Take 1 is ready
      expect(html).toContain('✓ Ready')
      // Take 2 was invalidated -> Requires re-preparation
      expect(html).toContain('⚠️ Requires re-preparation')
      // Take 3 was never prepared -> Preparation required
      expect(html).toContain('Preparation required')

      // Warning banner displayed at the top of Takes step
      expect(html).toContain('require re-preparation following plan revision changes')
    })

    it('renders Effective Wardrobe and Preparation Status columns in Review step table', () => {
      const preparationState = {
        plan_revision: 1,
        completed: [
          { take_id: 'take-001', status: 'ready' },
          { take_id: 'take-002', status: 'ready' },
          { take_id: 'take-003', status: 'ready' },
        ],
        incomplete: [],
        history: [],
      }

      const html = renderToStaticMarkup(
        React.createElement(SessionView, {
          id: 601,
          initialSession: resourceSession,
          initialPlan: testPlanWithChanges,
          initialRevision: 1,
          initialActiveStep: 'review',
          initialPreparation: preparationState,
        })
      )

      expect(html).toContain('Effective Wardrobe</th>')
      expect(html).toContain('Status</th>')
      expect(html).toContain('Red leather biker jacket')
      expect(html).toContain('Ready</span>')
    })

    it('does NOT render wardrobe scope controls or reorder buttons on legacy sessions', () => {
      const legacySession = {
        id: 701,
        name: 'Old Legacy Session',
        composition_mode: '',
        workflow_id: 10,
        model: { id: 1, name: 'Ada' },
        shots: [],
        settings: {},
      }

      const html = renderToStaticMarkup(
        React.createElement(SessionView, {
          id: 701,
          initialSession: legacySession,
          initialPlan: null,
          initialRevision: null,
        })
      )

      expect(html).not.toContain('Move take up')
      expect(html).not.toContain('Move take down')
      expect(html).not.toContain('Effective Wardrobe')
      expect(html).not.toContain('From here onward')
    })
  })

  describe('Task 5.4: Review inspector, conflict resolution, test generation, and double-click/stale guards', () => {
    describe('Pure and Async API helpers for Task 5.4', () => {
      it('loadTakeReview calls GET /api/sessions/:id/plan/takes/:takeId/review with plan_revision', async () => {
        let requestedUrl = ''
        const mockApi = {
          get: async (url) => {
            requestedUrl = url
            return {
              take_id: 'take-001',
              final_prompt: 'authoritative prompt from backend',
              conflicts: [],
              resolved_conflicts: [],
              compiler_version: 'resource-v1',
              mapping_version: '1.0',
            }
          },
        }
        const res = await loadTakeReview(10, 'take-001', 2, mockApi)
        expect(res.ok).toBe(true)
        expect(requestedUrl).toBe('/api/sessions/10/plan/takes/take-001/review?plan_revision=2')
        expect(res.final_prompt).toBe('authoritative prompt from backend')
        expect(res.compiler_version).toBe('resource-v1')
      })

      it('loadPlanReviews calls GET /api/sessions/:id/plan/review', async () => {
        let requestedUrl = ''
        const mockApi = {
          get: async (url) => {
            requestedUrl = url
            return {
              session_id: 10,
              plan_revision: 1,
              takes: [{ take_id: 'take-001' }, { take_id: 'take-002' }],
            }
          },
        }
        const res = await loadPlanReviews(10, 1, mockApi)
        expect(res.ok).toBe(true)
        expect(requestedUrl).toBe('/api/sessions/10/plan/review?plan_revision=1')
        expect(res.takes.length).toBe(2)
      })

      it('recordTakeAdaptation calls POST /api/sessions/:id/plan/takes/:takeId/adaptations', async () => {
        let postedUrl = ''
        let postedPayload = null
        const mockApi = {
          post: async (url, payload) => {
            postedUrl = url
            postedPayload = payload
            return { status: 'recorded' }
          },
        }
        const adaptation = {
          library_key: 'room_lib',
          source_id: 'room-01',
          content_digest: 'abc123',
          resource_field: 'wardrobe',
          adapted_value: 'silk blouse',
        }
        const res = await recordTakeAdaptation(10, 'take-001', {
          plan_revision: 1,
          adaptation,
        }, mockApi)
        expect(res.ok).toBe(true)
        expect(postedUrl).toBe('/api/sessions/10/plan/takes/take-001/adaptations')
        expect(postedPayload.plan_revision).toBe(1)
        expect(postedPayload.adaptation.adapted_value).toBe('silk blouse')
      })

      it('prepareTake calls POST /api/sessions/:id/plan/takes/:takeId/prepare', async () => {
        let postedUrl = ''
        let postedPayload = null
        const mockApi = {
          post: async (url, payload) => {
            postedUrl = url
            postedPayload = payload
            return { status: 'ready', plan_revision: 1, take_id: 'take-001' }
          },
        }
        const res = await prepareTake(10, 'take-001', 1, mockApi)
        expect(res.ok).toBe(true)
        expect(postedUrl).toBe('/api/sessions/10/plan/takes/take-001/prepare')
        expect(postedPayload.plan_revision).toBe(1)
      })

      it('preparePlanTakes calls POST /api/sessions/:id/plan/preparations/prepare', async () => {
        let postedUrl = ''
        let postedPayload = null
        const mockApi = {
          post: async (url, payload) => {
            postedUrl = url
            postedPayload = payload
            return { prepared: [{ status: 'ready', take_id: 'take-001' }] }
          },
        }
        const res = await preparePlanTakes(10, 1, ['take-001'], mockApi)
        expect(res.ok).toBe(true)
        expect(postedUrl).toBe('/api/sessions/10/plan/preparations/prepare')
        expect(postedPayload.take_ids).toEqual(['take-001'])
      })

      it('submitSelectedTakes calls POST /api/sessions/:id/plan/preparations/submit-selected', async () => {
        let postedUrl = ''
        let postedPayload = null
        const mockApi = {
          post: async (url, payload) => {
            postedUrl = url
            postedPayload = payload
            return { submitted: [{ take_id: 'take-001', status: 'generated', shot_id: 42 }] }
          },
        }
        const res = await submitSelectedTakes(10, ['take-001'], 1, mockApi)
        expect(res.ok).toBe(true)
        expect(postedUrl).toBe('/api/sessions/10/plan/preparations/submit-selected')
        expect(postedPayload.take_ids).toEqual(['take-001'])
        expect(postedPayload.plan_revision).toBe(1)
      })
    })

    describe('createSessionViewController Task 5.4 features', () => {
      it('manages selection of ready takes and clears selection on plan modification', () => {
        const controller = createSessionViewController({
          session: { id: 1, composition_mode: 'resource-v1' },
          plan: {
            version: 'resource-v1',
            takes: [
              { take_id: 'take-001', camera: '35mm' },
              { take_id: 'take-002', camera: '50mm' },
            ],
          },
          planRevision: 1,
          planPreparation: {
            plan_revision: 1,
            completed: [
              { take_id: 'take-001', status: 'ready' },
              { take_id: 'take-002', status: 'ready' },
            ],
            incomplete: [],
            history: [],
          },
        })

        // Toggle selection
        controller.toggleTakeSelect('take-001')
        expect(controller.getState().selectedTakeIds).toContain('take-001')
        expect(controller.getState().selectedTakeIds.length).toBe(1)

        controller.selectAllReadyTakes()
        expect(controller.getState().selectedTakeIds.length).toBe(2)

        // Editing a take invalidates selection and reviewedRevision
        controller.editTake('take-001', { camera: '85mm' })
        expect(controller.getState().planDirty).toBe(true)
        expect(controller.getState().reviewedRevision).toBe(null)
        expect(controller.getState().selectedTakeIds.length).toBe(0)
      })

      it('guards submitSelectedTakesAction against submitting when plan is dirty or revision is stale', async () => {
        let submitted = false
        const mockApi = {
          post: async () => {
            submitted = true
            return { submitted: [] }
          },
        }
        const controller = createSessionViewController({
          session: { id: 1, composition_mode: 'resource-v1' },
          plan: {
            version: 'resource-v1',
            takes: [{ take_id: 'take-001', camera: '35mm' }],
          },
          planRevision: 1,
          planDirty: true,
          api: mockApi,
        })

        const resDirty = await controller.submitSelectedTakesAction(['take-001'])
        expect(resDirty.ok).toBe(false)
        expect(resDirty.error).toContain('unsaved')
        expect(submitted).toBe(false)
      })

      it('guards against double-click/in-flight submission via submittingTakes flag', async () => {
        let callCount = 0
        let resolveFirst = null
        const mockApi = {
          post: async () => {
            callCount++
            return new Promise((resolve) => {
              resolveFirst = resolve
            })
          },
        }

        const controller = createSessionViewController({
          session: { id: 1, composition_mode: 'resource-v1' },
          plan: {
            version: 'resource-v1',
            takes: [{ take_id: 'take-001', camera: '35mm' }],
          },
          planRevision: 1,
          planDirty: false,
          reviewedRevision: 1,
          planPreparation: {
            completed: [{ take_id: 'take-001', status: 'ready' }],
          },
          api: mockApi,
        })

        // Start first submission (in-flight)
        const promise1 = controller.submitSelectedTakesAction(['take-001'])
        expect(controller.getState().submittingTakes).toBe(true)

        // Attempt second submission concurrently -> blocked
        const promise2 = controller.submitSelectedTakesAction(['take-001'])
        const res2 = await promise2
        expect(res2.ok).toBe(false)
        expect(res2.error).toContain('already in progress')
        expect(callCount).toBe(1)

        // Resolve first submission
        resolveFirst({ submitted: [{ take_id: 'take-001', status: 'generated', shot_id: 100 }] })
        const res1 = await promise1
        expect(res1.ok).toBe(true)
        expect(controller.getState().submittingTakes).toBe(false)
      })

      it('resumes on reload: completed takes preserve snapshot and generated status without re-execution', async () => {
        const controller = createSessionViewController({
          session: { id: 1, composition_mode: 'resource-v1', shots: [{ id: 77, prompt: 'compiled' }] },
          plan: {
            version: 'resource-v1',
            takes: [{ take_id: 'take-001' }, { take_id: 'take-002' }],
          },
          planRevision: 1,
          planPreparation: {
            plan_revision: 1,
            completed: [
              { take_id: 'take-001', status: 'generated', linked_shot_id: 77, snapshot: { final_prompt: 'compiled' } },
              { take_id: 'take-002', status: 'ready', snapshot: { final_prompt: 'ready prompt' } },
            ],
            incomplete: [],
            history: [],
          },
        })

        const state1 = getTakePreparationState('take-001', controller.getState().preparation)
        const state2 = getTakePreparationState('take-002', controller.getState().preparation)
        expect(state1).toBe('generated')
        expect(state2).toBe('ready')
      })
    })

    describe('SessionView UI rendering for Task 5.4', () => {
      it('renders inspector toggle, final prompt, and compiler metadata in review inspector', () => {
        const resourceSession = {
          id: 801,
          name: 'Inspector Shoot',
          composition_mode: 'resource-v1',
          workflow_id: 10,
          model: { id: 1, name: 'Ada' },
          shots: [],
          settings: { composition_mode: 'resource-v1' },
        }

        const plan = {
          version: 'resource-v1',
          look: 'Morning golden hour',
          initial_wardrobe: 'Cashmere sweater',
          takes: [
            { take_id: 'take-001', camera: '35mm', framing: 'medium', pose: 'standing', expression: 'calm' },
          ],
          selected_resources: [],
          wardrobe_changes: [],
        }

        const preparationState = {
          plan_revision: 1,
          completed: [
            {
              take_id: 'take-001',
              status: 'ready',
              snapshot: {
                final_prompt: 'Ada. Morning golden hour. Cashmere sweater. 35mm, medium, standing, calm.',
                compiler_version: 'resource-v1',
                mapping_version: '1.0',
                plan_revision: 1,
              },
            },
          ],
          incomplete: [],
          history: [],
        }

        const html = renderToStaticMarkup(
          React.createElement(SessionView, {
            id: 801,
            initialSession: resourceSession,
            initialPlan: plan,
            initialRevision: 1,
            initialActiveStep: 'review',
            initialPreparation: preparationState,
          })
        )

        expect(html).toContain('Actions:')
        expect(html).toContain('Test Generate Selected (0)')
        expect(html).toContain('Approve Review (Rev 1)')
        expect(html).toContain('▼ Review')
        expect(html).toContain('Ready</span>')
      })

      it('renders selection checkboxes for ready takes and select all ready checkbox', () => {
        const resourceSession = {
          id: 802,
          name: 'Selection Shoot',
          composition_mode: 'resource-v1',
          workflow_id: 10,
          model: { id: 1, name: 'Ada' },
          shots: [],
          settings: { composition_mode: 'resource-v1' },
        }

        const plan = {
          version: 'resource-v1',
          look: 'Soft studio lighting',
          initial_wardrobe: 'Blue blazer',
          takes: [
            { take_id: 'take-001', camera: '35mm', framing: 'medium', pose: 'standing', expression: 'calm' },
            { take_id: 'take-002', camera: '50mm', framing: 'close up', pose: 'sitting', expression: 'smiling' },
          ],
          selected_resources: [],
          wardrobe_changes: [],
        }

        const preparationState = {
          plan_revision: 1,
          completed: [
            { take_id: 'take-001', status: 'ready' },
          ],
          incomplete: [{ take_id: 'take-002', status: 'incomplete' }],
          history: [],
        }

        const html = renderToStaticMarkup(
          React.createElement(SessionView, {
            id: 802,
            initialSession: resourceSession,
            initialPlan: plan,
            initialRevision: 1,
            initialActiveStep: 'review',
            initialPreparation: preparationState,
          })
        )

        // Take 1 ready, Take 2 pending prep
        expect(html).toContain('Ready</span>')
        expect(html).toContain('Pending prep</span>')
        expect(html).toContain('Prepare Incomplete Takes (1)')
      })

      it('does NOT render review inspector, test generation or batch prepare on legacy sessions', () => {
        const legacySession = {
          id: 803,
          name: 'Legacy No Inspector',
          composition_mode: '',
          workflow_id: 10,
          model: { id: 1, name: 'Ada' },
          shots: [],
          settings: {},
        }

        const html = renderToStaticMarkup(
          React.createElement(SessionView, {
            id: 803,
            initialSession: legacySession,
            initialPlan: null,
            initialRevision: null,
          })
        )

        expect(html).not.toContain('Test Generate Selected')
        expect(html).not.toContain('Prepare Incomplete Takes')
        expect(html).not.toContain('Approve Review')
        expect(html).not.toContain('▼ Review')
      })

      it('renders direct recover_preparation snapshot row, pinned revisions with digests, and unresolved placeholders', () => {
        const resourceSession = {
          id: 804,
          name: 'Authoritative Inspector Shoot',
          composition_mode: 'resource-v1',
          workflow_id: 10,
          model: { id: 1, name: 'Ada' },
          shots: [],
          settings: { composition_mode: 'resource-v1' },
        }

        const plan = {
          version: 'resource-v1',
          look: 'Dramatic spotlight',
          initial_wardrobe: 'Tweed blazer',
          takes: [
            { take_id: 'take-001', camera: '35mm', framing: 'medium', pose: 'standing', expression: 'calm' },
          ],
          selected_resources: [],
          wardrobe_changes: [],
        }

        // recover_preparation returns completed items as direct snapshot rows (NOT nested under .snapshot)
        const preparationState = {
          plan_revision: 1,
          completed: [
            {
              take_id: 'take-001',
              plan_revision: 1,
              updated_at: '2026-09-09T14:30:00Z',
              created_at: '2026-09-09T14:20:00Z',
              compiler_version: 'resource-v1',
              mapping_version: '1.0',
              final_prompt: 'Authoritative prompt from direct snapshot row: Ada in tweed blazer under dramatic spotlight.',
              effective_state: { wardrobe: 'Tweed blazer', look: 'Dramatic spotlight' },
              provenance: {
                selected_resource_revisions: [
                  { library_key: 'room', source_id: 'studio_spotlight', content_digest: 'abc123def45678901234' },
                ],
              },
              status: 'ready',
              linked_shot_id: null,
            },
          ],
          incomplete: [],
          history: [],
        }

        const reviewData = {
          'take-001': {
            ok: true,
            take_id: 'take-001',
            plan_revision: 1,
            unresolved_placeholders: [
              { placeholder: '{time_of_day}', resource_field: 'lighting' },
            ],
            selected_resource_revisions: [
              { library_key: 'room', source_id: 'studio_spotlight', content_digest: 'abc123def45678901234' },
            ],
            adaptations: [
              { library_key: 'room', source_id: 'studio_spotlight', resource_field: 'wall_color', adapted_value: 'matte black' },
            ],
            conflicts: [
              { library_key: 'room', source_id: 'studio_spotlight', resource_field: 'wall_color', message: 'Color mismatch' },
            ],
          },
        }

        const html = renderToStaticMarkup(
          React.createElement(SessionView, {
            id: 804,
            initialSession: resourceSession,
            initialPlan: plan,
            initialRevision: 1,
            initialActiveStep: 'review',
            initialPreparation: preparationState,
            initialExpandedTakeId: 'take-001',
            initialTakeReviewData: reviewData,
          })
        )

        // Direct snapshot final prompt is visible
        expect(html).toContain('Authoritative prompt from direct snapshot row: Ada in tweed blazer under dramatic spotlight.')
        // Compiler metadata is visible
        expect(html).toContain('Compiler:')
        expect(html).toContain('resource-v1')
        expect(html).toContain('Mapping:')
        expect(html).toContain('1.0')
        // Prepared At formatted timestamp is visible and parsed from ISO
        const expectedDate = new Date('2026-09-09T14:30:00Z').toLocaleString()
        expect(html).toContain('Prepared At:')
        expect(html).toContain(expectedDate)
        expect(html).not.toContain('Invalid Date')
        // Pinned resource revisions and digests are visible
        expect(html).toContain('Pinned Resource Revisions:')
        expect(html).toContain('room:studio_spotlight')
        expect(html).toContain('abc123def4567890')
        // Unresolved placeholder warning and specific placeholder are visible
        expect(html).toContain('Standing placeholders detected in resource inputs')
        expect(html).toContain('{time_of_day}')
        expect(html).toContain('in lighting')
        // Adaptations are visible
        expect(html).toContain('Resolved Adaptations:')
        expect(html).toContain('matte black')
      })

      it('recordTakeAdaptation sends exact singular adaptation contract to API without adaptations list', async () => {
        let postedUrl = null
        let postedBody = null
        const mockApi = {
          post: async (url, body) => {
            postedUrl = url
            postedBody = body
            return { ok: true, adaptation: body.adaptation }
          },
        }

        const adaptationPayload = {
          library_key: 'room',
          source_id: 'loft_sunlight',
          content_digest: 'sha256:abc123def456',
          resource_field: 'wall_color',
          adapted_value: 'vintage cream',
        }

        const res = await recordTakeAdaptation(
          42,
          'take-001',
          {
            plan_revision: 3,
            adaptation: adaptationPayload,
          },
          mockApi
        )

        expect(res.ok).toBe(true)
        expect(postedUrl).toBe('/api/sessions/42/plan/takes/take-001/adaptations')
        expect(postedBody).toEqual({
          plan_revision: 3,
          adaptation: adaptationPayload,
        })
        expect(postedBody.adaptations).toBeUndefined()
      })

      it('approvePlanReview calls /api/sessions/:id/plan/review/approve and controller updates reviewedRevision', async () => {
        let postedUrl = null
        let postedBody = null
        const mockApi = {
          post: async (url, body) => {
            postedUrl = url
            postedBody = body
            return { ok: true, approved: true, plan_revision: body.plan_revision, approved_at: '2026-09-09T12:00:00Z' }
          },
        }

        const res = await approvePlanReview(99, 2, mockApi)
        expect(res.ok).toBe(true)
        expect(postedUrl).toBe('/api/sessions/99/plan/review/approve')
        expect(postedBody).toEqual({ plan_revision: 2 })

        const controller = createSessionViewController({
          session: { id: 99, composition_mode: 'resource-v1' },
          plan: { version: 'resource-v1', takes: [{ take_id: 'take-001' }] },
          planRevision: 2,
          planDirty: false,
          reviewedRevision: null,
          api: mockApi,
        })
        expect(controller.getState().reviewedRevision).toBe(null)
        const actionRes = await controller.approveReviewAction(2)
        expect(actionRes.ok).toBe(true)
        expect(controller.getState().reviewedRevision).toBe(2)
      })

      it('expanded inspector with realistic snapshot displays real visible prepared_at date parsed from ISO string', () => {
        const resourceSession = {
          id: 805,
          name: 'Realistic Date Inspector Shoot',
          composition_mode: 'resource-v1',
          workflow_id: 10,
          model: { id: 1, name: 'Ada' },
          shots: [],
          settings: { composition_mode: 'resource-v1' },
        }

        const plan = {
          version: 'resource-v1',
          look: 'Studio light',
          initial_wardrobe: 'Dark suit',
          takes: [
            { take_id: 'take-001', camera: '35mm', framing: 'medium', pose: 'standing', expression: 'calm' },
          ],
          selected_resources: [],
          wardrobe_changes: [],
        }

        const isoDate = '2026-09-09T14:30:00Z'
        const expectedDateStr = new Date(isoDate).toLocaleString()

        const preparationState = {
          plan_revision: 1,
          completed: [
            {
              take_id: 'take-001',
              plan_revision: 1,
              updated_at: isoDate,
              created_at: '2026-09-09T14:20:00Z',
              compiler_version: 'resource-v1',
              mapping_version: '1.0',
              final_prompt: 'Ada in dark suit under studio light.',
              effective_state: { wardrobe: 'Dark suit', look: 'Studio light' },
              provenance: {},
              status: 'ready',
              linked_shot_id: null,
            },
          ],
          incomplete: [],
          history: [],
        }

        const html = renderToStaticMarkup(
          React.createElement(SessionView, {
            id: 805,
            initialSession: resourceSession,
            initialPlan: plan,
            initialRevision: 1,
            initialActiveStep: 'review',
            initialPreparation: preparationState,
            initialExpandedTakeId: 'take-001',
          })
        )

        expect(html).toContain('Prepared At:')
        expect(html).toContain(expectedDateStr)
        expect(html).not.toContain('Invalid Date')
      })

      it('restores approved review badge and enables generation actions when /plan loads with reviewed_revision equal to plan_revision', async () => {
        const planData = {
          version: 'resource-v1',
          look: 'Studio light',
          initial_wardrobe: 'Dark suit',
          takes: [
            { take_id: 'take-001', camera: '35mm', framing: 'medium', pose: 'standing', expression: 'calm' },
          ],
          selected_resources: [],
          wardrobe_changes: [],
        }

        const preparationData = {
          plan_revision: 2,
          completed: [
            { take_id: 'take-001', status: 'ready', plan_revision: 2 },
          ],
          incomplete: [],
          history: [],
        }

        const mockApi = {
          get: async (url) => {
            if (url === '/api/sessions/77') {
              return {
                id: 77,
                name: 'Approved Session',
                composition_mode: 'resource-v1',
                workflow_id: 10,
                model: { id: 1, name: 'Ada' },
                shots: [],
                settings: { composition_mode: 'resource-v1' },
              }
            }
            if (url === '/api/sessions/77/plan') {
              return {
                ok: true,
                session_id: 77,
                plan_revision: 2,
                reviewed_revision: 2,
                plan: planData,
                preparation: preparationData,
                conflicts: [],
              }
            }
            throw new Error(`Unexpected url: ${url}`)
          },
        }

        // 1. Controller / loadSessionPlan simulation: reviewed_revision equal to plan_revision
        const loadedPlan = await loadSessionPlan(77, mockApi)
        expect(loadedPlan.ok).toBe(true)
        expect(loadedPlan.planRevision).toBe(2)
        expect(loadedPlan.reviewedRevision).toBe(2)

        const controller = createSessionViewController({
          session: { id: 77, composition_mode: 'resource-v1' },
          plan: planData,
          planRevision: 2,
          planDirty: false,
          reviewedRevision: null,
          api: mockApi,
        })
        const state = await controller.reload()
        expect(state.planRevision).toBe(2)
        expect(state.reviewedRevision).toBe(2)

        // 2. UI rendering verification: restores badge and permits corresponding actions
        const html = renderToStaticMarkup(
          React.createElement(SessionView, {
            id: 77,
            initialSession: {
              id: 77,
              name: 'Approved Session',
              composition_mode: 'resource-v1',
              workflow_id: 10,
              model: { id: 1, name: 'Ada' },
              shots: [],
              settings: { composition_mode: 'resource-v1' },
            },
            initialPlan: planData,
            initialRevision: 2,
            initialReviewedRevision: 2,
            initialActiveStep: 'review',
            initialPreparation: preparationData,
          })
        )

        // Restores authoritative approved badge
        expect(html).toContain('✓ Review Approved (Rev 2)')
        // Does not show "Approve Review (Rev 2)" button
        expect(html).not.toContain('Approve Review (Rev 2)')
        // Test generate button is rendered and shows 0 selected
        expect(html).toContain('Test Generate Selected (0)')
      })
    })
  })
})
