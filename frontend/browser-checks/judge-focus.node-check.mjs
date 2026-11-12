// Run explicitly: node --test frontend/browser-checks/judge-focus.node-check.mjs
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { build, stop } from 'esbuild'
import { chromium, expect } from '@playwright/test'
import { cleanupOwnedRoot, createOwnedRoot, ownedPath } from '../e2e/owned-root.mjs'

const origin = 'http://judge-focus.invalid'
const reading = { id: 1, slot: 'camera', manner: 'directed', key: 'front', label: 'Front-facing view', session_id: null }
const session = { id: 1, name: 'Synthetic studio', manner: 'directed', shot_count: 2 }
const image = '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#394b62"/></svg>'
const html = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Synthetic Judge focus check</title><link rel="stylesheet" href="/app.css"><div id="root"></div><script src="/app.js"></script></html>'

async function tabTo(page, locator, backwards = false) {
  for (let count = 0; count < 60; count++) {
    if (await locator.evaluate((element) => document.activeElement === element)) return
    await page.keyboard.press(backwards ? 'Shift+Tab' : 'Tab')
  }
  throw new Error('The keyboard target was not reached.')
}
const frame = (page) => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))))
const heading = (page) => page.locator('.judge-pass h2')
const choices = (page) => page.locator('.judge-choices button')

test('Judge preserves keyboard focus without stealing foreground ownership in Chromium', { timeout: 120000 }, async (suite) => {
  assert.equal(Number(process.versions.node.split('.')[0]), 22, 'Use Node 22 for browser checks.')
  const owned = await createOwnedRoot()
  const unexpected = [], runtimeErrors = []
  let context
  try {
    const bundle = await build({
      absWorkingDir: fileURLToPath(new URL('..', import.meta.url)),
      entryPoints: ['src/main.jsx'], bundle: true, write: false, outdir: 'memory',
      format: 'iife', define: { 'process.env.NODE_ENV': '"production"' }, logLevel: 'silent',
    })
    const js = Buffer.from(bundle.outputFiles.find((file) => file.path.endsWith('.js')).contents)
    const css = Buffer.from(bundle.outputFiles.find((file) => file.path.endsWith('.css')).contents)
    context = await chromium.launchPersistentContext(await ownedPath(owned, 'browser-profile'), {
      headless: true, serviceWorkers: 'block',
    })
    await context.routeWebSocket('**/*', async (socket) => {
      unexpected.push('WebSocket'); await socket.close()
    })
    console.log(JSON.stringify({ node: process.version, chromium: context.browser().version() }))

    async function scenario(width, name, run) {
      await suite.test(`${width}px: ${name}`, { timeout: 10000 }, async () => {
        const page = await context.newPage()
        page.setDefaultTimeout(4000)
        page.on('pageerror', (error) => runtimeErrors.push(error.message))
        const posts = [], gates = new Map(), handlers = new Set()
        let imageFails = false
        const hold = (method, path) => {
          let release
          const promise = new Promise((resolve) => { release = resolve })
          const gate = { count: 0, release, promise }
          gates.set(`${method} ${path}`, gate)
          return gate
        }
        await page.route('**/*', (route) => {
          const task = (async () => {
            const request = route.request(), url = new URL(request.url())
            const key = `${request.method()} ${url.pathname}${url.search}`
            if (url.origin !== origin) {
              unexpected.push(`${request.method()} ${url.origin}${url.pathname}`)
              return route.abort('blockedbyclient')
            }
            if (request.method() === 'POST' && url.pathname === '/api/shots/11/judge') posts.push(request.postDataJSON())
            const gate = gates.get(key)
            if (gate) {
              gate.count++
              const result = await gate.promise
              gates.delete(key)
              if (result) return route.fulfill(result)
            }
            if (request.method() === 'GET') {
              const fixtures = {
                '/api/components?all=1': [], '/api/wardrobe': { garments: [], outfits: [] },
                '/api/comfy/status': { online: false, output_dir_ok: true },
                '/api/sessions': [session], '/api/sessions/1': session,
                '/api/readings?manner=directed&session_id=1': [reading],
                '/api/sessions/1/judge-pass?slot=camera': { shots: [11, 12], controls: [], readings: [reading] },
              }
              if (Object.hasOwn(fixtures, url.pathname + url.search)) return route.fulfill({ json: fixtures[url.pathname + url.search] })
              if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html })
              if (url.pathname === '/app.js') return route.fulfill({ contentType: 'text/javascript', body: js })
              if (url.pathname === '/app.css') return route.fulfill({ contentType: 'text/css', body: css })
              if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204, body: '' })
              if (/^\/api\/shots\/(11|12)\/image$/.test(url.pathname)) return route.fulfill(imageFails
                ? { status: 404, body: '' } : { contentType: 'image/svg+xml', body: image })
            }
            if (key === 'POST /api/shots/11/judge') return route.fulfill({ json: { answered: 'front', agreed: null, stored: null } })
            unexpected.push(key)
            return route.abort('blockedbyclient')
          })()
          handlers.add(task)
          task.finally(() => handlers.delete(task)).catch(() => {})
          return task
        })
        const begin = async () => {
          const start = page.getByRole('button', { name: 'Start Judging Pass', exact: true })
          await expect(start).toBeEnabled()
          await tabTo(page, start)
          await page.keyboard.press('Enter')
          await expect(heading(page)).toHaveText('Photo 1 of 2')
          if (imageFails) await expect(page.getByRole('button', { name: 'Reload photograph', exact: true })).toBeVisible()
          else await expect(choices(page).first()).toBeEnabled()
        }
        const external = async (kind) => {
          await page.evaluate((type) => {
            const modal = document.createElement(type === 'native' ? 'dialog' : 'div')
            modal.id = 'judge-focus-external'
            if (type !== 'native') { modal.setAttribute('role', type); modal.setAttribute('aria-modal', 'true') }
            modal.innerHTML = '<button id="judge-focus-external-action">External action</button>'
            document.body.append(modal)
            if (type === 'native') modal.showModal()
            document.getElementById('judge-focus-external-action').focus()
          }, kind)
          return page.locator('#judge-focus-external-action')
        }
        try {
          await page.setViewportSize({ width, height: 1000 })
          await page.goto(`${origin}/#/judge?session_id=1`)
          await run({ page, posts, hold, begin, external, failImage: (value) => { imageFails = value } })
          const focus = await page.evaluate(() => ({ tag: document.activeElement.tagName,
            text: document.activeElement.textContent.trim().slice(0, 80), viewport: [innerWidth, innerHeight] }))
          console.log(JSON.stringify({ width, scenario: name, focus, posts }))
        } finally {
          for (const gate of gates.values()) gate.release({ status: 503, json: { detail: 'Synthetic cleanup' } })
          await Promise.allSettled([...handlers])
          await page.close()
        }
      })
    }

    for (const width of [375, 1440]) {
      for (const activation of ['Enter', 'Space']) await scenario(width, `pending ${activation}, duplicates and Escape`, async ({ page, posts, hold, begin }) => {
        await begin()
        const gate = hold('POST', '/api/shots/11/judge')
        await tabTo(page, choices(page).first())
        await page.keyboard.press(activation)
        await expect.poll(() => gate.count).toBe(1)
        await frame(page)
        await expect(heading(page)).toBeFocused()
        await expect(choices(page).first()).toBeDisabled()
        await page.keyboard.press('0'); await page.keyboard.press('C')
        assert.deepEqual(posts, [{ camera: 'front', control: false }])
        await page.keyboard.press('Escape')
        await expect(page.locator('.judge-pass')).toHaveCount(0)
        await expect(page.locator('main h2').first()).toBeFocused()
        gate.release()
        await expect(page.getByRole('button', { name: 'Start Judging Pass', exact: true })).toBeEnabled()
        await expect(page.locator('.judge-pass')).toHaveCount(0)
        assert.equal(posts.length, 1)
      })

      for (const status of [200, 503]) for (const modal of ['dialog', 'alertdialog', 'native']) await scenario(width, `${modal} retains focus on ${status}`, async ({ page, posts, hold, begin, external }) => {
        await begin()
        const gate = hold('POST', '/api/shots/11/judge')
        await page.keyboard.press('1')
        await expect.poll(() => gate.count).toBe(1)
        const action = await external(modal)
        await expect(action).toBeFocused()
        gate.release(status === 200 ? undefined : { status, json: { detail: 'Synthetic uncertain response' } })
        if (status === 200) await expect(heading(page)).toHaveText('Photo 2 of 2')
        else await expect(page.locator('.judge-pass [role="alert"]')).toHaveText('Synthetic uncertain response')
        await expect(action).toBeFocused()
        await page.keyboard.press('0')
        assert.equal(posts.length, 1)
      })

      for (const status of [200, 503]) await scenario(width, `voluntary navigation focus on ${status}`, async ({ page, posts, hold, begin }) => {
        await begin()
        const gate = hold('POST', '/api/shots/11/judge')
        await page.keyboard.press('1')
        await expect.poll(() => gate.count).toBe(1)
        const navigation = width === 375 ? page.getByRole('button', { name: 'Open navigation', exact: true }) : page.getByRole('link', { name: 'Judge', exact: true })
        await tabTo(page, navigation, true)
        gate.release(status === 200 ? undefined : { status, json: { detail: 'Synthetic uncertain response' } })
        if (status === 200) await expect(heading(page)).toHaveText('Photo 2 of 2')
        else await expect(page.locator('.judge-pass [role="alert"]')).toHaveText('Synthetic uncertain response')
        await expect(navigation).toBeFocused()
        assert.equal(posts.length, 1)
      })

      await scenario(width, 'image retry keeps focus while loading and after success', async ({ page, posts, hold, begin, failImage }) => {
        failImage(true); await begin()
        const gate = hold('GET', '/api/shots/11/image')
        failImage(false)
        await tabTo(page, page.getByRole('button', { name: 'Reload photograph', exact: true }))
        await page.keyboard.press('Enter')
        await expect.poll(() => gate.count).toBe(1)
        await expect(heading(page)).toBeFocused()
        await expect(choices(page).first()).toBeDisabled()
        await page.keyboard.press('1'); assert.equal(posts.length, 0)
        gate.release()
        await expect(choices(page).first()).toBeEnabled()
        await expect(heading(page)).toBeFocused()
        await page.keyboard.press('1')
        await expect(heading(page)).toHaveText('Photo 2 of 2')
        await expect(heading(page)).toBeFocused()
        assert.deepEqual(posts, [{ camera: 'front', control: false }])
      })

      await scenario(width, 'image completion cannot steal later modal focus', async ({ page, posts, hold, begin, failImage, external }) => {
        failImage(true); await begin()
        const gate = hold('GET', '/api/shots/11/image')
        failImage(false)
        await tabTo(page, page.getByRole('button', { name: 'Reload photograph', exact: true }))
        await page.keyboard.press('Enter')
        await expect.poll(() => gate.count).toBe(1)
        const action = await external('dialog')
        gate.release()
        await expect(choices(page).first()).toBeEnabled()
        await expect(action).toBeFocused()
        await page.keyboard.press('1'); assert.equal(posts.length, 0)
      })

      for (const status of [200, 503]) await scenario(width, `unmount discards late ${status}`, async ({ page, posts, hold, begin }) => {
        await begin()
        const gate = hold('POST', '/api/shots/11/judge')
        await page.keyboard.press('1')
        await expect.poll(() => gate.count).toBe(1)
        await page.evaluate(() => { location.hash = '#/audit-away' })
        await expect(page.locator('.judge-view')).toHaveCount(0)
        await expect(page.locator('main')).toBeFocused()
        const received = page.waitForResponse((response) => response.url() === `${origin}/api/shots/11/judge`
          && response.request().method() === 'POST')
        gate.release(status === 200 ? undefined : { status, json: { detail: 'Synthetic uncertain response' } })
        await (await received).finished()
        await frame(page)
        await expect(page.locator('.judge-view')).toHaveCount(0)
        await expect(page.locator('main')).toBeFocused()
        assert.equal(posts.length, 1)
      })
    }
    assert.deepEqual(unexpected, [], 'Unknown requests must never reach the network.')
    assert.deepEqual(runtimeErrors, [], 'No browser runtime errors are allowed.')
  } finally {
    try { if (context) await context.close() }
    finally { stop(); await cleanupOwnedRoot(owned) }
  }
})
