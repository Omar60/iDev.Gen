## Why

The component catalogue is not data. Cameras, acts and framings are JavaScript
constants in `frontend/src/kinds.js` (`CAMERA_POSITIONS`, `CANDID_POSITIONS`,
`SELFIE_POSITIONS`, `ARRANGEMENTS`, `FRAMING_CONCEPT`), so adding a camera means
editing source and shipping a build, and the operator sitting in front of the
app cannot add one at all. The evidence store the matrix change built (`cell`)
holds counts about wordings it cannot enumerate, and its `EVIDENCE_SEED` carries
verdicts translated by hand out of comments in that same file — a second copy of
the catalogue that no code keeps in step with the first.

The judging screen inherits the same source and reads the wrong text out of it.
`slotChoices` (`frontend/src/judge.js:19`) offers the operator the wording the
*writer* is handed — `Taken from behind her left shoulder, her back
three-quarters to the camera` — while `scripts/judge_camera.py:236` deliberately
does the opposite for the vision judge, re-wording every choice for someone
looking at a photograph and not for someone composing one. A judge shown the
prompt's own sentence is being told what to answer, which is the one thing the
blind pass exists to prevent.

And the screen has no way to record the failure the operator actually sees. A
photograph rendered with her feet away from the camera and her torso and phone
turned back into it is not the drawn camera and not any other camera; it is a
contradiction between the two. Today it lands as "None or cannot tell", which
decrements nothing and names nothing, so the same defect is re-measured from
scratch every session.

## What Changes

- **BREAKING** The component catalogue moves from `kinds.js` constants into
  SQLite and is served by the API. The constants are deleted; the wordings that
  survive today's measurements are exported once to a checked-in JSON file the
  UI can import, and nothing imports it automatically.
- **BREAKING** The catalogue starts **empty**. A fresh database has no cameras,
  no acts and no framings, so composing and the writer's camera plan both refuse
  loudly until the operator adds components. The one-click import above is the
  way back to the measured set.
- **BREAKING** `EVIDENCE_SEED` is deleted and the `cell` table starts empty. Every
  trio reads `unknown` until it is re-measured through the judging screen.
- A catalogue screen: list, add, edit, retire and restore components per slot
  (`camera`, `act`, `framing`) and per manner, including each component's family
  and its judge label.
- Every component carries **two texts**: the `wording` placed into the prompt,
  and the `judge_label` shown to the operator on the judging screen, written for
  someone looking at a photograph. The judging screen reads `judge_label` and is
  refused a component that has none.
- The judging screen gains a **defect answer** beside the forced choice: the
  operator can record that the photograph contradicts itself (the body turned
  one way and the camera asking for another) rather than choosing "cannot tell".
  A defect is stored on the shot and counted on the cell.
- `scripts/judge_camera.py` reads its camera and act choices from the same
  catalogue rather than from its own hand-kept copies, so the vision judge and
  the human judge are asking one question.

## Capabilities

### New Capabilities
- `component-catalogue`: the catalogue as an editable store — where components
  live, what a component carries, who may change one, and what happens when the
  catalogue is empty.

### Modified Capabilities
- `prompt-components`: components are rows in a store rather than source
  constants, they carry a judge label beside the prompt wording, and a retired
  component stays readable without being drawable.
- `component-matrix`: no seeded evidence; the judging screen shows the judge
  label and never the prompt wording; a contradiction is a recordable answer
  with its own count.
- `shot-composer`: composing from an empty or unpopulated catalogue refuses and
  names what is missing, instead of drawing from a default.

## Impact

- `backend/db.py`: new catalogue tables; `EVIDENCE_SEED` and its migration guard
  deleted; `cell` unchanged in shape.
- `backend/main.py`: catalogue CRUD routes; the compose endpoint's draw and the
  judge endpoint's counts read the catalogue.
- `frontend/src/kinds.js`: the four catalogue constants removed; the readers
  (`cameraPlan`, `fitCameras`, `kissCameraFor`) take the catalogue as data.
- `frontend/src/enhance.js`, `compose.js`, `judge.js`, `views/ShotsEditor.jsx`,
  `views/Judge.jsx`: fed from the loaded catalogue; a new catalogue screen and
  its route.
- `scripts/judge_camera.py` and the shoot scripts that name wordings inline.
- `tests/test_arrangements.py`, `test_camera_plan.py`, `test_cell.py`,
  `test_kiss_frames.py`, `test_one_home.py`, `test_shoot_checks.py`: the ones
  that probe `kinds.js` through node for constants that will not exist.
- `README.md` and the matching page under `docs/`: the new screen, the empty
  first run, and the import.
