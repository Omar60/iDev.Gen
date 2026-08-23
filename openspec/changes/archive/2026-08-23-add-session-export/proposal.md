## Why

Picks live in the session folder as plain PNG files and there is no way to get a
selection out of the app. Keeping the best eight frames of a forty-photograph
shoot means reading the ratings on screen, then matching them by hand against
filenames in a file manager — the one place where the app's own knowledge of
which shot is which is unavailable. `docs/known-limitations.md` records the gap.

## What Changes

- A new `GET /api/sessions/{sid}/export` route streams the session's images as a
  single ZIP, filtered by a `min_rating` query parameter (default `1`).
- Each entry is named from the shot's position and rating, not the ComfyUI
  filename, so the archive reads in shooting order outside the app.
- A **Download picks** control in `SessionView` beside the existing rating
  filter, showing how many shots the current threshold selects and disabled at
  zero.
- `README.md` and `docs/sessions.md` describe the control; the "No contact sheet
  or export" entry in `docs/known-limitations.md` narrows to the contact sheet.

Not in scope: a contact sheet, exporting the prompts alongside the images, and
export across sessions. Rating stays per shot and local.

## Capabilities

### New Capabilities
- `session-export`: getting a rated selection of a session's finished images out
  of the app as a single file.

### Modified Capabilities

(none — no existing requirement changes)

## Impact

- `backend/main.py`: one new read-only route. It reads `shot.rating` and
  `shot.filename` and serves from `SESSIONS_DIR / str(session_id)`, the same
  path `shot_image` already uses.
- `frontend/src/views/SessionView.jsx`: one control. Already the largest view at
  852 lines.
- `frontend/src/api.js`: the download is a plain link to the route, not a fetch,
  so the browser names the file from `Content-Disposition`.
- No schema change, no new dependency: `zipfile` is stdlib and FastAPI already
  returns `FileResponse`.
- Read-only with respect to the session: nothing is moved out of the session
  folder, which keeps the "moving, not copying" hazard away from this path.
- `tests/test_api.py`: the route is exercised with the existing fixtures; no GPU,
  no ComfyUI, no network.
