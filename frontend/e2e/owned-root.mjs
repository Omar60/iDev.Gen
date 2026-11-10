import { randomBytes } from 'node:crypto'
import { lstat, mkdtemp, readFile, readdir, realpath, rm, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { tmpdir } from 'node:os'

const markerName = '.idevgen-e2e-owner'
const reparsePoint = 0x400

function samePath(left, right) {
  return process.platform === 'win32'
    ? left.toLowerCase() === right.toLowerCase()
    : left === right
}

function isReparsePoint(stat) {
  return stat.isSymbolicLink() || (process.platform === 'win32' && Boolean(stat.attributes & reparsePoint))
}

function assertDirectChild(root, parent) {
  if (!samePath(path.dirname(root), parent) || samePath(root, parent)) {
    throw new Error('E2E root is not a direct child of its owned temporary parent.')
  }
}

async function assertNoLinks(root) {
  const visit = async (directory) => {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const target = path.join(directory, entry.name)
      const stat = await lstat(target)
      if (isReparsePoint(stat)) throw new Error('E2E root contains a symbolic link or reparse point.')
      if (stat.isDirectory()) await visit(target)
      else if (!stat.isFile()) throw new Error('E2E root contains a non-file entry.')
    }
  }
  await visit(root)
}

export async function createOwnedRoot() {
  const parent = await realpath(tmpdir())
  const root = await mkdtemp(path.join(parent, 'idevgen-e2e-'))
  const token = randomBytes(32).toString('hex')
  try {
    if (isReparsePoint(await lstat(root))) throw new Error('Created E2E root is a symbolic link or reparse point.')
    const resolved = await realpath(root)
    assertDirectChild(resolved, parent)
    await writeFile(path.join(resolved, markerName), `${token}\n`, { flag: 'wx' })
    return { root: resolved, parent, token }
  } catch (error) {
    try {
      const resolved = await realpath(root)
      assertDirectChild(resolved, parent)
      if (!isReparsePoint(await lstat(root))) {
        await assertNoLinks(resolved)
        await rm(resolved, { recursive: true, force: false })
      }
    } catch {
      throw new Error(`${error.message} E2E temp root was preserved because safe cleanup could not be proven: ${root}`)
    }
    throw error
  }
}

export async function ownedPath(owned, relative) {
  if (typeof relative !== 'string' || !relative || path.isAbsolute(relative) || path.win32.isAbsolute(relative)) {
    throw new Error('E2E paths must be relative to the owned temporary root.')
  }
  if (relative.split(/[\\/]/).includes('..')) throw new Error('E2E path escapes the owned temporary root.')
  const resolved = path.resolve(owned.root, relative)
  const inside = path.relative(owned.root, resolved)
  if (!inside || inside === '..' || inside.startsWith(`..${path.sep}`) || path.isAbsolute(inside)) {
    throw new Error('E2E path escapes the owned temporary root.')
  }
  let current = owned.root
  for (const part of inside.split(path.sep)) {
    current = path.join(current, part)
    try {
      if (isReparsePoint(await lstat(current))) throw new Error('E2E path crosses a symbolic link or reparse point.')
    } catch (error) {
      if (error.code !== 'ENOENT') throw error
      break
    }
  }
  return resolved
}

export async function cleanupOwnedRoot(owned) {
  const parent = await realpath(owned.parent)
  const rootStat = await lstat(owned.root)
  const root = await realpath(owned.root)
  assertDirectChild(root, parent)
  if (isReparsePoint(rootStat)) throw new Error('Refusing to clean a linked E2E root.')
  const marker = path.join(root, markerName)
  const markerStat = await lstat(marker)
  if (isReparsePoint(markerStat) || !markerStat.isFile() || await readFile(marker, 'utf8') !== `${owned.token}\n`) {
    throw new Error('Refusing to clean an E2E root with a mismatched owner marker.')
  }
  await assertNoLinks(root)
  await rm(root, { recursive: true, force: false })
}
