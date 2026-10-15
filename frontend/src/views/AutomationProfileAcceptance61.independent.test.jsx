// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api.js'
import AutomationProfileEditor from './AutomationProfileEditor.jsx'

const look = {
  key: 'look-task61',
  version: 2,
  name: 'Task 6.1 target',
  content_digest: 'a'.repeat(64),
  appearance: 'Hair tied back. She wears a brass bracelet.',
  outfit: {
    outfit_key: 'outfit-task61',
    garments: [{ key: 'bracelet', wording: 'a brass bracelet', aside: 'bracelet moved aside' }],
  },
}
const profileDigest = 'b'.repeat(64)
const regions = { chest: 'full', hips: 'partial', legs: 'none', feet: 'unknown' }
const provenance = {
  origin: 'user', source_identity: 'looks-profile-editor',
  source_digest: look.content_digest, accepted_revision: 1,
}
const discovery = {
  look: { key: look.key, version: look.version, content_digest: look.content_digest },
  latest_profile_revision: 0,
  profiles: [],
}

let root
let container

async function drain() {
  await act(async () => {
    for (let index = 0; index < 16; index += 1) await Promise.resolve()
  })
}

async function click(element) {
  expect(element).toBeTruthy()
  await act(async () => { element.click() })
  await drain()
}

async function selectValue(element, value) {
  expect(element).toBeTruthy()
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), 'value')?.set
    if (setter) setter.call(element, String(value))
    else element.value = String(value)
    element.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await drain()
}

async function check(element) {
  expect(element).toBeTruthy()
  if (!element.checked) await click(element)
}

function mount(editorLook = look, disabled = false) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<AutomationProfileEditor look={editorLook} disabled={disabled} />))
}

async function openEditor() {
  await click(container.querySelector('summary'))
  await drain()
}

async function selectSourceWornCoverage() {
  const sourceRevision = container.querySelector('[aria-label="Exact source profile revision"]')
  await selectValue(sourceRevision, '1')
  const worn = [...container.querySelectorAll('label')]
    .find((item) => item.textContent.includes('Copy reviewed worn coverage'))
  await check(worn.querySelector('input[type="checkbox"]'))
  await selectValue(container.querySelector('[aria-label="Target state for a brass bracelet worn coverage"]'), 'worn')
}

beforeEach(() => vi.restoreAllMocks())

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  container = null
  vi.restoreAllMocks()
})

describe('Task 6.1 independent profile-editor acceptance', () => {
  it('limits the no-coverage assertion to previewed states and records reusable structured facts', async () => {
    vi.spyOn(api, 'getVersioned').mockResolvedValue(discovery)
    const publish = vi.spyOn(api, 'postVersioned').mockResolvedValue({
      profile_revision: 1, profile_digest: 'c'.repeat(64),
    })
    mount()
    await openEditor()

    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent === 'No coverage in supported regions'))
    const targets = container.querySelector('[aria-label="No-coverage target selection"]')
    await check(targets.querySelector('[aria-label="Assert no coverage for Baseline · every listed garment absent"]'))
    await check(targets.querySelector('[aria-label="Assert no coverage for a brass bracelet · worn"]'))
    await click([...targets.querySelectorAll('button')]
      .find((item) => item.textContent === 'Confirm this user assertion'))

    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent === 'All appearance is permanent'))
    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent === 'Confirm this user assertion'))

    await selectValue(container.querySelector('[aria-label="Worn chest"]'), 'partial')
    await selectValue(container.querySelector('[aria-label="Moved aside · bracelet moved aside feet"]'), 'full')
    await selectValue(container.querySelector('[aria-label="Inventory confirmation"]'), 'true')
    const review = [...container.querySelectorAll('label')]
      .find((item) => item.textContent.includes('I reviewed ownership, every coverage state'))
    await check(review.querySelector('input[type="checkbox"]'))
    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent.includes('Publish immutable profile revision')))

    const profile = publish.mock.calls[0][1].profile
    expect(profile.baseline_coverage.regions).toEqual({ chest: 'none', hips: 'none', legs: 'none', feet: 'none' })
    expect(profile.garment_coverage.bracelet.worn.regions).toEqual({
      chest: 'partial', hips: 'none', legs: 'none', feet: 'none',
    })
    expect(profile.garment_coverage.bracelet.aside.regions).toEqual({
      chest: 'unknown', hips: 'unknown', legs: 'unknown', feet: 'full',
    })
    expect(profile).not.toHaveProperty('takes')
    expect(container.textContent).not.toContain('per take')
  })

  it('selects Unicode text visually, corrects ownership, and previews the exact joined permanent projection', async () => {
    const unicodeLook = {
      ...look,
      appearance: 'A 😀 model with e\u0301. She wears a brass bracelet.',
    }
    vi.spyOn(api, 'getVersioned').mockResolvedValue({
      ...discovery,
      look: { key: unicodeLook.key, version: unicodeLook.version, content_digest: unicodeLook.content_digest },
    })
    const publish = vi.spyOn(api, 'postVersioned').mockResolvedValue({
      profile_revision: 1, profile_digest: 'd'.repeat(64),
    })
    mount(unicodeLook)
    await openEditor()

    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent === 'All appearance is permanent'))
    const permanentPreview = container.querySelector('[aria-label="Profile shortcut preview"]')
    expect(permanentPreview).toBeTruthy()
    await click([...permanentPreview.querySelectorAll('button')]
      .find((item) => item.textContent === 'Confirm this user assertion'))

    const phrase = 'She wears a brass bracelet.'
    const textarea = container.querySelector('[aria-label="Saved appearance for ownership selection"]')
    const utf16Start = unicodeLook.appearance.indexOf(phrase)
    await act(async () => {
      textarea.setSelectionRange(utf16Start, utf16Start + phrase.length)
      textarea.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    })
    await drain()
    expect(container.textContent).toContain(`Selected phrase: “${phrase}”`)
    await selectValue(container.querySelector('[aria-label="Selected phrase owner"]'), 'garment:bracelet')
    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent === 'Assign or correct selected phrase'))
    expect(container.querySelector('[aria-label="Permanent appearance projection preview"]').textContent)
      .toContain('A 😀 model with e\u0301. ')
    expect(container.querySelector('[aria-label="Permanent appearance projection preview"]').textContent)
      .not.toContain(phrase)

    await act(async () => {
      textarea.setSelectionRange(utf16Start, utf16Start + phrase.length)
      textarea.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    })
    await drain()
    await selectValue(container.querySelector('[aria-label="Selected phrase owner"]'), 'permanent')
    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent === 'Assign or correct selected phrase'))
    const joined = container.querySelector('[aria-label="Permanent appearance projection preview"]').textContent
    expect(joined).toContain(unicodeLook.appearance)

    await selectValue(container.querySelector('[aria-label="Baseline · every listed garment absent chest"]'), 'full')
    await selectValue(container.querySelector('[aria-label="Inventory confirmation"]'), 'true')
    const review = [...container.querySelectorAll('label')]
      .find((item) => item.textContent.includes('I reviewed ownership, every coverage state'))
    await check(review.querySelector('input[type="checkbox"]'))
    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent.includes('Publish immutable profile revision')))

    const profile = publish.mock.calls[0][1].profile
    expect(profile.appearance_segments.every((segment) => segment.owner === 'permanent')).toBe(true)
    expect(profile.appearance_segments.map((segment) => segment.text).join('')).toBe(unicodeLook.appearance)
    expect(profile.appearance_segments[0].start).toBe(0)
    expect(profile.appearance_segments.at(-1).end).toBe(Array.from(unicodeLook.appearance).length)
    expect(profile.appearance_segments.every((segment) => (
      Array.from(unicodeLook.appearance).slice(segment.start, segment.end).join('') === segment.text
    ))).toBe(true)
  })

  it('disables edit controls during a copy preview and invalidates the preview when the source revision changes', async () => {
    const secondDigest = 'e'.repeat(64)
    const sourceLook = {
      ...look, version: 1, content_digest: 'f'.repeat(64),
      appearance: 'A source-only appearance.',
    }
    const sourceIdentity = {
      key: sourceLook.key, version: sourceLook.version,
      content_digest: sourceLook.content_digest,
    }
    const profile = {
      schema_version: 1,
      look: sourceIdentity,
      profile_revision: 1,
      profile_digest: profileDigest,
      appearance_segments: [{ segment_id: 'fixed', start: 0, end: Array.from(sourceLook.appearance).length,
        text: sourceLook.appearance, owner: 'permanent', provenance }],
      garment_coverage: { bracelet: {
        worn: { regions, provenance },
        aside: { regions, provenance },
      } },
      baseline_coverage: null,
      inventory_complete: { value: true, provenance },
      authored_provenance: provenance,
    }
    const exactProfile2 = { ...profile, profile_revision: 2, profile_digest: secondDigest }
    vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => {
      if (path.includes('look_key=')) return path.includes(`profile_revision=2`)
        ? exactProfile2 : profile
      if (path === `/api/looks/${look.key}/versions/1`) return sourceLook
      const sourceCatalogue = path.includes(`/versions/1/automation-profiles?`)
      return sourceCatalogue ? {
        look: sourceIdentity,
        profiles: [1, 2].map((revision) => ({
          profile_revision: revision,
          profile_digest: revision === 1 ? profileDigest : secondDigest,
          readiness: { profile_ready: true, manual_available: true, diagnostics: [] },
        })),
        latest_profile_revision: 2,
      } : discovery
    })
    let resolvePreview
    const post = vi.spyOn(api, 'postVersioned').mockImplementation(() => new Promise((resolve) => {
      resolvePreview = resolve
    }))
    mount()
    await openEditor()

    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent === 'No coverage in supported regions'))
    const noCoverageTargets = container.querySelector('[aria-label="No-coverage target selection"]')
    await check(noCoverageTargets.querySelector('[aria-label="Assert no coverage for Baseline · every listed garment absent"]'))

    await selectValue(container.querySelector('[aria-label="Source look version"]'), '1')
    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent === 'Load source version'))

    await selectSourceWornCoverage()
    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent.includes('Preview selected reviewed copy')))
    expect(container.querySelector('[aria-label="Baseline · every listed garment absent chest"]').disabled).toBe(true)
    expect(noCoverageTargets.querySelector('[aria-label^="Assert no coverage"]').disabled).toBe(true)
    expect([...container.querySelectorAll('button')]
      .find((item) => item.textContent === 'Confirm this user assertion').disabled).toBe(true)
    expect(post).toHaveBeenCalledTimes(1)

    await act(async () => resolvePreview({
      target: { look_key: look.key, version: 2, content_digest: look.content_digest, expected_profile_revision: 0, profile_revision: 1 },
      source: { look_key: sourceLook.key, version: 1, content_digest: sourceLook.content_digest, profile_revision: 1, profile_digest: profileDigest },
      selections: [{ garment_key: 'bracelet', source_state: 'worn', state: 'worn' }],
      garment_coverage: { bracelet: { worn: { regions, provenance } } },
      skipped: [],
    }))
    await drain()
    expect(container.querySelector('[aria-label="Reviewed coverage copy preview"]')).toBeTruthy()

    await selectValue(container.querySelector('[aria-label="Exact source profile revision"]'), '2')
    expect(container.querySelector('[aria-label="Reviewed coverage copy preview"]')).toBeNull()
    expect(container.textContent).not.toContain(`Exact source profile: revision 1 · ${profileDigest}`)
  })
})
