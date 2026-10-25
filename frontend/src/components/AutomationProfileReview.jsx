import React, { useEffect, useId, useRef, useState } from 'react'
import { assignScalarSpanOwner, scalarSpanFromUtf16Selection } from '../appearanceOwnership.js'
import '../ui/foundations.css'
import './AutomationProfile.css'

export const REGIONS = ['chest', 'hips', 'legs', 'feet']
export const COVERAGE = ['unknown', 'full', 'partial', 'none']

export const unknownRegions = () => Object.fromEntries(REGIONS.map((region) => [region, 'unknown']))

let appearanceSegmentSequence = 0
const idFactoryFor = () => `appearance-${Date.now().toString(36)}-${(++appearanceSegmentSequence).toString(36)}`

export function readyPartition(appearance, segments, owners) {
  const ownerValues = new Set(owners.map((owner) => owner.value))
  let offset = 0
  for (const segment of segments) {
    if (
      segment.start !== offset || segment.end <= segment.start
      || segment.text !== Array.from(appearance).slice(segment.start, segment.end).join('')
      || !ownerValues.has(segment.owner)
    ) return false
    offset = segment.end
  }
  return offset === Array.from(appearance).length
}

export default function AutomationProfileReview({
  appearance,
  garments,
  draft,
  onDraftChange,
  inventoryConfirmed,
  onInventoryConfirmed,
  baselineConfirmed,
  onBaselineConfirmed,
  reviewConfirmed,
  onReviewConfirmed,
  provenance,
  reviewLabel = 'I reviewed ownership, every coverage state, the baseline and inventory for this exact look.',
  appearanceLabel = 'Appearance · select a phrase to assign or correct',
  appearanceInputLabel = 'Appearance for ownership selection',
  onNotice,
  showReviewConfirmation = true,
  disabled = false,
}) {
  const [selection, setSelection] = useState(null)
  const [ownerChoice, setOwnerChoice] = useState('')
  const [pendingShortcut, setPendingShortcut] = useState('')
  const [noneCoverageTargets, setNoneCoverageTargets] = useState([])
  const [error, setError] = useState('')
  const appearanceRef = useRef(null)
  const ownershipHeading = useRef(null)
  const coverageHeading = useRef(null)
  const inventoryRef = useRef(null)
  const errorRef = useRef(null)
  const shortcutTrigger = useRef(null)
  const helpId = useId()
  const focusSection = (target) => {
    target.current?.focus()
    target.current?.scrollIntoView({ block: 'start' })
  }
  useEffect(() => { if (error) errorRef.current?.focus() }, [error])
  const inputIdentity = JSON.stringify([
    appearance, garments.map(({ id, wording, aside }) => [id, wording, aside]),
  ])
  const owners = [
    { value: 'permanent', label: 'Permanent appearance' },
    ...garments.map((garment) => ({
      value: `garment:${garment.id}`,
      label: `Progressive garment · ${garment.wording}`,
    })),
  ]
  const partitionValid = readyPartition(appearance, draft.appearance_segments, owners)
  const permanentProjection = draft.appearance_segments
    .filter((segment) => segment.owner === 'permanent')
    .map((segment) => segment.text)
    .join('')

  useEffect(() => {
    setSelection(null)
    setOwnerChoice('')
    setPendingShortcut('')
    setNoneCoverageTargets([])
  }, [inputIdentity])
  const noneTargetOptions = [
    { id: 'baseline', kind: 'baseline', label: 'Baseline · every listed garment absent' },
    ...garments.flatMap((garment) => [
      { id: `${garment.id}\u0000worn`, kind: 'garment_state', garment_id: garment.id, state: 'worn', label: `${garment.wording} · worn` },
      ...(garment.aside ? [{ id: `${garment.id}\u0000aside`, kind: 'garment_state', garment_id: garment.id, state: 'aside', label: `${garment.wording} · moved aside` }] : []),
    ]),
  ]

  const changeDraft = (update) => {
    onDraftChange(update(structuredClone(draft)))
    onReviewConfirmed(false)
  }

  const updateCoverage = (selector, state, region, value) => {
    changeDraft((next) => {
      if (selector === 'baseline') {
        next.baseline_coverage ||= { regions: unknownRegions() }
        next.baseline_coverage.regions[region] = value
        if (provenance) next.baseline_coverage.provenance = provenance
      } else {
        next.garment_coverage[selector] ||= {}
        next.garment_coverage[selector][state] ||= { regions: unknownRegions() }
        next.garment_coverage[selector][state].regions[region] = value
        if (provenance) next.garment_coverage[selector][state].provenance = provenance
      }
      return next
    })
    if (selector === 'baseline') onBaselineConfirmed(true)
  }

  const selectAppearanceSpan = (event) => {
    try {
      const next = scalarSpanFromUtf16Selection(
        appearance, event.currentTarget.selectionStart, event.currentTarget.selectionEnd,
      )
      setSelection(next.end > next.start ? next : null)
    } catch {
      setSelection(null)
    }
  }

  const assignSelectedOwner = () => {
    if (!selection || !ownerChoice) return
    try {
      changeDraft((next) => ({
        ...next,
        appearance_segments: assignScalarSpanOwner(
          appearance, next.appearance_segments, selection.start, selection.end,
          ownerChoice, idFactoryFor(),
        ),
      }))
      setSelection(null)
    } catch (cause) {
      setError(cause?.message || 'Could not assign appearance ownership.')
      setSelection(null)
    }
  }

  const confirmShortcut = () => {
    if (pendingShortcut === 'permanent') {
      const length = Array.from(appearance).length
      changeDraft((next) => ({
        ...next,
        appearance_segments: length ? [{
          segment_id: 'appearance-permanent', start: 0, end: length,
          text: appearance, owner: 'permanent',
        }] : [],
      }))
      onNotice?.('Preview accepted: every appearance clause is now asserted permanent for this exact look.')
    } else if (pendingShortcut === 'none') {
      changeDraft((next) => {
        for (const target of noneCoverageTargets) {
          const regions = Object.fromEntries(REGIONS.map((region) => [region, 'none']))
          if (target.kind === 'baseline') {
            next.baseline_coverage = { regions, ...(provenance ? { provenance } : {}) }
          } else {
            const garment = garments.find((item) => item.id === target.garment_id)
            if (!garment || (target.state === 'aside' && !garment.aside)) continue
            next.garment_coverage[target.garment_id] ||= {}
            next.garment_coverage[target.garment_id][target.state] = {
              regions, ...(provenance ? { provenance } : {}),
            }
          }
        }
        return next
      })
      if (noneCoverageTargets.some((target) => target.kind === 'baseline')) onBaselineConfirmed(true)
      setNoneCoverageTargets([])
    }
    setPendingShortcut('')
    shortcutTrigger.current?.focus()
  }

  const setNoneCoverageTarget = (target, checked) => {
    setNoneCoverageTargets((current) => checked
      ? current.some((item) => item.id === target.id) ? current : [...current, target]
      : current.filter((item) => item.id !== target.id))
    onReviewConfirmed(false)
  }

  return (
    <div className="studio-ui automation-ui">
      <div className="automation-review-summary" aria-label="Profile review status">
        <span className="ui-state" data-tone={partitionValid ? undefined : 'warning'}>{partitionValid ? 'Ownership assigned' : 'Ownership pending'}</span>
        <span className="ui-state" data-tone={baselineConfirmed ? undefined : 'warning'}>{baselineConfirmed ? 'Baseline reviewed' : 'Baseline review pending'}</span>
        <span className="ui-state" data-tone={draft.inventory_complete?.value === true && inventoryConfirmed ? undefined : 'warning'}>Inventory: {draft.inventory_complete?.value === true ? 'complete' : draft.inventory_complete?.value === false ? 'incomplete' : 'unknown'}</span>
        <span className="ui-state" data-tone={reviewConfirmed ? 'success' : 'warning'}>{reviewConfirmed ? 'Draft review confirmed' : 'Draft review pending'}</span>
      </div>
      <p className="ui-help">These are draft facts and review states. Unknown is not none. Server readiness and Full Auto authorization are separate checks.</p>
      {disabled && <p className="ui-notice" role="status">Profile editing is currently blocked by the active parent operation or unsaved look changes.</p>}
      <nav className="ui-actions automation-section-nav" aria-label="Profile review sections">
        <button type="button" className="ui-button" onClick={() => focusSection(ownershipHeading)}>Review ownership</button>
        <button type="button" className="ui-button" onClick={() => focusSection(coverageHeading)}>Review coverage</button>
        <button type="button" className="ui-button" onClick={() => focusSection(inventoryRef)}>Review inventory</button>
      </nav>
      <fieldset className="automation-section">
        <legend ref={ownershipHeading} tabIndex={-1}>Appearance ownership</legend>
        <p className="ui-help" id={`${helpId}-ownership`}>Select a phrase in the appearance, choose its owner, and assign it. Every Unicode character must have exactly one owner. Use Shift + Arrow keys to select text with the keyboard.</p>
        <label className="ui-field">
          {appearanceLabel}
          <textarea className="ui-control"
            ref={appearanceRef}
            aria-describedby={`${helpId}-ownership`}
            aria-label={appearanceInputLabel}
            value={appearance}
            readOnly
            rows={Math.min(8, Math.max(3, appearance.split('\n').length + 1))}
            onMouseUp={selectAppearanceSpan}
            onKeyUp={selectAppearanceSpan}
            onSelect={selectAppearanceSpan}
          />
        </label>
        {selection && <p className="muted" role="status">Selected phrase: “{selection.text}”</p>}
        <div className="row" style={{ flexWrap: 'wrap' }}>
          <label className="ui-field">
            Selected phrase owner
            <select className="ui-control" aria-label="Selected phrase owner" value={ownerChoice} onChange={(event) => setOwnerChoice(event.target.value)} disabled={disabled}>
              <option value="">Choose an owner…</option>
              {owners.map((owner) => <option key={owner.value} value={owner.value}>{owner.label}</option>)}
            </select>
          </label>
          <button className="ui-button" type="button" onClick={assignSelectedOwner} disabled={disabled || !selection || !ownerChoice}>Assign or correct selected phrase</button>
          <button className="ui-button" type="button" onClick={(event) => { shortcutTrigger.current = event.currentTarget; setPendingShortcut('permanent') }} disabled={disabled}>All appearance is permanent</button>
        </div>
        {!partitionValid && <p className="error" role="status">Ownership is incomplete or refers to a garment that is no longer listed. Assign every part of the appearance before accepting this review.</p>}
        {error && <p className="error ui-notice" data-tone="error" role="alert" ref={errorRef} tabIndex={-1}>{error}</p>}
        {draft.appearance_segments.length > 0 && (
          <ul aria-label="Appearance ownership preview">
            {draft.appearance_segments.map((segment) => (
              <li key={segment.segment_id}>
                “{segment.text}” — {owners.find((owner) => owner.value === segment.owner)?.label || 'Unassigned'}
                <FactProvenance value={segment.provenance} />
              </li>
            ))}
          </ul>
        )}
        <p className="muted" aria-label="Permanent appearance projection preview">
          Joined permanent appearance projection: {permanentProjection ? `“${permanentProjection}”` : 'empty'}
        </p>
        {pendingShortcut === 'permanent' && <ShortcutPreview
          text={`Preview: mark every exact source clause as permanent for this look: “${appearance}”`}
          onConfirm={confirmShortcut} onCancel={() => { setPendingShortcut(''); shortcutTrigger.current?.focus() }}
          disabled={disabled}
        />}
      </fieldset>

      <fieldset className="automation-section">
        <legend ref={coverageHeading} tabIndex={-1}>Supported-region coverage</legend>
        <p className="muted">For each state, record chest, hips, legs and feet as full, partial, none or unknown. Unknown is never treated as none.</p>
          <CoverageEditor title="Baseline · every listed garment absent" fact={draft.baseline_coverage} disabled={disabled} onChange={(region, value) => updateCoverage('baseline', '', region, value)} />
        {garments.map((garment) => (
          <div key={garment.id} className="automation-garment-coverage" role="group" aria-label={`Coverage for ${garment.wording}`}>
            <b>{garment.wording}</b>
            <CoverageEditor title={garment.wornLabel || `${garment.wording} · worn`} fact={draft.garment_coverage?.[garment.id]?.worn} disabled={disabled} onChange={(region, value) => updateCoverage(garment.id, 'worn', region, value)} />
            {garment.aside && <CoverageEditor title={garment.asideLabel || `Moved aside · ${garment.aside}`} fact={draft.garment_coverage?.[garment.id]?.aside} disabled={disabled} onChange={(region, value) => updateCoverage(garment.id, 'aside', region, value)} />}
          </div>
        ))}
        <button className="ui-button" type="button" onClick={(event) => { shortcutTrigger.current = event.currentTarget; setNoneCoverageTargets([]); setPendingShortcut('none') }} disabled={disabled}>No coverage in supported regions</button>
        {pendingShortcut === 'none' && <div aria-label="No-coverage target selection">
          <p className="muted">Choose the exact states for this assertion. Unselected states and inventory confirmation stay unchanged.</p>
          {noneTargetOptions.map((target) => (
            <label key={target.id} className="automation-check">
              <input
                type="checkbox"
                aria-label={`Assert no coverage for ${target.label}`}
                checked={noneCoverageTargets.some((item) => item.id === target.id)}
                onChange={(event) => setNoneCoverageTarget(target, event.target.checked)}
                disabled={disabled}
              />
              {target.label}
            </label>
          ))}
          {noneCoverageTargets.length === 0
            ? <p className="muted" role="status">Choose one or more states to preview this assertion.</p>
            : <ShortcutPreview
              text={`Preview: assert none for chest, hips, legs and feet only in ${noneCoverageTargets.map((target) => target.label).join('; ')}. Inventory completeness stays unchanged.`}
              onConfirm={confirmShortcut} onCancel={() => { setPendingShortcut(''); setNoneCoverageTargets([]); shortcutTrigger.current?.focus() }}
              disabled={disabled}
            />}
        </div>}
        <div className="automation-inventory">
          <label className="ui-field">
            Inventory confirmation
            <select className="ui-control"
              aria-label="Inventory confirmation"
              ref={inventoryRef}
              aria-describedby={`${helpId}-inventory`}
              value={draft.inventory_complete?.value === null || draft.inventory_complete?.value === undefined ? '' : String(draft.inventory_complete.value)}
              disabled={disabled}
              onChange={(event) => {
                const value = event.target.value === '' ? null : event.target.value === 'true'
                changeDraft((next) => ({ ...next, inventory_complete: {
                  value, ...(provenance ? { provenance } : {}),
                } }))
                onInventoryConfirmed(value !== null)
              }}
            >
              <option value="">Choose explicitly…</option>
              <option value="true">I confirm the listed garment inventory is complete</option>
              <option value="false">The listed garment inventory is incomplete</option>
            </select>
          </label>
          <p className="ui-help" id={`${helpId}-inventory`}>Confirm completeness explicitly. Marking an inventory incomplete keeps that distinction; it does not authorize Full Auto.</p>
          <FactProvenance value={draft.inventory_complete?.provenance} />
          {!inventoryConfirmed && <p className="error" role="status">Explicit inventory confirmation is required before accepting this review.</p>}
        </div>
        {!baselineConfirmed && <p className="error" role="status">Review the inventory-absent baseline before accepting this review.</p>}
        {showReviewConfirmation && <label className="automation-check">
          <input type="checkbox" checked={reviewConfirmed} onChange={(event) => onReviewConfirmed(event.target.checked)} disabled={disabled || !partitionValid || !baselineConfirmed || !inventoryConfirmed} />
          {reviewLabel}
        </label>}
      </fieldset>
    </div>
  )
}

function CoverageEditor({ title, fact, disabled = false, onChange }) {
  return (
    <fieldset className="automation-coverage" aria-label={`${title} coverage`}>
      <legend>{title}</legend>
      <p className="ui-help">{REGIONS.filter((region) => !fact?.regions?.[region] || fact.regions[region] === 'unknown').length} unknown region(s) · review each value explicitly.</p>
      <div className="automation-coverage-grid">
        {REGIONS.map((region) => (
          <label key={region}>
            {region[0].toUpperCase() + region.slice(1)}
            <select className="ui-control" aria-label={`${title} ${region}`} value={fact?.regions?.[region] || 'unknown'} onChange={(event) => onChange(region, event.target.value)} disabled={disabled}>
              {COVERAGE.map((coverage) => <option key={coverage} value={coverage}>{coverage}</option>)}
            </select>
          </label>
        ))}
      </div>
      <FactProvenance value={fact?.provenance} />
    </fieldset>
  )
}

function ShortcutPreview({ text, onConfirm, onCancel, disabled = false }) {
  return (
    <div className="panel automation-shortcut" aria-label="Profile shortcut preview">
      <p>{text}</p>
      <div className="row">
        <button className="ui-button" type="button" onClick={onConfirm} disabled={disabled}>Confirm this user assertion</button>
        <button className="ui-button" type="button" onClick={onCancel} disabled={disabled}>Cancel preview</button>
      </div>
    </div>
  )
}

function FactProvenance({ value }) {
  return value ? <details className="automation-provenance">
    <summary>Recorded provenance</summary>
    <pre>{JSON.stringify(value, null, 2)}</pre>
  </details> : <p className="ui-help">No recorded provenance for this draft fact.</p>
}
