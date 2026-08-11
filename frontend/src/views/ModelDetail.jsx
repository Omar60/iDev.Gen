import React, { useEffect, useState } from 'react'
import { api, shotImage } from '../api'
import { go } from '../App.jsx'
import { ModelForm, BaseModelSelect } from './Models.jsx'
import ShotsEditor, { blankShot } from './ShotsEditor.jsx'

export default function ModelDetail({ id }) {
  const [model, setModel] = useState(null)
  const [edit, setEdit] = useState(null)
  const [loras, setLoras] = useState([])
  const [baseModels, setBaseModels] = useState({})
  const [workflows, setWorkflows] = useState([])
  const [newSession, setNewSession] = useState(null)
  const [error, setError] = useState('')

  const reload = () => api.get(`/api/models/${id}`).then(setModel).catch((e) => setError(e.message))
  useEffect(() => {
    reload()
    api.get('/api/workflows').then(setWorkflows).catch(() => {})
    api.get('/api/comfy/loras').then((d) => setLoras(d.loras)).catch(() => {})
    api.get('/api/comfy/models').then(setBaseModels).catch(() => {})
  }, [id])

  if (!model) return <p className="muted">{error || 'Loading…'}</p>

  const saveModel = async () => {
    try { await api.patch(`/api/models/${id}`, edit); setEdit(null); reload() }
    catch (e) { setError(e.message) }
  }

  const startSession = () => setNewSession({
    model_id: id,
    name: `Session ${model.sessions.length + 1}`,
    look: '',
    shots: [blankShot()],
    workflow_id: model.workflow_id,
    settings: { ...model.settings, lora_strength: model.lora_strength },
    seed_mode: 'random',
    seed: 0,
  })

  const createSession = async () => {
    try {
      const { id: sid } = await api.post('/api/sessions', newSession)
      go(`/session/${sid}`)
    } catch (e) { setError(e.message) }
  }

  return (
    <>
      {error && <div className="error">{error}</div>}
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h1>{model.name}</h1>
        <div className="row">
          <button onClick={() => setEdit(edit ? null : { ...model })}>{edit ? 'Cancel' : 'Edit'}</button>
          <button className="primary" onClick={startSession} disabled={!!newSession}>+ New session</button>
          <button className="danger" onClick={async () => {
            if (confirm(`Delete the model "${model.name}" and all its sessions?`)) {
              await api.del(`/api/models/${id}`); go('/models')
            }
          }}>Delete</button>
        </div>
      </div>
      <p className="muted">
        {model.lora_name || 'no LoRA'} · trigger “{model.trigger || '—'}” · strength {model.lora_strength}
      </p>

      {edit && (
        <div className="panel" style={{ margin: '14px 0' }}>
          <ModelForm form={edit} setForm={setEdit} loras={loras} workflows={workflows} models={baseModels} />
          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" onClick={saveModel}>Save</button>
          </div>
        </div>
      )}

      {newSession && (
        <div className="panel" style={{ margin: '14px 0' }}>
          <h3>New session</h3>
          <div className="grid-form">
            <div>
              <label>Name</label>
              <input value={newSession.name} onChange={(e) => setNewSession({ ...newSession, name: e.target.value })} />
            </div>
            <div>
              <label>Workflow</label>
              <select value={newSession.workflow_id ?? ''}
                      onChange={(e) => setNewSession({ ...newSession, workflow_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">— the model's —</option>
                {workflows.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            </div>
            <div style={{ gridColumn: 'span 2' }}>
              <label>Base model</label>
              <BaseModelSelect value={newSession.settings.checkpoint} models={baseModels}
                               onChange={(v) => setNewSession({ ...newSession, settings: { ...newSession.settings, checkpoint: v } })} />
            </div>
            <div>
              <label>Seeds</label>
              <select value={newSession.seed_mode} onChange={(e) => setNewSession({ ...newSession, seed_mode: e.target.value })}>
                <option value="random">Random</option>
                <option value="fixed">Fixed (from…)</option>
              </select>
            </div>
            {newSession.seed_mode === 'fixed' && (
              <div>
                <label>Starting seed</label>
                <input type="number" value={newSession.seed} onChange={(e) => setNewSession({ ...newSession, seed: Number(e.target.value) })} />
              </div>
            )}
            <div><label>Width</label><input type="number" step="8" value={newSession.settings.width ?? 832}
              onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, width: Number(e.target.value) } })} /></div>
            <div><label>Height</label><input type="number" step="8" value={newSession.settings.height ?? 1216}
              onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, height: Number(e.target.value) } })} /></div>
            <div><label>Steps</label><input type="number" value={newSession.settings.steps ?? 8}
              onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, steps: Number(e.target.value) } })} /></div>
            <div><label>CFG</label><input type="number" step="0.1" value={newSession.settings.cfg ?? 1}
              onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, cfg: parseFloat(e.target.value) } })} /></div>
            <div><label>LoRA strength</label><input type="number" step="0.05" value={newSession.settings.lora_strength ?? 1}
              onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, lora_strength: parseFloat(e.target.value) } })} /></div>
          </div>

          <h3 style={{ marginTop: 16 }}>Look</h3>
          <p className="muted" style={{ margin: '0 0 6px' }}>
            Wardrobe, hair, styling and setting — identical in every photo of the session.
            Change the look and it is a different session.
          </p>
          <textarea rows={2} value={newSession.look}
                    placeholder="white summer dress, hair down, gold hoop earrings, on a beach at golden hour"
                    onChange={(e) => setNewSession({ ...newSession, look: e.target.value })} />

          <h3 style={{ marginTop: 16 }}>Shots</h3>
          <ShotsEditor shots={newSession.shots} onChange={(shots) => setNewSession({ ...newSession, shots })} />

          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" onClick={createSession}
                    disabled={!newSession.shots.some((s) => s.prompt.trim())}>
              Create session ({newSession.shots.reduce((n, s) => n + (s.prompt.trim() ? Math.max(1, s.count) : 0), 0)} photos)
            </button>
            <button onClick={() => setNewSession(null)}>Cancel</button>
          </div>
        </div>
      )}

      <h2>Sessions</h2>
      <div className="cards">
        {model.sessions.map((s) => (
          <a className="card" key={s.id} href={`#/session/${s.id}`}>
            {s.cover_shot_id
              ? <img className="thumb" src={shotImage(s.cover_shot_id)} alt="" />
              : <div className="thumb" />}
            <div className="body">
              <div className="name">{s.name}</div>
              <div className="muted">{s.done_count}/{s.shot_count} · <span className={'badge ' + s.status}>{s.status}</span></div>
            </div>
          </a>
        ))}
        {!model.sessions.length && <p className="muted">No sessions yet. Create one to start the shoot.</p>}
      </div>
    </>
  )
}
