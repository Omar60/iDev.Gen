import React, { useEffect, useState } from 'react'
import { api, shotImage } from '../api'
import { go } from '../App.jsx'
import ShotsEditor, { blankShot } from './ShotsEditor.jsx'

export default function SessionView({ id }) {
  const [s, setS] = useState(null)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('all')
  const [zoom, setZoom] = useState(null)
  const [adding, setAdding] = useState(null)

  const reload = () => api.get(`/api/sessions/${id}`).then(setS).catch((e) => setError(e.message))
  useEffect(() => { reload() }, [id])

  // Only poll while it runs: the queue is serial, one photo every few seconds.
  useEffect(() => {
    if (!s || s.status !== 'running') return
    const t = setInterval(reload, 2500)
    return () => clearInterval(t)
  }, [s?.status, id])

  if (!s) return <p className="muted">{error || 'Loading…'}</p>

  const done = s.shots.filter((x) => x.status === 'done').length
  const failed = s.shots.filter((x) => ['failed', 'cancelled'].includes(x.status)).length
  const pending = s.shots.filter((x) => x.status === 'pending').length
  const shots = s.shots.filter((x) => (
    filter === 'picks' ? x.rating >= 4 && !x.rejected
      : filter === 'keep' ? !x.rejected
        : true))

  const call = async (fn) => { try { await fn(); reload() } catch (e) { setError(e.message) } }

  const rate = (shot, rating) => call(async () => {
    await api.patch(`/api/shots/${shot.id}`, { rating: shot.rating === rating ? 0 : rating })
  })

  // The stored prompt already carries trigger + base + look, so reshooting from
  // a keeper reuses it whole rather than recomposing and drifting.
  const moreLikeThis = (shot) => setAdding([{
    label: shot.shot_label, prompt: shot.prompt, negative: shot.negative, count: 4,
    verbatim: true, seed: 0,
  }])

  // Same prompt AND same noise: edit one word in the panel and the difference you
  // see is that word, not another seed.
  const reshootSameSeed = (shot) => setAdding([{
    label: shot.shot_label, prompt: shot.prompt, negative: shot.negative, count: 1,
    verbatim: true, seed: shot.seed,
  }])

  return (
    <>
      {error && <div className="error" onClick={() => setError('')}>{error}</div>}
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div>
          <h1>{s.name}</h1>
          <p className="muted">
            <a href={`#/model/${s.model.id}`}>{s.model.name}</a> · {s.settings.width}×{s.settings.height} ·
            {' '}{s.settings.steps} steps · cfg {s.settings.cfg} · LoRA {s.settings.lora_strength}
          </p>
          {s.look && <p className="muted" style={{ marginTop: -6 }}><b>Look:</b> {s.look}</p>}
        </div>
        <div className="row">
          <span className={'badge ' + s.status}>{s.status}</span>
          {pending > 0 && s.status !== 'running' &&
            <button className="primary" onClick={() => call(() => api.post(`/api/sessions/${id}/run`))}>
              Run ({pending})
            </button>}
          {s.status === 'running' &&
            <button onClick={() => call(() => api.post(`/api/sessions/${id}/cancel`))}>Cancel</button>}
          {failed > 0 && s.status !== 'running' &&
            <button onClick={() => call(() => api.post(`/api/sessions/${id}/retry`))}>Retry {failed}</button>}
          <button onClick={() => setAdding(adding ? null : [blankShot()])}>+ Shots</button>
          <button className="danger" onClick={() => {
            if (confirm('Delete this session and its images?')) call(async () => {
              const r = await api.del(`/api/sessions/${id}`)
              if (r?.warning) alert(r.warning)
              go(`/model/${s.model.id}`)
            })
          }}>Delete</button>
        </div>
      </div>

      <div className="row" style={{ margin: '10px 0' }}>
        <div className="progress"><div style={{ width: `${(done / Math.max(1, s.shots.length)) * 100}%` }} /></div>
        <span className="muted">{done}/{s.shots.length} done{failed ? ` · ${failed} failed` : ''}</span>
        <span className="spacer" style={{ flex: 1 }} />
        <select style={{ width: 'auto' }} value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="all">All</option>
          <option value="keep">Without rejects</option>
          <option value="picks">Picks only (4★+)</option>
        </select>
      </div>

      {adding && (
        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>Add shots to this session</h3>
          <p className="muted" style={{ margin: '0 0 6px' }}>
            Same look ({s.look || 'none set'}) — only the take changes.
          </p>
          <ShotsEditor shots={adding} onChange={setAdding} />
          <div className="row" style={{ marginTop: 10 }}>
            <button className="primary" onClick={() => call(async () => {
              await api.post(`/api/sessions/${id}/shots`, { shots: adding, seed_mode: 'random' })
              setAdding(null)
            })}>Add</button>
            <button onClick={() => setAdding(null)}>Cancel</button>
          </div>
        </div>
      )}

      <div className="shots">
        {shots.map((shot) => (
          <div className={'shot' + (shot.rejected ? ' rejected' : '')} key={shot.id}>
            {shot.status === 'done'
              ? <img src={shotImage(shot.id)} alt={shot.shot_label} loading="lazy" onClick={() => setZoom(shot)} />
              : <div className="ph">
                  {shot.status === 'running' ? '⏳ generating…'
                    : shot.status === 'pending' ? '· queued'
                      : `⚠ ${shot.error || shot.status}`}
                </div>}
            <div className="bar">
              <div className="stars">
                {[1, 2, 3, 4, 5].map((n) => (
                  <span key={n} className={'star' + (shot.rating >= n ? ' on' : '')} onClick={() => rate(shot, n)}>★</span>
                ))}
              </div>
              <span className="spacer" style={{ flex: 1 }} />
              <button className="icon" title="More like this — same prompt, new seeds"
                      onClick={() => moreLikeThis(shot)}>⟳</button>
              <button className="icon" title="Tweak on this same seed — edit the prompt, compare the change"
                      onClick={() => reshootSameSeed(shot)}>⚖</button>
              <button className="icon" title={shot.rejected ? 'Restore' : 'Reject'}
                      onClick={() => call(() => api.patch(`/api/shots/${shot.id}`, { rejected: !shot.rejected }))}>
                {shot.rejected ? '↩' : '✕'}
              </button>
            </div>
            <div className="muted" style={{ padding: '0 6px 6px', fontSize: 11 }} title={shot.prompt}>
              {shot.shot_label} · seed {shot.seed}
            </div>
          </div>
        ))}
      </div>
      {!shots.length && <p className="muted">Nothing to show with this filter.</p>}

      {zoom && (
        <div className="lightbox" onClick={() => setZoom(null)}>
          <div>
            <img src={shotImage(zoom.id)} alt="" />
            <div className="meta">{zoom.prompt}<br />seed {zoom.seed} · {zoom.filename}</div>
          </div>
        </div>
      )}
    </>
  )
}
