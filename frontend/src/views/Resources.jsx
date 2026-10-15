import React, { useEffect, useLayoutEffect, useState, useRef } from 'react'
import { api, versionText } from '../api'
import { go } from '../App.jsx'
import CanvasSize from './CanvasSize.jsx'
import AutomationProfileSelector from '../components/AutomationProfileSelector.jsx'
import { deriveSavedLookWardrobeProgression } from '../wardrobe.js'
import {
  normalizeFieldRole,
  extractCategories,
  filterLibraries,
  checkReadiness,
  extractImportedIdentities,
  buildSessionDraftPayload,
  parsePreviewSummary,
  selectAvailableModelId,
  parseTranslationPreview,
  buildTranslationMapFromRows,
  exactRevisionTriple,
  normalizeSelectionView,
  reduceSelectionView,
  isImportEligible,
  getRowIdentityKey,
  buildProposalRequestEntry,
  isRowEligibleForProposal,
  applyProposalsToRows,
} from '../resources.js'

const GUIDED_DIMENSIONS = [
  ['camera', 'Camera'],
  ['framing', 'Framing'],
  ['pose', 'Pose'],
  ['expression', 'Expression'],
]
const SHA256 = /^[0-9a-f]{64}$/

const defaultGuidedPolicy = () => Object.fromEntries(
  GUIDED_DIMENSIONS.map(([field]) => [field, { mode: 'vary', value: '' }]),
)

const fullAutoWardrobeAtStage = (look, stageIndex) => {
  if (!Number.isInteger(stageIndex) || stageIndex < 0) return ''
  if (look?.outfit === null) {
    if (stageIndex !== 0) return ''
    const unchanged = deriveSavedLookWardrobeProgression(null, '', 1)[0]
    return unchanged ? `Appearance-only look; ${unchanged}` : 'Appearance-only saved look; wardrobe remains unchanged.'
  }
  if (!look?.outfit) return ''
  try {
    return deriveSavedLookWardrobeProgression(
      look.outfit, '', stageIndex + 1, { stageIndices: [stageIndex] },
    )[stageIndex] || ''
  } catch {
    return ''
  }
}

const fullAutoWardrobeStageCount = (look) => {
  if (look?.outfit === null) return 1
  const garments = look?.outfit?.garments
  if (!Array.isArray(garments) || garments.length === 0
    || typeof garments[garments.length - 1]?.aside !== 'string') return 0
  return garments.length + 1 + Number(Boolean(garments[garments.length - 1].aside))
}

const freezeGuidedRequest = (request) => Object.freeze({
  ...request,
  scene_anchor: Object.freeze({ ...request.scene_anchor }),
  variation_policy: Object.freeze(Object.fromEntries(
    Object.entries(request.variation_policy).map(([field, setting]) => [field, Object.freeze({ ...setting })]),
  )),
  ...(request.saved_look ? { saved_look: Object.freeze({ ...request.saved_look }) } : {}),
  ...(request.full_auto ? { full_auto: Object.freeze({ ...request.full_auto }) } : {}),
})

const isGuidedSuccessResponse = ({ status, data }, request) => {
  const plan = data?.plan
  const snapshot = plan?.authoring?.look_snapshot
  const matchesAnchor = (anchor) => anchor?.library_key === request.scene_anchor.library_key
    && anchor?.source_id === request.scene_anchor.source_id
    && anchor?.content_digest === request.scene_anchor.content_digest

  return (status === 200 || status === 201)
    && Number.isSafeInteger(data?.session_id) && data.session_id > 0
    && data?.plan_revision === 1
    && plan?.version === 'resource-v1'
    && Array.isArray(plan.takes) && plan.takes.length === request.photo_count
    && plan.takes.every((take) => typeof take?.take_id === 'string' && take.take_id.length > 0)
    && Array.isArray(plan.selected_resources) && plan.selected_resources.some(matchesAnchor)
    && plan.authoring?.schema_version === 1
    && plan.authoring.mode === request.mode
    && matchesAnchor(plan.authoring.scene_anchor)
    && (!request.saved_look || (
      snapshot?.look_id === request.saved_look.look_key
      && versionText(snapshot?.version) === versionText(request.saved_look.version)
      && snapshot?.content_digest === request.saved_look.content_digest
    ))
    && (!request.full_auto || (typeof data.full_auto_run_id === 'string' && data.full_auto_run_id.length > 0))
}

const isFullAutoConsent = (result, request, look) => {
  const summary = result?.summary
  const stages = summary?.progression?.stages
  const limits = summary?.limits
  const saved = summary?.saved_look
  const profile = summary?.automation_profile
  const room = summary?.room
  const workflow = summary?.workflow
  const provider = summary?.provider
  const canvas = summary?.canvas
  const issued = Date.parse(result?.scope_issued_at || '')
  const expires = Date.parse(summary?.authorization_expires_at || '')
  const stageCount = fullAutoWardrobeStageCount(look)
  const expectedStageCounts = Array.from({ length: stageCount }, () => 0)
  if (stageCount > 0 && Number.isSafeInteger(summary?.photo_count)
    && summary.photo_count >= stageCount) {
    for (let takeIndex = 0; takeIndex < summary.photo_count; takeIndex += 1) {
      const stageIndex = stageCount === 1
        ? 0
        : Math.floor(takeIndex * (stageCount - 1) / (summary.photo_count - 1))
      expectedStageCounts[stageIndex] += 1
    }
  }
  let nextTake = 1
  return SHA256.test(result?.scope_digest || '')
    && Number.isFinite(issued) && Number.isFinite(expires) && expires > issued
    && result.scope_issued_at === summary.scope_issued_at
    && saved?.look_key === request.saved_look?.look_key
    && versionText(saved?.version) === versionText(request.saved_look?.version)
    && saved?.content_digest === request.saved_look?.content_digest
    && versionText(profile?.revision) === versionText(request.full_auto?.profile_revision)
    && profile?.digest === request.full_auto?.profile_digest && profile?.ready === true
    && room?.library_key === request.scene_anchor?.library_key
    && room?.source_id === request.scene_anchor?.source_id
    && room?.content_digest === request.scene_anchor?.content_digest
    && summary.character_id === request.character_id
    && Number.isSafeInteger(summary.photo_count) && summary.photo_count === request.photo_count
    && Number.isSafeInteger(workflow?.workflow_id) && typeof workflow.kind === 'string'
    && SHA256.test(workflow.graph_digest || '') && SHA256.test(workflow.node_map_digest || '')
    && typeof provider?.model === 'string' && SHA256.test(provider.endpoint_identity || '')
    && Number.isInteger(canvas?.width) && canvas.width > 0
    && Number.isInteger(canvas?.height) && canvas.height > 0
    && summary.framing_intent === request.full_auto.framing_intent
    && summary.progression?.policy === 'all_stages_full_interval'
    && Array.isArray(stages) && stageCount > 0 && stages.length === stageCount
    && summary.photo_count >= stageCount
    && stages.every((stage, index) => {
      const expectedCount = expectedStageCounts[index]
      const firstTake = `take-${String(nextTake).padStart(3, '0')}`
      const lastTake = `take-${String(nextTake + expectedCount - 1).padStart(3, '0')}`
      nextTake += expectedCount
      return typeof stage.coverage === 'string'
      && stage.first_take_id === firstTake && stage.last_take_id === lastTake
      && Number.isInteger(stage.stage_index) && stage.stage_index === index
      && Number.isInteger(stage.take_count) && stage.take_count === expectedCount && expectedCount > 0
      && Boolean(fullAutoWardrobeAtStage(look, stage.stage_index))
    })
    && stages.reduce((total, stage) => total + stage.take_count, 0) === request.photo_count
    && limits?.assistant_calls_per_take === 2
    && limits.assistant_calls_total === request.photo_count * 2
    && limits.image_attempts_per_take === 1
    && limits.image_attempts_total === request.photo_count
    && typeof summary.policy_version === 'string'
    && typeof summary.coverage_compiler_version === 'string'
}

export default function Resources({ requestedModelId = '' }) {
  const [tab, setTab] = useState('inventory') // 'inventory' | 'import'
  const [libraries, setLibraries] = useState([])
  const [models, setModels] = useState([])
  const [selectedModelId, setSelectedModelId] = useState('')
  const [assistantConfigured, setAssistantConfigured] = useState(null)
  const [workflows, setWorkflows] = useState([])
  const [guidedSceneKey, setGuidedSceneKey] = useState('')
  const [guidedCharacterId, setGuidedCharacterId] = useState('')
  const [guidedPhotoCount, setGuidedPhotoCount] = useState('12')
  const [guidedBrief, setGuidedBrief] = useState('')
  const [guidedMode, setGuidedMode] = useState('')
  const [guidedWorkflowOverrideId, setGuidedWorkflowOverrideId] = useState('')
  const [guidedVariationPolicy, setGuidedVariationPolicy] = useState(defaultGuidedPolicy)
  const [guidedLook, setGuidedLook] = useState('')
  const [guidedInitialWardrobe, setGuidedInitialWardrobe] = useState('')
  const [guidedSavedLooks, setGuidedSavedLooks] = useState([])
  const [guidedSavedLooksLoading, setGuidedSavedLooksLoading] = useState(false)
  const [guidedSavedLooksError, setGuidedSavedLooksError] = useState('')
  const [guidedSavedLookKey, setGuidedSavedLookKey] = useState('')
  const [guidedSavedLookVersion, setGuidedSavedLookVersion] = useState('')
  const [guidedSavedLook, setGuidedSavedLook] = useState(null)
  const [guidedSavedLookLoading, setGuidedSavedLookLoading] = useState(false)
  const [guidedSavedLookError, setGuidedSavedLookError] = useState('')
  const [guidedSelectedProfile, setGuidedSelectedProfile] = useState(null)
  const [fullAutoEnabled, setFullAutoEnabled] = useState(false)
  const [guidedFramingIntent, setGuidedFramingIntent] = useState('vary')
  const [fullAutoQuote, setFullAutoQuote] = useState(null)
  const [fullAutoPreflightLoading, setFullAutoPreflightLoading] = useState(false)
  const [fullAutoPreflightError, setFullAutoPreflightError] = useState('')
  const [fullAutoRefreshNotice, setFullAutoRefreshNotice] = useState('')
  const [guidedCanvasOverride, setGuidedCanvasOverride] = useState(false)
  const [guidedCanvasSize, setGuidedCanvasSize] = useState({ width: 768, height: 1360 })
  const [guidedAdvancedOpen, setGuidedAdvancedOpen] = useState(false)
  const [guidedRequestId, setGuidedRequestId] = useState('')
  const [guidedOutcomeUnknown, setGuidedOutcomeUnknown] = useState(false)
  const [guidedRequestConflict, setGuidedRequestConflict] = useState(false)
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('all')
  const [importedIdentities, setImportedIdentities] = useState(() => new Set())

  // Detailed revision inspection state: key = `${library_key}:${source_id}:${content_digest}`
  const [expandedDetails, setExpandedDetails] = useState({})
  const [detailLoading, setDetailLoading] = useState({})

  // Server-owned SelectionView state (sole authoritative source of truth for browser import)
  const [selectionView, setSelectionView] = useState(null)
  const [activeSelectionId, setActiveSelectionId] = useState(null)
  const [selectedFiles, setSelectedFiles] = useState([])
  const [targetDrafts, setTargetDrafts] = useState({}) // file_id -> { library_key, auxiliary_kind }
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [showLegacyPathImport, setShowLegacyPathImport] = useState(false)
  const genRef = useRef(0)
  const epochRef = useRef(1)
  const activeSelectionIdRef = useRef(null)
  const checkingCleanupRef = useRef(false)
  const selectionViewRef = useRef(null)
  const guidedRequestMountedRef = useRef(true)
  const guidedRequestSnapshotRef = useRef(null)
  const guidedSubmissionInFlightRef = useRef(false)
  const guidedCreationCompleteRef = useRef(false)
  const guidedLooksRequestRef = useRef(0)
  const guidedLookRequestRef = useRef(0)
  const fullAutoPreflightRequestRef = useRef(0)
  const fullAutoInputSignatureRef = useRef('')

  // Legacy path import state (for compatibility)
  const [selections, setSelections] = useState([{ path: '', library_key: '' }])
  const [legacyRawPreview, setLegacyRawPreview] = useState(null)
  const [legacyPreviewReport, setLegacyPreviewReport] = useState(null)
  const [legacyCommitReport, setLegacyCommitReport] = useState(null)

  const [busy, setBusy] = useState(false)
  const [checkingCleanup, setCheckingCleanup] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  // Translation mapping state per library key
  const [translationState, setTranslationState] = useState({})
  const manualGenerationRef = useRef({})
  const libraryEpochRef = useRef(0)
  const libraryRefreshPromiseRef = useRef(null)
  const translationStateRef = useRef(translationState)
  translationStateRef.current = translationState

  const updateTState = (libraryKey, patch) => {
    setTranslationState((prev) => ({
      ...prev,
      [libraryKey]: { ...(prev[libraryKey] || {}), ...patch },
    }))
  }

  const nextManualGeneration = (libraryKey) => {
    const next = (manualGenerationRef.current[libraryKey] || 0) + 1
    manualGenerationRef.current[libraryKey] = next
    return next
  }

  const invalidateProposalGeneration = (libraryKey, patch = {}) => {
    nextManualGeneration(libraryKey)
    updateTState(libraryKey, {
      proposalBusy: false,
      ...patch,
    })
  }

  const clearManualPreview = (libraryKey, patch = {}) => {
    invalidateProposalGeneration(libraryKey, {
      manualBusy: false,
      manualPreview: null,
      manualSnapshot: '',
      manualError: '',
      ...patch,
    })
  }

  const handleLoadTranslationRows = async (libraryKey) => {
    const generation = nextManualGeneration(libraryKey)
    updateTState(libraryKey, {
      manualBusy: true,
      proposalBusy: false,
      manualError: '',
      manualNotice: '',
      manualPreview: null,
      manualSnapshot: '',
    })
    try {
      const result = await api.get(
        `/api/resources/libraries/${encodeURIComponent(libraryKey)}/translations/rows`
      )
      if (manualGenerationRef.current[libraryKey] !== generation) return
      updateTState(libraryKey, {
        manualRows: (result.rows || []).map((row) => ({
          ...row,
          emit: Boolean(row.emit_by_default),
          suggest_selected: false,
        })),
        manualDiagnostics: result.diagnostics || [],
        manualNotice: `Loaded ${(result.rows || []).length} editable source-backed row(s).`,
        preProposalRows: null,
        proposalBusy: false,
      })
    } catch (e) {
      if (manualGenerationRef.current[libraryKey] === generation) {
        updateTState(libraryKey, { manualError: e.message })
      }
    } finally {
      if (manualGenerationRef.current[libraryKey] === generation) {
        updateTState(libraryKey, { manualBusy: false })
      }
    }
  }

  const updateManualRow = (libraryKey, index, patch) => {
    nextManualGeneration(libraryKey)
    setTranslationState((prev) => {
      const state = prev[libraryKey] || {}
      const rows = [...(state.manualRows || [])]
      rows[index] = { ...rows[index], ...patch }
      return {
        ...prev,
        [libraryKey]: {
          ...state,
          manualRows: rows,
          manualBusy: false,
          proposalBusy: false,
          manualPreview: null,
          manualSnapshot: '',
          manualError: '',
          preProposalRows: null,
        },
      }
    })
  }

  const handleSuggestTranslations = async (libraryKey) => {
    const state = translationState[libraryKey] || {}
    const rows = state.manualRows || []
    const selectedRows = rows.filter((r) => r.suggest_selected && isRowEligibleForProposal(r))
    if (selectedRows.length === 0 || selectedRows.length > 20) return

    const generation = nextManualGeneration(libraryKey)
    const preSnapshot = rows.map((r) => ({ ...r }))
    updateTState(libraryKey, {
      proposalBusy: true,
      manualError: '',
      manualNotice: '',
      manualPreview: null,
      manualSnapshot: '',
    })

    const entries = selectedRows.map(buildProposalRequestEntry)
    try {
      const result = await api.post(
        `/api/resources/libraries/${encodeURIComponent(libraryKey)}/translations/proposals`,
        { entries }
      )
      if (manualGenerationRef.current[libraryKey] !== generation) return

      const currentRows = (translationState[libraryKey]?.manualRows || rows)
      const { updatedRows, appliedCount } = applyProposalsToRows(currentRows, result.proposals)

      updateTState(libraryKey, {
        manualRows: updatedRows,
        preProposalRows: preSnapshot,
        manualNotice: `Suggested ${appliedCount} translation(s). Review and preview when ready.`,
        manualPreview: null,
        manualSnapshot: '',
      })
    } catch (e) {
      if (manualGenerationRef.current[libraryKey] === generation) {
        updateTState(libraryKey, { manualError: e.message })
      }
    } finally {
      if (manualGenerationRef.current[libraryKey] === generation) {
        updateTState(libraryKey, { proposalBusy: false })
      }
    }
  }

  const handleDiscardSuggestions = (libraryKey) => {
    const state = translationState[libraryKey] || {}
    if (!state.preProposalRows) return
    invalidateProposalGeneration(libraryKey, {
      manualRows: state.preProposalRows,
      preProposalRows: null,
      manualPreview: null,
      manualSnapshot: '',
      manualNotice: 'Discarded translation suggestions.',
      manualError: '',
    })
  }

  const handlePreviewManualTranslations = async (libraryKey) => {
    const state = translationState[libraryKey] || {}
    const built = buildTranslationMapFromRows(state.manualRows || [])
    if (built.errors.length || Object.keys(built.translationMap).length === 0) return
    const generation = manualGenerationRef.current[libraryKey] || 0
    const snapshot = JSON.stringify(built.translationMap)
    updateTState(libraryKey, {
      manualBusy: true,
      manualError: '',
      manualNotice: '',
      manualPreview: null,
      manualSnapshot: '',
    })
    try {
      const result = await api.post(
        `/api/resources/libraries/${encodeURIComponent(libraryKey)}/translations/preview`,
        { translation_map: built.translationMap }
      )
      if (manualGenerationRef.current[libraryKey] !== generation) return
      const preview = parseTranslationPreview(result)
      updateTState(libraryKey, {
        manualPreview: preview,
        manualSnapshot: snapshot,
        manualNotice: `Manual preview ready: ${preview.matchedRevisions} of ${preview.totalRevisions} revisions matched.`,
      })
    } catch (e) {
      if (manualGenerationRef.current[libraryKey] === generation) {
        updateTState(libraryKey, { manualError: e.message })
      }
    } finally {
      if (manualGenerationRef.current[libraryKey] === generation) {
        updateTState(libraryKey, { manualBusy: false })
      }
    }
  }

  const handleApplyManualTranslations = async (libraryKey) => {
    const state = translationState[libraryKey] || {}
    const built = buildTranslationMapFromRows(state.manualRows || [])
    const snapshot = JSON.stringify(built.translationMap)
    if (
      built.errors.length
      || !state.manualPreview?.attestationToken
      || snapshot !== state.manualSnapshot
    ) {
      clearManualPreview(libraryKey, {
        manualError: 'Translation rows changed since preview. Preview again before applying.',
      })
      return
    }
    updateTState(libraryKey, { manualBusy: true, manualError: '', manualNotice: '' })
    try {
      const result = await api.post(
        `/api/resources/libraries/${encodeURIComponent(libraryKey)}/translations/apply`,
        {
          translation_map: built.translationMap,
          attestation_token: state.manualPreview.attestationToken,
        }
      )
      clearManualPreview(libraryKey, {
        preProposalRows: null,
        manualNotice: `Translations applied: ${result.updated} updated, ${result.ready} ready, ${result.pending} pending.`,
      })
      try {
        await awaitCurrentLibraryRefresh(reloadLibraries({ reportError: false }))
      } catch (refreshErr) {
        clearManualPreview(libraryKey, {
          preProposalRows: null,
          manualError: `Translations applied, but failed to refresh readiness: ${refreshErr.message}`,
        })
        return
      }
      await handleLoadTranslationRows(libraryKey)
    } catch (e) {
      clearManualPreview(libraryKey, { manualError: e.message })
    } finally {
      updateTState(libraryKey, { manualBusy: false })
    }
  }

  const handleMapPathChange = (libraryKey, newPath) => {
    nextManualGeneration(libraryKey)
    const mapOperation = translationStateRef.current[libraryKey]?.mapOperation || null
    updateTState(libraryKey, {
      mapPath: newPath,
      preview: null,
      error: '',
      notice: '',
      mapOperation: mapOperation === 'preview' ? null : mapOperation,
    })
  }

  const handlePreviewTranslations = async (libraryKey) => {
    const tState = translationState[libraryKey] || {}
    const mapPath = (tState.mapPath || '').trim()
    if (!mapPath || tState.mapOperation) return

    const generation = nextManualGeneration(libraryKey)
    const pathSnapshot = mapPath
    updateTState(libraryKey, {
      mapOperation: 'preview',
      error: '',
      notice: '',
      preview: null,
    })
    try {
      const res = await api.post(`/api/resources/libraries/${encodeURIComponent(libraryKey)}/translations/preview`, {
        map_path: mapPath,
      })
      const current = translationStateRef.current[libraryKey] || {}
      if (
        manualGenerationRef.current[libraryKey] !== generation
        || (current.mapPath || '').trim() !== pathSnapshot
      ) {
        return
      }
      const parsed = parseTranslationPreview(res)
      updateTState(libraryKey, {
        preview: parsed,
        notice: `Preview ready: ${parsed.matchedRevisions} of ${parsed.totalRevisions} revisions matched.`,
      })
    } catch (e) {
      if (manualGenerationRef.current[libraryKey] === generation) {
        updateTState(libraryKey, { error: e.message, preview: null })
      }
    } finally {
      if (manualGenerationRef.current[libraryKey] === generation) {
        updateTState(libraryKey, { mapOperation: null })
      }
    }
  }

  const handleApplyTranslations = async (libraryKey) => {
    const tState = translationState[libraryKey] || {}
    const mapPath = (tState.mapPath || '').trim()
    if (!mapPath || !tState.preview?.attestationToken || tState.mapOperation) return

    updateTState(libraryKey, { mapOperation: 'apply', error: '', notice: '' })
    try {
      const res = await api.post(`/api/resources/libraries/${encodeURIComponent(libraryKey)}/translations/apply`, {
        map_path: mapPath,
        attestation_token: tState.preview.attestationToken,
      })
      updateTState(libraryKey, {
        preview: null,
        notice: `Translations applied: ${res.updated} updated, ${res.ready} ready, ${res.pending} pending.`,
      })
      try {
        await awaitCurrentLibraryRefresh(reloadLibraries({ reportError: false }))
      } catch (refreshErr) {
        updateTState(libraryKey, {
          preview: null,
          error: `Translations applied, but failed to refresh readiness: ${refreshErr.message}`,
        })
      }
    } catch (e) {
      let msg = e.message
      if (msg && (msg.includes('409') || msg.includes('drift') || msg.includes('changed since preview'))) {
        msg = 'Library state or translation map changed since preview. Please preview again before applying.'
      }
      updateTState(libraryKey, { preview: null, error: msg })
    } finally {
      updateTState(libraryKey, { mapOperation: null })
    }
  }

  const awaitCurrentLibraryRefresh = async (initialPromise) => {
    let refreshPromise = initialPromise
    let result = await refreshPromise
    while (result.status === 'superseded') {
      const currentPromise = libraryRefreshPromiseRef.current
      if (!currentPromise || currentPromise === refreshPromise) return result
      refreshPromise = currentPromise
      result = await refreshPromise
    }
    return result
  }

  const reloadLibraries = ({ reportError = true } = {}) => {
    const currentEpoch = ++libraryEpochRef.current
    for (const key of Object.keys(manualGenerationRef.current)) {
      nextManualGeneration(key)
    }
    setTranslationState((prev) => Object.fromEntries(
      Object.entries(prev).map(([key, state]) => [key, {
        ...state,
        manualBusy: false,
        proposalBusy: false,
        manualPreview: null,
        manualSnapshot: '',
        preview: null,
        mapOperation: state.mapOperation === 'apply' ? 'apply' : null,
        preProposalRows: null,
      }])
    ))
    const refreshPromise = api.get('/api/resources/libraries')
      .then((data) => {
        if (libraryEpochRef.current !== currentEpoch) return { status: 'superseded' }
        setLibraries(data || [])
        return { status: 'applied', libraries: data || [] }
      })
      .catch((e) => {
        if (libraryEpochRef.current !== currentEpoch) return { status: 'superseded' }
        if (reportError && libraryEpochRef.current === currentEpoch) {
          setError(e.message)
        }
        throw e
      })
    libraryRefreshPromiseRef.current = refreshPromise
    return refreshPromise
  }

  const handleTranslateRevision = (libraryKey) => {
    handleLoadTranslationRows(libraryKey)
    const el = document.getElementById(`translation-mapping-${libraryKey}`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth' })
    }
  }

  const reloadModels = () => {
    api.get('/api/models')
      .then((data) => {
        const list = data || []
        setModels(list)
        setSelectedModelId((current) => selectAvailableModelId(list, current || requestedModelId))
      })
      .catch(() => {})
  }

  useEffect(() => {
    reloadLibraries()
    reloadModels()
  }, [requestedModelId])

  useEffect(() => {
    guidedRequestMountedRef.current = true
    return () => { guidedRequestMountedRef.current = false }
  }, [])

  useEffect(() => {
    api.get('/api/config')
      .then((config) => setAssistantConfigured(Boolean(config?.llm_ok)))
      .catch(() => setAssistantConfigured(false))
    api.get('/api/workflows')
      .then((data) => setWorkflows(data || []))
      .catch(() => setWorkflows([]))
  }, [])

  useEffect(() => {
    if (!guidedSceneKey) return undefined
    const request = ++guidedLooksRequestRef.current
    let active = true
    setGuidedSavedLooksLoading(true)
    setGuidedSavedLooksError('')
    api.getVersioned('/api/looks')
      .then((result) => {
        if (!active || request !== guidedLooksRequestRef.current) return
        if (!Array.isArray(result) || result.some((look) => (
          !look || typeof look.key !== 'string' || !look.key
          || typeof look.name !== 'string' || !versionText(look.version)
        ))) throw new Error('The saved-look list is invalid. Reload it from Looks.')
        setGuidedSavedLooks(result)
      })
      .catch((cause) => {
        if (active && request === guidedLooksRequestRef.current) {
          setGuidedSavedLooksError(cause?.message || 'Could not load saved looks.')
        }
      })
      .finally(() => {
        if (active && request === guidedLooksRequestRef.current) setGuidedSavedLooksLoading(false)
      })
    return () => {
      active = false
      if (request === guidedLooksRequestRef.current) guidedLooksRequestRef.current += 1
    }
  }, [guidedSceneKey])

  useEffect(() => {
    if (guidedSceneKey && !guidedMode && assistantConfigured !== null) {
      setGuidedMode(assistantConfigured ? 'automatic' : 'manual')
    }
  }, [guidedSceneKey, guidedMode, assistantConfigured])

  // Inspection drawer fetch
  const toggleDetail = async (libraryKey, revision) => {
    const key = `${libraryKey}:${revision.source_id}:${revision.content_digest}`
    if (expandedDetails[key]) {
      const next = { ...expandedDetails }
      delete next[key]
      setExpandedDetails(next)
      return
    }

    setDetailLoading((prev) => ({ ...prev, [key]: true }))
    try {
      const detail = await api.get(
        `/api/resources/revisions/${encodeURIComponent(libraryKey)}/${encodeURIComponent(revision.source_id)}/${encodeURIComponent(revision.content_digest)}`
      )
      setExpandedDetails((prev) => ({ ...prev, [key]: detail }))
    } catch (e) {
      setError(e.message)
    } finally {
      setDetailLoading((prev) => ({ ...prev, [key]: false }))
    }
  }

  // Start session handler
  const startSessionWithRevision = async (libraryKey, revision) => {
    setError('')
    setNotice('')
    const readiness = checkReadiness(revision)
    if (!readiness.isReady) {
      setError(`Cannot start session: revision is not ready. ${readiness.reasons.join('; ')}`)
      return
    }

    const model = models.find((m) => String(m.id) === String(selectedModelId))
    if (!model) {
      setError('Please select a model first.')
      return
    }

    setBusy(true)
    try {
      const payload = buildSessionDraftPayload({
        model,
        revision,
        libraryKey,
      })

      // 1. Create session with composition_mode: "resource-v1"
      const { id: sid } = await api.post('/api/sessions', payload.session)

      // 2. Persist plan with compare-and-swap expected_revision: 0
      await api.post(`/api/sessions/${sid}/plan`, {
        plan: payload.plan,
        expected_revision: payload.expected_revision,
      })

      // 3. Navigate to the newly created session draft
      go(`/session/${sid}`)
    } catch (e) {
      setError(e.message)
      setBusy(false)
    }
  }

  const openGuidedCreation = (libraryKey, revision) => {
    guidedLooksRequestRef.current += 1
    guidedLookRequestRef.current += 1
    fullAutoPreflightRequestRef.current += 1
    fullAutoInputSignatureRef.current = ''
    const anchor = exactRevisionTriple(libraryKey, revision)
    setGuidedSceneKey(JSON.stringify([anchor.library_key, anchor.source_id, anchor.content_digest]))
    setGuidedCharacterId(String(selectedModelId || requestedModelId || ''))
    setGuidedPhotoCount('12')
    setGuidedBrief('')
    setGuidedMode(assistantConfigured === null ? '' : assistantConfigured ? 'automatic' : 'manual')
    setGuidedWorkflowOverrideId('')
    setGuidedVariationPolicy(defaultGuidedPolicy())
    setGuidedLook('')
    setGuidedInitialWardrobe('')
    setGuidedSavedLooks([])
    setGuidedSavedLooksError('')
    setGuidedSavedLookKey('')
    setGuidedSavedLookVersion('')
    setGuidedSavedLook(null)
    setGuidedSavedLookError('')
    setGuidedSelectedProfile(null)
    setFullAutoEnabled(false)
    setGuidedFramingIntent('vary')
    setFullAutoQuote(null)
    setFullAutoPreflightLoading(false)
    setFullAutoPreflightError('')
    setFullAutoRefreshNotice('')
    setGuidedCanvasOverride(false)
    setGuidedCanvasSize({ width: 768, height: 1360 })
    setGuidedAdvancedOpen(false)
    setGuidedRequestId(crypto.randomUUID())
    guidedRequestSnapshotRef.current = null
    guidedCreationCompleteRef.current = false
    setGuidedOutcomeUnknown(false)
    setGuidedRequestConflict(false)
    setError('')
  }

  const loadGuidedSavedLookVersion = async (lookKey, version) => {
    const exactVersion = versionText(version)
    const request = ++guidedLookRequestRef.current
    setGuidedSavedLook(null)
    setGuidedSelectedProfile(null)
    setGuidedSavedLookError('')
    setFullAutoQuote(null)
    setFullAutoRefreshNotice('')
    if (!lookKey || !exactVersion) {
      setGuidedSavedLookLoading(false)
      setGuidedSavedLookError('Enter an exact saved-look version supported by the library.')
      return
    }
    setGuidedSavedLookLoading(true)
    try {
      const result = await api.getVersioned(
        `/api/looks/${encodeURIComponent(lookKey)}/versions/${exactVersion}`,
      )
      if (request !== guidedLookRequestRef.current || !guidedRequestMountedRef.current) return
      if (result?.key !== lookKey || versionText(result?.version) !== exactVersion
        || !SHA256.test(result?.content_digest || '') || typeof result.appearance !== 'string') {
        throw new Error('The server returned a different or incomplete exact saved-look version.')
      }
      setGuidedSavedLook(result)
      setGuidedSavedLookVersion(exactVersion)
    } catch (cause) {
      if (request === guidedLookRequestRef.current) {
        setGuidedSavedLookError(cause?.message || 'Could not load this exact saved-look version.')
      }
    } finally {
      if (request === guidedLookRequestRef.current) setGuidedSavedLookLoading(false)
    }
  }

  const selectGuidedSavedLook = (lookKey) => {
    setGuidedSavedLookKey(lookKey)
    setGuidedSavedLook(null)
    setGuidedSelectedProfile(null)
    setGuidedSavedLookError('')
    const selected = guidedSavedLooks.find((look) => look.key === lookKey)
    const latestVersion = versionText(selected?.version)
    setGuidedSavedLookVersion(latestVersion || '')
    if (lookKey && latestVersion) void loadGuidedSavedLookVersion(lookKey, latestVersion)
    else {
      guidedLookRequestRef.current += 1
      setGuidedSavedLookLoading(false)
    }
  }

  const fetchFullAutoPreflight = async (body, signature) => {
    const request = ++fullAutoPreflightRequestRef.current
    let active = true
    setFullAutoQuote(null)
    setFullAutoPreflightError('')
    setFullAutoPreflightLoading(true)
    try {
      const response = await api.postWithStatus(
        '/api/sessions/guided/full-auto/preflight', body,
        ['saved_look.version', 'full_auto.profile_revision'],
      )
      if (!active || request !== fullAutoPreflightRequestRef.current
        || signature !== fullAutoInputSignatureRef.current) return null
      if (response?.status !== 200 || !isFullAutoConsent(response.data, body, guidedSavedLook)) {
        throw new Error('The server returned an incomplete Full Auto consent summary. Refresh the selection before authorizing.')
      }
      const quote = { ...response.data, signature }
      setFullAutoQuote(quote)
      return quote
    } catch (cause) {
      if (active && request === fullAutoPreflightRequestRef.current
        && signature === fullAutoInputSignatureRef.current) {
        setFullAutoPreflightError(cause?.message || 'Could not resolve Full Auto consent.')
      }
      return null
    } finally {
      if (active && request === fullAutoPreflightRequestRef.current) setFullAutoPreflightLoading(false)
    }
  }

  const sendGuidedRequest = async (request) => {
    if (guidedSubmissionInFlightRef.current) return
    guidedSubmissionInFlightRef.current = true
    setBusy(true)
    setError('')
    try {
      const response = await api.postWithStatus('/api/sessions/guided', request, [
        ...(request.saved_look ? ['saved_look.version'] : []),
        ...(request.full_auto ? ['full_auto.profile_revision'] : []),
      ])
      if (!guidedRequestMountedRef.current || guidedRequestSnapshotRef.current !== request) return
      if (!isGuidedSuccessResponse(response, request)) {
        setGuidedOutcomeUnknown(true)
        setGuidedRequestConflict(false)
        setError('The server returned an incomplete guided creation result.')
        return
      }
      setGuidedOutcomeUnknown(false)
      setGuidedRequestConflict(false)
      guidedCreationCompleteRef.current = true
      go(`/session/${response.data.session_id}`)
    } catch (e) {
      if (!guidedRequestMountedRef.current || guidedRequestSnapshotRef.current !== request) return
      const stableClientError = Number.isInteger(e.status)
        && e.status >= 400 && e.status < 500
        && e.hasStableErrorBody === true
        && typeof e.detail?.code === 'string'
        && typeof e.detail?.message === 'string'
      if (stableClientError && e.status === 409 && e.detail.code === 'idempotency_conflict') {
        setGuidedOutcomeUnknown(false)
        setGuidedRequestConflict(true)
        setError(e.message)
      } else if (stableClientError && e.status === 409 && e.detail.code === 'authorization_scope_changed'
        && request.full_auto) {
        guidedRequestSnapshotRef.current = null
        setGuidedOutcomeUnknown(false)
        setGuidedRequestConflict(false)
        const body = { ...request }
        delete body.request_id
        body.full_auto = Object.fromEntries(Object.entries(request.full_auto)
          .filter(([key]) => key !== 'scope_digest' && key !== 'scope_issued_at'))
        const current = e.detail.current
        const refreshed = current && isFullAutoConsent(current, body, guidedSavedLook)
          ? { ...current, signature: fullAutoInputSignatureRef.current }
          : null
        if (refreshed) {
          setFullAutoQuote(refreshed)
          setFullAutoPreflightError('')
        } else {
          void fetchFullAutoPreflight(body, fullAutoInputSignatureRef.current)
        }
        setFullAutoRefreshNotice('The authorized inputs changed. Review this refreshed summary, then click Generate without pauses again to authorize it.')
        setError('')
      } else if (stableClientError) {
        guidedRequestSnapshotRef.current = null
        setGuidedOutcomeUnknown(false)
        setGuidedRequestConflict(false)
        setError(e.message)
      } else {
        setGuidedOutcomeUnknown(true)
        setGuidedRequestConflict(false)
        setError(e.message)
      }
    } finally {
      guidedSubmissionInFlightRef.current = false
      if (guidedRequestMountedRef.current) setBusy(false)
    }
  }

  const submitGuidedCreation = async () => {
    if (guidedSubmissionInFlightRef.current || guidedOutcomeUnknown || guidedRequestConflict) return
    const character = models.find((model) => String(model.id) === guidedCharacterId)
    const scene = readyRoomChoices.find((choice) => choice.key === guidedSceneKey)
    const photoCount = Number(guidedPhotoCount)
    const workflowOverrideId = Number(guidedWorkflowOverrideId)
    const hasDefaultWorkflow = Number.isInteger(Number(character?.workflow_id)) && Number(character.workflow_id) > 0

    if (!character || !scene || !guidedRequestId || !['automatic', 'manual'].includes(guidedMode)) {
      setError('Choose a character, a ready scene, and an authoring mode.')
      return
    }
    if (!Number.isInteger(photoCount) || photoCount < 1 || photoCount > 500) {
      setError('Photo count must be an integer from 1 through 500.')
      return
    }
    if (guidedBrief.length > 2000) {
      setError('Brief must be 2,000 characters or fewer.')
      return
    }
    const variationPolicy = Object.fromEntries(GUIDED_DIMENSIONS.map(([field]) => {
      const setting = guidedVariationPolicy[field] || { mode: 'vary', value: '' }
      if (setting.mode === 'fixed') {
        if (typeof setting.value !== 'string' || !setting.value.trim()) {
          return [field, { mode: 'fixed', value: '', value_origin: 'user' }]
        }
        return [field, { mode: 'fixed', value: setting.value, value_origin: 'user' }]
      }
      return [field, { mode: 'vary' }]
    }))
    if (GUIDED_DIMENSIONS.some(([field]) => variationPolicy[field].mode === 'fixed' && !variationPolicy[field].value.trim())) {
      setGuidedAdvancedOpen(true)
      setError('Enter a value for every fixed camera, framing, pose, and expression choice.')
      return
    }
    if (!hasDefaultWorkflow && !(Number.isInteger(workflowOverrideId) && workflowOverrideId > 0)) {
      setGuidedAdvancedOpen(true)
      setError('This character needs a default workflow or an Advanced workflow override.')
      return
    }
    if (guidedCanvasOverride) {
      const { width, height } = guidedCanvasSize
      if (!Number.isInteger(width) || !Number.isInteger(height)
        || width < 8 || height < 8 || width % 8 !== 0 || height % 8 !== 0) {
        setGuidedAdvancedOpen(true)
        setError('Canvas width and height must be positive multiples of 8.')
        return
      }
    }

    const savedLookIdentity = guidedSavedLook && guidedSavedLook.key === guidedSavedLookKey
      && versionText(guidedSavedLook.version) === versionText(guidedSavedLookVersion)
      ? {
        look_key: guidedSavedLook.key,
        version: versionText(guidedSavedLook.version),
        content_digest: guidedSavedLook.content_digest,
      }
      : null
    if (guidedSavedLookKey && !savedLookIdentity) {
      setError('Load the selected exact saved-look version before creating this session.')
      return
    }
    if (savedLookIdentity && (guidedLook.trim() || guidedInitialWardrobe.trim())) {
      setGuidedAdvancedOpen(true)
      setError('Clear both shared overrides to create from this saved look, or remove the saved look and use the existing post-creation Keep/Replace flow.')
      return
    }

    const request = freezeGuidedRequest({
      request_id: guidedRequestId,
      character_id: character.id,
      workflow_id: guidedWorkflowOverrideId ? workflowOverrideId : null,
      scene_anchor: exactRevisionTriple(scene.libraryKey, scene.revision),
      photo_count: photoCount,
      brief: guidedBrief,
      mode: guidedMode,
      variation_policy: variationPolicy,
      look: guidedLook,
      initial_wardrobe: guidedInitialWardrobe,
      ...(guidedCanvasOverride ? guidedCanvasSize : {}),
      ...(savedLookIdentity ? { saved_look: savedLookIdentity } : {}),
    })
    guidedRequestSnapshotRef.current = request
    await sendGuidedRequest(request)
  }

  const submitFullAutoCreation = async () => {
    if (guidedSubmissionInFlightRef.current || guidedOutcomeUnknown || guidedRequestConflict) return
    const quote = fullAutoQuote
    const body = fullAutoPreflightBody
    const signature = fullAutoPreflightInputSignature
    if (!body || !quote || quote.signature !== signature || !signature || fullAutoPreflightLoading) return
    const age = Date.now() - Date.parse(quote.scope_issued_at)
    if (!Number.isFinite(age) || age < 0 || age > 300_000) {
      setFullAutoRefreshNotice('This consent check is older than five minutes. The summary is refreshing; review it and click again.')
      void fetchFullAutoPreflight(body, signature)
      return
    }
    const request = freezeGuidedRequest({
      ...body,
      request_id: guidedRequestId,
      full_auto: {
        ...body.full_auto,
        scope_digest: quote.scope_digest,
        scope_issued_at: quote.scope_issued_at,
      },
    })
    guidedRequestSnapshotRef.current = request
    setFullAutoRefreshNotice('')
    await sendGuidedRequest(request)
  }

  const retryGuidedCreation = () => {
    if (!guidedOutcomeUnknown || guidedSubmissionInFlightRef.current) return
    const request = guidedRequestSnapshotRef.current
    if (request) void sendGuidedRequest(request)
  }

  const startGuidedCreationAttempt = () => {
    if (busy || guidedSubmissionInFlightRef.current) return
    guidedRequestSnapshotRef.current = null
    guidedCreationCompleteRef.current = false
    setGuidedRequestId(crypto.randomUUID())
    setGuidedOutcomeUnknown(false)
    setGuidedRequestConflict(false)
    setError('')
  }

  const chooseStructuredScene = () => {
    setTab('inventory')
    setCategory('rooms')
    setQuery('')
    setNotice('Showing structured room resources. Choose a revision marked Ready.')
  }

  // Centralized SelectionView state updater (Highest revision wins / epoch & generation fenced)
  const applySelectionView = (rawCandidate, candidateEpoch, candidateGen) => {
    if (candidateEpoch < epochRef.current) return null

    const normalized = normalizeSelectionView(rawCandidate)
    if (!normalized) return null

    const result = reduceSelectionView(selectionViewRef.current, normalized, {
      activeSelectionId: activeSelectionIdRef.current,
      currentEpoch: epochRef.current,
      candidateEpoch,
      currentGen: genRef.current,
      candidateGen,
    })

    if (result.accepted) {
      selectionViewRef.current = result.view
      if (!activeSelectionIdRef.current || activeSelectionIdRef.current !== result.view.selection_id) {
        activeSelectionIdRef.current = result.view.selection_id
        setActiveSelectionId(result.view.selection_id)
      }
      genRef.current = Math.max(genRef.current, candidateGen)
      setSelectionView(result.view)
      return result.view
    }

    return null
  }

  // Poll for status if selection is in committing state
  useEffect(() => {
    if (selectionView?.state === 'committing' && activeSelectionId) {
      const pollEpoch = epochRef.current
      const interval = setInterval(async () => {
        try {
          const pollGen = ++genRef.current
          const view = await api.get(`/api/resources/import-selections/${activeSelectionId}`)
          if (pollEpoch !== epochRef.current) return
          const applied = applySelectionView(view, pollEpoch, pollGen)
          if (applied?.state === 'committed') {
            setNotice('')
            const report = applied.commit_result?.report || applied.commit_result
            if (report) {
              setImportedIdentities(extractImportedIdentities(report))
            }
            try {
              await awaitCurrentLibraryRefresh(reloadLibraries())
              if (pollEpoch === epochRef.current) {
                setNotice('Import committed successfully. Local resource inventory updated.')
              }
            } catch (refreshErr) {
              if (pollEpoch === epochRef.current) {
                setNotice('')
                setError(`Import committed, but failed to refresh inventory: ${refreshErr.message}`)
              }
            }
          }
        } catch {
          // ignore transient poll failures
        }
      }, 2000)
      return () => clearInterval(interval)
    }
  }, [selectionView?.state, activeSelectionId])

  // Native browser file selection handlers
  const handleFilesChosen = (e) => {
    const files = Array.from(e.target.files || [])
    if (!files.length) return
    setSelectedFiles(files)
  }

  const uploadSelectedFiles = async (filesToUpload = selectedFiles) => {
    if (!filesToUpload || filesToUpload.length === 0) return
    setBusy(true)
    setError('')
    setNotice('')

    const uploadEpoch = epochRef.current
    let sid = activeSelectionIdRef.current
    try {
      if (!sid || (selectionView && selectionView.state !== 'open')) {
        const requestId = crypto.randomUUID()
        const createGen = ++genRef.current
        const view = await api.post('/api/resources/import-selections', { request_id: requestId })
        if (uploadEpoch !== epochRef.current) return
        sid = view.selection_id
        activeSelectionIdRef.current = sid
        setActiveSelectionId(sid)
        applySelectionView(view, uploadEpoch, createGen)
      }

      await Promise.all(
        filesToUpload.map(async (file) => {
          const uploadId = crypto.randomUUID()
          const formData = new FormData()
          formData.append('file', file)
          formData.append('upload_id', uploadId)
          const uploadGen = ++genRef.current
          try {
            const updatedView = await api.uploadMultipart(
              `/api/resources/import-selections/${sid}/files`,
              formData
            )
            if (uploadEpoch !== epochRef.current) return
            applySelectionView(updatedView, uploadEpoch, uploadGen)
          } catch (err) {
            if (uploadEpoch !== epochRef.current) return
            if (err.detail?.current) {
              applySelectionView(err.detail.current, uploadEpoch, uploadGen)
            }
            throw err
          }
        })
      )

      if (uploadEpoch !== epochRef.current) return
      setSelectedFiles([])
      setNotice(`Uploaded ${filesToUpload.length} file(s) into selection.`)
    } catch (err) {
      if (uploadEpoch === epochRef.current) {
        setError(err.message || 'Upload failed')
      }
    } finally {
      if (uploadEpoch === epochRef.current) {
        setBusy(false)
      }
    }
  }

  const handleRemoveFile = async (fileId) => {
    if (!selectionView || !activeSelectionId) return
    setBusy(true)
    setError('')
    setNotice('')
    const removeEpoch = epochRef.current
    const removeGen = ++genRef.current
    try {
      const updatedView = await api.del(
        `/api/resources/import-selections/${activeSelectionId}/files/${fileId}?expected_revision=${selectionView.selection_revision}`
      )
      if (removeEpoch !== epochRef.current) return
      applySelectionView(updatedView, removeEpoch, removeGen)
      setNotice('File removed from selection.')
    } catch (err) {
      if (removeEpoch !== epochRef.current) return
      if (err.detail?.current) {
        applySelectionView(err.detail.current, removeEpoch, removeGen)
      }
      setError(err.message || 'Failed to remove file')
    } finally {
      if (removeEpoch === epochRef.current) {
        setBusy(false)
      }
    }
  }

  const handlePatchTarget = async (fileId, patch) => {
    if (!selectionView || !activeSelectionId) return
    setBusy(true)
    setError('')
    setNotice('')
    const patchEpoch = epochRef.current
    const patchGen = ++genRef.current
    const body = {
      expected_revision: selectionView.selection_revision,
      ...patch,
    }
    try {
      const updatedView = await api.patch(
        `/api/resources/import-selections/${activeSelectionId}/files/${fileId}`,
        body
      )
      if (patchEpoch !== epochRef.current) return
      applySelectionView(updatedView, patchEpoch, patchGen)
      setNotice('Target compatibility choice applied.')
    } catch (err) {
      if (patchEpoch !== epochRef.current) return
      if (err.detail?.current) {
        applySelectionView(err.detail.current, patchEpoch, patchGen)
      }
      setError(err.message || 'Failed to update target')
    } finally {
      if (patchEpoch === epochRef.current) {
        setBusy(false)
      }
    }
  }

  const handlePreview = async () => {
    if (!selectionView || !activeSelectionId) return
    setPreviewing(true)
    setError('')
    setNotice('')
    const previewEpoch = epochRef.current
    const previewGen = ++genRef.current
    try {
      const updatedView = await api.post(
        `/api/resources/import-selections/${activeSelectionId}/preview`,
        { expected_revision: selectionView.selection_revision }
      )
      if (previewEpoch !== epochRef.current) return
      const applied = applySelectionView(updatedView, previewEpoch, previewGen)
      if (applied?.preview?.committable) {
        setNotice('Preview generated successfully. Review outcomes below before committing.')
      } else {
        setNotice('Preview generated. Selection is not currently committable.')
      }
    } catch (err) {
      if (previewEpoch !== epochRef.current) return
      if (err.detail?.current) {
        applySelectionView(err.detail.current, previewEpoch, previewGen)
      }
      setError(err.message || 'Preview generation failed')
    } finally {
      if (previewEpoch === epochRef.current) {
        setPreviewing(false)
      }
    }
  }

  const handleCommit = async () => {
    if (!isImportEligible(selectionView, { activeSelectionId, pendingMutation: busy })) return
    const previewToken = selectionView?.preview?.preview_token
    if (!previewToken) return

    setBusy(true)
    setError('')
    setNotice('')
    const commitEpoch = epochRef.current
    const commitGen = ++genRef.current
    try {
      const updatedView = await api.post(
        `/api/resources/import-selections/${activeSelectionId}/commit`,
        {
          expected_revision: selectionView.selection_revision,
          preview_token: previewToken,
        }
      )
      if (commitEpoch !== epochRef.current) return
      const applied = applySelectionView(updatedView, commitEpoch, commitGen)
      if (applied?.state === 'committing') {
        setNotice('Commit is actively processing in the background.')
      } else if (applied?.state === 'committed') {
        setNotice('')
        const report = applied.commit_result?.report || applied.commit_result
        if (report) {
          setImportedIdentities(extractImportedIdentities(report))
        }
        try {
          await awaitCurrentLibraryRefresh(reloadLibraries())
          if (commitEpoch === epochRef.current) {
            setNotice('Import committed successfully. Local resource inventory updated.')
          }
        } catch (refreshErr) {
          if (commitEpoch === epochRef.current) {
            setNotice('')
            setError(`Import committed, but failed to refresh inventory: ${refreshErr.message}`)
          }
        }
      } else if (applied) {
        setError(`Unexpected selection state: ${applied.state}`)
      }
    } catch (err) {
      if (commitEpoch !== epochRef.current) return
      if (err.detail?.current) {
        const applied = applySelectionView(err.detail.current, commitEpoch, commitGen)
        if (applied?.state === 'committing') {
          setNotice('Commit is actively processing in the background.')
        } else {
          setError(err.message || 'Commit failed')
        }
      } else {
        setError(err.message || 'Commit failed')
      }
    } finally {
      if (commitEpoch === epochRef.current) {
        setBusy(false)
      }
    }
  }

  const handleCancel = async () => {
    if (!selectionView || !activeSelectionId || selectionView.state !== 'open') return
    setBusy(true)
    setError('')
    setNotice('')
    const cancelEpoch = epochRef.current
    const cancelGen = ++genRef.current
    try {
      const updatedView = await api.post(
        `/api/resources/import-selections/${activeSelectionId}/cancel`,
        { expected_revision: selectionView.selection_revision }
      )
      if (cancelEpoch !== epochRef.current) return
      applySelectionView(updatedView, cancelEpoch, cancelGen)
      setNotice('Selection cancelled.')
    } catch (err) {
      if (cancelEpoch !== epochRef.current) return
      if (err.detail?.current) {
        applySelectionView(err.detail.current, cancelEpoch, cancelGen)
      }
      setError(err.message || 'Cancel failed')
    } finally {
      if (cancelEpoch === epochRef.current) {
        setBusy(false)
      }
    }
  }

  const handleRetryCleanup = async () => {
    const selectionId = selectionView?.selection_id
    if (!selectionId || checkingCleanupRef.current) return
    checkingCleanupRef.current = true
    setCheckingCleanup(true)
    setError('')
    setNotice('')
    const requestEpoch = epochRef.current
    const requestGen = ++genRef.current
    try {
      const view = await api.get(`/api/resources/import-selections/${encodeURIComponent(selectionId)}`)
      if (requestEpoch !== epochRef.current) return
      const applied = applySelectionView(view, requestEpoch, requestGen)
      if (!applied) {
        setError('Cleanup status could not be verified. Retry the status check.')
      } else {
        setNotice(applied.cleanup_warning
          ? 'Temporary cleanup is still pending. Retry the status check to request another cleanup attempt.'
          : 'Temporary cleanup completed.')
      }
    } catch (err) {
      if (requestEpoch === epochRef.current) setError(err.message || 'Could not retry temporary cleanup.')
    } finally {
      if (requestEpoch === epochRef.current) {
        checkingCleanupRef.current = false
        setCheckingCleanup(false)
      }
    }
  }

  const handleReset = () => {
    epochRef.current += 1
    genRef.current = 0
    activeSelectionIdRef.current = null
    selectionViewRef.current = null
    setActiveSelectionId(null)
    setSelectionView(null)
    setSelectedFiles([])
    setTargetDrafts({})
    setBusy(false)
    checkingCleanupRef.current = false
    setCheckingCleanup(false)
    setPreviewing(false)
    setError('')
    setNotice('')
  }

  // Legacy path import helpers (for backwards compatibility)
  const updateSelection = (index, field, value) => {
    setSelections((prev) => {
      const next = [...prev]
      next[index] = { ...next[index], [field]: value }
      return next
    })
  }

  const addSelectionRow = () => {
    setSelections((prev) => [...prev, { path: '', library_key: '' }])
  }

  const removeSelectionRow = (index) => {
    setSelections((prev) => prev.filter((_, i) => i !== index))
  }

  const runLegacyPreview = async () => {
    setBusy(true)
    setError('')
    setNotice('')
    setLegacyCommitReport(null)
    const validSelections = selections
      .map((s) => ({ path: s.path.trim(), library_key: s.library_key.trim() }))
      .filter((s) => s.path && s.library_key)

    if (validSelections.length === 0) {
      setError('At least one selection with path and library_key is required.')
      setBusy(false)
      return
    }

    try {
      const res = await api.post('/api/resources/import/preview', { selections: validSelections })
      setLegacyRawPreview(res.preview)
      setLegacyPreviewReport(parsePreviewSummary(res.report))
      setNotice('Legacy preview generated successfully.')
    } catch (e) {
      setError(e.message)
      setLegacyRawPreview(null)
      setLegacyPreviewReport(null)
    } finally {
      setBusy(false)
    }
  }

  const runLegacyCommit = async () => {
    if (!legacyRawPreview) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const res = await api.post('/api/resources/import/commit', { preview: legacyRawPreview })
      setLegacyCommitReport(res.report)
      if (res.report) {
        setImportedIdentities(extractImportedIdentities(res.report))
      }
      setLegacyRawPreview(null)
      setLegacyPreviewReport(null)
      try {
        await awaitCurrentLibraryRefresh(reloadLibraries())
        setNotice('Legacy import committed successfully. Local resource inventory updated.')
      } catch (refreshErr) {
        setError(`Legacy import committed, but failed to refresh inventory: ${refreshErr.message}`)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const categories = extractCategories(libraries)
  const filteredLibraries = filterLibraries(libraries, { query, category })
  const readyRoomChoices = libraries.flatMap((library) => library.kind === 'rooms'
    ? (library.revisions || []).filter((revision) => checkReadiness(revision, { kind: library.kind }).isReady).map((revision) => {
      const anchor = exactRevisionTriple(library.library_key, revision)
      return {
        key: JSON.stringify([anchor.library_key, anchor.source_id, anchor.content_digest]),
        libraryKey: library.library_key,
        label: `${library.display_name || library.library_key} · ${revision.source_id}`,
        revision,
      }
    })
    : [])
  const guidedCharacter = models.find((model) => String(model.id) === guidedCharacterId)
  const guidedCharacterHasDefaultWorkflow = Number.isInteger(Number(guidedCharacter?.workflow_id))
    && Number(guidedCharacter.workflow_id) > 0
  const guidedHasWorkflow = guidedCharacterHasDefaultWorkflow
    || (Number.isInteger(Number(guidedWorkflowOverrideId)) && Number(guidedWorkflowOverrideId) > 0)
  const guidedLocked = busy || guidedOutcomeUnknown || guidedRequestConflict
  const guidedSceneChoice = readyRoomChoices.find((choice) => choice.key === guidedSceneKey)
  const currentGuidedVariationPolicy = Object.fromEntries(GUIDED_DIMENSIONS.map(([field]) => {
    const setting = guidedVariationPolicy[field] || { mode: 'vary', value: '' }
    return setting.mode === 'fixed'
      ? [field, { mode: 'fixed', value: setting.value, value_origin: 'user' }]
      : [field, { mode: 'vary' }]
  }))
  const guidedSavedLookIdentity = guidedSavedLook && guidedSavedLook.key === guidedSavedLookKey
    && versionText(guidedSavedLook.version) === versionText(guidedSavedLookVersion)
    ? {
      look_key: guidedSavedLook.key,
      version: versionText(guidedSavedLook.version),
      content_digest: guidedSavedLook.content_digest,
    }
    : null
  const guidedProfileMatchesLook = guidedSelectedProfile && guidedSavedLookIdentity
    && guidedSelectedProfile.look_key === guidedSavedLookIdentity.look_key
    && versionText(guidedSelectedProfile.version) === guidedSavedLookIdentity.version
    && guidedSelectedProfile.content_digest === guidedSavedLookIdentity.content_digest
    && SHA256.test(guidedSelectedProfile.profile_digest || '')
    && versionText(guidedSelectedProfile.profile_revision)
    && guidedSelectedProfile.readiness?.profile_ready === true
  const guidedSharedOverridesConflict = Boolean(guidedSavedLookIdentity
    && (guidedLook.trim() || guidedInitialWardrobe.trim()))
  const guidedFullAutoInputsReady = Boolean(
    fullAutoEnabled && guidedCharacter && guidedSceneChoice && guidedSavedLookIdentity
    && guidedProfileMatchesLook && assistantConfigured === true && guidedMode === 'automatic'
    && guidedHasWorkflow && guidedRequestId && !guidedSharedOverridesConflict
    && Number.isInteger(Number(guidedPhotoCount)) && Number(guidedPhotoCount) >= 1
    && Number(guidedPhotoCount) <= 500 && guidedBrief.length <= 2000
    && !GUIDED_DIMENSIONS.some(([field]) => currentGuidedVariationPolicy[field].mode === 'fixed'
      && !currentGuidedVariationPolicy[field].value.trim())
    && (!guidedCanvasOverride || (Number.isInteger(guidedCanvasSize.width)
      && Number.isInteger(guidedCanvasSize.height) && guidedCanvasSize.width >= 8
      && guidedCanvasSize.height >= 8 && guidedCanvasSize.width % 8 === 0
      && guidedCanvasSize.height % 8 === 0))
  )
  const fullAutoPreflightBody = guidedFullAutoInputsReady ? {
    character_id: guidedCharacter.id,
    workflow_id: guidedWorkflowOverrideId ? Number(guidedWorkflowOverrideId) : null,
    scene_anchor: exactRevisionTriple(guidedSceneChoice.libraryKey, guidedSceneChoice.revision),
    photo_count: Number(guidedPhotoCount),
    brief: guidedBrief,
    mode: 'automatic',
    variation_policy: currentGuidedVariationPolicy,
    look: guidedLook,
    initial_wardrobe: guidedInitialWardrobe,
    ...(guidedCanvasOverride ? guidedCanvasSize : {}),
    saved_look: guidedSavedLookIdentity,
    full_auto: {
      authorization_version: 1,
      generate_without_pauses: true,
      profile_revision: versionText(guidedSelectedProfile.profile_revision),
      profile_digest: guidedSelectedProfile.profile_digest,
      progression: 'all_stages_full_interval',
      framing_intent: guidedFramingIntent,
    },
  } : null
  const fullAutoPreflightInputSignature = fullAutoPreflightBody
    ? JSON.stringify(fullAutoPreflightBody) : ''

  useLayoutEffect(() => {
    fullAutoInputSignatureRef.current = fullAutoPreflightInputSignature
    fullAutoPreflightRequestRef.current += 1
    setFullAutoQuote((current) => current?.signature === fullAutoPreflightInputSignature ? current : null)
  }, [fullAutoPreflightInputSignature])

  useEffect(() => {
    fullAutoPreflightRequestRef.current += 1
    setFullAutoQuote(null)
    setFullAutoPreflightError('')
    setFullAutoRefreshNotice('')
    if (!fullAutoPreflightBody || guidedOutcomeUnknown || guidedRequestConflict || guidedCreationCompleteRef.current) {
      setFullAutoPreflightLoading(false)
      return undefined
    }
    void fetchFullAutoPreflight(fullAutoPreflightBody, fullAutoPreflightInputSignature)
    return () => { fullAutoPreflightRequestRef.current += 1 }
  }, [fullAutoEnabled, fullAutoPreflightInputSignature, guidedOutcomeUnknown, guidedRequestConflict])

  const canImport = isImportEligible(selectionView, { activeSelectionId, pendingMutation: busy })
  const previewReport = selectionView?.preview ? parsePreviewSummary(selectionView.preview) : null
  const commitReport = selectionView?.commit_result?.report || selectionView?.commit_result || null

  return (
    <>
      {error && <div className="error">{error}</div>}
      {notice && <div className="panel" style={{ marginBottom: 12, borderColor: 'var(--accent)' }}>{notice}</div>}

      {guidedSceneKey && (
        <section className="panel" aria-label="Guided session setup" style={{ marginBottom: 14 }}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <h2 style={{ margin: 0 }}>Start a guided session</h2>
            <button
              type="button"
              disabled={busy || guidedOutcomeUnknown}
              onClick={() => {
                guidedRequestSnapshotRef.current = null
                guidedLooksRequestRef.current += 1
                guidedLookRequestRef.current += 1
                fullAutoPreflightRequestRef.current += 1
                fullAutoInputSignatureRef.current = ''
                setGuidedSceneKey('')
                setGuidedRequestId('')
                setGuidedOutcomeUnknown(false)
                setGuidedRequestConflict(false)
                setFullAutoEnabled(false)
                setFullAutoQuote(null)
                setGuidedSavedLook(null)
                setGuidedSelectedProfile(null)
                setError('')
              }}
            >
              Cancel
            </button>
          </div>
          <div className="grid-form" style={{ marginTop: 12 }}>
            <div>
              <label htmlFor="guided-character">Character</label>
              <select
                id="guided-character"
                value={guidedCharacterId}
                onChange={(event) => {
                  setGuidedCharacterId(event.target.value)
                  setGuidedWorkflowOverrideId('')
                }}
                disabled={guidedLocked}
                required
              >
                <option value="">Choose a character</option>
                {models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="guided-scene">Ready scene</label>
              <select
                id="guided-scene"
                value={guidedSceneKey}
                onChange={(event) => setGuidedSceneKey(event.target.value)}
                disabled={guidedLocked}
                required
              >
                {readyRoomChoices.map((choice) => <option key={choice.key} value={choice.key}>{choice.label}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="guided-photo-count">Photo count</label>
              <input
                id="guided-photo-count"
                type="number"
                min="1"
                max="500"
                step="1"
                required
                value={guidedPhotoCount}
                onChange={(event) => setGuidedPhotoCount(event.target.value)}
                disabled={guidedLocked}
              />
            </div>
            <div>
              <label htmlFor="guided-brief">Brief (optional)</label>
              <textarea
                id="guided-brief"
                maxLength={2000}
                value={guidedBrief}
                onChange={(event) => setGuidedBrief(event.target.value)}
                disabled={guidedLocked}
              />
            </div>
          </div>

          <section aria-label="Optional exact saved look" style={{ marginTop: 14 }}>
            <h3 style={{ margin: '0 0 6px' }}>Optional saved look</h3>
            <p className="muted" style={{ margin: '0 0 8px', fontSize: 12 }}>
              Select an immutable version to apply its appearance and initial outfit at creation. Manual creation keeps its normal review and run controls.
            </p>
            {guidedSavedLooksLoading && <p className="muted" role="status">Loading saved looks…</p>}
            {guidedSavedLooksError && <p className="error" role="alert">{guidedSavedLooksError}</p>}
            <div className="grid-form">
              <label htmlFor="guided-saved-look">Saved look
                <select
                  id="guided-saved-look"
                  value={guidedSavedLookKey}
                  onChange={(event) => selectGuidedSavedLook(event.target.value)}
                  disabled={guidedLocked || guidedSavedLooksLoading}
                >
                  <option value="">No saved look</option>
                  {guidedSavedLooks.map((look) => <option key={look.key} value={look.key}>{look.name} · latest v{versionText(look.version)}</option>)}
                </select>
              </label>
              <div>
                <label htmlFor="guided-saved-look-version">Exact saved-look version</label>
                <div className="row">
                  <input
                    id="guided-saved-look-version"
                    type="text"
                    inputMode="numeric"
                    value={guidedSavedLookVersion}
                    onChange={(event) => {
                      const value = event.target.value
                      guidedLookRequestRef.current += 1
                      setGuidedSavedLookVersion(value)
                      setGuidedSavedLook(null)
                      setGuidedSelectedProfile(null)
                      setGuidedSavedLookLoading(false)
                      setGuidedSavedLookError('')
                    }}
                    disabled={guidedLocked || !guidedSavedLookKey}
                  />
                  <button
                    type="button"
                    onClick={() => void loadGuidedSavedLookVersion(guidedSavedLookKey, guidedSavedLookVersion)}
                    disabled={guidedLocked || !guidedSavedLookKey || !versionText(guidedSavedLookVersion) || guidedSavedLookLoading}
                  >
                    {guidedSavedLookLoading ? 'Loading exact version…' : 'Load exact version'}
                  </button>
                </div>
              </div>
            </div>
            {guidedSavedLookLoading && <p className="muted" role="status">Verifying the immutable saved-look version…</p>}
            {guidedSavedLookError && <p className="error" role="alert">{guidedSavedLookError}</p>}
            {guidedSavedLookIdentity && (
              <div className="muted" aria-label="Loaded exact saved look" style={{ marginTop: 8 }}>
                <p>Loaded {guidedSavedLook.name} · version {guidedSavedLookIdentity.version} · {guidedSavedLookIdentity.content_digest}</p>
                <p>Appearance: {guidedSavedLook.appearance || 'None'}</p>
                <p>Outfit: {guidedSavedLook.outfit?.garments?.map((item) => item.wording).join(' · ') || 'No saved outfit'}</p>
              </div>
            )}
            {guidedSharedOverridesConflict && (
              <p className="error" role="alert">
                Clear the Advanced look and initial-wardrobe overrides before using this exact saved look. They will not be replaced silently.
              </p>
            )}
          </section>

          {assistantConfigured === false && (
            <p className="muted">
              Manual is the default without a configured text assistant. <a href="#/setup">Configure assistant</a>.
            </p>
          )}
          {assistantConfigured === null && <p className="muted">Checking assistant configuration…</p>}
          {guidedCharacter && !guidedCharacterHasDefaultWorkflow && !guidedWorkflowOverrideId && (
            <div className="error" role="alert">
              This character has no default workflow. <a href={`#/model/${guidedCharacter.id}`}>Assign a character default</a> or{' '}
              <button type="button" className="link" onClick={() => setGuidedAdvancedOpen(true)}>
                choose an Advanced workflow override
              </button>.
            </div>
          )}

          <section aria-label="Full Auto setup" style={{ marginTop: 14 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600 }}>
              <input
                id="guided-full-auto"
                type="checkbox"
                checked={fullAutoEnabled}
                onChange={(event) => {
                  const enabled = event.target.checked
                  setFullAutoEnabled(enabled)
                  setFullAutoQuote(null)
                  setFullAutoRefreshNotice('')
                  if (enabled) setGuidedMode('automatic')
                }}
                disabled={guidedLocked}
              />
              Enable opt-in Full Auto
            </label>
            {fullAutoEnabled && (
              <div style={{ display: 'grid', gap: 9, marginTop: 8 }}>
                <p className="muted" style={{ margin: 0 }}>
                  Full Auto requires an exact ready saved-look profile and Automatic mode. It keeps manual creation available when a look is ineligible.
                </p>
                {assistantConfigured === false && <p className="error" role="alert">Full Auto needs a configured text assistant. <a href="#/setup">Configure assistant</a>; manual creation remains available.</p>}
                {guidedSavedLookKey && !guidedSavedLookIdentity && <p className="muted" role="status">Load the selected exact saved-look version to continue.</p>}
                {!guidedSavedLookKey && <p className="muted" role="status">Choose an exact saved-look version to continue.</p>}
                {guidedSavedLookIdentity && (
                  <div>
                    <AutomationProfileSelector
                      key={`${guidedSavedLookIdentity.look_key}:${guidedSavedLookIdentity.version}:${guidedSavedLookIdentity.content_digest}`}
                      look={guidedSavedLook}
                      active={fullAutoEnabled}
                      disabled={guidedLocked || guidedSavedLookLoading}
                      label="Exact automation profile revision"
                      onProfileChange={setGuidedSelectedProfile}
                    />
                    {guidedSelectedProfile && <p className="muted">Selected profile revision {versionText(guidedSelectedProfile.profile_revision)} · {guidedSelectedProfile.profile_digest}</p>}
                  </div>
                )}
                <fieldset disabled={guidedLocked} style={{ border: 0, padding: 0, margin: 0 }}>
                  <legend>Framing intent</legend>
                  <label style={{ marginRight: 16 }}>
                    <input type="radio" name="guided-full-auto-framing" value="vary" checked={guidedFramingIntent === 'vary'} onChange={() => setGuidedFramingIntent('vary')} />
                    Vary framing
                  </label>
                  <label>
                    <input type="radio" name="guided-full-auto-framing" value="full_body" checked={guidedFramingIntent === 'full_body'} onChange={() => setGuidedFramingIntent('full_body')} />
                    Full body, head to toe
                  </label>
                </fieldset>
                {guidedSharedOverridesConflict && <p className="error" role="alert">Clear both Advanced shared overrides before requesting Full Auto consent.</p>}
                {fullAutoPreflightLoading && <p className="muted" role="status">Refreshing the read-only Full Auto consent summary…</p>}
                {fullAutoPreflightError && <p className="error" role="alert">{fullAutoPreflightError}</p>}
                {fullAutoRefreshNotice && <p className="panel" role="status">{fullAutoRefreshNotice}</p>}
                {fullAutoQuote && fullAutoQuote.signature === fullAutoPreflightInputSignature && (
                  <div className="panel" aria-label="Full Auto consent summary">
                    <h3 style={{ marginTop: 0 }}>Review before Generate without pauses</h3>
                    <p>Saved look: {fullAutoQuote.summary.saved_look.look_key} · version {versionText(fullAutoQuote.summary.saved_look.version)} · {fullAutoQuote.summary.saved_look.content_digest}</p>
                    <p>Automation profile: revision {versionText(fullAutoQuote.summary.automation_profile.revision)} · {fullAutoQuote.summary.automation_profile.digest}</p>
                    <p>Room: {fullAutoQuote.summary.room.library_key} / {fullAutoQuote.summary.room.source_id} · {fullAutoQuote.summary.room.content_digest}</p>
                    <p>Photos: {fullAutoQuote.summary.photo_count} · all stages: {fullAutoQuote.summary.progression.stages.length}</p>
                    <ol aria-label="Authorized wardrobe stage timeline">
                      {fullAutoQuote.summary.progression.stages.map((stage) => (
                        <li key={stage.stage_index}>
                          Stage {stage.stage_index + 1}: {stage.first_take_id} to {stage.last_take_id} ({stage.take_count} takes) — {fullAutoWardrobeAtStage(guidedSavedLook, stage.stage_index)} — {stage.coverage}
                        </li>
                      ))}
                    </ol>
                    <p>Effective workflow ({fullAutoPreflightBody.workflow_id === null ? 'character default' : 'selected override'}): {fullAutoQuote.summary.workflow.workflow_id} · {fullAutoQuote.summary.workflow.kind} · graph {fullAutoQuote.summary.workflow.graph_digest.slice(0, 12)} · mapping {fullAutoQuote.summary.workflow.node_map_digest.slice(0, 12)}</p>
                    <p>Provider: {fullAutoQuote.summary.provider.model} · endpoint fingerprint {fullAutoQuote.summary.provider.endpoint_identity}</p>
                    <p>Canvas: {fullAutoQuote.summary.canvas.width} × {fullAutoQuote.summary.canvas.height} · framing: {fullAutoQuote.summary.framing_intent === 'full_body' ? 'Full body, head to toe, both feet visible, with space above the head and below the feet.' : 'Vary framing'}</p>
                    <p>Effective framing policy: {JSON.stringify(fullAutoQuote.summary.effective_framing)} · policy {fullAutoQuote.summary.policy_version} · coverage compiler {fullAutoQuote.summary.coverage_compiler_version}</p>
                    <p>Allowance: {fullAutoQuote.summary.limits.assistant_calls_per_take} assistant calls and {fullAutoQuote.summary.limits.image_attempts_per_take} initial image attempt per take; {fullAutoQuote.summary.limits.assistant_calls_total} assistant calls and {fullAutoQuote.summary.limits.image_attempts_total} image attempts total. Calls are counts, not a monetary cap; a compatibility fallback can use the second call. Failed images are not automatically rerolled.</p>
                    <p>Authorization expires (UTC): {fullAutoQuote.summary.authorization_expires_at}</p>
                    <p>One click authorizes automatic preparation, configured policy review and approval, submission, and one initial execution attempt per take. Policy review is automated and does not guarantee visual results.</p>
                    <p>After creation, the backend continues if this browser closes and resumes unchanged work after a backend restart. Canvas and framing guide composition but do not guarantee visible feet, anatomy, or exact garment reproduction.</p>
                    <button
                      type="button"
                      className="primary"
                      disabled={guidedLocked || fullAutoPreflightLoading || !guidedFullAutoInputsReady || fullAutoQuote.signature !== fullAutoPreflightInputSignature}
                      onClick={submitFullAutoCreation}
                    >
                      {busy ? 'Starting Full Auto…' : 'Generate without pauses'}
                    </button>
                  </div>
                )}
              </div>
            )}
          </section>

          <details open={guidedAdvancedOpen} onToggle={(event) => setGuidedAdvancedOpen(event.currentTarget.open)}>
            <summary>Advanced</summary>
            <div className="grid-form" style={{ marginTop: 10 }}>
              <div>
                <label htmlFor="guided-mode">Authoring mode</label>
                <select
                  id="guided-mode"
                  value={guidedMode}
                  onChange={(event) => setGuidedMode(event.target.value)}
                  disabled={assistantConfigured === null || guidedLocked || fullAutoEnabled}
                >
                  {!guidedMode && <option value="" disabled>Checking assistant configuration…</option>}
                  <option value="automatic">Automatic</option>
                  <option value="manual">Manual</option>
                </select>
              </div>
              <div>
                <label htmlFor="guided-workflow-override">Workflow override</label>
                <select
                  id="guided-workflow-override"
                  value={guidedWorkflowOverrideId}
                  onChange={(event) => setGuidedWorkflowOverrideId(event.target.value)}
                  disabled={guidedLocked}
                >
                  <option value="">{guidedCharacterHasDefaultWorkflow ? 'Use character default' : 'Choose an override'}</option>
                  {workflows.map((workflow) => <option key={workflow.id} value={workflow.id}>{workflow.name}</option>)}
                </select>
                <p className="muted" style={{ margin: '4px 0 0' }}>
                  {guidedCharacterHasDefaultWorkflow
                    ? 'The selected character default is used unless you choose an override.'
                    : guidedWorkflowOverrideId
                      ? 'The selected workflow override will be used.'
                      : 'No character default is assigned. Choose an override or assign a default on the character page.'}
                </p>
                {workflows.length === 0 && <p className="muted">No workflows are available. <a href="#/workflows">Open Workflows</a>.</p>}
              </div>
            </div>
            <h3 style={{ margin: '14px 0 6px' }}>Variation locks</h3>
            <p className="muted" style={{ margin: '0 0 8px', fontSize: 12 }}>
              All four choices vary by default. Fix a choice only when every take should use the same value.
            </p>
            <div className="grid-form">
              {GUIDED_DIMENSIONS.map(([field, label]) => {
                const setting = guidedVariationPolicy[field] || { mode: 'vary', value: '' }
                return (
                  <div key={field}>
                    <label htmlFor={`guided-${field}-mode`}>{label}</label>
                    <select
                      id={`guided-${field}-mode`}
                      value={setting.mode}
                      onChange={(event) => setGuidedVariationPolicy((previous) => ({
                        ...previous,
                        [field]: { ...setting, mode: event.target.value },
                      }))}
                      disabled={guidedLocked}
                    >
                      <option value="vary">Vary</option>
                      <option value="fixed">Fixed</option>
                    </select>
                    {setting.mode === 'fixed' && (
                      <input
                        aria-label={`Fixed ${label.toLowerCase()}`}
                        value={setting.value}
                        onChange={(event) => setGuidedVariationPolicy((previous) => ({
                          ...previous,
                          [field]: { ...setting, value: event.target.value },
                        }))}
                        placeholder={`Fixed ${label.toLowerCase()} value`}
                        disabled={guidedLocked}
                      />
                    )}
                  </div>
                )
              })}
            </div>
            <h3 style={{ margin: '14px 0 6px' }}>Session canvas</h3>
            <p className="muted" style={{ margin: '0 0 8px', fontSize: 12 }}>
              By default, the session inherits the character's current canvas. A canvas override applies only to this session.
            </p>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                id="guided-canvas-override"
                type="checkbox"
                checked={guidedCanvasOverride}
                onChange={(event) => setGuidedCanvasOverride(event.target.checked)}
                disabled={guidedLocked}
              />
              Override the canvas for this session
            </label>
            {guidedCanvasOverride && (
              <fieldset
                aria-label="Canvas override size"
                disabled={guidedLocked}
                style={{ border: 0, padding: 0, margin: '10px 0 0' }}
              >
                <div className="grid-form">
                  <CanvasSize
                    width={guidedCanvasSize.width}
                    height={guidedCanvasSize.height}
                    onChange={(width, height) => setGuidedCanvasSize({ width, height })}
                  />
                </div>
                <p className="muted" style={{ margin: '4px 0 0', fontSize: 12 }}>
                  The custom starting size is 768×1360. Character defaults are unchanged.
                </p>
              </fieldset>
            )}
            <h3 style={{ margin: '14px 0 6px' }}>Shared overrides</h3>
            <div className="grid-form">
              <div>
                <label htmlFor="guided-look">Look override</label>
                <textarea
                  id="guided-look"
                  rows={3}
                  value={guidedLook}
                  onChange={(event) => setGuidedLook(event.target.value)}
                  placeholder="Optional appearance, place, or lighting constraint"
                  disabled={guidedLocked}
                />
              </div>
              <div>
                <label htmlFor="guided-initial-wardrobe">Initial wardrobe override</label>
                <textarea
                  id="guided-initial-wardrobe"
                  rows={3}
                  value={guidedInitialWardrobe}
                  onChange={(event) => setGuidedInitialWardrobe(event.target.value)}
                  placeholder="Optional clothing constraint for the first take"
                  disabled={guidedLocked}
                />
              </div>
            </div>
            {guidedMode === 'automatic' && assistantConfigured === false && (
              <p className="muted">This automatic draft is valid, but synthesis is unavailable until a text assistant is configured.</p>
            )}
          </details>

          {guidedOutcomeUnknown && (
            <div className="error" role="alert">
              The creation result is unknown. Retry the same request to retrieve its stored result.
              <button type="button" disabled={busy} onClick={retryGuidedCreation}>
                {busy ? 'Retrying…' : 'Retry creation'}
              </button>
            </div>
          )}
          {guidedRequestConflict && (
            <div className="error" role="alert">
              This request ID is already bound to different creation details. Start a new attempt to use a fresh request ID.
              <button type="button" disabled={busy} onClick={startGuidedCreationAttempt}>
                Start a new creation attempt
              </button>
            </div>
          )}
          <div className="row" style={{ justifyContent: 'flex-end', marginTop: 12 }}>
            <button
              type="button"
              className="primary"
              disabled={guidedLocked || !guidedCharacter || !guidedHasWorkflow || !guidedMode || assistantConfigured === null
                || (guidedSavedLookKey && !guidedSavedLookIdentity)}
              onClick={submitGuidedCreation}
            >
              {busy ? 'Creating…' : fullAutoEnabled ? 'Create manual/assisted session' : 'Create guided session'}
            </button>
          </div>
        </section>
      )}

      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
        <div>
          <h1>Resources</h1>
          <p className="muted" style={{ margin: 0 }}>
            Inspect local resource libraries, field roles and readiness, run atomic import previews, or start resource-v1 sessions.
          </p>
        </div>
        <div className="row">
          <button
            className={`chip ${tab === 'inventory' ? 'on' : ''}`}
            onClick={() => setTab('inventory')}
          >
            Inventory ({libraries.reduce((acc, l) => acc + (l.revision_count || 0), 0)} revisions)
          </button>
          <button
            className={`chip ${tab === 'import' ? 'on' : ''}`}
            onClick={() => setTab('import')}
          >
            Import Preview
          </button>
        </div>
      </div>

      {tab === 'inventory' && (
        <>
          <div className="panel" style={{ marginBottom: 14 }}>
            <div className="row" style={{ alignItems: 'flex-end' }}>
              <div style={{ flex: 2, minWidth: 200 }}>
                <label>Search resources</label>
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search resources..."
                />
              </div>
              <div style={{ flex: 1, minWidth: 150 }}>
                <label>Category (Kind)</label>
                <select value={category} onChange={(e) => setCategory(e.target.value)}>
                  <option value="all">All categories</option>
                  {categories.map((cat) => (
                    <option key={cat} value={cat}>{cat}</option>
                  ))}
                </select>
              </div>
              <div style={{ flex: 1, minWidth: 180 }}>
                <label>Active Character Model</label>
                <select
                  value={selectedModelId}
                  onChange={(e) => setSelectedModelId(e.target.value)}
                  title="Session drafts bind strictly to this model. Resource identity suggestions cannot override it."
                >
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>{m.name}</option>
                  ))}
                  {models.length === 0 && <option value="">No models available</option>}
                </select>
              </div>
              <div>
                <button onClick={reloadLibraries} title="Refresh local inventory">
                  Refresh
                </button>
              </div>
            </div>
          </div>

          {filteredLibraries.length === 0 ? (
            <div className="panel" style={{ textAlign: 'center', padding: '32px 16px' }}>
              <p className="muted" style={{ margin: 0 }}>
                {libraries.length === 0
                  ? 'No resource libraries imported yet. Switch to the Import Preview tab to import resources.'
                  : 'No resources match your filter.'}
              </p>
            </div>
          ) : (
            filteredLibraries.map((lib) => (
              <div key={lib.library_key} className="panel" style={{ marginBottom: 16 }}>
                <div className="row" style={{ justifyContent: 'space-between', marginBottom: 10 }}>
                  <div>
                    <h3 style={{ margin: 0, display: 'inline-block' }}>
                      {lib.display_name || lib.library_key}
                    </h3>
                    <span className="muted" style={{ marginLeft: 10 }}>
                      key: <code>{lib.library_key}</code> · kind: <b>{lib.kind}</b>
                    </span>
                  </div>
                  <span className="muted">
                    {lib.revisions?.length ?? 0} revision(s) · {lib.auxiliary?.length ?? 0} auxiliary
                  </span>
                </div>

                {/* Revisions table */}
                {(lib.revisions || []).length > 0 && (
                  <table style={{ marginBottom: 12 }}>
                    <thead>
                      <tr>
                        <th>Source ID</th>
                        <th>Content Digest</th>
                        <th>Readiness</th>
                        <th>Created</th>
                        <th style={{ textAlign: 'right' }}>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {lib.revisions.map((rev) => {
                        const readiness = checkReadiness(rev, {
                          kind: lib.kind,
                          hasModelSelected: Boolean(selectedModelId),
                          libraryKey: lib.library_key,
                          importedIdentities,
                        })
                        const detailKey = `${lib.library_key}:${rev.source_id}:${rev.content_digest}`
                        const isExpanded = !!expandedDetails[detailKey]
                        const detail = expandedDetails[detailKey]

                        return (
                          <React.Fragment key={rev.content_digest}>
                            <tr>
                              <td>
                                <b>{rev.source_id}</b>
                                {rev.translation && Object.values(rev.translation).some((v) => typeof v === 'string' && v.trim()) && (
                                  <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
                                    {Object.values(rev.translation).filter((v) => typeof v === 'string' && v.trim()).join(' · ')}
                                  </div>
                                )}
                              </td>
                              <td style={{ fontFamily: 'monospace', fontSize: 12 }} title={rev.content_digest}>
                                {rev.content_digest.slice(0, 12)}…
                              </td>
                              <td>
                                <div className="row" style={{ alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                                  {readiness.isImported && (
                                    <span className="badge chip" style={{ fontWeight: 600 }}>Imported</span>
                                  )}
                                  <span className={`badge ${readiness.readiness === 'ready' ? 'ready' : readiness.readiness === 'needs_source_correction' ? 'bad' : 'pending'}`}>
                                    {readiness.label}
                                  </span>
                                </div>
                                {!readiness.isReady && (
                                  <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
                                    {readiness.reasons[0]}
                                  </div>
                                )}
                                {readiness.primaryAction?.id === 'use_advanced_editor' && (
                                  <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                                    Fused descriptions can combine location, camera, pose, and wardrobe, so simple automatic scene choices use ready structured rooms. The advanced editor keeps this full description intact.
                                  </div>
                                )}
                              </td>
                              <td className="muted" style={{ fontSize: 12 }}>
                                {rev.created_at ? rev.created_at.slice(0, 16).replace('T', ' ') : '—'}
                              </td>
                              <td style={{ textAlign: 'right' }}>
                                <div className="row" style={{ justifyContent: 'flex-end', gap: 6 }}>
                                  {readiness.readiness === 'ready' && (
                                    <>
                                      <button
                                        className="icon"
                                        onClick={() => toggleDetail(lib.library_key, rev)}
                                        title="Inspect field roles and coverage"
                                      >
                                        {detailLoading[detailKey] ? '…' : isExpanded ? 'Hide' : 'Inspect'}
                                      </button>
                                      {readiness.primaryAction?.id === 'use_advanced_editor' ? (
                                        <>
                                          <button
                                            className="primary"
                                            disabled={busy || !selectedModelId || Boolean(guidedSceneKey) || guidedOutcomeUnknown}
                                            title={
                                              !selectedModelId
                                                ? 'Select a model to open the advanced editor'
                                                : 'Open an expert resource-v1 draft with this exact fused revision'
                                            }
                                            onClick={() => startSessionWithRevision(lib.library_key, rev)}
                                          >
                                            {readiness.primaryAction.label}
                                          </button>
                                          <button
                                            className="icon"
                                            onClick={chooseStructuredScene}
                                            title="Show structured room resources and clear the current search"
                                          >
                                            {readiness.secondaryAction.label}
                                          </button>
                                        </>
                                      ) : (
                                        <button
                                          className="primary"
                                          disabled={busy || !selectedModelId || Boolean(guidedSceneKey) || guidedOutcomeUnknown}
                                          title={
                                            !selectedModelId
                                              ? 'Select a model to create a session'
                                              : 'Create a resource-v1 session draft with this exact revision'
                                          }
                                          onClick={() => lib.kind === 'rooms'
                                            ? openGuidedCreation(lib.library_key, rev)
                                            : startSessionWithRevision(lib.library_key, rev)}
                                        >
                                          {readiness.primaryAction?.label || 'Create session'}
                                        </button>
                                      )}
                                    </>
                                  )}

                                  {readiness.readiness === 'needs_translation' && (
                                    <>
                                      <button
                                        className="icon"
                                        onClick={() => toggleDetail(lib.library_key, rev)}
                                        title="Inspect field roles and coverage"
                                      >
                                        {detailLoading[detailKey] ? '…' : isExpanded ? 'Hide' : 'Inspect'}
                                      </button>
                                      <button
                                        className="primary"
                                        onClick={() => handleTranslateRevision(lib.library_key)}
                                        title="Load and edit translations for this library"
                                      >
                                        Translate
                                      </button>
                                    </>
                                  )}

                                  {readiness.readiness === 'needs_source_correction' && (
                                    <>
                                      <button
                                        className="icon"
                                        onClick={() => setTab('import')}
                                        title="Switch to Import to provide corrected source files"
                                      >
                                        Re-import
                                      </button>
                                      <button
                                        className="primary"
                                        onClick={() => toggleDetail(lib.library_key, rev)}
                                        title="Inspect source errors and diagnostic fields"
                                      >
                                        {detailLoading[detailKey] ? '…' : isExpanded ? 'Hide' : 'Inspect source'}
                                      </button>
                                    </>
                                  )}
                                </div>
                              </td>
                            </tr>

                            {/* Expanded Inspection Drawer */}
                            {isExpanded && detail && (
                              <tr>
                                <td colSpan={5} style={{ background: 'var(--panel-2)', padding: 12 }}>
                                  <h4 style={{ margin: '0 0 8px' }}>Field Roles & Readiness Coverage</h4>
                                  <p className="muted" style={{ margin: '0 0 10px' }}>
                                    Identity: <code>{detail.library_key}</code> · <code>{detail.source_id}</code> · <code>{detail.content_digest}</code>
                                  </p>

                                  {detail.readiness?.coverage?.sidecar_error && (
                                    <div
                                      className="alert error"
                                      style={{
                                        margin: '0 0 12px',
                                        padding: '8px 12px',
                                        background: 'rgba(239, 68, 68, 0.12)',
                                        border: '1px solid var(--bad)',
                                        borderRadius: 4,
                                        color: 'var(--bad)',
                                        fontSize: 12,
                                      }}
                                    >
                                      <b>Translation Sidecar Error:</b> {detail.readiness.coverage.sidecar_error}
                                    </div>
                                  )}

                                  {detail.readiness?.diagnostics && detail.readiness.diagnostics.length > 0 && (
                                    <div
                                      className="alert error"
                                      style={{
                                        margin: '0 0 12px',
                                        padding: '8px 12px',
                                        background: 'rgba(239, 68, 68, 0.12)',
                                        border: '1px solid var(--bad)',
                                        borderRadius: 4,
                                        color: 'var(--bad)',
                                        fontSize: 12,
                                      }}
                                    >
                                      <b>Source Diagnostics:</b>
                                      <ul style={{ margin: '4px 0 0', paddingLeft: 20 }}>
                                        {detail.readiness.diagnostics.map((d, i) => (
                                          <li key={i}>
                                            {d.field ? <code>{d.field}</code> : null} {d.code}: {d.message}
                                          </li>
                                        ))}
                                      </ul>
                                    </div>
                                  )}

                                  {detail.readiness?.coverage?.unmapped_fields && detail.readiness.coverage.unmapped_fields.length > 0 && (
                                    <div
                                      className="alert error"
                                      style={{
                                        margin: '0 0 12px',
                                        padding: '8px 12px',
                                        background: 'rgba(239, 68, 68, 0.12)',
                                        border: '1px solid var(--bad)',
                                        borderRadius: 4,
                                        color: 'var(--bad)',
                                        fontSize: 12,
                                      }}
                                    >
                                      <b>Unmapped Source Fields:</b> {detail.readiness.coverage.unmapped_fields.join(', ')}
                                    </div>
                                  )}

                                  <table style={{ marginBottom: 12, background: 'var(--panel)' }}>
                                    <thead>
                                      <tr>
                                        <th>Field</th>
                                        <th>Role</th>
                                        <th>Translated</th>
                                        <th>Reason / Note</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {(detail.readiness?.field_readiness || []).map((f) => (
                                        <tr key={f.name}>
                                          <td><code>{f.name}</code></td>
                                          <td>
                                            <span className="chip" style={{ fontSize: 11 }}>
                                              {normalizeFieldRole(f.role)}
                                            </span>
                                          </td>
                                          <td>
                                            {f.translated ? (
                                              <span style={{ color: 'var(--good)' }}>Yes</span>
                                            ) : (
                                              <span style={{ color: 'var(--bad)' }}>No</span>
                                            )}
                                          </td>
                                          <td className="muted">{f.reason || '—'}</td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>

                                  {/* Optional raw payload and translation inspection */}
                                  <div className="row" style={{ gap: 16 }}>
                                    <div style={{ flex: 1 }}>
                                      <h5 style={{ margin: '4px 0', color: 'var(--muted)' }}>Original Payload</h5>
                                      <pre style={{ margin: 0, padding: 8, background: 'var(--bg)', borderRadius: 4, maxHeight: 150, overflow: 'auto', fontSize: 11 }}>
                                        {JSON.stringify(detail.payload || {}, null, 2)}
                                      </pre>
                                    </div>
                                    <div style={{ flex: 1 }}>
                                      <h5 style={{ margin: '4px 0', color: 'var(--muted)' }}>Translation & Stored Coverage vs Live Readiness</h5>
                                      <pre style={{ margin: 0, padding: 8, background: 'var(--bg)', borderRadius: 4, maxHeight: 150, overflow: 'auto', fontSize: 11 }}>
                                        {JSON.stringify({
                                          translation: detail.translation,
                                          stored_coverage: detail.coverage,
                                          live_readiness: detail.readiness,
                                        }, null, 2)}
                                      </pre>
                                    </div>
                                  </div>
                                </td>
                              </tr>
                            )}
                          </React.Fragment>
                        )
                      })}
                    </tbody>
                  </table>
                )}

                {/* Auxiliary resources if any */}
                {(lib.auxiliary || []).length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <h4 style={{ margin: '4px 0 6px', color: 'var(--muted)', fontSize: 12 }}>
                      Auxiliary Resources ({lib.auxiliary.length})
                    </h4>
                    <table>
                      <thead>
                        <tr>
                          <th>Kind</th>
                          <th>Digest</th>
                          <th>Role</th>
                          <th>Created</th>
                        </tr>
                      </thead>
                      <tbody>
                        {lib.auxiliary.map((aux) => (
                          <tr key={aux.auxiliary_id}>
                            <td><code>{aux.kind}</code></td>
                            <td style={{ fontFamily: 'monospace', fontSize: 12 }}>
                              {aux.content_digest}
                            </td>
                            <td>
                              <span className="chip" style={{ fontSize: 11 }}>
                                auxiliary data
                              </span>
                            </td>
                            <td className="muted" style={{ fontSize: 12 }}>
                              {aux.created_at ? aux.created_at.slice(0, 16).replace('T', ' ') : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* Translation Mapping section */}
                <div id={`translation-mapping-${lib.library_key}`} style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
                  <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <h4 style={{ margin: 0 }}>Translation Mapping</h4>
                    <div className="row">
                      <span className="badge ready" style={{ marginRight: 6 }}>
                        {(lib.revisions || []).filter((r) => r.readiness?.status === 'ready').length} Ready
                      </span>
                      <span className="badge pending">
                        {(lib.revisions || []).filter((r) => r.readiness?.status !== 'ready').length} Pending
                      </span>
                    </div>
                  </div>

                  {(() => {
                    const tState = translationState[lib.library_key] || {}
                    const manualBuild = buildTranslationMapFromRows(tState.manualRows || [])
                    const suggestSelectedCount = (tState.manualRows || []).filter(
                      (r) => r.suggest_selected && isRowEligibleForProposal(r)
                    ).length
                    const manualCanPreview = (
                      !tState.manualBusy
                      && manualBuild.errors.length === 0
                      && Object.keys(manualBuild.translationMap).length > 0
                    )
                    return (
                      <>
                        <div className="panel" style={{ marginBottom: 10, background: 'var(--card-bg)' }}>
                          <div className="row" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
                            <div>
                              <b>Manual source-backed translations</b>
                              <div className="muted" style={{ fontSize: 12 }}>
                                Edit safe source rows, then use canonical bulk preview and attested apply.
                              </div>
                            </div>
                            <button
                              onClick={() => handleLoadTranslationRows(lib.library_key)}
                              disabled={tState.manualBusy}
                            >
                              {tState.manualBusy ? 'Working…' : 'Load editable rows'}
                            </button>
                          </div>

                          {(tState.manualDiagnostics || []).map((diagnostic, index) => (
                            <div className="error" key={`${diagnostic.code}-${index}`} style={{ fontSize: 12, marginBottom: 6 }}>
                              {diagnostic.code}: {diagnostic.field || 'payload'} — {diagnostic.message}
                            </div>
                          ))}
                          {manualBuild.errors.map((item, index) => (
                            <div className="error" key={`${item.code}-${item.source}-${index}`} style={{ fontSize: 12, marginBottom: 6 }}>
                              {item.code}: source {JSON.stringify(item.source)}
                            </div>
                          ))}
                          {tState.manualError && <div className="error" style={{ fontSize: 12, marginBottom: 8 }}>{tState.manualError}</div>}
                          {tState.manualNotice && <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>{tState.manualNotice}</div>}

                          {(tState.manualRows || []).length > 0 && (
                            <div className="row" style={{ gap: 8, alignItems: 'center', marginBottom: 8 }}>
                              <button
                                onClick={() => handleSuggestTranslations(lib.library_key)}
                                disabled={
                                  tState.manualBusy
                                  || tState.proposalBusy
                                  || suggestSelectedCount === 0
                                  || suggestSelectedCount > 20
                                }
                              >
                                {tState.proposalBusy ? 'Suggesting…' : 'Suggest translations'}
                              </button>
                              {suggestSelectedCount > 0 && (
                                <span className="muted" style={{ fontSize: 12 }}>
                                  {suggestSelectedCount > 20
                                    ? `${suggestSelectedCount} selected (max 20)`
                                    : `${suggestSelectedCount} selected`}
                                </span>
                              )}
                              {tState.preProposalRows && (
                                <button
                                  onClick={() => handleDiscardSuggestions(lib.library_key)}
                                  disabled={tState.manualBusy || tState.proposalBusy}
                                >
                                  Discard suggestions
                                </button>
                              )}
                            </div>
                          )}

                          {(tState.manualRows || []).length > 0 && (
                            <table style={{ marginBottom: 8 }}>
                              <thead>
                                <tr>
                                  <th>Use</th>
                                  <th>Suggest</th>
                                  <th>Source</th>
                                  <th>Field / Revision</th>
                                  <th>Translation</th>
                                </tr>
                              </thead>
                              <tbody>
                                {tState.manualRows.map((row, index) => (
                                  <tr key={`${row.revision.source_id}-${row.revision.content_digest}-${row.field}-${row.list_index ?? 'scalar'}`}>
                                    <td>
                                      <input
                                        type="checkbox"
                                        aria-label={`Use translation for ${row.source_value}`}
                                        checked={Boolean(row.emit)}
                                        onChange={(e) => updateManualRow(lib.library_key, index, { emit: e.target.checked })}
                                      />
                                    </td>
                                    <td>
                                      {isRowEligibleForProposal(row) ? (
                                        <input
                                          type="checkbox"
                                          aria-label={`Suggest translation for ${row.source_value}`}
                                          checked={Boolean(row.suggest_selected)}
                                          onChange={(e) => updateManualRow(lib.library_key, index, { suggest_selected: e.target.checked })}
                                        />
                                      ) : (
                                        <span className="muted" style={{ fontSize: 11 }}>—</span>
                                      )}
                                    </td>
                                    <td>
                                      <code>{row.source_value}</code>
                                      {row.required && <span className="muted"> · required</span>}
                                      {row.source_shape === 'list' && <span className="muted"> · item {row.list_index + 1}</span>}
                                    </td>
                                    <td style={{ fontSize: 12 }}>
                                      <code>{row.field}</code>
                                      <div className="muted">{row.revision.source_id} · {row.revision.content_digest.slice(0, 12)}…</div>
                                    </td>
                                    <td>
                                      <input
                                        aria-label={`Translation for ${row.source_value}`}
                                        value={row.translation}
                                        onChange={(e) => updateManualRow(lib.library_key, index, {
                                          translation: e.target.value,
                                          emit: row.emit || Boolean(e.target.value.trim()),
                                        })}
                                      />
                                      {row.current_translation != null && (
                                        <div className="muted" style={{ fontSize: 11 }}>Existing translation</div>
                                      )}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}

                          {(tState.manualRows || []).length > 0 && (
                            <div className="row" style={{ justifyContent: 'flex-end' }}>
                              <button
                                onClick={() => handlePreviewManualTranslations(lib.library_key)}
                                disabled={!manualCanPreview}
                              >
                                Preview manual translations
                              </button>
                              <button
                                className="primary"
                                onClick={() => handleApplyManualTranslations(lib.library_key)}
                                disabled={
                                  tState.manualBusy
                                  || !tState.manualPreview?.attestationToken
                                  || JSON.stringify(manualBuild.translationMap) !== tState.manualSnapshot
                                  || manualBuild.errors.length > 0
                                }
                              >
                                Confirm & Apply manual translations
                              </button>
                            </div>
                          )}

                          {tState.manualPreview && (
                            <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
                              Preview: {tState.manualPreview.matchedRevisions} of {tState.manualPreview.totalRevisions} revisions matched; {tState.manualPreview.wouldUpdate} would update.
                            </div>
                          )}
                        </div>

                        <div className="row" style={{ alignItems: 'flex-end', gap: 8, marginBottom: 8 }}>
                          <div style={{ flex: 1 }}>
                            <label style={{ fontSize: 12 }}>Translation Map Path (beside source material or absolute JSON path)</label>
                            <input
                              value={tState.mapPath || ''}
                              onChange={(e) => handleMapPathChange(lib.library_key, e.target.value)}
                              placeholder="e.g. /path/to/translations.json or relative/path.json"
                            />
                          </div>
                          <button
                            onClick={() => handlePreviewTranslations(lib.library_key)}
                            disabled={Boolean(tState.mapOperation) || !(tState.mapPath || '').trim()}
                            title="Preview matching without writing database state"
                          >
                            {tState.mapOperation === 'preview' ? 'Working…' : 'Preview Translations'}
                          </button>
                        </div>

                        {tState.error && <div className="error" style={{ fontSize: 12, marginBottom: 8 }}>{tState.error}</div>}
                        {tState.notice && <div className="panel" style={{ fontSize: 12, marginBottom: 8, borderColor: 'var(--accent)' }}>{tState.notice}</div>}

                        {tState.preview && (
                          <div className="panel" style={{ background: 'var(--card-bg)', marginTop: 8, marginBottom: 8 }}>
                            <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                              <div style={{ fontSize: 13 }}>
                                <b>Preview Summary:</b> {tState.preview.matchedRevisions} of {tState.preview.totalRevisions} revisions matched
                                {' · '}
                                <span style={{ color: 'var(--accent)' }}>{tState.preview.wouldUpdate} would update</span>
                                {' · '}
                                <span style={{ color: 'var(--success, #4caf50)' }}>{tState.preview.wouldBeReady} would be ready</span>
                                {' · '}
                                <span style={{ color: 'var(--warning, #ff9800)' }}>{tState.preview.wouldRemainPending} would remain pending</span>
                                {tState.preview.unmatchedEntries > 0 && (
                                  <span className="muted"> · {tState.preview.unmatchedEntries} unmatched entries</span>
                                )}
                              </div>
                              <button
                                className="primary"
                                onClick={() => handleApplyTranslations(lib.library_key)}
                                disabled={Boolean(tState.mapOperation)}
                                title="Atomically apply translations to library sidecars"
                              >
                                {tState.mapOperation === 'apply' ? 'Applying…' : 'Confirm & Apply Translations'}
                              </button>
                            </div>
                          </div>
                        )}
                      </>
                    )
                  })()}
                </div>
              </div>
            ))
          )}
        </>
      )}

      {tab === 'import' && (
        <>
          <div className="panel" style={{ marginBottom: 16 }}>
            <h2>Import Resources</h2>
            <p className="muted">
              Select source JSON resource files directly from your browser. The server validates declarations, verifies fingerprints, preserves exact bytes, and generates a preview before committing.
            </p>

            {selectionView?.cleanup_warning && (
              <div
                className="alert warning"
                style={{
                  marginBottom: 14,
                  padding: '10px 14px',
                  background: 'rgba(234, 179, 8, 0.15)',
                  border: '1px solid var(--warning, #eab308)',
                  borderRadius: 4,
                  color: 'var(--warning-text, #ca8a04)',
                }}
              >
                <div>
                  <b>Temporary Cleanup Notice:</b> {selectionView.cleanup_warning}
                </div>
                <button
                  type="button"
                  onClick={handleRetryCleanup}
                  disabled={checkingCleanup}
                  style={{ marginTop: 8 }}
                >
                  {checkingCleanup ? 'Checking cleanup…' : 'Retry cleanup status'}
                </button>
              </div>
            )}

            {selectionView && (
              <div className="row" style={{ alignItems: 'center', gap: 10, marginBottom: 14, fontSize: 13 }}>
                <span><b>Selection:</b> <code>{selectionView.selection_id}</code></span>
                <span><b>Revision:</b> {selectionView.selection_revision}</span>
                <span className={`badge ${selectionView.state}`}>{selectionView.state}</span>
                {selectionView.state !== 'open' && (
                  <span className="muted" style={{ fontStyle: 'italic' }}>
                    {selectionView.state === 'committed' && 'Resources successfully committed.'}
                    {selectionView.state === 'cancelled' && 'Selection cancelled. Mutation and import are disabled.'}
                    {selectionView.state === 'expired' && 'Selection expired. Start a new selection to import resources.'}
                    {selectionView.state === 'committing' && 'Commit is actively processing...'}
                  </span>
                )}
              </div>
            )}

            <div className="row" style={{ alignItems: 'center', gap: 10, marginBottom: 8 }}>
              <input
                type="file"
                multiple
                accept=".json"
                id="resource-file-input"
                onChange={handleFilesChosen}
                disabled={busy || (selectionView && selectionView.state !== 'open')}
              />
              <button
                className="primary"
                onClick={() => uploadSelectedFiles()}
                disabled={busy || selectedFiles.length === 0 || (selectionView && selectionView.state !== 'open')}
              >
                {busy ? 'Uploading…' : selectedFiles.length > 0 ? `Upload ${selectedFiles.length} file(s)` : 'Upload'}
              </button>
              {selectionView && (
                <button onClick={handleReset}>
                  New Selection
                </button>
              )}
            </div>
            {selectedFiles.length > 0 && (
              <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
                Selected: {selectedFiles.map((f) => f.name).join(', ')}
              </div>
            )}
          </div>

          {/* Selection Staged Files & Actions */}
          {selectionView && (
            <div className="panel" style={{ marginBottom: 16 }}>
              <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <h3 style={{ margin: 0 }}>Staged Files ({selectionView.files?.length ?? 0})</h3>
                <div className="row" style={{ gap: 8 }}>
                  <button
                    onClick={handlePreview}
                    disabled={busy || previewing || selectionView.state !== 'open' || (selectionView.files?.length ?? 0) === 0}
                  >
                    {previewing ? 'Processing…' : 'Preview Import'}
                  </button>
                  <button
                    className="primary"
                    onClick={handleCommit}
                    disabled={!canImport || busy}
                    title={!canImport ? 'Requires an open selection with a current committable preview' : 'Commit import'}
                  >
                    {busy ? 'Processing…' : 'Commit Import'}
                  </button>
                  {selectionView.state === 'open' && (
                    <button
                      className="danger"
                      onClick={handleCancel}
                      disabled={busy}
                    >
                      Cancel Selection
                    </button>
                  )}
                </div>
              </div>

              {(!selectionView.files || selectionView.files.length === 0) ? (
                <p className="muted" style={{ margin: 0 }}>No files added to this selection yet.</p>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>File Name</th>
                      <th>Size</th>
                      <th>Declared Library</th>
                      <th>Target Key</th>
                      <th>Matched Auxiliary</th>
                      <th>Effective Auxiliary</th>
                      <th>Status</th>
                      {selectionView.state === 'open' && <th style={{ textAlign: 'right' }}>Actions</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {selectionView.files.map((file) => (
                      <tr key={file.file_id}>
                        <td><b>{file.file_name}</b></td>
                        <td>{file.byte_count} B</td>
                        <td>{file.declared_library ? <code>{file.declared_library}</code> : <span className="muted">—</span>}</td>
                        <td>{file.effective_library_key ? <code>{file.effective_library_key}</code> : <span className="muted" style={{ color: 'var(--bad)' }}>Unresolved</span>}</td>
                        <td>
                          {file.matched_auxiliary_kinds && file.matched_auxiliary_kinds.length > 0 ? (
                            file.matched_auxiliary_kinds.map((k) => <span key={k} className="chip" style={{ fontSize: 11, marginRight: 4 }}>{k}</span>)
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                        <td>{file.effective_auxiliary_kind ? <code>{file.effective_auxiliary_kind}</code> : <span className="muted">—</span>}</td>
                        <td><span className={`badge ${file.status}`}>{file.status}</span></td>
                        {selectionView.state === 'open' && (
                          <td style={{ textAlign: 'right' }}>
                            <button
                              className="danger icon"
                              onClick={() => handleRemoveFile(file.file_id)}
                              disabled={busy}
                              title="Remove file from selection"
                            >
                              ✕
                            </button>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {/* Advanced Compatibility Controls Toggle */}
              {selectionView.files && selectionView.files.length > 0 && selectionView.state === 'open' && (
                <div style={{ marginTop: 14, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
                  <button
                    className="chip"
                    onClick={() => setShowAdvanced((v) => !v)}
                    style={{ fontSize: 12 }}
                  >
                    {showAdvanced ? '▼ Hide Advanced Compatibility Options' : '► Show Advanced Compatibility Options'}
                  </button>

                  {showAdvanced && (
                    <div style={{ marginTop: 10, background: 'var(--panel-2)', padding: 12, borderRadius: 4 }}>
                      <h4 style={{ margin: '0 0 8px' }}>Compatibility Targeting</h4>
                      <p className="muted" style={{ margin: '0 0 12px', fontSize: 12 }}>
                        Set explicit target library keys or select ambiguous auxiliary kinds when declared identities are missing, ambiguous, or conflict with historical libraries.
                      </p>

                      {selectionView.files.map((file) => {
                        const draft = targetDrafts[file.file_id] || {}
                        const draftLib = draft.library_key !== undefined ? draft.library_key : (file.effective_library_key || '')
                        const draftAux = draft.auxiliary_kind !== undefined ? draft.auxiliary_kind : (file.effective_auxiliary_kind || '')

                        return (
                          <div key={file.file_id} style={{ marginBottom: 12, paddingBottom: 10, borderBottom: '1px solid var(--border)' }}>
                            <div className="row" style={{ alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                              <div>
                                <b>{file.file_name}</b>{' '}
                                <span className="muted" style={{ fontSize: 11 }}>
                                  (declared: {file.declared_library || 'none'})
                                </span>
                              </div>
                            </div>

                            <div className="row" style={{ alignItems: 'flex-end', gap: 10 }}>
                              <div style={{ flex: 2 }}>
                                <label style={{ fontSize: 12 }}>Target Library Key</label>
                                <div className="row" style={{ gap: 6 }}>
                                  <input
                                    value={draftLib}
                                    onChange={(e) => setTargetDrafts((prev) => ({
                                      ...prev,
                                      [file.file_id]: { ...(prev[file.file_id] || {}), library_key: e.target.value }
                                    }))}
                                    placeholder="Target library key"
                                    style={{ flex: 1 }}
                                  />
                                  {libraries.length > 0 && (
                                    <select
                                      value=""
                                      onChange={(e) => {
                                        if (e.target.value) {
                                          setTargetDrafts((prev) => ({
                                            ...prev,
                                            [file.file_id]: { ...(prev[file.file_id] || {}), library_key: e.target.value }
                                          }))
                                        }
                                      }}
                                      style={{ width: 'auto' }}
                                      title="Choose existing library key"
                                    >
                                      <option value="">Existing library…</option>
                                      {libraries.map((l) => (
                                        <option key={l.library_key} value={l.library_key}>{l.library_key}</option>
                                      ))}
                                    </select>
                                  )}
                                </div>
                              </div>

                              {file.matched_auxiliary_kinds && file.matched_auxiliary_kinds.length > 0 && (
                                <div style={{ flex: 1 }}>
                                  <label style={{ fontSize: 12 }}>Auxiliary Kind</label>
                                  <select
                                    value={draftAux}
                                    onChange={(e) => setTargetDrafts((prev) => ({
                                      ...prev,
                                      [file.file_id]: { ...(prev[file.file_id] || {}), auxiliary_kind: e.target.value }
                                    }))}
                                  >
                                    <option value="">None / Default</option>
                                    {file.matched_auxiliary_kinds.map((k) => (
                                      <option key={k} value={k}>{k}</option>
                                    ))}
                                  </select>
                                </div>
                              )}

                              <div>
                                <button
                                  onClick={() => {
                                    const patch = {}
                                    if (draftLib !== (file.effective_library_key || '')) {
                                      patch.effective_library_key = draftLib
                                    }
                                    if (draftAux !== (file.effective_auxiliary_kind || '')) {
                                      patch.effective_auxiliary_kind = draftAux || null
                                    }
                                    if (Object.keys(patch).length > 0) {
                                      handlePatchTarget(file.file_id, patch)
                                    }
                                  }}
                                  disabled={busy}
                                >
                                  Apply Choice
                                </button>
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Preview Results */}
          {previewReport && (
            <div className="panel" style={{ marginBottom: 16 }}>
              <div className="row" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
                <h2>Import Preview Results</h2>
                <button
                  className="primary"
                  disabled={!canImport || busy}
                  onClick={handleCommit}
                >
                  {busy ? 'Committing…' : 'Commit Import'}
                </button>
              </div>
              <p className="muted" style={{ margin: '0 0 12px' }}>
                Outcome classification is distinct from preparation readiness. Readiness is evaluated upon persistence.
              </p>

              {/* Outcome summary badges */}
              <div className="row" style={{ gap: 8, marginBottom: 14 }}>
                <span className="badge new">New: {previewReport.counts.new}</span>
                <span className="badge updated">Updated: {previewReport.counts.updated}</span>
                <span className="badge unchanged">Unchanged: {previewReport.counts.unchanged}</span>
                <span className="badge unresolved">Unresolved: {previewReport.counts.unresolved}</span>
                <span className="badge">Accepted: {previewReport.counts.accepted}</span>
                <span className="badge">Auxiliary: {previewReport.counts.auxiliary}</span>
                <span className="badge">Duplicates: {previewReport.counts.duplicates}</span>
                <span className="badge">Missing: {previewReport.counts.missing}</span>
              </div>

              {/* Accepted Outcomes */}
              {previewReport.accepted.length > 0 && (
                <>
                  <h3>Accepted Entries ({previewReport.accepted.length})</h3>
                  <table style={{ marginBottom: 14 }}>
                    <thead>
                      <tr>
                        <th>Source ID</th>
                        <th>Library Key</th>
                        <th>Kind</th>
                        <th>Outcome</th>
                        <th>New Digest</th>
                        <th>Prior Digest</th>
                      </tr>
                    </thead>
                    <tbody>
                      {previewReport.accepted.map((acc, i) => (
                        <tr key={i}>
                          <td><b>{acc.source_id}</b></td>
                          <td><code>{acc.library_key}</code></td>
                          <td>{acc.kind}</td>
                          <td>
                            <span className={`badge ${acc.classification}`}>
                              {acc.classification}
                            </span>
                          </td>
                          <td style={{ fontFamily: 'monospace', fontSize: 12 }}>
                            {acc.new_content_digest ? `${acc.new_content_digest.slice(0, 10)}…` : '—'}
                          </td>
                          <td style={{ fontFamily: 'monospace', fontSize: 12 }}>
                            {acc.previous_content_digest ? `${acc.previous_content_digest.slice(0, 10)}…` : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}

              {/* Auxiliary Outcomes */}
              {previewReport.auxiliary.length > 0 && (
                <>
                  <h3>Auxiliary Resources ({previewReport.auxiliary.length})</h3>
                  <table style={{ marginBottom: 14 }}>
                    <thead>
                      <tr>
                        <th>Kind</th>
                        <th>Library Key</th>
                        <th>Outcome</th>
                        <th>Content Digest</th>
                      </tr>
                    </thead>
                    <tbody>
                      {previewReport.auxiliary.map((aux, i) => (
                        <tr key={i}>
                          <td><code>{aux.kind}</code></td>
                          <td><code>{aux.library_key}</code></td>
                          <td>
                            <span className={`badge ${aux.classification}`}>
                              {aux.classification}
                            </span>
                          </td>
                          <td style={{ fontFamily: 'monospace', fontSize: 12 }}>
                            {aux.new_content_digest ? `${aux.new_content_digest.slice(0, 10)}…` : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}

              {/* Unresolved items */}
              {previewReport.unresolved.length > 0 && (
                <>
                  <h3 style={{ color: 'var(--bad)' }}>
                    Unresolved Items ({previewReport.unresolved.length})
                  </h3>
                  <table style={{ marginBottom: 14 }}>
                    <thead>
                      <tr>
                        <th>Bucket</th>
                        <th>Index</th>
                        <th>Reason</th>
                        <th>Received Type</th>
                        <th>Expected Kind</th>
                      </tr>
                    </thead>
                    <tbody>
                      {previewReport.unresolved.map((un, i) => (
                        <tr key={i}>
                          <td><code>{un.bucket}</code></td>
                          <td>{un.index}</td>
                          <td style={{ color: 'var(--bad)' }}>{un.reason}</td>
                          <td><code>{un.received_type || '—'}</code></td>
                          <td><code>{un.expected_kind || '—'}</code></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}

              {/* Duplicates */}
              {previewReport.duplicates.length > 0 && (
                <>
                  <h3 style={{ color: 'var(--warn)' }}>
                    Duplicate Identifiers ({previewReport.duplicates.length})
                  </h3>
                  <table style={{ marginBottom: 14 }}>
                    <thead>
                      <tr>
                        <th>Source ID</th>
                        <th>Occurrences</th>
                      </tr>
                    </thead>
                    <tbody>
                      {previewReport.duplicates.map((dup, i) => (
                        <tr key={i}>
                          <td><code>{dup.source_id}</code></td>
                          <td>{dup.occurrences}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}

              {/* Missing source entries */}
              {previewReport.missing.length > 0 && (
                <>
                  <h3>Missing Source Entries ({previewReport.missing.length})</h3>
                  <table style={{ marginBottom: 14 }}>
                    <thead>
                      <tr>
                        <th>Library Key</th>
                        <th>Source ID</th>
                        <th>Latest Stored Digest</th>
                      </tr>
                    </thead>
                    <tbody>
                      {previewReport.missing.map((mis, i) => (
                        <tr key={i}>
                          <td><code>{mis.library_key}</code></td>
                          <td><code>{mis.source_id}</code></td>
                          <td style={{ fontFamily: 'monospace', fontSize: 12 }}>
                            {mis.latest_content_digest}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </div>
          )}

          {/* Commit Result */}
          {commitReport && (
            <div className="panel" style={{ marginBottom: 16 }}>
              <h2>Commit Report</h2>
              <div className="row" style={{ gap: 8, marginBottom: 10 }}>
                <span className="badge done">Recorded: {commitReport.summary?.recorded ?? 0}</span>
                <span className="badge new">New Revisions: {commitReport.summary?.new_scene_revisions ?? 0}</span>
                <span className="badge updated">Updated Revisions: {commitReport.summary?.updated_scene_revisions ?? 0}</span>
                <span className="badge unchanged">Unchanged: {commitReport.summary?.unchanged_scene_revisions ?? 0}</span>
              </div>
              <p className="muted" style={{ margin: 0 }}>
                Resources have been persisted into local SQLite storage. You can now browse them under the Inventory tab.
              </p>
              <div className="row" style={{ marginTop: 12 }}>
                <button
                  className="primary"
                  onClick={async () => {
                    try {
                      await awaitCurrentLibraryRefresh(reloadLibraries())
                    } catch {}
                    setTab('inventory')
                  }}
                >
                  View readiness in Inventory
                </button>
              </div>
            </div>
          )}

          {/* Legacy Path Import (Compatibility) */}
          <div style={{ marginTop: 20 }}>
            <button
              className="chip"
              onClick={() => setShowLegacyPathImport((v) => !v)}
              style={{ fontSize: 12 }}
            >
              {showLegacyPathImport ? '▼ Hide Legacy Path Import' : '► Legacy Path Import (Compatibility)'}
            </button>
            {showLegacyPathImport && (
              <div className="panel" style={{ marginTop: 10 }}>
                <h3>Legacy Path Import</h3>
                <p className="muted">
                  Legacy path-based input for local filesystem paths. Kept for backwards compatibility.
                </p>

                {selections.map((sel, idx) => (
                  <div key={idx} className="row" style={{ marginBottom: 8 }}>
                    <div style={{ flex: 3 }}>
                      <label>File path</label>
                      <input
                        value={sel.path}
                        placeholder="e.g. /path/to/source_library.json"
                        onChange={(e) => updateSelection(idx, 'path', e.target.value)}
                      />
                    </div>
                    <div style={{ flex: 2 }}>
                      <label>Library Key</label>
                      <input
                        value={sel.library_key}
                        placeholder="e.g. rooms_main"
                        onChange={(e) => updateSelection(idx, 'library_key', e.target.value)}
                      />
                    </div>
                    {selections.length > 1 && (
                      <div style={{ alignSelf: 'flex-end', marginBottom: 2 }}>
                        <button className="danger icon" onClick={() => removeSelectionRow(idx)} title="Remove row">
                          ✕
                        </button>
                      </div>
                    )}
                  </div>
                ))}

                <div className="row" style={{ marginTop: 12 }}>
                  <button onClick={addSelectionRow}>+ Add file</button>
                  <button
                    className="primary"
                    disabled={busy || !selections.some((s) => s.path.trim() && s.library_key.trim())}
                    onClick={runLegacyPreview}
                  >
                    {busy ? 'Previewing…' : 'Preview Legacy Import'}
                  </button>
                  {legacyRawPreview && (
                    <button
                      className="primary"
                      disabled={busy}
                      onClick={runLegacyCommit}
                    >
                      Commit Legacy Import
                    </button>
                  )}
                </div>

                {legacyCommitReport && (
                  <div style={{ marginTop: 12 }}>
                    <b>Legacy Commit Result:</b> {legacyCommitReport.summary?.recorded ?? 0} recorded.
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </>
  )
}
