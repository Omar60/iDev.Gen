import { describe, expect, test } from 'vitest'
import { FRAMING, shootChunkNote } from './kinds.js'
import { problemsWith, withDealtFraming } from './enhance.js'

/** The framing is dealt like the camera instead of chosen, because the frame
 *  reaches the lowest part of her the line NAMES and a line obeying the
 *  whole-body walk names her feet. One framing, and the writer may not pick
 *  another: what is checked here is that it reaches the writer, that the check
 *  refuses any other, and that the code puts it back without a repair call. */
describe('the framing dealt to a written shoot', () => {
  test('it reaches the writer on the camera row, not in a list of its own', () => {
    const note = shootChunkNote({ from: 7, want: 1, total: 10,
                                  cameras: ['Taken from directly in front of her'],
                                  framing: FRAMING })
    expect(note).toContain('7 | frame: a full-length photograph, head to feet')
    expect(note).toContain('COPIED WORD FOR WORD')
  })

  test('and a manner with no camera to deal keeps the framing it always had', () => {
    const note = shootChunkNote({ from: 1, want: 1, total: 4,
                                  cameras: ['Taken from directly in front of her'] })
    expect(note).not.toContain('frame:')
    expect(note).toContain('your framing after it')
  })
})

describe('the check that has to agree with the deal', () => {
  const LINE = 'Taken from directly in front of her, a full-length photograph, head to feet, '
             + 'her chest bare above a black satin bra, the charcoal denim on her hips and '
             + 'legs, white cotton socks on her feet, her mouth soft.'
  const complaints = (line, framing) => problemsWith(line, '', 400, framing).join(' ')

  test('the dealt framing passes', () => {
    expect(complaints(LINE, FRAMING)).toBe('')
  })

  test('a framing the line chose for itself is refused', () => {
    const chosen = LINE.replace('a full-length photograph, head to feet',
                                'a three-quarter photograph from the knees up')
    expect(complaints(chosen, FRAMING)).toContain('not the one this shoot was dealt')
  })

  test('and with nothing dealt the check is the one it always was', () => {
    const chosen = LINE.replace('a full-length photograph, head to feet',
                                'a three-quarter photograph from the knees up')
    expect(complaints(chosen, null)).toBe('')
    expect(complaints(LINE.replace('a full-length photograph, head to feet, ', ''), null))
      .toContain('does not say its framing')
  })
})

/** The repair does not fix a framing the writer picked for itself: measured, it
 *  accepted none of four. The swap is deterministic instead, and it only ever
 *  writes the one framing the shoot has - tightening a line would mean deleting
 *  the garments below the waist. */
describe('the framing put back by the code', () => {
  const line = 'Taken from her right side, a waist-up photograph, her chest bare, '
             + 'the denim on her hips and white socks on her feet.'

  test('a framing the line chose is rewritten to the one dealt', () => {
    expect(withDealtFraming(line, FRAMING))
      .toContain('a full-length photograph, head to feet, her chest bare')
    expect(withDealtFraming(line, FRAMING)).not.toContain('waist-up')
  })

  test('a line with no framing clause at all is left alone to be flagged', () => {
    const bare = 'Taken from her right side, her chest bare and her feet on the carpet.'
    expect(withDealtFraming(bare, FRAMING)).toBe(bare)
  })

  test('and nothing is rewritten when nothing was dealt', () => {
    expect(withDealtFraming(line, null)).toBe(line)
  })
})
