import { COVERAGE, REGIONS, unknownRegions } from './components/AutomationProfileReview.jsx'

let rowSequence = 0

export function newLookRowId() {
  if (typeof globalThis.crypto?.randomUUID === 'function') return `row-${globalThis.crypto.randomUUID()}`
  rowSequence += 1
  return `row-${Date.now().toString(36)}-${rowSequence.toString(36)}`
}

export function emptyAutomationDraft(appearance, garments, provenance) {
  const length = Array.from(appearance).length
  return {
    appearance_segments: length ? [{
      segment_id: `unassigned-${newLookRowId()}`,
      start: 0,
      end: length,
      text: appearance,
      owner: null,
    }] : [],
    garment_coverage: Object.fromEntries(garments.map((garment) => [garment.id, {
      worn: { regions: unknownRegions(), ...(provenance ? { provenance } : {}) },
      ...(garment.aside ? { aside: { regions: unknownRegions(), ...(provenance ? { provenance } : {}) } } : {}),
    }])),
    baseline_coverage: null,
    inventory_complete: { value: null, ...(provenance ? { provenance } : {}) },
  }
}

export function draftFromProspectiveSuggestion(profile, garments, provenance) {
  if (!profile || !Array.isArray(profile.appearance_segments) || !Array.isArray(profile.garment_coverage)) {
    throw new TypeError('The server returned an invalid automation profile proposal.')
  }
  const garmentAt = (index) => Number.isInteger(index) && index >= 0 && index < garments.length
    ? garments[index]
    : null
  const appearance_segments = profile.appearance_segments.map((segment) => {
    let owner = segment.owner
    if (owner && typeof owner === 'object') {
      const garment = garmentAt(owner.garment_index)
      if (!garment) throw new TypeError('The automation proposal refers to an unknown garment row.')
      owner = `garment:${garment.id}`
    }
    return { ...segment, owner }
  })
  const garment_coverage = {}
  for (const entry of profile.garment_coverage) {
    const garment = garmentAt(entry.garment_index)
    if (!garment || Object.hasOwn(garment_coverage, garment.id)) {
      throw new TypeError('The automation proposal contains an invalid garment mapping.')
    }
    garment_coverage[garment.id] = Object.fromEntries(Object.entries(entry.states || {}).map(([state, fact]) => [
      state,
      { regions: { ...fact.regions }, ...(provenance ? { provenance } : {}) },
    ]))
  }
  return {
    appearance_segments,
    garment_coverage,
    baseline_coverage: profile.baseline_coverage
      ? { regions: { ...profile.baseline_coverage.regions }, ...(provenance ? { provenance } : {}) }
      : null,
    inventory_complete: { value: profile.inventory_complete ?? null, ...(provenance ? { provenance } : {}) },
  }
}

export function prospectiveSource(appearance, rows) {
  return {
    appearance,
    garments: rows.map((row) => ({
      key: row.key || null,
      wording: row.wording.trim(),
      aside: row.aside.trim(),
    })),
  }
}

function regionsFor(fact) {
  return Object.fromEntries(REGIONS.map((region) => [region, fact?.regions?.[region] || 'unknown']))
}

export function hasCompleteAutomationFacts(draft, appearance, garments) {
  const owners = new Set(['permanent', ...garments.map((garment) => `garment:${garment.id}`)])
  let offset = 0
  for (const segment of draft.appearance_segments) {
    if (
      segment.start !== offset
      || segment.end <= segment.start
      || segment.text !== Array.from(appearance).slice(segment.start, segment.end).join('')
      || !owners.has(segment.owner)
    ) return false
    offset = segment.end
  }
  if (offset !== Array.from(appearance).length || draft.inventory_complete?.value !== true) return false
  const completeRegions = (fact) => fact && REGIONS.every((region) => COVERAGE.includes(fact.regions?.[region])
    && fact.regions[region] !== 'unknown')
  if (!completeRegions(draft.baseline_coverage)) return false
  return garments.every((garment) => {
    const states = draft.garment_coverage?.[garment.id]
    if (!completeRegions(states?.worn)) return false
    return !garment.aside || completeRegions(states?.aside)
  })
}

export function prospectiveProfile(draft, appearance, garments, draftContentDigest) {
  const indexForOwner = new Map(garments.map((garment, index) => [`garment:${garment.id}`, index]))
  return {
    schema_version: 1,
    draft_content_digest: draftContentDigest,
    appearance_segments: draft.appearance_segments.map((segment) => ({
      segment_id: segment.segment_id,
      start: segment.start,
      end: segment.end,
      text: segment.text,
      owner: segment.owner === 'permanent'
        ? 'permanent'
        : indexForOwner.has(segment.owner) ? { garment_index: indexForOwner.get(segment.owner) } : null,
    })),
    garment_coverage: garments.map((garment, garment_index) => {
      const row = draft.garment_coverage?.[garment.id] || {}
      const states = { worn: { regions: regionsFor(row.worn) } }
      if (garment.aside) states.aside = { regions: regionsFor(row.aside) }
      return { garment_index, states }
    }),
    baseline_coverage: draft.baseline_coverage
      ? { regions: regionsFor(draft.baseline_coverage) }
      : null,
    inventory_complete: draft.inventory_complete?.value ?? null,
  }
}

export async function draftContentDigest(source) {
  if (!globalThis.crypto?.subtle || typeof TextEncoder === 'undefined') {
    throw new Error('This browser cannot create an exact automation review digest.')
  }
  const sortKeys = (value) => {
    if (Array.isArray(value)) return value.map(sortKeys)
    if (!value || typeof value !== 'object') return value
    return Object.fromEntries(Object.entries(value)
      .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
      .map(([key, item]) => [key, sortKeys(item)]))
  }
  const canonical = JSON.stringify(sortKeys({
    schema_version: 1,
    appearance: source.appearance,
    garments: source.garments.map((garment) => ({
      wording: garment.wording,
      aside: garment.aside,
      ...(garment.key ? { key: garment.key } : {}),
    })),
  }))
  const bytes = new TextEncoder().encode(canonical)
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes)
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}
