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

async function assertVisibleFocus(locator, label) {
  const focus = await locator.evaluate((element) => {
    const rect = element.getBoundingClientRect()
    const region = element.closest('.slideshow-controls, .slideshow-header').getBoundingClientRect()
    const style = getComputedStyle(element)
    return {
      active: document.activeElement === element,
      focusVisible: element.matches(':focus-visible'),
      outlineStyle: style.outlineStyle,
      outlineWidth: parseFloat(style.outlineWidth),
      left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
      width: rect.width, height: rect.height,
      regionLeft: region.left, regionRight: region.right, regionTop: region.top, regionBottom: region.bottom,
      viewportWidth: innerWidth, viewportHeight: innerHeight,
    }
  })
  assert.ok(focus.active, `${label} lost keyboard focus: ${JSON.stringify(focus)}`)
  assert.ok(focus.focusVisible && focus.outlineStyle !== 'none' && focus.outlineWidth >= 2,
    `${label} has no visible keyboard focus: ${JSON.stringify(focus)}`)
  assert.ok(focus.width >= 44 && focus.height >= 44, `${label} is smaller than 44px: ${JSON.stringify(focus)}`)
  assert.ok(focus.left >= 0 && focus.right <= focus.viewportWidth
    && focus.top >= 0 && focus.bottom <= focus.viewportHeight
    && focus.left >= focus.regionLeft && focus.right <= focus.regionRight
    && focus.top >= focus.regionTop && focus.bottom <= focus.regionBottom,
  `${label} is not fully visible: ${JSON.stringify(focus)}`)
}

async function waitForScrollToSettle(page, selector, start) {
  await page.locator(selector).evaluate((element, scrollTop) => {
    element.__scrollCheck = { last: scrollTop, changed: false, stableFrames: 0 }
  }, start)
  await page.waitForFunction((target) => {
    const element = document.querySelector(target), state = element.__scrollCheck
    const current = element.scrollTop
    if (Math.abs(current - state.last) > 0.1) {
      state.last = current; state.changed = true; state.stableFrames = 0
    } else if (state.changed) state.stableFrames += 1
    return state.changed && state.stableFrames >= 3
  }, selector, { polling: 'raf', timeout: 5000 })
  return page.locator(selector).evaluate((element) => {
    const scrollTop = element.scrollTop
    delete element.__scrollCheck
    return scrollTop
  })
}

async function pageKeyScrollState(page) {
  return page.evaluate(() => {
    const rect = (selector) => {
      const { left, right, top, bottom } = document.querySelector(selector).getBoundingClientRect()
      return { left, right, top, bottom }
    }
    const active = document.activeElement
    const footer = document.querySelector('.slideshow-controls')
    return {
      active: active === document.querySelector('.slideshow-actions > button'),
      focusVisible: active.matches(':focus-visible'),
      name: active.textContent.trim(),
      clicks: window.pauseActivations,
      footerScrollTop: footer.scrollTop,
      footerMaxScroll: Math.max(0, footer.scrollHeight - footer.clientHeight),
      dialogScrollTop: document.querySelector('.slideshow-view').scrollTop,
      documentScrollTop: document.scrollingElement.scrollTop,
      stage: rect('.slideshow-stage'),
      photo: rect('.slideshow-photo'),
    }
  })
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
          if (state.mode === 'all-failed-images' && state.photoCalls === 1) return route.fulfill({ json: photos })
          if (['paused-rating', 'paused-rating-error'].includes(state.mode) && state.photoCalls === 1) return route.fulfill({ json: photos })
          if (['paused-rating', 'paused-rating-error'].includes(state.mode) && state.photoCalls === 2 && url.searchParams.get('min_rating') === '5') {
            state.started.resolve()
            const response = await state.response.promise
            return route.fulfill({ status: response.status, json: response.body })
          }
          if (state.mode === 'paused-rating-error' && state.photoCalls === 3) {
            state.retryStarted.resolve()
            const response = await state.retryResponse.promise
            return route.fulfill({ status: response.status, json: response.body })
          }
          if (state.mode === 'paused-rating-error' && state.photoCalls >= 4) return route.fulfill({ json: photos })
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
          const id = Number(url.pathname.match(/\d+/)[0])
          state.imageCalls.set(id, (state.imageCalls.get(id) || 0) + 1)
          if (state.failAllImages || (state.failedImageId === id && url.searchParams.has('synthetic_failure'))) {
            state.failedImageRequests += 1
            return route.fulfill({ status: 503, contentType: 'text/plain', body: 'Synthetic image failure' })
          }
          return route.fulfill({ contentType: 'image/svg+xml', body: image })
        }
        unexpected.push(`Unlisted API fixture ${request.method()} ${url.pathname}${url.search}`)
        return route.abort('blockedbyclient')
      })()
      handlers.add(task)
      task.finally(() => handlers.delete(task)).catch(() => {})
      return task
    })

    async function openPage(width, height, mode, { interval = 60, clock = false, lookahead = 2 } = {}) {
      const page = await context.newPage()
      page.setDefaultTimeout(5000)
      page.on('pageerror', (error) => runtimeErrors.push(error.message))
      const state = {
        mode, photoCalls: 0, response: deferred(), started: deferred(), retryResponse: deferred(), retryStarted: deferred(),
        imageCalls: new Map(), failedImageId: null, failedImageRequests: 0, failAllImages: mode === 'all-failed-images',
      }
      states.set(page, state)
      await page.setViewportSize({ width, height })
      if (clock) await page.clock.install()
      if (mode === 'all-failed-images') {
        await page.addInitScript(() => {
          const active = new Set()
          const audit = { created: 0, cleared: 0, active: 0 }
          const set = window.setInterval.bind(window)
          const clear = window.clearInterval.bind(window)
          window.__slideshowTimerAudit = audit
          window.setInterval = (callback, delay, ...args) => {
            const id = set(callback, delay, ...args)
            if (delay === 1000) { audit.created += 1; active.add(id); audit.active = active.size }
            return id
          }
          window.clearInterval = (id) => {
            if (active.delete(id)) { audit.cleared += 1; audit.active = active.size }
            return clear(id)
          }
        })
      }
      await page.goto(`${origin}/#/slideshow?interval=${interval}&min_rating=0&lookahead=${lookahead}`)
      await page.getByRole('heading', { name: 'Slideshow', level: 1 }).waitFor()
      return { page, state }
    }

    async function closePage(page) {
      let failure
      try {
        await page.close()
      } catch (error) {
        failure = error
      }
      if (!failure) await Promise.allSettled([...handlers])
      if (failure) throw failure
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

    await suite.test('Pause and Resume have dynamic accessible names, keyboard order, and stable focus', async () => {
      const { page } = await openPage(375, 812, 'fullscreen')
      let failure
      try {
        await page.waitForFunction(() => {
          const current = document.querySelector('.slideshow-photo')
          return current?.complete && current.naturalWidth > 0
        })
        const pause = page.getByRole('button', { name: 'Pause slideshow', exact: true })
        await tabTo(page, pause)
        assert.equal(await page.evaluate(() => document.activeElement.textContent.trim()), 'Pause slideshow')
        assert.equal(await pause.getAttribute('aria-pressed'), null)
        assert.equal(await pause.evaluate((button) => getComputedStyle(button).outlineStyle !== 'none'), true)
        await page.keyboard.press('Shift+Tab')
        assert.equal(await page.evaluate(() => document.activeElement.id), 'ss-interval')
        await page.keyboard.press('Tab')
        await page.keyboard.press('Space')
        const resume = page.getByRole('button', { name: 'Resume slideshow', exact: true })
        assert.equal(await resume.isEnabled(), true)
        assert.equal(await resume.evaluate((button) => document.activeElement === button), true)
        await page.keyboard.press('Tab')
        assert.equal(await page.evaluate(() => document.activeElement.textContent.trim()), 'Fullscreen')
        await page.keyboard.press('Shift+Tab')
        assert.equal(await resume.evaluate((button) => document.activeElement === button), true)
        await page.keyboard.press('Enter')
        const playing = page.getByRole('button', { name: 'Pause slideshow', exact: true })
        assert.equal(await playing.evaluate((button) => document.activeElement === button), true)
        assert.equal(await playing.getAttribute('aria-pressed'), null)
      } catch (error) {
        failure = error
      } finally {
        try { await closePage(page) } catch (error) { failure = combineFailures(failure, [error], 'Keyboard scenario and cleanup failed') }
      }
      if (failure) throw failure
    })

    await suite.test('Rating while paused keeps Resume available through the pending request and new set', async () => {
      const { page, state } = await openPage(375, 812, 'paused-rating')
      let failure
      try {
        await page.waitForFunction(() => {
          const current = document.querySelector('.slideshow-photo')
          return current?.complete && current.naturalWidth > 0
        })
        await page.getByRole('button', { name: 'Pause slideshow', exact: true }).click()
        await page.locator('#ss-rating').selectOption('5')
        await state.started.promise
        const resume = page.getByRole('button', { name: 'Resume slideshow', exact: true })
        assert.equal(await resume.isEnabled(), true)
        assert.equal(await page.locator('.slideshow-header .ui-help').textContent(), 'Photograph 1 of 2')
        assert.equal(await page.locator('.slideshow-context .ui-help').textContent(), 'Updating the photograph set… Playback is paused; resume to continue.')
        assert.match(await page.evaluate(() => location.hash), /min_rating=5/)
        state.response.resolve({ status: 200, body: photos })
        await page.waitForFunction(() => {
          const current = document.querySelector('.slideshow-photo')
          return current?.complete && current.naturalWidth > 0
        })
        assert.equal(await resume.isEnabled(), true)
        assert.equal(await resume.textContent(), 'Resume slideshow')
        assert.equal(await page.evaluate(() => location.hash.includes('playing=')), false)
      } catch (error) {
        failure = error
      } finally {
        state.response.resolve({ status: 503, body: { detail: 'Synthetic teardown' } })
        try { await closePage(page) } catch (error) { failure = combineFailures(failure, [error], 'Rating scenario and cleanup failed') }
      }
      if (failure) throw failure
    })

    await suite.test('A fully failed one-photo lookahead cancels the timer and makes no further image requests', async () => {
      const { page, state } = await openPage(375, 812, 'all-failed-images', { interval: 1, clock: true, lookahead: 1 })
      let failure
      try {
        await page.getByRole('heading', { name: 'No photograph could be displayed' }).waitFor()
        await page.waitForFunction(() => window.__slideshowTimerAudit?.active === 0)
        assert.deepEqual(await page.evaluate(() => window.__slideshowTimerAudit), { created: 1, cleared: 1, active: 0 })
        assert.equal(await page.locator('.slideshow-context .ui-help').textContent(), 'No photographs are available to advance.')
        await new Promise((resolve) => setTimeout(resolve, 25))
        await Promise.allSettled([...handlers])
        const exhaustedRequests = [...state.imageCalls.entries()].sort(([a], [b]) => a - b)
        await page.clock.runFor(5000)
        await Promise.allSettled([...handlers])
        assert.deepEqual([...state.imageCalls.entries()].sort(([a], [b]) => a - b), exhaustedRequests)
        assert.equal(state.photoCalls, 1)
      } catch (error) {
        failure = error
      } finally {
        try { await closePage(page) } catch (error) { failure = combineFailures(failure, [error], 'All-failed scenario and cleanup failed') }
      }
      if (failure) throw failure
    })

    await suite.test('A paused rating error and Retry preserve visible pause status, focus, and playback state', async () => {
      const { page, state } = await openPage(375, 812, 'paused-rating-error', { interval: 1, clock: true })
      let failure
      try {
        await page.waitForFunction(() => {
          const current = document.querySelector('.slideshow-photo')
          return current?.complete && current.naturalWidth > 0
        })
        await page.getByRole('button', { name: 'Pause slideshow', exact: true }).click()
        await page.locator('#ss-rating').focus()
        await page.locator('#ss-rating').selectOption('5')
        await state.started.promise
        const resume = page.getByRole('button', { name: 'Resume slideshow', exact: true })
        assert.equal(await resume.isEnabled(), true)
        assert.equal(await page.locator('.slideshow-context .ui-help').textContent(), 'Updating the photograph set… Playback is paused; resume to continue.')
        state.response.resolve({ status: 503, body: { detail: 'Threshold unavailable' } })
        await page.getByText('Threshold unavailable').waitFor()
        assert.equal(await resume.isDisabled(), true)
        assert.equal(await resume.textContent(), 'Resume slideshow')
        assert.equal(await page.locator('.slideshow-context .ui-help').textContent(), 'Playback is paused; no photographs will advance while this set is unavailable.')
        assert.equal(await page.evaluate(() => document.activeElement.id), 'ss-rating')
        const statuses = await statusNodes(page)
        assert.equal(statuses.filter((item) => item.className.split(' ').includes('slideshow-empty-status')).length, 1)
        assert.equal(statuses.find((item) => item.className.split(' ').includes('slideshow-empty-status')).text, '')
        assert.equal(await page.getByRole('alert').count(), 1)

        await page.getByRole('button', { name: 'Retry photographs', exact: true }).focus()
        await page.keyboard.press('Enter')
        await state.retryStarted.promise
        assert.equal(await page.evaluate(() => document.activeElement.id), 'ss-title')
        assert.equal(await resume.isDisabled(), true)
        assert.equal(await page.locator('.slideshow-context .ui-help').textContent(), 'Playback is paused; no photographs will advance while this set is unavailable.')
        state.retryResponse.resolve({ status: 503, body: { detail: 'Retry unavailable' } })
        await page.getByText('Retry unavailable').waitFor()
        assert.equal(await page.getByRole('alert').count(), 1)
        assert.equal(await resume.isDisabled(), true)
        assert.equal(await page.locator('.slideshow-context .ui-help').textContent(), 'Playback is paused; no photographs will advance while this set is unavailable.')
        assert.equal(await page.evaluate(() => document.activeElement.id), 'ss-title')

        await page.getByRole('button', { name: 'Retry photographs', exact: true }).focus()
        await page.keyboard.press('Enter')
        await page.waitForFunction(() => {
          const current = document.querySelector('.slideshow-photo')
          return current?.complete && current.naturalWidth > 0
        })
        assert.equal(await resume.isEnabled(), true)
        assert.equal(await resume.textContent(), 'Resume slideshow')
        assert.equal(await page.locator('.slideshow-context .ui-help').textContent(), 'Playback is paused; resume to continue.')
        assert.equal(await page.evaluate(() => document.activeElement.id), 'ss-title')
        assert.equal(await page.getByRole('alert').count(), 0)
        assert.equal(state.photoCalls, 4)
        const currentSrc = await page.locator('.slideshow-photo').getAttribute('src')
        await page.clock.runFor(3000)
        assert.equal(await page.locator('.slideshow-photo').getAttribute('src'), currentSrc)
        await resume.click()
        await page.clock.runFor(500)
        assert.equal(await page.locator('.slideshow-photo').getAttribute('src'), currentSrc)
        await page.clock.runFor(500)
        await page.waitForFunction((src) => {
          const current = document.querySelector('.slideshow-photo')
          return current?.complete && current.naturalWidth > 0 && current.getAttribute('src') !== src
        }, currentSrc)
      } catch (error) {
        failure = error
      } finally {
        state.response.resolve({ status: 503, body: { detail: 'Synthetic teardown' } })
        state.retryResponse.resolve({ status: 503, body: { detail: 'Synthetic teardown' } })
        try { await closePage(page) } catch (error) { failure = combineFailures(failure, [error], 'Paused rating error scenario and cleanup failed') }
      }
      if (failure) throw failure
    })

    await suite.test('A real failed image stays selected while paused and Resume waits a full interval', async () => {
      const { page, state } = await openPage(375, 812, 'fullscreen', { interval: 1, clock: true })
      let failure
      try {
        await page.waitForFunction(() => {
          const current = document.querySelector('.slideshow-photo')
          return current?.complete && current.naturalWidth > 0
        })
        const current = page.locator('.slideshow-photo')
        const originalSrc = await current.getAttribute('src')
        const selected = await page.locator('.slideshow-header .ui-help').textContent()
        const imageId = Number(originalSrc.match(/\/(\d+)\/image$/)[1])
        await page.getByRole('button', { name: 'Pause slideshow', exact: true }).click()
        state.mode = 'paused-image-failure'
        state.failedImageId = imageId
        await page.evaluate((src) => { document.querySelector('.slideshow-photo').src = `${src}?synthetic_failure=1` }, originalSrc)
        await page.getByText('This photograph is unavailable. Resume playback to continue.').waitFor()
        assert.equal(await page.locator('.slideshow-header .ui-help').textContent(), selected)
        assert.equal(await page.locator('.slideshow-stage').getAttribute('aria-busy'), 'false')
        assert.equal(await page.getByText('Loading photograph…').count(), 0)
        assert.equal(await page.getByText('The current photograph is unavailable. Playback is paused.').count(), 1)
        const failedRequests = state.failedImageRequests
        assert.equal(failedRequests, 1)
        await page.clock.runFor(3000)
        assert.equal(await page.locator('.slideshow-header .ui-help').textContent(), selected)
        assert.equal(state.failedImageRequests, failedRequests)
        assert.equal(await page.getByRole('button', { name: 'Resume slideshow', exact: true }).count(), 1)
        await page.getByRole('button', { name: 'Resume slideshow', exact: true }).click()
        await page.clock.runFor(500)
        assert.equal(await page.locator('.slideshow-header .ui-help').textContent(), selected)
        await page.clock.runFor(500)
        await page.waitForFunction((src) => {
          const currentPhoto = document.querySelector('.slideshow-photo')
          return currentPhoto?.complete && currentPhoto.naturalWidth > 0 && currentPhoto.getAttribute('src') !== src
        }, originalSrc)
        assert.equal(state.failedImageRequests, failedRequests)
      } catch (error) {
        failure = error
      } finally {
        try { await closePage(page) } catch (error) { failure = combineFailures(failure, [error], 'Paused image scenario and cleanup failed') }
      }
      if (failure) throw failure
    })

    await suite.test('Fullscreen Escape returns to the window without resuming playback', async () => {
      const { page } = await openPage(375, 812, 'fullscreen')
      let failure
      try {
        await page.waitForFunction(() => Boolean(document.querySelector('.slideshow-photo')?.naturalWidth))
        await page.getByRole('button', { name: 'Pause slideshow', exact: true }).click()
        const selected = await page.locator('.slideshow-header .ui-help').textContent()
        await page.getByRole('button', { name: 'Fullscreen', exact: true }).click()
        await page.waitForFunction(() => Boolean(document.fullscreenElement))
        await page.keyboard.press('Escape')
        await page.waitForFunction(() => !document.fullscreenElement && document.querySelector('dialog.slideshow-view')?.open)
        assert.equal(await page.getByRole('button', { name: 'Resume slideshow', exact: true }).isEnabled(), true)
        assert.equal(await page.locator('.slideshow-header .ui-help').textContent(), selected)
      } catch (error) {
        failure = error
      } finally {
        const cleanupErrors = []
        try { await page.evaluate(() => document.fullscreenElement && document.exitFullscreen()) } catch (error) { cleanupErrors.push(error) }
        try { await closePage(page) } catch (error) { cleanupErrors.push(error) }
        failure = combineFailures(failure, cleanupErrors, 'Paused fullscreen scenario and cleanup failed')
      }
      if (failure) throw failure
    })

    for (const [width, height] of [
      [320, 320], [320, 568], [375, 320], [375, 812], [768, 1024], [1440, 1000], [844, 390], [844, 320],
      [499, 320], [499, 390], [499, 420], [499, 421],
      [500, 320], [560, 320], [607, 320], [608, 320],
      [500, 390], [607, 390], [608, 390],
      [500, 420], [500, 421], [608, 420], [608, 421],
      [899, 320], [900, 320],
    ]) {
      const compact = width <= 499 && height <= 420
      const nativeVerticalScroll = width === 499 && height === 421
      await suite.test(`${width}x${height}: slideshow layout and keyboard controls remain usable`, async () => {
        const { page } = await openPage(width, height, 'fullscreen')
        let failure
        try {
          await page.waitForFunction(() => {
            const current = document.querySelector('.slideshow-photo')
            return current?.complete && current.naturalWidth > 0
          })
          const metrics = await page.evaluate(() => {
            const bounds = (element) => {
              const { left, right, top, bottom, width, height } = element.getBoundingClientRect()
              return { left, right, top, bottom, width, height }
            }
            const dialog = document.querySelector('dialog.slideshow-view')
            const header = document.querySelector('.slideshow-header')
            const play = document.querySelector('.slideshow-actions > button')
            const fullscreen = document.querySelector('.slideshow-fullscreen .ui-button')
            const stage = document.querySelector('.slideshow-stage')
            const photo = document.querySelector('.slideshow-photo')
            const footer = document.querySelector('.slideshow-controls')
            const documentScroller = document.scrollingElement
            const compactMedia = [...document.styleSheets].flatMap((sheet) => [...sheet.cssRules])
              .find((rule) => rule.type === CSSRule.MEDIA_RULE
                && rule.conditionText.includes('max-width: 499px') && rule.conditionText.includes('max-height: 420px'))
            const compactCSS = compactMedia ? [...compactMedia.cssRules].map((rule) => rule.cssText).join('\n') : ''
            const headerStyle = getComputedStyle(header), footerStyle = getComputedStyle(footer)
            return {
              documentWidth: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
              viewportWidth: innerWidth,
              viewportHeight: innerHeight,
              documentScrollHeight: documentScroller.scrollHeight,
              documentClientHeight: documentScroller.clientHeight,
              documentScrollTop: documentScroller.scrollTop,
              dialog: {
                ...bounds(dialog),
                scrollWidth: dialog.scrollWidth,
                clientWidth: dialog.clientWidth,
                scrollHeight: dialog.scrollHeight,
                clientHeight: dialog.clientHeight,
                scrollTop: dialog.scrollTop,
              },
              header: bounds(header), play: bounds(play), fullscreen: bounds(fullscreen),
              stage: bounds(stage), photo: bounds(photo), footer: bounds(footer),
              footerScrollHeight: footer.scrollHeight,
              footerClientHeight: footer.clientHeight,
              footerScrollWidth: footer.scrollWidth,
              footerClientWidth: footer.clientWidth,
              footerScrollTop: footer.scrollTop,
              footerOverflowY: getComputedStyle(footer).overflowY,
              compactPadding: {
                headerBlockStart: parseFloat(headerStyle.paddingBlockStart),
                headerBlockEnd: parseFloat(headerStyle.paddingBlockEnd),
                headerInlineStart: parseFloat(headerStyle.paddingInlineStart),
                headerInlineEnd: parseFloat(headerStyle.paddingInlineEnd),
                footerBlockStart: parseFloat(footerStyle.paddingBlockStart),
                footerBlockEnd: parseFloat(footerStyle.paddingBlockEnd),
                footerInlineStart: parseFloat(footerStyle.paddingInlineStart),
                footerInlineEnd: parseFloat(footerStyle.paddingInlineEnd),
              },
              compactCSS,
              objectFit: getComputedStyle(photo).objectFit,
            }
          })
          assert.ok(metrics.dialog.scrollWidth <= metrics.dialog.clientWidth, 'dialog horizontal overflow: ' + JSON.stringify(metrics))
          assert.ok(metrics.footerScrollWidth <= metrics.footerClientWidth, 'footer horizontal overflow: ' + JSON.stringify(metrics))
          assert.ok(metrics.documentWidth <= metrics.viewportWidth, `horizontal overflow: ${JSON.stringify(metrics)}`)
          for (const button of [metrics.play, metrics.fullscreen]) {
            assert.ok(button.left >= 0 && button.right <= width, `button outside horizontal viewport: ${JSON.stringify(metrics)}`)
            if (!compact && !nativeVerticalScroll) {
              assert.ok(button.top >= 0 && button.bottom <= height, `button outside vertical viewport: ${JSON.stringify(metrics)}`)
            }
            assert.ok(button.width >= 44 && button.height >= 44, `undersized target: ${JSON.stringify(metrics)}`)
          }
          assert.ok(metrics.play.right <= metrics.fullscreen.left || metrics.fullscreen.right <= metrics.play.left
            || metrics.play.bottom <= metrics.fullscreen.top || metrics.fullscreen.bottom <= metrics.play.top,
          `playback and fullscreen controls overlap: ${JSON.stringify(metrics)}`)
          assert.ok(metrics.header.bottom <= metrics.stage.top, 'header overlaps photo area: ' + JSON.stringify(metrics))
          assert.ok(metrics.stage.bottom <= metrics.footer.top, `photo area overlaps footer: ${JSON.stringify(metrics)}`)
          assert.ok(metrics.photo.left >= metrics.stage.left && metrics.photo.right <= metrics.stage.right
            && metrics.photo.top >= metrics.stage.top && metrics.photo.bottom <= metrics.stage.bottom,
          'photograph escapes its stage: ' + JSON.stringify(metrics))
          if (compact) {
            assert.ok(Object.entries(metrics.compactPadding)
              .every(([side, value]) => value >= (side.includes('Inline') ? 12 : 4)),
            'compact padding is below its CSS minimum: ' + JSON.stringify(metrics.compactPadding))
            for (const declaration of [
              'max(4px, env(safe-area-inset-top))', 'max(4px, env(safe-area-inset-bottom))',
              'max(12px, env(safe-area-inset-left))', 'max(12px, env(safe-area-inset-right))',
            ]) assert.ok(metrics.compactCSS.includes(declaration), `compact CSS lost ${declaration}`)
            assert.ok(metrics.dialog.scrollHeight <= metrics.dialog.clientHeight, 'dialog has exterior vertical overflow: ' + JSON.stringify(metrics))
            assert.ok(metrics.documentScrollHeight <= metrics.documentClientHeight && metrics.documentScrollTop === 0,
              'document has exterior vertical scrolling: ' + JSON.stringify(metrics))
            assert.equal(metrics.dialog.scrollTop, 0, 'dialog scrolled outside the compact footer: ' + JSON.stringify(metrics))
            assert.ok(metrics.header.top >= 0 && metrics.header.bottom <= height, 'header outside compact viewport: ' + JSON.stringify(metrics))
            assert.ok(metrics.stage.top >= 0 && metrics.stage.bottom <= height && metrics.stage.height >= 160,
              'compact photo stage is outside the viewport or below 160px: ' + JSON.stringify(metrics))
            assert.ok(metrics.footer.top >= 0 && metrics.footer.bottom <= height, 'footer outside compact viewport: ' + JSON.stringify(metrics))
            assert.equal(metrics.footerOverflowY, 'auto', 'compact footer is not the scroll container: ' + JSON.stringify(metrics))
          } else if (!nativeVerticalScroll) {
            assert.ok(metrics.header.top >= 0 && metrics.header.bottom <= height, 'header outside vertical viewport: ' + JSON.stringify(metrics))
            assert.ok(metrics.stage.top >= 0 && metrics.stage.bottom <= height, 'photo area outside vertical viewport: ' + JSON.stringify(metrics))
            assert.ok(metrics.footer.top >= 0 && metrics.footer.bottom <= height, 'footer outside vertical viewport: ' + JSON.stringify(metrics))
          } else {
            await page.locator('.slideshow-view').evaluate((dialog) => { dialog.scrollTop = dialog.scrollHeight })
            const scroll = await page.locator('.slideshow-view').evaluate((dialog) => ({
              top: dialog.scrollTop,
              playBottom: document.querySelector('.slideshow-actions > button').getBoundingClientRect().bottom,
              fullscreenBottom: document.querySelector('.slideshow-fullscreen .ui-button').getBoundingClientRect().bottom,
            }))
            assert.ok(scroll.top > 0 && scroll.playBottom <= height && scroll.fullscreenBottom <= height,
              'native dialog scroll did not reveal the controls: ' + JSON.stringify({ metrics, scroll }))
          }
          assert.equal(metrics.objectFit, 'contain')
          const play = page.getByRole('button', { name: 'Pause slideshow', exact: true })
          const fullscreen = page.getByRole('button', { name: 'Fullscreen', exact: true })
          if (compact) {
            const focusOrder = [
              [page.getByRole('link', { name: 'Exit to Library', exact: true }), 'Exit to Library'],
              [page.getByLabel('Minimum rating', { exact: true }), 'Minimum rating'],
              [page.getByLabel('Seconds per photograph', { exact: true }), 'Seconds per photograph'],
              [play, 'Pause slideshow'],
              [fullscreen, 'Fullscreen'],
            ]
            assert.equal(await page.evaluate(() => document.activeElement.id), 'ss-title',
              'the compact keyboard path did not begin at the focused slideshow heading')
            for (const [target, label] of focusOrder) {
              await page.keyboard.press('Tab')
              await assertVisibleFocus(target, label)
            }
            for (let index = focusOrder.length - 2; index >= 0; index -= 1) {
              await page.keyboard.press('Shift+Tab')
              await assertVisibleFocus(focusOrder[index][0], `${focusOrder[index][1]} after Shift+Tab`)
            }
            await page.keyboard.press('Shift+Tab')
            await assertVisibleFocus(fullscreen, 'Fullscreen after Shift+Tab wraps')
            await page.keyboard.press('Tab')
            await assertVisibleFocus(focusOrder[0][0], 'Exit to Library after Tab wraps')
            for (let index = 1; index <= 3; index += 1) {
              await page.keyboard.press('Tab')
              await assertVisibleFocus(focusOrder[index][0], `${focusOrder[index][1]} before manual scrolling`)
            }
            await page.evaluate(() => {
              window.pauseActivations = 0
              document.querySelector('.slideshow-actions > button').addEventListener('click', () => { window.pauseActivations += 1 })
            })
            const beforePageKeys = await pageKeyScrollState(page)
            const requirePageKeys = height === 320
            assert.ok(!requirePageKeys || beforePageKeys.footerMaxScroll > 0,
              'compact 320px viewport has no footer scroll range: ' + JSON.stringify(beforePageKeys))
            if (beforePageKeys.footerMaxScroll > 0) {
              const pauseNode = await play.elementHandle()
              assert.ok(pauseNode, 'Pause button DOM node is missing')
              await page.locator('.slideshow-controls').evaluate((footer) => { footer.scrollTop = footer.scrollHeight })
              const initial = await pageKeyScrollState(page)
              assert.ok(initial.footerScrollTop > 0 && Math.abs(initial.footerScrollTop - initial.footerMaxScroll) <= 1,
                'footer was not scrolled to a position that allows PageUp: ' + JSON.stringify({ beforePageKeys, initial }))
              assert.equal(await page.evaluate((pause) => document.activeElement === pause, pauseNode), true,
                'Pause lost focus before PageUp')
              await page.keyboard.press('PageUp')
              await waitForScrollToSettle(page, '.slideshow-controls', initial.footerScrollTop)
              const afterPageUp = await pageKeyScrollState(page)
              assert.equal(await page.evaluate((pause) => document.activeElement === pause, pauseNode), true,
                'PageUp moved focus from the original Pause button')
              assert.ok(afterPageUp.active && afterPageUp.focusVisible && afterPageUp.name === 'Pause slideshow',
                'PageUp activated Pause or lost its focus ring: ' + JSON.stringify({ initial, afterPageUp }))
              assert.equal(afterPageUp.clicks, 0, 'PageUp activated Pause: ' + JSON.stringify({ initial, afterPageUp }))
              assert.equal(afterPageUp.dialogScrollTop, initial.dialogScrollTop, 'PageUp scrolled the dialog: ' + JSON.stringify({ initial, afterPageUp }))
              assert.equal(afterPageUp.documentScrollTop, initial.documentScrollTop, 'PageUp scrolled the document: ' + JSON.stringify({ initial, afterPageUp }))
              assert.deepEqual(afterPageUp.stage, initial.stage, 'PageUp moved the photo stage')
              assert.deepEqual(afterPageUp.photo, initial.photo, 'PageUp moved the photograph')
              assert.ok(afterPageUp.footerScrollTop <= 1, 'PageUp did not reach the footer top: ' + JSON.stringify({ initial, afterPageUp }))
              await page.keyboard.press('PageDown')
              await waitForScrollToSettle(page, '.slideshow-controls', afterPageUp.footerScrollTop)
              const afterPageDown = await pageKeyScrollState(page)
              assert.equal(await page.evaluate((pause) => document.activeElement === pause, pauseNode), true,
                'PageDown moved focus from the original Pause button')
              assert.ok(afterPageDown.active && afterPageDown.focusVisible && afterPageDown.name === 'Pause slideshow',
                'PageDown activated Pause or lost its focus ring: ' + JSON.stringify({ initial, afterPageUp, afterPageDown }))
              assert.equal(afterPageDown.clicks, 0, 'PageDown activated Pause: ' + JSON.stringify({ initial, afterPageUp, afterPageDown }))
              assert.equal(afterPageDown.dialogScrollTop, initial.dialogScrollTop, 'PageDown scrolled the dialog: ' + JSON.stringify({ initial, afterPageUp, afterPageDown }))
              assert.equal(afterPageDown.documentScrollTop, initial.documentScrollTop, 'PageDown scrolled the document: ' + JSON.stringify({ initial, afterPageUp, afterPageDown }))
              assert.deepEqual(afterPageDown.stage, initial.stage, 'PageDown moved the photo stage')
              assert.deepEqual(afterPageDown.photo, initial.photo, 'PageDown moved the photograph')
              assert.ok(Math.abs(afterPageDown.footerScrollTop - afterPageDown.footerMaxScroll) <= 1,
                'PageDown did not reach the footer end: ' + JSON.stringify({ initial, afterPageUp, afterPageDown }))
            }
            await assertVisibleFocus(play, 'Pause slideshow after manual footer scrolling')
            await page.keyboard.press('Tab')
            await assertVisibleFocus(fullscreen, 'Fullscreen after manual footer scrolling')
            await page.keyboard.press('Shift+Tab')
            await assertVisibleFocus(play, 'Pause slideshow after returning from manual footer scrolling')
          } else {
            await tabTo(page, play)
            await assertVisibleFocus(play, 'Pause slideshow')
            await page.keyboard.press('Tab')
            await assertVisibleFocus(fullscreen, 'Fullscreen')
            await page.keyboard.press('Shift+Tab')
            await assertVisibleFocus(play, 'Pause slideshow after Shift+Tab')
          }
        } catch (error) {
          failure = error
        } finally {
          try { await closePage(page) } catch (error) { failure = combineFailures(failure, [error], 'Responsive scenario and cleanup failed') }
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
