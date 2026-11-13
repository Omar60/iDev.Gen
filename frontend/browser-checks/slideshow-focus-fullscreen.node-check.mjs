// Run explicitly: node --test frontend/browser-checks/slideshow-focus-fullscreen.node-check.mjs
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { build } from 'esbuild'
import { chromium } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { cleanupOwnedRoot, createOwnedRoot, ownedPath } from '../e2e/owned-root.mjs'

const origin = 'http://slideshow-focus.localhost'
const photos = [
  { id: 11, session_id: 101, session_name: 'Synthetic Morning' },
  { id: 12, session_id: 102, session_name: 'Synthetic Evening' },
]
const image = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 480"><rect width="640" height="480" fill="#394b62"/></svg>'
const html = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Synthetic Slideshow check</title><link rel="stylesheet" href="/app.css"><div id="root"></div><script src="/app.js"></script></html>'

function deferred() {
  let resolve
  const promise = new Promise((yes) => { resolve = yes })
  return { promise, resolve }
}

function combineFailures(failure, cleanupErrors, message) {
  const errors = failure ? [failure, ...cleanupErrors] : cleanupErrors
  return errors.length === 0 ? undefined : errors.length === 1 ? errors[0] : new AggregateError(errors, message)
}

async function tabTo(page, locator) {
  for (let count = 0; count < 20; count += 1) {
    if (await locator.evaluate((element) => document.activeElement === element)) return
    await page.keyboard.press('Tab')
  }
  throw new Error('The keyboard target was not reached.')
}

test('Slideshow preserves focus through list recovery and separates fullscreen Escape in Chromium', { timeout: 120000 }, async (suite) => {
  const owned = await createOwnedRoot()
  const unexpected = [], runtimeErrors = [], handlers = new Set(), states = new WeakMap()
  let context, failure
  try {
    const bundle = await build({
      absWorkingDir: fileURLToPath(new URL('..', import.meta.url)),
      entryPoints: ['src/main.jsx'], bundle: true, write: false, outdir: 'memory',
      format: 'iife', define: { 'process.env.NODE_ENV': '"production"' }, logLevel: 'silent',
    })
    const js = Buffer.from(bundle.outputFiles.find((file) => file.path.endsWith('.js')).contents)
    const css = Buffer.from(bundle.outputFiles.find((file) => file.path.endsWith('.css')).contents)
    context = await chromium.launchPersistentContext(await ownedPath(owned, 'browser-profile'), {
      headless: true, viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block',
    })
    await context.routeWebSocket('**/*', async (socket) => {
      unexpected.push(`WebSocket ${socket.url()}`)
      await socket.close()
    })
    console.log(JSON.stringify({ node: process.version, chromium: context.browser().version() }))

    await context.route('**/*', (route) => {
      const task = (async () => {
        const request = route.request(), url = new URL(request.url())
        if (url.origin !== origin || request.method() !== 'GET') {
          unexpected.push(`${request.method()} ${url.origin}${url.pathname}`)
          return route.abort('blockedbyclient')
        }
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html })
        if (url.pathname === '/app.js') return route.fulfill({ contentType: 'text/javascript', body: js })
        if (url.pathname === '/app.css') return route.fulfill({ contentType: 'text/css', body: css })
        if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204, body: '' })
        if (!url.pathname.startsWith('/api/')) {
          unexpected.push(`Unknown fixture ${url.pathname}`)
          return route.abort('blockedbyclient')
        }
        const page = request.frame().page(), state = states.get(page)
        if (url.pathname === '/api/components') return route.fulfill({ json: [] })
        if (url.pathname === '/api/wardrobe') return route.fulfill({ json: { garments: [], outfits: [] } })
        if (url.pathname === '/api/comfy/status') return route.fulfill({ json: { online: false, output_dir_ok: true } })
        if (url.pathname === '/api/sessions') return route.fulfill({ json: [] })
        if (url.pathname === '/api/photos') {
          state.photoCalls += 1
          if (state.mode === 'empty-initial' && state.photoCalls === 1) {
            state.started.resolve()
            const response = await state.response.promise
            return route.fulfill({ status: response.status, json: response.body })
          }
          if (state.mode === 'empty-retry' && state.photoCalls === 1) return route.fulfill({ json: [] })
          if (state.mode === 'empty-retry' && state.photoCalls === 2) {
            state.started.resolve()
            const response = await state.response.promise
            return route.fulfill({ status: response.status, json: response.body })
          }
          if (state.mode === 'empty-rating' && state.photoCalls === 1) return route.fulfill({ json: photos })
          if (state.mode === 'empty-rating' && state.photoCalls === 2 && url.searchParams.get('min_rating') === '5') {
            state.started.resolve()
            const response = await state.response.promise
            return route.fulfill({ status: response.status, json: response.body })
          }
          if (state.mode === 'empty-reload' && state.photoCalls === 1) return route.fulfill({ json: [] })
          if (state.mode !== 'fullscreen' && state.photoCalls === 1) {
            return route.fulfill({ status: 503, json: { detail: 'Synthetic list failure' } })
          }
          if (state.mode !== 'fullscreen' && state.photoCalls === 2) {
            state.started.resolve()
            const response = await state.response.promise
            return route.fulfill({ status: response.status, json: response.body })
          }
          return route.fulfill({ json: photos })
        }
        if (/^\/api\/shots\/\d+\/image$/.test(url.pathname)) {
          return route.fulfill({ contentType: 'image/svg+xml', body: image })
        }
        unexpected.push(`Unlisted API fixture ${request.method()} ${url.pathname}${url.search}`)
        return route.abort('blockedbyclient')
      })()
      handlers.add(task)
      task.finally(() => handlers.delete(task)).catch(() => {})
      return task
    })

    async function openPage(width, height, mode) {
      const page = await context.newPage()
      page.setDefaultTimeout(5000)
      page.on('pageerror', (error) => runtimeErrors.push(error.message))
      const state = { mode, photoCalls: 0, response: deferred(), started: deferred() }
      states.set(page, state)
      await page.setViewportSize({ width, height })
      await page.goto(`${origin}/#/slideshow?interval=60&min_rating=0&lookahead=2`)
      await page.getByRole('heading', { name: 'Slideshow', level: 1 }).waitFor()
      return { page, state }
    }

    async function statusNodes(page) {
      const session = await page.context().newCDPSession(page)
      try {
        const { nodes } = await session.send('Accessibility.getFullAXTree')
        const byId = new Map(nodes.map((node) => [node.nodeId, node]))
        const collectText = (nodeId, seen = new Set()) => {
          if (seen.has(nodeId)) return []
          seen.add(nodeId)
          const node = byId.get(nodeId)
          if (!node) return []
          return [
            node.name?.value || '',
            ...(node.childIds || []).flatMap((childId) => collectText(childId, seen)),
          ].filter(Boolean)
        }
        const statuses = []
        for (const node of nodes.filter((item) => item.role?.value === 'status')) {
          let className = ''
          if (node.backendDOMNodeId) {
            const { node: domNode } = await session.send('DOM.describeNode', { backendNodeId: node.backendDOMNodeId })
            const classIndex = domNode.attributes?.indexOf('class') ?? -1
            if (classIndex >= 0) className = domNode.attributes[classIndex + 1]
          }
          statuses.push({
            className,
            live: node.properties?.find((item) => item.name === 'live')?.value?.value || null,
            text: collectText(node.nodeId).join(' ').trim(),
          })
        }
        return statuses
      } finally {
        await session.detach()
      }
    }

    async function recovery(width, height, mode, key, outcome, preserveExternalFocus = false) {
      await suite.test(`${width}x${height}: ${mode} by ${key}`, async () => {
        const { page, state } = await openPage(width, height, mode)
        let external, failure
        try {
          if (mode === 'empty-reload') {
            await page.getByRole('heading', { name: 'No photographs meet this threshold' }).waitFor()
            await tabTo(page, page.getByRole('button', { name: 'Reload photographs', exact: true }))
            await page.keyboard.press(key)
          } else {
            await page.getByRole('alert').waitFor()
            await tabTo(page, page.getByRole('button', { name: 'Retry photographs', exact: true }))
            await page.keyboard.press(key)
          }
          await state.started.promise
          await page.getByText('Loading photographs…').waitFor()
          const pending = await page.evaluate(() => ({
            focusId: document.activeElement.id,
            dialogOpen: document.querySelector('dialog.slideshow-view').open,
            modal: document.querySelector('dialog.slideshow-view').matches(':modal'),
            busy: document.querySelector('.slideshow-stage').getAttribute('aria-busy'),
            hash: location.hash,
          }))
          assert.equal(pending.focusId, 'ss-title')
          assert.equal(pending.dialogOpen && pending.modal, true)
          assert.equal(pending.busy, 'true')
          assert.equal(pending.hash, '#/slideshow?interval=60&min_rating=0&lookahead=2')

          if (preserveExternalFocus) {
            await page.evaluate(() => {
              const dialog = document.createElement('dialog')
              dialog.id = 'synthetic-external-dialog'
              dialog.innerHTML = '<button type="button">External action</button>'
              document.body.append(dialog)
              dialog.showModal()
              dialog.querySelector('button').focus()
            })
            external = page.locator('#synthetic-external-dialog')
          }

          const result = outcome === 'error'
            ? { status: 503, body: { detail: 'Synthetic retry failure' } }
            : { status: 200, body: mode === 'empty-reload' ? [] : photos }
          state.response.resolve(result)
          if (outcome === 'error') {
            await page.getByRole('alert').filter({ hasText: 'Synthetic retry failure' }).waitFor()
            assert.equal(await page.evaluate(() => document.activeElement.id), 'ss-title')
          } else if (mode === 'empty-reload') {
            await page.getByRole('heading', { name: 'No photographs meet this threshold' }).waitFor()
            assert.equal(await page.evaluate(() => document.activeElement.id), 'ss-title')
          } else {
            await page.waitForFunction(() => {
              const current = document.querySelector('.slideshow-photo')
              return current?.complete && current.naturalWidth > 0
            })
            assert.equal(await page.evaluate(() => document.activeElement.id), preserveExternalFocus ? '': 'ss-title')
            if (preserveExternalFocus) {
              assert.equal(await page.evaluate(() => document.activeElement.textContent.trim()), 'External action')
              await page.evaluate(() => {
                const dialog = document.getElementById('synthetic-external-dialog')
                dialog.close(); dialog.remove()
              })
              assert.equal(await page.evaluate(() => document.activeElement.id), 'ss-title')
            }
          }
          assert.equal(state.photoCalls, 2)
        } catch (error) {
          failure = error
        } finally {
          const cleanupErrors = []
          state.response.resolve({ status: 503, body: { detail: 'Synthetic teardown' } })
          try {
            if (external && await external.count()) await page.evaluate(() => {
              const dialog = document.getElementById('synthetic-external-dialog')
              dialog?.close(); dialog?.remove()
            })
          } catch (error) {
            cleanupErrors.push(error)
          }
          let pageCloseFailed = false
          try {
            await page.close()
          } catch (error) {
            cleanupErrors.push(error); pageCloseFailed = true
          }
          if (!pageCloseFailed) await Promise.allSettled([...handlers])
          failure = combineFailures(failure, cleanupErrors, 'Recovery scenario and cleanup failed')
        }
        if (failure) throw failure
      })
    }

    for (const [width, height] of [[375, 812], [1440, 1000]]) {
      await recovery(width, height, 'retry-success', 'Enter', 'success', width === 1440)
      await recovery(width, height, 'retry-error', 'Space', 'error')
      await recovery(width, height, 'empty-reload', 'Enter', 'success')
      await suite.test(`${width}x${height}: first Escape exits fullscreen only`, async () => {
        const { page } = await openPage(width, height, 'fullscreen')
        let failure
        try {
          await page.getByRole('button', { name: 'Fullscreen', exact: true }).click()
          await page.waitForFunction(() => Boolean(document.fullscreenElement))
          const originalHash = await page.evaluate(() => location.hash)
          await page.keyboard.press('Escape')
          await page.waitForFunction(() => !document.fullscreenElement)
          await page.waitForFunction(() => document.querySelector('dialog.slideshow-view')?.open)
          assert.equal(await page.evaluate(() => location.hash), originalHash)
          assert.equal(await page.locator('dialog.slideshow-view').evaluate((dialog) => dialog.matches(':modal')), true)
          await page.keyboard.press('Escape')
          await page.waitForFunction(() => location.hash === '#/library')
          await page.waitForFunction(() => !document.querySelector('dialog.slideshow-view'))
          assert.equal(stateFor(page)?.photoCalls, 1)
        } catch (error) {
          failure = error
        } finally {
          const cleanupErrors = []
          try {
            await page.evaluate(() => document.fullscreenElement && document.exitFullscreen())
          } catch (error) {
            cleanupErrors.push(error)
          }
          let pageCloseFailed = false
          try {
            await page.close()
          } catch (error) {
            cleanupErrors.push(error); pageCloseFailed = true
          }
          if (!pageCloseFailed) await Promise.allSettled([...handlers])
          failure = combineFailures(failure, cleanupErrors, 'Fullscreen scenario and cleanup failed')
        }
        if (failure) throw failure
      })
    }

    async function emptyResultScenario(width, height, mode) {
      await suite.test(width + 'x' + height + ': ' + mode + ' announces only the current empty result', async () => {
        const { page, state } = await openPage(width, height, mode)
        let failure
        try {
          const region = page.locator('.slideshow-empty-status')
          await region.waitFor()
          assert.equal(await region.getAttribute('role'), 'status')

          if (mode === 'empty-initial') {
            await state.started.promise
            await page.getByText('Loading photographs…').waitFor()
            assert.equal(await region.textContent(), '')
            const loadingAx = await statusNodes(page)
            assert.ok(loadingAx.some((item) => item.className.split(' ').includes('slideshow-empty-status') && item.live === 'polite'))
            assert.ok(loadingAx.some((item) => item.text.includes('Loading photographs…')))
            state.response.resolve({ status: 200, body: [] })
          } else if (mode === 'empty-retry') {
            await page.getByRole('heading', { name: 'No photographs meet this threshold' }).waitFor()
            assert.equal(await region.textContent(), 'No photographs meet this threshold')
            await tabTo(page, page.getByRole('button', { name: 'Reload photographs', exact: true }))
            await page.keyboard.press('Enter')
            await state.started.promise
            await page.getByText('Loading photographs…').waitFor()
            assert.equal(await region.textContent(), '')
            const loadingAx = await statusNodes(page)
            assert.ok(loadingAx.some((item) => item.className.split(' ').includes('slideshow-empty-status') && item.text === '' && item.live === 'polite'))
            state.response.resolve({ status: 200, body: [] })
          } else {
            await page.waitForFunction(() => {
              const image = document.querySelector('.slideshow-photo')
              return image?.complete && image.naturalWidth > 0
            })
            assert.equal(await region.textContent(), '')
            await page.locator('#ss-rating').focus()
            await page.locator('#ss-rating').selectOption('5')
            await state.started.promise
            await page.waitForFunction(() => document.querySelector('.slideshow-stage')?.getAttribute('aria-busy') === 'true')
            assert.equal(await region.textContent(), '')
            assert.equal(await page.evaluate(() => document.activeElement.id), 'ss-rating')
            state.response.resolve({ status: 200, body: [] })
          }

          await page.getByRole('heading', { name: 'No photographs meet this threshold' }).waitFor()
          assert.equal(await region.textContent(), 'No photographs meet this threshold')
          assert.equal(await page.getByRole('button', { name: 'Reload photographs', exact: true }).count(), 1)
          assert.equal(await page.locator('.slideshow-stage').getAttribute('aria-busy'), 'false')
          const resultAx = await statusNodes(page)
          assert.ok(resultAx.some((item) => item.className.split(' ').includes('slideshow-empty-status')
            && item.live === 'polite' && item.text.includes('No photographs meet this threshold')))
          assert.equal(await page.evaluate(() => location.hash), mode === 'empty-rating'
            ? '#/slideshow?interval=60&min_rating=5&lookahead=2'
            : '#/slideshow?interval=60&min_rating=0&lookahead=2')
          assert.equal(await page.evaluate(() => document.activeElement.id), mode === 'empty-rating' ? 'ss-rating' : 'ss-title')

          if (mode === 'empty-rating') {
            await page.locator('#ss-rating').selectOption('0')
            await page.waitForFunction(() => {
              const image = document.querySelector('.slideshow-photo')
              return image?.complete && image.naturalWidth > 0
            })
            assert.equal(await region.textContent(), '')
            assert.equal(await page.evaluate(() => location.hash), '#/slideshow?interval=60&min_rating=0&lookahead=2')
            assert.equal(await page.evaluate(() => document.activeElement.id), 'ss-rating')
          }
          assert.equal(state.photoCalls, mode === 'empty-rating' ? 3 : mode === 'empty-initial' ? 1 : 2)
        } catch (error) {
          failure = error
        } finally {
          const cleanupErrors = []
          state.response.resolve({ status: 503, body: { detail: 'Synthetic teardown' } })
          let pageCloseFailed = false
          try {
            await page.close()
          } catch (error) {
            cleanupErrors.push(error); pageCloseFailed = true
          }
          if (!pageCloseFailed) await Promise.allSettled([...handlers])
          failure = combineFailures(failure, cleanupErrors, 'Empty-result scenario and cleanup failed')
        }
        if (failure) throw failure
      })
    }

    for (const [width, height] of [[375, 812], [1440, 1000]]) {
      await emptyResultScenario(width, height, 'empty-initial')
      await emptyResultScenario(width, height, 'empty-retry')
      await emptyResultScenario(width, height, 'empty-rating')
    }

    function stateFor(page) { return states.get(page) }

    assert.deepEqual(unexpected, [], `Unexpected requests: ${unexpected.join(', ')}`)
    assert.deepEqual(runtimeErrors, [], `Runtime errors: ${runtimeErrors.join(', ')}`)
  } catch (error) {
    failure = error
  } finally {
    const cleanupErrors = []
    try {
      if (context) await context.close()
    } catch (error) {
      cleanupErrors.push(error)
    } finally {
      await Promise.allSettled([...handlers])
      try {
        await cleanupOwnedRoot(owned)
      } catch (error) {
        cleanupErrors.push(error)
      }
    }
    failure = combineFailures(failure, cleanupErrors, 'Browser scenario and cleanup failed')
  }
  if (failure) throw failure
})
