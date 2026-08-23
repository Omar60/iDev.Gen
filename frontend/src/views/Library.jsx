import React, { useEffect, useState } from 'react'
import { api, shotImage } from '../api'
import { go } from '../App.jsx'

/** A session across every model, one row per shoot, the cover photograph as
 *  thumbnail. The search box and the tag chips above the list both filter
 *  the same `/api/sessions` route; the tag list comes from the unfiltered
 *  payload so picking a chip does not hide the other ones. */
export default function Library() {
  const [search, setSearch] = useState('')   // what the user is typing
  const [q, setQ] = useState('')             // debounced into the request
  const [tag, setTag] = useState('')         // active tag filter, exact
  const [sessions, setSessions] = useState([])
  // Every tag currently in use, regardless of the filter. The chip row above
  // the list reads this: the user wants to see "the others" before clicking.
  const [tagsInUse, setTagsInUse] = useState([])
  const [error, setError] = useState('')

  // Wait 250ms after the last keystroke before firing the request. A network
  // round-trip per character would visibly lag on a long name search, and the
  // filtered route is the one the list is built from.
  useEffect(() => {
    const id = setTimeout(() => setQ(search.trim()), 250)
    return () => clearTimeout(id)
  }, [search])

  // The list itself. Empty `q` and `tag` mean "every session" — the spec's
  // no-filters case.
  useEffect(() => {
    const params = new URLSearchParams()
    if (q) params.set('q', q)
    if (tag) params.set('tag', tag)
    const qs = params.toString()
    api.get('/api/sessions' + (qs ? '?' + qs : ''))
      .then(setSessions)
      .catch((e) => setError(e.message))
  }, [q, tag])

  // The tag chips. Always fetched unfiltered so the chip row stays stable
  // while the list narrows; the unfiltered list is small and a second request
  // is the difference between a screen that explains itself and one that
  // quietly empties. The list re-reads after every filter change because a
  // future session adding a tag would otherwise show up on the chips only
  // after a refresh of the whole page.
  useEffect(() => {
    api.get('/api/sessions').then((all) => {
      const seen = new Set()
      const out = []
      for (const s of all) for (const t of (s.tags || [])) {
        const key = t.toLowerCase()
        if (seen.has(key)) continue
        seen.add(key)
        out.push(t)
      }
      out.sort((a, b) => a.localeCompare(b))
      setTagsInUse(out)
    }).catch(() => {})
  }, [q, tag])

  return (
    <>
      {error && <div className="error" onClick={() => setError('')}>{error}</div>}
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h1>Library</h1>
        <div className="row" style={{ flex: '0 1 420px' }}>
          <input value={search} onChange={(e) => setSearch(e.target.value)}
                 placeholder="Search by name, look or wardrobe"
                 title="Case-insensitive substring of the session's name, look or wardrobe" />
          {search && <button onClick={() => setSearch('')}>Clear</button>}
        </div>
      </div>
      <p className="muted">Every session across every model, newest first.</p>

      {tagsInUse.length > 0 && (
        <div className="row" style={{ margin: '8px 0 14px' }}>
          {tagsInUse.map((t) => (
            <button key={t} className={'chip' + (tag === t ? ' on' : '')}
                    onClick={() => setTag(tag === t ? '' : t)}
                    title={tag === t ? 'Click to clear the filter' : `Filter by ${t}`}>
              {t}
            </button>
          ))}
          {tag && <button onClick={() => setTag('')} className="chip">All</button>}
        </div>
      )}

      {sessions.length === 0 ? (
        // Plain text rather than a blank area: the spec is explicit that an
        // empty result reads as empty, not as a broken list.
        <p className="muted">Nothing matched.</p>
      ) : (
        <div className="cards" style={{ marginTop: 6 }}>
          {sessions.map((s) => (
            <a key={s.id} className="card" href={`#/session/${s.id}`}
               onClick={(e) => { e.preventDefault(); go(`/session/${s.id}`) }}>
              {s.cover_shot_id
                ? <img className="thumb" src={shotImage(s.cover_shot_id)} alt="" />
                : <div className="thumb" />}
              <div className="body">
                <div className="name">{s.name}</div>
                <div className="muted">{s.model_name}</div>
                <div className="muted">{s.done_count}/{s.shot_count} done</div>
                {(s.tags || []).length > 0 && (
                  <div className="row" style={{ marginTop: 6, gap: 4 }}>
                    {s.tags.map((t) => (
                      <span key={t} className="badge">{t}</span>
                    ))}
                  </div>
                )}
              </div>
            </a>
          ))}
        </div>
      )}
    </>
  )
}
