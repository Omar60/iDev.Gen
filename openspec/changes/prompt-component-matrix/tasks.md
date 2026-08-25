## 1. Reshape the catalogues into concepts with wordings

Behaviour-neutral throughout: the shufflers must keep drawing the same lines.

- [x] 1.1 Give every catalogue entry the concept shape — key, slot, wordings, optional reference image — keeping each existing string as that concept's first wording, and verify `python -m pytest` and `npm --prefix frontend test` stay green with no prompt text changed
- [ ] 1.2 Bring `KISS_CAMERA` into the catalogue as camera components carrying that they override a dealt camera, and verify a session with planted kiss frames still ends up with the same camera text on those photographs
- [ ] 1.3 Add a test asserting no catalogue wording's text appears anywhere else in the prompt system, and confirm it fails against the two inline camera examples still present
- [ ] 1.4 Add a test asserting `SHOOT_FIELDS` and `BLOCK_HEADINGS` still carry the same seven keys in the same order after the reshape, or confirm the existing one in `tests/test_enhance.py` covers it unchanged

## 2. Evidence store

- [ ] 2.1 Add cell storage keyed by concept, wording, manner and checkpoint holding judged and arrived counts, and verify a write missing any of the four is rejected
- [ ] 2.2 Derive the three states from the counts — verified at 10 judged and 8 arrived, dead below that, unknown under 10 judged — and verify each boundary with a unit test including 0 of 3 landing as unknown
- [ ] 2.3 Seed the existing verdicts with their real sample sizes, manners and checkpoints, including the wordings currently deleted from `ARRANGEMENTS` as dead, and verify `astride` seeds per family (front 6/6, overhead 4/4, mirror 4/6, pov 4/6) rather than as its 18/22 aggregate, and `back` and `side` per checkpoint (0 of 12 and 0 of 12; 0 of 9 and 0 of 8) rather than as their 0-of-41 sum
- [ ] 2.4 Add a test that every seeded verdict names a wording that exists in the catalogue, and verify it fails when a seed names a wording the reshape did not carry over — the seeding is a mapping change and a seed pointing at nothing is the failure that would otherwise surface far from its cause
- [ ] 2.5 Replace the `noneDead` assertion in `tests/test_arrangements.py` with one that a dead wording is never drawn, and verify the previously deleted keys are present in the catalogue and absent from every draw

## 3. Composer, strict mode

- [ ] 3.1 Compose and queue a single shot from drawn components with no writer request, recording the components on the shot, and verify the queued line joins identically to a written one
- [ ] 3.2 Restrict strict draws to cells verified for the session's manner and checkpoint, and verify a component verified on another checkpoint is not drawn
- [ ] 3.3 Refuse a strict composition that cannot fill a slot without repeating, naming the slot, its verified count and the largest fillable count, and verify nothing is queued, no shorter run is delivered, and the message names exploratory mode
- [ ] 3.4 Decide duplicates on the drawn component tuple before queueing, keep the existing line-level repeat check running over composed lines too, and verify two distinct tuples that join into the same line are still refused
- [ ] 3.5 Compose a whole session as the same draw plus ordering, and verify no two consecutive photographs share a family in the spread slots
- [ ] 3.6 Record on the session whether its lines were composed or written, and verify a written session behaves exactly as before

## 4. Measure the composer against the fixed-line scripts

- [ ] 4.1 Point one `scripts/shoot_*.py` at the composer instead of its hand-built line and verify it produces the same line it built by hand
- [ ] 4.2 Shoot one already-measured question through both the script and the composer at n=10 and report the two rates side by side, so the composer is checked against a control before anything is judged through it

## 5. Judging screen

- [ ] 5.1 Present a photograph with no brief, no composed line, no wording and no reference image, and verify none of them reach the client
- [ ] 5.2 Ask one question across a batch as a forced choice over the slot's whole list plus "none or cannot tell", recording the answer chosen and not only whether it matched, and verify a miss stores which component was seen
- [ ] 5.3 Re-present already-judged photographs unmarked during a pass and report the agreement rate at the end, and verify a disagreement does not overwrite the stored verdict
- [ ] 5.4 Judge an already-judged session through the screen against a verdict already known, and report whether the screen reproduces it
- [ ] 5.5 Time one batch of human judging against one batch of vision judging and record the real ratio, replacing the estimate in design.md

## 6. Exploratory mode

- [ ] 6.1 Allow draws from unknown cells marked as exploratory, never from dead wordings, and verify a dead wording is undrawable in both modes
- [ ] 6.2 Count a judged exploratory photograph towards its cell and verify the cell flips to verified or dead on reaching the threshold

## 7. Cleanup and documentation

- [ ] 7.1 Remove the two inline camera examples from the instruction prose and verify the single-home test from 1.3 now passes and no test asserting prompt text changes
- [ ] 7.2 Update `README.md` and the matching page under `docs/` with the judging screen and the composer path, including that strict mode refuses rather than repeats
- [ ] 7.3 Run the full gates — `python -m pytest`, `npm --prefix frontend run build`, `npm --prefix frontend test` — and report the output rather than the summary
