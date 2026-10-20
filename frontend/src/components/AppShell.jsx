import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import './AppShell.css'

const groups = [
  ['Workspace', [['sessions', 'Sessions'], ['library', 'Library'], ['models', 'Models']]],
  ['Assets', [['resources', 'Resources'], ['looks', 'Looks'], ['catalogue', 'Catalogue'], ['rooms', 'Rooms']]],
  ['Review', [['judge', 'Judge'], ['slideshow', 'Slideshow']]],
  ['System', [['workflows', 'Workflows'], ['setup', 'Setup']]],
]

const icons = {
  aperture: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20 M7 3l6 10 M3 11h12 M8 21l6-10 M20 18l-6-10 M20 5H9 M12 2L6 12',
  sessions: 'M4 5h16v14H4z M4 9h16 M8 3v4 M16 3v4',
  library: 'M4 4h6v16H4z M14 4h6v16h-6z M7 8h0 M17 8h0',
  models: 'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M4 21v-2a8 8 0 0 1 16 0v2',
  resources: 'm12 3 9 5-9 5-9-5 9-5 M3 12l9 5 9-5 M3 16l9 5 9-5',
  looks: 'm12 3 3 6 6 3-6 3-3 6-3-6-6-3 6-3 3-6',
  catalogue: 'M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z',
  rooms: 'M3 21h18 M5 21V5l10-2v18 M15 8h4v13 M11 12h0',
  judge: 'M12 3v18 M5 7h14 M5 7l-3 7h6L5 7 M19 7l-3 7h6l-3-7 M8 21h8',
  slideshow: 'M3 4h18v14H3z M9 21h6 M12 18v3 m-2-13 5 3-5 3V8',
  workflows: 'M3 3h6v6H3z M15 15h6v6h-6z M6 9v9h9 M9 6h9v9',
  setup: 'M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6',
  menu: 'M4 6h16 M4 12h16 M4 18h16',
  close: 'm6 6 12 12 M6 18 18 6',
}

function Icon({ name }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={icons[name]} />
  </svg>
}

export function ComfyStatus() {
  const [status, setStatus] = useState(null)
  useEffect(() => {
    let active = true
    let request = 0
    const tick = async () => {
      const current = ++request
      try {
        const next = await api.get('/api/comfy/status')
        if (active && current === request) setStatus(next)
      } catch {
        if (active && current === request) setStatus({ online: false })
      }
    }
    tick()
    const timer = setInterval(tick, 5000)
    return () => { active = false; clearInterval(timer) }
  }, [])
  const state = status === null ? 'Checking' : status.online ? 'Online' : 'Offline'
  const vram = status?.vram_total > 0 && Number.isFinite(status.vram_free)
    ? `${(status.vram_free / 1e9).toFixed(1)}/${(status.vram_total / 1e9).toFixed(0)} GB` : ''
  return <div className="shell-comfy">
    <span className="shell-connection" role="status">
      <span className={`shell-dot ${state.toLowerCase()}`} aria-hidden="true" />
      <span>ComfyUI <strong>{state}</strong></span>
    </span>
    {status?.online && (status.version || vram) && <span className="shell-comfy-detail">
      {status.version}{status.version && vram ? ' · ' : ''}{vram}
    </span>}
    {status?.output_dir_ok === false && <a className="badge failed" href="#/setup"
      title={`comfy_output_dir: ${status.output_dir || 'not configured'}`}>Finish setup</a>}
    {status?.busy && (status.running_session
      ? <a className="badge running" href={`#/session/${status.running_session}`}>generating · #{status.running_session}</a>
      : <span className="badge running">generating</span>)}
  </div>
}

export default function AppShell({ view, routeKey, children }) {
  const [open, setOpen] = useState(false)
  const trigger = useRef(null)
  const sidebar = useRef(null)
  const main = useRef(null)
  const returnFocus = useRef(null)
  const previousRoute = useRef(routeKey)
  const active = view === 'model' ? 'models' : view === 'session' ? 'sessions' : view
  const group = groups.find(([, items]) => items.some(([key]) => key === active))
  const title = view === 'model' ? 'Model details' : view === 'session' ? 'Session editor'
    : group?.[1].find(([key]) => key === active)?.[1] || 'iDev.Gen'

  const close = () => { returnFocus.current = trigger.current; setOpen(false) }

  useEffect(() => {
    if (previousRoute.current !== routeKey) {
      previousRoute.current = routeKey
      returnFocus.current = main.current
      setOpen(false)
      if (!open) { main.current?.focus(); returnFocus.current = null }
    }
  }, [routeKey])

  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 1200px)')
    const resize = () => {
      if (desktop.matches) {
        setOpen(false)
        if (sidebar.current?.contains(document.activeElement)) returnFocus.current = main.current
      } else if (sidebar.current?.contains(document.activeElement)) {
        trigger.current?.focus()
      }
    }
    desktop.addEventListener('change', resize)
    return () => desktop.removeEventListener('change', resize)
  }, [])

  useEffect(() => {
    if (!open) {
      returnFocus.current?.focus()
      returnFocus.current = null
      return
    }
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const initialFocus = sidebar.current?.querySelector('[aria-current="page"]')
      || sidebar.current?.querySelector('.shell-close')
    initialFocus?.focus()
    const trap = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        close()
      }
      if (event.key !== 'Tab') return
      const items = [...sidebar.current.querySelectorAll('a[href], button')]
      const first = items[0]
      const last = items[items.length - 1]
      if (event.shiftKey && (document.activeElement === first || !sidebar.current.contains(document.activeElement))) {
        event.preventDefault(); last.focus()
      } else if (!event.shiftKey && (document.activeElement === last || !sidebar.current.contains(document.activeElement))) {
        event.preventDefault(); first.focus()
      }
    }
    document.addEventListener('keydown', trap, true)
    return () => {
      document.body.style.overflow = overflow
      document.removeEventListener('keydown', trap, true)
    }
  }, [open])

  return <div className="app-shell">
    <a className="shell-skip" href="#main-content" inert={open}
      onClick={(event) => { event.preventDefault(); main.current?.focus() }}>Skip to content</a>
    {open && <div className="shell-backdrop" onClick={close} aria-hidden="true" />}
    <aside className={`shell-sidebar${open ? ' is-open' : ''}`} id="app-navigation" ref={sidebar}
      role={open ? 'dialog' : undefined} aria-modal={open ? 'true' : undefined}
      aria-label={open ? 'Navigation' : 'Sidebar'}>
      <div className="shell-brand-row">
        <a className="shell-brand" href="#/models" onClick={() => { if (open) close() }}>
          <span className="shell-brand-mark"><Icon name="aperture" /></span> iDev<span>.Gen</span>
        </a>
        <button className="shell-menu shell-close" type="button" aria-label="Close navigation" onClick={close}>
          <Icon name="close" />
        </button>
      </div>
      <nav aria-label="Main navigation" onClick={(event) => {
        if (open && event.target.closest('a')) close()
      }}>
        {groups.map(([name, items]) => <div className="shell-nav-group" key={name}>
          <div className="shell-group-label">{name}</div>
          {items.map(([key, label]) => <a key={key} href={`#/${key}`}
            aria-current={active === key ? 'page' : undefined}>
            <Icon name={key} /><span>{label}</span>
          </a>)}
        </div>)}
      </nav>
    </aside>
    <div className="shell-workspace" inert={open}>
      <header className="shell-header">
        <div className="shell-context">
          <button className="shell-menu" ref={trigger} type="button" aria-label="Open navigation"
            aria-expanded={open} aria-controls="app-navigation" onClick={() => setOpen(true)}>
            <Icon name="menu" />
          </button>
          <div className="shell-heading"><span>{group?.[0] || 'Workspace'}</span><strong>{title}</strong></div>
        </div>
        <ComfyStatus />
      </header>
      <main className="shell-main" id="main-content" ref={main} tabIndex={-1} aria-label={title}>
        <div className="shell-content">{children}</div>
      </main>
    </div>
  </div>
}
