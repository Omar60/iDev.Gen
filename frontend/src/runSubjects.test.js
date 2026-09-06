import { describe, expect, test } from 'vitest'
import { RUN_SUBJECTS, subjectNote, missingSubjects, SHOOT_LINE_INSTRUCTION } from './kinds.js'
import { problemsWith, shootInstruction, shootLines } from './enhance.js'

/** The three subjects a run can switch on. Each one is a thing the sampler
 *  invents when the line leaves room for it, and never the same way twice: a
 *  tattoo in photograph 6 and none in photograph 7 is two different women. So
 *  the run says whether there is one and what it is, and the words are the
 *  operator's.
 *
 *  OFF is SILENCE, not a prohibition, and that is the half worth a test: a rule
 *  naming a tattoo is a rule that puts a tattoo in the reader's head. */
describe('a switched-off subject', () => {
  test('reaches the writer nowhere at all', () => {
    const instruction = SHOOT_LINE_INSTRUCTION + subjectNote({})
    // On the WORD and not on the letters: `pet` is inside `competes` and
    // `repetition`, both of which the instruction has always said, and a
    // substring check would fail on prose that has nothing to do with a pet.
    //
    // Split into words rather than matched with a word-boundary pattern, and
    // that is not a style choice: the first version of this line built
    // `new RegExp(`\b...`)` inside a template literal, where a lone backslash-b
    // is the BACKSPACE escape and not a boundary. The test then looked for a
    // control character, matched nothing, and passed against an instruction
    // with `tattoo` written into it. There is no backslash in this version at
    // all. See [[idevgen-invisible-byte-lesson]].
    const words = new Set(instruction.toLowerCase().match(/[a-z]+/g))
    for (const subject of RUN_SUBJECTS) {
      expect(words.has(subject.input)).toBe(false)
    }
    expect(subjectNote({})).toBe('')
    // A switch on with nothing behind it is the same silence: the note is built
    // from what was supplied, and the refusal below is what stops the run.
    expect(subjectNote({ with_tattoo: true })).toBe('')
  })

  test('is a fault when a line describes it anyway', () => {
    const line = 'Taken from her left side, a full-length photograph, head to feet. She stands '
               + 'with her weight on one hip. A small swallow tattoo on her left hip.'
    const off = problemsWith(line, '', 400, null, {}).filter((p) => p.includes('does not have'))
    expect(off).toHaveLength(1)
    expect(off[0]).toContain('tattoo')

    const on = problemsWith(line, '', 400, null, { with_tattoo: true, tattoo: 'a small swallow' })
    expect(on.filter((p) => p.includes('does not have'))).toHaveLength(0)
  })

  test('is checked per subject, so one switched on does not excuse another', () => {
    const line = 'Taken from her left side, a full-length photograph, head to feet. She kneels '
               + 'beside the dog, a small swallow tattoo on her left hip.'
    const found = problemsWith(line, '', 400, null, { with_tattoo: true, tattoo: 'a small swallow' })
                    .filter((p) => p.includes('does not have'))
    expect(found).toHaveLength(1)
    expect(found[0]).toContain('dog')
  })
})

describe('a switched-on subject', () => {
  test('is handed to the writer verbatim, with the field it belongs in', () => {
    const note = subjectNote({ with_tattoo: true, tattoo: 'a small swallow on her left hip' })
    expect(note).toContain('a small swallow on her left hip')
    expect(note).toContain('`marks`')
    expect(note).toContain('word for word')
  })

  test('reaches the instruction the writer is actually handed', () => {
    // The half that a note built perfectly and never appended would pass: what
    // is asserted here is the string the caller sends, not the note on its own.
    const run = { with_pet: true, pet: 'a grey cat asleep on the sill' }
    expect(shootInstruction('directed', { run })).toContain('a grey cat asleep on the sill')
    const off = new Set(shootInstruction('directed', {}).toLowerCase().match(/[a-z]+/g))
    for (const subject of RUN_SUBJECTS) {
      expect(off.has(subject.input)).toBe(false)
    }
  })

  test('names every input the run is short of, not just the first', () => {
    expect(missingSubjects({})).toEqual([])
    expect(missingSubjects({ with_tattoo: true, tattoo: '  ' })).toEqual(['tattoo'])
    expect(missingSubjects({ with_tattoo: true, with_pet: true, with_liquids: true }))
      .toEqual(['tattoo', 'pet', 'liquids'])
    expect(missingSubjects({ with_pet: true, pet: 'a grey cat on the sill' })).toEqual([])
  })

  test('is a fault when a line describes it in its own words instead', () => {
    // 9.9. The carry is the point of supplying the words at all: a tattoo the
    // writer describes again is a different tattoo by the next chunk, and this
    // is the check that catches it before the shoot is shot.
    const run = { with_tattoo: true, tattoo: 'a small swallow on her left hip' }
    const reworded = 'Taken from her left side, a full-length photograph, head to feet. She '
                   + 'stands. A little bird tattooed above her hipbone.'
    const drift = problemsWith(reworded, '', 400, null, run).filter((p) => p.includes('own words'))
    expect(drift).toHaveLength(1)
    expect(drift[0]).toContain('a small swallow on her left hip')

    const carried = 'Taken from her left side, a full-length photograph, head to feet. She '
                  + 'stands. A small swallow on her left hip, the tattoo catching the light.'
    expect(problemsWith(carried, '', 400, null, run).filter((p) => p.includes('own words')))
      .toHaveLength(0)

    // A photograph that simply does not show it is not a fault: the line names
    // nothing of the kind, so there is nothing to have reworded.
    const silent = 'Taken from her left side, a full-length photograph, head to feet. She stands.'
    expect(problemsWith(silent, '', 400, null, run)
             .filter((p) => p.includes('own words') || p.includes('does not have'))).toHaveLength(0)
  })

  test('refuses the run before a line is written when its words are missing', async () => {
    // 9.8. Refused at the top of `shootLines`, before the stage plan and before
    // any call goes out: by the time an invented tattoo is visible in the lines,
    // the run has been spent.
    await expect(shootLines('a brief', 'a look', 'a wardrobe', 4, () => {}, 'nude',
                            'directed', [], { with_tattoo: true }))
      .rejects.toThrow(/tattoo/)
    await expect(shootLines('a brief', 'a look', 'a wardrobe', 4, () => {}, 'nude',
                            'directed', [], { with_tattoo: true, with_pet: true }))
      .rejects.toThrow(/tattoo, pet/)
  })

  test('puts each subject in a field the writer is actually asked for', () => {
    // A note that names a field the joiner has no heading for lands the whole
    // line in the flat fallback, which is the format measured to come back as a
    // lit set instead of a photograph.
    const fields = ['camera', 'act', 'her', 'him', 'marks', 'worn', 'accessories', 'props',
                    'technique', 'style', 'face', 'story']
    for (const subject of RUN_SUBJECTS) {
      expect(fields).toContain(subject.field)
    }
  })
})
