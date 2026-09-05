import { describe, it, expect } from 'vitest'
import { composeLook, registerFor } from './rooms.js'
import candidRooms from '../../data/candid-rooms-seed.json'
import directedLooks from '../../data/directed-looks-seed.json'

describe('composeLook', () => {
  it('puts the manner register in front of the room place', () => {
    expect(composeLook('candid', 'A kitchen.')).toBe(`${registerFor('candid')} A kitchen.`)
    expect(composeLook('directed', 'A kitchen.')).toBe(`${registerFor('directed')} A kitchen.`)
  })

  // The reason the split exists: the same place reads in either voice, and the
  // register is the only thing that differs. Before this, a room's manner was
  // the register baked into its text and the two could not come apart.
  it('gives one place a different look under each manner', () => {
    const place = candidRooms[0].place
    expect(composeLook('candid', place)).not.toBe(composeLook('directed', place))
    expect(composeLook('candid', place).endsWith(place)).toBe(true)
    expect(composeLook('directed', place).endsWith(place)).toBe(true)
  })

  // Empty is not an error on either side: a manner nobody wrote a register for
  // hands back the place alone, which is a room with no register rather than a
  // room wearing the wrong one, and neither case leaks a stray space.
  it('never composes a leading or doubled space', () => {
    expect(composeLook('nobody-wrote-this', 'A kitchen.')).toBe('A kitchen.')
    expect(composeLook('candid', '')).toBe(registerFor('candid'))
    expect(composeLook('candid', '   ')).toBe(registerFor('candid'))
    expect(composeLook('nobody-wrote-this', '')).toBe('')
  })

  it('leaves every shipped room a place with no register of its own', () => {
    for (const room of [...candidRooms, ...directedLooks]) {
      expect(room.look).toBeUndefined()
      expect(room.place).toBeTruthy()
      expect(room.place).not.toContain('She wears her hair loose')
    }
  })
})
