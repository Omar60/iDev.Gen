import { spawnSync } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { cleanupOwnedRoot, createOwnedRoot, ownedPath } from './owned-root.mjs'

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

if (Number(process.versions.node.split('.')[0]) !== 22) {
  throw new Error(`The E2E harness requires Node.js 22; found ${process.versions.node}.`)
}

const owned = await createOwnedRoot()
try {
  const envDirectory = await ownedPath(owned, 'vite-env')
  await mkdir(envDirectory)
  const configFile = await ownedPath(owned, 'vite.e2e.config.mjs')
  const originalConfig = pathToFileURL(path.join(frontend, 'vite.config.js')).href
  await writeFile(configFile,
    `import appConfig from ${JSON.stringify(originalConfig)};\n` +
    `export default { ...appConfig, envDir: ${JSON.stringify(envDirectory)} };\n`,
    { flag: 'wx' })
  const childEnvironment = {
    PATH: process.env.PATH || '',
    TEMP: owned.root,
    TMP: owned.root,
    TMPDIR: owned.root,
  }
  for (const key of ['SYSTEMROOT', 'WINDIR', 'COMSPEC']) {
    if (process.env[key]) childEnvironment[key] = process.env[key]
  }

  const viteCli = path.join(frontend, 'node_modules', 'vite', 'bin', 'vite.js')
  const result = spawnSync(process.execPath, [viteCli, 'build', '--mode', 'production', '--config', configFile, '--configLoader', 'runner'], {
    cwd: frontend,
    env: childEnvironment,
    stdio: 'inherit',
    windowsHide: true,
  })
  if (result.error) throw new Error(`The synthetic Vite production build could not start: ${result.error.message}`)
  if (result.status !== 0) {
    throw new Error(`The synthetic Vite production build failed with status ${result.status ?? result.signal}.`)
  }
} finally {
  await cleanupOwnedRoot(owned)
}
