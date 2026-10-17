## 1. Archive contract and persistence

- [x] 1.1 Add the archive tombstone table and domain operation; filter archived identities from active listing and reject writes to archived identities.
- [x] 1.2 Add the DELETE API route behind the saved-look write feature gate and preserve exact historical reads.
- [x] 1.3 Reject stale archived-look selection for new guided sessions and session-plan application, while retaining exact existing Full Auto dependencies.

## 2. User interface and documentation

- [x] 2.1 Add an accessible, confirmed archive action to Looks; keep request races and errors safe and update selection only after success.
- [x] 2.2 Document archive behavior and preserved history in README and the Looks and Sessions pages.

## 3. Verification

- [x] 3.1 Add backend tests for archive idempotency, unknown keys, feature gating, preserved history, blocked writes/imports, and active session selection.
- [x] 3.2 Add Full Auto coverage proving archive preserves active exact dependencies.
- [x] 3.3 Add frontend confirmation, cancellation, success, selection, and failure coverage; run required backend/frontend and OpenSpec gates.
