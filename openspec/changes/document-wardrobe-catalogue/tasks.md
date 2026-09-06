## 1. Verify every requirement against the code by probe

Each task below probes ONE requirement of `specs/wardrobe/spec.md`. The probe
runs against the test suite's fixtures and the shipped seed, never the live
database. A requirement the probe contradicts is REWRITTEN to what the code
does — not turned into an implementation task — and the contradiction is
recorded in the task note.

- [ ] 1.1 "A garment is one piece of clothing, an outfit is an order": read the shipped seed and the served catalogue, and verify an outfit's garments come back in the authored order and a garment with no aside wording carries an empty one. Verify no cell key anywhere in the store names a garment
- [ ] 1.2 "The undressing is derived, never stored": derive the arc of an outfit of three garments and verify four states, each naming only what is still worn; verify no table and no seed file holds a state; verify an outfit naming an unknown garment key drops it rather than writing it into a state
- [ ] 1.3 "The bare state says it": verify the last state of an arc is a sentence stating nothing is worn, and that it is not an empty string
- [ ] 1.4 "A garment moved aside is a stage of its own": derive an arc whose last garment carries an aside wording and verify the extra stage exists, gives access, and appears only where that garment is the last one left; derive one whose last garment carries none and verify the arc steps straight to bare
- [ ] 1.5 "How far a garment reaches is answered once": verify the served catalogue carries the reached part and the covers answer per garment, verify they come from the crop law's own calculation over the garment wording, and verify no column and no second reader computes them again
- [ ] 1.6 "Access is answered per photograph": compose a run whose arc covers her in the early states and gives access in the late ones, and verify an act requiring access lands only on the photographs whose stage gives it — not excluded from the run, not dealt to photograph one. Verify the run's fallback answer decides only the photographs the caller had no answer for
- [ ] 1.7 "A session dresses every photograph, a take may answer for itself": queue a take with its own wardrobe on a session with another and verify only the take's reaches the line; verify a run dealt fewer wardrobes than it queues composes the remainder in the session's rather than wrapping round; verify a muted run writes no session wardrobe
- [ ] 1.8 "Every wardrobe the run may write constrains the draw": verify a state naming stockings takes the tight crops out of the pool for the whole run, and that a run dealing every photograph a wardrobe is not constrained by the session's own
- [ ] 1.9 "The catalogue is imported, idempotent on the key, and refuses a hole": import the shipped seed twice and verify the second adds nothing and rewords nothing; import an outfit naming an absent garment and verify the refusal names the missing garments and wrote neither garments nor outfits; verify a retired row leaves the offered catalogue and is still readable on request

## 2. Reconcile and close

- [ ] 2.1 Rewrite any requirement a probe in section 1 contradicted, and record in this file what the code does instead and which probe found it. Re-run `openspec validate document-wardrobe-catalogue --strict` and verify it passes
- [ ] 2.2 Record every real gap the probing turned up — a rule the comments claim and the code does not keep — as a note here, and propose it separately rather than folding a fix into this change. Verify no source file outside `openspec/` is modified by this change (`git status --short`)
- [ ] 2.3 Run the gates and report the output rather than the summary: `python -m pytest`, `npm --prefix frontend run build`, `npm --prefix frontend test`. They must be green even though this change touches no code — a docs-only change that reds the suite means something else moved underneath it
- [ ] 2.4 Sync the delta into `openspec/specs/wardrobe/spec.md` and verify `openspec validate --all --strict` passes with eighteen capabilities
