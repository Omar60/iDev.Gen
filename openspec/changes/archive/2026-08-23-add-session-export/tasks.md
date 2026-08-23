## 1. The export route

- [x] 1.1 Add `GET /api/sessions/{sid}/export` to `backend/main.py`, taking
      `min_rating` (default 1), returning 404 for an unknown session and 400
      when nothing meets the threshold. Verify with the new tests in 3.1.
- [x] 1.2 Build the archive in memory with stdlib `zipfile` and return it as a
      streaming response whose `Content-Disposition` names the file after the
      session id. No new dependency. Verify: the response is a readable ZIP in
      the test, and `backend/requirements.txt` is unchanged.
- [x] 1.3 Name each entry from the shot's position and rating, zero-padded so a
      lexicographic sort is shooting order, keeping the original extension.
      Verify with the twelve-shot ordering test in 3.1.
- [x] 1.4 Skip a shot whose file is missing instead of failing the export, and
      read images only — nothing is moved, renamed or deleted. Verify: the
      missing-file test passes and the session folder is byte-identical after.

## 2. The control

- [x] 2.1 Add a **Download picks** control to `frontend/src/views/SessionView.jsx`
      beside the rating filter, reporting how many shots the active threshold
      selects and disabled at zero. Verify: `npm --prefix frontend run build`
      succeeds and the count matches the visible grid.
- [x] 2.2 Make it a plain link to the route rather than a `fetch`, so the browser
      takes the filename from `Content-Disposition`. Verify by downloading from
      a session with picks and reading the saved filename.

## 3. Tests and docs

- [x] 3.1 Add tests to `tests/test_api.py` covering each scenario in the spec:
      default threshold, raised threshold, empty selection, missing file,
      unknown session, entry ordering, and the download name. Use the existing
      fixtures — no GPU, no ComfyUI, no network. Verify: `python -m pytest`
      green.
- [x] 3.2 Document the control in `README.md` and `docs/sessions.md`, and narrow
      the "No contact sheet or export" entry in `docs/known-limitations.md` to
      the contact sheet. Verify: no doc still claims there is no export.
- [x] 3.3 Run the full gate: `python -m pytest` green and
      `npm --prefix frontend run build` succeeds, with `frontend/dist/` left out
      of the commit.
