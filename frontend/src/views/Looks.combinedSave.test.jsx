// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRoot } from 'react-dom/client'
import { api } from '../api.js'
import { draftContentDigest, prospectiveSource } from '../looksAutomationReview.js'
import Looks from './Looks.jsx'

const contentDigest = 'a'.repeat(64)

function combinedResult(body, overrides = {}) {
  return {
    key: 'look-combined',
    version: 1,
    name: body.name,
    content_digest: contentDigest,
    appearance: body.appearance,
    outfit: null,
    automation_profile: {
      look_key: 'look-combined',
      version: 1,
      content_digest: contentDigest,
      profile_revision: 1,
      profile_digest: 'b'.repeat(64),
      readiness: { profile_ready: true, manual_available: true, diagnostics: [] },
    },
    ...overrides,
  }
}

describe('combined look and automation save', () => {
  let container
  let root

  const flush = () => new Promise((resolve) => setTimeout(resolve, 0))
  const waitFor = async (predicate) => {
    for (let attempt = 0; attempt < 20 && !predicate(); attempt += 1) {
      await act(async () => { await flush() })
    }
    expect(predicate()).toBeTruthy()
  }
  const render = async () => act(async () => { root.render(<Looks />); await flush() })
  const click = async (element) => {
    expect(element).toBeTruthy()
    await act(async () => { element.click(); await flush() })
  }
  const button = (label) => Array.from(container.querySelectorAll('button'))
    .find((item) => item.textContent.trim() === label || item.getAttribute('aria-label') === label)
  const field = (label) => container.querySelector(`[aria-label="${label}"]`)
  const setValue = async (element, value) => {
    expect(element).toBeTruthy()
    const prototype = element instanceof HTMLSelectElement
      ? window.HTMLSelectElement.prototype
      : element instanceof HTMLTextAreaElement
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype
    await act(async () => {
      Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value)
      element.dispatchEvent(new Event('input', { bubbles: true }))
      element.dispatchEvent(new Event('change', { bubbles: true }))
      await flush()
    })
  }
  const checkLabel = (text) => [...container.querySelectorAll('label')]
    .find((label) => label.textContent.includes(text))?.querySelector('input[type="checkbox"]')
  const accept = async (checkbox) => {
    expect(checkbox).toBeTruthy()
    await act(async () => { checkbox.click(); await flush() })
  }
  const readyManualDraft = async (name = 'Reviewed look') => {
    await click(button('Create look'))
    await setValue(field('Look name'), name)
    for (const region of ['chest', 'hips', 'legs', 'feet']) {
      await setValue(field(`Baseline · every listed garment absent ${region}`), 'none')
    }
    await setValue(field('Inventory confirmation'), 'true')
    await accept(checkLabel('I confirm the complete garment list and removal order shown above.'))
    await accept(checkLabel('I reviewed ownership, every coverage state, the baseline and inventory for this exact look draft.'))
    expect(button('Save look + automation profile').disabled).toBe(false)
  }
  const validResult = (body) => combinedResult(body)
  const validVersionResult = (body) => {
    const result = combinedResult(body, { key: 'look-target', version: 2 })
    result.automation_profile = {
      ...result.automation_profile,
      look_key: result.key,
      version: result.version,
    }
    return result
  }
  const openTargetLook = async () => {
    const detail = {
      key: 'look-target', version: 1, name: 'Original look',
      content_digest: 'c'.repeat(64), appearance: '', outfit: null,
    }
    api.getVersioned.mockImplementation(async (path) => path === '/api/looks'
      ? [{ key: detail.key, version: detail.version, name: detail.name, content_digest: detail.content_digest }]
      : detail)
    await render()
    await click(button('Original look · version 1'))
    await setValue(field('Look name'), 'Edited target')
  }
  const reviewCurrentDraft = async () => {
    for (const region of ['chest', 'hips', 'legs', 'feet']) {
      await setValue(field(`Baseline · every listed garment absent ${region}`), 'none')
    }
    await setValue(field('Inventory confirmation'), 'true')
    await accept(checkLabel('I confirm the complete garment list and removal order shown above.'))
    await accept(checkLabel('I reviewed ownership, every coverage state, the baseline and inventory for this exact look draft.'))
  }

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    vi.restoreAllMocks()
    vi.spyOn(api, 'getVersioned').mockResolvedValue([])
    vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === '/api/wardrobe') return { garments: [], outfits: [] }
      if (path === '/api/config') return { llm_ok: false, llm_vision_model: '' }
      throw new Error(`Unexpected GET ${path}`)
    })
    vi.spyOn(api, 'postVersioned').mockImplementation(async (_path, body) => validResult(body))
    vi.spyOn(api, 'post').mockRejectedValue(new Error('Unexpected POST'))
  })

  afterEach(() => {
    act(() => root?.unmount())
    container?.remove()
    container = null
    vi.restoreAllMocks()
  })

  it('shows unknown blockers and allows look-only saving only through its explicit action', async () => {
    await render()
    await click(button('Create look'))
    await setValue(field('Look name'), 'Look without complete facts')
    expect(button('Save look + automation profile').disabled).toBe(true)
    expect(container.querySelector('[aria-label="Combined save blockers"]')?.textContent)
      .toContain('Baseline coverage needs a known value')
    expect(container.textContent).toContain('Unknown is never treated as none.')

    await click(button('Save look only'))

    expect(api.postVersioned).toHaveBeenCalledTimes(1)
    expect(api.postVersioned).toHaveBeenCalledWith('/api/looks', {
      name: 'Look without complete facts', appearance: '', garments: [],
    }, [])
    expect(container.textContent).toContain('no automation profile was created')
  })

  it('creates the look and ready profile in one request after explicit review', async () => {
    await render()
    await readyManualDraft()
    await click(button('Save look + automation profile'))

    expect(api.postVersioned).toHaveBeenCalledTimes(1)
    const [path, body, integerFields] = api.postVersioned.mock.calls[0]
    expect(path).toBe('/api/looks')
    expect(integerFields).toEqual([])
    expect(body).toMatchObject({
      name: 'Reviewed look',
      appearance: '',
      garments: [],
      automation: {
        profile_review_confirmed: true,
        inventory_complete_confirmed: true,
        baseline_coverage_confirmed: true,
        removal_order_confirmed: true,
        profile: {
          schema_version: 1,
          appearance_segments: [],
          garment_coverage: [],
          baseline_coverage: { regions: { chest: 'none', hips: 'none', legs: 'none', feet: 'none' } },
          inventory_complete: true,
        },
      },
    })
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/i)
    await expect(draftContentDigest(prospectiveSource('', [])))
      .resolves.toBe(body.automation.profile.draft_content_digest)
    expect(container.textContent).toContain('automation profile revision 1')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('blocks double-click writes and replays the exact frozen body and request ID after response loss', async () => {
    let resolveWrite
    const writeGate = new Promise((resolve) => { resolveWrite = resolve })
    api.postVersioned.mockRejectedValueOnce(new TypeError('connection lost'))
      .mockImplementationOnce(() => writeGate)
    await render()
    await readyManualDraft('Retry look')
    const save = button('Save look + automation profile')
    await act(async () => { save.click(); save.click(); await flush() })
    expect(api.postVersioned).toHaveBeenCalledTimes(1)
    const firstBody = api.postVersioned.mock.calls[0][1]
    expect(firstBody.request_id).toMatch(/^[0-9a-f-]{36}$/i)
    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeTruthy()
    expect(field('Look name').disabled).toBe(true)

    await click(button('Retry the identical combined save'))
    expect(api.postVersioned).toHaveBeenCalledTimes(2)
    expect(api.postVersioned.mock.calls[1][0]).toBe(api.postVersioned.mock.calls[0][0])
    expect(api.postVersioned.mock.calls[1][1]).toBe(firstBody)
    expect(api.postVersioned.mock.calls[1][1].request_id).toBe(firstBody.request_id)
    resolveWrite(validResult(firstBody))
    await act(async () => { await flush() })
    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeNull()
    expect(container.textContent).toContain('automation profile revision 1')
  })

  it('releases only a stable known pre-write refusal while preserving the editable form', async () => {
    const refusal = Object.assign(new Error('The reviewed profile is incomplete.'), {
      status: 422,
      hasStableErrorBody: true,
      detail: { code: 'automation_profile_not_ready', message: 'The reviewed profile is incomplete.' },
    })
    api.postVersioned.mockRejectedValueOnce(refusal)
    await render()
    await readyManualDraft('Refused look')
    await click(button('Save look + automation profile'))

    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeNull()
    expect(field('Look name').disabled).toBe(false)
    expect(field('Look name').value).toBe('Refused look')
    expect(button('Save look + automation profile').disabled).toBe(false)
  })

  it('treats a malformed success identity as unknown and keeps the same replayable request', async () => {
    api.postVersioned.mockImplementationOnce(async (_path, body) => combinedResult(body, {
      automation_profile: {
        ...combinedResult(body).automation_profile,
        profile_revision: 2,
      },
    }))
    await render()
    await readyManualDraft('Malformed result look')
    await click(button('Save look + automation profile'))
    const originalBody = api.postVersioned.mock.calls[0][1]

    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeTruthy()
    expect(field('Look name').disabled).toBe(true)
    await click(button('Retry the identical combined save'))
    expect(api.postVersioned.mock.calls[1][1]).toBe(originalBody)
    expect(api.postVersioned.mock.calls[1][1].request_id).toBe(originalBody.request_id)
    expect(container.textContent).toContain('automation profile revision 1')
  })

  it.each([
    ['look key', (result) => {
      result.key = 'look-other'
      result.automation_profile.look_key = 'look-other'
    }],
    ['successor version', (result) => {
      result.version = 3
      result.automation_profile.version = 3
    }],
    ['saved appearance', (result) => { result.appearance = 'different appearance' }],
    ['saved name', (result) => { result.name = 'Different name' }],
  ])('binds a combined result to the requested %s and replays only that frozen request', async (_label, alter) => {
    await openTargetLook()
    await setValue(field('Constant appearance'), 'short dark hair')
    await click(button('All appearance is permanent'))
    await click(button('Confirm this user assertion'))
    await reviewCurrentDraft()

    api.postVersioned.mockImplementationOnce(async (_path, body) => {
      const result = validVersionResult(body)
      alter(result)
      return result
    }).mockImplementationOnce(async (_path, body) => validVersionResult(body))
    await click(button('Save look + automation profile'))
    const [path, body] = api.postVersioned.mock.calls.at(-1)

    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeTruthy()
    expect(container.textContent).not.toContain('automation profile revision 1. The profile is ready.')
    await click(button('Retry the identical combined save'))
    expect(api.postVersioned).toHaveBeenCalledTimes(2)
    expect(api.postVersioned.mock.calls[0][0]).toBe('/api/looks/look-target/versions')
    expect(api.postVersioned.mock.calls[1][0]).toBe(path)
    expect(api.postVersioned.mock.calls[1][1]).toBe(body)
    expect(api.postVersioned.mock.calls[1][1].request_id).toBe(body.request_id)
    expect(container.textContent).toContain('Saved look version 2 and automation profile revision 1')
  })

  it('rejects an internally consistent response that changes the submitted outfit rows', async () => {
    await openTargetLook()
    await setValue(field('Optional outfit'), '__manual__')
    await click(button('Add new garment'))
    await setValue(field('Garment 1 worn wording'), 'a blue jacket')
    for (const region of ['chest', 'hips', 'legs', 'feet']) {
      await setValue(field(`Baseline · every listed garment absent ${region}`), 'none')
      await setValue(field(`a blue jacket · worn ${region}`), 'full')
    }
    await setValue(field('Inventory confirmation'), 'true')
    await accept(checkLabel('I confirm the complete garment list and removal order shown above.'))
    await accept(checkLabel('I reviewed ownership, every coverage state, the baseline and inventory for this exact look draft.'))

    api.postVersioned.mockImplementationOnce(async (_path, body) => combinedResult(body, {
      key: 'look-target',
      version: 2,
      outfit: { outfit_key: 'outfit-target', garments: [{ key: 'garment-1', wording: 'a green coat', aside: '' }] },
      automation_profile: {
        ...combinedResult(body).automation_profile,
        look_key: 'look-target',
        version: 2,
      },
    })).mockImplementationOnce(async (_path, body) => combinedResult(body, {
      key: 'look-target',
      version: 2,
      outfit: { outfit_key: 'outfit-target', garments: [{ key: 'garment-1', wording: 'a blue jacket', aside: '' }] },
      automation_profile: {
        ...combinedResult(body).automation_profile,
        look_key: 'look-target',
        version: 2,
      },
    }))
    await click(button('Save look + automation profile'))
    const frozenBody = api.postVersioned.mock.calls[0][1]
    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeTruthy()
    await click(button('Retry the identical combined save'))
    expect(api.postVersioned.mock.calls[1][1]).toBe(frozenBody)
    expect(api.postVersioned.mock.calls[1][1].request_id).toBe(frozenBody.request_id)
    expect(container.textContent).toContain('Saved look version 2 and automation profile revision 1')
  })

  it('retains an in-flight request across editor unmount and blocks a new surface until its outcome is known', async () => {
    let resolveWrite
    const pending = new Promise((resolve) => { resolveWrite = resolve })
    api.postVersioned.mockImplementationOnce(() => pending)
    await render()
    await readyManualDraft('Unmounted attempt')
    await click(button('Save look + automation profile'))
    const originalBody = api.postVersioned.mock.calls[0][1]
    expect(container.textContent).toContain('still awaiting a response')

    await act(async () => { root.render(<Looks key="new-surface" />); await flush() })
    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeTruthy()
    expect(button('Create look').disabled).toBe(true)
    resolveWrite(validResult(originalBody))
    await act(async () => { await flush() })

    expect(container.querySelector('[aria-label="Combined save recovery"]')).toBeNull()
    expect(container.textContent).toContain('completed after its editor closed')
    expect(button('Create look').disabled).toBe(false)
  })

  it('replays the exact unknown request after remount without allocating a new UUID', async () => {
    let rejectWrite
    const pending = new Promise((_resolve, reject) => { rejectWrite = reject })
    api.postVersioned.mockImplementationOnce(() => pending)
    await render()
    await readyManualDraft('Recover after remount')
    await click(button('Save look + automation profile'))
    const originalPath = api.postVersioned.mock.calls[0][0]
    const originalBody = api.postVersioned.mock.calls[0][1]
    await act(async () => { root.render(<Looks key="retry-surface" />); await flush() })

    rejectWrite(new TypeError('connection lost'))
    await act(async () => { await flush() })
    expect(container.querySelector('[aria-label="Combined save recovery"]')?.textContent)
      .toContain('outcome is unknown')
    await click(button('Retry the identical combined save'))

    expect(api.postVersioned).toHaveBeenCalledTimes(2)
    expect(api.postVersioned.mock.calls[1][0]).toBe(originalPath)
    expect(api.postVersioned.mock.calls[1][1]).toBe(originalBody)
    expect(api.postVersioned.mock.calls[1][1].request_id).toBe(originalBody.request_id)
    expect(container.textContent).toContain('Saved look version 1 and automation profile revision 1')
  })

  it('ignores an A-to-B-to-A proposal response after the source changed while pending', async () => {
    let resolveProposal
    const proposalGate = new Promise((resolve) => { resolveProposal = resolve })
    const post = vi.spyOn(api, 'post').mockReturnValue(proposalGate)
    await render()
    await click(button('Create look'))
    await setValue(field('Look name'), 'Proposal fence')
    await setValue(field('Constant appearance'), 'dark hair')
    await click(button('Suggest profile facts'))
    await act(async () => { await flush() })
    const proposalSource = post.mock.calls[0][1]
    const digest = await draftContentDigest(proposalSource)
    const profile = {
      schema_version: 1,
      draft_content_digest: digest,
      appearance_segments: [{
        segment_id: 'suggested-permanent', start: 0, end: 9, text: 'dark hair', owner: 'permanent',
      }],
      garment_coverage: [],
      baseline_coverage: null,
      inventory_complete: null,
    }
    await setValue(field('Constant appearance'), 'light hair')
    await setValue(field('Constant appearance'), 'dark hair')
    resolveProposal({ proposal_id: 'c'.repeat(32), draft_content_digest: digest, profile })
    await act(async () => { await flush() })

    expect(container.innerHTML).toContain('Appearance ownership preview')
    expect(container.querySelector('[aria-label="Appearance ownership preview"]')?.textContent)
      .toContain('Unassigned')
    expect(container.textContent).not.toContain('An optional suggestion is linked')
  })

  it('keeps photo uncertainty and order confirmation separate while mapping reordered suggestion rows by identity', async () => {
    const stage = {
      photo_id: 'b'.repeat(32), state: 'staged', created_at: '2026-10-05T10:00:00+00:00',
      expires_at: '2026-10-06T10:00:00+00:00', format: 'PNG', media_type: 'image/png',
      byte_count: 68, width: 1, height: 1, cleanup_warning: null, saved_look: null,
    }
    const appearance = 'short dark hair'
    const proposalId = 'c'.repeat(32)
    const initialRows = [
      { id: 'first-photo-row', key: null, wording: 'a blue jacket', aside: '' },
      { id: 'second-photo-row', key: null, wording: 'silver earrings', aside: '' },
    ]
    const initialDigest = await draftContentDigest(prospectiveSource(appearance, initialRows))
    const regionValues = (value) => ({ chest: value, hips: value, legs: value, feet: value })
    const extraction = {
      proposal_id: proposalId,
      appearance,
      garments: ['a blue jacket', 'silver earrings'],
      unresolved: [{ id: 'u1', detail: 'the exact shade of the hair' }],
      automation: {
        status: 'ready',
        proposal_id: proposalId,
        draft_content_digest: initialDigest,
        profile: {
          schema_version: 1,
          draft_content_digest: initialDigest,
          appearance_segments: [{
            segment_id: 'photo-appearance', start: 0, end: 15, text: appearance,
            owner: { garment_index: 0 },
          }],
          garment_coverage: [
            { garment_index: 0, states: { worn: { regions: regionValues('full') } } },
            { garment_index: 1, states: { worn: { regions: regionValues('partial') } } },
          ],
          baseline_coverage: null,
          inventory_complete: null,
        },
      },
    }
    api.get.mockImplementation(async (path) => {
      if (path === '/api/wardrobe') return { garments: [], outfits: [] }
      if (path === '/api/config') return { llm_ok: true, llm_vision_model: 'declared-vision-model' }
      if (path === `/api/looks/photo-stages/${stage.photo_id}`) return { ...stage, state: 'saved' }
      throw new Error(`Unexpected GET ${path}`)
    })
    vi.spyOn(api, 'uploadMultipart').mockResolvedValue(stage)
    api.post.mockImplementation(async (path) => {
      if (path.endsWith('/extract')) return extraction
      throw new Error(`Unexpected POST ${path}`)
    })
    api.postVersioned.mockImplementation(async (_path, body) => combinedResult(body, {
      outfit: {
        outfit_key: 'photo-outfit',
        garments: body.garments.map((garment, index) => ({ key: `photo-garment-${index}`, ...garment })),
      },
    }))
    const choosePhoto = async () => {
      const input = field('Choose one photo')
      Object.defineProperty(input, 'files', {
        configurable: true,
        value: [new File([new Uint8Array([137, 80, 78, 71])], 'synthetic.png', { type: 'image/png' })],
      })
      await act(async () => { input.dispatchEvent(new Event('change', { bubbles: true })); await flush() })
    }

    await render()
    await choosePhoto()
    const optIn = [...container.querySelectorAll('label')]
      .find((label) => label.textContent.includes('Include optional automation profile suggestions'))
      ?.querySelector('input[type="checkbox"]')
    await accept(optIn)
    await click(button('Extract look + profile suggestions'))
    expect(api.post).toHaveBeenCalledWith(`/api/looks/photo-stages/${stage.photo_id}/extract`, {
      include_automation: true,
    })
    await waitFor(() => checkLabel('I reviewed ownership, every coverage state, the baseline and inventory for this exact look draft.'))
    expect(checkLabel('I reviewed ownership, every coverage state, the baseline and inventory for this exact look draft.').checked)
      .toBe(false)
    expect(field('Inventory confirmation').value).toBe('')
    expect(field('Resolution for uncertain detail 1').value).toBe('')

    await setValue(field('Look name'), 'Photo look')
    await setValue(field('Resolution for uncertain detail 1'), 'correct')
    await setValue(field('Correction for uncertain detail 1'), 'short dark hair')
    await click(button('Move garment 2 up'))
    expect(checkLabel('I reviewed the appearance and corrected or omitted every uncertain detail.').checked).toBe(false)
    expect(checkLabel('I confirm the complete garment list and removal order shown above.').checked).toBe(false)

    for (const region of ['chest', 'hips', 'legs', 'feet']) {
      await setValue(field(`Baseline · every listed garment absent ${region}`), 'none')
    }
    await setValue(field('Inventory confirmation'), 'true')
    await accept(checkLabel('I confirm the complete garment list and removal order shown above.'))
    await accept(checkLabel('I reviewed the appearance and corrected or omitted every uncertain detail.'))
    await accept(checkLabel('I reviewed ownership, every coverage state, the baseline and inventory for this exact look draft.'))
    expect(button('Save look + automation profile').disabled).toBe(false)
    await click(button('Save look + automation profile'))

    expect(api.postVersioned).toHaveBeenCalledTimes(1)
    const [path, body] = api.postVersioned.mock.calls[0]
    expect(path).toBe(`/api/looks/photo-stages/${stage.photo_id}/save-extracted`)
    expect(body.proposal_id).toBe(proposalId)
    expect(body.unresolved_decisions).toEqual([{ id: 'u1', action: 'correct', correction: 'short dark hair' }])
    expect(body.review_confirmed).toBe(true)
    expect(body.removal_order_confirmed).toBe(true)
    expect(body.automation.removal_order_confirmed).toBe(true)
    expect(body.automation.suggestion).toEqual({ proposal_id: proposalId, draft_content_digest: initialDigest })
    expect(body.automation.profile.draft_content_digest).not.toBe(initialDigest)
    expect(body.garments.map((row) => row.wording)).toEqual(['silver earrings', 'a blue jacket'])
    expect(body.automation.profile.appearance_segments[0].owner).toEqual({ garment_index: 1 })
    expect(body.automation.profile.garment_coverage[0].states.worn.regions).toEqual(regionValues('partial'))
    expect(body.automation.profile.garment_coverage[1].states.worn.regions).toEqual(regionValues('full'))
  })
})
