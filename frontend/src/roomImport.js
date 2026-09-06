// What the room import screen shows, as plain data.
//
// The shaping lives here rather than inside the view because the frontend
// suite has no jsdom and no testing-library: every test in it is a pure module
// test. What is worth asserting about this screen is which numbers and which
// strings come out of a report — not how they are painted — and that is
// exactly what these three functions answer.

// The counts the import reports, in the order the CLI prints them, so the
// screen and `scripts/import_assets.py` read the same way round.
const COUNTS = [
  ['accepted', 'Accepted'],
  ['refused', 'Refused'],
  ['written', 'Written'],
  ['created', 'Created'],
  ['updated', 'Updated'],
  ['unchanged', 'Unchanged'],
  ['orphaned', 'Orphaned'],
  ['skipped_empty', 'Skipped (empty theme)'],
]

/** The report's counts, one row per count. A count the report does not carry
 *  reads as 0 rather than disappearing: a missing row is a row the operator
 *  cannot tell from one nobody wrote. */
export function countRows(report) {
  if (!report) return []
  return COUNTS.map(([key, label]) => ({ key, label, value: report[key] ?? 0 }))
}

/** What was refused and why. A whole source file is refused by library name
 *  with the manifest's reason; an individual entry is refused by the guard,
 *  which reports a count per signal and never the entry's text. */
export function refusalRows(report) {
  const rows = []
  for (const r of report?.refused_libraries ?? []) {
    rows.push({ what: r?.library ?? '', why: r?.reason ?? '' })
  }
  for (const [signal, n] of Object.entries(report?.by_signal ?? {})) {
    if (n) rows.push({ what: `${n} ${n === 1 ? 'entry' : 'entries'}`, why: signal })
  }
  return rows
}

/** Every string the translation map does not cover, with the entry and the
 *  field it came from.
 *
 *  `api.js` throws an Error whose message is the JSON of a non-string `detail`,
 *  so the 422 body reaches the view as text; the parsed body is accepted too.
 *  Anything that is not a translation refusal answers an empty list, which is
 *  how the view tells a missing translation from every other failure. */
export function uncoveredRows(detail) {
  let body = detail
  if (typeof body === 'string') {
    try { body = JSON.parse(body) } catch { return [] }
  }
  const list = Array.isArray(body) ? body : body?.uncovered
  if (!Array.isArray(list)) return []
  return list.map((u) => ({
    identifier: String(u?.identifier ?? ''),
    field: String(u?.field ?? ''),
    string: String(u?.string ?? ''),
  }))
}
