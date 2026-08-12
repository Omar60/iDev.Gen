import React, { useEffect, useState } from 'react'
import { api } from '../api'

const SLOT_LABEL = {
  positive: 'Positive prompt', negative: 'Negative prompt', seed: 'Seed',
  steps: 'Steps', cfg: 'CFG', width: 'Width', height: 'Height',
  checkpoint: 'Base model',
  lora_name: 'LoRA (file)', lora_strength: 'LoRA strength', filename_prefix: 'Filename prefix',
  reference: 'Reference image', reference2: 'Reference image 2', reference3: 'Reference image 3',
  reference_strength: 'Reference strength', denoise: 'Denoise',
}

export default function Workflows() {
  const [list, setList] = useState([])
  const [draft, setDraft] = useState(null)   // {name, graph, node_map, slots, nodes}
  const [error, setError] = useState('')

  const reload = () => api.get('/api/workflows').then(setList).catch((e) => setError(e.message))
  useEffect(() => { reload() }, [])

  const importFile = async (file) => {
    setError('')
    try {
      const graph = JSON.parse(await file.text())
      if (graph.nodes && graph.links) throw new Error('That is the editor format. Export with Workflow → Export (API).')
      const det = await api.post('/api/workflows/detect', { graph })
      setDraft({ name: file.name.replace(/\.json$/i, ''), graph, ...det, detected: det.node_map })
    } catch (e) { setError(e.message) }
  }

  const open = async (w) => {
    const full = await api.get(`/api/workflows/${w.id}`)
    const det = await api.post('/api/workflows/detect', { graph: full.graph })
    setDraft({ id: w.id, name: full.name, graph: full.graph, node_map: full.node_map,
               slots: det.slots, nodes: det.nodes, detected: det.node_map })
  }

  // Slots are added to the app over time; a workflow saved before one existed has
  // a gap, not a mistake. Fill only the empty rows so manual fixes survive.
  const detectMissing = () => {
    const filled = { ...draft.node_map }
    let added = 0
    for (const [slot, path] of Object.entries(draft.detected || {})) {
      if (!filled[slot]) { filled[slot] = path; added++ }
    }
    setDraft({ ...draft, node_map: filled })
    setError(added ? '' : 'Nothing left to detect — every slot this workflow exposes is already mapped.')
  }

  const save = async () => {
    try {
      const body = { name: draft.name, graph: draft.graph, node_map: draft.node_map }
      if (draft.id) await api.patch(`/api/workflows/${draft.id}`, body)
      else await api.post('/api/workflows', body)
      setDraft(null); reload()
    } catch (e) { setError(e.message) }
  }

  return (
    <>
      {error && <div className="error">{error}</div>}
      <h1>Workflows</h1>
      <p className="muted">
        Import your ComfyUI workflow in <b>API format</b> (Workflow → Export (API)) and confirm which widget
        drives each slot. Anything left unmapped keeps the workflow's own value.
      </p>

      <div className="row" style={{ margin: '12px 0' }}>
        {/* The native file input renders its label in the browser's locale, so it
            is hidden behind our own button to keep the UI in one language. */}
        <label className="filebtn">
          Import workflow…
          <input type="file" accept="application/json" hidden
                 onChange={(e) => e.target.files[0] && importFile(e.target.files[0])} />
        </label>
        <span className="muted">a .json exported with Workflow → Export (API)</span>
      </div>

      {draft && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <div className="grid-form">
            <div>
              <label>Name</label>
              <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </div>
          </div>
          <h3 style={{ marginTop: 14 }}>Node mapping</h3>
          <table>
            <thead><tr><th style={{ width: 200 }}>Slot</th><th>Workflow widget</th></tr></thead>
            <tbody>
              {draft.slots.map((slot) => (
                <tr key={slot}>
                  <td>{SLOT_LABEL[slot] || slot}</td>
                  <td>
                    <select value={draft.node_map[slot] || ''}
                            onChange={(e) => setDraft({ ...draft, node_map: { ...draft.node_map, [slot]: e.target.value } })}>
                      <option value="">— do not control —</option>
                      {draft.nodes.flatMap((n) => n.widgets.map((w) => (
                        <option key={`${n.id}.${w}`} value={`${n.id}.inputs.${w}`}>
                          #{n.id} {n.title || n.class_type} · {w}
                        </option>
                      )))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" onClick={save} disabled={!draft.name}>Save workflow</button>
            <button onClick={detectMissing} title="Fill empty rows from the graph, keeping your manual choices">
              Detect missing slots
            </button>
            <button onClick={() => setDraft(null)}>Cancel</button>
          </div>
        </div>
      )}

      <table>
        <thead><tr><th>Name</th><th>Mapped slots</th><th /></tr></thead>
        <tbody>
          {list.map((w) => (
            <tr key={w.id}>
              <td><a href="#/workflows" onClick={(e) => { e.preventDefault(); open(w) }}>{w.name}</a></td>
              {/* No denominator: the slot list grows, a hardcoded one goes stale. */}
              <td className="muted">{Object.keys(w.node_map).length} mapped</td>
              <td style={{ textAlign: 'right' }}>
                <button className="icon danger" onClick={async () => {
                  if (confirm(`Delete the workflow "${w.name}"?`)) { await api.del(`/api/workflows/${w.id}`); reload() }
                }}>×</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!list.length && <p className="muted">No workflows yet. Import one to be able to create sessions.</p>}
    </>
  )
}
