import React, { useEffect, useRef, useState } from 'react'
import { api, versionText } from '../api.js'

const digestPattern = /^[0-9a-f]{64}$/
const revisionPattern = /^[1-9]\d*$/

const pathForLook = (look) => (
  `/api/looks/${encodeURIComponent(look.key)}/versions/${versionText(look.version)}`
)

const validIdentity = (identity, look) => Boolean(
  identity && identity.look_key === look.key
  && versionText(identity.version) === versionText(look.version)
  && identity.content_digest === look.content_digest
  && versionText(identity.profile_revision)
  && digestPattern.test(identity.profile_digest || '')
)

const validCatalogue = (result, look) => {
  if (
    !result || result.look?.key !== look.key
    || versionText(result.look?.version) !== versionText(look.version)
    || result.look?.content_digest !== look.content_digest
    || !Array.isArray(result.profiles)
  ) return false

  const revisions = new Set()
  return result.profiles.every((item) => {
    const revision = versionText(item?.profile_revision)
    if (!revisionPattern.test(revision || '') || revisions.has(revision)
      || !digestPattern.test(item?.profile_digest || '')
      || typeof item?.readiness?.profile_ready !== 'boolean'
      || !Array.isArray(item.readiness.diagnostics)) return false
    revisions.add(revision)
    return true
  })
}

const diagnosticText = (item) => {
  const names = {
    automation_profile_identity_required: 'Select an exact profile revision.',
    automation_profile_missing: 'No automation profile has been saved for this look version.',
    profile_provenance_unaccepted: 'The profile needs reviewed source provenance.',
    appearance_provenance_unaccepted: 'Appearance ownership needs review.',
    appearance_progression_conflict: 'A permanent appearance phrase conflicts with a removable garment.',
    inventory_unconfirmed: 'Confirm whether the listed garment inventory is complete.',
    inventory_incomplete: 'The inventory is marked incomplete.',
    baseline_missing: 'Set the coverage baseline with every listed garment absent.',
    baseline_progression_conflict: 'Baseline coverage conflicts with the removable outfit.',
    coverage_state_missing: 'A required worn or moved-aside state is missing.',
    coverage_unknown: `Coverage is unknown for ${item.region || 'a supported region'}.`,
    coverage_provenance_unaccepted: 'A coverage fact needs accepted provenance.',
    inventory_provenance_unaccepted: 'The inventory confirmation needs accepted provenance.',
  }
  return names[item?.code] || 'The server reports that this profile is not ready.'
}

export default function AutomationProfileSelector({
  look,
  active = true,
  disabled = false,
  label = 'Exact automation profile revision',
  autoSelectSoleReady = false,
  onProfileChange = () => {},
  onDiscovery = () => {},
}) {
  const [catalogue, setCatalogue] = useState(null)
  const [selected, setSelected] = useState('')
  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const requestRef = useRef(0)
  const mountedRef = useRef(false)
  const catalogueRef = useRef(null)
  const selectedRef = useRef('')
  const profileRef = useRef(null)
  const identityRef = useRef(null)
  const onProfileChangeRef = useRef(onProfileChange)
  const onDiscoveryRef = useRef(onDiscovery)
  const lookIdentity = `${look?.key || ''}\u0000${versionText(look?.version) || ''}\u0000${look?.content_digest || ''}`
  const lookIdentityRef = useRef(lookIdentity)
  lookIdentityRef.current = lookIdentity
  onProfileChangeRef.current = onProfileChange
  onDiscoveryRef.current = onDiscovery

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      requestRef.current += 1
    }
  }, [])

  const loadSelected = async (revision, sourceCatalogue = catalogueRef.current, identity = lookIdentityRef.current) => {
    const selectedRevision = versionText(revision)
    const request = ++requestRef.current
    selectedRef.current = selectedRevision || ''
    setSelected(selectedRevision || '')
    profileRef.current = null
    setProfile(null)
    onProfileChangeRef.current(null)
    setError('')
    if (!selectedRevision) return

    const currentLook = look
    const matchingRows = sourceCatalogue?.profiles?.filter((item) => (
      versionText(item.profile_revision) === selectedRevision
    )) || []
    const listed = matchingRows.length === 1 ? matchingRows[0] : null
    if (!listed || !digestPattern.test(listed.profile_digest || '')
      || typeof listed.readiness?.profile_ready !== 'boolean') {
      if (mountedRef.current && request === requestRef.current && identity === lookIdentityRef.current) {
        setError('Select a profile revision from this exact version.')
      }
      return
    }

    setLoading(true)
    const params = new URLSearchParams({
      look_key: currentLook.key,
      version: versionText(currentLook.version),
      content_digest: currentLook.content_digest,
      profile_revision: selectedRevision,
      profile_digest: listed.profile_digest,
    })
    try {
      const result = await api.getVersioned(`/api/looks/automation-profiles?${params.toString()}`)
      if (!mountedRef.current || request !== requestRef.current || identity !== lookIdentityRef.current) return
      const identityFromResponse = {
        look_key: result?.look?.key,
        version: result?.look?.version,
        content_digest: result?.look?.content_digest,
        profile_revision: result?.profile_revision,
        profile_digest: result?.profile_digest,
      }
      if (!validIdentity(identityFromResponse, currentLook)
          || versionText(result.profile_revision) !== selectedRevision
          || result.profile_digest !== listed.profile_digest
          || !Array.isArray(result.appearance_segments)) {
        throw new Error('The server returned a different or incomplete profile revision.')
      }
      const loaded = {
        look_key: currentLook.key,
        version: versionText(currentLook.version),
        content_digest: currentLook.content_digest,
        profile_revision: selectedRevision,
        profile_digest: listed.profile_digest,
        readiness: listed.readiness,
        profile: result,
        permanent_appearance: result.appearance_segments
          .filter((segment) => segment.owner === 'permanent')
          .map((segment) => segment.text).join(''),
      }
      profileRef.current = loaded
      setProfile(loaded)
      onProfileChangeRef.current(loaded)
    } catch (cause) {
      if (mountedRef.current && request === requestRef.current && identity === lookIdentityRef.current) {
        setError(cause?.message || 'Could not load the exact profile revision.')
      }
    } finally {
      if (mountedRef.current && request === requestRef.current && identity === lookIdentityRef.current) {
        setLoading(false)
      }
    }
  }

  useEffect(() => {
    const request = ++requestRef.current
    const identity = lookIdentity
    const identityChanged = identityRef.current !== identity
    const resetSelection = identityChanged || (!autoSelectSoleReady && !active)
    if (identityChanged) {
      identityRef.current = identity
    }
    if (resetSelection) {
      catalogueRef.current = null
      selectedRef.current = ''
      profileRef.current = null
      setCatalogue(null)
      setSelected('')
      setProfile(null)
      setError('')
      onProfileChangeRef.current(null)
    }
    if (!active) {
      setLoading(false)
      return undefined
    }
    if (!look?.key || !versionText(look?.version) || !digestPattern.test(look?.content_digest || '')) {
      setLoading(false)
      return undefined
    }
    if (autoSelectSoleReady && !identityChanged && catalogueRef.current) {
      setError('')
      if (selectedRef.current && !profileRef.current) {
        void loadSelected(selectedRef.current, catalogueRef.current, identity)
      }
      setLoading(false)
      return undefined
    }

    const currentLook = look
    const query = new URLSearchParams({ content_digest: currentLook.content_digest })
    setLoading(true)
    void (async () => {
      try {
        const result = await api.getVersioned(`${pathForLook(currentLook)}/automation-profiles?${query.toString()}`)
        if (!mountedRef.current || request !== requestRef.current || identity !== lookIdentityRef.current) return
        if (!validCatalogue(result, currentLook)) {
          throw new Error('The server returned profile discovery for a different or incomplete identity.')
        }
        catalogueRef.current = result
        setCatalogue(result)
        onDiscoveryRef.current(result)
        const ready = result.profiles.filter((item) => item.readiness.profile_ready)
        if (autoSelectSoleReady && !selectedRef.current && ready.length === 1) {
          void loadSelected(ready[0].profile_revision, result, identity)
        }
      } catch (cause) {
        if (mountedRef.current && request === requestRef.current && identity === lookIdentityRef.current) {
          setError(cause?.message || 'Could not load profile revisions for this exact version.')
        }
      } finally {
        if (mountedRef.current && request === requestRef.current && identity === lookIdentityRef.current) {
          setLoading(false)
        }
      }
    })()
    return undefined
  }, [active, lookIdentity, autoSelectSoleReady])

  const selectedRow = catalogue?.profiles?.find((item) => (
    versionText(item.profile_revision) === selected
    && (!profile || item.profile_digest === profile.profile_digest)
  ))
  const readyProfiles = catalogue?.profiles?.filter((item) => item.readiness.profile_ready) || []
  const needsProfileCorrection = autoSelectSoleReady && catalogue && readyProfiles.length === 0

  return (
    <div style={{ display: 'grid', gap: 7 }}>
      <label>
        {label}
        <select
          aria-label={label}
          value={selected}
          disabled={disabled || loading || !catalogue}
          onChange={(event) => void loadSelected(event.target.value)}
        >
          <option value="">Choose a revision explicitly…</option>
          {(catalogue?.profiles || []).map((item) => (
            <option key={`${item.profile_revision}:${item.profile_digest}`} value={versionText(item.profile_revision)}>
              Revision {versionText(item.profile_revision)} · {item.profile_digest.slice(0, 12)} · {item.readiness.profile_ready ? 'ready' : 'needs review'}
            </option>
          ))}
        </select>
      </label>
      {loading && <p className="muted" role="status">Loading exact profile identity…</p>}
      {catalogue && catalogue.profiles.length === 0 && (
        <p className="muted">No profile exists for this exact look version. A profile-free manual look remains available.</p>
      )}
      {autoSelectSoleReady && readyProfiles.length > 1 && !selected && (
        <p className="muted" role="status">More than one profile revision is ready. Choose the exact revision for this session.</p>
      )}
      {needsProfileCorrection && (
        <div className="muted" role="status" aria-label="No ready automation profile">
          <p style={{ margin: '2px 0' }}>No exact profile revision is ready for Full Auto. Review the diagnostics in Looks before continuing.</p>
          {catalogue.profiles.length > 0 && (
            <ul style={{ margin: '3px 0', paddingLeft: 20 }}>
              {catalogue.profiles.flatMap((item) => (
                item.readiness.diagnostics.length > 0
                  ? item.readiness.diagnostics.map((diagnostic, index) => (
                    <li key={`${item.profile_revision}:${index}`}>
                      Revision {versionText(item.profile_revision)}: {diagnosticText(diagnostic)}
                    </li>
                  ))
                  : [<li key={item.profile_revision}>Revision {versionText(item.profile_revision)} needs review.</li>]
              ))}
            </ul>
          )}
          <a href="#/looks">Open Looks to review the saved look profile.</a>
        </div>
      )}
      {selectedRow && selectedRow.readiness?.profile_ready === false && (
        <div className="muted" role="status" aria-label="Server profile readiness diagnostics">
          <p style={{ margin: '2px 0' }}>Server readiness: not ready for Full Auto; manual use remains available.</p>
          <ul style={{ margin: '3px 0', paddingLeft: 20 }}>
            {selectedRow.readiness.diagnostics.map((item, index) => (
              <li key={`${item.code}:${index}`}>{diagnosticText(item)}</li>
            ))}
          </ul>
        </div>
      )}
      {selectedRow?.readiness?.profile_ready && <p className="muted" role="status">Server readiness: ready for Full Auto.</p>}
      {profile && <p className="muted">Exact profile digest: {profile.profile_digest}</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  )
}
