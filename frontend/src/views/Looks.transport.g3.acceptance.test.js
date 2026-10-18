import fs from 'node:fs'
import { it, expect } from 'vitest'
import { api, parseVersionJson } from '../api.js'
import {
  draftContentDigest,
  prospectiveProfile,
  prospectiveSource,
} from '../looksAutomationReview.js'

const inputPath = process.env.IDEVGEN_G3_INPUT_FILE
const outputPath = process.env.IDEVGEN_G3_OUTPUT_FILE
const mode = process.env.IDEVGEN_G3_MODE
const defaultInput = {
  appearance: 'Short dark hair e\u0301 ✨.',
  garments: [{ key: null, wording: 'a blue jacket', aside: 'the jacket folded over one arm' }],
}

it('builds a ready profile from the real review helpers', async () => {
  const input = inputPath && mode === 'profile'
    ? JSON.parse(fs.readFileSync(inputPath, 'utf8'))
    : defaultInput
  const rows = input.garments.map((garment, index) => ({
    id: `g3-row-${index + 1}`,
    key: garment.key || '',
    wording: garment.wording,
    aside: garment.aside || '',
  }))
  const source = prospectiveSource(input.appearance, rows)
  const digest = await draftContentDigest(source)
  const regions = (value) => ({ chest: value, hips: value, legs: value, feet: value })
  const draft = {
    appearance_segments: input.appearance ? [{
      segment_id: 'g3-exact-appearance',
      start: 0,
      end: Array.from(input.appearance).length,
      text: input.appearance,
      owner: 'permanent',
    }] : [],
    garment_coverage: Object.fromEntries(rows.map((row) => [row.id, {
      worn: { regions: regions('partial') },
      ...(row.aside ? { aside: { regions: regions('partial') } } : {}),
    }])),
    baseline_coverage: { regions: regions('none') },
    inventory_complete: { value: true },
  }
  const profile = prospectiveProfile(draft, input.appearance, rows, digest)
  expect(profile.draft_content_digest).toBe(digest)
  expect(profile.appearance_segments.map((item) => item.end))
    .toEqual(input.appearance ? [Array.from(input.appearance).length] : [])
  expect(profile.garment_coverage.map((item) => item.garment_index))
    .toEqual(rows.map((_, index) => index))
  if (outputPath && mode === 'profile') {
    fs.writeFileSync(outputPath, JSON.stringify({ source, profile, digest }))
  }
})

it('serializes a large expected version losslessly and parses exact API versions', async () => {
  if (mode === 'parse' && inputPath && outputPath) {
    const input = JSON.parse(fs.readFileSync(inputPath, 'utf8'))
    const apiResponse = fs.readFileSync(input.apiResponsePath, 'utf8')
    const parsed = parseVersionJson(apiResponse)
    expect(String(parsed.version)).toBe(input.responseVersion)
    fs.writeFileSync(outputPath, JSON.stringify({ parsedVersion: String(parsed.version) }))
    return
  }

  const input = mode === 'transport' && inputPath
    ? JSON.parse(fs.readFileSync(inputPath, 'utf8'))
    : {
      expectedVersion: '9223372036854775806',
      path: '/api/looks/look-g3-large/versions',
      body: { expected_version: '9223372036854775806', request_id: 'g3-transport-test' },
    }
  const originalFetch = globalThis.fetch
  let capturedBody = ''
  try {
    globalThis.fetch = async (_path, options) => {
      capturedBody = options.body
      throw new Error('Transport capture only.')
    }
    await expect(api.postVersioned(input.path, input.body, ['expected_version']))
      .rejects.toThrow('Transport capture only.')
  } finally {
    globalThis.fetch = originalFetch
  }
  expect(JSON.parse(capturedBody).expected_version).toBe(Number(input.expectedVersion))
  expect(capturedBody).toContain(`"expected_version":${input.expectedVersion}`)
  expect(capturedBody).not.toContain(`"expected_version":"${input.expectedVersion}"`)
  if (outputPath && mode === 'transport') {
    fs.writeFileSync(outputPath, JSON.stringify({ capturedBody }))
  }

  const parsed = parseVersionJson('{"version":9223372036854775807}')
  expect(String(parsed.version)).toBe('9223372036854775807')
})
