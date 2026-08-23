## 1. The route

- [x] 1.1 Read `reshoot_shot` in `backend/main.py` end to end first, docstring and
      comments included, and list its refusals before writing anything. Verify:
      every refusal it makes appears in what you write.
- [x] 1.2 Add `POST /api/sessions/{sid}/reshoot-below` taking `min_rating`,
      returning 404 for an unknown session and 400 when nothing qualifies.
      Verify with the tests in 3.1.
- [x] 1.3 Re-queue each finished shot under the threshold, rejected ones
      included: delete the image, then clear status, filename, prompt id, error,
      seed, rejected and finish time. Verify: a re-queued row reads pending with
      an empty filename and a zero seed, and its file is gone from the session
      folder.
- [x] 1.4 Skip a running shot and skip any shot in the session's
      `anchor_shot_ids`, without failing the request. Verify: the anchor's image
      is still on disk after a call that re-queued its neighbours.
- [x] 1.5 Answer with the re-queued count and the skipped count. Verify: a call
      over one running shot and two finished ones reports two and one.
- [x] 1.6 Return a session reading done, cancelled or failed to draft, and leave
      any other status alone. Verify with the two status tests in 3.1.

## 2. The control

- [x] 2.1 Add a **Reshoot below** control to `frontend/src/views/SessionView.jsx`
      beside the rating filter and the export, showing how many shots it would
      re-queue and disabled at zero. Verify: `npm --prefix frontend run build`
      succeeds and the count matches the cards on screen.
- [x] 2.2 Confirm before sending, naming the count, because the action deletes
      photographs. Verify: declining sends no request.
- [x] 2.3 Refresh the session after it returns so the re-queued cards show as
      pending. Verify by eye in the running app, or state that you could not.

## 3. Tests and docs

- [x] 3.1 Add tests to `tests/test_api.py` covering every scenario in
      `openspec/changes/add-bulk-reshoot/specs/bulk-reshoot/spec.md` — there are
      twelve, and each one is a test. Use the existing fixtures; no GPU, no
      ComfyUI, no network. Verify: `python -m pytest` green.
- [x] 3.2 Document the control in `README.md` and `docs/sessions.md`, and narrow
      the retry entry in `docs/known-limitations.md`. Verify: read each sentence
      you wrote against the route as implemented, not against this task list, and
      say which sentences you checked.
- [x] 3.3 Run the whole gate and paste the output:
      `python -m pytest`, `npm --prefix frontend run build`, and
      `git diff | grep -nE "^\+.*[ \t]+$"` which must print nothing.
