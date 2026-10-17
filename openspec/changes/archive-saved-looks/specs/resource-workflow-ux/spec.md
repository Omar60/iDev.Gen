## ADDED Requirements

### Requirement: Users can archive saved looks from Looks

Looks SHALL provide an accessible archive action with confirmation that says history is retained. Cancel sends no request. Success removes the look from the active list; archive is unavailable while edits or photo staging are pending. Failures retain the list and show a readable error.

#### Scenario: Confirm archive

- **WHEN** the user confirms archiving a saved look
- **THEN** the client sends one archive request and removes the look from the active list after success
- **AND** the existing look versions and session snapshots remain available

#### Scenario: Cancel archive

- **WHEN** the user cancels the archive confirmation
- **THEN** no request is sent and the active list and selection remain unchanged

#### Scenario: Archive fails

- **WHEN** the archive request fails
- **THEN** the look remains in the active list and the view presents a readable error

### Requirement: Archived looks stay out of active selectors

Archived looks SHALL not appear in active selectors for new sessions. Exact historical version reads SHALL remain available. This change adds no archive browser or restore action.

#### Scenario: Select a look for a new session

- **WHEN** a user opens an active saved-look selector after a look was archived
- **THEN** the archived look is not offered for selection
