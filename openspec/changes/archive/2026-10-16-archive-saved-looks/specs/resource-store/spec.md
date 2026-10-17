## ADDED Requirements

### Requirement: Saved look archival preserves stored history

The system SHALL archive a saved-look identity without deleting its immutable versions or associated records. Archived looks SHALL be omitted from active saved-look listings.

#### Scenario: Archive a look with multiple versions

- **WHEN** a user archives a saved look
- **THEN** the look no longer appears in active look listings
- **AND** all versions, profiles, photo evidence, import receipts, and session snapshots remain unchanged

### Requirement: Archived look history remains readable

Exact reads and exports of archived versions SHALL remain available. Read-only receipt replays SHALL remain valid when they perform no writes.

#### Scenario: Read archived history

- **WHEN** a client requests an exact archived version or its export
- **THEN** the same immutable historical content remains available

### Requirement: Archived look writes are refused

Version creation, profile publication or copy targets, and imports writing to an archived identity SHALL fail with `409 look_archived` and SHALL NOT write look, profile, catalogue, or receipt data.

#### Scenario: Write to an archived look

- **WHEN** a request attempts a new version, profile write, or import under an archived identity
- **THEN** it returns `409 look_archived` without writes

### Requirement: Saved look archive requests are stable

Repeated archive requests for an existing look SHALL succeed. Archiving an unknown identity SHALL return `404 look_not_found`.

#### Scenario: Retry archiving an existing look

- **WHEN** a user archives the same existing look more than once
- **THEN** each request succeeds without creating duplicate archive records

#### Scenario: Archive an unknown look

- **WHEN** a user archives a key with no saved versions
- **THEN** the request returns `404 look_not_found` and stores no archive record
