import React, { useEffect, useRef, useState } from 'react'
import { api, parseVersionJson, versionText } from '../api.js'
import AutomationProfileReview from '../components/AutomationProfileReview.jsx'
import {
  draftContentDigest,
  draftFromProspectiveSuggestion,
  emptyAutomationDraft,
  newLookRowId,
  prospectiveProfile,
  prospectiveSource,
} from '../looksAutomationReview.js'
import AutomationProfileEditor from './AutomationProfileEditor.jsx'

const combinedSaveEvent = 'idevgen:combined-look-save'
let pendingCombinedSave = null

function announceCombinedSave(outcome = '') {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(combinedSaveEvent, { detail: { outcome } }))
  }
}

function retainCombinedSave(attempt, inFlight) {
  pendingCombinedSave = { attempt, inFlight }
  announceCombinedSave()
}

function releaseCombinedSave(attempt, outcome = '') {
  if (pendingCombinedSave?.attempt !== attempt) return false
  pendingCombinedSave = null
  announceCombinedSave(outcome)
  return true
}

async function postRawJson(path, body, action) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
  })
  if (!response.ok) {
    let detail = response.statusText
    try { detail = (await response.json()).detail ?? detail } catch { /* response was not JSON */ }
    throw Object.assign(new Error(
      typeof detail === 'string' ? detail : (detail?.message || JSON.stringify(detail))
        || `Could not ${action}.`,
    ), { status: response.status, detail })
  }
  return parseVersionJson(await response.text())
}

function boundedVersionOptions(latest, selected) {
  const options = []
  if (latest) {
    for (let version = 1; version <= 20; version += 1) {
      const text = String(version)
      if (latest.length > text.length || (latest.length === text.length && latest >= text)) options.push(text)
    }
    options.push(latest)
  }
  if (selected && !options.includes(selected)) options.push(selected)
  return [...new Set(options)]
}

async function getPortableExportBlob(path) {
  const response = await fetch(path)
  if (!response.ok) {
    let detail = response.statusText
    try { detail = (await response.json()).detail ?? detail } catch { /* response was not JSON */ }
    throw Object.assign(new Error(
      typeof detail === 'string' ? detail : (detail?.message || JSON.stringify(detail)) || 'Could not export this look version.',
    ), { status: response.status, detail })
  }
  return response.blob()
}

const blankForm = () => ({
  name: '',
  appearance: '',
  outfitMode: 'none',
  outfitKey: '',
  garments: [],
})

const listKeys = (value) => (Array.isArray(value) ? value : String(value || '').split(','))
  .map((key) => String(key).trim())
  .filter(Boolean)

const rowFromGarment = (garment) => ({
  id: newLookRowId(),
  key: garment?.key || '',
  sourceWording: garment?.wording || '',
  sourceAside: garment?.aside || '',
  wording: garment?.wording || '',
  aside: garment?.aside || '',
})

function rowsForOutfit(outfit, wardrobe) {
  if (Array.isArray(outfit?.garments) && outfit.garments.every((item) => typeof item === 'object')) {
    return outfit.garments.map(rowFromGarment)
  }
  const byKey = new Map((wardrobe?.garments || []).map((garment) => [garment.key, garment]))
  return listKeys(outfit?.garments).map((key) => byKey.get(key)).filter(Boolean).map(rowFromGarment)
}

function formForLook(look, wardrobe) {
  return {
    name: look?.name || '',
    appearance: look?.appearance || '',
    outfitMode: look?.outfit ? 'catalogue' : 'none',
    outfitKey: look?.outfit?.outfit_key || '',
    garments: rowsForOutfit(look?.outfit, wardrobe),
  }
}

function visibleName(item, fallback) {
  const name = String(item?.name || item?.display_name || item?.label || '').trim()
  return name && name !== item?.key ? name : fallback
}

function wardrobeRows(outfit, wardrobe) {
  const rows = rowsForOutfit(outfit, wardrobe)
  return rows.map((row) => row.wording).filter(Boolean)
}

function outfitLabel(outfit, wardrobe) {
  return visibleName(outfit, wardrobeRows(outfit, wardrobe).join(' → ') || 'Saved outfit')
}

function errorText(error, action) {
  const message = error?.message || `Could not ${action}.`
  return error?.status === 503
    ? `Resource planning is disabled. ${message}`
    : message
}

function editableGarmentPayload(row) {
  if (row.key && row.wording === row.sourceWording && row.aside === row.sourceAside) {
    return { key: row.key }
  }
  return { wording: row.wording.trim(), aside: row.aside.trim() }
}

function reviewedRows(form) {
  if (!form || form.outfitMode === 'none') return []
  if (form.outfitMode === 'catalogue') return form.garments
  return form.garments.map((row) => ({
    ...row,
    key: row.key && row.wording === row.sourceWording && row.aside === row.sourceAside ? row.key : '',
  }))
}

function lookPayloadForForm(form) {
  const body = { name: form.name.trim(), appearance: form.appearance }
  if (form.outfitMode === 'catalogue' && form.outfitKey) body.outfit_key = form.outfitKey
  else if (form.outfitMode === 'garments') body.garments = form.garments.map(editableGarmentPayload)
  else body.garments = []
  return body
}

function requestUuid() {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (token) => {
    const random = Math.floor(Math.random() * 16)
    return (token === 'x' ? random : (random & 0x3) | 0x8).toString(16)
  })
}

function freezeRequestValue(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value).forEach(freezeRequestValue)
    Object.freeze(value)
  }
  return value
}

const preWriteCodes = new Set([
  'automation_review_required', 'automation_profile_not_ready', 'automation_proposal_stale',
  'combined_save_incomplete', 'idempotency_conflict', 'invalid_look', 'look_archived',
  'look_data_invalid', 'look_not_found', 'look_version_limit', 'look_version_stale', 'look_write_conflict',
  'automation_proposal_expired',
  'photo_proposal_stale', 'photo_review_required', 'photo_stage_expired',
  'photo_stage_not_found', 'photo_stage_not_saved', 'photo_stage_state_changed',
])

function isKnownPreWriteRefusal(error) {
  return Number.isInteger(error?.status) && error.status >= 400 && error.status < 500
    && error.hasStableErrorBody === true
    && preWriteCodes.has(error.detail?.code)
}

function responseOutfitMatchesAttempt(outfit, expected) {
  if (expected.outfitKey == null && expected.garments.length === 0) return outfit === null
  if (!outfit || typeof outfit !== 'object' || !Array.isArray(outfit.garments)) return false
  if (expected.outfitKey != null && outfit.outfit_key !== expected.outfitKey) return false
  if (outfit.garments.length !== expected.garments.length) return false
  return expected.garments.every((garment, index) => {
    const saved = outfit.garments[index]
    return saved && typeof saved.key === 'string'
      && (garment.key == null || saved.key === garment.key)
      && saved.wording === garment.wording
      && saved.aside === garment.aside
  })
}

function combinedSuccessIsExact(result, attempt) {
  const savedVersion = versionText(result?.version)
  const profile = result?.automation_profile
  const expected = attempt?.expected
  let expectedVersion = '1'
  if (expected?.expectedVersion != null) {
    try {
      expectedVersion = (BigInt(expected.expectedVersion) + 1n).toString()
    } catch {
      return null
    }
  }
  const identityMatches = expected && typeof result?.key === 'string' && result.key.length > 0
    && (expected.key == null || result.key === expected.key)
    && savedVersion === expectedVersion
    && result.name === expected.name
    && result.appearance === expected.appearance
    && responseOutfitMatchesAttempt(result.outfit, expected)
  return identityMatches && Boolean(savedVersion)
    && typeof result.content_digest === 'string' && /^[0-9a-f]{64}$/.test(result.content_digest)
    && profile?.look_key === result.key
    && versionText(profile.version) === savedVersion
    && profile.content_digest === result.content_digest
    && versionText(profile.profile_revision) === '1'
    && typeof profile.profile_digest === 'string' && /^[0-9a-f]{64}$/.test(profile.profile_digest)
    && profile.readiness?.profile_ready === true
  ? savedVersion
  : null
}

function validateAutomationCandidate(profile, appearance, rows, expectedDigest) {
  const expectedProfileFields = [
    'appearance_segments', 'baseline_coverage', 'draft_content_digest',
    'garment_coverage', 'inventory_complete', 'schema_version',
  ]
  if (!profile || Object.keys(profile).sort().join(',') !== expectedProfileFields.join(',')
    || profile.schema_version !== 1 || profile.draft_content_digest !== expectedDigest
    || profile.baseline_coverage !== null || profile.inventory_complete !== null
    || !Array.isArray(profile.appearance_segments) || !Array.isArray(profile.garment_coverage)) {
    throw new Error('The server returned an invalid automation profile proposal.')
  }
  const scalars = Array.from(appearance)
  const seenSegmentIds = new Set()
  let offset = 0
  for (const segment of profile.appearance_segments) {
    if (!segment || Object.keys(segment).sort().join(',') !== 'end,owner,segment_id,start,text'
      || typeof segment.segment_id !== 'string' || !segment.segment_id
      || seenSegmentIds.has(segment.segment_id)
      || !Number.isInteger(segment.start) || !Number.isInteger(segment.end)
      || segment.start !== offset || segment.end <= segment.start || segment.end > scalars.length
      || segment.text !== scalars.slice(segment.start, segment.end).join('')) {
      throw new Error('The automation proposal does not match the exact appearance text.')
    }
    const owner = segment.owner
    if (owner !== null && owner !== 'permanent'
      && (!owner || typeof owner !== 'object' || Array.isArray(owner)
        || Object.keys(owner).join(',') !== 'garment_index'
        || !Number.isInteger(owner.garment_index) || owner.garment_index < 0
        || owner.garment_index >= rows.length)) {
      throw new Error('The automation proposal refers to an invalid garment row.')
    }
    seenSegmentIds.add(segment.segment_id)
    offset = segment.end
  }
  if (offset !== scalars.length) throw new Error('The automation proposal leaves appearance text unreviewed.')
  const seenIndices = new Set()
  for (const entry of profile.garment_coverage) {
    if (!entry || Object.keys(entry).sort().join(',') !== 'garment_index,states'
      || !Number.isInteger(entry.garment_index) || entry.garment_index < 0
      || entry.garment_index >= rows.length || seenIndices.has(entry.garment_index)
      || !entry.states || typeof entry.states !== 'object' || Array.isArray(entry.states)
      || Object.keys(entry.states).some((state) => !['worn', 'aside'].includes(state))) {
      throw new Error('The automation proposal contains an invalid garment mapping.')
    }
    for (const [state, fact] of Object.entries(entry.states)) {
      if ((state === 'aside' && !rows[entry.garment_index].aside)
        || !fact || Object.keys(fact).join(',') !== 'regions'
        || !fact.regions || typeof fact.regions !== 'object' || Array.isArray(fact.regions)
        || Object.keys(fact.regions).sort().join(',') !== 'chest,feet,hips,legs'
        || Object.values(fact.regions).some((value) => !['unknown', 'full', 'partial', 'none'].includes(value))) {
        throw new Error('The automation proposal contains an invalid coverage state.')
      }
    }
    seenIndices.add(entry.garment_index)
  }
}

function automationReviewBlockers(draft, appearance, garments, confirmations, photo) {
  const blockers = []
  if (!draft) return ['Automation review is not available for this draft.']
  const owners = new Set(['permanent', ...garments.map((garment) => `garment:${garment.id}`)])
  let offset = 0
  for (const segment of draft.appearance_segments) {
    if (segment.start !== offset || segment.end <= segment.start
      || segment.text !== Array.from(appearance).slice(segment.start, segment.end).join('')
      || !owners.has(segment.owner)) {
      blockers.push('Assign every exact appearance span to permanent appearance or a listed garment.')
      break
    }
    offset = segment.end
  }
  if (offset !== Array.from(appearance).length
    && !blockers.some((item) => item.startsWith('Assign every exact appearance'))) {
    blockers.push('Assign every exact appearance span to permanent appearance or a listed garment.')
  }
  const unknown = (fact) => ['chest', 'hips', 'legs', 'feet'].filter((region) => (
    !['full', 'partial', 'none'].includes(fact?.regions?.[region])
  ))
  const missingBaseline = unknown(draft.baseline_coverage)
  if (missingBaseline.length) blockers.push(`Baseline coverage needs a known value for: ${missingBaseline.join(', ')}.`)
  garments.forEach((garment, index) => {
    const states = draft.garment_coverage?.[garment.id]
    const worn = unknown(states?.worn)
    if (worn.length) blockers.push(`Garment ${index + 1} worn coverage needs a known value for: ${worn.join(', ')}.`)
    if (garment.aside) {
      const aside = unknown(states?.aside)
      if (aside.length) blockers.push(`Garment ${index + 1} moved-aside coverage needs a known value for: ${aside.join(', ')}.`)
    }
  })
  if (draft.inventory_complete?.value == null) blockers.push('Choose explicitly whether the listed garment inventory is complete.')
  else if (draft.inventory_complete.value !== true) blockers.push('The inventory is marked incomplete; add the missing items or choose look-only save.')
  if (!confirmations.inventory) blockers.push('Confirm the inventory answer explicitly.')
  if (!confirmations.baseline) blockers.push('Review the inventory-absent baseline explicitly.')
  if (!confirmations.review) blockers.push('Confirm the complete appearance and automation profile review.')
  if (!confirmations.order) blockers.push('Confirm the complete garment list and removal order.')
  if (photo && !photo.resolutions) blockers.push('Resolve or omit every uncertain photo detail and include each correction in the look.')
  if (photo && !photo.review) blockers.push('Confirm that the photo appearance and uncertainty corrections were reviewed.')
  if (photo && !photo.order) blockers.push('Confirm the photo proposal garment list and removal order.')
  return blockers
}

function importChoiceLabel(choice, option) {
  if (choice === 'new_version') {
    return `Add as a new version${option?.destination?.version ? ` (version ${option.destination.version})` : ''}`
  }
  if (choice === 'save_copy') return 'Save as a separate look or outfit copy'
  return 'Import the reviewed look or outfit'
}

function photoReviewPayload(decisions) {
  return decisions.map(({ id, action, correction }) => action === 'correct'
    ? { id, action, correction: correction.trim() }
    : { id, action })
}

export default function Looks() {
  const [looks, setLooks] = useState([])
  const [wardrobe, setWardrobe] = useState({ garments: [], outfits: [] })
  const [selectedKey, setSelectedKey] = useState('')
  const [selectedVersion, setSelectedVersion] = useState(0)
  const [versionChoice, setVersionChoice] = useState('')
  const [currentLook, setCurrentLook] = useState(null)
  const [form, setForm] = useState(null)
  const [automationDraft, setAutomationDraft] = useState(null)
  const [isNew, setIsNew] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [looksLoading, setLooksLoading] = useState(true)
  const [archivingKey, setArchivingKey] = useState('')
  const [wardrobeLoading, setWardrobeLoading] = useState(true)
  const [lookLoading, setLookLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [looksError, setLooksError] = useState('')
  const [looksNotice, setLooksNotice] = useState('')
  const [wardrobeError, setWardrobeError] = useState('')
  const [lookError, setLookError] = useState('')
  const [saveError, setSaveError] = useState('')
  const [notice, setNotice] = useState('')
  const [newGarmentKey, setNewGarmentKey] = useState('')
  const [importBusy, setImportBusy] = useState(false)
  const [importError, setImportError] = useState('')
  const [importNotice, setImportNotice] = useState('')
  const [importPreview, setImportPreview] = useState(null)
  const [importChoice, setImportChoice] = useState('')
  const [legacyOutfits, setLegacyOutfits] = useState([])
  const [exportBusy, setExportBusy] = useState(false)
  const [photoStage, setPhotoStage] = useState(null)
  const [photoBusy, setPhotoBusy] = useState(false)
  const [photoError, setPhotoError] = useState('')
  const [photoNotice, setPhotoNotice] = useState('')
  const [photoMode, setPhotoMode] = useState('')
  const [photoProposal, setPhotoProposal] = useState(null)
  const [photoDecisions, setPhotoDecisions] = useState([])
  const [photoReviewConfirmed, setPhotoReviewConfirmed] = useState(false)
  const [photoOrderConfirmed, setPhotoOrderConfirmed] = useState(false)
  const [lookOrderConfirmed, setLookOrderConfirmed] = useState(false)
  const [automationInventoryConfirmed, setAutomationInventoryConfirmed] = useState(false)
  const [automationBaselineConfirmed, setAutomationBaselineConfirmed] = useState(false)
  const [automationReviewConfirmed, setAutomationReviewConfirmed] = useState(false)
  const [automationProposalBusy, setAutomationProposalBusy] = useState(false)
  const [automationProposalError, setAutomationProposalError] = useState('')
  const [automationProposalNotice, setAutomationProposalNotice] = useState('')
  const [profileSuggestion, setProfileSuggestion] = useState(null)
  const [includePhotoAutomation, setIncludePhotoAutomation] = useState(false)
  const [uncertainSave, setUncertainSave] = useState(false)
  const [combinedSaveInFlight, setCombinedSaveInFlight] = useState(false)
  const [cleanupStage, setCleanupStage] = useState(null)
  const [visionAvailability, setVisionAvailability] = useState('checking')
  const [visionError, setVisionError] = useState('')
  const jsonInput = useRef(null)
  const photoInput = useRef(null)
  const savingRef = useRef(false)
  const importRequest = useRef(0)
  const importCommitLock = useRef(false)
  const exportRequest = useRef(0)
  const photoRequest = useRef(0)
  const photoStageRef = useRef(null)
  const photoBusyRef = useRef(false)
  const automationProposalRequest = useRef(0)
  const automationSourceEpoch = useRef(0)
  const automationDraftEpoch = useRef(0)
  const combinedSaveRef = useRef(null)
  const visionRequest = useRef(0)
  const looksRequest = useRef(0)
  const wardrobeRequest = useRef(0)
  const detailRequest = useRef(0)
  const mountedRef = useRef(false)
  const selectedSummary = looks.find((item) => item.key === selectedKey)

  const resetAutomationDraft = (nextForm, suggestedProfile = null) => {
    automationSourceEpoch.current += 1
    automationDraftEpoch.current += 1
    automationProposalRequest.current += 1
    setAutomationProposalBusy(false)
    setAutomationProposalError('')
    setAutomationProposalNotice('')
    setProfileSuggestion(null)
    setAutomationInventoryConfirmed(false)
    setAutomationBaselineConfirmed(false)
    setAutomationReviewConfirmed(false)
    setLookOrderConfirmed(false)
    if (!nextForm) {
      setAutomationDraft(null)
      return []
    }
    const rows = reviewedRows(nextForm)
    try {
      setAutomationDraft(suggestedProfile
        ? draftFromProspectiveSuggestion(suggestedProfile, rows)
        : emptyAutomationDraft(nextForm.appearance, rows))
    } catch (error) {
      setAutomationDraft(emptyAutomationDraft(nextForm.appearance, rows))
      setAutomationProposalNotice(errorText(error, 'apply the optional automation proposal'))
    }
    return rows
  }

  const loadLooks = async () => {
    const request = ++looksRequest.current
    setLooksLoading(true)
    setLooksError('')
    setLooksNotice('')
    try {
      const result = await api.getVersioned('/api/looks')
      if (request !== looksRequest.current) return
      setLooks(Array.isArray(result) ? result : [])
    } catch (error) {
      if (request === looksRequest.current) setLooksError(errorText(error, 'load saved looks'))
    } finally {
      if (request === looksRequest.current) setLooksLoading(false)
    }
  }

  const loadWardrobe = async () => {
    const request = ++wardrobeRequest.current
    setWardrobeLoading(true)
    setWardrobeError('')
    try {
      const result = await api.get('/api/wardrobe')
      if (request !== wardrobeRequest.current) return
      setWardrobe({
        garments: Array.isArray(result?.garments) ? result.garments : [],
        outfits: Array.isArray(result?.outfits) ? result.outfits : [],
      })
      return result
    } catch (error) {
      if (request === wardrobeRequest.current) setWardrobeError(errorText(error, 'load wardrobe items'))
      return null
    } finally {
      if (request === wardrobeRequest.current) setWardrobeLoading(false)
    }
  }

  const setCurrentPhotoStage = (stage) => {
    photoStageRef.current = stage
    setPhotoStage(stage)
    setCleanupStage((current) => {
      if (stage?.cleanup_warning) return stage
      return current?.photo_id === stage?.photo_id ? null : current
    })
  }

  const loadVisionAvailability = async () => {
    const request = ++visionRequest.current
    setVisionAvailability('checking')
    setVisionError('')
    try {
      const config = await api.get('/api/config')
      if (request !== visionRequest.current) return
      setVisionAvailability(config?.llm_ok
        && typeof config.llm_vision_model === 'string'
        && Boolean(config.llm_vision_model.trim()) ? 'available' : 'unavailable')
    } catch (error) {
      if (request === visionRequest.current) {
        setVisionAvailability('unknown')
        setVisionError(errorText(error, 'check vision availability'))
      }
    }
  }

  useEffect(() => {
    mountedRef.current = true
    const syncPendingCombinedSave = (event) => {
      if (!pendingCombinedSave) {
        combinedSaveRef.current = null
        setUncertainSave(false)
        setCombinedSaveInFlight(false)
        if (event?.detail?.outcome === 'success-offscreen') {
          void loadLooks().then(() => {
            if (mountedRef.current) {
              setLooksNotice('A combined save completed after its editor closed. The saved-look list was refreshed.')
            }
          })
        }
        return
      }
      combinedSaveRef.current = pendingCombinedSave.attempt
      setUncertainSave(true)
      setCombinedSaveInFlight(pendingCombinedSave.inFlight)
    }
    syncPendingCombinedSave()
    window.addEventListener(combinedSaveEvent, syncPendingCombinedSave)
    loadLooks()
    loadWardrobe()
    loadVisionAvailability()
    return () => {
      mountedRef.current = false
      window.removeEventListener(combinedSaveEvent, syncPendingCombinedSave)
      looksRequest.current += 1
      wardrobeRequest.current += 1
      detailRequest.current += 1
      importRequest.current += 1
      exportRequest.current += 1
      photoRequest.current += 1
      automationProposalRequest.current += 1
      visionRequest.current += 1
    }
  }, [])

  const openVersion = async (key, version) => {
    const exactVersion = versionText(version)
    if (!exactVersion) {
      setLookError('Enter a saved look version between 1 and the supported maximum.')
      return
    }
    const request = ++detailRequest.current
    setSelectedKey(key)
    setSelectedVersion(exactVersion)
    setVersionChoice(exactVersion)
    setCurrentLook(null)
    setForm(null)
    resetAutomationDraft(null)
    setIsNew(false)
    setDirty(false)
    setNotice('')
    setLookError('')
    setSaveError('')
    setLookLoading(true)
    try {
      const path = `/api/looks/${encodeURIComponent(key)}/versions/${exactVersion}`
      const result = await api.getVersioned(path)
      if (request !== detailRequest.current) return
      if (versionText(result?.version) !== exactVersion) {
        throw new Error('The server returned a different saved look version.')
      }
      setCurrentLook(result)
      const nextForm = formForLook(result, wardrobe)
      setForm(nextForm)
      resetAutomationDraft(nextForm)
    } catch (error) {
      if (request === detailRequest.current) setLookError(errorText(error, 'load this look version'))
    } finally {
      if (request === detailRequest.current) setLookLoading(false)
    }
  }

  const startNew = () => {
    detailRequest.current += 1
    setSelectedKey('')
    setSelectedVersion(0)
    setVersionChoice('')
    setCurrentLook(null)
    const nextForm = blankForm()
    setForm(nextForm)
    resetAutomationDraft(nextForm)
    setIsNew(true)
    setDirty(false)
    setLookError('')
    setSaveError('')
    setNotice('')
    setPhotoMode('')
    setPhotoProposal(null)
    setPhotoDecisions([])
  }

  const archiveLook = async (item) => {
    if (navigationBlocked || !window.confirm(
      `Archive "${item.name}" from the active library? Its versions and session history will be kept.`,
    )) return
    setLooksError('')
    setLooksNotice('')
    setArchivingKey(item.key)
    try {
      const result = await api.del(`/api/looks/${encodeURIComponent(item.key)}`)
      if (result?.key !== item.key || result?.archived !== true) {
        throw new Error('The server did not confirm archiving this look.')
      }
      looksRequest.current += 1
      setLooksLoading(false)
      setLooks((current) => current.filter((look) => look.key !== item.key))
      setLooksNotice(`Archived ${item.name}. Its versions and session history are retained.`)
      if (selectedKey === item.key) {
        detailRequest.current += 1
        setSelectedKey('')
        setSelectedVersion(0)
        setVersionChoice('')
        setCurrentLook(null)
        setForm(null)
        resetAutomationDraft(null)
        setIsNew(false)
        setDirty(false)
        setLookLoading(false)
        setLookError('')
        setSaveError('')
        setNotice('')
      }
    } catch (error) {
      setLooksError(errorText(error, 'archive this look'))
    } finally {
      setArchivingKey('')
    }
  }

  const cancelEdits = () => {
    if (photoMode === 'extracted') {
      void cancelPhotoStage()
      return
    }
    if (photoMode) {
      if (!window.confirm('Discard this photo description? The staged photo will remain available.')) return
      detailRequest.current += 1
      setSelectedKey('')
      setSelectedVersion(0)
      setCurrentLook(null)
      setForm(null)
      resetAutomationDraft(null)
      setIsNew(false)
      setDirty(false)
      setPhotoMode('')
      setPhotoProposal(null)
      setPhotoDecisions([])
      setPhotoReviewConfirmed(false)
      setPhotoOrderConfirmed(false)
      setLookOrderConfirmed(false)
      setSaveError('')
      setNotice('')
      return
    }
    if (!isNew && currentLook) {
      const nextForm = formForLook(currentLook, wardrobe)
      setForm(nextForm)
      resetAutomationDraft(nextForm)
      setDirty(false)
      setSaveError('')
      setNotice('')
      return
    }
    detailRequest.current += 1
    setSelectedKey('')
    setSelectedVersion(0)
    setCurrentLook(null)
    setForm(null)
    resetAutomationDraft(null)
    setIsNew(false)
    setDirty(false)
    setSaveError('')
  }

  const updateForm = (patch) => {
    setForm((current) => ({ ...current, ...patch }))
    setDirty(true)
    setSaveError('')
    setNotice('')
    const changesSource = ['appearance', 'outfitMode', 'outfitKey', 'garments']
      .some((field) => Object.hasOwn(patch, field))
    if (changesSource) {
      automationSourceEpoch.current += 1
      automationProposalRequest.current += 1
      setAutomationProposalBusy(false)
      setAutomationProposalError('')
      setAutomationReviewConfirmed(false)
      setLookOrderConfirmed(false)
      if (['outfitMode', 'outfitKey', 'garments'].some((field) => Object.hasOwn(patch, field))) {
        setAutomationInventoryConfirmed(false)
        setAutomationBaselineConfirmed(false)
      }
      if (Object.hasOwn(patch, 'appearance')) {
        const length = Array.from(patch.appearance).length
        setAutomationDraft((current) => current && ({
          ...current,
          appearance_segments: length ? [{
            segment_id: `unassigned-${newLookRowId()}`,
            start: 0,
            end: length,
            text: patch.appearance,
            owner: null,
          }] : [],
        }))
      }
    }
    if (changesSource && photoMode === 'extracted') {
      setPhotoReviewConfirmed(false)
      setPhotoOrderConfirmed(false)
    }
  }

  const updateGarment = (index, patch) => {
    setForm((current) => ({
      ...current,
      garments: current.garments.map((row, rowIndex) => rowIndex === index
        ? { ...row, ...patch }
        : row),
    }))
    setDirty(true)
    setSaveError('')
    setNotice('')
    automationSourceEpoch.current += 1
    automationProposalRequest.current += 1
    setAutomationProposalBusy(false)
    setAutomationProposalError('')
    setAutomationReviewConfirmed(false)
    setLookOrderConfirmed(false)
    setAutomationInventoryConfirmed(false)
    setAutomationBaselineConfirmed(false)
    if (photoMode === 'extracted') {
      setPhotoReviewConfirmed(false)
      setPhotoOrderConfirmed(false)
    }
  }

  const updatePhotoDecision = (id, patch) => {
    setPhotoDecisions((current) => current.map((item) => item.id === id ? { ...item, ...patch } : item))
    setPhotoReviewConfirmed(false)
  }

  const requestAutomationProposal = async () => {
    if (!form || automationProposalBusy || saving || uncertainSave) return
    const rows = reviewedRows(form)
    if (rows.some((row) => !row.wording.trim())) {
      setAutomationProposalError('Add worn wording for each garment before requesting suggestions.')
      return
    }
    const request = ++automationProposalRequest.current
    const sourceEpoch = automationSourceEpoch.current
    const draftEpoch = automationDraftEpoch.current
    setAutomationProposalBusy(true)
    setAutomationProposalError('')
    setAutomationProposalNotice('')
    try {
      const source = prospectiveSource(form.appearance, rows)
      const digest = await draftContentDigest(source)
      if (request !== automationProposalRequest.current || sourceEpoch !== automationSourceEpoch.current) return
      const result = await api.post('/api/looks/automation-proposals', source)
      if (request !== automationProposalRequest.current || sourceEpoch !== automationSourceEpoch.current) return
      if (!/^[0-9a-f]{32}$/.test(result?.proposal_id || '')
        || result.draft_content_digest !== digest) {
        throw new Error('The server returned an invalid automation suggestion identity.')
      }
      validateAutomationCandidate(result.profile, source.appearance, source.garments, digest)
      if (draftEpoch !== automationDraftEpoch.current) {
        setAutomationProposalNotice('Your profile edits were kept while suggestions were pending. Request suggestions again to apply a fresh proposal.')
        return
      }
      const suggestion = draftFromProspectiveSuggestion(result.profile, rows)
      setAutomationDraft((current) => ({
        ...suggestion,
        baseline_coverage: current?.baseline_coverage || null,
        inventory_complete: current?.inventory_complete || { value: null },
      }))
      setProfileSuggestion({ proposal_id: result.proposal_id, draft_content_digest: digest })
      setAutomationInventoryConfirmed(automationDraft?.inventory_complete?.value != null)
      setAutomationBaselineConfirmed(Boolean(automationDraft?.baseline_coverage))
      setAutomationReviewConfirmed(false)
      setAutomationProposalNotice('Assistant suggestions are unaccepted. Review every fact and confirm the baseline, inventory, ownership and coverage yourself.')
    } catch (error) {
      if (request === automationProposalRequest.current && sourceEpoch === automationSourceEpoch.current) {
        setAutomationProposalError(errorText(error, 'request automation suggestions'))
      }
    } finally {
      if (request === automationProposalRequest.current) setAutomationProposalBusy(false)
    }
  }

  const handleJsonFile = async (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setImportPreview(null)
    setImportChoice('')
    setImportNotice('')
    setImportError('')
    if (file.size > 10 * 1024 * 1024) {
      setImportError('The JSON file exceeds the 10 MiB import limit.')
      return
    }
    const request = ++importRequest.current
    setImportBusy(true)
    try {
      const envelope = await file.text()
      if (request !== importRequest.current) return
      const plan = await postRawJson('/api/looks/import/preview', envelope, 'preview this JSON import')
      if (request !== importRequest.current) return
      const parsed = JSON.parse(envelope)
      const kind = Object.hasOwn(parsed, 'schema_version') ? 'portable' : 'legacy'
      setImportPreview({ envelope, kind, plan, committed: false, result: null })
      setImportChoice(plan.mode === 'choice_required' ? '' : (plan.choices?.[0] || 'import'))
    } catch (error) {
      if (request === importRequest.current) setImportError(errorText(error, 'preview this JSON import'))
    } finally {
      if (request === importRequest.current) setImportBusy(false)
    }
  }

  const commitImport = async () => {
    if (!importPreview || importPreview.committed || importBusy || importCommitLock.current) return
    const { plan, envelope, kind } = importPreview
    const choices = Array.isArray(plan.choices) ? plan.choices : []
    const choice = importChoice || (plan.mode === 'choice_required' ? '' : choices[0])
    const allowed = choices.some((option) => (typeof option === 'string' ? option : option?.choice) === choice)
    if (!allowed) {
      setImportError('Choose one of the reviewed import options before committing.')
      return
    }

    importCommitLock.current = true
    setImportBusy(true)
    setImportError('')
    setImportNotice('')
    const body = `{"envelope":${envelope},"preview_token":${JSON.stringify(plan.preview_token)},"review_digest":${JSON.stringify(plan.review_digest)},"choice":${JSON.stringify(choice)}}`
    try {
      const result = await postRawJson('/api/looks/import/commit', body, 'commit this JSON import')
      setImportPreview((current) => current?.envelope === envelope
        ? { ...current, committed: true, result }
        : current)
      if (kind === 'legacy') {
        const definitions = new Map((result.garments || []).map((item) => [item.key, item]))
        setLegacyOutfits((result.outfits || []).map((outfit) => ({
          ...outfit,
          garments: listKeys(outfit.garments).map((key) => definitions.get(key)).filter(Boolean),
        })))
        setImportNotice(`Imported ${result.outfits?.length || 0} outfit(s). Choose an outfit below to start a named look; no saved look was created by import.`)
      } else {
        setImportNotice(`Imported ${result.look?.name || 'the reviewed look'} as version ${result.look?.version}. Refresh saved looks when ready to view it; your open editor remains unchanged.`)
      }
      void loadWardrobe()
    } catch (error) {
      setImportError(errorText(error, 'commit this JSON import'))
    } finally {
      importCommitLock.current = false
      setImportBusy(false)
    }
  }

  const exportSelectedVersion = async () => {
    const exactVersion = versionText(selectedVersion)
    if (!selectedKey || !exactVersion || exportBusy) return
    const request = ++exportRequest.current
    setExportBusy(true)
    setSaveError('')
    setNotice('')
    try {
      const blob = await getPortableExportBlob(
        `/api/looks/${encodeURIComponent(selectedKey)}/versions/${exactVersion}/export`,
      )
      if (request !== exportRequest.current) return
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      const name = String(currentLook?.name || selectedSummary?.name || 'saved-look')
        .normalize('NFKD').replace(/[\u0300-\u036f]/g, '').replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '')
      link.href = url
      link.download = `${name || 'saved-look'}-v${exactVersion}.json`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      setNotice(`Exported version ${exactVersion} as portable JSON.`)
    } catch (error) {
      if (request === exportRequest.current) setSaveError(errorText(error, 'export this look version'))
    } finally {
      if (request === exportRequest.current) setExportBusy(false)
    }
  }

  const handlePhotoFile = async (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (photoStageRef.current?.state === 'staged') {
      setPhotoError('Cancel the current staged photo before choosing another one.')
      return
    }
    setPhotoError('')
    setPhotoNotice('')
    if (file.size > 10 * 1024 * 1024) {
      setPhotoError('The photo exceeds the 10 MiB file limit.')
      return
    }
    if (photoBusyRef.current) return
    const discardDraft = Boolean(photoMode && dirty)
    if (discardDraft && !window.confirm('Discard the unsaved photo look draft and choose another photo?')) return
    const request = ++photoRequest.current
    photoBusyRef.current = true
    setPhotoBusy(true)
    try {
      const body = new FormData()
      body.append('file', file)
      const stage = await api.uploadMultipart('/api/looks/photo-stages', body)
      if (request !== photoRequest.current) {
        if (stage?.photo_id) void api.post(`/api/looks/photo-stages/${stage.photo_id}/cancel`).catch(() => {})
        return
      }
      if (typeof stage?.photo_id !== 'string' || stage.state !== 'staged') {
        throw new Error('The server returned an invalid photo stage.')
      }
      if (discardDraft) {
        setForm(null)
        setCurrentLook(null)
        setSelectedKey('')
        setSelectedVersion(0)
        setIsNew(false)
        setDirty(false)
      }
      setCurrentPhotoStage(stage)
      setPhotoProposal(null)
      setPhotoDecisions([])
      setPhotoMode('')
      setPhotoReviewConfirmed(false)
      setPhotoOrderConfirmed(false)
      setPhotoNotice('Photo staged for look description only. It is not a generation reference.')
    } catch (error) {
      if (request === photoRequest.current) setPhotoError(errorText(error, 'stage this photo'))
    } finally {
      if (request === photoRequest.current) {
        photoBusyRef.current = false
        setPhotoBusy(false)
      }
    }
  }

  const startPhotoDescription = (mode, proposal = null, alreadyConfirmed = false) => {
    if (!alreadyConfirmed && dirty && !window.confirm('Replace the unsaved editor content with this photo description?')) return false
    detailRequest.current += 1
    setSelectedKey('')
    setSelectedVersion(0)
    setCurrentLook(null)
    setLookError('')
    setSaveError('')
    setNotice('')
    setIsNew(true)
    if (mode === 'extracted') {
      const nextForm = {
        ...blankForm(),
        appearance: proposal.appearance,
        outfitMode: 'garments',
        garments: proposal.garments.map((wording) => rowFromGarment({ wording: wording.trim(), aside: '' })),
      }
      setForm(nextForm)
      const rows = resetAutomationDraft(nextForm)
      setPhotoProposal(proposal)
      const companion = proposal.automation
      if (companion?.status === 'ready') {
        try {
          validateAutomationCandidate(
            companion.profile, proposal.appearance,
            proposal.garments.map((wording) => ({ aside: '', wording: wording.trim() })),
            companion.draft_content_digest,
          )
          if (companion.proposal_id !== proposal.proposal_id
            || companion.profile?.draft_content_digest !== companion.draft_content_digest) {
            throw new Error('The photo automation suggestion identity does not match its extraction.')
          }
          setAutomationDraft(draftFromProspectiveSuggestion(companion.profile, rows))
          setProfileSuggestion({
            proposal_id: companion.proposal_id,
            draft_content_digest: companion.draft_content_digest,
          })
          setAutomationProposalNotice('Photo automation suggestions are editable and unaccepted. Resolve every uncertain photo detail and review all profile facts.')
        } catch {
          setAutomationProposalNotice('The optional photo automation suggestions could not be used. The ordinary extraction is available for manual review.')
        }
      } else if (companion?.status === 'unavailable') {
        setAutomationProposalNotice('The optional photo automation suggestions are unavailable. The ordinary extraction is available for manual review.')
      }
      setPhotoDecisions(proposal.unresolved.map((item) => ({ id: item.id, detail: item.detail, action: '', correction: '' })))
      setPhotoReviewConfirmed(false)
      setPhotoOrderConfirmed(false)
      setLookOrderConfirmed(false)
      setDirty(true)
    } else {
      const nextForm = blankForm()
      setForm(nextForm)
      resetAutomationDraft(nextForm)
      setPhotoProposal(null)
      setPhotoDecisions([])
      setPhotoReviewConfirmed(false)
      setPhotoOrderConfirmed(false)
      setLookOrderConfirmed(false)
      setDirty(false)
    }
    setPhotoMode(mode)
    return true
  }

  const extractPhoto = async () => {
    const stage = photoStageRef.current
    if (stage?.state !== 'staged' || visionAvailability !== 'available' || photoBusyRef.current) return
    if (dirty && !window.confirm('Replace the unsaved editor content with the extracted photo proposal?')) return
    const request = ++photoRequest.current
    photoBusyRef.current = true
    setPhotoBusy(true)
    setPhotoError('')
    setPhotoNotice('')
    try {
      const proposal = await api.post(`/api/looks/photo-stages/${stage.photo_id}/extract`, includePhotoAutomation
        ? { include_automation: true }
        : {})
      if (request !== photoRequest.current || photoStageRef.current?.photo_id !== stage.photo_id) return
      if (typeof proposal?.proposal_id !== 'string' || !Array.isArray(proposal.garments)
        || !Array.isArray(proposal.unresolved) || typeof proposal.appearance !== 'string') {
        throw new Error('The server returned an invalid photo proposal.')
      }
      if (includePhotoAutomation && proposal.automation?.status === 'ready') {
        try {
          const source = {
            appearance: proposal.appearance,
            garments: proposal.garments.map((wording) => ({ key: null, wording, aside: '' })),
          }
          const digest = await draftContentDigest(source)
          if (request !== photoRequest.current || photoStageRef.current?.photo_id !== stage.photo_id) return
          validateAutomationCandidate(proposal.automation.profile, proposal.appearance, source.garments, digest)
          if (proposal.automation.proposal_id !== proposal.proposal_id
            || proposal.automation.draft_content_digest !== digest
            || proposal.automation.profile?.draft_content_digest !== digest) {
            throw new Error('The photo automation suggestion identity does not match its extraction.')
          }
        } catch {
          proposal.automation = { status: 'unavailable' }
        }
      }
      if (startPhotoDescription('extracted', proposal, true)) {
        setPhotoNotice('Review the appearance, resolve every uncertain detail, and confirm the garment removal order before saving.')
      }
    } catch (error) {
      if (request === photoRequest.current) {
        if (error?.detail?.code === 'vision_unavailable') setVisionAvailability('unavailable')
        setPhotoError(errorText(error, 'extract a look from this photo'))
      }
    } finally {
      if (request === photoRequest.current) {
        photoBusyRef.current = false
        setPhotoBusy(false)
      }
    }
  }

  const startLookFromImportedOutfit = (outfit) => {
    if (dirty && !window.confirm('Replace the unsaved editor content with this imported outfit?')) return
    detailRequest.current += 1
    setSelectedKey('')
    setSelectedVersion(0)
    setCurrentLook(null)
    setIsNew(true)
    const nextForm = {
      ...blankForm(),
      name: outfit.label || 'Imported outfit',
      outfitMode: 'catalogue',
      outfitKey: outfit.key,
      garments: rowsForOutfit(outfit, wardrobe),
    }
    setForm(nextForm)
    resetAutomationDraft(nextForm)
    setDirty(false)
    setLookError('')
    setSaveError('')
    setNotice('Name and review this imported outfit, then save it to create a reusable look.')
    setPhotoMode('')
    setPhotoProposal(null)
    setPhotoDecisions([])
  }

  const cancelPhotoStage = async () => {
    const stage = photoStageRef.current
    if (stage?.state !== 'staged' || photoBusyRef.current) return
    if (photoMode && !window.confirm('Cancel this photo stage and discard its photo-based editor draft?')) return
    const request = ++photoRequest.current
    photoBusyRef.current = true
    setPhotoBusy(true)
    setPhotoError('')
    setPhotoNotice('')
    try {
      const result = await api.post(`/api/looks/photo-stages/${stage.photo_id}/cancel`)
      if (request !== photoRequest.current || photoStageRef.current?.photo_id !== stage.photo_id) return
      setCurrentPhotoStage(result)
      if (photoMode) {
        detailRequest.current += 1
        setForm(null)
        setCurrentLook(null)
        setSelectedKey('')
        setSelectedVersion(0)
        setIsNew(false)
        setDirty(false)
      }
      setPhotoMode('')
      setPhotoProposal(null)
      setPhotoDecisions([])
      setPhotoReviewConfirmed(false)
      setPhotoOrderConfirmed(false)
      setPhotoNotice(result.cleanup_warning
        ? 'Photo stage cancelled. Temporary-file cleanup needs another attempt.'
        : 'Photo stage cancelled.')
    } catch (error) {
      if (request === photoRequest.current) setPhotoError(errorText(error, 'cancel this photo stage'))
    } finally {
      if (request === photoRequest.current) {
        photoBusyRef.current = false
        setPhotoBusy(false)
      }
    }
  }

  const retryPhotoCleanup = async () => {
    const target = cleanupStage || (photoStage?.cleanup_warning ? photoStage : null)
    if (!target || photoBusyRef.current) return
    photoBusyRef.current = true
    setPhotoBusy(true)
    setPhotoError('')
    try {
      const result = await api.get(`/api/looks/photo-stages/${target.photo_id}`)
      if (photoStageRef.current?.photo_id === target.photo_id) setCurrentPhotoStage(result)
      else if (result.cleanup_warning) setCleanupStage(result)
      else setCleanupStage(null)
      if (!result.cleanup_warning) setPhotoNotice('Temporary photo-file cleanup completed.')
    } catch (error) {
      setPhotoError(errorText(error, 'retry photo cleanup'))
    } finally {
      photoBusyRef.current = false
      setPhotoBusy(false)
    }
  }

  const refreshPhotoStatus = async () => {
    const target = photoStageRef.current
    if (!target || photoBusyRef.current) return
    const request = ++photoRequest.current
    photoBusyRef.current = true
    setPhotoBusy(true)
    setPhotoError('')
    try {
      const result = await api.get(`/api/looks/photo-stages/${target.photo_id}`)
      if (request === photoRequest.current && photoStageRef.current?.photo_id === target.photo_id) {
        setCurrentPhotoStage(result)
      }
    } catch (error) {
      if (request === photoRequest.current) setPhotoError(errorText(error, 'refresh photo status'))
    } finally {
      if (request === photoRequest.current) {
        photoBusyRef.current = false
        setPhotoBusy(false)
      }
    }
  }

  const customizeOutfit = () => updateForm({ outfitMode: 'garments', outfitKey: '' })

  const chooseOutfit = (value) => {
    if (value === '__manual__') {
      updateForm({ outfitMode: 'garments', outfitKey: '' })
    } else if (!value) {
      updateForm({ outfitMode: 'none', outfitKey: '' })
    } else {
      const outfit = outfitOptions.find((item) => item.key === value)
      updateForm({
        outfitMode: 'catalogue',
        outfitKey: value,
        garments: rowsForOutfit(outfit, wardrobe),
      })
    }
  }

  const garmentByKey = new Map(wardrobe.garments.map((garment) => [garment.key, garment]))
  const outfitOptions = [...wardrobe.outfits]
  for (const outfit of legacyOutfits) {
    if (!outfitOptions.some((item) => item.key === outfit.key)) outfitOptions.push(outfit)
  }
  const loadedOutfit = currentLook?.outfit
  if (loadedOutfit?.outfit_key && !outfitOptions.some((item) => item.key === loadedOutfit.outfit_key)) {
    outfitOptions.push({
      key: loadedOutfit.outfit_key,
      garments: loadedOutfit.garments,
    })
  }
  const usedGarmentKeys = new Set((form?.garments || []).map((row) => row.key).filter(Boolean))
  const availableGarments = wardrobe.garments.filter((item) => !usedGarmentKeys.has(item.key))

  const addSelectedGarment = () => {
    const garment = garmentByKey.get(newGarmentKey)
    if (!garment || usedGarmentKeys.has(garment.key)) return
    updateForm({
      outfitMode: 'garments',
      outfitKey: '',
      garments: [...(form?.garments || []), rowFromGarment(garment)],
    })
    setNewGarmentKey('')
  }

  const addBlankGarment = () => updateForm({
    outfitMode: 'garments',
    outfitKey: '',
    garments: [...(form?.garments || []), rowFromGarment(null)],
  })

  const moveGarment = (index, offset) => {
    const rows = [...form.garments]
    const target = index + offset
    if (target < 0 || target >= rows.length) return
    ;[rows[index], rows[target]] = [rows[target], rows[index]]
    updateForm({ outfitMode: 'garments', outfitKey: '', garments: rows })
  }

  const removeGarment = (index) => updateForm({
    outfitMode: 'garments',
    outfitKey: '',
    garments: form.garments.filter((_, rowIndex) => rowIndex !== index),
  })

  const runSaveAttempt = async (attempt, combined, surfaceEpoch) => {
    const isCurrentSurface = () => mountedRef.current
      && surfaceEpoch === detailRequest.current
    if (!isCurrentSurface()) return
    setSaveError('')
    setNotice('')
    try {
      const result = await api.postVersioned(attempt.path, attempt.body, attempt.integerFields)
      const resultVersion = combined ? combinedSuccessIsExact(result, attempt) : versionText(result?.version)
      if (!result || typeof result.key !== 'string' || !resultVersion) {
        throw new Error(combined
          ? 'The combined save returned an invalid look/profile identity or readiness result.'
          : 'The server returned an invalid saved look version.')
      }
      const currentSurface = isCurrentSurface()
      if (combined) releaseCombinedSave(attempt, currentSurface ? 'success' : 'success-offscreen')
      if (!currentSurface) return
      const summary = {
        key: result.key,
        version: resultVersion,
        name: result.name,
        content_digest: result.content_digest,
      }
      looksRequest.current += 1
      setLooksLoading(false)
      setLooksError('')
      setLooks((current) => {
        const index = current.findIndex((item) => item.key === summary.key)
        if (index < 0) return [...current, summary]
        return current.map((item) => item.key === summary.key ? summary : item)
      })
      setSelectedKey(result.key)
      setSelectedVersion(resultVersion)
      setVersionChoice(resultVersion)
      setCurrentLook(result)
      const nextForm = formForLook(result, wardrobe)
      setForm(nextForm)
      resetAutomationDraft(nextForm)
      setIsNew(false)
      setDirty(false)
      setNotice(combined
        ? `Saved look version ${resultVersion} and automation profile revision 1. The profile is ready.`
        : `Saved as version ${resultVersion}. Previous versions remain available; no automation profile was created.`)
      setPhotoMode('')
      setPhotoProposal(null)
      setPhotoDecisions([])
      setPhotoReviewConfirmed(false)
      setPhotoOrderConfirmed(false)
      setLookOrderConfirmed(false)
      combinedSaveRef.current = null
      setUncertainSave(false)
      if (photoStage?.photo_id) {
        const savedPhotoId = photoStage.photo_id
        void api.get(`/api/looks/photo-stages/${savedPhotoId}`).then((stage) => {
          if (photoStageRef.current?.photo_id === savedPhotoId) setCurrentPhotoStage(stage)
        }).catch(() => {})
      }
      void loadWardrobe()
    } catch (error) {
      if (combined) {
        if (isKnownPreWriteRefusal(error)) releaseCombinedSave(attempt)
        else retainCombinedSave(attempt, false)
      }
      if (!isCurrentSurface()) return
      setSaveError(errorText(error, 'save this look'))
      if (combined) {
        if (isKnownPreWriteRefusal(error)) {
          combinedSaveRef.current = null
          setUncertainSave(false)
          setCombinedSaveInFlight(false)
        } else {
          setUncertainSave(true)
          setCombinedSaveInFlight(false)
        }
      }
    }
  }

  const retryCombinedSave = async () => {
    const attempt = combinedSaveRef.current
    if (!attempt || saving || savingRef.current || !mountedRef.current) return
    savingRef.current = true
    setSaving(true)
    retainCombinedSave(attempt, true)
    await runSaveAttempt(attempt, true, detailRequest.current)
    savingRef.current = false
    if (mountedRef.current) setSaving(false)
  }

  const saveLook = async (event, mode = 'combined') => {
    event?.preventDefault()
    if (!form || saving || savingRef.current || uncertainSave) return
    if (mode === 'combined' && pendingCombinedSave) return
    const surfaceEpoch = detailRequest.current
    const combined = mode === 'combined'
    const name = form.name.trim()
    if (!name) {
      setSaveError('Enter a name for this look.')
      return
    }
    if (form.outfitMode === 'garments' && form.garments.some((row) => !row.wording.trim())) {
      setSaveError('Add worn wording for each garment, or remove the empty row.')
      return
    }

    const finalPhotoText = [form.appearance, ...form.garments.flatMap((row) => [row.wording, row.aside])]
    if (photoMode === 'extracted') {
      const unresolvedValid = photoDecisions.every((item) => item.action === 'omit'
        || (item.action === 'correct' && item.correction.trim()
          && finalPhotoText.some((text) => text.includes(item.correction.trim()))))
      if (!photoProposal || !unresolvedValid) {
        setSaveError('Resolve or explicitly omit every uncertain detail. Include each correction in the appearance or a garment description.')
        return
      }
      if (!photoReviewConfirmed || !photoOrderConfirmed) {
        setSaveError('Review the extraction and confirm the complete garment removal order before saving.')
        return
      }
      if (photoStage?.state !== 'staged') {
        setSaveError('This photo stage is no longer available. Stage the photo again before saving its extraction.')
        return
      }
    }
    if (photoMode === 'manual' && photoStage?.state !== 'staged') {
      setSaveError('This photo stage is no longer available. Stage the photo again before saving.')
      return
    }
    if (combined) {
      const rows = reviewedRows(form)
      const blockers = automationReviewBlockers(automationDraft, form.appearance, rows, {
        inventory: automationInventoryConfirmed,
        baseline: automationBaselineConfirmed,
        review: automationReviewConfirmed,
        order: photoMode === 'extracted' ? photoOrderConfirmed : lookOrderConfirmed,
      }, photoMode === 'extracted' ? {
        resolutions: photoDecisions.every((item) => item.action === 'omit'
          || (item.action === 'correct' && item.correction.trim()
            && finalPhotoText.some((text) => text.includes(item.correction.trim())))),
        review: photoReviewConfirmed,
        order: photoOrderConfirmed,
      } : null)
      if (blockers.length) {
        setSaveError('Complete the automation review before using the combined save. See the blockers below.')
        return
      }
    }

    const previous = selectedSummary
    const expectedVersion = isNew || photoMode ? null : versionText(previous?.version)
    if (!isNew && !photoMode && !expectedVersion) {
      setSaveError('The selected saved look has an invalid version. Refresh saved looks before saving.')
      return
    }
    savingRef.current = true
    setSaving(true)
    setSaveError('')
    setNotice('')
    try {
      const lookBody = lookPayloadForForm(form)
      let path
      let body
      let integerFields = []
      if (combined) {
        const rows = reviewedRows(form)
        const source = prospectiveSource(form.appearance, rows)
        const digest = await draftContentDigest(source)
        const profile = prospectiveProfile(automationDraft, form.appearance, rows, digest)
        const suggestion = profileSuggestion
          && (photoMode !== 'extracted' || profileSuggestion.proposal_id === photoProposal?.proposal_id)
          ? profileSuggestion
          : null
        const automation = {
          profile,
          profile_review_confirmed: true,
          inventory_complete_confirmed: true,
          baseline_coverage_confirmed: true,
          removal_order_confirmed: true,
          ...(suggestion ? { suggestion } : {}),
        }
        if (photoMode === 'extracted') {
          path = `/api/looks/photo-stages/${photoStage.photo_id}/save-extracted`
          body = {
            proposal_id: photoProposal.proposal_id,
            ...lookBody,
            garments: rows.map((row) => ({ wording: row.wording.trim(), aside: row.aside.trim() })),
            unresolved_decisions: photoReviewPayload(photoDecisions),
            review_confirmed: photoReviewConfirmed,
            removal_order_confirmed: photoOrderConfirmed,
            request_id: requestUuid(),
            automation,
          }
        } else if (photoMode === 'manual') {
          path = `/api/looks/photo-stages/${photoStage.photo_id}/save`
          body = { ...lookBody, request_id: requestUuid(), automation }
        } else if (isNew) {
          path = '/api/looks'
          body = { ...lookBody, request_id: requestUuid(), automation }
        } else {
          path = `/api/looks/${encodeURIComponent(selectedKey)}/versions`
          body = { ...lookBody, expected_version: expectedVersion, request_id: requestUuid(), automation }
          integerFields = ['expected_version']
        }
        const attemptBody = freezeRequestValue(structuredClone(body))
        const expectedGarments = form.outfitMode === 'none' ? [] : form.garments.map((row, index) => {
          const sent = lookBody.garments?.[index]
          return {
            key: sent?.key || (lookBody.outfit_key ? row.key : null) || null,
            wording: sent?.wording ?? row.wording,
            aside: sent?.aside ?? row.aside,
          }
        })
        const attempt = freezeRequestValue({
          path,
          body: attemptBody,
          integerFields: [...integerFields],
          expected: {
            key: expectedVersion == null ? null : selectedKey,
            expectedVersion,
            name: lookBody.name,
            appearance: lookBody.appearance,
            outfitKey: lookBody.outfit_key || null,
            garments: expectedGarments,
          },
        })
        combinedSaveRef.current = attempt
        setUncertainSave(false)
        setCombinedSaveInFlight(true)
        retainCombinedSave(attempt, true)
        await runSaveAttempt(attempt, true, surfaceEpoch)
      } else {
        if (photoMode === 'extracted') {
          path = `/api/looks/photo-stages/${photoStage.photo_id}/save-extracted`
          body = {
            proposal_id: photoProposal.proposal_id,
            ...lookBody,
            garments: form.outfitMode === 'garments'
              ? form.garments.map((row) => ({ wording: row.wording.trim(), aside: row.aside.trim() }))
              : [],
            unresolved_decisions: photoReviewPayload(photoDecisions),
            review_confirmed: photoReviewConfirmed,
            removal_order_confirmed: photoOrderConfirmed,
          }
        } else if (photoMode === 'manual') {
          path = `/api/looks/photo-stages/${photoStage.photo_id}/save`
          body = lookBody
        } else if (isNew) {
          path = '/api/looks'
          body = lookBody
        } else {
          path = `/api/looks/${encodeURIComponent(selectedKey)}/versions`
          body = { ...lookBody, expected_version: expectedVersion }
          integerFields = ['expected_version']
        }
        await runSaveAttempt({ path, body, integerFields }, false, surfaceEpoch)
      }
    } catch (error) {
      setSaveError(errorText(error, 'prepare this look save'))
      combinedSaveRef.current = null
      setUncertainSave(false)
    } finally {
      savingRef.current = false
      if (mountedRef.current && surfaceEpoch === detailRequest.current) setSaving(false)
    }
  }

  const navigationBlocked = saving || dirty || Boolean(photoMode) || Boolean(archivingKey)
    || uncertainSave || Boolean(pendingCombinedSave)
  const latestVersion = versionText(selectedSummary?.version)
  const requestedVersion = versionText(versionChoice)
  const versionOptions = boundedVersionOptions(latestVersion, versionText(selectedVersion))
  const selectedOutfitValue = form?.outfitMode === 'catalogue'
    ? form.outfitKey
    : form?.outfitMode === 'garments' ? '__manual__' : ''
  const importChoices = Array.isArray(importPreview?.plan?.choices) ? importPreview.plan.choices : []
  const extractionResolutionsComplete = photoDecisions.every((item) => item.action === 'omit'
    || (item.action === 'correct' && item.correction.trim()
      && [form?.appearance || '', ...(form?.garments || []).flatMap((row) => [row.wording, row.aside])]
        .some((text) => text.includes(item.correction.trim()))))
  const canConfirmPhotoReview = extractionResolutionsComplete
  const currentReviewRows = reviewedRows(form)
  const combinedBlockers = form ? automationReviewBlockers(
    automationDraft,
    form.appearance,
    currentReviewRows,
    {
      inventory: automationInventoryConfirmed,
      baseline: automationBaselineConfirmed,
      review: automationReviewConfirmed,
      order: photoMode === 'extracted' ? photoOrderConfirmed : lookOrderConfirmed,
    },
    photoMode === 'extracted' ? {
      resolutions: extractionResolutionsComplete,
      review: photoReviewConfirmed,
      order: photoOrderConfirmed,
    } : null,
  ) : []

  return (
    <section className="looks-page" aria-label="Looks">
      <div className="row">
        <div>
          <h1>Looks</h1>
          <p className="muted">Save reusable appearance and optional outfit details.</p>
        </div>
        <div className="row" aria-label="Look actions">
          <button type="button" className="primary" onClick={startNew} disabled={navigationBlocked}>
            Create look
          </button>
          <input
            ref={jsonInput}
            type="file"
            accept=".json,application/json"
            aria-label="Choose look JSON file"
            onChange={handleJsonFile}
            disabled={importBusy}
            hidden
          />
          <button type="button" onClick={() => jsonInput.current?.click()} disabled={importBusy}>
            Import JSON
          </button>
          <button
            type="button"
            onClick={exportSelectedVersion}
            disabled={!selectedKey || !versionText(selectedVersion) || exportBusy}
          >
            {exportBusy ? 'Exporting…' : 'Export selected version'}
          </button>
          <input
            ref={photoInput}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            aria-label="Choose one photo"
            onChange={handlePhotoFile}
            disabled={saving || uncertainSave || photoBusy || photoStage?.state === 'staged'}
            hidden
          />
          <button
            type="button"
            onClick={() => photoInput.current?.click()}
            disabled={saving || uncertainSave || photoBusy || photoStage?.state === 'staged'}
          >
            From photo
          </button>
        </div>
      </div>

      {importError && !importPreview && <p className="error" role="alert">{importError}</p>}
      {importNotice && <p role="status">{importNotice}</p>}
      {importPreview && (
        <section className="panel" aria-label="JSON import review">
          <div className="row">
            <div>
              <h2>Review JSON import</h2>
              <p className="muted">
                {importPreview.kind === 'legacy'
                  ? 'Legacy outfit JSON imports catalogue garments and outfits. It does not create a saved look.'
                  : 'Review this portable look and its local catalogue mappings before importing.'}
              </p>
            </div>
          </div>
          <p role="status">
            {importPreview.plan.status === 'already_imported' || importPreview.plan.status === 'already_equal'
              ? 'This content already matches an imported or saved item; commit will be a no-op.'
              : importPreview.kind === 'legacy'
                ? `${importPreview.plan.resolved_garments?.length || 0} garments and ${importPreview.plan.resolved_outfits?.length || 0} outfits are ready for catalogue import.`
                : 'One saved look version is ready for import.'}
          </p>
          {importPreview.plan.remapped?.length > 0 && (
            <p className="muted" role="note">
              {importPreview.plan.remapped.length} catalogue item(s) will use local mappings because their imported keys conflict.
            </p>
          )}
          {importPreview.plan.mode === 'choice_required' && (
            <fieldset aria-label="Import conflict choice">
              <legend>Choose how to resolve this conflict</legend>
              {importChoices.map((option) => {
                const choice = typeof option === 'string' ? option : option.choice
                return (
                  <label key={choice}>
                    <input
                      type="radio"
                      name="look-import-choice"
                      value={choice}
                      checked={importChoice === choice}
                      onChange={() => setImportChoice(choice)}
                      disabled={importBusy || importPreview.committed}
                    />
                    {importChoiceLabel(choice, typeof option === 'object' ? option : null)}
                  </label>
                )
              })}
            </fieldset>
          )}
          {importError && <p className="error" role="alert">{importError}</p>}
          {importPreview.committed ? (
            <p role="status">Import committed. The preview is closed to further writes.</p>
          ) : (
            <div className="row">
              <button
                type="button"
                className="primary"
                onClick={commitImport}
                disabled={importBusy || (importPreview.plan.mode === 'choice_required' && !importChoice)}
              >
                {importBusy ? 'Importing…' : 'Commit reviewed import'}
              </button>
              <button
                type="button"
                onClick={() => {
                  importRequest.current += 1
                  setImportPreview(null)
                  setImportChoice('')
                  setImportError('')
                }}
                disabled={importBusy}
              >
                Discard preview
              </button>
            </div>
          )}
        </section>
      )}

      {legacyOutfits.length > 0 && (
        <section className="panel" aria-label="Imported outfits">
          <h2>Imported outfits</h2>
          <p className="muted">Choose an outfit to convert it into a named saved look.</p>
          <ul>
            {legacyOutfits.map((outfit) => (
              <li key={outfit.key}>
                {outfit.label || 'Imported outfit'}
                <button type="button" onClick={() => startLookFromImportedOutfit(outfit)} disabled={navigationBlocked}>
                  Start a look from this outfit
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="panel" aria-label="Look photo input">
        <h2>From photo</h2>
        <p className="muted">Stage one JPEG, PNG, or WebP photo for an editable look description. The photo is not a generation reference.</p>
        {visionAvailability === 'checking' && <p className="muted" role="status">Checking whether a vision model is configured…</p>}
        {visionAvailability === 'available' && <p className="muted" role="status">Photo extraction is available with the explicitly configured vision model.</p>}
        {visionAvailability === 'unavailable' && (
          <p className="muted" role="status">
            Photo extraction is unavailable without a configured text assistant and explicit vision model. <a href="#/setup">Configure vision</a> or describe the photo manually.
          </p>
        )}
        {visionAvailability === 'unknown' && (
          <p className="muted" role="status">
            {visionError || 'Vision availability could not be checked.'} <a href="#/setup">Open Setup</a>
            {' '}<button type="button" onClick={loadVisionAvailability}>Check again</button>
          </p>
        )}
        {photoStage?.state === 'staged' && (
          <div>
            <img
              src={`/api/looks/photo-stages/${encodeURIComponent(photoStage.photo_id)}/preview`}
              alt="Temporary photo preview for look description"
              style={{ maxWidth: '100%', maxHeight: '28rem' }}
            />
            <p className="muted" role="status">
              {photoStage.format?.toUpperCase()} · {photoStage.width} × {photoStage.height} · {photoStage.byte_count} bytes
            </p>
            <label>
              <input
                type="checkbox"
                checked={includePhotoAutomation}
                onChange={(event) => setIncludePhotoAutomation(event.target.checked)}
                disabled={photoBusy}
              />
              Include optional automation profile suggestions with photo extraction
            </label>
            <div className="row">
              <button type="button" onClick={extractPhoto} disabled={photoBusy || saving || uncertainSave || visionAvailability !== 'available'}>
                {photoBusy ? 'Working…' : includePhotoAutomation ? 'Extract look + profile suggestions' : 'Extract look'}
              </button>
              <button type="button" onClick={() => startPhotoDescription('manual')} disabled={photoBusy || saving || uncertainSave || Boolean(photoProposal)}>
                Describe this photo manually
              </button>
              <button type="button" className="danger" onClick={cancelPhotoStage} disabled={photoBusy || saving || uncertainSave}>
                Cancel photo stage
              </button>
            </div>
            {photoProposal && <p className="muted" role="note">This photo already has a reviewable extraction proposal. Edit it, or cancel the stage and select the photo again to start with a manual description.</p>}
          </div>
        )}
        {photoStage?.state && photoStage.state !== 'staged' && (
          <p className="muted" role="status">
            {photoStage.state === 'saved'
              ? `Photo stage saved with look version ${photoStage.saved_look?.version}.`
              : `Photo stage ${photoStage.state}. Choose another photo when ready.`}
          </p>
        )}
        {photoStage?.photo_id && (
          <button type="button" onClick={refreshPhotoStatus} disabled={photoBusy || saving || uncertainSave}>Refresh photo status</button>
        )}
        {(cleanupStage?.cleanup_warning || photoStage?.cleanup_warning) && (
          <div>
            <p className="error" role="alert">{(cleanupStage || photoStage).cleanup_warning}</p>
            <button type="button" onClick={retryPhotoCleanup} disabled={photoBusy || saving || uncertainSave}>Retry cleanup</button>
          </div>
        )}
        {photoError && <p className="error" role="alert">{photoError}</p>}
        {photoNotice && <p role="status">{photoNotice}</p>}
      </section>

      <div className="looks-layout">
        <aside className="panel" aria-label="Saved looks">
          <div className="row">
            <h2>Saved looks</h2>
            <button type="button" onClick={loadLooks} disabled={looksLoading || navigationBlocked}>Refresh</button>
          </div>
          {looksError && <p className="error" role="alert">{looksError}</p>}
          {looksNotice && <p role="status">{looksNotice}</p>}
          {looksLoading && <p className="muted">Loading saved looks…</p>}
          {!looksLoading && !looksError && looks.length === 0 && <p className="muted">No saved looks yet.</p>}
          <div className="looks-list">
            {looks.map((item) => (
              <div className="looks-list-item" key={item.key}>
                <button
                  type="button"
                  aria-current={selectedKey === item.key ? 'true' : undefined}
                  disabled={navigationBlocked || !versionText(item.version)}
                  onClick={() => openVersion(item.key, item.version)}
                >
                  {item.name} · version {item.version}
                </button>
                <button
                  type="button"
                  className="danger"
                  aria-label={`Archive ${item.name}`}
                  aria-busy={archivingKey === item.key}
                  disabled={navigationBlocked || !versionText(item.version)}
                  onClick={() => archiveLook(item)}
                >
                  {archivingKey === item.key ? 'Archiving…' : 'Archive'}
                </button>
              </div>
            ))}
          </div>
        </aside>

        <section className="panel" aria-label="Look editor">
          {uncertainSave && <div className="error" role="alert" aria-label="Combined save recovery">
            {combinedSaveInFlight
              ? <p>A combined save from this browser tab is still awaiting a response. Editing and new writes stay blocked until its exact outcome is known.</p>
              : <p>The combined save outcome is unknown. Its exact request body and request ID are retained for recovery.</p>}
            {!combinedSaveInFlight && <button type="button" onClick={retryCombinedSave} disabled={saving}>Retry the identical combined save</button>}
          </div>}
          {!form && !lookLoading && !lookError && <p className="muted">Select a saved look or create one.</p>}
          {lookLoading && <p className="muted" role="status">Loading look version…</p>}
          {lookError && <p className="error" role="alert">{lookError}</p>}
          {lookError && selectedKey && versionText(selectedVersion) && (
            <button type="button" onClick={() => openVersion(selectedKey, selectedVersion)}>Retry</button>
          )}
          {form && (
            <form onSubmit={saveLook} aria-label={isNew ? 'Create look form' : 'Edit look form'}>
              {!isNew && selectedSummary && (
                <div className="grid-form">
                  <p className="muted" role="status">
                    Viewing version {versionText(selectedVersion) || 'unavailable'}{latestVersion ? ` · latest version ${latestVersion}` : ''}.
                  </p>
                  <label>
                    Version history
                    <select
                      aria-label="Version history"
                      value={versionText(selectedVersion) || ''}
                      onChange={(event) => openVersion(selectedKey, event.target.value)}
                      disabled={saving || uncertainSave || dirty}
                    >
                      {versionOptions.map((version) => (
                        <option key={version} value={version}>
                          Version {version}{version === latestVersion ? ' · latest' : ''}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Version number
                    <input
                      aria-label="Version number"
                      type="text"
                      inputMode="numeric"
                      pattern="[1-9][0-9]*"
                      value={versionChoice}
                      onChange={(event) => setVersionChoice(event.target.value)}
                      disabled={saving || uncertainSave || dirty}
                    />
                  </label>
                  <div className="row">
                    <button
                      type="button"
                      onClick={() => openVersion(selectedKey, latestVersion)}
                      disabled={!latestVersion || latestVersion === versionText(selectedVersion) || saving || uncertainSave || dirty}
                    >
                      Open latest version
                    </button>
                    <button
                      type="button"
                      onClick={() => openVersion(selectedKey, versionChoice)}
                      disabled={!requestedVersion || saving || uncertainSave || dirty}
                    >
                      Open version
                    </button>
                  </div>
                </div>
              )}
              <label>
                Look name
                <input
                  aria-label="Look name"
                  value={form.name}
                  maxLength={128}
                  onChange={(event) => updateForm({ name: event.target.value })}
                  disabled={saving || uncertainSave}
                  required
                />
              </label>
              <label>
                Constant appearance (optional)
                <textarea
                  aria-label="Constant appearance"
                  value={form.appearance}
                  onChange={(event) => updateForm({ appearance: event.target.value })}
                  disabled={saving || uncertainSave}
                  placeholder="Hair, makeup, or styling that stays constant"
                />
              </label>
              <p className="muted" role="note">
                Review this description for clothing and move removable items into the outfit. This is a reminder, not automatic detection.
              </p>
              <p className="muted">Use garments for one-piece clothing, layers, and removable accessories. No body-part categories are required.</p>

              {photoMode === 'manual' && (
                <p className="muted" role="note">This form saves a manual description for the staged photo. The photo is not attached to a generation or session.</p>
              )}

              {photoMode === 'extracted' ? (
                <p className="muted" role="note">Appearance stays constant. Review the separate garment list and arrange it in removal order.</p>
              ) : (
                <label>
                  Optional outfit
                  <select
                    aria-label="Optional outfit"
                    value={selectedOutfitValue}
                    onChange={(event) => chooseOutfit(event.target.value)}
                    disabled={saving || uncertainSave}
                  >
                    <option value="">No outfit (appearance only)</option>
                    <option value="__manual__">Build an outfit from garments</option>
                    {outfitOptions.map((outfit) => (
                      <option key={outfit.key} value={outfit.key}>{outfitLabel(outfit, wardrobe)}</option>
                    ))}
                  </select>
                </label>
              )}

              {form.outfitMode === 'catalogue' && (
                <div>
                  <ol aria-label="Outfit removal order">
                    {form.garments.map((row, index) => (
                      <li key={row.id}>
                        {row.wording}{row.aside ? ` · moved aside: ${row.aside}` : ''}
                      </li>
                    ))}
                  </ol>
                  <button type="button" onClick={customizeOutfit} disabled={saving || uncertainSave}>Edit garments and order</button>
                </div>
              )}

              {form.outfitMode === 'garments' && (
                <div>
                  <div className="row">
                    {photoMode !== 'extracted' && (
                      <>
                        <label>
                          Add existing garment
                          <select
                            aria-label="Add existing garment"
                            value={newGarmentKey}
                            onChange={(event) => setNewGarmentKey(event.target.value)}
                            disabled={saving || uncertainSave || wardrobeLoading || availableGarments.length === 0}
                          >
                            <option value="">Choose a garment</option>
                            {availableGarments.map((garment) => (
                              <option key={garment.key} value={garment.key}>
                                {visibleName(garment, garment.wording || 'Garment')}
                              </option>
                            ))}
                          </select>
                        </label>
                        <button type="button" onClick={addSelectedGarment} disabled={saving || uncertainSave || !newGarmentKey}>
                          Add existing garment
                        </button>
                      </>
                    )}
                    <button type="button" onClick={addBlankGarment} disabled={saving || uncertainSave}>Add new garment</button>
                  </div>
                  {wardrobeError && <p className="error" role="alert">{wardrobeError}</p>}
                  {form.garments.map((row, index) => (
                    <fieldset className="looks-garment" key={row.id} aria-label={`Garment ${index + 1}`}>
                      <legend>Garment {index + 1}</legend>
                      <div className="looks-garment-fields">
                        <label>
                          Worn wording
                          <textarea
                            aria-label={`Garment ${index + 1} worn wording`}
                            value={row.wording}
                            onChange={(event) => updateGarment(index, { wording: event.target.value })}
                            disabled={saving || uncertainSave}
                            required
                          />
                        </label>
                        <label>
                          Moved-aside wording (optional)
                          <textarea
                            aria-label={`Garment ${index + 1} moved-aside wording`}
                            value={row.aside}
                            onChange={(event) => updateGarment(index, { aside: event.target.value })}
                            disabled={saving || uncertainSave}
                          />
                        </label>
                      </div>
                      <div className="row">
                        <button type="button" aria-label={`Move garment ${index + 1} up`} onClick={() => moveGarment(index, -1)} disabled={saving || uncertainSave || index === 0}>Move up</button>
                        <button type="button" aria-label={`Move garment ${index + 1} down`} onClick={() => moveGarment(index, 1)} disabled={saving || uncertainSave || index === form.garments.length - 1}>Move down</button>
                        <button type="button" className="danger" aria-label={`Remove garment ${index + 1}`} onClick={() => removeGarment(index)} disabled={saving || uncertainSave}>Remove</button>
                      </div>
                    </fieldset>
                  ))}
                </div>
              )}

              {photoMode === 'extracted' && photoProposal && (
                <fieldset className="panel" aria-label="Photo extraction review">
                  <legend>Review photo extraction</legend>
                  {photoDecisions.map((item, index) => (
                    <div key={item.id}>
                      <p><strong>Uncertain detail {index + 1}:</strong> {item.detail}</p>
                      <label>
                        Resolution
                        <select
                          aria-label={`Resolution for uncertain detail ${index + 1}`}
                          value={item.action}
                          onChange={(event) => updatePhotoDecision(item.id, { action: event.target.value, correction: '' })}
                          disabled={saving || uncertainSave}
                        >
                          <option value="">Choose correction or omission</option>
                          <option value="correct">Correct this detail</option>
                          <option value="omit">Omit this detail</option>
                        </select>
                      </label>
                      {item.action === 'correct' && (
                        <label>
                          Correction
                          <input
                            aria-label={`Correction for uncertain detail ${index + 1}`}
                            value={item.correction}
                            maxLength={1000}
                            onChange={(event) => updatePhotoDecision(item.id, { correction: event.target.value })}
                            disabled={saving || uncertainSave}
                          />
                        </label>
                      )}
                      {item.action === 'correct' && item.correction.trim()
                        && ![form.appearance, ...form.garments.flatMap((row) => [row.wording, row.aside])]
                          .some((text) => text.includes(item.correction.trim())) && (
                            <p className="muted">Include the correction in the appearance or a garment description below.</p>
                          )}
                    </div>
                  ))}
                  <label>
                    <input
                      type="checkbox"
                      checked={photoReviewConfirmed}
                      onChange={(event) => setPhotoReviewConfirmed(event.target.checked)}
                      disabled={saving || uncertainSave || !canConfirmPhotoReview}
                    />
                    I reviewed the appearance and corrected or omitted every uncertain detail.
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={photoOrderConfirmed}
                      onChange={(event) => setPhotoOrderConfirmed(event.target.checked)}
                      disabled={saving || uncertainSave}
                    />
                    I confirm the complete garment list and removal order shown above.
                  </label>
                </fieldset>
              )}

              <fieldset aria-label="Automation profile review">
                <legend>Automation profile review</legend>
                <p className="muted">Review this profile with the look before saving. Suggestions are optional, editable and never accepted automatically.</p>
                {photoMode !== 'extracted' && (
                  <button
                    type="button"
                    onClick={requestAutomationProposal}
                    disabled={automationProposalBusy || saving || uncertainSave
                      || currentReviewRows.some((row) => !row.wording.trim())}
                  >
                    {automationProposalBusy ? 'Requesting suggestions…' : 'Suggest profile facts'}
                  </button>
                )}
                {profileSuggestion && <p className="muted" role="status">An optional suggestion is linked to its original draft identity; current look edits and profile corrections remain separate.</p>}
                {profileSuggestion && <button type="button" onClick={() => setProfileSuggestion(null)} disabled={saving || uncertainSave}>Save without optional suggestion evidence</button>}
                {automationProposalError && <p className="error" role="alert">{automationProposalError}</p>}
                {automationProposalNotice && <p className="muted" role="status">{automationProposalNotice}</p>}
                <ol aria-label="Reviewed garment removal order">
                  {currentReviewRows.map((row, index) => (
                    <li key={row.id}>{index + 1}. {row.wording}{row.aside ? ` · moved aside: ${row.aside}` : ''}</li>
                  ))}
                </ol>
                {photoMode !== 'extracted' && <label>
                  <input
                    type="checkbox"
                    checked={lookOrderConfirmed}
                    onChange={(event) => setLookOrderConfirmed(event.target.checked)}
                    disabled={saving || uncertainSave}
                  />
                  I confirm the complete garment list and removal order shown above.
                </label>}
                <AutomationProfileReview
                  appearance={form.appearance}
                  garments={currentReviewRows.map((row) => ({
                    id: row.id,
                    wording: row.wording,
                    aside: row.aside,
                  }))}
                  draft={automationDraft || emptyAutomationDraft(form.appearance, currentReviewRows)}
                  onDraftChange={(next) => {
                    automationDraftEpoch.current += 1
                    setAutomationDraft(next)
                  }}
                  inventoryConfirmed={automationInventoryConfirmed}
                  onInventoryConfirmed={setAutomationInventoryConfirmed}
                  baselineConfirmed={automationBaselineConfirmed}
                  onBaselineConfirmed={setAutomationBaselineConfirmed}
                  reviewConfirmed={automationReviewConfirmed}
                  onReviewConfirmed={setAutomationReviewConfirmed}
                  reviewLabel="I reviewed ownership, every coverage state, the baseline and inventory for this exact look draft."
                  appearanceLabel="Look appearance · select a phrase to assign or correct"
                  showReviewConfirmation
                  disabled={saving || uncertainSave}
                />
                {combinedBlockers.length > 0 && <div className="error" role="status" aria-label="Combined save blockers">
                  <strong>Combined save needs these facts:</strong>
                  <ul>{combinedBlockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul>
                  <p>Use “Save look only” to create the look without a profile.</p>
                </div>}
              </fieldset>

              {saveError && <p className="error" role="alert">{saveError}</p>}
              {notice && <p role="status">{notice}</p>}
              <div className="row">
                <button type="submit" className="primary" disabled={saving || uncertainSave || combinedBlockers.length > 0}>
                  {saving ? 'Saving…' : 'Save look + automation profile'}
                </button>
                <button type="button" onClick={() => saveLook(undefined, 'normal')} disabled={saving || uncertainSave}>
                  Save look only
                </button>
                <button type="button" onClick={cancelEdits} disabled={saving || uncertainSave}>
                  {photoMode === 'extracted'
                    ? 'Cancel photo stage and discard draft'
                    : photoMode ? 'Discard photo description' : 'Cancel edits'}
                </button>
              </div>
            </form>
          )}
          {currentLook && !isNew && !photoMode
            && currentLook.key === selectedKey
            && versionText(currentLook.version) === versionText(selectedVersion)
            && (
              <AutomationProfileEditor
                key={`${currentLook.key}:${versionText(currentLook.version)}:${currentLook.content_digest}`}
                look={currentLook}
                disabled={saving || uncertainSave || dirty || lookLoading}
              />
            )}
          {wardrobeError && form?.outfitMode !== 'garments' && <p className="error" role="alert">{wardrobeError}</p>}
        </section>
      </div>
    </section>
  )
}
