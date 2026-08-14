import React, { useEffect, useState } from 'react'
import { api, shotImage } from '../api'
import { go } from '../App.jsx'
import ShotsEditor, { blankShot } from './ShotsEditor.jsx'
import AnglePicker from './AnglePicker.jsx'
import { BaseModelSelect } from './Models.jsx'
import { KINDS, forKind, sessionKind } from '../kinds.js'
import { composed } from '../enhance.js'

export default function SessionView({ id }) {
  const [s, setS] = useState(null)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('all')
  const [zoom, setZoom] = useState(null)
  const [split, setSplit] = useState(50)
  const [adding, setAdding] = useState(null)
  const [workflows, setWorkflows] = useState([])
  const [baseModels, setBaseModels] = useState({})
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [llm, setLlm] = useState(false)

  const reload = () => api.get(`/api/sessions/${id}`).then(setS).catch((e) => setError(e.message))
  useEffect(() => {
    reload()
    api.get('/api/workflows').then(setWorkflows).catch(() => {})
    api.get('/api/comfy/models').then(setBaseModels).catch(() => {})
    // Optional: with no endpoint configured the ✨ buttons simply do not appear.
    api.get('/api/config').then((c) => setLlm(!!c.llm_ok)).catch(() => {})
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

  // Null for a session created before kinds existed: no badge, no filtering and
  // no guidance beats a wrong guess about what that session was for.
  const kind = sessionKind(s)
  const anchors = s.anchor_shot_ids || []
  // The same two counts the run preflight uses: takes that need a photo to edit,
  // and takes that would produce one.
  const refTakes = s.shots.filter((x) => x.use_reference && x.status === 'pending').length
  const willShoot = s.shots.some((x) => !x.use_reference && x.status === 'pending')
  const running = s.status === 'running' || s.running
  // As many anchors as the reference workflow actually reads. Keeping three
  // regardless made 📎 on a keeper *add* a second reference to a graph with one
  // slot, and the run is then refused for a count mismatch — a guaranteed
  // refusal produced by the button whose whole job is picking the photo to edit.
  // Re-pointing a one-slot session is now one click, not unpin-then-pin. An
  // unknown workflow falls back to three, the most any graph can read.
  const refWf = workflows.find((w) => w.id === s.reference_workflow_id)
  const refSlots = refWf
    ? Math.max(1, ['reference', 'reference2', 'reference3'].filter((r) => refWf.node_map?.[r]).length)
    : 3
  // Clicking one already picked drops it.
  const toggleAnchor = (shot) => call(() => api.patch(`/api/sessions/${id}`, {
    anchor_shot_ids: anchors.includes(shot.id)
      ? anchors.filter((a) => a !== shot.id)
      : [...anchors, shot.id].slice(-refSlots),
  }))

  // "Now edit this one" is one decision, and it used to be four clicks in three
  // places: the kind chip, the reference workflow, 📎 and + Shots. Into another
  // session it was worse — download the photo and upload it back, because
  // nothing carried a shot across. Every kind that edits a photo is offered.
  const continuations = Object.entries(KINDS).filter(([, spec]) => spec.refKind)

  /** The editing graph for the kind we are switching to. An untagged graph is
   *  offered everywhere so it stays; one tagged for the job we are leaving does
   *  not, or a photoshoot turned camera-angles runs its takes through the edit
   *  graph. 0 clears it, and the panels already say how to pick one. */
  const refWfFor = (k) => {
    // Before the list has loaded every graph looks untagged, and clearing the
    // session's own pick over a race is the one outcome worth guarding against.
    if (!workflows.length) return s.reference_workflow_id || 0
    const cur = workflows.find((w) => w.id === s.reference_workflow_id)
    if (cur && (!cur.kind || cur.kind === KINDS[k].refKind)) return cur.id
    const tagged = workflows.filter((w) => w.kind === KINDS[k].refKind)
    return tagged.length === 1 ? tagged[0].id : 0
  }

  const continueWith = (shot, choice) => {
    const [where, k] = choice.split(':')
    if (where === 'here') {
      return call(async () => {
        await api.patch(`/api/sessions/${id}`, {
          settings: { kind: k }, anchor_shot_ids: [shot.id], reference_workflow_id: refWfFor(k),
        })
        setAdding([blankShot(k)])
      })
    }
    // The new session starts with no look and no takes: the look belongs to the
    // shoot that produced the photo, and an edit take carries none anyway.
    call(async () => {
      const { id: sid } = await api.post('/api/sessions', {
        model_id: s.model_id, name: `${s.name} — ${KINDS[k].label}`,
        workflow_id: s.workflow_id, reference_workflow_id: refWfFor(k) || null,
        settings: { ...s.settings, kind: k },
      })
      const copy = await api.post(`/api/sessions/${sid}/import?from_shot=${shot.id}`)
      await api.patch(`/api/sessions/${sid}`, { anchor_shot_ids: [copy.id] })
      go(`/session/${sid}`)
    })
  }

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
          {anchors.length > 0 ? (
            <div className="anchor">
              {anchors.map((a) => <img key={a} src={shotImage(a)} alt="" title={`Reference — shot ${a}`} />)}
              <span className="muted">
                Reference · takes marked <b>ref</b> edit this photo, so their prompt is an
                instruction and carries no look.
              </span>
            </div>
          ) : refTakes > 0 && (
            // The state that used to show nothing at all, which is the one state
            // where you need to be told: takes that edit a photo, and no photo.
            // Left to Run, it is a refusal several clicks after the decision.
            <p className="rule">
              <b>No reference photo yet.</b> {refTakes === 1 ? '1 take edits' : `${refTakes} takes edit`} one.
              {willShoot
                ? ' The first photo this session shoots becomes it, and the edits follow in the same Run.'
                : ' Nothing here shoots one, so Run is refused: Import photo… (it becomes the reference), '
                  + 'or 📎 a finished photo, or add a take with ref unticked.'}
            </p>
          )}
        </div>
        <div className="row">
          {kind && <span className="badge" title={KINDS[kind].blurb}>{KINDS[kind].label}</span>}
          <span className={'badge ' + s.status}>{s.status}</span>
          {pending > 0 && s.status !== 'running' &&
            <button className="primary" onClick={() => call(() => api.post(`/api/sessions/${id}/run`))}>
              Run ({pending})
            </button>}
          {s.status === 'running' &&
            <button onClick={() => call(() => api.post(`/api/sessions/${id}/cancel`))}>Cancel</button>}
          {failed > 0 && s.status !== 'running' &&
            <button onClick={() => call(() => api.post(`/api/sessions/${id}/retry`))}>Retry {failed}</button>}
          <button onClick={() => setSettingsOpen(!settingsOpen)}
                  title="The workflows and the base model this session shoots with">⚙ Settings</button>
          <button onClick={() => setAdding(adding ? null : [blankShot(kind)])}>+ Shots</button>
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

      {/* The three choices every refused Run is about. Each saves on change, like
          the reference workflow selector below: a Save button here would be one
          more thing to forget between the error message and the retry. */}
      {settingsOpen && (
        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>Settings</h3>
          {/* A shoot changes job halfway on purpose: edit the pose, keep the one
              that worked, then turn the camera on it. That is one session with
              two graphs in turn, so the kind moves with it — otherwise the
              selector below filters away the very graph the next batch needs. */}
          {/* The runner re-reads the session before every take, so a graph swapped
              mid-queue silently sends the rest of the shoot somewhere else. The
              queue is serial and short-lived; waiting is the whole fix. */}
          {running && (
            <p className="rule">
              This session is running. The remaining takes read these values as they
              come up, so changing one now would send the rest of the queue through a
              different graph. Wait for it to finish, or Cancel.
            </p>
          )}
          <div className="row" style={{ marginBottom: 10 }}>
            <label style={{ width: 'auto', margin: 0 }}>Kind</label>
            {Object.entries(KINDS).map(([k, spec]) => (
              <button key={k} className={'chip' + (kind === k ? ' on' : '')} title={spec.blurb}
                      disabled={running}
                      onClick={() => call(() => api.patch(`/api/sessions/${id}`, { settings: { kind: k } }))}>
                {spec.label}
              </button>
            ))}
          </div>
          <div className="grid-form">
            <div>
              <label title="The graph for takes with ref unticked — the ones painted from noise. An editing or camera-angle graph does not go here.">
                Workflow (new photos)
              </label>
              <select value={s.workflow_id ?? ''} disabled={running}
                      onChange={(e) => call(() => api.patch(`/api/sessions/${id}`,
                        { workflow_id: Number(e.target.value) || 0 }))}>
                <option value="">— the model's —</option>
                {forKind(workflows, 't2i').map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            </div>
            <div>
              <label title="The graph for takes marked ref — the ones that edit the reference photo.">
                Reference workflow (edits)
              </label>
              <select value={s.reference_workflow_id ?? ''} disabled={running}
                      onChange={(e) => call(() => api.patch(`/api/sessions/${id}`,
                        { reference_workflow_id: Number(e.target.value) || 0 }))}>
                <option value="">— none, text to image only —</option>
                {forKind(workflows, kind && KINDS[kind].refKind).map((w) => (
                  <option key={w.id} value={w.id}>{w.name}</option>
                ))}
              </select>
            </div>
            <div style={{ gridColumn: 'span 2' }}>
              <label title="Only applied to the workflow above, and only if it maps the slot. An editing graph loads its own model.">
                Base model
              </label>
              <BaseModelSelect value={s.settings.checkpoint} models={baseModels} disabled={running}
                               onChange={(v) => call(() => api.patch(`/api/sessions/${id}`,
                                 { settings: { checkpoint: v } }))} />
            </div>
            {/* The two dials an identity pass is made of: how far the edit may
                travel from the photo, and how hard the character LoRA pulls. They
                were only settable when the session was created, which is before
                you have the photo whose face drifted. */}
            <div>
              <label title="How far an img2img edit may travel from the reference. Low keeps the frame and repaints detail — 0.2 to 0.35 is an identity pass. High repaints the outfit and moves the pose.">
                Denoise
              </label>
              <input type="number" step="0.05" min="0" max="1" disabled={running}
                     value={s.settings.denoise ?? ''} placeholder="workflow's own"
                     onChange={(e) => call(() => api.patch(`/api/sessions/${id}`,
                       { settings: { denoise: e.target.value === '' ? null : parseFloat(e.target.value) } }))} />
            </div>
            <div>
              <label title="Only applied if the workflow maps it.">LoRA strength</label>
              <input type="number" step="0.05" min="0" disabled={running}
                     value={s.settings.lora_strength ?? ''} placeholder="workflow's own"
                     onChange={(e) => call(() => api.patch(`/api/sessions/${id}`,
                       { settings: { lora_strength: e.target.value === '' ? null : parseFloat(e.target.value) } }))} />
            </div>
          </div>
          <p className="muted" style={{ marginBottom: 0 }}>
            Photos already shot keep the settings they were shot with. These apply to what runs next.
          </p>
        </div>
      )}

      {adding && (
        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>Add shots to this session</h3>
          <p className="muted" style={{ margin: '0 0 6px' }}>
            Same look ({s.look || 'none set'}) — only the take changes. A take marked
            <b> ref</b> skips the look entirely and edits the reference photo instead.
          </p>
          {kind && KINDS[kind].rule && <p className="rule">{KINDS[kind].rule}</p>}
          {kind === 'angles' && (
            <AnglePicker llm={llm}
                         onAdd={(takes) => setAdding([...adding.filter((x) => x.prompt.trim()), ...takes])} />
          )}
          {/* No `onLook` here: the look belongs to the session, and `add_shots`
              re-reads it from the server anyway. A shoot whose wardrobe changed
              halfway is two sessions. */}
          <ShotsEditor kind={kind} shots={adding} onChange={setAdding} llm={llm}
                       context={composed(s.model, '')} look={s.look} />

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
                {forKind(workflows, kind && KINDS[kind].refKind).map((w) => (
                  <option key={w.id} value={w.id}>{w.name}</option>
                ))}
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
                <>
                  <button className="icon" onClick={() => toggleAnchor(shot)}
                          title={anchors.includes(shot.id)
                            ? 'Stop using this photo as the reference'
                            : 'Use as the reference — takes marked ref will edit this photo'}>
                    {anchors.includes(shot.id) ? '📌' : '📎'}
                  </button>
                  {/* A native menu on purpose: a popover would need its own
                      dismiss, focus and z-index for six items the browser
                      already knows how to show. */}
                  <select className="continue" value="" disabled={running}
                          title="Continue with this photo — as the reference of this session, or of a new one"
                          onChange={(e) => continueWith(shot, e.target.value)}>
                    <option value="">→</option>
                    <optgroup label="Continue here">
                      {continuations.map(([k, spec]) => (
                        <option key={k} value={`here:${k}`}>{spec.label}</option>
                      ))}
                    </optgroup>
                    <optgroup label="In a new session">
                      {continuations.map(([k, spec]) => (
                        <option key={k} value={`new:${k}`}>{spec.label}…</option>
                      ))}
                    </optgroup>
                  </select>
                </>
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
