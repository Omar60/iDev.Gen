import { expect, test as base } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { assertHTMLIntegrity } from './preflight.mjs'

export const state = JSON.parse(await readFile(process.env.IDEVGEN_E2E_STATE, 'utf8'))
const origin = new URL(state.baseUrl).origin
const expectedHTML = await readFile(new URL('../dist/index.html', import.meta.url))
if (!state.htmlSHA256) throw new Error('E2E runner state has no verified HTML reference.')

export const test = base.extend({
  egressGuard: [async ({ context }, use, testInfo) => {
    const expected = new Set()
    const denied = []
    const unexpected = []
    const integrityFailures = []
    const fulfill = async (route, response) => {
      if (route.request().resourceType() === 'document') {
        try {
          assertHTMLIntegrity(expectedHTML, await response.body(), state.htmlSHA256)
        } catch (error) {
          integrityFailures.push(error.message)
          await route.abort('blockedbyclient')
          return
        }
      }
      await route.fulfill({ response })
    }
    const barriers = new Map()
    const releases = []
    const faults = []
    const key = (value) => new URL(value).href
    const requestKey = (method, path) => `${method.toUpperCase()} ${path}`
    const takeBarrier = (request) => {
      const url = new URL(request.url())
      const queue = barriers.get(requestKey(request.method(), `${url.pathname}${url.search}`))
      const barrier = queue?.shift()
      if (queue?.length === 0) barriers.delete(requestKey(request.method(), `${url.pathname}${url.search}`))
      return barrier
    }
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
      const barrier = takeBarrier(route.request())
      if (barrier?.phase === 'before-send') {
        barrier.arrive({ request: route.request() })
        const options = await barrier.released
        if (options.abort) {
          faults.push({ method: route.request().method(), path: `${requestUrl.pathname}${requestUrl.search}`, action: 'abort' })
          await route.abort('failed')
          return
        }
        let response
        if (options.responsePath) {
          response = await route.fetch({ url: new URL(options.responsePath, origin).href, maxRedirects: 0 })
          faults.push({
            method: route.request().method(), path: `${requestUrl.pathname}${requestUrl.search}`,
            action: 'response', responsePath: options.responsePath, status: response.status(),
          })
        } else {
          response = await route.fetch({ maxRedirects: 0 })
        }
        if (response.status() >= 300 && response.status() < 400) {
          const location = response.headers().location || ''
          block('HTTP redirect', new URL(location, requestUrl).href)
          await route.abort('blockedbyclient')
          return
        }
        await fulfill(route, response)
        return
      }
      const response = await route.fetch({ maxRedirects: 0 })
      if (response.status() >= 300 && response.status() < 400) {
        const location = response.headers().location || ''
        block('HTTP redirect', new URL(location, requestUrl).href)
        await route.abort('blockedbyclient')
        return
      }
      if (barrier?.phase === 'after-response') {
        barrier.arrive({ request: route.request(), response })
        await barrier.released
      }
      await fulfill(route, response)
    })
    await context.routeWebSocket('**/*', async (webSocket) => {
      block('WebSocket', webSocket.url())
      await webSocket.close({ code: 1008, reason: 'Blocked by the isolated E2E harness.' })
    })

    try {
      await use({
        expectBlocked(value) { expected.add(key(value)) },
        denied,
        faults,
        hold({ method, path, phase = 'after-response' }) {
          const normalizedMethod = String(method || '').toUpperCase()
          if (!/^[A-Z]+$/.test(normalizedMethod)) throw new Error('A barrier requires an exact HTTP method.')
          const parsedPath = new URL(path, origin)
          if (!path.startsWith('/') || path.startsWith('//') || parsedPath.origin !== origin ||
              `${parsedPath.pathname}${parsedPath.search}` !== path || parsedPath.hash || path.includes('\\')) {
            throw new Error('A barrier requires an exact same-origin path, optionally including its query.')
          }
          if (!['before-send', 'after-response'].includes(phase)) throw new Error('Unknown barrier phase.')
          const id = requestKey(normalizedMethod, path)
          const queue = barriers.get(id) || []
          let arrive, release
          const received = new Promise((resolve) => { arrive = resolve })
          const released = new Promise((resolve) => { release = resolve })
          let isReleased = false
          const releaseBarrier = (options = {}) => {
            if (isReleased) throw new Error('This request barrier has already been released.')
            if (!options || typeof options !== 'object' || Array.isArray(options)) throw new Error('Barrier release options must be an object.')
            const releaseOptions = Object.freeze(Object.fromEntries(
              Object.keys(options).map((key) => [key, options[key]]),
            ))
            if (phase === 'after-response' && Object.keys(releaseOptions).length) throw new Error('An after-response barrier cannot alter the real response.')
            if (phase === 'before-send') {
              const keys = Object.keys(releaseOptions)
              const isAbort = keys.length === 1 && releaseOptions.abort === true
              const isResponse = keys.length === 1 && typeof releaseOptions.responsePath === 'string'
              if (keys.length && !isAbort && !isResponse) throw new Error('Before-send release accepts only { abort: true } or { responsePath }.')
              if (isResponse) {
                const responsePath = releaseOptions.responsePath
                const source = new URL(responsePath, origin)
                if (normalizedMethod !== 'GET' || !responsePath.startsWith('/') || responsePath.startsWith('//') ||
                    source.origin !== origin || `${source.pathname}${source.search}` !== responsePath || source.search ||
                    source.hash || responsePath.includes('\\') || !/^\/api\/sessions\/\d+(?:\/plan)?$/.test(responsePath)) {
                  throw new Error('A substituted response requires an exact same-origin owned-session GET path without query or fragment.')
                }
              }
            }
            isReleased = true
            release(releaseOptions)
          }
          const barrier = { phase, arrive, released }
          queue.push(barrier)
          barriers.set(id, queue)
          releases.push(() => { if (!isReleased) { isReleased = true; release({}) } })
          return { received, release: releaseBarrier }
        },
        holdPatch(path) { return this.hold({ method: 'PATCH', path, phase: 'after-response' }) },
      })
    } finally {
      for (const release of releases) release()
    }
    if (integrityFailures.length) throw new Error(integrityFailures.join('\n'))
    if (testInfo.status === testInfo.expectedStatus) {
      expect(expected, 'expected browser egress probes were not intercepted').toEqual(new Set())
      expect(unexpected, `unexpected browser egress was blocked: ${unexpected.join(', ')}`).toEqual([])
      expect([...barriers.values()].flat(), 'registered request barriers were not received').toEqual([])
    }
  }, { auto: true }],
})
