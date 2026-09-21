# Latest Session Work

## Detailed Current State

OpenSpec Task 3.2 of `simplify-resource-session-workflow` passed independent
acceptance and is formally closed. `GET
/api/resources/libraries/{library_key}/translations/rows` exposes a safe
source-backed projection without raw payloads, paths, or internal fingerprints.
`buildTranslationMapFromRows(rows)` builds a direct `translation_map` while
preserving scalar/list contracts and keeping list items as independent scalar
entries.

Required already-English strings receive explicit identity translations;
optional English strings do not. Compatible duplicate source strings merge
deterministically. Incompatible translation or shape duplicates produce an
explicit diagnostic, and the workflow never uses last-write-wins. Existing
valid sidecars are preserved. Corrupt sidecars are diagnosed and do not create
editable rows. Public diagnostics are sanitized.

Manual Preview uses canonical bulk `/translations/preview`, and manual Apply
uses canonical bulk `/translations/apply`. Attestation, map digest, and library
fingerprint remain authoritative. `apply_revision_translation` and the
single-revision endpoint are not used in this flow. Editing, reload, or Apply
errors invalidate preview/token authorization, and late responses cannot
reactivate stale authorization.

Task 3.1's selected-map flow and `map_path`/direct-map compatibility remain
intact. Task 3.3 is the next pending task and must not be started automatically.

## Verification

- Backend focal suite: 115 passed.
- Frontend focal suite: 130 passed.
- Reproducible backend collection: 2,072 tests.
- Complete backend suite: 2,072 passed.
- Complete frontend suite: 362 passed.
- Frontend build: successful.
- Privacy checks: 10 passed.
- Shoot checks: 36 passed.
- Strict OpenSpec validation: 1 passed, 0 failed.
- `git diff --check`: clean.

## Closure Scope

- `backend/main.py`
- `backend/resource_service.py`
- `frontend/src/resources.js`
- `frontend/src/views/Resources.jsx`
- `tests/test_resource_service.py`
- `tests/test_resource_translation.py`
- `frontend/src/resources.test.js`
- `frontend/src/views/Resources.test.jsx`
- `openspec/changes/simplify-resource-session-workflow/tasks.md`
- `agent_docs/project_progress.md`
- `agent_docs/project_diary.md`
- `agent_docs/latest_session_work.md`

`backend/resource_translation.py`, `backend/request_limits.py`, external
handoffs, and Task 3.3+ files are outside this closure scope. No push is
authorized.
