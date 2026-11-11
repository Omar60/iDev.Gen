import assert from 'node:assert/strict'
import { test } from 'node:test'
import { spawn } from 'node:child_process'
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { cleanupOwnedRoot, createOwnedRoot, ownedPath } from './owned-root.mjs'
import globalSetup, { pythonCommand } from './global-setup.mjs'
import { cleanStop, observeChild, stopOwnedServer } from './lifecycle.mjs'
import { assertHTMLIntegrity } from './preflight.mjs'

test('owned paths refuse a synthetic sibling sentinel and cleanup only marked roots', async () => {
  const owned = await createOwnedRoot()
  const outside = await createOwnedRoot()
  try {
    const sentinel = await ownedPath(outside, 'outside-sentinel.txt')
    await writeFile(sentinel, 'synthetic sentinel\n', { flag: 'wx' })
    await assert.rejects(ownedPath(owned, path.relative(owned.root, sentinel)), /escapes the owned temporary root/)
    await assert.rejects(ownedPath(owned, sentinel), /must be relative/)
    assert.equal(await readFile(sentinel, 'utf8'), 'synthetic sentinel\n')
  } finally {
    await cleanupOwnedRoot(outside)
    await cleanupOwnedRoot(owned)
  }
})

test('invalid project Python environments fail before child or temporary-root creation', async () => {
  const owned = await createOwnedRoot()
  try {
    const repository = await ownedPath(owned, 'synthetic-repository')
    await mkdir(repository)
    const beforeMissingVenv = await readdir(owned.root)
    await assert.rejects(pythonCommand(repository), /valid project \.venv/)
    assert.deepEqual(await readdir(owned.root), beforeMissingVenv)

    const venvRoot = path.join(repository, '.venv')
    const executableDirectory = process.platform === 'win32'
      ? path.join(venvRoot, 'Scripts')
      : path.join(venvRoot, 'bin')
    await mkdir(executableDirectory, { recursive: true })
    const executableName = process.platform === 'win32' ? 'python.exe' : 'python'
    await writeFile(path.join(executableDirectory, executableName), 'Synthetic placeholder; never executed.\n')
    await writeFile(path.join(venvRoot, 'pyvenv.cfg'), 'synthetic invalid configuration\n')
    const beforeInvalidConfig = await readdir(owned.root)
    await assert.rejects(pythonCommand(repository), /pyvenv\.cfg must declare executable and Python version/)
    assert.deepEqual(await readdir(owned.root), beforeInvalidConfig)
  } finally {
    await cleanupOwnedRoot(owned)
  }
})

test('resolved Playwright worker overrides are refused before server setup', async () => {
  let teardown
  let failure
  try {
    teardown = await globalSetup({ workers: 2 })
  } catch (error) {
    failure = error
  }
  if (typeof teardown === 'function') await teardown()
  assert.match(failure?.message || '', /requires exactly one resolved Playwright worker; found 2/)
})

test('HTML integrity accepts exact bytes and rejects injection without altering either buffer', () => {
  const expected = Buffer.from('<!doctype html><title>Synthetic app</title>')
  const intact = Buffer.from(expected)
  assert.doesNotThrow(() => assertHTMLIntegrity(expected, intact))
  const contaminated = Buffer.concat([expected, Buffer.from('<script src="https://synthetic-unowned.invalid/script.js"></script>')])
  const before = Buffer.from(contaminated)
  assert.throws(() => assertHTMLIntegrity(expected, contaminated), /HTML integrity preflight failed.*without modification/)
  assert.deepEqual(contaminated, before)
  assert.deepEqual(expected, intact)
  assert.throws(() => assertHTMLIntegrity(expected, expected, 'invalid-reference'), /build changed after/)
})

test('owned child shutdown waits for all stdout/stderr and reports graceful exit', async () => {
  const child = spawn(process.execPath, ['-e', `process.stdin.once('data', () => {
    process.stdout.write('synthetic output\\n'.repeat(4096) + ${JSON.stringify(cleanStop)} + '\\n');
    process.stderr.write('synthetic stderr tail\\n'); process.stdin.destroy();
  });`], { stdio: ['pipe', 'pipe', 'pipe'] })
  const observed = observeChild(child)
  const result = await stopOwnedServer(child, observed, 5000)
  assert.deepEqual(result, { code: 0, signal: null, streamsDrained: true, forced: false, termination: 'graceful' })
  assert.equal(observed.lines.length, 4097)
  assert.equal(observed.lines.at(-1), cleanStop)
  assert.equal(observed.errors.at(-1), 'synthetic stderr tail')
  assert.equal(observed.events.at(-1).event, 'close')
})

test('a shutdown timeout is forced and cannot claim clean success', async () => {
  const child = spawn(process.execPath, ['-e', `console.log(${JSON.stringify(cleanStop)}); process.stdin.resume()`], { stdio: ['pipe', 'pipe', 'pipe'] })
  const observed = observeChild(child)
  await new Promise((resolve) => observed.stdout.once('line', resolve))
  const result = await stopOwnedServer(child, observed, 50)
  assert.equal(result.forced, true)
  assert.equal(result.termination, 'forced')
  assert.equal(result.streamsDrained, true)
  assert.equal(observed.lines.includes(cleanStop), true)
  assert.equal(observed.events.some(({ event }) => event === 'forced-termination'), true)
})

test('controlled process failure cannot pass even when it prints a clean marker', async () => {
  const child = spawn(process.execPath, ['-e', `process.stdin.once('data', () => {
    console.log(${JSON.stringify(cleanStop)}); process.exitCode=7; process.stdin.destroy();
  });`], { stdio: ['pipe', 'pipe', 'pipe'] })
  const observed = observeChild(child)
  const result = await stopOwnedServer(child, observed, 5000)
  assert.equal(result.code, 7)
  assert.equal(result.forced, false)
  assert.equal(result.termination, 'failed')
  assert.equal(result.streamsDrained, true)
})
