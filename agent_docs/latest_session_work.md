# Latest Session Work

## Current State

OpenSpec Task 7.4 of `simplify-resource-session-workflow` passed independent
acceptance. Finalized snapshots persist versioned effective resource input
evidence from authorized descriptions and reviewed adaptations. Review and
recovery detect changed or missing evidence for unlinked ready rows without
writing or backfilling history. Linked/generated rows retain saved prompt and
provenance. Raw expert prompts remain caller-owned; declared adaptations must
match persisted approvals, while semantic use in arbitrary prompt text cannot
be proven by the server.

## Independent Verification

The independent reviewer passed `python -m pytest` (2,389 tests), the
privacy/control checks (46 tests), strict OpenSpec validation, and
`git diff --check`. Frontend files were unchanged.

## Continuation

Task 7.5 is next and has not started. Task 7.4 closes locally without push.
