import { expect } from '@playwright/test'
import { createServer } from 'node:http'
import { state } from './fixtures.mjs'
import { snapshot, test } from './session-fixture.mjs'

const sessionPath = (id) => `/api/sessions/${id}`

test('freezes validated response routing before the caller can mutate its options', async ({ page, request, sessionPair, egressGuard }) => {
  const [session, otherSession] = sessionPair
  const siblingHits = { tcpConnections: 0, httpRequests: 0 }
  const siblingListener = createServer((_request, response) => {
    siblingHits.httpRequests += 1
    response.end('Synthetic sibling listener received an unexpected request.')
  })
  siblingListener.on('connection', (socket) => {
    siblingHits.tcpConnections += 1
    socket.on('error', () => {})
  })
  await new Promise((resolve, reject) => {
    siblingListener.once('error', reject)
    siblingListener.listen({ host: '127.0.0.1', port: 0, exclusive: true }, resolve)
  })

  try {
    const siblingUrl = `http://127.0.0.1:${siblingListener.address().port}/e2e-barrier-mutation`
    const path = sessionPath(session.id)
    const barrier = egressGuard.hold({ method: 'GET', path, phase: 'before-send' })
    await page.goto(`${state.baseUrl}/#/session/${session.id}?step=review`)
    const received = await barrier.received
    expect(received.request.url()).toBe(`${state.baseUrl}${path}`)

    const delivered = page.waitForResponse((response) => (
      response.url() === `${state.baseUrl}${path}` && response.request().method() === 'GET'
    ))
    const releaseOptions = { responsePath: sessionPath(otherSession.id) }
    barrier.release(releaseOptions)
    releaseOptions.responsePath = siblingUrl

    const response = await delivered
    expect(response.status()).toBe(200)
    expect(await response.json()).toEqual(otherSession.session)
    await expect(page.getByRole('alert')).toContainText('The session response did not match the requested session.')
    await expect(page.getByRole('button', { name: 'Retry loading session', exact: true })).toBeVisible()
    expect(egressGuard.faults).toMatchObject([{
      method: 'GET', path, action: 'response',
      responsePath: sessionPath(otherSession.id), status: 200,
    }])

    const audited = await snapshot(request)
    const otherSessionReads = audited.requests.filter((entry) => (
      entry.method === 'GET' && entry.path === sessionPath(otherSession.id)
    ))
    expect(otherSessionReads).toHaveLength(1)
    expect(otherSessionReads[0].status).toBe(200)
    expect(siblingHits).toEqual({ tcpConnections: 0, httpRequests: 0 })
  } finally {
    await new Promise((resolve, reject) => siblingListener.close((error) => (
      error ? reject(error) : resolve()
    )))
  }
  expect(siblingListener.listening).toBe(false)
})
