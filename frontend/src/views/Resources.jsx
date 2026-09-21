import React, { useEffect, useState, useRef } from 'react'
import { api } from '../api'
import { go } from '../App.jsx'
import {
  normalizeFieldRole,
  extractCategories,
  filterLibraries,
  checkReadiness,
  buildSessionDraftPayload,
  parsePreviewSummary,
  selectAvailableModelId,
  parseTranslationPreview,
  buildTranslationMapFromRows,
  normalizeSelectionView,
  reduceSelectionView,
  isImportEligible,
} from '../resources.js'

export default function Resources({ requestedModelId = '' }) {
  const [tab, setTab] = useState('inventory') // 'inventory' | 'import'
  const [libraries, setLibraries] = useState([])
  const [models, setModels] = useState([])
  const [selectedModelId, setSelectedModelId] = useState('')
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('all')

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
  const selectionViewRef = useRef(null)

  // Legacy path import state (for compatibility)
  const [selections, setSelections] = useState([{ path: '', library_key: '' }])
  const [legacyRawPreview, setLegacyRawPreview] = useState(null)
  const [legacyPreviewReport, setLegacyPreviewReport] = useState(null)
  const [legacyCommitReport, setLegacyCommitReport] = useState(null)

  const [busy, setBusy] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  // Translation mapping state per library key
  const [translationState, setTranslationState] = useState({})
  const manualGenerationRef = useRef({})

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

  const clearManualPreview = (libraryKey, patch = {}) => {
    nextManualGeneration(libraryKey)
    updateTState(libraryKey, {
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
        })),
        manualDiagnostics: result.diagnostics || [],
        manualNotice: `Loaded ${(result.rows || []).length} editable source-backed row(s).`,
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
          manualPreview: null,
          manualSnapshot: '',
          manualError: '',
        },
      }
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
    const generation = manualGenerationRef.current[libraryKey] || 0
    updateTState(libraryKey, { manualBusy: true, manualError: '', manualNotice: '' })
    try {
      const result = await api.post(
        `/api/resources/libraries/${encodeURIComponent(libraryKey)}/translations/apply`,
        {
          translation_map: built.translationMap,
          attestation_token: state.manualPreview.attestationToken,
        }
      )
      if (manualGenerationRef.current[libraryKey] !== generation) return
      clearManualPreview(libraryKey, {
        manualNotice: `Translations applied: ${result.updated} updated, ${result.ready} ready, ${result.pending} pending.`,
      })
      reloadLibraries()
      handleLoadTranslationRows(libraryKey)
    } catch (e) {
      if (manualGenerationRef.current[libraryKey] === generation) {
        clearManualPreview(libraryKey, { manualError: e.message })
      }
    } finally {
      if (manualGenerationRef.current[libraryKey] === generation) {
        updateTState(libraryKey, { manualBusy: false })
      }
    }
  }

  const handlePreviewTranslations = async (libraryKey) => {
    const tState = translationState[libraryKey] || {}
    const mapPath = (tState.mapPath || '').trim()
    if (!mapPath) return

    updateTState(libraryKey, { busy: true, error: '', notice: '', preview: null })
    try {
      const res = await api.post(`/api/resources/libraries/${encodeURIComponent(libraryKey)}/translations/preview`, {
        map_path: mapPath,
      })
      const parsed = parseTranslationPreview(res)
      updateTState(libraryKey, {
        preview: parsed,
        notice: `Preview ready: ${parsed.matchedRevisions} of ${parsed.totalRevisions} revisions matched.`,
      })
    } catch (e) {
      updateTState(libraryKey, { error: e.message })
    } finally {
      updateTState(libraryKey, { busy: false })
    }
  }

  const handleApplyTranslations = async (libraryKey) => {
    const tState = translationState[libraryKey] || {}
    const mapPath = (tState.mapPath || '').trim()
    if (!mapPath || !tState.preview?.attestationToken) return

    updateTState(libraryKey, { busy: true, error: '', notice: '' })
    try {
      const res = await api.post(`/api/resources/libraries/${encodeURIComponent(libraryKey)}/translations/apply`, {
        map_path: mapPath,
        attestation_token: tState.preview.attestationToken,
      })
      updateTState(libraryKey, {
        preview: null,
        notice: `Translations applied: ${res.updated} updated, ${res.ready} ready, ${res.pending} pending.`,
      })
      reloadLibraries()
    } catch (e) {
      let msg = e.message
      if (msg && (msg.includes('409') || msg.includes('drift') || msg.includes('changed since preview'))) {
        msg = 'Library state or translation map changed since preview. Please preview again before applying.'
      }
      updateTState(libraryKey, { preview: null, error: msg })
    } finally {
      updateTState(libraryKey, { busy: false })
    }
  }

  const reloadLibraries = () => {
    for (const key of Object.keys(manualGenerationRef.current)) {
      nextManualGeneration(key)
    }
    setTranslationState((prev) => Object.fromEntries(
      Object.entries(prev).map(([key, state]) => [key, {
        ...state,
        manualBusy: false,
        manualPreview: null,
        manualSnapshot: '',
      }])
    ))
    api.get('/api/resources/libraries')
      .then((data) => setLibraries(data || []))
      .catch((e) => setError(e.message))
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
            setNotice('Import committed successfully. Local resource inventory updated.')
            reloadLibraries()
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
        setNotice('Import committed successfully. Local resource inventory updated.')
        reloadLibraries()
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
      setLegacyRawPreview(null)
      setLegacyPreviewReport(null)
      setNotice('Legacy import committed successfully.')
      reloadLibraries()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const categories = extractCategories(libraries)
  const filteredLibraries = filterLibraries(libraries, { query, category })

  const canImport = isImportEligible(selectionView, { activeSelectionId, pendingMutation: busy })
  const previewReport = selectionView?.preview ? parsePreviewSummary(selectionView.preview) : null
  const commitReport = selectionView?.commit_result?.report || selectionView?.commit_result || null

  return (
    <>
      {error && <div className="error">{error}</div>}
      {notice && <div className="panel" style={{ marginBottom: 12, borderColor: 'var(--accent)' }}>{notice}</div>}

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
                        const readiness = checkReadiness(rev)
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
                                <span className={`badge ${readiness.status}`}>
                                  {readiness.status}
                                </span>
                                {!readiness.isReady && (
                                  <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
                                    {readiness.reasons[0]}
                                  </div>
                                )}
                              </td>
                              <td className="muted" style={{ fontSize: 12 }}>
                                {rev.created_at ? rev.created_at.slice(0, 16).replace('T', ' ') : '—'}
                              </td>
                              <td style={{ textAlign: 'right' }}>
                                <div className="row" style={{ justifyContent: 'flex-end' }}>
                                  <button
                                    className="icon"
                                    onClick={() => toggleDetail(lib.library_key, rev)}
                                    title="Inspect field roles and coverage"
                                  >
                                    {detailLoading[detailKey] ? '…' : isExpanded ? 'Hide' : 'Inspect'}
                                  </button>
                                  <button
                                    className="primary"
                                    disabled={busy || !readiness.isReady || !selectedModelId}
                                    title={
                                      !readiness.isReady
                                        ? `Cannot start session: revision is not ready (${readiness.reasons.join('; ')})`
                                        : !selectedModelId
                                        ? 'Select a model to start a session'
                                        : 'Start a resource-v1 session draft with this exact revision'
                                    }
                                    onClick={() => startSessionWithRevision(lib.library_key, rev)}
                                  >
                                    Start session
                                  </button>
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
                <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
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
                            <table style={{ marginBottom: 8 }}>
                              <thead>
                                <tr>
                                  <th>Use</th>
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
                              onChange={(e) => updateTState(lib.library_key, { mapPath: e.target.value })}
                              placeholder="e.g. /path/to/translations.json or relative/path.json"
                              disabled={tState.busy}
                            />
                          </div>
                          <button
                            onClick={() => handlePreviewTranslations(lib.library_key)}
                            disabled={tState.busy || !(tState.mapPath || '').trim()}
                            title="Preview matching without writing database state"
                          >
                            {tState.busy ? 'Working…' : 'Preview Translations'}
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
                                disabled={tState.busy}
                                title="Atomically apply translations to library sidecars"
                              >
                                {tState.busy ? 'Applying…' : 'Confirm & Apply Translations'}
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
                <b>Temporary Cleanup Notice:</b> {selectionView.cleanup_warning}
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
