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
  normalizeSelectionView,
  reduceSelectionView,
  isImportEligible,
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

  describe('filterLibraries with safe payload-free responses', () => {
    const PAYLOAD_FREE_LIBRARIES = [
      {
        library_key: 'safe_rooms',
        display_name: 'Safe Rooms Library',
        kind: 'rooms',
        revisions: [
          {
            source_id: 'studio_01',
            content_digest: 'digest_alpha_111',
            translation: { title: 'Bright Studio Room', mood: 'Airy atmosphere' },
            readiness: { status: 'ready' },
          },
          {
            source_id: 'kitchen_02',
            content_digest: 'digest_beta_222',
            translation: { title: 'Modern Kitchen' },
            readiness: { status: 'pending' },
          },
        ],
        auxiliary: [
          {
            kind: 'translation_map',
            content_digest: 'aux_digest_333',
          },
        ],
      },
    ]

    it('filters correctly when revisions completely omit payload property', () => {
      // By library key
      const matchKey = filterLibraries(PAYLOAD_FREE_LIBRARIES, { query: 'safe_rooms' })
      expect(matchKey).toHaveLength(1)

      // By source ID
      const matchSource = filterLibraries(PAYLOAD_FREE_LIBRARIES, { query: 'kitchen_02' })
      expect(matchSource).toHaveLength(1)
      expect(matchSource[0].revisions).toHaveLength(1)
      expect(matchSource[0].revisions[0].source_id).toBe('kitchen_02')

      // By content digest
      const matchDigest = filterLibraries(PAYLOAD_FREE_LIBRARIES, { query: 'digest_alpha_111' })
      expect(matchDigest).toHaveLength(1)
      expect(matchDigest[0].revisions).toHaveLength(1)
      expect(matchDigest[0].revisions[0].source_id).toBe('studio_01')

      // By translated value
      const matchTrans = filterLibraries(PAYLOAD_FREE_LIBRARIES, { query: 'Airy atmosphere' })
      expect(matchTrans).toHaveLength(1)
      expect(matchTrans[0].revisions[0].source_id).toBe('studio_01')

      // By auxiliary content digest
      const matchAux = filterLibraries(PAYLOAD_FREE_LIBRARIES, { query: 'aux_digest_333' })
      expect(matchAux).toHaveLength(1)
      expect(matchAux[0].auxiliary).toHaveLength(1)
    })

    it('never accesses or requires payload on revision objects', () => {
      const protectedRev = {
        source_id: 'no_payload_source',
        content_digest: 'digest_safe_444',
        translation: { description: 'Verified safe' },
      }
      Object.defineProperty(protectedRev, 'payload', {
        get() {
          throw new Error('Forbidden: payload was accessed during library filtering')
        },
      })

      const testLibs = [
        {
          library_key: 'protected_lib',
          display_name: 'Protected Lib',
          kind: 'custom',
          revisions: [protectedRev],
          auxiliary: [],
        },
      ]

      expect(() => {
        const result = filterLibraries(testLibs, { query: 'Verified safe' })
        expect(result).toHaveLength(1)
      }).not.toThrow()
    })
  })

  describe('normalizeSelectionView (Public Boundary & Privacy Enforcement)', () => {
    const validRawView = {
      selection_id: 'sel_test_norm',
      selection_revision: 2,
      state: 'open',
      expires_at: '2026-09-18T12:00:00Z',
      files: [
        {
          file_id: 'fid_1',
          file_name: 'test.json',
          byte_count: 120,
          declared_library: 'characters',
          effective_library_key: 'characters',
          matched_auxiliary_kinds: ['camera_preset'],
          effective_auxiliary_kind: null,
          status: 'staged',
          staged_path: '/tmp/private/internal/staged/character.json',
          mtime_ns: 123456789,
          fingerprint: 'sha256-abc',
        },
      ],
      preview: {
        preview_token: 'ptok_123',
        manifest_digest: '0123456789abcdef',
        committable: true,
        report: {
          summary: { files: 1, accepted: 1 },
          outcomes: { accepted: 1 },
          details: [{ library_key: 'characters', source_id: 'c1', action: 'accept', reason: 'ok' }],
        },
        private_hash_tree: { secret: 'do_not_leak' },
      },
      commit_result: null,
      cleanup_warning: 'Cleanup warning text',
      cleanup_state: { attempts: 3, last_error: 'disk_busy' },
      raw_payload: { raw: 'data' },
    }

    it('reconstructs a clean SelectionView and strictly strips private fields', () => {
      const normalized = normalizeSelectionView(validRawView)
      expect(normalized).not.toBeNull()

      // Allowlisted public fields
      expect(normalized.selection_id).toBe('sel_test_norm')
      expect(normalized.selection_revision).toBe(2)
      expect(normalized.state).toBe('open')
      expect(normalized.expires_at).toBe('2026-09-18T12:00:00Z')
      expect(normalized.cleanup_warning).toBe('Cleanup warning text')
      expect(normalized.files).toHaveLength(1)
      expect(normalized.files[0]).toEqual({
        file_id: 'fid_1',
        file_name: 'test.json',
        byte_count: 120,
        declared_library: 'characters',
        effective_library_key: 'characters',
        matched_auxiliary_kinds: ['camera_preset'],
        effective_auxiliary_kind: null,
        status: 'staged',
      })
      expect(normalized.preview).toEqual({
        preview_token: 'ptok_123',
        manifest_digest: '0123456789abcdef',
        committable: true,
        report: expect.objectContaining({
          phase: 'preview',
          summary: expect.objectContaining({ files: 1, accepted: 1 }),
        }),
      })

      // Forbidden private fields must not exist
      expect(normalized.cleanup_state).toBeUndefined()
      expect(normalized.raw_payload).toBeUndefined()
      expect(normalized.files[0].staged_path).toBeUndefined()
      expect(normalized.files[0].mtime_ns).toBeUndefined()
      expect(normalized.files[0].fingerprint).toBeUndefined()
      expect(normalized.preview.private_hash_tree).toBeUndefined()

      // Exact allowed keys
      expect(Object.keys(normalized).sort()).toEqual([
        'cleanup_warning',
        'commit_result',
        'expires_at',
        'files',
        'preview',
        'selection_id',
        'selection_revision',
        'state',
      ])
    })

    it('never evaluates getters for private fields during normalization', () => {
      const objectWithThrowingGetters = {
        selection_id: 'sel_getter_test',
        selection_revision: 1,
        state: 'open',
        expires_at: '2026-09-18T12:00:00Z',
        files: [],
        preview: null,
        commit_result: null,
        cleanup_warning: null,
      }
      Object.defineProperty(objectWithThrowingGetters, 'staged_path', {
        get() {
          throw new Error('Forbidden: staged_path getter called!')
        },
      })
      Object.defineProperty(objectWithThrowingGetters, 'cleanup_state', {
        get() {
          throw new Error('Forbidden: cleanup_state getter called!')
        },
      })
      Object.defineProperty(objectWithThrowingGetters, 'raw_payload', {
        get() {
          throw new Error('Forbidden: raw_payload getter called!')
        },
      })

      expect(() => {
        const normalized = normalizeSelectionView(objectWithThrowingGetters)
        expect(normalized).not.toBeNull()
        expect(normalized.selection_id).toBe('sel_getter_test')
      }).not.toThrow()
    })

    it('fails closed (returns null) on missing mandatory fields or invalid types', () => {
      expect(normalizeSelectionView(null)).toBeNull()
      expect(normalizeSelectionView(undefined)).toBeNull()
      expect(normalizeSelectionView([])).toBeNull()
      expect(normalizeSelectionView({})).toBeNull()
      expect(normalizeSelectionView({ ...validRawView, selection_id: '' })).toBeNull()
      expect(normalizeSelectionView({ ...validRawView, selection_id: 123 })).toBeNull()
      expect(normalizeSelectionView({ ...validRawView, selection_revision: -1 })).toBeNull()
      expect(normalizeSelectionView({ ...validRawView, selection_revision: 1.5 })).toBeNull()
      expect(normalizeSelectionView({ ...validRawView, selection_revision: '2' })).toBeNull()
      expect(normalizeSelectionView({ ...validRawView, selection_revision: 9007199254740992 })).toBeNull()
      expect(normalizeSelectionView({ ...validRawView, state: 'invalid_state' })).toBeNull()
      expect(normalizeSelectionView({ ...validRawView, expires_at: '' })).toBeNull()
      expect(normalizeSelectionView({ ...validRawView, files: 'not_an_array' })).toBeNull()
      expect(normalizeSelectionView({ ...validRawView, files: [{ file_id: 'f1' }] })).toBeNull() // missing byte_count
      expect(normalizeSelectionView({ ...validRawView, preview: 'not_an_object' })).toBeNull()
      expect(normalizeSelectionView({ ...validRawView, commit_result: 'not_an_object' })).toBeNull()
    })
  })

  describe('reduceSelectionView (Highest Revision Wins & Stale Response Protection)', () => {
    const viewRev3 = {
      selection_id: 'sel_123',
      selection_revision: 3,
      state: 'open',
      expires_at: '2026-09-18T12:00:00Z',
      files: [],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    const viewRev4 = {
      selection_id: 'sel_123',
      selection_revision: 4,
      state: 'open',
      expires_at: '2026-09-18T12:00:00Z',
      files: [{ file_id: 'f1', file_name: 'test.json', byte_count: 100 }],
      preview: null,
      commit_result: null,
      cleanup_warning: null,
    }

    it('accepts the initial candidate view', () => {
      const res = reduceSelectionView(null, viewRev3, { activeSelectionId: 'sel_123' })
      expect(res.accepted).toBe(true)
      expect(res.view.selection_id).toBe('sel_123')
      expect(res.view.selection_revision).toBe(3)
    })

    it('rejects candidate for a different selection_id within same epoch', () => {
      const differentSel = { ...viewRev3, selection_id: 'sel_other' }
      const res = reduceSelectionView(null, differentSel, { activeSelectionId: 'sel_123' })
      expect(res.accepted).toBe(false)
      expect(res.view).toBeNull()

      const res2 = reduceSelectionView(viewRev3, differentSel)
      expect(res2.accepted).toBe(false)
      expect(res2.view).toEqual(viewRev3)
    })

    it('rejects malformed views and non-safe integer revisions', () => {
      expect(reduceSelectionView(viewRev3, null).accepted).toBe(false)
      expect(reduceSelectionView(viewRev3, { selection_id: 'sel_123' }).accepted).toBe(false)
      expect(reduceSelectionView(viewRev3, { selection_id: 'sel_123', selection_revision: -1 }).accepted).toBe(false)
      expect(reduceSelectionView(viewRev3, { selection_id: 'sel_123', selection_revision: '4' }).accepted).toBe(false)
      expect(reduceSelectionView(viewRev3, { selection_id: 'sel_123', selection_revision: 9007199254740992 }).accepted).toBe(false)
    })

    it('Case A: drops responses from older epoch (candidateEpoch < currentEpoch)', () => {
      const current = { ...viewRev3, selection_id: 'sel_new', selection_revision: 1 }
      const olderCandidate = { ...viewRev4, selection_id: 'sel_old', selection_revision: 10 }
      const res = reduceSelectionView(current, olderCandidate, {
        activeSelectionId: 'sel_new',
        currentEpoch: 2,
        candidateEpoch: 1,
        currentGen: 1,
        candidateGen: 5,
      })
      expect(res.accepted).toBe(false)
      expect(res.view).toEqual(current)
    })

    it('Case B: adopts candidate from newer epoch (candidateEpoch > currentEpoch)', () => {
      const current = { ...viewRev3, selection_id: 'sel_old', selection_revision: 5 }
      const newerCandidate = { ...viewRev4, selection_id: 'sel_new', selection_revision: 1 }
      const res = reduceSelectionView(current, newerCandidate, {
        activeSelectionId: 'sel_old',
        currentEpoch: 1,
        candidateEpoch: 2,
        currentGen: 5,
        candidateGen: 1,
      })
      expect(res.accepted).toBe(true)
      expect(res.view.selection_id).toBe('sel_new')
      expect(res.view.selection_revision).toBe(1)
    })

    it('Case C: request A initiated first (gen 1) but completes with rev 4; request B initiated later (gen 2) completes with rev 3 -> rev 4 wins', () => {
      let state = { view: null, gen: 0 }
      // Response B arrives first with rev 3
      const step1 = reduceSelectionView(state.view, viewRev3, {
        activeSelectionId: 'sel_123',
        currentGen: state.gen,
        candidateGen: 2,
      })
      expect(step1.accepted).toBe(true)
      state = { view: step1.view, gen: step1.gen }
      expect(state.view.selection_revision).toBe(3)

      // Response A arrives later with rev 4 (higher revision despite earlier initiation gen 1)
      const step2 = reduceSelectionView(state.view, viewRev4, {
        activeSelectionId: 'sel_123',
        currentGen: state.gen,
        candidateGen: 1,
      })
      expect(step2.accepted).toBe(true)
      expect(step2.view.selection_revision).toBe(4)
    })

    it('Case D: rev 4 arrives first, rev 3 arrives later -> rev 4 remains', () => {
      let state = { view: null, gen: 0 }
      // Response B (rev 4) arrives first
      const step1 = reduceSelectionView(state.view, viewRev4, {
        activeSelectionId: 'sel_123',
        currentGen: state.gen,
        candidateGen: 2,
      })
      expect(step1.accepted).toBe(true)
      state = { view: step1.view, gen: step1.gen }
      expect(state.view.selection_revision).toBe(4)

      // Response A (rev 3) arrives later
      const step2 = reduceSelectionView(state.view, viewRev3, {
        activeSelectionId: 'sel_123',
        currentGen: state.gen,
        candidateGen: 1,
      })
      expect(step2.accepted).toBe(false)
      expect(step2.view.selection_revision).toBe(4)
    })

    it('protects equal revisions using operation generation fencing', () => {
      const candidateOlder = { ...viewRev4, state: 'committing' }
      const candidateNewer = { ...viewRev4, state: 'committed' }

      // Newer generation accepted
      const resNewer = reduceSelectionView(viewRev4, candidateNewer, {
        activeSelectionId: 'sel_123',
        currentGen: 5,
        candidateGen: 6,
      })
      expect(resNewer.accepted).toBe(true)
      expect(resNewer.view.state).toBe('committed')

      // Older generation rejected
      const resOlder = reduceSelectionView(resNewer.view, candidateOlder, {
        activeSelectionId: 'sel_123',
        currentGen: 6,
        candidateGen: 4,
      })
      expect(resOlder.accepted).toBe(false)
      expect(resOlder.view.state).toBe('committed')
    })
  })

  describe('isImportEligible', () => {
    const baseView = {
      selection_id: 'sel_100',
      selection_revision: 2,
      state: 'open',
      preview: {
        committable: true,
        preview_token: 'tok_valid_123',
        manifest_digest: '0123456789abcdef',
        report: { summary: { files: 1 } },
      },
    }

    it('returns true for an open view with complete committable preview and no pending mutations', () => {
      expect(isImportEligible(baseView, { activeSelectionId: 'sel_100', pendingMutation: false })).toBe(true)
    })

    it('returns false when preview is missing or not committable', () => {
      expect(isImportEligible({ ...baseView, preview: null })).toBe(false)
      expect(isImportEligible({ ...baseView, preview: { ...baseView.preview, committable: false } })).toBe(false)
    })

    it('returns false when preview_token is missing, null, empty or whitespace', () => {
      expect(isImportEligible({ ...baseView, preview: { ...baseView.preview, preview_token: null } })).toBe(false)
      expect(isImportEligible({ ...baseView, preview: { ...baseView.preview, preview_token: '' } })).toBe(false)
      expect(isImportEligible({ ...baseView, preview: { ...baseView.preview, preview_token: '   ' } })).toBe(false)
      expect(isImportEligible({ ...baseView, preview: { ...baseView.preview, preview_token: 123 } })).toBe(false)
    })

    it('returns false when manifest_digest is missing, non-hex, too short, too long or uppercase', () => {
      expect(isImportEligible({ ...baseView, preview: { ...baseView.preview, manifest_digest: null } })).toBe(false)
      expect(isImportEligible({ ...baseView, preview: { ...baseView.preview, manifest_digest: '' } })).toBe(false)
      expect(isImportEligible({ ...baseView, preview: { ...baseView.preview, manifest_digest: 'shorthex123' } })).toBe(false) // < 16
      expect(isImportEligible({ ...baseView, preview: { ...baseView.preview, manifest_digest: 'not_hex_chars_at_all!' } })).toBe(false)
      expect(isImportEligible({ ...baseView, preview: { ...baseView.preview, manifest_digest: '0123456789ABCDEF' } })).toBe(false) // uppercase
      expect(isImportEligible({ ...baseView, preview: { ...baseView.preview, manifest_digest: 'a'.repeat(65) } })).toBe(false) // > 64
    })

    it('returns false when report is missing, null, or not an object', () => {
      expect(isImportEligible({ ...baseView, preview: { ...baseView.preview, report: null } })).toBe(false)
      expect(isImportEligible({ ...baseView, preview: { ...baseView.preview, report: [] } })).toBe(false)
      expect(isImportEligible({ ...baseView, preview: { ...baseView.preview, report: 'string' } })).toBe(false)
    })

    it('returns false when state is terminal or committing', () => {
      expect(isImportEligible({ ...baseView, state: 'committing' })).toBe(false)
      expect(isImportEligible({ ...baseView, state: 'committed' })).toBe(false)
      expect(isImportEligible({ ...baseView, state: 'cancelled' })).toBe(false)
      expect(isImportEligible({ ...baseView, state: 'expired' })).toBe(false)
    })

    it('returns false when a mutation is in-flight or selection ID does not match', () => {
      expect(isImportEligible(baseView, { activeSelectionId: 'sel_100', pendingMutation: true })).toBe(false)
      expect(isImportEligible(baseView, { activeSelectionId: 'sel_different', pendingMutation: false })).toBe(false)
      expect(isImportEligible(null)).toBe(false)
    })
  })
})
