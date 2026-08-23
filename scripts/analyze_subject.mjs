/** Does the `her` field argue with the camera? One count, over runs of
 *  `measure_writer.mjs`. Usage: node scripts/analyze_subject.mjs run_*.json
 *
 *  Why it exists: measured 2026-08-23, sessions 247-250. One line fixed by hand,
 *  only the `her` field swapped, three seeds. Written as `The black knit sweater
 *  covers her chest and torso with the black satin bra visible at the bust`, a
 *  camera asked for behind her left shoulder rendered FRONTAL 0/3. The same line
 *  with the same garments named without a side - `She is in the black knit
 *  sweater with one shoulder bare` - rendered the shoulder 3/3. Nothing else in
 *  the prompt moved.
 *
 *  So a `her` field written from the front is not a wording preference, it is a
 *  contradiction with any camera that is not in front of her, and this sampler
 *  resolves a contradiction by keeping the body and throwing away the camera.
 *  That was measured in a hand-fixed line; this counts how often the real writer
 *  builds one.
 *
 *  Three columns, and the third is the whole point:
 *
 *    offFront  lines whose camera is NOT in front of her - behind, shoulder,
 *              overhead, floor. `side` is not counted: a profile sees chest and
 *              back alike and nothing about it contradicts either.
 *    frontBody of those, the ones whose `her` field names front-only anatomy
 *              (chest, breasts, bust, nipples, navel, stomach) and NO back
 *              anatomy at all. A field naming both is the writer doing the right
 *              thing - `the bra across her chest visible from this angle, her
 *              shoulder blades exposed` is a shoulder camera being answered.
 *    rate      frontBody / offFront. This is the share of non-frontal
 *              photographs the writer is quietly cancelling.
 */
import { readFileSync } from 'node:fs'

const HEADING = /^[A-Z][A-Za-z& ]{2,24}:[ \t]*$/gm
const body = (l) => (l || '').replace(HEADING, '').replace(/\s*\n\s*/g, ' ').trim()

// The field, by its heading, as the writer prints it. `Subject` is what the
// `her` field is titled in a written line; the fallback keeps this working if a
// run was made before the headings were fixed.
const field = (line, name) => {
  const at = line.split(/\n\n/).find((p) => new RegExp(`^${name}:`, 'i').test(p.trim()))
  return at ? at.replace(new RegExp(`^\\s*${name}:\\s*`, 'i'), '') : ''
}

// Same table as analyze_camera.mjs, ordered, first match wins.
const FAMILY = [
  ['overhead', /overhead|from above|above her|high camera|looking (steeply )?down/],
  ['floor', /floor level|from the floor|low-angle|low camera|looking up|below her/],
  ['behind', /directly behind her|from behind her(?!.{0,20}shoulder)|full back/],
  ['shoulder', /shoulder/],
  ['side', /her (right|left) side|side-angle|in full profile|\bprofile\b/],
  ['front', /in front of her|facing her|front of her/],
]

const FRONT_BODY = /\bchest\b|\bbreasts?\b|\bbust\b|\bnipples?\b|\bnavel\b|\bstomach\b|\bbelly\b|\bcleavage\b/i
// Deliberately NOT a bare `back`: this wardrobe has a sweater with an open back
// and `the open back of it` is a garment, not a camera being answered. What
// counts is her body from behind, or the field naming the view it is written for.
const BACK_BODY = /her back\b|shoulder blades?|\bspine\b|\bbuttocks\b|backside|from behind|behind her|this angle|the camera|over her shoulder|turned away/i

const OFF_FRONT = ['overhead', 'floor', 'behind', 'shoulder']

// Which family the front-only fields land in, pooled over every file. It is the
// number that decides whether the rate above matters: `behind` and `shoulder`
// are the two the renders showed a front-written body cancels outright, while a
// camera above her can see her chest and is not contradicted by a field that
// names it.
const byFamily = {}

const stats = (file) => {
  const raw = readFileSync(file, 'utf8')
  const lines = JSON.parse(raw.slice(raw.indexOf('\n[\n')))
  let offFront = 0
  let frontBody = 0
  for (const line of lines) {
    const camera = body(field(line, 'Angle & Framing')).toLowerCase()
    const family = (FAMILY.find(([, re]) => re.test(camera)) || ['other'])[0]
    if (!OFF_FRONT.includes(family)) continue
    offFront += 1
    const her = field(line, 'Subject')
    const front = FRONT_BODY.test(her) && !BACK_BODY.test(her)
    if (front) frontBody += 1
    byFamily[family] = byFamily[family] || [0, 0]
    byFamily[family][0] += front ? 1 : 0
    byFamily[family][1] += 1
  }
  return { file, n: lines.length, offFront, frontBody }
}

const files = process.argv.slice(2)
const rows = files.map(stats)
const pad = (s, w) => String(s).padStart(w)
console.log('file'.padEnd(22), pad('n', 5), pad('offFront', 9), pad('frontBody', 10), pad('rate', 6))
for (const r of rows) {
  console.log(r.file.slice(-22).padEnd(22), pad(r.n, 5), pad(r.offFront, 9), pad(r.frontBody, 10),
              pad(r.offFront ? (r.frontBody / r.offFront).toFixed(2) : '-', 6))
}
const sum = (k) => rows.reduce((a, r) => a + r[k], 0)
console.log('total'.padEnd(22), pad(sum('n'), 5), pad(sum('offFront'), 9), pad(sum('frontBody'), 10),
            pad(sum('offFront') ? (sum('frontBody') / sum('offFront')).toFixed(2) : '-', 6))

console.log('\nfront-only `her` by camera family, pooled:')
for (const family of OFF_FRONT) {
  const [front, all] = byFamily[family] || [0, 0]
  if (all) console.log(`  ${family.padEnd(9)} ${pad(front, 3)}/${pad(all, 3)}  ${(front / all).toFixed(2)}`)
}
