# Design

## Decision

Represent archival with a small `saved_look_archive` tombstone keyed by `look_key`. Do not update or delete immutable look versions or dependent rows. Keep `get_version` and exact exports unchanged for history; filter only active library listings and selection paths.

The archive operation is idempotent for an existing identity and returns `404 look_not_found` when no version exists. The existing saved-look write feature gate also guards this operation. A tombstone is permanent in this change; no archive browser or restore flow is introduced.

Writes that could extend an archived identity are refused with `409 look_archived`: version creation, profile publication/copy targets, new-session or session-plan look selection, and reviewed imports whose destination is that identity. Exact reads, historical session snapshots, active Full Auto work, and receipt replays that perform no writes remain valid.

## Integration

- `backend/db.py` creates the archive table through the existing additive schema initialization.
- `backend/saved_looks.py` owns the archive transaction, active-list filtering, and the shared archived-write guard.
- `backend/main.py` exposes `DELETE /api/looks/{look_key}` behind the existing saved-look write gate.
- `backend/guided_sessions.py` and `backend/session_plan.py` reject stale attempts to select an archived look for new use while leaving exact historical reads unchanged.
- The Looks view confirms the action, invalidates pending list reads, removes the item only after success, and keeps unrelated selections intact.
- Existing dependencies are reused; no dependency or migration backfill is needed.

## Risks

Archiving is intentionally one-way in the current UI. Old exact version URLs remain readable, but no archived-look browser or restore action is included. Database rows and associated metadata remain stored.
