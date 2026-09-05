import { describe, it, expect } from 'vitest'
import {
  allTags, composeLook, drawRoom, filterRooms, guidanceLines, hasTag, lookFromRoom,
  matchesText, openingLook, pickRoom, pickerRooms, refillLook, registerFor,
  roomAllows, roomOption, verdictFor, verdictLabel,
} from './rooms.js'
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

// 6.11 and 6.12: allowed is not measured. A room may be offered under every
// manner and measured under one, and the picker has to say which.
describe('what the picker says a room measured', () => {
  const studio = directedLooks[0]
  const bedroom = candidRooms[0]

  it('reads a verdict only under the manner it was taken in', () => {
    expect(verdictFor(studio, 'directed')).toMatchObject({ verdict: 'verified', sample_size: 10 })
    // Same room, another manner. Not verified, and not because somebody wrote
    // "unknown" down for it - because nobody has shot it there.
    expect(verdictFor(studio, 'candid').verdict).toBe('unknown')
    expect(verdictFor(studio, 'selfie').verdict).toBe('unknown')
    // A room nobody has measured at all, and a room that does not exist.
    expect(verdictFor({ key: 'gs-stockroom-01' }, 'candid'))
      .toEqual({ verdict: 'unknown', sample_size: 0, note: '' })
    expect(verdictFor(undefined, 'candid').verdict).toBe('unknown')
  })

  // A served room carries its verdicts joined on by the route; a tracked room
  // is read from the same tracked file. One file, so they cannot disagree.
  it('reads the verdicts the route joined on to an imported room', () => {
    const served = { key: 'gs-stockroom-01', label: 'Storeroom',
                     verdicts: { candid: { verdict: 'dead', sample_size: 12 } } }
    expect(verdictLabel(served, 'candid')).toBe('dead, 12 judged')
    expect(verdictLabel(served, 'directed')).toBe('not measured yet')
  })

  it('tells a measured room from an unmeasured one in the words it lists', () => {
    expect(verdictLabel(studio, 'directed')).toBe('verified, 10 judged')
    expect(verdictLabel(studio, 'candid')).toBe('not measured yet')
    // The nine were converted at the sample size their sentence stated, so
    // they are unmeasured WITH a count - eight at one frame, the oldest at
    // none. Both say so rather than showing the catalogue's word.
    expect(verdictLabel(candidRooms[1], 'candid')).toBe('not measured yet, 1 so far')
    expect(verdictLabel(bedroom, 'candid')).toBe('not measured yet')
  })

  it('lists what the room is, what it offers and what it measured', () => {
    expect(roomOption(studio, 'directed'))
      .toBe('Studio, one softbox (shipped) - verified, 10 judged')
    expect(roomOption(bedroom, 'candid'))
      .toBe('Bedroom, bare bulb (shipped) - offers bed - not measured yet')
    // The one room the library has measured, listed under the manner it was
    // measured in and under one it was not: same row, two different lines.
    // That difference is the whole of 6.11 seen from the screen.
    expect(roomOption(studio, 'candid'))
      .toBe('Studio, one softbox (shipped) - not measured yet')
    expect(roomOption(studio, 'candid')).not.toBe(roomOption(studio, 'directed'))
  })
})

// 6.13: the tag filter's options are the tags the rooms carry. The vocabulary
// is 239 tags long and belongs to the source, so nothing here writes it down.
describe('the tag filter', () => {
  const IMPORTED = [
    { key: 'gs-stockroom-01', label: 'Stockroom', tags: ['indoor', 'private'] },
    { key: 'gs-alley-02', label: 'Alley', tags: ['outdoor', 'public'] },
    { key: 'gs-corridor-03', label: 'Corridor', tags: ['indoor', 'working'] },
  ]

  it('offers every tag the rooms carry, once each', () => {
    expect(allTags(IMPORTED)).toEqual(['indoor', 'outdoor', 'private', 'public', 'working'])
    // A clone where nobody ran the import lists the nine tracked rooms, which
    // carry no tags: no options, and the picker simply has no filter.
    expect(allTags(candidRooms)).toEqual([])
    expect(allTags([])).toEqual([])
    expect(allTags(undefined)).toEqual([])
  })

  it('narrows the list to the rooms whose source entry carried the tag', () => {
    const listed = (tag) => IMPORTED.filter((r) => hasTag(r, tag)).map((r) => r.key)
    expect(listed('indoor')).toEqual(['gs-stockroom-01', 'gs-corridor-03'])
    expect(listed('public')).toEqual(['gs-alley-02'])
    // A tag used once still finds its room, which is why nothing is
    // thresholded away.
    expect(listed('working')).toEqual(['gs-corridor-03'])
    // No tag chosen is where the filter starts, and it hides nothing - a
    // tagless tracked room included.
    expect(listed('')).toEqual(IMPORTED.map((r) => r.key))
    expect(hasTag(candidRooms[0], '')).toBe(true)
    expect(hasTag(candidRooms[0], 'indoor')).toBe(false)
  })
})

// 6.15: a session can have its room dealt instead of chosen. The weight is the
// source's own, which is the only reason that field was adopted at all.
describe('the room a session is dealt', () => {
  const POOL = [
    { key: 'common', label: 'Common', place: 'a common room', weight: 8 },
    { key: 'rare', label: 'Rare', place: 'a rare room', weight: 2 },
    { key: 'studio', label: 'Studio', place: 'a studio', manners: ['directed'] },
  ]
  // A rand that walks the interval instead of a stubbed constant: a draw that
  // only ever returns the first room passes every single-value test.
  const at = (fraction) => () => fraction

  it('deals by weight, and the weights are the ones stored', () => {
    // Candid allows the two weighted rooms: 8 and 2, so the cut falls in the
    // common room for the first four fifths of the interval.
    expect(drawRoom(POOL, 'candid', at(0)).key).toBe('common')
    expect(drawRoom(POOL, 'candid', at(0.79)).key).toBe('common')
    expect(drawRoom(POOL, 'candid', at(0.81)).key).toBe('rare')
    expect(drawRoom(POOL, 'candid', at(0.999)).key).toBe('rare')
  })

  it('deals only what the manner allows, and a room with no weight draws at one', () => {
    // Under directed the studio joins the pool at a weight of one: 8, 2, 1.
    expect(drawRoom(POOL, 'directed', at(0.95)).key).toBe('studio')
    // Under candid it is not in the pool at all, at any cut.
    for (const cut of [0, 0.5, 0.99]) {
      expect(drawRoom(POOL, 'candid', at(cut)).key).not.toBe('studio')
    }
    // Nothing to deal is null, not a throw and not an empty room: a clone
    // where nobody imported anything opens a session with an empty look.
    expect(drawRoom([], 'candid', at(0))).toBe(null)
    // A manner nobody has written yet still deals the unrestricted rooms,
    // because restricting nothing means restricting nothing.
    expect(drawRoom(POOL, 'a-manner-written-next-year', at(0)).key).toBe('common')
    // Weight zero is a room the picker offers and the draw never deals, which
    // is a real thing to want - and a pool of nothing but those is null.
    expect(drawRoom([{ key: 'never', weight: 0 }], 'candid', at(0))).toBe(null)
  })

  it('fills the look once and never again', () => {
    const draft = { manner: 'candid', look: '' }
    const dealt = openingLook(draft, POOL, at(0))
    expect(dealt.room.key).toBe('common')
    expect(dealt.look).toBe(composeLook('candid', 'a common room'))

    // Reopened, or re-rendered, or looked at a second time: a session that
    // already carries a look is never dealt another one. Both halves matter -
    // the dealt look must not be re-dealt, and a look somebody typed must not
    // be thrown away by a draw that happens to run.
    expect(openingLook({ ...draft, look: dealt.look }, POOL, at(0.99))).toBe(null)
    expect(openingLook({ manner: 'candid', look: 'a look somebody typed' }, POOL, at(0.99)))
      .toBe(null)
    expect(openingLook(null, POOL, at(0))).toBe(null)
    // And a session whose manner allows nothing opens with the empty look it
    // had, rather than with a room from another manner.
    expect(openingLook({ manner: 'candid', look: '' }, [POOL[2]], at(0))).toBe(null)
  })
})

// 6.17 and 6.18: 428 rooms is a list nobody reads, so the picker narrows - and
// whatever it narrows to, the room the session is using stays reachable.
describe('narrowing the room list', () => {
  const ROOMS = [
    { key: 'gs-dressing-01', label: 'Dressing room', tags: ['indoor'],
      place: 'a vanity mirror ringed with bulbs and a velvet stool' },
    { key: 'gs-alley-02', label: 'Alley', tags: ['outdoor'],
      place: 'a wet brick lane behind a kitchen door' },
    { key: 'gs-studio-03', label: 'Studio', tags: ['indoor'], manners: ['directed'],
      place: 'a softbox and a roll of seamless paper' },
  ]
  const listed = (opts) => filterRooms(ROOMS, opts).map((r) => r.key)

  it('finds a room by its label and another by a word inside its prose', () => {
    // The label is a translation somebody wrote; the prose is what the
    // photograph is made of. Both have to be searched, or a room whose
    // sentence has a mirror in it is unfindable under any label.
    expect(listed({ manner: 'candid', text: 'alley' })).toEqual(['gs-alley-02'])
    expect(listed({ manner: 'candid', text: 'mirror' })).toEqual(['gs-dressing-01'])
    expect(listed({ manner: 'candid', text: 'MIRROR' })).toEqual(['gs-dressing-01'])
    expect(matchesText(ROOMS[0], '')).toBe(true)
    expect(matchesText(ROOMS[0], '   ')).toBe(true)
    expect(listed({ manner: 'candid', text: 'nothing here' })).toEqual([])
  })

  it('narrows by tag and by manner at the same time', () => {
    expect(listed({ manner: 'candid', tag: 'indoor' })).toEqual(['gs-dressing-01'])
    expect(listed({ manner: 'directed', tag: 'indoor' }))
      .toEqual(['gs-dressing-01', 'gs-studio-03'])
    expect(listed({ manner: 'candid', tag: 'indoor', text: 'velvet' }))
      .toEqual(['gs-dressing-01'])
  })

  it('keeps the room the session is using, whatever the filter says', () => {
    // Filtered out three ways over - wrong tag, wrong text, and under a manner
    // it is not even allowed in - and still listed, because a select whose
    // value is not among its options renders blank: the screen would say the
    // session has no room while its look is full of one.
    expect(listed({ manner: 'candid', tag: 'outdoor', text: 'nothing here',
                    current: 'gs-studio-03' })).toEqual(['gs-studio-03'])
    expect(listed({ manner: 'candid', text: 'alley', current: 'gs-dressing-01' }))
      .toEqual(['gs-dressing-01', 'gs-alley-02'])
    // And it is listed once, not twice, when it also passes the filter.
    expect(listed({ manner: 'candid', text: 'mirror', current: 'gs-dressing-01' }))
      .toEqual(['gs-dressing-01'])
    // A look somebody typed has no current room and nothing is pinned.
    expect(listed({ manner: 'candid', text: 'alley', current: '' }))
      .toEqual(['gs-alley-02'])
  })
})

// 6.19 and 6.20: what the picker shows beside a room, and what it changes when
// one is chosen.
describe('picking a room', () => {
  const ROOM = {
    key: 'gs-stockroom-01', label: 'Stockroom', manners: [],
    place: 'steel shelving and a bare bulb overhead',
    guidance: {
      action_anchor: 'she is crouching to restock, not posing',
      mood: ['tender', 'hushed'],
      notes: 'no crystal sparkle',
      empty_anchor: '   ',
    },
  }

  it('reads the room the entry author wrote it for, and composes none of it', () => {
    expect(guidanceLines(ROOM)).toEqual([
      { field: 'action_anchor', text: 'she is crouching to restock, not posing' },
      { field: 'mood', text: 'tender, hushed' },
      { field: 'notes', text: 'no crystal sparkle' },
    ])
    // The field names are the row's own: which slots count as guidance is the
    // backend's rule, and a copy of it here would be free to disagree.
    // A room with none, and a row from before the field existed, both read as
    // nothing to show rather than as an empty list on the screen.
    expect(guidanceLines({ key: 'bare' })).toEqual([])
    expect(guidanceLines(undefined)).toEqual([])
    // And none of it is in the look the same room composes, in either manner.
    for (const manner of ['candid', 'directed']) {
      const look = composeLook(manner, ROOM.place)
      for (const word of ['crouching', 'sparkle', 'tender', 'hushed']) {
        expect(look).not.toContain(word)
      }
    }
  })

  it('fills the look and touches nothing else', () => {
    const session = {
      manner: 'candid', look: '', wardrobe: 'a grey cotton dress',
      shots: [{ prompt: 'one' }], settings: { lora_strength: 0.8 },
      name: 'Session 4', seed: 0,
    }
    const after = pickRoom(session, [ROOM], 'gs-stockroom-01')
    expect(after.look).toBe(composeLook('candid', ROOM.place))
    // The same objects, not equal copies: an edit that rebuilt the shot list
    // would pass a deep comparison and reset the shoot somebody was writing.
    expect(after.wardrobe).toBe(session.wardrobe)
    expect(after.shots).toBe(session.shots)
    expect(after.settings).toBe(session.settings)
    expect(after.name).toBe(session.name)
    expect(after.seed).toBe(session.seed)
    expect(Object.keys(after).sort()).toEqual(Object.keys(session).sort())
    // The session it was asked about is not mutated on the way.
    expect(session.look).toBe('')

    // Choosing the first option back is not an empty room: a look somebody
    // typed comes back as the very same object, untouched.
    const typed = { ...session, look: 'a look somebody typed' }
    expect(pickRoom(typed, [ROOM], '')).toBe(typed)
    expect(pickRoom(typed, [ROOM], 'a-key-no-room-carries')).toBe(typed)
  })
})
