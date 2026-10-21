import React, { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import '../ui/foundations.css'
import './SessionsList.css'

const STATUSES = { draft: 'Draft', running: 'Running', done: 'Done', cancelled: 'Cancelled', failed: 'Failed' }

export default function SessionsList() {
  const [sessions, setSessions] = useState([])
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const request = useRef(0)
  const searchInput = useRef(null)
  const errorNotice = useRef(null)

  // Search edits do not change rows; avoid rebuilding the list on each keystroke.
  const sessionTable = useMemo(() => (
    <table className="sessions-table" role="table" aria-label="Sessions">
      <thead role="rowgroup"><tr role="row">
        <th role="columnheader" scope="col">Session / model</th><th role="columnheader" scope="col">Status</th>
        <th role="columnheader" scope="col">Completed</th><th role="columnheader" scope="col">Created</th>
      </tr></thead>
      <tbody role="rowgroup">{sessions.map((session) => {
        const named = typeof session.name === 'string' && session.name.trim().length > 0
        const created = session.created_at || ''
        const label = named ? session.name : `Session #${session.id}`
        return <tr role="row" key={session.id}>
          <td role="cell" className="sessions-identity">
            <a className="sessions-name" href={`#/session/${session.id}`}>{label}</a>
            <div className="sessions-meta"><span>{session.model_name || 'Model unavailable'}</span>{`#${session.id}`}
              {!named && <span>Unnamed session</span>}</div>
          </td>
          <td role="cell" className="sessions-status"><span className="ui-state" data-status={session.status}
            data-tone={session.status === 'done' ? 'success' : session.status === 'failed' ? 'error' : undefined}>
            {STATUSES[session.status] || session.status || 'Unknown'}</span></td>
          <td role="cell" className="sessions-completed">{session.shot_count > 0 ? <div className="sessions-progress">
            {`${session.done_count} / ${session.shot_count} photos`}
            <progress max={session.shot_count} value={session.done_count} aria-label={`${label}: ${session.done_count} of ${session.shot_count} photos completed`} />
          </div> : <span className="ui-help">No shots yet</span>}</td>
          <td role="cell" className="sessions-created">{created ? <time dateTime={created} title={created}>
            <span>{created.slice(0, 10)}</span><span>{created.slice(11, 16)}</span>
          </time> : <span className="ui-help">Date unavailable</span>}</td>
        </tr>
      })}</tbody>
    </table>
  ), [sessions])

  const load = async (q = '', restoreFocus = false) => {
    const current = ++request.current
    if (restoreFocus) searchInput.current?.focus()
    setQuery(q); setLoading(true); setError('')
    const params = new URLSearchParams()
    if (q) params.set('q', q)
    try {
      const rows = await api.get('/api/sessions' + (q ? `?${params}` : ''))
      if (current !== request.current) return
      setSessions(rows)
    } catch (e) { if (current === request.current) setError(e.message) }
    finally { if (current === request.current) setLoading(false) }
  }
  useEffect(() => { load(); return () => { request.current += 1 } }, [])
  useEffect(() => { if (error) errorNotice.current?.focus() }, [error])

  const clear = () => { setSearch(''); load(''); searchInput.current?.focus() }
  return <div className="studio-ui ui-page sessions-page">
    <header className="ui-page-header">
      <h1 className="ui-page-title">Sessions</h1>
      <p className="ui-lead">Follow your shoots, from saved drafts to finished photographs.</p>
    </header>
    <form className="sessions-search" role="search" aria-label="Session search"
      onSubmit={(event) => { event.preventDefault(); load(search.trim()) }}>
      <div className="ui-field">
        <label className="ui-label" htmlFor="session-search">Search sessions</label>
        <input className="ui-control" id="session-search" name="q" type="search" autoComplete="off" spellCheck={false}
          placeholder="Name, look or wardrobe…" aria-describedby="session-search-help" value={search} ref={searchInput}
          onChange={(event) => setSearch(event.target.value)} />
        <p className="ui-help" id="session-search-help">Case-insensitive text search by name, look or wardrobe. Press Enter or Search.</p>
      </div>
      <div className="ui-actions sessions-search-actions">
        <button className="ui-button" type="submit">Search</button>
        {(search || query) && <button className="ui-button" type="button" onClick={clear}>Clear search</button>}
      </div>
    </form>
    <div className="sessions-summary" role="status">
      {loading ? query ? 'Searching sessions…' : 'Loading sessions…' : error ? 'Sessions unavailable.' :
        <><strong>{sessions.length} {sessions.length === 1 ? 'session' : 'sessions'}</strong><span>Newest first</span>
          {query && <span>Results for “{query}”</span>}</>}
    </div>
    {error && <div className="ui-notice" data-tone="error" role="alert" tabIndex={-1} ref={errorNotice}>
      <p className="sessions-error-text">{error}</p>
      <button className="ui-button" type="button" onClick={() => load(query, true)}>Retry loading sessions</button>
    </div>}
    {!loading && !error && (sessions.length ?
      sessionTable : <section className="ui-surface sessions-empty" aria-labelledby="sessions-empty-title">
        <h2 className="ui-section-title" id="sessions-empty-title">{query ? 'No sessions match' : 'No sessions yet'}</h2>
        <p className="ui-section-description">{query ? 'Try a different name, look or wardrobe, or clear the search.' :
          'Choose a model to create your first session.'}</p>
        {!query && <a className="ui-button" href="#/models">Choose a model</a>}
      </section>)}
  </div>
}
