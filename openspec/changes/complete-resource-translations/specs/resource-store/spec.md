## Purpose

Preserve complete accepted source resources locally and expose their readiness and provenance so sessions can use them without reducing them to the legacy room schema.

## ADDED Requirements

### Requirement: Field coverage and readiness are inspectable

The system SHALL classify source fields as descriptive input, selection metadata, writer guidance, auxiliary data or unused data. It SHALL distinguish importable resources from resources ready for preparation. An unmapped or untranslated field required for preparation SHALL block that preparation with a field-specific reason. Keys in pending_fields SHALL be canonical field names, with any source alias recorded in reason and field metadata. Optional unused fields SHALL remain visible without entering the prompt automatically.

#### Scenario: Translation is incomplete
- **WHEN** an accepted source is stored but its required display or preparation fields lack English translations in the translation sidecar
- **THEN** it remains inspectable by identifier with a pending status and cannot be silently treated as generation-ready

#### Scenario: An alias is used in the source payload
- **WHEN** an accepted source payload uses an alias such as theme or name without an authorized translation
- **THEN** pending_fields records the canonical key scene_theme or label and the reason specifies source_field

### Requirement: Translation map application and attestation

The system SHALL support previewing and applying translation maps to stored resource libraries. Preview SHALL be strictly read-only and SHALL produce an HMAC-SHA256 attestation token binding the library state and map digest. Apply SHALL verify the token and library fingerprint against TOCTOU drift, execute validated merge updates into the translation sidecar atomically, and recompute readiness. Invalid translation maps SHALL be rejected up front with zero database writes. Valid unmatched entries SHALL be reported without failing the operation.

#### Scenario: Applying a valid translation map
- **WHEN** an operator applies a translation map matching source strings in a library
- **THEN** canonical translations are merged into asset_revision.translation and affected revisions become ready

#### Scenario: Source library state drifts between preview and apply
- **WHEN** library revisions are modified after preview issuance
- **THEN** apply is rejected with a conflict error requiring a fresh preview

#### Scenario: Translation map is invalid
- **WHEN** a translation map contains non-English translations or empty fields
- **THEN** the entire operation is rejected with zero writes to stored translations or coverage
