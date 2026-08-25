import { describe, expect, test } from 'vitest'
import { cameraPlan, shootChunkNote, MANNER, TECHNIQUE_DEFECTS } from './kinds.js'

/** The defect plan exists to stop one subject running through the tail of a long
 *  shoot, and it is dealt by the camera spreader. Both halves are worth a check:
 *  a plan that repeats a family back to back does nothing, and a plan the chunk
 *  note never prints does nothing either — and neither failure shows up in a
 *  diff. */
describe('the technique defect plan', () => {
  test('never opens two photographs running on the same kind of defect', () => {
    const family = (line) => TECHNIQUE_DEFECTS.find((d) => d.line === line).family
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
