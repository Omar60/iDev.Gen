// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true
import React, { act, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import SessionPhotoViewer from './SessionPhotoViewer.jsx'

let root, container
const photos = [{ id: 11, shot_label: 'Portrait', prompt: 'First prompt', seed: 7, filename: 'first.png' },
  { id: 14, shot_label: 'Wide photo', prompt: 'Second prompt', seed: 8, filename: 'wide.png' }]
const click = async (element) => { expect(element).toBeTruthy(); await act(async () => element.click()) }
const button = (text) => [...container.querySelectorAll('button')].find((item) => item.textContent === text)
const key = async (element, value) => act(async () => element.dispatchEvent(new KeyboardEvent('keydown', { key: value, bubbles: true })))
const mount = async ({ comparison = null, current = 11 } = {}) => {
  function Harness() {
    const [selected, select] = useState(null)
    const [split, setSplit] = useState(50)
    const fallbackFocus = useRef(null)
    return <><h2 ref={fallbackFocus} tabIndex={-1}>Gallery</h2><button onClick={() => select(current)}>Open</button>
      {selected !== null && <SessionPhotoViewer photos={photos} selectedId={selected} onSelect={(photo) => select(photo.id)}
        onClose={() => select(null)} comparisonFor={() => comparison} split={split} onSplit={setSplit} fallbackFocus={fallbackFocus} />}</>
  }
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  await act(async () => root.render(<Harness />))
  const trigger = button('Open'); trigger.focus(); await click(trigger)
  return trigger
}
afterEach(async () => { if (root) await act(async () => root.unmount()); root = null; container?.remove(); vi.restoreAllMocks() })

it('navigates in filtered shooting order with buttons and arrows, keeps metadata, and restores focus on Escape', async () => {
  const trigger = await mount()
  expect(container.querySelector('dialog').open).toBe(true)
  expect(document.body.style.overflow).toBe('hidden')
  const close = container.querySelector('[aria-label="Close photo viewer"]')
  close.focus()
  await act(async () => close.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })))
  expect(document.activeElement).toBe(container.querySelector('.photo-viewer-details summary'))
  await key(document.activeElement, 'Tab')
  expect(document.activeElement).toBe(close)
  expect(button('Previous photo').disabled).toBe(true)
  await key(button('Next photo'), 'ArrowRight')
  expect(container.querySelector('#session-photo-title').textContent).toBe('Wide photo')
  expect(container.querySelector('.photo-viewer-details').textContent).toContain('Second prompt')
  expect(container.querySelector('.photo-viewer-media img').getAttribute('src')).toBe('/api/shots/14/image')
  expect(button('Next photo').disabled).toBe(true)
  await click(button('Previous photo'))
  expect(container.querySelector('#session-photo-title').textContent).toBe('Portrait')
  await act(async () => container.querySelector('dialog').dispatchEvent(new Event('cancel', { cancelable: true })))
  expect(container.querySelector('dialog')).toBeNull()
  expect(document.activeElement).toBe(trigger)
  expect(document.body.style.overflow).toBe('')
})

it('keeps reference comparison identity and range keys independent from photo navigation', async () => {
  await mount({ comparison: { id: 21, label: 'Reference shot 21', warning: 'Seeds differ.' } })
  expect([...container.querySelectorAll('.photo-viewer-media img')].map((item) => item.getAttribute('src'))).toEqual(['/api/shots/11/image', '/api/shots/21/image'])
  const range = container.querySelector('input[type="range"]')
  await key(range, 'ArrowRight')
  expect(container.querySelector('#session-photo-title').textContent).toBe('Portrait')
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(range, '75')
    range.dispatchEvent(new Event('input', { bubbles: true }))
  })
  expect(container.querySelector('.photo-viewer-media').style.getPropertyValue('--split')).toBe('75%')
  expect(container.textContent).toContain('Seeds differ.')
})

it('exposes image load errors and retries only after an explicit reload', async () => {
  await mount()
  const image = container.querySelector('.photo-viewer-media img')
  expect(image.getAttribute('decoding')).toBe('async')
  await act(async () => image.dispatchEvent(new Event('error')))
  expect(container.querySelector('.photo-viewer-media img')).toBeNull()
  expect(container.querySelector('.photo-unavailable').textContent).toContain('Image unavailable')
  await click(button('Reload image'))
  const retried = container.querySelector('.photo-viewer-media img')
  expect(retried.getAttribute('src')).toBe('/api/shots/11/image')
  await act(async () => retried.dispatchEvent(new Event('load')))
  expect(container.querySelector('.photo-loading')).toBeNull()
})

it('closes an unavailable selection without selecting a different identity', async () => {
  const close = vi.fn(), select = vi.fn()
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  await act(async () => root.render(<SessionPhotoViewer photos={photos} selectedId={999} onSelect={select}
    onClose={close} comparisonFor={() => null} split={50} onSplit={() => {}} fallbackFocus={{ current: null }} />))
  expect(close).toHaveBeenCalledOnce()
  expect(select).not.toHaveBeenCalled()
  expect(container.querySelector('dialog')).toBeNull()
})
