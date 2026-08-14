/** The prompt assistant, from the browser's side.
 *
 *  It suggests text for a box on screen. Nothing here queues anything, and
 *  nothing here composes: a take line stays a take line, and the trigger, the
 *  base prompt and the look are still prepended by the server at insert time.
 *  What the model is told about them is "this is already in the prompt, do not
 *  repeat it" — which is the rule the docs give a human writing the same take.
 */
import { api } from './api'
import { KINDS, LOOK_INSTRUCTION, LOOK_FROM_PHOTO_INSTRUCTION, ANGLE_FROM_TEXT_INSTRUCTION } from './kinds.js'

export const ask = (payload) => api.post('/api/enhance', payload).then((r) => r.lines || [])

/** What is already in the prompt of a take painted from noise. A reference take
 *  carries none of it, so it is never given any of this. */
export const composed = (model, look) =>
  [model?.trigger, model?.base_positive, look].map((x) => (x || '').trim()).filter(Boolean).join(', ')

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

// A look is one line in the session, but it is written head to toe — hair, upper
// body, lower body, feet, accessories, the place. Asking for one line gets one
// garment and nothing else, so the sections are asked for and joined back into
// the single line the session holds. The server drops the repeats.
const LOOK_PIECES = 6

export const lookFromBrief = (brief) =>
  ask({ instruction: LOOK_INSTRUCTION, text: brief, n: LOOK_PIECES }).then(joined)

export const lookFromPhoto = (image) =>
  ask({ instruction: LOOK_FROM_PHOTO_INSTRUCTION, image, n: LOOK_PIECES }).then(joined)

export const rewriteLook = (text) =>
  ask({ instruction: LOOK_INSTRUCTION, text, n: LOOK_PIECES }).then(joined)

export const anglesFromText = (text, allowed) =>
  ask({ instruction: ANGLE_FROM_TEXT_INSTRUCTION, text, allowed, n: 1 }).then(first)

const first = (lines) => (lines[0]?.prompt || '')

// A section with nothing in it answers `none` — that is what makes the other five
// safe to demand. The labels are the checklist, not the look, so they are dropped
// here: `Feet | black boots` is one line of the session's look, and the word
// "Feet" in a prompt is a foot in the photo.
const EMPTY = /^(none|n\/a|-|—|nothing)\.?$/i
const joined = (lines) => lines
  .filter((l) => !EMPTY.test(l.prompt.trim()))
  .map((l) => l.prompt)
  .join(', ')

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
