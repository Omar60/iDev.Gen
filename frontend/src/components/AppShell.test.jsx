// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api.js'
import AppShell, { ComfyStatus } from './AppShell.jsx'

let container
let root
let media
let mediaListeners

const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

async function mount(element) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(element))
}

async function renderShell(props = {}, child = <p>Screen content</p>) {
  const element = <AppShell view="sessions" routeKey="sessions" {...props}>{child}</AppShell>
  if (root) await act(async () => root.render(element))
  else await mount(element)
}

async function click(element) {
  expect(element).toBeTruthy()
  await act(async () => element.dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

async function key(element, value, options = {}) {
  const event = new KeyboardEvent('keydown', { key: value, bubbles: true, cancelable: true, ...options })
  await act(async () => element.dispatchEvent(event))
  return event
}

const button = (label) => container.querySelector(`button[aria-label="${label}"]`)
const navigation = () => container.querySelector('nav[aria-label="Main navigation"]')
const drawer = () => container.querySelector('#app-navigation')

beforeEach(() => {
  vi.spyOn(api, 'get').mockResolvedValue({ online: true, output_dir_ok: true })
  mediaListeners = new Set()
  media = {
    matches: false,
    media: '(min-width: 1200px)',
    addEventListener: vi.fn((type, handler) => mediaListeners.add(handler)),
    removeEventListener: vi.fn((type, handler) => mediaListeners.delete(handler)),
  }
  vi.spyOn(window, 'matchMedia').mockReturnValue(media)
})

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  container = null
  document.body.style.overflow = ''
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('global application navigation', () => {
  it('preserves the eleven route links and marks detail routes under their parent sections', async () => {
    const routes = ['sessions', 'library', 'models', 'resources', 'looks', 'catalogue', 'rooms', 'judge', 'slideshow', 'workflows', 'setup']
    await renderShell({ view: 'session', routeKey: 'session/23' })
    expect([...navigation().querySelectorAll('a')].map((link) => link.getAttribute('href')))
      .toEqual(routes.map((route) => `#/${route}`))
    expect(navigation().querySelectorAll('[aria-current="page"]')).toHaveLength(1)
    expect(navigation().querySelector('[aria-current="page"]').getAttribute('href')).toBe('#/sessions')

    await renderShell({ view: 'model', routeKey: 'model/7' })
    expect(navigation().querySelector('[aria-current="page"]').getAttribute('href')).toBe('#/models')
    await renderShell({ view: 'resources', routeKey: 'resources/7' })
    expect(navigation().querySelector('[aria-current="page"]').getAttribute('href')).toBe('#/resources')
  })

  it('opens a modal drawer, traps focus, closes with Escape and returns focus to the trigger', async () => {
    await renderShell()
    const trigger = button('Open navigation')
    trigger.focus()
    await click(trigger)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(drawer().getAttribute('role')).toBe('dialog')
    expect(drawer().getAttribute('aria-modal')).toBe('true')
    expect(drawer().contains(document.activeElement)).toBe(true)
    expect(document.body.style.overflow).toBe('hidden')
    expect(container.querySelector('main').closest('[inert]')).toBeTruthy()

    const focusable = [...drawer().querySelectorAll('button, a[href]')]
    const first = focusable[0]
    const last = focusable.at(-1)
    last.focus()
    expect((await key(last, 'Tab')).defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(first)
    await key(first, 'Tab', { shiftKey: true })
    expect(document.activeElement).toBe(last)

    const underlyingEscape = vi.fn()
    window.addEventListener('keydown', underlyingEscape)
    expect((await key(last, 'Escape')).defaultPrevented).toBe(true)
    window.removeEventListener('keydown', underlyingEscape)
    expect(underlyingEscape).not.toHaveBeenCalled()
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(drawer().getAttribute('aria-modal')).not.toBe('true')
    expect(document.activeElement).toBe(trigger)
    expect(document.body.style.overflow).toBe('')
    expect(container.querySelector('main').closest('[inert]')).toBeNull()
  })

  it('closes from the close button and restores the previous scroll lock', async () => {
    document.body.style.overflow = 'auto'
    await renderShell()
    await click(button('Open navigation'))
    await click(button('Close navigation'))
    expect(document.activeElement).toBe(button('Open navigation'))
    expect(document.body.style.overflow).toBe('auto')
  })

  it('closes on route changes, focuses the screen and retains mounted screen state', async () => {
    function Screen() {
      const [count, setCount] = useState(0)
      return <button onClick={() => setCount(count + 1)}>Count {count}</button>
    }
    await renderShell({ view: 'session', routeKey: 'session/23' }, <Screen />)
    await click([...container.querySelectorAll('button')].find((element) => element.textContent === 'Count 0'))
    await click(button('Open navigation'))
    await renderShell({ view: 'session', routeKey: 'session/24' }, <Screen />)
    expect(button('Open navigation').getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(container.querySelector('main#main-content'))
    expect(container.textContent).toContain('Count 1')
    expect(document.body.style.overflow).toBe('')
  })

  it('releases the drawer when entering desktop and cleans listeners and scroll locking on unmount', async () => {
    await renderShell()
    await click(button('Open navigation'))
    await act(async () => {
      media.matches = true
      mediaListeners.forEach((handler) => handler({ matches: true }))
    })
    expect(button('Open navigation').getAttribute('aria-expanded')).toBe('false')
    expect(drawer().getAttribute('role')).not.toBe('dialog')
    expect(document.body.style.overflow).toBe('')

    await act(async () => {
      media.matches = false
      mediaListeners.forEach((handler) => handler({ matches: false }))
    })
    await click(button('Open navigation'))
    await act(async () => root.unmount())
    root = null
    expect(mediaListeners.size).toBe(0)
    expect(document.body.style.overflow).toBe('')
  })

  it('moves focus from a desktop sidebar link to the mobile trigger when the sidebar becomes hidden', async () => {
    media.matches = true
    await renderShell()
    navigation().querySelector('a').focus()
    await act(async () => {
      media.matches = false
      mediaListeners.forEach((handler) => handler({ matches: false }))
    })
    expect(document.activeElement).toBe(button('Open navigation'))
    expect(button('Open navigation').getAttribute('aria-expanded')).toBe('false')
  })
})

describe('ComfyUI header status', () => {
  it('shows Checking until the first response, then Online with the running-session deep link', async () => {
    const response = deferred()
    api.get.mockReturnValue(response.promise)
    await mount(<ComfyStatus />)
    expect(container.textContent).toContain('Checking')
    expect(container.textContent).not.toContain('Offline')
    await act(async () => response.resolve({
      online: true, output_dir_ok: true, busy: true, running_session: 23,
      version: '1.0', vram_free: 4000000000, vram_total: 8000000000,
    }))
    expect(container.textContent).toContain('Online')
    expect(container.querySelector('a[href="#/session/23"]')).toBeTruthy()
    expect(container.querySelector('a[href="#/setup"]')).toBeNull()
  })

  it('shows setup guidance only when the status confirms an invalid output directory', async () => {
    api.get.mockResolvedValue({ online: false, output_dir_ok: false })
    await mount(<ComfyStatus />)
    expect(container.textContent).toContain('Offline')
    expect(container.querySelector('a[href="#/setup"]').textContent).toContain('Finish setup')
  })

  it('retains a valid zero-free-VRAM reading and never displays missing VRAM as a number', async () => {
    api.get.mockResolvedValue({ online: true, output_dir_ok: true, vram_free: 0, vram_total: 8000000000 })
    await mount(<ComfyStatus />)
    expect(container.textContent).toContain('0.0/8 GB')
    await act(async () => root.unmount())
    root = null
    container.remove()
    api.get.mockResolvedValue({ online: true, output_dir_ok: true, version: '1.0' })
    await mount(<ComfyStatus />)
    expect(container.textContent).toContain('1.0')
    expect(container.textContent).not.toContain('NaN')
    expect(container.textContent).not.toContain('GB')
  })

  it('polls every five seconds, reports connectivity errors and stops polling on unmount', async () => {
    vi.useFakeTimers()
    api.get.mockRejectedValue(new Error('unreachable'))
    await mount(<ComfyStatus />)
    expect(container.textContent).toContain('Offline')
    expect(container.querySelector('a[href="#/setup"]')).toBeNull()
    expect(api.get).toHaveBeenCalledExactlyOnceWith('/api/comfy/status')
    await act(async () => vi.advanceTimersByTimeAsync(5000))
    expect(api.get).toHaveBeenCalledTimes(2)
    await act(async () => root.unmount())
    root = null
    await act(async () => vi.advanceTimersByTimeAsync(10000))
    expect(api.get).toHaveBeenCalledTimes(2)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('ignores a stale polling response when a newer status request has already finished', async () => {
    vi.useFakeTimers()
    const first = deferred()
    api.get.mockReturnValueOnce(first.promise).mockResolvedValue({ online: true, output_dir_ok: true })
    await mount(<ComfyStatus />)
    await act(async () => vi.advanceTimersByTimeAsync(5000))
    expect(container.textContent).toContain('Online')
    await act(async () => first.resolve({ online: false, output_dir_ok: false }))
    expect(container.textContent).toContain('Online')
    expect(container.querySelector('a[href="#/setup"]')).toBeNull()
  })
})
