import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { WORKFLOW_KINDS } from '../kinds.js'
import '../ui/foundations.css'
import './Workflows.css'

const SLOT_LABEL = {
  positive: 'Positive prompt', negative: 'Negative prompt', seed: 'Seed',
  steps: 'Steps', cfg: 'CFG', sampler: 'Sampler', scheduler: 'Scheduler',
  width: 'Width', height: 'Height', checkpoint: 'Base model',
  lora_name: 'LoRA (file)', lora_strength: 'LoRA strength', filename_prefix: 'Filename prefix',
  reference: 'Reference image', reference2: 'Reference image 2', reference3: 'Reference image 3',
  reference_strength: 'Reference strength', denoise: 'Denoise',
}
const signature = (draft) => JSON.stringify([draft.name, draft.kind || '', draft.node_map])
const graphValue = (value) => value === undefined ? 'Unavailable' : JSON.stringify(value, null, 2)
function mappingDetails(graph, path) {
  const match = path?.match(/^([^.]+)\.inputs\.(.+)$/)
  const node = match && Object.hasOwn(graph, match[1]) ? graph[match[1]] : undefined
  const inputs = node?.inputs || {}
  const value = match && Object.hasOwn(inputs, match[2]) ? inputs[match[2]] : undefined
  return { node, nodeId: match?.[1], value,
    state: !path ? 'Unmapped' : value === undefined ? 'Missing widget' : Array.isArray(value) ? 'Connected' : 'Mapped' }
}

export default function Workflows() {
  const [list, setList] = useState([])
  const [draft, setDraft] = useState(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [busy, setBusy] = useState('')
  const original = useRef('')
  const fileInput = useRef(null)
  const importButton = useRef(null)
  const editorHeading = useRef(null)
  const listHeading = useRef(null)
  const errorNotice = useRef(null)
  const editorTrigger = useRef(null)
  const dirty = draft && (!draft.id || signature(draft) !== original.current)

  const reload = async () => {
    setLoading(true); setLoadFailed(false)
    try { setList(await api.get('/api/workflows')); return true }
    catch (e) { setError(e.message); setLoadFailed(true); return false }
    finally { setLoading(false) }
  }
  useEffect(() => { reload() }, [])
  useEffect(() => { if (error) errorNotice.current?.focus() }, [error])
  useEffect(() => { if (draft) editorHeading.current?.focus() }, [draft?.graph])

  const discardAllowed = () => !dirty || window.confirm('Discard unsaved changes to this workflow?')
  const showDraft = (next) => { original.current = signature(next); setDraft(next) }
  const cancel = () => {
    if (busy || !discardAllowed()) return
    setDraft(null); setError(''); setMessage('Workflow editor closed. No changes saved.')
    if (editorTrigger.current?.isConnected) editorTrigger.current.focus()
    else importButton.current?.focus()
  }
  const importFile = async (file) => {
    if (busy) return
    setError(''); setMessage(''); setBusy('Importing workflow…')
    try {
      const graph = JSON.parse(await file.text())
      if (graph.nodes && graph.links) throw new Error('That is the editor format. Export with Workflow → Export (API).')
      const det = await api.post('/api/workflows/detect', { graph })
      editorTrigger.current = importButton.current
      showDraft({ name: file.name.replace(/\.json$/i, ''), kind: '', graph, ...det, detected: det.node_map })
      setMessage('Workflow imported for review. Save workflow to add it to the list.')
    } catch (e) { setError(e.message) }
    finally { setBusy('') }
  }
  const open = async (workflow, trigger) => {
    if (busy || !discardAllowed()) return
    setError(''); setMessage(''); setBusy('Opening workflow…')
    try {
      const full = await api.get(`/api/workflows/${workflow.id}`)
      const det = await api.post('/api/workflows/detect', { graph: full.graph })
      editorTrigger.current = trigger
      showDraft({ id: workflow.id, name: full.name, kind: full.kind || '', graph: full.graph,
        node_map: full.node_map, slots: det.slots, nodes: det.nodes, detected: det.node_map })
    } catch (e) { setError(e.message) }
    finally { setBusy('') }
  }
  const detectMissing = () => {
    if (busy) return
    const filled = { ...draft.node_map }
    let added = 0
    for (const [slot, path] of Object.entries(draft.detected || {})) {
      if (!filled[slot]) { filled[slot] = path; added++ }
    }
    setDraft({ ...draft, node_map: filled }); setError('')
    setMessage(added ? `${added} missing slot${added === 1 ? '' : 's'} filled. Review and save to keep these mappings.`
      : 'Nothing left to detect — every slot this workflow exposes is already mapped.')
  }
  const save = async () => {
    if (busy) return
    setError(''); setMessage(''); setBusy('Saving workflow…')
    try {
      const body = { name: draft.name, graph: draft.graph, node_map: draft.node_map, kind: draft.kind || '' }
      if (draft.id) await api.patch(`/api/workflows/${draft.id}`, body)
      else await api.post('/api/workflows', body)
      setDraft(null); setMessage('Workflow saved.'); listHeading.current?.focus()
      await reload()
    } catch (e) { setError(e.message) }
    finally { setBusy('') }
  }
  const remove = async (workflow) => {
    if (busy || !window.confirm(`Delete the workflow "${workflow.name}"?${draft?.id === workflow.id && dirty ? ' Unsaved changes to this workflow will also be discarded.' : ''}`)) return
    setError(''); setMessage(''); setBusy('Deleting workflow…')
    try {
      await api.del(`/api/workflows/${workflow.id}`)
      if (draft?.id === workflow.id) setDraft(null)
      setMessage('Workflow deleted.'); listHeading.current?.focus()
      await reload()
    } catch (e) { setError(e.message) }
    finally { setBusy('') }
  }

  const options = draft?.nodes.flatMap((node) => node.widgets.map((widget) => ({
    path: `${node.id}.inputs.${widget}`, label: `#${node.id} ${node.title || node.class_type} · ${widget}`,
  }))).filter((item) => !Array.isArray(mappingDetails(draft.graph, item.path).value)) || []
  const connections = draft ? Object.entries(draft.graph).flatMap(([id, node]) =>
    Object.entries(node.inputs || {}).filter(([, value]) => Array.isArray(value))
      .map(([widget, value]) => ({ path: `${id}.inputs.${widget}`, title: node._meta?.title || node.class_type, value }))) : []

  return <div className="studio-ui ui-page workflows-page">
    <header className="ui-page-header">
      <h1 className="ui-page-title">Workflows</h1>
      <p className="ui-lead">Connect session values to your ComfyUI graph. Import an API export, review its mappings and save.
        Anything left unmapped keeps the workflow’s own value.</p>
    </header>
    {error && <div className="ui-notice workflow-feedback" data-tone="error" role="alert" tabIndex={-1} ref={errorNotice}>
      {error}{loadFailed && <div className="ui-actions ui-help-gap"><button type="button" className="ui-button"
        onClick={async () => { if (loading) return; setError(''); if (await reload()) listHeading.current?.focus() }}>Retry loading workflows</button></div>}
    </div>}
    <p className="ui-help workflow-feedback" role="status">{busy || message}</p>
    <div className="workflow-layout">
      <div className="ui-stack workflow-library">
        <section className="ui-surface" aria-labelledby="workflow-import-title">
          <div className="ui-section-heading"><h2 className="ui-section-title" id="workflow-import-title">Import</h2></div>
          <p className="ui-section-description" id="workflow-import-help">Use a .json file from ComfyUI’s Workflow → Export (API).
            The editor’s regular Save format is not supported.</p>
          <button className="ui-button ui-button--primary" type="button" ref={importButton}
            aria-describedby="workflow-import-help" aria-disabled={!!busy}
            onClick={() => { if (!busy && discardAllowed()) fileInput.current?.click() }}>Import workflow…</button>
          <input type="file" accept="application/json" hidden ref={fileInput} aria-label="Workflow API JSON file"
            onChange={(event) => { const file = event.target.files[0]; event.target.value = ''; if (file) importFile(file) }} />
        </section>
        <section className="ui-surface" aria-labelledby="workflow-list-title" aria-busy={loading}>
          <div className="ui-section-heading"><h2 className="ui-section-title" id="workflow-list-title" tabIndex={-1} ref={listHeading}>Saved workflows</h2>
            <span className="ui-state">{loading ? 'Loading…' : loadFailed ? 'Not refreshed' : list.length}</span></div>
          {loading && <p className="ui-help" role="status">Loading workflows…</p>}
          {!loading && !loadFailed && !list.length && <p className="ui-help">No workflows yet. Import one to be able to create sessions.</p>}
          {loadFailed && <p className="ui-help">The list could not be refreshed. Use Retry loading workflows above.</p>}
          <ul className="workflow-list">
            {list.map((workflow) => <li key={workflow.id} className="workflow-list-item">
              <button className="ui-button workflow-open" type="button" aria-pressed={draft?.id === workflow.id}
                aria-disabled={!!busy} onClick={(event) => open(workflow, event.currentTarget)}>
                <span className="workflow-name">{workflow.name || 'Unnamed workflow'}</span>
                <span className="ui-help">{WORKFLOW_KINDS[workflow.kind] || '— untagged —'}</span>
                <span className="ui-help">{Object.values(workflow.node_map).filter(Boolean).length} mapped slots</span>
              </button>
              <button className="ui-button workflow-delete" type="button" aria-label={`Delete ${workflow.name || 'unnamed workflow'}`}
                aria-disabled={!!busy} onClick={() => remove(workflow)}>Delete</button>
            </li>)}
          </ul>
        </section>
      </div>
      <section className="ui-surface workflow-editor" aria-labelledby="workflow-editor-title" aria-busy={!!busy}>
        <div className="ui-section-heading"><h2 className="ui-section-title" id="workflow-editor-title" ref={editorHeading} tabIndex={-1}>
          {draft ? draft.id ? 'Edit workflow' : 'Review import' : 'Mapping editor'}</h2>
          {draft && <span className="ui-state">{dirty ? 'Unsaved changes' : 'Saved workflow'}</span>}
        </div>
        {!draft ? <p className="ui-section-description">Choose a saved workflow or import an API export to review slots, widget values and connections.
          Importing and detecting do not save a workflow.</p> : <form className="ui-stack" aria-label="Workflow mapping" onSubmit={(event) => event.preventDefault()}>
          <div className="ui-form-grid">
            <div className="ui-field"><label className="ui-label" htmlFor="workflow-name">Name</label>
              <input className="ui-control" id="workflow-name" name="workflow_name" aria-describedby="workflow-name-help"
                autoComplete="off" value={draft.name} readOnly={!!busy} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
              <p className="ui-help" id="workflow-name-help">The name shown when choosing a workflow.</p></div>
            <div className="ui-field"><label className="ui-label" htmlFor="workflow-kind">Kind</label>
              <select className="ui-control" id="workflow-kind" name="workflow_kind" aria-describedby="workflow-kind-help"
                value={draft.kind || ''} disabled={!!busy} onChange={(event) => setDraft({ ...draft, kind: event.target.value })}>
                <option value="">— untagged —</option>
                {Object.entries(WORKFLOW_KINDS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </select>
              <p className="ui-help" id="workflow-kind-help">Sessions of that kind offer this graph. Untagged graphs stay offered everywhere.</p></div>
          </div>
          <div>
            <h3 className="workflow-subtitle">Node mapping</h3>
            <p className="ui-section-description" id="workflow-mapping-help">Choose a literal widget for each session slot. Graph values below are read only;
              no value is changed here. Connected inputs are excluded from the choices and are never replaced.</p>
            {draft.node_map.reference && ['checkpoint', 'lora_name', 'lora_strength'].some((slot) => draft.node_map[slot]) &&
              <p className="ui-notice workflow-warning">This graph edits a photo, and it is driving{' '}
                {['checkpoint', 'lora_name', 'lora_strength'].filter((slot) => draft.node_map[slot]).map((slot) => SLOT_LABEL[slot]).join(', ')}.
                An editing graph loads its own model and often a speed LoRA of its own, so the session’s picks would be written over them and every take would come back wrong.
                Set them to <b>do not control</b> unless you know this graph wants them.</p>}
            <ol className="workflow-mappings">
              {draft.slots.map((slot, index) => {
                const path = draft.node_map[slot] || ''
                const detail = mappingDetails(draft.graph, path)
                const helpId = `workflow-slot-help-${index}`
                return <li className="workflow-mapping" key={slot}>
                  <div className="workflow-slot-heading"><label className="ui-label" htmlFor={`workflow-slot-${index}`}>{SLOT_LABEL[slot] || slot}</label>
                    <code className="ui-help">{slot}</code>
                    <span className="ui-state" data-tone={detail.state === 'Mapped' ? 'success' : path ? 'warning' : undefined}>{detail.state}</span>
                  </div>
                  <div className="ui-field">
                    <select className="ui-control" id={`workflow-slot-${index}`} name={`mapping-${slot}`} disabled={!!busy}
                      aria-describedby={`workflow-mapping-help ${helpId}`} value={path}
                      onChange={(event) => setDraft({ ...draft, node_map: { ...draft.node_map, [slot]: event.target.value } })}>
                      <option value="">— do not control —</option>
                      {!!path && !options.some((item) => item.path === path) && <option value={path} disabled>{path} — {detail.state}</option>}
                      {options.map((item) => <option key={item.path} value={item.path}>{item.label}</option>)}
                    </select>
                    <div id={helpId} className="workflow-widget-detail">
                      {!path ? <p className="ui-help">The workflow keeps its own value.</p> : <>
                        <p className="ui-help"><b>Node #{detail.nodeId || '?'}</b> · {detail.node?._meta?.title || detail.node?.class_type || 'Unavailable node'}</p>
                        <p className="ui-help"><b>Widget:</b> <code>{path}</code></p>
                        <p className="ui-help"><b>Graph value{detail.value === null ? ' · null' : detail.value !== undefined ? ` · ${Array.isArray(detail.value) ? 'connection' : typeof detail.value}` : ''}:</b></p>
                        <pre className="workflow-value">{graphValue(detail.value)}</pre>
                        {detail.state === 'Connected' && <p className="ui-help">This is a link, not an editable widget. The mapping is retained but ignored during generation.</p>}
                        {detail.state === 'Missing widget' && <p className="ui-help">This saved path is unavailable. It is retained until you choose another widget or do not control.</p>}
                      </>}
                    </div>
                  </div>
                </li>
              })}
            </ol>
          </div>
          <details className="workflow-connections">
            <summary>Connected inputs ({connections.length}) · read only</summary>
            <p className="ui-help">These graph links are not mapping destinations. Their values are preserved.</p>
            {!connections.length && <p className="ui-help">This graph has no connected inputs.</p>}
            <dl>{connections.map((item) => <div key={item.path}><dt><code>{item.path}</code> · {item.title}</dt>
              <dd><code>{JSON.stringify(item.value)}</code></dd></div>)}</dl>
          </details>
          <footer className="ui-form-footer">
            <button className="ui-button ui-button--primary" type="button" disabled={!draft.name} aria-disabled={!!busy} onClick={save}>Save workflow</button>
            <button className="ui-button" type="button" aria-describedby="workflow-detect-help" aria-disabled={!!busy} onClick={detectMissing}>Detect missing slots</button>
            <button className="ui-button" type="button" aria-disabled={!!busy} onClick={cancel}>Cancel</button>
            <p className="ui-help" id="workflow-detect-help">Detection fills empty mappings only, keeping your manual choices. Review before saving.</p>
          </footer>
        </form>}
      </section>
    </div>
  </div>
}
