## 1. The listing route

- [x] 1.1 Add a read-only route to `backend/main.py` listing finished, un-rejected photographs across every session at or above a `min_rating`, each entry carrying at least the shot id and its session's name; verify by hand against the live database that a threshold of 4 returns entries from more than one session
- [x] 1.2 Add route tests to `tests/test_api.py` covering the inclusive threshold (a 4 is listed at `min_rating=4`, a 3 is not), `min_rating=0` listing the never-rated, and rejected / pending / failed shots being excluded; verify `python -m pytest tests/test_api.py` passes
- [x] 1.3 Add tests for the empty result succeeding as an empty list, and for the route leaving every rating, shot and session unmodified; verify `python -m pytest` is green

## 2. The slideshow screen

- [x] 2.1 Add the slideshow view under `frontend/src/views/` and route it from `App.jsx`, reading interval, threshold and look-ahead from the hash with clamped fallbacks; verify by opening the screen with an absent, an out-of-range and a non-numeric value in the hash and seeing it play at defaults in each case
- [x] 2.2 Implement the deck: a Fisher–Yates shuffle over the fetched set, re-drawn on exhaustion; verify by logging the order over one full pass at `min_rating=4` that all thirteen appear before any repeats, and that a second pass differs from the first
- [x] 2.3 Implement look-ahead preparation using `new Image()` plus an awaited `decode()` for the next N photographs, swallowing per-photograph failures; verify in the browser network panel that upcoming photographs are fetched before their turn, and that deleting a file mid-play skips rather than stops
- [x] 2.4 Present the photograph full screen via the Fullscreen API, whole and uncropped, with a way back to the app and no control that rates, rejects, deletes, edits or generates; verify by opening the screen and confirming a portrait photograph is fully visible and that leaving returns to the app
- [x] 2.5 Handle the empty and single-photograph sets: say so plainly when nothing meets the threshold, and play without failing when exactly one does; verify at `min_rating=5` and at a threshold matching nothing
- [x] 2.6 Rebuild the set when the threshold changes and apply a changed interval without restarting the set; verify by raising the threshold mid-play and seeing the set narrow, then changing the interval and seeing the current photograph hold its place
- [x] 2.7 Add a navigation entry for the slideshow in `App.jsx`; verify `npm --prefix frontend run build` succeeds
- [x] 2.8 Add on-screen controls for the interval and the rating threshold to the slideshow's overlay, each showing the value currently in effect and each writing its change back to the hash so the address keeps carrying the configuration; verify in the browser that changing the threshold rebuilds the set, that changing the interval retunes the timer without restarting the set, and that the address updates to match
- [x] 2.9 Make the interval control show an address-supplied value that is not one of its listed choices rather than appearing blank; verify by opening the screen at an interval of 7 and reading the control back

## 3. Network access

- [x] 3.1 Give `start.bat` an optional host argument defaulting to the loopback address it uses today; verify that running it with no argument still serves on loopback only and that another device on the network cannot reach it
- [x] 3.2 Add `start-lan.bat` that prints a warning naming the exposure and the absence of authentication, then calls `start.bat` with the all-interfaces host and no copy of the bootstrap; verify a phone on the same network loads the app and that the warning is printed before the server starts
- [x] 3.3 Verify an unrecognised or empty argument to `start.bat` falls back to loopback rather than opening to the network

## 4. Documentation

- [x] 4.1 Document the slideshow screen and its three settings in `README.md` and the matching page under `docs/`, including that `min_rating=0` is what makes it useful before rating has happened; verify the new screen and the new entry point are both findable from `README.md`
- [x] 4.2 Add to `docs/known-limitations.md`: the display sleeping mid-slideshow on plain HTTP and setting the phone's display timeout as the answer, the unauthenticated exposure `start-lan.bat` creates, and the interval below which the network rather than look-ahead is the limit
- [x] 4.3 Add to `docs/troubleshooting.md` the Windows Firewall rule being bound to the exact executable path, so the first `start-lan.bat` run raising a fresh prompt has an answer already written

## 5. Gates

- [x] 5.1 Run `python -m pytest` and confirm it is green with no `-k` subset
- [x] 5.2 Run `npm --prefix frontend run build` and confirm it succeeds, with `frontend/dist/` left out of the commit
- [x] 5.3 Confirm no personal data, machine path or IP address entered the repository — the new documentation is the likely place — and that `tests/test_no_personal_data.py` passes
