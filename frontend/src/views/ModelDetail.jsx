import React, { useEffect, useId, useMemo, useRef, useState } from 'react'
import { api, loraPreview, shotImage } from '../api'
import ModelPreview from '../components/ModelPreview.jsx'
import { go } from '../App.jsx'
import { ModelForm, BaseModelSelect, SamplerSelect } from './Models.jsx'
import ShotsEditor, { blankShot } from './ShotsEditor.jsx'
import AnglePicker from './AnglePicker.jsx'
import CanvasSize from './CanvasSize.jsx'
import { KINDS, WORKFLOW_KINDS, forKind, checkpointProfile, profileSummary } from '../kinds.js'
import { composed, lookFromPhoto, photoDataUri, rewriteLook, rewriteWardrobe } from '../enhance.js'
// The measured rooms, read straight from the seed file the way `compose.test.js`
// reads the component seed. No endpoint and no table: a room is a starting text
// for `session.look`, not a row the draw deals.
import candidRooms from '../../../data/candid-rooms-seed.json'
// Directed's own, added session 381. Candid's text could never serve here: its
// first sentence is an amateur-technique register, so offering it on a directed
// session would not give directed a look, it would turn directed into candid.
import directedLooks from '../../../data/directed-looks-seed.json'
// The register is the manner's, not the room's, so composing is what turns a
// place into a look. `rooms.js` holds both halves and the join.
import {
  allTags, filterRooms, guidanceLines, openingLook, pickerRooms, roomChoice,
  refillLook, roomOption, verdictLabel,
} from '../rooms.js'
import { sessionCreationActions } from '../resources.js'

// A row says which manners its PLACE makes sense under, and most say "any" -
// the register left the text in the split, so candid's bedroom is a bedroom and
// a directed session can be shot in it. The studio is the exception the field
// was rewritten for: it is a photographic set-up, and nobody is photographing
// her under the other two manners.
// The tracked half, carried by the build. The imported half arrives at
// runtime from `/api/rooms`, because those seeds are untracked and a build
// that imported them would not build on a clone that never ran the import.
const BUILT_IN = [...candidRooms, ...directedLooks]

export default function ModelDetail({ id }) {
  const [model, setModel] = useState(null)
  const [edit, setEdit] = useState(null)
  const [loras, setLoras] = useState([])
  const [baseModels, setBaseModels] = useState({})
  const [workflows, setWorkflows] = useState([])
  const [newSession, setNewSession] = useState(null)
  // The whole config: it also carries the per-checkpoint profiles.
  const [config, setConfig] = useState({})
  // Empty until the route answers, and empty forever if it cannot: an
  // absent library is a reason, not an error, and the picker still opens.
  const [servedRooms, setServedRooms] = useState([])
  // The registered libraries and, for one that offered nothing, the sentence
  // saying why. A library switched off or never imported is the ordinary state
  // of a clone, and the picker showing nine rooms with no explanation is how
  // that reads as a fault.
  const [servedLibraries, setServedLibraries] = useState([])
  // The tag the room list is narrowed to, empty for all of them. The
  // options come from the rooms themselves - 239 tags belong to the
  // source, and a list written here would go stale on the next import.
  const [roomTag, setRoomTag] = useState('')
  // What was typed into the room filter. Matched against the label and the
  // room's own prose, so "mirror" finds the room with a mirror in it whatever
  // its label says.
  const [roomText, setRoomText] = useState('')
  // The room the session is using - dealt at creation or picked since. Kept as
  // a KEY and not as a row: it is what the select's value has to be, it is
  // what keeps that room in the list whatever the filter says, and the look
  // itself is the session's from the moment it is filled.
  const [roomKey, setRoomKey] = useState('')
  const llm = !!config.llm_ok
  const [writing, setWriting] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const errorRef = useRef(null)
  const editRef = useRef(null)
  const editTrigger = useRef(null)
  const legacyRef = useRef(null)
  const legacyTrigger = useRef(null)
  const returnFocus = useRef(null)
  const formId = useId()
  const fieldId = (name) => `${formId}-${name}`

  const reload = () => api.get(`/api/models/${id}`).then(setModel).catch((e) => setError(e.message))
  useEffect(() => {
    reload()
    api.get('/api/workflows').then(setWorkflows).catch(() => {})
    api.get('/api/comfy/loras').then((d) => setLoras(d.loras)).catch(() => {})
    api.get('/api/comfy/models').then(setBaseModels).catch(() => {})
    // No endpoint configured is not an error: the assistant is optional, and the
    // buttons simply do not appear.
    api.get('/api/config').then(setConfig).catch(() => {})
    api.get('/api/rooms').then((d) => {
      setServedRooms(d.rooms || [])
      setServedLibraries(d.libraries || [])
    }).catch(() => {})
  }, [id])
  useEffect(() => { if (error) errorRef.current?.focus() }, [error, saving])
  useEffect(() => { if (edit) editRef.current?.querySelector('input')?.focus() }, [!!edit])
  useEffect(() => { if (newSession) legacyRef.current?.focus() }, [!!newSession])
  useEffect(() => {
    if (!saving && returnFocus.current) {
      returnFocus.current.focus()
      returnFocus.current = null
    }
  }, [!!edit, !!newSession, saving])

  // The two halves, deduplicated: the registry's default entry is the nine
  // tracked rooms, so the route hands back rooms the bundle already carries.
  const ROOMS = pickerRooms(BUILT_IN, servedRooms)
  // The room the look came from, or nothing for a look somebody typed.
  const ROOM = ROOMS.find((r) => r.key === roomKey) || null
  const sessionActions = sessionCreationActions(id)

  const sessionCards = useMemo(() => (model?.sessions || []).map((s) => (
          <li key={s.id}><a className="model-card" href={`#/session/${s.id}`}>
            <ModelPreview src={s.cover_shot_id ? shotImage(s.cover_shot_id) : null} emptyLabel="No photos yet" />
            <div className="model-card-body">
              <h3 className="model-card-name">{s.name?.trim() ? s.name : `Session #${s.id}`}</h3>
              <p className="model-card-count">{s.done_count} / {s.shot_count} photos</p>
              <span className="ui-state model-session-status" data-tone={s.status === 'done' ? 'success' : s.status === 'failed' ? 'error' : undefined}>{s.status || 'Unknown'}</span>
            </div>
          </a></li>
        )), [model?.sessions])

  if (!model) return <div className="studio-ui ui-page model-detail-page">
    {error ? <div className="ui-notice" data-tone="error" role="alert" tabIndex={-1} ref={errorRef}>
      <p>{error}</p><button className="ui-button" type="button" onClick={() => { setError(''); reload() }}>Retry loading model</button>
    </div> : <p className="ui-help" role="status">Loading model…</p>}
  </div>

  const saveModel = async () => {
    setSaving(true); setError('')
    try { await api.patch(`/api/models/${id}`, edit); returnFocus.current = editTrigger.current; setEdit(null); reload() }
    catch (e) { setError(e.message) }
    finally { setSaving(false) }
  }
  const cancelEdit = () => { returnFocus.current = editTrigger.current; setEdit(null); setError('') }
  const cancelSession = () => { returnFocus.current = legacyTrigger.current; setNewSession(null) }

  const startSession = () => {
    const draft = newSessionDraft()
    // Dealt once, here, in the handler that makes the draft - never in render,
    // where every keystroke in the form would deal another room. A drawn room
    // is an ordinary default: the picker above replaces it and the textarea
    // edits it, and neither is undone by anything that happens afterwards.
    const drawn = openingLook(draft, ROOMS)
    setRoomKey(drawn?.room?.key ?? '')
    setRoomText('')
    setRoomTag('')
    setNewSession(drawn ? { ...draft, look: drawn.look } : draft)
  }

  const newSessionDraft = () => ({
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
    // Manner rides on the row, not only on the editor's <select>. The editor
    // already shows it and uses it to ask the writer for a line; without this
    // default, the POST would carry no manner, the session would be born with
    // manner='', and 3.2's strict check would refuse every compose. The
    // default matches the editor's (ShotsEditor.jsx:67 was the second home
    // for this fact), and the <ShotsEditor> onManner callback below updates
    // it when the user picks another.
    manner: 'directed',
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
      // The blank shot the editor opens with is not a shot. Dropping it here
      // rather than refusing to create is what lets a session start empty and
      // be filled from the composer, which is how a cell gets measured.
      const { id: sid } = await api.post('/api/sessions', {
        // The room the look was filled from rides along as provenance. It is
        // state of its own and not part of the draft, because it is the
        // select's value first and the session's record second.
        ...newSession, room_key: roomKey,
        shots: newSession.shots.filter((s) => s.prompt.trim()) })
      go(`/session/${sid}`)
    } catch (e) { setError(e.message) }
  }

  return (
    <div className="studio-ui ui-page model-detail-page ui-stack">
      {error && <div className="ui-notice" data-tone="error" role="alert" tabIndex={-1} ref={errorRef}>
        <p>{error}</p><p className="ui-help">Your entries are kept. Review the settings and try again.</p>
      </div>}
      <header className="ui-page-header model-page-header">
        <div><a className="model-back" href="#/models">Models</a>
          <h1 className="ui-page-title">{model.name?.trim() ? model.name : `Model #${model.id}`}</h1>
          <p className="ui-help">Model #{model.id}</p></div>
        <div className="ui-actions model-detail-actions">
          <a className="ui-button ui-button--primary" href={`#${sessionActions.primary.path}`}>+ New session</a>
          <button className="ui-button" type="button" ref={editTrigger} aria-expanded={!!edit} aria-controls="edit-model-form"
            disabled={saving} onClick={() => { if (edit) cancelEdit(); else { setError(''); setEdit({ ...model }) } }}>{edit ? 'Cancel editing' : 'Edit model'}</button>
          <button className="ui-button" type="button" ref={legacyTrigger} aria-expanded={!!newSession} aria-controls="legacy-session-form"
            onClick={startSession} disabled={!!newSession}>Legacy session</button>
          <button className="ui-button model-delete" type="button" onClick={async () => {
            if (confirm(`Delete the model "${model.name}" and all its sessions?`)) {
              try { await api.del(`/api/models/${id}`); go('/models') }
              catch (e) { setError(e.message) }
            }
          }}>Delete</button>
        </div>
      </header>
      <section className="ui-surface model-overview" aria-label="Model defaults">
        <ModelPreview src={model.lora_name ? loraPreview(model.lora_name) : null} />
        <dl className="ui-definition-list model-summary">
          <div><dt>Character LoRA</dt><dd>{model.lora_name || 'No LoRA selected'}</dd></div>
          <div><dt>Trigger word</dt><dd>{model.trigger || 'Not set'}</dd></div>
          <div><dt>LoRA strength</dt><dd>{model.lora_strength}</dd></div>
          <div><dt>Default workflow</dt><dd>{model.workflow_id ? workflows.find((w) => w.id === model.workflow_id)?.name || `Workflow #${model.workflow_id} — unavailable` : 'No default workflow'}</dd></div>
          <div><dt>Default canvas</dt><dd>{model.settings?.width ?? 832} × {model.settings?.height ?? 1216}</dd></div>
          <div><dt>Sessions</dt><dd>{model.sessions.length}</dd></div>
        </dl>
      </section>
      <p className="ui-help">New session opens resource-based setup. Legacy session opens the original manual form.</p>

      {edit && (
        <form className="model-edit-form ui-stack" id="edit-model-form" ref={editRef} noValidate aria-labelledby="edit-model-title"
          aria-busy={saving} onSubmit={(event) => { event.preventDefault(); saveModel() }}>
          <h2 className="ui-section-title" id="edit-model-title">Edit model</h2>
          <ModelForm form={edit} setForm={setEdit} loras={loras} workflows={workflows} models={baseModels} />
          <div className="ui-form-footer">
            <button className="ui-button ui-button--primary" type="submit" disabled={saving}>{saving ? 'Saving model…' : 'Save model'}</button>
            <button className="ui-button" type="button" disabled={saving} onClick={cancelEdit}>Cancel editing</button>
          </div>
        </form>
      )}

      {newSession && (
        <section className="ui-surface model-legacy-form" id="legacy-session-form" aria-labelledby="legacy-session-title" ref={legacyRef} tabIndex={-1}>
          <h2 className="ui-section-title" id="legacy-session-title">Legacy session</h2>
          <div className="row" style={{ marginBottom: 4 }}>
            {Object.entries(KINDS).map(([k, spec]) => (
              <button key={k} className={'ui-button chip' + (kind === k ? ' on' : '')} aria-pressed={kind === k}
                      title={spec.blurb} onClick={() => setKind(k)}>{spec.label}</button>
            ))}
          </div>
          <p className="muted" style={{ margin: '0 0 8px' }}>{KINDS[kind].blurb}</p>
          {KINDS[kind].rule && <p className="rule">{KINDS[kind].rule}</p>}

          <div className="ui-form-grid">
            <div className="ui-field">
              <label className="ui-label" htmlFor={fieldId('session-name')}>Name</label>
              <input className="ui-control" id={fieldId('session-name')} name="session_name" autoComplete="off" value={newSession.name} onChange={(e) => setNewSession({ ...newSession, name: e.target.value })} />
            </div>
            <div className="ui-field">
              <label className="ui-label" htmlFor={fieldId('workflow')} title="The graph for takes with ref unticked — the ones painted from noise. An editing or camera-angle graph belongs in the next box, not this one.">
                Workflow{KINDS[kind].refKind ? ' (new photos)' : ''}
              </label>
              <select className="ui-control" id={fieldId('workflow')} value={newSession.workflow_id ?? ''}
                      onChange={(e) => setNewSession({ ...newSession, workflow_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">— the model's —</option>
                {forKind(workflows, 't2i').map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
            </div>
            {/* Text to image only: a session with no editing graph is exactly what
                the shoot kind is, so there is nothing to pick. */}
            {KINDS[kind].refKind && (
              <div className="ui-field">
                <label className="ui-label" htmlFor={fieldId('reference-workflow')} title="The graph that edits the session's reference photo instead of painting a new one. Takes marked ref run through it.">
                  Reference workflow (edits)
                </label>
                <select className="ui-control" id={fieldId('reference-workflow')} value={newSession.reference_workflow_id ?? ''}
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
            <div className="ui-field ui-field-wide">
              <label className="ui-label" htmlFor={fieldId('checkpoint')}>Base model</label>
              {/* Its recommended settings come with it — the boxes below fill in,
                  and stay editable. Overwriting and not filling blanks: steps and
                  cfg always hold a value, so a fill-the-blanks rule would never
                  fire and the shoot would run at the last model's numbers. */}
              <BaseModelSelect className="ui-control" id={fieldId('checkpoint')} value={newSession.settings.checkpoint} models={baseModels}
                               onChange={(v) => setNewSession({ ...newSession,
                                 settings: { ...newSession.settings, checkpoint: v,
                                             ...(checkpointProfile(config, v) || {}) } })} />
              {checkpointProfile(config, newSession.settings.checkpoint) && (
                <p className="muted" style={{ margin: '4px 0 0' }}>
                  This model's profile: <b>{profileSummary(checkpointProfile(config, newSession.settings.checkpoint))}</b>
                </p>
              )}
            </div>
            <div className="ui-field">
              <label className="ui-label" htmlFor={fieldId('seed-mode')}>Seeds</label>
              <select className="ui-control" id={fieldId('seed-mode')} value={newSession.seed_mode} onChange={(e) => setNewSession({ ...newSession, seed_mode: e.target.value })}>
                <option value="random">Random</option>
                <option value="fixed">Fixed (from…)</option>
              </select>
            </div>
            {newSession.seed_mode === 'fixed' && (
              <div className="ui-field">
                <label className="ui-label" htmlFor={fieldId('seed')}>Starting seed</label>
                <input className="ui-control" id={fieldId('seed')} type="number" value={newSession.seed} onChange={(e) => setNewSession({ ...newSession, seed: Number(e.target.value) })} />
              </div>
            )}
            <CanvasSize studio idPrefix={fieldId('canvas')} width={newSession.settings.width} height={newSession.settings.height}
              onChange={(w, h) => setNewSession({ ...newSession, settings: { ...newSession.settings, width: w, height: h } })} />
            <div className="ui-field"><label className="ui-label" htmlFor={fieldId('steps')}>Steps</label><input className="ui-control" id={fieldId('steps')} type="number" value={newSession.settings.steps ?? 8}
              onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, steps: Number(e.target.value) } })} /></div>
            <div className="ui-field"><label className="ui-label" htmlFor={fieldId('cfg')}>CFG</label><input className="ui-control" id={fieldId('cfg')} type="number" step="0.1" value={newSession.settings.cfg ?? 1}
              onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, cfg: parseFloat(e.target.value) } })} /></div>
            <div className="ui-field"><label className="ui-label" htmlFor={fieldId('sampler')} title="Each Krea 2 finetune asks for its own. Only applied if the workflow maps the slot.">Sampler</label>
              <SamplerSelect className="ui-control" id={fieldId('sampler')} value={newSession.settings.sampler} options={baseModels.samplers}
                onChange={(v) => setNewSession({ ...newSession, settings: { ...newSession.settings, sampler: v } })} /></div>
            <div className="ui-field"><label className="ui-label" htmlFor={fieldId('scheduler')} title="simple, beta or bong_tangent, depending on the checkpoint. Only applied if the workflow maps the slot.">Scheduler</label>
              <SamplerSelect className="ui-control" id={fieldId('scheduler')} value={newSession.settings.scheduler} options={baseModels.schedulers}
                onChange={(v) => setNewSession({ ...newSession, settings: { ...newSession.settings, scheduler: v } })} /></div>
            <div className="ui-field"><label className="ui-label" htmlFor={fieldId('strength')}>LoRA strength</label><input className="ui-control" id={fieldId('strength')} type="number" step="0.05" value={newSession.settings.lora_strength ?? 1}
              onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, lora_strength: parseFloat(e.target.value) } })} /></div>
            {/* Only worth showing once a reference workflow is picked: nothing else
                reads them, and an unmapped slot changes nothing. */}
            {newSession.reference_workflow_id && (
              <>
                <div className="ui-field"><label className="ui-label" htmlFor={fieldId('denoise')} title="How far an img2img edit may travel from the reference. Low keeps the face and changes little; high changes a lot and drifts.">Denoise</label>
                  <input className="ui-control" id={fieldId('denoise')} type="number" step="0.05" min="0" max="1" value={newSession.settings.denoise ?? ''} placeholder="workflow's own"
                    onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, denoise: e.target.value === '' ? undefined : parseFloat(e.target.value) } })} /></div>
                <div className="ui-field"><label className="ui-label" htmlFor={fieldId('reference-strength')} title="IPAdapter weight, for graphs that use one.">Reference strength</label>
                  <input className="ui-control" id={fieldId('reference-strength')} type="number" step="0.05" value={newSession.settings.reference_strength ?? ''} placeholder="workflow's own"
                    onChange={(e) => setNewSession({ ...newSession, settings: { ...newSession.settings, reference_strength: e.target.value === '' ? undefined : parseFloat(e.target.value) } })} /></div>
              </>
            )}
          </div>

          <h3 style={{ marginTop: 16 }}>
            <label className="model-look-toggle" style={{ fontSize: 'inherit', fontWeight: 'inherit', cursor: 'pointer' }}
                   title="Off: the look is not written into any prompt of this session. The text stays where it is, so it can be switched back on. Length is NOT the reason to switch it off: sessions 395-400 shot one calibrated cell against a room ladder and the camera arrived 7/10 with no room at all, 9/10 at 53 words and 10/10 at both 84 and 176 - the trend runs upward. Session 394 shot the same line twice, once whole and once with 87 to 187 words of blocks deleted, and got 9 of 16 either way. The room word budget is 200, above the longest length measured good and twice the longest room in the catalogue: it is a tripwire against an absurd input, not a limit anybody meets.">
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
          {/* The rooms are a starting point, not a catalogue row: picking one
              fills the textarea and the text is yours to edit afterwards. They
              live in a seed file rather than in `component` because that table's
              slot is the closed vocabulary of the trio (camera, act, framing),
              and a fourth slot in the same namespace would reach the draw.
              Measured in sessions 370 and 371: every one of these builds its
              room, and where the sentence puts the furniture does not matter —
              the act naming a piece is what puts her on it. */}
          {/* The tag filter, and it is only shown when the rooms carry tags:
              nobody has imported a library on a fresh clone, and an empty
              select is a control that promises a filter it cannot apply. */}
          {allTags(ROOMS).length > 0 && (
            <label className="ui-label">Room type<select className="ui-control" value={roomTag} onChange={(e) => setRoomTag(e.target.value)}
                    title="Narrow the rooms to one kind of place. The tags come from the library the room was imported from.">
              <option value="">Every kind of place</option>
              {allTags(ROOMS).map((tag) => (
                <option key={tag} value={tag}>{tag}</option>
              ))}
            </select></label>
          )}
          <label className="ui-label" htmlFor={fieldId('room-search')}>Find a room</label>
          <input className="ui-control" id={fieldId('room-search')} value={roomText} onChange={(e) => setRoomText(e.target.value)}
                 placeholder="Find a room…"
                 title="Matched against the room's label and against its own text, so a word that is only in the sentence still finds it." />
          <label className="ui-label" htmlFor={fieldId('room')}>Measured room</label>
          <select className="ui-control" id={fieldId('room')} value={roomKey}
                  title="Fill the look with a measured room. Every one of these was rendered; the text stays editable."
                  onChange={(e) => {
                    // Pick, replace and detach are one decision and it is made
                    // in `roomChoice`, not here: only the look ever moves, the
                    // wardrobe and the shots and the settings come back as the
                    // same objects, and null is a value that changes nothing.
                    const next = roomChoice(newSession, ROOMS, e.target.value)
                    if (next) {
                      setRoomKey(next.key)
                      setNewSession(next.session)
                    }
                  }}>
            <option value="">
              {roomKey ? 'Detach this room (the text stays)' : 'Start from a measured room…'}
            </option>
            {filterRooms(ROOMS, { manner: newSession.manner, tag: roomTag,
                                  text: roomText, current: roomKey }).map((r) => (
              <option key={r.key} value={r.key}>
                {roomOption(r, newSession.manner)}
              </option>
            ))}
          </select>
          {/* A registered library that offered nothing, and the reason it
              gives. Said here rather than swallowed: the route answers with an
              empty list and a sentence for a library switched off, absent or
              unreadable, and a picker that shows only the rooms it does have
              reads as a fault instead of as an import nobody ran. */}
          {servedLibraries.filter((lib) => lib.reason).map((lib) => (
            <p key={lib.name} className="muted" style={{ margin: '4px 0 0' }}>
              {lib.name}: no rooms here - {lib.reason}.
            </p>
          ))}
          <label className="ui-label" htmlFor={fieldId('look')}>Look description</label>
          <textarea className="ui-control" id={fieldId('look')} rows={2} value={newSession.look}
                    placeholder="hair down with a centre part, soft natural makeup, on a beach at golden hour"
                    onChange={(e) => setNewSession({ ...newSession, look: e.target.value })} />
          {/* Which room the look came from, and what it measured under this
              manner. Said and not enforced: the look is the operator's from
              here on, and the text is edited below like any other. */}
          {ROOM && (
            <p className="muted" style={{ marginTop: 4 }}>
              From {ROOM.label} - {verdictLabel(ROOM, newSession.manner)}.
            </p>
          )}
          {/* What the entry's author wrote about the room, for whoever is
              writing the line - never composed into it. */}
          {ROOM && guidanceLines(ROOM).length > 0 && (
            <ul className="muted" style={{ margin: '4px 0 0', paddingLeft: 18 }}>
              {guidanceLines(ROOM).map((line) => (
                <li key={line.field}>{line.field}: {line.text}</li>
              ))}
            </ul>
          )}
          {/* The manner can be changed after the look was filled, and the
              register in the text is then the other manner's. Said, not fixed:
              the swap is one click and nothing happens without it, because a
              look edited after the room was picked is the operator's text and
              a silent rewrite is how an edit disappears. */}
          {(() => {
            const offer = refillLook(newSession.manner, newSession.look)
            return offer && (
              <p className="muted" style={{ marginTop: 4 }}>
                This look opens with {offer.carries}'s register, not {newSession.manner}'s.{' '}
                <button onClick={() => setNewSession({ ...newSession, look: offer.look })}>
                  Use {newSession.manner}'s register
                </button>
              </p>
            )
          })()}
          {llm && (
            <div className="row" style={{ marginTop: 6 }}>
              <button className="ui-button" disabled={!newSession.look.trim() || !!writing}
                      title="Rewrite the hair, the makeup, the place and the light precisely. The clothes are not written here — they belong to the wardrobe below."
                      onClick={() => writeLook('look', () => rewriteLook(newSession.look))}>
                {writing === 'look' ? '…' : 'Describe it precisely'}
              </button>
              {/* The native file input renders its label in the browser's locale,
                  so it is hidden behind our own, as everywhere else. */}
              <label className="filebtn model-file-picker"
                     title="Read the clothes out of a photo into the wardrobe box. The look is not filled from the photo. Never the person — the character comes from the LoRA, and another face written here fights it in every frame.">
                {writing === 'photo' ? '…' : 'Wardrobe from a photo…'}
                <input className="model-file-input" type="file" accept="image/png,image/jpeg,image/webp" aria-label="Wardrobe from a photo"
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

          <h3 style={{ marginTop: 16 }} id={fieldId('wardrobe-title')}>Wardrobe</h3>
          <p className="muted" style={{ margin: '0 0 6px' }}>
            The clothes. This is the <b>starting point</b>, not a constant: it is written into
            every take, and a take that sets its own wardrobe wins. That is what lets one
            shoot open a jacket, push a top up and end with none of it, instead of every
            frame arguing with a sentence that says the jacket is still on.
          </p>
          <textarea className="ui-control" aria-labelledby={fieldId('wardrobe-title')} rows={2} value={newSession.wardrobe}
                    placeholder="white linen midi dress, thin straps, square neckline, bare legs, flat tan leather sandals"
                    onChange={(e) => setNewSession({ ...newSession, wardrobe: e.target.value })} />
          {llm && (
            <div className="row" style={{ marginTop: 6 }}>
              <button className="ui-button" disabled={!newSession.wardrobe.trim() || !!writing}
                      title="Rewrite every garment precisely — colour, fabric, cut, and the counts. A vague garment comes back different in every photo."
                      onClick={() => writeLook('wardrobe',
                                               async () => ({ wardrobe: await rewriteWardrobe(newSession.wardrobe) }))}>
                {writing === 'wardrobe' ? '…' : 'Describe it precisely'}
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
                       onManner={(m) => setNewSession((cur) => ({ ...cur, manner: m }))}
                       onChange={(shots) => setNewSession((cur) => ({ ...cur, shots }))} />

          <div className="row" style={{ marginTop: 12 }}>
            <button className="ui-button ui-button--primary" onClick={createSession}>
              Create session ({newSession.shots.reduce((n, s) => n + (s.prompt.trim() ? Math.max(1, s.count) : 0), 0)} photos)
            </button>
            <button className="ui-button" type="button" onClick={cancelSession}>Cancel</button>
          </div>
        </section>
      )}

      <section aria-labelledby="model-sessions-title">
      <div className="ui-section-heading"><h2 className="ui-section-title" id="model-sessions-title">Sessions</h2>
        <span className="ui-state">{model.sessions.length}</span></div>
      <p className="ui-section-description">Open a shoot to continue its preparation or review its photographs.</p>
      <ul className="model-cards" aria-label="Model sessions">
        {sessionCards}
      </ul>
      {!model.sessions.length && <p className="ui-surface ui-help">No sessions yet. Create one to start the shoot.</p>}
      </section>
    </div>
  )
}
