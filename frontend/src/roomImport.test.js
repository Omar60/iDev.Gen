import { describe, it, expect } from 'vitest'
import { countRows, refusalRows, uncoveredRows } from './roomImport.js'

// The report the route hands back, trimmed to the keys the screen reads.
const REPORT = {
  accepted: 4,
  refused: 2,
  written: 5,
  created: 3,
  updated: 1,
  unchanged: 1,
  orphaned: 1,
  skipped_empty: 0,
  refused_identifiers: ['room_042', 'room_043'],
  refused_libraries: [{ library: 'amateurs', reason: 'not_adopted' }],
  by_signal: { identifier: 1, tags: 1, theme_text: 0 },
  by_library: { general_scenes: 2 },
  destinations: {},
}

// Invented placeholder source strings. The real ones are prose in another
// script; a tracked file in this repo stays pure ASCII, and what this test is
// about is that every string survives with its entry and its field, not which
// alphabet it was written in.
const UNCOVERED = [
  { identifier: 'gs_01', field: 'label', string: 'SRC-LABEL-ONE' },
  { identifier: 'gs_01', field: 'notes.ambience', string: 'SRC-AMBIENCE-ONE' },
  { identifier: 'gs_02', field: 'theme', string: 'SRC-THEME-TWO' },
]

describe('the report the import screen shows', () => {
  it('renders every count, in the order the command-line entry prints them', () => {
    expect(countRows(REPORT)).toEqual([
      { key: 'accepted', label: 'Accepted', value: 4 },
      { key: 'refused', label: 'Refused', value: 2 },
      { key: 'written', label: 'Written', value: 5 },
      { key: 'created', label: 'Created', value: 3 },
      { key: 'updated', label: 'Updated', value: 1 },
      { key: 'unchanged', label: 'Unchanged', value: 1 },
      { key: 'orphaned', label: 'Orphaned', value: 1 },
      { key: 'skipped_empty', label: 'Skipped (empty theme)', value: 0 },
    ])
  })

  it('shows a count the report omitted as zero rather than dropping the row', () => {
    const rows = countRows({ accepted: 1 })
    expect(rows).toHaveLength(8)
    expect(rows.find((r) => r.key === 'orphaned')).toEqual(
      { key: 'orphaned', label: 'Orphaned', value: 0 })
  })

  it('has nothing to show before an import has run', () => {
    expect(countRows(null)).toEqual([])
  })

  it('names what was refused and why, and never an entry text', () => {
    expect(refusalRows(REPORT)).toEqual([
      { what: 'amateurs', why: 'not_adopted' },
      { what: '1 entry', why: 'identifier' },
      { what: '1 entry', why: 'tags' },
    ])
  })
})

describe('the uncovered-string list', () => {
  it('names every uncovered string with its identifier and its field', () => {
    const rows = uncoveredRows({ error: 'translation_missing', uncovered: UNCOVERED })
    expect(rows).toHaveLength(UNCOVERED.length)
    expect(rows).toEqual(UNCOVERED)
  })

  it('reads the 422 body back out of the message api.js throws', () => {
    // A non-string `detail` reaches the view as JSON text; the whole list has
    // to survive that trip, not just its first item.
    const thrown = JSON.stringify({ error: 'translation_missing', uncovered: UNCOVERED })
    expect(uncoveredRows(thrown)).toEqual(UNCOVERED)
  })

  it('is empty for a failure that is not a missing translation', () => {
    expect(uncoveredRows('source_dir is required and cannot be empty')).toEqual([])
    expect(uncoveredRows({ detail: 'no such directory' })).toEqual([])
    expect(uncoveredRows(undefined)).toEqual([])
  })
})
