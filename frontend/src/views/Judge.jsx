import React, { useState, useEffect, useCallback } from 'react'
import { api, shotImage } from '../api.js'
import { slotChoices, buildJudgeDeck, computeAgreement } from '../judge.js'

export function Judge() {
  const [sessions, setSessions] = useState([])
  const [sessionId, setSessionId] = useState('')
  const [session, setSession] = useState(null)
  const [slot, setSlot] = useState('camera')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

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
      if (sSlot && (sSlot === 'camera' || sSlot === 'act')) setSlot(sSlot)
    }
  }, [])

  // Load sessions list
  useEffect(() => {
    api.get('/api/sessions')
      .then((data) => {
        setSessions(data || [])
        setLoading(false)
      })
      .catch((err) => {
        setError(err.message || 'Failed to load sessions')
        setLoading(false)
      })
  }, [])

  // Load selected session details
  useEffect(() => {
    if (!sessionId) {
      setSession(null)
      return
    }
    api.get(`/api/sessions/${sessionId}`)
      .then((data) => setSession(data))
      .catch((err) => setError(err.message || 'Failed to load session'))
  }, [sessionId])

  // The families the pass actually photographed, once a pass has been
  // fetched. Before that the screen previews the whole catalogue slice.
  const [passFamilies, setPassFamilies] = useState(null)
  const choices = slotChoices(slot, session?.manner, passFamilies)

  // A different slot or session is a different pass, and the previous pass's
  // families would otherwise filter the preview of the new one.
  useEffect(() => { setPassFamilies(null) }, [slot, sessionId])

  // Start judging pass
  const startPass = async () => {
    if (!sessionId || !slot) return
    setError(null)
    setLoading(true)
    try {
      const passData = await api.get(`/api/sessions/${sessionId}/judge-pass?slot=${slot}`)
      const shots = passData.shots || []
      const controls = passData.controls || []
      setPassFamilies(passData.families || null)
      const newDeck = buildJudgeDeck(shots, controls)
      if (newDeck.length === 0) {
        setError(`No photographs in session #${sessionId} are waiting to be judged for ${slot}.`)
        setLoading(false)
        return
      }
      setDeck(newDeck)
      setDeckIndex(0)
      setResults([])
      setPassDone(false)
      setInPass(true)
    } catch (err) {
      setError(err.message || 'Failed to fetch judging pass')
    } finally {
      setLoading(false)
    }
  }

  // Submit an answer for current photo
  const submitAnswer = useCallback(async (choiceKey, defect = null) => {
    if (submitting || deckIndex >= deck.length) return
    const current = deck[deckIndex]
    setSubmitting(true)
    setError(null)

    try {
      const payload = defect
        ? { defect, slot, control: current.isControl }
        : { [slot]: choiceKey, control: current.isControl }
      const res = await api.post(`/api/shots/${current.shot_id}/judge`, payload)
      const recorded = {
        shot_id: current.shot_id,
        control: current.isControl,
        agreed: res.agreed,
        stored: res.stored,
        answered: defect ? `defect: ${defect}` : res.answered,
        defect: defect || null,
      }
      const nextResults = [...results, recorded]
      setResults(nextResults)

      if (deckIndex + 1 < deck.length) {
        setDeckIndex(deckIndex + 1)
      } else {
        setInPass(false)
        setPassDone(true)
      }
    } catch (err) {
      setError(err.message || 'Failed to record verdict')
    } finally {
      setSubmitting(false)
    }
  }, [deck, deckIndex, slot, submitting, results])

  // Keyboard shortcut listener during active pass
  useEffect(() => {
    if (!inPass || deckIndex >= deck.length || submitting) return

    const onKeyDown = (e) => {
      // Ignore if user is typing in an input
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
        return
      }

      if (e.key === 'Escape') {
        setInPass(false)
        return
      }

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
  }, [inPass, deckIndex, choices, submitAnswer, submitting])

  if (loading && sessions.length === 0) {
    return <div className="panel"><p className="muted">Loading judging screen…</p></div>
  }

  // 1. Completion & Report Screen
  if (passDone) {
    const agreement = computeAgreement(results)
    return (
      <div className="panel" style={{ maxWidth: 800, margin: '20px auto' }}>
        <h2>Judging Pass Complete</h2>
        <p className="muted" style={{ marginBottom: 16 }}>
          Judged <b>{results.length}</b> photograph{results.length === 1 ? '' : 's'} on slot <b>{slot}</b> in session <b>{session?.name || sessionId}</b>.
        </p>

        {agreement.totalControls > 0 ? (
          <div style={{ background: 'var(--panel-2)', padding: 14, borderRadius: 8, marginBottom: 16 }}>
            <h4 style={{ margin: '0 0 6px' }}>Control Photograph Verification</h4>
            <p style={{ margin: 0 }}>
              Agreement with stored verdicts: <b>{agreement.agreedCount} / {agreement.totalControls}</b> ({Math.round((agreement.rate || 0) * 100)}%)
            </p>
            {agreement.disagreements.length > 0 ? (
              <div style={{ marginTop: 12 }}>
                <p className="muted" style={{ margin: '0 0 6px' }}>Disagreements during pass:</p>
                <table>
                  <thead>
                    <tr>
                      <th>Photo</th>
                      <th>Stored verdict</th>
                      <th>Answered this pass</th>
                    </tr>
                  </thead>
                  <tbody>
                    {agreement.disagreements.map((d, i) => (
                      <tr key={i}>
                        <td>#{d.shot_id}</td>
                        <td>{d.stored || '—'}</td>
                        <td>{d.answered || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="good" style={{ margin: '6px 0 0', fontSize: 13 }}>
                ✓ All control photographs agreed with stored verdicts.
              </p>
            )}
          </div>
        ) : (
          <p className="muted">No control photographs were available for this slot in this session.</p>
        )}

        <div className="row" style={{ gap: 10, marginTop: 20 }}>
          <button className="primary" onClick={() => { setPassDone(false); setInPass(false); }}>
            Judge Another Pass
          </button>
          <a href={`#/sessions/${sessionId}`} className="button">
            Back to Session
          </a>
        </div>
      </div>
    )
  }

  // 2. Active Pass Screen (Blind photo presentation & forced choice)
  if (inPass && deck.length > 0 && deckIndex < deck.length) {
    const current = deck[deckIndex]
    return (
      <div className="judge-view" style={{ textAlign: 'center', padding: '10px 20px' }}>
        {error && <div className="error">{error}</div>}

        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <span className="badge">Photo {deckIndex + 1} of {deck.length}</span>
          <span className="muted" style={{ fontSize: 13 }}>
            Judging <b>{slot}</b> · {session?.name}
          </span>
          <button className="icon" onClick={() => setInPass(false)} title="Exit judging pass">✕ Exit</button>
        </div>

        {/* Bare photograph presentation: no brief, no wording, no label, no reference */}
        <div style={{ margin: '10px auto', display: 'flex', justifyContent: 'center', minHeight: '50vh' }}>
          <img
            src={shotImage(current.shot_id)}
            alt="Blind evaluation"
            style={{
              maxHeight: '62vh',
              maxWidth: '90vw',
              objectFit: 'contain',
              borderRadius: 6,
              boxShadow: '0 4px 18px rgba(0,0,0,0.6)',
            }}
          />
        </div>

        {/* Forced choice buttons */}
        <div style={{ maxWidth: 860, margin: '14px auto 0' }}>
          <p style={{ margin: '0 0 10px', fontSize: 14, fontWeight: 500 }}>
            {slot === 'camera' ? 'Which camera position is in this photograph?' : 'Which act/arrangement is in this photograph?'}
          </p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center' }}>
            {choices.map((c, i) => {
              const shortcut = i < 9 ? `${i + 1}` : (i === choices.length - 1 ? '0' : '')
              return (
                <button
                  key={c.key}
                  disabled={submitting}
                  onClick={() => submitAnswer(c.key)}
                  style={{
                    padding: '8px 14px',
                    fontSize: 13,
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    background: c.key === '' ? 'var(--panel-2)' : 'var(--panel)',
                  }}
                >
                  {shortcut && (
                    <kbd style={{
                      background: 'rgba(255,255,255,0.1)',
                      padding: '1px 5px',
                      borderRadius: 3,
                      fontSize: 11,
                      fontFamily: 'monospace',
                      color: 'var(--accent)',
                    }}>{shortcut}</kbd>
                  )}
                  <span>{c.label}</span>
                </button>
              )
            })}
            <button
              disabled={submitting}
              onClick={() => submitAnswer('', 'contradiction')}
              style={{
                padding: '8px 14px',
                fontSize: 13,
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                background: 'var(--panel-2)',
                border: '1px dashed var(--accent)',
              }}
            >
              <kbd style={{
                background: 'rgba(255,255,255,0.1)',
                padding: '1px 5px',
                borderRadius: 3,
                fontSize: 11,
                fontFamily: 'monospace',
                color: 'var(--accent)',
              }}>c</kbd>
              <span>Contradiction (body & camera disagree)</span>
            </button>
          </div>
        </div>
      </div>
    )
  }

  // 3. Setup Screen (Pick session and slot)
  return (
    <div className="panel" style={{ maxWidth: 640, margin: '20px auto' }}>
      <h2>Judging Pass</h2>
      <p className="muted">
        Blind forced-choice evaluation of photographed components. Photographs are presented
        bare — without the prompt, wording, or reference image — and measured against the catalogue.
      </p>

      {error && <div className="error">{error}</div>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 16 }}>
        <div>
          <label>Session</label>
          <select value={sessionId} onChange={(e) => setSessionId(e.target.value)}>
            <option value="">— Select a session to judge —</option>
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} ({s.manner || 'no manner'} · {s.checkpoint || 'no checkpoint'} · {s.shot_count} shots)
              </option>
            ))}
          </select>
        </div>

        <div>
          <label>Slot</label>
          <div className="row" style={{ gap: 8 }}>
            <button
              className={'chip' + (slot === 'camera' ? ' on' : '')}
              onClick={() => setSlot('camera')}
            >
              Camera position
            </button>
            <button
              className={'chip' + (slot === 'act' ? ' on' : '')}
              onClick={() => setSlot('act')}
            >
              Act / arrangement
            </button>
            <button
              className={'chip' + (slot === 'framing' ? ' on' : '')}
              onClick={() => setSlot('framing')}
              // Disabled only when the manner has NO framing to offer. The
              // button used to be disabled outright, on the rule that a forced
              // choice over one option is not a question — true while the
              // screen offered one choice per wording, and the backend gate
              // moved with `slotChoices` to one choice per family plus
              // "None or cannot tell", which is a yes/no question.
              disabled={slotChoices('framing', session?.manner).length === 0}
              title={slotChoices('framing', session?.manner).length === 0
                ? `No framing component for manner ${session?.manner || 'none'}`
                : ''}
            >
              Framing
            </button>
          </div>
        </div>

        {session && (
          <div style={{ background: 'var(--panel-2)', padding: 10, borderRadius: 6, fontSize: 13 }}>
            <div>Manner: <b>{session.manner || 'none'}</b></div>
            <div>Checkpoint: <b>{session.checkpoint || 'none'}</b></div>
            <div>Choices available: <b>{choices.length}</b> ({choices.slice(0, 3).map(c => c.label).join(', ')}…)</div>
          </div>
        )}

        <div style={{ marginTop: 10 }}>
          <button
            className="primary"
            disabled={!sessionId || !slot || loading}
            onClick={startPass}
          >
            Start Judging Pass
          </button>
        </div>
      </div>
    </div>
  )
}
