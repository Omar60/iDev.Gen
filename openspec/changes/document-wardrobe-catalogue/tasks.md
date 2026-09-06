## 1. Verify every requirement against the code by probe

Each task below probes ONE requirement of `specs/wardrobe/spec.md`. The probe
runs against the test suite's fixtures and the shipped seed, never the live
database. A requirement the probe contradicts is REWRITTEN to what the code
does — not turned into an implementation task — and the contradiction is
recorded in the task note.

- [x] 1.1 "A garment is one piece of clothing, an outfit is an order": read the shipped seed and the served catalogue, and verify an outfit's garments come back in the authored order and a garment with no aside wording carries an empty one. Verify no cell key anywhere in the store names a garment
- [x] 1.2 "The undressing is derived, never stored": derive the arc of an outfit of three garments and verify four states, each naming only what is still worn; verify no table and no seed file holds a state; verify an outfit naming an unknown garment key drops it rather than writing it into a state
- [x] 1.3 "The bare state says it": verify the last state of an arc is a sentence stating nothing is worn, and that it is not an empty string
- [x] 1.4 "A garment moved aside is a stage of its own": derive an arc whose last garment carries an aside wording and verify the extra stage exists, gives access, and appears only where that garment is the last one left; derive one whose last garment carries none and verify the arc steps straight to bare
- [x] 1.5 "How far a garment reaches is answered once": verify the served catalogue carries the reached part and the covers answer per garment, verify they come from the crop law's own calculation over the garment wording, and verify no column and no second reader computes them again
- [x] 1.6 "Access is answered per photograph": compose a run whose arc covers her in the early states and gives access in the late ones, and verify an act requiring access lands only on the photographs whose stage gives it — not excluded from the run, not dealt to photograph one. Verify the run's fallback answer decides only the photographs the caller had no answer for
- [x] 1.7 "A session dresses every photograph, a take may answer for itself": queue a take with its own wardrobe on a session with another and verify only the take's reaches the line; verify a run dealt fewer wardrobes than it queues composes the remainder in the session's rather than wrapping round; verify a muted run writes no session wardrobe
- [x] 1.8 "Every wardrobe the run may write constrains the draw": verify a state naming stockings takes the tight crops out of the pool for the whole run, and that a run dealing every photograph a wardrobe is not constrained by the session's own
- [x] 1.9 "The catalogue is imported, idempotent on the key, and refuses a hole": import the shipped seed twice and verify the second adds nothing and rewords nothing; import an outfit naming an absent garment and verify the refusal names the missing garments and wrote neither garments nor outfits; verify a retired row leaves the offered catalogue and is still readable on request

### What the probes ran

The probes were written as two throwaway files — one pytest module against the
`client`/`seeded` fixtures, one vitest module against the shipped seed — run,
read, and deleted. They are not left in the tree: every fact they establish is
already pinned by `tests/test_api.py` and `frontend/src/wardrobe.test.js`, and a
second copy of a green assertion is a test that cannot fail.

**1.1** — the five shipped outfits round-trip their authored garment order
through `POST /api/wardrobe/import` and `GET /api/wardrobe` unchanged.
`black-leggings` carries `aside: ''` and `black-knickers` carries a wording;
every `aside` served is a string, never null. `component`'s CHECK is
`slot IN ('camera', 'act', 'framing')` and no `cell` column names a garment.

**1.2** — `tshirt-and-joggers` (three garments) derives five states, not four:
its last garment is `black-knickers`, which carries an aside. An outfit of three
garments none of which can be moved derives exactly four. **This contradicted
the requirement and the requirement was rewritten** — see 2.1. No table has a
state column and no seed file holds a state (`data/manner-registers-seed.json`
matches "She wears" only in the LOOK, about her hair). An outfit naming
`not-a-garment` drops the key: the identifier reaches no composed line.

**1.3** — the last state of every arc is `She wears nothing at all.`

**1.4** — `blouse-and-skirt` names two garments carrying an aside
(`black-satin-skirt` and `black-knickers`) and derives exactly ONE aside stage,
the knickers, immediately before the bare one, with `access: true`. An outfit
ending in `black-leggings` steps from `She wears black leggings.` (`access:
false`) straight to bare.

**1.5** — `reaches` and `covers` served for all ten seed garments equal
`crop.PART_NAME[crop.lowest_named(wording)]` and `rung >= crop.WAIST` for every
row. `PRAGMA table_info(garment)` is `id, key, wording, aside, retired_at,
created_at` — no third copy. The only `lowest_named` callers are `crop.py`
itself and `main.py:1001`; the browser reads the served boolean
(`SessionView.jsx:35` says so out loud).

**1.6** — one run, two photographs, `access: [false, true]`: the `needs=access`
act landed on photograph 1 and not photograph 0, in the same run. With no
per-photograph answer, a run of one `needs=access` act composes at `bare: true`
and is refused at `bare: false` — the fallback decides, and only where the
caller had no answer.

**1.7** — a take's own wardrobe replaces the session's, `""` writes neither, and
`null` follows the session. A run of three photographs dealt one wardrobe wrote
it into the first and the session's into the other two — no wrap-round. A run
with `mute_wardrobe` wrote no session wardrobe.

**1.8** — the discriminating arms, all four on the same trio and the same
`a close-up of her face` framing:

| session wardrobe | dealt | result |
|---|---|---|
| `''` | nothing | 200 |
| `''` | `She wears white stockings.` | 422 |
| `She wears white stockings.` | `She wears nothing at all.` (every row) | 200 |
| `She wears white stockings.` | nothing | 422 |

A dealt state naming her feet takes the tight framing out of the pool for the
whole run; a run dealing every photograph a wardrobe is not constrained by the
session's own.

**1.9** — the shipped seed imports 15 rows, and the second import adds 0 and
skips 15. A re-import naming `black-knickers` with a different wording left the
stored wording untouched. An outfit naming `absent-thing` was refused 422 with
the missing key named, and neither the garment nor the outfit in that payload
was written. A retired garment left `GET /api/wardrobe` and stayed in
`GET /api/wardrobe?all=true`. A garment with no wording and an outfit with no
garments are both 422.

## 2. Reconcile and close

- [x] 2.1 Rewrite any requirement a probe in section 1 contradicted, and record in this file what the code does instead and which probe found it. Re-run `openspec validate document-wardrobe-catalogue --strict` and verify it passes

**One contradiction, found by probe 1.2.** The requirement said "N garments
SHALL produce N+1 states" and its scenario said three garments derive four,
flatly. What the code does: `arcFor` derives N+1 states of undressing and then
adds one more where the last garment carries an aside wording, so an outfit of
three garments ending in `black-knickers` derives FIVE. Every outfit the project
ships ends in a garment that can be moved, so N+1 was the count of an arc nobody
has. The requirement now says N+1 counts the undressing alone and names the
aside stage as additional; the scenario now says "none of which can be moved
aside". Nothing about the code changed.

- [x] 2.2 Record every real gap the probing turned up — a rule the comments claim and the code does not keep — as a note here, and propose it separately rather than folding a fix into this change. Verify no source file outside `openspec/` is modified by this change (`git status --short`)

**No gap.** Every rule the wardrobe's comments claim, the code keeps, and each
one was checked by a call rather than by reading the comment beside it. The four
that were most likely to have drifted, and what the probe found:

- `db.py:264` "No `rung` column ... a second copy of it here is a second answer".
  True: `PRAGMA table_info(garment)` has six columns and none of them is it.
- `ComposeRunIn.access` "a regex over that sentence here would be a second answer
  to 'is she covered'". True: the server never inspects the composed sentence for
  clothing. The only `lowest_named` callers are `crop.py` and `main.py:1001`.
- `compose_shot` drops the wardrobe when `mute_wardrobe`, and the crop context in
  `compose-run` drops the dealt wardrobes on the same flag. Two places, one rule,
  and they agree — a muted run neither writes a wardrobe nor is constrained by
  one.
- `arcFor`'s `left.length === 1` is marked `ponytail:` as a deliberate ceiling
  (a skirt pushed up over knickers is the same idea two layers up, and the rule
  to relax is the condition, not the data). A named ceiling is not a gap.

Two observations that are not gaps and not requirements, recorded because they
would otherwise be re-derived:

- All five shipped outfits end in `black-knickers`, so nothing in `data/` exercises
  the plain N+1 arc. The probe had to build a three-garment outfit of its own to
  see four states. That is what made the requirement's flat "N garments produce
  N+1 states" survive review — no shipped datum contradicts it.
- The aside stage reports `access: true` unconditionally; the `covers` of the
  aside WORDING is never consulted. That is the design (the stage exists to give
  access), not an oversight, but it means an aside wording that still covered her
  would report access anyway. Nothing in the seed is such a wording.

`git status --short` after deleting the two probe files: only
`openspec/changes/document-wardrobe-catalogue/specs/wardrobe/spec.md` and
`openspec/changes/document-wardrobe-catalogue/tasks.md` are modified. No source
file outside `openspec/` was touched.
- [x] 2.3 Run the gates and report the output rather than the summary: `python -m pytest`, `npm --prefix frontend run build`, `npm --prefix frontend test`. They must be green even though this change touches no code — a docs-only change that reds the suite means something else moved underneath it

All three green, with the probe files deleted first so the gates ran against the
tree as it will be committed.

`python -m pytest` — every progress row is dots through `[100%]`, no `F` and no
`E`, and the process exits `0`. The count line does not reach stdout in this
environment (it never has; the same is true of `--collect-only -q`, which prints
no node ids here), so the exit code is the reading, not a number quoted from a
summary that was not printed.

```
........................................................................ [ 58%]
........................................................................ [ 68%]
........................................................................ [ 78%]
........................................................................ [ 88%]
........................................................................ [ 98%]
.............                                                            [100%]
exit 0
```

`npm --prefix frontend run build` — `✓ 57 modules transformed`, `✓ built in
1.49s`, exit `0`. `dist/` is not staged.

`npm --prefix frontend test` — `Test Files 9 passed (9)`, `Tests 127 passed
(127)`, exit `0`.
- [x] 2.4 Sync the delta into `openspec/specs/wardrobe/spec.md` and verify `openspec validate --all --strict` passes with eighteen capabilities

The capability did not exist, so the sync created it: the delta's `## Purpose`
copied verbatim, its seven ADDED requirements moved under one `## Requirements`
header, and no delta operation header left in the file.

`openspec validate --all --strict` — `Totals: 19 passed, 0 failed`, which is the
eighteen capabilities plus this change. `openspec validate --specs` alone reports
`Totals: 18 passed, 0 failed`, with `spec/wardrobe` among them.
