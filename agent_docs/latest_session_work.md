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
intact. Tasks 3.3, 3.4, and 3.5 are now formally closed. Task 4.1 remains
pending and Task 4 has not been started.

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
Tasks 3.1 and 3.2 remain compatible.

Task 3.4 is now formally closed. The Resource Browser distinguishes the
authoritative Imported outcome from Needs translation, Ready, and Needs source
correction. Direct actions are Create session, Translate, and Inspect
source/Re-import. Readiness refreshes after manual/map Apply and after import;
superseded proposal, manual-preview, and map-preview responses cannot restore
stale state. Safe readiness projections omit raw payloads and arbitrary
coverage/translation markers, while invalid persisted sidecars remain pending
and inspectable. Imported identity comes only from
`commit_result.files[].accepted` and uses the complete collision-free
`(library_key, source_id, content_digest)` identity.

The translation-map path remains editable during both Preview and Apply.
`mapOperation` fences Preview versus Apply, allows a real A-to-B edit and a
new Preview while A is pending, ignores late A, preserves B authorization, and
keeps Apply in flight while edits block incompatible actions. Successful Apply
still performs the authoritative readiness refresh.

Task 3.5 is now formally closed after independent acceptance. It is test-only:
the manual source-backed flow works without a map or assistant, distinguishes
missing translation from invalid source, diagnoses invalid sidecars, and keeps
provider failure recoverable through a valid manual Preview and explicit Apply.
The tests reject malformed provider output as a whole, unauthorized/stale/
ineligible proposals before the provider, 21-entry requests while accepting 20,
compatible and conflicting duplicates, stale attestation/library state, and
selected-map stale coverage. They exercise real bodies above 10 MiB with
missing or false `Content-Length`, remain write-free until Apply, and prove no
automatic Apply, session, or generation. No product defect was found.

## Verification

- Focused `tests/test_resource_translation.py`: 85 passed.
- Focused `tests/test_resource_service.py`: 79 passed.
- Complete backend suite: 2,121 of 2,121 collected tests passed.
- Focused frontend Resources suite: 78 passed.
- Combined resource frontend suites: 186 passed.
- Complete frontend suite: 418 passed.
- Frontend build: successful.
- Privacy checks: 10 passed.
- Shoot checks: 36 passed.
- Strict OpenSpec validation: 1 passed, 0 failed.
- `git diff --check`: clean.

## Closure Scope

- `tests/test_resource_translation.py`
- `frontend/src/views/Resources.test.jsx`
- `openspec/changes/simplify-resource-session-workflow/tasks.md`
- `agent_docs/latest_session_work.md`
- `agent_docs/project_progress.md`
- `agent_docs/project_diary.md`

No product code changed. Task 4.1 and later tasks are outside this closure
scope. No push is authorized.
