// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api.js'
import AutomationProfileEditor from './AutomationProfileEditor.jsx'

const look = {
  key: 'look-profile-editor',
  version: 1,
  name: 'Reviewed evening look',
  content_digest: 'a'.repeat(64),
  appearance: 'Hair swept back. She wears a brass bracelet.',
  outfit: {
    outfit_key: 'outfit-profile-editor',
    garments: [{ key: 'bracelet', wording: 'a brass bracelet', aside: 'bracelet moved aside' }],
  },
}
const profileDigest = 'b'.repeat(64)
const regions = { chest: 'full', hips: 'partial', legs: 'none', feet: 'unknown' }
const provenance = {
  origin: 'user', source_identity: 'looks-profile-editor',
  source_digest: look.content_digest, accepted_revision: 1,
}
const sourceProfile = {
  schema_version: 1,
  look: { key: look.key, version: look.version, content_digest: look.content_digest },
  profile_revision: 1,
  profile_digest: profileDigest,
  appearance_segments: [{
    segment_id: 'all-permanent', start: 0, end: Array.from(look.appearance).length,
    text: look.appearance, owner: 'permanent', provenance,
  }],
  garment_coverage: { bracelet: {
    worn: { regions, provenance },
    aside: { regions: { chest: 'none', hips: 'none', legs: 'none', feet: 'none' }, provenance },
  } },
  baseline_coverage: { regions: { chest: 'none', hips: 'none', legs: 'none', feet: 'none' }, provenance },
  inventory_complete: { value: true, provenance },
  authored_provenance: provenance,
}
const discovery = {
  look: { key: look.key, version: look.version, content_digest: look.content_digest },
  latest_profile_revision: 1,
  profiles: [{ profile_revision: 1, profile_digest: profileDigest,
    readiness: { profile_ready: true, manual_available: true, diagnostics: [] } }],
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

async function setChecked(element, checked = true) {
  expect(element).toBeTruthy()
  if (element.checked !== checked) await click(element)
}

function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<AutomationProfileEditor look={look} />))
}

beforeEach(() => vi.restoreAllMocks())

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  container = null
  vi.restoreAllMocks()
})

describe('reusable automation profile editor', () => {
  it('previews and confirms permanent/no-coverage shortcuts, then publishes explicit structured facts', async () => {
    vi.spyOn(api, 'getVersioned').mockResolvedValue({
      ...discovery, latest_profile_revision: 0, profiles: [],
    })
    const publish = vi.spyOn(api, 'postVersioned').mockResolvedValue({
      profile_revision: 1, profile_digest: 'c'.repeat(64),
    })
    mount()
    await click(container.querySelector('summary'))
    await drain()

    await click([...container.querySelectorAll('button')].find((item) => item.textContent === 'All appearance is permanent'))
    expect(container.querySelector('[aria-label="Profile shortcut preview"]')).toBeTruthy()
    await click([...container.querySelectorAll('button')].find((item) => item.textContent === 'Confirm this user assertion'))
    await click([...container.querySelectorAll('button')].find((item) => item.textContent === 'No coverage in supported regions'))
    const noCoverageSelection = container.querySelector('[aria-label="No-coverage target selection"]')
    const baselineTarget = noCoverageSelection.querySelector('[aria-label="Assert no coverage for Baseline · every listed garment absent"]')
    const wornTarget = noCoverageSelection.querySelector('[aria-label="Assert no coverage for a brass bracelet · worn"]')
    await setChecked(baselineTarget)
    await setChecked(wornTarget)
    expect(noCoverageSelection.textContent).toContain('only in Baseline · every listed garment absent; a brass bracelet · worn')
    expect(noCoverageSelection.querySelector('[aria-label="Profile shortcut preview"]').textContent).not.toContain('moved aside')
    await click([...container.querySelectorAll('button')].find((item) => item.textContent === 'Confirm this user assertion'))

    await selectValue(container.querySelector('[aria-label="Inventory confirmation"]'), 'true')
    const review = [...container.querySelectorAll('label')]
      .find((item) => item.textContent.includes('I reviewed ownership, every coverage state'))
    await setChecked(review.querySelector('input[type="checkbox"]'))
    const publishButton = [...container.querySelectorAll('button')]
      .find((item) => item.textContent.includes('Publish immutable profile revision'))
    expect(publishButton.disabled).toBe(false)
    await click(publishButton)

    expect(publish).toHaveBeenCalledTimes(1)
    const [path, body, fields] = publish.mock.calls[0]
    expect(path).toBe('/api/looks/automation-profiles')
    expect(body.expected_profile_revision).toBe('0')
    expect(body.profile.appearance_segments).toEqual([expect.objectContaining({ owner: 'permanent', text: look.appearance })])
    expect(container.querySelector('[aria-label="Permanent appearance projection preview"]').textContent).toContain(look.appearance)
    expect(body.profile.baseline_coverage.regions).toEqual({ chest: 'none', hips: 'none', legs: 'none', feet: 'none' })
    expect(body.profile.garment_coverage.bracelet.worn.regions).toEqual({ chest: 'none', hips: 'none', legs: 'none', feet: 'none' })
    expect(body.profile.garment_coverage.bracelet.aside.regions).toEqual({ chest: 'unknown', hips: 'unknown', legs: 'unknown', feet: 'unknown' })
    expect(body.profile.inventory_complete.value).toBe(true)
    expect(body.profile.authored_provenance.accepted_revision).toBe('1')
    expect(fields).toContain('profile.authored_provenance.accepted_revision')
    expect(container.textContent).toContain('Saved exact profile revision 1')
  })

  it('previews and confirms copying a reviewed source fact to an explicitly selected equivalent state', async () => {
    const exactReads = vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => (
      path.includes('look_key=') ? sourceProfile : discovery
    ))
    const preview = vi.spyOn(api, 'postVersioned').mockImplementation(async (path, body) => {
      if (path.includes('copy-preview')) return {
        target: {
          look_key: look.key, version: 1, content_digest: look.content_digest,
          expected_profile_revision: 1, profile_revision: 2,
        },
        source: {
          look_key: look.key, version: 1, content_digest: look.content_digest,
          profile_digest: profileDigest, profile_revision: 1,
        },
        selections: [{ garment_key: 'bracelet', source_state: 'worn', state: 'aside' }],
        garment_coverage: { bracelet: { aside: {
          regions,
          provenance: {
            origin: 'reviewed_structured',
            source_identity: `automation-profile:${profileDigest}`,
            source_digest: profileDigest,
            accepted_revision: 2,
          },
        } } },
        skipped: [],
      }
      return { profile_revision: 2, profile_digest: 'e'.repeat(64) }
    })
    mount()
    await click(container.querySelector('summary'))
    await drain()

    const sourceRevision = [...container.querySelectorAll('select')]
      .find((item) => item.getAttribute('aria-label') === 'Exact source profile revision')
    await selectValue(sourceRevision, '1')
    const copyFact = [...container.querySelectorAll('label')]
      .find((item) => item.textContent.includes('Copy reviewed worn coverage'))
    await setChecked(copyFact.querySelector('input[type="checkbox"]'))
    await selectValue(container.querySelector('[aria-label="Target state for a brass bracelet worn coverage"]'), 'aside')
    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent.includes('Preview selected reviewed copy')))

    expect(preview).toHaveBeenCalledWith('/api/looks/automation-profiles/copy-preview', expect.objectContaining({
      target_version: '1',
      expected_profile_revision: '1',
      source_profile_revision: '1',
      selections: [{ garment_key: 'bracelet', source_state: 'worn', state: 'aside' }],
    }), ['target_version', 'expected_profile_revision', 'source_version', 'source_profile_revision'])
    const previewBox = container.querySelector('[aria-label="Reviewed coverage copy preview"]')
    expect(previewBox.textContent).toContain('worn → aside')
    await setChecked([...previewBox.querySelectorAll('input[type="checkbox"]')][0])
    await click([...previewBox.querySelectorAll('button')].find((item) => item.textContent === 'Apply reviewed copy to draft'))

    await click([...container.querySelectorAll('button')].find((item) => item.textContent === 'All appearance is permanent'))
    await click([...container.querySelectorAll('button')].find((item) => item.textContent === 'Confirm this user assertion'))
    for (const region of ['chest', 'hips', 'legs', 'feet']) {
      await selectValue(container.querySelector(`[aria-label="Baseline · every listed garment absent ${region}"]`), 'none')
    }
    await selectValue(container.querySelector('[aria-label="Inventory confirmation"]'), 'true')
    const review = [...container.querySelectorAll('label')]
      .find((item) => item.textContent.includes('I reviewed ownership, every coverage state'))
    await setChecked(review.querySelector('input[type="checkbox"]'))
    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent.includes('Publish immutable profile revision')))

    const profile = preview.mock.calls[1][1].profile
    expect(profile.garment_coverage.bracelet.aside.regions).toEqual(regions)
    expect(profile.garment_coverage.bracelet.aside.provenance).toEqual({
      origin: 'user',
      source_identity: `automation-profile:${profileDigest}`,
      source_digest: look.content_digest,
      accepted_revision: '2',
    })
    expect(exactReads).toHaveBeenCalled()
  })

  it('locks coverage and shortcut controls during copy preview, then invalidates preview after an edit', async () => {
    let resolveCopyPreview
    vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => (
      path.includes('look_key=') ? sourceProfile : discovery
    ))
    const post = vi.spyOn(api, 'postVersioned').mockImplementation(() => new Promise((resolve) => {
      resolveCopyPreview = resolve
    }))
    mount()
    await click(container.querySelector('summary'))
    await drain()

    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent === 'No coverage in supported regions'))
    await setChecked(container.querySelector('[aria-label="Assert no coverage for Baseline · every listed garment absent"]'))

    await selectValue([...container.querySelectorAll('select')]
      .find((item) => item.getAttribute('aria-label') === 'Exact source profile revision'), '1')
    await setChecked([...container.querySelectorAll('label')]
      .find((item) => item.textContent.includes('Copy reviewed worn coverage'))
      .querySelector('input[type="checkbox"]'))
    await selectValue(container.querySelector('[aria-label="Target state for a brass bracelet worn coverage"]'), 'worn')
    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent.includes('Preview selected reviewed copy')))

    expect(container.querySelector('[aria-label="Baseline · every listed garment absent chest"]').disabled).toBe(true)
    expect([...container.querySelectorAll('button')]
      .find((item) => item.textContent === 'Confirm this user assertion').disabled).toBe(true)
    expect(post).toHaveBeenCalledTimes(1)

    await act(async () => {
      resolveCopyPreview({
        target: {
          look_key: look.key, version: 1, content_digest: look.content_digest,
          expected_profile_revision: 1, profile_revision: 2,
        },
        source: {
          look_key: look.key, version: 1, content_digest: look.content_digest,
          profile_digest: profileDigest, profile_revision: 1,
        },
        selections: [{ garment_key: 'bracelet', source_state: 'worn', state: 'worn' }],
        garment_coverage: { bracelet: { worn: { regions, provenance } } },
        skipped: [],
      })
    })
    await drain()
    expect(container.querySelector('[aria-label="Reviewed coverage copy preview"]')).toBeTruthy()
    await selectValue(container.querySelector('[aria-label="Baseline · every listed garment absent chest"]'), 'full')
    expect(container.querySelector('[aria-label="Reviewed coverage copy preview"]')).toBeNull()
  })

  it('invalidates a confirmed copy preview and source mappings when the exact source revision changes', async () => {
    const secondSourceProfile = {
      ...sourceProfile,
      profile_revision: 2,
      profile_digest: 'd'.repeat(64),
    }
    const catalogue = {
      ...discovery,
      latest_profile_revision: 2,
      profiles: [
        ...discovery.profiles,
        { profile_revision: 2, profile_digest: secondSourceProfile.profile_digest,
          readiness: { profile_ready: true, manual_available: true, diagnostics: [] } },
      ],
    }
    vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => {
      if (!path.includes('look_key=')) return catalogue
      const revision = new URLSearchParams(path.split('?')[1]).get('profile_revision')
      return revision === '2' ? secondSourceProfile : sourceProfile
    })
    vi.spyOn(api, 'postVersioned').mockResolvedValue({
      target: {
        look_key: look.key, version: 1, content_digest: look.content_digest,
        expected_profile_revision: 2, profile_revision: 3,
      },
      source: {
        look_key: look.key, version: 1, content_digest: look.content_digest,
        profile_digest: sourceProfile.profile_digest, profile_revision: 1,
      },
      selections: [{ garment_key: 'bracelet', source_state: 'worn', state: 'worn' }],
      garment_coverage: { bracelet: { worn: { regions, provenance } } },
      skipped: [],
    })
    mount()
    await click(container.querySelector('summary'))
    await drain()

    await selectValue(container.querySelector('[aria-label="Source look version"]'), '1')
    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent === 'Load source version'))
    await drain()
    const sourceRevision = container.querySelector('[aria-label="Exact source profile revision"]')
    await selectValue(sourceRevision, '1')
    await setChecked([...container.querySelectorAll('label')]
      .find((item) => item.textContent.includes('Copy reviewed worn coverage'))
      .querySelector('input[type="checkbox"]'))
    await selectValue(container.querySelector('[aria-label="Target state for a brass bracelet worn coverage"]'), 'worn')
    await click([...container.querySelectorAll('button')]
      .find((item) => item.textContent.includes('Preview selected reviewed copy')))

    const previewBox = container.querySelector('[aria-label="Reviewed coverage copy preview"]')
    expect(previewBox.textContent).toContain('revision 1')
    await setChecked(previewBox.querySelector('input[type="checkbox"]'))
    expect([...previewBox.querySelectorAll('button')]
      .find((item) => item.textContent === 'Apply reviewed copy to draft').disabled).toBe(false)

    await selectValue(sourceRevision, '2')
    expect(container.querySelector('[aria-label="Reviewed coverage copy preview"]')).toBeNull()
    expect([...container.querySelectorAll('label')]
      .find((item) => item.textContent.includes('Copy reviewed worn coverage'))
      .querySelector('input[type="checkbox"]').checked).toBe(false)
    expect([...container.querySelectorAll('button')]
      .some((item) => item.textContent === 'Apply reviewed copy to draft')).toBe(false)
  })
})
