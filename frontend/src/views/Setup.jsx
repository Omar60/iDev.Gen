import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import '../ui/foundations.css'

const FIELDS = [
  ['comfy_url', "ComfyUI API", 'http://127.0.0.1:8188'],
  ['comfy_output_dir', "ComfyUI output folder", 'where ComfyUI saves its images'],
  ['lora_dir', "LoRA folder (optional)", 'only used for LoRA preview thumbnails'],
  ['data_dir', "Data folder", 'database and sessions; relative paths are resolved from the repo'],
]

// The optional prompt assistant. Any OpenAI-compatible endpoint answers this —
// a local Ollama or LM Studio, or a hosted one — and leaving the URL empty is a
// setting, not a gap: the ✨ buttons only appear once there is something to ask.
const LLM_URL_HINT = 'e.g. http://127.0.0.1:11434/v1 — empty turns the ✨ buttons off'
const LLM_KEY_HINT = 'only needed by a hosted endpoint; stored in config.json, which stays out of git'

/** Where the assistant runs. Nothing here is a mode: every one of them ends up
 *  as a URL in `llm_url`, and the buttons only save typing the base by hand. So
 *  the choice is *derived* from the URL rather than stored next to it — one
 *  setting cannot then disagree with the other. */
const PROVIDERS = [
  { id: 'local', label: 'On this machine', url: '',
    hint: 'Ollama, LM Studio or llama.cpp — found by probing their usual ports. Free, private, '
        + 'and sharing the GPU with ComfyUI: a click while a session runs makes both wait.' },
  { id: 'openai', label: 'OpenAI', url: 'https://api.openai.com/v1',
    hint: 'Needs an API key and bills per token. Leaves the GPU to ComfyUI.' },
  { id: 'minimax', label: 'MiniMax', url: 'https://api.minimax.io/v1',
    hint: 'Needs an API key and bills per token. Configure a vision model explicitly for photo input; '
        + 'the provider may reject unsupported models.' },
]

const providerOf = (url) => {
  if (!url) return 'local'
  if (/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])/.test(url)) return 'local'
  return PROVIDERS.find((p) => p.url && url.startsWith(p.url))?.id || 'other'
}

/** One of the two model boxes.
 *
 *  A select, not a datalist: a datalist filters itself down to whatever is
 *  already in the box, so a field with a model picked shows a list of one. It
 *  falls back to a text box when the endpoint told us nothing — a hosted one
 *  that does not list its models still has to be typeable. */
function ModelSelect({ id, name, describedBy, value, onChange, models, empty, hint, ariaLabel, unlistedLabel }) {
  if (!models.length) {
    return <input className="ui-control" id={id} name={name} aria-describedby={describedBy}
                  aria-label={ariaLabel} value={value ?? ''} placeholder={hint} autoComplete="off" spellCheck={false}
                  onChange={(e) => onChange(e.target.value)} />
  }
  return (
    <select className="ui-control" id={id} name={name} aria-describedby={describedBy}
            aria-label={ariaLabel} value={value ?? ''} onChange={(e) => onChange(e.target.value)}>
      <option value="">{empty}</option>
      {/* A name the endpoint does not report — renamed, removed, or saved
          against another endpoint — reads as "nothing picked" if it is not
          shown, which is the one thing it is not. */}
      {!!value && !models.some((m) => m.id === value) && (
        <option value={value}>{value} — {unlistedLabel || 'not on this endpoint'}</option>
      )}
      {models.map((m) => (
        <option key={m.id} value={m.id}>{m.id}{m.params ? ` · ${m.params}B` : ''}</option>
      ))}
    </select>
  )
}

export default function Setup() {
  const [cfg, setCfg] = useState(null)
  const [detected, setDetected] = useState(null)
  const [llm, setLlm] = useState(null)      // {url, models:[{id, vision}]}
  const [probing, setProbing] = useState(false)
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')
  const [keyVisible, setKeyVisible] = useState(false)
  const [saving, setSaving] = useState(false)
  const [detecting, setDetecting] = useState(false)
  const errorNotice = useRef(null)
  const discoveryRequest = useRef(0)
  const detectionRequest = useRef(0)
  const detectionFieldEdits = useRef({ comfy_output_dir: 0, lora_dir: 0 })

  useEffect(() => { if (error) errorNotice.current?.focus() }, [error])

  /** Ask an endpoint what it can run — or, with no URL, find one. Quiet on the
   *  page load, loud when the button asked for it.
   *
   *  Declared above the early return on purpose: the effect below runs after a
   *  render that returned at `if (!cfg)`, and a `const` after that line has not
   *  been initialised in that render's scope by the time the effect calls it. */
  const findAssistant = async (url = '', quiet = false, key = '', expected = {}) => {
    const request = ++discoveryRequest.current
    const expectedEndpoint = expected.endpoint ?? cfg?.llm_url ?? url
    const expectedModel = expected.model ?? cfg?.llm_model ?? ''
    const expectedVisionModel = expected.visionModel ?? cfg?.llm_vision_model ?? ''
    const expectedKey = expected.key ?? key ?? ''
    setProbing(true)
    if (!quiet) { setError(''); setMsg('') }
    try {
      // POST: a hosted endpoint lists nothing without its key, and a key does
      // not belong in a query string.
      const found = await api.post('/api/llm/models', { url, key })
      if (request !== discoveryRequest.current) return
      setLlm(found)
      setCfg((cur) => {
        if (cur.llm_url !== expectedEndpoint
            || (cur.llm_model ?? '') !== expectedModel
            || (cur.llm_vision_model ?? '') !== expectedVisionModel
            || (cur.llm_key ?? '') !== expectedKey) return cur
        const textModel = cur.llm_model || found.models[0]?.id || ''
        const visualTextModel = found.models.find((m) => m.id === textModel && m.vision)
        const explicitVision = typeof cur.llm_vision_model === 'string'
          && cur.llm_vision_model.trim() ? cur.llm_vision_model : ''
        // A model already picked is a decision, so detection only fills blanks.
        // If that text model is reported as visual, save it explicitly first.
        return {
        ...cur,
        llm_url: found.url,
        llm_model: textModel,
        llm_vision_model: explicitVision || visualTextModel?.id
          || found.models.find((m) => m.vision)?.id || '',
        }
      })
    } catch (e) {
      if (request === discoveryRequest.current && !quiet) setError(e.message)
    } finally {
      if (request === discoveryRequest.current) setProbing(false)
    }
  }

  useEffect(() => {
    api.get('/api/config').then((c) => {
      setCfg(c)
      // Already configured: fill the dropdowns without being asked. Nothing is
      // written, and an endpoint that is off right now is not an error to show.
      if (c.llm_url) findAssistant(c.llm_url, true, c.llm_key, {
        endpoint: c.llm_url, model: c.llm_model ?? '',
        visionModel: c.llm_vision_model ?? '', key: c.llm_key ?? '',
      })
    }).catch((e) => setError(e.message))
  }, [])
  if (!cfg) return <div className="studio-ui ui-page">
    <header className="ui-page-header"><h1 className="ui-page-title">Setup</h1></header>
    {error
      ? <div className="ui-notice" data-tone="error" role="alert" ref={errorNotice} tabIndex={-1}>{error}</div>
      : <p className="ui-notice" role="status">Loading…</p>}
  </div>

  const provider = providerOf(cfg.llm_url)
  const found = llm?.models || []
  // MiniMax documents Flash but may omit it from /models for subscription keys.
  // Offer its ID without presenting documented support as detected capability.
  const textModels = provider === 'minimax' && found.length && !found.some((m) => m.id === 'MiniMax-M3.1-Flash-Preview')
    ? [...found, { id: 'MiniMax-M3.1-Flash-Preview', vision: false }]
    : found
  const visionModels = found.filter((m) => m.vision)
  const set = (k, v) => {
    if (k in detectionFieldEdits.current) detectionFieldEdits.current[k] += 1
    if (['llm_url', 'llm_model', 'llm_vision_model', 'llm_key'].includes(k)) {
      discoveryRequest.current += 1
      setProbing(false)
      if (k === 'llm_url') setLlm(null)
    }
    setCfg((cur) => {
      const next = { ...cur, [k]: v }
      if (k === 'llm_url' && cur.llm_url !== v) {
        next.llm_model = ''
        next.llm_vision_model = ''
      }
      if (k === 'llm_model' && !(typeof cur.llm_vision_model === 'string'
          && cur.llm_vision_model.trim()) && visionModels.some((model) => model.id === v)) {
        next.llm_vision_model = v
      }
      return next
    })
  }

  /** Switching where the assistant runs. The model names do not travel — they
   *  belong to the endpoint that had them — so they are cleared with it, and the
   *  new one is asked what it has straight away. */
  const pickProvider = (p) => {
    discoveryRequest.current += 1
    setProbing(false)
    setLlm(null)
    setError('')
    setCfg((cur) => ({ ...cur, llm_url: p.url, llm_model: '', llm_vision_model: '' }))
    // A hosted endpoint refuses to list anything without its key, and "it does
    // not list its models" is the wrong thing to say about a key nobody typed
    // yet. The row below says what is missing instead.
    if (p.url && !cfg.llm_key) return
    findAssistant(p.url, false, cfg.llm_key, {
      endpoint: p.url, model: '', visionModel: '', key: cfg.llm_key ?? '',
    })
  }

  const detect = async () => {
    const request = ++detectionRequest.current
    const edits = { ...detectionFieldEdits.current }
    setError(''); setMsg('')
    setDetecting(true)
    try {
      const d = await api.post('/api/config/detect')
      if (request !== detectionRequest.current) return
      setDetected(d)
      setCfg((cur) => ({
        ...cur,
        comfy_output_dir: d.comfy_output_dir.exists && detectionFieldEdits.current.comfy_output_dir === edits.comfy_output_dir
          ? d.comfy_output_dir.path : cur.comfy_output_dir,
        lora_dir: d.lora_dir.exists && detectionFieldEdits.current.lora_dir === edits.lora_dir
          ? d.lora_dir.path : cur.lora_dir,
      }))
    } catch (e) {
      if (request === detectionRequest.current) setError(e.message)
    }
    finally { if (request === detectionRequest.current) setDetecting(false) }
  }

  const save = async () => {
    setError(''); setMsg('')
    setSaving(true)
    try {
      // Every key the schema has: the route writes what it is given over
      // config.json, so one left out here is one deleted from the file.
      const r = await api.patch('/api/config', {
        comfy_url: cfg.comfy_url, comfy_output_dir: cfg.comfy_output_dir,
        lora_dir: cfg.lora_dir, data_dir: cfg.data_dir,
        llm_url: cfg.llm_url ?? '', llm_model: cfg.llm_model ?? '',
        llm_vision_model: cfg.llm_vision_model ?? '', llm_key: cfg.llm_key ?? '',
        // Nothing on this screen edits the per-checkpoint profiles; carried
        // through untouched so saving a path does not wipe them.
        checkpoints: cfg.checkpoints ?? {},
        // Same reason, for the room library registry the import screen writes.
        // Left undefined when the config never carried it, so the key is
        // omitted from the body and the schema's own default applies rather
        // than an empty list wiping the shipped library.
        room_libraries: cfg.room_libraries,
      })
      setMsg(r.restart_required ? 'Saved. Restart the app for the new data folder.' : 'Saved.')
      api.get('/api/config').then(setCfg)
    } catch (e) { setError(e.message) }
    finally { setSaving(false) }
  }

  return (
    <div className="studio-ui ui-page">
      <header className="ui-page-header">
        <h1 className="ui-page-title">Setup</h1>
        <p className="ui-lead">
          Connect ComfyUI, choose storage folders and configure the optional prompt assistant.
          Settings are written to <code>config.json</code>, which stays out of git.
        </p>
      </header>

      <form className="ui-stack" aria-label="Application settings" onSubmit={(event) => event.preventDefault()}>
        {error && <div className="ui-notice" data-tone="error" role="alert" ref={errorNotice} tabIndex={-1}>{error}</div>}

        <section className="ui-surface" aria-labelledby="setup-comfy-title">
          <div className="ui-section-heading"><h2 className="ui-section-title" id="setup-comfy-title">ComfyUI and storage</h2></div>
          <p className="ui-section-description">Detect asks the running ComfyUI where it was launched from and fills the folders in.</p>
          <div className="ui-form-grid">
            {FIELDS.map(([key, label, hint]) => (
              <div className="ui-field" key={key}>
                <label className="ui-label" htmlFor={`setup-${key}`}>{label}</label>
                <input className="ui-control" id={`setup-${key}`} name={key} aria-describedby={`setup-${key}-help`}
                       value={cfg[key] ?? ''} placeholder={hint} autoComplete="off" spellCheck={false}
                       onChange={(event) => set(key, event.target.value)} />
                <p className="ui-help" id={`setup-${key}-help`}>{hint}</p>
              </div>
            ))}
          </div>
          <div className="ui-actions ui-section-actions">
            <button className="ui-button" type="button" onClick={detecting ? undefined : detect} aria-disabled={detecting}>
              {detecting ? 'Detecting…' : 'Detect from ComfyUI'}
            </button>
            {detecting && <span className="ui-help" role="status">Looking for ComfyUI folders…</span>}
          </div>
          {detected && <p className="ui-notice ui-help-gap" role="status">
            ComfyUI found at <code>{detected.comfy_root}</code>
            {!detected.comfy_output_dir.exists && ' · no output/ folder there, set it by hand'}
          </p>}
        </section>

        <section className="ui-surface" aria-labelledby="setup-assistant-title">
          <div className="ui-section-heading">
            <h2 className="ui-section-title" id="setup-assistant-title">Prompt assistant</h2>
            <span className="ui-state">Optional</span>
          </div>
          <p className="ui-section-description">
            Writes takes and look text. Photo reading requires an explicit vision model and may be
            refused by the provider. The assistant suggests text in a box — nothing is generated or queued
            by it. Leave the endpoint empty and the app works exactly as before, with no ✨ buttons.
          </p>
          <div className="ui-actions" role="group" aria-label="Assistant location">
            {PROVIDERS.map((item) => (
              <button className="ui-button" type="button" key={item.id} aria-pressed={provider === item.id}
                      title={item.hint} disabled={probing} onClick={() => pickProvider(item)}>{item.label}</button>
            ))}
            {provider === 'other' && <span className="ui-state">Somewhere else</span>}
          </div>
          <p className="ui-help ui-help-gap">
            {(PROVIDERS.find((item) => item.id === provider) || {}).hint
              || 'Any OpenAI-compatible endpoint. The URL below is what it talks to.'}
          </p>

          <div className="ui-form-grid ui-section-actions">
            <div className="ui-field ui-field-wide">
              <label className="ui-label" htmlFor="setup-llm-url">Prompt assistant endpoint</label>
              <input className="ui-control" id="setup-llm-url" name="llm_url" aria-label="Prompt assistant endpoint"
                     aria-describedby="setup-llm-url-help" value={cfg.llm_url ?? ''} placeholder={LLM_URL_HINT}
                     autoComplete="off" spellCheck={false} onChange={(event) => set('llm_url', event.target.value)} />
              <p className="ui-help" id="setup-llm-url-help">{LLM_URL_HINT}</p>
            </div>
            <div className="ui-field">
              <label className="ui-label" htmlFor="setup-llm-model">Model</label>
              <ModelSelect id="setup-llm-model" name="llm_model" describedBy="setup-llm-model-help"
                           ariaLabel="Text model" value={cfg.llm_model} models={textModels} empty="— pick the model that writes —"
                           hint="the model that writes the takes and the look" onChange={(value) => set('llm_model', value)} />
              <p className="ui-help" id="setup-llm-model-help">
                writes the takes and the look. The biggest one is proposed; a smaller one answers faster
                {provider === 'minimax' && ' · Flash Preview requires provider access; enter the same ID below to use it for photos.'}
              </p>
            </div>
            <div className="ui-field">
              <label className="ui-label" htmlFor="setup-llm-vision-model">Vision model (optional)</label>
              <ModelSelect id="setup-llm-vision-model" name="llm_vision_model" describedBy="setup-llm-vision-model-help"
                           ariaLabel="Vision model" value={cfg.llm_vision_model} models={visionModels}
                           empty="— choose a detected vision model —" hint="enter a provider-supported vision model ID"
                           unlistedLabel="operator-declared" onChange={(value) => set('llm_vision_model', value)} />
              <p className="ui-help" id="setup-llm-vision-model-help">
                Photo requests require this explicit model and never fall back to the text model. Choose a detected
                vision model, or enter an ID as an operator declaration; the provider may still reject it.
              </p>
            </div>
            <div className="ui-field ui-field-wide">
              <label className="ui-label" htmlFor="setup-llm-key">API key (optional)</label>
              <div className="ui-secret-control">
                <input className="ui-control" id="setup-llm-key" name="llm_key" type={keyVisible ? 'text' : 'password'}
                       aria-describedby="setup-llm-key-help" value={cfg.llm_key ?? ''} placeholder="API key"
                       autoComplete="off" autoCapitalize="none" spellCheck={false}
                       onChange={(event) => set('llm_key', event.target.value)} />
                <button className="ui-button" type="button" aria-controls="setup-llm-key"
                        aria-label={keyVisible ? 'Hide API key' : 'Show API key'} onClick={() => setKeyVisible((value) => !value)}>
                  {keyVisible ? 'Hide' : 'Show'}
                </button>
              </div>
              <p className="ui-help" id="setup-llm-key-help">{LLM_KEY_HINT}</p>
            </div>
          </div>

          <div className="ui-actions ui-section-actions">
            <button className="ui-button" type="button" onClick={() => findAssistant(cfg.llm_url, false, cfg.llm_key)} disabled={probing}>
              {probing ? 'Looking…' : cfg.llm_url ? 'List its models' : 'Find an assistant'}
            </button>
            <span className="ui-help" role="status">
              {probing ? 'Checking available models…' : !llm && provider !== 'local' && !cfg.llm_key
                ? 'Add the API key above, then list its models.' : llm && <>
                  {llm.url} · {found.length
                    ? visionModels.length
                      ? <>{found.length} model{found.length === 1 ? '' : 's'}; {visionModels.length} reported vision support</>
                      : <>{found.length} model{found.length === 1 ? '' : 's'}; none reported vision support</>
                    : 'answers, but does not report model capabilities — enter a vision model ID only if it supports images'}
                </>}
            </span>
          </div>
        </section>

        <section className="ui-surface" aria-labelledby="setup-state-title">
          <div className="ui-section-heading"><h2 className="ui-section-title" id="setup-state-title">Current state</h2></div>
          <p className="ui-section-description">Status reflects the last configuration response. Save to refresh.</p>
          <dl className="ui-definition-list">
            <div><dt>Output folder</dt><dd><span className="ui-state" data-tone={cfg.output_dir_ok ? 'success' : 'error'}>
              {cfg.output_dir_ok ? 'ok' : 'missing or not set'}</span></dd></div>
            <div><dt>LoRA folder</dt><dd><span className="ui-state" data-tone={cfg.lora_dir_ok ? 'success' : undefined}>
              {cfg.lora_dir_ok ? 'ok' : 'not set (no previews)'}</span></dd></div>
            <div><dt>Data folder</dt><dd><code>{cfg.data_dir_resolved}</code></dd></div>
            <div><dt>Prompt assistant</dt><dd><span className="ui-state" data-tone={cfg.llm_ok ? 'success' : undefined}>
              {cfg.llm_ok ? 'on' : 'off (no endpoint set)'}</span></dd></div>
          </dl>
        </section>

        <footer className="ui-form-footer">
          <button className="ui-button ui-button--primary" type="button" onClick={saving ? undefined : save}
                  disabled={!cfg.comfy_url} aria-disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          <span className="ui-help" role="status">{msg || (saving ? 'Saving configuration…' : 'Changes take effect when saved.')}</span>
        </footer>
      </form>
    </div>
  )
}
