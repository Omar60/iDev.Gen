## Why

Resource-v1 can create, prepare, review, submit and run sessions, but the user must coordinate those steps and apply a saved look after creation. A single explicitly authorized journey needs durable orchestration and trustworthy stage data: constant appearance can currently reintroduce accessories that progression removed, and free-text wardrobe does not establish body coverage.

## What Changes

- Add opt-in Full Auto creation with an exact saved-look version and automation-profile revision, ready room, resolved character and compatible t2i workflow, photo count and one Generate without pauses authorization bound to the displayed preflight scope.
- Snapshot the look during atomic guided creation; derive all saved stages across all takes, compile evidenced coverage, prepare in existing bounded batches, validate, approve, submit and run through existing services.
- Add version-bound structured coverage and appearance ownership metadata. Legacy looks remain usable manually; insufficient data needs one reusable look correction, never invented anatomy or routine per-take entry.
- **BREAKING contract extension, opt-in only:** replace unconditional manual-only coverage, progression-preview and review-button requirements with narrowly scoped Full Auto authorization; explicit manual profile application also supports evidenced coverage and ownership without automating the manual controls. Profile-free manual behavior, closed authoring-v1 snapshots and portable look-v1 remain compatible.
- Add discoverable durable run state, bounded authorization/calls, renewable leases, explicit recovery consent and deletion guards. Reconcile remote attempts without blind resend and use safe cancellation or visible draining on older servers. Reuse the existing resource-planning feature flag and define isolated-data rollback.
- Distinguish actual conflicts from neutral resource notices and disclose that canvas, framing and prompts are instructions, not pixel guarantees.

## Capabilities

### New Capabilities

- `resource-full-auto`: One-authorization orchestration, execution recovery, progress and end-to-end acceptance.

### Modified Capabilities

- `session-plan`: Exact look selection at creation, authorized progression and coverage provenance with existing CAS and history protection.
- `resource-store`: Immutable automation profiles attached to exact saved-look versions, with explicit structured evidence and migration boundaries.
- `resource-prompts`: Evidence-derived coverage, separation of permanent appearance from progressive accessories, and retained finalization gates.
- `resource-workflow-ux`: Full Auto selection and progress/recovery UI alongside the current manual journey.

## Impact

Backend integration: `guided_sessions.py`, `saved_looks.py`, `session_plan.py`, `authoring_operations.py`, `resource_preparation.py`, `workflow_binding.py`, `main.py`, `runner.py`, `comfy.py`, `enhance.py`, and additive SQLite persistence in `db.py`. Frontend integration: `Resources.jsx`, `Looks.jsx`, `SessionLookProgression.jsx`, `SessionView.jsx`, `sessionPlan.js` and API tests. Extend existing contract/recovery tests and browser journeys; update README and matching docs during implementation. No new external service, queue framework or dependency is proposed.

This change contains planning artifacts only: no runtime code, private session data or generated images. A commit requires independent review and all applicable repository gates; no push is authorized. Real generation is a later, separately authorized acceptance exercise.
