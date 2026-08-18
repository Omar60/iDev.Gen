import React, { useEffect, useState } from 'react'
import { api, shotImage } from '../api'
import { go } from '../App.jsx'
import { ModelForm, BaseModelSelect, SamplerSelect } from './Models.jsx'
import ShotsEditor, { blankShot } from './ShotsEditor.jsx'
import AnglePicker from './AnglePicker.jsx'
import { KINDS, WORKFLOW_KINDS, forKind, checkpointProfile, profileSummary } from '../kinds.js'
import { composed, lookFromPhoto, photoDataUri, rewriteLook, rewriteWardrobe } from '../enhance.js'

export default function ModelDetail({ id }) {
  const [model, setModel] = useState(null)
  const [edit, setEdit] = useState(null)
  const [loras, setLoras] = useState([])
  const [baseModels, setBaseModels] = useState({})
  const [workflows, setWorkflows] = useState([])
  const [newSession, setNewSession] = useState(null)
  // The whole config: it also carries the per-checkpoint profiles.
  const [config, setConfig] = useState({})
  const llm = !!config.llm_ok
  const [writing, setWriting] = useState('')
  const [error, setError] = useState('')

  const reload = () => api.get(`/api/models/${id}`).then(setModel).catch((e) => setError(e.message))
  useEffect(() => {
    reload()
    api.get('/api/workflows').then(setWorkflows).catch(() => {})
    api.get('/api/comfy/loras').then((d) => setLoras(d.loras)).catch(() => {})
    api.get('/api/comfy/models').then(setBaseModels).catch(() => {})
    // No endpoint configured is not an error: the assistant is optional, and the
    // buttons simply do not appear.
    api.get('/api/config').then(setConfig).catch(() => {})
  }, [id])

  if (!model) return <p className="muted">{error || 'Loading…'}</p>

  const saveModel = async () => {
    try { await api.patch(`/api/models/${id}`, edit); setEdit(null); reload() }
    catch (e) { setError(e.message) }
  }

  const startSession = () => setNewSession({
    model_id: id,
    name: `Session ${model.sessions.length + 1}`,
    look: '',
    wardrobe: '',
    shots: [blankShot('shoot')],
    workflow_id: model.workflow_id || only('t2i'),
    reference_workflow_id: null,
    // The kind rides in the settings blob: it is read by the screens, never by
    // the runner, so it needs no column and no route of its own.
    settings: { ...model.settings, lora_strength: model.lora_strength, kind: 'shoot' },
    seed_mode: 'random',
    seed: 0,
  })

  // One candidate is not a choice worth making twice, so it is made here.
  const only = (tag) => {
    const tagged = workflows.filter((w) => w.kind === tag)
    return tagged.length === 1 ? tagged[0].id : null
  }

  /** Switching kind re-picks the graphs and the take defaults, but never the
   *  prompts already typed: changing your mind about the kind should not throw
   *  away the shoot you were writing. */
  const setKind = (kind) => setNewSession({
    ...newSession,
    settings: { ...newSession.settings, kind },
    reference_workflow_id: KINDS[kind].refKind ? only(KINDS[kind].refKind) : null,
    shots: newSession.shots.map((s) => (
      s.prompt.trim() ? s : { ...blankShot(kind), label: s.label })),
  })

  const kind = newSession?.settings.kind || 'shoot'

  /** Fills one box or both. A photo is read once and lands in both — the same six
   *  sections, split where the session splits: what holds still, and what the
   *  takes are free to move. */
  const writeLook = async (what, fn) => {
    setWriting(what); setError('')
    try {
      const written = await fn()
      const fields = typeof written === 'string' ? { look: written } : written
      if (Object.values(fields).some((v) => (v || '').trim())) {
        setNewSession((cur) => ({ ...cur, ...fields }))
      } else setError('the assistant answered nothing usable')
    } catch (e) { setError(e.message) } finally { setWriting('') }
  }

  const createSession = async () => {
    try {
      const { id: sid } = await api.post('/api/sessions', newSession)
      go(`/session/${sid}`)
    } catch (e) { setError(e.message) }
  }

  return (
    <>
      {error && <div className="error">{error}</div>}
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h1>{model.name}</h1>
        <div className="row">
          <button onClick={() => setEdit(edit ? null : { ...model })}>{edit ? 'Cancel' : 'Edit'}</button>
          <button className="primary" onClick={startSession} disabled={!!newSession}>+ New session</button>
          <button className="danger" onClick={async () => {
            if (confirm(`Delete the model "${model.name}" and all its sessions?`)) {
              await api.del(`/api/models/${id}`); go('/models')
            }
          }}>Delete</button>
        </div>
      </div>
      <p className="muted">
        {model.lora_name || 'no LoRA'} · trigger “{model.trigger || '—'}” · strength {model.lora_strength}
      </p>

      {edit && (
        <div className="panel" style={{ margin: '14px 0' }}>
          <ModelForm form={edit} setForm={setEdit} loras={loras} workflows={workflows} models={baseModels} />
          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" onClick={saveModel}>Save</button>
          </div>
        </div>
      )}

      {newSession && (
        <div className="panel" style={{ margin: '14px 0' }}>
          <h3>New session</h3>
          <div className="row" style={{ marginBottom: 4 }}>
            {Object.entries(KINDS).map(([k, spec]) => (
              <button key={k} className={'chip' + (kind === k ? ' on' : '')}
                      title={spec.blurb} onClick={() => setKind(k)}>{spec.label}</button>
            ))}
          </div>
          <p className="muted" style={{ margin: '0 0 8px' }}>{KINDS[kind].blurb}</p>
          {KINDS[kind].rule && <p className="rule">{KINDS[kind].rule}</p>}

          <div className="grid-form">
            <div>
              <label>Name</label>
              <input value={newSession.name} onChange={(e) => setNewSession({ ...newSession, name: e.target.value })} />
            </div>
            <div>
              <label title="The graph for takes with ref unticked — the ones painted from noise. An editing or camera-angle graph belongs in the next box, not this one.">
                Workflow{KINDS[kind].refKind ? ' (new photos)' : ''}
              </label>
              <select value={newSession.workflow_id ?? ''}
                      onChange={(e) => setNewSession({ ...newSession, workflow_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">— the model's —</option>
                {forKind(workflows, 't2i').map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            </div>
            {/* Text to image only: a session with no editing graph is exactly what
                the shoot kind is, so there is nothing to pick. */}
            {KINDS[kind].refKind && (
              <div>
                <label title="The graph that edits the session's reference photo instead of painting a new one. Takes marked ref run through it.">
                  Reference workflow (edits)
                </label>
                <select value={newSession.reference_workflow_id ?? ''}
                        onChange={(e) => setNewSession({ ...newSession, reference_workflow_id: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">— pick the graph that edits —</option>
                  {forKind(workflows, KINDS[kind].refKind).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                </select>
                {/* With nothing tagged, both selects list all the graphs and the
                    kind can pick nothing for you — which is how an editing graph
                    ends up in the box above. Say so where the choice is made. */}
                {!workflows.some((w) => w.kind === KINDS[kind].refKind) && (
                  <span className="muted">
                    No graph is tagged “{WORKFLOW_KINDS[KINDS[kind].refKind]}” yet. Tag one on the
                    <a href="#/workflows"> Workflows</a> screen and it gets picked for you.
                  </span>
                )}
              </div>
            )}
            <div style={{ gridColumn: 'span 2' }}>
              <label>Base model</label>
              {/* Its recommended settings come with it — the boxes below fill in,
                  and stay editable. Overwriting and not filling blanks: steps and
                  cfg always hold a value, so a fill-the-blanks rule would never
                  fire and the shoot would run at the last model's numbers. */}
              <BaseModelSelect value={newSession.settings.checkpoint} models={baseModels}
                               onChange={(v) => setNewSession({ ...newSession,
                                 settings: { ...newSession.settings, checkpoint: v,
                                             ...(checkpointProfile(config, v) || {}) } })} />
              {checkpointProfile(config, newSession.settings.checkpoint) && (
                <p className="muted" style={{ margin: '4px 0 0' }}>
                  This model's profile: <b>{profileSummary(checkpointProfile(config, newSession.settings.checkpoint))}</b>
                </p>
              )}
            </div>
            <div>
              <label>Seeds</label>
              <select value={newSession.seed_mode} onChange={(e) => setNewSession({ ...newSession, seed_mode: e.target.value })}>
                <option value="random">Random</option>
                <option value="fixed">Fixed (from…)</option>
              </select>
            </div>
            {newSession.seed_mode === 'fixed' && (
              <div>
                <label>Starting seed</label>
                <input type="number" value={newSession.seed} onChange={(e) => setNewSession({ ...newSession, seed: Number(e.target.value) })} />
              </div>
            )}
            <div><label>Width</label><input type="number" step="8" value={newSession.settings.width ?? 832}
              onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, width: Number(e.target.value) } })} /></div>
            <div><label>Height</label><input type="number" step="8" value={newSession.settings.height ?? 1216}
              onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, height: Number(e.target.value) } })} /></div>
            <div><label>Steps</label><input type="number" value={newSession.settings.steps ?? 8}
              onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, steps: Number(e.target.value) } })} /></div>
            <div><label>CFG</label><input type="number" step="0.1" value={newSession.settings.cfg ?? 1}
              onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, cfg: parseFloat(e.target.value) } })} /></div>
            <div><label title="Each Krea 2 finetune asks for its own. Only applied if the workflow maps the slot.">Sampler</label>
              <SamplerSelect value={newSession.settings.sampler} options={baseModels.samplers}
                onChange={(v) => setNewSession({ ...newSession, settings: { ...newSession.settings, sampler: v } })} /></div>
            <div><label title="simple, beta or bong_tangent, depending on the checkpoint. Only applied if the workflow maps the slot.">Scheduler</label>
              <SamplerSelect value={newSession.settings.scheduler} options={baseModels.schedulers}
                onChange={(v) => setNewSession({ ...newSession, settings: { ...newSession.settings, scheduler: v } })} /></div>
            <div><label>LoRA strength</label><input type="number" step="0.05" value={newSession.settings.lora_strength ?? 1}
              onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, lora_strength: parseFloat(e.target.value) } })} /></div>
            {/* Only worth showing once a reference workflow is picked: nothing else
                reads them, and an unmapped slot changes nothing. */}
            {newSession.reference_workflow_id && (
              <>
                <div><label title="How far an img2img edit may travel from the reference. Low keeps the face and changes little; high changes a lot and drifts.">Denoise</label>
                  <input type="number" step="0.05" min="0" max="1" value={newSession.settings.denoise ?? ''} placeholder="workflow's own"
                    onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, denoise: e.target.value === '' ? undefined : parseFloat(e.target.value) } })} /></div>
                <div><label title="IPAdapter weight, for graphs that use one.">Reference strength</label>
                  <input type="number" step="0.05" value={newSession.settings.reference_strength ?? ''} placeholder="workflow's own"
                    onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, reference_strength: e.target.value === '' ? undefined : parseFloat(e.target.value) } })} /></div>
              </>
            )}
          </div>

          <h3 style={{ marginTop: 16 }}>
            <label style={{ fontSize: 'inherit', fontWeight: 'inherit', cursor: 'pointer' }}
                   title="Off: the look is not written into any prompt of this session. The text stays where it is, so it can be switched back on. Measured: past ~85 composed words this sampler stops rendering the position and framing a take asks for, and the look is the largest block before the take.">
              <input type="checkbox"
                     checked={newSession.settings.use_look !== false}
                     onChange={(e) => setNewSession({ ...newSession,
                       settings: { ...newSession.settings, use_look: e.target.checked } })} />
              {' '}Look
            </label>
          </h3>
          <p className="muted" style={{ margin: '0 0 6px' }}>
            Hair, makeup, the place and the light — identical in every photo of the session.
            Change the look and it is a different session.
          </p>
          <textarea rows={2} value={newSession.look}
                    placeholder="hair down with a centre part, soft natural makeup, on a beach at golden hour"
                    onChange={(e) => setNewSession({ ...newSession, look: e.target.value })} />
          {llm && (
            <div className="row" style={{ marginTop: 6 }}>
              <button disabled={!newSession.look.trim() || !!writing}
                      title="Rewrite the hair, the makeup, the place and the light precisely. The clothes are not written here — they belong to the wardrobe below."
                      onClick={() => writeLook('look', () => rewriteLook(newSession.look))}>
                {writing === 'look' ? '…' : '✨ Describe it precisely'}
              </button>
              {/* The native file input renders its label in the browser's locale,
                  so it is hidden behind our own, as everywhere else. */}
              <label className="filebtn"
                     title="Read the clothes out of a photo into the wardrobe box. The look is not filled from the photo. Never the person — the character comes from the LoRA, and another face written here fights it in every frame.">
                {writing === 'photo' ? '…' : '📷 Wardrobe from a photo…'}
                <input type="file" accept="image/png,image/jpeg,image/webp" hidden
                       onChange={(e) => {
                         const file = e.target.files[0]
                         e.target.value = ''   // same file twice in a row still fires
                         // The clothes only. The look half of the read is dropped on the
                         // way in rather than at the source, so turning it back on is one line.
                         if (file) writeLook('photo', async () =>
                           ({ wardrobe: (await lookFromPhoto(await photoDataUri(file))).wardrobe }))
                       }} />
              </label>
              <span className="muted">
                the clothes and the place, never the person — that comes from the LoRA
              </span>
            </div>
          )}

          <h3 style={{ marginTop: 16 }}>Wardrobe</h3>
          <p className="muted" style={{ margin: '0 0 6px' }}>
            The clothes. This is the <b>starting point</b>, not a constant: it is written into
            every take, and a take that sets its own wardrobe wins. That is what lets one
            shoot open a jacket, push a top up and end with none of it, instead of every
            frame arguing with a sentence that says the jacket is still on.
          </p>
          <textarea rows={2} value={newSession.wardrobe}
                    placeholder="white linen midi dress, thin straps, square neckline, bare legs, flat tan leather sandals"
                    onChange={(e) => setNewSession({ ...newSession, wardrobe: e.target.value })} />
          {llm && (
            <div className="row" style={{ marginTop: 6 }}>
              <button disabled={!newSession.wardrobe.trim() || !!writing}
                      title="Rewrite every garment precisely — colour, fabric, cut, and the counts. A vague garment comes back different in every photo."
                      onClick={() => writeLook('wardrobe',
                                               async () => ({ wardrobe: await rewriteWardrobe(newSession.wardrobe) }))}>
                {writing === 'wardrobe' ? '…' : '✨ Describe it precisely'}
              </button>
              <span className="muted">
                undressing is done take by take, in the wardrobe box on each row below
              </span>
            </div>
          )}

          <h3 style={{ marginTop: 16 }}>Shots</h3>
          {/* Which photo the ref takes edit is the first thing asked and the last
              thing the app used to say. It cannot be picked here — the session
              does not exist yet — so what it can do is say how it gets picked. */}
          {KINDS[kind].refKind && (
            <p className="muted" style={{ margin: '0 0 8px' }}>
              These takes edit <b>the session's reference photo</b>, and there is none yet.
              {newSession.shots.some((x) => x.prompt.trim() && !x.reference)
                ? ' The first take with ref unticked shoots it, and the edits follow in the same Run.'
                : ' Every take below is an edit, so after creating the session either import a photo'
                  + ' (it becomes the reference) or add a take with ref unticked. Run is refused until then.'}
            </p>
          )}
          {kind === 'angles' && (
            <AnglePicker llm={llm} onAdd={(takes) => setNewSession({
                           // The blank first row is scaffolding, not a take.
                           ...newSession,
                           shots: [...newSession.shots.filter((s) => s.prompt.trim()), ...takes],
                         })} />
          )}
          {/* Both updates are functional rather than a spread of `newSession`: the
              brief writes the look and the takes in two awaits, and a spread of the
              session captured before the first one puts the old look back. */}
          <ShotsEditor kind={kind} shots={newSession.shots} llm={llm}
                       context={composed(model, '')} look={newSession.look}
                       wardrobe={newSession.wardrobe}
                       onLook={({ look, wardrobe }) => setNewSession((cur) => ({
                         ...cur, look: look ?? cur.look, wardrobe: wardrobe ?? cur.wardrobe }))}
                       onChange={(shots) => setNewSession((cur) => ({ ...cur, shots }))} />

          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" onClick={createSession}
                    disabled={!newSession.shots.some((s) => s.prompt.trim())}>
              Create session ({newSession.shots.reduce((n, s) => n + (s.prompt.trim() ? Math.max(1, s.count) : 0), 0)} photos)
            </button>
            <button onClick={() => setNewSession(null)}>Cancel</button>
          </div>
        </div>
      )}

      <h2>Sessions</h2>
      <div className="cards">
        {model.sessions.map((s) => (
          <a className="card" key={s.id} href={`#/session/${s.id}`}>
            {s.cover_shot_id
              ? <img className="thumb" src={shotImage(s.cover_shot_id)} alt="" />
              : <div className="thumb" />}
            <div className="body">
              <div className="name">{s.name}</div>
              <div className="muted">{s.done_count}/{s.shot_count} · <span className={'badge ' + s.status}>{s.status}</span></div>
            </div>
          </a>
        ))}
        {!model.sessions.length && <p className="muted">No sessions yet. Create one to start the shoot.</p>}
      </div>
    </>
  )
}
