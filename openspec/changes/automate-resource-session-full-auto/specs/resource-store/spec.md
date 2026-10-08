## ADDED Requirements

### Requirement: Profiles retain exact immutable identity

A saved-look version SHALL support separately stored immutable automation-profile revisions bound to its exact key, version and content digest. A profile SHALL have its own canonical digest, schema version and accepted provenance. Editing profile facts SHALL create a new revision; existing session evidence SHALL not follow latest. Before a shot is linked, an explicit session profile application SHALL verify exact identity and plan CAS, fence the run and invalidate affected derived preparation.

#### Scenario: Profiles retain exact immutable identity
- **WHEN** a profile revision is edited after a session selected it
- **THEN** a new revision is created and the session keeps its original binding

#### Scenario: Profile changes after session creation
- **WHEN** a new profile revision is saved for an existing look version
- **THEN** an already created session retains its exact accepted profile and digest
- **AND** new sessions can select the newer revision explicitly

### Requirement: Linked profiles freeze without breaking manual portability

Once any shot is linked, the complete session profile SHALL be frozen with its look snapshot and a different profile SHALL require a new session. Absence of a profile SHALL not invalidate ordinary manual look use. The existing closed look snapshot and portable look-v1 schema SHALL remain unchanged; portable export/import SHALL disclose that separate automation readiness is not transferred.

#### Scenario: Linked profiles freeze without breaking manual portability
- **WHEN** a different profile is applied after any shot is linked
- **THEN** application requires a new session while portable-v1 remains unchanged

#### Scenario: Portable look lacks a profile
- **WHEN** a portable-v1 look is imported or exported
- **THEN** its existing compatibility and digest rules remain valid
- **AND** the UI explains that Full Auto profile setup is separate

### Requirement: Profile coverage uses supported exact state facts

Profile v1 SHALL record explicit appearance-clause ownership, ordered garment identity references, worn/aside region coverage, baseline coverage, inventory completeness and the provenance of accepted facts. Coverage regions SHALL be chest, hips, legs and feet with values full, partial, none or unknown. Unknown SHALL remain representable but SHALL prevent Full Auto when applicable to a selected stage. Every garment reference SHALL belong to the bound snapshot.

#### Scenario: Profile coverage uses supported exact state facts
- **WHEN** a garment reference is absent from the bound snapshot
- **THEN** profile validation rejects the reference

### Requirement: Uncovered and aside states require explicit evidence

A selected moved-aside state SHALL require its own authored coverage. Complete inventory and baseline SHALL be explicitly confirmed before uncovered-region claims are allowed. A selected garment-free final stage SHALL reject baseline facts asserting additional worn covering items.

#### Scenario: Uncovered and aside states require explicit evidence
- **WHEN** the selected aside state lacks its own coverage facts
- **THEN** the profile is ineligible without extrapolating from worn-state wording

### Requirement: Appearance clauses have exactly one owner

Appearance ownership SHALL cover the complete source text without overlapping or omitted spans. Each segment SHALL belong to permanent appearance or an identified progressive garment/accessory. Accepted progressive clauses SHALL be represented by canonical wardrobe wording rather than repeated in fixed appearance. Ambiguous wording SHALL require an explicit profile correction or a new corrected look version, not inferred removal.

#### Scenario: Appearance clauses have exactly one owner
- **WHEN** accepted spans overlap or omit part of the appearance
- **THEN** profile validation rejects the ownership partition

#### Scenario: Accessory appears in two competing sources
- **WHEN** the same accessory is marked permanent in appearance and removed by progression
- **THEN** profile eligibility fails with both sources and a correction action

### Requirement: Accessory removal does not imply uncovered anatomy

Accessories SHALL be able to declare no body coverage without implying that their removal exposes a body region.

#### Scenario: Accessory removal does not imply uncovered anatomy
- **WHEN** an accessory explicitly declares none in all supported regions and is removed
- **THEN** its removal contributes no inferred body-region exposure

### Requirement: Prose alone cannot establish profile facts

Accepted facts SHALL come from user input or reviewed structured evidence identifying the exact source and meaning. Free-text garment names, arbitrary prose and unreviewed assistant/photo proposals SHALL NOT establish region coverage or ownership automatically. Full Auto eligibility SHALL identify missing fields and contradictions, not ask users to duplicate the same detail across every take.

#### Scenario: Prose alone cannot establish profile facts
- **WHEN** an assistant proposes coverage without accepted structured evidence
- **THEN** the facts remain non-authoritative and Full Auto readiness is not granted

#### Scenario: Legacy wording supplies no coverage facts
- **WHEN** a look contains only appearance and garment wording
- **THEN** it remains manually usable but is not automatically certified Full Auto ready
- **AND** the user can complete a reusable profile once

#### Scenario: Previously reviewed structured facts are available
- **WHEN** exact source-bound evidence already declares all required region/state facts
- **THEN** those facts can populate a versioned profile without per-take entry or free-text inference
- **AND** their provenance remains inspectable

### Requirement: Appearance spans use exact Unicode scalar indices

Appearance offsets SHALL be zero-based half-open Unicode scalar-value indices into the exact immutable snapshot text, with no normalization or trimming before indexing/digest verification. Unpaired surrogates SHALL be rejected. Browser and backend SHALL agree for non-BMP characters and combining sequences; users SHALL select text visually, not enter numeric offsets.

#### Scenario: Appearance spans use exact Unicode scalar indices
- **WHEN** appearance includes an emoji and a decomposed accent
- **THEN** browser and backend select identical spans without normalization or digest changes

#### Scenario: Appearance contains emoji and combining marks
- **WHEN** ownership spans cross non-BMP or decomposed Unicode text
- **THEN** browser and backend validate identical source spans and digest without rewriting the snapshot

### Requirement: Profile shortcuts require explicit acceptance

The editor SHALL offer explicit whole-appearance-permanent and no-supported-region-coverage shortcuts, and explicit copying of already accepted coverage to a selected equivalent state. Each SHALL produce a previewed user assertion with provenance, not an automatic default derived from a garment name. Existing ownership and inventory checks SHALL still apply.

#### Scenario: Profile shortcuts require explicit acceptance
- **WHEN** the user previews then confirms no-supported-region coverage for selected states
- **THEN** only those states receive the assertion and its provenance

#### Scenario: Accessory shortcut is chosen
- **WHEN** a user explicitly confirms no coverage in supported regions for selected accessory states
- **THEN** the profile records that user assertion for those states and does not infer it for other garments or regions

#### Scenario: A corrected version reuses unchanged facts
- **WHEN** the user explicitly copies accepted coverage from another profile to states whose garment key, canonical wording and state wording are unchanged
- **THEN** the new profile records reviewed provenance to the source profile digest without requiring re-entry of those facts
- **AND** changed states remain unknown and changed appearance requires new ownership spans

### Requirement: Partial coverage does not certify subregions

V1 partial coverage SHALL mean some but not all of the named region without implying side or subregion. V1 SHALL NOT certify arms, hands, back or more precise subregions; canonical garment wording SHALL remain intact and unsupported precise detail SHALL use manual authoring rather than fabricated structured facts.

#### Scenario: Partial coverage does not certify subregions
- **WHEN** a garment has partial chest coverage
- **THEN** the profile asserts neither full coverage nor a side or exact subregion

### Requirement: Look bindings use the snapshot content digest

The saved_look content_digest SHALL use the existing canonical digest of `{appearance, outfit}` produced by saved-look snapshot validation; it SHALL NOT use portable_content_digest or hash the export envelope. Profile bindings SHALL use that same verified snapshot digest.

#### Scenario: Look bindings use the snapshot content digest
- **WHEN** portable_content_digest is supplied instead of the snapshot digest
- **THEN** exact-version verification rejects the substituted hash

#### Scenario: Export digest is submitted as snapshot identity
- **WHEN** a request supplies portable_content_digest instead of the saved version content_digest
- **THEN** the exact-version verification refuses it without substituting either digest

### Requirement: Baseline describes inventory-absent coverage

Baseline SHALL describe supported-region coverage remaining when every listed outfit item is absent, not the initial fully worn outfit. No baseline value SHALL be inferred from an empty inventory. An appearance-only look SHALL have one constant stage, no progression events and empty wardrobe description; Full Auto eligibility SHALL require explicit baseline and inventory confirmation, and consent SHALL show its compiled coverage.

#### Scenario: Baseline describes inventory-absent coverage
- **WHEN** an appearance-only look has explicit confirmed baseline facts
- **THEN** its single stage has no progression events and the consent panel shows compiled coverage

### Requirement: Uncovered assertions are distinct from empty wardrobe

Explicit none means an accepted uncovered-region assertion, whereas absent or unknown facts SHALL block. It SHALL NOT create an implicit no-clothing wardrobe sentence. A selected explicit garment-free final stage SHALL reject a baseline asserting residual worn covering items.

#### Scenario: Uncovered assertions are distinct from empty wardrobe
- **WHEN** an appearance-only look has unknown baseline or an explicit none baseline
- **THEN** unknown blocks eligibility while accepted none compiles coverage without adding a no-clothing wardrobe sentence

#### Scenario: Appearance-only look has no baseline facts
- **WHEN** Full Auto selects an appearance-only look whose baseline is missing or unknown
- **THEN** preflight requests explicit reusable facts rather than equating empty wardrobe with uncovered regions
