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

/** The room's authoring guidance, ready to read: one line per source field,
 *  under the source's own field name.
 *
 *  It is READ and never composed. The entry author wrote it for whoever picks
 *  or edits the room - what the subject is doing and why, what breaks the shot,
 *  the mood words - and "avoid crystal sparkle" is an instruction to a human
 *  and a description of glitter to a sampler. Keeping it out of the line is
 *  structural: `composeLook` joins the register to the place and reads nothing
 *  else, so there is no filter here anybody can forget to apply.
 *
 *  The field names come from the row, not from a list here: which slots count
 *  as guidance is `is_guidance_field`'s decision on the backend, and a second
 *  copy of that rule in the frontend would be free to disagree with it.
 */
export function guidanceLines(room) {
  return Object.entries(room?.guidance ?? {})
    .map(([field, value]) => ({
      field,
      text: Array.isArray(value) ? value.join(', ') : String(value ?? ''),
    }))
    .filter((line) => line.text.trim())
}

/** The session after a room is picked: the same session with its look filled,
 *  and NOTHING else touched.
 *
 *  Returned as a new object rather than mutated, and returned AS IT WAS when
 *  nothing was chosen - the picker's first option is "start from a measured
 *  room", and choosing it back has to leave a look somebody typed exactly as
 *  they typed it.
 *
 *  A function rather than a spread inside the onChange because that is what
 *  makes "only the look" assertable: the wardrobe, the shots and the settings
 *  come back as the same objects, not as equal copies, so an edit that ever
 *  rebuilt one of them is caught here rather than by somebody noticing their
 *  shot list reset.
 */
export function pickRoom(session, rooms, key) {
  const look = lookFromRoom(session?.manner, rooms, key)
  return look === null ? session : { ...session, look }
}

/** What the picker does with the value the select just handed it: the key the
 *  session records, and the session that goes with it.
 *
 *  Three transitions, one function, because the third is the one that gets
 *  written wrong. Picking a room fills the look and records the key. Picking
 *  another replaces both. Choosing the empty option DETACHES: the key clears
 *  and the session comes back as the very same object, because the words are
 *  the operator's from the moment the room filled them and a detach that also
 *  clears the sentence is unrecoverable once the screen is left.
 *
 *  A key no room carries is refused outright - neither half moves. That is the
 *  stale-select case: a library unregistered while the form was open would
 *  otherwise record a key whose room nobody can look up, over a look it never
 *  wrote.
 */
export function roomChoice(session, rooms, key) {
  if (!key) return { key: '', session }
  const picked = pickRoom(session, rooms, key)
  return picked === session ? null : { key, session: picked }
}

/** Does this room match what was typed into the filter box.
 *
 *  Matched against the label AND the room's own prose, because the two answer
 *  different questions: the label is a translation somebody wrote and is what
 *  the operator reads in the list, while the prose is the English text the
 *  photograph is actually made of. Typing "mirror" has to find the room whose
 *  sentence has a mirror in it even when its label is "Dressing room".
 *
 *  Not matched against the tags - they have their own filter - and not against
 *  the guidance, which is advice about the room and not a description of it.
 */
export function matchesText(room, text) {
  const needle = (text ?? '').trim().toLowerCase()
  if (!needle) return true
  return `${room?.label ?? ''} ${room?.place ?? ''}`.toLowerCase().includes(needle)
}

/** The rooms the picker shows: allowed under this manner, matching the tag and
 *  the text, and ALWAYS the one the session is currently using.
 *
 *  That last clause is the whole of 6.18. A filter that hides the current room
 *  leaves a select whose value is not among its options, which browsers render
 *  as blank - so the screen would say the session has no room while its look is
 *  full of one, and picking anything else would be the only way out.
 */
export function filterRooms(rooms, { manner, tag, text, current } = {}) {
  return (rooms ?? []).filter((room) => (
    (current && room?.key === current)
    || (roomAllows(room, manner) && hasTag(room, tag) && matchesText(room, text))
  ))
}

/** One room, drawn by weight, from the rooms this manner allows.
 *
 *  The weight is the source's own and it is the reason the field was adopted:
 *  its libraries deal a scene by weight, and a room nobody weighted draws at
 *  one. A weight of zero is a room that is offered in the picker and never
 *  dealt, which is a real thing to want and the reason the total is summed
 *  rather than the count used.
 *
 *  Null when the manner allows nothing - a clone with no import and a manner
 *  no tracked room allows - and the caller then opens the session with an empty
 *  look, which is what it did before there was a draw at all.
 *
 *  ponytail: the library weight in the registry is NOT multiplied in here. The
 *  route serves rooms flattened, so this reads the room's own weight only; if
 *  weighting a whole library up ever matters, it is the route that has to carry
 *  the library weight down onto the row.
 */
export function drawRoom(rooms, manner, rand = Math.random) {
  const pool = (rooms ?? []).filter((r) => roomAllows(r, manner))
  const weight = (r) => (Number.isFinite(r?.weight) ? Math.max(0, r.weight) : 1)
  const total = pool.reduce((sum, r) => sum + weight(r), 0)
  if (!pool.length || total <= 0) return null
  let cut = rand() * total
  for (const room of pool) {
    cut -= weight(room)
    if (cut < 0) return room
  }
  return pool[pool.length - 1]
}

/** The look a session OPENS with, drawn once, or null to leave it alone.
 *
 *  Null whenever the session already carries a look, which is what makes the
 *  draw a thing that happens at creation rather than every time a session is
 *  looked at: a drawn room is an ordinary default the operator can replace, and
 *  a second draw on reopening would throw away both the replacement and the
 *  original.
 */
export function openingLook(session, rooms, rand = Math.random) {
  if (!session || (session.look ?? '').trim()) return null
  const room = drawRoom(rooms, session.manner, rand)
  return room ? { room, look: composeLook(session.manner, room.place) } : null
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
