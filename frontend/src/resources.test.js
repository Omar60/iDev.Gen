import { describe, it, expect } from 'vitest'
import {
  normalizeFieldRole,
  extractCategories,
  filterLibraries,
  checkReadiness,
  sessionCreationActions,
  selectAvailableModelId,
  exactRevisionTriple,
  buildSessionDraftPayload,
  parsePreviewSummary,
  parseTranslationPreview,
} from './resources.js'

describe('resources module', () => {
  const SAMPLE_LIBRARIES = [
    {
      id: 1,
      library_key: 'test_rooms',
      display_name: 'Test Rooms Library',
      kind: 'rooms',
      revisions: [
        {
          id: 101,
          source_id: 'studio_room_01',
          content_digest: 'digest_abc_123',
          translation: { label: 'Studio Room', scene_theme: 'A bright airy photo studio' },
          readiness: {
            status: 'ready',
            pending_fields: {},
            field_readiness: [
              { name: 'label', role: 'selection_metadata', translated: true, reason: '' },
              { name: 'scene_theme', role: 'descriptive_input', translated: true, reason: '' },
              { name: 'weight', role: 'selection_metadata', translated: true, reason: '' },
            ],
          },
        },
        {
          id: 102,
          source_id: 'studio_room_01',
          content_digest: 'digest_abc_updated',
          translation: { label: 'Studio Room Updated', scene_theme: 'A warm photo studio' },
          readiness: {
            status: 'pending',
            pending_fields: {
              scene_theme: "Required field 'scene_theme' lacks a valid English translation",
            },
            field_readiness: [
              { name: 'label', role: 'selection_metadata', translated: true, reason: '' },
              {
                name: 'scene_theme',
                role: 'descriptive_input',
                translated: false,
                reason: "Required field 'scene_theme' lacks a valid English translation",
              },
            ],
          },
        },
      ],
      auxiliary: [],
    },
    {
      id: 2,
      library_key: 'test_fused',
      display_name: 'Fused Scenes Collection',
      kind: 'fused_scenes',
      revisions: [
        {
          id: 201,
          source_id: 'fused_scene_01',
          content_digest: 'digest_fused_789',
          payload: { prompt: 'Dramatic lighting from the side' },
          translation: { prompt: 'Dramatic lighting from the side' },
          readiness: {
            status: 'ready',
            pending_fields: {},
            field_readiness: [
              { name: 'prompt', role: 'descriptive_input', translated: true, reason: '' },
              { name: 'notes', role: 'intentionally_unused', translated: true, reason: '' },
            ],
          },
        },
      ],
      auxiliary: [
        {
          auxiliary_id: 1,
          kind: 'translation_map',
          content_digest: 'digest_aux_map',
        },
      ],
    },
  ]

  describe('normalizeFieldRole', () => {
    it('normalizes backend role strings to specification-compliant names', () => {
      expect(normalizeFieldRole('descriptive_input')).toBe('descriptive input')
      expect(normalizeFieldRole('selection_metadata')).toBe('selection metadata')
      expect(normalizeFieldRole('writer_guidance')).toBe('writer guidance')
      expect(normalizeFieldRole('auxiliary_data')).toBe('auxiliary data')
      expect(normalizeFieldRole('intentionally_unused')).toBe('unused data')
      expect(normalizeFieldRole('identity')).toBe('identity')
      expect(normalizeFieldRole('unmapped')).toBe('unmapped')
    })

    it('handles falsy or custom role names gracefully', () => {
      expect(normalizeFieldRole(null)).toBe('unused data')
      expect(normalizeFieldRole(undefined)).toBe('unused data')
      expect(normalizeFieldRole('')).toBe('unused data')
      expect(normalizeFieldRole('custom_role_name')).toBe('custom role name')
    })
  })

  describe('extractCategories', () => {
    it('extracts unique sorted kind values from libraries', () => {
      const categories = extractCategories(SAMPLE_LIBRARIES)
      expect(categories).toEqual(['fused_scenes', 'rooms'])
    })

    it('handles empty or missing libraries', () => {
      expect(extractCategories([])).toEqual([])
      expect(extractCategories(null)).toEqual([])
    })
  })

  describe('filterLibraries', () => {
    it('returns all libraries when no filters are provided', () => {
      const result = filterLibraries(SAMPLE_LIBRARIES)
      expect(result).toHaveLength(2)
    })

    it('filters by category / kind correctly', () => {
      const roomsOnly = filterLibraries(SAMPLE_LIBRARIES, { category: 'rooms' })
      expect(roomsOnly).toHaveLength(1)
      expect(roomsOnly[0].library_key).toBe('test_rooms')

      const fusedOnly = filterLibraries(SAMPLE_LIBRARIES, { category: 'fused_scenes' })
      expect(fusedOnly).toHaveLength(1)
      expect(fusedOnly[0].library_key).toBe('test_fused')
    })

    it('filters by text query across library metadata and revision content', () => {
      // Matches library name
      const byLibName = filterLibraries(SAMPLE_LIBRARIES, { query: 'Fused' })
      expect(byLibName).toHaveLength(1)
      expect(byLibName[0].library_key).toBe('test_fused')

      // Matches revision source_id
      const bySourceId = filterLibraries(SAMPLE_LIBRARIES, { query: 'studio_room' })
      expect(bySourceId).toHaveLength(1)
      expect(bySourceId[0].revisions).toHaveLength(2)

      // Matches specific content_digest
      const byDigest = filterLibraries(SAMPLE_LIBRARIES, { query: 'digest_abc_updated' })
      expect(byDigest).toHaveLength(1)
      expect(byDigest[0].revisions).toHaveLength(1)
      expect(byDigest[0].revisions[0].content_digest).toBe('digest_abc_updated')

      // Matches translated text
      const byTrans = filterLibraries(SAMPLE_LIBRARIES, { query: 'airy photo' })
      expect(byTrans).toHaveLength(1)
      expect(byTrans[0].revisions[0].source_id).toBe('studio_room_01')
    })

    it('returns empty array when filter matches nothing', () => {
      const result = filterLibraries(SAMPLE_LIBRARIES, { query: 'nonexistent_query_xyz' })
      expect(result).toEqual([])
    })

    it('handles empty inventory gracefully', () => {
      expect(filterLibraries([])).toEqual([])
      expect(filterLibraries(null)).toEqual([])
    })
  })

  describe('checkReadiness', () => {
    it('recognizes a ready revision', () => {
      const readyRev = SAMPLE_LIBRARIES[0].revisions[0]
      const verdict = checkReadiness(readyRev)
      expect(verdict.isReady).toBe(true)
      expect(verdict.status).toBe('ready')
      expect(verdict.reasons).toEqual([])
    })

    it('recognizes a pending revision and returns specific field reasons', () => {
      const pendingRev = SAMPLE_LIBRARIES[0].revisions[1]
      const verdict = checkReadiness(pendingRev)
      expect(verdict.isReady).toBe(false)
      expect(verdict.status).toBe('pending')
      expect(verdict.reasons).toHaveLength(1)
      expect(verdict.reasons[0]).toContain("Required field 'scene_theme'")
    })

    it('defaults to safe pending explanation when reasons are not explicitly provided', () => {
      const barePending = { readiness: { status: 'pending' } }
      const verdict = checkReadiness(barePending)
      expect(verdict.isReady).toBe(false)
      expect(verdict.reasons).toContain('Revision is pending required translations or adaptations')
    })

    it('surfaces sidecar_error from readiness coverage in reasons', () => {
      const rev = {
        readiness: {
          status: 'pending',
          coverage: {
            sidecar_error: "Disallowed field in translation sidecar: 'weight'",
          },
        },
      }
      const verdict = checkReadiness(rev)
      expect(verdict.isReady).toBe(false)
      expect(verdict.reasons).toContain("Disallowed field in translation sidecar: 'weight'")
    })

    it('surfaces sidecar_error from top-level coverage in reasons', () => {
      const rev = {
        status: 'pending',
        coverage: {
          sidecar_error: 'Conflicting alias entries in translation sidecar',
        },
      }
      const verdict = checkReadiness(rev)
      expect(verdict.isReady).toBe(false)
      expect(verdict.reasons).toContain('Conflicting alias entries in translation sidecar')
    })
  })

  describe('session entry selection', () => {
    const models = [
      { id: 42, name: 'InventedModel' },
      { id: 84, name: 'SecondModel' },
    ]

    it('uses resource planning as the normal entry and keeps legacy explicit', () => {
      expect(sessionCreationActions(42)).toEqual({
        primary: { mode: 'resource-v1', path: '/resources/42' },
        legacy: { mode: 'legacy' },
      })
    })

    it('selects an existing requested model', () => {
      expect(selectAvailableModelId(models, '84')).toBe('84')
    })

    it('falls back safely when the requested model is invalid or absent', () => {
      expect(selectAvailableModelId(models, '999')).toBe('42')
      expect(selectAvailableModelId(models)).toBe('42')
      expect(selectAvailableModelId([], '42')).toBe('')
    })
  })

  describe('exactRevisionTriple', () => {
    it('extracts complete triple when all parts are valid', () => {
      const rev = SAMPLE_LIBRARIES[0].revisions[0]
      const triple = exactRevisionTriple('test_rooms', rev)
      expect(triple).toEqual({
        library_key: 'test_rooms',
        source_id: 'studio_room_01',
        content_digest: 'digest_abc_123',
      })
    })

    it('preserves distinct content_digest for same source_id without collapsing', () => {
      const rev1 = SAMPLE_LIBRARIES[0].revisions[0]
      const rev2 = SAMPLE_LIBRARIES[0].revisions[1]
      const triple1 = exactRevisionTriple('test_rooms', rev1)
      const triple2 = exactRevisionTriple('test_rooms', rev2)
      expect(triple1.source_id).toBe(triple2.source_id)
      expect(triple1.content_digest).not.toBe(triple2.content_digest)
    })

    it('throws error when any part of the revision identity is missing', () => {
      expect(() => exactRevisionTriple('', { source_id: 'a', content_digest: 'b' }))
        .toThrow(/Incomplete revision identity/)
      expect(() => exactRevisionTriple('lib', { source_id: '', content_digest: 'b' }))
        .toThrow(/Incomplete revision identity/)
      expect(() => exactRevisionTriple('lib', { source_id: 'a', content_digest: '' }))
        .toThrow(/Incomplete revision identity/)
    })
  })

  describe('buildSessionDraftPayload', () => {
    const model = { id: 42, name: 'InventedModel', workflow_id: 10 }
    const revision = SAMPLE_LIBRARIES[0].revisions[0]

    it('constructs valid session and plan draft payloads for resource-v1', () => {
      const payload = buildSessionDraftPayload({
        model,
        sessionName: 'Custom Session Name',
        revision,
        libraryKey: 'test_rooms',
        look: 'Constant look text',
        initialWardrobe: 'Constant wardrobe text',
      })

      // Session creation payload
      expect(payload.session.model_id).toBe(42)
      expect(payload.session.name).toBe('Custom Session Name')
      expect(payload.session.composition_mode).toBe('resource-v1')
      expect(payload.session.workflow_id).toBe(10)
      expect(payload.session.look).toBe('Constant look text')
      expect(payload.session.wardrobe).toBe('Constant wardrobe text')

      // Plan payload
      expect(payload.plan.version).toBe('resource-v1')
      expect(payload.plan.look).toBe('Constant look text')
      expect(payload.plan.initial_wardrobe).toBe('Constant wardrobe text')
      expect(payload.plan.takes).toEqual([{ take_id: 'take-001' }])
      expect(payload.plan.selected_resources).toEqual([
        {
          library_key: 'test_rooms',
          source_id: 'studio_room_01',
          content_digest: 'digest_abc_123',
        },
      ])
      expect(payload.plan.wardrobe_changes).toEqual([])
      expect(payload.expected_revision).toBe(0)
    })

    it('strictly binds to explicitly selected model without letting resource suggest character', () => {
      const payload = buildSessionDraftPayload({
        model: { id: 99, name: 'StrictModel', workflow_id: null },
        revision: {
          ...revision,
          payload: { character: 'Contradictory Character Name' },
        },
        libraryKey: 'test_rooms',
      })

      expect(payload.session.model_id).toBe(99)
      expect(payload.session.name).toBe('StrictModel - studio_room_01')
      expect(payload.session.composition_mode).toBe('resource-v1')
    })

    it('throws error if model is not selected', () => {
      expect(() => buildSessionDraftPayload({ model: null, revision, libraryKey: 'test_rooms' }))
        .toThrow(/An explicit model selection is required/)
    })
  })

  describe('parsePreviewSummary', () => {
    const sampleReport = {
      phase: 'preview',
      summary: {
        files: 2,
        inputs: 10,
        accepted: 6,
        auxiliary: 1,
        duplicates: 1,
        unresolved: 2,
        new: 3,
        unchanged: 2,
        updated: 1,
        missing: 1,
      },
      files: [
        {
          file_path: 'scenes.json',
          library_key: 'scenes_lib',
          accepted: [
            {
              source_id: 'scene_01',
              classification: 'new',
              new_content_digest: 'dig1',
            },
            {
              source_id: 'scene_02',
              classification: 'unchanged',
              new_content_digest: 'dig2',
            },
            {
              source_id: 'scene_03',
              classification: 'updated',
              new_content_digest: 'dig3',
              previous_content_digest: 'dig3_old',
            },
          ],
          auxiliary: [
            {
              kind: 'translation_map',
              classification: 'new',
              new_content_digest: 'auxdig1',
            },
          ],
          duplicates: [
            {
              source_id: 'dup_scene',
              occurrences: 2,
            },
          ],
          unresolved: [
            {
              bucket: 'unsupported_shape',
              index: 5,
              reason: 'expected mapping but got scalar',
              received_type: 'int',
              expected_kind: 'rooms',
              identifier_fields: [],
            },
          ],
        },
        {
          file_path: 'malformed.json',
          library_key: 'broken_lib',
          accepted: [],
          auxiliary: [],
          duplicates: [],
          unresolved: [
            {
              bucket: 'file_read_error',
              index: 0,
              reason: 'source file could not be read or parsed',
              received_type: 'json_decode_error',
              expected_kind: '',
              identifier_fields: [],
            },
          ],
        },
      ],
      missing_source_entries: [
        {
          library_key: 'scenes_lib',
          source_id: 'vanished_scene',
          latest_content_digest: 'dig_vanished',
        },
      ],
    }

    it('extracts all inventory outcome counts completely', () => {
      const parsed = parsePreviewSummary(sampleReport)
      expect(parsed.counts).toEqual({
        files: 2,
        inputs: 10,
        accepted: 6,
        auxiliary: 1,
        duplicates: 1,
        unresolved: 2,
        new: 3,
        unchanged: 2,
        updated: 1,
        missing: 1,
      })
    })

    it('preserves and separates new, unchanged, updated, and unresolved items', () => {
      const parsed = parsePreviewSummary(sampleReport)
      expect(parsed.accepted).toHaveLength(3)
      expect(parsed.accepted.map((a) => a.classification)).toEqual(['new', 'unchanged', 'updated'])
      expect(parsed.unresolved).toHaveLength(2)
      expect(parsed.unresolved[0].bucket).toBe('unsupported_shape')
      expect(parsed.unresolved[1].bucket).toBe('file_read_error')
      expect(parsed.auxiliary).toHaveLength(1)
      expect(parsed.duplicates).toHaveLength(1)
      expect(parsed.missing).toHaveLength(1)
      expect(parsed.missing[0].source_id).toBe('vanished_scene')
    })

    it('does not conflate import outcome classification with readiness status', () => {
      const parsed = parsePreviewSummary(sampleReport)
      // Every accepted outcome carries its import classification ('new', 'unchanged', 'updated')
      for (const item of parsed.accepted) {
        expect(['new', 'unchanged', 'updated']).toContain(item.classification)
        // Readiness is a separate property evaluated on stored revisions, not in the preview report
        expect(item.readiness).toBeUndefined()
      }
    })
  })

  describe('parseTranslationPreview', () => {
    it('returns null for null or empty input', () => {
      expect(parseTranslationPreview(null)).toBeNull()
      expect(parseTranslationPreview(undefined)).toBeNull()
    })

    it('parses and normalizes translation preview metrics correctly', () => {
      const raw = {
        library_key: 'test_rooms',
        total_revisions: 10,
        matched_revisions: 8,
        unmatched_map_entries: 2,
        would_update: 5,
        unchanged: 3,
        would_be_ready: 7,
        would_remain_pending: 1,
        attestation_token: 'token.sig123',
        expires_at: 1700000000,
      }
      const parsed = parseTranslationPreview(raw)
      expect(parsed).toEqual({
        libraryKey: 'test_rooms',
        totalRevisions: 10,
        matchedRevisions: 8,
        unmatchedEntries: 2,
        wouldUpdate: 5,
        unchanged: 3,
        wouldBeReady: 7,
        wouldRemainPending: 1,
        attestationToken: 'token.sig123',
        expiresAt: 1700000000,
      })
    })
  })
})
