/** Count what the writer did to the `camera` field, over one or more runs of
 *  `measure_writer.mjs`. Usage: node scripts/analyze_camera.mjs run_*.json
 *
 *  Five counts, and the last two are the ones that decide an arm:
 *
 *    five      verbatim copies of the five canned eye-level positions
 *    distinct  distinct WORDINGS of the camera clause
 *    families  distinct POSITIONS - which side of her the camera stands on,
 *              ignoring the wording. Two wordings of the same frontal position
 *              are one photograph, so this is what a shoot's variety really is,
 *              and it is where an arm that wins on `distinct` usually dies.
 *    order     lines that write the camera BEFORE the framing. Measured on a
 *              seventy-photograph run, the other way round asked for a non-frontal
 *              view 53 times and delivered about 10.
 *    renders   of the off-eye-level asks, those written in a form sessions 227
 *              and 228 measured the sampler to obey. Only *above* and *from the
 *              floor* survive; `at knee height`, `at hip height` and any height
 *              hung off a place come back at eye level, so an arm can ask for the
 *              floor twice as often and reach it less.
 */
import { readFileSync } from 'node:fs'

const HEADING = /^[A-Z][A-Za-z& ]{2,24}:[ \t]*$/gm
const FRAMING = /\ba (full-length|three-quarter|waist-up) photograph[^.,]*/i
const body = (l) => (l || '').replace(HEADING, '').replace(/\s*\n\s*/g, ' ').trim()
const clause = (l) => body(l).split(/\.\s/)[0].replace(FRAMING, '').toLowerCase()

const FIVE = [
  'taken from directly in front of her',
  'taken from behind her left shoulder',
  'taken from her right side, her body in full profile',
  'taken from her left side, her body in full profile',
  'taken from directly behind her',
]

// Ordered, first match wins: vertical before horizontal, because a camera on the
// floor is a floor shot whichever side of her it stands on.
const FAMILY = [
  // `mirror` first: a mirror selfie is a mirror photograph whatever else the
  // clause says about where the phone is pointing.
  ['mirror', /mirror selfie|in the mirror/],
  ['overhead', /overhead|from above|above her|high camera|looking (steeply )?down|propped on a high shelf|held above her/],
  ['floor', /floor level|from the floor|low-angle|low camera|looking up|below her/],
  ['behind', /directly behind her|from behind her(?!.{0,20}shoulder)|full back/],
  ['shoulder', /shoulder/],
  ['side', /her (right|left) side|side-angle|in full profile|\bprofile\b/],
  ['front', /in front of her|facing her|front of her/],
]

const OFF_EYE = /overhead|from above|above her|high camera|looking (steeply )?down|propped on a high shelf|held above her|floor level|from the floor|low-angle|low camera|looking up|below her|mattress level|bed height|knee (height|level)|hip (height|level)|waist height|carpet|foot of the bed/
const OBEYED = /overhead camera|high camera looking (steeply )?down|phone propped on a high shelf|phone held above her|low-angle shot from the floor|from the floor at her feet|directly above (her|the bed)|low-angle shot from the foot of the bed looking up|side-angle camera at mattress level|rear low camera behind him at bed height/

const stats = (file) => {
  // `shootLines` logs its check tally to stdout ahead of the array.
  const raw = readFileSync(file, 'utf8')
  const lines = JSON.parse(raw.slice(raw.indexOf('\n[\n')))
  const cs = lines.map(clause)
  const tally = {}
  for (const c of cs) {
    const f = (FAMILY.find(([, re]) => re.test(c)) || ['other'])[0]
    tally[f] = (tally[f] || 0) + 1
  }
  const off = cs.filter((c) => OFF_EYE.test(c))
  return {
    n: lines.length,
    five: cs.filter((c) => FIVE.some((f) => c.includes(f))).length,
    distinct: new Set(cs).size,
    families: Object.keys(tally).length,
    biggest: Math.max(...Object.values(tally)),
    order: lines.filter((l) => {
      const f = body(l).search(FRAMING)
      // `phone` and `mirror selfie` are in here because candid's catalogue
      // writes the position without ever saying `camera` - `Phone propped on a
      // high shelf`, `Mirror selfie`. Without them this column reported 13.6 of
      // 25 for a shoot whose every line opened with its planned position.
      const c = body(l).toLowerCase().search(/taken from|\bcamera\b|\bshot\b|\bangle\b|\bphone\b|mirror selfie/)
      return c >= 0 && (f < 0 || c < f)
    }).length,
    off: off.length,
    renders: off.filter((c) => OBEYED.test(c)).length,
  }
}

const KEYS = ['n', 'five', 'distinct', 'families', 'biggest', 'order', 'off', 'renders']
const rows = process.argv.slice(2).map((f) => [f, stats(f)])
console.log(['file'.padEnd(16), ...KEYS.map((k) => k.padStart(9))].join(''))
for (const [f, s] of rows) console.log([f.slice(-16).padEnd(16), ...KEYS.map((k) => String(s[k]).padStart(9))].join(''))
if (rows.length > 1) {
  const mean = (k) => (rows.reduce((a, [, s]) => a + s[k], 0) / rows.length).toFixed(1)
  console.log(['mean'.padEnd(16), ...KEYS.map((k) => mean(k).padStart(9))].join(''))
}
