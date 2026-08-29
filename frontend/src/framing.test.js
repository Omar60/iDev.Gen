import { describe, expect, test } from 'vitest'
import { FRAMING, CLOSE_FRAMING, KISS_FRAMES, FRAMING_SLIPS, MANNER,
         cameraPlan, shootChunkNote } from './kinds.js'
import { problemsWith, withDealtFraming, framingFor } from './enhance.js'

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

/** The two photographs that are not full-length. Both are exceptions the code
 *  knows about before the line exists, and both were being written over by the
 *  swap until this was checked. */
describe('the framings the deal does not touch', () => {
  test('a kiss frame and an explicit photograph are dealt the close framing', () => {
    expect(framingFor(FRAMING)).toBe(FRAMING)
    expect(framingFor(FRAMING, { kiss: true })).toBe(CLOSE_FRAMING)
    expect(framingFor(FRAMING, { explicit: true })).toBe(CLOSE_FRAMING)
    // A manner with no camera catalogue is dealt no framing at all, and the
    // exception cannot invent one.
    expect(framingFor(null, { explicit: true })).toBe(null)
  })

  const PAIR = 'Taken from their side, a waist-up photograph, a naked man standing behind her '
             + 'and penetrating her from behind, his penis inside her, two people in frame.'

  test('a two-person line keeps the tighter framing it was told to use', () => {
    // Full-length does not come back in a two-person frame: measured thirteen
    // times without one. Writing it over the line is the code overruling that.
    expect(withDealtFraming(PAIR, FRAMING)).toBe(PAIR)
    expect(problemsWith(PAIR, '', 400, FRAMING).join(' '))
      .not.toContain('not the one this shoot was dealt')
  })

  test('a kiss photograph is given no `frame:` row, because its own paragraph has one', () => {
    const kisses = [{ at: 3, frame: KISS_FRAMES[0], camera: 'Taken from directly in front of her' }]
    const note = shootChunkNote({ from: 3, want: 2, total: 8, framing: FRAMING, kisses,
                                  cameras: ['Taken from directly in front of her',
                                            'Taken from her right side'] })
    expect(note).not.toContain('3 | frame:')
    expect(note).toContain('4 | frame: a full-length photograph, head to feet')
    expect(note).toContain(CLOSE_FRAMING.text)
  })
})

/** The careless framing candid exists for. It survived being dealt a camera only
 *  because it is dealt too: measured 2026-08-29 at n=12, four runs a side, it fell
 *  from 10.0 lines to 2.5 when the candid cameras were imported and came back at
 *  11.5 when it got a row of its own. */
describe('the careless framing, dealt', () => {
  test('candid carries the slips and directed does not', () => {
    expect(MANNER.candid.slips).toBe(FRAMING_SLIPS)
    expect(MANNER.directed.slips).toBeUndefined()
  })

  test('the spreader never opens two photographs running on the same slip', () => {
    const family = (text) => FRAMING_SLIPS.find((s) => s.wordings[0].text === text).wordings[0].family
    for (let run = 0; run < 20; run += 1) {
      const plan = cameraPlan(24, Math.random, FRAMING_SLIPS).map(family)
      expect(plan.slice(1).some((f, i) => f === plan[i])).toBe(false)
    }
  })

  test('it rides on the camera row and skips the kiss frame', () => {
    const kisses = [{ at: 2, frame: KISS_FRAMES[0], camera: 'Taken from directly in front of her' }]
    const note = shootChunkNote({ from: 1, want: 2, total: 8, framing: FRAMING, kisses,
                                  cameras: ['Phone propped on a high shelf across the room',
                                            'Taken from directly in front of her'],
                                  slips: [FRAMING_SLIPS[1].wordings[0].text,
                                          FRAMING_SLIPS[2].wordings[0].text] })
    expect(note).toContain('1 | careless: the horizon is tilted a few degrees')
    expect(note).not.toContain('2 | careless:')
    expect(note).toContain('IT GOES IN THE `camera` FIELD')
  })
})
