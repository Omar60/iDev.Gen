import React, { useEffect, useState } from 'react'
import { api } from '../api'

const FIELDS = [
  ['comfy_url', "ComfyUI API", 'http://127.0.0.1:8188'],
  ['comfy_output_dir', "ComfyUI output folder", 'where ComfyUI saves its images'],
  ['lora_dir', "LoRA folder (optional)", 'only used for LoRA preview thumbnails'],
  ['data_dir', "Data folder", 'database and sessions; relative paths are resolved from the repo'],
]

export default function Setup() {
  const [cfg, setCfg] = useState(null)
  const [detected, setDetected] = useState(null)
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')

  useEffect(() => { api.get('/api/config').then(setCfg).catch((e) => setError(e.message)) }, [])
  if (!cfg) return <p className="muted">{error || 'Loading…'}</p>

  const set = (k, v) => setCfg({ ...cfg, [k]: v })

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
      const r = await api.patch('/api/config', {
        comfy_url: cfg.comfy_url, comfy_output_dir: cfg.comfy_output_dir,
        lora_dir: cfg.lora_dir, data_dir: cfg.data_dir,
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
        </tbody>
      </table>
    </>
  )
}
