import React, { useEffect, useRef } from 'react'

const STEPS = [
  ['character', 'Character'], ['constants', 'Scene / Constants'],
  ['takes', 'Takes'], ['review', 'Review'], ['generation', 'Generation'],
]

export default function SessionNavigation({ id, activeStep, onNavigate, planReady,
  generationDisabled, generationReason, takeCount, shotCount, revision, dirty, notice }) {
  const heading = useRef(null)
  const previousStep = useRef(activeStep)
  const currentStep = useRef(activeStep)
  currentStep.current = activeStep
  const navigate = useRef(onNavigate)
  navigate.current = onNavigate
  useEffect(() => {
    if (previousStep.current !== activeStep) {
      heading.current?.focus()
      if (window.location.hash.split('?')[0] === `#/session/${id}`) {
        const params = new URLSearchParams(window.location.hash.split('?')[1])
        params.set('step', activeStep)
        window.history.replaceState(null, '', `#/session/${id}?${params}`)
      }
    }
    previousStep.current = activeStep
  }, [activeStep, id])
  useEffect(() => {
    if (!planReady) return
    const read = () => {
      const [path, query] = window.location.hash.split('?')
      if (path !== `#/session/${id}`) return
      const requested = new URLSearchParams(query).get('step') || 'character'
      // Deep links use the same guards as in-editor navigation, including Review.
      if (STEPS.some(([step]) => step === requested) && requested !== currentStep.current) {
        if (!navigate.current(requested)) {
          const params = new URLSearchParams(query)
          params.set('step', currentStep.current)
          window.history.replaceState(null, '', `#/session/${id}?${params}`)
        }
      }
    }
    read()
    window.addEventListener('hashchange', read)
    return () => window.removeEventListener('hashchange', read)
  }, [id, planReady])
  const href = (step) => {
    const params = new URLSearchParams(typeof window === 'undefined' ? '' : window.location.hash.split('?')[1])
    params.set('step', step)
    return `#/session/${id}?${params}`
  }
  return <section className="studio-ui session-navigation" aria-label="Resource session navigation">
    <div className="session-stage-heading">
      <h2 ref={heading} tabIndex={-1}>{planReady ? `Current stage: ${STEPS.find(([step]) => step === activeStep)?.[1]}` : 'Session stages unavailable'}</h2>
      {planReady && <a href={href(activeStep)}>Link to current stage</a>}
      <div className="ui-actions">
        {revision !== null && <span className="ui-state">Rev {revision}</span>}
        {dirty && <span className="ui-state" data-tone="warning">Unsaved changes</span>}
      </div>
    </div>
    {planReady && <nav aria-label="Session stages">
      <ol>{STEPS.map(([step, label], index) => {
        const blocked = !planReady || (step === 'generation' && generationDisabled)
        return <li key={step}><button type="button"
          className={'chip' + (activeStep === step ? ' on' : '')}
          aria-current={activeStep === step ? 'step' : undefined}
          disabled={blocked} aria-describedby={blocked ? `session-${id}-stage-blocker` : undefined}
          onClick={() => {
            if (onNavigate(step) && window.location.hash.split('?')[0] === `#/session/${id}`) window.location.hash = href(step)
          }}>
          {`${index + 1}. ${label}${step === 'takes' ? ` (${takeCount})` : step === 'generation' && shotCount ? ` (${shotCount})` : ''}`}
        </button></li>
      })}</ol>
    </nav>}
    {(!planReady || generationDisabled) && <p className="ui-help" id={`session-${id}-stage-blocker`}>
      {!planReady ? 'Load a valid plan draft to navigate stages. Preparation and generation remain blocked.' : `Generation blocked: ${generationReason || 'Complete the current plan review before proceeding.'}`}
    </p>}
    <p className="ui-help">Changing stages does not save, prepare, approve or generate.</p>
    {notice && <p className="ui-help" role="status">{notice}</p>}
  </section>
}
