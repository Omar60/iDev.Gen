import React, { useEffect, useRef, useState } from 'react'
import { api, parseVersionJson, versionText } from '../api.js'

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
  const [isNew, setIsNew] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [looksLoading, setLooksLoading] = useState(true)
  const [wardrobeLoading, setWardrobeLoading] = useState(true)
  const [lookLoading, setLookLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [looksError, setLooksError] = useState('')
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
  const visionRequest = useRef(0)
  const looksRequest = useRef(0)
  const wardrobeRequest = useRef(0)
  const detailRequest = useRef(0)
  const selectedSummary = looks.find((item) => item.key === selectedKey)

  const loadLooks = async () => {
    const request = ++looksRequest.current
    setLooksLoading(true)
    setLooksError('')
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
    loadLooks()
    loadWardrobe()
    loadVisionAvailability()
    return () => {
      looksRequest.current += 1
      wardrobeRequest.current += 1
      detailRequest.current += 1
      importRequest.current += 1
      exportRequest.current += 1
      photoRequest.current += 1
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
      setForm(formForLook(result, wardrobe))
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
    setForm(blankForm())
    setIsNew(true)
    setDirty(false)
    setLookError('')
    setSaveError('')
    setNotice('')
    setPhotoMode('')
    setPhotoProposal(null)
    setPhotoDecisions([])
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
      setIsNew(false)
      setDirty(false)
      setPhotoMode('')
      setPhotoProposal(null)
      setPhotoDecisions([])
      setPhotoReviewConfirmed(false)
      setPhotoOrderConfirmed(false)
      setSaveError('')
      setNotice('')
      return
    }
    if (!isNew && currentLook) {
      setForm(formForLook(currentLook, wardrobe))
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
    setIsNew(false)
    setDirty(false)
    setSaveError('')
  }

  const updateForm = (patch) => {
    setForm((current) => ({ ...current, ...patch }))
    setDirty(true)
    setSaveError('')
    setNotice('')
    if (photoMode === 'extracted') {
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
    if (photoMode === 'extracted') {
      setPhotoReviewConfirmed(false)
      setPhotoOrderConfirmed(false)
    }
  }

  const updatePhotoDecision = (id, patch) => {
    setPhotoDecisions((current) => current.map((item) => item.id === id ? { ...item, ...patch } : item))
    setPhotoReviewConfirmed(false)
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
      setForm({
        ...blankForm(),
        appearance: proposal.appearance,
        outfitMode: 'garments',
        garments: proposal.garments.map((wording) => rowFromGarment({ wording: wording.trim(), aside: '' })),
      })
      setPhotoProposal(proposal)
      setPhotoDecisions(proposal.unresolved.map((item) => ({ id: item.id, detail: item.detail, action: '', correction: '' })))
      setPhotoReviewConfirmed(false)
      setPhotoOrderConfirmed(false)
      setDirty(true)
    } else {
      setForm(blankForm())
      setPhotoProposal(null)
      setPhotoDecisions([])
      setPhotoReviewConfirmed(false)
      setPhotoOrderConfirmed(false)
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
      const proposal = await api.post(`/api/looks/photo-stages/${stage.photo_id}/extract`, {})
      if (request !== photoRequest.current || photoStageRef.current?.photo_id !== stage.photo_id) return
      if (typeof proposal?.proposal_id !== 'string' || !Array.isArray(proposal.garments)
        || !Array.isArray(proposal.unresolved) || typeof proposal.appearance !== 'string') {
        throw new Error('The server returned an invalid photo proposal.')
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
    setForm({
      ...blankForm(),
      name: outfit.label || 'Imported outfit',
      outfitMode: 'catalogue',
      outfitKey: outfit.key,
      garments: rowsForOutfit(outfit, wardrobe),
    })
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

  const saveLook = async (event) => {
    event.preventDefault()
    if (!form || saving || savingRef.current) return
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

    const body = { name, appearance: form.appearance }
    if (form.outfitMode === 'catalogue' && form.outfitKey) {
      body.outfit_key = form.outfitKey
    } else if (form.outfitMode === 'garments') {
      body.garments = form.garments.map(editableGarmentPayload)
    } else {
      body.garments = []
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
      const result = photoMode === 'extracted'
        ? await api.post(`/api/looks/photo-stages/${photoStage.photo_id}/save-extracted`, {
          proposal_id: photoProposal.proposal_id,
          name,
          appearance: form.appearance,
          garments: form.outfitMode === 'garments'
            ? form.garments.map((row) => ({ wording: row.wording.trim(), aside: row.aside.trim() }))
            : [],
          unresolved_decisions: photoReviewPayload(photoDecisions),
          review_confirmed: photoReviewConfirmed,
          removal_order_confirmed: photoOrderConfirmed,
        })
        : photoMode === 'manual'
          ? await api.post(`/api/looks/photo-stages/${photoStage.photo_id}/save`, body)
          : isNew
            ? await api.post('/api/looks', body)
            : await api.postVersioned(`/api/looks/${encodeURIComponent(selectedKey)}/versions`, {
                ...body,
                expected_version: expectedVersion,
              }, ['expected_version'])
      const resultVersion = versionText(result?.version)
      if (!result || typeof result.key !== 'string' || !resultVersion) {
        throw new Error('The server returned an invalid saved look version.')
      }
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
      setForm(formForLook(result, wardrobe))
      setIsNew(false)
      setDirty(false)
      setNotice(`Saved as version ${resultVersion}. Previous versions remain available.`)
      setPhotoMode('')
      setPhotoProposal(null)
      setPhotoDecisions([])
      setPhotoReviewConfirmed(false)
      setPhotoOrderConfirmed(false)
      if (photoStage?.photo_id) {
        const savedPhotoId = photoStage.photo_id
        void api.get(`/api/looks/photo-stages/${savedPhotoId}`).then((stage) => {
          if (photoStageRef.current?.photo_id === savedPhotoId) setCurrentPhotoStage(stage)
        }).catch(() => {})
      }
      void loadWardrobe()
    } catch (error) {
      setSaveError(errorText(error, 'save this look'))
    } finally {
      savingRef.current = false
      setSaving(false)
    }
  }

  const navigationBlocked = saving || dirty || Boolean(photoMode)
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
            disabled={photoBusy || photoStage?.state === 'staged'}
            hidden
          />
          <button
            type="button"
            onClick={() => photoInput.current?.click()}
            disabled={photoBusy || photoStage?.state === 'staged'}
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
            <div className="row">
              <button type="button" onClick={extractPhoto} disabled={photoBusy || visionAvailability !== 'available'}>
                {photoBusy ? 'Working…' : 'Extract look'}
              </button>
              <button type="button" onClick={() => startPhotoDescription('manual')} disabled={photoBusy || Boolean(photoProposal)}>
                Describe this photo manually
              </button>
              <button type="button" className="danger" onClick={cancelPhotoStage} disabled={photoBusy}>
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
          <button type="button" onClick={refreshPhotoStatus} disabled={photoBusy}>Refresh photo status</button>
        )}
        {(cleanupStage?.cleanup_warning || photoStage?.cleanup_warning) && (
          <div>
            <p className="error" role="alert">{(cleanupStage || photoStage).cleanup_warning}</p>
            <button type="button" onClick={retryPhotoCleanup} disabled={photoBusy}>Retry cleanup</button>
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
          {looksLoading && <p className="muted">Loading saved looks…</p>}
          {!looksLoading && !looksError && looks.length === 0 && <p className="muted">No saved looks yet.</p>}
          <div className="looks-list">
            {looks.map((item) => (
              <button
                type="button"
                key={item.key}
                aria-current={selectedKey === item.key ? 'true' : undefined}
                disabled={navigationBlocked || !versionText(item.version)}
                onClick={() => openVersion(item.key, item.version)}
              >
                {item.name} · version {item.version}
              </button>
            ))}
          </div>
        </aside>

        <section className="panel" aria-label="Look editor">
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
                      disabled={saving || dirty}
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
                      disabled={saving || dirty}
                    />
                  </label>
                  <div className="row">
                    <button
                      type="button"
                      onClick={() => openVersion(selectedKey, latestVersion)}
                      disabled={!latestVersion || latestVersion === versionText(selectedVersion) || saving || dirty}
                    >
                      Open latest version
                    </button>
                    <button
                      type="button"
                      onClick={() => openVersion(selectedKey, versionChoice)}
                      disabled={!requestedVersion || saving || dirty}
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
                  disabled={saving}
                  required
                />
              </label>
              <label>
                Constant appearance (optional)
                <textarea
                  aria-label="Constant appearance"
                  value={form.appearance}
                  onChange={(event) => updateForm({ appearance: event.target.value })}
                  disabled={saving}
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
                    disabled={saving}
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
                      <li key={`${row.key || 'garment'}-${index}`}>
                        {row.wording}{row.aside ? ` · moved aside: ${row.aside}` : ''}
                      </li>
                    ))}
                  </ol>
                  <button type="button" onClick={customizeOutfit} disabled={saving}>Edit garments and order</button>
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
                            disabled={saving || wardrobeLoading || availableGarments.length === 0}
                          >
                            <option value="">Choose a garment</option>
                            {availableGarments.map((garment) => (
                              <option key={garment.key} value={garment.key}>
                                {visibleName(garment, garment.wording || 'Garment')}
                              </option>
                            ))}
                          </select>
                        </label>
                        <button type="button" onClick={addSelectedGarment} disabled={saving || !newGarmentKey}>
                          Add existing garment
                        </button>
                      </>
                    )}
                    <button type="button" onClick={addBlankGarment} disabled={saving}>Add new garment</button>
                  </div>
                  {wardrobeError && <p className="error" role="alert">{wardrobeError}</p>}
                  {form.garments.map((row, index) => (
                    <fieldset className="looks-garment" key={`${row.key || 'new'}-${index}`} aria-label={`Garment ${index + 1}`}>
                      <legend>Garment {index + 1}</legend>
                      <div className="looks-garment-fields">
                        <label>
                          Worn wording
                          <textarea
                            aria-label={`Garment ${index + 1} worn wording`}
                            value={row.wording}
                            onChange={(event) => updateGarment(index, { wording: event.target.value })}
                            disabled={saving}
                            required
                          />
                        </label>
                        <label>
                          Moved-aside wording (optional)
                          <textarea
                            aria-label={`Garment ${index + 1} moved-aside wording`}
                            value={row.aside}
                            onChange={(event) => updateGarment(index, { aside: event.target.value })}
                            disabled={saving}
                          />
                        </label>
                      </div>
                      <div className="row">
                        <button type="button" aria-label={`Move garment ${index + 1} up`} onClick={() => moveGarment(index, -1)} disabled={saving || index === 0}>Move up</button>
                        <button type="button" aria-label={`Move garment ${index + 1} down`} onClick={() => moveGarment(index, 1)} disabled={saving || index === form.garments.length - 1}>Move down</button>
                        <button type="button" className="danger" aria-label={`Remove garment ${index + 1}`} onClick={() => removeGarment(index)} disabled={saving}>Remove</button>
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
                          disabled={saving}
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
                            disabled={saving}
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
                      disabled={saving || !canConfirmPhotoReview}
                    />
                    I reviewed the appearance and corrected or omitted every uncertain detail.
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={photoOrderConfirmed}
                      onChange={(event) => setPhotoOrderConfirmed(event.target.checked)}
                      disabled={saving}
                    />
                    I confirm the complete garment list and removal order shown above.
                  </label>
                </fieldset>
              )}

              {saveError && <p className="error" role="alert">{saveError}</p>}
              {notice && <p role="status">{notice}</p>}
              <div className="row">
                <button type="submit" className="primary" disabled={saving}>{saving ? 'Saving…' : 'Save look'}</button>
                <button type="button" onClick={cancelEdits} disabled={saving}>
                  {photoMode === 'extracted'
                    ? 'Cancel photo stage and discard draft'
                    : photoMode ? 'Discard photo description' : 'Cancel edits'}
                </button>
              </div>
            </form>
          )}
          {wardrobeError && form?.outfitMode !== 'garments' && <p className="error" role="alert">{wardrobeError}</p>}
        </section>
      </div>
    </section>
  )
}
