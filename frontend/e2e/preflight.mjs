import { createHash } from 'node:crypto'

export function assertHTMLIntegrity(expected, received, referenceSHA256) {
  const original = Buffer.from(expected)
  const actual = Buffer.from(received)
  const digest = (value) => createHash('sha256').update(value).digest('hex')
  if (referenceSHA256 && digest(original) !== referenceSHA256) {
    throw new Error('E2E build changed after the HTML preflight; functional E2E is blocked.')
  }
  if (!original.equals(actual)) {
    throw new Error(`E2E HTML integrity preflight failed: build=${original.length} bytes sha256=${digest(original)}; received=${actual.length} bytes sha256=${digest(actual)}. The response was rejected without modification; functional E2E is blocked.`)
  }
}
