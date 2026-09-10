## MODIFIED Requirements

### Requirement: Resource revisions are reproducible

The system SHALL use library identity and source entry identity together and retain immutable accepted revisions. Reimporting identical content SHALL create no duplicate revision. A changed source SHALL create a new revision without changing existing session snapshots or measured evidence. Missing source entries SHALL be reported and SHALL NOT silently delete existing data.

Logical deletion SHALL NOT physically remove or mutate accepted immutable revisions. A deleted library or logical source entry SHALL be excluded from normal inventory and new selection/preparation while historical exact revision references remain resolvable for finalized prepared/generated evidence.

#### Scenario: A source entry changes
- **WHEN** the same library and identifier are reimported with different content
- **THEN** new selections can use the new revision while previously prepared takes retain their original inputs

#### Scenario: A logical resource is deleted
- **WHEN** a library or source entry is logically deleted
- **THEN** its accepted revision rows remain unchanged
- **AND** finalized historical snapshots that reference exact revisions remain explainable and replayable according to existing snapshot semantics

### Requirement: Logical resource deletion and restoration are explicit

The system SHALL support transactional, idempotent logical Delete and Restore operations for imported libraries and individual logical source entries. Library-level deletion state SHALL be stored separately from immutable revision payloads. Individual entry deletion state SHALL be keyed by logical source identity rather than by a particular immutable revision.

Deleting a library SHALL hide all of its entries from normal inventory and new selection/preparation through parent library state. Restoring a library SHALL clear only the library deletion state. An entry that was individually deleted SHALL remain deleted until explicitly restored.

A new preparation request that depends on a deleted library/entry SHALL fail with a deleted-resource reason rather than silently substituting another revision. Historical finalized prepared/generated snapshots SHALL continue resolving the exact immutable revisions they already captured.

#### Scenario: Delete library twice
- **WHEN** an already deleted library receives Delete again
- **THEN** the operation succeeds idempotently without altering immutable revisions

#### Scenario: Restore library twice
- **WHEN** an active library receives Restore again
- **THEN** the operation succeeds idempotently without altering immutable revisions

#### Scenario: Restore parent library with deleted child entry
- **WHEN** a deleted library is restored while one source entry has an individual deletion tombstone
- **THEN** the library becomes active
- **AND** that source entry remains excluded until explicitly restored

#### Scenario: New preparation references deleted resource
- **WHEN** an unfinalized plan requests preparation using an exact revision whose logical library or source entry is deleted
- **THEN** preparation is refused with a deleted-resource diagnostic
- **AND** no replacement revision is selected automatically

### Requirement: Browser re-import restores a matching deleted library without duplicating identity

For the browser-selected import flow, the supported source envelope's valid top-level `library` declaration SHALL identify the logical target library. If that exact library identity already exists in soft-deleted state, preview SHALL report reuse/restoration and successful atomic commit SHALL reactivate the same logical library row while applying normal immutable revision import rules.

Re-import SHALL NOT create a second logical library with the same declared identity and SHALL NOT silently restore individually deleted source entries. Individual entry tombstones remain authoritative until explicit Restore.

#### Scenario: Re-import deleted library
- **WHEN** a source declares the exact key of an existing soft-deleted library
- **THEN** preview identifies the existing library as the target to restore
- **AND** commit reactivates that same logical library identity atomically with accepted revision writes

#### Scenario: Re-import includes deleted source entry
- **WHEN** a re-imported file contains a source ID that is individually deleted
- **THEN** normal immutable revision accounting still occurs
- **AND** the logical source entry remains deleted after import until explicitly restored

### Requirement: Field coverage and readiness are inspectable

The system SHALL classify source fields as descriptive input, selection metadata, writer guidance, auxiliary data or unused data. It SHALL distinguish importable resources from resources ready for preparation. An unmapped or untranslated field required for preparation SHALL block that preparation with a field-specific reason. Keys in pending_fields SHALL be canonical field names, with any source alias recorded in reason and field metadata. Optional unused fields SHALL remain visible without entering the prompt automatically. Stored revisions with invalid persisted translation sidecars SHALL remain inspectable with pending status and diagnostic sidecar_error metadata.

Normal inventory/readiness surfaces SHALL also expose enough safe logical deletion state to explain why a library or source entry is unavailable. Deleted state SHALL NOT be confused with translation/readiness failure.

#### Scenario: Deleted entry is inspected through management details
- **WHEN** an operator views advanced/management details for a deleted logical source entry
- **THEN** its immutable revisions and readiness metadata remain inspectable
- **AND** the UI/API identifies logical deletion separately from readiness or translation errors

### Requirement: Translation map application, attestation, and TOCTOU protection

The system SHALL support previewing and applying translation maps to stored resource libraries. Preview SHALL be strictly read-only and SHALL produce an HMAC-SHA256 attestation token signed by a database-directory-scoped private key (.resource-translation-preview-key), binding library metadata, map digest, and raw sidecar fingerprints (excluding coverage). Apply SHALL verify the token, library existence, logical deletion state, and library fingerprint against TOCTOU drift inside a transaction under BEGIN IMMEDIATE, execute validated merge updates into the translation sidecar, repair stale coverage without mutating unchanged translation counts, and suppress redundant SQL writes. If the target library is logically deleted or modified after preview, apply SHALL fail with HTTP 409 Conflict.

#### Scenario: Applying a valid translation map
- **WHEN** an operator applies a translation map matching source strings in an active library
- **THEN** canonical translations are merged into asset_revision.translation and affected revisions become ready

#### Scenario: Target library is deleted after preview
- **WHEN** a target library is logically deleted between preview token issuance and apply execution
- **THEN** apply is rejected with an HTTP 409 Conflict error

#### Scenario: Source library state drifts between preview and apply
- **WHEN** library revisions are modified after preview issuance
- **THEN** apply is rejected with a conflict error requiring a fresh preview

#### Scenario: Translation map is invalid
- **WHEN** a translation map contains non-English translations or empty fields
- **THEN** the entire operation is rejected with zero writes to stored translations or coverage
