import { describe, expect, test } from 'vitest'
import { cameraPlan, shootChunkNote, MANNER, TECHNIQUE_DEFECTS, BODY_OPENINGS,
         BRIEF_AXES, setCatalogue, arrangements, positionsFor,
         fitCameras } from './kinds.js'
import { undressBy } from './enhance.js'

/** The defect plan exists to stop one subject running through the tail of a long
 *  shoot, and it is dealt by the camera spreader. Both halves are worth a check:
 *  a plan that repeats a family back to back does nothing, and a plan the chunk
 *  note never prints does nothing either — and neither failure shows up in a
 *  diff. */
describe('the technique defect plan', () => {
  test('never opens two photographs running on the same kind of defect', () => {
    const family = (line) => TECHNIQUE_DEFECTS.find((d) => d.wordings[0].text === line).wordings[0].family
    for (let run = 0; run < 20; run += 1) {
      const plan = cameraPlan(30, Math.random, MANNER.candid.defects)
      expect(plan).toHaveLength(30)
      const families = plan.map(family)
      expect(families.slice(1).some((f, i) => f === families[i])).toBe(false)
    }
  })

  test('reaches the writer, one row per photograph', () => {
    const note = shootChunkNote({ from: 4, want: 2, total: 30,
                                  cameras: ['CAM A', 'CAM B'],
                                  defects: ['a shadow on her gone to noise',
                                            'one side of her a stop too bright'] })
    expect(note).toContain('4 | technique: a shadow on her gone to noise')
    expect(note).toContain('5 | technique: one side of her a stop too bright')
    expect(note).toContain('`technique:` LINE')
  })

  test('is silent for a manner that has no technique field', () => {
    expect(MANNER.directed.defects).toBeUndefined()
    const note = shootChunkNote({ from: 1, want: 1, total: 8, cameras: ['CAM A'] })
    expect(note).not.toContain('technique:')
  })
})

/** Same spreader, same failure, a different field: the `her` field opened on the
 *  chest in half the lines of three shoots of thirty. All three regions are
 *  still written every line — only which one comes first is dealt. */
describe('the body opening plan', () => {
  test('never opens two photographs running on the same region', () => {
    const plan = cameraPlan(30, Math.random, BODY_OPENINGS)
    expect(plan.slice(1).some((o, i) => o === plan[i])).toBe(false)
  })

  test('reaches the writer, and says all three are still written', () => {
    const note = shootChunkNote({ from: 9, want: 1, total: 30,
                                  cameras: ['CAM A'], opens: ['her feet'] })
    expect(note).toContain('9 | her opens on: her feet')
    expect(note).toContain('All three are still written in every single line')
  })
})

/** Every axis rolls on every brief — `briefFromLook` picks one row from each and
 *  hands them over as constraints together. So a row that names a room, a
 *  garment or a pose is not a style choice, it is a clause fighting the look,
 *  the reach or the writer of the lines, and none of the three shows up in a
 *  diff. */
describe('the brief axes', () => {
  const rows = Object.values(BRIEF_AXES).flat()

  test('read as the continuation of "it" that the brief hands over', () => {
    for (const row of rows) expect(row).toMatch(/^[a-z]/)
  })

  test('name no room, no garment, no pose and no camera', () => {
    // The look owns the place and the light, the wardrobe owns the clothes, and
    // the writer of the lines owns the body and the camera.
    const forbidden =
      /\b(kitchen|bathroom|bedroom|hallway|studio|bed|couch|sofa|chair|table|mirror|window|floor|dress|skirt|top|blouse|bra|panties|briefs|stockings|heels|naked|nude|undress|pose|posing|angle|close-up|frame[ds]|camera|lens)\b/
    for (const row of rows) expect(row).not.toMatch(forbidden)
  })
})

/** The stage plan is handed a photograph number, not a proportion: measured over
 *  six plans of thirty, "roughly a quarter getting there" produced first-bare at
 *  12, 12, 15, 21, 27 and never. */
describe('undressBy', () => {
  test('is 55% of the shoot when there is undressing to do', () => {
    expect(undressBy(30, 'nude', false)).toBe(17)
    expect(undressBy(8, 'couple', false)).toBe(4)
  })

  test('is nothing for a shoot that keeps its clothes or never had them on', () => {
    expect(undressBy(30, 'sfw', false)).toBe(0)
    expect(undressBy(30, 'explicit', true)).toBe(0)
  })
})

/** An arrangement's compatible camera families are the store's, not a guess
 *  made from its `family` value.
 *
 *  This was an if-chain over three literal families (`ontop`, `away`,
 *  `standing`). Every act added through the catalogue screen fell off the end
 *  of it with an empty list, so `fitCameras` skipped the photograph and the
 *  planted arrangement kept whatever camera the spread had dealt it — the
 *  failure the fit exists to prevent, reintroduced for exactly the acts the
 *  screen was built to add. */
describe('an act carries its own camera families', () => {
  const ROW = {
    concept_key: 'spooning', slot: 'act', manner: 'directed', family: 'spooning',
    faces: 'back', wording: 'They are lying on their sides, he is behind her.',
    judge_label: 'Both lying on their sides, he is behind her',
    cameras: ['shoulder', 'overhead'],
  }
  const CAMERAS = [
    { concept_key: 'front-direct', slot: 'camera', manner: 'directed', family: 'front',
      wording: 'Taken from directly in front of her', judge_label: 'From the front' },
    { concept_key: 'shoulder-left', slot: 'camera', manner: 'directed', family: 'shoulder',
      wording: 'Taken from behind her left shoulder', judge_label: 'From behind her shoulder' },
  ]

  test('a store-defined act keeps the families the row gives it', () => {
    setCatalogue([...CAMERAS, ROW])
    expect(arrangements('directed')[0].cameras).toEqual(['shoulder', 'overhead'])
  })

  test('and fitCameras moves its photograph onto one of them', () => {
    setCatalogue([...CAMERAS, ROW])
    const dealt = ['Taken from directly in front of her']
    const out = fitCameras(dealt, { 1: arrangements('directed')[0] }, positionsFor('directed'))
    expect(out[0]).toBe('Taken from behind her left shoulder')
  })

  test('an act with no families in the row is left where it was dealt', () => {
    setCatalogue([...CAMERAS, { ...ROW, cameras: [] }])
    const dealt = ['Taken from directly in front of her']
    const out = fitCameras(dealt, { 1: arrangements('directed')[0] }, positionsFor('directed'))
    expect(out[0]).toBe('Taken from directly in front of her')
  })
})
