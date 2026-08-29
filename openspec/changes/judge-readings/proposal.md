## Why

The judging screen builds its forced choice from the catalogue rows that were
photographed, so a photograph that did not deliver what the line asked for can
only be recorded as "none or cannot tell". The measurement then says the ask did
not arrive and never says **what did** — a camera wording that came back frontal
and one that came back overhead are the same row in the cell table. On a bench
that isolates one slot, the deck also ends up with a single expected answer,
which is an answer an operator can give without looking.

## What Changes

- A new **reading** vocabulary: per slot and manner, the things a judge can see
  in a photograph — the outcome the line asked for and the outcomes it did not.
  Readings are what the judging screen offers.
- Readings come from two sources that **cannot contradict each other**: base
  readings (`session_id` null) and readings added for one session. A
  session-scoped reading whose key already exists in the base for that
  (slot, manner) is refused at write time, so no precedence rule is needed at
  read time.
- A reading's key **is** a component family. `arrived` becomes "the reading the
  judge picked is the family the line asked for", which is what the family-match
  rule already computes — the change is the set of answers offered, not the
  meaning of a hit.
- `GET /api/sessions/{sid}/judge-pass` returns the reading union for the slot
  and **refuses, naming them**, when a family present in the deck has no
  reading: the correct answer would not be on the list.
- The judging screen renders the readings plus "cannot tell", and records which
  reading was seen on the shot's verdicts.
- CRUD for readings from the catalogue screen, and per-session readings.
- `docs/catalogue-candidate-prompt.md` gains the request that produces a reading
  list from the reference photograph: mutually exclusive, each decidable by a
  landmark rather than by degree, and always including the outcomes the
  checkpoint produces unasked (the measured floor of the current bench is
  frontal, so "frontal" is a reading whether or not anything asked for it).

**BREAKING** for stored verdicts: an answer used to be a component key and is a
reading key now. Existing verdicts stay readable — the scoring keeps the
component-key and family fallbacks — but new answers are reading keys.

## Capabilities

### New Capabilities
- `judge-readings`: the vocabulary a judging pass offers, where it comes from,
  how the two sources are kept from disagreeing, and what a recorded answer
  means for a cell.

### Modified Capabilities
<!-- component-matrix's judging requirements live in the unarchived
     prompt-component-matrix change, not under openspec/specs/, so there is no
     delta file to write against them. The requirements below supersede its
     "forced choice over the catalogue" reading; reconcile when that change
     archives. -->

## Impact

- `backend/db.py`: new `reading` table and its migration.
- `backend/main.py`: reading CRUD, the judge-pass union and its refusal, and the
  scoring in `judge_shot`.
- `frontend/src/judge.js`: choices come from readings, not from `slotChoices`
  over the catalogue.
- `frontend/src/views/Judge.jsx`, `frontend/src/views/Catalogue.jsx`: render and
  edit them.
- `docs/catalogue-candidate-prompt.md`, `README.md`, `docs/` page for the
  catalogue screen.
