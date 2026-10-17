# Proposal

## Why

The Looks library has no way to retire a saved look. Physically deleting all versions can break immutable profile, evidence, import-receipt, and session-history references, so users need a safe way to remove a look from future selection while retaining its history.

## What Changes

- Add an archive action for a saved-look identity. Archiving hides it from active look lists and selectors while retaining its versions and related records.
- Keep exact historical reads and exports available, and preserve sessions and active Full Auto dependencies that reference the look.
- Reject new versions, profile writes, and import writes targeting an archived look.
- Add a confirmation action in Looks and document archive semantics.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `resource-store`: Define safe saved-look archival, historical retention, and write behavior.
- `resource-workflow-ux`: Add the Looks archive action and hide archived looks from active selectors.
- `resource-full-auto`: Clarify that archiving preserves exact dependencies used by active runs.

## Impact

The change affects SQLite schema initialization, saved-look queries and writes, the FastAPI looks API, guided/session look selection, the Looks React view and tests, related API and lifecycle tests, `README.md`, `docs/looks.md`, and `docs/sessions.md`. No new dependency is required.
