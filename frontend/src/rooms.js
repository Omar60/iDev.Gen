// A room is a place, and a manner is a register. The look is the two of them.
//
// Before this they were one string per room, which meant the nine candid rooms
// each carried their own copy of candid's capture clause and every imported
// room would have had to be handed one. `ModelDetail.jsx` recorded the cost of
// that fusion: a room could only be offered under the manner whose register was
// baked into it, so candid's bedrooms were hidden from a directed shoot for a
// reason that was about the first sentence and not about the bedroom.
import registers from '../../data/manner-registers-seed.json'
// This project's own measurements, tracked, keyed by room key and then by
// the manner the run was shot under. The rooms it measures may be imported
// and untracked; the measurements are ours and are not.
import verdicts from '../../data/room-verdicts-seed.json'

/** The register a manner speaks in, or '' for a manner nobody has written one
 *  for. Empty is not an error: `composeLook` then hands back the place alone,
 *  which is a room with no register rather than a room with the wrong one. */
export function registerFor(manner) {
  return registers[manner] ?? ''
}

/** Is this room allowed under this manner.
 *
 *  `manners` is a RESTRICTION and not a membership: an empty or absent list is
 *  every manner, which is what an imported room carries and what the nine
 *  rooms carry since the register left their text. Storing the manners of the
 *  day instead would read the same now and quietly exclude every existing room
 *  from the fourth manner the day somebody writes one.
 *
 *  What the field means changed with the split. It used to say which register
 *  was fused into the room's text; it now says whether the PLACE makes sense
 *  under the manner at all, which is why only the studio carries one - a room
 *  that is itself a photographic set-up has nobody in it under a manner where
 *  nobody is photographing her.
 */
export function roomAllows(room, manner) {
  const allowed = room?.manners
  return !Array.isArray(allowed) || allowed.length === 0 || allowed.includes(manner)
}

/** The look a room composes to under a manner: the register, then the place.
 *  Either half may be absent, and the result never carries a leading or
 *  doubled space. Nothing here edits the place - the room text is stored as it
 *  was written and reaches the textarea unchanged. */
export function composeLook(manner, place) {
  return [registerFor(manner), (place ?? '').trim()].filter(Boolean).join(' ')
}

/** The look the picker fills when a room is chosen, or null when nothing was
 *  chosen. Null is not an error and it is not an empty look: the picker's first
 *  option is "start from a measured room", and choosing it back has to leave a
 *  look somebody typed exactly as they typed it. So the caller writes the look
 *  only when this returns a string, and the one branch that decides whether a
 *  hand-written look is overwritten is here rather than inline in an onChange,
 *  where nothing could reach it. */
export function lookFromRoom(manner, rooms, key) {
  const room = (rooms ?? []).find((r) => r.key === key)
  return room ? composeLook(manner, room.place) : null
}

/** The register a look opens with when it is not the session's own, and the
 *  same look wearing the session's instead. Null when the look opens with the
 *  right register, or with none at all - a look somebody typed is not wrong,
 *  it is theirs, and there is nothing to offer them.
 *
 *  Nothing here rewrites anything. It returns the offer and the caller writes
 *  it only if the operator asks, because a manner change that silently rewrote
 *  the look would throw away an edit made after the room was picked - and the
 *  place is the half we are told never to edit. What is swapped is the
 *  register alone: everything after it survives character for character,
 *  including whatever was typed into it since.
 */
export function refillLook(manner, look) {
  const text = look ?? ''
  const carries = Object.keys(registers).find(
    (m) => m !== manner && registers[m] && text.startsWith(registers[m]))
  if (!carries) return null
  return { carries, look: composeLook(manner, text.slice(registers[carries].length)) }
}

/** What this project measured about this room under THIS manner, and never
 *  under another one. A room verified under directed says nothing about candid:
 *  the register, the framing and half the catalogue differ, so presenting one
 *  verdict as if it covered every manner the room is allowed in is how 428
 *  unmeasured pairings would read as measured.
 *
 *  Absence is the answer for most rooms and it is not a gap: `unknown` at a
 *  sample size of nobody-shot-it is what the store not carrying a record MEANS,
 *  and writing that record out per room per manner would be a file of zeroes.
 *
 *  Two sources, one for each half of the picker. A served room arrives with its
 *  verdicts joined on by `/api/rooms`; a tracked room is in the bundle and its
 *  verdicts are read from the same tracked file the route reads. They cannot
 *  disagree - it is one file - and the served copy is preferred only because a
 *  room the route served is the row the route also measured.
 */
export function verdictFor(room, manner) {
  const stored = room?.verdicts?.[manner] ?? verdicts[room?.key]?.[manner]
  return {
    verdict: stored?.verdict ?? 'unknown',
    sample_size: stored?.sample_size ?? 0,
    note: stored?.note ?? '',
  }
}

/** The verdict as the picker says it, in words rather than in a colour: a
 *  select holds text and nothing else, and the operator has to be able to tell
 *  a measured room from an unmeasured one while choosing between them.
 *
 *  The sample size is always shown with the word, because "verified" alone is
 *  the free text this store replaced - it says nothing until it says out of how
 *  many. An unmeasured room says so in plain words instead of showing
 *  `unknown`, which reads as a fault in the app rather than as a room nobody
 *  has shot yet.
 */
export function verdictLabel(room, manner) {
  const { verdict, sample_size: n } = verdictFor(room, manner)
  if (verdict === 'unknown') return n ? `not measured yet, ${n} so far` : 'not measured yet'
  return `${verdict}, ${n} judged`
}

/** One room as the picker lists it: what it is, what it offers, and what it
 *  measured under the manner being written. */
export function roomOption(room, manner) {
  const offers = room?.offers?.length ? ` - offers ${room.offers.join(', ')}` : ''
  return `${room?.label ?? ''}${offers} - ${verdictLabel(room, manner)}`
}

/** Every tag the listed rooms carry, once each, in alphabetical order.
 *
 *  The filter's options are read off the rooms rather than written down here:
 *  the vocabulary is 239 tags long, it belongs to the source, and a list in the
 *  frontend would be a second copy of it that goes stale on the next import.
 *  A library nobody imported contributes no tags, which is why an empty result
 *  is the ordinary state of a fresh clone and not a fault.
 */
export function allTags(rooms) {
  const seen = new Set()
  for (const room of rooms ?? []) for (const tag of room?.tags ?? []) seen.add(tag)
  return [...seen].sort()
}

/** Does this room carry this tag. No tag selected matches every room - the
 *  filter is a narrowing, and "all" is where it starts. */
export function hasTag(room, tag) {
  return !tag || (room?.tags ?? []).includes(tag)
}

/** The rooms the picker lists: the tracked ones the build carries, then every
 *  room the route served that the build does not already have.
 *
 *  Deduplicated by key on purpose. `/api/rooms` reads the room library
 *  registry, and the registry's default entry is the nine tracked rooms
 *  themselves - so the route hands back rooms the bundle already has, and
 *  without this the picker lists every one of them twice. The tracked copy
 *  wins because it is the one the build was tested against.
 *
 *  An empty or absent payload is not an error: the imported seeds are
 *  untracked, so a clone where nobody ran the import serves nothing, and the
 *  picker is then the nine rooms it was before any of this.
 */
export function pickerRooms(builtIn, served) {
  const rooms = [...(builtIn ?? [])]
  const have = new Set(rooms.map((r) => r.key))
  for (const room of served ?? []) {
    if (room && !have.has(room.key)) {
      have.add(room.key)
      rooms.push(room)
    }
  }
  return rooms
}
