import React, { useEffect, useState } from 'react'
import { api } from '../api'

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
    hint: 'Needs an API key. Of its models only MiniMax-M3 reads a photo, so that is the one '
        + 'for the vision box.' },
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
function ModelSelect({ value, onChange, models, empty, hint }) {
  if (!models.length) {
    return <input value={value ?? ''} placeholder={hint} onChange={(e) => onChange(e.target.value)} />
  }
  return (
    <select value={value ?? ''} onChange={(e) => onChange(e.target.value)}>
      <option value="">{empty}</option>
      {/* A name the endpoint does not report — renamed, removed, or saved
          against another endpoint — reads as "nothing picked" if it is not
          shown, which is the one thing it is not. */}
      {!!value && !models.some((m) => m.id === value) && (
        <option value={value}>{value} — not on this endpoint</option>
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

  /** Ask an endpoint what it can run — or, with no URL, find one. Quiet on the
   *  page load, loud when the button asked for it.
   *
   *  Declared above the early return on purpose: the effect below runs after a
   *  render that returned at `if (!cfg)`, and a `const` after that line has not
   *  been initialised in that render's scope by the time the effect calls it. */
  const findAssistant = async (url = '', quiet = false, key = '') => {
    setProbing(true)
    if (!quiet) { setError(''); setMsg('') }
    try {
      // POST: a hosted endpoint lists nothing without its key, and a key does
      // not belong in a query string.
      const found = await api.post('/api/llm/models', { url, key })
      setLlm(found)
      setCfg((cur) => ({
        ...cur,
        llm_url: found.url,
        // Only proposals: a model already picked is a decision, and a decision
        // is not overwritten by a detection. The list arrives biggest first, so
        // both of these are "the biggest one that can do the job" — a starting
        // point, and the dropdown is right there.
        llm_model: cur.llm_model || found.models[0]?.id || '',
        llm_vision_model: cur.llm_vision_model || found.models.find((m) => m.vision)?.id || '',
      }))
    } catch (e) { if (!quiet) setError(e.message) } finally { setProbing(false) }
  }

  useEffect(() => {
    api.get('/api/config').then((c) => {
      setCfg(c)
      // Already configured: fill the dropdowns without being asked. Nothing is
      // written, and an endpoint that is off right now is not an error to show.
      if (c.llm_url) findAssistant(c.llm_url, true, c.llm_key)
    }).catch((e) => setError(e.message))
  }, [])
  if (!cfg) return <p className="muted">{error || 'Loading…'}</p>

  const set = (k, v) => setCfg({ ...cfg, [k]: v })
  const provider = providerOf(cfg.llm_url)
  const found = llm?.models || []
  // Ollama says which of its models read photos. A hosted endpoint says nothing,
  // and then every model is a candidate: offering an empty box would hide the
  // very model that can do it.
  const visionModels = found.some((m) => m.vision) ? found.filter((m) => m.vision) : found

  /** Switching where the assistant runs. The model names do not travel — they
   *  belong to the endpoint that had them — so they are cleared with it, and the
   *  new one is asked what it has straight away. */
  const pickProvider = (p) => {
    setLlm(null)
    setError('')
    setCfg((cur) => ({ ...cur, llm_url: p.url, llm_model: '', llm_vision_model: '' }))
    // A hosted endpoint refuses to list anything without its key, and "it does
    // not list its models" is the wrong thing to say about a key nobody typed
    // yet. The row below says what is missing instead.
    if (p.url && !cfg.llm_key) return
    findAssistant(p.url, false, cfg.llm_key)
  }

  const detect = async () => {
    setError(''); setMsg('')
    try {
      const d = await api.post('/api/config/detect')
      setDetected(d)
      setCfg({
        ...cfg,
        comfy_output_dir: d.comfy_output_dir.exists ? d.comfy_output_dir.path : cfg.comfy_output_dir,
        lora_dir: d.lora_dir.exists ? d.lora_dir.path : cfg.lora_dir,
      })
    } catch (e) { setError(e.message) }
  }

  const save = async () => {
    setError(''); setMsg('')
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
      })
      setMsg(r.restart_required ? 'Saved. Restart the app for the new data folder.' : 'Saved.')
      api.get('/api/config').then(setCfg)
    } catch (e) { setError(e.message) }
  }

  return (
    <>
      {error && <div className="error">{error}</div>}
      <h1>Setup</h1>
      <p className="muted">
        Written to <code>config.json</code>, which stays out of git. Detect asks the running ComfyUI
        where it was launched from and fills the folders in.
      </p>

      <div className="panel" style={{ marginTop: 14 }}>
        <div className="grid-form">
          {FIELDS.map(([key, label, hint]) => (
            <div key={key} style={{ gridColumn: key === 'comfy_url' ? 'auto' : 'span 2' }}>
              <label>{label}</label>
              <input value={cfg[key] ?? ''} placeholder={hint} onChange={(e) => set(key, e.target.value)} />
              <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>{hint}</div>
            </div>
          ))}
        </div>

        <div className="row" style={{ marginTop: 14 }}>
          <button onClick={detect}>Detect from ComfyUI</button>
          <button className="primary" onClick={save} disabled={!cfg.comfy_url}>Save</button>
          {msg && <span className="muted">{msg}</span>}
        </div>

        <h3 style={{ marginTop: 18 }}>Prompt assistant (optional)</h3>
        <p className="muted" style={{ margin: '0 0 8px' }}>
          Writes the takes and the look, and reads the wardrobe off a photo. It suggests
          text in a box — nothing is generated or queued by it. Leave the endpoint empty
          and the app works exactly as before, with no ✨ buttons.
        </p>
        <div className="row" style={{ marginBottom: 10 }}>
          {PROVIDERS.map((p) => (
            <button key={p.id} className={'chip' + (provider === p.id ? ' on' : '')}
                    title={p.hint} disabled={probing} onClick={() => pickProvider(p)}>
              {p.label}
            </button>
          ))}
          {/* An endpoint that is none of the three is a real answer — a proxy, a
              second machine — so it gets a chip of its own rather than looking
              like nothing is selected. */}
          {provider === 'other' && <span className="chip on">Somewhere else</span>}
        </div>
        <p className="muted" style={{ margin: '0 0 10px' }}>
          {(PROVIDERS.find((p) => p.id === provider) || {}).hint
            || 'Any OpenAI-compatible endpoint. The URL below is what it talks to.'}
        </p>

        <div className="grid-form">
          <div style={{ gridColumn: 'span 2' }}>
            <label>Prompt assistant endpoint</label>
            <input value={cfg.llm_url ?? ''} placeholder={LLM_URL_HINT}
                   onChange={(e) => set('llm_url', e.target.value)} />
            <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>{LLM_URL_HINT}</div>
          </div>
          <div>
            <label>Model</label>
            <ModelSelect value={cfg.llm_model} models={found} empty="— pick the model that writes —"
                         hint="the model that writes the takes and the look"
                         onChange={(v) => set('llm_model', v)} />
            <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
              writes the takes and the look. The biggest one is proposed; a smaller one answers faster
            </div>
          </div>
          <div>
            <label>Vision model (optional)</label>
            {/* Only the ones that can actually read a photo: the others answer
                the photo buttons with an error and nothing else. */}
            <ModelSelect value={cfg.llm_vision_model} models={visionModels}
                         empty="— the model above —" hint="falls back to the model above"
                         onChange={(v) => set('llm_vision_model', v)} />
            <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
              reads a photo — the look from a photo, and the anchor. Falls back to the model above
            </div>
          </div>
          <div>
            <label>API key (optional)</label>
            <input value={cfg.llm_key ?? ''} placeholder={LLM_KEY_HINT}
                   onChange={(e) => set('llm_key', e.target.value)} />
            <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>{LLM_KEY_HINT}</div>
          </div>
        </div>

        <div className="row" style={{ marginTop: 10 }}>
          <button onClick={() => findAssistant(cfg.llm_url, false, cfg.llm_key)} disabled={probing}>
            {probing ? 'Looking…' : cfg.llm_url ? 'List its models' : 'Find an assistant'}
          </button>
          {!llm && provider !== 'local' && !cfg.llm_key && (
            <span className="muted">Add the API key above, then list its models.</span>
          )}
          {llm && (
            <span className="muted">
              {llm.url} · {found.length
                ? <>{found.length} model{found.length === 1 ? '' : 's'}, {visionModels.length} of
                    them {visionModels.length === found.length ? '— as far as it says —' : ''} can
                    read a photo</>
                // A hosted endpoint that only serves chat is perfectly usable;
                // it just cannot be asked what it has.
                : 'answers, but does not list its models — type the name in'}
            </span>
          )}
        </div>

        {detected && (
          <p className="muted" style={{ marginTop: 10 }}>
            ComfyUI found at <code>{detected.comfy_root}</code>
            {!detected.comfy_output_dir.exists && ' · no output/ folder there, set it by hand'}
          </p>
        )}
      </div>

      <h2>Current state</h2>
      <table>
        <tbody>
          <tr><td>Output folder</td><td>{cfg.output_dir_ok
            ? <span className="badge done">ok</span>
            : <span className="badge failed">missing or not set</span>}</td></tr>
          <tr><td>LoRA folder</td><td>{cfg.lora_dir_ok
            ? <span className="badge done">ok</span>
            : <span className="badge">not set (no previews)</span>}</td></tr>
          <tr><td>Data folder</td><td className="muted">{cfg.data_dir_resolved}</td></tr>
          <tr><td>Prompt assistant</td><td>{cfg.llm_ok
            ? <span className="badge done">on</span>
            : <span className="badge">off (no endpoint set)</span>}</td></tr>
        </tbody>
      </table>
    </>
  )
}
