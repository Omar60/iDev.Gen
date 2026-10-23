// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Resources from './Resources.jsx'
import { api } from '../api.js'
const digest = 'a'.repeat(64)
const library = { id: 1, library_key: 'studio_rooms', display_name: 'Studio Rooms', kind: 'rooms', auxiliary: [], revisions: [
  { revision_id: 1, source_id: 'room-one', content_digest: digest, readiness: { status: 'ready', pending_fields: {} } },
  { revision_id: 2, source_id: 'room-two', content_digest: 'b'.repeat(64), readiness: { status: 'ready', pending_fields: {} } },
] }
let root, box, post, get, trigger
const flush = () => act(async () => { for (let n = 0; n < 24; n++) await Promise.resolve() })
const button = text => [...box.querySelectorAll('button')].find(b => b.textContent.trim() === text)
const click = async element => { expect(element).toBeTruthy(); await act(async () => element.click()); await flush() }
const edit = async (selector, value) => { const input = box.querySelector(selector); await act(async () => { Object.getOwnPropertyDescriptor(input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value').set.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })) }); await flush() }
const select = async (selector, value) => { const input = box.querySelector(selector); await act(async () => { input.value = value; input.dispatchEvent(new Event('change', { bubbles: true })) }); await flush() }
const open = async () => { trigger = button('Create session'); trigger.focus(); await click(trigger) }
beforeEach(async () => {
  window.location.hash = ''
  box = document.createElement('div'); document.body.append(box); root = createRoot(box)
  get = vi.spyOn(api, 'get').mockImplementation(async url => url === '/api/resources/libraries' ? [library] : url === '/api/models' ? [{ id: 1, name: 'Ada Studio', workflow_id: 12 }, { id: 2, name: 'No default', workflow_id: null }] : url === '/api/config' ? { llm_ok: false } : [])
  vi.spyOn(api, 'getVersioned').mockResolvedValue([])
  vi.spyOn(api, 'post').mockResolvedValue({})
  post = vi.spyOn(api, 'postWithStatus').mockRejectedValue(new Error('Connection interrupted'))
  await act(async () => root.render(<Resources />)); await flush()
})
afterEach(() => { act(() => root.unmount()); box.remove(); vi.restoreAllMocks() })
it('keeps the four initial inputs and exact identity without starting any operation', async () => {
  await open()
  const initial = box.querySelector('[aria-label="Initial authoring inputs"]')
  expect([...initial.querySelectorAll('input,select,textarea')].map(e => e.id)).toEqual(['guided-character','guided-scene','guided-photo-count','guided-brief'])
  expect(box.querySelector('#guided-photo-count').value).toBe('12')
  expect(box.querySelector('#guided-mode').value).toBe('manual')
  expect(box.querySelector('[aria-label="Selected session identity"]').textContent).toContain(digest)
  for (const input of initial.querySelectorAll('input,select,textarea')) { expect(input.labels.length).toBe(1); expect(document.getElementById(input.getAttribute('aria-describedby'))).not.toBe(null) }
  expect(document.activeElement.textContent).toBe('Start a guided session')
  expect(post).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled()
})
it('retains photo count and brief when the scene changes without stealing focus', async () => {
  await open(); await edit('#guided-photo-count', '24'); await edit('#guided-brief', 'Keep the studio mood')
  box.querySelector('#guided-scene').focus()
  await select('#guided-scene', JSON.stringify(['studio_rooms','room-two','b'.repeat(64)]))
  expect(box.querySelector('#guided-photo-count').value).toBe('24')
  expect(box.querySelector('#guided-brief').value).toBe('Keep the studio mood')
  expect(document.activeElement.id).toBe('guided-scene')
  expect(box.querySelector('[aria-label="Selected session identity"]').textContent).toContain('b'.repeat(64))
  expect(post).not.toHaveBeenCalled()
})
it('focuses validation errors and preserves input without a creation request', async () => {
  await open(); await edit('#guided-photo-count','0'); await edit('#guided-brief','Retain this brief')
  await click(button('Create guided session'))
  expect(document.activeElement.getAttribute('role')).toBe('alert')
  expect(document.activeElement.textContent).toContain('integer from 1 through 500')
  expect(box.querySelector('#guided-brief').value).toBe('Retain this brief')
  expect(post).not.toHaveBeenCalled()
})
it('keeps Full Auto ineligible without implicit creation and leaves manual creation available', async () => {
  await open(); await click(box.querySelector('#guided-full-auto'))
  expect(box.querySelector('[aria-label="Full Auto eligibility"]').textContent).toContain('Required: text assistant')
  expect(button('Generate without pauses')).toBeUndefined()
  expect(button('Create manual/assisted session').disabled).toBe(false)
  expect(post).not.toHaveBeenCalled()
})
it('keeps an unknown creation request frozen and replays only through its explicit retry', async () => {
  await open(); await edit('#guided-brief','Recoverable brief'); await click(button('Create guided session'))
  const request = post.mock.calls[0][1]
  expect(Object.isFrozen(request)).toBe(true)
  expect(document.activeElement.textContent).toContain('The creation result is unknown')
  expect(box.querySelector('#guided-brief').disabled).toBe(true)
  expect(box.querySelector('#guided-brief').value).toBe('Recoverable brief')
  expect(button('Retry creation').closest('[role=alert]')).not.toBe(null)
  await click(button('Retry creation')); expect(post.mock.calls[1][1]).toBe(request)
})
it('returns focus to the opening control on Cancel', async () => {
  await open(); button('Cancel').focus(); await click(button('Cancel'))
  expect(box.querySelector('[aria-label="Guided session setup"]')).toBe(null)
  expect(document.activeElement).toBe(trigger)
  expect(post).not.toHaveBeenCalled()
})
it('shows actionable workflow blocking without replacing the current brief', async () => {
  await open(); await edit('#guided-brief','Keep this'); await select('#guided-character','2')
  expect(box.textContent).toContain('This character has no default workflow')
  expect(button('Create guided session').disabled).toBe(true)
  expect(box.querySelector('#guided-brief').value).toBe('Keep this')
  expect(box.querySelector('a[href="#/model/2"]')).not.toBe(null)
})
