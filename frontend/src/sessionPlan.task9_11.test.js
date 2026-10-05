import { describe, expect, it, vi } from 'vitest'
import { executeSavePlan, loadSessionPlan } from './sessionPlan.js'

const largeVersion = '9223372036854775807'

const planWithLookSnapshot = () => ({
  version: 'resource-v1',
  look: 'Saved appearance',
  initial_wardrobe: '',
  selected_resources: [],
  takes: [],
  wardrobe_changes: [],
  authoring: {
    schema_version: 1,
    mode: 'manual',
    shared_state: {
      look: { origin: 'saved_look', evidence_id: null },
      initial_wardrobe: { origin: 'user', evidence_id: null },
    },
    look_snapshot: {
      look_id: 'look-large-version',
      version: largeVersion,
      content_digest: 'a'.repeat(64),
      appearance: 'Saved appearance',
      outfit: null,
    },
    wardrobe_progression: null,
  },
})

describe('Task 9.11 exact saved-look version session-plan boundary', () => {
  it('loads the authoritative plan through the lossless version reader and preserves schema strings', async () => {
    const getVersioned = vi.fn().mockResolvedValue({
      plan_revision: 4,
      plan: planWithLookSnapshot(),
    })
    const get = vi.fn(() => { throw new Error('lossy plan reader must not be used') })

    const loaded = await loadSessionPlan(911, { get, getVersioned })

    expect(loaded.ok).toBe(true)
    expect(getVersioned).toHaveBeenCalledWith('/api/sessions/911/plan')
    expect(get).not.toHaveBeenCalled()
    expect(loaded.plan.version).toBe('resource-v1')
    expect(loaded.plan.authoring.look_snapshot.version).toBe(largeVersion)
    expect(loaded.plan.authoring.schema_version).toBe(1)
  })

  it('echoes the exact nested saved-look version through the lossless CAS writer', async () => {
    const postVersioned = vi.fn().mockResolvedValue({ plan_revision: 5, conflicts: [] })
    const post = vi.fn(() => { throw new Error('lossy plan writer must not be used') })

    const saved = await executeSavePlan(911, planWithLookSnapshot(), 4, { post, postVersioned })

    expect(saved).toMatchObject({ ok: true, planRevision: 5 })
    expect(postVersioned).toHaveBeenCalledWith(
      '/api/sessions/911/plan',
      expect.objectContaining({
        expected_revision: 4,
        plan: expect.objectContaining({
          version: 'resource-v1',
          authoring: expect.objectContaining({
            look_snapshot: expect.objectContaining({ version: largeVersion }),
          }),
        }),
      }),
      ['plan.authoring.look_snapshot.version'],
    )
    expect(post).not.toHaveBeenCalled()
  })

  it('keeps existing API test doubles compatible when versioned methods are absent', async () => {
    const get = vi.fn().mockResolvedValue({ plan_revision: 4, plan: planWithLookSnapshot() })
    const post = vi.fn().mockResolvedValue({ plan_revision: 5, conflicts: [] })
    const api = { get, post }

    const loaded = await loadSessionPlan(911, api)
    const saved = await executeSavePlan(911, loaded.plan, 4, api)

    expect(loaded.ok).toBe(true)
    expect(saved.ok).toBe(true)
    expect(get).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledTimes(1)
  })
})
