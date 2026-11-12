// Run explicitly: node --test browser-checks/placeholders.node-check.mjs
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { build, stop } from 'esbuild'
import { chromium } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { cleanupOwnedRoot, createOwnedRoot, ownedPath } from '../e2e/owned-root.mjs'

const origin = 'http://placeholder.invalid'
const widths = [320, 375, 768, 1024, 1440]
const consumers = ['setup', 'sessions', 'library', 'looks', 'models', 'model/7']
const expectedFields = { setup: 8, sessions: 1, library: 1, looks: 2, models: 3, 'model/7': 3 }
const model = {
  id: 7, name: 'Invented model', lora_name: 'characters/ada.safetensors',
  trigger: 'invented', lora_strength: 1, workflow_id: null,
  base_positive: 'Invented portrait', base_negative: '', settings: {}, sessions: [],
}
const fixtures = {
  '/api/config': { comfy_url: '', comfy_output_dir: '', data_dir: '', lora_dir: '', llm_ok: false },
  '/api/comfy/status': { online: true, output_dir_ok: true },
  '/api/comfy/models': { checkpoints: [], samplers: [], schedulers: [] },
  '/api/comfy/loras': { loras: [] },
  '/api/models': [model], '/api/models/7': model, '/api/workflows': [],
  '/api/sessions': [], '/api/looks': [], '/api/components': [],
  '/api/wardrobe': { garments: [], outfits: [] }, '/api/rooms': { rooms: [], libraries: [] },
}

// Composite transparent backgrounds instead of comparing declared token values.
function sample(element) {
  const rgba = (value) => value.match(/[\d.]+/g).map(Number)
  const over = (front, back) => front.slice(0, 3)
    .map((value, index) => value * (front[3] ?? 1) + back[index] * (1 - (front[3] ?? 1)))
  const ancestors = []
  for (let node = element; node; node = node.parentElement) ancestors.unshift(node)
  let background = [255, 255, 255]
  for (const node of ancestors) {
    const style = getComputedStyle(node)
    if (Number(style.opacity) !== 1 || style.backgroundImage !== 'none') {
      throw new Error('Contrast sample requires opaque ancestors without background images.')
    }
    background = over(rgba(style.backgroundColor), background)
  }
  const pseudo = getComputedStyle(element, '::placeholder')
  const color = rgba(pseudo.color)
  color[3] = (color[3] ?? 1) * Number(pseudo.opacity)
  const foreground = over(color, background)
  const luminance = (channels) => {
    const linear = channels.map((value) => {
      value /= 255
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
    })
    return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722
  }
  const first = luminance(foreground), second = luminance(background)
  return {
    field: element.id || element.name, empty: element.value === '',
    shown: element.matches(':placeholder-shown'), color: pseudo.color,
    opacity: pseudo.opacity, background, textColor: getComputedStyle(element).color,
    ratio: (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05),
  }
}

async function checkConsumer(page, consumer, viewport) {
  await page.goto(`${origin}/?consumer=${encodeURIComponent(consumer)}#/${consumer}`)
  if (consumer === 'models') await page.getByRole('button', { name: '+ New model', exact: true }).click()
  if (consumer === 'model/7') await page.getByRole('button', { name: 'Edit model', exact: true }).click()
  if (consumer === 'looks') await page.getByRole('button', { name: 'Create look', exact: true }).click()
  const fields = page.locator('main input.ui-control[placeholder]:visible, main textarea.ui-control[placeholder]:visible')
  await fields.first().waitFor()
  const count = await fields.count()
  assert.equal(count, expectedFields[consumer], `${consumer}: all expected placeholder fields must render`)
  const samples = []
  for (let index = 0; index < count; index++) {
    const field = fields.nth(index)
    const textColor = await field.evaluate((element) => getComputedStyle(element).color)
    await field.fill('')
    await field.evaluate((element) => element.blur())
    await page.mouse.move(0, 0)
    for (const state of ['normal', 'hover', 'focus']) {
      if (state === 'hover') await field.hover()
      if (state === 'focus') {
        await field.focus()
        await page.keyboard.press('Tab')
        await page.keyboard.press('Shift+Tab')
        assert.equal(await field.evaluate((element) => document.activeElement === element), true)
        assert.equal(await field.evaluate((element) => {
          const style = getComputedStyle(element), rect = element.getBoundingClientRect()
          const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
          return style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0
            && rect.left >= 0 && rect.right <= innerWidth && element.contains(hit)
        }), true, `${consumer}: keyboard focus must remain visible and unobscured`)
      }
      const value = await field.evaluate(sample)
      assert.equal(value.empty && value.shown, true, `${consumer}: placeholder must be visible`)
      assert.equal(value.opacity, '1')
      assert.equal(value.textColor, textColor, 'Filled-field text color must remain unchanged')
      assert.ok(value.ratio >= 4.5, `${consumer}/${value.field}/${state}: ${value.ratio}:1 < 4.5:1`)
      samples.push({ state, ...value })
    }
  }
  const layout = await page.evaluate(() => {
    const shell = document.querySelector('.shell-content')
    return {
      viewport: [innerWidth, innerHeight],
      document: [document.documentElement.clientWidth, document.documentElement.scrollWidth],
      shell: [shell.clientWidth, shell.scrollWidth],
    }
  })
  assert.ok(layout.document[1] <= layout.document[0] + 1, `${consumer}: document overflow`)
  assert.ok(layout.shell[1] <= layout.shell[0] + 1, `${consumer}: workspace overflow`)
  await page.locator('.shell-skip').focus()
  await page.keyboard.press('Enter')
  assert.equal(await page.evaluate(() => document.activeElement.id), 'main-content')
  const states = [...new Set(samples.map(({ state, color, opacity, background, ratio }) =>
    JSON.stringify({ state, color, opacity, background, ratio })))].map((value) => JSON.parse(value))
  console.log(JSON.stringify({ consumer, requested: viewport, layout,
    fields: [...new Set(samples.map(({ field }) => field))], sampleCount: samples.length, states }))
}

test('shared placeholders meet textual contrast in Chromium, including native 200% zoom', { timeout: 120000 }, async () => {
  const owned = await createOwnedRoot()
  let context
  const unexpected = [], errors = []
  try {
    const bundle = await build({
      absWorkingDir: fileURLToPath(new URL('..', import.meta.url)),
      entryPoints: ['src/main.jsx'], bundle: true, write: false, outdir: 'memory',
      format: 'iife', define: { 'process.env.NODE_ENV': '"production"' }, logLevel: 'silent',
    })
    const js = Buffer.from(bundle.outputFiles.find((file) => file.path.endsWith('.js')).contents)
    const css = Buffer.from(bundle.outputFiles.find((file) => file.path.endsWith('.css')).contents)
    context = await chromium.launchPersistentContext(await ownedPath(owned, 'browser-profile'), {
      headless: false, viewport: null, serviceWorkers: 'block', args: ['--window-size=1440,1000'],
    })
    context.on('page', (page) => page.on('pageerror', (error) => errors.push(error.message)))
    await context.routeWebSocket('**/*', async (socket) => {
      unexpected.push('WebSocket'); await socket.close()
    })
    await context.route('**/*', async (route) => {
      const request = route.request(), url = new URL(request.url())
      if (url.origin !== origin || request.method() !== 'GET') {
        unexpected.push(`${request.method()} ${url.origin}${url.pathname}`)
        return route.abort('blockedbyclient')
      }
      if (Object.hasOwn(fixtures, url.pathname)) return route.fulfill({ json: fixtures[url.pathname] })
      if (url.pathname === '/api/loras/preview') return route.fulfill({ status: 404, body: '' })
      if (url.pathname === '/app.js') return route.fulfill({ contentType: 'text/javascript', body: js })
      if (url.pathname === '/app.css') return route.fulfill({ contentType: 'text/css', body: css })
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body:
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Synthetic contrast check</title><link rel="stylesheet" href="/app.css"><div id="root"></div><script src="/app.js"></script></html>',
      })
      unexpected.push(`Unknown fixture: ${url.pathname}`)
      return route.abort('blockedbyclient')
    })
    const page = await context.newPage()
    page.setDefaultTimeout(5000)
    for (const width of widths) {
      await page.setViewportSize({ width, height: 1000 })
      for (const consumer of consumers) await checkConsumer(page, consumer, width)
    }
    await page.close()
    const settings = await context.newPage()
    await settings.goto('chrome://settings/appearance')
    const zoomPage = await context.newPage()
    const dimensions = () => zoomPage.evaluate(() => ({
      outer: [outerWidth, outerHeight], css: [innerWidth, innerHeight],
      dpr: devicePixelRatio, cssZoom: getComputedStyle(document.body).zoom,
    }))
    await zoomPage.goto(origin)
    const before = await dimensions()
    await settings.evaluate(() => new Promise((resolve) => chrome.settingsPrivate.setDefaultZoom(2, resolve)))
    await zoomPage.reload()
    const after = await dimensions()
    const cdp = await context.newCDPSession(zoomPage)
    const metrics = await cdp.send('Page.getLayoutMetrics')
    assert.equal(metrics.cssVisualViewport.zoom, 2)
    assert.equal(metrics.cssVisualViewport.scale, 1)
    assert.equal(after.cssZoom, '1')
    assert.deepEqual(after.outer, before.outer)
    assert.equal(after.dpr, before.dpr * 2)
    console.log(JSON.stringify({ nativeZoom: { before, after, cdp: metrics.cssVisualViewport }, chromium: context.browser().version() }))
    for (const consumer of consumers) await checkConsumer(zoomPage, consumer, 'native-200%')
    assert.deepEqual(unexpected, [], 'Every browser request must use an explicit synthetic fixture')
    assert.deepEqual(errors, [], 'No browser runtime errors')
  } finally {
    try { if (context) await context.close() }
    finally { stop(); await cleanupOwnedRoot(owned) }
  }
})
