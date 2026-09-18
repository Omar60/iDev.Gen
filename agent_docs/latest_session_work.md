# Latest Session Work

## Detailed Current State

Task 2.1 of `simplify-resource-session-workflow` passed final independent
acceptance over the complete working-tree delta from the accepted baseline
`7b51c67851893950807b066dedd248d8cdb36529` and is formally closed.

The accepted implementation adds a reusable opt-in `RequestLimitRoute` and
actual-streamed-body pre-read boundary with an authoritative 10 MiB limit.
Actual bytes remain authoritative when Content-Length is missing or false;
verified bodies replay without a second network read, and incomplete
disconnects never reach downstream parsing or writes. Legacy endpoints remain
unaffected. The accepted test file adds 27 tests and the reproducible backend
collection contains 1,924 tests.

## Verification

- The focused request-limit suite passed with 27 tests; the complete pytest
  suite passed for all 1,924 collected items; privacy passed 10 tests; and
  `git diff --check` plus strict OpenSpec validation passed.
- The accepted closure includes `backend/request_limits.py`,
  `tests/test_request_limits.py`, OpenSpec task state, and these workflow
  documents only.

## Next Entry Point

Task 2.1 is accepted and checked. Task 2.2 remains pending. Do not begin Task
2.2 automatically; select and verify its own scope first.
