import { createInterface } from 'node:readline'

export const cleanStop = 'E2E_SERVER_STOPPED clean=true'

export function observeChild(child) {
  const lines = []
  const errors = []
  const events = []
  const stdout = createInterface({ input: child.stdout })
  const stderr = createInterface({ input: child.stderr })
  const record = (event, data = {}) => events.push({ sequence: events.length, time: Date.now(), event, ...data })
  stdout.on('line', (text) => { lines.push(text); record('stdout', { text }) })
  stderr.on('line', (text) => { errors.push(text); record('stderr', { text }) })
  child.on('error', (error) => record('error', { text: error.message }))
  child.stdin.on('error', (error) => record('stdin-error', { text: error.message }))
  child.once('exit', (code, signal) => record('exit', { code, signal }))
  const drained = Promise.all([
    new Promise((resolve) => stdout.once('close', resolve)),
    new Promise((resolve) => stderr.once('close', resolve)),
  ])
  const closed = new Promise((resolve) => child.once('close', async (code, signal) => {
    await drained
    record('close', { code, signal })
    resolve({ code, signal, streamsDrained: true })
  }))
  return { lines, errors, events, stdout, closed }
}

async function beforeDeadline(promise, milliseconds) {
  let timer
  try {
    return await Promise.race([promise, new Promise((resolve) => { timer = setTimeout(() => resolve(null), milliseconds) })])
  } finally {
    clearTimeout(timer)
  }
}

export async function stopOwnedServer(child, observed, timeoutMs = 15000, killTimeoutMs = 5000) {
  let forced = false
  if (child.exitCode === null && child.signalCode === null && !child.stdin.destroyed) {
    observed.events.push({ sequence: observed.events.length, time: Date.now(), event: 'stop-requested' })
    child.stdin.write('stop\n')
  }
  let result = await beforeDeadline(observed.closed, timeoutMs)
  if (!result) {
    // An exited child may still have undrained pipes; do not signal a reused PID.
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error('E2E server exited but its streams did not close within the deadline; owned root preserved.')
    }
    forced = true
    observed.events.push({ sequence: observed.events.length, time: Date.now(), event: 'forced-termination' })
    child.kill()
    result = await beforeDeadline(observed.closed, killTimeoutMs)
    if (!result) throw new Error(`E2E owned process ${child.pid} did not close after forced termination; owned root preserved.`)
  }
  const graceful = !forced && result.code === 0 && result.signal === null && observed.lines.includes(cleanStop)
  return { ...result, forced, termination: forced ? 'forced' : graceful ? 'graceful' : 'failed' }
}
