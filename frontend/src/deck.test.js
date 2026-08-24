import { describe, it, expect } from 'vitest'
import { shuffle, reshuffle, nextPlay } from './deck'

// Thirteen is not an arbitrary size: it is what a four-star threshold returns
// on a real database, and it is small enough that both defects these tests
// guard against are visible within a minute of watching. A test at n=1000 would
// pass on the broken code.
const N = 13
const makeDeck = () => Array.from({ length: N }, (_, i) => ({ id: i + 1 }))
const ids = (deck) => deck.map((p) => p.id)

/** Walk the deck for `steps` advances, returning the id shown at each one. */
function play(steps, deck = shuffle(makeDeck())) {
  const seen = []
  let state = { deck, index: 0 }
  for (let i = 0; i < steps; i++) {
    seen.push(state.deck[state.index].id)
    state = nextPlay(state)
  }
  return seen
}

describe('shuffle', () => {
  it('keeps every photograph exactly once', () => {
    for (let t = 0; t < 500; t++) {
      const out = ids(shuffle(makeDeck())).sort((a, b) => a - b)
      expect(out).toEqual(ids(makeDeck()))
    }
  })

  it('does not return the input array', () => {
    const deck = makeDeck()
    expect(shuffle(deck)).not.toBe(deck)
    expect(ids(deck)).toEqual(ids(makeDeck()))   // input untouched
  })

  it('reaches more than one order', () => {
    // Guards the degenerate "shuffle" that returns its input: a single order
    // over 200 draws of thirteen is not luck.
    const orders = new Set()
    for (let t = 0; t < 200; t++) orders.add(ids(shuffle(makeDeck())).join(','))
    expect(orders.size).toBeGreaterThan(1)
  })

  it('is uniform enough that no photograph favours its starting position', () => {
    // The guard against going back to `sort(() => Math.random() - 0.5)`. That
    // shuffle is a valid permutation, so every other test here passes on it —
    // only its distribution gives it away. It leaves elements near where they
    // started: measured over 20000 draws of thirteen, its worst position holds
    // on 16.0% of the time against a fair 7.7%, while Fisher-Yates peaks at
    // 8.0%. The 11% bar sits far outside the noise of a fair shuffle (one
    // position's rate has a standard deviation of 0.19 points here, so 11% is
    // seventeen of them away) and far below what the biased sort produces.
    const T = 20000
    const stay = new Array(N).fill(0)
    for (let t = 0; t < T; t++) {
      const out = shuffle(makeDeck())
      for (let i = 0; i < N; i++) if (out[i].id === i + 1) stay[i]++
    }
    const worst = Math.max(...stay) / T
    expect(worst).toBeLessThan(0.11)
  })
})

describe('reshuffle', () => {
  it('never puts the photograph that just played first', () => {
    // The seam. Without the swap this fails within a few dozen draws: the odds
    // are one in thirteen per pass.
    const deck = makeDeck()
    for (let t = 0; t < 5000; t++) {
      const lastId = deck[Math.floor(Math.random() * N)].id
      expect(reshuffle(deck, lastId)[0].id).not.toBe(lastId)
    }
  })

  it('still keeps every photograph exactly once', () => {
    for (let t = 0; t < 500; t++) {
      const out = ids(reshuffle(makeDeck(), 1)).sort((a, b) => a - b)
      expect(out).toEqual(ids(makeDeck()))
    }
  })

  it('leaves a one-photograph deck alone', () => {
    const one = [{ id: 7 }]
    expect(reshuffle(one, 7)).toEqual(one)
  })
})

describe('nextPlay', () => {
  it('shows every photograph before repeating any', () => {
    const deck = shuffle(makeDeck())
    let state = { deck, index: 0 }
    for (let pass = 0; pass < 200; pass++) {
      const seen = []
      for (let k = 0; k < N; k++) {
        seen.push(state.deck[state.index].id)
        state = nextPlay(state)
      }
      expect(new Set(seen).size).toBe(N)
    }
  })

  it('never shows the same photograph twice in a row, across many seams', () => {
    // 20000 advances is ~1538 passes: on the pre-fix code, which reshuffled
    // without protecting the seam, this fails inside the first hundred.
    const seen = play(20000)
    const backToBack = seen.filter((v, i) => i > 0 && v === seen[i - 1])
    expect(backToBack).toEqual([])
  })

  it('draws a fresh order for the next pass', () => {
    const deck = shuffle(makeDeck())
    const seen = play(N * 60, deck)
    const passes = []
    for (let i = 0; i + N <= seen.length; i += N) passes.push(seen.slice(i, i + N).join(','))
    // Not "all distinct" — 13! orders make a collision astronomically unlikely,
    // but the claim being tested is that a pass is not a replay of the previous
    // one, which is what a missing reshuffle would produce for every pass.
    expect(new Set(passes).size).toBeGreaterThan(passes.length / 2)
  })

  it('advances within a pass without touching the deck', () => {
    const deck = shuffle(makeDeck())
    const state = nextPlay({ deck, index: 0 })
    expect(state.index).toBe(1)
    expect(state.deck).toBe(deck)   // same array: no reshuffle mid-pass
  })

  it('wraps to zero at the end of a pass', () => {
    const deck = shuffle(makeDeck())
    expect(nextPlay({ deck, index: N - 1 }).index).toBe(0)
  })

  it('holds a one-photograph deck without failing', () => {
    const one = [{ id: 7 }]
    const state = nextPlay({ deck: one, index: 0 })
    expect(state.index).toBe(0)
    expect(state.deck).toEqual(one)
  })

  it('does not throw on an empty deck', () => {
    expect(() => nextPlay({ deck: [], index: 0 })).not.toThrow()
    expect(nextPlay({ deck: [], index: 0 }).index).toBe(0)
  })
})
