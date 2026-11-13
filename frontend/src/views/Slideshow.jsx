import React, { useEffect, useRef, useState } from 'react'
import { api, shotImage } from '../api'
import { shuffle, nextPlay } from '../deck'
import '../ui/foundations.css'
import './Slideshow.css'

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
  const [loading, setLoading] = useState(true)
  const [reload, setReload] = useState(0)
  const [failedIds, setFailedIds] = useState(new Set())
  const [imageState, setImageState] = useState({ id: null, ready: false })
  const [fullscreen, setFullscreen] = useState(Boolean(document.fullscreenElement))
  const [fullscreenError, setFullscreenError] = useState('')
  const dialogRef = useRef(null)
  const headingRef = useRef(null)
  const mounted = useRef(false)
  const ownsFullscreen = useRef(false)
  const fullscreenEscapePending = useRef(false)
  const fullscreenEscapeFailed = useRef(false)
  const currentId = useRef(null)
  currentId.current = deck[index]?.id

  // Mutable, on purpose: filling a buffer is a side effect of advancing, and
  // doing it through state would re-render with a stale buffer for one frame.
  const bufferRef = useRef([])

  useEffect(() => {
    mounted.current = true
    const dialog = dialogRef.current
    const overflow = document.body.style.overflow
    dialog.showModal()
    document.body.style.overflow = 'hidden'
    headingRef.current?.focus()
    const onFullscreen = () => {
      if (!document.fullscreenElement) fullscreenEscapeFailed.current = false
      setFullscreen(Boolean(document.fullscreenElement))
    }
    document.addEventListener('fullscreenchange', onFullscreen)
    return () => {
      mounted.current = false
      document.removeEventListener('fullscreenchange', onFullscreen)
      bufferRef.current = []
      document.body.style.overflow = overflow
      dialog.close()
      if (ownsFullscreen.current && document.fullscreenElement) document.exitFullscreen().catch(() => {})
    }
  }, [])

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
    setLoading(true)
    api.get(`/api/photos?min_rating=${min_rating}`)
      .then((rows) => {
        if (cancelled) return
        setFailedIds(new Set())
        setImageState({ id: null, ready: false })
        setPlay({ deck: shuffle(rows), index: 0 })
        // The old buffer points at rows that no longer exist. Clear it; the
        // buffer-refill effect will fill the new deck.
        bufferRef.current = []
      })
      .catch((e) => {
        if (cancelled) return
        setLoadError(e.message || 'The photograph list could not be loaded.')
        setPlay({ deck: [], index: 0 })
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [min_rating, reload])

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
      held.add(photo.id)
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

  const leave = () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
    window.location.hash = '#/library'
  }

  const retryPhotos = (event) => {
    if (event.detail === 0 && document.activeElement === event.currentTarget) headingRef.current?.focus()
    setReload((value) => value + 1)
  }

  const current = deck[index]
  const name = current?.session_name || (current ? `Session #${current.session_id}` : '')
  const allFailed = deck.length > 0 && failedIds.size === deck.length
  const imageLoading = current && (imageState.id !== current.id || !imageState.ready)
  const intervalChoices = INTERVAL_CHOICES.includes(interval)
    ? INTERVAL_CHOICES
    : [...INTERVAL_CHOICES, interval].sort((a, b) => a - b)

  const onImgError = (id) => {
    if (!mounted.current || currentId.current !== id) return
    setFailedIds((previous) => new Set(previous).add(id))
    advance()
  }
  const toggleFullscreen = async () => {
    setFullscreenError('')
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
      else if (document.documentElement.requestFullscreen) {
        await document.documentElement.requestFullscreen()
        if (!mounted.current) { document.exitFullscreen().catch(() => {}); return }
        ownsFullscreen.current = true
      }
    } catch (error) {
      if (mounted.current) setFullscreenError(`Fullscreen could not be changed. Try again or use the window view. ${error.message || ''}`.trim())
    }
  }

  const trapFocus = (event) => {
    if (event.key !== 'Tab') return
    const controls = [...event.currentTarget.querySelectorAll('a[href], button:not(:disabled), select:not(:disabled)')]
    const first = controls[0]
    const last = controls.at(-1)
    if (event.shiftKey && (event.target === first || !controls.includes(event.target))) {
      event.preventDefault(); last?.focus()
    } else if (!event.shiftKey && event.target === last) {
      event.preventDefault(); first?.focus()
    }
  }

  // The native dialog cancel event runs before fullscreenchange on Escape.
  const onDialogKeyDown = (event) => {
    fullscreenEscapePending.current = event.key === 'Escape' && Boolean(document.fullscreenElement) && !fullscreenEscapeFailed.current
    trapFocus(event)
  }
  const onDialogKeyUp = (event) => {
    if (event.key === 'Escape') fullscreenEscapePending.current = false
  }
  const onDialogCancel = (event) => {
    event.preventDefault()
    if (fullscreenEscapePending.current) {
      fullscreenEscapePending.current = false
      document.exitFullscreen().catch((error) => {
        fullscreenEscapeFailed.current = true
        if (mounted.current) setFullscreenError(`Fullscreen could not be changed. Try again or use the window view. ${error.message || ''}`.trim())
      })
      return
    }
    leave()
  }

  return <dialog ref={dialogRef} className="studio-ui slideshow-view" aria-labelledby="ss-title" aria-modal="true"
    onKeyDown={onDialogKeyDown} onKeyUp={onDialogKeyUp} onCancel={onDialogCancel}>
    <header className="slideshow-header">
      <div><h1 id="ss-title" ref={headingRef} tabIndex={-1}>Slideshow</h1>
        <p className="ui-help">{current && !allFailed ? `Photograph ${index + 1} of ${deck.length}` : 'Photographs across sessions'}</p></div>
      <span className="ui-state">Read-only</span>
      <a className="ui-button" href="#/library">Exit to Library</a>
    </header>

    <div className="slideshow-stage" aria-busy={Boolean(loading || (!allFailed && imageLoading))}>
      {loadError ? <section className="slideshow-message ui-surface" aria-labelledby="ss-error-title">
        <h2 id="ss-error-title" className="ui-section-title">Photographs could not be loaded</h2>
        <p className="ui-notice" data-tone="error" role="alert">{loadError}</p>
        <p className="ui-section-description">Your interval and minimum rating are kept in the address. Retry the list or adjust the rating below.</p>
        <button className="ui-button ui-button--primary" onClick={retryPhotos}>Retry photographs</button>
      </section> : allFailed ? <section className="slideshow-message ui-surface" aria-labelledby="ss-unavailable-title">
        <h2 id="ss-unavailable-title" className="ui-section-title">No photograph could be displayed</h2>
        <p className="ui-section-description" role="status">The images in this set could not be loaded. Retry the list or change the minimum rating.</p>
        <button className="ui-button" onClick={retryPhotos}>Retry photographs</button>
      </section> : current ? <>
        <img key={current.id} className="slideshow-photo" src={shotImage(current.id)}
          alt={`Photograph from ${name}`} decoding="async" onError={() => onImgError(current.id)}
          onLoad={() => { if (mounted.current && currentId.current === current.id) setImageState({ id: current.id, ready: true }) }} />
        {imageLoading && <p className="slideshow-image-loading" role="status">Loading photograph…</p>}
      </> : loading ? <div className="slideshow-message" role="status"><h2 className="ui-section-title">Loading photographs…</h2><p className="ui-help">Reading the photographs that meet the minimum rating.</p></div>
        : <section className="slideshow-message ui-surface" aria-labelledby="ss-empty-title"><h2 id="ss-empty-title" className="ui-section-title">No photographs meet this threshold</h2>
          <p className="ui-section-description">Lower the minimum rating below, or return to Library to review your photographs.</p>
          <button className="ui-button" onClick={retryPhotos}>Reload photographs</button></section>}
    </div>

    <footer className="slideshow-controls">
      <div className="slideshow-context"><p className="slideshow-session">{name || 'No photograph selected'}</p>
        <p className="ui-help">{loading && current ? 'Updating the photograph set…' : current && !allFailed ? deck.length === 1 ? 'Single photograph; no automatic advance.' : `Advances automatically every ${interval} seconds.` : 'Adjust the minimum rating to choose the set.'}</p>
        {current && failedIds.size > 0 && !allFailed && <p className="ui-help" role="status">Unavailable photographs encountered: {failedIds.size}. Playback continues.</p>}
      </div>
      <div className="ui-field"><label className="ui-label" htmlFor="ss-rating">Minimum rating</label>
        <select id="ss-rating" name="min_rating" className="ui-control" value={min_rating} onChange={(event) => setSetting('min_rating', event.target.value)}>
          {RATING_LABELS.map((label, rating) => <option key={rating} value={rating}>{label}</option>)}
        </select></div>
      <div className="ui-field"><label className="ui-label" htmlFor="ss-interval">Seconds per photograph</label>
        <select id="ss-interval" name="interval" className="ui-control" value={interval} onChange={(event) => setSetting('interval', event.target.value)}>
          {intervalChoices.map((seconds) => <option key={seconds} value={seconds}>{seconds}s</option>)}
        </select></div>
      <div className="slideshow-fullscreen"><button className="ui-button" onClick={toggleFullscreen}
        disabled={!fullscreen && !document.documentElement.requestFullscreen}>{fullscreen ? 'Exit fullscreen' : 'Fullscreen'}</button>
        {!document.documentElement.requestFullscreen && <p className="ui-help">Fullscreen is unavailable in this browser.</p>}</div>
      {fullscreenError && <p className="ui-notice slideshow-fullscreen-error" data-tone="error" role="alert">{fullscreenError}</p>}
    </footer>
  </dialog>
}
