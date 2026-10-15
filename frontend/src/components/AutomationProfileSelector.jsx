import React, { useEffect, useRef, useState } from 'react'
import { api, versionText } from '../api.js'

const digestPattern = /^[0-9a-f]{64}$/

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
  onProfileChange = () => {},
  onDiscovery = () => {},
}) {
  const [catalogue, setCatalogue] = useState(null)
  const [selected, setSelected] = useState('')
  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const requestRef = useRef(0)
  const lookIdentity = `${look?.key || ''}\u0000${versionText(look?.version) || ''}\u0000${look?.content_digest || ''}`

  useEffect(() => {
    requestRef.current += 1
    setCatalogue(null)
    setSelected('')
    setProfile(null)
    setError('')
    onProfileChange(null)
    if (!active || !look?.key || !versionText(look?.version) || !digestPattern.test(look?.content_digest || '')) return undefined
    const request = requestRef.current
    let mounted = true
    const query = new URLSearchParams({ content_digest: look.content_digest })
    setLoading(true)
    api.getVersioned(`${pathForLook(look)}/automation-profiles?${query.toString()}`)
      .then((result) => {
        if (!mounted || request !== requestRef.current) return
        if (
          !result || result.look?.key !== look.key
          || versionText(result.look?.version) !== versionText(look.version)
          || result.look?.content_digest !== look.content_digest
          || !Array.isArray(result.profiles)
        ) throw new Error('The server returned profile discovery for a different look version.')
        setCatalogue(result)
        onDiscovery(result)
      })
      .catch((cause) => {
        if (mounted && request === requestRef.current) {
          setError(cause?.message || 'Could not load profile revisions for this exact version.')
        }
      })
      .finally(() => {
        if (mounted && request === requestRef.current) setLoading(false)
      })
    return () => { mounted = false }
  }, [active, lookIdentity])

  const loadSelected = async (revision) => {
    setSelected(revision)
    setProfile(null)
    onProfileChange(null)
    setError('')
    if (!revision) return
    const listed = catalogue?.profiles?.find((item) => versionText(item.profile_revision) === revision)
    if (!listed || !digestPattern.test(listed.profile_digest || '')) {
      setError('Select a profile revision from this exact version.')
      return
    }
    const request = ++requestRef.current
    setLoading(true)
    const params = new URLSearchParams({
      look_key: look.key,
      version: versionText(look.version),
      content_digest: look.content_digest,
      profile_revision: revision,
      profile_digest: listed.profile_digest,
    })
    try {
      const result = await api.getVersioned(`/api/looks/automation-profiles?${params.toString()}`)
      if (request !== requestRef.current) return
      const identity = {
        look_key: look.key,
        version: versionText(look.version),
        content_digest: look.content_digest,
        profile_revision: revision,
        profile_digest: listed.profile_digest,
      }
      if (!validIdentity({
        look_key: result?.look?.key,
        version: result?.look?.version,
        content_digest: result?.look?.content_digest,
        profile_revision: result?.profile_revision,
        profile_digest: result?.profile_digest,
      }, look) || versionText(result.profile_revision) !== revision
          || result.profile_digest !== listed.profile_digest
          || !Array.isArray(result.appearance_segments)) {
        throw new Error('The server returned a different or incomplete profile revision.')
      }
      const loaded = {
        ...identity,
        readiness: listed.readiness,
        profile: result,
        permanent_appearance: result.appearance_segments
          .filter((segment) => segment.owner === 'permanent')
          .map((segment) => segment.text).join(''),
      }
      setProfile(loaded)
      onProfileChange(loaded)
    } catch (cause) {
      if (request === requestRef.current) setError(cause?.message || 'Could not load the exact profile revision.')
    } finally {
      if (request === requestRef.current) setLoading(false)
    }
  }

  const selectedRow = catalogue?.profiles?.find((item) => (
    versionText(item.profile_revision) === selected
  ))

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
              Revision {versionText(item.profile_revision)} · {item.profile_digest.slice(0, 12)} · {item.readiness?.profile_ready ? 'ready' : 'needs review'}
            </option>
          ))}
        </select>
      </label>
      {loading && <p className="muted" role="status">Loading exact profile identity…</p>}
      {catalogue && catalogue.profiles.length === 0 && (
        <p className="muted">No profile exists for this exact look version. A profile-free manual look remains available.</p>
      )}
      {selectedRow && selectedRow.readiness?.profile_ready === false && (
        <div className="muted" role="status" aria-label="Server profile readiness diagnostics">
          <p style={{ margin: '2px 0' }}>Server readiness: not ready for Full Auto; manual use remains available.</p>
          <ul style={{ margin: '3px 0', paddingLeft: 20 }}>
            {(selectedRow.readiness.diagnostics || []).map((item, index) => (
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
