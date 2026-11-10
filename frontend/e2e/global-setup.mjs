import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { createInterface } from 'node:readline'
import { access, mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createOwnedRoot, ownedPath, cleanupOwnedRoot } from './owned-root.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontend = path.resolve(here, '..')
const repository = path.resolve(frontend, '..')
const serverScript = path.join(repository, 'scripts', 'e2e_harness', 'server.py')
const readyPattern = /^E2E_SERVER_READY base_url=(http:\/\/127\.0\.0\.1:\d+) pid=(\d+) parent_pid=(\d+) port=(\d+) nonce=([a-f0-9]+) root_id=([a-f0-9]+)$/

export async function pythonCommand(repositoryRoot = repository) {
  const candidate = process.platform === 'win32'
    ? path.join(repositoryRoot, '.venv', 'Scripts', 'python.exe')
    : path.join(repositoryRoot, '.venv', 'bin', 'python')
  try {
    await access(candidate)
    const venvRoot = path.dirname(path.dirname(candidate))
    const configuration = await readFile(path.join(venvRoot, 'pyvenv.cfg'), 'utf8')
    const executable = /^executable\s*=\s*(.+)$/mi.exec(configuration)?.[1]?.trim()
    const version = /^version\s*=\s*(\d+)\.(\d+)/mi.exec(configuration)
    if (!executable || !version) throw new Error('pyvenv.cfg must declare executable and Python version.')
    const pythonPath = process.platform === 'win32'
      ? path.join(venvRoot, 'Lib', 'site-packages')
      : path.join(venvRoot, 'lib', `python${version?.[1]}.${version?.[2]}`, 'site-packages')
    await access(executable)
    await access(pythonPath)
    return { executable, args: ['-S'], pythonPath }
  } catch (error) {
    throw new Error(`E2E requires a valid project .venv with pyvenv.cfg and site-packages before creating its temporary root: ${error.message}`)
  }
}

function childEnvironment(owned, configPath, dataPath, nonce, pythonPath) {
  const env = {
    PATH: process.env.PATH || '',
    PYTHONUNBUFFERED: '1',
    PYTHONDONTWRITEBYTECODE: '1',
    PYTHONNOUSERSITE: '1',
    IDEVGEN_CONFIG: configPath,
    IDEVGEN_DATA_DIR: dataPath,
    IDEVGEN_E2E_ROOT: owned.root,
    IDEVGEN_E2E_PARENT: owned.parent,
    IDEVGEN_E2E_ROOT_TOKEN: owned.token,
    IDEVGEN_E2E_NONCE: nonce,
    TEMP: owned.root,
    TMP: owned.root,
    TMPDIR: owned.root,
  }
  if (pythonPath) env.PYTHONPATH = pythonPath
  for (const key of ['SYSTEMROOT', 'WINDIR']) {
    if (process.env[key]) env[key] = process.env[key]
  }
  return env
}

function waitForReady(child, nonce, rootToken, timeoutMs) {
  return new Promise((resolve, reject) => {
    const lines = []
    const errors = []
    const stdout = createInterface({ input: child.stdout })
    const stderr = createInterface({ input: child.stderr })
    const timer = setTimeout(() => finish(new Error(`E2E server startup timed out.\n${errors.join('\n')}`)), timeoutMs)
    let settled = false
    const finish = (error, identity) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      if (error) reject(error)
      else resolve({ identity, lines, errors })
    }
    stdout.on('line', (line) => {
      lines.push(line)
      const match = readyPattern.exec(line)
      if (!match) return
      const identity = {
        baseUrl: match[1],
        pid: Number(match[2]),
        parentPid: Number(match[3]),
        port: Number(match[4]),
        nonce: match[5],
        rootId: match[6],
      }
      if (identity.pid !== child.pid || identity.parentPid !== process.pid || identity.nonce !== nonce || identity.rootId !== rootToken) {
        finish(new Error(`E2E server readiness identity mismatch: server pid=${identity.pid}, expected child pid=${child.pid}; parent pid=${identity.parentPid}, expected launcher pid=${process.pid}; nonce/root match=${identity.nonce === nonce && identity.rootId === rootToken}.`))
      } else if (Number(new URL(identity.baseUrl).port) !== identity.port) {
        finish(new Error('E2E server readiness port did not match its bound URL.'))
      } else {
        finish(null, identity)
      }
    })
    stderr.on('line', (line) => errors.push(line))
    child.once('exit', () => { stdout.close(); stderr.close() })
    child.once('error', (error) => finish(new Error(`E2E server process could not start: ${error.message}`)))
    child.once('exit', (code, signal) => {
      if (!settled) finish(new Error(`E2E server exited before readiness (code=${code}, signal=${signal}).\n${[...lines, ...errors].join('\n')}`))
    })
  })
}

async function stopOwnedServer(child, timeoutMs = 15000) {
  if (child.exitCode !== null || child.signalCode !== null) return
  const exited = new Promise((resolve) => child.once('exit', resolve))
  if (!child.stdin.destroyed) child.stdin.write('stop\n')
  let timer
  const timeout = new Promise((resolve) => { timer = setTimeout(() => resolve(null), timeoutMs) })
  if (await Promise.race([exited.then(() => true), timeout]) === true) {
    clearTimeout(timer)
    return
  }
  clearTimeout(timer)
  child.kill()
  let killTimer
  const killed = await Promise.race([
    new Promise((resolve) => child.once('exit', () => resolve(true))),
    new Promise((resolve) => { killTimer = setTimeout(() => resolve(false), 5000) }),
  ])
  clearTimeout(killTimer)
  if (!killed) throw new Error(`E2E server process ${child.pid} did not exit after its owned-process termination request.`)
}

async function removeOwnedTemp(owned) {
  await cleanupOwnedRoot(owned)
}

export default async function globalSetup(playwrightConfig) {
  if (Number(process.versions.node.split('.')[0]) !== 22) {
    throw new Error(`The E2E harness requires Node.js 22; found ${process.versions.node}.`)
  }
  if (playwrightConfig?.workers !== 1) {
    throw new Error(`The E2E harness requires exactly one resolved Playwright worker; found ${playwrightConfig?.workers}.`)
  }
  const python = await pythonCommand()
  const owned = await createOwnedRoot()
  let child
  try {
    const nonce = randomBytes(24).toString('hex')
    const paths = {
      config: await ownedPath(owned, 'config.json'),
      data: await ownedPath(owned, 'data'),
      sessions: await ownedPath(owned, 'data/sessions'),
      staging: await ownedPath(owned, 'data/staging'),
      lora: await ownedPath(owned, 'lora'),
      output: await ownedPath(owned, 'output'),
      state: await ownedPath(owned, 'runner-state.json'),
    }
    for (const directory of [paths.data, paths.sessions, paths.staging, paths.lora, paths.output]) {
      await mkdir(directory, { recursive: true })
    }
    for (const directory of [paths.sessions, paths.staging, paths.lora, paths.output]) {
      await writeFile(path.join(directory, '.e2e-synthetic-seed'), 'Synthetic E2E data only.\n', { flag: 'wx' })
    }
    await writeFile(paths.config, `${JSON.stringify({
      comfy_url: 'http://synthetic-comfy.invalid',
      comfy_output_dir: paths.output,
      lora_dir: paths.lora,
      data_dir: paths.data,
      resource_planning_enabled: true,
      llm_url: 'http://synthetic-assistant.invalid/v1',
      llm_model: 'e2e-synthetic-assistant',
      llm_vision_model: '',
      llm_key: '',
      checkpoints: {},
      room_libraries: [],
    }, null, 2)}\n`, { flag: 'wx' })
    await writeFile(paths.state, `${JSON.stringify({
      root: owned.root,
      rootId: owned.token,
      rootParent: owned.parent,
      nonce,
      paths,
    }, null, 2)}\n`, { flag: 'wx' })

    child = spawn(python.executable, [...python.args, serverScript, '--root', owned.root, '--root-token', owned.token,
      '--parent', owned.parent, '--nonce', nonce, '--workers', '1'], {
      cwd: repository,
      env: childEnvironment(owned, paths.config, paths.data, nonce, python.pythonPath),
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    const { identity, lines, errors } = await waitForReady(child, nonce, owned.token, 60000)
    if (identity.port < 1 || identity.port > 65535) throw new Error('E2E server reported an invalid loopback port.')
    console.log(lines.find((line) => line.startsWith('E2E_SERVER_READY ')))

    const runtime = {
      ...identity,
      root: owned.root,
      rootParent: owned.parent,
      rootId: owned.token,
      nonce,
      startupLog: lines,
      startupErrors: errors,
      stateFile: paths.state,
    }
    await writeFile(paths.state, `${JSON.stringify(runtime, null, 2)}\n`)

    const response = await fetch(`${identity.baseUrl}/__e2e/identity`, { redirect: 'manual', signal: AbortSignal.timeout(5000) })
    if (response.status !== 200) throw new Error(`E2E server identity health check returned ${response.status}; redirects are disabled.`)
    const health = await response.json()
    if (health.server_pid !== identity.pid || health.port !== identity.port || health.server_nonce !== nonce || health.root_id !== owned.token) {
      throw new Error('E2E server health identity did not match readiness identity.')
    }
    process.env.IDEVGEN_E2E_STATE = paths.state
    process.env.IDEVGEN_E2E_BASE_URL = identity.baseUrl

    return async () => {
      await stopOwnedServer(child)
      const exitCode = child.exitCode
      const stopLine = lines.find((line) => line === 'E2E_SERVER_STOPPED clean=true')
      await removeOwnedTemp(owned)
      if (!stopLine) {
        throw new Error(`E2E server exited without clean shutdown evidence.\n${[...lines, ...errors].join('\n')}`)
      }
      console.log(stopLine)
      if (exitCode !== 0) {
        throw new Error(`E2E server exited with code ${exitCode}.\n${[...lines, ...errors].join('\n')}`)
      }
    }
  } catch (error) {
    if (child) {
      try {
        await stopOwnedServer(child, 5000)
      } catch (stopError) {
        throw new Error(`${error.message}\n${stopError.message}; owned temp root preserved at ${owned.root}`)
      }
    }
    await removeOwnedTemp(owned)
    throw error
  }
}
