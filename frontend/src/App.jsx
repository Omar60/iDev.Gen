import React, { useEffect, useState } from 'react'
import { api } from './api'
import { setCatalogue } from './kinds.js'
import { setWardrobe } from './wardrobe.js'
import Models from './views/Models.jsx'
import ModelDetail from './views/ModelDetail.jsx'
import SessionView from './views/SessionView.jsx'
import Workflows from './views/Workflows.jsx'
import Setup from './views/Setup.jsx'
import Library from './views/Library.jsx'
import Slideshow from './views/Slideshow.jsx'
import { Judge } from './views/Judge.jsx'
import Catalogue from './views/Catalogue.jsx'
import RoomImport from './views/RoomImport.jsx'
import Resources from './views/Resources.jsx'
import Looks from './views/Looks.jsx'
import AppShell from './components/AppShell.jsx'

// Hash router: views and their detail pages. react-router would be a whole
// dependency for what `location.hash` already does. The query string is
// stripped before splitting, so the slideshow's `#/slideshow?interval=3…` is
// still routed to its view and the view itself reads the params.
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

export default function App() {
  const hash = useHash()
  // Strip the query string so a settings-bearing hash like
  // `#/slideshow?interval=3` still routes to the slideshow view; the view
  // itself reads the query params from the hash it gets.
  const path = hash.replace('#/', '').split('?')[0]
  const parts = path.split('/')
  const view = parts[0] || 'models'   // '#/' yields [''], not undefined
  const arg = parts[1]

  useEffect(() => {
    api.get('/api/components?all=1')
      .then((data) => setCatalogue(data || []))
      .catch(() => {})
    api.get('/api/wardrobe')
      .then((data) => setWardrobe(data))
      .catch(() => {})
  }, [])

  return (
    <AppShell view={view} routeKey={path}>
      {view === 'model' && <ModelDetail id={Number(arg)} />}
      {view === 'session' && <SessionView id={Number(arg)} />}
      {view === 'resources' && <Resources requestedModelId={arg} />}
      {view === 'looks' && <Looks />}
      {view === 'judge' && <Judge />}
      {view === 'catalogue' && <Catalogue />}
      {view === 'rooms' && <RoomImport />}
      {view === 'workflows' && <Workflows />}
      {view === 'setup' && <Setup />}
      {view === 'library' && <Library />}
      {view === 'slideshow' && <Slideshow />}
      {(view === 'models' || view === 'sessions') && <Models tab={view} />}
    </AppShell>
  )
}
