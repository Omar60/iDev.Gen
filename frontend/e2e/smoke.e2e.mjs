import { expect } from '@playwright/test'
import { test, state } from './fixtures.mjs'
import { createServer } from 'node:http'
import { readFile, writeFile } from 'node:fs/promises'
import { cleanupOwnedRoot, createOwnedRoot, ownedPath } from './owned-root.mjs'

test('serves the production React build from its isolated FastAPI process', async ({ page, egressGuard }) => {
  await page.goto(`${state.baseUrl}/#/`)
  await expect(page).toHaveTitle(/iDev\.Gen/)
  await expect(page.getByRole('heading', { name: 'E2E Synthetic Character' })).toBeVisible()

  const identity = await page.evaluate(async () => {
    const response = await fetch('/__e2e/identity', { redirect: 'manual' })
    return { status: response.status, body: await response.json() }
  })
  expect(identity.status).toBe(200)
  expect(identity.body).toMatchObject({
    server_pid: state.pid,
    port: state.port,
    server_nonce: state.nonce,
    root_id: state.rootId,
    database_path_owned: true,
    seeded_models: 1,
    seeded_sessions: 1,
    file_paths_owned: {
      config: true,
      data: true,
      sessions: true,
      lora: true,
      output: true,
      staging: true,
    },
    backend_socket_denials: [],
    request_denials: [],
  })
  expect(identity.body.synthetic_httpx_requests.length).toBeGreaterThan(0)
  expect(identity.body.synthetic_httpx_requests.every((request) => (
    request.host === 'synthetic-assistant.invalid' || request.host === 'synthetic-comfy.invalid'
  ))).toBe(true)
  expect(await page.evaluate(() => navigator.serviceWorker.getRegistrations().then((items) => items.length))).toBe(0)

  const siblingHits = { tcpConnections: 0, httpRequests: 0, redirects: 0, websocketUpgrades: 0 }
  const siblingListener = createServer((request, response) => {
    siblingHits.httpRequests += 1
    if (request.url === '/e2e-redirect-probe') siblingHits.redirects += 1
    response.end('Synthetic sibling listener received an unexpected request.')
  })
  siblingListener.on('connection', (socket) => {
    siblingHits.tcpConnections += 1
    socket.on('error', () => {})
  })
  siblingListener.on('upgrade', (_request, socket) => {
    siblingHits.websocketUpgrades += 1
    socket.destroy()
  })
  await new Promise((resolve, reject) => {
    siblingListener.once('error', reject)
    siblingListener.listen({ host: '127.0.0.1', port: 0, exclusive: true }, resolve)
  })
  try {
    const siblingPort = siblingListener.address().port
    const httpProbe = `http://127.0.0.1:${siblingPort}/e2e-blocked-http`
    egressGuard.expectBlocked(httpProbe)
    await expect.poll(async () => page.evaluate(async (url) => {
      try {
        await fetch(url)
        return 'resolved'
      } catch {
        return 'blocked'
      }
    }, httpProbe)).toBe('blocked')

    const socketProbe = `ws://127.0.0.1:${siblingPort}/e2e-blocked-websocket`
    egressGuard.expectBlocked(socketProbe)
    await expect.poll(async () => page.evaluate((url) => new Promise((resolve) => {
      const socket = new WebSocket(url)
      const timeout = setTimeout(() => resolve('timeout'), 3000)
      socket.addEventListener('error', () => { clearTimeout(timeout); resolve('blocked') }, { once: true })
      socket.addEventListener('close', () => { clearTimeout(timeout); resolve('blocked') }, { once: true })
    }), socketProbe)).toBe('blocked')

    const redirectedUrl = `http://127.0.0.1:${siblingPort}/e2e-redirect-probe`
    egressGuard.expectBlocked(redirectedUrl)
    const redirectResult = await page.evaluate(async (port) => {
      try {
        await fetch(`/__e2e/redirect?port=${port}`)
        return 'followed'
      } catch {
        return 'blocked'
      }
    }, siblingPort)
    expect(redirectResult).toBe('blocked')
    expect(siblingHits).toEqual({ tcpConnections: 0, httpRequests: 0, redirects: 0, websocketUpgrades: 0 })
  } finally {
    await new Promise((resolve) => siblingListener.close(resolve))
  }

  const externalRoot = await createOwnedRoot()
  const sentinel = await ownedPath(externalRoot, 'sentinel.txt')
  const sentinelText = 'Synthetic outside-root sentinel; do not expose.'
  try {
    await writeFile(sentinel, `${sentinelText}\n`, { flag: 'wx' })
    const deniedConfigWrite = await page.evaluate(async (value) => {
      const response = await fetch('/api/config', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lora_dir: value }),
      })
      return { status: response.status, body: await response.text() }
    }, sentinel)
    expect(deniedConfigWrite.status).toBe(403)
    expect(deniedConfigWrite.body).not.toContain(sentinelText)

    const deniedFileRead = await page.evaluate(async (value) => {
      const response = await fetch(`/api/loras/preview?name=${encodeURIComponent(value)}`)
      return { status: response.status, body: await response.text() }
    }, sentinel)
    expect(deniedFileRead.status).toBe(403)
    expect(deniedFileRead.body).not.toContain(sentinelText)
    await expect(readFile(sentinel, 'utf8')).resolves.toBe(`${sentinelText}\n`)

    const finalIdentity = await page.evaluate(async () => (await fetch('/__e2e/identity')).json())
    expect(finalIdentity.backend_socket_denials).toEqual([])
    expect(finalIdentity.synthetic_httpx_requests.length).toBeGreaterThan(0)
    expect(finalIdentity.synthetic_httpx_requests.every((request) => (
      request.host === 'synthetic-assistant.invalid' || request.host === 'synthetic-comfy.invalid'
    ))).toBe(true)
    expect(finalIdentity.request_denials).toEqual([
      { method: 'PATCH', path: '/api/config' },
      { method: 'GET', path: '/api/loras/preview' },
    ])
  } finally {
    await cleanupOwnedRoot(externalRoot)
  }
})
