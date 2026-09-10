## Purpose

Preserve complete accepted source resources locally and expose their readiness and provenance so sessions can use them without reducing them to the legacy room schema.

## ADDED Requirements

### Requirement: Field coverage and readiness are inspectable

The system SHALL classify source fields as descriptive input, selection metadata, writer guidance, auxiliary data or unused data. It SHALL distinguish importable resources from resources ready for preparation. An unmapped or untranslated field required for preparation SHALL block that preparation with a field-specific reason. Keys in pending_fields SHALL be canonical field names, with any source alias recorded in reason and field metadata. Optional unused fields SHALL remain visible without entering the prompt automatically. Stored revisions with invalid persisted translation sidecars SHALL remain inspectable with pending status and diagnostic sidecar_error metadata.

#### Scenario: Translation is incomplete
- **WHEN** an accepted source is stored but its required display or preparation fields lack English translations in the translation sidecar
- **THEN** it remains inspectable by identifier with a pending status and cannot be silently treated as generation-ready

#### Scenario: An alias is used in the source payload
- **WHEN** an accepted source payload uses an alias such as theme or name without an authorized translation
- **THEN** pending_fields records the canonical key scene_theme or label and the reason specifies source_field

#### Scenario: Invalid persisted translation sidecar remains inspectable
- **WHEN** a stored revision has a malformed or unauthorized translation sidecar
- **THEN** readiness evaluation returns pending status with coverage.sidecar_error diagnostic information without throwing an exception

#### Scenario: Invalid optional translation entry forces pending
- **WHEN** a stored revision has an invalid translation entry for an optional field
- **THEN** readiness evaluation returns pending status due to contract violation

### Requirement: Translation map input format and role authorization

The system SHALL accept translation maps as direct dictionaries or lists of translation entries, rejecting duplicate source strings in list form without requiring internal envelopes. Literal source keys "items" and "translation_map" SHALL remain valid source strings. Translation maps SHALL be authorized strictly against source-backed fields with role ROLE_DESCRIPTIVE_INPUT for the target kind; unauthorized target fields SHALL be rejected upfront.

#### Scenario: List-form translation map contains duplicate sources
- **WHEN** a translation map provided as a list contains duplicate source strings
- **THEN** normalization is rejected upfront with a ValueError

#### Scenario: Translation map targets non-descriptive field
- **WHEN** a translation map targets a field whose role is not ROLE_DESCRIPTIVE_INPUT
- **THEN** preview is rejected with a client error naming the disallowed field

### Requirement: Translation map application, attestation, and TOCTOU protection

The system SHALL support previewing and applying translation maps to stored resource libraries. Preview SHALL be strictly read-only and SHALL produce an HMAC-SHA256 attestation token signed by a database-directory-scoped private key (.resource-translation-preview-key), binding library metadata, map digest, and raw sidecar fingerprints (excluding coverage). Apply SHALL verify the token, library existence, and library fingerprint against TOCTOU drift inside a transaction under BEGIN IMMEDIATE, execute validated merge updates into the translation sidecar, repair stale coverage without mutating unchanged translation counts, and suppress redundant SQL writes. If the target library is deleted or modified after preview, apply SHALL fail with HTTP 409 Conflict.

#### Scenario: Applying a valid translation map
- **WHEN** an operator applies a translation map matching source strings in a library
- **THEN** canonical translations are merged into asset_revision.translation and affected revisions become ready

#### Scenario: Target library is deleted after preview
- **WHEN** a target library is deleted between preview token issuance and apply execution
- **THEN** apply is rejected with an HTTP 409 Conflict error

#### Scenario: Source library state drifts between preview and apply
- **WHEN** library revisions are modified after preview issuance
- **THEN** apply is rejected with a conflict error requiring a fresh preview

#### Scenario: Translation map is invalid
- **WHEN** a translation map contains non-English translations or empty fields
- **THEN** the entire operation is rejected with zero writes to stored translations or coverage
