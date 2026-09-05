import React, { useState } from 'react'
import { api } from '../api'
import { countRows, refusalRows, uncoveredRows } from '../roomImport.js'

/** Import a source asset library into room seed files.
 *
 *  Two machine paths, typed by the operator — nothing here guesses one, and no
 *  example path is written into the app. The import refuses the whole upload
 *  when a string is missing from the translation map and writes nothing, so
 *  the uncovered list below is a worklist for the translator, not a warning. */
export default function RoomImport() {
  const [sourceDir, setSourceDir] = useState('')
  const [mapPath, setMapPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState(null)
  const [uncovered, setUncovered] = useState([])
  const [error, setError] = useState('')

  const run = async () => {
    setBusy(true); setError(''); setReport(null); setUncovered([])
    try {
      setReport(await api.post('/api/rooms/import', {
        source_dir: sourceDir.trim(), map_path: mapPath.trim(),
      }))
    } catch (e) {
      // An empty list means this was not a translation refusal, and then the
      // message the route sent is the whole story.
      const rows = uncoveredRows(e.message)
      setUncovered(rows)
      setError(rows.length
        ? `${rows.length} string${rows.length === 1 ? ' is' : 's are'} missing from the translation map. Nothing was written.`
        : e.message)
    } finally { setBusy(false) }
  }

  const refusals = refusalRows(report)

  return (
    <>
      {error && <div className="error">{error}</div>}
      <h1>Import rooms</h1>
      <p className="muted">
        Reads a source asset directory and its translation map, and writes one room seed file per
        library. Refusal and translation run over the whole upload before anything is written: if
        one string is uncovered, no seed file changes.
      </p>

      <div className="panel" style={{ marginTop: 14 }}>
        <div className="grid-form">
          <div style={{ gridColumn: 'span 2' }}>
            <label htmlFor="ri-source">Source directory</label>
            <input id="ri-source" value={sourceDir} placeholder="folder holding the source JSON files"
                   onChange={(e) => setSourceDir(e.target.value)} />
          </div>
          <div style={{ gridColumn: 'span 2' }}>
            <label htmlFor="ri-map">Translation map</label>
            <input id="ri-map" value={mapPath} placeholder="translation map JSON, beside the source"
                   onChange={(e) => setMapPath(e.target.value)} />
          </div>
        </div>
        <button className="primary" disabled={busy || !sourceDir.trim() || !mapPath.trim()}
                onClick={run} style={{ marginTop: 12 }}>
          {busy ? 'Importing...' : 'Import'}
        </button>
      </div>

      {report && (
        <div className="panel" style={{ marginTop: 14 }}>
          <h2>Report</h2>
          <table>
            <tbody>
              {countRows(report).map((row) => (
                <tr key={row.key}><td>{row.label}</td><td>{row.value}</td></tr>
              ))}
            </tbody>
          </table>

          {refusals.length > 0 && (
            <>
              <h3>Refused</h3>
              <table>
                <thead><tr><th>What</th><th>Why</th></tr></thead>
                <tbody>
                  {refusals.map((r, i) => (
                    <tr key={i}><td>{r.what}</td><td>{r.why}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {(report.refused_identifiers || []).length > 0 && (
            <p className="muted">
              Refused entries: {report.refused_identifiers.join(', ')}
            </p>
          )}

          {/* Stored unchanged, named so somebody notices before a session's
              worth of frames comes back in one room. */}
          {(report.outlying_weights || []).length > 0 && (
            <p className="muted">
              Weights far outside their library, stored as they were:{' '}
              {report.outlying_weights.map((o) => `${o.identifier} (${o.weight})`).join(', ')}
            </p>
          )}

          {/* Said, not done. A measurement stays whatever the import found:
              the room usually comes back - another machine, another library,
              a seed nobody has imported here yet. */}
          {(report.orphaned_verdicts || []).length > 0 && (
            <p className="muted">
              Verdicts with no room here, kept: {report.orphaned_verdicts.join(', ')}
            </p>
          )}
        </div>
      )}

      {uncovered.length > 0 && (
        <div className="panel" style={{ marginTop: 14 }}>
          <h2>Strings still to translate</h2>
          <p className="muted">
            Add each of these to the translation map, then import again.
          </p>
          <table>
            <thead><tr><th>Entry</th><th>Field</th><th>String</th></tr></thead>
            <tbody>
              {uncovered.map((u, i) => (
                <tr key={i}>
                  <td>{u.identifier}</td>
                  <td>{u.field}</td>
                  <td>{u.string}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
