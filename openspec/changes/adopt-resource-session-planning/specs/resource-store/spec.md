## Purpose

Preserve complete accepted source resources locally and expose their readiness and provenance so sessions can use them without reducing them to the legacy room schema.

## ADDED Requirements

### Requirement: Complete accepted entries are retained

The system SHALL retain every field, nested value, original string and source identifier of an accepted entry in its local database. It SHALL keep the original payload separate from translations and derived values. Auxiliary maps SHALL be stored as auxiliary resources, not interpreted as scenes. Refused content SHALL be persisted as imported and SHALL appear in the import report.

#### Scenario: A nested field has no current consumer
- **WHEN** an accepted resource includes an unfamiliar nested field
- **THEN** its value survives a database round trip and is reported as retained but unused

#### Scenario: A fused resource is accepted
- **WHEN** an accepted entry combines scene and camera descriptions
- **THEN** its complete original object is retained without requiring decomposition

### Requirement: Import accounts for every input

The system SHALL preview each discovered file and account for its entries as new, unchanged, updated or unresolved. Readiness SHALL be reported separately from import outcome. It SHALL identify duplicate identifiers, unsupported shapes, malformed input and missing translations before committing. A commit SHALL bind to the previewed source fingerprints and be atomic for its accepted set.

#### Scenario: Source changes after preview
- **WHEN** a source file differs from its previewed fingerprint
- **THEN** commit is refused and requires a fresh preview without changing stored resources

#### Scenario: A file cannot be interpreted safely
- **WHEN** a file has an unsupported shape or ambiguous identifiers
- **THEN** it appears as unresolved with a reason and is not silently ignored or guessed into a scene

### Requirement: Resource revisions are reproducible

The system SHALL use library identity and source entry identity together and retain immutable accepted revisions. Reimporting identical content SHALL create no duplicate revision. A changed source SHALL create a new revision without changing existing session snapshots or measured evidence. Missing source entries SHALL be reported and SHALL NOT silently delete existing data.

#### Scenario: A source entry changes
- **WHEN** the same library and identifier are reimported with different content
- **THEN** new selections can use the new revision while previously prepared takes retain their original inputs

### Requirement: Field coverage and readiness are inspectable

The system SHALL classify source fields as descriptive input, selection metadata, writer guidance, auxiliary data or unused data. It SHALL distinguish importable resources from resources ready for preparation. An unmapped or untranslated field required for preparation SHALL block that preparation with a field-specific reason. Optional unused fields SHALL remain visible without entering the prompt automatically.

#### Scenario: Translation is incomplete
- **WHEN** an accepted source is stored but its required display or preparation fields lack English translations
- **THEN** it remains inspectable by identifier with a pending status and cannot be silently treated as generation-ready
