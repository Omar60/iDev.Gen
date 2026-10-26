import React, { useEffect, useRef, useState } from 'react'
import { shotImage } from '../api.js'

export function SessionPhoto({ shotId, alt, lazy = false, allowRetry = false }) {
  const [state, setState] = useState('loading')
  const [attempt, setAttempt] = useState(0)
  return <div className="session-photo" aria-busy={state === 'loading'}>
    {state !== 'failed' && <img key={attempt} src={shotImage(shotId)} alt={alt}
      width="768" height="1024" loading={lazy ? 'lazy' : 'eager'} decoding="async"
      onLoad={() => setState('ready')} onError={() => setState('failed')} />}
    {state === 'loading' && <span className="photo-loading">Loading image…</span>}
    {state === 'failed' && <div className="photo-unavailable" role="status">
      <span>Image unavailable · shot #{shotId}</span>
      {allowRetry && <button type="button" onClick={() => { setState('loading'); setAttempt((value) => value + 1) }}>Reload image</button>}
    </div>}
  </div>
}

export default function SessionPhotoViewer({ photos, selectedId, onSelect, onClose, comparisonFor, split, onSplit, fallbackFocus }) {
  const dialogRef = useRef(null)
  const index = photos.findIndex((photo) => photo.id === selectedId)
  const photo = photos[index]
  const available = Boolean(photo)
  useEffect(() => {
    if (!available) return
    const trigger = document.activeElement
    const overflow = document.body.style.overflow
    const dialog = dialogRef.current
    dialog.showModal()
    document.body.style.overflow = 'hidden'
    return () => {
      dialog.close()
      document.body.style.overflow = overflow
      if (trigger?.isConnected) trigger.focus()
      else fallbackFocus.current?.focus()
    }
  }, [available, fallbackFocus])
  useEffect(() => { if (!available) onClose() }, [available, onClose])
  if (!photo) return null
  const comparison = comparisonFor(photo)
  const label = photo.shot_label || `Shot #${photo.id}`
  const move = (direction) => {
    const next = photos[index + direction]
    if (next) onSelect(next)
  }
  return <dialog ref={dialogRef} className="studio-ui session-photo-viewer" aria-labelledby="session-photo-title"
    onCancel={(event) => { event.preventDefault(); onClose() }}
    onClose={onClose}
    onClick={(event) => { if (event.target === event.currentTarget) onClose() }}
    onKeyDown={(event) => {
      if (event.key === 'Tab') {
        const controls = [...event.currentTarget.querySelectorAll('button:not(:disabled), input:not(:disabled), summary')]
        const first = controls[0], last = controls[controls.length - 1]
        if (event.shiftKey && event.target === first) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && event.target === last) { event.preventDefault(); first?.focus() }
        return
      }
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName) || event.target.isContentEditable) return
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault()
        move(event.key === 'ArrowLeft' ? -1 : 1)
      }
    }}>
    <div className="photo-viewer-content">
      <header className="photo-viewer-header">
        <div><h2 id="session-photo-title" className="ui-section-title">{label}</h2>
          <p className="ui-help" aria-live="polite">Photo {index + 1} of {photos.length} · shot #{photo.id}</p></div>
        <button className="ui-button" type="button" onClick={onClose} aria-label="Close photo viewer">Close</button>
      </header>
      <div className="photo-viewer-media" style={{ '--split': `${split}%` }}>
        <SessionPhoto key={photo.id} shotId={photo.id} alt={label} allowRetry />
        {comparison && <div className="photo-viewer-before">
          <SessionPhoto key={comparison.id} shotId={comparison.id} alt={comparison.label} />
        </div>}
        {comparison && <span className="photo-viewer-handle" aria-hidden="true" />}
      </div>
      {comparison && <div className="photo-viewer-comparison">
        <label htmlFor="session-photo-split">Comparison split</label>
        <input id="session-photo-split" type="range" min="0" max="100" value={split}
          onChange={(event) => onSplit(Number(event.target.value))} />
        <p className="ui-help">{comparison.label}</p>
        {comparison.warning && <p className="ui-notice">{comparison.warning}</p>}
      </div>}
      <nav className="photo-viewer-navigation" aria-label="Photo navigation">
        <button className="ui-button" type="button" disabled={index === 0} onClick={() => move(-1)}>Previous photo</button>
        <span className="ui-help">← / → to navigate · Escape to close</span>
        <button className="ui-button" type="button" disabled={index === photos.length - 1} onClick={() => move(1)}>Next photo</button>
      </nav>
      <details className="photo-viewer-details" open>
        <summary>Prompt and file details</summary>
        <p>{photo.prompt || 'No prompt recorded.'}</p>
        <dl><dt>Seed</dt><dd>{photo.seed ?? 'Not recorded'}</dd>
          <dt>File</dt><dd>{photo.filename || 'Not recorded'}</dd></dl>
      </details>
    </div>
  </dialog>
}
