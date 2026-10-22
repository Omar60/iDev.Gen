import React, { useEffect, useState } from 'react'

export default function ModelPreview({ src, emptyLabel = 'No preview' }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => { setFailed(false) }, [src])
  const available = !!src && !failed
  return <div className={'model-preview' + (available ? '' : ' model-preview--empty')}>
    {available ? <img src={src} alt="" width="96" height="128" loading="lazy" onError={() => setFailed(true)} /> : <>
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
        <path d="M3 3h18v18H3z M3 17l6-6 4 4 3-3 5 5 M15 7h.01" />
      </svg>
      <span>{failed ? 'Preview unavailable' : emptyLabel}</span>
    </>}
  </div>
}
