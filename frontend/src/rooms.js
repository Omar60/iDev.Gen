// A room is a place, and a manner is a register. The look is the two of them.
//
// Before this they were one string per room, which meant the nine candid rooms
// each carried their own copy of candid's capture clause and every imported
// room would have had to be handed one. `ModelDetail.jsx` recorded the cost of
// that fusion: a room could only be offered under the manner whose register was
// baked into it, so candid's bedrooms were hidden from a directed shoot for a
// reason that was about the first sentence and not about the bedroom.
import registers from '../../data/manner-registers-seed.json'

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
