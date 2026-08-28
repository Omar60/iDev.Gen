Assumption carried from design.md's open question: `framing` stays the single
fixed concept it is today, held as one row like any other component. Nothing
here decides which framings exist.

## 1. The store

- [ ] 1.1 Add the `component` table to `backend/db.py` per design D1/D2 — `id, concept_key, slot, manner, family, faces, wording, judge_label, retired_at, created_at`, `UNIQUE(slot, manner, wording)`, `CHECK` that `slot` is one of camera/act/framing, that `wording` and `judge_label` are non-empty, and that `judge_label <> wording` — and verify a test inserting a row whose label equals its wording is rejected by the database, not by Python
- [ ] 1.2 Add `contradicted INTEGER NOT NULL DEFAULT 0` to `cell` with `CHECK (arrived + contradicted <= judged)` and verify a test writing 5 arrived + 6 contradicted against 10 judged is rejected
- [ ] 1.3 Confirm `cell_state` is unchanged by 1.2 and add the test that says so: a cell with 10 judged, 0 arrived, 10 contradicted is `dead`, same as one with 10 judged and 0 arrived from misses
- [ ] 1.4 Write the migration that dumps `cell` to `data/cell-backup-<timestamp>.json` and then empties it (design D8), delete `EVIDENCE_SEED` and the row-count guard at `db.py:475-490`, and verify with a test that starts from a database holding rows: the dump file exists, holds every row, and the table is empty afterwards

## 2. The API

- [ ] 2.1 `GET /api/components` returning non-retired rows, `?all=1` including retired, grouped nowhere — flat rows, the client groups — and verify against a fixture database that a retired row is absent by default and present with the flag
- [ ] 2.2 `POST /api/components`, `PATCH /api/components/{id}`, `POST /api/components/{id}/retire` and `POST /api/components/{id}/restore`, and verify each refusal separately: an empty judge label, a label equal to the wording, and a duplicate `(slot, manner, wording)` each return 422 naming the field
- [ ] 2.3 `DELETE /api/components/{id}` deletes only a component with no judged photographs against it, and verify a test where a component carrying cell counts returns 422 offering retirement and the row survives
- [ ] 2.4 `POST /api/components/import` taking the seed file's shape, inserting what is absent by `(slot, manner, concept_key)` and reporting `{added, skipped}`, and verify importing the same file twice adds on the first call and adds nothing on the second
- [ ] 2.5 Extend the judge payload with an optional `defect` field per design D5 — a defect implies an empty answer, a defect sent with a component key is 422 — and verify the three cases (answer only, defect only, both) against the endpoint

## 3. The seed file

- [ ] 3.1 Generate `data/catalogue-seed.json` from today's constants: every entry in `CAMERA_POSITIONS`, `CANDID_POSITIONS`, `SELFIE_POSITIONS`, `ARRANGEMENTS` and `FRAMING_CONCEPT`, carrying `concept_key`, `slot`, `manner`, `family`, `faces`, `wording`, and the verdict and sample size the `kinds.js` comments record for it — and verify the file round-trips through `/api/components/import` to a catalogue whose wordings are byte-identical to the constants
- [ ] 3.2 Write the `judge_label` for every entry, taking the camera and arrangement labels from the viewer wordings already in `scripts/judge_camera.py` (`POSITION`, `TURN`, `ARRANGEMENT`), and verify no label equals its wording and no two labels in one slot are identical
- [ ] 3.3 Fill `faces` per design D2 — `front`, `side`, `back`, or empty — and verify a test asserting the shoulder and behind families are `back`, the mirror and pov families are empty, and no camera row is left unset by accident (an explicit empty is a value, an omission is a bug)

## 4. The readers

- [ ] 4.1 Add the catalogue holder to `kinds.js` (`setCatalogue`, `positionsFor(manner)`, `arrangements()`, `framings()`) and make `cameraPlan`, `fitCameras`, `kissCameraFor` and `candidatePool` read through it, keeping every existing signature, and verify `npm --prefix frontend test` stays green with the holder loaded from a fixture that reproduces today's constants
- [ ] 4.2 Load the catalogue in `App.jsx` before the first route renders and verify in the browser preview that the shots editor's camera dropdown is populated from the API and empty when the store is empty
- [ ] 4.3 Delete `CAMERA_POSITIONS`, `CANDID_POSITIONS`, `SELFIE_POSITIONS`, `ARRANGEMENTS`, `FRAMING_CONCEPT` and `POSITIONS` from `kinds.js`, moving the measurement prose beside them into `docs/` in the same commit that adds its replacement, and verify `npm --prefix frontend run build` succeeds and no import of a deleted name remains
- [ ] 4.4 Point the single-home test (`tests/test_one_home.py`) at the store and the named seed file per the `prompt-components` delta, and verify it fails when a catalogue wording is pasted into a second file and passes with the seed file present
- [ ] 4.5 Move `tests/test_arrangements.py`, `test_camera_plan.py` and `test_kiss_frames.py` off their node probes of the deleted constants onto a fixture catalogue, keeping every behaviour they assert, and verify each file's test count is unchanged or higher — a test dropped in the move is the regression this repo has already paid for

## 5. Empty-catalogue refusals

- [ ] 5.1 Refuse composition when a slot has no component for the session's manner, naming slot and manner and mentioning the import (`shot-composer` delta), and verify by calling `/api/sessions/{id}/compose` against an empty store that the response is 422 and `shot` has no new rows
- [ ] 5.2 Refuse creating a written session whose manner has an empty camera catalogue, naming the manner, and verify no shots are written
- [ ] 5.3 Refuse a session whose planted kiss frame names a camera component the manner's catalogue does not hold, and verify the photograph does not silently keep the camera it was dealt
- [ ] 5.4 Ask of 5.1-5.3 which branch no test executes, and add the missing one — every refusal above has a matching "and it does not refuse when the catalogue is populated" case

## 6. The catalogue screen

- [ ] 6.1 New route and view: components listed per slot and manner, showing wording, judge label, family, faces and cell counts, with retired rows visibly retired, and verify in the browser preview against a populated store
- [ ] 6.2 Add, edit, retire and restore from the screen, with the API's refusals shown as they are returned rather than re-derived client-side, and verify each refusal appears on screen by driving the form in the browser preview
- [ ] 6.3 The import control, offered when a slot is empty and never run automatically, and verify a fresh database still reads empty after the screen has been opened and closed without pressing it
- [ ] 6.4 Probe the finished screen from outside the app — call every catalogue endpoint directly with the payloads the screen sends and a few it should refuse — and report what the probe found rather than what the code reads like

## 7. The judge

- [ ] 7.1 `slotChoices` offers `judge_label` and never `wording` (`component-matrix` delta), and verify with a frontend test asserting no rendered choice string equals any component's wording — the test must fail against today's `judge.js:19`
- [ ] 7.2 Add the contradiction answer to `Judge.jsx` beside the choices and "none or cannot tell", posting `defect`, and verify the counts land on the right cell by reading the cell back after a judging pass in the browser preview
- [ ] 7.3 Report `contradicted` wherever a cell's counts are shown, distinct from misses, and verify a cell with 10 judged / 0 arrived / 7 contradicted reads differently on screen from one with 10 judged / 0 arrived / 0 contradicted
- [ ] 7.4 Point `scripts/judge_camera.py` at `GET /api/components` for its camera and act choice lists, keeping its per-question prose in the script, and verify a run against a stored session produces the same answers as before the change for the components the seed carries
- [ ] 7.5 Judge one already-judged session through the screen and report whether the contradiction answer changes what the pass concludes about it — the defect this change exists for is one the old pass could only record as "cannot tell"

## 8. Documentation and gates

- [ ] 8.1 Update `README.md` and the matching page under `docs/` with the catalogue screen, the empty first run, the import, the cell dump file and the contradiction answer, and verify each named path and route exists
- [ ] 8.2 Run the full gates — `python -m pytest`, `npm --prefix frontend run build`, `npm --prefix frontend test` — and report the output rather than the summary
- [ ] 8.3 Run the control-character and trailing-whitespace scan (`tests/test_shoot_checks.py`) over everything this change wrote, including the generated seed JSON, and report the output
