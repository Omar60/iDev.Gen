import { describe, expect, test } from 'vitest'
import { FRAMES, framePlan, shootChunkNote } from './kinds.js'
import { stageRow, problemsWith, withDealtFraming } from './enhance.js'

/** The framing is dealt from the stage's wardrobe instead of chosen by the
 *  writer, because the frame reaches the lowest part of her the line NAMES and a
 *  line obeying the whole-body walk names her feet. What is checked here is the
 *  three places that arithmetic can go wrong silently: the column the planner
 *  writes, the plan built from it, and the line check that has to agree with the
 *  plan rather than with the writer. */
describe('the column the stage plan carries', () => {
  test('`below: no` is the only thing that opens the framing', () => {
    expect(stageRow({ label: '9-14', prompt: 'down to the bra | below: no' }))
      .toEqual([{ from: 9, to: 14, what: 'down to the bra', lower: false }])
  })

  test('and everything else leaves it closed', () => {
    const lower = (prompt) => stageRow({ label: '1-8', prompt })[0]?.lower
    expect(lower('still in the denim | below: yes')).toBe(true)
    // The column absent, the column mangled, the column answered some other way:
    // all three are the unknown, and the unknown is full-length. A wrong `no` is
    // a photograph whose frame cuts above a garment it never named.
    expect(lower('still in the denim')).toBe(true)
    expect(lower('still in the denim | below: maybe')).toBe(true)
    expect(lower('still in the denim | nothing below the waist')).toBe(true)
  })

  test('the marker is cut out of what the writer is shown', () => {
    expect(stageRow({ label: '1-8', prompt: 'she drifts around | below: yes' })[0].what)
      .toBe('she drifts around')
  })
})

describe('the plan built from it', () => {
  const stages = [{ from: 1, to: 6, what: 'dressed', lower: true },
                  { from: 7, to: 10, what: 'bare', lower: false }]

  test('a photograph is waist-up only inside a stage that says so', () => {
    expect(framePlan(10, stages).map((f) => f.floor))
      .toEqual(['feet', 'feet', 'feet', 'feet', 'feet', 'feet',
                'waist', 'waist', 'waist', 'waist'])
  })

  test('a photograph in no stage at all is full-length', () => {
    expect(framePlan(12, stages)[11]).toBe(FRAMES.feet)
    expect(framePlan(4, [])[0]).toBe(FRAMES.feet)
  })

  test('the row reaches the writer with the camera and not in a list of its own', () => {
    const note = shootChunkNote({ from: 7, want: 1, total: 10,
                                  cameras: ['Taken from directly in front of her'],
                                  frames: [FRAMES.waist] })
    expect(note).toContain('7 | frame: a waist-up photograph')
    expect(note).toContain('NOTHING BELOW HER')
  })
})

describe('the check that has to agree with the plan', () => {
  const CAMERA = 'Taken from directly in front of her, '
  const WAIST_UP = `${CAMERA}a waist-up photograph, her chest bare above a black satin bra, `
                 + 'her arms loose at her sides, looking straight at the lens.'
  const complaints = (line, frame) => problemsWith(line, '', 400, frame).join(' ')

  test('a waist-up line that keeps its feet is refused', () => {
    const withFeet = WAIST_UP.replace('looking straight',
                                      'white socks on her feet, looking straight')
    expect(complaints(withFeet, FRAMES.waist)).toContain('below her waist')
  })

  test('and the same line is fine without them', () => {
    expect(complaints(WAIST_UP, FRAMES.waist)).toBe('')
  })

  test('the whole-body walk still runs when the frame is full-length', () => {
    // The same line, and now the two regions it does not name are the failure:
    // the exemption is the framing's, never the writer's.
    expect(complaints(WAIST_UP, FRAMES.feet)).toContain('says nothing about')
  })

  test('a framing the line chose for itself is refused when one was dealt', () => {
    const chosen = WAIST_UP.replace('a waist-up photograph',
                                    'a three-quarter photograph from the knees up')
    expect(complaints(chosen, FRAMES.waist)).toContain('not the one this photograph was given')
  })

  test('and with nothing dealt the check is the one it always was', () => {
    expect(complaints(WAIST_UP, null)).toContain('says nothing about')
  })
})

/** The repair does not fix a framing the writer picked for itself: measured, it
 *  accepted none of four. The swap towards full-length is deterministic instead,
 *  and it is the only direction that can be. */
describe('the framing put back by the code', () => {
  const line = 'Taken from her right side, a waist-up photograph, her chest bare, '
             + 'the denim on her hips and white socks on her feet.'

  test('a tight framing is rewritten to the full-length one that was dealt', () => {
    expect(withDealtFraming(line, FRAMES.feet))
      .toContain('a full-length photograph, head to feet, her chest bare')
    expect(withDealtFraming(line, FRAMES.feet)).not.toContain('waist-up')
  })

  test('a tight framing the line did not earn is handed back', () => {
    // Never the other way: tightening means deleting her legs and the garments on
    // them, which is the one thing a regex must never do to a prompt. So the line
    // that still names her socks becomes full-length even where waist-up was
    // dealt, and the photograph and the clause agree again.
    expect(withDealtFraming(line, FRAMES.waist))
      .toContain('a full-length photograph, head to feet, her chest bare')
  })

  test('and a line that earned it keeps it', () => {
    const above = 'Taken from her right side, a waist-up photograph, her chest bare above a '
                + 'black satin bra, her hands at her collarbones.'
    expect(withDealtFraming(above, FRAMES.waist)).toBe(above)
  })

  test('a line with no framing clause at all is left alone to be flagged', () => {
    const bare = 'Taken from her right side, her chest bare and her feet on the carpet.'
    expect(withDealtFraming(bare, FRAMES.feet)).toBe(bare)
  })
})
