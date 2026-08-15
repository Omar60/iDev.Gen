/** The prompt assistant, from the browser's side.
 *
 *  It suggests text for a box on screen. Nothing here queues anything, and
 *  nothing here composes: a take line stays a take line, and the trigger, the
 *  base prompt and the look are still prepended by the server at insert time.
 *  What the model is told about them is "this is already in the prompt, do not
 *  repeat it" — which is the rule the docs give a human writing the same take.
 */
import { api } from './api'
import {
  KINDS, LOOK_LINES, WARDROBE_LINES, LOOK_INSTRUCTION, LOOK_ONLY_INSTRUCTION,
  LOOK_FROM_PHOTO_INSTRUCTION, WARDROBE_INSTRUCTION, WARDROBE_PROGRESSION_INSTRUCTION,
  ANGLE_FROM_TEXT_INSTRUCTION, BRIEF_INSTRUCTION, BRIEF_AXES, REACH,
  SHOOT_LINE_INSTRUCTION, STAGE_PLAN_INSTRUCTION, REPAIR_INSTRUCTION,
  takesChunkNote, wardrobeChunkNote, shootChunkNote,
} from './kinds.js'

export const ask = (payload) => api.post('/api/enhance', payload).then((r) => r.lines || [])

/** What is already in the prompt of a take painted from noise. A reference take
 *  carries none of it, so it is never given any of this. */
export const composed = (model, look) =>
  [model?.trigger, model?.base_positive, look].map((x) => (x || '').trim()).filter(Boolean).join(', ')

/** What the take box must not say again: the session's look plus the wardrobe
 *  this particular take will carry, which is the session's unless the row set its
 *  own. Per row, because two rows of the same session are now allowed to be
 *  wearing different things. */
export const alreadySaid = (...parts) =>
  parts.map((x) => (x || '').trim()).filter(Boolean).join(', ')

/** A row that opted out of its kind's default is the other kind of take, and the
 *  two want opposite prompts — the same rule the placeholders follow. */
export const takeKind = (kind, reference) => {
  if (!reference) return 'shoot'
  return KINDS[kind]?.refKind ? kind : 'edit'
}

/** Null where writing prose is the wrong help — a camera-angle take is a closed
 *  vocabulary, and the picker builds those. */
export const guideFor = (kind, reference) => KINDS[takeKind(kind, reference)]?.enhance || null

export const rewriteTake = (kind, reference, text, context) =>
  ask({ instruction: guideFor(kind, reference).line, text,
        context: reference ? '' : context, n: 1 }).then(first)

export const takesFromBrief = (kind, reference, brief, context, n) => {
  const guide = guideFor(kind, reference)
  return ask({ instruction: `${guide.line}\n\n${guide.batch}`,
               text: brief, context: reference ? '' : context, n })
}

// How many lines to ask for at once. Eight is what a shoot of eight came back
// perfect at; forty in one call came back as thirty-two stubs with the arc spent
// by line nineteen. The limit is not the context window — it is that a long list
// is answered shorter, and the shoot loses its middle.
export const CHUNK = 8

/** `n` lines out of an assistant that reliably writes about eight.
 *
 *  Each call is told where in the shoot it is and what the line before it said,
 *  so the arc keeps its pace and the wardrobe keeps carrying over. `onProgress`
 *  gets the running total, because forty lines is five rounds and a button that
 *  says nothing for four minutes looks broken.
 */
const inChunks = async (n, onProgress, askOne, stopWhenShort = false) => {
  const out = []
  // The cap is what stops a model answering one line at a time from being asked
  // fifty times over.
  for (let call = 0; out.length < n && call < Math.ceil(n / CHUNK) + 3; call += 1) {
    const want = Math.min(CHUNK, n - out.length)
    // The text of the line before, never the `{label, prompt}` object it arrives
    // as: `previous` goes straight into a template string in the chunk notes, and
    // an object there renders `[object Object]` — a carry-over note that says
    // nothing, silently, in every chunk after the first.
    const last = out[out.length - 1]
    const lines = await askOne({ from: out.length + 1, want, total: n,
                                 previous: last ? (last.prompt ?? String(last)) : '' })
    if (!lines.length) break
    // A line identical to the one before it is not a photograph, it is the model
    // marking time — and it survives `clean`, whose dedupe only sees one answer at
    // a time, so a repeat across a chunk seam went out unnoticed. Dropped here and
    // the round is simply short, which the loop asks again for.
    for (const line of lines.slice(0, want)) {
      const text = line.prompt ?? String(line)
      const before = out[out.length - 1]
      if (text.trim() && text.trim() === (before?.prompt ?? String(before ?? '')).trim()) continue
      out.push(line)
    }
    if (onProgress) onProgress(out.length, n)
    // A short chunk means different things to the two streams. Takes: a slow
    // round, ask again. Wardrobe: the shoot has run out of clothes to change,
    // which is the answer — asking again is what makes an assistant invent a
    // garment rather than admit the shoot is undressed.
    if (stopWhenShort && lines.length < want) break
  }
  return out
}

// A look is written head to toe — hair, upper body, lower body, feet,
// accessories, the place. Asking for one line gets one garment and nothing else,
// so the sections are asked for by name and joined back afterwards.
const LOOK_PIECES = LOOK_LINES.length

/** One reading of a look, into the session's two boxes.
 *
 *  Hair, makeup, the place and the light are the same in every frame and are
 *  written once; the four sections in between are the clothes, and those ride on
 *  every take so that a take can change them. The label decides where a line
 *  goes — a model that dropped the labels falls back to position, which is the
 *  order the instruction asked for. */
const split = (lines) => {
  const out = { look: [], wardrobe: [] }
  lines.forEach((line, i) => {
    const text = (line.prompt || '').trim()
    if (!text || EMPTY.test(text)) return
    const section = LOOK_LINES.find((s) => key(s.name) === key(line.label)) || LOOK_LINES[i]
    out[section ? section.part : 'wardrobe'].push(text)
  })
  return { look: sentences(out.look), wardrobe: sentences(out.wardrobe) }
}

// The first word is enough and survives a model that writes "Hair & makeup" or
// "Feet:" — the six section names differ from each other on it.
const key = (name) => (name || '').trim().toLowerCase().split(/[^a-z]+/)[0]

export const lookFromBrief = (brief) =>
  ask({ instruction: LOOK_INSTRUCTION, text: brief, n: LOOK_PIECES }).then(split)

export const lookFromPhoto = (image) =>
  ask({ instruction: LOOK_FROM_PHOTO_INSTRUCTION, image, n: LOOK_PIECES }).then(split)

/** The look box alone: hair, makeup, place, light. Never the clothes — see
 *  LOOK_ONLY_INSTRUCTION. */
export const rewriteLook = (text) =>
  ask({ instruction: LOOK_ONLY_INSTRUCTION, text, n: 2 }).then(joined)

/** A shoot to run, from the look and the wardrobe already in the boxes.
 *
 *  The dice are rolled here rather than left to the sampler: asked the same
 *  question about the same wardrobe, an assistant writes the same sentence back
 *  however warm it is, and "give me another one" has to actually give another
 *  one. So how far the shoot goes, how fast, and how it reads are chosen at
 *  random and handed over as constraints — the assistant writes the shoot around
 *  them and keeps it in the room the look describes.
 */
export const briefFromLook = (look, wardrobe, reach = 'nude') => {
  const pick = (list) => list[Math.floor(Math.random() * list.length)]
  const how = REACH[reach] || REACH.nude
  const rolled = [pick(how.endings), pick(how.paces), ...Object.values(BRIEF_AXES).map(pick)]
  return ask({
    instruction: `${BRIEF_INSTRUCTION}\n\nThis shoot in particular:\n`
               + rolled.map((x) => `- it ${x}`).join('\n')
               + (how.also ? `\n\n${how.also}` : ''),
    text: [look && `The look: ${look}`, wardrobe && `The wardrobe: ${wardrobe}`]
      .filter(Boolean).join('\n'),
    n: 1,
  }).then(first)
}

/** One take's wardrobe, written or rewritten. */
export const rewriteWardrobe = (text) =>
  ask({ instruction: WARDROBE_INSTRUCTION, text, n: WARDROBE_LINES.length }).then(joined)

/** `n` wardrobes, one per take, walking from the session's wardrobe through
 *  whatever the brief describes.
 *
 *  The brief joins the instruction and the wardrobe goes in `text`: the thing
 *  being rewritten is the wardrobe, `n` times over. It is deliberately NOT sent
 *  as `context` — that field means "already in the prompt, do not repeat it", and
 *  repeating it word for word is the entire job here. */
export const wardrobeProgression = (brief, wardrobe, n, onProgress) =>
  inChunks(n, onProgress, async (at) => {
    const lines = await ask({
      instruction: `${WARDROBE_PROGRESSION_INSTRUCTION}\n\nThe shoot goes like this:\n${brief}`
                 + `\n\n${wardrobeChunkNote(at)}`,
      // The wardrobe of the photograph before this chunk, so the carry-over
      // survives the seam between two calls — that is the whole reason a
      // progression can be written in pieces at all.
      text: at.previous || wardrobe,
      n: at.want,
    })
    return lines.map((l) => l.prompt)
  }, true)

/** The takes of a shoot that walks somewhere: same rules as a batch, plus the
 *  order, and written in chunks for the same reason the wardrobe is.
 *
 *  `worn` is the wardrobe of each photograph, when it is already known. Every
 *  chunk is then told what she is actually wearing across the stretch it is
 *  writing, which is the only thing that stops a take reaching for the garment
 *  as the thing that changed. Measured without it, at forty takes: row twelve
 *  came back `both hands sliding the jersey hem upward` — twenty rows after the
 *  wardrobe had put the jersey down, and a prompt naming a jersey is a jersey.
 */
export const takesAlongArc = (brief, context, n, onProgress, worn = []) => {
  const guide = KINDS.shoot.enhance
  return inChunks(n, onProgress, (at) => {
    const last = at.from + at.want - 1
    const dressed = worn[at.from - 1] && [
      context,
      `In photograph ${at.from} she is wearing: ${worn[at.from - 1]}`,
      last !== at.from && worn[last - 1] ? `and by photograph ${last}: ${worn[last - 1]}` : '',
    ].filter(Boolean).join('\n')
    return ask({
      instruction: `${guide.line}\n\n${guide.arc}\n\n${takesChunkNote(at)}`,
      text: brief, context: dressed || context, n: at.want,
    })
  })
}

/** One brief, a whole session: N takes along the arc and the N wardrobes that
 *  walk beside them.
 *
 *  The wardrobe is written first and the takes after it, not both at once. They
 *  describe different things on purpose — the take is the body and the camera,
 *  the wardrobe is the clothes — but a take written blind to the clothes puts
 *  them back on, so the second half is given the first as context and told, in
 *  the words the assistant already gets, not to contradict it.
 */
export const sessionFromBrief = async (brief, look, wardrobe, n, onProgress, reach = 'nude') => {
  // No wardrobe to walk: there is no arc, so the old two-stream writer is still
  // the right one — takes varying pose and framing, and nothing to desync with.
  if (!wardrobe.trim()) {
    const takes = await takesAlongArc(brief, look, n, (made) => onProgress?.(made, n))
    return takes.map((take) => ({ ...take, wardrobe: null }))
  }
  return shootLines(brief, look, wardrobe, n, onProgress, reach)
}

/** A whole shoot, one complete photograph per line, from one stream.
 *
 *  `wardrobe: ''` on every row and not `null`: the line already says what she is
 *  wearing at that moment, so the session's wardrobe must NOT be appended behind
 *  it. Empty string is the app's way of saying "this take names its own clothes",
 *  which is exactly true here — the composed prompt is the look, then the line.
 */
export const shootLines = async (brief, look, wardrobe, n, onProgress, reach = 'nude') => {
  // The one thing the setting decides in here, and it is not a paragraph of
  // prose: whether photograph 1 is dressed. Handing the writer the outfit as
  // `what she is wearing in photograph 1` is what dressed the first seven frames
  // of an explicit shoot whose own brief opened `already undressed with him` —
  // the outfit is concrete and the brief is one sentence away.
  const bare = reach === 'explicit'
  // The arc, decided once and in numbers. Derived per round it was derived
  // differently per round; see STAGE_PLAN_INSTRUCTION.
  const stages = await stagePlan(brief, wardrobe, n)
  onProgress?.(0, n)

  const lines = await inChunks(n, (made) => onProgress?.(made, n), (at) => ask({
    instruction: `${SHOOT_LINE_INSTRUCTION}\n\nThe shoot goes like this:\n${brief}`
               + `\n\n${shootChunkNote({ ...at, stages: covering(stages, at) })}`,
    // The look is context and not part of the answer: it is prepended to every
    // frame by the app, so the writer needs to know it in order not to repeat it.
    context: look,
    // The starting wardrobe only starts the shoot. Handing it back on every chunk
    // is handing back the outfit she has already taken off: measured, the jersey
    // came off at photograph 8, and photograph 20 opened the third chunk by
    // restating the original wardrobe word for word and putting it back on. What
    // a later chunk continues from is the photograph before it, and nothing else.
    text: at.from === 1 && !bare ? wardrobe : (at.from === 1 ? '' : at.previous),
    n: at.want,
  }))

  // The repair pass is counted after the writing, never restarting the tally: a
  // progress number that goes backwards reads as a bug even when nothing is wrong.
  const written = lines.map((l) => l.prompt)
  // How long a line of this shoot is allowed: measured off this shoot, because
  // there is no length that is right for every wardrobe.
  const limit = lengthLimit(written)
  const needing = written.filter((line, i) =>
    problemsWith(line, i === 0 ? wardrobe : written[i - 1], limit).length).length
  const { lines: checked, repaired, stillWrong } =
    await repairAll(written, wardrobe, onProgress, n, n + needing, limit)
  // The word counts are in the line because the writer's own length varies enough
  // between runs to hide what the repair did: comparing two runs compares two
  // different shoots, and only before-and-after inside one run is the repair.
  if (needing) console.info(`[shoot] ${needing} lines failed the check, ${repaired} repaired`
                          + `, longest ${longest(written)} words before and `
                          + `${longest(checked)} after, limit ${limit}`
                          + (stillWrong.length ? `, still wrong: ${stillWrong.join(', ')}` : ''))
  // `suspect` is the residue: a line the check refused and the repair could not
  // fix. Measured over three runs the repair lands about three times in four, so
  // this is never empty for long — and a row nobody can see is a row nobody
  // fixes, which is how the first version of this shipped eighteen broken lines
  // while reporting success.
  return checked.map((prompt, i) => ({
    ...lines[i], prompt, wardrobe: '', suspect: stillWrong.includes(i + 1),
  }))
}

/** How much of a shoot one stage may cover.
 *
 *  A quarter, and it is the fix for the worst thing a seventy-frame run did: the
 *  plan gave photographs 1 to 40 to "still dressed at the counter", so forty
 *  frames were the same photograph and the whole undressing happened between 40
 *  and 41 — top, skirt and stockings gone in a single step, with no frame in
 *  between wearing one of them. The instruction asks for six to twelve stages and
 *  says where the photographs go; asked for seventy it wrote five and put more
 *  than half of them in the first. */
const STAGE_SHARE = 0.25

/** The stages, as `{from, to, what}`. The model answers `1-8 | …`, which is the
 *  `label | text` shape `clean` already splits on.
 *
 *  Asked once and checked; a plan with one stage swallowing the shoot is asked
 *  for again, once, with the offending range quoted back. Once and not until it
 *  is right: this is a minute of somebody's time per attempt, and a plan that is
 *  merely lopsided still shoots. */
export const stagePlan = async (brief, wardrobe, n) => {
  const parse = (rows) => rows.flatMap((r) => {
    const span = /(\d+)\s*[-–—]\s*(\d+)/.exec(r.label || '')
    // A row whose label is not a range is a row the model formatted its own way.
    // Dropped rather than guessed at: a wrong range silently mis-paces the shoot.
    return span && r.prompt.trim()
      ? [{ from: +span[1], to: +span[2], what: r.prompt.trim() }]
      : []
  })
  const cap = Math.max(2, Math.round(n * STAGE_SHARE))
  const tooBig = (stages) => stages.filter((s) => s.to - s.from + 1 > cap)
  const askFor = (extra) => ask({
    instruction: `${STAGE_PLAN_INSTRUCTION}\n\nNo stage covers more than ${cap} photographs `
               + `of the ${n}${extra}`,
    text: `The shoot:\n${brief}\n\nThe wardrobe it starts in:\n${wardrobe}\n\n`
        + `It is ${n} photographs long.`,
    n: 12,
  }).then(parse)

  const first = await askFor('.')
  const over = tooBig(first)
  if (!over.length) return first
  const again = await askFor('. Your last plan gave '
    + over.map((s) => `${s.from}-${s.to}`).join(' and ')
    + ' to a single stage, which is the whole shoot standing still and then changing all at '
    + 'once. Break that stretch into the steps it is made of.')
  return tooBig(again).length && again.length < first.length ? first : (again.length ? again : first)
}

const covering = (stages, at) =>
  stages.filter((s) => s.to >= at.from && s.from <= at.from + at.want - 1)

/* ---- what the code can check without guessing ------------------------------
 *
 * Two failures survived every rewrite of the instruction, and both are decidable
 * from the text alone — which is exactly the work that should not have been left
 * to the writer in the first place:
 *
 *   1. A line stops naming part of the body. Photograph 24 of a real run dropped
 *      `bare chest`, and the shoot came back in a black nightgown nobody wrote,
 *      for the next twenty-six frames. An unstated torso is not a bare torso.
 *   2. A garment comes back. Photograph 20 of another run re-dressed her in the
 *      jersey that came off at 8, by restating the opening wardrobe.
 *
 * Both are checked here and handed back to the writer to fix. The code decides
 * *that* a line is wrong; the model decides what it should say instead.
 */

// Every line names the chest, the hips-and-legs, and the feet — clothed or bare.
const BODY = [
  { part: 'the chest and torso', re: /\b(chest|breasts?|torso|midriff|bust|nude|topless|jersey|shirt|top|harness|bra)\b/i },
  { part: 'the hips and legs', re: /\b(hips?|thighs?|legs?|knees?|briefs|panties|stockings?|fishnets?|skirt|shorts)\b/i },
  { part: 'the feet', re: /\b(feet|foot|boots?|heels?|shoes?|barefoot|toes)\b/i },
]

/** Where a line stops being a photograph and starts being an inventory.
 *
 *  The instruction asks for sixty words and is ignored: measured at n=50, forty-
 *  five of forty-five lines were over it, median ninety-five, longest a hundred
 *  and thirty-six. And it is not cosmetic — the same run put the pleated mini
 *  skirt back as a long cream one and the white top back as a navy one, which is
 *  the drift LENGTH in `kinds.js` was written about. Instructions had their turn;
 *  this is the check that follows them.
 *
 *  There is no right number here, and four were tried before giving up on
 *  finding one. A line carries the framing, the camera position, every garment
 *  *restated word for word* — that carry is what holds a wardrobe together over
 *  forty frames — the pose and the expression, so how long it runs depends on how
 *  many pieces the wardrobe has and on how many rules this file has grown: the
 *  framing and the camera clauses alone are twenty-odd words a line that were not
 *  there a day ago. Runs of the same brief came back at medians of 99, 107, 140
 *  and 162. Sixty flagged nine lines in twelve, a hundred and ten flagged twelve
 *  in twelve, and a flag every row wears is not a flag.
 *
 *  So the line is measured against the shoot it belongs to. Forty per cent longer
 *  than its neighbours is a line that has started listing something twice —
 *  whatever the outfit, whatever the instructions — and that is the only claim
 *  here worth making. The absolute wall stays only to catch a runaway in a shoot
 *  too short to have a shape. */
export const MAX_WORDS = 200
const RELATIVE = 1.4

const words = (text) => (text || '').trim().split(/\s+/).filter(Boolean).length

const longest = (lines) => Math.max(0, ...lines.map(words))

/** What a garment is recognised by, and what an explicit photograph is of.
 *
 *  Shortening a line deletes words, and the check that accepts the shortening
 *  cannot tell filler from fact. Measured over seven repaired lines: about three
 *  words of filler lost per line — `of`, `still`, `at her throat`, `resting` —
 *  against about one word that carried meaning. But that one is the whole
 *  question: one repair dropped `pleated mini skirt` and another dropped `erect
 *  penis pressed`, which is a photograph of something else. */
const IDENTIFYING = /^(white|navy|red|black|blue|cream|grey|silver|gold|green|brown|pink|pleated|cropped|fishnet|open-weave|leather|denim|linen|knotted|collar|trim|emblem|anchor|hem|sleeves?|mini|midi|micro|high-waisted|strappy|platform|eyelets?|buttons?|zip|straps?|buckles?|rings?|one|two|three|four|five|six|seven|eight)$/i

const ACT = /\b(penetrat\w*|penis|inside her|entering her|cock|fucking|thrust\w*)\b/i

const tokens = (text) => (text || '').toLowerCase().replace(/[^a-z\s-]/g, ' ').split(/\s+/).filter(Boolean)

/** Did the rewrite keep everything that was not filler?
 *
 *  A garment that came OFF between the two is not a loss — that is the shoot
 *  moving — so the attributes are only demanded back while the garment families
 *  themselves are unchanged. The act is demanded back unconditionally: no
 *  complaint this check can raise is answered by making a photograph less
 *  explicit than the writer made it. */
export const keepsTheFacts = (line, fixed) => {
  if (ACT.test(line) && !ACT.test(fixed)) return false
  const before = familiesIn(line).join()
  if (before !== familiesIn(fixed).join()) return true
  const kept = new Set(tokens(fixed))
  return !tokens(line).some((w) => IDENTIFYING.test(w) && !kept.has(w))
}

/** The length a line of THIS shoot is allowed, from the lines of this shoot. */
export const lengthLimit = (lines) => {
  const counts = lines.map(words).filter(Boolean).sort((a, b) => a - b)
  if (!counts.length) return MAX_WORDS
  return Math.min(MAX_WORDS, Math.round(counts[Math.floor(counts.length / 2)] * RELATIVE))
}

const tooLong = (line, limit = MAX_WORDS) => (words(line) <= limit ? [] : [
  `It is ${words(line)} words long, half as long again as the other photographs of this `
  + 'shoot. Cut it back to their length '
  + 'by saying each garment once and in the fewest words that identify it — colour, cut, the '
  + 'one detail that tells it apart — and by dropping every phrase that repeats what the line '
  + 'has already said. Keep the framing, the camera position, the state of every garment and '
  + 'all three of the chest, the hips and legs and the feet: what goes is the wording, never '
  + 'a fact.',
])

/** Everything wrong with a line that shortening it cannot fix.
 *
 *  Split from the length check because the two are judged differently when a
 *  repair comes back: a shorter line that still runs long is progress and is
 *  kept, a line missing the feet is not a photograph however short it is. */
const contentProblems = (line, previous) => {
  const found = []
  // Both of these went missing the moment a repair was asked to make a line
  // shorter: told to cut, the model cuts the two clauses that are not about her.
  // They are the two that were measured to matter, so they are checked.
  if (!/\ba (full-length|three-quarter|waist-up) photograph/i.test(line)) {
    found.push('It does not say its framing. Every line has one of `a full-length photograph, '
             + 'head to feet`, `a three-quarter photograph from the knees up` or `a waist-up '
             + 'photograph`, straight after the camera clause it opens with.')
  }
  if (!/^\s*taken from\b/i.test(line)) {
    found.push('It does not OPEN with where the camera is. Every line begins with that clause '
             + 'and nothing before it — `Taken from directly in front of her, …`, `Taken from '
             + 'behind her left shoulder, her back three-quarters to the camera, …`, `Taken '
             + 'from her right side, her body in full profile, …`, `Taken from directly behind '
             + 'her, …`, `Taken from above her, looking down, …` — because everything after it '
             + 'is eighty words about clothes, and what the reader meets first is what frames '
             + 'the photograph.')
  }
  // Introducing her is what comes back under pressure: told to hold a shape the
  // brief argues with, the writer opened four lines in six with `a young woman`
  // and `the same young woman`. The trigger at the front of the prompt already
  // says who she is, and a description of her competes with it in every frame.
  if (/\b(a|the same|another|one) (young |naked )?(woman|girl|model|lady|female)\b/i.test(line)) {
    found.push('It introduces her — `a young woman`, `the same young woman`. The trigger word '
             + 'at the front of the prompt already says who she is, and a description of her '
             + 'competes with it. She is `her`, and nothing else. A second person is named as a '
             + 'body — `a naked man` — and that is different.')
  }
  const shed = namesWhatItSheds(line)
  if (shed.length) {
    found.push(`It names the ${shed.join(', the ')} in the very line that takes it off. The `
             + 'photograph the line before it took still had that piece on; this one does not, '
             + 'and a line that says `gone`, `removed` or `discarded` has put the garment back '
             + 'in the photograph whatever the words around it say. Write the skin instead — '
             + '`her chest bare, her shoulders bare` — and let the piece simply not be there. '
             + 'A piece that is only moved is different and stays named: pushed up, pulled '
             + 'aside, unbuttoned, off one shoulder.')
  }
  const missing = BODY.filter((b) => !b.re.test(line)).map((b) => b.part)
  if (missing.length) {
    found.push(`It says nothing about ${missing.join(' or ')}. Every photograph names the `
             + 'chest and torso, the hips and legs, and the feet — and where there is no '
             + 'garment, it names the skin. An unstated part is not a bare part: it is a '
             + 'part the reader dresses for you.')
  }
  if (previous) {
    const before = new Set(familiesIn(previous))
    const back = [...new Set(familiesIn(line))].filter((f) => !before.has(f))
    if (back.length) {
      found.push(`It puts back the ${back.join(', the ')}, which the photograph before it `
               + 'was not wearing. Once a piece is off it stays off: it is simply not in '
               + 'the line.')
    }
  }
  return found
}

/** What is wrong with this line, in words the writer can act on. Empty = fine.
 *
 *  `limit` is the shoot's own length, from `lengthLimit`. Left out — a single
 *  line, checked on its own — only the absolute wall applies, because one line
 *  has no neighbours to be long against. */
export const problemsWith = (line, previous, limit = MAX_WORDS) =>
  [...tooLong(line, limit), ...contentProblems(line, previous)]

/** Garments by family, not by word.
 *
 *  Comparing word sets flagged two perfectly good lines out of twenty-four: one
 *  said `fishnet` and the next said `fishnet stockings`, and `stockings` read as
 *  a garment that had come back; another said `boots` and the next `one platform
 *  boot`. A check that cries wolf on singular-plural is a check that gets turned
 *  off, so the comparison is between the things themselves. */
const GARMENT_FAMILIES = {
  jersey: /\b(jersey|shirt|tee|t-shirt|blouse|sweater|jumper)\b/i,
  top: /\b(top|crop top|camisole|vest)\b/i,
  bra: /\b(bra|bralette)\b/i,
  harness: /\b(harness|strappy|straps? across)\b/i,
  briefs: /\b(briefs|panties|knickers|thong|underwear)\b/i,
  stockings: /\b(stockings?|fishnets?|tights|hold-ups|hosiery)\b/i,
  boots: /\b(boots?|heels?|shoes?|sandals?)\b/i,
  skirt: /\b(skirt|shorts)\b/i,
  dress: /\b(dress|gown|nightgown|robe|bodysuit|leotard)\b/i,
  jacket: /\b(jacket|coat|cardigan|blazer)\b/i,
  corset: /\b(corset|bustier|basque)\b/i,
}

const familiesIn = (text) =>
  Object.entries(GARMENT_FAMILIES).filter(([, re]) => re.test(text || '')).map(([name]) => name)

/** The line where a piece finally comes off is the one that goes wrong, every
 *  time — and until now nothing checked it.
 *
 *  `GARMENT_CARRY` has said for a while that the removing line must not contain
 *  the name of the piece at all, and the check beside it only ever asked the
 *  opposite question: has a garment come *back*. So `the ruched drawstrings of
 *  the olive green ribbed crop top gone from her body` passed, and a prompt that
 *  names a crop top has a crop top in it. Found in a user's own session, and in
 *  four lines of a seventy-frame run and five of a twenty-four before it.
 *
 *  Only words that mean the piece is OFF count. A piece that is merely moved is
 *  still worn and is still named — pushed up, pulled aside, unbuttoned, off one
 *  shoulder, hooked under a waistband — and flagging those would flag the whole
 *  middle of every shoot. */
const SHED = /\b(gone|removed|discarded|cast aside|set aside|no longer (?:on|worn)|off her body|lying (?:on|in) the (?:floor|ground|tiles?|grass)|pooled (?:on|at)|crumpled (?:on|at))\b/i

export const namesWhatItSheds = (line) => {
  const text = line || ''
  // Near each other, not merely in the same sentence: `the blouse already gone,
  // the pleated mini skirt still at her hips` is one sentence about two pieces,
  // and flagging the skirt there would ask the repair to take off a garment that
  // is still on.
  const near = (re) => {
    for (const m of text.matchAll(new RegExp(re.source, 'gi'))) {
      const around = text.slice(Math.max(0, m.index - 10), m.index + m[0].length + 50)
      if (SHED.test(around)) return true
    }
    return false
  }
  return Object.entries(GARMENT_FAMILIES).filter(([, re]) => near(re)).map(([name]) => name)
}

/** Check every line and ask the writer to fix the ones that fail.
 *
 *  Sequential on purpose — a repaired line becomes the previous line of the next
 *  check, so a run of bad lines is repaired forwards rather than each one being
 *  compared against a version that is about to change.
 *
 *  A repair is only accepted if it actually repairs. The first version of this
 *  swallowed both failures silently — a repair that errored and a repair that
 *  came back still broken looked exactly like a line that never needed one — and
 *  eighteen lines of a twenty-four line shoot went out unfixed while the code
 *  reported success. Whatever survives that check is still returned, and
 *  `stillWrong` says which lines to look at.
 */
const repairAll = async (lines, wardrobe, onProgress, done, total, limit = MAX_WORDS) => {
  const out = []
  const stillWrong = []
  let repaired = 0
  for (const [i, line] of lines.entries()) {
    const previous = i === 0 ? wardrobe : out[i - 1]
    const problems = problemsWith(line, previous, limit)
    if (!problems.length) { out.push(line); continue }
    // A line whose only fault is its length is flagged and left alone. Measured
    // on eight long lines: the only rewrites that actually came back shorter were
    // the ones that had dropped something — `pleated mini skirt` in one, `erect
    // penis pressed` in another — and `keepsTheFacts` rejects exactly those, so
    // the call was being spent to be refused. This model cannot compress a line
    // of this kind without deleting a fact, because in a prompt the words ARE the
    // garment. Content problems still get their repair; they are the ones it can
    // actually fix.
    if (!contentProblems(line, previous).length) {
      out.push(line); stillWrong.push(i + 1)
      onProgress?.(Math.min(total, done + repaired + stillWrong.length), total)
      continue
    }

    let fixed = ''
    try {
      fixed = await ask({
        instruction: REPAIR_INSTRUCTION + '\n\nThe problems:\n'
                   + problems.map((p) => `- ${p}`).join('\n'),
        context: `The photograph before this one:\n${previous}`,
        text: line,
        n: 1,
      }).then(first)
    } catch {
      // Asked and refused. The line stays as it was and is reported below: a
      // flagged line you can see beats a run that dies on its forty-first photo.
    }
    fixed = (fixed || '').trim()
    // A "correction" shorter than half the line is the model answering with the
    // fragment it changed, which would silently delete the rest of the shoot's
    // photograph. Rejected in favour of the original — unless the complaint was
    // the length itself, where cutting a hundred and thirty words to sixty is the
    // repair working, and half the original is the wrong floor to measure it by.
    const floor = words(line) > limit ? 35 : words(line) / 2
    // A repair is accepted when it leaves FEWER problems than it found, not when
    // it leaves none. Demanding none was measured twice and failed twice: a
    // hundred-and-six-word line cut to seventy-eight was thrown away for still
    // being long, and once the framing and the camera were checked too, seven
    // repairs in twelve were thrown away for missing one of five conditions —
    // each time the original, worse line went out instead. Perfect is the enemy
    // here; the residue is flagged rather than argued with.
    // Length is the one complaint a repair can answer *partly*, and usually does:
    // asked to cut a hundred and five words to sixty this model returns eighty.
    // Counting problems alone that is no better than the original — one problem
    // before, one after — and three repairs in twelve survived. A tenth shorter
    // with nothing new broken is progress, and progress is what gets kept.
    // …but not by trading one problem for another. Counting all the problems
    // together, a repair that cut the line by a quarter *and* dropped the camera
    // clause scored the same and was kept: the camera survived in seven lines of
    // twelve, against twelve before the repair ran. So what a shortening may not
    // do is lose any of the things shortening is not about.
    const after = problemsWith(fixed, previous, limit)
    const shorter = tooLong(line, limit).length && words(fixed) <= words(line) * 0.9
      && contentProblems(fixed, previous).length <= contentProblems(line, previous).length
    const usable = fixed && words(fixed) > floor && keepsTheFacts(line, fixed)
      && (after.length < problems.length || shorter)
    // Repaired and still flagged are no longer the same question: a line cut from
    // a hundred and six words to ninety is a repair worth keeping AND a row worth
    // outlining, and reporting only one of the two hides whichever it drops.
    const kept = usable ? fixed : line
    out.push(kept)
    if (usable) repaired += 1
    if (problemsWith(kept, previous, limit).length) stillWrong.push(i + 1)
    onProgress?.(Math.min(total, done + repaired + stillWrong.length), total)
  }
  return { lines: out, repaired, stillWrong }
}

/** How many times the clothes may change in one shoot.
 *
 *  A wardrobe has as many states as it has pieces, and a shoot has as many
 *  photographs as you asked for; those are different numbers and conflating them
 *  is what broke a forty-take session. Asked for forty states from six garments,
 *  the assistant was bare by fifteen, repeated itself while it had nothing left
 *  to remove — and the repeats were dropped as duplicates, so the count never
 *  reached forty — and then dressed her in a schoolgirl uniform to have something
 *  to take off again. Twelve is what a rich wardrobe supports.
 */
export const WARDROBE_STATES = 12

/** What a take names that its own wardrobe has already taken off.
 *
 *  The one failure the instructions cannot close on their own: at the seam where
 *  a garment goes, a take reaches for it as the thing that changed — measured, at
 *  forty takes, three of them, `the jersey slipping further off one shoulder` on
 *  a row whose wardrobe has no jersey in it. Naming a jersey puts a jersey in the
 *  photograph, so the row is flagged rather than quietly shot.
 *
 *  A word in the session's wardrobe but not in this take's is precisely a garment
 *  that has come off; a colour, a fabric or a bare shoulder is in both, so it
 *  does not flag. Nothing is rewritten — the box is yours, this only points.
 */
export const namesWhatIsGone = (take, sessionWardrobe, takeWardrobe) => {
  // A take that names its own clothes has nothing to contradict: the garments and
  // the pose are one sentence, written in one breath, and this whole check exists
  // because they used to be two. `''` is exactly that row — see `shootLines`.
  if (takeWardrobe === '') return []
  const words = (text) => new Set((text || '').toLowerCase().match(/[a-z]{4,}/g) || [])
  const still = words(takeWardrobe)
  const said = words(take)
  return [...words(sessionWardrobe)]
    .filter((w) => !still.has(w) && said.has(w) && !NOT_A_GARMENT.has(w))
}

// A wardrobe is written on a body and in space, so it is full of words that are
// neither: `above the ribs` and `high-waisted` flagged two perfectly good takes
// for saying `over her ribs` and `elbows lifted high`. A warning that goes off on
// good rows is a warning nobody reads, which costs more than the two it catches.
const NOT_A_GARMENT = new Set([
  'shoulder', 'shoulders', 'chest', 'ribs', 'waist', 'hips', 'thigh', 'thighs', 'legs',
  'knees', 'ankles', 'ankle', 'arms', 'wrist', 'wrists', 'back', 'neck', 'throat', 'skin',
  'feet', 'toes', 'stomach', 'body', 'bare', 'nude', 'naked', 'topless', 'breast', 'breasts',
  'high', 'above', 'below', 'over', 'under', 'down', 'open', 'loose', 'across', 'against',
  'with', 'from', 'thin', 'wide', 'long', 'short', 'cut', 'worn', 'left', 'right', 'side',
  'still', 'that', 'this', 'them', 'they', 'then', 'onto', 'into', 'past', 'held', 'hand',
  'hands', 'mouth', 'eyes', 'lips', 'head', 'hair', 'face', 'mirror', 'floor', 'phone',
])

/** The states stretched across the photographs: state one for the first stretch,
 *  the last state for the last. A shoot of forty passes through a dozen changes
 *  of clothes, not forty. */
export const spread = (states, n) => (states.length
  ? Array.from({ length: n }, (_, i) =>
    states[Math.min(states.length - 1, Math.floor((i * states.length) / n))])
  : [])

export const anglesFromText = (text, allowed) =>
  ask({ instruction: ANGLE_FROM_TEXT_INSTRUCTION, text, allowed, n: 1 }).then(first)

const first = (lines) => (lines[0]?.prompt || '')

// A section with nothing in it answers `none` — that is what makes the other five
// safe to demand. The labels are the checklist, not the look, so they are dropped
// here: `Feet | black boots` is one line of the session's look, and the word
// "Feet" in a prompt is a foot in the photo.
const EMPTY = /^(none|n\/a|-|—|nothing)\.?$/i

/** The sections back into one box, one full stop between them.
 *
 *  Not `, ` — the sections are written as prose now, and a comma between two
 *  sentences is the splice `…a silver stud in each earlobe., The room is…`. Same
 *  rule the server joins its pieces by, for the same reason: the reader is a
 *  language model, and a run-on clause is where relations start bleeding between
 *  the things they belong to. */
const sentences = (parts) => parts
  .map((p) => p.trim().replace(/[,\s]+$/, ''))
  .filter(Boolean)
  .map((p) => (/[.!?]$/.test(p) ? p : `${p}.`))
  .join(' ')

const joined = (lines) => sentences(lines
  .filter((l) => !EMPTY.test(l.prompt.trim()))
  .map((l) => l.prompt))

/** A photo the model has to *read*, so it is scaled down before it travels: a
 *  camera file is megabytes of base64 in a JSON body, and a small vision model
 *  works at 1024px anyway. Canvas does it, so nothing is installed for it. */
export const photoDataUri = (file, max = 1024) => new Promise((resolve, reject) => {
  const url = URL.createObjectURL(file)
  const img = new Image()
  img.onload = () => {
    URL.revokeObjectURL(url)
    const scale = Math.min(1, max / Math.max(img.width, img.height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(img.width * scale)
    canvas.height = Math.round(img.height * scale)
    canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height)
    resolve(canvas.toDataURL('image/jpeg', 0.9))
  }
  img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('that file is not an image')) }
  img.src = url
})
