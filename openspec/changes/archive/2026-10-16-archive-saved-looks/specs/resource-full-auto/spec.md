## ADDED Requirements

### Requirement: Archived looks retain active Full Auto dependencies

Archiving a saved look SHALL retain the exact saved-look version and automation-profile rows used by a Full Auto run. Archiving SHALL not cancel, block, or invalidate that run. Existing Full Auto dependency checks SHALL continue to resolve the retained exact identities; destructive deletion of dependent rows remains subject to the existing `full_auto_in_use` protections.

#### Scenario: Archive a look used by an active run

- **WHEN** a user archives a look referenced by an active or unsettled Full Auto run
- **THEN** archiving succeeds and removes the look from active library listings
- **AND** the run's exact look and profile dependencies remain present and valid
