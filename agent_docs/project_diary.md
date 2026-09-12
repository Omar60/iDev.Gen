# Project Diary

## Decisions and Lessons

- Repository-authored code, comments, UI, and documentation are English-only
  and must not contain personal or machine-specific data.
- `AGENTS.md`, the README/docs, source code, tests, and OpenSpec artifacts are
  the project sources of truth; bootstrap documentation records only facts
  verified from them.
- Legacy sessions and the `resource-v1` session-planning path are deliberate
  compatibility boundaries. Future work must preserve their distinction and
  the no-GPU/no-network test boundary.
- The approved `simplify-resource-session-workflow` plan is implemented in task
  order. Task 1.1 is the first real dependency and remains backend-only:
  persisted browser-import selection/file/claim state, reservations, recovery,
  and cleanup. HTTP contracts, canonical import bridging, compatibility
  adapters, and UI belong to later tasks.
- The Task 1.1 architecture uses an additive selection table plus an ordered
  selection-file table, with the active commit claim stored on the selection
  row and acquired by conditional transactional state change. This is enough
  to prove single ownership without introducing a queue or generic operation
  framework early.
- Browser-import staging must remain separate from the existing path-import
  attestation files. Cleanup targets only opaque staged files, remains
  retryable after a terminal transition, and never infers or deletes committed
  resource state.
- Filesystem deletion is not transactional with SQLite. A helper called inside
  a nested `db.transaction()` cannot delete staged bytes before the outermost
  commit: a later rollback restores the database but cannot restore the file.
  Persist the terminal transition first, clean afterward, then persist the
  verified cleanup result or warning separately.
- File finalization must keep one durable path to the bytes across every crash
  boundary. Renaming before the manifest transaction commits can leave the row
  pointing at a missing old name and recovery falsely deleting only that name.
- Bounded recovery needs deterministic forward progress. Limiting an
  unfiltered candidate query can repeatedly revisit ineligible tombstones and
  starve expired claims or cleanup failures; tests must prove both the bound and
  progress. A concurrency test must execute concurrent workers rather than
  assert the same outcomes sequentially.
- Post-commit deletion is a rule for every valid staged-byte removal path, not
  only terminal cancel/commit helpers. File removal and expiry branches must
  also preserve bytes when an enclosing transaction rolls back; checking every
  `_safe_delete_file` and `_clean_selection_files` caller is the reliable
  acceptance method.
- A post-commit callback is not durable work by itself. Recovery queries must
  explicitly select the database state left between a successful commit and a
  process stopping before its callback runs. For file removal, that means an
  open selection can own a `removed` file with pending cleanup; startup,
  record-local, and opportunistic recovery must all make bounded progress on
  that state without waiting for selection expiry.
- Task 1.1 acceptance requires reproducing the callback-loss crash window, not
  only exercising the normal callback. The accepted recovery keeps the removed
  file outside the manifest, preserves revision, counters, and invalidated
  preview fields, and changes only cleanup evidence while startup, record-local,
  and bounded opportunistic paths remove the recorded staged bytes.
