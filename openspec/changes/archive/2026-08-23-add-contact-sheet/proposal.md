## Why

A finished session is dozens of photographs in a folder. Judging a shoot as a
whole — which takes worked, where the arc sagged — means opening them one at a
time, or scrolling a gallery that shows a handful at once. A contact sheet is
the one view that puts the whole shoot on a page, and it is the last thing
`docs/known-limitations.md` still lists as missing now that the export exists.

## What Changes

- A new `GET /api/sessions/{sid}/contact-sheet` returns a single image laying
  out the session's selected photographs as a grid, filtered by a `min_rating`
  query parameter with the same meaning it has on the export.
- Each cell is labelled, so a frame picked off the sheet can be found again
  among the session's files.
- A **Contact sheet** control in `SessionView`, beside the existing
  **Download picks**.
- `README.md`, `docs/sessions.md` and the "No contact sheet" entry in
  `docs/known-limitations.md` are updated.

Not in scope: printing, page breaks or paper sizes; a sheet across sessions;
choosing the layout from the UI; and putting the prompts on the sheet.

## Capabilities

### New Capabilities
- `contact-sheet`: seeing a whole shoot on one page, at a size where the takes
  can be compared against each other rather than one at a time.

### Modified Capabilities

(none — no existing requirement changes)

## Impact

- `backend/main.py`: one new read-only route. It reads the same rows and the
  same folder as `export_session`, which is the closest existing route.
- **Pillow is the image library for this.** It is already installed in the
  development venv but missing from `backend/requirements.txt`; add it there,
  pinned the way the existing entries are. This is decided — do not substitute
  another library and do not hand-roll the composition.
- `frontend/src/views/SessionView.jsx`: one control, beside the export's.
- `frontend/src/api.js`: the sheet is fetched as a plain link, like the export.
- Read-only with respect to the session: the route reads the session's images
  and writes nothing into the session folder.
- `tests/test_api.py`: exercised with the existing fixtures — no GPU, no
  ComfyUI, no network. Pillow can both write the fixture images and read the
  response back, so the assertions do not need a real shoot.
