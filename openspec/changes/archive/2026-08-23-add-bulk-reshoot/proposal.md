## Why

`docs/known-limitations.md` records the gap: retry only re-queues failed and
cancelled shots, and a photo that came out is re-rolled one at a time with
**↺ Reshoot**. After a forty-photograph shoot, refusing the weak frames means
forty decisions and forty clicks, one dialog at a time, on a page already
scrolled.

## What Changes

- A new `POST /api/sessions/{sid}/reshoot-below` route re-queues every finished
  shot of one session whose rating is under a `min_rating` query parameter,
  applying the same rules `POST /api/shots/{shot_id}/reshoot` applies to one.
- The route answers with what it re-queued and what it refused to touch, so the
  screen can say so rather than leaving the user to count cards.
- A **Reshoot below** control in `SessionView`, next to the existing rating
  filter and the export, stating how many shots it would re-queue.
- `README.md` and `docs/sessions.md` describe it; the "Retry only re-queues
  failed and cancelled shots" entry in `docs/known-limitations.md` narrows.

Not in scope: reshooting across sessions, a different prompt on the reshot take,
and any change to `⟳ More like this` or to single-shot **↺ Reshoot**.

## Capabilities

### New Capabilities
- `bulk-reshoot`: refusing a session's weak frames in one action, on the same
  terms as refusing them one at a time.

### Modified Capabilities

(none — no existing requirement changes)

## Impact

- `backend/main.py`: one new route. It has to reuse the single-shot rules rather
  than restate them; `reshoot_shot` is the reference, and its refusals are not
  decoration — read its docstring and the comments around the anchor check
  before writing anything.
- `frontend/src/views/SessionView.jsx`: one control, and a confirmation, because
  this one deletes photographs.
- No schema change, no new dependency.
- `tests/test_api.py`: exercised with the existing fixtures; no GPU, no ComfyUI,
  no network.
- Touches the session's status, which the Run button reads. `AGENTS.md` carries
  the invariants this sits on; the route is inside the same serial-queue world.
