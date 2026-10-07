// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true

import React, { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRoot } from 'react-dom/client'
import { api } from '../api.js'
import Setup from './Setup.jsx'

const baseConfig = (overrides = {}) => ({
  comfy_url: 'http://127.0.0.1:8188',
  comfy_output_dir: '',
  lora_dir: '',
  data_dir: 'data',
  llm_url: 'https://assistant.example/v1',
  llm_model: 'text-model',
  llm_vision_model: '',
  llm_key: '',
  checkpoints: {},
  room_libraries: [],
  output_dir_ok: false,
  lora_dir_ok: false,
  llm_ok: true,
  data_dir_resolved: 'data',
  ...overrides,
})

const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

describe('Setup vision model capability', () => {
  let container
  let root
  let config
  let models

  const drain = () => act(async () => {
    for (let index = 0; index < 16; index += 1) await Promise.resolve()
  })
  const render = async () => {
    await act(async () => { root.render(<Setup />) })
    await drain()
  }
  const field = (name) => container.querySelector(`[aria-label="${name}"]`)
  const setValue = async (element, value) => {
    expect(element).toBeTruthy()
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    await act(async () => {
      setter.call(element, value)
      element.dispatchEvent(new Event('input', { bubbles: true }))
      element.dispatchEvent(new Event('change', { bubbles: true }))
    })
  }
  const save = async () => {
    const button = Array.from(container.querySelectorAll('button'))
      .find((item) => item.textContent.trim() === 'Save')
    expect(button).toBeTruthy()
    await act(async () => { button.click() })
    await drain()
  }

  beforeEach(() => {
    vi.restoreAllMocks()
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    config = baseConfig()
    models = []
    vi.spyOn(api, 'get').mockImplementation(async (path) => {
      if (path === '/api/config') return config
      throw new Error(`Unexpected GET ${path}`)
    })
    vi.spyOn(api, 'post').mockImplementation(async (path) => {
      if (path === '/api/llm/models') return { url: config.llm_url, models }
      throw new Error(`Unexpected POST ${path}`)
    })
    vi.spyOn(api, 'patch').mockResolvedValue({ restart_required: false })
  })

  afterEach(() => {
    act(() => root?.unmount())
    container?.remove()
    container = null
    vi.restoreAllMocks()
  })

  it('offers only detected visual models and stores the selected visual text model explicitly', async () => {
    config = baseConfig({ llm_model: 'vision-text' })
    models = [
      { id: 'vision-text', vision: true, params: 8 },
      { id: 'other-vision', vision: true, params: 11 },
      { id: 'text-only', vision: false, params: 14 },
    ]

    await render()

    expect(field('Vision model').value).toBe('vision-text')
    expect(Array.from(field('Vision model').options).map((option) => option.value))
      .toEqual(['', 'vision-text', 'other-vision'])
    expect(Array.from(field('Text model').options).map((option) => option.value))
      .toEqual(['', 'vision-text', 'other-vision', 'text-only'])
    expect(container.textContent).toContain('2 reported vision support')
    expect(container.textContent).not.toContain('falls back to the model above')

    await save()

    expect(api.patch).toHaveBeenCalledWith('/api/config', expect.objectContaining({
      llm_model: 'vision-text',
      llm_vision_model: 'vision-text',
    }))
  })

  it('keeps a saved operator declaration editable without claiming hosted models are visual', async () => {
    config = baseConfig({ llm_vision_model: 'operator-vision-id' })
    models = [{ id: 'chat-only', vision: false, params: 8 }]

    await render()

    expect(field('Vision model').tagName).toBe('INPUT')
    expect(field('Vision model').value).toBe('operator-vision-id')
    expect(container.textContent).toContain('none reported vision support')
    expect(container.textContent).toContain('provider may still reject it')
    expect(container.textContent).not.toContain('1 of them can read a photo')

    await save()

    expect(api.patch).toHaveBeenCalledWith('/api/config', expect.objectContaining({
      llm_vision_model: 'operator-vision-id',
    }))
  })

  it('offers unlisted MiniMax Flash without inventing detected vision support', async () => {
    config = baseConfig({ llm_url: 'https://api.minimax.io/v1', llm_model: 'MiniMax-M3' })
    models = [{ id: 'MiniMax-M3', vision: false, params: 0 }]
    await render()
    const text = field('Text model')
    expect(Array.from(text.options).map((option) => option.value))
      .toEqual(['', 'MiniMax-M3', 'MiniMax-M3.1-Flash-Preview'])
    expect(field('Vision model').tagName).toBe('INPUT')
    expect(container.textContent).toContain('none reported vision support')
    await act(async () => {
      text.value = 'MiniMax-M3.1-Flash-Preview'
      text.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await setValue(field('Vision model'), 'MiniMax-M3.1-Flash-Preview')
    await save()
    expect(api.patch).toHaveBeenCalledWith('/api/config', expect.objectContaining({
      llm_model: 'MiniMax-M3.1-Flash-Preview',
      llm_vision_model: 'MiniMax-M3.1-Flash-Preview',
    }))
  })

  it('keeps manual MiniMax text entry when discovery returns no models', async () => {
    config = baseConfig({ llm_url: 'https://api.minimax.io/v1', llm_model: 'operator-model' })
    models = []
    await render()
    expect(field('Text model').tagName).toBe('INPUT')
    await setValue(field('Text model'), 'another-provider-supported-model')
    await save()
    expect(api.patch).toHaveBeenCalledWith('/api/config', expect.objectContaining({
      llm_model: 'another-provider-supported-model',
    }))
  })

  it('ignores late discovery after the endpoint and text model change', async () => {
    const pending = deferred()
    vi.spyOn(api, 'post').mockReturnValueOnce(pending.promise)
    await render()

    await setValue(field('Prompt assistant endpoint'), 'https://new.example/v1')
    await setValue(field('Text model'), 'new-text-model')
    await act(async () => {
      pending.resolve({ url: 'https://assistant.example/v1', models: [
        { id: 'old-vision-model', vision: true, params: 8 },
      ] })
      await pending.promise
    })

    expect(field('Prompt assistant endpoint').value).toBe('https://new.example/v1')
    expect(field('Text model').value).toBe('new-text-model')
    expect(field('Vision model').value).toBe('')

    await save()

    expect(api.patch).toHaveBeenCalledWith('/api/config', expect.objectContaining({
      llm_url: 'https://new.example/v1',
      llm_model: 'new-text-model',
      llm_vision_model: '',
    }))
  })
})
