import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api.js'

const jsonResponse = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: 'HTTP response',
  json: vi.fn().mockResolvedValue(body),
})

describe('api guided creation transport', () => {
  afterEach(() => vi.unstubAllGlobals())

  it.each([201, 200])('preserves HTTP %i for a valid JSON response', async (status) => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(status, { session_id: 42 }))
    vi.stubGlobal('fetch', fetchMock)
    const body = { request_id: 'same-request', character_id: 4 }

    await expect(api.postWithStatus('/api/sessions/guided', body)).resolves.toEqual({
      status,
      data: { session_id: 42 },
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/guided', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  })

  it('marks a decoded stable idempotency conflict', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(409, {
      detail: { code: 'idempotency_conflict', message: 'Request ID is already in use.' },
    })))

    await expect(api.postWithStatus('/api/sessions/guided', {})).rejects.toMatchObject({
      status: 409,
      detail: { code: 'idempotency_conflict' },
      hasStableErrorBody: true,
    })
  })

  it('does not treat an unreadable error body as a definitive contract error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ...jsonResponse(422, null),
      json: vi.fn().mockRejectedValue(new SyntaxError('invalid JSON')),
    }))

    await expect(api.postWithStatus('/api/sessions/guided', {})).rejects.toMatchObject({
      status: 422,
      hasStableErrorBody: false,
    })
  })

  it('keeps existing post callers on the response body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(201, { id: 42 })))

    await expect(api.post('/api/sessions', {})).resolves.toEqual({ id: 42 })
  })
})
