import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'

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

export default function Looks() {
  const [looks, setLooks] = useState([])
  const [wardrobe, setWardrobe] = useState({ garments: [], outfits: [] })
  const [selectedKey, setSelectedKey] = useState('')
  const [selectedVersion, setSelectedVersion] = useState(0)
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
  const looksRequest = useRef(0)
  const wardrobeRequest = useRef(0)
  const detailRequest = useRef(0)
  const selectedSummary = looks.find((item) => item.key === selectedKey)

  const loadLooks = async () => {
    const request = ++looksRequest.current
    setLooksLoading(true)
    setLooksError('')
    try {
      const result = await api.get('/api/looks')
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
    } catch (error) {
      if (request === wardrobeRequest.current) setWardrobeError(errorText(error, 'load wardrobe items'))
    } finally {
      if (request === wardrobeRequest.current) setWardrobeLoading(false)
    }
  }

  useEffect(() => {
    loadLooks()
    loadWardrobe()
    return () => {
      looksRequest.current += 1
      wardrobeRequest.current += 1
      detailRequest.current += 1
    }
  }, [])

  const openVersion = async (key, version) => {
    const request = ++detailRequest.current
    setSelectedKey(key)
    setSelectedVersion(version)
    setCurrentLook(null)
    setForm(null)
    setIsNew(false)
    setDirty(false)
    setNotice('')
    setLookError('')
    setSaveError('')
    setLookLoading(true)
    try {
      const result = await api.get(
        `/api/looks/${encodeURIComponent(key)}/versions/${version}`
      )
      if (request !== detailRequest.current) return
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
    setCurrentLook(null)
    setForm(blankForm())
    setIsNew(true)
    setDirty(false)
    setLookError('')
    setSaveError('')
    setNotice('')
  }

  const cancelEdits = () => {
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
    if (!form || saving) return
    const name = form.name.trim()
    if (!name) {
      setSaveError('Enter a name for this look.')
      return
    }
    if (form.outfitMode === 'garments' && form.garments.some((row) => !row.wording.trim())) {
      setSaveError('Add worn wording for each garment, or remove the empty row.')
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
    setSaving(true)
    setSaveError('')
    setNotice('')
    try {
      const result = isNew
        ? await api.post('/api/looks', body)
        : await api.post(`/api/looks/${encodeURIComponent(selectedKey)}/versions`, {
          ...body,
          expected_version: previous.version,
        })
      if (!result || typeof result.key !== 'string' || !Number.isSafeInteger(result.version)) {
        throw new Error('The server returned an invalid saved look version.')
      }
      const summary = {
        key: result.key,
        version: result.version,
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
      setSelectedVersion(result.version)
      setCurrentLook(result)
      setForm(formForLook(result, wardrobe))
      setIsNew(false)
      setDirty(false)
      setNotice(`Saved as version ${result.version}. Previous versions remain available.`)
      void loadWardrobe()
    } catch (error) {
      setSaveError(errorText(error, 'save this look'))
    } finally {
      setSaving(false)
    }
  }

  const navigationBlocked = saving || dirty
  const versionCount = Math.max(0, Number(selectedSummary?.version) || 0)
  const selectedOutfitValue = form?.outfitMode === 'catalogue'
    ? form.outfitKey
    : form?.outfitMode === 'garments' ? '__manual__' : ''

  return (
    <section className="looks-page" aria-label="Looks">
      <div className="row">
        <div>
          <h1>Looks</h1>
          <p className="muted">Save reusable appearance and optional outfit details.</p>
        </div>
        <button type="button" className="primary" onClick={startNew} disabled={navigationBlocked}>
          Create look
        </button>
      </div>

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
                disabled={navigationBlocked}
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
          {lookError && selectedKey && selectedVersion > 0 && (
            <button type="button" onClick={() => openVersion(selectedKey, selectedVersion)}>Retry</button>
          )}
          {form && (
            <form onSubmit={saveLook} aria-label={isNew ? 'Create look form' : 'Edit look form'}>
              {!isNew && selectedSummary && (
                <div className="grid-form">
                  <label>
                    Version history
                    <select
                      aria-label="Version history"
                      value={selectedVersion}
                      onChange={(event) => openVersion(selectedKey, Number(event.target.value))}
                      disabled={saving || dirty}
                    >
                      {Array.from({ length: versionCount }, (_, index) => index + 1).map((version) => (
                        <option key={version} value={version}>
                          Version {version}{version === selectedSummary.version ? ' · latest' : ''}
                        </option>
                      ))}
                    </select>
                  </label>
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

              {saveError && <p className="error" role="alert">{saveError}</p>}
              {notice && <p role="status">{notice}</p>}
              <div className="row">
                <button type="submit" className="primary" disabled={saving}>{saving ? 'Saving…' : 'Save look'}</button>
                <button type="button" onClick={cancelEdits} disabled={saving}>Cancel edits</button>
              </div>
            </form>
          )}
          {wardrobeError && form?.outfitMode !== 'garments' && <p className="error" role="alert">{wardrobeError}</p>}
        </section>
      </div>
    </section>
  )
}
