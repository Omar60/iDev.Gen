import React, { useState, useEffect } from 'react'
import { api } from '../api.js'
import { setCatalogue } from '../kinds.js'

export default function Catalogue() {
  const [components, setComponents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const [slotFilter, setSlotFilter] = useState('camera')
  const [mannerFilter, setMannerFilter] = useState('directed')
  const [showRetired, setShowRetired] = useState(false)

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

  const loadComponents = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.get('/api/components?all=1')
      setComponents(data || [])
      setCatalogue(data || [])
    } catch (err) {
      setError(err.message || 'Failed to load components')
    } finally {
      setLoading(false)
    }
  }

  const loadBaseReadings = async () => {
    setReadingError(null)
    try {
      const data = await api.get(`/api/readings?slot=${slotFilter}&manner=${mannerFilter}`)
      setBaseReadings(data || [])
    } catch (err) {
      setReadingError(err.message || 'Failed to load readings')
    }
  }

  useEffect(() => {
    loadComponents()
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
      setReadingError(err.message || 'Failed to add reading')
    } finally {
      setReadingSaving(false)
    }
  }

  const handleDeleteBaseReading = async (readingId) => {
    setReadingError(null)
    try {
      await api.delete(`/api/readings/${readingId}`)
      await loadBaseReadings()
    } catch (err) {
      setReadingError(err.message || 'Failed to delete reading')
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
    setError(null)
    try {
      await api.delete(`/api/components/${id}`)
      await loadComponents()
    } catch (err) {
      setError(err.message || 'Failed to delete component')
    }
  }

  const filtered = components.filter((c) => {
    if (slotFilter && c.slot !== slotFilter) return false
    if (mannerFilter && c.manner !== mannerFilter) return false
    if (!showRetired && c.retired_at) return false
    return true
  })

  return (
    <div className="catalogue-view" style={{ maxWidth: 1080, margin: '20px auto', padding: '0 16px' }}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h2 style={{ margin: 0 }}>Component Catalogue</h2>
          <p className="muted" style={{ margin: '4px 0 0', fontSize: 13 }}>
            Prompt components stored in SQLite for camera positions, acts, and framing.
          </p>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button className="primary" onClick={openAdd}>+ Add Component</button>
          <button onClick={handleImport}>Import Measured Seed</button>
        </div>
      </div>

      {error && <div className="error" style={{ marginBottom: 14 }}>{error}</div>}
      {notice && <div className="notice" style={{ marginBottom: 14, background: 'var(--panel)', padding: '8px 14px', borderRadius: 4 }}>{notice}</div>}

      <div className="row panel" style={{ gap: 16, alignItems: 'center', marginBottom: 16, padding: '10px 14px' }}>
        <div className="row" style={{ gap: 6, alignItems: 'center' }}>
          <label style={{ fontSize: 13, fontWeight: 500 }}>Slot:</label>
          <select value={slotFilter} onChange={(e) => setSlotFilter(e.target.value)}>
            <option value="camera">camera</option>
            <option value="act">act</option>
            <option value="framing">framing</option>
          </select>
        </div>

        <div className="row" style={{ gap: 6, alignItems: 'center' }}>
          <label style={{ fontSize: 13, fontWeight: 500 }}>Manner:</label>
          <select value={mannerFilter} onChange={(e) => setMannerFilter(e.target.value)}>
            <option value="directed">directed</option>
            <option value="candid">candid</option>
            <option value="selfie">selfie</option>
          </select>
        </div>

        <label className="row" style={{ gap: 6, alignItems: 'center', fontSize: 13, cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={showRetired}
            onChange={(e) => setShowRetired(e.target.checked)}
          />
          Show retired components
        </label>
      </div>

      {loading ? (
        <p className="muted">Loading components...</p>
      ) : filtered.length === 0 ? (
        <div className="panel" style={{ textAlign: 'center', padding: '30px 20px' }}>
          <p className="muted" style={{ margin: '0 0 12px' }}>
            No components found for slot <b>{slotFilter}</b> and manner <b>{mannerFilter}</b>.
          </p>
          <button onClick={handleImport}>Import Measured Catalogue</button>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 10 }}>
          {filtered.map((c) => (
            <div key={c.id} className="panel" style={{ opacity: c.retired_at ? 0.6 : 1, padding: 14 }}>
              <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                  <div className="row" style={{ gap: 8, alignItems: 'center', marginBottom: 4 }}>
                    <b style={{ fontSize: 15 }}>{c.concept_key}</b>
                    <span className="badge">{c.slot} · {c.manner}</span>
                    {c.family && <span className="badge muted">family: {c.family}</span>}
                    {c.faces && <span className="badge muted">faces: {c.faces}</span>}
                    {c.cameras?.length > 0 && (
                      <span className="badge muted">cameras: {c.cameras.join(', ')}</span>
                    )}
                    {c.retired_at && <span className="badge failed">retired</span>}
                  </div>
                  <div style={{ margin: '4px 0', fontSize: 14 }}>
                    <span className="muted">Prompt wording: </span>
                    <span>{c.wording}</span>
                  </div>
                  <div style={{ margin: '4px 0', fontSize: 14 }}>
                    <span className="muted">Judge label: </span>
                    <span style={{ color: 'var(--accent)' }}>{c.judge_label}</span>
                  </div>
                  {/* The evidence, and `contradicted` said out loud beside the
                      rest of it. A photograph that failed because the body and
                      the camera disagree is a different finding from one that
                      rendered some other component, and merging them into a
                      single miss count is how the same defect gets measured
                      twice. */}
                  <div className="row" style={{ gap: 6, marginTop: 6, alignItems: 'center' }}>
                    <span className="muted" style={{ fontSize: 12 }}>Evidence:</span>
                    {c.judged > 0 ? (
                      <>
                        <span className="badge">{c.state}</span>
                        <span className="badge muted">
                          arrived {c.arrived} of {c.judged}
                        </span>
                        {c.contradicted > 0 && (
                          <span
                            className="badge failed"
                            title="Misses the judge recorded as the body and the camera contradicting each other, not as some other component"
                          >
                            {c.contradicted} contradicted
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="badge muted" title="No photograph has been judged against this component yet">
                        not measured
                      </span>
                    )}
                  </div>
                </div>

                <div className="row" style={{ gap: 6 }}>
                  <button onClick={() => openEdit(c)}>Edit</button>
                  {c.retired_at ? (
                    <button onClick={() => handleRestore(c.id)}>Restore</button>
                  ) : (
                    <button onClick={() => handleRetire(c.id)}>Retire</button>
                  )}
                  <button className="icon" onClick={() => handleDelete(c.id)} title="Delete component">🗑</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Base Readings Management */}
      <div className="panel" style={{ marginTop: 24, padding: 16 }}>
        <h3 style={{ fontSize: 16, marginBottom: 6 }}>
          Base Readings ({slotFilter} · {mannerFilter})
        </h3>
        <p className="muted" style={{ fontSize: 12, marginBottom: 12 }}>
          The baseline vocabulary for judging passes on {slotFilter} in {mannerFilter} manner.
        </p>

        {readingError && <div className="error" style={{ marginBottom: 12 }}>{readingError}</div>}

        {baseReadings.length > 0 ? (
          <table style={{ width: '100%', marginBottom: 16, fontSize: 13 }}>
            <thead>
              <tr>
                <th style={{ width: '30%' }}>Key (family)</th>
                <th>Label (viewer description)</th>
                <th style={{ width: 80 }}></th>
              </tr>
            </thead>
            <tbody>
              {baseReadings.map((r) => (
                <tr key={r.id}>
                  <td><code>{r.key}</code></td>
                  <td>{r.label}</td>
                  <td style={{ textAlign: 'right' }}>
                    <button
                      className="danger"
                      style={{ padding: '2px 8px', fontSize: 12 }}
                      onClick={() => handleDeleteBaseReading(r.id)}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted" style={{ fontSize: 12, fontStyle: 'italic', marginBottom: 16 }}>
            No base readings defined for {slotFilter} ({mannerFilter}).
          </p>
        )}

        <form onSubmit={handleAddBaseReading} className="row" style={{ gap: 8, alignItems: 'flex-end' }}>
          <div style={{ flex: 1 }}>
            <label style={{ display: 'block', fontSize: 11, marginBottom: 2 }}>Key (family)</label>
            <input
              type="text"
              placeholder="e.g. side"
              value={newReadingKey}
              onChange={(e) => setNewReadingKey(e.target.value)}
              style={{ width: '100%', fontSize: 12 }}
              required
            />
          </div>
          <div style={{ flex: 2 }}>
            <label style={{ display: 'block', fontSize: 11, marginBottom: 2 }}>Label (viewer description)</label>
            <input
              type="text"
              placeholder="e.g. Profile shot from the side, level with torso"
              value={newReadingLabel}
              onChange={(e) => setNewReadingLabel(e.target.value)}
              style={{ width: '100%', fontSize: 12 }}
              required
            />
          </div>
          <button type="submit" className="primary" disabled={readingSaving} style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
            {readingSaving ? 'Adding...' : 'Add Base Reading'}
          </button>
        </form>
      </div>

      {editing && (
        <div className="modal-backdrop" style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 999,
        }}>
          <form onSubmit={handleSave} className="panel" style={{ width: 500, maxWidth: '90vw', padding: 20 }}>
            <h3>{editing.id ? 'Edit Component' : 'Add Component'}</h3>
            {formError && <div className="error" style={{ marginBottom: 12 }}>{formError}</div>}

            <div style={{ marginBottom: 10 }}>
              <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>Concept Key</label>
              <input
                type="text"
                required
                value={formValues.concept_key}
                onChange={(e) => setFormValues({ ...formValues, concept_key: e.target.value })}
                style={{ width: '100%' }}
              />
            </div>

            <div className="row" style={{ gap: 10, marginBottom: 10 }}>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>Slot</label>
                <select
                  value={formValues.slot}
                  onChange={(e) => setFormValues({ ...formValues, slot: e.target.value })}
                  style={{ width: '100%' }}
                >
                  <option value="camera">camera</option>
                  <option value="act">act</option>
                  <option value="framing">framing</option>
                </select>
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>Manner</label>
                <select
                  value={formValues.manner}
                  onChange={(e) => setFormValues({ ...formValues, manner: e.target.value })}
                  style={{ width: '100%' }}
                >
                  <option value="directed">directed</option>
                  <option value="candid">candid</option>
                  <option value="selfie">selfie</option>
                </select>
              </div>
            </div>

            <div className="row" style={{ gap: 10, marginBottom: 10 }}>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>Family</label>
                <input
                  type="text"
                  value={formValues.family}
                  onChange={(e) => setFormValues({ ...formValues, family: e.target.value })}
                  style={{ width: '100%' }}
                />
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>Faces</label>
                <select
                  value={formValues.faces}
                  onChange={(e) => setFormValues({ ...formValues, faces: e.target.value })}
                  style={{ width: '100%' }}
                >
                  <option value="">(none)</option>
                  <option value="front">front</option>
                  <option value="side">side</option>
                  <option value="back">back</option>
                </select>
              </div>
            </div>

            {formValues.slot === 'act' && (
              <div style={{ marginBottom: 10 }}>
                <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>
                  Camera families that can see it, strongest first (comma-separated)
                </label>
                <input
                  type="text"
                  value={formValues.cameras}
                  onChange={(e) => setFormValues({ ...formValues, cameras: e.target.value })}
                  placeholder="shoulder, mirror"
                  style={{ width: '100%' }}
                />
                <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>
                  Left empty, the camera plan never moves a photograph to see this act.
                </div>
              </div>
            )}

            <div style={{ marginBottom: 10 }}>
              <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>Prompt Wording</label>
              <textarea
                required
                rows={2}
                value={formValues.wording}
                onChange={(e) => setFormValues({ ...formValues, wording: e.target.value })}
                style={{ width: '100%' }}
              />
            </div>

            <div style={{ marginBottom: 14 }}>
              <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>Judge Label (Viewer description)</label>
              <textarea
                required
                rows={2}
                value={formValues.judge_label}
                onChange={(e) => setFormValues({ ...formValues, judge_label: e.target.value })}
                style={{ width: '100%' }}
              />
            </div>

            <div className="row" style={{ gap: 8, justifyContent: 'flex-end' }}>
              <button type="button" onClick={() => setEditing(null)}>Cancel</button>
              <button type="submit" className="primary" disabled={saving}>
                {saving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}
