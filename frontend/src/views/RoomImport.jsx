import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { countRows, refusalRows, uncoveredRows } from '../roomImport.js'
import '../ui/foundations.css'
import './RoomImport.css'

// Retain only the latest explicit import for this document's lifetime.
let lastImport = null

export default function RoomImport() {
  const operationRef = useRef(lastImport)
  const [sourceDir, setSourceDir] = useState(operationRef.current?.sourceDir ?? '')
  const [mapPath, setMapPath] = useState(operationRef.current?.mapPath ?? '')
  const [busy, setBusy] = useState(Boolean(operationRef.current && !operationRef.current.outcome))
  const [outcome, setOutcome] = useState(operationRef.current?.outcome ?? null)
  const mounted = useRef(false)
  const resultRef = useRef(null)

  const showOutcome = (operation, result) => {
    if (mounted.current && operationRef.current === operation && lastImport === operation) {
      setOutcome(result)
      setBusy(false)
    }
  }
  useEffect(() => {
    mounted.current = true
    const operation = operationRef.current
    // A settled promise also covers completion between the initial render and this effect.
    if (operation) operation.promise.then((result) => showOutcome(operation, result))
    return () => { mounted.current = false }
  }, [])
  useEffect(() => {
    // A completed request must not pull focus out of the mobile navigation.
    if (outcome && !resultRef.current?.closest('[inert]') && !document.querySelector('dialog[open], [aria-modal="true"]')) {
      resultRef.current?.focus({ preventScroll: true })
      resultRef.current?.scrollIntoView({ block: 'start' })
    }
  }, [outcome])

  const run = async (event) => {
    event.preventDefault()
    if ((lastImport && !lastImport.outcome) || !sourceDir.trim() || !mapPath.trim()) return
    const request = { source_dir: sourceDir.trim(), map_path: mapPath.trim() }
    const operation = { sourceDir, mapPath, request, promise: null, outcome: null }
    lastImport = operation
    operationRef.current = operation
    setBusy(true)
    setOutcome(null)
    operation.promise = api.post('/api/rooms/import', request).then((report) => (
      { request, report, uncovered: [], error: '' }
    ), (error) => {
      const uncovered = uncoveredRows(error.detail ?? error.message)
      return { request, report: null, uncovered, error: uncovered.length
        ? `${uncovered.length} string${uncovered.length === 1 ? ' is' : 's are'} missing from the translation map. Nothing was written.`
        : error.message || 'The import result could not be confirmed.' }
    }).then((result) => {
      operation.outcome = result
      return result
    })
    try {
      const result = await operation.promise
      showOutcome(operation, result)
    } finally {
      if (mounted.current && operationRef.current === operation && lastImport === operation) setBusy(false)
    }
  }

  const report = outcome?.report
  const refusals = refusalRows(report)
  const notices = refusals.length > 0 || (report?.outlying_weights?.length ?? 0) > 0 || (report?.orphaned_verdicts?.length ?? 0) > 0

  return <div className="studio-ui ui-page room-import-view">
    <header className="ui-page-header">
      <h1 className="ui-page-title">Import rooms</h1>
      <p className="ui-lead">Bring a source library into the Legacy room seeds. Choose the source directory and its authored translation map, then review the import report.</p>
    </header>
    <div className="room-import-layout">
      <section className="ui-surface" aria-labelledby="ri-paths-title">
        <h2 id="ri-paths-title" className="ui-section-title">Source and translation</h2>
        <p className="ui-section-description">Both paths are on the machine running the backend. No files are uploaded by this form.</p>
        <form onSubmit={run} className="ui-stack" aria-busy={busy}>
          <div className="ui-field">
            <label htmlFor="ri-source" className="ui-label">Source directory</label>
            <input id="ri-source" name="source_dir" className="ui-control" value={sourceDir} disabled={busy} required
              autoComplete="off" spellCheck={false} placeholder="Folder holding the source JSON files…"
              aria-describedby="ri-source-help" onChange={(event) => setSourceDir(event.target.value)} />
            <p id="ri-source-help" className="ui-help">The importer reads the declared libraries in this directory.</p>
          </div>
          <div className="ui-field">
            <label htmlFor="ri-map" className="ui-label">Translation map</label>
            <input id="ri-map" name="map_path" className="ui-control" value={mapPath} disabled={busy} required
              autoComplete="off" spellCheck={false} placeholder="Authored translation map JSON…"
              aria-describedby="ri-map-help" onChange={(event) => setMapPath(event.target.value)} />
            <p id="ri-map-help" className="ui-help">Every required source string must be covered before seed files are written.</p>
          </div>
          <div className="ui-actions"><button type="submit" className="ui-button ui-button--primary" disabled={busy || !sourceDir.trim() || !mapPath.trim()}>{busy ? 'Importing…' : 'Import rooms'}</button></div>
          <p className="ui-help">Import writes the declared seed destinations and updates <code>room_libraries</code>. Leaving this screen does not cancel a request already sent.</p>
        </form>
      </section>
      <aside className="ui-surface" aria-labelledby="ri-scope-title">
        <h2 id="ri-scope-title" className="ui-section-title">Legacy seed import</h2>
        <ol className="room-import-process">
          <li><strong>Read and validate</strong><span>Source declarations and translation coverage are checked.</span></li>
          <li><strong>Write and register</strong><span>Eligible rows reach their declared seed files and library registry. Empty themes and unresolved placeholders are skipped.</span></li>
          <li><strong>Review the report</strong><span>Inspect counts, refusals, unchanged weights and retained verdicts.</span></li>
        </ol>
        <p className="ui-help">This is separate from the resource database. Use <a href="#/resources">Resources</a> for resource revisions and readiness.</p>
      </aside>
    </div>
    {busy && <div className="ui-notice" role="status">Importing source libraries… Waiting for the backend report. No additional import will be sent while this request is pending.</div>}
    {!busy && !outcome && <section className="ui-surface" aria-labelledby="ri-empty-title"><h2 id="ri-empty-title" className="ui-section-title">No import report yet</h2><p className="ui-section-description">Enter both paths and select Import rooms to begin. Opening this screen does not import or change any data.</p></section>}
    {outcome && <section className="ui-surface ui-stack" aria-labelledby="ri-result-title">
      <div ref={resultRef} tabIndex={-1} className="room-import-result" role={outcome.error ? 'alert' : 'status'}>
        <div className="ui-section-heading"><h2 id="ri-result-title" className="ui-section-title">{outcome.error ? 'Import needs attention' : 'Import report'}</h2>
          <span className="ui-state" data-tone={outcome.error ? 'error' : notices ? 'warning' : 'success'}>{outcome.error ? 'Result needs review' : notices ? 'Report with notices' : 'Report received'}</span></div>
        {outcome.error && <div className="ui-notice" data-tone="error">{outcome.error}</div>}
        {outcome.error && <p className="ui-help ui-help-gap">{outcome.uncovered.length
          ? 'Add every listed string to the translation map, then explicitly import again. Your path entries are kept.'
          : 'Files or registry entries may already have changed. Inspect the seed files and room_libraries before explicitly retrying. Your path entries are kept; nothing is retried automatically.'}</p>}
      </div>
      <dl className="ui-definition-list room-import-context"><div><dt>Submitted source</dt><dd>{outcome.request.source_dir}</dd></div><div><dt>Submitted map</dt><dd>{outcome.request.map_path}</dd></div></dl>
      {report && <>
        <dl className="room-import-counts" aria-label="Import counts">{countRows(report).map((row) => <div key={row.key}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}</dl>
        <p className="ui-help">Counts describe different stages of the import; accepted entries and written rows are not the same measure.</p>
        {report.written === 0 && <p className="ui-notice">This report contains no written rows. Inspect the counts and details before importing again.</p>}
        {refusals.length > 0 && <div className="room-import-table" role="region" aria-label="Refusal details" tabIndex={0}><table><caption>Refused libraries and entries</caption><thead><tr><th scope="col">What</th><th scope="col">Why</th></tr></thead><tbody>{refusals.map((row, index) => <tr key={index}><td>{row.what}</td><td>{row.why}</td></tr>)}</tbody></table></div>}
        {(report.refused_identifiers || []).length > 0 && <div><h3>Refused entries</h3><ul className="room-import-identities">{report.refused_identifiers.map((id, index) => <li key={index}>{id}</li>)}</ul></div>}
        {(report.outlying_weights || []).length > 0 && <div><h3>Outlying weights</h3><p className="ui-help">Far outside their library; stored unchanged.</p><ul>{report.outlying_weights.map((item, index) => <li key={index}>{item.identifier} <strong>({item.weight})</strong></li>)}</ul></div>}
        {(report.orphaned_verdicts || []).length > 0 && <div><h3>Verdicts with no room here</h3><p className="ui-help">Kept without modification. Import does not remove these measurements.</p><ul>{report.orphaned_verdicts.map((id, index) => <li key={index}>{id}</li>)}</ul></div>}
        <details className="room-import-details"><summary>Full report and destination details</summary><pre>{JSON.stringify(report, null, 2)}</pre></details>
      </>}
      {outcome.uncovered.length > 0 && <div><h3>Strings still to translate</h3><p className="ui-section-description">Every uncovered entry, field and source string is listed below. No seed file was changed by this translation refusal.</p>
        <div className="room-import-table" role="region" aria-label="Strings still to translate" tabIndex={0}><table><caption>Translation worklist</caption><thead><tr><th scope="col">Entry</th><th scope="col">Field</th><th scope="col">String</th></tr></thead><tbody>{outcome.uncovered.map((row, index) => <tr key={index}><td>{row.identifier}</td><td>{row.field}</td><td>{row.string}</td></tr>)}</tbody></table></div>
      </div>}
    </section>}
  </div>
}
