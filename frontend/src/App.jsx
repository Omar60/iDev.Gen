import React, { useEffect, useState } from 'react'
import { api } from './api'
import Models from './views/Models.jsx'
import ModelDetail from './views/ModelDetail.jsx'
import SessionView from './views/SessionView.jsx'
import Workflows from './views/Workflows.jsx'
import Setup from './views/Setup.jsx'

// Hash router: three views plus one detail page. react-router would be a whole
// dependency for what `location.hash` already does.
function useHash() {
  const [hash, setHash] = useState(() => window.location.hash || '#/')
  useEffect(() => {
    const on = () => setHash(window.location.hash || '#/')
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return hash
}

export const go = (path) => { window.location.hash = path }

function ComfyStatus() {
  const [st, setSt] = useState(null)
  useEffect(() => {
    const tick = () => api.get('/api/comfy/status').then(setSt).catch(() => setSt({ online: false }))
    tick()
    const id = setInterval(tick, 5000)
    return () => clearInterval(id)
  }, [])
  const vram = st?.vram_free ? `${(st.vram_free / 1e9).toFixed(1)}/${(st.vram_total / 1e9).toFixed(0)} GB` : ''
  return (
    <div className="status">
      {st && !st.output_dir_ok &&
        <a className="badge failed" href="#/setup"
           title={`comfy_output_dir: ${st.output_dir || 'not configured'}`}>
          Finish setup
        </a>}
      <span className={'dot' + (st?.online ? ' on' : '')} />
      {st?.online ? `ComfyUI ${st.version || ''} · ${vram}` : 'ComfyUI offline'}
      {st?.busy && <span className="badge running">generating</span>}
    </div>
  )
}

export default function App() {
  const hash = useHash()
  const parts = hash.replace('#/', '').split('/')
  const view = parts[0] || 'models'   // '#/' yields [''], not undefined
  const arg = parts[1]

  return (
    <>
      <header className="topbar">
        <span className="brand">iDev<span style={{ color: 'var(--accent)' }}>.Gen</span></span>
        <nav>
          <a href="#/models">Models</a>
          <a href="#/sessions">Sessions</a>
          <a href="#/workflows">Workflows</a>
          <a href="#/setup">Setup</a>
        </nav>
        <span className="spacer" />
        <ComfyStatus />
      </header>
      <main>
        {view === 'model' && <ModelDetail id={Number(arg)} />}
        {view === 'session' && <SessionView id={Number(arg)} />}
        {view === 'workflows' && <Workflows />}
        {view === 'setup' && <Setup />}
        {(view === 'models' || view === 'sessions') && <Models tab={view} />}
      </main>
    </>
  )
}
