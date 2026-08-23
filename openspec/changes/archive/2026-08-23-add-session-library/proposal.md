## Why

Sessions are reachable only through the model that owns them: `ModelDetail`
lists a model's sessions and nothing lists across models. Finding "the balcony
shoot" means remembering which character it belonged to, opening that model and
scrolling. Rating is per shot and local, so there is no way to mark a session as
a whole either. `docs/known-limitations.md` records both gaps in one line —
"no tags, no collections across sessions, no search".

## What Changes

- A session carries free-text tags, edited in `SessionView`.
- A new `GET /api/sessions` lists sessions across every model, narrowed by a `q`
  text query and a `tag` query parameter, newest first, each result carrying its
  cover photograph.
- A new **Library** screen listing those results, with a search box and the tags
  currently in use, each row linking through to its session.
- `README.md`, `docs/sessions.md` and the tags/search entry in
  `docs/known-limitations.md` are updated.

Not in scope: tags on shots, tags on models, renaming or merging a tag, saving a
look for reuse across sessions, and exporting across sessions.

## Capabilities

### New Capabilities
- `session-library`: marking sessions with tags, and finding one across every
  model without knowing which character shot it.

### Modified Capabilities

(none — no existing requirement changes)

## Impact

- `backend/db.py`: one added column on `session`, through the existing
  `_migrate` path. No new table and no join — this app has four tables on
  purpose.
- `backend/main.py`: one new read-only list route, and one field accepted by the
  session PATCH that already exists.
- `frontend/src/App.jsx`: one new screen and its entry in the nav.
- `frontend/src/views/SessionView.jsx`: the tag editor. Already the largest view.
- `frontend/src/api.js`: two calls.
- No new dependency. No schema change beyond the added column, and an older
  database keeps every session it has.
- `tests/test_api.py`: the route and the PATCH are exercised with the existing
  fixtures — no GPU, no ComfyUI, no network.
