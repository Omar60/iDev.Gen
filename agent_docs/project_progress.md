# Project Progress

## Current Deployment: A2c-a Review E2E

Deployment `idevgen-410-a2ca` uses Medium on local branch `e2e-a2c`, created
from validated `cf063991826ea895179f2643486571e7eb3e7b10`. No staging, commit,
push, PR, main, production, dependency or CI changes are authorized.

A2b passed Linux run 42942686174 at that base: complete Python 3,727 passed
and one opt-in skip, frontend 1,007 passed, Node seven passed, and three fresh
Chromium runs of fourteen passed with clean lifecycle audits. These historical
results do not validate the new Review cases or changed fixture helpers.

## Implemented Boundary

Eight new Chromium cases cover persisted Review, explicit take refresh,
A-to-B-to-A, delayed aggregate/direct reviews, revision changes, stage reentry,
and explicit recovery from real revision 409 or transport failure. Review is
GET-only; Approval is a separate operation and is never clicked here.

The opt-in Review fixture starts without approval and rejects runtime product
POSTs. Its explicit owned control advances one session from revision 1 to 2
using real save/prepare services; it records the control and checks the other
session remains unchanged. Full temporary plan/preparation rows, exact review
request order/IDs/revisions, null GET bodies, zero product writes, approvals,
shots and authoring operations are asserted. Normal A2b fixture mode remains.

## Verification and Next Boundary

Pertinent Python/API/harness/privacy checks: 292 passed, one Windows symlink
skip. Frontend: 1,007 passed; Node: seven passed; sanitized build passed with
the existing chunk-size warning. Collection lists twenty-two tests, including
eight Review cases; collection alone is not a browser execution.

Windows preflight still rejects 417 build bytes versus 906 delivered bytes.
It exits before functional cases, then closes gracefully without kill, exit0,
streams drained, root removed; independent PID absence, exclusive port rebind
and unchanged temporary-root inventory passed.

GO to clean Linux validation after final local gates; HOLD on functional A2c-a
acceptance until the eight Review cases and all fourteen regressions pass and
repeat with fresh servers and clean lifecycle audits. Full Python, Linux
frontend/build/Node/privacy, exact HTML integrity and all lifecycle checks are
pending for the new tree. No Linux run or publication is claimed.
