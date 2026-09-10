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
        // Check translated values or payload values
        for (const val of Object.values(rev.translation || {})) {
          if (typeof val === 'string' && val.toLowerCase().includes(q)) return true
        }
        for (const val of Object.values(rev.payload || {})) {
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
  const status = revision?.readiness?.status || 'pending'
  const isReady = status === 'ready'
  const reasons = []

  if (!isReady) {
    const pendingFields = revision?.readiness?.pending_fields || {}
    for (const [field, reason] of Object.entries(pendingFields)) {
      if (reason) {
        reasons.push(String(reason))
      } else {
        reasons.push(`Required field '${field}' lacks a valid English translation`)
      }
    }

    // Check individual field readiness if pendingFields was empty
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
        file_path: f.file_path || '',
        library_key: acc.library_key || f.library_key || '',
      })
    }
    for (const aux of f.auxiliary || []) {
      auxiliary.push({
        ...aux,
        file_path: f.file_path || '',
        library_key: aux.library_key || f.library_key || '',
      })
    }
    for (const un of f.unresolved || []) {
      unresolved.push({
        ...un,
        file_path: f.file_path || '',
        library_key: f.library_key || '',
      })
    }
    for (const dup of f.duplicates || []) {
      duplicates.push({
        ...dup,
        file_path: f.file_path || '',
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
