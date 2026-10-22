import React, { useEffect, useMemo, useRef, useState } from 'react'
import { api, shotImage } from '../api'
import { isResourceSession } from '../sessionPlan.js'
import '../ui/foundations.css'
import './Library.css'

function LibraryCover({ id }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => { setFailed(false) }, [id])
  return <div className="library-cover">
    {id && !failed ? <img src={shotImage(id)} alt="" width="480" height="640" loading="lazy"
      onError={() => setFailed(true)} /> : <span>{failed ? 'Cover unavailable' : 'No cover yet'}</span>}
  </div>
}

function tagsFrom(sessions) {
  const seen = new Set()
  const out = []
  for (const session of sessions) for (const tag of (session.tags || [])) {
    const key = tag.toLowerCase()
    if (!seen.has(key)) { seen.add(key); out.push(tag) }
  }
  return out.sort((a, b) => a.localeCompare(b))
}

export default function Library() {
  const [search, setSearch] = useState('')   // what the user is typing
  const [q, setQ] = useState('')             // debounced into the request
  const [tag, setTag] = useState('')         // active tag filter, exact
  const [sessions, setSessions] = useState([])
  const [tagsInUse, setTagsInUse] = useState([])
  const [error, setError] = useState('')
  const [tagsError, setTagsError] = useState('')
  const [loading, setLoading] = useState(true)
  const [retry, setRetry] = useState(0)
  const searchInput = useRef(null)

  // Wait 250ms after the last keystroke before firing the request. A network
  // round-trip per character would visibly lag on a long name search, and the
  // filtered route is the one the list is built from.
  useEffect(() => {
    const id = setTimeout(() => setQ(search.trim()), 250)
    return () => clearTimeout(id)
  }, [search])

  useEffect(() => {
    let active = true
    setLoading(true); setError(''); setTagsError('')
    const params = new URLSearchParams()
    if (q) params.set('q', q)
    if (tag) params.set('tag', tag)
    const qs = params.toString()
    // Share the unfiltered request; filtered searches still refresh all tags.
    const rows = api.get('/api/sessions' + (qs ? '?' + qs : ''))
    rows.then((data) => { if (active) setSessions(data) })
      .catch((e) => { if (active) setError(e.message) })
      .finally(() => { if (active) setLoading(false) })
    const inventory = qs ? api.get('/api/sessions') : rows
    inventory.then((data) => { if (active) setTagsInUse(tagsFrom(data)) })
      .catch((e) => { if (active && qs) setTagsError(e.message) })
    return () => { active = false }
  }, [q, tag, retry])

  // Search edits must not rebuild every cover during the debounce.
  const cards = useMemo(() => sessions.map((s) => (
    <li key={s.id}><a className="library-card" href={`#/session/${s.id}`}>
      <LibraryCover id={s.cover_shot_id} />
      <div className="library-card-body">
        <h2 className="library-card-name">{s.name?.trim() ? s.name : `Session #${s.id}`}</h2>
        <p className="library-model">{s.model_name || 'Model unavailable'}</p>
        <p className="library-count">{s.done_count}/{s.shot_count} done</p>
        {isResourceSession(s) && <div className="library-constants">
          {s.diagnostic ? <div className="library-diagnostic" role="alert">
            Resource plan needs attention{s.diagnostic.code ? <> (<code>{s.diagnostic.code}</code>)</> : null}: {s.diagnostic.message || 'Look and wardrobe are unavailable.'}
          </div> : <>
            <p><b>Look:</b> {typeof s.look === 'string' ? (s.look || 'No additional look constraint') : 'Unavailable'}</p>
            <p><b>Initial wardrobe:</b> {typeof s.wardrobe === 'string' ? (s.wardrobe || 'No wardrobe description') : 'Unavailable'}</p>
          </>}
        </div>}
        {!!s.tags?.length && <ul className="library-card-tags" aria-label="Session tags">
          {s.tags.map((t) => <li className="ui-state" key={t}>{t}</li>)}
        </ul>}
      </div>
    </a></li>
  )), [sessions])

  const clearSearch = () => { setSearch(''); setQ(''); searchInput.current?.focus() }
  const clearFilters = () => { clearSearch(); setTag('') }
  const filtered = !!(q || tag)
  const pending = loading || search.trim() !== q
  return <div className="studio-ui ui-page library-page">
    <header className="ui-page-header">
      <h1 className="ui-page-title">Library</h1>
      <p className="ui-lead">Every session across every model, newest first.</p>
    </header>
    <section className="ui-surface library-filters" aria-label="Library filters">
      <form role="search" aria-label="Library search" onSubmit={(event) => { event.preventDefault(); setQ(search.trim()) }}>
        <div className="ui-field">
          <label className="ui-label" htmlFor="library-search">Search sessions</label>
          <input className="ui-control" id="library-search" name="q" type="search" autoComplete="off" spellCheck={false}
            placeholder="Search by name, look or wardrobe…" aria-describedby="library-search-help" ref={searchInput}
            value={search} onChange={(event) => setSearch(event.target.value)} />
          <p className="ui-help" id="library-search-help">Searches name, look and wardrobe across all models. Results update as you type.</p>
        </div>
        {!!search && <button className="ui-button" type="button" onClick={clearSearch}>Clear search</button>}
      </form>
      {(tagsInUse.length > 0 || tag) && <fieldset className="library-tags">
        <legend className="ui-label">Filter by tag</legend>
        <p className="ui-help" id="library-tag-help">Matches a whole tag. Search and tag filters apply together.</p>
        <div className="ui-actions" aria-describedby="library-tag-help">
          <button className="ui-button" type="button" aria-pressed={!tag} onClick={() => setTag('')}>All tags</button>
          {tagsInUse.map((t) => <button className="ui-button" key={t} type="button" aria-pressed={tag === t}
            onClick={() => setTag(tag === t ? '' : t)}>{t}</button>)}
          {tag && !tagsInUse.includes(tag) && <button className="ui-button" type="button" aria-pressed="true" onClick={() => setTag('')}>{tag}</button>}
        </div>
      </fieldset>}
      {tagsError && <div className="ui-notice" data-tone="error" role="alert">
        <p>Could not refresh tags: {tagsError}</p>
        <button className="ui-button" type="button" onClick={() => { searchInput.current?.focus(); setRetry(value => value + 1) }}>Retry library</button>
      </div>}
    </section>
    <p className="library-summary ui-help" role="status">{pending ? 'Loading sessions…' : error ? 'Sessions unavailable.' :
      `${sessions.length} ${sessions.length === 1 ? 'session' : 'sessions'}${filtered ? ' matched' : ''} · Newest first`}</p>
    {error && <div className="ui-notice" data-tone="error" role="alert">
      <p>{error}</p><p>Your search and tag selection are kept.</p>
      <button className="ui-button" type="button" onClick={() => { searchInput.current?.focus(); setRetry(value => value + 1) }}>Retry loading sessions</button>
    </div>}
    {!error && <div aria-busy={pending} aria-label="Library results">
      <ul className="library-gallery" aria-label="Sessions">{cards}</ul>
      {!pending && !sessions.length && <section className="ui-surface library-empty" aria-labelledby="library-empty-title">
        <h2 className="ui-section-title" id="library-empty-title">{filtered ? 'Nothing matched' : 'No sessions yet'}</h2>
        <p>{filtered ? 'Try another name, look or wardrobe, or clear the filters.' : 'Create a session from a model to start your library.'}</p>
        {filtered ? <button className="ui-button" type="button" onClick={clearFilters}>Clear filters</button> :
          <a className="ui-button" href="#/models">Browse models</a>}
      </section>}
    </div>}
  </div>
}
