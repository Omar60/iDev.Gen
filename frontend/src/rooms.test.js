import { describe, it, expect } from 'vitest'
import { composeLook, lookFromRoom, pickerRooms, refillLook, registerFor, roomAllows } from './rooms.js'
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

describe('filling the look from the picker', () => {
  const ROOMS = [
    { key: 'stockroom', manner: 'candid', label: 'Stockroom',
      place: 'stockroom with a scarred workbench under a bare bulb' },
  ]

  // 6.2: the same room under two manners is one place read in two voices. The
  // weaker version of this - that the two differ - passes on a compose that
  // reworded the place as well, which is the thing the split exists to prevent.
  it('differs only in the register when the manner changes', () => {
    const candid = lookFromRoom('candid', ROOMS, 'stockroom')
    const directed = lookFromRoom('directed', ROOMS, 'stockroom')
    expect(candid).not.toBe(directed)
    expect(candid.slice(registerFor('candid').length + 1))
      .toBe(directed.slice(registerFor('directed').length + 1))
    expect(candid.slice(registerFor('candid').length + 1)).toBe(ROOMS[0].place)
  })

  // The other half, and the one with a hand-written look at stake: choosing the
  // picker's own first option, or a key no room carries, writes nothing. Null
  // is what the caller checks, and it is not the same as an empty look - an
  // empty look would clear the textarea somebody had just typed into.
  it('fills nothing when no room was chosen', () => {
    expect(lookFromRoom('candid', ROOMS, '')).toBe(null)
    expect(lookFromRoom('candid', ROOMS, 'no-such-room')).toBe(null)
    expect(lookFromRoom('candid', [], 'stockroom')).toBe(null)
    expect(lookFromRoom('candid', undefined, 'stockroom')).toBe(null)
  })

  // A hand-written look never passes through here, so it never gains a
  // register: the only text this function can produce is a room's.
  it('never puts a register on text it was not given as a room', () => {
    const typed = 'on a beach at golden hour'
    expect(lookFromRoom('candid', ROOMS, typed)).toBe(null)
    expect(lookFromRoom('candid', ROOMS, 'stockroom')).not.toContain(typed)
  })
})

describe('a look wearing another manner register', () => {
  const PLACE = 'stockroom with a scarred workbench under a bare bulb'

  // 6.3: the manner is changed after the look was filled, which is the ordinary
  // order - the room picker is above the manner select on the screen. The look
  // then opens with the wrong register and nobody is told.
  it('names the register the look carries and offers the session own', () => {
    const filled = composeLook('candid', PLACE)
    const offer = refillLook('directed', filled)
    expect(offer.carries).toBe('candid')
    expect(offer.look).toBe(composeLook('directed', PLACE))
  })

  // The half this task is actually about. Everything after the register is the
  // operator text - the room prose plus whatever was typed into it since - and
  // it survives character for character. A refill that recomposed from the room
  // would pass the assertion above and drop the edit.
  it('keeps an edit made after the room was picked', () => {
    const edited = composeLook('candid', PLACE) + ' and her coat is over the chair'
    const offer = refillLook('directed', edited)
    expect(offer.look.slice(registerFor('directed').length + 1))
      .toBe(PLACE + ' and her coat is over the chair')
  })

  // And it offers nothing when there is nothing to offer: a look already in the
  // session own register, a look somebody typed with no register at all, and an
  // empty one. An offer here would be a screen telling the operator their own
  // sentence is wrong.
  it('offers nothing when the look carries no foreign register', () => {
    expect(refillLook('candid', composeLook('candid', PLACE))).toBe(null)
    expect(refillLook('candid', 'on a beach at golden hour')).toBe(null)
    expect(refillLook('candid', '')).toBe(null)
    expect(refillLook('candid', undefined)).toBe(null)
  })

  // Nothing is rewritten by asking. The offer is a value the caller may write,
  // and the look it was asked about is untouched.
  it('rewrites nothing on its own', () => {
    const filled = composeLook('candid', PLACE)
    const before = String(filled)
    refillLook('directed', filled)
    expect(filled).toBe(before)
  })
})

describe('the rooms the picker lists', () => {
  const BUILT_IN = [
    { key: 'bedroom-night', manner: 'candid', label: 'Bedroom', place: 'a bedroom' },
  ]
  const SERVED = [
    { key: 'gs-stockroom-01', manner: 'candid', label: 'Stockroom', place: 'a stockroom' },
  ]

  // 6.4: the imported seeds are untracked, so a clone where nobody ran the
  // import serves nothing. The picker is then the rooms the build carries, and
  // an absent library is a reason rather than a broken screen.
  it('is the built-in rooms when the route serves nothing', () => {
    expect(pickerRooms(BUILT_IN, [])).toEqual(BUILT_IN)
    expect(pickerRooms(BUILT_IN, undefined)).toEqual(BUILT_IN)
    expect(pickerRooms(BUILT_IN, null)).toEqual(BUILT_IN)
  })

  it('appends what the route served', () => {
    expect(pickerRooms(BUILT_IN, SERVED).map((r) => r.key))
      .toEqual(['bedroom-night', 'gs-stockroom-01'])
  })

  // The route reads the room library registry, whose default entry is the nine
  // tracked rooms themselves - so it hands back rooms the bundle already has.
  // Without the dedup the picker lists every one of them twice.
  it('lists a room once when the route also serves the tracked copy', () => {
    const alsoServed = [{ key: 'bedroom-night', manner: 'candid', label: 'Bedroom', place: 'a bedroom' }]
    const listed = pickerRooms(BUILT_IN, [...alsoServed, ...SERVED])
    expect(listed.map((r) => r.key)).toEqual(['bedroom-night', 'gs-stockroom-01'])
    // The tracked copy wins: it is the one the build was tested against.
    expect(listed[0]).toBe(BUILT_IN[0])
  })
})

// 6.7 and 6.8: the picker filters by what a room ALLOWS, not by what it equals.
// Before the split a room's `manner` said which register was baked into its
// text, so candid's bedroom could not be offered to a directed shoot - for a
// reason that was about the first sentence and not about the bedroom.
describe('the manners a room allows', () => {
  const MANNERS = ['candid', 'directed', 'selfie']
  const bedroom = candidRooms[0]
  const studio = directedLooks[0]

  it('allows every manner when the room restricts none', () => {
    expect(bedroom.manners).toEqual([])
    for (const manner of MANNERS) expect(roomAllows(bedroom, manner)).toBe(true)
    // Empty is a restriction nobody wrote, not the list of today's manners: a
    // manner written next year is allowed by it too, which is the whole reason
    // the field is stored empty rather than filled in with the three.
    expect(roomAllows(bedroom, 'a-manner-written-next-year')).toBe(true)
    // An imported room arrives the same way, and so does a row from before the
    // field existed.
    expect(roomAllows({ key: 'gs-stockroom-01', manners: [] }, 'directed')).toBe(true)
    expect(roomAllows({ key: 'no-field-at-all' }, 'directed')).toBe(true)
  })

  it('allows the studio only where somebody is photographing her', () => {
    expect(studio.manners).toEqual(['directed'])
    expect(studio.manners_reason).toBeTruthy()
    expect(roomAllows(studio, 'directed')).toBe(true)
    expect(roomAllows(studio, 'candid')).toBe(false)
    expect(roomAllows(studio, 'selfie')).toBe(false)
  })

  // What the picker itself does with the two: the shipped ten under each
  // manner. The bedroom is listed under all three and the studio under one.
  it('lists the unrestricted rooms under every manner and the studio under one', () => {
    const shipped = pickerRooms(candidRooms, directedLooks)
    for (const manner of MANNERS) {
      const listed = shipped.filter((r) => roomAllows(r, manner)).map((r) => r.key)
      expect(listed).toContain(bedroom.key)
      expect(listed.includes(studio.key)).toBe(manner === 'directed')
      expect(listed.length).toBe(manner === 'directed' ? 10 : 9)
    }
  })
})
