# Latest Session Work

## Detailed Current State

OpenSpec Task 3.1 of `simplify-resource-session-workflow` passed independent
acceptance and is formally closed. The selected translation-map interface uses
the server-owned tuple `selection_id`, `file_id`, and `expected_revision`.
Selection/file/revision/state/target/type checks remain enforced before the
selected content is consumed.

The staged file is the exclusive byte authority. The resolver opens the staged
file once and materializes `raw_bytes`; handle metadata and those bytes provide
the size, mtime, SHA-256, fingerprint, candidate inspection, UTF-8 decoding,
and JSON parsing inputs. The TOCTOU regression test replaces the path after
materialization and proves that the already-read map remains the processed map.

Selected preview delegates to `preview_translation_map()`. Selected apply
delegates to `apply_translation_map()`. `apply_revision_translation()` is not
used in this flow, and the historical bulk validator remains the only semantic
authority for source/translation types, fields, duplicates, authorization,
canonical digest, attestation, and library-fingerprint checks.

Both translation routes use `RequestLimitRoute` with the actual 10 MiB body
boundary. Missing or false-small `Content-Length` bodies over the limit return
413 before Pydantic/parser/domain work or writes. Exact 10 MiB and 10 MiB plus
one byte are covered. Malformed and semantically invalid selected maps return
controlled, sanitized errors; the invalid-map regression proves 422 and zero
translation writes.

Task 3.2 is the next pending task and must not be started automatically.

## Verification

- `tests/test_resource_translation.py`: 59 passed.
- `tests/test_request_limits.py`: 27 passed.
- `tests/test_resource_selection_api.py`: 414 passed.
- `tests/test_resource_selection.py`: 95 passed.
- Reproducible full collection: 2,063 tests.
- Complete suite: 2,063 tests, green.
- Privacy checks: 10 passed.
- Shoot checks: 36 passed.
- Strict OpenSpec validation: 1 passed, 0 failed.
- `git diff --check`: clean apart from line-ending warnings.

## Closure Scope

- `backend/main.py`
- `backend/resource_selection.py`
- `tests/test_resource_translation.py`
- `openspec/changes/simplify-resource-session-workflow/tasks.md`
- `agent_docs/project_progress.md`
- `agent_docs/project_diary.md`
- `agent_docs/latest_session_work.md`

No push is authorized. Do not begin Task 3.2 automatically.
