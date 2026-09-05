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
