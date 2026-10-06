# Project Progress

## Goal

Implement `simplify-resource-session-workflow` one independently accepted task
at a time, preserving unrelated work and explicit generation gates.

## Current Position

Heavy internal Codex deployment `srsw-10-6-20261006` completed only Task 10.6
on 2026-10-06. Main accepted independent Tester evidence and the corrected
compatibility report. Task 10.6 is checked; 70 of 72 tasks are complete.
The tree was clean at intake. No production behavior changed.

The added raw-expert API regression connects two selected resources to
completion, server-derived dependency evidence, review, approval and explicit
submission. It preserves the caller snapshot and the actual pending shot
through retry and a future-take edit. The compatibility matrix and final
evidence are canonical in `latest_session_work.md`.

Independent gates passed: 2,762 Python tests, one environment-dependent symlink
skip, 587 frontend tests, build, 46 privacy/control checks, two historical-source
rollback tests, strict OpenSpec validation and diff checks. The build retains
its existing chunk-size advisory.

## Next Milestone

Task 10.6 is accepted for its authorized isolated local commit and Heavy
closure. No independent behavior review remains pending. Tasks 10.7 and 10.8
remain unchecked and unstarted; another task requires a new user instruction.
Do not push or create external handoffs.
