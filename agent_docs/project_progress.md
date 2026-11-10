# Project Progress

## Current Deployment: A2a Navigation and ModelDetail E2E

Deployment `idevgen-410-a2a` uses Medium from clean
`main@d7cc18bd1e0ed77565cc7da97cc163ddeea6a770`. A1 and the Authoring repair
were integrated in two commits before this deployment. The user authorizes
only A2a, with no staging, commit, push, dependency changes or mandatory CI gate.
Main owns implementation and verification; Archivist owns public documentation
and the closing usage report. No new OpenSpec change was selected.

## Delivered Boundary

Three Chromium scenarios exercise real ModelDetail navigation, independent model
and Legacy drafts, A-to-B-to-A, Back/Forward, and delayed real PATCH responses.
Test-only fixtures create A/B through the real model validators and handlers in
owned temporary SQLite. Only those IDs permit GET/PATCH. Config writes,
session creation, foreign IDs, queries and other product writes remain denied.
No product frontend/backend source, API contract or dependencies changed.

## Verification

- E2E: four passed (three new scenarios plus A1 smoke); Node checks: three passed.
- Determinism: the three new scenarios repeated three times, nine passed.
- Full frontend: 1,007 passed across 67 files, Node 22.23.4.
- Relevant Python including API, harness, privacy and source controls:
  285 passed, one Windows symlink-capability skip.
- Sanitized production build passed; existing large-chunk warning remains.
- Both final browser runs emitted clean stop evidence. Independent audits found
  no surviving owned PID or new temporary roots and exclusively rebound each
  released port. Backend/socket and browser denial probes remain enforced.

## Continuation

GO for A2b planning/implementation when separately authorized; A2b is not started.
Changes remain unstaged and uncommitted for review. Windows executed; Linux
commands are documented without a Linux run. Draft retention covers the current
SPA ModelDetail instance, not a reload or navigation that unmounts it. This
harness is not an OS sandbox; no real provider, GPU or generation was exercised.
