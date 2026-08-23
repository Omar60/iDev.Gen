## 1. Tags on a session

- [x] 1.1 Add the tags column to `session` in `backend/db.py`, both in `SCHEMA`
      and in `_migrate` so a database that predates it keeps every row. No new
      table and no join. Verify: `python -m pytest` green, and
      `grep -c "CREATE TABLE" backend/db.py` still reports 4.
- [x] 1.2 Accept tags on the existing `PATCH /api/sessions/{sid}` and return
      them from the session reads, trimming, de-duplicating case-insensitively
      and dropping empties. Verify with the tests in 4.1.
- [x] 1.3 Carry the tags across a session clone. Verify with the clone test in
      4.1.

## 2. The list route

- [x] 2.1 Add `GET /api/sessions` to `backend/main.py` taking optional `q` and
      `tag`, listing across every model, newest first, and returning an empty
      list rather than an error when nothing matches. Verify with the tests in
      4.1.
- [x] 2.2 Match `q` case-insensitively as a substring of the session's name,
      look and wardrobe; match `tag` as a whole tag. Both given, both must hold.
      Verify with the `night` / `nightclub` test in 4.1.
- [x] 2.3 Include the cover photograph's id on each listed session, so the
      screen shows one frame per row without a request per row. Verify with the
      cover test in 4.1.

## 3. The screen

- [x] 3.1 Add a **Library** screen and its nav entry in `frontend/src/App.jsx`,
      listing the route's results with a search box and the tags in use, each
      row linking to its session and an explicit message when nothing matches.
      Verify: `npm --prefix frontend run build` succeeds.
- [x] 3.2 Add the tag editor to `frontend/src/views/SessionView.jsx` and the two
      calls to `frontend/src/api.js`. Verify: `npm --prefix frontend run build`
      succeeds, and adding a tag then reloading the session still shows it.

## 4. Tests and docs

- [x] 4.1 Add tests to `tests/test_api.py` covering each scenario in the spec:
      tagging a shot session, the same tag in two cases, an empty tag, a clone,
      text across models, a match in the wardrobe, whole-tag matching, both
      filters at once, no filters, no match, and the cover id. Use the existing
      fixtures — no GPU, no ComfyUI, no network. Verify: `python -m pytest`
      green.
- [x] 4.2 Document the screen and the tags in `README.md` and
      `docs/sessions.md`, and narrow the "Rating is per shot and local" entry in
      `docs/known-limitations.md` to what is still missing. Verify:
      `grep -rn "no search" docs/known-limitations.md` returns nothing that is
      now false, and every sentence added describes code that exists.
- [x] 4.3 Run the full gate: `python -m pytest` green and
      `npm --prefix frontend run build` succeeds, with `frontend/dist/` left out
      of the commit. Verify: `git status --porcelain` lists no build output.
