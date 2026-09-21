/** Pure utility functions for the Resource Browser and Import Preview (Task 5.1).
 *
 *  Kept strictly pure and dependency-free so the logic can be tested
 *  independently of React and the DOM.
 */

export const ROLE_LABELS = {
  descriptive_input: 'descriptive input',
  selection_metadata: 'selection metadata',
  writer_guidance: 'writer guidance',
  auxiliary_data: 'auxiliary data',
  intentionally_unused: 'unused data',
  identity: 'identity',
  unmapped: 'unmapped',
}

/** Map backend field role identifier to standard user-facing name. */
export function normalizeFieldRole(role) {
  if (!role) return 'unused data'
  return ROLE_LABELS[role] || String(role).replace(/_/g, ' ')
}

/** Extract distinct categories / kinds from a list of libraries. */
export function extractCategories(libraries = []) {
  const kinds = new Set()
  for (const lib of libraries || []) {
    if (lib?.kind) kinds.add(lib.kind)
  }
  return Array.from(kinds).sort()
}

/** Filter resource libraries and their revisions by text search and category. */
export function filterLibraries(libraries = [], { query = '', category = '' } = {}) {
  const q = (query || '').trim().toLowerCase()
  const cat = (category || '').trim().toLowerCase()

  return (libraries || [])
    .filter((lib) => {
      if (!cat || cat === 'all') return true
      return (lib.kind || '').toLowerCase() === cat
    })
    .map((lib) => {
      if (!q) return lib

      const libKeyMatch = (lib.library_key || '').toLowerCase().includes(q)
      const libNameMatch = (lib.display_name || '').toLowerCase().includes(q)
      const libKindMatch = (lib.kind || '').toLowerCase().includes(q)

      if (libKeyMatch || libNameMatch || libKindMatch) {
        return lib
      }

      // Filter revisions and auxiliary within this library
      const matchedRevisions = (lib.revisions || []).filter((rev) => {
        if ((rev.source_id || '').toLowerCase().includes(q)) return true
        if ((rev.content_digest || '').toLowerCase().includes(q)) return true
        // Check translated values only (no raw source payload)
        for (const val of Object.values(rev.translation || {})) {
          if (typeof val === 'string' && val.toLowerCase().includes(q)) return true
        }
        return false
      })

      const matchedAuxiliary = (lib.auxiliary || []).filter((aux) => {
        if ((aux.kind || '').toLowerCase().includes(q)) return true
        if ((aux.content_digest || '').toLowerCase().includes(q)) return true
        return false
      })

      if (matchedRevisions.length > 0 || matchedAuxiliary.length > 0) {
        return {
          ...lib,
          revisions: matchedRevisions,
          auxiliary: matchedAuxiliary,
        }
      }

      return null
    })
    .filter(Boolean)
}

/** Check whether a revision is ready for session draft initialization. */
export function checkReadiness(revision) {
  const status = revision?.readiness?.status || revision?.status || 'pending'
  const isReady = status === 'ready'
  const reasons = []

  if (!isReady) {
    const sidecarError = revision?.readiness?.coverage?.sidecar_error || revision?.coverage?.sidecar_error
    if (sidecarError) {
      reasons.push(String(sidecarError))
    }

    const pendingFields = revision?.readiness?.pending_fields || revision?.pending_fields || {}
    for (const [field, reason] of Object.entries(pendingFields)) {
      if (reason) {
        reasons.push(String(reason))
      } else {
        reasons.push(`Required field '${field}' lacks a valid English translation`)
      }
    }

    // Check individual field readiness if pendingFields was empty and no reasons yet
    if (reasons.length === 0) {
      for (const item of revision?.readiness?.field_readiness || []) {
        if (item.reason) reasons.push(String(item.reason))
      }
    }

    if (reasons.length === 0) {
      reasons.push('Revision is pending required translations or adaptations')
    }
  }

  return {
    isReady,
    status,
    reasons,
  }
}

/** Define the normal resource entry and the explicit legacy alternative. */
export function sessionCreationActions(modelId) {
  return {
    primary: {
      mode: 'resource-v1',
      path: `/resources/${encodeURIComponent(String(modelId))}`,
    },
    legacy: { mode: 'legacy' },
  }
}

/** Select a requested model only when it still exists, otherwise use a safe fallback. */
export function selectAvailableModelId(models, requestedModelId = '') {
  const available = models || []
  const requested = String(requestedModelId || '')
  const match = available.find((model) => String(model.id) === requested)
  return String(match?.id ?? available[0]?.id ?? '')
}

/** Extract the exact immutable (library_key, source_id, content_digest) triple. */
export function exactRevisionTriple(libraryKey, revision) {
  const libKey = String(libraryKey || '').trim()
  const sourceId = String(revision?.source_id || '').trim()
  const contentDigest = String(revision?.content_digest || '').trim()

  if (!libKey || !sourceId || !contentDigest) {
    throw new Error(
      `Incomplete revision identity: library_key='${libKey}', source_id='${sourceId}', content_digest='${contentDigest}'`
    )
  }

  return {
    library_key: libKey,
    source_id: sourceId,
    content_digest: contentDigest,
  }
}

/** Build creation payloads for starting a resource-v1 draft session. */
export function buildSessionDraftPayload({
  model,
  sessionName = '',
  revision,
  libraryKey,
  look = '',
  initialWardrobe = '',
}) {
  if (!model || typeof model.id !== 'number') {
    throw new Error('An explicit model selection is required to start a resource session')
  }

  const triple = exactRevisionTriple(libraryKey, revision)
  const name = (sessionName || '').trim() || `${model.name || 'Model'} - ${triple.source_id}`

  const session = {
    model_id: model.id,
    name,
    composition_mode: 'resource-v1',
    workflow_id: model.workflow_id || null,
    look: look || '',
    wardrobe: initialWardrobe || '',
  }

  const plan = {
    version: 'resource-v1',
    look: look || '',
    initial_wardrobe: initialWardrobe || '',
    takes: [{ take_id: 'take-001' }],
    selected_resources: [triple],
    wardrobe_changes: [],
  }

  return {
    session,
    plan,
    expected_revision: 0,
  }
}

/** Extract and structure all inventory outcomes from an import report. */
export function parsePreviewSummary(rawReport) {
  const report = rawReport?.report || rawReport || {}
  const summary = report.summary || {}

  const counts = {
    files: summary.files ?? 0,
    inputs: summary.inputs ?? 0,
    accepted: summary.accepted ?? 0,
    auxiliary: summary.auxiliary ?? 0,
    duplicates: summary.duplicates ?? 0,
    unresolved: summary.unresolved ?? 0,
    new: summary.new ?? 0,
    unchanged: summary.unchanged ?? 0,
    updated: summary.updated ?? 0,
    missing: summary.missing ?? 0,
  }

  const accepted = []
  const auxiliary = []
  const unresolved = []
  const duplicates = []

  for (const f of report.files || []) {
    for (const acc of f.accepted || []) {
      accepted.push({
        ...acc,
        library_key: acc.library_key || f.library_key || '',
      })
    }
    for (const aux of f.auxiliary || []) {
      auxiliary.push({
        ...aux,
        library_key: aux.library_key || f.library_key || '',
      })
    }
    for (const un of f.unresolved || []) {
      unresolved.push({
        ...un,
        library_key: f.library_key || '',
      })
    }
    for (const dup of f.duplicates || []) {
      duplicates.push({
        ...dup,
        library_key: f.library_key || '',
      })
    }
  }

  const missing = (report.missing_source_entries || []).map((m) => ({
    library_key: m.library_key || '',
    source_id: m.source_id || '',
    latest_content_digest: m.latest_content_digest || '',
  }))

  return {
    phase: report.phase || 'preview',
    counts,
    accepted,
    auxiliary,
    unresolved,
    duplicates,
    missing,
  }
}

/** Parse and normalize translation preview metrics. */
export function parseTranslationPreview(preview) {
  if (!preview) return null
  return {
    libraryKey: preview.library_key || '',
    totalRevisions: preview.total_revisions ?? 0,
    matchedRevisions: preview.matched_revisions ?? 0,
    unmatchedEntries: preview.unmatched_map_entries ?? 0,
    wouldUpdate: preview.would_update ?? 0,
    unchanged: preview.unchanged ?? 0,
    wouldBeReady: preview.would_be_ready ?? 0,
    wouldRemainPending: preview.would_remain_pending ?? 0,
    attestationToken: preview.attestation_token || '',
    expiresAt: preview.expires_at || 0,
  }
}

const TRANSLATION_FIELD_ALIASES = {
  name: 'label',
  title: 'label',
  display_name: 'label',
  theme: 'scene_theme',
  theme_text: 'scene_theme',
  tag: 'tags',
}

/** Build the canonical direct translation_map used by bulk preview/apply. */
export function buildTranslationMapFromRows(rows = []) {
  const translationMap = {}
  const errors = []
  const occurrences = new Map()

  for (const [index, row] of (rows || []).entries()) {
    const source = row?.source_value
    const translation = row?.translation
    const shouldEmit = row?.emit ?? row?.emit_by_default ?? (
      typeof translation === 'string' && Boolean(translation.trim())
    )
    if (!shouldEmit) continue

    const reference = {
      index,
      revision: row?.revision || null,
      field: row?.field || '',
      source_shape: row?.source_shape || '',
      list_index: row?.list_index ?? null,
    }
    if (typeof source !== 'string' || !source.trim()) {
      errors.push({ code: 'source_shape_conflict', source, rows: [reference] })
      continue
    }
    const validShape = row?.source_shape === 'scalar'
      ? row?.list_index == null
      : row?.source_shape === 'list' && Number.isInteger(row?.list_index) && row.list_index >= 0
    if (!validShape || typeof translation !== 'string') {
      errors.push({ code: 'source_shape_conflict', source, rows: [reference] })
      continue
    }
    const normalizedTranslation = translation.trim()
    if (!normalizedTranslation) {
      errors.push({ code: 'blank_translation', source, rows: [reference] })
      continue
    }
    const field = TRANSLATION_FIELD_ALIASES[row?.field] || row?.field
    if (typeof field !== 'string' || !field.trim()) {
      errors.push({ code: 'source_shape_conflict', source, rows: [reference] })
      continue
    }

    if (!occurrences.has(source)) {
      occurrences.set(source, { translation: normalizedTranslation, fields: new Set(), rows: [] })
    }
    const group = occurrences.get(source)
    group.rows.push(reference)
    group.fields.add(field.trim())
    if (group.translation !== normalizedTranslation) {
      group.conflict = true
    }
  }

  for (const [source, group] of occurrences) {
    if (group.conflict) {
      errors.push({ code: 'duplicate_source_conflict', source, rows: group.rows })
      continue
    }
    translationMap[source] = {
      source,
      translation: group.translation,
      fields: Array.from(group.fields).sort(),
    }
  }

  return { translationMap, errors }
}

/**
 * Normalizes and validates a raw SelectionView against the closed public specification.
 * Reconstructs a clean public object containing ONLY allowed fields.
 * Strips any private fields (e.g. staged_path, cleanup_state, raw_payload) and
 * fails closed (returns null) if mandatory fields are missing or of invalid type.
 */
export function normalizeSelectionView(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return null
  }

  const {
    selection_id,
    selection_revision,
    state,
    expires_at,
    files,
    preview,
    commit_result,
    cleanup_warning,
  } = raw

  // Validate selection_id
  if (typeof selection_id !== 'string' || !selection_id.trim()) {
    return null
  }

  // Validate selection_revision
  if (
    typeof selection_revision !== 'number' ||
    !Number.isInteger(selection_revision) ||
    selection_revision < 0 ||
    selection_revision > Number.MAX_SAFE_INTEGER
  ) {
    return null
  }

  // Validate state
  const VALID_STATES = ['open', 'committing', 'committed', 'cancelled', 'expired']
  if (typeof state !== 'string' || !VALID_STATES.includes(state)) {
    return null
  }

  // Validate expires_at
  if (typeof expires_at !== 'string' || !expires_at.trim()) {
    return null
  }

  // Validate files array
  if (!Array.isArray(files)) {
    return null
  }

  const normalizedFiles = []
  for (const f of files) {
    if (!f || typeof f !== 'object' || Array.isArray(f)) {
      return null
    }
    if (typeof f.file_id !== 'string' || !f.file_id.trim()) {
      return null
    }
    if (
      typeof f.byte_count !== 'number' ||
      !Number.isInteger(f.byte_count) ||
      f.byte_count < 0 ||
      f.byte_count > Number.MAX_SAFE_INTEGER
    ) {
      return null
    }

    normalizedFiles.push({
      file_id: String(f.file_id),
      file_name: typeof f.file_name === 'string' ? f.file_name : '',
      byte_count: f.byte_count,
      declared_library: typeof f.declared_library === 'string' ? f.declared_library : null,
      effective_library_key: typeof f.effective_library_key === 'string' ? f.effective_library_key : null,
      matched_auxiliary_kinds: Array.isArray(f.matched_auxiliary_kinds)
        ? f.matched_auxiliary_kinds.filter((k) => typeof k === 'string').map(String)
        : [],
      effective_auxiliary_kind: typeof f.effective_auxiliary_kind === 'string' ? f.effective_auxiliary_kind : null,
      status: typeof f.status === 'string' ? f.status : 'staged',
    })
  }

  // Normalize preview
  let normalizedPreview = null
  if (preview !== null && preview !== undefined) {
    if (typeof preview !== 'object' || Array.isArray(preview)) {
      return null
    }

    const { preview_token, manifest_digest, committable, report } = preview
    const normToken = typeof preview_token === 'string' && preview_token.trim() ? preview_token.trim() : null
    const normDigest = typeof manifest_digest === 'string' && /^[0-9a-f]{16,64}$/.test(manifest_digest.trim())
      ? manifest_digest.trim()
      : null
    const normCommittable = Boolean(committable)

    const normReport = normalizeSafeReport(report, 'preview')
    if (!normReport) return null

    normalizedPreview = {
      preview_token: normToken,
      manifest_digest: normDigest,
      committable: normCommittable,
      report: normReport,
    }
  }

  // Normalize commit_result
  let normalizedCommitResult = null
  if (commit_result !== null && commit_result !== undefined) {
    normalizedCommitResult = normalizeSafeReport(commit_result, 'commit')
    if (!normalizedCommitResult) {
      return null
    }
  }

  // Normalize cleanup_warning
  const normalizedCleanupWarning = typeof cleanup_warning === 'string' && cleanup_warning.trim()
    ? cleanup_warning.trim()
    : null

  // Reconstruct clean object strictly without spread or Object.assign
  return {
    selection_id: String(selection_id),
    selection_revision,
    state: String(state),
    expires_at: String(expires_at),
    files: normalizedFiles,
    preview: normalizedPreview,
    commit_result: normalizedCommitResult,
    cleanup_warning: normalizedCleanupWarning,
  }
}

// Public transport contract keys for SafeCommitReport.
// These are the EXACT keys a canonical backend response MUST carry.
const SAFE_REPORT_TOP_KEYS = ['files', 'missing_source_entries', 'phase', 'summary', 'version']
const PREVIEW_SUMMARY_KEYS = [
  'accepted', 'auxiliary', 'duplicates', 'files', 'inputs',
  'missing', 'new', 'unchanged', 'unresolved', 'updated',
]
const COMMIT_SUMMARY_KEYS = [
  'accepted', 'auxiliary', 'duplicates', 'files', 'inputs',
  'missing', 'new', 'new_auxiliary_revisions', 'new_scene_revisions',
  'recorded', 'unchanged', 'unchanged_auxiliary_revisions',
  'unchanged_scene_revisions', 'unresolved', 'updated', 'updated_scene_revisions',
]
const FILE_REPORT_KEYS = ['accepted', 'auxiliary', 'duplicates', 'library_key', 'total_inputs', 'unresolved']
const ACCEPTED_ITEM_KEYS = ['classification', 'kind', 'library_key', 'new_content_digest', 'previous_content_digest', 'source_id']
const AUXILIARY_ITEM_KEYS = ['classification', 'kind', 'library_key', 'new_content_digest', 'previous_content_digest']
const DUPLICATE_ITEM_KEYS = ['occurrences', 'source_id']
const UNRESOLVED_ITEM_KEYS = ['bucket', 'expected_kind', 'identifier_fields', 'index', 'reason', 'received_type']
const MISSING_SOURCE_KEYS = ['latest_content_digest', 'library_key', 'source_id']

function isSafeInteger(v, minVal = 0) {
  return typeof v === 'number' && Number.isInteger(v) && v >= minVal && v <= Number.MAX_SAFE_INTEGER
}

function hasExactKeys(obj, expectedSortedKeys) {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return false
  const keys = Object.keys(obj).sort()
  if (keys.length !== expectedSortedKeys.length) return false
  for (let i = 0; i < keys.length; i++) {
    if (keys[i] !== expectedSortedKeys[i]) return false
  }
  return true
}

function copyStringArray(raw) {
  if (!Array.isArray(raw)) return null
  const result = []
  for (const item of raw) {
    if (typeof item !== 'string') return null
    result.push(item)
  }
  return result
}

/**
 * Validate and canonicalize a SafeCommitReport against the public transport contract.
 * Fails closed (returns null) on any structural violation.
 *
 * Scope of validation: ONLY the public transport contract.
 *   - top-level shape (exact keys, version=1, phase discriminator)
 *   - summary structural keys per phase and integer counters
 *   - file report structure (exact keys, primitive types, total_inputs integer)
 *   - per-file item structures (accepted/auxiliary/duplicates/unresolved/missing_source_entries)
 *
 * Out of scope (backend business semantics — NOT validated here):
 *   - reconciliation between summary counters and files/missing entries
 *   - commit-specific counters (recorded, new_scene_revisions, ...) consistency
 *   - kind / classification / bucket / expected_kind / auxiliary-kind enum closure
 *   - classification <-> previous_content_digest relationship invariants
 *   - file_read_error exact reason match
 *   - digest spelling, library/source naming, and diagnostic string content
 *   - identifier_fields vocabulary and ordering
 *   - new/updated/unchanged business rules
 */
export function normalizeSafeReport(raw, expectedPhase = null) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null

  if (!hasExactKeys(raw, SAFE_REPORT_TOP_KEYS)) return null
  if (raw.version !== 1) return null

  const phase = raw.phase
  if (phase !== 'preview' && phase !== 'commit') return null
  if (expectedPhase !== null && phase !== expectedPhase) return null

  const rawSummary = raw.summary
  if (!rawSummary || typeof rawSummary !== 'object' || Array.isArray(rawSummary)) return null
  const expectedSummaryKeys = phase === 'preview' ? PREVIEW_SUMMARY_KEYS : COMMIT_SUMMARY_KEYS
  if (!hasExactKeys(rawSummary, expectedSummaryKeys)) return null

  const summary = {}
  for (const k of expectedSummaryKeys) {
    if (!isSafeInteger(rawSummary[k], 0)) return null
    summary[k] = rawSummary[k]
  }

  if (!Array.isArray(raw.files)) return null
  const files = []
  for (const f of raw.files) {
    if (!hasExactKeys(f, FILE_REPORT_KEYS)) return null
    if (typeof f.library_key !== 'string') return null
    if (!isSafeInteger(f.total_inputs, 0)) return null

    if (!Array.isArray(f.accepted)) return null
    const accepted = []
    for (const item of f.accepted) {
      if (!hasExactKeys(item, ACCEPTED_ITEM_KEYS)) return null
      if (typeof item.source_id !== 'string') return null
      if (typeof item.library_key !== 'string') return null
      if (typeof item.kind !== 'string') return null
      if (typeof item.classification !== 'string') return null
      if (typeof item.new_content_digest !== 'string') return null
      const prev = item.previous_content_digest
      if (prev !== null && typeof prev !== 'string') return null
      accepted.push({
        source_id: item.source_id,
        library_key: item.library_key,
        kind: item.kind,
        classification: item.classification,
        new_content_digest: item.new_content_digest,
        previous_content_digest: prev,
      })
    }

    if (!Array.isArray(f.auxiliary)) return null
    const auxiliary = []
    for (const item of f.auxiliary) {
      if (!hasExactKeys(item, AUXILIARY_ITEM_KEYS)) return null
      if (typeof item.library_key !== 'string') return null
      if (typeof item.kind !== 'string') return null
      if (typeof item.classification !== 'string') return null
      if (typeof item.new_content_digest !== 'string') return null
      const prev = item.previous_content_digest
      if (prev !== null && typeof prev !== 'string') return null
      auxiliary.push({
        library_key: item.library_key,
        kind: item.kind,
        classification: item.classification,
        new_content_digest: item.new_content_digest,
        previous_content_digest: prev,
      })
    }

    if (!Array.isArray(f.duplicates)) return null
    const duplicates = []
    for (const item of f.duplicates) {
      if (!hasExactKeys(item, DUPLICATE_ITEM_KEYS)) return null
      if (typeof item.source_id !== 'string') return null
      if (!isSafeInteger(item.occurrences, 0)) return null
      duplicates.push({
        source_id: item.source_id,
        occurrences: item.occurrences,
      })
    }

    if (!Array.isArray(f.unresolved)) return null
    const unresolved = []
    for (const item of f.unresolved) {
      if (!hasExactKeys(item, UNRESOLVED_ITEM_KEYS)) return null
      if (typeof item.bucket !== 'string') return null
      if (!isSafeInteger(item.index, 0)) return null
      if (typeof item.reason !== 'string') return null
      if (typeof item.received_type !== 'string') return null
      if (typeof item.expected_kind !== 'string') return null
      const idFields = copyStringArray(item.identifier_fields)
      if (idFields === null) return null
      unresolved.push({
        bucket: item.bucket,
        index: item.index,
        reason: item.reason,
        received_type: item.received_type,
        expected_kind: item.expected_kind,
        identifier_fields: idFields,
      })
    }

    files.push({
      library_key: f.library_key,
      total_inputs: f.total_inputs,
      accepted,
      auxiliary,
      duplicates,
      unresolved,
    })
  }

  if (!Array.isArray(raw.missing_source_entries)) return null
  const missingSourceEntries = []
  for (const m of raw.missing_source_entries) {
    if (!hasExactKeys(m, MISSING_SOURCE_KEYS)) return null
    if (typeof m.library_key !== 'string') return null
    if (typeof m.source_id !== 'string') return null
    if (typeof m.latest_content_digest !== 'string') return null
    missingSourceEntries.push({
      library_key: m.library_key,
      source_id: m.source_id,
      latest_content_digest: m.latest_content_digest,
    })
  }

  return {
    version: 1,
    phase,
    summary,
    files,
    missing_source_entries: missingSourceEntries,
  }
}

/** Pure reducer for SelectionView response acceptance with highest-revision-wins and epoch rules. */
export function reduceSelectionView(
  currentView,
  candidateView,
  {
    activeSelectionId = null,
    currentEpoch = 0,
    candidateEpoch = 0,
    currentGen = 0,
    candidateGen = 0,
  } = {}
) {
  // 1. Normalize and validate candidate through public boundary
  const normalized = normalizeSelectionView(candidateView)
  if (!normalized) {
    return { view: currentView, accepted: false, epoch: currentEpoch, gen: currentGen }
  }

  // 2. Epoch rule: an old epoch can NEVER affect or overwrite current epoch
  if (candidateEpoch < currentEpoch) {
    return { view: currentView, accepted: false, epoch: currentEpoch, gen: currentGen }
  }

  // 3. Newer epoch: candidate from newer epoch supersedes previous selection
  if (candidateEpoch > currentEpoch) {
    return { view: normalized, accepted: true, epoch: candidateEpoch, gen: candidateGen }
  }

  // Within the same epoch:
  const { selection_id, selection_revision } = normalized

  // Reject responses for a different selection than the active one for this epoch
  if (activeSelectionId && selection_id !== activeSelectionId) {
    return { view: currentView, accepted: false, epoch: currentEpoch, gen: currentGen }
  }

  // First view accepted for this epoch
  if (!currentView) {
    return { view: normalized, accepted: true, epoch: candidateEpoch, gen: candidateGen }
  }

  // Reject if currentView is for a different selection
  if (currentView.selection_id && selection_id !== currentView.selection_id) {
    return { view: currentView, accepted: false, epoch: currentEpoch, gen: currentGen }
  }

  // Within the active selection: highest selection_revision wins!
  // A response with higher revision is never discarded just because its request started earlier.
  if (selection_revision > currentView.selection_revision) {
    return { view: normalized, accepted: true, epoch: candidateEpoch, gen: Math.max(currentGen, candidateGen) }
  }

  // Lower revision is strictly ignored
  if (selection_revision < currentView.selection_revision) {
    return { view: currentView, accepted: false, epoch: currentEpoch, gen: currentGen }
  }

  // Equal revision: request generation fencing
  if (candidateGen >= currentGen) {
    return { view: normalized, accepted: true, epoch: candidateEpoch, gen: candidateGen }
  }

  return { view: currentView, accepted: false, epoch: currentEpoch, gen: currentGen }
}

/** Pure evaluator of whether Import action is eligible on the current selection. */
export function isImportEligible(
  selectionView,
  { activeSelectionId = null, pendingMutation = false } = {}
) {
  if (!selectionView || typeof selectionView !== 'object') return false
  if (activeSelectionId && selectionView.selection_id !== activeSelectionId) return false
  if (typeof selectionView.selection_id !== 'string' || !selectionView.selection_id.trim()) return false
  if (
    typeof selectionView.selection_revision !== 'number' ||
    !Number.isInteger(selectionView.selection_revision) ||
    selectionView.selection_revision < 0 ||
    selectionView.selection_revision > Number.MAX_SAFE_INTEGER
  ) {
    return false
  }
  if (selectionView.state !== 'open') return false
  if (pendingMutation) return false

  const preview = selectionView.preview
  if (!preview || typeof preview !== 'object') return false
  if (preview.committable !== true) return false
  if (typeof preview.preview_token !== 'string' || !preview.preview_token.trim()) return false
  if (typeof preview.manifest_digest !== 'string' || !/^[0-9a-f]{16,64}$/.test(preview.manifest_digest.trim())) {
    return false
  }
  if (!preview.report || typeof preview.report !== 'object' || Array.isArray(preview.report)) {
    return false
  }

  return true
}
