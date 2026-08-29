## 1. The store

- [ ] 1.1 Add the `reading` table and its two partial unique indexes to the schema and the on-open migration in `backend/db.py`, and verify with a test that opens a database created before this change and finds the table empty with sessions and shots untouched
- [ ] 1.2 Type `ReadingIn.slot` as `Literal["camera", "act", "framing"]` rather than a `str` checked in the body, the way `ComposeIn.mode` already is and for the reason written there — a wrong value is refused at the boundary and never reaches the branch. Add `GET /api/readings?slot=&manner=&session_id=` returning the union (base plus that session's) and verify with a test that a base reading and a session reading both come back for that session and only the base one for another session
- [ ] 1.3 Add `POST /api/readings` and verify with a test that a session-scoped reading whose key already exists in the base for the same slot and manner is refused 422 naming the key, that nothing is written, and that the same key in two different sessions is accepted
- [ ] 1.3b Refuse the collision in the OTHER direction too — a base reading whose key a session reading already holds — and verify with a test that inserts the session reading FIRST and then the base one; the two partial unique indexes cover disjoint rows and catch neither, so the test fails without application code
- [ ] 1.4 Add `DELETE /api/readings/{id}` and verify with a test that a reading referenced by a stored verdict is refused with the count of answers referencing it, and that an unreferenced one is removed — the scan is scoped by the reading's own scope, a session reading against that session's shots only and a base reading against every session of that manner, or a session reading becomes unremovable because another session answered the same key

## 2. The pass

- [ ] 2.1 Have `GET /api/sessions/{sid}/judge-pass` return the reading union for the slot alongside the deck, and verify with a test that the union is what `/api/readings` returns for that session and slot
- [ ] 2.1b Update the seven assertions that pin the exact judge-pass response shape (`tests/test_api.py` lines 3685, 3722, 3752, 3757, 3787, 3825, 6254), and decide whether `families` stays in the response at all — the frontend stops needing it once readings are served, and two sources for one question is the bug shape this branch keeps hitting; delete the losing one, including the `families` filter in `slotChoices`
- [ ] 2.2 Refuse the pass 422, naming the families, when a family present among the deck's photographs has no reading in either scope, and verify with a test that plants a photograph of a family with no reading, asserts the refusal names it, and asserts the deck is not served
- [ ] 2.3 Verify with a test that the refusal is a pre-check: a deck that would be refused serves nothing at all, not a partial list
- [ ] 2.3b Walk the CONTROLS as well as the shots when collecting the families the pass must have readings for, and verify with a test that a control photograph whose family has no reading refuses the pass — a control is intercalated into the deck by `buildJudgeDeck` and is answered like any other photograph, so an answer that is not on the list stops the pass halfway through

## 3. Scoring

- [ ] 3.1 Score a hit as "the reading picked is the family the line asked for", keeping the existing exact-key and family branches ahead of nothing, and verify with a test that an answer stored as a component key before this change still scores as it did
- [ ] 3.2 Verify with a test that a reading that is not the family asked for counts as judged and not arrived, and that the reading key stays readable on `shot.verdicts` as what was seen
- [ ] 3.3 Verify with a test that a slot the line asked nothing of counts toward no cell whatever reading is picked (the rule task 2 of the previous session added, re-asserted against the reading vocabulary)

## 4. The screens

- [ ] 4.1 Build the judging screen's forced choice from the pass's readings plus "cannot tell" instead of from the catalogue, and verify with a `vitest` test over the pure function that the choices are the readings in order with the explicit answer last
- [ ] 4.1b Rewrite the Framing tab's enabled/disabled guard at `frontend/src/views/Judge.jsx:379`, which calls `slotChoices(slot, manner)` — a third caller the signature change breaks — and verify the tab is still disabled for a manner with nothing to offer
- [ ] 4.2 Surface the pass refusal on the judging screen with the families it names, and verify by starting a pass against a session with a missing reading and reading the message
- [ ] 4.3 Add base reading management (list, add, remove per slot and manner) to the catalogue screen, and verify `npm --prefix frontend run build` succeeds and the screen round-trips a reading through the API
- [ ] 4.4 Add session reading management to the judging screen, and verify a reading added there appears in that session's pass and not in another session's

## 5. Documentation and the writing contract

- [ ] 5.1 Add the reading-list request to `docs/catalogue-candidate-prompt.md`: mutually exclusive readings, each decidable by a landmark rather than by degree, no terms of art, and always including the outcomes the checkpoint produces unasked — naming the measured frontal floor as the worked example
- [ ] 5.2 Update `README.md` and add the judging page under `docs/` (there is none today — `docs/` holds `catalogue-measurements.md` and no judging page), covering the reading vocabulary, the two scopes and the pass refusal
- [ ] 5.3 Verify the whole change with `python -m pytest` and `npm --prefix frontend test` green and `npm --prefix frontend run build` succeeding

## 6. First use

- [ ] 6.1 Write the base readings for `camera`, `act` and `framing` under `directed` for the current bench — labels written by LOOKING at the photographs, never copied from a component's `judge_label` (the test fixture may seed them that way to keep older tests running; the real set must not, because a label taken from the component is the contamination the reading vocabulary exists to remove) — including `front` for the camera slot, which the measured floor produces with an empty prompt — and verify the framing pass on session 308 opens with them
- [ ] 6.2 Judge the framing row of session 308 under the new vocabulary and verify each cell reaches its threshold with a recorded reading on every photograph
