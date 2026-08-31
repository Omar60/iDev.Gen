import React, { useEffect, useState } from 'react'
import { api, shotImage } from '../api'
import { go } from '../App.jsx'
import ShotsEditor, { blankShot } from './ShotsEditor.jsx'
import AnglePicker from './AnglePicker.jsx'
import ExpressionPicker from './ExpressionPicker.jsx'
import { BaseModelSelect, SamplerSelect } from './Models.jsx'
import { KINDS, forKind, sessionKind, checkpointProfile, profileSummary } from '../kinds.js'
import { candidatePool, defaultCount, fillCellDefaultCount } from '../compose.js'
import { composed } from '../enhance.js'

/** A checkpoint's name for a session title: no folder, no extension. Three copies
 *  called "shoot (copy)" are three copies you have to open to tell apart. */
const modelStem = (checkpoint) =>
  (checkpoint || '').split(/[\\/]/).pop().replace(/\.[^.]+$/, '') || 'copy'

export default function SessionView({ id }) {
  const [s, setS] = useState(null)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('all')
  const [zoom, setZoom] = useState(null)
  const [split, setSplit] = useState(50)
  const [adding, setAdding] = useState(null)
  // The wardrobe the add-shots panel is working from. Its own state because it is
  // editable there and only written back on Add.
  const [worn, setWorn] = useState('')
  const [workflows, setWorkflows] = useState([])
  const [baseModels, setBaseModels] = useState({})
  const [settingsOpen, setSettingsOpen] = useState(false)
  // The copy being set up: null when the panel is closed.
  const [clone, setClone] = useState(null)
  // Every session, for the copies-of-this-shoot list, and the one picked to
  // compare against, loaded whole because the comparison needs its shots.
  const [sessions, setSessions] = useState([])
  const [twinId, setTwinId] = useState(0)
  const [twin, setTwin] = useState(null)
  // The whole config, not just `llm_ok`: it also carries the per-checkpoint
  // profiles that picking a base model fills in from.
  const [config, setConfig] = useState({})
  // The tag editor's draft input. A PATCH fires on submit so the network
  // round-trip is one per tag, not one per keystroke, and on remove so each
  // click is its own action with its own undo.
  const [tagDraft, setTagDraft] = useState('')
  // Open when the user starts typing; close on blur once the field is empty
  // again, so a session with no tags does not eat a row of vertical space.
  const [tagsOpen, setTagsOpen] = useState(false)
  // The compose-run control. `mode` defaults to "exploratory" rather than
  // "strict" because the cell table holds 17 rows and two verified trios on
  // the current checkpoint, and a strict default makes the first use of
  // this feature a 422 — which the operator reads as broken. Exploratory
  // draws unknown and verified cells, never dead ones, so the first click
  // queues a real shot; strict is one click away when the operator has
  // measured enough to want it.
  // Opens on the largest run the no-repeat rule allows for this manner
  // (see `defaultCount`), so the first click is not a 422 for the same
  // reason the mode defaults to exploratory. The initialiser runs once,
  // before the session has loaded, so it reads the `directed` fallback —
  // which is the right number for every manner today because the binding
  // slot is the act list and that list is shared. A manner-specific act
  // catalogue would make this stale and it would have to move to an effect.
  const [composeCount, setComposeCount] = useState(() => defaultCount(s?.manner))
  const [composeMode, setComposeMode] = useState('exploratory')
  // Compose the line WITHOUT the session's wardrobe, so a reference can deliver
  // the clothing instead. Measured 2026-08-31: a written garment beats a
  // reference card 0/9 at every strength, and struck out it lands 3/3. One
  // checkbox for both compose controls — the switch is a property of the take,
  // not of which button queued it. Off by default: with no reference attached
  // a silent line renders her undressed.
  const [muteWardrobe, setMuteWardrobe] = useState(false)
  // The fill-cell control: pick one trio (camera, act, framing),
  // pick a count, queue N photographs of that trio on
  // THIS session. The picker reads the same catalogue slice the
  // Compose control reads (`candidatePool(manner)`) — no second
  // pool, no second source of truth. Initial state opens on the
  // first camera and first act of the slice and the threshold
  // count (10) a cell needs to reach verified or dead. The
  // `s?.manner` initialiser runs before the session has loaded
  // and falls back to the `directed` slice, which is the right
  // first paint while we wait: the catalogue re-resolves on
  // every press.
  const [fillCellCamera, setFillCellCamera] = useState(() => [candidatePool(s?.manner).camera[0]?.key || 'none'])
  const [fillCellAct, setFillCellAct] = useState(() => [candidatePool(s?.manner).act[0]?.key || 'none'])
  const [fillCellFraming, setFillCellFraming] = useState(() => [candidatePool(s?.manner).framing[0]?.key || 'none'])
  const [fillCellMode, setFillCellMode] = useState('exploratory')
  const [fillCellCount, setFillCellCount] = useState(() => fillCellDefaultCount())
  const llm = !!config.llm_ok

  const reload = () => api.get(`/api/sessions/${id}`).then(setS).catch((e) => setError(e.message))
  useEffect(() => {
    reload()
    api.get('/api/workflows').then(setWorkflows).catch(() => {})
    api.get('/api/sessions').then(setSessions).catch(() => {})
    api.get('/api/comfy/models').then(setBaseModels).catch(() => {})
    // Optional: with no endpoint configured the ✨ buttons simply do not appear.
    api.get('/api/config').then(setConfig).catch(() => {})
  }, [id])

  // The session being compared against. Loaded whole and separately: the list
  // route carries counts, not shots, and the pairing needs the shots.
  useEffect(() => {
    if (!twinId) return setTwin(null)
    api.get(`/api/sessions/${twinId}`).then(setTwin).catch(() => setTwin(null))
  }, [twinId])

  // However the panel was opened — "add shots", "more like this", a kind switch —
  // it opens on what the session is currently wearing.
  useEffect(() => { if (adding) setWorn(s?.wardrobe || '') }, [adding !== null])

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

  // Compose a run of N photographs from the catalogue. The button is on
  // an EXISTING session (not in the create flow) because the 3.2 rework
  // lifted `manner` and `checkpoint` out of the editor onto the row, and
  // the compose endpoints add shots to a session that already carries
  // them; putting this in the create form would mean duplicating the
  // checkpoint derivation before there is a row to derive it onto. The
  // 422 path is the refusal 3.3 already wrote (`backend/main.py`),
  // surfaced verbatim through the same `setError(e.message)` the other
  // call sites use — the slot, its verified count, the largest fillable
  // count and the word "exploratory" all reach the screen the way the
  // operator's eye expects them.
  const composeRun = (n, mode) => call(async () => {
    const candidates = candidatePool(s.manner)
    await api.post(`/api/sessions/${id}/compose-run`, { count: n, candidates, mode, mute_wardrobe: muteWardrobe })
  })

  // Fill a cell: queue N photographs of the picked trio on this
  // session. The cell check (verified in strict, unknown refused
  // in strict but drawable in exploratory, dead refused in both)
  // runs ONCE on the backend before any insert; a 422 leaves
  // nothing queued, and the response's `detail` reaches the
  // screen verbatim through `setError(e.message)` the way 8.3
  // already pins. The trio is built from the same `candidatePool`
  // the Compose control reads — picking the first wording of
  // each selected concept — so the payload matches the shape
  // `/compose` reads, and the operator sees no second list to
  // learn.
  // The control arm: a cell shot with NO phrase for that slot, so a wording's
  // arrival rate can be read against what the model does when nobody asks. It
  // is not a catalogue row — the component table refuses an empty wording, and
  // a row carrying any text at all is a treatment rather than a control. The
  // empty text is dropped by the same `_sentences` join the writer goes
  // through, and the cell records the slot as `none`.
  const NONE = { key: 'none', wordings: [{ key: 'none', text: '' }] }
  const pick = (list, key) => (key === 'none' ? NONE : list.find((c) => c.key === key) || list[0])

  // Every slot goes as a LIST. The endpoint takes the cross product and
  // checks every cell before inserting anything, so a selection whose
  // ninth combination is dead queues nothing at all — the same rule the
  // count already kept for one cell.
  const fillCells = fillCellCamera.length * fillCellAct.length * fillCellFraming.length

  const fillCell = (camKeys, actKeys, framingKeys, n, mode) => call(async () => {
    const pool = candidatePool(s.manner)
    await api.post(`/api/sessions/${id}/compose`, {
      camera: camKeys.map((k) => pick(pool.camera, k)),
      act: actKeys.map((k) => pick(pool.act, k)),
      framing: framingKeys.map((k) => pick(pool.framing, k)),
      count: n, mode, mute_wardrobe: muteWardrobe,
    })
  })

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

  // The copies of this shoot, and only those: two sessions are comparable when
  // the takes, the prompts and the seeds are the same, which is exactly what a
  // clone guarantees and nothing else does. A clone of a clone carries the same
  // root, so the family is flat and every member sees every other one.
  const root = s.settings.cloned_from || s.id
  const family = sessions.filter((x) => x.id !== s.id && (x.settings?.cloned_from || x.id) === root)

  // What makes two photos the same take: the id of the take they were both
  // copied from. A shot of the session that was cloned is its own original, and
  // a clone of a clone carries the same id, so the whole family pairs up.
  //
  // NOT the seed. Reshooting (↺) rolls a new one on purpose — that is the button
  // for a frame that came back wrong — and pairing on the seed loses the twin at
  // exactly the moment you reshot the photo you wanted to compare.
  const takeKey = (x) => `take ${x.origin_shot_id || x.id}`
  // Copies made before the take id existed carry neither, so they keep pairing
  // the way they always did: the row it belongs to and its noise. Not the seed
  // alone — a strength sweep (⚖) pins one seed across four rows; not the row
  // alone — a take with count 4 is four variations under one index.
  const seedKey = (x) => `seed ${x.shot_index}|${x.seed}`
  const twinShots = {}
  for (const x of (twin?.shots || [])) if (x.status === 'done') {
    twinShots[takeKey(x)] = x
    twinShots[seedKey(x)] ??= x
  }
  const twinOf = (shot) => twinShots[takeKey(shot)] || twinShots[seedKey(shot)]
  const shotWith = (session) => `${session.name} · ${session.settings?.checkpoint || "the workflow's own"}`

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
  // Which of the session's numbers actually reach ComfyUI. An unmapped slot is
  // not patched at all — the graph's own widget value stands — so printing the
  // session's number next to it is a lie, and the lie is the whole reason
  // "one workflow per model" looks mysterious instead of deliberate. Until the
  // workflows have loaded, assume mapped: the honest state is the quiet one.
  const shootWf = workflows.find((w) => w.id === (s.workflow_id || s.model.workflow_id))
  const maps = (slots) => !shootWf || slots.split(' ').every((x) => !!shootWf.node_map?.[x])
  // Struck through and explained rather than hidden: the number is still what a
  // clone of this session would carry, it just is not what this graph shoots.
  const Sent = ({ slot, children }) => (maps(slot) ? <>{children}</> : (
    <span style={{ textDecoration: 'line-through' }}
          title={`This workflow does not map ${slot.split(' ').join(' or ')} — its own value is what runs`}>
      {children}
    </span>
  ))
  // Same question one row of the Clone panel at a time: a copy shoots through
  // its own graph, so whether its boxes drive anything is that graph's answer,
  // not this session's.
  const rowWfOf = (r) => workflows.find((w) => w.id === Number(r.workflow_id)) || shootWf
  const rowMaps = (r, slot) => { const w = rowWfOf(r); return !w || !!w.node_map?.[slot] }
  // The graph written for a checkpoint is the one whose own loader names it, so
  // picking a base model can pick its workflow and nothing is typed twice. Only
  // the shooting graphs: an editing graph loads its own model by design.
  const wfFor = (checkpoint) => checkpoint
    && workflows.find((w) => w.kind === 't2i' && w.base_model === checkpoint)
  const tunedWf = wfFor(s.settings.checkpoint)
  const profile = checkpointProfile(config, s.settings.checkpoint)
  // Everything the Settings panel offers that this session's graphs ignore.
  // Denoise is the reference graph's dial, not the shooting one's.
  // Sampler and scheduler are opt-in, so they are only worth reporting when the
  // session actually picks one — listed unconditionally, every graph that leaves
  // the pair alone would report two problems it does not have.
  const unmapped = ['steps', 'cfg', 'width', 'height', 'checkpoint', 'lora_strength']
    .concat(['sampler', 'scheduler'].filter((x) => s.settings[x]))
    .filter((x) => !maps(x))
    .concat(refWf && !refWf.node_map?.denoise ? ['denoise (the editing graph)'] : [])
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

  const tags = s.tags || []
  const addTag = (raw) => {
    const v = (raw || '').trim()
    if (!v) return
    if (tags.some((t) => t.toLowerCase() === v.toLowerCase())) {
      // Backend would dedupe it anyway; skipping the round-trip keeps the
      // response identical and the list from re-rendering for nothing.
      setTagDraft('')
      return
    }
    call(() => api.patch(`/api/sessions/${id}`, { tags: [...tags, v] })).then(() => setTagDraft(''))
  }
  const removeTag = (t) => call(() => api.patch(`/api/sessions/${id}`, {
    tags: tags.filter((x) => x !== t),
  }))

  return (
    <>
      {error && <div className="error" onClick={() => setError('')}>{error}</div>}
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div>
          <h1>{s.name}</h1>
          <p className="muted">
            <a href={`#/model/${s.model.id}`}>{s.model.name}</a> ·{' '}
            <Sent slot="width height">{s.settings.width}×{s.settings.height}</Sent> ·{' '}
            <Sent slot="steps">{s.settings.steps} steps</Sent> ·{' '}
            <Sent slot="cfg">cfg {s.settings.cfg}</Sent> ·{' '}
            <Sent slot="lora_strength">LoRA {s.settings.lora_strength}</Sent>
            {s.settings.sampler && <> · <Sent slot="sampler">{s.settings.sampler}</Sent></>}
            {s.settings.scheduler && <> · <Sent slot="scheduler">{s.settings.scheduler}</Sent></>}
          </p>
          {s.look && <p className="muted" style={{ marginTop: -6 }}><b>Look:</b> {s.look}</p>}
          {s.wardrobe && (
            <p className="muted" style={{ marginTop: -6 }}>
              <b>Wardrobe:</b> {s.wardrobe} <i>— what a take wears unless it says otherwise</i>
            </p>
          )}
          {/* Tag editor. Existing tags as removable badges, plus an input that
              opens on focus and stays open for as long as it has text. The
              Library screen reads the same column, so a tag added here shows
              up in the chip row on the next visit. */}
          <div className="row" style={{ marginTop: -4, marginBottom: 4, gap: 6 }}>
            {tags.map((t) => (
              <span key={t} className="badge" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                {t}
                <button className="icon" style={{ padding: '0 2px', border: 'none', background: 'transparent',
                                                  color: 'var(--muted)', cursor: 'pointer' }}
                        onClick={() => removeTag(t)} title={`Remove tag "${t}"`}>×</button>
              </span>
            ))}
            {(tagsOpen || tags.length > 0) && (
              <form onSubmit={(e) => { e.preventDefault(); addTag(tagDraft) }}
                    style={{ display: 'flex', gap: 4, flex: '0 1 200px' }}>
                <input value={tagDraft} onChange={(e) => setTagDraft(e.target.value)}
                       onBlur={() => { if (!tagDraft) setTagsOpen(false) }}
                       onFocus={() => setTagsOpen(true)}
                       placeholder="Add a tag" style={{ width: '100%' }} />
                <button type="submit" disabled={!tagDraft.trim()}>Add</button>
              </form>
            )}
            {!tagsOpen && tags.length === 0 && (
              <button onClick={() => setTagsOpen(true)} title="Mark this session with a tag, then find it from Library">+ Tag</button>
            )}
          </div>
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
          {/* The compose control: a count, a mode, and a button, on the session
              that's already open. The candidate pool is the whole catalogue slice
              for the session's manner (see compose.js for the per-manner rule);
              the framing is fixed and the screen says so, because picking
              framings is a measurement decision not yet made. Disabled when
              manner or checkpoint is missing, with the reason on the title —
              the same refusal the endpoint will give, surfaced before the click
              so the operator does not pay a round trip to learn what is
              missing. Mode defaults to "exploratory" (8.4): a strict default
              makes the first use of this feature a 422 on a 17-row, 2-trios
              cell table, and reads as broken. */}
          <span className="muted" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <label title="Compose the line without the session's wardrobe, so a reference image can deliver the clothing instead. With no reference attached the line renders her undressed."
                   style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
              <input type="checkbox" checked={muteWardrobe}
                     disabled={!s.manner || !s.checkpoint || s.running}
                     onChange={(e) => setMuteWardrobe(e.target.checked)} />
              no wardrobe
            </label>
            <input type="number" min={1} max={50} value={composeCount}
                   disabled={!s.manner || !s.checkpoint || s.running}
                   onChange={(e) => setComposeCount(Math.max(1, Number(e.target.value) || 1))}
                   style={{ width: 60 }}
                   title="How many photographs to compose and queue" />
            <select value={composeMode}
                    disabled={!s.manner || !s.checkpoint || s.running}
                    onChange={(e) => setComposeMode(e.target.value)}
                    title="exploratory draws unknown and verified cells (never dead); strict draws verified only">
              <option value="exploratory">exploratory</option>
              <option value="strict">strict</option>
            </select>
            <button disabled={!s.manner || !s.checkpoint || s.running || composeCount < 1}
                    onClick={() => composeRun(composeCount, composeMode)}
                    title={!s.manner || !s.checkpoint
                      ? `Compose needs manner and checkpoint (manner="${s.manner || ''}", checkpoint="${s.checkpoint || ''}")`
                      : `Compose ${composeCount} ${composeMode} photograph${composeCount === 1 ? '' : 's'} from the catalogue`}>
              Compose
            </button>
          </span>
          {/* The fill-cell control: pick one trio, queue N photographs of it on
              this session so an operator can take a single cell to its
              `judged=10` threshold without a script. The camera and act
              are <select>s of the catalogue slice the Compose control also
              reads (`candidatePool(manner)`), one per slot. Default count is 10 — the
              threshold `db.cell_state` reads — so a single press queues
              the batch that pushes a cell to verified or dead. Strict
              mode refuses unknowns (the cell check 3.2 already pinned);
              exploratory draws them too. The 422 path is the same
              `setError(e.message)` the Compose control uses. */}
          <span className="muted" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <select multiple size={4} value={fillCellCamera}
                    disabled={!s.manner || !s.checkpoint || s.running}
                    onChange={(e) => setFillCellCamera([...e.target.selectedOptions].map((o) => o.value))}
                    title="Camera concepts for the fill-cell compose — pick several and every combination becomes its own cell">
              {candidatePool(s.manner).camera.map((x) => (
                <option key={x.key} value={x.key}>{x.key}</option>
              ))}
              <option value="none">none (control)</option>
            </select>
            <select multiple size={4} value={fillCellAct}
                    disabled={!s.manner || !s.checkpoint || s.running}
                    onChange={(e) => setFillCellAct([...e.target.selectedOptions].map((o) => o.value))}
                    title="Act concepts for the fill-cell compose — pick several and every combination becomes its own cell">
              {candidatePool(s.manner).act.map((x) => (
                <option key={x.key} value={x.key}>{x.key}</option>
              ))}
              <option value="none">none (control)</option>
            </select>
            <select multiple size={4} value={fillCellFraming}
                    disabled={!s.manner || !s.checkpoint || s.running}
                    onChange={(e) => setFillCellFraming([...e.target.selectedOptions].map((o) => o.value))}
                    title="Framing concepts for the fill-cell compose — pick several and every combination becomes its own cell">
              {candidatePool(s.manner).framing.map((x) => (
                <option key={x.key} value={x.key}>{x.key}</option>
              ))}
              <option value="none">none (control)</option>
            </select>
            <input type="number" min={1} max={50} value={fillCellCount}
                   disabled={!s.manner || !s.checkpoint || s.running}
                   onChange={(e) => setFillCellCount(Math.max(1, Number(e.target.value) || 1))}
                   style={{ width: 50 }}
                   title="How many photographs of this trio to queue" />
            <select value={fillCellMode}
                    disabled={!s.manner || !s.checkpoint || s.running}
                    onChange={(e) => setFillCellMode(e.target.value)}
                    title="Mode for the fill-cell compose (strict refuses unknown cells; exploratory draws them)">
              <option value="exploratory">exploratory</option>
              <option value="strict">strict</option>
            </select>
            <button disabled={!s.manner || !s.checkpoint || s.running || fillCellCount < 1
                              || !fillCellCamera.length || !fillCellAct.length || !fillCellFraming.length}
                    onClick={() => fillCell(fillCellCamera, fillCellAct, fillCellFraming, fillCellCount, fillCellMode)}
                    title={!s.manner || !s.checkpoint
                      ? `Fill cell needs manner and checkpoint (manner="${s.manner || ''}", checkpoint="${s.checkpoint || ''}")`
                      : `${fillCells} cell${fillCells === 1 ? '' : 's'} × ${fillCellCount} = ${fillCells * fillCellCount} ${fillCellMode} photographs. Every cell is checked before any of them is queued.`}>
              Fill {fillCells * fillCellCount} photo{fillCells * fillCellCount === 1 ? '' : 's'}
            </button>
          </span>
          {s.status === 'running' &&
            <button onClick={() => call(() => api.post(`/api/sessions/${id}/cancel`))}>Cancel</button>}
          {failed > 0 && s.status !== 'running' &&
            <button onClick={() => call(() => api.post(`/api/sessions/${id}/retry`))}>Retry {failed}</button>}
          <button onClick={() => setSettingsOpen(!settingsOpen)}
                  title="The workflows and the base model this session shoots with">⚙ Settings</button>
          <button onClick={() => setClone(clone ? null : {
            name: s.name,
            steps: s.settings.steps ?? '',
            // It opens on what this session shoots with, so pressing Create
            // straight away is the plain copy. Every other model is one more row.
            rows: [{ checkpoint: s.settings.checkpoint || '', steps: s.settings.steps ?? '',
                     cfg: s.settings.cfg ?? '', sampler: s.settings.sampler ?? '',
                     scheduler: s.settings.scheduler ?? '' }],
          })} title="Shoot this whole session again on other base models — same takes, same seeds">
            ⧉ Clone
          </button>
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
        {/* Only the copies of this shoot are offered: comparing two photos means
            the same take on the same seed, and no other pair of sessions has
            that. Picking one turns every photo that has a twin into a
            before/after wipe in the lightbox. */}
        {family.length > 0 && (
          <select style={{ width: 'auto' }} value={twinId}
                  title="Compare with a copy of this session — same takes, same seeds, other base model"
                  onChange={(e) => setTwinId(Number(e.target.value))}>
            <option value={0}>Compare with…</option>
            {family.map((f) => (
              <option key={f.id} value={f.id}>{shotWith(f)} · {f.done_count}/{f.shot_count}</option>
            ))}
          </select>
        )}
        <select style={{ width: 'auto' }} value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="all">All</option>
          <option value="keep">Without rejects</option>
          <option value="picks">Picks only (4★+)</option>
        </select>
        {(() => {
          const minRating = filter === 'picks' ? 4 : 1
          const exportCount = s.shots.filter((x) => x.status === 'done' && !x.rejected && x.rating >= minRating).length
          const url = `/api/sessions/${id}/export?min_rating=${minRating}`
          return (
            <a href={exportCount > 0 ? url : undefined} download
               className={exportCount > 0 ? 'button' : 'button disabled'}>
              Download picks ({exportCount})
            </a>
          )
        })()}
        {(() => {
          const minRating = filter === 'picks' ? 4 : 1
          const count = s.shots.filter((x) => x.status === 'done' && !x.rejected && x.rating >= minRating).length
          const url = `/api/sessions/${id}/contact-sheet?min_rating=${minRating}`
          return (
            <a href={count > 0 ? url : undefined} download
               className={count > 0 ? 'button' : 'button disabled'}>
              Contact sheet ({count})
            </a>
          )
        })()}
        {(() => {
          // Same threshold the export uses, read the other way: "below X" is the
          // complement of "X and up". Picks filter -> reshoot everything that
          // isn't a pick; otherwise just the unrated. The anchor stays put for
          // the same reason the per-shot ↺ refuses it.
          const minRating = filter === 'picks' ? 4 : 1
          const reshootCount = s.shots.filter((x) =>
            x.status === 'done' && x.rating < minRating && !anchors.includes(x.id)
          ).length
          return (
            <button disabled={reshootCount === 0}
                    title={reshootCount > 0
                      ? `Delete ${reshootCount} photo${reshootCount === 1 ? '' : 's'} and put ${reshootCount === 1 ? 'it' : 'them'} back in the queue on a new seed`
                      : 'No finished shots are below the current threshold'}
                    onClick={() => {
                      if (confirm(`Reshoot ${reshootCount} shot${reshootCount === 1 ? '' : 's'} below ${minRating}★? Their photos will be deleted and the takes go back in the queue.`)) {
                        call(() => api.post(`/api/sessions/${id}/reshoot-below?min_rating=${minRating}`))
                      }
                    }}>
              Reshoot below {minRating}★ ({reshootCount})
            </button>
          )
        })()}
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
              {/* The profile rides along with the choice. Overwriting rather than
                  filling blanks is deliberate: steps and cfg always hold a value,
                  so a fill-the-blanks rule would never fire and picking a model
                  would keep shooting it at the last model's settings. What makes
                  it safe is that it is not silent — the line below says what
                  arrived, and every value stays editable. */}
              <BaseModelSelect value={s.settings.checkpoint} models={baseModels} disabled={running}
                               onChange={(v) => call(() => api.patch(`/api/sessions/${id}`,
                                 { settings: { checkpoint: v, ...(checkpointProfile(config, v) || {}) } }))} />
              {profile && (
                <p className="muted" style={{ margin: '4px 0 0' }}>
                  This model's profile: <b>{profileSummary(profile)}</b> — filled in when you pick it.
                </p>
              )}
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
          {/* Offered, not applied: swapping the graph out from under a session
              because a dropdown moved is exactly the silent change the panel
              above warns about. One click, and it says what it will do. */}
          {tunedWf && tunedWf.id !== shootWf?.id && (
            <p className="rule">
              <b>{tunedWf.name}</b> is written for this base model — it loads it itself, with
              the sampler, steps and cfg that model wants.{' '}
              <button disabled={running}
                      onClick={() => call(() => api.patch(`/api/sessions/${id}`,
                        { workflow_id: tunedWf.id }))}>Shoot with it</button>
            </p>
          )}
          {/* A graph tuned for one checkpoint carries its own sampler, steps and
              cfg, and the sane way to use one is to leave those unmapped. Then
              this panel and the header are quietly describing a session that is
              not the one being shot — unless they say so. */}
          {unmapped.length > 0 && (
            <p className="rule">
              Not mapped by the graphs above: <b>{unmapped.join(', ')}</b> — whatever the session
              says, the workflow's own value is what runs. That is how a graph tuned for one base
              model is meant to work; map the slot in <a href="#/workflows">Workflows</a> if you
              want the session to drive it instead.
            </p>
          )}
          <p className="muted" style={{ marginBottom: 0 }}>
            Photos already shot keep the settings they were shot with. These apply to what runs next.
          </p>
        </div>
      )}

      {/* Two base models on the same shoot is the only way to tell them apart:
          one frame is luck, twenty is the model. So the copy carries the takes,
          the composed prompts and the seeds unchanged, and the dials here are the
          four things a different checkpoint asks for: the model, its graph, its
          steps and its sampler pair. */}
      {clone && (
        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>Clone this session</h3>
          <p className="muted" style={{ margin: '0 0 6px' }}>
            Same look, wardrobe, takes and seeds — so what changes in the photos is what
            you change here. Nothing is queued: the copy lands as a draft with every take
            pending. Photos brought in from outside are copied as they are; everything else
            is shot again.
          </p>
          <div className="grid-form">
            <div style={{ gridColumn: 'span 2' }}>
              <label>Name</label>
              <input value={clone.name} onChange={(e) => setClone({ ...clone, name: e.target.value })} />
            </div>
            <div style={{ gridColumn: 'span 2' }}>
              <label title="Only applied if the workflow maps the slot — the same rule the run preflight checks. Pick as many as you want to try: each one is a copy of its own.">
                Add a base model
              </label>
              {/* The single-model select, used as an *add* control: picking one
                  appends a row below. Three models is three copies from one
                  press — the alternative is opening this panel three times and
                  retyping the name each go. */}
              <BaseModelSelect value="" models={baseModels}
                               onChange={(v) => v && !clone.rows.some((r) => r.checkpoint === v)
                                 && setClone({
                                   ...clone,
                                   rows: [...clone.rows, {
                                     checkpoint: v, steps: clone.steps, cfg: s.settings.cfg ?? '',
                                     // Inherited from the source, then overwritten
                                     // by whatever this checkpoint's profile names.
                                     // A sweep is worth running at each model's own
                                     // settings; holding one sampler across four
                                     // checkpoints compares the sampler, not them.
                                     sampler: s.settings.sampler ?? '',
                                     scheduler: s.settings.scheduler ?? '',
                                     ...(checkpointProfile(config, v) || {}),
                                     // Its own graph if one exists, the source's otherwise.
                                     workflow_id: wfFor(v)?.id || '',
                                   }],
                                 })} />
            </div>
          </div>
          {/* Steps per row, not one for all: mixing a distilled model with a full
              one is the normal case, and 8 steps on the full one wastes the whole
              copy — twenty-odd photos before it is visible. */}
          <table style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>Base model</th><th>Workflow</th>
                <th style={{ width: 80 }}>Steps</th>
                <th style={{ width: 70 }}>CFG</th>
                <th style={{ width: 150 }}>Sampler</th>
                <th style={{ width: 140 }}>Scheduler</th>
                <th style={{ width: 40 }} />
              </tr>
            </thead>
            <tbody>
              {clone.rows.map((r, i) => {
                // A row's graph decides whether its boxes are anything but
                // decoration — the same rule the header line strikes through.
                const rowSteps = rowMaps(r, 'steps')
                const edit = (patch) => setClone({
                  ...clone,
                  rows: clone.rows.map((x, j) => (j === i ? { ...x, ...patch } : x)),
                })
                return (
                <tr key={r.checkpoint || i}>
                  <td>{r.checkpoint || "the workflow's own"}</td>
                  <td>
                    {/* Prefilled with the graph that names this checkpoint, and
                        still a dropdown: the whole point of a sweep is shooting
                        one model through another's graph on purpose. */}
                    <select value={r.workflow_id || ''} style={{ width: '100%' }}
                            onChange={(e) => edit({ workflow_id: e.target.value })}>
                      <option value="">— this session's —</option>
                      {forKind(workflows, 't2i').map((w) => (
                        <option key={w.id} value={w.id}>
                          {w.name}{w.base_model === r.checkpoint ? ' — written for this model' : ''}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input type="number" min="1" value={rowSteps ? r.steps : ''}
                           disabled={!rowSteps} placeholder="the graph's own"
                           title={rowSteps ? '' : "This graph does not map steps — its own value is what runs"}
                           onChange={(e) => edit({ steps: e.target.value })} />
                  </td>
                  <td>
                    {/* Carried because a profile can set it — muse wants 2 where
                        every other Krea 2 finetune wants 1 — and shown because a
                        value that arrives on its own has to be visible. */}
                    {rowMaps(r, 'cfg') ? (
                      <input type="number" step="0.1" min="0" value={r.cfg ?? ''}
                             placeholder="the graph's own"
                             onChange={(e) => edit({ cfg: e.target.value })} />
                    ) : (
                      <span className="muted" title="This graph does not map cfg — its own value is what runs">—</span>
                    )}
                  </td>
                  {/* The two dials this whole table was missing: across seven Krea 2
                      finetunes no two ask for the same pair, so sweeping checkpoints
                      while holding one sampler compares the sampler, not the models. */}
                  {[['sampler', baseModels.samplers], ['scheduler', baseModels.schedulers]].map(([slot, options]) => (
                    <td key={slot}>
                      {rowMaps(r, slot) ? (
                        <SamplerSelect value={r[slot]} options={options}
                                       onChange={(v) => edit({ [slot]: v })} />
                      ) : (
                        <span className="muted" title={`This graph does not map ${slot} — its own value is what runs`}>
                          the graph's own
                        </span>
                      )}
                    </td>
                  ))}
                  <td>
                    <button className="icon" title="Drop this copy"
                            onClick={() => setClone({ ...clone, rows: clone.rows.filter((_, j) => j !== i) })}>✕</button>
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
          {clone.rows.length > 1 && (
            <p className="muted" style={{ margin: '6px 0 0' }}>
              {clone.rows.length} copies, each named after its model. They are drafts: the
              queue is serial, so you still run them one at a time.
            </p>
          )}
          <div className="row" style={{ marginTop: 10 }}>
            <button className="primary" disabled={!clone.rows.length} onClick={() => call(async () => {
              // One POST per copy. The route takes a name and the settings to
              // override, which is all a second model is, so nothing on the
              // server had to learn about batches.
              const made = []
              for (const r of clone.rows) {
                made.push(await api.post(`/api/sessions/${id}/clone`, {
                  name: `${clone.name} — ${modelStem(r.checkpoint)}`,
                  // A row whose graph does not map the pair carries none: the run
                  // preflight refuses a chosen sampler the graph would ignore, and
                  // that refusal would land on a copy nobody chose one for.
                  settings: { checkpoint: r.checkpoint, steps: Number(r.steps) || s.settings.steps,
                              cfg: r.cfg === '' || r.cfg == null ? s.settings.cfg : Number(r.cfg),
                              sampler: rowMaps(r, 'sampler') ? (r.sampler || '') : '',
                              scheduler: rowMaps(r, 'scheduler') ? (r.scheduler || '') : '' },
                  workflow_id: Number(r.workflow_id) || null,
                }))
              }
              setClone(null)
              // One copy is the shoot you are about to look at. Several are a
              // batch to launch from the list, and jumping into an arbitrary one
              // of them hides the other two.
              if (made.length === 1) go(`/session/${made[0].id}`)
              else api.get('/api/sessions').then(setSessions).catch(() => {})
            })}>
              {clone.rows.length > 1 ? `Create ${clone.rows.length} copies` : 'Create the copy'}
            </button>
            <button onClick={() => setClone(null)}>Cancel</button>
          </div>
        </div>
      )}

      {adding && (
        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>Add shots to this session</h3>
          <p className="muted" style={{ margin: '0 0 6px' }}>
            Same look ({s.look || 'none set'}) — that part does not change. A take marked
            <b> ref</b> skips it entirely and edits the reference photo instead.
          </p>
          {/* The wardrobe is the half that moves, so it is editable here: twenty
              takes in, the shoot is rarely still wearing what it started in, and
              the takes added next should start from where it got to. Saved with
              the shots, so Cancel changes nothing. */}
          <label style={{ marginTop: 8 }}>
            Wardrobe the next takes start from — each row below can still set its own
          </label>
          <textarea rows={2} value={worn} placeholder="none set"
                    onChange={(e) => setWorn(e.target.value)} />
          {kind && KINDS[kind].rule && <p className="rule">{KINDS[kind].rule}</p>}
          {kind === 'angles' && (
            <AnglePicker llm={llm}
                         onAdd={(takes) => setAdding([...adding.filter((x) => x.prompt.trim()), ...takes])} />
          )}
          {/* Here and not on the new-session panel: an expression is an edit of a
              photograph that exists, and a session being created has none. */}
          {kind === 'edit' && (
            <ExpressionPicker
              onAdd={(takes) => setAdding([...adding.filter((x) => x.prompt.trim()), ...takes])} />
          )}
          {/* No `onLook` here: the look belongs to the session, and `add_shots`
              re-reads it from the server anyway. A shoot whose hair, place and
              light changed halfway is two sessions. */}
          <ShotsEditor kind={kind} shots={adding} onChange={setAdding} llm={llm}
                       context={composed(s.model, '')} look={s.look} wardrobe={worn} />

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
              // The session's wardrobe first: `add_shots` reads it from the row,
              // and a take that left its own box empty is asking for this one.
              if (worn !== s.wardrobe) await api.patch(`/api/sessions/${id}`, { wardrobe: worn })
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
              {shot.status === 'done' && (
                <button className="icon"
                        title="Reshoot — this photo is deleted and the take goes back in the queue with a new seed"
                        onClick={() => {
                          if (confirm(`Delete this photo and shoot "${shot.shot_label}" again?`)) {
                            call(() => api.post(`/api/shots/${shot.id}/reshoot`))
                          }
                        }}>↺</button>
              )}
              <button className="icon" title={shot.rejected ? 'Restore' : 'Reject'}
                      onClick={() => call(() => api.patch(`/api/shots/${shot.id}`, { rejected: !shot.rejected }))}>
                {shot.rejected ? '↩' : '✕'}
              </button>
              {/* Delete, as opposed to Reject: the row and the file go, and
                  nothing takes their place. The cell counts are NOT touched -
                  a judged photograph stays counted after its row is gone, so
                  the confirm says so rather than the button quietly corrupting
                  a measurement. Reject is the one that takes a photograph out
                  of a judging pass and leaves the evidence where it is. */}
              <button className="icon" title="Delete this photo"
                      onClick={() => {
                        const judged = !!shot.verdicts
                        if (confirm(judged
                          ? 'This photo has already been judged and its answer stays counted in the cell. Delete it anyway?'
                          : 'Delete this photo?')) {
                          call(() => api.del(`/api/shots/${shot.id}`))
                        }
                      }}>🗑</button>
            </div>
            <div className="muted" style={{ padding: '0 6px 6px', fontSize: 11 }} title={shot.prompt}>
              {shot.shot_label} · seed {shot.seed}
              {/* Which photos the picked copy actually has a twin for: one that
                  has not been shot there yet, or was reshot on a new seed, is
                  not comparable and says so instead of opening a plain photo. */}
              {twin && (twinOf(shot)
                ? <span title={`Compares with ${shotWith(twin)}`}> · ⇄</span>
                : <span title="No twin in the session being compared — not shot yet, or reshot on another seed"> · —</span>)}
            </div>
          </div>
        ))}
      </div>
      {!shots.length && <p className="muted">Nothing to show with this filter.</p>}

      {zoom && (
        <div className="lightbox" onClick={() => setZoom(null)}>
          <div>
            {/* The same wipe the reference comparison uses, on the same frame,
                for the same reason: two models rarely differ by more than a face
                and a fabric, and that difference is invisible when the eye has
                to travel between two pictures. A picked twin wins over the
                reference view — it is the comparison that was asked for. */}
            {twin && twinOf(zoom)
              ? <div onClick={(e) => e.stopPropagation()}>
                  <div className="compare" style={{ '--split': `${split}%` }}>
                    <img src={shotImage(zoom.id)} alt="" />
                    <img className="before" src={shotImage(twinOf(zoom).id)} alt="" />
                    <span className="handle" />
                  </div>
                  <input type="range" min="0" max="100" value={split}
                         onChange={(e) => setSplit(Number(e.target.value))} />
                  <div className="meta">
                    ← {shotWith(twin)} · {shotWith(s)} →
                    {/* One of the two was reshot, so the noise differs as well
                        as the model. Still worth comparing — same take, same
                        prompt — but it is no longer the model alone, and a wipe
                        that does not say so reads as if it were. */}
                    {twinOf(zoom).seed !== zoom.seed && (
                      <><br />seed {twinOf(zoom).seed} vs {zoom.seed} — one side was reshot,
                        so the noise differs too, not only the model</>
                    )}
                  </div>
                </div>
              : before(zoom)
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
