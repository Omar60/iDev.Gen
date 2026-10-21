// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SessionsList from './SessionsList.jsx'
import Models from './Models.jsx'

vi.mock('../App.jsx', () => ({ go: vi.fn() }))

const session = (overrides = {}) => ({
  id: 12, name: 'Window light study', model_name: 'Ada', status: 'done',
  done_count: 3, shot_count: 4, created_at: '2026-10-19T12:30:00', ...overrides,
})
const response = (data, ok = true) => ({ ok, status: ok ? 200 : 503, json: async () => data })
const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

describe('Sessions list', () => {
  let container, root, fetchMock
  const drain = () => act(async () => { for (let i = 0; i < 16; i += 1) await Promise.resolve() })
  const render = async (element = <SessionsList />) => {
    await act(async () => { root.render(element) })
    await drain()
  }
  const search = () => container.querySelector('input[type="search"]')
  const type = async (value) => {
    await act(async () => {
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(search(), value)
      search().dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  const submit = async () => {
    await act(async () => { container.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })) })
    await drain()
  }
  const click = async (name) => {
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent === name).click() })
    await drain()
  }
  beforeEach(() => {
    container = document.createElement('div'); document.body.appendChild(container)
    root = createRoot(container)
    fetchMock = vi.fn().mockResolvedValue(response([session()]))
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => {
    act(() => root.unmount()); container.remove()
    vi.unstubAllGlobals(); vi.restoreAllMocks()
  })

  it('keeps server order, names, deep links, partial completion and stored dates', async () => {
    const rows = [session({ id: 2 }), session({ id: 19, name: '   ', status: 'draft', shot_count: 0, done_count: 0 }),
      session({ id: 1, name: 'A'.repeat(180), model_name: '', status: 'future-status', created_at: '' })]
    fetchMock.mockResolvedValue(response(rows))
    await render()
    const links = [...container.querySelectorAll('tbody a')]
    expect(links.map(a => a.getAttribute('href'))).toEqual(['#/session/2', '#/session/19', '#/session/1'])
    expect(links[1].textContent).toBe('Session #19')
    expect(rows[1].name).toBe('   ')
    expect(links[2].textContent).toBe('A'.repeat(180))
    expect(container.textContent).toContain('Unnamed session')
    expect(container.textContent).toContain('Model unavailable')
    expect(container.textContent).toContain('future-status')
    expect(container.textContent).toContain('No shots yet')
    expect(container.textContent).toContain('Date unavailable')
    expect(container.querySelector('[data-status="done"]').textContent).toBe('Done')
    const progress = container.querySelector('progress')
    expect([progress.value, progress.max]).toEqual([3, 4])
    expect(progress.getAttribute('aria-label')).toContain('3 of 4 photos completed')
    expect(container.querySelector('time').getAttribute('datetime')).toBe(rows[0].created_at)
    expect(container.querySelectorAll('thead th[scope="col"]')).toHaveLength(4)
    const modifiedClick = new MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true })
    links[0].dispatchEvent(modifiedClick)
    expect(modifiedClick.defaultPrevented).toBe(false)
    expect(fetchMock.mock.calls.every(([, options]) => options.method === 'GET')).toBe(true)
  })

  it('shows every known status independently of successful photo counts', async () => {
    fetchMock.mockResolvedValue(response(['draft', 'running', 'done', 'failed', 'cancelled'].map((status, id) => session({ id, status, done_count: 0 }))))
    await render()
    expect([...container.querySelectorAll('[data-status]')].map(s => s.textContent))
      .toEqual(['Draft', 'Running', 'Done', 'Failed', 'Cancelled'])
  })

  it('submits trimmed server queries only on Search and restores focus on clear', async () => {
    await render()
    expect(search().labels[0].textContent).toBe('Search sessions')
    expect(document.getElementById(search().getAttribute('aria-describedby')).textContent).toContain('name, look or wardrobe')
    await type('  Soft light & blue jacket  ')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    fetchMock.mockResolvedValue(response([session({ name: 'Unrelated title returned by wardrobe search' })]))
    await submit()
    expect(fetchMock.mock.calls.at(-1)[0]).toBe('/api/sessions?q=Soft+light+%26+blue+jacket')
    expect(container.textContent).toContain('Unrelated title returned by wardrobe search')
    expect(container.textContent).toContain('Results for “Soft light & blue jacket”')
    await click('Clear search')
    expect(fetchMock.mock.calls.at(-1)[0]).toBe('/api/sessions')
    expect(search().value).toBe('')
    expect(document.activeElement).toBe(search())
  })

  it('reuses row rendering while typing and renders fresh rows when results change', async () => {
    const rows = [session()]
    const mapping = vi.spyOn(rows, 'map')
    fetchMock.mockResolvedValue(response(rows))
    await render()
    expect(mapping).toHaveBeenCalledTimes(1)
    await type('blue coat')
    expect(mapping).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const updated = [session({ id: 20, name: 'Updated result', done_count: 4 })]
    fetchMock.mockResolvedValue(response(updated))
    await submit()
    expect(container.querySelector('tbody a').getAttribute('href')).toBe('#/session/20')
    expect(container.querySelector('tbody a').textContent).toBe('Updated result')
    expect(container.querySelector('progress').value).toBe(4)
  })

  it('distinguishes loading, an empty inventory and no search results', async () => {
    const pending = deferred()
    fetchMock.mockReturnValueOnce(pending.promise)
    await render()
    expect(container.querySelector('[role="status"]').textContent).toBe('Loading sessions…')
    expect(container.textContent).not.toContain('No sessions yet')
    await act(async () => { pending.resolve(response([])); await pending.promise })
    await drain()
    expect(container.textContent).toContain('No sessions yet')
    expect(container.querySelector('a').getAttribute('href')).toBe('#/models')
    fetchMock.mockResolvedValue(response([]))
    await type('no match'); await submit()
    expect(container.textContent).toContain('No sessions match')
    expect(container.textContent).not.toContain('No sessions yet')
  })

  it('focuses API errors and retries the submitted query without losing the draft input', async () => {
    await render(); await type('wardrobe'); await submit()
    fetchMock.mockResolvedValue(response({ detail: 'Fixture: sessions unavailable.' }, false))
    await submit()
    expect(container.querySelector('[role="alert"]').textContent).toContain('Fixture: sessions unavailable.')
    expect(document.activeElement).toBe(container.querySelector('[role="alert"]'))
    expect(container.textContent).not.toContain('No sessions match')
    await type('unsubmitted change')
    fetchMock.mockResolvedValue(response([session()]))
    await click('Retry loading sessions')
    expect(fetchMock.mock.calls.at(-1)[0]).toBe('/api/sessions?q=wardrobe')
    expect(search().value).toBe('unsubmitted change')
    expect(document.activeElement).toBe(search())
    expect(container.querySelector('[role="alert"]')).toBeNull()
  })

  it('keeps focus in the form while a retry is pending and does not steal it on success', async () => {
    fetchMock.mockResolvedValue(response({ detail: 'Fixture: request failed.' }, false))
    await render()
    const pending = deferred()
    fetchMock.mockReturnValueOnce(pending.promise)
    await click('Retry loading sessions')
    expect(document.activeElement).toBe(search())
    const button = container.querySelector('button[type="submit"]')
    button.focus()
    await act(async () => { pending.resolve(response([session()])); await pending.promise })
    await drain()
    expect(document.activeElement).toBe(button)
  })

  it('ignores an older success or error after a newer query completes', async () => {
    await render()
    const older = deferred(), failed = deferred()
    fetchMock.mockReturnValueOnce(older.promise)
    await type('older'); await submit()
    fetchMock.mockReturnValueOnce(failed.promise)
    await type('failed'); await submit()
    fetchMock.mockResolvedValue(response([session({ name: 'Newest result' })]))
    await type('newest'); await submit()
    await act(async () => { older.resolve(response([session({ name: 'Stale result' })])); failed.resolve(response({ detail: 'Stale error' }, false)) })
    await drain()
    expect(container.textContent).toContain('Newest result')
    expect(container.textContent).not.toContain('Stale result')
    expect(container.textContent).not.toContain('Stale error')
    expect(container.querySelector('[role="status"]').textContent).toContain('newest')
  })

  it('preserves the existing Models form across tab changes and fetches sessions only in its list', async () => {
    fetchMock.mockImplementation(async (path) => response(path === '/api/comfy/loras' ? { loras: [] }
      : path === '/api/comfy/models' ? {} : path === '/api/sessions' ? [session()] : []))
    await render(<Models tab="models" />)
    expect(fetchMock.mock.calls.some(([path]) => path === '/api/sessions')).toBe(false)
    await click('+ New model')
    const name = container.querySelector('input')
    await act(async () => {
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(name, 'Draft model')
      name.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await render(<Models tab="sessions" />)
    expect(container.querySelector('tbody a').getAttribute('href')).toBe('#/session/12')
    expect(fetchMock.mock.calls.filter(([path]) => path === '/api/sessions')).toHaveLength(1)
    await render(<Models tab="models" />)
    expect(container.querySelector('input').value).toBe('Draft model')
  })
})
