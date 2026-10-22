import React, { useEffect, useId, useMemo, useRef, useState } from 'react'
import { api, loraPreview } from '../api'
import { go } from '../App.jsx'
import CanvasSize from './CanvasSize.jsx'
import SessionsList from './SessionsList.jsx'
import ModelPreview from '../components/ModelPreview.jsx'
import '../ui/foundations.css'
import './Models.css'

const EMPTY = {
  name: '', lora_name: '', trigger: '', lora_strength: 1.0,
  base_positive: '', base_negative: '', workflow_id: null, notes: '',
  settings: { width: 832, height: 1216, steps: 8, cfg: 1.0 },
}

export default function Models({ tab }) {
  const [models, setModels] = useState([])
  const [workflows, setWorkflows] = useState([])
  const [loras, setLoras] = useState([])
  const [models_, setModels_] = useState({})
  const [form, setForm] = useState(null)
  const [error, setError] = useState('')
  const [loadError, setLoadError] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const formRef = useRef(null)
  const triggerRef = useRef(null)
  const errorRef = useRef(null)
  const loadErrorRef = useRef(null)

  const reload = async () => {
    setLoading(true); setLoadError('')
    try { setModels(await api.get('/api/models')) }
    catch (e) { setLoadError(e.message) }
    finally { setLoading(false) }
  }
  useEffect(() => {
    reload()
    api.get('/api/workflows').then(setWorkflows).catch(() => {})
    api.get('/api/comfy/loras').then((d) => setLoras(d.loras)).catch(() => {})
    api.get('/api/comfy/models').then(setModels_).catch(() => {})
  }, [])
  useEffect(() => { if (form) formRef.current?.querySelector('input')?.focus() }, [!!form])
  useEffect(() => { if (error) errorRef.current?.focus() }, [error, saving])
  useEffect(() => { if (loadError) loadErrorRef.current?.focus() }, [loadError])

  const cancel = () => { setForm(null); setError(''); triggerRef.current?.focus() }

  const save = async () => {
    setSaving(true); setError('')
    try {
      const { id } = await api.post('/api/models', form)
      setForm(null)
      reload()
      go(`/model/${id}`)
    } catch (e) { setError(e.message) }
    finally { setSaving(false) }
  }

  const modelCards = useMemo(() => {
    if (tab === 'sessions') return null
    return models.map((m) => (
      <li key={m.id}><a className="model-card" href={`#/model/${m.id}`}>
        <ModelPreview src={m.lora_name ? loraPreview(m.lora_name) : null} />
        <div className="model-card-body">
          <h2 className="model-card-name">{m.name?.trim() ? m.name : `Model #${m.id}`}</h2>
          <p className="model-card-count">{m.session_count} {m.session_count === 1 ? 'session' : 'sessions'}</p>
          <p className="model-card-file" title={m.lora_name}>{(m.lora_name || '').split(/[\\/]/).pop() || 'No LoRA selected'}</p>
        </div>
      </a></li>
    ))
  }, [models, tab])

  if (tab === 'sessions') return <SessionsList />

  return (
    <div className="studio-ui ui-page models-page">
      <header className="ui-page-header model-page-header">
        <div><h1 className="ui-page-title">Models</h1>
          <p className="ui-lead">Character LoRAs and the defaults their sessions inherit.</p></div>
        <button className="ui-button ui-button--primary" ref={triggerRef} type="button" aria-expanded={!!form}
          aria-controls="new-model-form" disabled={saving}
          onClick={() => { if (form) cancel(); else { setError(''); setForm({ ...EMPTY }) } }}>
          {form ? 'Cancel' : '+ New model'}</button>
      </header>

      {form && (
        <form className="model-edit-form ui-stack" id="new-model-form" ref={formRef} noValidate aria-labelledby="new-model-title"
          aria-busy={saving} onSubmit={(event) => { event.preventDefault(); save() }}>
          <h2 className="ui-section-title" id="new-model-title">New model</h2>
          {error && <div className="ui-notice" data-tone="error" role="alert" tabIndex={-1} ref={errorRef}>
            <p>{error}</p><p className="ui-help">Your entries are kept. Review the model settings and try again.</p>
          </div>}
          <ModelForm form={form} setForm={setForm} loras={loras} workflows={workflows} models={models_} />
          <div className="ui-form-footer">
            <button className="ui-button ui-button--primary" type="submit" disabled={!form.name || saving}>{saving ? 'Creating model…' : 'Create model'}</button>
            <button className="ui-button" type="button" onClick={cancel} disabled={saving}>Cancel creation</button>
          </div>
        </form>
      )}

      {loadError && <div className="ui-notice" data-tone="error" role="alert" tabIndex={-1} ref={loadErrorRef}>
        <p>{loadError}</p><button className="ui-button" type="button" onClick={reload}>Retry loading models</button>
      </div>}
      <p className="ui-help model-list-summary" role="status">{loading ? 'Loading models…' : loadError ? 'Models unavailable.' : `${models.length} ${models.length === 1 ? 'model' : 'models'}`}</p>
      <ul className="model-cards" aria-label="Models">
        {modelCards}
      </ul>
      {!loading && !loadError && !models.length && !form && <section className="ui-surface model-empty" aria-labelledby="models-empty-title">
        <h2 className="ui-section-title" id="models-empty-title">No models yet</h2>
        <p className="ui-section-description">Create a model to choose a character LoRA and its session defaults.</p>
        <button className="ui-button" type="button" onClick={() => { setForm({ ...EMPTY }); setError('') }}>Create your first model</button>
      </section>}
    </div>
  )
}

/** The base-model dropdown, grouped by loader kind: an all-in-one checkpoint and
 *  a standalone diffusion model are different files and different nodes. Empty
 *  keeps whatever the workflow itself loads. */
export function BaseModelSelect({ value, onChange, models, disabled, ...props }) {
  return (
    <select {...props} value={value ?? ''} disabled={disabled} onChange={(e) => onChange(e.target.value)}
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
export function SamplerSelect({ value, onChange, options = [], disabled, ...props }) {
  return (
    <select {...props} value={value ?? ''} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
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

export { ModelForm }
function ModelForm({ form, setForm, loras, workflows, models = {} }) {
  const prefix = useId()
  const fieldId = (name) => `${prefix}-${name}`
  const set = (k, v) => setForm({ ...form, [k]: v })
  const setS = (k, v) => setForm({ ...form, settings: { ...form.settings, [k]: v } })
  const s = form.settings || {}
  return (
    <div className="model-form ui-stack">
      <fieldset className="ui-surface model-form-section">
        <legend className="ui-section-title">Character identity</legend>
        <p className="ui-section-description">Choose the character file and the words that activate it.</p>
        <div className="ui-form-grid">
          <div className="ui-field"><label className="ui-label" htmlFor={fieldId('name')}>Name</label>
            <input className="ui-control" id={fieldId('name')} name="name" autoComplete="off" value={form.name}
              onChange={(e) => set('name', e.target.value)} placeholder="Ada…" /></div>
          <div className="ui-field"><label className="ui-label" htmlFor={fieldId('trigger')}>Trigger word</label>
            <input className="ui-control" id={fieldId('trigger')} name="trigger" autoComplete="off" spellCheck={false}
              value={form.trigger} onChange={(e) => set('trigger', e.target.value)} placeholder="4da woman…" /></div>
          <div className="ui-field"><label className="ui-label" htmlFor={fieldId('lora')}>LoRA (from ComfyUI)</label>
            <select className="ui-control" id={fieldId('lora')} name="lora_name" value={form.lora_name}
              onChange={(e) => set('lora_name', e.target.value)}>
              <option value="">— none —</option>
              {!!form.lora_name && !loras.includes(form.lora_name) && <option value={form.lora_name}>{form.lora_name} — not in ComfyUI's list</option>}
              {loras.map((l) => <option key={l} value={l}>{l}</option>)}
            </select></div>
          <div className="ui-field"><label className="ui-label" htmlFor={fieldId('strength')}>LoRA strength</label>
            <input className="ui-control" id={fieldId('strength')} name="lora_strength" type="number" step="0.05"
              value={form.lora_strength} onChange={(e) => set('lora_strength', parseFloat(e.target.value))} /></div>
        </div>
      </fieldset>
      <fieldset className="ui-surface model-form-section">
        <legend className="ui-section-title">Workflow and generation defaults</legend>
        <p className="ui-section-description">Sessions inherit these values. Empty model, sampler or scheduler selections keep the workflow's own values.</p>
        <div className="ui-form-grid">
          <div className="ui-field"><label className="ui-label" htmlFor={fieldId('workflow')}>Workflow</label>
            <select className="ui-control" id={fieldId('workflow')} name="workflow_id" value={form.workflow_id ?? ''}
              onChange={(e) => set('workflow_id', e.target.value ? Number(e.target.value) : null)}>
              <option value="">— none —</option>
              {form.workflow_id != null && !workflows.some((w) => w.id === form.workflow_id) &&
                <option value={form.workflow_id}>Workflow #{form.workflow_id} — unavailable</option>}
              {workflows.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select></div>
          <div className="ui-field"><label className="ui-label" htmlFor={fieldId('checkpoint')}>Base model</label>
            <BaseModelSelect className="ui-control" id={fieldId('checkpoint')} name="checkpoint" value={s.checkpoint} models={models}
              aria-describedby={fieldId('checkpoint-help')} onChange={(v) => setS('checkpoint', v)} />
            <p className="ui-help" id={fieldId('checkpoint-help')}>Must belong to the same family as the LoRA. A Krea LoRA on a Z-Image model fails.</p></div>
          <CanvasSize studio idPrefix={fieldId('canvas')} width={s.width} height={s.height}
            onChange={(w, h) => setForm({ ...form, settings: { ...s, width: w, height: h } })} />
          <div className="ui-field"><label className="ui-label" htmlFor={fieldId('steps')}>Steps</label>
            <input className="ui-control" id={fieldId('steps')} name="steps" type="number" value={s.steps ?? 8} onChange={(e) => setS('steps', Number(e.target.value))} /></div>
          <div className="ui-field"><label className="ui-label" htmlFor={fieldId('cfg')}>CFG</label>
            <input className="ui-control" id={fieldId('cfg')} name="cfg" type="number" step="0.1" value={s.cfg ?? 1} onChange={(e) => setS('cfg', parseFloat(e.target.value))} /></div>
          <div className="ui-field"><label className="ui-label" htmlFor={fieldId('sampler')}>Sampler</label>
            <SamplerSelect className="ui-control" id={fieldId('sampler')} name="sampler" value={s.sampler} options={models.samplers}
              aria-describedby={fieldId('sampling-help')} onChange={(v) => setS('sampler', v)} /></div>
          <div className="ui-field"><label className="ui-label" htmlFor={fieldId('scheduler')}>Scheduler</label>
            <SamplerSelect className="ui-control" id={fieldId('scheduler')} name="scheduler" value={s.scheduler} options={models.schedulers}
              aria-describedby={fieldId('sampling-help')} onChange={(v) => setS('scheduler', v)} /></div>
          <p className="ui-help ui-field-wide" id={fieldId('sampling-help')}>Use the sampler and scheduler recommended for your checkpoint. They apply only when the workflow maps their slots.</p>
        </div>
      </fieldset>
      <fieldset className="ui-surface model-form-section">
        <legend className="ui-section-title">Prompt defaults</legend>
        <p className="ui-section-description">The base prompt is prepended to every look; the negative provides the session default.</p>
        <div className="ui-form-grid">
          <div className="ui-field"><label className="ui-label" htmlFor={fieldId('positive')}>Base prompt (prepended to every look)</label>
            <textarea className="ui-control" id={fieldId('positive')} name="base_positive" value={form.base_positive}
              onChange={(e) => set('base_positive', e.target.value)} placeholder="photo, 35mm, natural light…" /></div>
          <div className="ui-field"><label className="ui-label" htmlFor={fieldId('negative')}>Default negative</label>
            <textarea className="ui-control" id={fieldId('negative')} name="base_negative" value={form.base_negative} onChange={(e) => set('base_negative', e.target.value)} /></div>
        </div>
      </fieldset>
    </div>
  )
}
