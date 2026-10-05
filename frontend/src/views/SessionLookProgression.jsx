import React, { useEffect, useRef, useState } from 'react'
import { api, versionText } from '../api.js'
import { deriveSavedLookWardrobeProgression } from '../wardrobe.js'
import { resolveEffectiveWardrobes } from '../sessionPlan.js'

const digestPattern = /^[0-9a-f]{64}$/
const resourcePlanningDisabledMessage = 'Resource planning is disabled by configuration'
const progressionPreviewUnavailableMessage = 'Wardrobe progression preview is temporarily unavailable.'

const errorMessage = (error) => (
  error?.detail?.message || error?.message || 'The request could not be completed.'
)

const remediesFor = (error) => {
  if (error?.status === 409) {
    return 'No changes were applied. Reload the current plan and resolve any stale revision, wardrobe conflict, or generated-history freeze before trying again.'
  }
  if (error?.status === 422) {
    return 'No changes were applied. Check the selected decisions and stages, then request a fresh preview.'
  }
  if (error?.status === 503) {
    if (error?.detail === resourcePlanningDisabledMessage) {
      return 'No changes were applied. Resource planning is disabled; retry after it is enabled.'
    }
    if (error?.detail === progressionPreviewUnavailableMessage) {
      return 'No changes were applied. Wait for preview verification to recover, then request and review a fresh preview before applying again.'
    }
    return 'No changes were applied. Wait for the preview service to recover, then request a fresh preview.'
  }
  return 'Review the current plan and try again.'
}

const isKnownNoWrite503 = (kind, error) => (
  error?.status === 503
  && (
    error.detail === resourcePlanningDisabledMessage
    || (kind === 'progression' && error.detail === progressionPreviewUnavailableMessage)
  )
)

const snapshotStages = (outfit) => {
  if (!outfit || !Array.isArray(outfit.garments) || outfit.garments.length === 0) {
    throw new Error('The saved look snapshot has no ordered garments.')
  }
  const lastGarment = outfit.garments[outfit.garments.length - 1]
  const count = outfit.garments.length + 1 + (lastGarment?.aside ? 1 : 0)
  return Array.from({ length: count }, (_, index) => ({
    index,
    wardrobe: deriveSavedLookWardrobeProgression(outfit, '', 1, {
      stageIndices: [index],
    })[0],
  }))
}

const isLookDetail = (look, key, version) => {
  if (
    !look || typeof look !== 'object' || Array.isArray(look)
    || look.key !== key || versionText(look.version) !== version
    || typeof look.name !== 'string' || typeof look.appearance !== 'string'
    || !digestPattern.test(look.content_digest || '')
  ) return false
  if (look.outfit === null) return true
  const outfit = look.outfit
  if (
    !outfit || typeof outfit !== 'object' || Array.isArray(outfit)
    || typeof outfit.outfit_key !== 'string' || !outfit.outfit_key
    || !Array.isArray(outfit.garments) || outfit.garments.length === 0
  ) return false
  const keys = new Set()
  return outfit.garments.every((garment) => {
    if (
      !garment || typeof garment !== 'object' || Array.isArray(garment)
      || typeof garment.key !== 'string' || !garment.key || keys.has(garment.key)
      || typeof garment.wording !== 'string' || !garment.wording.trim()
      || typeof garment.aside !== 'string'
    ) return false
    keys.add(garment.key)
    return true
  })
}

const isProgressionPreview = (preview, revision, eventPolicy, takes) => Boolean(
  preview && typeof preview === 'object' && !Array.isArray(preview)
  && preview.expected_revision === revision
  && preview.event_policy === eventPolicy
  && typeof preview.initial_wardrobe === 'string'
  && Array.isArray(preview.wardrobe_changes)
  && Array.isArray(preview.reviewed_wardrobes)
  && preview.reviewed_wardrobes.length === takes.length
  && preview.reviewed_wardrobes.every((item, index) => (
    item && item.take_id === takes[index]?.take_id
    && typeof item.wardrobe === 'string'
  ))
  && digestPattern.test(preview.review_digest || '')
  && typeof preview.preview_token === 'string' && preview.preview_token.length > 0
  && preview.preview_token.length <= 65536
  && preview.wardrobe_progression && typeof preview.wardrobe_progression === 'object',
)

const sameWardrobes = (plan, reviewed) => {
  try {
    const effective = resolveEffectiveWardrobes(plan)
    return reviewed.every((item) => effective[item.take_id] === item.wardrobe)
  } catch {
    return false
  }
}

const wasLookApplied = (loaded, request) => {
  const current = loaded?.plan
  const snapshot = current?.authoring?.look_snapshot
  if (
    !current || !snapshot
    || snapshot.look_id !== request.look.key
    || versionText(snapshot.version) !== versionText(request.look.version)
    || snapshot.content_digest !== request.look.content_digest
  ) return false

  const decisions = request.decisions
  const before = request.planBefore
  const expectedLook = decisions.look === 'replace' ? request.look.appearance : before.look
  const expectedLookOrigin = decisions.look === 'replace' ? 'saved_look' : 'user'
  if (
    current.look !== expectedLook
    || current.authoring.shared_state?.look?.origin !== expectedLookOrigin
  ) return false

  if (decisions.initial_wardrobe === 'replace') {
    let expectedWardrobe
    try {
      expectedWardrobe = deriveSavedLookWardrobeProgression(
        request.look.outfit, before.initial_wardrobe, 1, { stageIndices: [0] },
      )[0]
    } catch { return false }
    return current.initial_wardrobe === expectedWardrobe
      && current.authoring.shared_state?.initial_wardrobe?.origin === 'saved_look'
  }
  if (decisions.initial_wardrobe === 'keep') {
    return current.initial_wardrobe === before.initial_wardrobe
      && current.authoring.shared_state?.initial_wardrobe?.origin === 'user'
  }
  return current.initial_wardrobe === before.initial_wardrobe
    && current.authoring.shared_state?.initial_wardrobe?.origin
      === before.authoring?.shared_state?.initial_wardrobe?.origin
}

const wasProgressionApplied = (loaded, request) => {
  const current = loaded?.plan
  const actual = current?.authoring?.wardrobe_progression
  const expected = request.preview.wardrobe_progression
  return Boolean(
    current && actual && expected
    && actual.source_look_digest === expected.source_look_digest
    && actual.start_take_id === expected.start_take_id
    && actual.end_take_id === expected.end_take_id
    && JSON.stringify(actual.stage_indices) === JSON.stringify(expected.stage_indices)
    && actual.applied_revision === loaded.planRevision
    && sameWardrobes(current, request.preview.reviewed_wardrobes),
  )
}

export default function SessionLookProgression({
  sessionId,
  plan,
  revision,
  disabled = false,
  onBusyChange = () => {},
  onReload = null,
}) {
  const [looks, setLooks] = useState([])
  const [looksLoading, setLooksLoading] = useState(false)
  const [looksError, setLooksError] = useState('')
  const [selectedKey, setSelectedKey] = useState('')
  const [selectedVersion, setSelectedVersion] = useState('')
  const [lookDetail, setLookDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [decisions, setDecisions] = useState({ look: '', initial_wardrobe: '' })
  const [removalOrderConfirmed, setRemovalOrderConfirmed] = useState(false)
  const [finalStage, setFinalStage] = useState('')
  const [intermediateStages, setIntermediateStages] = useState([])
  const [startTakeId, setStartTakeId] = useState('')
  const [endTakeId, setEndTakeId] = useState('')
  const [eventPolicy, setEventPolicy] = useState('')
  const [preview, setPreview] = useState(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [mutationBusy, setMutationBusy] = useState(false)
  const [checkingReadback, setCheckingReadback] = useState(false)
  const [unknownOutcome, setUnknownOutcome] = useState(null)
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState('')

  const sessionIdRef = useRef(sessionId)
  sessionIdRef.current = sessionId
  const mountedRef = useRef(false)
  const looksRequestRef = useRef(0)
  const detailRequestRef = useRef(0)
  const previewRequestRef = useRef(0)
  const mutationRequestRef = useRef(0)
  const mutationBusyRef = useRef(false)
  const unknownOutcomeRef = useRef(null)
  const previewSignatureRef = useRef('')

  const snapshot = plan?.authoring?.look_snapshot || null
  const snapshotDigest = snapshot?.content_digest || ''
  const takes = Array.isArray(plan?.takes) ? plan.takes : []
  const takeIds = takes.map((take) => take?.take_id)
  const resolvedStartTakeId = takeIds.includes(startTakeId) ? startTakeId : (takeIds[0] || '')
  const resolvedEndTakeId = takeIds.includes(endTakeId) ? endTakeId : (takeIds[takeIds.length - 1] || '')
  const startIndex = takeIds.indexOf(resolvedStartTakeId)
  const endIndex = takeIds.indexOf(resolvedEndTakeId)
  const finalStageIndex = finalStage === '' ? null : Number(finalStage)
  const stageIndices = finalStageIndex === null
    ? []
    : [...new Set([
        ...intermediateStages.filter((index) => index < finalStageIndex),
        finalStageIndex,
      ])].sort((a, b) => a - b)

  let stages = []
  let snapshotError = ''
  if (snapshot?.outfit) {
    try {
      stages = snapshotStages(snapshot.outfit)
    } catch (error) {
      snapshotError = error.message || 'The saved look snapshot is invalid.'
    }
  }

  const inputSignature = JSON.stringify({
    sessionId: String(sessionId),
    revision,
    snapshotDigest,
    outfit: snapshot?.outfit || null,
    initialWardrobe: plan?.initial_wardrobe,
    takeIds,
    wardrobeChanges: plan?.wardrobe_changes || [],
    removalOrderConfirmed,
    finalStageIndex,
    intermediateStages: [...intermediateStages].sort((a, b) => a - b),
    startTakeId: resolvedStartTakeId,
    endTakeId: resolvedEndTakeId,
    eventPolicy,
    disabled,
  })
  previewSignatureRef.current = inputSignature
  const hasOutfit = Boolean(snapshot?.outfit)
  const wardrobeOrigin = plan?.authoring?.shared_state?.initial_wardrobe?.origin
  const needsWardrobeDecision = Boolean(lookDetail?.outfit) || wardrobeOrigin === 'saved_look'
  const selectedVersionIdentity = versionText(selectedVersion)
  const takeCount = takes.length

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      looksRequestRef.current += 1
      detailRequestRef.current += 1
      previewRequestRef.current += 1
      mutationRequestRef.current += 1
    }
  }, [])

  useEffect(() => {
    previewRequestRef.current += 1
    setPreview(null)
    setPreviewBusy(false)
    setRemovalOrderConfirmed(false)
    setFinalStage('')
    setIntermediateStages([])
    setStartTakeId('')
    setEndTakeId('')
    setEventPolicy('')
  }, [sessionId, snapshotDigest])

  useEffect(() => {
    if (preview && preview.signature !== inputSignature) {
      previewRequestRef.current += 1
      setPreview(null)
      setPreviewBusy(false)
    }
  }, [inputSignature, preview])

  const isCurrent = (requestSessionId, requestId, requestRef) => (
    mountedRef.current
    && String(sessionIdRef.current) === String(requestSessionId)
    && requestRef.current === requestId
  )

  const invalidatePreview = () => {
    previewRequestRef.current += 1
    setPreview(null)
    setPreviewBusy(false)
    setActionError('')
    setNotice('')
  }

  const loadLooks = async (force = false) => {
    if (disabled || looksLoading || (!force && looks.length)) return
    const requestId = ++looksRequestRef.current
    const requestSessionId = sessionId
    setLooksLoading(true)
    setLooksError('')
    try {
      const result = await api.getVersioned('/api/looks')
      if (!isCurrent(requestSessionId, requestId, looksRequestRef)) return
      if (!Array.isArray(result) || result.some((item) => (
        !item || typeof item.key !== 'string' || !item.key
        || typeof item.name !== 'string' || !versionText(item.version)
      ))) throw new Error('The saved-look list is invalid. Reload it from Looks.')
      setLooks(result)
    } catch (error) {
      if (isCurrent(requestSessionId, requestId, looksRequestRef)) {
        setLooksError(`${errorMessage(error)} Retry loading saved looks from this panel.`)
      }
    } finally {
      if (isCurrent(requestSessionId, requestId, looksRequestRef)) setLooksLoading(false)
    }
  }

  const loadLookVersion = async (key = selectedKey, version = selectedVersionIdentity) => {
    const exactVersion = versionText(version)
    if (disabled || !key || !exactVersion) return
    const requestId = ++detailRequestRef.current
    const requestSessionId = sessionId
    setDetailLoading(true)
    setDetailError('')
    setLookDetail(null)
    setDecisions({ look: '', initial_wardrobe: '' })
    try {
      const result = await api.getVersioned(
        `/api/looks/${encodeURIComponent(key)}/versions/${exactVersion}`,
      )
      if (!isCurrent(requestSessionId, requestId, detailRequestRef)) return
      if (!isLookDetail(result, key, version)) {
        throw new Error('The immutable look version is incomplete or does not match this selection.')
      }
      setLookDetail(result)
      invalidatePreview()
    } catch (error) {
      if (isCurrent(requestSessionId, requestId, detailRequestRef)) {
        setDetailError(`${errorMessage(error)} Reload this exact immutable version before applying it.`)
      }
    } finally {
      if (isCurrent(requestSessionId, requestId, detailRequestRef)) setDetailLoading(false)
    }
  }

  const planStagesError = (() => {
    if (snapshotError) return `The saved look snapshot cannot be used: ${snapshotError}`
    if (!hasOutfit) return 'Apply a saved look with ordered garments first. An appearance-only look leaves clothing unchanged.'
    if (!removalOrderConfirmed) return 'Confirm the saved garment removal order before reviewing a progression.'
    if (finalStageIndex === null || !stages.some((stage) => stage.index === finalStageIndex)) {
      return 'Choose the final clothing stage for this progression.'
    }
    if (takeCount === 0) return 'Add a take before planning clothing changes.'
    if (startIndex < 0 || endIndex < startIndex) return 'Choose an end take that follows the start take in the current plan order.'
    if (!eventPolicy) return 'Choose whether saved wardrobe events should be merged or replaced.'
    try {
      deriveSavedLookWardrobeProgression(snapshot.outfit, plan.initial_wardrobe || '', takeCount, {
        stageIndices,
        intervalStart: startIndex,
        intervalEnd: endIndex,
      })
    } catch (error) {
      const detail = error?.message || 'The selected stages do not fit this interval.'
      if (/interval has \d+ takes for \d+ selected stages/.test(detail)) {
        return `${detail}. Widen the take interval or choose fewer stages.`
      }
      return `${detail}. Adjust the selected stages or take interval and preview again.`
    }
    return ''
  })()

  const resolveReadback = async (request, minimumRevision) => {
    if (typeof onReload !== 'function') return false
    const requestSessionId = request.sessionId
    const requestId = request.requestId
    setCheckingReadback(true)
    try {
      const loaded = await onReload(minimumRevision)
      if (!isCurrent(requestSessionId, requestId, mutationRequestRef)) return false
      if (!loaded || loaded === false || !Number.isInteger(loaded.planRevision)) {
        setActionError('The saved-plan readback is still unavailable. Retry readback; the write will not be sent again.')
        return false
      }
      if (loaded.planRevision < minimumRevision) {
        setActionError(`The saved plan is still below revision ${minimumRevision}. Retry readback; the write will not be sent again.`)
        return false
      }
      const applied = request.kind === 'look'
        ? wasLookApplied(loaded, request)
        : wasProgressionApplied(loaded, request)
      setUnknownOutcome(null)
      unknownOutcomeRef.current = null
      mutationBusyRef.current = false
      onBusyChange(false)
      if (applied) {
        setActionError('')
        setNotice(request.kind === 'look'
          ? 'The saved look is present in the authoritative session plan.'
          : 'The reviewed clothing progression is present in the authoritative session plan.')
      } else {
        setActionError(`The plan was reloaded at revision ${loaded.planRevision}, but it does not contain this requested change. Review the current plan before starting a new action.`)
      }
      setPreview(null)
      return true
    } catch (error) {
      if (isCurrent(requestSessionId, requestId, mutationRequestRef)) {
        setActionError(`${errorMessage(error)} Retry saved-plan readback; do not resubmit the write.`)
      }
      return false
    } finally {
      if (isCurrent(requestSessionId, requestId, mutationRequestRef)) setCheckingReadback(false)
    }
  }

  const applyMutation = async (kind, requestBody, requestDetails) => {
    if (
      disabled || unknownOutcomeRef.current || mutationBusyRef.current
      || !Number.isInteger(revision) || revision < 1 || typeof onReload !== 'function'
    ) return
    const requestId = ++mutationRequestRef.current
    const requestSessionId = sessionId
    const expectedRevision = revision
    const pending = {
      kind,
      requestId,
      sessionId: requestSessionId,
      expectedRevision,
      minimumRevision: expectedRevision + 1,
      ...requestDetails,
    }
    mutationBusyRef.current = true
    setMutationBusy(true)
    setActionError('')
    setNotice('')
    onBusyChange(true)
    try {
      const path = kind === 'look'
        ? `/api/sessions/${requestSessionId}/plan/apply-look`
        : `/api/sessions/${requestSessionId}/plan/wardrobe-progression/apply`
      const result = kind === 'look'
        ? await api.postVersioned(path, requestBody, ['version'])
        : await api.post(path, requestBody)
      if (!isCurrent(requestSessionId, requestId, mutationRequestRef)) return
      const minimumRevision = Number.isInteger(result?.plan_revision)
        && result.plan_revision > expectedRevision
        ? result.plan_revision
        : expectedRevision + 1
      pending.minimumRevision = minimumRevision
      setUnknownOutcome(pending)
      unknownOutcomeRef.current = pending
      await resolveReadback(pending, minimumRevision)
    } catch (error) {
      if (!isCurrent(requestSessionId, requestId, mutationRequestRef)) return
      if (isKnownNoWrite503(kind, error)) {
        setActionError(`${errorMessage(error)} ${remediesFor(error)}`)
        unknownOutcomeRef.current = null
        setUnknownOutcome(null)
        mutationBusyRef.current = false
        if (error.detail === progressionPreviewUnavailableMessage) setPreview(null)
        onBusyChange(false)
        return
      }
      if (Number.isInteger(error?.status) && error.status >= 400 && error.status < 500) {
        setActionError(`${errorMessage(error)} ${remediesFor(error)}`)
        mutationBusyRef.current = false
        onBusyChange(false)
        if (error.status === 409) {
          try { await onReload(0) } catch { /* The visible conflict remains actionable. */ }
        }
        return
      }
      setUnknownOutcome(pending)
      unknownOutcomeRef.current = pending
      setActionError('The write result is unknown. The request will not be repeated. Check the authoritative plan below.')
      await resolveReadback(pending, pending.minimumRevision)
    } finally {
      if (isCurrent(requestSessionId, requestId, mutationRequestRef)) {
        mutationBusyRef.current = false
        setMutationBusy(false)
      }
    }
  }

  const handleApplyLook = () => {
    if (!lookDetail || !Number.isInteger(revision) || revision < 1) return
    const required = needsWardrobeDecision ? ['look', 'initial_wardrobe'] : ['look']
    if (required.some((field) => !['replace', 'keep'].includes(decisions[field]))) return
    if (lookDetail.outfit === null && decisions.initial_wardrobe === 'replace') return
    void applyMutation('look', {
      expected_revision: revision,
      look_key: lookDetail.key,
      version: versionText(lookDetail.version),
      decisions: Object.fromEntries(required.map((field) => [field, decisions[field]])),
    }, {
      look: lookDetail,
      decisions: Object.fromEntries(required.map((field) => [field, decisions[field]])),
      planBefore: plan,
    })
  }

  const handlePreview = async () => {
    if (disabled || previewBusy || planStagesError || !Number.isInteger(revision) || revision < 1) return
    const requestId = ++previewRequestRef.current
    const requestSessionId = sessionId
    const signature = previewSignatureRef.current
    setPreviewBusy(true)
    setActionError('')
    setNotice('')
    try {
      const result = await api.post(
        `/api/sessions/${requestSessionId}/plan/wardrobe-progression/preview`,
        {
          expected_revision: revision,
          start_take_id: resolvedStartTakeId,
          end_take_id: resolvedEndTakeId,
          stage_indices: stageIndices,
          event_policy: eventPolicy,
        },
      )
      if (!isCurrent(requestSessionId, requestId, previewRequestRef)) return
      if (previewSignatureRef.current !== signature) return
      if (!isProgressionPreview(result, revision, eventPolicy, takes)) {
        throw new Error('The server returned an incomplete progression review. Request a fresh preview.')
      }
      setPreview({ ...result, signature })
    } catch (error) {
      if (!isCurrent(requestSessionId, requestId, previewRequestRef)) return
      setPreview(null)
      const remedy = error?.status === 503
        ? remediesFor(error)
        : 'Nothing was applied. Adjust the take interval, stage selection, or event policy and request a new preview.'
      setActionError(`${errorMessage(error)} ${remedy}`)
    } finally {
      if (isCurrent(requestSessionId, requestId, previewRequestRef)) setPreviewBusy(false)
    }
  }

  const handleApplyProgression = () => {
    if (!preview || preview.signature !== previewSignatureRef.current || disabled) return
    const reviewed = {
      expected_revision: revision,
      preview_token: preview.preview_token,
      review_digest: preview.review_digest,
      reviewed_wardrobes: preview.reviewed_wardrobes,
    }
    void applyMutation('progression', reviewed, { preview })
  }

  const handleReadbackRetry = () => {
    const request = unknownOutcomeRef.current
    if (!request || checkingReadback) return
    void resolveReadback(request, request.minimumRevision)
  }

  const currentEvents = Array.isArray(plan?.wardrobe_changes) ? plan.wardrobe_changes : []
  const currentEventByTake = new Map(currentEvents.map((change) => [change?.take_id, change]))
  const reviewedEventRows = (preview?.wardrobe_changes || []).map((change) => {
    const prior = currentEventByTake.get(change.take_id)
    const retained = prior && prior.scope === change.scope && prior.wardrobe === change.wardrobe
    return { ...change, retained: Boolean(retained) }
  })
  const removedFromHereEvents = eventPolicy === 'replace'
    ? currentEvents.filter((change) => (
        change?.scope === 'from_here'
        && !reviewedEventRows.some((row) => row.take_id === change.take_id)
      ))
    : []

  return (
    <section aria-label="Optional saved look and clothing progression" style={{ borderTop: '1px solid var(--line)', marginTop: 10, paddingTop: 10 }}>
      <details onToggle={(event) => { if (event.currentTarget.open) void loadLooks() }}>
        <summary style={{ cursor: 'pointer', fontWeight: 600 }}>Choose a saved look (optional)</summary>
        <div style={{ display: 'grid', gap: 8, paddingTop: 10 }}>
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>
            A saved look adds fixed appearance and an immutable outfit snapshot. It does not choose a character, scene, place, or light. Clothing stays unchanged unless you choose Replace.
          </p>
          {looksLoading && <p className="muted" role="status">Loading saved looks…</p>}
          {looksError && <p className="error" role="alert">{looksError}</p>}
          <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}>
            <label>
              Saved look
              <select
                aria-label="Saved look"
                value={selectedKey}
                disabled={disabled || looksLoading}
                onChange={(event) => {
                  const key = event.target.value
                  const summary = looks.find((item) => item.key === key)
                  detailRequestRef.current += 1
                  setSelectedKey(key)
                  setSelectedVersion(summary ? (versionText(summary.version) || '') : '')
                  setLookDetail(null)
                  setDetailLoading(false)
                  setDetailError('')
                  setDecisions({ look: '', initial_wardrobe: '' })
                  invalidatePreview()
                  if (summary) void loadLookVersion(key, versionText(summary.version))
                }}
              >
                <option value="">Select an optional saved look…</option>
                {looks.map((item) => (
                  <option key={item.key} value={item.key}>{item.name} (v{item.version})</option>
                ))}
              </select>
            </label>
            <label>
              Immutable version
              <input
                aria-label="Immutable version"
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                value={selectedVersion}
                disabled={disabled || !selectedKey || detailLoading}
                onChange={(event) => {
                  detailRequestRef.current += 1
                  setSelectedVersion(event.target.value)
                  setLookDetail(null)
                  setDetailLoading(false)
                  setDetailError('')
                  setDecisions({ look: '', initial_wardrobe: '' })
                  invalidatePreview()
                }}
              />
            </label>
            <button
              type="button"
              onClick={() => void loadLookVersion()}
              disabled={disabled || !selectedKey || !selectedVersionIdentity || detailLoading}
            >
              {detailLoading ? 'Loading version…' : 'Load version'}
            </button>
            <button
              type="button"
              onClick={() => void loadLooks(true)}
              disabled={disabled || looksLoading}
            >Reload saved looks</button>
          </div>
          {detailError && <p className="error" role="alert">{detailError}</p>}
          {lookDetail && (
            <div style={{ border: '1px solid var(--line)', borderRadius: 6, padding: 10 }}>
              <b>{lookDetail.name} · version {lookDetail.version}</b>
              <div style={{ marginTop: 6 }}><b>Fixed appearance</b></div>
              <div style={{ whiteSpace: 'pre-wrap' }}>{lookDetail.appearance || 'No additional appearance description'}</div>
              {lookDetail.outfit ? (
                <>
                  <div style={{ marginTop: 8 }}><b>Removable garments in confirmed removal order</b></div>
                  <ol style={{ marginTop: 4 }}>
                    {lookDetail.outfit.garments.map((garment, index) => (
                      <li key={`${garment.key}:${index}`}>
                        {garment.wording}
                        {garment.aside ? <span className="muted"> — moved-aside wording: {garment.aside}</span> : null}
                      </li>
                    ))}
                  </ol>
                </>
              ) : (
                <p className="muted" style={{ margin: '8px 0 0' }}>
                  This appearance-only look leaves the current wardrobe unchanged.
                </p>
              )}
              <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
                <label>
                  Look decision
                  <select
                    aria-label="Look decision"
                    value={decisions.look}
                    disabled={disabled}
                    onChange={(event) => setDecisions((previous) => ({ ...previous, look: event.target.value }))}
                  >
                    <option value="">Choose Replace or Keep…</option>
                    <option value="replace">Replace current appearance with this version</option>
                    <option value="keep">Keep current appearance</option>
                  </select>
                </label>
                {needsWardrobeDecision ? (
                  <label>
                    Wardrobe decision
                    <select
                      aria-label="Wardrobe decision"
                      value={decisions.initial_wardrobe}
                      disabled={disabled}
                      onChange={(event) => setDecisions((previous) => ({ ...previous, initial_wardrobe: event.target.value }))}
                    >
                      <option value="">Choose Replace or Keep…</option>
                      {lookDetail.outfit && <option value="replace">Replace current wardrobe with this outfit</option>}
                      <option value="keep">Keep current wardrobe</option>
                    </select>
                  </label>
                ) : (
                  <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                    This version has no outfit, so it cannot replace clothing. The current wardrobe remains unchanged.
                  </p>
                )}
                <div className="muted" style={{ fontSize: 12 }}>
                  Current appearance: {plan?.look || 'No additional appearance constraint'}
                  {' · '}current wardrobe: {plan?.initial_wardrobe || 'No wardrobe description'}
                </div>
                {wardrobeOrigin === 'saved_look' && lookDetail.outfit === null && (
                  <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                    The current wardrobe came from a saved look, so explicitly choose Keep to preserve it.
                  </p>
                )}
                <button
                  type="button"
                  className="primary"
                  onClick={handleApplyLook}
                  disabled={disabled || mutationBusy || Boolean(unknownOutcome)
                    || !Number.isInteger(revision) || revision < 1
                    || !['replace', 'keep'].includes(decisions.look)
                    || (needsWardrobeDecision && !['replace', 'keep'].includes(decisions.initial_wardrobe))
                    || (!lookDetail.outfit && decisions.initial_wardrobe === 'replace')}
                >
                  {mutationBusy ? 'Applying saved look…' : 'Apply saved look with these decisions'}
                </button>
              </div>
            </div>
          )}
        </div>
      </details>

      <details style={{ marginTop: 10 }}>
        <summary style={{ cursor: 'pointer', fontWeight: 600 }}>Plan clothing changes (optional)</summary>
        <div style={{ display: 'grid', gap: 8, paddingTop: 10 }}>
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>
            Clothing stays constant by default. This planner uses only the session’s immutable look snapshot. It does not run or redistribute again after you apply it.
          </p>
          {!snapshot && <p className="muted">Apply a saved look first. Existing session clothing remains unchanged.</p>}
          {snapshot && !snapshot.outfit && <p className="muted">This snapshot is appearance-only and has no removable garments.</p>}
          {snapshotError && <p role="alert" className="error">{planStagesError} Use a valid saved look snapshot before planning.</p>}
          {hasOutfit && !snapshotError && (
            <>
              <ol aria-label="Snapshotted garment removal order" style={{ margin: '0 0 0 20px' }}>
                {snapshot.outfit.garments.map((garment, index) => (
                  <li key={`${garment.key}:${index}`}>{garment.wording}</li>
                ))}
              </ol>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <input
                  aria-label="Confirm garment removal order"
                  type="checkbox"
                  checked={removalOrderConfirmed}
                  disabled={disabled || Boolean(unknownOutcome)}
                  onChange={(event) => { invalidatePreview(); setRemovalOrderConfirmed(event.target.checked) }}
                />
                I confirm this saved garment removal order.
              </label>
              <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}>
                <label>
                  Final clothing stage
                  <select
                    aria-label="Final clothing stage"
                    value={finalStage}
                    disabled={disabled || !removalOrderConfirmed || Boolean(unknownOutcome)}
                    onChange={(event) => {
                      invalidatePreview()
                      const next = event.target.value
                      setFinalStage(next)
                      const index = next === '' ? -1 : Number(next)
                      setIntermediateStages((previous) => previous.filter((stage) => stage < index))
                    }}
                  >
                    <option value="">Choose a final stage…</option>
                    {stages.map((stage) => (
                      <option key={stage.index} value={stage.index}>Stage {stage.index + 1}: {stage.wardrobe}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Start take
                  <select
                    aria-label="Progression start take"
                    value={resolvedStartTakeId}
                    disabled={disabled || !takeCount || Boolean(unknownOutcome)}
                    onChange={(event) => { invalidatePreview(); setStartTakeId(event.target.value) }}
                  >
                    {takes.map((take) => <option key={take.take_id} value={take.take_id}>{take.label || take.take_id}</option>)}
                  </select>
                </label>
                <label>
                  End take
                  <select
                    aria-label="Progression end take"
                    value={resolvedEndTakeId}
                    disabled={disabled || !takeCount || Boolean(unknownOutcome)}
                    onChange={(event) => { invalidatePreview(); setEndTakeId(event.target.value) }}
                  >
                    {takes.map((take) => <option key={take.take_id} value={take.take_id}>{take.label || take.take_id}</option>)}
                  </select>
                </label>
              </div>
              {finalStageIndex !== null && finalStageIndex > 0 && (
                <fieldset disabled={disabled || !removalOrderConfirmed || Boolean(unknownOutcome)}>
                  <legend>Additional stages to include</legend>
                  {stages.filter((stage) => stage.index < finalStageIndex).map((stage) => (
                    <label key={stage.index} style={{ display: 'flex', gap: 8, marginBottom: 4 }}>
                      <input
                        type="checkbox"
                        aria-label={`Include stage ${stage.index + 1}`}
                        checked={intermediateStages.includes(stage.index)}
                        onChange={(event) => {
                          invalidatePreview()
                          setIntermediateStages((previous) => event.target.checked
                            ? [...new Set([...previous, stage.index])].sort((a, b) => a - b)
                            : previous.filter((item) => item !== stage.index))
                        }}
                      />
                      Stage {stage.index + 1}: {stage.wardrobe}
                    </label>
                  ))}
                  <div className="muted" style={{ fontSize: 12 }}>
                    Selected order: {stageIndices.map((index) => `Stage ${index + 1}`).join(' → ') || 'Choose a final stage'}.
                  </div>
                </fieldset>
              )}
              <label>
                Existing wardrobe events
                <select
                  aria-label="Existing wardrobe event policy"
                  value={eventPolicy}
                  disabled={disabled || Boolean(unknownOutcome)}
                  onChange={(event) => { invalidatePreview(); setEventPolicy(event.target.value) }}
                >
                  <option value="">Choose how to handle saved events…</option>
                  <option value="merge">Merge saved events (conflicts require a new review)</option>
                  <option value="replace">Replace saved from-here events and keep this-take overrides</option>
                </select>
              </label>
              {currentEvents.length > 0 && (
                <div aria-label="Current wardrobe events" style={{ borderLeft: '3px solid var(--line)', paddingLeft: 10 }}>
                  <b>Current saved events</b>
                  {currentEvents.map((event) => (
                    <div key={event.take_id} className="muted" style={{ fontSize: 12 }}>
                      {event.take_id} · {event.scope === 'this_take' ? 'this take only' : 'from this take onward'}: {event.wardrobe}
                    </div>
                  ))}
                </div>
              )}
              {planStagesError && <p className="muted" role="status" style={{ margin: 0 }}>{planStagesError}</p>}
              <button
                type="button"
                onClick={() => void handlePreview()}
                disabled={disabled || previewBusy || mutationBusy || Boolean(unknownOutcome)
                  || !Number.isInteger(revision) || revision < 1 || Boolean(planStagesError)}
              >
                {previewBusy ? 'Requesting reviewed timeline…' : 'Preview clothing for every take'}
              </button>
              {preview && preview.signature === inputSignature && (
                <div style={{ border: '1px solid var(--line)', borderRadius: 6, padding: 10 }}>
                  <h5 style={{ margin: '0 0 6px' }}>Reviewed effective wardrobe for every take</h5>
                  <div style={{ display: 'grid', gap: 5 }}>
                    {preview.reviewed_wardrobes.map((item) => (
                      <div key={item.take_id} style={{ borderTop: '1px solid var(--line)', paddingTop: 5 }}>
                        <b>{item.take_id}:</b> {item.wardrobe || <span className="muted">(no wardrobe description)</span>}
                      </div>
                    ))}
                  </div>
                  <h5 style={{ margin: '12px 0 6px' }}>Events after this review</h5>
                  {reviewedEventRows.length ? reviewedEventRows.map((event) => (
                    <div key={event.take_id} style={{ fontSize: 12 }}>
                      {event.take_id} · {event.scope === 'this_take' ? 'this take only' : 'from this take onward'}: {event.wardrobe}
                      <span className="muted"> — {event.retained ? 'retained saved event' : 'new reviewed event'}</span>
                    </div>
                  )) : <p className="muted" style={{ margin: 0 }}>No scoped events are needed; the reviewed initial wardrobe applies to every take.</p>}
                  {removedFromHereEvents.length > 0 && (
                    <p className="muted" style={{ margin: '6px 0 0', fontSize: 12 }}>
                      Replace will remove saved from-here events on: {removedFromHereEvents.map((event) => event.take_id).join(', ')}. This-take overrides remain in the reviewed timeline.
                    </p>
                  )}
                  <button
                    type="button"
                    className="primary"
                    onClick={handleApplyProgression}
                    disabled={disabled || mutationBusy || Boolean(unknownOutcome)
                      || preview.expected_revision !== revision}
                    style={{ marginTop: 10 }}
                  >
                    {mutationBusy ? 'Applying reviewed progression…' : 'Apply this reviewed timeline'}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </details>

      {disabled && !unknownOutcome && (
        <p className="muted" role="status" style={{ margin: '8px 0 0', fontSize: 12 }}>
          Save or discard plan edits and let shared authoring requests finish before changing saved looks or clothing.
        </p>
      )}
      {actionError && <p role="alert" className="error" style={{ margin: '8px 0 0' }}>{actionError}</p>}
      {notice && <p role="status" className="muted" style={{ margin: '8px 0 0' }}>{notice}</p>}
      {unknownOutcome && (
        <button type="button" onClick={handleReadbackRetry} disabled={checkingReadback} style={{ marginTop: 8 }}>
          {checkingReadback ? 'Checking saved plan…' : 'Retry saved-plan readback'}
        </button>
      )}
      {!Number.isInteger(revision) && (
        <p role="alert" className="muted" style={{ margin: '8px 0 0', fontSize: 12 }}>
          Reload the session plan before applying a saved look or wardrobe progression.
        </p>
      )}
    </section>
  )
}
