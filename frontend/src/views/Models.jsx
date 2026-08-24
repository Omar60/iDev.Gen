import React, { useEffect, useState } from 'react'
import { api, loraPreview } from '../api'
import { go } from '../App.jsx'
import CanvasSize from './CanvasSize.jsx'

const EMPTY = {
  name: '', lora_name: '', trigger: '', lora_strength: 1.0,
  base_positive: '', base_negative: '', workflow_id: null, notes: '',
  settings: { width: 832, height: 1216, steps: 8, cfg: 1.0 },
}

export default function Models({ tab }) {
  const [models, setModels] = useState([])
  const [sessions, setSessions] = useState([])
  const [workflows, setWorkflows] = useState([])
  const [loras, setLoras] = useState([])
  const [models_, setModels_] = useState({})
  const [form, setForm] = useState(null)
  const [error, setError] = useState('')

  const reload = () => {
    api.get('/api/models').then(setModels).catch((e) => setError(e.message))
    api.get('/api/sessions').then(setSessions).catch(() => {})
  }
  useEffect(() => {
    reload()
    api.get('/api/workflows').then(setWorkflows).catch(() => {})
    api.get('/api/comfy/loras').then((d) => setLoras(d.loras)).catch(() => {})
    api.get('/api/comfy/models').then(setModels_).catch(() => {})
  }, [])

  const save = async () => {
    try {
      const { id } = await api.post('/api/models', form)
      setForm(null)
      reload()
      go(`/model/${id}`)
    } catch (e) { setError(e.message) }
  }

  if (tab === 'sessions') return <SessionsList sessions={sessions} />

  return (
    <>
      {error && <div className="error">{error}</div>}
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h1>Models</h1>
        <button className="primary" onClick={() => setForm(form ? null : { ...EMPTY })}>
          {form ? 'Cancel' : '+ New model'}
        </button>
      </div>
      <p className="muted">A model is one character LoRA with its default settings. Its sessions inherit them.</p>

      {form && (
        <div className="panel" style={{ margin: '14px 0' }}>
          <ModelForm form={form} setForm={setForm} loras={loras} workflows={workflows} models={models_} />
          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" disabled={!form.name} onClick={save}>Create model</button>
          </div>
        </div>
      )}

      <div className="cards" style={{ marginTop: 14 }}>
        {models.map((m) => (
          <a className="card" key={m.id} href={`#/model/${m.id}`}>
            {m.lora_name
              ? <img className="thumb" src={loraPreview(m.lora_name)} alt="" onError={(e) => { e.target.style.visibility = 'hidden' }} />
              : <div className="thumb" />}
            <div className="body">
              <div className="name">{m.name}</div>
              <div className="muted">{m.session_count} session(s)</div>
              <div className="muted" title={m.lora_name}>{m.lora_name.split(/[\\/]/).pop() || 'no LoRA'}</div>
            </div>
          </a>
        ))}
        {!models.length && <p className="muted">No models yet.</p>}
      </div>
    </>
  )
}

/** The base-model dropdown, grouped by loader kind: an all-in-one checkpoint and
 *  a standalone diffusion model are different files and different nodes. Empty
 *  keeps whatever the workflow itself loads. */
export function BaseModelSelect({ value, onChange, models, disabled }) {
  return (
    <select value={value ?? ''} disabled={disabled} onChange={(e) => onChange(e.target.value)}
            title="Must match the LoRA's family: a Krea LoRA on a Z-Image model fails or renders noise">
      <option value="">— the workflow's own —</option>
      {/* A value ComfyUI does not report — offline, renamed, moved — would render
          as "the workflow's own" and read as *no choice made*, which is the one
          thing it is not. Show it, and say why it is not in the list. */}
      {!!value && ![...(models.checkpoints || []), ...(models.unets || [])].includes(value) && (
        <option value={value}>{value} — not in ComfyUI's list</option>
      )}
      {!!models.checkpoints?.length && (
        <optgroup label="Checkpoints">
          {models.checkpoints.map((c) => <option key={c} value={c}>{c}</option>)}
        </optgroup>
      )}
      {!!models.unets?.length && (
        <optgroup label="Diffusion models (UNET)">
          {models.unets.map((u) => <option key={u} value={u}>{u}</option>)}
        </optgroup>
      )}
    </select>
  )
}

/** Sampler or scheduler, from the list ComfyUI reports for KSampler. Empty keeps
 *  the workflow's own, which is the right default: a graph tuned for one
 *  checkpoint already names the pair that checkpoint wants. Every Krea 2 finetune
 *  asks for a different one, which is why this is a slot and not a fixed graph. */
export function SamplerSelect({ value, onChange, options = [], disabled }) {
  return (
    <select value={value ?? ''} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      <option value="">— the workflow's own —</option>
      {/* Same reason as the base model: a value ComfyUI does not report would
          render as "the workflow's own" and read as no choice made. */}
      {!!value && !options.includes(value) && (
        <option value={value}>{value} — not in ComfyUI's list</option>
      )}
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  )
}

export function ModelForm({ form, setForm, loras, workflows, models = {} }) {
  const set = (k, v) => setForm({ ...form, [k]: v })
  const setS = (k, v) => setForm({ ...form, settings: { ...form.settings, [k]: v } })
  const s = form.settings || {}
  return (
    <>
      <div className="grid-form">
        <div>
          <label>Name</label>
          <input value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Ada" />
        </div>
        <div style={{ gridColumn: 'span 2' }}>
          <label>Base model</label>
          <BaseModelSelect value={s.checkpoint} models={models}
                           onChange={(v) => setS('checkpoint', v)} />
          <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
            Must belong to the same family as the LoRA below — a Krea LoRA on a Z-Image model fails.
          </div>
        </div>
        <div style={{ gridColumn: 'span 2' }}>
          <label>LoRA (from ComfyUI)</label>
          <select value={form.lora_name} onChange={(e) => set('lora_name', e.target.value)}>
            <option value="">— none —</option>
            {loras.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </div>
        <div>
          <label>Trigger word</label>
          <input value={form.trigger} onChange={(e) => set('trigger', e.target.value)} placeholder="4da woman" />
        </div>
        <div>
          <label>LoRA strength</label>
          <input type="number" step="0.05" value={form.lora_strength}
                 onChange={(e) => set('lora_strength', parseFloat(e.target.value))} />
        </div>
        <div>
          <label>Workflow</label>
          <select value={form.workflow_id ?? ''} onChange={(e) => set('workflow_id', e.target.value ? Number(e.target.value) : null)}>
            <option value="">— none —</option>
            {workflows.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </div>
        <CanvasSize width={s.width} height={s.height}
                    onChange={(w, h) => setForm({ ...form, settings: { ...s, width: w, height: h } })} />
        <div><label>Steps</label><input type="number" value={s.steps ?? 8} onChange={(e) => setS('steps', Number(e.target.value))} /></div>
        <div><label>CFG</label><input type="number" step="0.1" value={s.cfg ?? 1} onChange={(e) => setS('cfg', parseFloat(e.target.value))} /></div>
        <div>
          <label title="Each Krea 2 finetune asks for its own — euler, euler_ancestral, er_sde or res_2s. Only applied if the workflow maps the slot.">Sampler</label>
          <SamplerSelect value={s.sampler} options={models.samplers} onChange={(v) => setS('sampler', v)} />
        </div>
        <div>
          <label title="simple, beta or bong_tangent, depending on the checkpoint. Only applied if the workflow maps the slot.">Scheduler</label>
          <SamplerSelect value={s.scheduler} options={models.schedulers} onChange={(v) => setS('scheduler', v)} />
        </div>
      </div>
      <div className="grid-form" style={{ marginTop: 10 }}>
        <div>
          <label>Base prompt (prepended to every look)</label>
          <textarea value={form.base_positive} onChange={(e) => set('base_positive', e.target.value)}
                    placeholder="photo, 35mm, natural light" />
        </div>
        <div>
          <label>Default negative</label>
          <textarea value={form.base_negative} onChange={(e) => set('base_negative', e.target.value)} />
        </div>
      </div>
    </>
  )
}

function SessionsList({ sessions }) {
  return (
    <>
      <h1>Sessions</h1>
      <table style={{ marginTop: 12 }}>
        <thead>
          <tr><th>Session</th><th>Model</th><th>Status</th><th>Progress</th><th>Created</th></tr>
        </thead>
        <tbody>
          {sessions.map((s) => (
            <tr key={s.id} style={{ cursor: 'pointer' }} onClick={() => go(`/session/${s.id}`)}>
              <td>{s.name}</td>
              <td className="muted">{s.model_name}</td>
              <td><span className={'badge ' + s.status}>{s.status}</span></td>
              <td className="muted">{s.done_count}/{s.shot_count}</td>
              <td className="muted">{s.created_at.slice(0, 16).replace('T', ' ')}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!sessions.length && <p className="muted">No sessions yet.</p>}
    </>
  )
}
