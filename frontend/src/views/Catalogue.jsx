import React, { useState, useEffect, useRef } from 'react'
import { api } from '../api.js'
import { setCatalogue, ALL_MANNERS } from '../kinds.js'
import { setWardrobe } from '../wardrobe.js'
import '../ui/foundations.css'
import './Catalogue.css'

export default function Catalogue() {
  const [components, setComponents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const [slotFilter, setSlotFilter] = useState('camera')
  const [mannerFilter, setMannerFilter] = useState('directed')
  const [showRetired, setShowRetired] = useState(false)
  const [query, setQuery] = useState('')
  const active = useRef(true)
  const componentRequest = useRef(0)
  const readingRequest = useRef(0)
  const wardrobeRequest = useRef(0)
  const readingScope = useRef({ slot: slotFilter, manner: mannerFilter })
  readingScope.current = { slot: slotFilter, manner: mannerFilter }
  const [readingLoading, setReadingLoading] = useState(true)
  const [loadedReadingScope, setLoadedReadingScope] = useState('')
  const [wardrobeLoading, setWardrobeLoading] = useState(true)
  const [wardrobeLoaded, setWardrobeLoaded] = useState(false)
  const pendingRef = useRef(new Set())
  const [pending, setPending] = useState([])
  const dialogRef = useRef(null)
  const addButtonRef = useRef(null)
  const formErrorRef = useRef(null)
  const runAction = async (key, action) => {
    if (pendingRef.current.has(key)) return
    pendingRef.current.add(key)
    setPending([...pendingRef.current])
    try { await action() } finally {
      pendingRef.current.delete(key)
      if (active.current) setPending([...pendingRef.current])
    }
  }
  const jumpTo = (section) => {
    const heading = document.getElementById(`catalogue-${section}-title`)
    heading?.focus({ preventScroll: true })
    heading?.scrollIntoView({ block: 'start' })
  }

  // Add / Edit form modal state
  const [editing, setEditing] = useState(null) // null or component dict or {} for new
  const [formValues, setFormValues] = useState({
    concept_key: '',
    slot: 'camera',
    manner: 'directed',
    family: '',
    faces: '',
    cameras: '',
    wording: '',
    judge_label: '',
  })
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState(null)

  const [baseReadings, setBaseReadings] = useState([])
  const [newReadingKey, setNewReadingKey] = useState('')
  const [newReadingLabel, setNewReadingLabel] = useState('')
  const [readingSaving, setReadingSaving] = useState(false)
  const [readingError, setReadingError] = useState(null)
  const [readingNotice, setReadingNotice] = useState(null)

  const [wardrobe, setWardrobeRows] = useState({ garments: [], outfits: [] })
  const [wardrobeError, setWardrobeError] = useState(null)

  const loadComponents = async () => {
    const request = ++componentRequest.current
    setLoading(true)
    setError(null)
    try {
      const data = await api.get('/api/components?all=1')
      if (request !== componentRequest.current) return
      setCatalogue(data || [])
      if (active.current) setComponents(data || [])
    } catch (err) {
      if (active.current && request === componentRequest.current) setError(err.message || 'Failed to load components')
    } finally {
      if (active.current && request === componentRequest.current) setLoading(false)
    }
  }

  const loadBaseReadings = async () => {
    const { slot, manner } = readingScope.current
    const scope = `${slot}:${manner}`
    const request = ++readingRequest.current
    const current = () => active.current && request === readingRequest.current
      && scope === `${readingScope.current.slot}:${readingScope.current.manner}`
    setReadingLoading(true)
    setReadingError(null)
    try {
      const data = await api.get(`/api/readings?slot=${slot}&manner=${manner}`)
      if (!current()) return
      setBaseReadings(data || [])
      setLoadedReadingScope(scope)
    } catch (err) {
      if (current()) setReadingError(err.message || 'Failed to load readings')
    } finally {
      if (current()) setReadingLoading(false)
    }
  }

  const loadWardrobe = async () => {
    const request = ++wardrobeRequest.current
    setWardrobeLoading(true)
    setWardrobeError(null)
    try {
      const data = await api.get('/api/wardrobe')
      if (request !== wardrobeRequest.current) return
      setWardrobe(data)
      if (active.current) {
        setWardrobeRows({ garments: data?.garments || [], outfits: data?.outfits || [] })
        setWardrobeLoaded(true)
      }
    } catch (err) {
      if (active.current && request === wardrobeRequest.current) setWardrobeError(err.message || 'Failed to load wardrobe')
    } finally {
      if (active.current && request === wardrobeRequest.current) setWardrobeLoading(false)
    }
  }

  const handleImportReadings = async () => {
    setReadingError(null)
    setReadingNotice(null)
    try {
      const res = await api.post('/api/readings/import')
      // Counted and not shown: the table below is filtered to one slot and one
      // manner, and a seed adds rows to eleven of them. Without the count an
      // import that worked looks like an import that did nothing.
      setReadingNotice(`Imported readings: ${res.added} added, ${res.skipped} already present.`)
      await loadBaseReadings()
    } catch (err) {
      setReadingError(err.message || 'Import failed')
    }
  }

  const handleImportWardrobe = async () => {
    setWardrobeError(null)
    try {
      await api.post('/api/wardrobe/import')
      await loadWardrobe()
    } catch (err) {
      setWardrobeError(err.message || 'Import failed')
    }
  }

  useEffect(() => {
    active.current = true
    loadComponents()
    loadWardrobe()
    return () => { active.current = false; readingRequest.current++ }
  }, [])

  useEffect(() => {
    loadBaseReadings()
  }, [slotFilter, mannerFilter])

  const handleAddBaseReading = async (e) => {
    e.preventDefault()
    if (!newReadingKey.trim() || !newReadingLabel.trim()) return
    setReadingSaving(true)
    setReadingError(null)
    try {
      await api.post('/api/readings', {
        slot: slotFilter,
        manner: mannerFilter,
        key: newReadingKey.trim(),
        label: newReadingLabel.trim(),
      })
      setNewReadingKey('')
      setNewReadingLabel('')
      await loadBaseReadings()
    } catch (err) {
      setReadingError(`Reading ${newReadingKey.trim()} (${slotFilter} · ${mannerFilter}): ${err.message || 'Failed to add reading'}`)
    } finally {
      setReadingSaving(false)
    }
  }

  const handleDeleteBaseReading = async (readingId) => {
    if (!confirm(`Delete base reading #${readingId}? Readings referenced by stored answers cannot be deleted.`)) return
    setReadingError(null)
    try {
      await api.del(`/api/readings/${readingId}`)
      await loadBaseReadings()
    } catch (err) {
      setReadingError(`Reading #${readingId}: ${err.message || 'Failed to delete reading'}`)
    }
  }

  const handleImport = async () => {
    setError(null)
    setNotice(null)
    try {
      const res = await api.post('/api/components/import')
      setNotice(`Imported catalogue: ${res.added} added, ${res.skipped} already present.`)
      await loadComponents()
    } catch (err) {
      setError(err.message || 'Import failed')
    }
  }

  const openAdd = () => {
    setEditing({})
    setFormValues({
      concept_key: '',
      slot: slotFilter,
      manner: mannerFilter,
      family: '',
      faces: '',
      cameras: '',
      wording: '',
      judge_label: '',
    })
    setFormError(null)
  }

  const openEdit = (comp) => {
    setEditing(comp)
    setFormValues({
      concept_key: comp.concept_key || '',
      slot: comp.slot || 'camera',
      manner: comp.manner || 'directed',
      family: comp.family || '',
      faces: comp.faces || '',
      cameras: (comp.cameras || []).join(', '),
      wording: comp.wording || '',
      judge_label: comp.judge_label || '',
    })
    setFormError(null)
  }

  // `cameras` is one comma-separated line in the form and a list on the wire.
  const payloadFrom = (values) => ({
    ...values,
    cameras: values.cameras.split(',').map((c) => c.trim()).filter(Boolean),
  })

  const handleSave = async (e) => {
    e.preventDefault()
    setSaving(true)
    setFormError(null)
    try {
      if (editing?.id) {
        await api.patch(`/api/components/${editing.id}`, payloadFrom(formValues))
      } else {
        await api.post('/api/components', payloadFrom(formValues))
      }
      setEditing(null)
      await loadComponents()
    } catch (err) {
      setFormError(err.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleRetire = async (id) => {
    setError(null)
    try {
      await api.post(`/api/components/${id}/retire`)
      await loadComponents()
    } catch (err) {
      setError(err.message || 'Failed to retire component')
    }
  }

  const handleRestore = async (id) => {
    setError(null)
    try {
      await api.post(`/api/components/${id}/restore`)
      await loadComponents()
    } catch (err) {
      setError(err.message || 'Failed to restore component')
    }
  }

  const handleDelete = async (id) => {
    if (!confirm(`Delete component #${id}? This permanently removes an unmeasured component. Measured components must be retired instead.`)) return
    setError(null)
    try {
      await api.del(`/api/components/${id}`)
      await loadComponents()
    } catch (err) {
      setError(err.message || 'Failed to delete component')
    }
  }

  const filtered = components.filter((c) => {
    if (slotFilter && c.slot !== slotFilter) return false
    if (mannerFilter && c.manner !== mannerFilter) return false
    if (!showRetired && c.retired_at) return false
    const text = query.trim().toLowerCase()
    if (text && ![c.id, c.concept_key, c.family, c.faces, c.wording, c.judge_label, ...(c.cameras || [])]
      .some((value) => String(value ?? '').toLowerCase().includes(text))) return false
    return true
  })

  const editorOpen = Boolean(editing)
  useEffect(() => {
    if (!editorOpen) return
    const trigger = document.activeElement
    const overflow = document.body.style.overflow
    const dialog = dialogRef.current
    dialog.showModal()
    document.body.style.overflow = 'hidden'
    return () => {
      dialog.close()
      document.body.style.overflow = overflow
      if (trigger?.isConnected) trigger.focus()
      else addButtonRef.current?.focus()
    }
  }, [editorOpen])
  useEffect(() => { if (formError) formErrorRef.current?.focus() }, [formError])

  const visibleReadings = loadedReadingScope === `${slotFilter}:${mannerFilter}` ? baseReadings : []
  const editField = (field, value) => setFormValues((previous) => ({ ...previous, [field]: value }))
  return (
    <div className="studio-ui ui-page catalogue-view">
      <header className="ui-page-header">
        <h1 className="ui-page-title">Catalogue</h1>
        <p className="ui-lead">Manage prompt components, judging vocabulary and wardrobe seeds.</p>
      </header>
      <nav className="ui-actions catalogue-jumps" aria-label="Catalogue sections">
        <button className="ui-button" onClick={() => jumpTo('components')}>Components</button>
        <button className="ui-button" onClick={() => jumpTo('readings')}>Base readings</button>
        <button className="ui-button" onClick={() => jumpTo('wardrobe')}>Wardrobe</button>
      </nav>
      <div className="ui-stack">
        <section aria-labelledby="catalogue-components-title" className="ui-stack">
          <div className="catalogue-heading">
            <div><h2 id="catalogue-components-title" tabIndex={-1} className="ui-section-title">Prompt components</h2>
              <p className="ui-help">Camera positions, acts and framing. Evidence stays distinct from prompt wording.</p></div>
            <div className="ui-actions">
              <button ref={addButtonRef} className="ui-button ui-button--primary" onClick={openAdd}>Add component</button>
              <button className="ui-button" disabled={pending.includes('components-import')}
                onClick={() => runAction('components-import', handleImport)}>
                {pending.includes('components-import') ? 'Importing…' : 'Import measured seed'}</button>
            </div>
          </div>
          {error && <div className="ui-notice" data-tone="error" role="alert">{error}
            <p className="ui-help">Existing data stays visible when available. Refresh to verify the current state before retrying a write.</p>
            <button className="ui-button" onClick={loadComponents} disabled={loading}>Refresh components</button></div>}
          {notice && <p className="ui-notice" role="status">{notice}</p>}
          <div className="ui-surface catalogue-filters" role="search" aria-label="Component filters">
            <div className="ui-field catalogue-search"><label className="ui-label" htmlFor="catalogue-search">Search components</label>
              <input id="catalogue-search" type="search" className="ui-control" value={query} onChange={(event) => setQuery(event.target.value)} aria-describedby="catalogue-search-help" />
              <p id="catalogue-search-help" className="ui-help">Search the current slot and manner by key, wording, label, family, camera or ID.</p></div>
            <div className="ui-field"><label className="ui-label" htmlFor="catalogue-slot">Slot</label>
              <select id="catalogue-slot" className="ui-control" value={slotFilter} onChange={(event) => setSlotFilter(event.target.value)}>
                <option value="camera">camera</option><option value="act">act</option><option value="framing">framing</option>
              </select></div>
            <div className="ui-field"><label className="ui-label" htmlFor="catalogue-manner">Manner</label>
              <select id="catalogue-manner" className="ui-control" value={mannerFilter} onChange={(event) => setMannerFilter(event.target.value)}>
                {ALL_MANNERS.map((manner) => <option key={manner.key} value={manner.key}>{manner.key}</option>)}
              </select></div>
            <label className="catalogue-check"><input type="checkbox" checked={showRetired} onChange={(event) => setShowRetired(event.target.checked)} />Show retired components</label>
          </div>
          <p className="ui-help" role="status">{loading ? 'Loading components…' : error ? 'The latest refresh failed. Refresh to verify the current inventory.' : `${filtered.length} shown · ${components.length} total`}</p>
          {loading && !components.length ? <div className="ui-surface" aria-busy="true">Loading catalogue…</div>
            : !filtered.length ? <div className="ui-surface catalogue-empty">
              <h3 className="ui-section-title">{error ? 'Components unavailable' : !components.length ? 'No components yet' : 'No matching components'}</h3>
              <p className="ui-help">{error ? 'Refresh components to verify the inventory.' : !components.length
                ? 'Add a component or explicitly import the measured seed. Nothing is imported automatically.'
                : 'Change the slot, manner or search, or include retired components. No records have been removed.'}</p>
              {query && <button className="ui-button" onClick={() => setQuery('')}>Clear search</button>}
            </div> : <div className="ui-stack catalogue-cards">
              {filtered.map((component) => <article key={component.id} className="ui-surface catalogue-card" aria-labelledby={`component-${component.id}-title`} aria-busy={pending.includes(`component-${component.id}`)}>
                <div className="catalogue-card-main">
                  <div className="ui-section-heading"><h3 id={`component-${component.id}-title`} className="ui-section-title">{component.concept_key}</h3>
                    {component.retired_at && <span className="ui-state" data-tone="warning">Retired</span>}</div>
                  <p className="ui-help">Component #{component.id} · {component.slot} · {component.manner}</p>
                  <dl className="catalogue-metadata">
                    {component.family && <div><dt>Family</dt><dd>{component.family}</dd></div>}
                    {component.faces && <div><dt>Faces</dt><dd>{component.faces}</dd></div>}
                    {!!component.cameras?.length && <div><dt>Cameras</dt><dd>{component.cameras.join(', ')}</dd></div>}
                  </dl>
                  <dl className="catalogue-wording"><div><dt>Prompt wording</dt><dd>{component.wording}</dd></div>
                    <div><dt>Judge label</dt><dd>{component.judge_label}</dd></div></dl>
                  <div className="ui-actions catalogue-evidence" aria-label={`Evidence for component ${component.id}`} role="group">
                    <span className="ui-help">Evidence</span>
                    {component.judged > 0 ? <><span className="ui-state">{component.state}</span>
                      <span className="ui-state">Arrived {component.arrived} of {component.judged}</span>
                      {component.contradicted > 0 && <span className="ui-state" data-tone="error" title="The judge recorded the body and camera contradicting each other, not another component">{component.contradicted} contradicted</span>}</>
                      : <span className="ui-state" title="No photograph has been judged against this component yet">Not measured</span>}
                  </div>
                </div>
                <div className="ui-actions catalogue-card-actions" role="group" aria-label={`Actions for component ${component.id}`}>
                  <button className="ui-button" aria-label={`Edit component ${component.id}`} disabled={pending.includes(`component-${component.id}`)} onClick={() => openEdit(component)}>Edit</button>
                  <button className="ui-button" aria-label={`${component.retired_at ? 'Restore' : 'Retire'} component ${component.id}`} disabled={pending.includes(`component-${component.id}`)}
                    onClick={() => runAction(`component-${component.id}`, () => component.retired_at ? handleRestore(component.id) : handleRetire(component.id))}>{component.retired_at ? 'Restore' : 'Retire'}</button>
                  <button className="ui-button catalogue-danger" aria-label={`Delete component ${component.id}`} disabled={pending.includes(`component-${component.id}`)}
                    onClick={() => runAction(`component-${component.id}`, () => handleDelete(component.id))}>Delete</button>
                  {pending.includes(`component-${component.id}`) && <span className="ui-help" role="status">Updating…</span>}
                </div>
              </article>)}
            </div>}
        </section>

        <section className="ui-surface ui-stack" aria-labelledby="catalogue-readings-title">
          <div className="catalogue-heading"><div><h2 id="catalogue-readings-title" tabIndex={-1} className="ui-section-title">Base readings</h2>
            <p className="ui-help">Judging vocabulary for {slotFilter} · {mannerFilter}. Existing labels stay unchanged when importing.</p></div>
            <div className="ui-actions"><button className="ui-button" onClick={loadBaseReadings} disabled={readingLoading}>Refresh readings</button>
              <button className="ui-button" disabled={pending.includes('readings-import')} onClick={() => runAction('readings-import', handleImportReadings)}>{pending.includes('readings-import') ? 'Importing…' : 'Import readings seed'}</button></div></div>
          {readingError && <div className="ui-notice" data-tone="error" role="alert">{readingError}<p className="ui-help">Review the named record or scope before explicitly retrying. Refresh readings reloads only the current selection.</p></div>}
          {readingNotice && <p className="ui-notice" role="status">{readingNotice}</p>}
          {readingLoading && <p className="ui-help" role="status">Loading readings…</p>}
          {!!visibleReadings.length ? <ul className="catalogue-readings-list">
            {visibleReadings.map((reading) => <li key={reading.id}>
              <div><span className="ui-help">Key (family)</span><p><code>{reading.key}</code></p></div>
              <div><span className="ui-help">Label (viewer description)</span><p>{reading.label}</p></div>
              <button className="ui-button catalogue-danger" aria-label={`Delete reading ${reading.id}`} disabled={pending.includes(`reading-${reading.id}`)} onClick={() => runAction(`reading-${reading.id}`, () => handleDeleteBaseReading(reading.id))}>Delete</button>
            </li>)}
          </ul> : !readingLoading && <p className="ui-help">{readingError ? 'Readings are unavailable for the selected scope.' : `No base readings defined for ${slotFilter} (${mannerFilter}).`}</p>}
          <form onSubmit={(event) => { event.preventDefault(); runAction('reading-add', () => handleAddBaseReading(event)) }} className="catalogue-reading-form">
            <div className="ui-field"><label className="ui-label" htmlFor="catalogue-reading-key">Key (family)</label>
              <input id="catalogue-reading-key" className="ui-control" required value={newReadingKey} disabled={readingSaving} onChange={(event) => setNewReadingKey(event.target.value)} placeholder="side" /></div>
            <div className="ui-field"><label className="ui-label" htmlFor="catalogue-reading-label">Label (viewer description)</label>
              <input id="catalogue-reading-label" className="ui-control" required value={newReadingLabel} disabled={readingSaving} onChange={(event) => setNewReadingLabel(event.target.value)} placeholder="Profile view at torso height" /></div>
            <button className="ui-button ui-button--primary" type="submit" disabled={readingSaving}>{readingSaving ? 'Adding…' : 'Add base reading'}</button>
          </form>
        </section>

        <section className="ui-surface" aria-labelledby="catalogue-wardrobe-title">
          <div className="catalogue-heading"><div><h2 id="catalogue-wardrobe-title" tabIndex={-1} className="ui-section-title">Wardrobe</h2>
            <p className="ui-help">Inventory summary for the existing session outfit picker. This panel does not edit garments or outfits.</p></div>
            <div className="ui-actions"><button className="ui-button" onClick={loadWardrobe} disabled={wardrobeLoading}>Refresh wardrobe</button>
              <button className="ui-button" disabled={pending.includes('wardrobe-import')} onClick={() => runAction('wardrobe-import', handleImportWardrobe)}>{pending.includes('wardrobe-import') ? 'Importing…' : 'Import wardrobe seed'}</button></div></div>
          {wardrobeError && <div className="ui-notice" data-tone="error" role="alert">{wardrobeError}<p className="ui-help">Refresh wardrobe to verify the current inventory. Import is always explicit.</p></div>}
          {wardrobeLoading && <p className="ui-help" role="status">Loading wardrobe…</p>}
          <dl className="catalogue-wardrobe-counts"><div><dt>Garments</dt><dd>{wardrobeLoaded ? wardrobe.garments.length : '—'}</dd></div><div><dt>Outfits</dt><dd>{wardrobeLoaded ? wardrobe.outfits.length : '—'}</dd></div></dl>
          <p className="ui-help">Import skips existing keys and keeps their wording. An empty inventory leaves the outfit picker empty.</p>
        </section>
      </div>

      {editing && <dialog ref={dialogRef} className="catalogue-editor" aria-labelledby="catalogue-editor-title"
        onCancel={(event) => { event.preventDefault(); if (!saving) setEditing(null) }}
        onKeyDown={(event) => {
          if (event.key !== 'Tab') return
          const controls = [...event.currentTarget.querySelectorAll('input:not(:disabled), select:not(:disabled), textarea:not(:disabled), button:not(:disabled)')]
          const first = controls[0], last = controls[controls.length - 1]
          if (event.shiftKey && (event.target === first || !controls.includes(event.target))) { event.preventDefault(); last?.focus() }
          else if (!event.shiftKey && event.target === last) { event.preventDefault(); first?.focus() }
        }}>
        <form onSubmit={(event) => { event.preventDefault(); runAction('component-save', () => handleSave(event)) }} className="ui-stack">
          <div><h2 id="catalogue-editor-title" className="ui-section-title">{editing.id ? 'Edit component' : 'Add component'}</h2>
            <p className="ui-help">{editing.id ? `Component #${editing.id}` : 'New catalogue entry'} · Changes are saved only with Save.</p></div>
          {formError && <div ref={formErrorRef} className="ui-notice" data-tone="error" role="alert" tabIndex={-1}>{formError}</div>}
          <fieldset disabled={saving} className="catalogue-editor-fields"><legend>Identity and scope</legend>
            <div className="ui-form-grid">
              <div className="ui-field ui-field-wide"><label className="ui-label" htmlFor="component-concept-key">Concept key</label>
                <input id="component-concept-key" className="ui-control" required value={formValues.concept_key} onChange={(event) => editField('concept_key', event.target.value)} /></div>
              <div className="ui-field"><label className="ui-label" htmlFor="component-slot">Slot</label>
                <select id="component-slot" className="ui-control" value={formValues.slot} onChange={(event) => editField('slot', event.target.value)}>
                  <option value="camera">camera</option><option value="act">act</option><option value="framing">framing</option></select></div>
              <div className="ui-field"><label className="ui-label" htmlFor="component-manner">Manner</label>
                <select id="component-manner" className="ui-control" value={formValues.manner} onChange={(event) => editField('manner', event.target.value)}>
                  {ALL_MANNERS.map((manner) => <option key={manner.key} value={manner.key}>{manner.key}</option>)}</select></div>
              <div className="ui-field"><label className="ui-label" htmlFor="component-family">Family</label>
                <input id="component-family" className="ui-control" value={formValues.family} onChange={(event) => editField('family', event.target.value)} /></div>
              <div className="ui-field"><label className="ui-label" htmlFor="component-faces">Faces</label>
                <select id="component-faces" className="ui-control" value={formValues.faces} onChange={(event) => editField('faces', event.target.value)}>
                  <option value="">(none)</option><option value="front">front</option><option value="side">side</option><option value="back">back</option></select></div>
              {formValues.slot === 'act' && <div className="ui-field ui-field-wide"><label className="ui-label" htmlFor="component-cameras">Camera families, strongest first</label>
                <input id="component-cameras" className="ui-control" value={formValues.cameras} onChange={(event) => editField('cameras', event.target.value)} aria-describedby="component-cameras-help" placeholder="shoulder, mirror" />
                <p id="component-cameras-help" className="ui-help">Comma-separated and ordered. Empty means the camera plan never moves a photograph to see this act.</p></div>}
            </div>
          </fieldset>
          <fieldset disabled={saving} className="catalogue-editor-fields"><legend>Wording and recognition</legend>
            <div className="ui-stack">
              <div className="ui-field"><label className="ui-label" htmlFor="component-wording">Prompt wording</label>
                <textarea id="component-wording" className="ui-control" rows={3} required value={formValues.wording} onChange={(event) => editField('wording', event.target.value)} /></div>
              <div className="ui-field"><label className="ui-label" htmlFor="component-judge-label">Judge label (viewer description)</label>
                <textarea id="component-judge-label" className="ui-control" rows={3} required value={formValues.judge_label} onChange={(event) => editField('judge_label', event.target.value)} aria-describedby="component-judge-help" />
                <p id="component-judge-help" className="ui-help">Describe what the viewer sees. This must differ from the prompt wording; the server validates it.</p></div>
            </div>
          </fieldset>
          <div className="ui-actions catalogue-editor-actions"><button className="ui-button" type="button" disabled={saving} onClick={() => setEditing(null)}>Cancel</button>
            <button className="ui-button ui-button--primary" type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button></div>
        </form>
      </dialog>}
    </div>
  )
}
