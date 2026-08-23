/** The shoot writer, run outside the browser, so an instruction change can be
 *  measured without shooting anything.
 *
 *  It calls the real `shootLines` — the chunks, the stage plan, the checks and
 *  the repair — against a running backend, and prints the lines as JSON. What is
 *  measured is the TEXT: which camera positions the writer picks, whether a field
 *  arrives, how long a line runs. Whether the photograph changes is a different
 *  question and this cannot answer it; for that, fix a line by hand and shoot it
 *  with one field swapped, which is what sessions 227 and 228 did.
 *
 *  THE PROTOCOL, and it is the expensive half. One run of n=25 has a run-to-run
 *  spread of 5-6 photographs on any per-line count: the same code measured 1, 6
 *  and 5 on three consecutive runs. So a single run cannot see anything smaller
 *  than a field being switched on or off. Five runs a side is the minimum, ten
 *  before adopting - measured 2026-08-22, an arm that scored +3.8 (2.7 sigma) on
 *  its first five runs fell to +1.9 (1.6 sigma) on the second five and was
 *  dropped.
 *
 *  Arms are compared by building one bundle per arm and NOT touching the source
 *  while they run:
 *
 *    esbuild scripts/measure_writer.mjs --bundle --platform=node --format=esm \
 *      --outfile=/tmp/arm_a.mjs        # patch kinds.js, rebuild, restore
 *    node /tmp/arm_a.mjs 25 directed > /tmp/a_1.json
 *
 *  Usage: node <bundle> [n] [manner] [reach] [backend]
 */
import { shootLines } from '../frontend/src/enhance.js'

const [, , nArg, mannerArg, reachArg, baseArg] = process.argv
const n = Number(nArg || 25)
const manner = mannerArg || 'directed'
// The reach is an argument because the manner is not the only axis a shoot has:
// `selfie` only becomes the shoot it was taken from when the reach reaches the
// act. Default `nude`, so every measurement taken before this line still means
// what it meant.
const reach = reachArg || 'nude'
const BASE = baseArg || 'http://127.0.0.1:8777'

// `api` posts to a relative path, which has no meaning outside a browser.
const real = globalThis.fetch
globalThis.fetch = (p, o) => real(typeof p === 'string' && p.startsWith('/') ? BASE + p : p, o)

// One fixed session, so two arms differ by the instruction and nothing else. The
// look names no hair colour on purpose: naming one overrides the LoRA.
const LOOK =
  'She wears her hair loose and straightened down past her shoulders, with a light coating '
  + 'of natural-looking makeup and a soft pink tint on her lips. She is in a small lived-in '
  + 'room with a worn beige sofa, the carpeted floor running toward a half-curtained window '
  + 'that lets in weak grey daylight, a bed against the far wall and a bedside lamp.'

const WARDROBE =
  'She wears a black knit sweater with long sleeves and a large open back, over a black satin '
  + 'bra visible at the bust. She wears fitted high-waisted charcoal denim pants with a slim '
  + 'leg, and plain white cotton socks on her feet.'

const BRIEF =
  'A weekday afternoon at home with nothing to do. She drifts around the room, the sweater '
  + 'comes off partway through, and by the end she is down to the bra and the denim. She '
  + 'begins bored and idle and turns deliberate.'

// The explicit shoot cannot use the brief above: `reach: explicit` means she is
// undressed and with him in photograph one, and REACH.explicit's own note says a
// brief that leaves that to be inferred is read as a shoot that starts dressed.
// Same room, same wardrobe, so an arm against `nude` differs by the reach and the
// first clause and nothing else.
const EXPLICIT_BRIEF =
  'A weekday afternoon at home. It begins already naked with him on the bed, and the two of '
  + 'them stay there for the whole of it, moving through what they are doing rather than '
  + 'through any clothes. She begins direct and stays direct.'

const lines = await shootLines(reach === 'explicit' ? EXPLICIT_BRIEF : BRIEF,
                               LOOK, WARDROBE, n, null, reach, manner)
// `shootLines` logs its check tally to stdout first, so a reader has to skip to
// the first line that opens an array.
console.log(JSON.stringify(lines.map((l) => l.prompt), null, 1))
