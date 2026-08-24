import React, { useEffect, useRef, useState } from 'react'
import { api, shotImage } from '../api'
import { shuffle, nextPlay } from '../deck'

/** The three settings the screen reads from the URL, with the working defaults
 *  the page falls back to when the hash is absent, out of range or not a number.
 *  The address carries the configuration so a phone home-screen shortcut
 *  restores it on next open. */
const DEFAULTS = { interval: 3, min_rating: 0, lookahead: 3 }
const CLAMPS = { interval: [1, 60], min_rating: [0, 5], lookahead: [1, 10] }

// What the two on-screen controls offer. The intervals are a short list of the
// ones anyone actually picks rather than every value in the clamp: a menu of
// sixty entries is not a control on a phone. Any other valid interval still
// arrives through the address and is added to the list on the fly, so the
// control never shows blank for a value the screen is really playing.
const INTERVAL_CHOICES = [1, 2, 3, 5, 10, 15, 30, 60]
// `0` is "everything that was not rejected", which is the useful setting until
// rating has happened, so it leads the list rather than reading as "off".
const RATING_LABELS = ['All', '1★+', '2★+', '3★+', '4★+', '5★']

/** Parse the three settings from the hash, returning each clamped to its
 *  range. Anything that is not a usable number takes the default rather than
 *  leaving the screen stopped: a blank screen on a hand-edited URL is the
 *  worst outcome here. */
function parseSettings(hash) {
  const q = hash.split('?')[1] || ''
  const params = new URLSearchParams(q)
  const out = {}
  for (const key of Object.keys(DEFAULTS)) {
    const raw = params.get(key)
    if (raw === null || raw === '') { out[key] = DEFAULTS[key]; continue }
    const n = Number(raw)
    if (!Number.isFinite(n)) { out[key] = DEFAULTS[key]; continue }
    const [lo, hi] = CLAMPS[key]
    out[key] = Math.min(hi, Math.max(lo, Math.floor(n)))
  }
  return out
}

export default function Slideshow() {
  const [hash, setHash] = useState(() => window.location.hash || '#/slideshow')
  const settings = parseSettings(hash)
  const { interval, min_rating, lookahead } = settings

  // The deck is a snapshot of the photographs meeting the threshold, shuffled;
  // the index walks it. They are ONE piece of state because they change
  // together: walking off the end draws a new order and returns to zero, and
  // splitting that across two setters is what forces a `setDeck` call inside a
  // `setIndex` updater — a side effect in a function React is allowed to run
  // more than once, which would shuffle twice per pass the day StrictMode goes
  // on. As one updater it is a pure transition on one value.
  const [play, setPlay] = useState({ deck: [], index: 0 })
  const { deck, index } = play
  const [loadError, setLoadError] = useState('')
  // Mutable, on purpose: filling a buffer is a side effect of advancing, and
  // doing it through state would re-render with a stale buffer for one frame.
  const bufferRef = useRef([])

  // The settings live in the URL, so the screen reads them on every hash
  // change rather than only on mount. Back/forward across the history works.
  useEffect(() => {
    const on = () => setHash(window.location.hash || '#/slideshow')
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  // The set is rebuilt when the threshold changes. The interval does not
  // change the set; it only retunes the timer, handled by the effect below.
  useEffect(() => {
    let cancelled = false
    setLoadError('')
    api.get(`/api/photos?min_rating=${min_rating}`)
      .then((rows) => {
        if (cancelled) return
        setPlay({ deck: shuffle(rows), index: 0 })
        // The old buffer points at rows that no longer exist. Clear it; the
        // buffer-refill effect will fill the new deck.
        bufferRef.current = []
      })
      .catch((e) => {
        if (cancelled) return
        setLoadError(e.message)
        setPlay({ deck: [], index: 0 })
      })
    return () => { cancelled = true }
  }, [min_rating])

  // Pre-decode the next N photographs. `decode()` resolves once the bitmap is
  // ready, which is what removes the visible stall at the swap. A photograph
  // that fails to fetch or decode is dropped from the buffer — the onError
  // handler on the rendered image advances past a missing file, so the deck
  // keeps moving.
  //
  // The window is refilled, never rebuilt. Rebuilding it — clearing the buffer
  // and constructing N fresh Images on every advance — costs N decodes per tick
  // instead of the one photograph actually entering the window, which on a
  // phone is three 7.8 MB bitmaps every interval to save one. Keeping the
  // entries that are still wanted also keeps their decoded bitmaps alive:
  // dropping the last reference to an Image is what lets the decode be
  // collected and paid for again.
  useEffect(() => {
    if (deck.length === 0) { bufferRef.current = []; return }
    const want = []
    for (let k = 0; k < lookahead; k++) {
      const photo = deck[(index + 1 + k) % deck.length]
      if (photo) want.push(photo)
    }
    const wanted = new Set(want.map((p) => p.id))
    // Keep what is already decoded and still ahead of us; the rest has been
    // shown, or fell out of the window when it shrank.
    const buffer = bufferRef.current.filter((e) => wanted.has(e.photo.id))
    const held = new Set(buffer.map((e) => e.photo.id))
    for (const photo of want) {
      if (held.has(photo.id)) continue
      const img = new Image()
      img.src = shotImage(photo.id)
      img.decode().catch(() => {})   // swallowed: a single failure must not stop the slideshow
      buffer.push({ img, photo })
    }
    bufferRef.current = buffer
  }, [deck, index, lookahead])

  // One step of the walk, and the only place the deck advances: the timer and
  // the missing-file handler share it. Walking off the end draws a fresh order
  // — same set, different sequence — so a thirteen-photograph set is not a
  // memorised loop after one pass. The buffer is not touched here; the effect
  // above re-derives the window from the new index.
  const advance = () => setPlay(nextPlay)

  // The timer. Re-declared when the interval changes so a tighter or longer
  // setting takes effect on the next tick, mid-set. It is keyed on the deck's
  // LENGTH, not the deck: a reshuffle at the end of a pass keeps the same
  // length, so the timer is left running rather than being torn down and
  // restarted under the photograph that just appeared.
  useEffect(() => {
    if (deck.length < 2) return
    const id = setInterval(advance, interval * 1000)
    return () => clearInterval(id)
  }, [deck.length, interval])

  // Write one setting back into the hash. The hash IS the state: this changes
  // the address, the hashchange listener above re-parses it, and the effects
  // keyed on `min_rating` and `interval` do the rest — the threshold rebuilds
  // the set, the interval retunes the timer. Nothing here touches the deck
  // directly, so the on-screen controls and a hand-typed address cannot drift
  // apart, and the configuration keeps travelling with a saved address.
  const setSetting = (key, value) => {
    const [path, query] = hash.split('?')
    const params = new URLSearchParams(query || '')
    params.set(key, String(value))
    window.location.hash = `${path}?${params.toString()}`
  }

  // Escape leaves the slideshow, the same way the back link does.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') {
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
        window.location.hash = '#/library'
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (loadError) {
    return <div className="error" style={{ padding: 40 }}>Failed to load photographs: {loadError}</div>
  }

  if (deck.length === 0) {
    // The threshold control belongs here too: without it this screen is a dead
    // end, since the overlay that carries the controls is only drawn over a
    // photograph and there is no photograph to draw it over.
    return (
      <div className="slideshow-empty">
        <p>No photographs meet this threshold.</p>
        <p className="muted">Lower it here, or rate some photographs first.</p>
        <p>
          <select className="slideshow-sel" value={min_rating} title="Minimum rating to include"
                  onChange={(e) => setSetting('min_rating', e.target.value)}>
            {RATING_LABELS.map((label, r) => <option key={r} value={r}>{label}</option>)}
          </select>
        </p>
        <p><a href="#/library">← Back to Library</a></p>
      </div>
    )
  }

  const current = deck[index]
  // An interval that arrived from the address but is not one of the offered
  // choices joins the list, so the control shows what is really playing rather
  // than rendering blank and silently rewriting it on the next change.
  const intervalChoices = INTERVAL_CHOICES.includes(interval)
    ? INTERVAL_CHOICES
    : [...INTERVAL_CHOICES, interval].sort((a, b) => a - b)
  // The onError handler is the spec's "deleting a file mid-play skips rather
  // than stops": the next onError fires, the index advances, the deck keeps
  // walking past the missing file.
  const onImgError = () => advance()

  // Toggle the Fullscreen API on and off. The request needs a user gesture
  // on most browsers, so the button is the only place it is called from: the
  // page is already a fixed full-viewport overlay, and a user who wants the
  // browser chrome gone (the phone's address bar, status bar) has the button
  // for it.
  const toggleFullscreen = (e) => {
    if (e) e.stopPropagation()
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {})
    } else if (document.documentElement.requestFullscreen) {
      document.documentElement.requestFullscreen().catch(() => {})
    }
  }

  return (
    <div className="slideshow">
      <img src={shotImage(current.id)} alt="" onError={onImgError} />
      <div className="slideshow-overlay" onClick={(e) => e.stopPropagation()}>
        <span className="slideshow-name" title={current.session_name}>{current.session_name}</span>
        <select className="slideshow-sel" value={min_rating} title="Minimum rating to include"
                onChange={(e) => setSetting('min_rating', e.target.value)}>
          {RATING_LABELS.map((label, r) => <option key={r} value={r}>{label}</option>)}
        </select>
        <select className="slideshow-sel" value={interval} title="Seconds per photograph"
                onChange={(e) => setSetting('interval', e.target.value)}>
          {intervalChoices.map((s) => <option key={s} value={s}>{s}s</option>)}
        </select>
        <button className="slideshow-btn" onClick={toggleFullscreen} title="Toggle fullscreen (browser chrome)">
          {document.fullscreenElement ? 'Exit fullscreen' : 'Fullscreen'}
        </button>
        <a href="#/library" onClick={(e) => e.stopPropagation()}>← Exit</a>
      </div>
    </div>
  )
}
