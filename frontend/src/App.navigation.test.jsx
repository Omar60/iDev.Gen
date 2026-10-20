// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import App, { go } from './App.jsx'

vi.mock('./api', () => ({ api: { get: vi.fn(async (path) => path === '/api/comfy/status' ? { online: false } : []) } }))
vi.mock('./kinds.js', () => ({ setCatalogue: vi.fn() }))
vi.mock('./wardrobe.js', () => ({ setWardrobe: vi.fn() }))
vi.mock('./views/Models.jsx', () => ({ default: ({ tab }) => <p data-screen={tab} /> }))
vi.mock('./views/ModelDetail.jsx', () => ({ default: ({ id }) => <p data-screen="model" data-id={id} /> }))
vi.mock('./views/SessionView.jsx', () => ({ default: ({ id }) => <p data-screen="session" data-id={id} /> }))
vi.mock('./views/Resources.jsx', () => ({ default: ({ requestedModelId }) => <p data-screen="resources" data-id={requestedModelId} /> }))
vi.mock('./views/Workflows.jsx', () => ({ default: () => <p data-screen="workflows" /> }))
vi.mock('./views/Setup.jsx', () => ({ default: () => <p data-screen="setup" /> }))
vi.mock('./views/Library.jsx', () => ({ default: () => <p data-screen="library" /> }))
vi.mock('./views/Slideshow.jsx', () => ({ default: () => <p data-screen="slideshow" /> }))
vi.mock('./views/Judge.jsx', () => ({ Judge: () => <p data-screen="judge" /> }))
vi.mock('./views/Catalogue.jsx', () => ({ default: () => <p data-screen="catalogue" /> }))
vi.mock('./views/RoomImport.jsx', () => ({ default: () => <p data-screen="rooms" /> }))
vi.mock('./views/Looks.jsx', () => ({ default: () => <p data-screen="looks" /> }))

let root
let container

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = null
  container?.remove()
  vi.restoreAllMocks()
})

it('keeps all hash routes, detail arguments, query parameters and the go helper intact', async () => {
  vi.spyOn(window, 'matchMedia').mockReturnValue({ addEventListener() {}, removeEventListener() {} })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => root.render(<App />))
  const routes = ['models', 'sessions', 'resources', 'looks', 'judge', 'catalogue', 'library', 'slideshow', 'rooms', 'workflows', 'setup']
  const cases = [
    ['', 'models', null],
    ...routes.map((route) => [route, route, null]),
    ['model/7?source=library', 'model', '7'],
    ['session/23?step=review', 'session', '23'],
    ['resources/7?tab=import', 'resources', '7'],
    ['slideshow?interval=3&threshold=4', 'slideshow', null],
  ]
  for (const [path, screen, id] of cases) {
    await act(async () => {
      go(`/${path}`)
      window.dispatchEvent(new Event('hashchange'))
    })
    expect(window.location.hash).toBe(`#/${path}`)
    const content = container.querySelector('[data-screen]')
    expect(content.dataset.screen).toBe(screen)
    expect(content.getAttribute('data-id')).toBe(id)
    expect(container.querySelector('nav [aria-current="page"]').getAttribute('href'))
      .toBe(`#/${screen === 'model' ? 'models' : screen === 'session' ? 'sessions' : screen}`)
  }
})
