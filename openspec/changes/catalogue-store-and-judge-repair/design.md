## Context

See `proposal.md` — Why. The state that shapes the approach:

- The catalogue is four JavaScript constants in `frontend/src/kinds.js`
  (`CAMERA_POSITIONS`, `CANDID_POSITIONS`, `SELFIE_POSITIONS`, `ARRANGEMENTS`,
  plus the single `FRAMING_CONCEPT`), read from exactly four places:
  `cameraPlan` and `fitCameras` in `kinds.js`, `kissCameraFor` in `kinds.js`,
  `candidatePool` in `compose.js`, and `slotChoices` in `judge.js`. Two of those
  already take the catalogue as an argument; two reach for the module constant.
- The backend never holds the catalogue. `ComposeIn` receives `camera`, `act`
  and `framing` as `{key, wordings}` dicts from the client and stores the keys on
  the shot. The cell table is keyed on those keys.
- `EVIDENCE_SEED` (`backend/db.py:251`) is a hand-translation of measurements
  written as prose comments in `kinds.js`, with a row-count guard at
  `db.py:475-490` that refuses to start if the table has a count the seed did not
  produce.
- Several Python tests run node against `kinds.js` to read the constants
  (`tests/test_arrangements.py`, `test_camera_plan.py`, `test_kiss_frames.py`,
  `test_one_home.py`, `test_shoot_checks.py`).
- This repo's recurring failure, recorded across several changes, is two
  calculations that disagree: a value derived in the frontend and re-derived in
  the backend. Anything added here has to have one home.

## Goals / Non-Goals

**Goals:**

- One home for a component's text, and it is a row.
- The existing synchronous, pure catalogue readers keep working with an
  argument, not a promise: `cameraPlan(n, rand, positions)` is already the right
  shape and the tests depend on it.
- The judging screen never renders prompt text, and that is checkable by a test
  rather than by reading the screen.
- A contradiction is a counted outcome, not a comment on a photograph.

**Non-Goals:**

- No editing of prompt *instructions* from the UI. This change moves the
  catalogue, not `SHOOT_LINE_INSTRUCTION` or the manner briefs; those stay in
  source.
- No reference-image upload. `prompt-components` allows a concept to carry one;
  nothing in this change adds it.
- No automatic repair of a contradictory line. Recording the defect is this
  change; acting on it (the `problemsWith` check discussed on the branch) is a
  later one that needs this count to know whether it worked.
- No multi-user concerns. Single local operator, no auth, as everywhere else.

## Decisions

### D1. One table, one row per wording, grouped by concept key

`component(id, concept_key, slot, manner, family, faces, wording, judge_label,
retired_at, created_at)`, unique on `(slot, manner, wording)`.

The spec says evidence attaches to the wording and a concept groups wordings.
Two tables (`concept` + `wording`) model that literally and buy a join on every
read for a grouping that only the catalogue screen uses — every drawing path
takes the first available wording of a concept. A `concept_key` column gives the
grouping without the join, and the uniqueness that matters (no two components
with the same text in the same slot and manner) is a constraint on this table.

`manner` is a column and not a join table: a component belongs to `directed`,
`candid` or `selfie`, and the two shared today (`shoulder-left`,
`shoulder-right`) are two rows. Duplicating a row is cheaper than a many-to-many
for a catalogue that will hold tens of rows, and per-manner evidence is already
how the cell table is keyed — the same wording under two manners is genuinely two
things to measure.

Alternative rejected: keeping the catalogue in JSON on disk and reading it as a
file. It gets the operator nothing SQLite does not, and the evidence it must
join against is already in SQLite.

### D2. `faces` is a new column; `family` keeps its job

`family` is the spreading key — it stops two overhead cameras landing back to
back — and its values (`front`, `shoulder`, `mirror`, `pov`, `overhead`,
`floor`, `side`, `behind`) mix "which side of her" with "what the device is
doing". `mirror` and `pov` say nothing about which way she is turned.

So `faces` is its own column: `front`, `side`, `back`, or empty for "does not
constrain". It is what makes the contradiction in `prompt-components` machine-
readable, and it is what a later repair check reads. Overloading `family` would
force `mirror` to lie about the body.

### D3. The backend serves the catalogue; the frontend loads it once at boot

`GET /api/components` returns every non-retired row (and, with `?all=1`, the
retired ones for the catalogue screen). `App.jsx` fetches it before the first
route renders and hands it to a small module-level holder in `kinds.js`
(`setCatalogue(rows)` / `positionsFor(manner)`), so `cameraPlan`, `fitCameras`,
`kissCameraFor`, `candidatePool` and `slotChoices` stay synchronous and pure —
three of them already take the list as a parameter and the other two get one.

Alternatives rejected: threading an async catalogue through every caller (a large
diff across `enhance.js`, whose functions are the ones most covered by tests),
and duplicating the catalogue into the backend so both sides can draw (this is
exactly the two-calculations-that-disagree failure, and the backend does not draw
— it validates a trio the client already picked).

### D4. The judge label is required, and enforced at the boundary

`judge_label` is `NOT NULL` with a `CHECK` that it is neither empty nor equal to
`wording`. The API refuses a save that violates it, naming the field. The
screen's own test asserts that no rendered choice text equals any component's
`wording`.

A "should be different" rule that lives only in a code review is the rule that
produced today's defect: `slotChoices` reaching for `wordings[0].text` is one
plausible line, and nothing failed.

### D5. The contradiction is a third count on the cell

`cell` gains `contradicted INTEGER NOT NULL DEFAULT 0` with
`CHECK (arrived + contradicted <= judged)`.

The shot's `verdicts` JSON gains `"<slot>_defect": "contradiction"` beside the
existing `"<slot>": "<key>"` answer, so the per-photograph record says which slot
the contradiction was seen in. The judge payload gains an optional `defect`
field; a defect implies an answer of "" (none of the above), and the endpoint
refuses a defect sent together with a component key rather than guessing which
the operator meant.

`cell_state` does not change: a contradiction is a miss, and a cell that
contradicts eight times in ten is dead by the same ratio as one that renders the
wrong camera eight times in ten. The count exists to tell the operator *why*
before they spend another ten photographs.

Alternative rejected: a free-text defect note. It cannot be counted, and this
project has a measured history of findings that were sentences nobody could
aggregate.

### D6. The measured set ships as `data/catalogue-seed.json`, imported by hand

The wordings currently in `kinds.js`, with the judge labels written for them,
are exported once into a checked-in JSON file. `POST /api/components/import`
takes it and inserts what is not already present by `(slot, manner, concept_key)`.
Nothing calls it on startup.

This is what keeps "start from zero" from meaning "the app cannot take a
photograph until you have typed nine camera positions". The judge labels for the
existing camera families come from `scripts/judge_camera.py`, which already has
them: its `POSITION`, `TURN` and `ARRANGEMENT` blocks are viewer-worded
descriptions of exactly these components, and they are the reason the vision
judge could ask the question the screen could not.

### D7. `judge_camera.py` reads the catalogue over the API

The script already posts to `/api/enhance`; it gets its choice lists from
`GET /api/components` instead of its module-level constants, and its per-question
prose (what "arrangement" or "turn" means) stays in the script because that is
the question, not the choices.

The measured-set export in D6 goes the other way once, at implementation time:
the script's existing viewer wordings become the seed file's judge labels. After
that, the catalogue is the home and the script reads it.

### D8. The cell table is dumped before it is wiped

Startup migration: if `cell` has rows, write them to
`data/cell-backup-<timestamp>.json`, then delete them. The seed and its row-count
guard are removed in the same step.

The rows are cheap to keep and the operator has judged real sessions into this
table on the current branch. A wipe with no dump is the one step of this change
that cannot be undone.

## Risks / Trade-offs

- **A fresh install cannot shoot at all until components are added.** → The
  import in D6 is one click from the catalogue screen, and both refusal messages
  (composer and session create) name it.
- **Wiping `cell` throws away real judging passes.** → D8 dumps first. The
  restore path is an import of that dump; it is not built in this change, and the
  file is documented so the operator knows it is not decorative.
- **The `kinds.js` comments are the project's measurement record.** Deleting the
  constants deletes the prose beside them. → The comments move with the data:
  each seed entry carries its measured verdict and sample size in the JSON, and
  the prose that is a finding rather than a caption moves to `docs/`. Nothing is
  deleted in the same commit that adds its replacement.
- **Five Python tests probe `kinds.js` through node.** → They become tests over
  the API and the store. The behaviour they cover (spreading, no two adjacent
  families, kiss frames overriding a camera) is unchanged and must stay covered;
  a test deleted rather than moved is a regression this repo has been bitten by.
- **A retired component still referenced by a queued session.** → Retirement is a
  timestamp, not a delete; resolution by key ignores it, only drawing filters it.
- **Two calculations disagreeing.** → The backend never draws. It validates the
  trio it is handed against the store and refuses what it cannot resolve; the
  frontend never invents a component the store did not serve it.

## Migration Plan

1. Schema first, in one release: new `component` table, `cell.contradicted`, no
   readers yet.
2. Export the current constants and the script's viewer wordings into
   `data/catalogue-seed.json` (a one-time generation, checked in).
3. Switch every reader to the store; delete the constants; delete
   `EVIDENCE_SEED` and its guard; dump and wipe `cell`.
4. Ship the catalogue screen and the judging-screen changes together — the
   judge label is required by then, and the screen is the only way to write one.

Rollback is the previous commit: the constants are in git, and the store is
additive until step 3.

## Open Questions

- Whether `framing` gets a real catalogue in this change or stays the single
  fixed concept it is today. The store holds it either way and the screen can
  list one row; deciding which framings exist is a measurement decision of the
  same weight as the camera catalogue and does not block anything here.
