## 1. The dependency

- [x] 1.1 Add Pillow to `backend/requirements.txt`, pinned the way the existing
      entries are. It is already present in the development venv, so nothing
      needs installing to run the gates. Verify:
      `grep -i pillow backend/requirements.txt` prints one line.

## 2. The route

- [x] 2.1 Add `GET /api/sessions/{sid}/contact-sheet` to `backend/main.py`
      taking `min_rating` (default 1), returning 404 for an unknown session and
      400 when nothing meets the threshold. Verify with the tests in 4.1.
- [x] 2.2 Select the session's finished, unrejected photographs at or above the
      threshold, in shooting order, and leave out any whose file is missing.
      Verify with the rejected, unfinished and missing-file tests in 4.1.
- [x] 2.3 Compose the selection into one image with Pillow and return it as the
      response, under a filename carrying the session id. Nothing is written
      into the session folder. Verify: the test reads the response back with
      Pillow, and the session folder is byte-identical after the request.
- [x] 2.4 Label every cell so a frame can be traced back to its file, with two
      variations of one take distinguishable. Verify with the three-variation
      test in 4.1.

## 3. The control

- [x] 3.1 Add a **Contact sheet** control to
      `frontend/src/views/SessionView.jsx` beside **Download picks**, using the
      active rating threshold and disabled when it selects nothing. Verify:
      `npm --prefix frontend run build` succeeds.
- [x] 3.2 Make it a plain link to the route rather than a `fetch`, so the
      browser takes the filename from `Content-Disposition`. Verify by
      downloading from a session with picks and reading the saved filename.

## 4. Tests and docs

- [x] 4.1 Add tests to `tests/test_api.py` covering each scenario in the spec:
      the default threshold, a raised threshold, a rejected photograph, an
      unfinished shot, an empty selection, a missing file, an unknown session,
      the labels across three variations of one take, and the download name.
      Write the fixture images with Pillow and read the response back with it.
      Use the existing fixtures — no GPU, no ComfyUI, no network. Verify:
      `python -m pytest` green.
- [x] 4.2 Document the control in `README.md` and `docs/sessions.md`, and remove
      the "No contact sheet" entry from `docs/known-limitations.md`. Verify:
      `grep -rn "contact sheet" docs/known-limitations.md` returns nothing that
      is now false, and every sentence added describes code that exists.
- [x] 4.3 Run the full gate: `python -m pytest` green and
      `npm --prefix frontend run build` succeeds, with `frontend/dist/` left out
      of the commit. Verify: `git status --porcelain` lists no build output.

