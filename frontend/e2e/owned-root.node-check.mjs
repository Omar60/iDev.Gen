import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { cleanupOwnedRoot, createOwnedRoot, ownedPath } from './owned-root.mjs'
import globalSetup, { pythonCommand } from './global-setup.mjs'

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
