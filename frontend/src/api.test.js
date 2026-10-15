import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api.js'

const jsonResponse = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: 'HTTP response',
  json: vi.fn().mockResolvedValue(body),
  text: vi.fn().mockResolvedValue(JSON.stringify(body)),
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

  it('preserves exact saved-look and profile integer identities on guided requests', async () => {
    const maximumVersion = '9223372036854775807'
    const body = {
      saved_look: { look_key: 'look-example', version: maximumVersion, content_digest: 'a'.repeat(64) },
      full_auto: { profile_revision: maximumVersion },
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      text: vi.fn().mockResolvedValue(`{"session_id":42,"automation_profile":{"revision":${maximumVersion}},"plan":{"authoring":{"look_snapshot":{"version":${maximumVersion}}}}}`),
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.postWithStatus('/api/sessions/guided', body, [
      'saved_look.version', 'full_auto.profile_revision',
    ])).resolves.toEqual({
      status: 201,
      data: { session_id: 42, automation_profile: { revision: maximumVersion }, plan: { authoring: { look_snapshot: { version: maximumVersion } } } },
    })
    expect(fetchMock.mock.calls[0][1].body).toBe(
      `{"saved_look":{"look_key":"look-example","version":${maximumVersion},"content_digest":"${'a'.repeat(64)}"},"full_auto":{"profile_revision":${maximumVersion}}}`,
    )
  })

  it('accepts the refreshed consent evidence on a stable scope-change error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(409, {
      detail: {
        code: 'authorization_scope_changed',
        message: 'Review the refreshed summary and authorize again.',
        current: { summary: { workflow: { workflow_id: 17 } }, scope_digest: 'b'.repeat(64), scope_issued_at: '2026-10-14T12:00:00+00:00' },
      },
    })))

    await expect(api.postWithStatus('/api/sessions/guided', {})).rejects.toMatchObject({
      status: 409,
      hasStableErrorBody: true,
      detail: { code: 'authorization_scope_changed', current: { summary: { workflow: { workflow_id: 17 } } } },
    })
  })

  it('preserves large identities inside a refreshed scope-change error', async () => {
    const maximumVersion = '9223372036854775807'
    const text = `{"detail":{"code":"authorization_scope_changed","message":"Review again.","current":{"scope_digest":"${'b'.repeat(64)}","summary":{"saved_look":{"version":${maximumVersion}},"automation_profile":{"revision":${maximumVersion}}}}}}`
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      text: vi.fn().mockResolvedValue(text),
    }))

    await expect(api.postWithStatus('/api/sessions/guided', {
      saved_look: { version: maximumVersion },
    }, ['saved_look.version']))
      .rejects.toMatchObject({
        status: 409,
        hasStableErrorBody: true,
        detail: {
          current: {
            summary: {
              saved_look: { version: maximumVersion },
              automation_profile: { revision: maximumVersion },
            },
          },
        },
      })
  })

  it('does not widen stable error classification beyond scope-change current evidence', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(409, {
      detail: {
        code: 'idempotency_conflict',
        message: 'Request ID is already in use.',
        current: {},
      },
    })))

    await expect(api.postWithStatus('/api/sessions/guided', {})).rejects.toMatchObject({
      status: 409,
      hasStableErrorBody: false,
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
