import React, { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api.js'
import { checkReadiness, normalizeFieldRole } from '../resources.js'
import '../ui/foundations.css'
import './ResourceInventory.css'

// Identity includes all three parts; separators may occur in source identifiers.
const identity = (lib, rev) => JSON.stringify([lib.library_key, rev.source_id, rev.content_digest])
const name = rev => [rev.translation?.label, rev.translation?.display_label].find(value => typeof value === 'string' && value.trim()) || rev.source_id
const tone = readiness => readiness.isReady ? 'success' : readiness.readiness === 'needs_source_correction' ? 'error' : 'warning'
const created = value => {
  if (!value) return 'Not recorded'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

function RevisionInspection({ lib, rev }) {
  const [state, setState] = useState({ loading: true, detail: null, error: '' })
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true
    setState(previous => ({ ...previous, loading: true, error: '' }))
    api.get(`/api/resources/revisions/${encodeURIComponent(lib.library_key)}/${encodeURIComponent(rev.source_id)}/${encodeURIComponent(rev.content_digest)}`)
      .then(detail => { if (active) setState({ loading: false, detail, error: '' }) })
      .catch(error => { if (active) setState(previous => ({ ...previous, loading: false, error: error.message })) })
    return () => { active = false }
  }, [lib.library_key, rev, retry])
  const detail = state.detail
  return <section className="resource-inspection" aria-label="Revision inspection" aria-busy={state.loading}>
    {state.loading && <p role="status">Loading revision details…</p>}
    {state.error && <div className="ui-notice" data-tone="error" role="alert"><p>{state.error}</p>
      <button type="button" className="ui-button" onClick={() => setRetry(value => value + 1)}>Retry inspection</button></div>}
    {detail && <>
      <h3 className="ui-section-title">Field Roles & Readiness Coverage</h3>
      {detail.readiness?.coverage?.sidecar_error && <p className="ui-notice" data-tone="error" role="alert"><b>Translation Sidecar Error:</b> {detail.readiness.coverage.sidecar_error}</p>}
      {!!detail.readiness?.diagnostics?.length && <div className="ui-notice" data-tone="error"><b>Source Diagnostics:</b><ul>
        {detail.readiness.diagnostics.map((diagnostic, index) => <li key={index}><code>{diagnostic.field}</code> {diagnostic.code}: {diagnostic.message}</li>)}
      </ul></div>}
      {!!detail.readiness?.coverage?.unmapped_fields?.length && <p className="ui-notice" data-tone="error"><b>Unmapped fields:</b> {detail.readiness.coverage.unmapped_fields.join(', ')}</p>}
      <ul className="resource-fields" aria-label="Field readiness">
        {(detail.readiness?.field_readiness || []).map(field => <li key={field.name}>
          <strong><code>{field.name}</code></strong><span>{normalizeFieldRole(field.role)}</span>
          <span>Translated: {field.translated ? 'Yes' : 'No'}</span><p>{field.reason || 'No additional note'}</p>
        </li>)}
      </ul>
      <details className="resource-technical"><summary>Original Payload</summary><pre>{JSON.stringify(detail.payload || {}, null, 2)}</pre></details>
      <details className="resource-technical"><summary>Translation & Stored Coverage vs Live Readiness</summary><pre>{JSON.stringify({ translation: detail.translation, stored_coverage: detail.coverage, live_readiness: detail.readiness }, null, 2)}</pre></details>
    </>}
  </section>
}

export default function ResourceInventory({ libraries, filteredLibraries, categories, query, onQueryChange, category, onCategoryChange,
  models, selectedModelId, onModelChange, selection, onSelectionChange, loading, error, importedIdentities,
  onRefresh, onImport, creationLocked, onCreate, onTranslate, onStructuredScene }) {
  const entries = useMemo(() => filteredLibraries.flatMap(lib => (lib.revisions || []).map(rev => ({ lib, rev, key: identity(lib, rev) }))), [filteredLibraries])
  const allEntries = useMemo(() => libraries.flatMap(lib => (lib.revisions || []).map(rev => ({ lib, rev, key: identity(lib, rev) }))), [libraries])
  const selected = allEntries.find(entry => entry.key === selection)
    || (!selection ? entries[0] : null)
  const [inspection, setInspection] = useState('')
  const heading = useRef(null)
  const list = useRef(null)
  const searchInput = useRef(null)
  const focusDetail = useRef(false)
  useEffect(() => {
    if (focusDetail.current) { heading.current?.focus(); focusDetail.current = false }
  }, [selection])
  const readinessFor = (lib, rev) => checkReadiness(rev, { kind: lib.kind, hasModelSelected: Boolean(selectedModelId), libraryKey: lib.library_key, importedIdentities })
  const clear = () => { onQueryChange(''); onCategoryChange('all'); searchInput.current?.focus() }
  const readiness = selected && readinessFor(selected.lib, selected.rev)
  const select = entry => {
    if (entry.key === selection) heading.current?.focus()
    else { focusDetail.current = true; onSelectionChange(entry.key) }
  }
  return <section className="studio-ui ui-page resource-inventory" aria-label="Resource inventory">
    <div className="ui-surface resource-filters">
      <div className="ui-field"><label className="ui-label" htmlFor="resource-search">Search resources</label>
        <input id="resource-search" ref={searchInput} name="resources" type="search" className="ui-control" value={query} onChange={event => onQueryChange(event.target.value)}
          placeholder="Search resources..." autoComplete="off" spellCheck={false} aria-describedby="resource-search-help" />
        <p id="resource-search-help" className="ui-help">Search library names, source IDs, digests and translated values.</p></div>
      <div className="ui-field"><label className="ui-label" htmlFor="resource-category">Category (Kind)</label>
        <select id="resource-category" className="ui-control" value={category} onChange={event => onCategoryChange(event.target.value)}><option value="all">All categories</option>
          {categories.map(kind => <option key={kind} value={kind}>{kind}</option>)}</select></div>
      <div className="ui-field"><label className="ui-label" htmlFor="resource-model">Active Character Model</label>
        <select id="resource-model" className="ui-control" value={selectedModelId} onChange={event => onModelChange(event.target.value)} aria-describedby="resource-model-help">
          {models.map(model => <option key={model.id} value={model.id}>{model.name}</option>)}{!models.length && <option value="">No models available</option>}</select>
        <p className="ui-help" id="resource-model-help">Session drafts bind to this model.</p></div>
      <button type="button" className="ui-button" onClick={onRefresh}>Refresh</button>
    </div>
    <p role="status" className="resource-summary ui-help">{loading ? 'Loading inventory…' : `${entries.length} matching revisions in ${filteredLibraries.length} libraries`}</p>
    {error && <div className="ui-notice" data-tone="error" role="alert"><p>Could not refresh inventory: {error}</p><p>Existing content and selection are kept.</p><button type="button" className="ui-button" onClick={onRefresh}>Retry inventory</button></div>}
    <div className="resource-browser">
      <section className="ui-surface resource-list" aria-label="Resource revisions" ref={list} tabIndex={-1}>
        <h2 className="ui-section-title">Inventory</h2>
        {!entries.length && !loading && !error && <div className="resource-empty"><p>{!libraries.length ? 'No resource libraries imported yet.' : filteredLibraries.some(lib => lib.auxiliary?.length) ? 'No source revisions match this filter. Auxiliary data is listed below.' : 'No resources match your filter.'}</p>
          {!libraries.length ? <button type="button" className="ui-button" onClick={onImport}>Open Import Preview</button> : <button type="button" className="ui-button" onClick={clear}>Clear filters</button>}</div>}
        <ul>{entries.map(entry => {
          const state = readinessFor(entry.lib, entry.rev)
          return <li key={entry.key}><button type="button" className="resource-choice" aria-pressed={entry.key === selected?.key} onClick={() => select(entry)}>
            <strong>{name(entry.rev)}</strong><span>{entry.lib.display_name || entry.lib.library_key}</span>
            <span className="resource-choice-id">{entry.rev.source_id}</span>
            {state.isImported && <span className="ui-state">Imported</span>}
            <span className="ui-state" data-tone={tone(state)}>{state.label}</span>
            <span className="resource-choice-id">Revision {entry.rev.revision_id ?? '—'} · {entry.rev.content_digest.slice(0, 12)}…</span>
          </button></li>
        })}</ul>
        {filteredLibraries.some(lib => lib.auxiliary?.length) && <p className="ui-help">Auxiliary data is available below the selected revision.</p>}
      </section>
      <section className="ui-surface resource-detail" aria-label="Selected resource">
        <button type="button" className="ui-button resource-return" onClick={() => {
          const active = list.current?.querySelector('[aria-pressed="true"]')
          ;(active || list.current)?.focus()
        }}>Back to inventory</button>
        {!selected ? <div className="resource-empty"><h2 className="ui-section-title" ref={heading} tabIndex={-1}>Choose a revision</h2><p>{selection ? 'The selected revision is no longer in the inventory. Select another exact revision.' : 'Select a resource to inspect its readiness and available actions.'}</p></div> : <>
          <h2 className="ui-section-title" ref={heading} tabIndex={-1}>{name(selected.rev)}</h2>
          <p className="ui-help">{selected.lib.display_name || selected.lib.library_key} · {selected.lib.kind}</p>
          <div className="ui-actions">{readiness.isImported && <span className="ui-state">Imported</span>}<span className="ui-state" data-tone={tone(readiness)}>{readiness.label}</span></div>
          {!entries.some(entry => entry.key === selected.key) && <p className="ui-notice">This selection is outside the current filter. Its exact revision is kept.</p>}
          {!!readiness.reasons.length && <ul className="resource-reasons">{readiness.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>}
          {readiness.primaryAction?.id === 'use_advanced_editor' && <p className="ui-help">Fused descriptions can combine location, camera, pose, and wardrobe, so simple automatic scene choices use ready structured rooms. The advanced editor keeps this full description intact.</p>}
          <div className="ui-actions resource-next-actions">
            {readiness.isReady && <button type="button" className="ui-button ui-button--primary" disabled={creationLocked || !selectedModelId}
              title={readiness.primaryAction?.id === 'use_advanced_editor' ? 'Open an expert resource-v1 draft with this exact fused revision' : 'Create a resource-v1 session draft with this exact revision'}
              onClick={() => onCreate(selected.lib, selected.rev)}>{readiness.primaryAction?.label || 'Create session'}</button>}
            {readiness.primaryAction?.id === 'use_advanced_editor' && <button type="button" className="ui-button" onClick={onStructuredScene}>{readiness.secondaryAction.label}</button>}
            {readiness.readiness === 'needs_translation' && <button type="button" className="ui-button ui-button--primary" onClick={() => onTranslate(selected.lib.library_key)}>Translate</button>}
            {readiness.readiness === 'needs_source_correction' && <button type="button" className="ui-button" onClick={onImport}>Re-import</button>}
            <button type="button" className="ui-button" aria-expanded={inspection === selected.key} aria-controls="resource-inspection" onClick={() => setInspection(inspection === selected.key ? '' : selected.key)}>{inspection === selected.key ? 'Hide' : readiness.readiness === 'needs_source_correction' ? 'Inspect source' : 'Inspect'}</button>
          </div>
          {readiness.isReady && !selectedModelId && <p className="ui-help">Select an active character model to create a session.</p>}
          <dl className="ui-definition-list resource-identity">
            <div><dt>Library key</dt><dd><code>{selected.lib.library_key}</code></dd></div>
            <div><dt>Source ID</dt><dd><code>{selected.rev.source_id}</code></dd></div>
            <div><dt>Revision</dt><dd>{selected.rev.revision_id ?? 'Not recorded'}</dd></div>
            <div><dt>Content digest</dt><dd><code>{selected.rev.content_digest}</code></dd></div>
            <div><dt>Created</dt><dd>{created(selected.rev.created_at)}</dd></div>
          </dl>
          {Object.values(selected.rev.translation || {}).some(value => typeof value === 'string' && value.trim()) && <details className="resource-technical"><summary>Translated display values</summary><dl className="ui-definition-list">{Object.entries(selected.rev.translation).filter(([, value]) => typeof value === 'string' && value.trim()).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl></details>}
          <div id="resource-inspection">{inspection === selected.key && <RevisionInspection key={selected.key} lib={selected.lib} rev={selected.rev} />}</div>
        </>}
      </section>
    </div>
    {filteredLibraries.filter(lib => lib.auxiliary?.length).map(lib => <details className="ui-surface resource-technical" key={lib.library_key}>
      <summary>Auxiliary Resources ({lib.auxiliary.length}) · {lib.display_name || lib.library_key}</summary>
      <ul className="resource-fields">{lib.auxiliary.map(aux => <li key={aux.auxiliary_id}><strong>{aux.kind}</strong><span>auxiliary data</span><code>{aux.content_digest}</code><span>{created(aux.created_at)}</span></li>)}</ul>
    </details>)}
  </section>
}
