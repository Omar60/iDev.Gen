# Project Progress

## F03 Closure — 2026-11-15

F03 is closed on main at 016d7b12e4a950ac8a2104adad5a4baac21a8172.
PR [#11](https://github.com/Omar60/iDev.Gen/pull/11) was integrated with
GitHub's Rebase and merge from original head
bf6179e9eb5fdfff893a88f2d2dd5353317c43f7. The integrated commit is a direct
child of 890c326cb413021f7d0f15e46de25ba23c3ce218; its stable patch ID matches
the original, and the F03 commit diff contains only the three authorized files.

This closure resolves the minor landscape overflow listed as pending in the
historical U01-A3 record below; that record remains unchanged.

- D2-H uses three footer columns at widths 500–607px, with context spanning a
  full row, and four columns from 608px.
- D3-V applies at widths up to 499px and heights up to 420px. It compacts the
  header, keeps a 160px minimum stage, preserves object-fit: contain and safe
  area insets, and lets the footer scroll internally without horizontal or
  outer-dialog overflow.
- At 499×421, native dialog scrolling of about 26px remains documented; the
  controls remain reachable by keyboard.

Post-merge Windows run
[43740618171](https://github.com/Omar60/iDev.Gen/actions/runs/43740618171) completed SUCCESS on the exact
integrated SHA, attempt 1. Runtime: Node 22.23.4, Python 3.12.10,
Playwright 1.65.0 and Chromium 158.0.8127.18. The required
isolated-e2e check succeeded. Python reported 3,729 passed, one opt-in
benchmark skipped, and 14 warnings; frontend passed 1,063/1,063; Vite 7.3.6
build passed with the existing large-chunk warning; Node lifecycle passed
7/7; privacy/source passed 46/46; and Playwright passed 30/30 in each of three
runs, with zero E2E skips, retries, flaky or unexpected results. HTML, identity,
egress, isolation and teardown audits passed with no residual processes, ports
or temporary roots. Symlink coverage executed and passed with one warning.
The dedicated local Slideshow Chromium check passed 46/46; it is separate from
the hosted Playwright inventory.

Non-blocking CI observations include React act warnings, punycode
deprecations and the GitHub Actions Node 20 runtime deprecation warning.

Separate pending items remain outside F03: spoken screen-reader verification
for H01, a product decision on automatic reduced-motion behavior, and physical
device verification of safe-area insets. No global WCAG conformance is claimed.

## U01-A3 Closure — 2026-11-14

U01-A3 is closed on `main` at `890c326cb413021f7d0f15e46de25ba23c3ce218`.
PR [#10](https://github.com/Omar60/iDev.Gen/pull/10) was integrated using
GitHub's Rebase and merge. The rebased commits are:

- `38dcfdb742f09040c0671aac5a95e0aefe304c0c`, parent `2ac978a4f0a2bf87286e4ec5eeacfd9ad5ccbf3b`.
- `890c326cb413021f7d0f15e46de25ba23c3ce218`, parent `38dcfdb742f09040c0671aac5a95e0aefe304c0c`.

Their stable patch IDs match the two original commits, the resulting tree is
identical to the approved PR head tree, and the accumulated diff contains only
`frontend/src/views/Looks.combinedSave.test.jsx`, `tests/test_fa46_probe.py`,
and `tests/test_full_auto_control_api.py`. The history is linear.

Post-merge [Windows run 43620593551](https://github.com/Omar60/iDev.Gen/actions/runs/43620593551)
passed on the exact `main` SHA above, attempt 1. `isolated-e2e` and its audits
passed. Python reported 3,729 passed and one skipped; frontend 1,063/1,063;
build passed; Node lifecycle 7/7; privacy/source 46/46; and the 30-case, seven-file
A3 inventory passed three times with no E2E skips, retries, flaky or unexpected
results.

Non-blocking observations: the opt-in Python benchmark was skipped, the build
reported the existing large-chunk warning, and GitHub Actions reported a runtime
deprecation warning. No follow-up work was opened for these observations.

Separate pending items remain outside U01-A3: F03's minor landscape overflow,
any future product decision on automatic reduced-motion behavior, and spoken
screen-reader verification for H01. No global WCAG conformance is claimed.

## CI-W2 Branch Preparation — 2026-11-12

Base: `main@1cc55b275c9fb27700165c73a52b2fbb7fc4c978`. D4c is accepted and
RoomImport is closed. CI-W1 approved Windows as the sole CI acceptance platform.
Branch `ci/windows-first` retires `.github/workflows/ci.yml`, retains the
`isolated-e2e` job on `windows-2025`, and validates every pull request plus
pushes only to `main`. The actual PR base SHA and ancestry check remain intact.
Suites, inventory, permissions and isolation audits are unchanged.

This is preparation for a separate PR acceptance, not integration into `main`.
CI-W3 remains pending: verify the PR's Windows result before merge and handle
required-status branch protection separately. Historical evidence below remains
scoped to its original SHA and workflow configuration.

## A3 Closure Snapshot — 2026-11-11

A3 is technically closed on `main` at `d235127578683138b2e1e7df9c3a03e861ab43f1`.
The PR #2 commits were integrated by fast-forward at `ce9f495e`, and the
temporary A2c validation workflow was removed in `d235127`. The permanent
Windows workflow `.github/workflows/e2e-windows.yml` runs separately from the
general Ubuntu workflow `.github/workflows/ci.yml`.

- [General CI run 43121838365](https://github.com/Omar60/iDev.Gen/actions/runs/43121838365):
  SUCCESS; Python 3,729 passed, one skipped; frontend build SUCCESS.
- [Windows E2E run 43121838436](https://github.com/Omar60/iDev.Gen/actions/runs/43121838436):
  SUCCESS; Python 3,729 passed, one skipped; frontend 1,007/1,007; Node 7/7;
  privacy/source 46/46; exact 30-case inventory passed in three independent
  Chromium runs; symlink coverage executed.
- HTML preflight, egress guard, build, diff-check and Windows process/socket,
  server and root lifecycle audits passed. See `docs/e2e-testing.md` for the
  exact runtime versions, scenario inventory and scope limits.

The one Python skip was the opt-in 500-take synthesis benchmark. Python
dependency ranges remain floating; automatic execution of Windows E2E does not
establish required-status branch protection.

## Historical A2d Verification Snapshot — before A3 closure (2026-11-11)

By this date, `c365c47c95a5bde0e9d45c303a447bce743a6142` had been published to
`e2e-a2c`; it records the documentation reconciliation and removal of the
temporary A2b workflow. Consult Git for current refs, ancestry and main
integration status rather than treating this snapshot as live state. E2E
phases A1–A2c are complete; accepted Linux and Windows evidence applies to the
specific SHAs recorded below.

A2c-a Review previously passed Linux run 42953660419 with three 22/22 passes.
Subsequent run 42965352388 includes Approval and validates the complete 30-case
inventory, as detailed below.

## Implemented Boundary

Eight new Approval cases use the real Review UI, FastAPI routes and SQLite:
explicit approval and persisted revision; pending A approval through A-to-B-to-A;
late A approval on B; late success after revision change; newer success before an
older stale 409; response after view unmount; stale 409 followed by explicit
reload; and transport failure followed by explicit user retry. Exact POST
identity, payload, status and order, exact Review GET order, SQLite approval
rows/counts and unchanged plans/preparations/shots are asserted. The previous
22 scenarios and their assertions were preserved when adding Approval.

The opt-in Approval fixture starts both owned sessions unapproved and allows only
an exact `{plan_revision}` POST to the owned `/plan/review/approve` endpoint.
FastAPI handles every allowed request. Other product writes remain blocked, and
the fixture exposes raw approval rows for persistence checks. No production
source, contract or dependency changed for Approval.

## Historical Linux Evidence — d674826 — 2026-11-10

[Linux run 42965352388](https://github.com/Omar60/iDev.Gen/actions/runs/42965352388)
passed at exact SHA `d674826`: complete Python 3,729 passed, one opt-in 500-take
benchmark skipped; frontend 1,007/1,007; Node 7/7; privacy/source 46/46; build and
diff-check passed. Three independent Chromium passes each passed 30/30,
including Review 8/8 and Approval 8/8: 90/90 total. Every pass retained intact
HTML and active guards, graceful exit 0 with `forced=false`, drained streams,
`clean=true` stop/root markers, absent owned PID/listener and zero residual roots.

## Accepted Windows Evidence — c365c47 — 2026-11-11

Windows validation used Node 22.23.4, Playwright 1.65.0 and pinned Chromium
158.0.8127.18 (revision 1254). Four independent passes each passed 30/30
(120/120 total), with Review and Approval 8/8 per pass. Frontend passed
1,007/1,007, Node 7/7 and privacy/source 46/46; the build passed. HTML integrity
and clean teardown passed in every run.

Linux [Actions run 43036836646](https://github.com/Omar60/iDev.Gen/actions/runs/43036836646)
for c365 failed both attempts during dependency installation because Ubuntu
package-host DNS resolution failed during apt; the test suites never started.
It provides no Linux E2E result for c365.

The retained A2c workflow audits exactly 30 cases per pass, with one worker and
zero retries, and triggers only on push to `e2e-a2c`. The A2b workflow is retired;
its guide remains historical. A3 is the separate phase for deciding permanent
Windows CI. E2E remains outside the required main CI gates. Node.js 22.x is
required.

Existing dependency, bundle and GitHub Actions warnings were outside this
documentation change. For current integration state, consult Git.
