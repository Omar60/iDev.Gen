import React, { useState } from 'react'
import { KINDS, REACHES, REACH, MANNERS, MANNER, ARRANGEMENTS } from '../kinds.js'
import {
  guideFor, rewriteTake, takesFromBrief, lookFromBrief, rewriteWardrobe,
  wardrobeProgression, sessionFromBrief, briefFromLook, alreadySaid, spread,
  namesWhatIsGone, CHUNK, WARDROBE_STATES, withManner,
} from '../enhance.js'

/** A take of the given kind: an edit kind starts its rows ticked as `ref`,
 *  because a session whose whole point is editing a photo asking you to tick
 *  every row is the app making you repeat yourself.
 *
 *  `wardrobe: null` is "whatever the session is wearing" — not `''`, which is a
 *  take that names no clothes at all. */
export const blankShot = (kind) => ({
  label: '', prompt: '', negative: '', count: KINDS[kind]?.refDefault ? 1 : 4, seed: 0,
  reference: !!KINDS[kind]?.refDefault, reference_strength: null, wardrobe: null,
})

const BRIEF_TAKES = 4
// A slip in the count box is otherwise an hour of API calls nobody asked for.
const MAX_TAKES = 100

/** The takes of a session: pose, angle, framing — and, per row, what is worn.
 *
 *  The wardrobe box is per take because the session's one is only a starting
 *  point: it is written into every prompt rather than stated once, so a take can
 *  change it. A row that leaves the box empty wears the session's, which is the
 *  common case and what holds a shoot together.
 *
 *  Shared by session creation and the "add shots" panel. The kind only chooses
 *  the guidance and the defaults: every row keeps its own `ref` box, so nothing
 *  is locked.
 *
 *  With a prompt assistant configured it also writes: a brief fills the panel, ✨
 *  rewrites one row, and one brief can walk the wardrobe across every take. All
 *  suggestions — text in a box, editable, and nothing is queued until Run.
 *  `context`, `look` and `wardrobe` are what the server already prepends, sent so
 *  the assistant knows what *not* to repeat.
 */
export default function ShotsEditor({ shots, onChange, kind, llm = false,
                                      context = '', look = '', wardrobe = '',
                                      onLook = null }) {
  const set = (i, k, v) => onChange(shots.map((s, j) => (j === i ? { ...s, [k]: v } : s)))
  const total = shots.reduce((n, s) => n + (s.prompt.trim() ? Math.max(1, s.count) : 0), 0)
  const spec = KINDS[kind] || KINDS.shoot

  const [brief, setBrief] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  // Not an error: the writer answered, it just answered shorter. Asked for fifty
  // it gave back forty-five and nothing said so — a shoot missing five
  // photographs looks exactly like a shoot of forty-five until you count.
  const [notice, setNotice] = useState('')
  // How many photographs the shoot is. Not a constant any more: a session that
  // walks somewhere is thirty or fifty takes, and the writer is asked for that
  // many rather than for four, five times over.
  const [howMany, setHowMany] = useState(BRIEF_TAKES)
  // How far the shoot goes. Rolled inside this rather than across all of it: a
  // shoot briefed for a lingerie set and one briefed to end in penetration came
  // out of the same button before, and which of the two you get is not a thing
  // to leave to a die.
  const [reach, setReach] = useState('nude')
  // Whether anyone is photographing this. Beside the reach and not folded into
  // it: the two are orthogonal, and a candid shoot that ends in penetration is
  // as ordinary a thing to ask for as a directed one that keeps its clothes on.
  const [manner, setManner] = useState('directed')
  // Which arrangements of two bodies the shoot may pass through, picked rather
  // than planned: sessions 155 and 161 are made of a handful of them, and a
  // shoot chasing a particular photograph should be able to ask for it. None by
  // default, and a picked one lands in about one photograph in five - the rest
  // of the shoot is the stage plan's own, which is what keeps a session from
  // being one arrangement forty times.
  const [poses, setPoses] = useState([])
  // Which setting the brief in the box was written for. The brief is the shoot —
  // the writer of the lines reads that sentence and nothing else — so a setting
  // moved after the roll changes nothing at all, and silently. Handing the
  // setting to the writer as well was tried and is worse: two texts that
  // disagree, and which one wins is a coin toss. Measured, same brief and
  // setting twice: once undressed from line one, once dressed for all six.
  const [rolledFor, setRolledFor] = useState('')
  // [lines written, lines to write]. Forty takes is ten calls and a few minutes;
  // a button that says nothing for that long looks broken.
  const [made, setMade] = useState([0, 0])
  // What each row said before ✨ touched it. A rewrite that cannot be undone is a
  // rewrite you have to think about before clicking.
  const [undo, setUndo] = useState({})

  // A row that opted out of the kind's default is the other kind of take, and
  // the two want opposite prompts: a description or an instruction.
  const placeholder = (shot, i) => {
    const list = shot.reference === !!spec.refDefault ? spec.examples
      : (shot.reference ? KINDS.edit.examples : KINDS.shoot.examples)
    return list[i % list.length]
  }

  const run = async (what, fn) => {
    setBusy(what); setError(''); setNotice('')
    try { await fn() } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  /** What the writer actually delivered, when it is less than what was asked.
   *
   *  A long ask is answered shorter — that is measured and the rounds exist
   *  because of it — but the rounds do not always close the gap, and a silent
   *  shortfall is a shoot whose middle is missing with nothing on screen saying
   *  so. Say it and let the rows be added anyway: forty-five good photographs are
   *  worth having, and asking again is one more click. */
  const countBack = (got, asked) => {
    if (got < asked) {
      setNotice(`The assistant wrote ${got} of the ${asked} asked for. Add the rest with the `
              + 'button again, or shoot these.')
    }
  }

  // Trigger, base prompt, look and the wardrobe of this very row: what the server
  // prepends, so what the take itself must not say again. Per row, because two
  // rows of one session are now allowed to be wearing different things.
  const already = (theLook, theWardrobe) => alreadySaid(context, theLook, theWardrobe)
  const worn = (shot) => (shot.wardrobe === null || shot.wardrobe === undefined ? wardrobe : shot.wardrobe)
  // Garments this take names that its own wardrobe has already put down. Only a
  // take painted from noise can contradict a wardrobe — a reference one carries
  // neither.
  const gone = (shot) => (shot.reference || shot.verbatim
    ? [] : namesWhatIsGone(shot.prompt, wardrobe, worn(shot)))

  const rewrite = (i) => run(`row${i}`, async () => {
    const shot = shots[i]
    const text = await rewriteTake(kind, shot.reference, shot.prompt, already(look, worn(shot)))
    if (!text) throw new Error('the assistant answered nothing usable')
    setUndo({ ...undo, [i]: shot.prompt })
    set(i, 'prompt', text)
  })

  // Written into the row, never into the session: the session's wardrobe is where
  // the shoot starts, and a row that rewrites its own is the shoot moving on.
  const dress = (i) => run(`worn${i}`, async () => {
    const text = await rewriteWardrobe(worn(shots[i]))
    if (!text) throw new Error('the assistant answered nothing usable')
    set(i, 'wardrobe', text)
  })

  // The one that makes a shoot walk: the clothes change a dozen times at most,
  // spread across however many takes there are. Rows with no prompt yet are
  // skipped — a wardrobe for a photograph nobody wrote is a row you then have to
  // clear by hand.
  const progression = () => run('progression', async () => {
    const rows = shots.map((s, i) => i).filter((i) => (
      shots[i].prompt.trim() && !shots[i].reference && !shots[i].verbatim))
    const wanted = Math.min(rows.length, WARDROBE_STATES)
    setMade([0, wanted])
    const states = await wardrobeProgression(brief, wardrobe, wanted,
                                             (m) => setMade([m, wanted]))
    if (!states.length) throw new Error('the assistant answered nothing usable')
    const lines = spread(states, rows.length)
    onChange(shots.map((s, i) => {
      const at = rows.indexOf(i)
      return at >= 0 && lines[at] ? { ...s, wardrobe: lines[at] } : s
    }))
  })

  // A shoot to run, written from what is already in the two boxes. Into the brief
  // box and nowhere else: it is a sentence to read and edit before anything is
  // written from it.
  const roll = () => run('roll', async () => {
    const written = await briefFromLook(look, wardrobe, reach, manner)
    if (!written) throw new Error('the assistant answered nothing usable')
    setBrief(written)
    setRolledFor(`${reach}|${manner}`)
  })

  /** The look and the wardrobe first, so the takes can be told what not to
   *  repeat — and only into a box that is still empty. One already filled was
   *  decided, typed or read off a photo, and overwriting it is the worst thing
   *  these buttons could do: the session then shoots a wardrobe nobody chose, and
   *  the photo you picked it from is nowhere in it. In the add-shots panel there
   *  is no look to write at all; it belongs to the session. */
  const dressTheSession = async () => {
    let itsLook = look
    let itsWardrobe = wardrobe
    if (onLook && (!look.trim() || !wardrobe.trim())) {
      const written = await lookFromBrief(brief, manner)
      // `undefined` is what tells the session to keep the box it already had.
      const fill = { look: look.trim() ? undefined : written.look,
                     wardrobe: wardrobe.trim() ? undefined : written.wardrobe }
      if (fill.look || fill.wardrobe) {
        onLook(fill)
        itsLook = fill.look || look
        itsWardrobe = fill.wardrobe || wardrobe
      }
    }
    // The manner's own clause on the look, before a line is written from it: the
    // capture quality is constant for the whole shoot, so it rides on the block
    // the app prepends rather than on forty lines that each have to remember it.
    const withIt = withManner(itsLook, manner)
    if (onLook && withIt !== itsLook) {
      onLook({ look: withIt })
      itsLook = withIt
    }
    return { look: itsLook, wardrobe: itsWardrobe }
  }

  const fromBrief = () => run('brief', async () => {
    const it = await dressTheSession()
    const lines = await takesFromBrief(kind, !!spec.refDefault, brief,
                                       already(it.look, it.wardrobe), howMany, manner)
    if (!lines.length) throw new Error('the assistant answered nothing usable')
    countBack(lines.length, howMany)
    onChange([
      ...shots.filter((s) => s.prompt.trim()),
      ...lines.map((l) => ({ ...blankShot(kind), label: l.label, prompt: l.prompt })),
    ])
  })

  /** The whole shoot from one brief: N takes in order, and the N wardrobes that
   *  walk beside them.
   *
   *  `count: 1` and not the kind's default: a step of a progression is one
   *  photograph. Four variations of a step is a decision to make afterwards, in
   *  the count box, on the steps worth it.
   */
  const wholeShoot = () => run('shoot', async () => {
    setMade([0, howMany])
    const it = await dressTheSession()
    // The look alone as the base context: the clothes of each stretch are passed
    // in by the writer itself, photograph by photograph.
    const rows = await sessionFromBrief(brief, already(it.look), it.wardrobe, howMany,
                                        (made, total) => setMade([made, total]), reach, manner,
                                        poses)
    if (!rows.length) throw new Error('the assistant answered nothing usable')
    countBack(rows.length, howMany)
    onChange([
      ...shots.filter((s) => s.prompt.trim()),
      ...rows.map((r) => ({ ...blankShot(kind), count: 1, label: r.label,
                            prompt: r.prompt, wardrobe: r.wardrobe })),
    ])
  })

  return (
    <>
      {llm && guideFor(kind, !!spec.refDefault) && (
        <div className="row" style={{ marginBottom: 8 }}>
          <textarea rows={2} value={brief} disabled={!!busy}
                    placeholder={onLook
                      ? 'Describe the session: “a rooftop at sunset, streetwear, standing, sitting and walking”'
                      : 'Describe what to shoot next and it writes the takes'}
                    onChange={(e) => { setBrief(e.target.value); setRolledFor('') }} />
          {/* The brief is the one box the assistant could not fill, and the look
              and the wardrobe are the photo it was read from — which is exactly
              what a shoot has to be coherent with. */}
          <select value={reach} disabled={!!busy} style={{ width: 190 }}
                  title={REACH[reach]?.blurb}
                  onChange={(e) => setReach(e.target.value)}>
            {REACHES.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
          </select>
          {/* Who is holding the camera. Unlike the reach, this one is handed to the
              writer of the photographs as well as to the brief: the camera clause
              of every line is written from it. */}
          <select value={manner} disabled={!!busy} style={{ width: 170 }}
                  title={MANNER[manner]?.blurb}
                  onChange={(e) => setManner(e.target.value)}>
            {MANNERS.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
          </select>
          {/* The arrangements, only where they mean anything: a shoot that keeps
              its clothes on has no two bodies to arrange. Multiple on purpose -
              picking several is picking a pool, and the plan spreads them. */}
          {reach !== 'nude' && (
            <select multiple value={poses} disabled={!!busy} size={3} style={{ width: 210 }}
                    title="Arrangements the shoot may pass through, from sessions 155 and 161. Pick none and the shoot writes its own; pick several and one lands in about every fifth photograph."
                    onChange={(e) => setPoses([...e.target.selectedOptions].map((o) => o.value))}>
              {ARRANGEMENTS.map((a) => <option key={a.key} value={a.key}>{a.label}</option>)}
            </select>
          )}
          <button className="icon" onClick={roll} disabled={!!busy || !(look.trim() || wardrobe.trim())}
                  title={look.trim() || wardrobe.trim()
                    ? `Write me a shoot to run, from the look and the wardrobe: ${REACH[reach]?.blurb} A different one every time — how fast it moves and how it reads are rolled.`
                    : 'Fill the look or the wardrobe first — a shoot is written from them'}>
            {busy === 'roll' ? '…' : '🎲'}
          </button>
          {/* How long the shoot is. Beside the buttons because it is what both of
              them are asked for, and a session that walks somewhere is thirty or
              fifty photographs, not four. */}
          <input type="number" min="1" max={MAX_TAKES} value={howMany} disabled={!!busy}
                 style={{ width: 64 }} title="How many takes to write"
                 onChange={(e) => setHowMany(Math.min(MAX_TAKES,
                                                      Math.max(1, Number(e.target.value) || 1)))} />
          <button style={{ whiteSpace: 'nowrap' }}
                  title="Takes that vary the pose, the framing and the camera height, in no particular order — one look, photographed several ways."
                  disabled={!brief.trim() || !!busy} onClick={fromBrief}>
            {busy === 'brief' ? '…' : `✨ ${howMany} takes`}
          </button>
          {/* The shoot that walks somewhere, both halves of it in one click. */}
          {!spec.refDefault && (
            <button className="primary" style={{ whiteSpace: 'nowrap' }} onClick={wholeShoot}
                    disabled={!brief.trim() || !!busy}
                    title="The whole shoot from one brief: the takes in order, and a wardrobe for each of them walking beside the poses. Say where it starts, where it ends and what stays on.">
              {busy === 'shoot' ? `… ${made[0]}/${made[1]}` : '🎬 The whole shoot'}
            </button>
          )}
          {/* The same wardrobe walk, on takes that already exist — written by hand,
              kept from an earlier shoot, or added since. */}
          {!spec.refDefault && shots.some((s) => s.prompt.trim()) && (
            <button style={{ whiteSpace: 'nowrap' }} onClick={progression}
                    disabled={!brief.trim() || !!busy || !wardrobe.trim()}
                    title="One wardrobe per take already written, in order, carrying over word for word whatever the take before it did not change.">
              {busy === 'progression' ? `… ${made[0]}/${made[1]}` : '👗 Wardrobe per take'}
            </button>
          )}
          {/* Which of them it is about to do, before it does it. */}
          <span className="muted">
            {busy === 'shoot' || busy === 'progression'
              ? `written in rounds of ${CHUNK} — a long list comes back short and loses `
                + 'the middle of the shoot'
              : onLook && (!look.trim() || !wardrobe.trim())
                ? `writes the ${!look.trim() && !wardrobe.trim() ? 'look and the wardrobe'
                                : !look.trim() ? 'look' : 'wardrobe'} too, still empty`
                : 'takes only — the look and the wardrobe above are kept as they are'}
          </span>
        </div>
      )}
      {error && <div className="error" onClick={() => setError('')}>{error}</div>}
      {rolledFor && rolledFor !== `${reach}|${manner}` && (
        <div className="muted" style={{ marginBottom: 8 }}>
          The brief in the box was written for <b>{REACH[rolledFor.split('|')[0]]?.label}</b>,{' '}
          <b>{MANNER[rolledFor.split('|')[1]]?.label}</b>, and the shoot is written from that
          sentence — 🎲 again for <b>{REACH[reach]?.label}</b>, <b>{MANNER[manner]?.label}</b>, or
          edit it by hand. Changing these boxes alone changes nothing.
        </div>
      )}
      {notice && <div className="muted" onClick={() => setNotice('')}
                      style={{ marginBottom: 8, cursor: 'pointer' }}>{notice}</div>}
      <table className="looks-table">
        <tbody>
          {shots.map((shot, i) => (
            <tr key={i}>
              <td className="lbl">
                <input value={shot.label} placeholder={`shot ${i + 1}`}
                       onChange={(e) => set(i, 'label', e.target.value)} />
              </td>
              <td>
                <textarea value={shot.prompt} rows={2}
                          className={gone(shot).length || shot.suspect ? 'contradicts' : ''}
                          title={gone(shot).length
                            ? `This take names ${gone(shot).join(', ')} — and the wardrobe of `
                              + 'this very take does not have it any more. Both go into one '
                              + 'prompt, and a prompt that names a garment has that garment '
                              + 'in the photograph. Say what the body does instead.'
                            : shot.suspect
                              ? 'This line failed a check and the rewrite did not fix it: it '
                                + 'either leaves part of the body unsaid — and an unstated '
                                + 'part is one the model dresses for you — or it puts back a '
                                + 'garment the photograph before it was not wearing. Worth '
                                + 'a read before you run it.'
                              : ''}
                          placeholder={placeholder(shot, i)}
                          onChange={(e) => set(i, 'prompt', e.target.value)} />
                {/* A reference take carries no wardrobe at all: it is an
                    instruction on a photo that is already wearing one. Nor does a
                    verbatim one — "more like this" hands back a prompt with the
                    wardrobe already inside it. */}
                {!shot.reference && !shot.verbatim && (
                  <textarea rows={1} className="worn" value={shot.wardrobe ?? ''}
                            placeholder={wardrobe
                              ? `worn: ${wardrobe}`
                              : 'worn in this take — empty follows the session'}
                            title="What is worn in this take. Empty wears the session's wardrobe; anything here replaces it for this take only, which is how a shoot takes something off."
                            onChange={(e) => set(i, 'wardrobe',
                                                 e.target.value === '' ? null : e.target.value)} />
                )}
              </td>
              {llm && (
                <td className="n">
                  {guideFor(kind, shot.reference) && (
                    undo[i] !== undefined && undo[i] !== shot.prompt
                      ? <button className="icon" title="Put back what this take said before"
                                onClick={() => { set(i, 'prompt', undo[i]); setUndo({ ...undo, [i]: undefined }) }}>↩</button>
                      : <button className="icon" disabled={!shot.prompt.trim() || !!busy}
                                title={shot.reference
                                  ? 'Rewrite as an instruction on the reference photo'
                                  : 'Rewrite this take — the look and this take\'s wardrobe are not repeated in it'}
                                onClick={() => rewrite(i)}>{busy === `row${i}` ? '…' : '✨'}</button>
                  )}
                  {!shot.reference && !shot.verbatim && (
                    <button className="icon" disabled={!worn(shot).trim() || !!busy}
                            title="Rewrite what is worn in this take — every garment precisely, so it comes back the same twice"
                            onClick={() => dress(i)}>{busy === `worn${i}` ? '…' : '👗'}</button>
                  )}
                </td>
              )}
              <td className="n">
                <label className="chk" title="Edit the session's reference photo instead of shooting from scratch. The prompt is sent as an instruction, on its own — no trigger, no base prompt, no look.">
                  <input type="checkbox" checked={!!shot.reference}
                         onChange={(e) => set(i, 'reference', e.target.checked)} />
                  ref
                </label>
              </td>
              <td className="n">
                {/* Only a reference take has anything to be pulled towards. */}
                {shot.reference && (
                  <input type="number" step="0.1" min="0" placeholder="str"
                         value={shot.reference_strength ?? ''}
                         title="Reference strength. Empty follows the session. High holds the frame still so a garment edit lands cleanly; low lets the pose move. Shoot the same prompt and seed at a few values to find yours."
                         onChange={(e) => set(i, 'reference_strength',
                                              e.target.value === '' ? null : parseFloat(e.target.value))} />
                )}
              </td>
              <td className="n">
                <input type="number" min="1" value={shot.count} title="Variations"
                       onChange={(e) => set(i, 'count', Number(e.target.value))} />
              </td>
              <td className="n">
                <input type="number" min="0" value={shot.seed || ''} placeholder="seed"
                       title="Seed. Empty follows the session; set it to compare a prompt change on the same noise."
                       onChange={(e) => set(i, 'seed', Number(e.target.value))} />
              </td>
              <td style={{ width: 34 }}>
                <button className="icon danger" title="Remove"
                        onClick={() => onChange(shots.filter((_, j) => j !== i))}>×</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row" style={{ marginTop: 8 }}>
        <button onClick={() => onChange([...shots, blankShot(kind)])}>+ Shot</button>
        <span className="muted">
          {total} photos · {spec.footer}
          {!spec.refDefault && (
            <> Tick <b>ref</b> on a take to edit the session's reference photo instead: the prompt
            goes out on its own, as an instruction on a photo that already exists, which keeps
            the frame and changes one thing in it.</>
          )}
        </span>
      </div>
    </>
  )
}
