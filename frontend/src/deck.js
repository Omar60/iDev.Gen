// The order a slideshow plays its photographs in.
//
// Its own module, away from the screen that renders it, for the same reason
// canvas.js is: it is pure, it has no React in it, and it holds the two rules
// that are invisible in review and only show up as "the slideshow feels
// broken" — no photograph twice before every one has been shown, and never the
// same frame twice in a row. deck.test.js is what keeps both honest.

/** Fisher-Yates shuffle on a copy.
 *
 *  `sort(() => Math.random() - 0.5)` is the shuffle everyone writes and it is
 *  not uniform: the bias is invisible on a large set and pronounced on the
 *  thirteen photographs a four-star threshold actually returns today, which is
 *  exactly the size this runs at. */
export function shuffle(arr) {
  const a = arr.slice()
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[a[i], a[j]] = [a[j], a[i]]
  }
  return a
}

/** The order for the next pass, given the id of the photograph the last pass
 *  ended on.
 *
 *  A plain reshuffle satisfies "every photograph before any repeat" and still
 *  shows the same frame twice in a row one pass in N, because the new deck's
 *  first card can be the old deck's last. At thirteen photographs that lands
 *  often enough to read as the bug this deck exists to avoid, so the seam gets
 *  the one swap that closes it. */
export function reshuffle(deck, lastId) {
  if (deck.length < 2) return deck
  const a = shuffle(deck)
  if (a[0].id === lastId) {
    const j = 1 + Math.floor(Math.random() * (a.length - 1))
    ;[a[0], a[j]] = [a[j], a[0]]
  }
  return a
}

/** One step of the walk: the whole transition, deck and index together.
 *
 *  They are one value because they change together — walking off the end draws
 *  a new order AND returns to zero — and splitting that across two React
 *  setters is what forces a `setDeck` call inside a `setIndex` updater, a side
 *  effect in a function React is allowed to run more than once. As one
 *  transition it is a plain function, which is also what makes it testable
 *  without rendering anything. */
export function nextPlay({ deck, index }) {
  const next = index + 1
  if (next < deck.length) return { deck, index: next }
  return { deck: reshuffle(deck, deck[index]?.id), index: 0 }
}
