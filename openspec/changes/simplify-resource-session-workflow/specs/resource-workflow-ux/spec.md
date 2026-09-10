## Purpose

Make resource ingestion and `resource-v1` session authoring understandable to a normal user by making local file selection and automatic take preparation the default while retaining exact resource provenance, expert controls and explicit generation authorization.

## ADDED Requirements

### Requirement: Normal resource import uses browser-selected files

The application SHALL let a user select one or more local JSON resource files through the browser and preview them without typing a filesystem path that the backend must open. Browser-provided file names SHALL be treated as metadata, not trusted client filesystem paths. The selected content SHALL cross a server-controlled boundary that preserves the exact bytes or equivalent canonical content used for preview and commit.

#### Scenario: User selects resource files
- **WHEN** a user chooses one or more supported JSON files with the normal import control
- **THEN** the files become previewable without the user entering an absolute or relative backend filesystem path
- **AND** the backend does not attempt to open a client-side browser path

#### Scenario: Selected content changes after preview
- **WHEN** the content bound to an import preview is no longer the same content available for commit
- **THEN** the commit is refused and the user must obtain a fresh preview

#### Scenario: Existing local automation uses path import
- **WHEN** an expert or CLI caller uses the existing path-based resource import contract
- **THEN** that workflow remains supported and produces the same canonical import behavior as equivalent browser-selected content

### Requirement: Library identity is inferred when unambiguous

The application SHALL derive a candidate library identity for browser-selected resource files without requiring a `library_key` in the normal flow. It SHALL prefer a supported safe source-declared library identifier when one exists and otherwise derive a stable normalized identity from the file name. Ambiguous collisions SHALL be reported rather than silently merged or assigned unstable automatic suffixes.

#### Scenario: Source declares a supported library identity
- **WHEN** a selected resource envelope exposes a supported safe library identifier
- **THEN** preview uses that identifier as the inferred library identity
- **AND** the user is not required to retype it

#### Scenario: Source has no declared library identity
- **WHEN** a selected supported resource has no accepted source-declared library identifier
- **THEN** preview derives a stable candidate identity from the normalized file stem

#### Scenario: Inferred identity is ambiguous
- **WHEN** two inputs or an existing library create an identity collision that cannot be proven to represent the same logical library
- **THEN** preview reports the ambiguity and refuses silent merge
- **AND** an advanced explicit identity override may be supplied before a fresh preview

### Requirement: Simplified import preserves preview and atomic commit guarantees

The normal import UI SHALL present preview and commit as one user workflow while preserving the existing two-phase integrity boundary. Changing any preview-affecting input SHALL invalidate the prior preview. The primary summary SHALL describe user-relevant outcomes, while exact outcome classifications and technical identifiers remain inspectable.

#### Scenario: Valid selection is previewed
- **WHEN** selected files pass parsing and identity checks
- **THEN** the UI presents a concise summary of resources that are new, updated, unchanged or need attention
- **AND** the persistence action remains disabled until a valid current preview exists

#### Scenario: User changes an inferred identity override
- **WHEN** a user changes an advanced library identity after preview
- **THEN** the old preview is invalidated
- **AND** commit cannot use the stale preview

#### Scenario: User requests technical details
- **WHEN** a user expands import diagnostics
- **THEN** the full canonical report remains available including unresolved items and safe revision identifiers

### Requirement: Translation maps can be selected as local files

The normal Resources UI SHALL allow a user to select a local JSON translation map without typing a backend-visible map path. Translation preview and apply SHALL remain bound to the selected map content and current library state using the translation contract's existing validation and stale-preview protections.

#### Scenario: User selects a translation map
- **WHEN** a user chooses a supported translation-map JSON file for an imported library
- **THEN** translation preview can run without the user typing a filesystem path

#### Scenario: User replaces the selected translation map
- **WHEN** a different map file is selected after a translation preview
- **THEN** the previous translation preview is invalidated
- **AND** apply requires a preview of the newly selected content

### Requirement: Resource session creation requests high-level intent

The normal `resource-v1` creation flow SHALL let a user define a session using a character model, ready resource selection, requested take count, optional session brief, applicable constant look/wardrobe choices and a variation policy. It SHALL NOT require the user to manually author camera, framing, pose or expression for every take before the session can be prepared automatically.

#### Scenario: User creates a twelve-take resource session
- **WHEN** a user selects a character and ready resource, requests twelve takes and leaves automatic creative variation enabled
- **THEN** the system creates a persisted resource draft containing twelve stable take IDs
- **AND** the user is not required to type twelve sets of camera, framing, pose and expression values

#### Scenario: User fixes one creative dimension
- **WHEN** the variation policy marks a creative dimension as fixed
- **THEN** prepared takes use one explicit or automatically established value for that dimension across the requested scope
- **AND** the system does not silently vary that dimension between takes

### Requirement: Automatic authoring fills only unlocked take choices

When a configured prompt assistant is available, the application SHALL automatically fill unlocked take-level descriptive choices only from the closed set `camera`, `framing`, `pose` and `expression`. Assistant output SHALL pass through the resource preparation contract's validation before finalization and SHALL NOT overwrite fixed character identity, look, initial wardrobe, effective wardrobe, resource selection, adaptations or already locked take choices.

#### Scenario: All four creative choices are unlocked
- **WHEN** automatic preparation runs for a take with camera, framing, pose and expression unlocked
- **THEN** the configured assistant is asked only for those unlocked fields
- **AND** validated values are incorporated into the take's prepared effective state

#### Scenario: Take already fixes camera
- **WHEN** a take explicitly fixes camera but leaves framing, pose and expression unlocked
- **THEN** automatic authoring cannot replace the camera
- **AND** it may fill only framing, pose and expression

#### Scenario: Assistant attempts to change fixed session state
- **WHEN** assistant output contains a field outside the allowed unlocked take choices
- **THEN** preparation refuses that output
- **AND** no fixed session state is changed or silently persisted

### Requirement: Automatic authoring records assistant provenance

Assistant-authored take choices SHALL retain the existing resource writer synthesis provenance semantics, including the bounded writer input, validated writer output and synthesis kind. Automatically generated values SHALL NOT be persisted as if the user manually entered them solely to bypass the assistant provenance contract.

#### Scenario: Assistant successfully fills a take
- **WHEN** automatic authoring supplies valid values for an incomplete take
- **THEN** the prepared snapshot records assistant synthesis provenance for that take
- **AND** reopening the session does not require another assistant call to reconstruct the finalized prompt

#### Scenario: Ready take is prepared again
- **WHEN** automatic authoring is requested for a take whose current prepared snapshot is already ready or generated
- **THEN** the immutable snapshot is reused according to existing preparation semantics
- **AND** the assistant is not called again merely to recreate the same take

### Requirement: Automatic preparation is recoverable

The application SHALL persist the resource draft before lengthy assistant work and SHALL retain each successfully completed prepared take independently. Retrying after interruption SHALL resume only work that is incomplete or invalidated for the current authoritative plan revision.

#### Scenario: Authoring is interrupted after three takes
- **WHEN** a twelve-take automatic preparation stops after three takes are completed
- **THEN** reopening the session shows those three completed preparations
- **AND** retry continues with the remaining incomplete takes rather than rewriting the completed three

#### Scenario: Plan changes during or after authoring
- **WHEN** the authoritative plan revision changes
- **THEN** stale authoring work cannot overwrite the newer plan
- **AND** existing plan revision and invalidation rules determine which preparations remain valid

### Requirement: Generated take choices are reviewable without mandatory raw editing

The normal Takes and Review surfaces SHALL show the effective camera, framing, pose and expression produced for each prepared take. Raw per-field editing SHALL be available as an advanced override rather than four mandatory blank inputs in the primary workflow.

#### Scenario: Automatic preparation completes
- **WHEN** synthesized takes are available
- **THEN** the user can review each take's effective creative choices before generation without entering edit mode

#### Scenario: User overrides a generated choice
- **WHEN** a user opens advanced editing and changes a generated take choice
- **THEN** the explicit value is saved through the authoritative session plan
- **AND** affected preparation/review state is invalidated according to existing resource-plan rules before the changed take can be generated

### Requirement: Manual completion remains a fallback

The application SHALL remain usable for resource sessions when no prompt assistant is configured. In that condition automatic authoring SHALL report that it is unavailable, while advanced manual completion SHALL continue to support the same closed take-level descriptive choices and deterministic preparation.

#### Scenario: No assistant is configured
- **WHEN** a user creates or opens a resource draft on a system without a configured prompt assistant
- **THEN** automatic authoring is unavailable with a readable reason
- **AND** the user may complete required take choices manually without changing session mode

### Requirement: Automatic authoring does not authorize generation

Automatic session authoring and preparation SHALL stop at review. It SHALL NOT automatically approve the current plan revision, submit prepared takes, create implicit generation authorization or start the session runner. Existing conflict, workflow, review and pending-shot gates remain authoritative.

#### Scenario: All requested takes are prepared successfully
- **WHEN** automatic preparation finishes every take
- **THEN** the session enters or remains at the review stage
- **AND** no shot is submitted or generated until the existing explicit review and generation actions are performed

#### Scenario: A resource conflict remains unresolved
- **WHEN** automatic authoring encounters or leaves an unresolved resource conflict
- **THEN** review/generation remains blocked by the existing conflict gate
- **AND** automatic authoring does not silently adapt or discard the conflicting source content

### Requirement: Legacy and expert workflows remain available

The simplified resource workflow SHALL NOT change legacy session behavior or make resource automation a prerequisite for expert workflows. Legacy measured Catalogue/Compose/Fill/Judge behavior, path-based resource automation and detailed resource inspection SHALL remain available in their current scopes.

#### Scenario: User opens a legacy session
- **WHEN** a session does not use `resource-v1`
- **THEN** its existing composition and measured-catalogue workflow remains unchanged

#### Scenario: Expert inspects resource details
- **WHEN** a user opens advanced resource details
- **THEN** revision identity, readiness, field roles, coverage and other existing diagnostics remain inspectable
