import React, { useState, useEffect, useCallback, useRef } from 'react'
import { api, shotImage } from '../api.js'
import { slotChoices, buildJudgeDeck, computeAgreement } from '../judge.js'
import '../ui/foundations.css'
import './Judge.css'

export function Judge() {
  const [sessions, setSessions] = useState([])
  const [sessionId, setSessionId] = useState('')
  const [session, setSession] = useState(null)
  const [slot, setSlot] = useState('camera')
  // Which question of the slot to ask. A vocabulary whose readings carry an
  // `axis` asks more than one thing at once -- directed's cameras hold where the
  // camera stood AND how high it was, both true of every photograph -- and the
  // endpoint refuses a pass that does not name one.
  const [axis, setAxis] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [sessionLoading, setSessionLoading] = useState(false)
  const [readingsLoading, setReadingsLoading] = useState(false)
  const [readingsError, setReadingsError] = useState(null)
  const [readingBusy, setReadingBusy] = useState(false)
  const [answerUncertain, setAnswerUncertain] = useState(false)
  const [imageState, setImageState] = useState('loading')
  const [imageAttempt, setImageAttempt] = useState(0)
  const mounted = useRef(true)
  const sessionRequest = useRef(0)
  const readingRequest = useRef(0)
  const passEpoch = useRef(0)
  const startPending = useRef(false)
  const answerPending = useRef(false)
  const readingPending = useRef(false)
  const context = useRef('')
  context.current = `${sessionId}:${slot}:${axis}`
  const startRef = useRef(null)
  const setupHeading = useRef(null)
  const passHeading = useRef(null)
  const passRoot = useRef(null)
  const viewRoot = useRef(null)
  const focusTransfer = useRef(false)
  const reportHeading = useRef(null)
  const errorRef = useRef(null)
  const wasInPass = useRef(false)
  const sessionMatches = Boolean(session && String(session.id) === String(sessionId))
  const focusAllowed = (transfer = false) => {
    const root = viewRoot.current
    const focus = document.activeElement
    if (!mounted.current || !root || root.closest('[inert]') || focus?.closest('[inert]')) return false
    const modals = document.querySelectorAll('dialog[open], [role="dialog"][aria-modal="true"], [role="alertdialog"][aria-modal="true"]')
    if ([...modals].some((modal) => !root.contains(modal) && !modal.contains(root))) return false
    return root.contains(focus) || (transfer && focus === document.body)
  }
  const focusWithin = (target, transfer = false) => {
    if (!target?.isConnected || target.closest('[inert]') || !focusAllowed(transfer)) return false
    target.focus({ preventScroll: true })
    return document.activeElement === target
  }
  const exitPass = () => {
    focusTransfer.current = focusAllowed()
    passEpoch.current++
    setInPass(false)
  }

  // Pass execution state
  const [inPass, setInPass] = useState(false)
  const [deck, setDeck] = useState([])
  const [deckIndex, setDeckIndex] = useState(0)
  const [results, setResults] = useState([])
  const [submitting, setSubmitting] = useState(false)
  const [passDone, setPassDone] = useState(false)

  // Parse session_id and slot from hash query if present
  useEffect(() => {
    const hash = window.location.hash || ''
    const qIndex = hash.indexOf('?')
    if (qIndex !== -1) {
      const params = new URLSearchParams(hash.slice(qIndex + 1))
      const sid = params.get('session_id')
      const sSlot = params.get('slot')
      if (sid) setSessionId(sid)
      if (sSlot && (sSlot === 'camera' || sSlot === 'act' || sSlot === 'framing')) setSlot(sSlot)
    }
  }, [])

  const loadSessions = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.get('/api/sessions')
      if (mounted.current) setSessions(data || [])
    } catch (err) {
      if (mounted.current) setError(err.message || 'Failed to load sessions')
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [])
  useEffect(() => {
    mounted.current = true
    loadSessions()
    return () => { mounted.current = false; passEpoch.current++; sessionRequest.current++; readingRequest.current++ }
  }, [loadSessions])

  const loadSession = useCallback(async () => {
    const request = ++sessionRequest.current
    if (!sessionId) { setSession(null); setSessionLoading(false); return }
    setSessionLoading(true)
    setError(null)
    try {
      const data = await api.get(`/api/sessions/${sessionId}`)
      if (mounted.current && request === sessionRequest.current) setSession(data)
    } catch (err) {
      if (mounted.current && request === sessionRequest.current) setError(err.message || 'Failed to load session')
    } finally {
      if (mounted.current && request === sessionRequest.current) setSessionLoading(false)
    }
  }, [sessionId])
  useEffect(() => { loadSession() }, [loadSession])

  // All readings available for this session & manner
  const [allReadings, setAllReadings] = useState([])
  const [passReadings, setPassReadings] = useState([])
  const [newReadingKey, setNewReadingKey] = useState('')
  const [newReadingLabel, setNewReadingLabel] = useState('')
  const [newReadingSlot, setNewReadingSlot] = useState('camera')
  const [readingActionError, setReadingActionError] = useState(null)

  const loadReadings = useCallback(async () => {
    const request = ++readingRequest.current
    if (!sessionId || !sessionMatches || !session?.manner) {
      setAllReadings([])
      setReadingsLoading(false)
      setReadingsError(null)
      return
    }
    setReadingsLoading(true)
    setReadingsError(null)
    try {
      const data = await api.get(`/api/readings?manner=${session.manner}&session_id=${sessionId}`)
      if (mounted.current && request === readingRequest.current) setAllReadings(data || [])
    } catch (err) {
      if (mounted.current && request === readingRequest.current) setReadingsError(err.message || 'Failed to load readings')
    } finally {
      if (mounted.current && request === readingRequest.current) setReadingsLoading(false)
    }
  }, [sessionId, session?.manner, sessionMatches])

  useEffect(() => {
    loadReadings()
  }, [loadReadings])

  const slotReadings = allReadings.filter((r) => r.slot === slot)
  const axes = [...new Set(slotReadings.map((r) => r.axis).filter(Boolean))].sort()
  const activeReadings = inPass
    ? passReadings
    : (axis ? slotReadings.filter((r) => r.axis === axis) : slotReadings)
  const choices = slotChoices(activeReadings)

  // Start judging pass
  const startPass = async () => {
    if (!sessionId || !slot || !sessionMatches || sessionLoading || readingsLoading || readingsError || submitting || startPending.current) return
    if (axes.length && !axis) return
    startPending.current = true
    focusWithin(setupHeading.current)
    const requestedContext = context.current
    const epoch = ++passEpoch.current
    setError(null)
    setLoading(true)
    try {
      const passData = await api.get(
        `/api/sessions/${sessionId}/judge-pass?slot=${slot}` + (axis ? `&axis=${axis}` : ''))
      if (!mounted.current || epoch !== passEpoch.current || requestedContext !== context.current) return
      const shots = passData.shots || []
      const controls = passData.controls || []
      const readings = passData.readings || []
      setPassReadings(readings)
      setImageState('loading')
      const newDeck = buildJudgeDeck(shots, controls)
      if (newDeck.length === 0) {
        setError(`No photographs in session #${sessionId} are waiting to be judged for ${slot}.`)
        setLoading(false)
        return
      }
      focusTransfer.current = focusAllowed()
      setDeck(newDeck)
      setDeckIndex(0)
      setResults([])
      setPassDone(false)
      setInPass(true)
    } catch (err) {
      if (mounted.current && epoch === passEpoch.current && requestedContext === context.current) setError(err.message || 'Failed to fetch judging pass')
    } finally {
      startPending.current = false
      if (mounted.current) setLoading(false)
    }
  }

  const handleAddSessionReading = async (e) => {
    e.preventDefault()
    if (!sessionId || !sessionMatches || !session?.manner || !newReadingKey.trim() || !newReadingLabel.trim() || readingPending.current) return
    readingPending.current = true
    setReadingBusy(true)
    const selectedSession = sessionId
    setReadingActionError(null)
    try {
      await api.post('/api/readings', {
        slot: newReadingSlot,
        manner: session.manner,
        key: newReadingKey.trim(),
        label: newReadingLabel.trim(),
        session_id: parseInt(sessionId, 10),
      })
      if (!mounted.current || !context.current.startsWith(`${selectedSession}:`)) return
      setNewReadingKey('')
      setNewReadingLabel('')
      await loadReadings()
    } catch (err) {
      if (mounted.current && context.current.startsWith(`${selectedSession}:`)) setReadingActionError(err.message || 'Failed to add session reading')
    } finally {
      readingPending.current = false
      if (mounted.current) setReadingBusy(false)
    }
  }

  const handleDeleteSessionReading = async (readingId) => {
    if (readingPending.current || !confirm(`Remove session reading #${readingId}? Readings referenced by stored answers cannot be removed.`)) return
    readingPending.current = true
    setReadingBusy(true)
    const selectedSession = sessionId
    setReadingActionError(null)
    try {
      await api.del(`/api/readings/${readingId}`)
      if (mounted.current && context.current.startsWith(`${selectedSession}:`)) await loadReadings()
    } catch (err) {
      if (mounted.current && context.current.startsWith(`${selectedSession}:`)) setReadingActionError(err.message || 'Failed to delete session reading')
    } finally {
      readingPending.current = false
      if (mounted.current) setReadingBusy(false)
    }
  }

  // Submit an answer for current photo
  const submitAnswer = useCallback(async (choiceKey, defect = null) => {
    if (submitting || answerPending.current || imageState !== 'ready' || deckIndex >= deck.length) return
    answerPending.current = true
    const epoch = passEpoch.current
    const current = deck[deckIndex]
    if (document.activeElement?.matches('.judge-choices button, .judge-defect button')) focusWithin(passHeading.current)
    setSubmitting(true)
    setError(null)
    setAnswerUncertain(false)

    try {
      const payload = defect
        ? { defect, slot, control: current.isControl }
        : { [slot]: choiceKey, control: current.isControl }
      const res = await api.post(`/api/shots/${current.shot_id}/judge`, payload)
      if (!mounted.current || epoch !== passEpoch.current) return
      const recorded = {
        shot_id: current.shot_id,
        control: current.isControl,
        agreed: res.agreed,
        stored: res.stored,
        answered: defect ? `defect: ${defect}` : res.answered,
        defect: defect || null,
      }
      const nextResults = [...results, recorded]
      focusTransfer.current = focusAllowed()
      setResults(nextResults)

      if (deckIndex + 1 < deck.length) {
        setImageState('loading')
        setDeckIndex(deckIndex + 1)
      } else {
        setInPass(false)
        setPassDone(true)
      }
    } catch (err) {
      if (mounted.current && epoch === passEpoch.current) { setAnswerUncertain(!(err.status >= 400 && err.status < 500)); setError(err.message || 'Failed to record verdict') }
    } finally {
      answerPending.current = false
      if (mounted.current) setSubmitting(false)
    }
  }, [deck, deckIndex, slot, submitting, results, imageState])

  // Keyboard shortcut listener during active pass
  useEffect(() => {
    if (!inPass || deckIndex >= deck.length) return

    const onKeyDown = (e) => {
      const root = passRoot.current
      const focus = document.activeElement
      if (e.defaultPrevented || !(e.target instanceof Element) || !root?.contains(focus) || !root.contains(e.target)) return
      if (focus.closest('[inert]') || e.target.closest('[inert]')) return
      const modals = document.querySelectorAll('dialog[open], [role="dialog"][aria-modal="true"], [role="alertdialog"][aria-modal="true"]')
      if ([...modals].some((modal) => !root.contains(modal) && !modal.contains(root))) return
      if (e.repeat || e.ctrlKey || e.metaKey || e.altKey || e.target.isContentEditable) return
      // Ignore if user is typing in an input
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
        return
      }

      if (e.key === 'Escape') {
        exitPass()
        return
      }

      if (submitting || imageState !== 'ready') return
      if (e.key === 'c' || e.key === 'C') {
        e.preventDefault()
        submitAnswer('', 'contradiction')
        return
      }

      // Shortcut keys: '1'..'9' for choices[0..8], '0' for last choice ("None")
      if (e.key >= '1' && e.key <= '9') {
        const idx = parseInt(e.key, 10) - 1
        if (idx < choices.length - 1) {
          e.preventDefault()
          submitAnswer(choices[idx].key)
        }
      } else if (e.key === '0') {
        // '0' maps to the last option (None or cannot tell)
        if (choices.length > 0) {
          e.preventDefault()
          submitAnswer(choices[choices.length - 1].key)
        }
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [inPass, deckIndex, choices, submitAnswer, submitting, imageState])

  useEffect(() => {
    // Consume permission once, including when a modal blocks the transition.
    const transfer = focusTransfer.current
    focusTransfer.current = false
    const heading = inPass ? passHeading.current : passDone ? reportHeading.current : null
    if (heading) { if (focusWithin(heading, transfer)) heading.scrollIntoView({ block: 'start' }) }
    else if (wasInPass.current) focusWithin(startRef.current?.disabled ? setupHeading.current : startRef.current, transfer)
    wasInPass.current = inPass
  }, [inPass, deckIndex, passDone])
  useEffect(() => { if (error) focusWithin(errorRef.current) }, [error])


  const errorNotice = error && <div ref={errorRef} className="ui-notice" data-tone="error" role="alert" tabIndex={-1}>{error}</div>

  if (passDone) {
    const agreement = computeAgreement(results)
    return <div ref={viewRoot} className="studio-ui ui-page judge-view ui-stack">
      <header className="ui-page-header"><p className="judge-eyebrow">Judge / Report</p>
        <h2 ref={reportHeading} tabIndex={-1} className="ui-page-title">Judging Pass Complete</h2>
        <p className="ui-lead">Judged {results.length} photograph{results.length === 1 ? '' : 's'} on {slot} in {session?.name || `session #${sessionId}`}.</p></header>
      <section className="ui-surface ui-stack" aria-labelledby="judge-agreement">
        <h3 id="judge-agreement" className="ui-section-title">Control Photograph Verification</h3>
        {agreement.totalControls > 0 ? <>
          <p className="judge-result">{Math.round((agreement.rate || 0) * 100)}% <span>agreement with stored verdicts</span></p>
          <p>{agreement.agreedCount} / {agreement.totalControls} control photographs agreed. Stored verdicts are unchanged.</p>
          {agreement.disagreements.length > 0 ? <div className="judge-table" role="region" aria-label="Control disagreements" tabIndex={0}>
            <table><caption>Disagreements during this pass</caption><thead><tr><th scope="col">Photo</th><th scope="col">Stored verdict</th><th scope="col">Answered this pass</th></tr></thead>
              <tbody>{agreement.disagreements.map((d, i) => <tr key={i}><td>#{d.shot_id}</td><td>{d.stored === '' ? 'None or cannot tell' : d.stored ?? '—'}</td><td>{d.answered === '' ? 'None or cannot tell' : d.answered ?? '—'}</td></tr>)}</tbody></table>
          </div> : <p className="ui-state" data-tone="success">All control photographs agreed with stored verdicts.</p>}
        </> : <p className="ui-notice">No control photographs were available for this slot in this session. No agreement score is available.</p>}
      </section>
      <div className="ui-actions"><button className="ui-button ui-button--primary" onClick={() => { setPassDone(false); setInPass(false) }}>Judge Another Pass</button>
        <a href={`#/session/${sessionId}`} className="ui-button">Back to Session</a></div>
    </div>
  }

  if (inPass && deckIndex < deck.length) {
    const current = deck[deckIndex]
    const question = slot === 'camera' ? 'Which camera position is in this photograph?' : slot === 'framing' ? 'Which framing is in this photograph?' : 'Which act/arrangement is in this photograph?'
    return <div ref={(node) => { passRoot.current = node; viewRoot.current = node }} className="studio-ui ui-page judge-view judge-pass ui-stack">
      <header className="judge-pass-header"><div><p className="judge-eyebrow">Judge / Blind pass</p>
        <h2 ref={passHeading} tabIndex={-1} className="ui-page-title">Photo {deckIndex + 1} of {deck.length}</h2>
        <p className="ui-help">{slot}{axis ? ` / ${axis}` : ''} · {session?.name || `Session #${sessionId}`}</p></div>
        <button className="ui-button" onClick={exitPass}>Exit pass</button></header>
      <progress aria-label="Pass progress" value={deckIndex} max={deck.length} />
      {errorNotice}
      {error && <p className="ui-help">{answerUncertain ? 'The response was not confirmed. Check the session before repeating an uncertain operation.' : 'The answer was rejected. Review the error before trying again.'} This photo has not advanced.</p>}
      <div className="judge-photo">
        <img key={`${deckIndex}:${imageAttempt}`} src={shotImage(current.shot_id)} alt="Blind evaluation" decoding="async"
          onLoad={() => setImageState('ready')} onError={() => setImageState('error')} />
        {imageState === 'loading' && <p className="judge-photo-state" role="status">Loading photograph…</p>}
        {imageState === 'error' && <div className="judge-photo-state ui-notice" data-tone="error" role="alert"><p>Photograph could not be loaded. No answer has been recorded.</p>
          <button className="ui-button" onClick={() => { focusWithin(passHeading.current); setImageState('loading'); setImageAttempt(imageAttempt + 1) }}>Reload photograph</button></div>}
      </div>
      <section className="ui-surface" aria-labelledby="judge-question" aria-busy={submitting}>
        <h3 id="judge-question" className="ui-section-title">{question}</h3>
        <p className="ui-section-description">Choose what is visible. Prompts, references and stored verdicts are hidden during the pass.</p>
        <div className="judge-choices">{choices.map((c, i) => {
          const shortcut = i === choices.length - 1 ? '0' : i < 9 ? `${i + 1}` : ''
          return <button className="ui-button" key={c.key} disabled={submitting || imageState !== 'ready'} onClick={() => submitAnswer(c.key)}>
            {shortcut && <kbd>{shortcut}</kbd>}<span>{c.label}</span></button>
        })}</div>
        <div className="judge-defect"><button className="ui-button" disabled={submitting || imageState !== 'ready'} onClick={() => submitAnswer('', 'contradiction')}><kbd>c</kbd> Contradiction (body &amp; camera disagree)</button></div>
        <p className="ui-help" role="status">{submitting ? 'Recording answer…' : 'Keys 1–9: listed choices · 0: none or cannot tell · C: contradiction · Escape: exit'}</p>
      </section>
      <p className="ui-help">Each answer is recorded explicitly. Exiting does not undo recorded answers or cancel a request already sent.</p>
    </div>
  }

  const blocked = !sessionId ? 'Select a session to begin.' : sessionLoading ? 'Loading session details…' : !sessionMatches ? 'Session details are unavailable. Retry before continuing.' : readingsLoading ? 'Loading readings…' : readingsError ? 'Readings are unavailable. Retry before continuing.' : axes.length && !axis ? 'Choose the question this pass will ask.' : 'Ready to request a blind pass. No verdict is recorded when you start.'
  const sessionReadings = allReadings.filter((r) => r.session_id !== null)
  return <div ref={viewRoot} className="studio-ui ui-page judge-view ui-stack">
    <header className="ui-page-header"><p className="judge-eyebrow">Judge / Evaluation</p><h2 ref={setupHeading} tabIndex={-1} className="ui-page-title">Judging Pass</h2>
      <p className="ui-lead">Read the photograph, not the prompt. A blind forced-choice pass measures visible components against the catalogue.</p></header>
    {errorNotice}
    {loading && !sessions.length ? <p role="status" className="ui-notice">Loading sessions…</p> : !sessions.length && <div className="ui-notice"><p>No sessions are available.</p><button className="ui-button" onClick={loadSessions}>Reload sessions</button></div>}
    <div className="judge-setup-grid">
      <section className="ui-surface ui-stack" aria-labelledby="judge-setup">
        <h3 className="ui-section-title" id="judge-setup">Configure the pass</h3>
        <div className="ui-field"><label className="ui-label" htmlFor="judge-session">Session</label>
          <select className="ui-control" id="judge-session" value={sessionId} disabled={loading || readingBusy || submitting} onChange={(e) => { setSessionId(e.target.value); setAxis('') }}>
            <option value="">— Select a session to judge —</option>{sessions.map((s) => <option key={s.id} value={s.id}>{s.name || `Session #${s.id}`} ({s.manner || 'no manner'} · {s.checkpoint || 'no checkpoint'} · {s.shot_count} shots)</option>)}</select></div>
        <fieldset className="judge-fieldset"><legend className="ui-label">Slot</legend><div className="ui-actions">{[['camera', 'Camera position'], ['act', 'Act / arrangement'], ['framing', 'Framing']].map(([key, label]) =>
          <button className="ui-button" key={key} aria-pressed={slot === key} disabled={loading || (key === 'framing' && !allReadings.some((r) => r.slot === 'framing'))} onClick={() => { setSlot(key); setAxis('') }}>{label}</button>)}</div>
          {!allReadings.some((r) => r.slot === 'framing') && <p className="ui-help">Framing requires readings for the selected manner.</p>}</fieldset>
        {axes.length > 0 && <fieldset className="judge-fieldset"><legend className="ui-label">Question</legend><div className="ui-actions">{axes.map((a) => <button key={a} className="ui-button" aria-pressed={axis === a} onClick={() => setAxis(a)}>{a}</button>)}</div>
          <p className="ui-help">This slot has multiple axes. Choose one explicitly; the pass asks only that question.</p></fieldset>}
        <p className="ui-notice" role="status">{blocked}</p>
        {sessionId && !sessionMatches && !sessionLoading && <button className="ui-button" onClick={loadSession}>Retry session details</button>}
        <div className="ui-actions"><button ref={startRef} className="ui-button ui-button--primary" disabled={!sessionMatches || loading || sessionLoading || readingsLoading || Boolean(readingsError) || readingBusy || submitting || Boolean(axes.length && !axis)} onClick={startPass}>{loading ? 'Requesting pass…' : 'Start Judging Pass'}</button></div>
      </section>
      <aside className="ui-surface" aria-labelledby="judge-context"><h3 className="ui-section-title" id="judge-context">Session context</h3>
        {sessionMatches ? <dl className="ui-definition-list"><div><dt>Session</dt><dd>{session.name || `Session #${sessionId}`}</dd></div><div><dt>Manner</dt><dd>{session.manner || 'none'}</dd></div><div><dt>Checkpoint</dt><dd>{session.checkpoint || 'none'}</dd></div><div><dt>Readings in this question</dt><dd>{activeReadings.length}</dd></div></dl> : <p className="ui-section-description">Select a session to see its vocabulary and context.</p>}
        <p className="ui-help ui-help-gap">Controls are mixed into the pass without identification. Agreement is shown only after completion.</p>
      </aside>
    </div>
    {sessionMatches && <section className="ui-surface ui-stack" aria-labelledby="judge-readings">
      <div><h3 className="ui-section-title" id="judge-readings">Session Readings</h3><p className="ui-section-description">Vocabulary additions for session #{sessionId}. These extend the base readings for this session only.</p></div>
      {readingsError && <div className="ui-notice" data-tone="error" role="alert">{readingsError} <button className="ui-button" onClick={loadReadings}>Retry readings</button></div>}
      {readingActionError && <div className="ui-notice" data-tone="error" role="alert">{readingActionError}</div>}
      {readingsLoading ? <p role="status">Loading readings…</p> : sessionReadings.length > 0 ? <div className="judge-table" role="region" aria-label="Session readings" tabIndex={0}><table><caption>Session-scoped vocabulary</caption><thead><tr><th scope="col">Slot</th><th scope="col">Key (family)</th><th scope="col">Viewer description</th><th scope="col">Action</th></tr></thead><tbody>{sessionReadings.map((r) => <tr key={r.id}><td>{r.slot}</td><td><code>{r.key}</code></td><td>{r.label}</td><td><button className="ui-button judge-remove" disabled={readingBusy} onClick={() => handleDeleteSessionReading(r.id)} aria-label={`Remove reading ${r.label}`}>Remove</button></td></tr>)}</tbody></table></div> : !readingsError && <p className="ui-notice">No session-scoped readings yet. Base readings remain available.</p>}
      <form onSubmit={handleAddSessionReading} className="judge-reading-form">
        <div className="ui-field"><label htmlFor="judge-reading-slot" className="ui-label">Reading slot</label><select id="judge-reading-slot" className="ui-control" value={newReadingSlot} disabled={readingBusy} onChange={(e) => setNewReadingSlot(e.target.value)}>{['camera', 'act', 'framing'].map((s) => <option key={s}>{s}</option>)}</select></div>
        <div className="ui-field"><label htmlFor="judge-reading-key" className="ui-label">Key (family)</label><input id="judge-reading-key" className="ui-control" placeholder="e.g. overhead" required value={newReadingKey} disabled={readingBusy} onChange={(e) => setNewReadingKey(e.target.value)} aria-describedby="judge-reading-help" /></div>
        <div className="ui-field"><label htmlFor="judge-reading-label" className="ui-label">Label (viewer description)</label><input id="judge-reading-label" className="ui-control" placeholder="e.g. Overhead view from above" required value={newReadingLabel} disabled={readingBusy} onChange={(e) => setNewReadingLabel(e.target.value)} /></div>
        <button type="submit" className="ui-button" disabled={readingBusy}>{readingBusy ? 'Saving…' : 'Add Session Reading'}</button>
        <p id="judge-reading-help" className="ui-help">The key identifies the visible outcome; it may also match a catalogue family. Removing a reading requires confirmation; referenced readings cannot be removed.</p>
      </form>
    </section>}
  </div>
}
