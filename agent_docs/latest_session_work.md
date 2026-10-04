# Latest Session Work

## Current State

OpenSpec Task 9.7 of `simplify-resource-session-workflow` passed independent
acceptance under Heavy deployment `simplify-next-20261004`. Progress is 58 of 72
tasks. Internal Codex implemented from a clean baseline. No external handoff
was created, and no independent product review remains pending.

## Accepted Behavior

The photo-stage Extract API produces an editable appearance/visible-garment
proposal with unresolved details. Save requires the server proposal ID, reviewed
content, explicit removal-order confirmation and correction/omission decisions.
Published extraction proposals cannot bypass review through manual stage save;
failed extraction preserves preview and manual entry.

The exact textual request projection, selected vision model, final non-secret
parameters, validated output and user corrections are stored locally. Each image
part is replaced by its actual SHA-256, media type, bytes and dimensions. No image
bytes, data URI, endpoint or secret headers persist. Look, evidence and stage
receipt share one transaction before cleanup. Exact retries replay after cleanup,
and saved evidence survives stage purge. Portable export includes only the photo
origin annotation and digest. Photos never become generation references.

Cancellation, expiry, feature disablement and newer extraction starts fence
late output. Failed newer attempts cannot revive older pending owners. Staged
bytes are reverified before transmission; HTTP runs outside database transactions.
The browser photo editor remains Task 9.11, and session preset/progression
integration remains Tasks 9.8-9.10.

## Independent Verification

- Complete Python suite: 2,666 passed, one skipped, three warnings (176.38 s).
- Independent acceptance probes: 13 passed using fake HTTP and synthetic images.
- Extraction/staging/saved-look/shared-transport focused suite passed.
- Privacy and control-character checks: 46 passed.
- Strict noninteractive OpenSpec validation and diff checks passed.

No frontend files changed, so frontend gates were not applicable. The native
symlink test was skipped because Windows denied link creation. An implementation
full run passed 2,653 tests before a later run failed the unchanged concurrent
plan-CAS test; its isolated run and the final independent complete run passed.
No unrelated production repair or assertion weakening was introduced.

## Closure and Continuation

Accepted changes are the backend photo proposal/evidence services and routes,
additive schema, owned and independent tests, README/Looks/limitations guidance,
the three deployment-state documents and only the Task 9.7 checkbox. The user
authorized the isolated local acceptance commit. Archivist owns the closing
read-only Git receipt and Heavy token report. Task 9.8 remains unstarted; no
push is authorized.
