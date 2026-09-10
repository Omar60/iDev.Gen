import React, { useEffect, useState } from 'react'
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

  // Import preview state
  const [selections, setSelections] = useState([{ path: '', library_key: '' }])
  const [rawPreview, setRawPreview] = useState(null)
  const [previewReport, setPreviewReport] = useState(null)
  const [commitReport, setCommitReport] = useState(null)

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  // Translation mapping state per library key
  const [translationState, setTranslationState] = useState({})

  const updateTState = (libraryKey, patch) => {
    setTranslationState((prev) => ({
      ...prev,
      [libraryKey]: { ...(prev[libraryKey] || {}), ...patch },
    }))
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
      updateTState(libraryKey, { error: msg })
    } finally {
      updateTState(libraryKey, { busy: false })
    }
  }

  const reloadLibraries = () => {
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

  // Import selection helpers
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

  // Run import preview
  const runPreview = async () => {
    setBusy(true)
    setError('')
    setNotice('')
    setCommitReport(null)
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
      setRawPreview(res.preview)
      setPreviewReport(parsePreviewSummary(res.report))
      setNotice('Preview generated successfully. Review the outcomes below before committing.')
    } catch (e) {
      setError(e.message)
      setRawPreview(null)
      setPreviewReport(null)
    } finally {
      setBusy(false)
    }
  }

  // Commit import
  const runCommit = async () => {
    if (!rawPreview) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const res = await api.post('/api/resources/import/commit', { preview: rawPreview })
      setCommitReport(res.report)
      setRawPreview(null)
      setPreviewReport(null)
      setNotice('Import committed successfully. Local resource inventory updated.')
      reloadLibraries()
    } catch (e) {
      // 409 Conflict: stale preview
      if (e.message && (e.message.includes('changed') || e.message.includes('409') || e.message.includes('fresh preview'))) {
        setError('Source files changed since preview. Please generate a fresh preview before committing.')
      } else {
        setError(e.message)
      }
    } finally {
      setBusy(false)
    }
  }

  const categories = extractCategories(libraries)
  const filteredLibraries = filterLibraries(libraries, { query, category })

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
                  placeholder="Filter by source ID, digest, library key, or content..."
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
                    return (
                      <>
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
            <h2>Import Selection</h2>
            <p className="muted">
              Select source JSON files and assign target library keys. The preview checks shapes, fingerprints source files, accounts for every item, and gates atomic commit with an attestation token.
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
                onClick={runPreview}
              >
                {busy ? 'Previewing…' : 'Preview Import'}
              </button>
            </div>
          </div>

          {/* Preview Results */}
          {previewReport && (
            <div className="panel" style={{ marginBottom: 16 }}>
              <div className="row" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
                <h2>Import Preview Results</h2>
                <button
                  className="primary"
                  disabled={busy || !rawPreview}
                  onClick={runCommit}
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

              {/* Unresolved items / Malformed errors */}
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
            <div className="panel">
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
        </>
      )}
    </>
  )
}
