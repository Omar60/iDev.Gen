import { expect, test as base } from '@playwright/test'
import { readFile } from 'node:fs/promises'

export const state = JSON.parse(await readFile(process.env.IDEVGEN_E2E_STATE, 'utf8'))
const origin = new URL(state.baseUrl).origin

export const test = base.extend({
  egressGuard: [async ({ context }, use, testInfo) => {
    const expected = new Set()
    const denied = []
    const unexpected = []
    let heldResponse = null
    const key = (value) => new URL(value).href
    const block = (kind, value) => {
      const normalized = key(value)
      denied.push(`${kind} ${normalized}`)
      if (!expected.delete(normalized)) unexpected.push(`${kind} ${new URL(normalized).origin}${new URL(normalized).pathname}`)
    }

    await context.route('**/*', async (route) => {
      const requestUrl = new URL(route.request().url())
      if (requestUrl.origin !== origin) {
        block('HTTP', requestUrl.href)
        await route.abort('blockedbyclient')
        return
      }
      const response = await route.fetch({ maxRedirects: 0 })
      if (response.status() >= 300 && response.status() < 400) {
        const location = response.headers().location || ''
        block('HTTP redirect', new URL(location, requestUrl).href)
        await route.abort('blockedbyclient')
        return
      }
      const held = heldResponse
      if (held && route.request().method() === 'PATCH' && requestUrl.pathname === held.path) {
        heldResponse = null
        held.arrive({ request: route.request(), response })
        await held.released
      }
      await route.fulfill({ response })
    })
    await context.routeWebSocket('**/*', async (webSocket) => {
      block('WebSocket', webSocket.url())
      await webSocket.close({ code: 1008, reason: 'Blocked by the isolated E2E harness.' })
    })

    const barriers = []
    try {
      await use({
        expectBlocked(value) { expected.add(key(value)) },
        denied,
        holdPatch(path) {
          if (heldResponse) throw new Error('Only one PATCH response barrier may be active.')
          let arrive, release
          const received = new Promise((resolve) => { arrive = resolve })
          const released = new Promise((resolve) => { release = resolve })
          barriers.push(release)
          heldResponse = { path, arrive, released }
          return { received, release }
        },
      })
    } finally {
      for (const release of barriers) release()
    }
    if (testInfo.status === testInfo.expectedStatus) {
      expect(expected, 'expected browser egress probes were not intercepted').toEqual(new Set())
      expect(unexpected, `unexpected browser egress was blocked: ${unexpected.join(', ')}`).toEqual([])
    }
  }, { auto: true }],
})
