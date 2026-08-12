import React, { useEffect, useState } from 'react'
import { api, shotImage } from '../api'
import { go } from '../App.jsx'
import ShotsEditor, { blankShot } from './ShotsEditor.jsx'

export default function SessionView({ id }) {
  const [s, setS] = useState(null)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('all')
  const [zoom, setZoom] = useState(null)
  const [split, setSplit] = useState(50)
  const [adding, setAdding] = useState(null)
  const [workflows, setWorkflows] = useState([])

  const reload = () => api.get(`/api/sessions/${id}`).then(setS).catch((e) => setError(e.message))
  useEffect(() => {
    reload()
    api.get('/api/workflows').then(setWorkflows).catch(() => {})
  }, [id])

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
  // a keeper reuses it whole rather than recomposing and drifting. `reference` is
  // carried over too: a reshoot of an edit that came back as a fresh text2image
  // would silently be a different picture.
  const moreLikeThis = (shot) => setAdding([{
    label: shot.shot_label, prompt: shot.prompt, negative: shot.negative, count: 4,
    verbatim: true, reference: !!shot.use_reference,
    reference_strength: shot.reference_strength, seed: 0,
  }])

  // Same prompt AND same noise: edit one word in the panel and the difference you
  // see is that word, not another seed.
  // For a reference take this is the strength sweep: four rows, one prompt, one
  // seed, a different strength each. Whatever changes is the strength.
  const reshootSameSeed = (shot) => setAdding(
    (shot.use_reference ? [1.0, 1.5, 2.0, 3.0] : [null]).map((strength) => ({
      label: strength ? `${shot.shot_label} @${strength}` : shot.shot_label,
      prompt: shot.prompt, negative: shot.negative, count: 1,
      verbatim: true, reference: !!shot.use_reference,
      reference_strength: strength, seed: shot.seed,
    })))

  // The reference this shot really ran against, not whatever the session points
  // at now. A shot from before the feature existed has none, and gets no slider.
  const before = (shot) => (shot.reference_shot_ids || [])[0]

  const anchors = s.anchor_shot_ids || []
  // Up to three: the extra slots feed a graph that takes several images (a
  // character plus a garment, say). Clicking one already picked drops it.
  const toggleAnchor = (shot) => call(() => api.patch(`/api/sessions/${id}`, {
    anchor_shot_ids: anchors.includes(shot.id)
      ? anchors.filter((a) => a !== shot.id)
      : [...anchors, shot.id].slice(-3),
  }))

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
          {anchors.length > 0 && (
            <div className="anchor">
              {anchors.map((a) => <img key={a} src={shotImage(a)} alt="" title={`Reference — shot ${a}`} />)}
              <span className="muted">
                Reference · takes marked <b>ref</b> edit this photo, so their prompt is an
                instruction and carries no look.
              </span>
            </div>
          )}
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
          {/* The native file input renders its label in the browser's locale, so
              it is hidden behind our own, the same way Workflows does it. */}
          <label className="filebtn" title="Bring in a photo from outside — it lands as a finished shot, so it can be marked as a reference like any other">
            Import photo…
            <input type="file" accept="image/png,image/jpeg,image/webp" hidden
                   onChange={(e) => {
                     const file = e.target.files[0]
                     e.target.value = ''   // same file twice in a row still fires
                     if (file) call(() => api.upload(`/api/sessions/${id}/import`, file))
                   }} />
          </label>
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
            Same look ({s.look || 'none set'}) — only the take changes. A take marked
            <b> ref</b> skips the look entirely and edits the reference photo instead.
          </p>
          <ShotsEditor shots={adding} onChange={setAdding} />

          {/* Deciding to edit a keeper happens mid-shoot, looking at the gallery —
              not when the session was created. So the reference workflow is picked
              here, the moment a take is marked ref, or the run would be refused
              with no way to satisfy it. */}
          {adding.some((x) => x.reference) && (
            <div className="row" style={{ marginTop: 10 }}>
              <label style={{ width: 'auto', whiteSpace: 'nowrap' }}>Reference workflow</label>
              <select style={{ width: 'auto' }} value={s.reference_workflow_id ?? ''}
                      onChange={(e) => call(() => api.patch(`/api/sessions/${id}`, {
                        reference_workflow_id: e.target.value ? Number(e.target.value) : 0,
                      }))}>
                <option value="">— pick the graph that edits —</option>
                {workflows.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
              <span className="muted">
                {anchors.length ? 'an img2img or instruction-editing graph, with its reference image slot mapped'
                  : 'and mark a finished photo as the reference with 📎 — the ref takes have nothing to edit yet'}
              </span>
            </div>
          )}
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
          <div className={'shot' + (shot.rejected ? ' rejected' : '')
                          + (anchors.includes(shot.id) ? ' is-anchor' : '')} key={shot.id}>
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
              {shot.status === 'done' && (
                <button className="icon" onClick={() => toggleAnchor(shot)}
                        title={anchors.includes(shot.id)
                          ? 'Stop using this photo as the reference'
                          : 'Use as the reference — takes marked ref will edit this photo'}>
                  {anchors.includes(shot.id) ? '📌' : '📎'}
                </button>
              )}
              <button className="icon" title="More like this — same prompt, new seeds"
                      onClick={() => moreLikeThis(shot)}>⟳</button>
              <button className="icon"
                      title={shot.use_reference
                        ? 'Strength sweep — this prompt and seed at 1.0 / 1.5 / 2.0 / 3.0, so the only difference you see is the dial'
                        : 'Tweak on this same seed — edit the prompt, compare the change'}
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
            {before(zoom)
              // Before/after on one image rather than two side by side: an edit
              // that only moves a collar is invisible when the eye has to travel
              // between two frames.
              ? <div onClick={(e) => e.stopPropagation()}>
                  <div className="compare" style={{ '--split': `${split}%` }}>
                    <img src={shotImage(zoom.id)} alt="" />
                    <img className="before" src={shotImage(before(zoom))} alt="" />
                    <span className="handle" />
                  </div>
                  <input type="range" min="0" max="100" value={split}
                         onChange={(e) => setSplit(Number(e.target.value))} />
                  <div className="meta">
                    ← reference (shot {before(zoom)}) · this edit →
                  </div>
                </div>
              : <img src={shotImage(zoom.id)} alt="" />}
            <div className="meta">{zoom.prompt}<br />seed {zoom.seed} · {zoom.filename}</div>
          </div>
        </div>
      )}
    </>
  )
}
