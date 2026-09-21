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
  buildTranslationMapFromRows,
  getRowIdentityKey,
  buildProposalRequestEntry,
  isRowEligibleForProposal,
  applyProposalsToRows,
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
        manifest_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
        committable: true,
        report: {
          version: 1,
          phase: 'preview',
          summary: {
            files: 1,
            inputs: 1,
            accepted: 1,
            auxiliary: 0,
            duplicates: 0,
            unresolved: 0,
            new: 1,
            unchanged: 0,
            updated: 0,
            missing: 0,
          },
          files: [
            {
              library_key: 'characters',
              total_inputs: 1,
              accepted: [
                {
                  source_id: 'c1',
                  library_key: 'characters',
                  kind: 'rooms',
                  classification: 'new',
                  new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
                  previous_content_digest: null,
                },
              ],
              auxiliary: [],
              duplicates: [],
              unresolved: [],
            },
          ],
          missing_source_entries: [],
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
        manifest_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
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

  describe('normalizeSelectionView - SafeCommitReport strict validation (A6)', () => {
    const validSafeCommitReport = {
      version: 1,
      phase: 'commit',
      summary: {
        files: 1,
        inputs: 1,
        accepted: 1,
        auxiliary: 0,
        duplicates: 0,
        unresolved: 0,
        new: 1,
        unchanged: 0,
        updated: 0,
        missing: 0,
        recorded: 1,
        new_scene_revisions: 1,
        unchanged_scene_revisions: 0,
        updated_scene_revisions: 0,
        new_auxiliary_revisions: 0,
        unchanged_auxiliary_revisions: 0,
      },
      files: [
        {
          library_key: 'scenes',
          total_inputs: 1,
          accepted: [
            {
              source_id: 'scene_1',
              library_key: 'scenes',
              kind: 'rooms',
              classification: 'new',
              new_content_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
              previous_content_digest: null,
            },
          ],
          auxiliary: [],
          duplicates: [],
          unresolved: [],
        },
      ],
      missing_source_entries: [],
    }

    const baseCommittedView = {
      selection_id: 'sel_committed_strict',
      selection_revision: 3,
      state: 'committed',
      expires_at: '2026-09-18T12:00:00Z',
      files: [
        {
          file_id: 'fid_1',
          file_name: 'test.json',
          byte_count: 120,
          declared_library: 'scenes',
          effective_library_key: 'scenes',
          matched_auxiliary_kinds: [],
          effective_auxiliary_kind: null,
          status: 'staged',
        },
      ],
      preview: null,
      commit_result: validSafeCommitReport,
      cleanup_warning: null,
    }

    // 1. Valid direct SafeCommitReport -> accepted and reconstructed exactly
    it('1. accepts valid direct SafeCommitReport and reconstructs it exactly', () => {
      const normalized = normalizeSelectionView(baseCommittedView)
      expect(normalized).not.toBeNull()
      expect(normalized.commit_result).toEqual(validSafeCommitReport)
      expect(Object.keys(normalized.commit_result).sort()).toEqual([
        'files',
        'missing_source_entries',
        'phase',
        'summary',
        'version',
      ])
    })

    // 2. Incomplete direct commit_result (e.g. only phase) -> rejected / fail closed
    it('2. rejects incomplete direct commit_result that only provides phase', () => {
      const view = {
        ...baseCommittedView,
        commit_result: { phase: 'commit' },
      }
      expect(normalizeSelectionView(view)).toBeNull()
    })

    // 3. Incomplete direct commit_result (e.g. only summary) -> rejected / fail closed
    it('3. rejects incomplete direct commit_result that only provides summary', () => {
      const view = {
        ...baseCommittedView,
        commit_result: { summary: { files: 1, recorded: 1 } },
      }
      expect(normalizeSelectionView(view)).toBeNull()
    })

    // 4. Private direct commit_result with staged_path/fingerprint/raw_payload -> rejected, NOT simply sanitized
    it('4. rejects private direct commit_result with private fields rather than silently sanitizing them', () => {
      // Top-level private field
      const viewWithTopPrivate = {
        ...baseCommittedView,
        commit_result: {
          ...validSafeCommitReport,
          staged_path: '/tmp/private/internal/path.json',
        },
      }
      expect(normalizeSelectionView(viewWithTopPrivate)).toBeNull()

      // Nested private field in files
      const viewWithFilePrivate = {
        ...baseCommittedView,
        commit_result: {
          ...validSafeCommitReport,
          files: [
            {
              ...validSafeCommitReport.files[0],
              staged_path: '/tmp/private/internal/path.json',
            },
          ],
        },
      }
      expect(normalizeSelectionView(viewWithFilePrivate)).toBeNull()

      // Nested private field in accepted outcome
      const viewWithAcceptedPrivate = {
        ...baseCommittedView,
        commit_result: {
          ...validSafeCommitReport,
          files: [
            {
              ...validSafeCommitReport.files[0],
              accepted: [
                {
                  ...validSafeCommitReport.files[0].accepted[0],
                  fingerprint: 'sha256-secret-hash',
                },
              ],
            },
          ],
        },
      }
      expect(normalizeSelectionView(viewWithAcceptedPrivate)).toBeNull()
    })

    // 5. Extra unknown field -> closed behavior according to contract
    it('5. rejects commit_result with extra unknown field according to closed contract', () => {
      const viewWithUnknown = {
        ...baseCommittedView,
        commit_result: {
          ...validSafeCommitReport,
          extra_unknown_property: 'not_allowed',
        },
      }
      expect(normalizeSelectionView(viewWithUnknown)).toBeNull()

      const viewWithFileUnknown = {
        ...baseCommittedView,
        commit_result: {
          ...validSafeCommitReport,
          files: [
            {
              ...validSafeCommitReport.files[0],
              extra_file_property: 123,
            },
          ],
        },
      }
      expect(normalizeSelectionView(viewWithFileUnknown)).toBeNull()
    })

    // 6. Malformed nested summary/files -> rejected (structural-only contract)
    it('6. rejects malformed nested summary/files and counter types per the structural contract', () => {
      // Negative counter in summary (transport contract: integer >= 0)
      const viewNegCounter = {
        ...baseCommittedView,
        commit_result: {
          ...validSafeCommitReport,
          summary: { ...validSafeCommitReport.summary, recorded: -1 },
        },
      }
      expect(normalizeSelectionView(viewNegCounter)).toBeNull()

      // Non-integer counter (transport contract: integer type)
      const viewNonIntegerCounter = {
        ...baseCommittedView,
        commit_result: {
          ...validSafeCommitReport,
          summary: { ...validSafeCommitReport.summary, recorded: 1.5 },
        },
      }
      expect(normalizeSelectionView(viewNonIntegerCounter)).toBeNull()

      // Non-string digest (transport contract: string)
      const viewBadDigestType = {
        ...baseCommittedView,
        commit_result: {
          ...validSafeCommitReport,
          files: [
            {
              ...validSafeCommitReport.files[0],
              accepted: [
                {
                  ...validSafeCommitReport.files[0].accepted[0],
                  new_content_digest: 123,
                },
              ],
            },
          ],
        },
      }
      expect(normalizeSelectionView(viewBadDigestType)).toBeNull()

      // malformed nested files (not a list)
      const viewFilesNotArray = {
        ...baseCommittedView,
        commit_result: {
          ...validSafeCommitReport,
          files: 'not_an_array',
        },
      }
      expect(normalizeSelectionView(viewFilesNotArray)).toBeNull()

      // malformed nested summary (not a dict)
      const viewSummaryNotDict = {
        ...baseCommittedView,
        commit_result: {
          ...validSafeCommitReport,
          summary: null,
        },
      }
      expect(normalizeSelectionView(viewSummaryNotDict)).toBeNull()

      // missing_source_entries not an array
      const viewMissingNotArray = {
        ...baseCommittedView,
        commit_result: {
          ...validSafeCommitReport,
          missing_source_entries: 'not_an_array',
        },
      }
      expect(normalizeSelectionView(viewMissingNotArray)).toBeNull()
    })

    // 7. Canonical committed SelectionView complete -> accepted
    it('7. accepts canonical committed SelectionView complete', () => {
      const normalized = normalizeSelectionView(baseCommittedView)
      expect(normalized).not.toBeNull()
      expect(normalized.state).toBe('committed')
      expect(normalized.selection_id).toBe('sel_committed_strict')
      expect(normalized.selection_revision).toBe(3)
      expect(normalized.files).toHaveLength(1)
      expect(normalized.commit_result).toEqual(validSafeCommitReport)
      expect(normalized.preview).toBeNull()
      expect(normalized.cleanup_warning).toBeNull()
    })

    // ---------------------------------------------------------------------
    // Positive regression (A6.4): backend-canonical responses must be accepted.
    // The frontend validates ONLY the public transport contract.
    // ---------------------------------------------------------------------
    describe('positive regression - backend-canonical SafeCommitReport (A6.4)', () => {
      const prevSummaryKeys = [
        'accepted', 'auxiliary', 'duplicates', 'files', 'inputs',
        'missing', 'new', 'unchanged', 'unresolved', 'updated',
      ]
      const commitSummaryKeys = [
        'accepted', 'auxiliary', 'duplicates', 'files', 'inputs',
        'missing', 'new', 'new_auxiliary_revisions', 'new_scene_revisions',
        'recorded', 'unchanged', 'unchanged_auxiliary_revisions',
        'unchanged_scene_revisions', 'unresolved', 'updated', 'updated_scene_revisions',
      ]
      const allZeroSummary = (keys) => Object.fromEntries(keys.map((k) => [k, 0]))
      const baseFileShape = (overrides = {}) => ({
        library_key: 'scenes',
        total_inputs: 0,
        accepted: [],
        auxiliary: [],
        duplicates: [],
        unresolved: [],
        ...overrides,
      })
      // Wrap a canonical report into a SelectionView envelope. The report's
      // `phase` determines whether it is placed in `commit_result` (commit)
      // or `preview.report` (preview). Both paths must validate identically.
      const wrapReport = (report) => {
        if (report.phase === 'preview') {
          return {
            ...baseCommittedView,
            selection_revision: 4,
            state: 'open',
            commit_result: null,
            preview: {
              preview_token: 'ptok_canon',
              manifest_digest: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
              committable: true,
              report,
            },
          }
        }
        return {
          ...baseCommittedView,
          commit_result: report,
        }
      }
      // Resolve the normalized report back from a SelectionView envelope,
      // regardless of whether it landed in commit_result or preview.report.
      const readReport = (normalized, phase) => {
        if (!normalized) return null
        if (phase === 'preview') return normalized.preview?.report ?? null
        return normalized.commit_result
      }

      it('A. accepts empty canonical report when the backend permits it', () => {
        const report = {
          version: 1,
          phase: 'commit',
          summary: allZeroSummary(commitSummaryKeys),
          files: [],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(normalized.commit_result).toEqual(report)
      })

      it('B. accepts a canonical commit with only `new` accepted items', () => {
        const newDigest = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        const report = {
          version: 1,
          phase: 'commit',
          summary: { ...allZeroSummary(commitSummaryKeys), files: 1, inputs: 1, accepted: 1, new: 1, recorded: 1, new_scene_revisions: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            accepted: [{
              source_id: 'src_new',
              library_key: 'scenes',
              kind: 'rooms',
              classification: 'new',
              new_content_digest: newDigest,
              previous_content_digest: null,
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(normalized.commit_result.files[0].accepted[0].classification).toBe('new')
      })

      it('C. accepts a canonical commit with only `updated` accepted items', () => {
        const oldDigest = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        const newDigest = 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'
        const report = {
          version: 1,
          phase: 'commit',
          summary: { ...allZeroSummary(commitSummaryKeys), files: 1, inputs: 1, accepted: 1, updated: 1, recorded: 1, new_scene_revisions: 1, updated_scene_revisions: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            accepted: [{
              source_id: 'src_updated',
              library_key: 'scenes',
              kind: 'rooms',
              classification: 'updated',
              new_content_digest: newDigest,
              previous_content_digest: oldDigest,
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(normalized.commit_result.files[0].accepted[0].classification).toBe('updated')
      })

      it('D. accepts a canonical commit with only `unchanged` accepted items', () => {
        const sameDigest = 'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd'
        const report = {
          version: 1,
          phase: 'commit',
          summary: { ...allZeroSummary(commitSummaryKeys), files: 1, inputs: 1, accepted: 1, unchanged: 1, recorded: 1, unchanged_scene_revisions: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            accepted: [{
              source_id: 'src_unchanged',
              library_key: 'scenes',
              kind: 'rooms',
              classification: 'unchanged',
              new_content_digest: sameDigest,
              previous_content_digest: sameDigest,
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(normalized.commit_result.files[0].accepted[0].classification).toBe('unchanged')
      })

      it('E. accepts a canonical commit with auxiliary items', () => {
        const auxDigest = 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
        const report = {
          version: 1,
          phase: 'commit',
          summary: { ...allZeroSummary(commitSummaryKeys), files: 1, inputs: 1, auxiliary: 1, recorded: 1, new_auxiliary_revisions: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            auxiliary: [{
              library_key: 'scenes',
              kind: 'translation_map',
              classification: 'new',
              new_content_digest: auxDigest,
              previous_content_digest: null,
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(normalized.commit_result.files[0].auxiliary[0].kind).toBe('translation_map')
      })

      it('F. accepts canonical missing_source_entries', () => {
        const digest = 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
        const report = {
          version: 1,
          phase: 'commit',
          summary: { ...allZeroSummary(commitSummaryKeys), missing: 1 },
          files: [],
          missing_source_entries: [{
            library_key: 'scenes',
            source_id: 'src_missing',
            latest_content_digest: digest,
          }],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(normalized.commit_result.missing_source_entries).toHaveLength(1)
        expect(normalized.commit_result.missing_source_entries[0].source_id).toBe('src_missing')
      })

      it('G. accepts unresolved bucket "malformed"', () => {
        const report = {
          version: 1,
          phase: 'preview',
          summary: { ...allZeroSummary(prevSummaryKeys), files: 1, inputs: 1, unresolved: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            unresolved: [{
              bucket: 'malformed',
              index: 0,
              reason: 'payload could not be parsed',
              received_type: 'object',
              expected_kind: '',
              identifier_fields: [],
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(readReport(normalized, 'preview').files[0].unresolved[0].bucket).toBe('malformed')
      })

      it('H. accepts unresolved bucket "unsupported"', () => {
        const report = {
          version: 1,
          phase: 'preview',
          summary: { ...allZeroSummary(prevSummaryKeys), files: 1, inputs: 1, unresolved: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            unresolved: [{
              bucket: 'unsupported',
              index: 1,
              reason: 'shape not supported by importer',
              received_type: 'array',
              expected_kind: '',
              identifier_fields: [],
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(readReport(normalized, 'preview').files[0].unresolved[0].bucket).toBe('unsupported')
      })

      it('I. accepts unresolved bucket "ambiguous_identifier"', () => {
        const report = {
          version: 1,
          phase: 'preview',
          summary: { ...allZeroSummary(prevSummaryKeys), files: 1, inputs: 1, unresolved: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            unresolved: [{
              bucket: 'ambiguous_identifier',
              index: 2,
              reason: 'multiple identifier fields present',
              received_type: 'object',
              expected_kind: 'rooms',
              identifier_fields: ['id', 'identifier'],
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(readReport(normalized, 'preview').files[0].unresolved[0].bucket).toBe('ambiguous_identifier')
      })

      it('J. accepts unresolved bucket "missing_identifier"', () => {
        const report = {
          version: 1,
          phase: 'preview',
          summary: { ...allZeroSummary(prevSummaryKeys), files: 1, inputs: 1, unresolved: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            unresolved: [{
              bucket: 'missing_identifier',
              index: 0,
              reason: 'no identifier provided',
              received_type: 'object',
              expected_kind: 'fused_scenes',
              identifier_fields: [],
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(readReport(normalized, 'preview').files[0].unresolved[0].bucket).toBe('missing_identifier')
      })

      it('K. accepts unresolved bucket "duplicate_identifier"', () => {
        const report = {
          version: 1,
          phase: 'preview',
          summary: { ...allZeroSummary(prevSummaryKeys), files: 1, inputs: 1, unresolved: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            unresolved: [{
              bucket: 'duplicate_identifier',
              index: 4,
              reason: 'two items carry the same identifier',
              received_type: 'object',
              expected_kind: 'rooms',
              identifier_fields: ['key'],
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(readReport(normalized, 'preview').files[0].unresolved[0].bucket).toBe('duplicate_identifier')
      })

      it('L. accepts unresolved bucket "auxiliary"', () => {
        const report = {
          version: 1,
          phase: 'preview',
          summary: { ...allZeroSummary(prevSummaryKeys), files: 1, inputs: 1, unresolved: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            unresolved: [{
              bucket: 'auxiliary',
              index: 0,
              reason: 'moved to auxiliary bucket',
              received_type: 'object',
              expected_kind: '',
              identifier_fields: ['id'],
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(readReport(normalized, 'preview').files[0].unresolved[0].bucket).toBe('auxiliary')
      })

      it('M. accepts unresolved bucket "file_read_error" with its exact canonical reason', () => {
        const report = {
          version: 1,
          phase: 'preview',
          summary: { ...allZeroSummary(prevSummaryKeys), files: 1, inputs: 1, unresolved: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            unresolved: [{
              bucket: 'file_read_error',
              index: 0,
              reason: 'source file could not be read or parsed',
              received_type: 'io_error',
              expected_kind: '',
              identifier_fields: [],
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(readReport(normalized, 'preview').files[0].unresolved[0].bucket).toBe('file_read_error')
      })

      it('N. accepts identifier_fields = [] (empty)', () => {
        const report = {
          version: 1,
          phase: 'preview',
          summary: { ...allZeroSummary(prevSummaryKeys), files: 1, inputs: 1, unresolved: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            unresolved: [{
              bucket: 'malformed',
              index: 0,
              reason: 'no identifiers',
              received_type: 'object',
              expected_kind: '',
              identifier_fields: [],
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(readReport(normalized, 'preview').files[0].unresolved[0].identifier_fields).toEqual([])
      })

      it('O. accepts identifier_fields = ["id"]', () => {
        const report = {
          version: 1,
          phase: 'preview',
          summary: { ...allZeroSummary(prevSummaryKeys), files: 1, inputs: 1, unresolved: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            unresolved: [{
              bucket: 'malformed',
              index: 0,
              reason: 'single identifier',
              received_type: 'object',
              expected_kind: '',
              identifier_fields: ['id'],
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(readReport(normalized, 'preview').files[0].unresolved[0].identifier_fields).toEqual(['id'])
      })

      it('P. accepts identifier_fields = ["identifier"]', () => {
        const report = {
          version: 1,
          phase: 'preview',
          summary: { ...allZeroSummary(prevSummaryKeys), files: 1, inputs: 1, unresolved: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            unresolved: [{
              bucket: 'malformed',
              index: 0,
              reason: 'single identifier',
              received_type: 'object',
              expected_kind: '',
              identifier_fields: ['identifier'],
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(readReport(normalized, 'preview').files[0].unresolved[0].identifier_fields).toEqual(['identifier'])
      })

      it('Q. accepts identifier_fields = ["key"]', () => {
        const report = {
          version: 1,
          phase: 'preview',
          summary: { ...allZeroSummary(prevSummaryKeys), files: 1, inputs: 1, unresolved: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            unresolved: [{
              bucket: 'malformed',
              index: 0,
              reason: 'single identifier',
              received_type: 'object',
              expected_kind: '',
              identifier_fields: ['key'],
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(readReport(normalized, 'preview').files[0].unresolved[0].identifier_fields).toEqual(['key'])
      })

      it('R. accepts identifier_fields ordered combos from {id, identifier, key}', () => {
        const baseSummary = { ...allZeroSummary(prevSummaryKeys), files: 1, inputs: 1, unresolved: 1 }
        const buildReport = (idFields) => ({
          version: 1,
          phase: 'preview',
          summary: baseSummary,
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            unresolved: [{
              bucket: 'ambiguous_identifier',
              index: 0,
              reason: 'multiple identifiers',
              received_type: 'object',
              expected_kind: '',
              identifier_fields: idFields,
            }],
          }],
          missing_source_entries: [],
        })
        expect(normalizeSelectionView(wrapReport(buildReport(['id', 'identifier'])))).not.toBeNull()
        expect(normalizeSelectionView(wrapReport(buildReport(['identifier', 'key'])))).not.toBeNull()
        expect(normalizeSelectionView(wrapReport(buildReport(['id', 'key'])))).not.toBeNull()
        expect(normalizeSelectionView(wrapReport(buildReport(['id', 'identifier', 'key'])))).not.toBeNull()
      })

      it('S. accepts a legal library_key like "fingerprint_archive"', () => {
        const newDigest = '1111111111111111111111111111111111111111111111111111111111111111'
        const report = {
          version: 1,
          phase: 'commit',
          summary: { ...allZeroSummary(commitSummaryKeys), files: 1, inputs: 1, accepted: 1, new: 1, recorded: 1, new_scene_revisions: 1 },
          files: [{
            library_key: 'fingerprint_archive',
            total_inputs: 1,
            accepted: [{
              source_id: 'fingerprint_scene',
              library_key: 'fingerprint_archive',
              kind: 'rooms',
              classification: 'new',
              new_content_digest: newDigest,
              previous_content_digest: null,
            }],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(normalized.commit_result.files[0].library_key).toBe('fingerprint_archive')
        expect(normalized.commit_result.files[0].accepted[0].library_key).toBe('fingerprint_archive')
      })

      it('T. preserves opaque identifier_fields strings without allowlist or ordering semantics', () => {
        const report = {
          version: 1,
          phase: 'preview',
          summary: { ...allZeroSummary(prevSummaryKeys), files: 1, inputs: 1, unresolved: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            unresolved: [{
              bucket: 'ambiguous_identifier',
              index: 0,
              reason: '',
              received_type: '',
              expected_kind: 'future_kind',
              identifier_fields: ['future_identifier', 'key', 'id'],
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(readReport(normalized, 'preview').files[0].unresolved[0]).toEqual(
          report.files[0].unresolved[0]
        )
      })

      it('U. accepts an arbitrary future unresolved bucket and opaque diagnostic strings', () => {
        const report = {
          version: 1,
          phase: 'preview',
          summary: { ...allZeroSummary(prevSummaryKeys), files: 1, inputs: 1, unresolved: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            unresolved: [{
              bucket: 'future_backend_bucket',
              index: 0,
              reason: '',
              received_type: '',
              expected_kind: 'future_kind',
              identifier_fields: ['name'],
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(readReport(normalized, 'preview').files[0].unresolved[0].bucket).toBe('future_backend_bucket')
      })

      it('V. treats library_key and digest contents as opaque strings', () => {
        const newDigest = '2222222222222222222222222222222222222222222222222222222222222222'
        const report = {
          version: 1,
          phase: 'commit',
          summary: { ...allZeroSummary(commitSummaryKeys), files: 1, inputs: 1, accepted: 1, new: 1, recorded: 1, new_scene_revisions: 1 },
          files: [{
            library_key: 'backend-owned/library',
            total_inputs: 1,
            accepted: [{
              source_id: 'src',
              library_key: 'backend-owned/library',
              kind: 'future_scene_kind',
              classification: 'future_classification',
              new_content_digest: 'opaque-digest',
              previous_content_digest: newDigest,
            }],
            auxiliary: [],
            duplicates: [],
            unresolved: [],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(normalized.commit_result.files[0].accepted[0]).toEqual(report.files[0].accepted[0])
      })

      it('W. rejects canonical-invalid preview.report even with otherwise valid preview envelope', () => {
        // phase-only object, structurally invalid canonical report
        const badReport = { phase: 'preview' }
        const view = {
          ...baseCommittedView,
          selection_revision: 4,
          state: 'open',
          commit_result: null,
          preview: {
            preview_token: 'ptok-bad-report',
            manifest_digest: '0123456789abcdef',
            committable: true,
            report: badReport,
          },
        }
        expect(normalizeSelectionView(view)).toBeNull()
      })

      it('X. accepts backend-valid received_type="" through the real SelectionView path', () => {
        const report = {
          version: 1,
          phase: 'preview',
          summary: { ...allZeroSummary(prevSummaryKeys), files: 1, inputs: 1, unresolved: 1 },
          files: [{
            ...baseFileShape(),
            total_inputs: 1,
            unresolved: [{
              bucket: 'malformed',
              index: 0,
              reason: 'Invalid input',
              received_type: '',
              expected_kind: '',
              identifier_fields: [],
            }],
          }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(normalized.preview.report.files[0].unresolved[0].received_type).toBe('')
      })

      it('Y. accepts a backend-valid Unicode library_key beyond 64 UTF-16 code units', () => {
        const unicodeLibraryKey = '🎨'.repeat(40)
        expect(unicodeLibraryKey.length).toBeGreaterThan(64)
        const report = {
          version: 1,
          phase: 'commit',
          summary: { ...allZeroSummary(commitSummaryKeys), files: 1 },
          files: [{ ...baseFileShape(), library_key: unicodeLibraryKey }],
          missing_source_entries: [],
        }
        const normalized = normalizeSelectionView(wrapReport(report))
        expect(normalized).not.toBeNull()
        expect(normalized.commit_result.files[0].library_key).toBe(unicodeLibraryKey)
      })
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

describe('buildTranslationMapFromRows', () => {
  const row = (overrides = {}) => ({
    revision: { source_id: 'room-01', content_digest: 'a'.repeat(64) },
    field: 'label',
    source_value: 'SALLE_A',
    source_shape: 'scalar',
    list_index: null,
    translation: 'Room Alpha',
    emit_by_default: true,
    ...overrides,
  })

  it('builds canonical scalar and separate ordered list entries without mutating rows', () => {
    const rows = [
      row({ field: 'name' }),
      row({ field: 'tags', source_value: 'terraza', source_shape: 'list', list_index: 0, translation: 'terrace' }),
      row({ field: 'tags', source_value: 'soleado', source_shape: 'list', list_index: 1, translation: 'sunny' }),
    ]
    const before = structuredClone(rows)

    expect(buildTranslationMapFromRows(rows)).toEqual({
      translationMap: {
        SALLE_A: { source: 'SALLE_A', translation: 'Room Alpha', fields: ['label'] },
        terraza: { source: 'terraza', translation: 'terrace', fields: ['tags'] },
        soleado: { source: 'soleado', translation: 'sunny', fields: ['tags'] },
      },
      errors: [],
    })
    expect(rows).toEqual(before)
  })

  it('emits required English identity and preserves existing translations', () => {
    const rows = [
      row({ source_value: 'wooden table', translation: 'wooden table', identity_required: true }),
      row({ source_value: 'SALLE_B', translation: 'Existing room', current_translation: 'Existing room' }),
    ]
    expect(buildTranslationMapFromRows(rows).translationMap).toEqual({
      'wooden table': { source: 'wooden table', translation: 'wooden table', fields: ['label'] },
      SALLE_B: { source: 'SALLE_B', translation: 'Existing room', fields: ['label'] },
    })
  })

  it('merges compatible duplicates deterministically across shapes and revisions', () => {
    const rows = [
      row({ field: 'theme', source_value: 'shared', translation: 'Shared text' }),
      row({ field: 'label', source_value: 'shared', translation: ' Shared text ', source_shape: 'list', list_index: 2 }),
    ]
    expect(buildTranslationMapFromRows(rows)).toEqual({
      translationMap: {
        shared: { source: 'shared', translation: 'Shared text', fields: ['label', 'scene_theme'] },
      },
      errors: [],
    })
  })

  it.each([
    ['duplicate_source_conflict', [row(), row({ translation: 'Another room' })]],
    ['source_shape_conflict', [row({ translation: ['Room Alpha'] })]],
    ['source_shape_conflict', [row({ source_shape: 'list', list_index: null })]],
  ])('returns %s and emits no conflicting entry', (code, rows) => {
    const result = buildTranslationMapFromRows(rows)
    expect(result.translationMap).toEqual({})
    expect(result.errors[0].code).toBe(code)
  })

  it('omits unselected blank rows and diagnoses selected blanks', () => {
    expect(buildTranslationMapFromRows([row({ translation: '', emit_by_default: false })])).toEqual({
      translationMap: {}, errors: [],
    })
    expect(buildTranslationMapFromRows([row({ translation: '', emit: true })]).errors[0].code).toBe('blank_translation')
  })
})

describe('Task 3.3 translation proposal helpers', () => {
  const sampleRow = (overrides = {}) => ({
    revision: { source_id: 'room-01', content_digest: 'a'.repeat(64) },
    field: 'label',
    source_field: 'name',
    source_value: 'salle_principale',
    source_shape: 'scalar',
    list_index: null,
    current_translation: null,
    translation: '',
    required: true,
    role: 'descriptive_input',
    identity_required: false,
    emit_by_default: false,
    suggest_selected: false,
    ...overrides,
  })

  it('generates deterministic row identity keys for scalars and lists', () => {
    const scalar = sampleRow()
    expect(getRowIdentityKey(scalar)).toBe(`room-01|${'a'.repeat(64)}|label|scalar|null`)

    const listRow = sampleRow({ field: 'tags', source_shape: 'list', list_index: 3 })
    expect(getRowIdentityKey(listRow)).toBe(`room-01|${'a'.repeat(64)}|tags|list|3`)

    // Works with proposal response item shape
    const proposal = {
      revision: { source_id: 'room-01', content_digest: 'a'.repeat(64) },
      field: 'tags',
      source_shape: 'list',
      list_index: 3,
      translation: 'Balcony',
    }
    expect(getRowIdentityKey(proposal)).toBe(`room-01|${'a'.repeat(64)}|tags|list|3`)
  })

  it('builds identity-only proposal request entries and excludes authority fields', () => {
    const row = sampleRow({
      source_value: 'sensitive_source',
      translation: 'client_translation',
      required: true,
      role: 'descriptive_input',
      extra_arbitrary: 'drop_me',
    })
    const entry = buildProposalRequestEntry(row)
    expect(entry).toEqual({
      source_id: 'room-01',
      content_digest: 'a'.repeat(64),
      field: 'label',
      source_shape: 'scalar',
      list_index: null,
    })
    expect(entry).not.toHaveProperty('source_value')
    expect(entry).not.toHaveProperty('translation')
    expect(entry).not.toHaveProperty('extra_arbitrary')
  })

  it('accurately filters row proposal eligibility', () => {
    // Eligible: pending, descriptive, no translation, valid source
    expect(isRowEligibleForProposal(sampleRow())).toBe(true)

    // Ineligible: already translated
    expect(isRowEligibleForProposal(sampleRow({ current_translation: 'Main Room' }))).toBe(false)

    // Ineligible: identity_required (already English)
    expect(isRowEligibleForProposal(sampleRow({ identity_required: true }))).toBe(false)

    // Ineligible: non-descriptive role
    expect(isRowEligibleForProposal(sampleRow({ role: 'identifier' }))).toBe(false)

    // Ineligible: blank source
    expect(isRowEligibleForProposal(sampleRow({ source_value: '   ' }))).toBe(false)

    // Ineligible: invalid list shape
    expect(isRowEligibleForProposal(sampleRow({ source_shape: 'list', list_index: null }))).toBe(false)
    expect(isRowEligibleForProposal(sampleRow({ source_shape: 'list', list_index: -1 }))).toBe(false)
  })

  it('applies proposals matching exact row identities and preserves list order', () => {
    const row1 = sampleRow({ field: 'tags', source_shape: 'list', list_index: 0, source_value: 'shared' })
    const row2 = sampleRow({ field: 'tags', source_shape: 'list', list_index: 1, source_value: 'shared' })
    const row3 = sampleRow({ field: 'label', source_shape: 'scalar', list_index: null, source_value: 'shared' })
    const rows = [row1, row2, row3]

    const proposals = [
      {
        revision: { source_id: 'room-01', content_digest: 'a'.repeat(64) },
        field: 'tags',
        source_shape: 'list',
        list_index: 0,
        translation: 'tag zero',
      },
      {
        revision: { source_id: 'room-01', content_digest: 'a'.repeat(64) },
        field: 'tags',
        source_shape: 'list',
        list_index: 1,
        translation: 'tag one',
      },
      {
        revision: { source_id: 'room-01', content_digest: 'a'.repeat(64) },
        field: 'label',
        source_shape: 'scalar',
        list_index: null,
        translation: 'label scalar',
      },
    ]

    const { updatedRows, appliedCount } = applyProposalsToRows(rows, proposals)
    expect(appliedCount).toBe(3)
    expect(updatedRows[0].translation).toBe('tag zero')
    expect(updatedRows[0].emit).toBe(true)
    expect(updatedRows[1].translation).toBe('tag one')
    expect(updatedRows[1].emit).toBe(true)
    expect(updatedRows[2].translation).toBe('label scalar')
    expect(updatedRows[2].emit).toBe(true)

    // Input rows were not mutated
    expect(row1.translation).toBe('')
    expect(row2.translation).toBe('')
    expect(row3.translation).toBe('')
  })
})
