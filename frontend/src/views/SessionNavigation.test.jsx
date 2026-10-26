// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SessionNavigation from './SessionNavigation.jsx'
import SessionView from './SessionView.jsx'
import { api } from '../api.js'

let root, container
const render = async (props = {}) => {
  if (!root) {
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  }
  await act(async () => root.render(<SessionNavigation id={42} activeStep="character"
    onNavigate={() => true} planReady generationDisabled generationReason="Review first"
    takeCount={12} shotCount={0} revision={3} dirty={false} {...props} />))
}
const button = (name) => [...container.querySelectorAll('button')].find((item) => item.textContent === name)
afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  vi.restoreAllMocks()
  window.history.replaceState(null, '', '#/')
})

describe('SessionView navigation preservation', () => {
  const plan = { version: 'resource-v1', look: 'Saved appearance', initial_wardrobe: '', selected_resources: [],
    wardrobe_changes: [], takes: [{ take_id: 'take-1', label: 'Portrait', camera: '', framing: '', pose: '', expression: '' }] }
  const session = { id: 42, name: '', status: 'draft', model: { id: 4, name: 'Invented model' },
    workflow_id: 12, shots: [], tags: [], settings: { composition_mode: 'resource-v1', width: 1024, height: 1360 } }
  const mount = async () => {
    vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === '/api/sessions/42') return session
      if (path === '/api/config' || path === '/api/comfy/models') return {}
      return []
    })
    vi.spyOn(api, 'getVersioned').mockImplementation(async (path) => {
      if (path.endsWith('/full-auto')) return { current_run: null }
      if (path.endsWith('/plan')) return { plan_revision: 3, plan }
      return { plan_revision: 3, takes: [] }
    })
    const post = vi.spyOn(api, 'post').mockRejectedValue(new Error('Unexpected write'))
    const versioned = vi.spyOn(api, 'postVersioned').mockRejectedValue(new Error('Unexpected write'))
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    await act(async () => {
      root.render(<SessionView id={42} initialSession={session} initialPlan={plan} initialRevision={3} />)
      await Promise.resolve()
    })
    return [post, versioned]
  }
  it('keeps unsaved constants through stage changes and history without authorizing any operation', async () => {
    window.history.replaceState(null, '', '#/session/42?step=constants&source=library')
    const writes = await mount()
    expect(container.querySelector('h1').textContent).toBe('Session 42')
    const field = container.querySelector('textarea')
    await act(async () => {
      Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set.call(field, 'Unsaved appearance')
      field.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => button('3. Takes (1)').click())
    expect(document.activeElement.textContent).toBe('Current stage: Takes')
    expect(container.textContent).toContain('Unsaved changes')
    await act(async () => {
      window.history.replaceState(null, '', '#/session/42?step=constants&source=library')
      window.dispatchEvent(new Event('hashchange'))
    })
    expect(container.querySelector('textarea').value).toBe('Unsaved appearance')
    expect(button('5. Generation').disabled).toBe(true)
    for (const write of writes) expect(write).not.toHaveBeenCalled()
  })
  it('rejects a direct Generation link through the unchanged guard without approving or dispatching', async () => {
    window.history.replaceState(null, '', '#/session/42?step=generation')
    const writes = await mount()
    expect(button('1. Character').getAttribute('aria-current')).toBe('step')
    expect(container.textContent).toContain('Cannot skip to generation')
    expect(window.location.hash).toBe('#/session/42?step=character')
    for (const write of writes) expect(write).not.toHaveBeenCalled()
  })
})

describe('session stage navigation', () => {
  it('preserves route parameters, exposes blockers and focuses the new stage without remounting navigation', async () => {
    window.history.replaceState(null, '', '#/session/42?source=library')
    const navigate = vi.fn(() => true)
    await render({ onNavigate: navigate })
    expect(button('5. Generation').disabled).toBe(true)
    expect(container.textContent).toContain('Generation blocked: Review first')
    expect(button('1. Character').getAttribute('aria-current')).toBe('step')
    const section = container.firstElementChild
    await act(async () => button('3. Takes (12)').click())
    expect(navigate).toHaveBeenCalledWith('takes')
    expect(window.location.hash).toContain('source=library&step=takes')
    await render({ activeStep: 'takes', onNavigate: navigate, dirty: true })
    expect(container.firstElementChild).toBe(section)
    expect(document.activeElement.textContent).toBe('Current stage: Takes')
    expect(container.textContent).toContain('Unsaved changes')
    expect(container.querySelector('a').getAttribute('href')).toContain('step=takes')
  })

  it('passes direct and history links through the existing guard, never accepting a rejected transition', async () => {
    window.history.replaceState(null, '', '#/session/42?step=generation')
    const navigate = vi.fn(() => false)
    await render({ onNavigate: navigate })
    expect(navigate).toHaveBeenCalledWith('generation')
    expect(button('1. Character').getAttribute('aria-current')).toBe('step')
    expect(window.location.hash).toBe('#/session/42?step=character')
    await act(async () => {
      window.history.replaceState(null, '', '#/session/42?step=review')
      window.dispatchEvent(new Event('hashchange'))
    })
    expect(navigate).toHaveBeenLastCalledWith('review')
    await act(async () => button('2. Scene / Constants').click())
    expect(window.location.hash).toBe('#/session/42?step=character')
  })

  it('waits for a valid plan and ignores unrelated routes or unknown stage names', async () => {
    window.history.replaceState(null, '', '#/session/42?step=review')
    const navigate = vi.fn(() => true)
    await render({ onNavigate: navigate, planReady: false })
    expect(navigate).not.toHaveBeenCalled()
    expect(container.querySelector('nav')).toBeNull()
    await render({ onNavigate: navigate })
    expect(navigate).toHaveBeenCalledWith('review')
    navigate.mockClear()
    for (const route of ['#/session/7?step=takes', '#/session/42?step=approve']) {
      await act(async () => {
        window.history.replaceState(null, '', route)
        window.dispatchEvent(new Event('hashchange'))
      })
    }
    expect(navigate).not.toHaveBeenCalled()
  })
})
