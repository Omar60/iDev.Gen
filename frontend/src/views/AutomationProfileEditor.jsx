import React, { useMemo, useRef, useState } from 'react'
import { api, versionText } from '../api.js'
import AutomationProfileReview, { REGIONS, readyPartition, unknownRegions } from '../components/AutomationProfileReview.jsx'
import AutomationProfileSelector from '../components/AutomationProfileSelector.jsx'

const digestPattern = /^[0-9a-f]{64}$/
const SQLITE_MAX = 9223372036854775807n

const nonnegativeRevisionText = (value) => {
  const text = typeof value === 'string' ? value : typeof value === 'number' && Number.isSafeInteger(value) ? String(value) : ''
  if (!/^(?:0|[1-9]\d*)$/.test(text)) return null
  try { return BigInt(text) <= SQLITE_MAX ? text : null } catch { return null }
}

const userProvenance = (look) => ({
  origin: 'user',
  source_identity: 'looks-profile-editor',
  source_digest: look.content_digest,
})

const initialDraft = (look) => ({
  appearance_segments: look.appearance
    ? [{ segment_id: 'appearance-unassigned', start: 0, end: Array.from(look.appearance).length, text: look.appearance, owner: null }]
    : [],
  garment_coverage: Object.fromEntries((look.outfit?.garments || []).map((garment) => [garment.key, {
    worn: { regions: unknownRegions(), provenance: userProvenance(look) },
    ...(garment.aside ? { aside: { regions: unknownRegions(), provenance: userProvenance(look) } } : {}),
  }])),
  baseline_coverage: null,
  inventory_complete: { value: null, provenance: userProvenance(look) },
})

const draftFromProfile = (profile, look) => ({
  appearance_segments: profile.appearance_segments.map((segment) => ({ ...segment })),
  garment_coverage: structuredClone(profile.garment_coverage),
  baseline_coverage: profile.baseline_coverage ? structuredClone(profile.baseline_coverage) : null,
  inventory_complete: structuredClone(profile.inventory_complete),
})

const identityPath = (identity) => {
  const query = new URLSearchParams({
    look_key: identity.look_key,
    version: versionText(identity.version),
    content_digest: identity.content_digest,
    profile_revision: versionText(identity.profile_revision),
    profile_digest: identity.profile_digest,
  })
  return `/api/looks/automation-profiles?${query.toString()}`
}

const profileIdentityKey = (profile) => profile ? JSON.stringify([
  profile.look_key, versionText(profile.version), profile.content_digest,
  versionText(profile.profile_revision), profile.profile_digest,
]) : null

const copySelectionsKey = (selections) => JSON.stringify(selections.map((item) => ({
  garment_key: item.garment_key,
  source_state: item.source_state,
  state: item.state,
})))

const versionPath = (look, version) => (
  `/api/looks/${encodeURIComponent(look.key)}/versions/${versionText(version)}`
)

const errorText = (error) => error?.detail?.message || error?.message || 'The profile request failed.'

const profileIntegerFields = (profile) => {
  const fields = ['version', 'expected_profile_revision', 'profile.look.version']
  const visit = (value, path) => {
    if (!value || typeof value !== 'object') return
    if (Array.isArray(value)) {
      value.forEach((item, index) => visit(item, `${path}.${index}`))
      return
    }
    Object.entries(value).forEach(([key, item]) => {
      const child = path ? `${path}.${key}` : key
      if (key === 'accepted_revision') fields.push(child)
      visit(item, child)
    })
  }
  visit(profile, 'profile')
  return [...new Set(fields)]
}

export default function AutomationProfileEditor({ look, disabled = false }) {
  const [open, setOpen] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const [draft, setDraft] = useState(() => initialDraft(look))
  const [latestRevision, setLatestRevision] = useState('0')
  const [baseLoaded, setBaseLoaded] = useState(false)
  const [inventoryConfirmed, setInventoryConfirmed] = useState(false)
  const [baselineConfirmed, setBaselineConfirmed] = useState(false)
  const [reviewConfirmed, setReviewConfirmed] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [sourceVersion, setSourceVersion] = useState(versionText(look.version) || '')
  const [sourceLook, setSourceLook] = useState(look)
  const [sourceLoadError, setSourceLoadError] = useState('')
  const [copySelections, setCopySelections] = useState([])
  const [copyPreview, setCopyPreview] = useState(null)
  const [copyConfirmed, setCopyConfirmed] = useState(false)
  const [reviewResetKey, setReviewResetKey] = useState(0)
  const sourceProfileIdentityRef = useRef(null)
  const copyPreviewRequestRef = useRef(0)
  const copyPreviewContextRef = useRef(null)
  const draftRevisionRef = useRef(0)
  const savedLookIdentity = `${look.key}\u0000${versionText(look.version)}\u0000${look.content_digest}`

  const garments = look.outfit?.garments || []
  const owners = useMemo(() => [
    { value: 'permanent', label: 'Permanent appearance' },
    ...garments.map((garment) => ({
      value: `garment:${garment.key}`,
      label: `Progressive garment · ${garment.wording}`,
    })),
  ], [savedLookIdentity])
  const partitionValid = readyPartition(look.appearance, draft.appearance_segments, owners)
  const nextRevision = BigInt(nonnegativeRevisionText(latestRevision) || '0') + 1n
  const canPublish = !disabled && !busy && reviewConfirmed && partitionValid
    && baselineConfirmed && inventoryConfirmed && nextRevision <= SQLITE_MAX

  const invalidateCopyPreview = () => {
    copyPreviewRequestRef.current += 1
    copyPreviewContextRef.current = null
    setCopyPreview(null)
    setCopyConfirmed(false)
  }

  const resetDraft = () => {
    draftRevisionRef.current += 1
    invalidateCopyPreview()
    setDraft(initialDraft(look))
    setBaseLoaded(false)
    setInventoryConfirmed(false)
    setBaselineConfirmed(false)
    setReviewConfirmed(false)
    setNotice('')
    setReviewResetKey((key) => key + 1)
  }

  const setSourceProfile = (profile) => {
    const nextIdentity = profileIdentityKey(profile)
    if (sourceProfileIdentityRef.current !== nextIdentity) {
      sourceProfileIdentityRef.current = nextIdentity
      invalidateCopyPreview()
      setCopySelections([])
      setReviewConfirmed(false)
    }
    setSourceProfileState(profile)
  }

  const loadSourceVersion = async () => {
    const exactVersion = versionText(sourceVersion)
    if (!exactVersion) {
      setSourceLoadError('Enter a saved look version between 1 and the supported maximum.')
      return
    }
    setBusy(true)
    setSourceLoadError('')
    setSourceProfile(null)
    setCopySelections([])
    invalidateCopyPreview()
    try {
      if (exactVersion === versionText(look.version)) {
        setSourceLook(look)
      } else {
        const result = await api.getVersioned(versionPath(look, exactVersion))
        if (
          result?.key !== look.key || versionText(result.version) !== exactVersion
          || !digestPattern.test(result.content_digest || '')
          || !Array.isArray(result.outfit?.garments) && result.outfit !== null
        ) throw new Error('The server returned a different saved-look version.')
        setSourceLook(result)
      }
    } catch (cause) {
      setSourceLook(null)
      setSourceLoadError(errorText(cause))
    } finally {
      setBusy(false)
    }
  }

  const toggleCopySource = (garmentKey, sourceState, checked) => {
    invalidateCopyPreview()
    setReviewConfirmed(false)
    setCopySelections((current) => checked
      ? current.some((item) => item.garment_key === garmentKey && item.source_state === sourceState)
        ? current
        : [...current, { garment_key: garmentKey, source_state: sourceState, state: '' }]
      : current.filter((item) => !(item.garment_key === garmentKey && item.source_state === sourceState)))
  }

  const setCopyTargetState = (garmentKey, sourceState, targetState) => {
    invalidateCopyPreview()
    setReviewConfirmed(false)
    setCopySelections((current) => current.map((item) => (
      item.garment_key === garmentKey && item.source_state === sourceState
        ? { ...item, state: targetState }
        : item
    )))
  }

  const previewCopy = async () => {
    if (!sourceProfile || copySelections.length === 0 || copySelections.some((item) => !item.state) || !sourceLook || busy) return
    const request = ++copyPreviewRequestRef.current
    const source = { ...sourceProfile }
    const sourceIdentity = profileIdentityKey(source)
    const sourceLookIdentity = JSON.stringify([
      sourceLook.key, versionText(sourceLook.version), sourceLook.content_digest,
    ])
    const selectedMappings = copySelections.map((item) => ({ ...item }))
    const mappingsKey = copySelectionsKey(selectedMappings)
    const targetIdentity = JSON.stringify([look.key, versionText(look.version), look.content_digest])
    const expectedProfileRevision = nonnegativeRevisionText(latestRevision)
    const draftRevision = draftRevisionRef.current
    setBusy(true)
    setError('')
    setCopyPreview(null)
    setCopyConfirmed(false)
    copyPreviewContextRef.current = null
    try {
      const body = {
        target_look_key: look.key,
        target_version: versionText(look.version),
        target_content_digest: look.content_digest,
        expected_profile_revision: expectedProfileRevision,
        source_look_key: sourceLook.key,
        source_version: versionText(sourceLook.version),
        source_content_digest: sourceLook.content_digest,
        source_profile_revision: source.profile_revision,
        source_profile_digest: source.profile_digest,
        selections: selectedMappings.map((selection) => ({
          garment_key: selection.garment_key,
          source_state: selection.source_state,
          state: selection.state,
        })),
        copy_baseline: false,
      }
      const result = await api.postVersioned(
        '/api/looks/automation-profiles/copy-preview', body,
        ['target_version', 'expected_profile_revision', 'source_version', 'source_profile_revision'],
      )
      if (
        result?.target?.look_key !== look.key
        || versionText(result.target.version) !== versionText(look.version)
        || result.target.content_digest !== look.content_digest
        || nonnegativeRevisionText(result.target.expected_profile_revision) !== expectedProfileRevision
        || result.source?.look_key !== source.look_key
        || versionText(result.source?.version) !== versionText(source.version)
        || result.source?.content_digest !== source.content_digest
        || versionText(result.source?.profile_revision) !== versionText(source.profile_revision)
        || result.source?.profile_digest !== source.profile_digest
        || !result.garment_coverage || !Array.isArray(result.skipped)
        || JSON.stringify(result.selections) !== JSON.stringify(selectedMappings.map((selection) => ({
          garment_key: selection.garment_key,
          source_state: selection.source_state,
          state: selection.state,
        })))
      ) throw new Error('The server returned a copy preview for a different profile identity.')
      if (
        request !== copyPreviewRequestRef.current
        || sourceIdentity !== sourceProfileIdentityRef.current
        || sourceLookIdentity !== JSON.stringify([
          sourceLook.key, versionText(sourceLook.version), sourceLook.content_digest,
        ])
        || mappingsKey !== copySelectionsKey(copySelections)
        || targetIdentity !== JSON.stringify([look.key, versionText(look.version), look.content_digest])
        || expectedProfileRevision !== nonnegativeRevisionText(latestRevision)
        || draftRevision !== draftRevisionRef.current
      ) return
      copyPreviewContextRef.current = {
        request,
        sourceIdentity,
        sourceLookIdentity,
        mappingsKey,
        targetIdentity,
        expectedProfileRevision,
        draftRevision,
      }
      setCopyPreview(result)
    } catch (cause) {
      if (request === copyPreviewRequestRef.current) setError(errorText(cause))
    } finally {
      setBusy(false)
    }
  }

  const applyCopyPreview = () => {
    if (!copyPreview || !copyConfirmed) return
    const context = copyPreviewContextRef.current
    const currentSourceIdentity = profileIdentityKey(sourceProfile)
    const currentSourceLookIdentity = sourceLook ? JSON.stringify([
      sourceLook.key, versionText(sourceLook.version), sourceLook.content_digest,
    ]) : null
    const currentTargetIdentity = JSON.stringify([look.key, versionText(look.version), look.content_digest])
    if (
      !context || context.request !== copyPreviewRequestRef.current
      || context.sourceIdentity !== currentSourceIdentity
      || context.sourceLookIdentity !== currentSourceLookIdentity
      || context.mappingsKey !== copySelectionsKey(copySelections)
      || context.targetIdentity !== currentTargetIdentity
      || context.expectedProfileRevision !== nonnegativeRevisionText(latestRevision)
      || context.draftRevision !== draftRevisionRef.current
    ) {
      invalidateCopyPreview()
      setError('The selected source, target revision, or draft changed. Review a new copy preview before applying it.')
      return
    }
    draftRevisionRef.current += 1
    setDraft((current) => {
      const next = structuredClone(current)
      const user = userProvenance(look)
      const userAssertedTargets = new Set(copyPreview.selections
        .filter((selection) => selection.source_state !== selection.state)
        .map((selection) => `${selection.garment_key}\u0000${selection.state}`))
      for (const selectionValue of copySelections) {
        const targetGarment = garments.find((garment) => garment.key === selectionValue.garment_key)
        if (!targetGarment || (selectionValue.state === 'aside' && !targetGarment.aside)) continue
        next.garment_coverage[selectionValue.garment_key] ||= {}
        next.garment_coverage[selectionValue.garment_key][selectionValue.state] = {
          regions: unknownRegions(),
          provenance: user,
        }
      }
      for (const [key, states] of Object.entries(copyPreview.garment_coverage)) {
        next.garment_coverage[key] ||= {}
        for (const [state, fact] of Object.entries(states)) {
          const copiedFact = structuredClone(fact)
          if (userAssertedTargets.has(`${key}\u0000${state}`)) {
            copiedFact.provenance = {
              ...user,
              source_identity: `automation-profile:${copyPreview.source.profile_digest}`,
            }
          }
          next.garment_coverage[key][state] = copiedFact
        }
      }
      return next
    })
    setCopyConfirmed(false)
    setCopyPreview(null)
    copyPreviewContextRef.current = null
    setReviewConfirmed(false)
    setNotice('Reviewed copy applied to the unsaved draft. Confirmed cross-state equivalences are user assertions; changed or unaccepted states remain unknown.')
  }

  const loadBaseProfile = () => {
    if (!targetProfile) return
    invalidateCopyPreview()
    draftRevisionRef.current += 1
    setDraft(draftFromProfile(targetProfile.profile, look))
    setInventoryConfirmed(targetProfile.profile.inventory_complete.value !== null)
    setBaselineConfirmed(Boolean(targetProfile.profile.baseline_coverage))
    setBaseLoaded(true)
    setReviewConfirmed(false)
    setNotice(`Revision ${versionText(targetProfile.profile_revision)} loaded as the editing basis; publishing creates a new immutable revision.`)
  }

  const [targetProfile, setTargetProfile] = useState(null)
  const [sourceProfile, setSourceProfileState] = useState(null)

  const publish = async () => {
    if (!canPublish) return
    invalidateCopyPreview()
    setBusy(true)
    setError('')
    setNotice('')
    const revision = nextRevision.toString()
    const provenance = (existing) => ({
      ...(existing || userProvenance(look)),
      accepted_revision: revision,
    })
    const profile = {
      schema_version: 1,
      look: { key: look.key, version: versionText(look.version), content_digest: look.content_digest },
      appearance_segments: draft.appearance_segments.map((segment) => ({
        ...segment,
        provenance: provenance(segment.provenance),
      })),
      garment_coverage: Object.fromEntries(Object.entries(draft.garment_coverage).map(([key, states]) => [
        key,
        Object.fromEntries(Object.entries(states).map(([state, fact]) => [state, {
          regions: { ...fact.regions }, provenance: provenance(fact.provenance),
        }])),
      ])),
      baseline_coverage: draft.baseline_coverage ? {
        regions: { ...draft.baseline_coverage.regions },
        provenance: provenance(draft.baseline_coverage.provenance),
      } : null,
      inventory_complete: {
        value: draft.inventory_complete.value,
        provenance: provenance(draft.inventory_complete.provenance),
      },
      authored_provenance: {
        ...userProvenance(look), accepted_revision: revision,
      },
    }
    const body = {
      look_key: look.key,
      version: versionText(look.version),
      content_digest: look.content_digest,
      expected_profile_revision: nonnegativeRevisionText(latestRevision),
      profile,
    }
    try {
      const saved = await api.postVersioned(
        '/api/looks/automation-profiles', body, profileIntegerFields(profile),
      )
      if (
        versionText(saved?.profile_revision) !== revision
        || !digestPattern.test(saved?.profile_digest || '')
      ) throw new Error('The server returned an invalid immutable profile identity.')
      setLatestRevision(revision)
      setNotice(`Saved exact profile revision ${revision} (${saved.profile_digest}).`)
      setTargetProfile(null)
      setRefreshKey((key) => key + 1)
    } catch (cause) {
      setError(errorText(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <details
      style={{ borderTop: '1px solid var(--line)', marginTop: 12, paddingTop: 10 }}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      aria-label="Automation profile editor"
    >
      <summary style={{ cursor: 'pointer', fontWeight: 600 }}>Reusable automation profile (Portable look-v1 does not include profiles)</summary>
      <div style={{ display: 'grid', gap: 10, paddingTop: 10 }}>
        <p className="muted" style={{ margin: 0 }}>
          Profiles are separate immutable revisions for this exact saved-look version. Portable look-v1 export/import does not transfer automation profiles or readiness.
          Save any appearance or garment wording change as a new look version before assigning spans.
        </p>
        <AutomationProfileSelector
          key={`${savedLookIdentity}:${refreshKey}`}
          look={look}
          active={open}
          disabled={disabled || busy}
          label="Profile revision to use as an editing basis"
          onProfileChange={setTargetProfile}
          onDiscovery={(result) => setLatestRevision(nonnegativeRevisionText(result.latest_profile_revision) || '0')}
        />
        <div className="row" style={{ flexWrap: 'wrap' }}>
          <button type="button" onClick={loadBaseProfile} disabled={disabled || busy || !targetProfile}>
            Load selected revision as editing basis
          </button>
          <button type="button" onClick={resetDraft} disabled={disabled || busy}>Start a blank profile draft</button>
        </div>
        {baseLoaded && <p className="muted" role="status">Editing from an immutable revision; Save creates the next revision.</p>}

        <AutomationProfileReview
          key={reviewResetKey}
          appearance={look.appearance}
          garments={garments.map((garment) => ({
            id: garment.key,
            wording: garment.wording,
            aside: garment.aside,
            wornLabel: 'Worn',
            asideLabel: `Moved aside · ${garment.aside}`,
          }))}
          draft={draft}
          onDraftChange={(next) => {
            invalidateCopyPreview()
            draftRevisionRef.current += 1
            setDraft(next)
          }}
          inventoryConfirmed={inventoryConfirmed}
          onInventoryConfirmed={setInventoryConfirmed}
          baselineConfirmed={baselineConfirmed}
          onBaselineConfirmed={setBaselineConfirmed}
          reviewConfirmed={reviewConfirmed}
          onReviewConfirmed={setReviewConfirmed}
          provenance={userProvenance(look)}
          reviewLabel="I reviewed ownership, every coverage state, the baseline and inventory for this exact saved-look version."
          appearanceLabel="Saved appearance · select a phrase to assign or correct"
          appearanceInputLabel="Saved appearance for ownership selection"
          onNotice={setNotice}
          showReviewConfirmation={false}
          disabled={disabled || busy}
        />

        <fieldset>
          <legend>Copy reviewed state facts</legend>
          <p className="muted">Choose an exact source revision and explicitly map each accepted source state to an equivalent target state. Across look versions, the server copies only matching garment keys and canonical/state wording; changed states stay unknown. Appearance spans are never copied.</p>
          <div className="row" style={{ flexWrap: 'wrap' }}>
            <label>
              Source look version
              <input aria-label="Source look version" inputMode="numeric" value={sourceVersion} onChange={(event) => {
                setSourceVersion(event.target.value)
                setSourceLook(null)
                setSourceProfile(null)
                setCopySelections([])
                invalidateCopyPreview()
              }} disabled={disabled || busy} />
            </label>
            <button type="button" onClick={() => void loadSourceVersion()} disabled={disabled || busy || !versionText(sourceVersion)}>Load source version</button>
          </div>
          {sourceLoadError && <p className="error" role="alert">{sourceLoadError}</p>}
          {sourceLook && <p className="muted">Source: version {versionText(sourceLook.version)} · {sourceLook.content_digest}</p>}
          {sourceLook && <AutomationProfileSelector
            key={`source:${sourceLook.key}:${versionText(sourceLook.version)}:${sourceLook.content_digest}:${refreshKey}`}
            look={sourceLook}
            active={open}
            disabled={disabled || busy}
            label="Exact source profile revision"
            onProfileChange={setSourceProfile}
          />}
          {sourceProfile && Object.entries(sourceProfile.profile.garment_coverage || {}).map(([key, states]) => {
            const sourceGarment = sourceLook?.outfit?.garments?.find((item) => item.key === key)
            const targetGarment = garments.find((item) => item.key === key)
            const targetStates = targetGarment
              ? ['worn', ...(targetGarment.aside ? ['aside'] : [])]
              : []
            return <div key={key}>
              {Object.entries(states).map(([sourceState, fact]) => {
                const selectionValue = copySelections.find((item) => (
                  item.garment_key === key && item.source_state === sourceState
                ))
                return <div key={`${key}:${sourceState}`} style={{ display: 'grid', gap: 4, marginBottom: 6 }}>
                  <label>
                    <input
                      type="checkbox"
                      checked={Boolean(selectionValue)}
                      onChange={(event) => toggleCopySource(key, sourceState, event.target.checked)}
                      disabled={disabled || busy}
                    />
                    Copy reviewed {sourceState} coverage for {sourceGarment?.wording || 'the selected garment'}: {REGIONS.map((region) => `${region} ${fact.regions[region]}`).join(', ')}
                  </label>
                  {selectionValue && <label>
                    Equivalent target state for this source fact
                    <select
                      aria-label={`Target state for ${sourceGarment?.wording || key} ${sourceState} coverage`}
                      value={selectionValue.state}
                      onChange={(event) => setCopyTargetState(key, sourceState, event.target.value)}
                      disabled={disabled || busy || targetStates.length === 0}
                    >
                      <option value="">Choose explicitly…</option>
                      {targetStates.map((targetState) => (
                        <option key={targetState} value={targetState}>{targetState === 'worn' ? 'Worn' : `Moved aside · ${targetGarment.aside}`}</option>
                      ))}
                    </select>
                  </label>}
                </div>
              })}
            </div>
          })}
          {sourceProfile && <button type="button" onClick={() => void previewCopy()} disabled={disabled || busy || copySelections.length === 0 || copySelections.some((item) => !item.state)}>Preview selected reviewed copy</button>}
          {copyPreview && <div className="panel" aria-label="Reviewed coverage copy preview">
            <p>Exact source profile: revision {versionText(copyPreview.source.profile_revision)} · {copyPreview.source.profile_digest}</p>
            <p>Selected mappings: {copyPreview.selections.map((item) => `${item.garment_key} · ${item.source_state} → ${item.state}`).join('; ')}</p>
            <p>Copied target states: {Object.entries(copyPreview.garment_coverage).flatMap(([key, states]) => Object.keys(states).map((state) => `${key} · ${state}`)).join('; ') || 'none'}</p>
            <p>Skipped facts: {copyPreview.skipped.map((item) => `${item.garment_key || 'baseline'} · ${item.source_state || item.state} → ${item.state}: ${item.code}`).join('; ') || 'none'}</p>
            <label><input type="checkbox" checked={copyConfirmed} disabled={disabled || busy} onChange={(event) => setCopyConfirmed(event.target.checked)} /> I reviewed this exact copy preview and accept the unchanged facts and selected equivalences.</label>
            <button type="button" onClick={applyCopyPreview} disabled={!copyConfirmed || disabled || busy}>Apply reviewed copy to draft</button>
          </div>}
        </fieldset>

        <label>
          <input type="checkbox" checked={reviewConfirmed} onChange={(event) => setReviewConfirmed(event.target.checked)} disabled={disabled || busy || !partitionValid || !baselineConfirmed || !inventoryConfirmed} />
          I reviewed ownership, every coverage state, the baseline and inventory for this exact saved-look version.
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        <button type="button" className="primary" onClick={() => void publish()} disabled={!canPublish}>
          {busy ? 'Saving profile…' : `Publish immutable profile revision ${nextRevision <= SQLITE_MAX ? nextRevision.toString() : 'unavailable'}`}
        </button>
        {latestRevision !== '0' && <p className="muted">Current latest revision for CAS: {latestRevision}. Editing an older revision still publishes after the latest.</p>}
      </div>
    </details>
  )
}
