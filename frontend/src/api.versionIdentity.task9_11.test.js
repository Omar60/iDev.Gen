import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, parseVersionJson, versionText } from './api.js'

const SQLITE_MAX = '9223372036854775807'

describe('lossless saved-look version transport', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('preserves large version tokens without rewriting strings or unknown fields', () => {
    const result = parseVersionJson(`{
      "version": ${SQLITE_MAX},
      "look_snapshot": {"version": 9007199254740993},
      "version_text": "version: ${SQLITE_MAX}",
      "unknown": {"version": "${SQLITE_MAX}", "count": 7}
    }`)

    expect(result.version).toBe(SQLITE_MAX)
    expect(result.look_snapshot.version).toBe('9007199254740993')
    expect(result.version_text).toBe(`version: ${SQLITE_MAX}`)
    expect(result.unknown).toEqual({ version: SQLITE_MAX, count: 7 })
    expect(versionText(result.version)).toBe(SQLITE_MAX)
    expect(versionText(9007199254740992)).toBeNull()
    expect(versionText('9223372036854775808')).toBeNull()
    expect(versionText('01')).toBeNull()
  })

  it('rejects fractional and exponent spellings in version fields before numeric conversion', () => {
    for (const token of ['9007199254740991.1', '9.007199254740992e15', '1.0']) {
      const parsed = parseVersionJson(`{"version":${token},"expected_version":${token}}`)
      expect(parsed.version).toBe(token)
      expect(parsed.expected_version).toBe(token)
      expect(versionText(parsed.version)).toBeNull()
      expect(versionText(parsed.expected_version)).toBeNull()
    }

    expect(parseVersionJson('{"count":9007199254740991.1}').count).toBe(9007199254740991.1)
  })

  it('opts into lossless response parsing for versioned reads', async () => {
    const responseText = `[{"key":"look-local","version":${SQLITE_MAX},"name":"Portable"}]`
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: vi.fn().mockResolvedValue(responseText),
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.getVersioned('/api/looks')).resolves.toEqual([
      { key: 'look-local', version: SQLITE_MAX, name: 'Portable' },
    ])
    expect(fetchMock).toHaveBeenCalledWith('/api/looks', {
      method: 'GET',
      headers: undefined,
      body: undefined,
    })
  })

  it('sends only declared version paths as exact JSON integers', async () => {
    const body = {
      expected_version: SQLITE_MAX,
      plan: { authoring: { look_snapshot: { version: SQLITE_MAX, note: `version ${SQLITE_MAX}` } } },
      version: 'authoring-v1',
      unknown: { version: SQLITE_MAX },
    }
    const responseText = `{"version":${SQLITE_MAX},"look_snapshot":{"version":${SQLITE_MAX}}}`
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: vi.fn().mockResolvedValue(responseText),
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.postVersioned('/api/sessions/1/plan', body, [
      'expected_version', 'plan.authoring.look_snapshot.version',
    ])).resolves.toEqual({ version: SQLITE_MAX, look_snapshot: { version: SQLITE_MAX } })

    const [, init] = fetchMock.mock.calls[0]
    expect(init.body).toBe(
      `{"expected_version":${SQLITE_MAX},"plan":{"authoring":{"look_snapshot":{"version":${SQLITE_MAX},"note":"version ${SQLITE_MAX}"}}},"version":"authoring-v1","unknown":{"version":"${SQLITE_MAX}"}}`,
    )
  })

  it('rejects an already-rounded numeric version before sending', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.postVersioned('/api/looks/look-local/versions', {
      expected_version: 9007199254740992,
    }, ['expected_version'])).rejects.toThrow('Invalid expected_version version.')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
