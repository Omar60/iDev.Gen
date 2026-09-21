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
intact. Task 3.3 is now formally closed. The next pending task is 3.4 and must
not be started automatically.

Task 3.3 adds optional proposals through
`POST /api/resources/libraries/{library_key}/translations/proposals`, capped at
twenty source entries per action. Scalar values and list items are independent
entries. Requests carry only `source_id`, `content_digest`, canonical `field`,
`source_shape`, and `list_index`; source text and eligibility are resolved
server-side. Only pending authorized `ROLE_DESCRIPTIVE_INPUT` rows are used;
existing translations are not implicitly overwritten and `identity_required`
rows do not consume the assistant.

The endpoint reuses `backend.enhance.run_structured(...)`; provider keys,
canonical/list order, and repeated-source identities are server-owned. Strict
schema validation rejects unsolicited fields and coercion of `list_index`, and
schema/stale/ineligible diagnostics are sanitized. Provider output must be an
exact `entry-0` through `entry-N` English-string object; extra, missing,
malformed, or non-English output rejects the whole proposal. Generation is
write-free: no sidecar, attestation, automatic Preview/Apply, or direct
revision shortcut. Suggest responses are fenced across edits, row reloads,
global/cross-library reloads, and a second Suggest; global reload clears
`proposalBusy`. The existing feature flag returns 503 before body/schema/source
or provider processing when disabled, without calling `run_structured`.
Tasks 3.1 and 3.2 remain compatible; Tasks 3.4 and 3.5 were not implemented.

## Verification

- Reproducible backend collection: 2,114 tests.
- Complete backend suite: 2,114 passed.
- Complete frontend suite: 373 passed.
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

`backend/enhance.py`, `backend/resource_translation.py`,
`backend/request_limits.py`, external handoffs, and Task 3.4+ files are
outside this closure scope. No push is authorized.
