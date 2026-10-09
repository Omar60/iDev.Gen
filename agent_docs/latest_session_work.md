# Latest Session Work

## Accepted Scope

Internal Heavy deployment `full-auto-first-20261008` addresses only Task 1.1 of
`automate-resource-session-full-auto`. Initial HEAD was `9685296` and the tree
was clean. Main accepted independent verification and marked only Task 1.1.

The additive `saved_look_automation_profile` table stores immutable revisions
separately from saved look snapshots. The saved-look domain publishes profiles
through revision CAS, checks exact source/profile digests and closed nested
validation, and verifies stored evidence on reads. Missing or unknown facts
are representable without claiming readiness. Legacy look, snapshot and
portable-v1 formats remain unchanged; portable JSON does not transfer profiles.
README, the Looks guide and the module ownership map describe this boundary.

## Independent Verification

Tester `verify_11` inspected the actual diff and specification and added
independent oversized-integer, prior-schema migration and portability probes.
Its first oversized-integer probe found OverflowError; Executor repaired only
the new profile boundaries. Profile checks passed after repair, including
strict nested types, Unicode spans, immutable history, corruption refusal,
concurrent publication and exact digest binding.

Final commands completed with exit code zero:

- `.venv/Scripts/python.exe -m pytest -rs`: 2,826 passed, one skipped,
  five warnings, 209.23 seconds; default full collection without instrumentation.
- Privacy/control suites: 46 passed.
- `openspec validate automate-resource-session-full-auto --strict --no-interactive`.
- `git diff --check`.

Frontend was unchanged, so frontend tests/build were not required or executed.
Raw verification logs remain outside the repository. Final closure-only edits
receive a separate privacy/control, OpenSpec and diff recheck before commit.

## Limitations and Handoff

The symlink refusal test was skipped because filesystem symlink creation raised
OSError. Two earlier full suites each reported 2,825 passed, one skipped and
one failure in the existing concurrent session-plan CAS test: its resource
revision lookup failed before CAS winner assertions. Isolated/profile-combined
runs and the final full suite passed. A diagnostic full run passed too, but
instrumentation changes timing. A clean temporary baseline had different
skips, so it is not equivalent proof. No causal profile defect or race repair
was established. An initial focused migration probe also failed transiently
during concurrent test development and passed unchanged afterward; its cause
was not confirmed. Preserve these evidence limits.

No real provider, GPU or ComfyUI generation was exercised. Readiness, appearance
projection, HTTP APIs and Full Auto execution are outside Task 1.1. Task 1.2 is
pending; do not start it, archive the change or push. Main owns the acceptance
commit; Archivist supplies the sealed read-only Git handoff and exact Deployment
Token Report after closure. No external handoff was used.
