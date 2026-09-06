## Why

Two things are true at once, and only the second one is still a problem.

The camera has already been taken away from the writer. `cameraPlan` deals the
position from `POSITIONS[manner]` before the writer is asked anything, the forty
lines of `CAMERA_FORMS` are gone, and the measured habit that justified the move
— 19-20 of 25 camera fields copied verbatim from the examples — no longer
decides where the camera stands. Adding a camera to a catalogue does take effect
today. That half is done.

What is not done is everything around it:

- **The evidence has nowhere to live.** Every verdict this project owns is prose
  in a comment. `fitCameras` reads `ARRANGEMENTS[].cameras`, a hand-derived list
  distilled from those comments by a person; nothing in code can ask what a
  wording was measured against, on which manner, on which checkpoint.
- **Dead is expressed by deletion.** `behind`, `back` and `side` are not marked
  failed — they were removed from the file, and `tests/test_arrangements.py`
  asserts they never come back as keys. So a measured failure loses the wording
  that failed, which is precisely what would be needed to re-test it. Both were
  shot in exactly one form each.
- **Almost every verdict is n=3.** A 0/3 admits a true success rate up to ~0.63
  and a 3/3 admits one as low as ~0.37 (one-sided 95% Clopper-Pearson). Neither
  says a component works and neither says it is dead. Only `back` (0 of 12 on
  finepornV4 plus 0 of 12 on the Krea 2 mix) and `side` (0 of 9 plus 0 of 8 on
  the same two) carry enough.
- **Six of seven fields are still written against shared prose.**
  `SHOOT_LINE_INSTRUCTION` plus the per-manner text serves every manner and all
  of `act`, `her`, `him`, `worn`, `technique`, `face` at once, so a fix aimed at
  one moves the others — the seven-keys header is inert in `directed` and worth
  81 points in `candid`, and tidying it was a regression.
- **There is no line the writer can be compared against.** Eight
  `scripts/shoot_*.py` each hand-roll a fixed line to measure one field. That
  practice is the missing product: a composer.

## What Changes

- Introduce a **component catalogue** as the single home for the parts a shot is
  composed from — camera, act, framing to begin with. An entry is a concept, not
  a string: it carries one or more candidate **wordings**, each with its own
  evidence, plus an optional **reference image** saying what photograph the
  concept aims at. A failed wording is marked `dead` and kept, not deleted.
- Bring **all four camera sources** under it: `CAMERA_POSITIONS`,
  `CANDID_POSITIONS`, `SELFIE_POSITIONS` and `KISS_CAMERA` — the last of which
  overwrites the planned camera at kiss frames (`enhance.js` in `shootLines`)
  and is therefore a camera origin the matrix has to represent, not a footnote.
- Introduce a **compatibility matrix** over `(concept, wording, manner,
  checkpoint)`, filled cell by cell at n=10. A cell holds `verified`, `dead` or
  `unknown`. Unknown is not dead: never measured and measured-and-failed are
  different facts, and today the codebase cannot tell them apart.
- Fill it from a **forced-choice judging screen**. The photograph is shown
  without its brief and the operator picks which camera family, act or framing
  they see, from the whole list. Blind by construction, and it yields a confusion
  matrix — where the failures land — which a yes/no question cannot.
- Add a **deterministic shot composer** that builds a line by drawing components
  from the catalogue, with no writer involved. Two modes: `strict` draws only
  `verified` cells; `exploratory` draws `unknown` cells and records the outcome,
  so ordinary use fills the matrix.
- The composer serves both a single independent shot and a whole session; a
  session is the same draw plus ordering constraints.
- Remove the two remaining camera examples inline in `SHOOT_LINE_INSTRUCTION`.
  **Not breaking**: measured, deleting them drops verbatim reuse to about 1 and
  *the shoot does not change*, because the position is dealt in code. This is
  residual cleanup that removes a second home for camera text, and it is not the
  justification for anything else in this change.
- Seed the matrix with the verdicts already paid for, each against the one
  wording, manner and checkpoint it was actually shot on: `astride` front 6/6,
  overhead 4/4, mirror 4/6, pov 4/6 (18/22 aggregate); `reverse` shoulder 3/3;
  `wall` mirror 3/3, shoulder 0/3; `back` 0 of 12 and 0 of 12, `side` 0 of 9 and
  0 of 8, each split by checkpoint rather than seeded as their 0-of-41 sum;
  `candid x behind` 0/6 and `candid x floor` 0/3; close-up-on-face 32/32 wrong
  act.

## Capabilities

### New Capabilities
- `prompt-components`: the catalogue of shot components — concepts, their
  candidate wordings, reference images, dead wordings retained rather than
  deleted, and the rule that a component's text has exactly one home.
- `component-matrix`: the evidence store and the judging screen that fills it —
  cell identity, the three verdict states, the n=10 / 8-of-10 admission rule,
  and forced-choice blind judging.
- `shot-composer`: composing a queued line from catalogue components without a
  writer, in strict and exploratory modes, for a single shot and for a session.

### Modified Capabilities

None. No existing spec covers prompt writing; the prose lives in `kinds.js` and
is described by AGENTS.md rather than by a spec.

## Impact

- `frontend/src/kinds.js` — the catalogues (`CAMERA_POSITIONS`,
  `CANDID_POSITIONS`, `SELFIE_POSITIONS`, `KISS_CAMERA`, `ARRANGEMENTS`,
  `BODY_OPENINGS`, `TECHNIQUE_DEFECTS`, `KISS_FRAMES`) become concepts with
  wordings and evidence; the two inline camera examples come out.
- `frontend/src/enhance.js` — `cameraPlan`, `spreadOver`, `fitCameras` and
  `arrangementPlan` draw against the matrix rather than over a flat list;
  `shootLines` keeps the kiss-frame camera override, now sourced from the
  catalogue.
- `backend/db.py`, `backend/main.py` — storage for cells, verdicts and judged
  outcomes; routes for the judging screen and for recording an exploratory draw.
- `backend/enhance.py` — `BLOCK_HEADINGS` is keyed by the same seven field names
  as the frontend's `SHOOT_FIELDS`, and `tests/test_enhance.py` asserts the two
  match. Any reshaping that touches field identity touches both files and that
  test.
- `frontend/src/views/` — a judging screen, and a composer path on the shot
  editor that does not call the writer.
- `scripts/` — the eight `shoot_*.py` scripts each re-implement a fixed-line
  composer by hand; they become callers of the composer.
- `tests/test_arrangements.py` — its `noneDead` assertion encodes "dead means
  absent from the file". Retaining dead wordings replaces that rule on purpose,
  and the test changes with it rather than being deleted.
- The writer path is untouched and remains the default: it still writes `act`,
  `her`, `him`, `worn`, `technique` and `face` against
  `SHOOT_LINE_INSTRUCTION`. The composer is a second generator beside it.
- No new runtime dependency. Reference images are authored outside the app and
  stored as files; nothing in the app calls an external image service.
- `README.md` and `docs/` gain the new screen and the composer path.


## Added 2026-08-26: the composer needs a way in

Group 5 shipped the judging screen and it opened empty. The reason is not in the
screen: of 292 sessions in the working database exactly one holds composed
photographs, and that one was a throwaway script written to measure something
else in task 4.2. `grep -rn "compose" frontend/src/` returns no call to
`/compose`, `/compose-run` or `/compose-session`.

So the composer — the half of this change that draws from measured evidence — is
reachable only from a script, while the writing half has had a screen all along.
Nothing in the plan ever asked for the button, and each group tested its own
endpoints from the outside and found them working.

Group 8 adds it: a count, a mode and a button on a session that already exists,
plus the refusal shown as the composer worded it. No backend work — the
endpoints are built, tested, and already documented as taking a catalogue slice
the operator picks.

This also unblocks 5.4 and 5.5, which need a batch of composed photographs to
judge, and it means 7.2 documents a path an operator can actually walk.
